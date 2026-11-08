#!/usr/bin/env python3
"""The collector child shared by ``cgprofile run`` (direct mode) and the daemon.

What remains of the 1.x ``cgprofile`` CLI:

    legacy.py _collect --run-dir DIR --target …    the collector child (direct mode)

The daemon is ``cgprofiled`` (``cgprofile.cli.cgprofiled``) and the user-facing client is
``cgprofilectl`` (``cgprofile.cli.cgprofilectl``); the 1.x ``serve`` and ``report``
subcommands, ``run``/``attach``/``mark``/``targets``/``doctor``/``ctl``, the
helper container, the log tailers, the bash shim and ``setup.sh`` are gone
(2.0.0).

The collector (``_collect``) is standard library only: it must never import
pandas or plotly (DESIGN.md section 1).
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


# ── small helpers ───────────────────────────────────────────────────────────

def _err(message: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    print(f"cgprofile: {message}", file=sys.stderr)
    raise SystemExit(code)


def _note(message: str) -> None:
    print(f"[cgprofile] {message}", file=sys.stderr, flush=True)


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
                # this run directory (``cgprofile mark``, a wrapper boundary — see
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


# ── parser ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgprofile-legacy",
        description="The collector child; the daemon is `cgprofiled`, the user client `cgprofilectl`.",
    )
    parser.add_argument(
        "--version", action="version", version=f"cgprofile {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

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
