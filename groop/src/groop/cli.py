from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path

from groop import __version__
from groop.collect.collector import Collector
from groop.config import load
from groop.model import frame_to_jsonable
from groop.record.live import live_frame_stream
from groop.record.replay import ReplayDriver, format_frame_summary
from groop.record.writer import RecordWriter


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


def _print_frame_json(frame, pretty: bool) -> None:
    payload = frame_to_jsonable(frame)
    print(json.dumps(payload, indent=2 if pretty else None, separators=None if pretty else (",", ":"), sort_keys=True))


def _replay_frame_source(driver: ReplayDriver, *, speed: float, step: bool) -> Iterator:
    for replay_frame in driver.play(speed=speed, step=step):
        yield replay_frame.frame


def _run_ui(frame_source, *, config, cgroup_root: Path | None, smoke: bool, profile: str | None) -> int:
    try:
        from groop.ui.app import run_ui
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("textual"):
            if smoke:
                print("textual is required for --ui-smoke", file=sys.stderr)
                return 2
            return -1
        raise
    result = run_ui(frame_source, config=config, cgroup_root=cgroup_root, smoke=smoke, profile=profile)
    if isinstance(result, str):
        print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load(args.config)
    if args.record is not None and args.replay is not None:
        print("choose either --record or --replay", file=sys.stderr)
        return 2
    if args.replay is not None:
        driver = ReplayDriver.from_path(args.replay, config=config)
        ui_code = _run_ui(
            _replay_frame_source(driver, speed=args.speed, step=args.step),
            config=config,
            cgroup_root=args.cgroup_root,
            smoke=args.ui_smoke,
            profile=args.profile,
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


if __name__ == "__main__":
    raise SystemExit(main())
