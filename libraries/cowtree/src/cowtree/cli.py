"""Command line surface for surveys, detection, garbage collection, and shared trees."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence

from .cache import Cache
from .detect import detect
from .errors import CowError
from .gc import gc, gc_json, make_shared, survey
from .provision import Settings, decide

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
    "level-unavailable": 12,
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
    cache_parser = commands.add_parser("cache", help="inspect and clean a shared keyed-tree cache directory")
    cache_commands = cache_parser.add_subparsers(dest="cache_command", required=True)

    def cache_cmd(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = cache_commands.add_parser(name, help=help_text)
        sub.add_argument("--root", required=True, help="the cache directory (explicit; no environment is read)")
        sub.add_argument("--json", action="store_true")
        return sub

    cache_cmd("survey", "list keys, sizes, live leases and idle time")
    cache_gc = cache_cmd("gc", "remove idle, unleased, unmounted keys and stale scratch")
    cache_gc.add_argument("--idle", type=float, default=3600.0, metavar="SECONDS", help="minimum idle time (default 3600)")
    cache_verify = cache_cmd("verify", "check a key against its manifest")
    cache_verify.add_argument("key")
    cache_verify.add_argument("--shallow", action="store_true", help="only check the COMPLETE marker")
    probe_parser = commands.add_parser("probe", help="probe capabilities and report the provisioning level A-D that would be used")
    probe_parser.add_argument("--level", default="auto", help="auto (default) or a pinned level A, B, C, D")
    probe_parser.add_argument("--shared-root")
    probe_parser.add_argument("--upper-root")
    probe_parser.add_argument("--overlay-via", default="docker", choices=("docker", "mount"))
    probe_parser.add_argument("--docker-socket")
    probe_parser.add_argument("--tmpfs-dir")
    probe_parser.add_argument("--clone-dir")
    probe_parser.add_argument("--copy-dir")
    probe_parser.add_argument("--json", action="store_true")
    return parser


def _cache_command(args) -> int:
    cache = Cache(args.root)
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


def _probe_command(args) -> int:
    settings = Settings(
        level=args.level, shared_root=args.shared_root, upper_root=args.upper_root, overlay_via=args.overlay_via,
        docker_socket=args.docker_socket, tmpfs_dir=args.tmpfs_dir, clone_dir=args.clone_dir, copy_dir=args.copy_dir,
    )
    report = decide(settings).report()
    if args.json:
        _output(report, True)
    else:
        print(f"level: {report['level']} (requested {report['requested']})")
        for level, result in report["checks"].items():
            print(f"  {level}: {result}")
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
        if args.command == "probe":
            return _probe_command(args)
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
