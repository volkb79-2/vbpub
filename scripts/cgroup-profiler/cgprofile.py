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
import os
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

from lib import access, targets as targets_mod, util  # noqa: E402

READY_FILE = "collector.ready"
STOP_FILE = "collector.stop"
DONE_FILE = "collector.done"
DEFAULT_OUT = os.path.join(HERE, "runs")


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

def _predigest_specs(specs: Sequence[str]) -> List[str]:
    """Turn ``container:``/``label:`` specs into ``containerid:`` ones.

    Name lookup needs the Docker socket, which only this side has; cgroup
    lookup needs the host tree, which only the collector side has. Splitting
    the two here means the collector never talks to the daemon — so a daemon
    restart mid-run cannot take the profiler down with it.
    """
    out: List[str] = []
    for spec in specs:
        scheme, rest = targets_mod._split_spec(spec)
        value, options = targets_mod._parse_options(rest)
        suffix = ("@" + ",".join(f"{k}={v}" for k, v in options.items())) if options else ""
        try:
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

            def on_sample(record):
                nonlocal prev, prev_mono
                run.append("samples", record)
                if prev is not None:
                    dt = record["mono"] - prev_mono
                    for event in detector.observe(prev, record, dt):
                        run.append("events", event.to_dict())
                prev, prev_mono = record, record["mono"]
                if damon_session and record["seq"] % 20 == 0:
                    for region in damon_session.collect():
                        run.append("damon", {"mono": record["mono"], **region})

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

def _launch_helper(run_path: str, collect_args: List[str],
                   image: Optional[str],
                   cgroup_parent: Optional[str] = None) -> subprocess.Popen:
    spec = access.build_helper_spec(HERE, os.path.dirname(run_path), image, cgroup_parent)
    docker_args = spec.docker_args()
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
    _note(f"starting helper container ({spec.image.split(':')[0][:48]})")
    return subprocess.Popen(command, stdout=sys.stderr, stderr=sys.stderr)


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


# ── driver commands ─────────────────────────────────────────────────────────

def _start_run(args: argparse.Namespace):
    store = _load_store()
    base = os.path.realpath(args.out_dir)
    os.makedirs(base, exist_ok=True)
    run = store.RunDir(base, args.run_id or store.new_run_id(), create=True)

    specs = _tag_role(_predigest_specs(args.target), "subject")
    observers = _tag_role(_predigest_specs(args.observe or []), "observer")
    if not specs:
        _err("no --target given (try --target dev-background.slice, or `self`)")

    mode = access.choose_mode(args.mode)
    collect_args = _collect_args_from(args, run.path, specs, observers)

    if mode == "direct":
        child = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "cgprofile.py"), "_collect", *collect_args],
            stdout=sys.stderr, stderr=sys.stderr,
        )
    else:
        child = _launch_helper(run.path, collect_args, args.helper_image,
                               getattr(args, "helper_cgroup_parent", None))

    _wait_for(os.path.join(run.path, READY_FILE), args.start_timeout, "the collector to start")
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
    label = args.phase or os.path.basename(args.command[0])
    phases_mod.emit_mark(run.path, label, kind="phase",
                         meta={"argv": args.command}, source="wrapper")
    _note(f"running: {' '.join(args.command)}")

    env = dict(os.environ)
    env["CGPROFILE_RUN_DIR"] = run.path
    env["CGPROFILE_MARK"] = os.path.join(HERE, "cgprofile")
    started = time.time()
    result = subprocess.run(args.command, env=env, check=False)
    elapsed = time.time() - started

    phases_mod.emit_mark(run.path, f"{label}:done", kind="event",
                         meta={"exit_code": result.returncode,
                               "elapsed_s": round(elapsed, 3)}, source="wrapper")
    if args.settle > 0:
        phases_mod.emit_mark(run.path, "settle", kind="phase", source="wrapper")
        _note(f"settling for {args.settle:.0f}s to capture the tail")
        time.sleep(args.settle)

    _stop_run(run, child)
    _note(f"command exited {result.returncode} after {elapsed:.1f}s")
    if not args.no_report:
        _report(run.path, args)
    return result.returncode


def cmd_attach(args: argparse.Namespace) -> int:
    """Attach mode: watch something already running."""
    run, child = _start_run(args)
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
        specs = _tag_role(_predigest_specs(args.target), "subject")
        spec = access.build_helper_spec(HERE, HERE, args.helper_image,
                                       getattr(args, "helper_cgroup_parent", None))
        command = [access.docker_bin(), *spec.docker_args(), "python3",
                   os.path.join(HERE, "cgprofile.py"), "targets", "--mode", "direct"]
        for item in specs:
            command += ["--target", item]
        return subprocess.run(command, check=False).returncode

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgprofile",
        description="Profile a container/cgroup's resource use over time, with "
                    "effective limits, phase marks, and a report at the end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See ATTACH-GUIDE.md for wiring this into a gate.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run", help="wrap a command and profile it")
    _add_common(run_parser)
    run_parser.add_argument("--phase", default=None, help="name for the wrapped command's phase")
    run_parser.add_argument("--settle", type=float, default=10.0,
                            help="keep sampling this long after the command exits, to "
                                 "capture the tail (default 10s; 0 disables)")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    run_parser.set_defaults(func=cmd_run)

    attach_parser = sub.add_parser("attach", help="profile something already running")
    _add_common(attach_parser)
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
