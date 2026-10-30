"""Command-line entry point for patchseries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import SeriesError, check, closure, delta, load_vocabulary, parse, render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchseries",
        description="Check, select, document and compare Git patch series.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check_command = commands.add_parser(
        "check", help="validate series metadata and dependencies"
    )
    check_command.add_argument("series", type=Path)
    check_command.add_argument("--vocabulary", type=Path, required=True)

    closure_command = commands.add_parser(
        "closure", help="select Change-Ids and their Requires closure"
    )
    closure_command.add_argument("series", type=Path)
    closure_command.add_argument("change_ids", nargs="+")

    render_command = commands.add_parser(
        "render", help="render capability and role tables"
    )
    render_command.add_argument(
        "--column", action="append", required=True, metavar="LABEL=SERIES_DIR"
    )

    delta_command = commands.add_parser("delta", help="compare series by Change-Id")
    delta_command.add_argument("before", type=Path)
    delta_command.add_argument("after", type=Path)
    return parser


def _columns(values: list[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for value in values:
        if value.count("=") != 1:
            raise SeriesError(f"column must be LABEL=SERIES_DIR: {value!r}")
        label, directory = value.split("=", 1)
        if not label or not directory:
            raise SeriesError(
                f"column label and series directory must not be empty: {value!r}"
            )
        result.append((label, Path(directory)))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            changes = parse(args.series)
            check(changes, load_vocabulary(args.vocabulary))
            sys.stdout.write(f"OK: {len(changes)} changes\n")
        elif args.command == "closure":
            selected = closure(parse(args.series), args.change_ids)
            sys.stdout.write("".join(f"{item.change_id}\n" for item in selected))
        elif args.command == "render":
            columns = _columns(args.column)
            source = [(label, parse(directory)) for label, directory in columns]
            sys.stdout.write(render(source))
        else:
            changes = delta(parse(args.before), parse(args.after))
            sys.stdout.write("| Change-Id | Status | Before | After |\n")
            sys.stdout.write("| --- | --- | --- | --- |\n")
            for item in changes:
                before_subject = item.before.subject if item.before else "—"
                after_subject = item.after.subject if item.after else "—"
                sys.stdout.write(
                    f"| {item.change_id} | {item.status} | {before_subject} | {after_subject} |\n"
                )
    except (OSError, SeriesError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    return 0
