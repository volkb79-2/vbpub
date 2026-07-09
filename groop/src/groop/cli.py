from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path

from groop import __version__
from groop.collect.collector import Collector
from groop.config import load
from groop.damon.control import APPROVAL_TEXT, DamonControlError, RootRequired, stop_owned_sessions
from groop.damon.passive import DEFAULT_DAMON_ROOT
from groop.damon.paddr import paddr_confirmation_text, plan_start_paddr_session, start_planned_paddr_session
from groop.model import frame_to_jsonable
from groop.record.live import live_frame_stream
from groop.record.replay import ReplayDriver, format_frame_summary
from groop.record.writer import RecordWriter
from groop.snapshot import inspect_bundle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--once", action="store_true", help="collect one frame and exit")
    parser.add_argument("--record", type=Path, default=None, help="record live frames to JSONL or JSONL.zst")
    parser.add_argument("--replay", type=Path, default=None, help="replay frames from a JSONL or JSONL.zst recording")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    parser.add_argument("--step", action="store_true", help="step through replay without wall-clock pacing")
    parser.add_argument("--json", action="store_true", help="emit JSON for --once")
    parser.add_argument("--pretty-json", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--config", type=Path, default=None, help="load config from PATH instead of the default XDG location")
    parser.add_argument("--profile", type=str, default=None, help="override the active UI column profile for this run")
    parser.add_argument("--cgroup-root", type=Path, default=None, help="cgroup v2 root for live or fixture collection")
    parser.add_argument("--ui-smoke", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def parse_damon_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop damon")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stop_parser = subparsers.add_parser("stop", help="stop groop-owned DAMON sessions")
    stop_parser.add_argument("--all-mine", action="store_true", help="stop every groop-owned DAMON session")
    stop_parser.add_argument("--damon-root", type=Path, default=DEFAULT_DAMON_ROOT, help="DAMON kdamonds sysfs root")
    stop_parser.add_argument("--state-dir", type=Path, default=None, help="groop state dir containing DAMON ownership markers")
    stop_parser.add_argument("--allow-non-root-fixture", action="store_true", help=argparse.SUPPRESS)
    paddr_parser = subparsers.add_parser("paddr", help="manage host paddr DAMON sessions")
    paddr_subparsers = paddr_parser.add_subparsers(dest="paddr_command", required=True)
    paddr_start = paddr_subparsers.add_parser("start", help="start a groop-owned host paddr session")
    paddr_start.add_argument("--damon-root", type=Path, default=DEFAULT_DAMON_ROOT, help="DAMON kdamonds sysfs root")
    paddr_start.add_argument("--state-dir", type=Path, default=None, help="groop state dir containing DAMON ownership markers")
    paddr_start.add_argument("--config", type=Path, default=None, help="load config from PATH instead of the default XDG location")
    paddr_start.add_argument("--confirm", type=str, default=None, help=f"type {APPROVAL_TEXT} to apply sysfs writes")
    paddr_start.add_argument("--allow-non-root-fixture", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def parse_snapshot_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop snapshot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect a groop incident snapshot bundle")
    inspect_parser.add_argument("file", type=Path, help="snapshot .tar or .tar.zst bundle")
    return parser.parse_args(argv)


def _print_frame_json(frame, pretty: bool) -> None:
    payload = frame_to_jsonable(frame)
    print(json.dumps(payload, indent=2 if pretty else None, separators=None if pretty else (",", ":"), sort_keys=True))


def _replay_frame_source(driver: ReplayDriver, *, speed: float, step: bool) -> Iterator:
    for replay_frame in driver.play(speed=speed, step=step):
        yield replay_frame.frame


def _run_ui(
    frame_source,
    *,
    config,
    cgroup_root: Path | None,
    smoke: bool,
    profile: str | None,
    replay_driver: ReplayDriver | None = None,
    replay_step: bool = False,
    replay_speed: float = 1.0,
) -> int:
    try:
        from groop.ui.app import run_ui
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("textual"):
            if smoke:
                print("textual is required for --ui-smoke", file=sys.stderr)
                return 2
            return -1
        raise
    result = run_ui(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        smoke=smoke,
        profile=profile,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
    )
    if isinstance(result, str):
        print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["damon"]:
        return _main_damon(raw_argv[1:])
    if raw_argv[:1] == ["snapshot"]:
        return _main_snapshot(raw_argv[1:])
    args = parse_args(raw_argv)
    config = load(args.config)
    if args.record is not None and args.replay is not None:
        print("choose either --record or --replay", file=sys.stderr)
        return 2
    if args.replay is not None:
        driver = ReplayDriver.from_path(args.replay, config=config)
        ui_code = _run_ui(
            (),
            config=config,
            cgroup_root=args.cgroup_root,
            smoke=args.ui_smoke,
            profile=args.profile,
            replay_driver=driver,
            replay_step=args.step,
            replay_speed=args.speed,
        )
        if ui_code == 0:
            return 0
        for replay_frame in driver.play(speed=args.speed, step=args.step):
            print(format_frame_summary(replay_frame))
        return 0
    if args.record is not None:
        if args.json and not args.once:
            print("--json is supported with --record only when --once is also set", file=sys.stderr)
            return 2
        collector = Collector(cgroup_root=args.cgroup_root, config=config)
        try:
            with RecordWriter(args.record, config=collector.config) as writer:
                stream = live_frame_stream(collector, writer=writer)
                if args.once:
                    frame = next(stream)
                    if args.json:
                        _print_frame_json(frame, args.pretty_json)
                    return 0
                ui_code = _run_ui(
                    stream,
                    config=config,
                    cgroup_root=args.cgroup_root,
                    smoke=args.ui_smoke,
                    profile=args.profile,
                )
                if ui_code == 0:
                    return 0
                print("textual is not installed; use --once --json or install UI dependencies", file=sys.stderr)
                return 2
        except KeyboardInterrupt:
            return 0
    if not args.once and not args.json:
        collector = Collector(cgroup_root=args.cgroup_root, config=config)
        ui_code = _run_ui(
            live_frame_stream(collector),
            config=config,
            cgroup_root=args.cgroup_root,
            smoke=args.ui_smoke,
            profile=args.profile,
        )
        if ui_code == 0:
            return 0
        print("textual is not installed; use --once --json or install UI dependencies", file=sys.stderr)
        return 2
    if not args.once or not args.json:
        print("groop implements --once --json for live collection and --replay for frame playback", file=sys.stderr)
        return 2
    frame = Collector(cgroup_root=args.cgroup_root, config=config).collect_once()
    _print_frame_json(frame, args.pretty_json)
    return 0


def _main_damon(argv: list[str]) -> int:
    args = parse_damon_args(argv)
    if args.command == "stop":
        if not args.all_mine:
            print("groop damon stop requires --all-mine", file=sys.stderr)
            return 2
        try:
            stopped = stop_owned_sessions(
                damon_root=args.damon_root,
                state_dir=args.state_dir,
                all_mine=True,
                require_root=not args.allow_non_root_fixture,
            )
        except RootRequired as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except DamonControlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"stopped {stopped} groop-owned DAMON session(s)")
        return 0
    if args.command == "paddr" and args.paddr_command == "start":
        config = load(args.config)
        try:
            plan = plan_start_paddr_session(
                damon_root=args.damon_root,
                state_dir=args.state_dir,
                config=config.damon,
                require_root=not args.allow_non_root_fixture,
            )
            if args.confirm is None:
                print(paddr_confirmation_text(plan))
                return 2
            session = start_planned_paddr_session(
                plan,
                confirmed_text=args.confirm,
                require_root=not args.allow_non_root_fixture,
            )
        except RootRequired as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except DamonControlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"started groop-owned paddr DAMON session on kdamond {session.kdamond_idx}")
        return 0
    print("unknown damon command", file=sys.stderr)
    return 2


def _main_snapshot(argv: list[str]) -> int:
    args = parse_snapshot_args(argv)
    if args.command == "inspect":
        try:
            print(inspect_bundle(args.file))
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    print("unknown snapshot command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
