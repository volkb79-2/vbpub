"""``assay`` command line entry point.

Two subcommands ship so far:

* ``assay lanes`` (P01a) — list and validate the declared lanes. **It must not
  execute one.** A-054 governs its output contract: it renders **no verdict
  artifact**. It does not run a lane, so A-027 ("emitted on every outcome")
  does not apply, and A-028 makes emission conditional on an explicit path
  this subcommand has no flag for.
* ``assay run`` (P04) — execute exactly one declared lane's ``argv`` and emit
  its R0 verdict. It never discovers, selects, orders or retries anything
  (§7): the argv is the lane's, plus whatever the CALLER appends after a
  literal ``--`` (A-036) — never derived by assay itself. An append attempted
  without the lane's ``allow_argv_append`` is refused before the process
  starts (A-095, via :mod:`assay.runner`).

  This build evaluates **R0 only**: a lane declaring any rigor beyond
  ``["R0"]`` is refused with ``ERROR``/``BAD_LANE_CONFIG`` before anything
  runs, rather than crashing partway through verdict assembly when
  :class:`~assay.verdict.Verdict` finds a declared rigor level with no
  claim to cover it. R1+ evaluation is a later package's job
  (:mod:`assay.runner`'s ``assemble_verdict`` already accepts additional
  claims — this gate is a CLI-level choice, not a runner limitation).

Both subcommands let the typed error out of :mod:`assay.config`/
:mod:`assay.runner`/:mod:`assay.git` and map it to an exit code — the exit
code *is* the verdict (§6), and stdout is for humans.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__
from . import git, runner
from .config import Lane, LaneFile, find_lane_file, load_lane_file
from .errors import AssayError, Outcome, ReasonCode
from .verdict import Verdict

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

    run = subparsers.add_parser(
        "run",
        help="execute a declared lane's argv and emit its R0 verdict",
        description=(
            "Execute exactly the named lane's declared argv (plus anything "
            "appended after a literal `--`, if the lane permits it) and emit "
            "a verdict. Runs the command once; does not discover, select, "
            "order or retry anything."
        ),
    )
    run.add_argument("lane", help="the lane name to run, as declared in assay.toml")
    run.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "path to assay.toml; by default assay searches upward from the "
            "current directory"
        ),
    )
    run.add_argument(
        "--verdict-json",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "write the verdict atomically to PATH, or '-' for stdout "
            "(A-028); omit to skip artifact emission entirely"
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
    raw = list(sys.argv[1:] if argv is None else argv)
    cli_argv, appended = _split_appended_argv(raw)
    args = build_parser().parse_args(cli_argv)
    try:
        if args.command == "lanes":
            _render_lanes(_resolve_lane_file(args.file), out)
        elif args.command == "run":
            return _cmd_run(args, appended, out)
        else:  # pragma: no cover - argparse rejects unknown subcommands first
            raise AssertionError(f"unhandled command {args.command!r}")
    except AssayError as exc:
        print(f"assay: {exc.outcome}/{exc.reason_code}: {exc}", file=err)
        return exc.exit_code
    return Outcome.PASS.exit_code


def _split_appended_argv(raw: list[str]) -> tuple[list[str], list[str]]:
    """Split *raw* on a literal ``--`` into (CLI tokens, appended argv).

    Mirrors the convention of ``docker run``/``kubectl exec``/``npm run --``:
    everything after the FIRST ``--`` is the caller's payload, verbatim, and
    never reinterpreted by argparse. Without this split, argparse would try
    to parse the appended tokens as assay's own flags.
    """
    if "--" in raw:
        index = raw.index("--")
        return raw[:index], raw[index + 1 :]
    return raw, []


def _resolve_lane_file(path: Path | None) -> LaneFile:
    return load_lane_file(find_lane_file() if path is None else path)


def _cmd_run(args: argparse.Namespace, appended: list[str], out: TextIO) -> int:
    lane_file = _resolve_lane_file(args.file)
    lane: Lane = lane_file.lane(args.lane)
    if lane.rigor != ("R0",):
        raise AssayError(
            f"lane {lane.name!r} declares rigor {list(lane.rigor)}; this "
            f"assay build evaluates R0 only, end to end -- R1+ evaluation "
            f"lands in a later package. Refusing before anything runs rather "
            f"than crashing partway through verdict assembly.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    commit = git.head_rev(lane_file.project_root)
    result = runner.execute_command(lane, argv_append=appended, cwd=lane_file.project_root)
    r0_claim = runner.build_r0_claim(result)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=(r0_claim,),
        assay_version=__version__,
    )
    if args.verdict_json is not None:
        runner.write_verdict(verdict, args.verdict_json, stdout=out)
    if args.verdict_json != "-":
        _print_run_summary(verdict, out)
    return verdict.exit_code


def _print_run_summary(verdict: Verdict, out: TextIO) -> None:
    label = verdict.outcome.value
    if verdict.reason_code is not None:
        label = f"{label}/{verdict.reason_code.value}"
    print(f"{verdict.lane}: {label} (exit {verdict.exit_code})", file=out)
    print(f"  commit: {verdict.commit}", file=out)
    print(f"  argv: {shlex.join(verdict.argv_effective or ())}", file=out)
    if verdict.argv_modified:
        print(f"    (appended: {shlex.join(verdict.argv_appended or ())})", file=out)


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
