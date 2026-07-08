from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from groop.collect.collector import Collector
from groop.model import frame_to_jsonable
from groop.record.replay import ReplayDriver, format_frame_summary
from groop.record.writer import RecordWriter


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop")
    parser.add_argument("--once", action="store_true", help="collect one frame and exit")
    parser.add_argument("--record", type=Path, default=None, help="record live frames to JSONL or JSONL.zst")
    parser.add_argument("--replay", type=Path, default=None, help="replay frames from a JSONL or JSONL.zst recording")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    parser.add_argument("--step", action="store_true", help="step through replay without wall-clock pacing")
    parser.add_argument("--json", action="store_true", help="emit JSON for --once")
    parser.add_argument("--pretty-json", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--cgroup-root", type=Path, default=None, help="cgroup v2 root for live or fixture collection")
    return parser.parse_args(argv)


def _print_frame_json(frame, pretty: bool) -> None:
    payload = frame_to_jsonable(frame)
    print(json.dumps(payload, indent=2 if pretty else None, separators=None if pretty else (",", ":"), sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.record is not None and args.replay is not None:
        print("choose either --record or --replay", file=sys.stderr)
        return 2
    if args.replay is not None:
        driver = ReplayDriver.from_path(args.replay)
        for replay_frame in driver.play(speed=args.speed, step=args.step):
            print(format_frame_summary(replay_frame))
        return 0
    if args.record is not None:
        if args.json and not args.once:
            print("--json is supported with --record only when --once is also set", file=sys.stderr)
            return 2
        collector = Collector(cgroup_root=args.cgroup_root)
        try:
            with RecordWriter(args.record, config=collector.config) as writer:
                while True:
                    started = time.monotonic()
                    frame = collector.collect_once()
                    writer.write_frame(frame)
                    if args.once:
                        if args.json:
                            _print_frame_json(frame, args.pretty_json)
                        return 0
                    elapsed = time.monotonic() - started
                    time.sleep(max(0.0, collector.config.interval - elapsed))
        except KeyboardInterrupt:
            return 0
    if not args.once or not args.json:
        print("groop implements --once --json for live collection and --replay for frame playback", file=sys.stderr)
        return 2
    frame = Collector(cgroup_root=args.cgroup_root).collect_once()
    _print_frame_json(frame, args.pretty_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
