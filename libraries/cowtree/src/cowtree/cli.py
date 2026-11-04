"""Command line surface for surveys, detection, garbage collection, and shared trees."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence

from .cache import Cache, shared_roots
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
    "busy": 8,
    "key-missing": 9,
    "tree-invalid": 10,
    "unrepairable": 11,
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
    cache_parser = commands.add_parser("cache", help="inspect and clean the shared keyed-tree cache (MDT_TREE_CACHE_ROOT)")
    cache_commands = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_commands.add_parser("survey", help="list keys, sizes, live leases and idle time").add_argument(
        "--json", action="store_true")
    cache_gc = cache_commands.add_parser("gc", help="remove idle, unleased, unmounted keys and stale scratch")
    cache_gc.add_argument("--idle", type=float, default=3600.0, metavar="SECONDS", help="minimum idle time (default 3600)")
    cache_gc.add_argument("--json", action="store_true")
    cache_verify = cache_commands.add_parser("verify", help="check a key against its manifest")
    cache_verify.add_argument("key")
    cache_verify.add_argument("--shallow", action="store_true", help="only check the COMPLETE marker")
    cache_verify.add_argument("--json", action="store_true")
    return parser


def _cache_command(args) -> int:
    roots = shared_roots()
    if roots is None:
        print("cowtree: INFO: MDT_TREE_CACHE_ROOT is not set; no shared cache on this host", file=sys.stderr)
        _output([] if args.json else "no shared cache", args.json)
        return 0
    cache = Cache(roots.cache_root)
    if args.cache_command == "survey":
        rows = cache.survey()
        if args.json:
            _output(rows, True)
        else:
            for row in rows:
                print(" ".join(f"{name}={value}" for name, value in row.items()))
    elif args.cache_command == "gc":
        items = cache.gc(args.idle)
        if args.json:
            print(json.dumps([item.__dict__ for item in items], sort_keys=True))
        else:
            for item in items:
                print(f"{item.name}: {item.status}{(': ' + item.detail) if item.detail else ''}")
    else:
        document = cache.verify(args.key, deep=not args.shallow)
        _output({"key": args.key, "digest": document["digest"], "ok": True}, args.json)
    return 0


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
        if args.command == "cache":
            return _cache_command(args)
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
