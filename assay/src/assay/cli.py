"""``assay`` command line entry point.

P01a ships exactly one subcommand, ``assay lanes``: list and validate the
declared lanes. **It must not execute one** — running a lane is P07's, and a
subcommand that quietly ran the argv while claiming to list it would be the
"implies capability it does not have" failure in the tool that exists to remove
it.

A-054 governs the output contract here: ``assay lanes`` renders **no verdict
artifact**. It does not run a lane, so A-027 ("emitted on every outcome") does
not apply, and A-028 makes emission conditional on an explicit path this
subcommand has no flag for. What it does instead is let the typed error out of
:mod:`assay.config` and map it to an exit code — the exit code *is* the verdict
(§6), and stdout is for humans.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__
from .config import LaneFile, find_lane_file, load_lane_file
from .errors import AssayError, Outcome

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description="assay — judge a change against a project's declared lanes",
    )
    parser.add_argument("--version", action="version", version=f"assay {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lanes = subparsers.add_parser(
        "lanes",
        help="list and validate the lanes declared in assay.toml",
        description=(
            "Load assay.toml, validate every declared lane, and print what was "
            "declared. Runs nothing."
        ),
    )
    lanes.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "path to assay.toml; by default assay searches upward from the "
            "current directory"
        ),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return the process exit code."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    args = build_parser().parse_args(argv)
    try:
        if args.command == "lanes":
            _render_lanes(_resolve_lane_file(args.file), out)
        else:  # pragma: no cover - argparse rejects unknown subcommands first
            raise AssertionError(f"unhandled command {args.command!r}")
    except AssayError as exc:
        print(f"assay: {exc.outcome}/{exc.reason_code}: {exc}", file=err)
        return exc.exit_code
    return Outcome.PASS.exit_code


def _resolve_lane_file(path: Path | None) -> LaneFile:
    return load_lane_file(find_lane_file() if path is None else path)


def _render_lanes(lane_file: LaneFile, out: TextIO) -> None:
    count = len(lane_file.lanes)
    print(
        f"{lane_file.path}: schema_version={lane_file.schema_version}, "
        f"{count} lane{'' if count == 1 else 's'}",
        file=out,
    )
    for name, lane in lane_file.lanes.items():
        judge = lane.judge
        judged = "none" if judge is None else (judge.language or "declared")
        print(
            f"  {name}  scope={lane.scope}  rigor={','.join(lane.rigor)}  "
            f"enforcement={lane.enforcement}  "
            f"budget={lane.budget} ({lane.budget_seconds:g}s)  "
            f"allow_argv_append={str(lane.allow_argv_append).lower()}  "
            f"judge={judged}",
            file=out,
        )
        print(f"    argv: {shlex.join(lane.argv)}", file=out)
        if judge is not None and judge.source_roots is not None:
            print(
                f"    source_roots: {', '.join(judge.source_roots)} "
                f"(relative to {lane_file.project_root})",
                file=out,
            )


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
