"""Daemon and operator command lines for the iogovmon package."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import ConfigError, effective, load
from . import experiment
from .env import Env
from . import hostcheck
from .episodes import watch
from .report import analyze, status
from . import safeio
from .runner import annotate, build, cleanup, plan_text, preflight, prune_cmd, run_bench_cmd, run_tick


DAEMON_COMMANDS = frozenset({"tick", "bench", "watch", "cleanup"})
CONTROL_COMMANDS = frozenset({
    "plan", "analyze", "status", "preflight", "devices", "annotate", "note",
    "quiet-experiment", "prune", "config",
})
ALL_COMMANDS = DAEMON_COMMANDS | CONTROL_COMMANDS


def make_parser(program: str = "iogovmon", commands: frozenset[str] = ALL_COMMANDS) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=program, description="Recommend-only recorder of long-term virtual-disk capability shifts.")
    p.add_argument("--config", help="TOML file (default $IOGOVMON_CONFIG, then /etc/iogovmon/iogovmon.toml, then built-in defaults)")
    p.add_argument("--allow-root", action="store_true", help="permit running as root (otherwise refused); root also needs a root-owned 0700 scratch dir")
    p.add_argument("--version", action="version", version=f"{program} {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    if "plan" in commands:
        sub.add_parser("plan", help="dry run: print what a tick/bench would do and cost; touches no device and writes nothing")
    if "tick" in commands:
        t = sub.add_parser("tick", help="cheap raw 4 KiB read probes + passive snapshot (timer: every few minutes); gated ticks keep the passive snapshot")
        t.add_argument("--dry-run", action="store_true", help="same as `plan`")
    if "bench" in commands:
        b = sub.add_parser("bench", help="the ~6-hourly mini write bench in the scratch region (no-op unless due)")
        b.add_argument("--dry-run", action="store_true", help="same as `plan`")
        b.add_argument("--force", action="store_true", help="run now even if not due or backing off (gates and kill switch still apply)")
    if "analyze" in commands:
        a = sub.add_parser("analyze", help="offline, read-only (root or the service user): summary, shift detection, text plots over the journal")
        a.add_argument("path", nargs="?", help="journal dir or file (default: the configured journal)")
        a.add_argument("--json", action="store_true")
        a.add_argument("--lane", help="only lanes starting with this name")
        a.add_argument("--no-plots", action="store_true")
    if "status" in commands:
        sub.add_parser("status", help="read-only (root or the service user): kill switch, last runs, budget used today, recognizer state, prevention checks")
    if "preflight" in commands:
        sub.add_parser("preflight", help="check the host tools/packages/kernel facilities the install needs (apt package named per item); exit 1 if any is missing")
    if "devices" in commands:
        sub.add_parser("devices", help="show the disks that would be probed (sysfs lookups only)")
    for name in ("annotate", "note"):
        if name in commands:
            n = sub.add_parser(name, help="annotate the journal ('provider optimise run', 'snapshot taken', ...) or reset the baselines")
            n.add_argument("text")
            n.add_argument("--baseline-reset", action="store_true", help="re-establish every lane's reference from its latest windows")
            n.add_argument("--action", action="store_true", help="mark the text as an operator ACTION (e.g. a setting change) for stopping-distance analysis")
    if "watch" in commands:
        w = sub.add_parser("watch", help="resident sampler: records protected-side distress episodes from io.pressure and memory.pressure (no probe IO)")
        w.add_argument("--max-seconds", type=float, help="stop after this long (default: run until stopped)")
    if "quiet-experiment" in commands:
        q = sub.add_parser("quiet-experiment", help="print the plan for an approved nr_requests/io.max test (executes nothing)")
        q.add_argument("--knob", required=True)
        q.add_argument("--value", required=True)
    if "prune" in commands:
        pr = sub.add_parser("prune", help="delete monthly journal files older than journal_max_age_days (or beyond journal_max_bytes)")
        pr.add_argument("--dry-run", action="store_true")
    if "cleanup" in commands:
        sub.add_parser("cleanup", help="remove scratch files left by a killed run (under the scratch lock)")
    if "config" in commands:
        sub.add_parser("config", help="print the effective configuration")
    return p


def make_daemon_parser() -> argparse.ArgumentParser:
    return make_parser("iogovmond", DAEMON_COMMANDS)


def make_control_parser() -> argparse.ArgumentParser:
    return make_parser("iogovmonctl", CONTROL_COMMANDS)


def main(argv: list[str] | None = None, env: Env | None = None) -> int:
    """Combined developer entry point used by ``python -m iogovmon``."""
    return _main(argv, env, make_parser(), "iogovmon")


def daemon_main(argv: list[str] | None = None, env: Env | None = None) -> int:
    return _main(argv, env, make_daemon_parser(), "iogovmond")


def control_main(argv: list[str] | None = None, env: Env | None = None) -> int:
    return _main(argv, env, make_control_parser(), "iogovmonctl")


def _main(argv: list[str] | None, env: Env | None, parser: argparse.ArgumentParser, program: str) -> int:
    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"{program}: config error: {exc}", file=sys.stderr)
        return 2
    env = env or Env()
    cmd = args.cmd
    if cmd == "config":
        print(json.dumps(effective(cfg), indent=1, sort_keys=True))
        return 0
    if cmd == "preflight":
        text, code = hostcheck.render(hostcheck.run_checks(cfg))
        print(text)
        return code
    if cmd == "analyze":
        print(analyze(cfg, args.path or cfg.journal_dir(), as_json=args.json, lane_filter=args.lane, plots=not args.no_plots))
        return 0
    if cmd == "status":
        print(status(cfg, env.wall))
        return 0
    if cmd == "quiet-experiment":
        try:
            print(experiment.plan(cfg, args.knob, args.value))
        except ValueError as exc:
            print(f"{program}: {exc}", file=sys.stderr)
            return 2
        return 0
    if cmd == "cleanup":
        removed = cleanup(cfg)
        print("removed: " + (", ".join(removed) if removed else "nothing"))
        return 0
    rt = build(cfg, env, allow_root=args.allow_root)
    if cmd == "plan" or (cmd in ("tick", "bench") and args.dry_run):
        print(plan_text(rt))
        return 0
    if cmd == "devices":
        for d in rt.disks:
            print(f"{d.kname}\t{d.node}")
        for note in rt.notes:
            print(f"# {note}", file=sys.stderr)
        return 0
    if cmd == "watch":
        err = preflight(rt)
        if err:
            print(err, file=sys.stderr)
            return 2
        lock = safeio.FileLock(rt.path("watch.lock"))
        if not lock.acquire():
            print("iogovmond watch is already running", file=sys.stderr)
            return 0
        try:
            print(f"episodes recorded: {watch(rt, args.max_seconds)}")
        finally:
            lock.release()
        return 0
    if cmd == "prune":
        removed = prune_cmd(rt, args.dry_run)
        print(("would remove: " if args.dry_run else "removed: ") + (", ".join(removed) if removed else "nothing"))
        return 0
    if cmd in ("note", "annotate"):
        kind = "baseline_reset" if args.baseline_reset else ("action" if args.action else "annotation")
        rec = annotate(rt, args.text, kind)
        if isinstance(rec, int):
            return rec
        print(rec["ts"], rec["kind"])
        return 0
    if cmd == "tick":
        return run_tick(rt)
    if cmd == "bench":
        return run_bench_cmd(rt, force=args.force)
    return 2
