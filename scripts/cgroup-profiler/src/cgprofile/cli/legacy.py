#!/usr/bin/env python3
"""The collector and the daemon entry points shared by cgpd and ``cgpctl run``.

What remains of the 1.x ``cgprofile`` CLI after package 2 (cgpctl):

    legacy.py _collect --run-dir DIR --target …    the collector child (direct mode)
    legacy.py serve    …                           the daemon (``cgpd`` delegates here)
    legacy.py report   --run-dir DIR --html-only   the daemon's report-tier subprocess

The user-facing client is ``cgpctl`` (``cgprofile.cli.cgpctl``); the 1.x
``run``/``attach``/``mark``/``targets``/``doctor``/``ctl`` verbs, the helper
container, the log tailers, the bash shim and ``setup.sh`` are gone. Package 3
(daemon contract-2 verbs) moves ``serve`` out of here and retires this module.

The collector (``_collect``) is standard library only: it must never import
pandas or plotly (DESIGN.md section 1). The daemon's ``report`` verb renders a
finished session by running this file's ``report`` subcommand under the report
tier's interpreter.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import signal
import sys
import time
from typing import Dict, Optional, Sequence

# The checkout root: this file is <root>/src/cgprofile/cli/legacy.py. Only the
# daemon's report subprocess runs this file by path (python3 <path> report), and
# in that case sys.path[0] is this directory, not the source root, so name the
# package's parent explicitly.
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
SRC = os.path.join(HERE, "src")
SELF = os.path.join(SRC, "cgprofile", "cli", "legacy.py")
sys.path.insert(0, SRC)

from cgprofile import access, targets as targets_mod  # noqa: E402
from cgprofile.version import installed_version, runtime_version  # noqa: E402

__version__ = runtime_version(installed_version())

READY_FILE = "collector.ready"
STOP_FILE = "collector.stop"
DONE_FILE = "collector.done"
DEFAULT_OUT = os.path.join(HERE, "runs")

# RG55-INTERFACE-CONTRACT.md §1.8 — kept as literals here (rather than
# imported from lib.serve) so `build_parser()` never has to import that
# module just to read a default; `lib.serve.DEFAULT_SOCKET_PATH` /
# `DEFAULT_SESSIONS_DIR` are the same two values (a test asserts the two
# stay in sync, a drift guard, not a second source of truth).
DEFAULT_CTL_SOCKET = "/run/cgprofile/ctl.sock"
DEFAULT_CGPROFILE_SESSIONS = "/var/lib/cgprofile/sessions"


# ── small helpers ───────────────────────────────────────────────────────────

def _err(message: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    print(f"cgprofile: {message}", file=sys.stderr)
    raise SystemExit(code)


def _note(message: str) -> None:
    print(f"[cgprofile] {message}", file=sys.stderr, flush=True)


def _require_reporting_deps() -> None:
    """Fail with the fix, not with a traceback, when the report tier is missing."""
    try:
        import pandas  # noqa: F401
        import plotly  # noqa: F401
    except ImportError:
        _err(
            "reporting needs pandas and plotly (the report tier's interpreter); "
            "the collected data is safe — re-run `report --run-dir <dir>` once they are installed."
        )


def _limits_snapshot(limits_mod, eff) -> Dict[str, object]:
    """Serialise one cgroup's effective limits into the manifest.

    Both forms are written on purpose. ``described`` is what a human reads at
    the top of the report; ``resolved`` is what ``analyze.make_proposals``
    reasons over, and it has to be structured because "memory.high 6.0G (bound
    by /wings.slice/...)" is not something a proposal generator can compare
    against host RAM. Writing only the prose form left every real run producing
    no proposals at all, silently, because the consumer had nothing it could
    parse.

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


# ── collector side ──────────────────────────────────────────────────────────

