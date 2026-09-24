#!/usr/bin/env python3
"""cgprofile — profile what a workload does to a host, and to its neighbours.

    cgprofile run     --observe soulmask -- ./gate.sh      wrap a command
    cgprofile attach  --target dev-background.slice -d 300 watch something running
    cgprofile mark    "restoring database"                 label a phase
    cgprofile report  --run-dir runs/run-…                 re-render a run
    cgprofile targets --target …                           resolve and print
    cgprofile doctor                                       what can I reach?

Two processes, one directory
----------------------------
The collector needs the host's cgroup tree, which a devcontainer cannot see; the
reports need pandas and plotly, which have no business inside a privileged
container next to production. So the two halves run in different places and
meet in the run directory:

* the **collector** (``_collect``, standard library only) runs wherever it can
  see the host — directly if it already can, otherwise re-executed inside a
  privileged helper container;
* the **driver** (this process) creates the run directory, runs the wrapped
  command, emits marks, and afterwards renders the reports using the venv.

Coordination is by sentinel files in the shared run directory rather than by
pipes or signals, because the two sides may be in different PID namespaces and
because a file survives the driver being suspended mid-run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Sequence, Tuple
import tomllib

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "pyproject.toml"), "rb") as _project_file:
    _source_version = tomllib.load(_project_file)["project"]["version"]
sys.path.insert(0, HERE)

from lib import access, targets as targets_mod, util  # noqa: E402
from lib.version import runtime_version  # noqa: E402

__version__ = runtime_version(_source_version)

READY_FILE = "collector.ready"
STOP_FILE = "collector.stop"
DONE_FILE = "collector.done"
DEFAULT_OUT = os.path.join(HERE, "runs")

def cli_headline() -> str:
    return f"CGPROFILE {__version__} — cgroup resource profiler"


class CgprofileArgumentParser(argparse.ArgumentParser):
    """Prefix help and parse diagnostics with the profiler identity."""

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("parser_class", type(self))
        return super().add_subparsers(**kwargs)

    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)

# RG55-INTERFACE-CONTRACT.md §1.8 — kept as literals here (rather than
# imported from lib.serve) so `build_parser()` never has to import that
# module just to read a default; `lib.serve.DEFAULT_SOCKET_PATH` /
# `DEFAULT_SESSIONS_DIR` are the same two values (a test asserts the two
# stay in sync, the same "drift guard, not a second source of truth" shape
# `pyproject.toml`'s own header comment already uses for its lock file).
DEFAULT_CTL_SOCKET = "/run/cgprofile/ctl.sock"
DEFAULT_CGPROFILE_SESSIONS = "/var/lib/cgprofile/sessions"


# ── small helpers ───────────────────────────────────────────────────────────

def _err(message: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    print(f"cgprofile: {message}", file=sys.stderr)
    raise SystemExit(code)


def _note(message: str) -> None:
    print(f"[cgprofile] {message}", file=sys.stderr, flush=True)


def _venv_python() -> Optional[str]:
    candidate = os.path.join(HERE, "venv", "bin", "python")
    return candidate if os.access(candidate, os.X_OK) else None


def _require_reporting_deps() -> None:
    """Fail with the fix, not with a traceback, when the venv is missing.

    The reporting half is the only part that needs third-party libraries, and
    it is the part a user reaches last — long after the run they wanted to keep
    has finished. Losing that data to an ImportError would be the worst
    possible time to discover the venv was never built.
    """
    try:
        import pandas  # noqa: F401
        import plotly  # noqa: F401
    except ImportError:
        _err(
            "reporting needs the profiler venv — run ./setup.sh once.\n"
            "  The collected data is safe; re-run `cgprofile report --run-dir <dir>` after."
        )


def _load_store():
    from lib import store

    return store


# ── target specs: resolve names in the process that has the Docker socket ───

def _predigest_specs(specs: Sequence[str], *, resolve_self: bool = False) -> List[str]:
    """Resolve caller-side identities before handing specs to the helper.

    Name lookup needs the Docker socket, which only this side has; cgroup
    lookup needs the host tree, which only the collector side has. Splitting
    the two here means the collector never talks to the daemon — so a daemon
    restart mid-run cannot take the profiler down with it. In helper mode,
    ``self`` refers to the caller in this container; translate it to this
    container's immutable id before re-exec, since the helper's private PID
    and cgroup namespaces make its own ``self`` a different process/cgroup.
    """
    out: List[str] = []
    for spec in specs:
        scheme, rest = targets_mod._split_spec(spec)
        value, options = targets_mod._parse_options(rest)
        suffix = ("@" + ",".join(f"{k}={v}" for k, v in options.items())) if options else ""
        try:
            if resolve_self and (scheme == "self" or (not scheme and value == "self")):
                cid = _caller_container_id()
                extra = suffix or ""
                if "as" not in options:
                    extra = (extra + "," if extra else "@") + "as=self"
                out.append(f"containerid:{cid}{extra}")
                continue
            if resolve_self and scheme == "pid":
                try:
                    pid = int(value)
                except ValueError:
                    _err(f"pid: needs a number, got {value!r}")
                if "subpath" in options:
                    _err("subpath is reserved for the helper's internal container target")
                cgroup_path = _helper_pid_cgroup_path(pid)
                cid = _caller_container_id()
                resolved_options = [("subpath", quote(cgroup_path, safe="/"))]
                resolved_options.extend(options.items())
                if "as" not in options:
                    resolved_options.append(("as", f"pid-{pid}"))
                encoded = ",".join(f"{key}={option}" for key, option in resolved_options)
                out.append(f"containerid:{cid}@{encoded}")
                continue
            if scheme == "container":
                cid, name = targets_mod.resolve_container(value)
                extra = suffix or ""
                if "as" not in options:
                    extra = (extra + "," if extra else "@") + f"as={name}"
                out.append(f"containerid:{cid}{extra}")
                continue
            if scheme == "label":
                for cid, name in targets_mod.resolve_label(value):
                    extra = suffix or ""
                    if "as" not in options:
                        extra = (extra + "," if extra else "@") + f"as={name}"
                    out.append(f"containerid:{cid}{extra}")
                continue
            if not scheme and targets_mod.docker_bin():
                try:
                    cid, name = targets_mod.resolve_container(value)
                except targets_mod.TargetError:
                    out.append(spec)
                    continue
                extra = suffix or ""
                if "as" not in options:
                    extra = (extra + "," if extra else "@") + f"as={name}"
                out.append(f"containerid:{cid}{extra}")
                continue
        except targets_mod.TargetError as exc:
            _err(f"{spec}: {exc}")
        out.append(spec)
    return out


def _caller_container_id() -> str:
    cid = access.self_container_id()
    if not cid or not targets_mod._CONTAINER_ID_RE.fullmatch(cid):
        _err(
            "helper-mode self/pid target needs the caller container's full Docker id; "
            "Docker inspect could not establish it"
        )
    return cid


def _helper_pid_cgroup_path(pid: int) -> str:
    """Resolve a caller-visible PID to a path relative to its container root."""
    if pid <= 0:
        _err("pid: needs a positive process id")
    if access.have_host_cgroup_view():
        _err("helper-mode pid targets require the caller's private cgroup view; use direct mode")
    process_dir = os.path.join(access.PROC_ROOT, str(pid))
    if not os.path.isdir(process_dir):
        _err(f"pid {pid} is not visible in the caller's proc view")
    if not access.same_cgroup_namespace(pid, access.PROC_ROOT):
        _err(f"pid {pid} uses a different cgroup namespace; name its container or cgroup instead")
    text = util.read_text(os.path.join(process_dir, "cgroup"))
    path = targets_mod._unified_cgroup_path(text or "")
    if path is None or not path.startswith("/"):
        _err(f"pid {pid} has no absolute unified cgroup path in the caller's proc view")
    components = [] if path == "/" else path[1:].split("/")
    if any(part in ("", ".", "..") for part in components):
        _err(f"pid {pid} has an unsafe cgroup path in the caller's proc view")
    return path


def _limits_snapshot(limits_mod, eff) -> Dict[str, object]:
    """Serialise one cgroup's effective limits into the manifest.

    Both forms are written on purpose. ``described`` is what a human reads at
    the top of the report; ``resolved`` is what ``analyze.make_proposals``
    reasons over, and it has to be structured because "memory.high 6.0G (bound
    by /wings.slice/...)" is not something a proposal generator can compare
    against host RAM. Writing only the prose form — which an earlier version of
    this function did — left every real run producing no proposals at all,
    silently, because the consumer had nothing it could parse.

    ``chain`` is deliberately excluded: it is the full LimitSet of every
    ancestor, it dominates the manifest's size, and every fact downstream needs
    has already been resolved out of it into the scalars below.
    """
    resolved = {
        field: getattr(eff, field)
        for field in (
            "memory_max", "memory_max_by",
            "memory_high", "memory_high_by",
            "memory_swap_max", "memory_swap_max_by",
            "strict_min", "recursive_min", "strict_low", "recursive_low",
            "protection_mode",
            "cpu_cores", "cpu_cores_by",
            "io_max", "io_max_by",
            "pids_max", "warnings",
        )
    }
    return {
        "resolved": resolved,
        "described": limits_mod.describe(eff),
        "fingerprint": limits_mod.fingerprint(eff),
    }


def _tag_role(specs: Sequence[str], role: str) -> List[str]:
    out = []
    for spec in specs:
        out.append(spec if "role=" in spec else (spec + ("," if "@" in spec else "@") + f"role={role}"))
    return out


# ── collector side ──────────────────────────────────────────────────────────

def cmd_collect(args: argparse.Namespace) -> int:
    """Sample until the stop sentinel appears. Standard library only.

    This is the entry point the helper container runs. It must not import
    pandas, plotly, or anything else outside the standard library — see
    DESIGN.md §1.
    """
    from lib import caps as caps_mod
    from lib import events as events_mod
    from lib import limits as limits_mod
    from lib import metrics as metrics_mod
    from lib import phases as phases_mod
    from lib import sampler as sampler_mod
    from lib import store as store_mod

    run = store_mod.RunDir(os.path.dirname(args.run_dir), os.path.basename(args.run_dir),
                           create=True)
    root = access.CGROUP_ROOT

    resolved = targets_mod.resolve_all(args.target, root, default_follow=args.follow_children)
    observers = targets_mod.resolve_all(args.observe, root, default_follow=False,
                                        role="observer") if args.observe else []
    for target in observers:
        target.role = "observer"
    all_targets = resolved + observers
    if not all_targets:
        _err("no target resolved — nothing to sample")

    membership = targets_mod.Membership(all_targets, root=root, max_depth=args.max_depth)
    membership.refresh()

    flags = limits_mod.mount_flags()
    limit_map = {t.cgroup: limits_mod.effective(t.cgroup, root, flags) for t in all_targets}

    run.write_manifest({
        "run_id": run.run_id,
        "started": time.time(),
        "argv": sys.argv,
        "mode": "collector",
        "cgroup_root": root,
        "mount_flags": sorted(flags),
        "targets": [
            {"key": t.key, "cgroup": t.cgroup, "label": t.label, "kind": t.kind,
             "role": t.role, "follow_children": t.follow_children,
             "container_id": t.container_id, "pid": t.pid}
            for t in all_targets
        ],
        "limits": {cg: _limits_snapshot(limits_mod, eff) for cg, eff in limit_map.items()},
        "host": metrics_mod.sample_host(),
        "config": {"hot_interval": args.hot_interval, "idle_interval": args.idle_interval},
    })

    roles = {t.cgroup: t.role for t in all_targets}
    detector = events_mod.Detector(events_mod.DetectorConfig(), limit_map, roles)
    config = sampler_mod.SamplerConfig(
        hot_interval=args.hot_interval,
        idle_interval=args.idle_interval,
        discovery_interval=args.discovery_interval,
        max_duration=args.duration,
    )

    stop_path = os.path.join(run.path, STOP_FILE)
    cap_changes = json.loads(args.caps) if args.caps else {}

    stopping = {"now": False}

    def _handle(signum, frame):  # noqa: ARG001
        stopping["now"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    def should_stop() -> bool:
        return stopping["now"] or os.path.exists(stop_path)

    host_every = max(1, int(round(args.idle_interval / max(args.hot_interval, 0.05))))
    host_state = {"n": 0}

    def sample_fn(current):
        """Read every sampled cgroup, plus /proc for pid targets and the host.

        Host-wide counters move far more slowly than a cgroup's and cost a
        whole-file parse of /proc/vmstat, so they are read on a slower cadence
        than the cgroups; the analyser forward-fills them onto the sample grid.
        """
        out = {
            "cg": {
                path: metrics_mod.sample_cgroup(
                    os.path.join(root, path.lstrip("/")),
                    current.owner(path).metrics if current.owner(path) else None,
                )
                for path in current.paths()
            }
        }
        procs = {
            str(t.pid): metrics_mod.sample_proc(t.pid)
            for t in all_targets
            if t.pid
        }
        if procs:
            out["proc"] = {pid: data for pid, data in procs.items() if data}
        if host_state["n"] % host_every == 0:
            out["host"] = metrics_mod.sample_host()
        host_state["n"] += 1
        return out

    damon_session = None
    if args.damon:
        from lib import damon as damon_mod

        if damon_mod.available():
            pids = [t.pid for t in all_targets if t.pid]
            damon_targets = [damon_mod.DamonTarget(kind="vaddr", pid=p, label=str(p))
                             for p in pids] or [damon_mod.DamonTarget(kind="paddr", pid=None,
                                                                     label="physical")]
            damon_session = damon_mod.DamonSession(damon_targets)
        else:
            _note("DAMON requested but unavailable here — continuing without it")

    sampler = sampler_mod.Sampler(membership, config, sample_fn)

    with caps_mod.TempCaps(cap_changes, root) if cap_changes else _nullcontext():
        with damon_session if damon_session else _nullcontext():
            open(os.path.join(run.path, READY_FILE), "w").close()
            _note(f"collecting into {run.path}")
            prev = None
            prev_mono = None
            marks_seen = 0

            def on_sample(record):
                nonlocal prev, prev_mono, marks_seen
                run.append("samples", record)
                if prev is not None:
                    dt = record["mono"] - prev_mono
                    for event in detector.observe(prev, record, dt):
                        run.append("events", event.to_dict())
                prev, prev_mono = record, record["mono"]
                if damon_session and record["seq"] % 20 == 0:
                    for region in damon_session.collect():
                        run.append("damon", {"mono": record["mono"], **region})
                # A phase mark can be written by a different process sharing
                # this run directory (cgprofile mark, a wrapper boundary, a
                # log-tail match — see DESIGN.md §3) — this sampler has no
                # other way to notice one, so poll for new marks each tick
                # and give any that arrived the same next-tick-snaps-to-hot
                # treatment as an event or a topology change (sampler.py
                # §4.4's "any... phase mark" clause).
                marks = phases_mod.load_marks(run.path)
                if len(marks) > marks_seen:
                    marks_seen = len(marks)
                    sampler.force_hot()

            def on_topology(appeared, disappeared):
                # The sampler reports membership changes without timestamps;
                # anchor them to the most recent sample so an appearance lines
                # up with the series rather than with wall-clock drift.
                mono = prev_mono if prev_mono is not None else 0.0
                for event in detector.topology(appeared, disappeared, time.time(), mono):
                    run.append("events", event.to_dict())

            sampler.run(should_stop, on_sample, on_topology)

    manifest = run.read_manifest()
    manifest["ended"] = time.time()
    manifest["duration"] = manifest["ended"] - manifest["started"]
    run.write_manifest(manifest)
    open(os.path.join(run.path, DONE_FILE), "w").close()
    _note("collector finished")
    return 0


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ── helper launch ───────────────────────────────────────────────────────────

def _start_named_helper(
    command: List[str], helper_name: str, image: str,
    *, stdout=None, stderr=None,
) -> subprocess.Popen:
    """Start a named helper and confirm its create-time CPU cap live."""
    _note(f"starting helper container {helper_name} ({image.split(':')[0][:48]})")
    child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
    # --cpus=3 is present in the create request, so the helper is bounded from
    # its first instruction. Attempt the estate's required post-launch update
    # immediately; a very short helper may finish before Docker can update it,
    # but it was capped from creation and can then be returned safely.
    for _ in range(5):
        try:
            updated = access._docker("update", "--cpus=3", helper_name, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            updated = None
        if updated is not None and updated.returncode == 0:
            return child
        if child.poll() is not None:
            return child
        time.sleep(0.05)
    if child.poll() is None:
        # The exact name was printed before launch and is unique to this run.
        # Remove only that helper if the create-time cap could not be
        # confirmed; never leave a partially launched observer behind.
        try:
            access._docker("rm", "--force", helper_name, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        _err(f"could not confirm the 3-CPU cap on helper container {helper_name}")
    return child


def _launch_helper(run_path: str, collect_args: List[str],
                   image: Optional[str],
                   cgroup_parent: Optional[str] = None) -> subprocess.Popen:
    spec = access.build_helper_spec(HERE, os.path.dirname(run_path), image, cgroup_parent)
    helper_name = f"cgprofile-helper-{os.getpid()}-{time.time_ns()}"
    docker_args = spec.docker_args(name=helper_name)
    # Plain python3, not the venv: the collector is standard-library only by
    # contract, and running it on the bare interpreter is what keeps it that way.
    command = [
        access.docker_bin(),
        *docker_args,
        "python3",
        os.path.join(HERE, "cgprofile.py"),
        "_collect",
        *collect_args,
    ]
    return _start_named_helper(
        command, helper_name, spec.image, stdout=sys.stderr, stderr=sys.stderr,
    )


def _wait_for(path: str, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(0.05)
    _err(f"timed out after {timeout:.0f}s waiting for {what}")


def _collect_args_from(args: argparse.Namespace, run_path: str,
                       specs: Sequence[str], observers: Sequence[str]) -> List[str]:
    out = ["--run-dir", run_path]
    for spec in specs:
        out += ["--target", spec]
    for spec in observers:
        out += ["--observe", spec]
    out += ["--hot-interval", str(args.hot_interval),
            "--idle-interval", str(args.idle_interval),
            "--discovery-interval", str(args.discovery_interval),
            "--max-depth", str(args.max_depth)]
    # A truthiness check would drop an explicit `--duration 0` (attach and
    # take exactly one sample, then stop — SamplerConfig(max_duration=0) is
    # meaningful: Sampler.run() breaks after its very first tick) silently
    # into "no duration limit", turning a bounded one-shot attach into an
    # unbounded one. Only the unset (None) default should be omitted.
    if getattr(args, "duration", None) is not None:
        out += ["--duration", str(args.duration)]
    if args.follow_children:
        out += ["--follow-children"]
    if args.damon:
        out += ["--damon"]
    caps = _caps_from(args)
    if caps:
        out += ["--caps", json.dumps(caps)]
    return out


def _caps_from(args: argparse.Namespace) -> Dict[str, Dict[str, str]]:
    """Assemble ``--cap-*`` flags into the per-cgroup write map."""
    caps: Dict[str, Dict[str, str]] = {}
    for raw in getattr(args, "cap", None) or []:
        if "=" not in raw:
            _err(f"--cap needs <cgroup>:<file>=<value>, got {raw!r}")
        left, _, value = raw.partition("=")
        cgroup, _, filename = left.rpartition(":")
        if not cgroup or not filename:
            _err(f"--cap needs <cgroup>:<file>=<value>, got {raw!r}")
        caps.setdefault(cgroup, {})[filename] = value
    return caps


# ── log tailing: driver-tier only, see lib/logtail.py's module docstring ────

def _parse_log_tail_spec(spec: str) -> Tuple[str, Optional[str]]:
    """``container:<name|id>[@as=LABEL]`` -> ``(container_ref, label)``.

    Reuses ``targets_mod._parse_options`` for the ``@as=...`` half rather
    than inventing a second ``@opt`` grammar — this package already has one.
    Unlike ``--target``'s ``container:`` scheme, the container reference is
    never resolved to a full id here: ``docker logs`` accepts a name, a short
    id or a full id equally well, and this runs in the driver process, which
    already has the socket — there is no collector-side handoff to prepare
    for.
    """
    if not spec.startswith("container:"):
        _err(f"--log-tail needs container:<name|id>[@as=LABEL], got {spec!r}")
    value, options = targets_mod._parse_options(spec[len("container:"):])
    if not value:
        _err(f"--log-tail needs a container name or id, got {spec!r}")
    extra = set(options) - {"as"}
    if extra:
        _err(f"--log-tail: unknown option(s) {sorted(extra)} in {spec!r}")
    return value, options.get("as")


def _collect_log_patterns(args: argparse.Namespace):
    from lib import logtail as logtail_mod

    patterns = []
    for path in getattr(args, "log_match_file", None) or []:
        try:
            patterns.extend(logtail_mod.load_patterns(path))
        except OSError as exc:
            _err(f"--log-match-file {path}: {exc}")
        except logtail_mod.LogPatternError as exc:
            _err(f"--log-match-file {path}: {exc}")
    for raw in getattr(args, "log_match", None) or []:
        try:
            patterns.append(logtail_mod.parse_pattern(raw))
        except logtail_mod.LogPatternError as exc:
            _err(str(exc))
    return patterns


def _start_log_tailers(args: argparse.Namespace, run_path: str) -> List[object]:
    """Start one ``LogTailer`` per ``--log-tail``, only once the collector is
    already ready — a mark timestamped before ``samples.jsonl`` exists has
    nothing to be correlated against.
    """
    specs = getattr(args, "log_tail", None) or []
    if not specs:
        return []
    from lib import logtail as logtail_mod

    patterns = _collect_log_patterns(args)
    if not patterns:
        _err("--log-tail given with no --log-match/--log-match-file — nothing to look for")
    tailers = []
    for spec in specs:
        container, label = _parse_log_tail_spec(spec)
        tailer = logtail_mod.LogTailer(container, patterns, run_path, label=label)
        tailer.start()
        tailers.append(tailer)
    return tailers


def _stop_log_tailers(tailers: Sequence[object]) -> None:
    for tailer in tailers:
        tailer.stop()


# ── driver commands ─────────────────────────────────────────────────────────

def _start_run(args: argparse.Namespace):
    if not args.target:
        _err("no --target given (try --target dev-background.slice, or `self`)")

    mode = access.choose_mode(args.mode)
    resolve_self = mode == "helper"
    specs = _tag_role(_predigest_specs(args.target, resolve_self=resolve_self), "subject")
    observers = _tag_role(_predigest_specs(args.observe or [], resolve_self=resolve_self), "observer")
    if not specs:
        _err("no --target given (try --target dev-background.slice, or `self`)")

    store = _load_store()
    base = os.path.realpath(args.out_dir)
    os.makedirs(base, exist_ok=True)
    run = store.RunDir(base, args.run_id or store.new_run_id(), create=True)

    collect_args = _collect_args_from(args, run.path, specs, observers)

    if mode == "direct":
        child = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "cgprofile.py"), "_collect", *collect_args],
            stdout=sys.stderr, stderr=sys.stderr,
        )
    else:
        child = _launch_helper(run.path, collect_args, args.helper_image,
                               getattr(args, "helper_cgroup_parent", None))

    try:
        _wait_for(os.path.join(run.path, READY_FILE), args.start_timeout, "the collector to start")
    except SystemExit:
        # The collector (or, in helper mode, a whole privileged container)
        # never signalled ready — it would otherwise run forever with nothing
        # left holding a reference to stop it.
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
        raise
    return run, child


def _stop_run(run, child: subprocess.Popen, timeout: float = 30.0) -> None:
    open(os.path.join(run.path, STOP_FILE), "w").close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(os.path.join(run.path, DONE_FILE)):
            break
        if child.poll() is not None:
            break
        time.sleep(0.1)
    else:
        _note("collector did not stop cleanly — terminating")
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()


def cmd_run(args: argparse.Namespace) -> int:
    """Wrapper mode: profile the lifetime of a command, phase-marked."""
    if not args.command:
        _err("nothing to run — put the command after `--`")
    from lib import phases as phases_mod

    run, child = _start_run(args)
    tailers = _start_log_tailers(args, run.path)
    label = args.phase or os.path.basename(args.command[0])
    phases_mod.emit_mark(run.path, label, kind="phase",
                         meta={"argv": args.command}, source="wrapper")
    _note(f"running: {' '.join(args.command)}")

    env = dict(os.environ)
    env["CGPROFILE_RUN_DIR"] = run.path
    env["CGPROFILE_MARK"] = os.path.join(HERE, "cgprofile")
    started = time.time()
    try:
        result = subprocess.run(args.command, env=env, check=False)
    except OSError as exc:
        # A launch failure (bad command, no exec permission) is not caught
        # by check=False — that only governs a nonzero exit, not a failed
        # exec. Without this, the collector process (or a whole privileged
        # helper container) would be left running forever with nothing left
        # holding a reference to stop it.
        _stop_log_tailers(tailers)
        _stop_run(run, child)
        _err(f"failed to run {args.command[0]!r}: {exc}")
    elapsed = time.time() - started

    phases_mod.emit_mark(run.path, f"{label}:done", kind="event",
                         meta={"exit_code": result.returncode,
                               "elapsed_s": round(elapsed, 3)}, source="wrapper")
    if args.settle > 0:
        phases_mod.emit_mark(run.path, "settle", kind="phase", source="wrapper")
        _note(f"settling for {args.settle:.0f}s to capture the tail")
        time.sleep(args.settle)

    _stop_log_tailers(tailers)
    _stop_run(run, child)
    _note(f"command exited {result.returncode} after {elapsed:.1f}s")
    if not args.no_report:
        _report(run.path, args)
    return result.returncode


def cmd_attach(args: argparse.Namespace) -> int:
    """Attach mode: watch something already running."""
    run, child = _start_run(args)
    tailers = _start_log_tailers(args, run.path)
    stop_path = args.until_file
    _note(f"attached — run id {run.run_id}")
    try:
        deadline = time.monotonic() + args.duration if args.duration else None
        while True:
            if child.poll() is not None:
                break
            if deadline and time.monotonic() >= deadline:
                break
            if stop_path and os.path.exists(stop_path):
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        _note("interrupted — stopping collector")
    _stop_log_tailers(tailers)
    _stop_run(run, child)
    if not args.no_report:
        _report(run.path, args)
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    """Emit a phase mark. Callable from anywhere sharing the run directory."""
    from lib import phases as phases_mod

    run_path = args.run_dir or os.environ.get("CGPROFILE_RUN_DIR")
    if not run_path:
        _err("no run directory — pass --run-dir or set CGPROFILE_RUN_DIR "
             "(cgprofile run exports it into the wrapped command's environment)")
    if not os.path.isdir(run_path):
        _err(f"run directory does not exist: {run_path}")
    meta = json.loads(args.meta) if args.meta else {}
    mark = phases_mod.emit_mark(run_path, args.name, kind=args.kind, meta=meta)
    if not args.quiet:
        _note(f"mark {mark.kind}:{mark.name}")
    return 0


def _report(run_path: str, args: argparse.Namespace) -> None:
    _require_reporting_deps()
    from lib import analyze, report_html, report_md
    from lib import store as store_mod

    run = store_mod.RunDir(os.path.dirname(run_path), os.path.basename(run_path), create=False)
    analysis = analyze.build(run)
    wrote = []
    if not getattr(args, "md_only", False):
        wrote.append(report_html.render(
            analysis, os.path.join(run_path, "report.html"),
            plotlyjs=getattr(args, "plotlyjs", "inline")))
    if not getattr(args, "html_only", False):
        wrote.append(report_md.render(analysis, run_path))
    for path in wrote:
        print(path)


def cmd_report(args: argparse.Namespace) -> int:
    store = _load_store()
    run_path = args.run_dir
    if not run_path:
        latest = store.RunDir.latest(os.path.realpath(args.out_dir))
        if not latest:
            _err(f"no runs under {args.out_dir}")
        run_path = latest.path
    _report(run_path, args)
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    """Resolve target specs and print what would be sampled, without sampling."""
    mode = access.choose_mode(args.mode)
    if mode == "helper":
        _note("resolving through a helper container (no host cgroup view here)")
        specs = _tag_role(_predigest_specs(args.target, resolve_self=True), "subject")
        spec = access.build_helper_spec(HERE, DEFAULT_OUT, args.helper_image,
                                       getattr(args, "helper_cgroup_parent", None))
        helper_name = f"cgprofile-targets-{os.getpid()}-{time.time_ns()}"
        command = [access.docker_bin(), *spec.docker_args(name=helper_name), "python3",
                   os.path.join(HERE, "cgprofile.py"), "targets", "--mode", "direct"]
        for item in specs:
            command += ["--target", item]
        child = _start_named_helper(command, helper_name, spec.image)
        return child.wait()

    resolved = targets_mod.resolve_all(args.target, access.CGROUP_ROOT,
                                       default_follow=args.follow_children)
    if not resolved:
        _err("nothing resolved")
    from lib import limits as limits_mod

    flags = limits_mod.mount_flags()
    membership = targets_mod.Membership(resolved, root=access.CGROUP_ROOT,
                                        max_depth=args.max_depth)
    membership.refresh()
    for target in resolved:
        print(f"{target.label}  [{target.kind}]")
        print(f"  cgroup: {target.cgroup}")
        effective = limits_mod.effective(target.cgroup, access.CGROUP_ROOT, flags)
        for line in limits_mod.describe(effective):
            print(f"  {line}")
    extra = [p for p in membership.paths() if all(p != t.cgroup for t in resolved)]
    if extra:
        print(f"\n+ {len(extra)} descendant cgroups followed:")
        for path in extra[:20]:
            print(f"  {path}")
        if len(extra) > 20:
            print(f"  … and {len(extra) - 20} more")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what this process can reach, and what it would need to reach more."""
    info = access.describe_access()
    print("access")
    for key, value in info.items():
        print(f"  {key:20s} {value}")

    print("\nreporting venv")
    venv = _venv_python()
    if venv:
        try:
            import pandas, plotly  # noqa: F401

            print(f"  ok                   {sys.executable}")
        except ImportError:
            print(f"  present but this interpreter lacks the libraries: {sys.executable}")
            print(f"  use                  {venv}")
    else:
        print("  missing              run ./setup.sh")

    mode = "direct" if info["host_cgroup_view"] else "helper"
    print(f"\nresolved mode          {mode}")
    if mode == "helper":
        try:
            spec = access.build_helper_spec(HERE, DEFAULT_OUT, args.helper_image,
                                           getattr(args, "helper_cgroup_parent", None))
            print(f"  helper image         {spec.image}")
            print(f"  placed in            {spec.cgroup_parent}")
            print(f"  repo  {spec.repo_host_path} -> {spec.repo_mount_path}")
            print(f"  out   {spec.out_host_path} -> {spec.out_mount_path}")
        except access.AccessError as exc:
            print(f"  helper unavailable   {exc}")
            return 1
    return 0


