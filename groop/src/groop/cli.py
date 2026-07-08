from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from groop.collect.collector import Collector
from groop.model import frame_to_jsonable


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop")
    parser.add_argument("--once", action="store_true", help="collect one frame and exit")
    parser.add_argument("--json", action="store_true", help="emit JSON for --once")
    parser.add_argument("--pretty-json", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--cgroup-root", type=Path, default=None, help="cgroup v2 root for live or fixture collection")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.once or not args.json:
        print("groop v0 implements only --once --json in P1", file=sys.stderr)
        return 2
    frame = Collector(cgroup_root=args.cgroup_root).collect_once()
    payload = frame_to_jsonable(frame)
    print(json.dumps(payload, indent=2 if args.pretty_json else None, separators=None if args.pretty_json else (",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