def cmd_collect(args: argparse.Namespace) -> int:
    """Sample until the stop sentinel appears. Standard library only.

    This is the entry point the direct-mode driver runs as a child process
    (``python -m cgprofile.cli.legacy _collect``). It must not import pandas,
    plotly, or anything else outside the standard library — see DESIGN.md §1.
    """
    from cgprofile import caps as caps_mod
    from cgprofile import events as events_mod
    from cgprofile import limits as limits_mod
    from cgprofile import metrics as metrics_mod
    from cgprofile import phases as phases_mod
    from cgprofile import sampler as sampler_mod
    from cgprofile import store as store_mod

    run = store_mod.RunDir(os.path.dirname(args.run_dir), os.path.basename(args.run_dir),
                           create=True)
    root = access.CGROUP_ROOT

    resolved = targets_mod.resolve_all(
        args.target, root, default_follow=args.follow_children,
        proc_root=access.PROC_ROOT,
    )
    observers = targets_mod.resolve_all(args.observe, root, default_follow=False,
                                        role="observer", proc_root=access.PROC_ROOT) if args.observe else []
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
            str(t.pid): metrics_mod.sample_proc(t.pid, proc_root=access.PROC_ROOT)
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
        from cgprofile import damon as damon_mod

        if damon_mod.available():
            pids = [t.pid for t in all_targets if t.pid]
            damon_targets = [damon_mod.DamonTarget(kind="vaddr", pid=p, label=str(p))
                             for p in pids] or [damon_mod.DamonTarget(kind="paddr", pid=None,
                                                                     label="physical")]
            try:
                damon_session = damon_mod.DamonSession(damon_targets)
            except damon_mod.DamonSessionError as exc:
                _note(f"DAMON requested but could not start ({exc}) — continuing without it")
        else:
            _note("DAMON requested but unavailable here — continuing without it")

    sampler = sampler_mod.Sampler(membership, config, sample_fn)

    with caps_mod.TempCaps(cap_changes, root) if cap_changes else _nullcontext():
        with ExitStack() as resources:
            damon_resources = ExitStack()
            resources.enter_context(damon_resources)
            if damon_session is not None:
                try:
                    damon_session = damon_resources.enter_context(damon_session)
                except damon_mod.DamonSessionError as exc:
                    _note(f"DAMON requested but could not start ({exc}) — continuing without it")
                    damon_session = None
            open(os.path.join(run.path, READY_FILE), "w").close()
            _note(f"collecting into {run.path}")
            prev = None
            prev_mono = None
            marks_seen = 0

            def on_sample(record):
                nonlocal prev, prev_mono, marks_seen, damon_session
                run.append("samples", record)
                if prev is not None:
                    dt = record["mono"] - prev_mono
                    for event in detector.observe(prev, record, dt):
                        run.append("events", event.to_dict())
                prev, prev_mono = record, record["mono"]
                if damon_session and record["seq"] % 20 == 0:
                    try:
                        regions = damon_session.collect()
                    except Exception as exc:
                        _note(
                            f"DAMON collection failed ({type(exc).__name__}: {exc}) — "
                            "continuing without DAMON"
                        )
                        damon_session = None
                        try:
                            damon_resources.close()
                        except Exception as cleanup_exc:
                            _note(
                                "DAMON cleanup after collection failure also failed "
                                f"({type(cleanup_exc).__name__}: {cleanup_exc}) — "
                                "continuing ordinary profiling"
                            )
                    else:
                        for region in regions:
                            run.append("damon", {"mono": record["mono"], **region})
                # A phase mark can be written by a different process sharing
                # this run directory (``cgpctl mark``, a wrapper boundary — see
                # DESIGN.md §3) — this sampler has no other way to notice one,
                # so poll for new marks each tick and give any that arrived the
                # same next-tick-snaps-to-hot treatment as an event or a
                # topology change (sampler.py §4.4's "any... phase mark" clause).
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


# ── report tier (run by the daemon's `report` verb) ─────────────────────────

def cmd_report(args: argparse.Namespace) -> int:
    """Render a finished run directory's reports (needs pandas and plotly)."""
    _require_reporting_deps()
    from cgprofile import analyze, report_html, report_md
    from cgprofile import store as store_mod

    run_path = args.run_dir
    run = store_mod.RunDir(os.path.dirname(run_path), os.path.basename(run_path), create=False)
    analysis = analyze.build(run)
    wrote = []
    if not args.md_only:
        wrote.append(report_html.render(
            analysis, os.path.join(run_path, "report.html"), plotlyjs=args.plotlyjs))
    if not args.html_only:
        wrote.append(report_md.render(analysis, run_path))
    for path in wrote:
        print(path)
    return 0


# ── daemon ──────────────────────────────────────────────────────────────────