# ── daemon side: `serve` / `ctl` (RG55-INTERFACE-CONTRACT.md) ───────────────
#
# Both verbs are COLLECTOR tier (DESIGN.md §1: stdlib only) — `lib.serve`
# imports nothing beyond this package's own stdlib-only modules, so both
# `cmd_serve` and `cmd_ctl` run on the bare system python3, exactly the
# split the `cgprofile` bash shim's own header comment already draws
# between `_collect`/`mark` (system python) and everything else (the venv).
# `ctl report` is the one exception threaded through: `lib.serve.
# SessionServer.handle_report` itself shells out to the venv (RW-14) so this
# CLI layer needs no special-casing for it at all — it is just another verb.

def cmd_serve(args: argparse.Namespace) -> int:
    """Run the profiling daemon. Refuses `--cap` structurally (this parser
    defines no such flag — see build_parser()'s `serve_parser`) and refuses
    to start unless explicit host proc/cgroup bind mounts expose the host
    observations run-gate needs. PID and cgroup namespaces remain private
    (RG55-INTERFACE-CONTRACT.md §5).
    """
    if not access.have_host_cgroup_view(access.CGROUP_ROOT):
        _err(
            "serve needs the host cgroup v2 tree explicitly bind-mounted at "
            f"{access.CGROUP_ROOT}; keep the cgroup namespace private "
            "(RG55-INTERFACE-CONTRACT.md §5)"
        )
    if not access.have_host_proc_view(access.PROC_ROOT):
        _err(
            "serve needs the host /proc tree explicitly bind-mounted read-only "
            "and CGPROFILE_PROC_ROOT set to that mount; its PID 1 must resolve "
            "to a different PID namespace from this container. Keep the PID "
            "namespace private (RG55-INTERFACE-CONTRACT.md §5)"
        )
    from lib import serve as serve_mod

    observe_slices = tuple(
        name for name in (s.strip() for s in args.observe_slices.split(",")) if name
    ) if args.observe_slices else ()
    server = serve_mod.SessionServer(
        sessions_dir=args.sessions,
        socket_path=args.socket,
        damon_default=args.damon_default,
        interval=args.interval,
        keep_sessions=args.keep_sessions,
        keep_days=args.keep_days,
        observe_slices=observe_slices,
        max_sessions=args.max_sessions,
    )
    server.serve_forever()
    return 0


