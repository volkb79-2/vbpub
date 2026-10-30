"""Command line surface for surveys, detection, garbage collection, and shared trees."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence

from .detect import detect
from .errors import CowError
from .gc import gc, gc_json, make_shared, survey

_ERROR_EXIT_CODES = {
    "cow": 1,
    "root-invalid": 3,
    "space": 4,
    "path": 5,
    "backend": 6,
    "lease": 7,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cowtree")
    commands = parser.add_subparsers(dest="command", required=True)
    detect_parser = commands.add_parser("detect", help="validate and report the configured CoW area")
    detect_parser.add_argument("--json", action="store_true")
    survey_parser = commands.add_parser("survey", help="report backend, available space, and run inventory")
    survey_parser.add_argument("--json", action="store_true")
    gc_parser = commands.add_parser("gc", help="remove only dead, flock-owned runs")
    gc_parser.add_argument("--scan", metavar="DIR", help="scan a plain-mode run directory")
    gc_parser.add_argument("--json", action="store_true")
    shared_parser = commands.add_parser("make-shared", help="create a persistent shared tree")
    shared_parser.add_argument("name")
    shared_parser.add_argument("--compression", help="btrfs value: zstd, zstd:N, lzo, or none")
    shared_parser.add_argument("--json", action="store_true")
    return parser


def _output(document, as_json: bool) -> None:
    if as_json:
        print(json.dumps(document, sort_keys=True))
    elif isinstance(document, str):
        print(document)
    else:
        for key, value in document.items():
            print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "detect":
            area = detect()
            document = {"backend": "off", "root": None} if area is None else {
                "backend": area.caps.backend,
                "fs_type": area.caps.fs_type,
                "root": area.root,
            }
            _output(document, args.json)
            return 0
        if args.command == "survey":
            area = detect()
            if area is None:
                document = {"backend": "off", "plain_dir": tempfile.gettempdir()}
            else:
                document = survey(area)
            _output(document, args.json)
            return 0
        if args.command == "gc":
            if args.scan:
                scan_dir = os.path.abspath(args.scan)
            else:
                area = detect()
                if area is None:
                    print(
                        "cowtree: INFO: plain-mode runs are not included in default gc; "
                        "use cowtree gc --scan DIR with the directory that contains them",
                        file=sys.stderr,
                    )
                    document = []
                    if args.json:
                        _output(document, True)
                    return 0
                scan_dir = area.runs_dir
            items = gc(scan_dir)
            if args.json:
                print(gc_json(items))
            else:
                for item in items:
                    print(f"{item.name}: {item.status}{(': ' + item.detail) if item.detail else ''}")
            return 0
        if args.command == "make-shared":
            area = detect()
            if area is None:
                raise CowError("make-shared requires a valid MDT_COW_ROOT")
            path = make_shared(area, args.name, compression=args.compression)
            _output({"path": path}, args.json)
            return 0
    except CowError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": exc.code, "message": str(exc)}, sort_keys=True), file=sys.stderr)
        else:
            print(f"cowtree: {exc}", file=sys.stderr)
        return _ERROR_EXIT_CODES.get(exc.code, _ERROR_EXIT_CODES["cow"])
    return 2