def cmd_serve(args: argparse.Namespace) -> int:
    """Run the profiling daemon. Refuses `--cap` structurally (this parser
    defines no such flag — see build_parser()'s `serve_parser`) and refuses
    to start unless its deployment explicitly selects the host PID namespace
    and host proc/cgroup mounts expose the observations run-gate needs. The
    cgroup namespace remains private (RG55-INTERFACE-CONTRACT.md §5).
    """
    if not access.have_host_cgroup_view(access.CGROUP_ROOT):
        _err(
            "serve needs the host cgroup v2 tree explicitly bind-mounted at "
            f"{access.CGROUP_ROOT}; keep the cgroup namespace private "
            "(RG55-INTERFACE-CONTRACT.md §5)"
        )
    if os.environ.get(access.PID_NAMESPACE_MODE_ENV) != "host":
        _err(
            "serve is the daemon-only host-PID mode: set "
            f"{access.PID_NAMESPACE_MODE_ENV}=host and deploy the daemon with "
            "the host PID namespace; workload, helper, gate, and test "
            "containers must remain private (RG55-INTERFACE-CONTRACT.md §5)"
        )
    if not access.have_host_proc_view(access.PROC_ROOT):
        _err(
            "serve needs the host /proc tree explicitly bind-mounted read-only "
            "and CGPROFILE_PROC_ROOT set to that mount; with "
            f"{access.PID_NAMESPACE_MODE_ENV}=host, PID 1 in that view must "
            "resolve to the daemon's PID namespace "
            "(RG55-INTERFACE-CONTRACT.md §5)"
        )
    from cgprofile import serve as serve_mod

    # Sol B4: Docker/systemd auto-create an uninstalled `cgprofile.slice`
    # with no limits, and the daemon would appear governed while running
    # unbounded. Refuse to serve unless the slice is provably bounded.
    if not serve_mod.daemon_slice_bounded(access.CGROUP_ROOT, access.PROC_ROOT):
        problem = serve_mod.daemon_slice_problem(access.CGROUP_ROOT, access.PROC_ROOT)
        _err(
            f"serve refuses to run: {serve_mod.DAEMON_SLICE_NAME} does not "
            f"provably contain this daemon: {problem}. A missing unit makes "
            "systemd auto-create an implicit slice with no limits (zero, "
            "'max', unreadable and non-numeric memory.max are all refused; "
            "zero is refused because a slice that can hold no memory is a "
            "misconfiguration, not containment). Install infra/cgprofile.slice "
            "on the Docker host (sudo cp infra/cgprofile.slice "
            "/etc/systemd/system/ && sudo systemctl daemon-reload), verify "
            "`systemctl show cgprofile.slice --property=LoadState,MemoryMax`, "
            "then recreate the daemon container (its cgroup parent is chosen "
            "at creation). LoadState itself cannot be read from inside the "
            "container (CIU-150)"
        )
    observe_slices = tuple(
        name for name in (s.strip() for s in args.observe_slices.split(",")) if name
    ) if args.observe_slices else ()
    # A slice name that cannot expand to a cgroup path (`a--b`, `-a`, one
    # with a `/`) is refused at start: `ctl host` expands every observed
    # slice and the gates slice on every call, and a late TargetError there
    # would surface as a daemon fault (ctl exit 3) on every `host`/`status`.
    for name in (*observe_slices, args.gates_slice):
        try:
            targets_mod.slice_to_path(name)
        except targets_mod.TargetError as exc:
            _err(f"--observe-slices/--gates-slice: {exc}")
    # §8.1's socket-carrier uid allowlist. Deliberately environment-only
    # (the compose template passes it through): it is a DEPLOYMENT trust
    # decision, not a per-invocation one, and an operator who narrows access
    # must not have it silently ignored — a malformed value refuses to start
    # rather than falling back to "everyone the socket mode allows".
    try:
        allow_uids = serve_mod.parse_allow_uids(os.environ.get(serve_mod.ALLOW_UIDS_ENV))
    except ValueError as exc:
        _err(str(exc))
    server = serve_mod.SessionServer(
        sessions_dir=args.sessions,
        socket_path=args.socket,
        damon_default=args.damon_default,
        interval=args.interval,
        keep_sessions=args.keep_sessions,
        keep_days=args.keep_days,
        observe_slices=observe_slices,
        max_sessions=args.max_sessions,
        gates_slice_name=args.gates_slice,
        allow_uids=allow_uids,
    )
    try:
        # 0, or 3 when the control socket was lost and could not be rebound
        # (CP-31): non-zero so `restart: unless-stopped` recreates the daemon.
        return server.serve_forever() or 0
    except serve_mod.ControlSocketError as exc:
        # CP-34: a non-socket node or a live peer at the socket path.
        _err(f"serve refuses to start: {exc.strerror}")


# ── parser ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgprofile-legacy",
        description="Collector child, daemon entry and report subprocess; "
                    "the user client is `cgpctl`.",
    )
    parser.add_argument(
        "--version", action="version", version=f"cgprofile {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    report_parser = sub.add_parser("report", help="render reports for a finished run")
    report_parser.add_argument("--run-dir", required=True)
    report_parser.add_argument("--html-only", action="store_true")
    report_parser.add_argument("--md-only", action="store_true")
    report_parser.add_argument("--plotlyjs", choices=("inline", "directory"), default="inline")
    report_parser.set_defaults(func=cmd_report)

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
    # Deliberately no `--cap` flag: argparse itself rejects it, which is what
    # "serve refuses --cap" means structurally.
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
                              help="comma-separated extra *.slice names for `ctl host`; a name "
                                   "that is not a usable slice unit name (empty hierarchy "
                                   "component such as a--b, or a /) refuses to start, exit 2 "
                                   "(since 1.1.0; under a compose restart policy that is a "
                                   "restart loop until the flag is fixed)")
    serve_parser.add_argument("--max-sessions", type=int, default=16)
    serve_parser.add_argument("--gates-slice", default="dev-gates.slice",
                              help="mdt's admission-capacity slice C8 places leaves under "
                                   "(RG55-INTERFACE-CONTRACT.md §8.3/§8.5; default "
                                   "dev-gates.slice); an unusable unit name refuses to start "
                                   "(exit 2), same as --observe-slices")
    serve_parser.set_defaults(func=cmd_serve)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
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