def _ctl_request(args: argparse.Namespace) -> Dict[str, Any]:
    """Build the wire request for `args.verb` — RG55-INTERFACE-CONTRACT.md
    §2's own field names, kept close enough to the CLI flag names that no
    translation table is needed to audit one against the other. A malformed
    `--meta` is a client-side argument error (never reaches the socket),
    handled the same way every other bad argument in this file is:
    `_err()`, stderr + exit 2.
    """
    if args.verb == "start":
        try:
            meta = json.loads(args.meta)
        except json.JSONDecodeError as exc:
            _err(f"--meta must be valid JSON: {exc}")
        if not isinstance(meta, dict):
            _err("--meta must be a JSON object")
        return {
            "verb": "start", "target": args.target, "scope": args.scope,
            "token": args.token, "damon": args.damon, "interval": args.interval,
            "meta": meta,
        }
    if args.verb == "status":
        return {"verb": "status", "session": args.session}
    if args.verb == "stop":
        return {"verb": "stop", "session": args.session}
    if args.verb == "report":
        return {"verb": "report", "session": args.session}
    # "version", "host", "gc" — every other verb takes no arguments at all.
    return {"verb": args.verb}


def _ctl_roundtrip(socket_path: str, req: Dict[str, Any], timeout: float = 25.0) -> Any:
    """One request, one line in, one line out, over `socket_path`
    (RG55-INTERFACE-CONTRACT.md §1.1/§1.5's own 25 s client-side budget,
    distinct from run-gate's own `subprocess.run(timeout=)` on the `ctl`
    process itself). Raises `OSError` (connect refused, timeout) or
    `ValueError` (empty/unparsable response) — `cmd_ctl` treats both the
    same way: a daemon fault, contract exit code 3.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data.strip():
        raise ValueError("empty response from daemon")
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed response: {exc}") from exc


def _validate_ctl_response(verb: str, resp: Any) -> None:
    """Validate the complete response shape before the CLI accepts it.

    A socket peer can be reachable and still be an incompatible daemon, a
    proxy, or a stale test double. Treating any JSON object with a truthy
    ``ok`` member as success would print false evidence and can turn a
    protocol mismatch into a successful controller lane. The validator keeps
    contract-major and verb-specific checks in one client-side gate.
    """
    if not isinstance(resp, dict):
        raise ValueError("invalid response: expected a JSON object")
    if type(resp.get("contract")) is not int or resp["contract"] != 1:
        raise ValueError("invalid response: unsupported contract major")
    if type(resp.get("ok")) is not bool:
        raise ValueError("invalid response: missing boolean ok")
    if not resp["ok"]:
        error = resp.get("error")
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), str)
            or not error["code"]
            or not isinstance(error.get("message"), str)
        ):
            raise ValueError("invalid response: error must contain code and message")
        return

    def require(*keys: str) -> None:
        missing = [key for key in keys if key not in resp]
        if missing:
            raise ValueError(f"invalid response: {verb} missing {', '.join(missing)}")

    def object_at(key: str) -> Dict[str, Any]:
        value = resp.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"invalid response: {verb}.{key} must be an object")
        return value

    def string_at(obj: Dict[str, Any], key: str) -> None:
        if not isinstance(obj.get(key), str) or not obj[key]:
            raise ValueError(f"invalid response: {verb}.{key} must be a non-empty string")

    def host_at(key: str) -> None:
        host = object_at(key)
        for required in ("at", "loadavg", "meminfo", "pressure", "slices"):
            if required not in host:
                raise ValueError(f"invalid response: {verb}.{key} missing {required}")
        string_at(host, "at")
        if not isinstance(host["loadavg"], (list, type(None))):
            raise ValueError(f"invalid response: {verb}.{key}.loadavg must be a list or null")
        if not isinstance(host["meminfo"], dict) or not isinstance(host["pressure"], dict):
            raise ValueError(f"invalid response: {verb}.{key} has invalid metrics objects")
        if not isinstance(host["slices"], dict):
            raise ValueError(f"invalid response: {verb}.{key}.slices must be an object")

    def status_entry_at(entry: Dict[str, Any]) -> None:
        for required in ("session", "started_at", "scope", "elapsed_seconds", "target", "meta", "live"):
            if required not in entry:
                raise ValueError(f"invalid response: {verb}.session entry missing {required}")
        string_at(entry, "session")
        string_at(entry, "started_at")
        if entry["scope"] not in ("container", "container-shared"):
            raise ValueError("invalid response: status session has an invalid scope")
        if not isinstance(entry["elapsed_seconds"], (int, float)) or not math.isfinite(entry["elapsed_seconds"]):
            raise ValueError("invalid response: status.elapsed_seconds must be finite")
        if not isinstance(entry["target"], dict) or not isinstance(entry["live"], dict):
            raise ValueError("invalid response: status session has invalid target/live objects")

    if verb == "version":
        require("cgprofile", "daemon")
        if not isinstance(resp["cgprofile"], str) or not resp["cgprofile"]:
            raise ValueError("invalid response: version.cgprofile must be a string")
        daemon = object_at("daemon")
        for key in ("name", "started_at", "damon", "damon_default"):
            string_at(daemon, key)
        for key in ("sessions_live", "max_sessions"):
            if type(daemon.get(key)) is not int or daemon[key] < 0:
                raise ValueError(f"invalid response: version.daemon.{key} must be a non-negative integer")
    elif verb == "start":
        require("session", "reused", "started_at", "scope", "damon", "interval_seconds", "target")
        string_at(resp, "session")
        string_at(resp, "started_at")
        if resp["scope"] not in ("container", "container-shared"):
            raise ValueError("invalid response: start.scope is not a contract scope")
        if (
            resp["damon"] not in ("on", "off")
            and (not isinstance(resp["damon"], str) or not resp["damon"].startswith("unavailable:"))
        ):
            raise ValueError("invalid response: start.damon is not a contract status")
        if (
            type(resp["reused"]) is not bool
            or type(resp["interval_seconds"]) not in (int, float)
            or isinstance(resp["interval_seconds"], bool)
            or not math.isfinite(resp["interval_seconds"])
            or not 0.25 <= resp["interval_seconds"] <= 30.0
        ):
            raise ValueError("invalid response: start has invalid reused or interval_seconds")
        target = object_at("target")
        string_at(target, "container_id")
        string_at(target, "cgroup")
        if type(target.get("pids_at_start")) is not int or target["pids_at_start"] < 0:
            raise ValueError("invalid response: start.target.pids_at_start must be a non-negative integer")
    elif verb == "status":
        require("at", "host")
        string_at(resp, "at")
        host_at("host")
        if "session" in resp:
            entry = object_at("session")
            status_entry_at(entry)
        else:
            sessions = resp.get("sessions")
            if not isinstance(sessions, list) or any(not isinstance(item, dict) for item in sessions):
                raise ValueError("invalid response: status.sessions must be a list of objects")
            for entry in sessions:
                status_entry_at(entry)
    elif verb == "host":
        require("host")
        host_at("host")
    elif verb == "stop":
        require("session", "already_stopped", "summary", "session_dir", "series")
        string_at(resp, "session")
        if (
            type(resp["already_stopped"]) is not bool
            or not isinstance(resp["summary"], dict)
            or resp["summary"].get("schema") != 1
        ):
            raise ValueError("invalid response: stop has invalid summary or already_stopped")
        string_at(resp, "session_dir")
        series = object_at("series")
        for key in ("samples", "events", "host", "manifest", "summary"):
            string_at(series, key)
        if series.get("damon") is not None and not isinstance(series["damon"], str):
            raise ValueError("invalid response: stop.series.damon must be a path or null")
    elif verb == "report":
        require("path")
        string_at(resp, "path")
    elif verb == "gc":
        require("removed", "kept")
        if not isinstance(resp["removed"], list) or any(not isinstance(item, str) for item in resp["removed"]):
            raise ValueError("invalid response: gc.removed must be a list of strings")
        if type(resp["kept"]) is not int or resp["kept"] < 0:
            raise ValueError("invalid response: gc.kept must be a non-negative integer")


def cmd_ctl(args: argparse.Namespace) -> int:
    """Thin socket client. Always prints exactly one JSON document to
    stdout on success (RG55-INTERFACE-CONTRACT.md §1.2); a connection
    failure or a malformed response prints ONE line to stderr instead and
    exits 3 (daemon fault, §1.3) — never a JSON document nobody asked for
    and never a traceback.
    """
    req = _ctl_request(args)
    try:
        resp = _ctl_roundtrip(args.socket, req)
    except (OSError, ValueError) as exc:
        _note(f"ctl {args.verb} could not reach the daemon at {args.socket}: {exc}")
        return 3
    try:
        _validate_ctl_response(args.verb, resp)
    except ValueError as exc:
        _note(f"ctl {args.verb} received an invalid response: {exc}")
        return 3
    print(json.dumps(resp))
    return 0 if resp["ok"] else 2


# ── argument parsing ────────────────────────────────────────────────────────

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", "-t", action="append", default=[],
                        help="what to profile; repeatable. cgroup:/path, slice:NAME, "
                             "container:NAME, label:k=v, pid:N, or self. Options after "
                             "@: follow, nofollow, metrics=mem+io, as=LABEL, role=…")
    parser.add_argument("--observe", "-o", action="append", default=[],
                        help="watch but do not treat as the subject — the production "
                             "container you are checking for collateral damage. Same "
                             "spec syntax as --target; repeatable.")
    parser.add_argument("--follow-children", action="store_true",
                        help="also sample cgroups that appear under a target mid-run "
                             "(gates spawn containers; this catches them)")
    parser.add_argument("--max-depth", type=int, default=4,
                        help="how deep to follow children (default 4)")
    parser.add_argument("--hot-interval", type=float, default=0.25)
    parser.add_argument("--idle-interval", type=float, default=2.0)
    parser.add_argument("--discovery-interval", type=float, default=2.0)
    parser.add_argument("--damon", action="store_true",
                        help="also run DAMON for working-set hot/cold breakdown")
    parser.add_argument("--cap", action="append", default=[], metavar="CG:FILE=VALUE",
                        help="temporarily set a cgroup limit for the run and restore it "
                             "after, e.g. --cap /dev.slice/dev-background.slice:memory.max=2G")
    parser.add_argument("--mode", choices=("auto", "direct", "helper"), default="auto")
    parser.add_argument("--helper-image", default=None,
                        help="image for the privileged helper (default: this container's own)")
    parser.add_argument("--helper-cgroup-parent", default=None,
                        help="where to place the helper container itself. Defaults to "
                             "$CGPROFILE_HELPER_CGROUP_PARENT or "
                             "$CGROUP_PARENT_DEV_INTERACTIVE — never the daemon default, "
                             "which on this host is the tier gate runs are profiled in")
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--start-timeout", type=float, default=90.0)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--html-only", action="store_true")
    parser.add_argument("--md-only", action="store_true")
    parser.add_argument("--plotlyjs", choices=("inline", "directory"), default="inline",
                        help="inline gives one self-contained ~5MiB file; directory "
                             "writes plotly.min.js beside the report instead")


def _add_log_tail_args(parser: argparse.ArgumentParser) -> None:
    """``run``/``attach`` only — this is driver-tier (see lib/logtail.py);
    ``targets``/``doctor`` never start a run, so they have nothing to tail.
    """
    parser.add_argument("--log-tail", action="append", default=[],
                        metavar="container:NAME[@as=LABEL]",
                        help="tail a container's log and turn matching lines into phase "
                             "marks (see --log-match); repeatable")
    parser.add_argument("--log-match", action="append", default=[], metavar="NAME=PATTERN",
                        help="name=<substring>, or name=regex:<python regex>. Append "
                             "@repeat to the name (name@repeat=...) to record every "
                             "occurrence instead of just the first; repeatable")
    parser.add_argument("--log-match-file", action="append", default=[], metavar="PATH",
                        help="a file of name=pattern lines (# comments and blank lines "
                             "skipped), same syntax as --log-match; repeatable, merged "
                             "with any --log-match given")


def build_parser() -> argparse.ArgumentParser:
    parser = CgprofileArgumentParser(
        prog="cgprofile",
        description="Profile a container/cgroup's resource use over time, with "
                    "effective limits, phase marks, and a report at the end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See ATTACH-GUIDE.md for wiring this into a gate.",
    )
    parser.add_argument(
        "--version", action="version", version=f"cgprofile {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run", help="wrap a command and profile it")
    _add_common(run_parser)
    _add_log_tail_args(run_parser)
    run_parser.add_argument("--phase", default=None, help="name for the wrapped command's phase")
    run_parser.add_argument("--settle", type=float, default=10.0,
                            help="keep sampling this long after the command exits, to "
                                 "capture the tail (default 10s; 0 disables)")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    run_parser.set_defaults(func=cmd_run)

    attach_parser = sub.add_parser("attach", help="profile something already running")
    _add_common(attach_parser)
    _add_log_tail_args(attach_parser)
    attach_parser.add_argument("--duration", "-d", type=float, default=None)
    attach_parser.add_argument("--until-file", default=None,
                               help="stop when this path appears")
    attach_parser.set_defaults(func=cmd_attach)

    mark_parser = sub.add_parser("mark", help="record a phase boundary or annotation")
    mark_parser.add_argument("name")
    mark_parser.add_argument("--kind", choices=("phase", "event"), default="phase")
    mark_parser.add_argument("--run-dir", default=None)
    mark_parser.add_argument("--meta", default=None, help="JSON object of extra fields")
    mark_parser.add_argument("--quiet", "-q", action="store_true")
    mark_parser.set_defaults(func=cmd_mark)

    report_parser = sub.add_parser("report", help="render reports for a finished run")
    report_parser.add_argument("--run-dir", default=None)
    report_parser.add_argument("--out-dir", default=DEFAULT_OUT)
    report_parser.add_argument("--html-only", action="store_true")
    report_parser.add_argument("--md-only", action="store_true")
    report_parser.add_argument("--plotlyjs", choices=("inline", "directory"), default="inline")
    report_parser.set_defaults(func=cmd_report)

    targets_parser = sub.add_parser("targets", help="resolve targets and show their limits")
    _add_common(targets_parser)
    targets_parser.set_defaults(func=cmd_targets)

    doctor_parser = sub.add_parser("doctor", help="what can this process reach?")
    doctor_parser.add_argument("--helper-image", default=None)
    doctor_parser.add_argument("--helper-cgroup-parent", default=None)
    doctor_parser.set_defaults(func=cmd_doctor)

    collect_parser = sub.add_parser("_collect", help=argparse.SUPPRESS)
    collect_parser.add_argument("--run-dir", required=True)
    collect_parser.add_argument("--target", action="append", default=[])
    collect_parser.add_argument("--observe", action="append", default=[])
    collect_parser.add_argument("--follow-children", action="store_true")
    collect_parser.add_argument("--max-depth", type=int, default=4)
    collect_parser.add_argument("--hot-interval", type=float, default=0.25)
    collect_parser.add_argument("--idle-interval", type=float, default=2.0)
    collect_parser.add_argument("--discovery-interval", type=float, default=2.0)
    collect_parser.add_argument("--duration", type=float, default=None)
    collect_parser.add_argument("--damon", action="store_true")
    collect_parser.add_argument("--caps", default=None)
    collect_parser.set_defaults(func=cmd_collect)

    serve_parser = sub.add_parser(
        "serve", help="run the profiling daemon (RG55-INTERFACE-CONTRACT.md)"
    )
    # Deliberately NOT `_add_common(serve_parser)` — that is what "refuses
    # --cap" means structurally: this parser has no such flag to refuse at
    # runtime, argparse itself rejects it.
    serve_parser.add_argument("--sessions", default=DEFAULT_CGPROFILE_SESSIONS,
                              help=f"sessions directory (default {DEFAULT_CGPROFILE_SESSIONS})")
    serve_parser.add_argument("--socket", default=DEFAULT_CTL_SOCKET,
                              help=f"control socket path (default {DEFAULT_CTL_SOCKET})")
    serve_parser.add_argument("--damon-default", choices=("on", "off"), default="on",
                              help="DAMON state a `start` with no --damon inherits")
    serve_parser.add_argument("--interval", type=float, default=1.0,
                              help="sampling cadence in seconds, fixed for a session's "
                                   "whole lifetime (default 1.0)")
    serve_parser.add_argument("--keep-sessions", type=int, default=200)
    serve_parser.add_argument("--keep-days", type=int, default=14)
    serve_parser.add_argument("--observe-slices", default="",
                              help="comma-separated extra *.slice names for `ctl host`")
    serve_parser.add_argument("--max-sessions", type=int, default=16)
    serve_parser.set_defaults(func=cmd_serve)

    # RG55-INTERFACE-CONTRACT.md's own examples always place `--json` AFTER
    # the verb (`cgprofile ctl version --json`, `cgprofile ctl host --json`,
    # …) — argparse subparsers only recognize what the SELECTED subparser
    # itself defines once dispatch has happened, so `--json` (and `--socket`,
    # for the same "works wherever you'd expect it" reason) has to be
    # declared on every verb subparser too, not just the parent `ctl`
    # parser. `default=SUPPRESS` on this shared child mixin is load-bearing,
    # not decoration: without it, EVERY verb subparser re-applies its own
    # (identical-looking) default over whatever the parent `ctl` parser's
    # own `--socket`/`--json` already set from a LEADING flag
    # (`ctl --socket X version`), silently discarding it — verified live
    # (parses to the DEFAULT socket, not `X`) before this fix. SUPPRESS
    # means "only touch the namespace if the user actually typed this flag
    # at the child (trailing) position," which is exactly the fallback
    # order wanted: trailing beats leading beats the real default.
    _ctl_common = argparse.ArgumentParser(add_help=False)
    _ctl_common.add_argument("--socket", default=argparse.SUPPRESS)
    _ctl_common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                             help="accepted for run-gate's benefit; ctl always emits "
                                  "exactly one JSON document on stdout regardless")

    ctl_parser = sub.add_parser(
        "ctl", help="talk to a running `serve` daemon over its control socket",
    )
    ctl_parser.add_argument("--socket", default=DEFAULT_CTL_SOCKET)
    ctl_parser.add_argument("--json", action="store_true",
                            help="accepted for run-gate's benefit; ctl always emits "
                                 "exactly one JSON document on stdout regardless")
    ctl_parser.set_defaults(func=cmd_ctl)
    ctl_sub = ctl_parser.add_subparsers(dest="verb", required=True)

    ctl_sub.add_parser("version", help="daemon self-description", parents=[_ctl_common])

    ctl_start = ctl_sub.add_parser(
        "start", help="begin profiling a target", parents=[_ctl_common]
    )
    ctl_start.add_argument("--target", required=True, metavar="containerid:<64 hex>")
    ctl_start.add_argument("--scope", required=True, choices=("container", "container-shared"))
    ctl_start.add_argument("--token", default=None)
    ctl_start.add_argument("--damon", choices=("on", "off"), default=None)
    ctl_start.add_argument("--interval", type=float, default=None)
    ctl_start.add_argument("--meta", required=True, help="JSON object, RG55 contract §2.2")

    ctl_status = ctl_sub.add_parser(
        "status", help="registry + host snapshot", parents=[_ctl_common]
    )
    ctl_status.add_argument("session", nargs="?", default=None)

    ctl_sub.add_parser("host", help="host + slices snapshot", parents=[_ctl_common])

    ctl_stop = ctl_sub.add_parser(
        "stop", help="end a session, return its summary", parents=[_ctl_common]
    )
    ctl_stop.add_argument("session")

    ctl_report = ctl_sub.add_parser(
        "report", help="render the interactive HTML report", parents=[_ctl_common]
    )
    ctl_report.add_argument("session")

    ctl_sub.add_parser("gc", help="run retention now", parents=[_ctl_common])

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    try:
        return args.func(args)
    except access.AccessError as exc:
        _err(str(exc))
    except targets_mod.TargetError as exc:
        _err(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
