"""``assay`` command line entry point.

Three subcommands ship so far:

* ``assay lanes`` (P01a) — list and validate the declared lanes. **It must not
  execute one.** A-054 governs its output contract: it renders **no verdict
  artifact**. It does not run a lane, so A-027 ("emitted on every outcome")
  does not apply, and A-028 makes emission conditional on an explicit path
  this subcommand has no flag for.
* ``assay run`` (P04, R1 wiring P17) — execute exactly one declared lane's
  ``argv`` and emit its verdict. It never discovers, selects, orders or
  retries anything (§7): the argv is the lane's, plus whatever the CALLER
  appends after a literal ``--`` (A-036) — never derived by assay itself. An
  append attempted without the lane's ``allow_argv_append`` is refused before
  the process starts (A-095, via :mod:`assay.runner`).

  This build evaluates **R0 and R1**, for Python only: ``_built_in_registry``
  is the CLI's own closed capability declaration (work item 2) — Python is
  registered at R1 and nothing else, so a lane declaring ``judge.language``
  as anything but ``"python"``, or R1 for a language this registry does not
  know, is refused (``ERROR``/``BAD_LANE_CONFIG``) before the lane's command
  ever runs. A lane declaring R2 or R3 is refused the same way it always
  was: :func:`assay.runner.run_lane`'s call into
  :func:`assay.runner.assemble_verdict` finds a declared rigor level with no
  claim to cover it. That refusal lives in ``runner.py``, not here — that
  module is in every later producer package's ``scope.touch``, so the guard
  self-obsoletes as R2/R3 evaluation lands instead of needing a CLI-level
  edit this package's successors have no scope to make.
* ``assay verify`` (P14, A-129) — validate a verdict-JSON artifact
  independently of how it was produced. See :mod:`assay.verify` for the full
  contract; this module only wires its parser/dispatch in, exactly like the
  other two subcommands.

All three subcommands let the typed error out of :mod:`assay.config`/
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
from . import git, registry, runner
from .adapters.python import PythonAdapter
from .config import Lane, LaneFile, find_lane_file, load_lane_file
from .errors import AssayError, Outcome
from .verdict import Verdict
from .verify import build_verify_parser, cmd_verify

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
        help="execute a declared lane's argv and emit its verdict",
        description=(
            "Execute exactly the named lane's declared argv (plus anything "
            "appended after a literal `--`, if the lane permits it) and emit "
            "a verdict. Runs the command once; does not discover, select, "
            "order or retry anything. This build evaluates R0 and Python R1."
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

    build_verify_parser(subparsers)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return the process exit code."""
    inp = sys.stdin if stdin is None else stdin
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
        elif args.command == "verify":
            return cmd_verify(args.path, stdin=inp, stderr=err)
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


def _built_in_registry() -> registry.Registry:
    """This CLI's own closed capability declaration (P17, work item 2): a
    fresh :class:`~assay.registry.Registry`, built on every call rather than
    once at import time -- an adapter carries no state a test could leak
    between calls (AUTHORING.md §3b.B), so there is nothing a shared,
    module-level instance would buy beyond a mutable global to guard.

    Python is registered at R1 and nothing else: R2/R3 land in P18/P19, and
    Go (``adapters/go.py`` ships, DESIGN-GUIDE §10/§11's fixture-based
    proof) has no producer path wired in at any rigor level yet (P22).
    Naming a capability this build does not actually reach is exactly the
    failure the whole v1.1 repair series exists to remove one level up
    (the post-series review's own finding 1) -- this is that discipline
    applied to the registry itself.
    """
    return registry.new_registry(
        registry.RegistryEntry(adapter=PythonAdapter(), rigor=frozenset({"R1"})),
    )


def _cmd_run(args: argparse.Namespace, appended: list[str], out: TextIO) -> int:
    lane_file = _resolve_lane_file(args.file)
    lane: Lane = lane_file.lane(args.lane)
    commit = git.head_rev(lane_file.project_root)
    adapter = None
    if "R1" in lane.rigor:
        adapter = registry.get_adapter(_built_in_registry(), lane.judge.language, "R1")
    verdict = runner.run_lane(
        lane,
        commit=commit,
        repo=lane_file.project_root,
        project_root=lane_file.project_root,
        adapter=adapter,
        assay_version=__version__,
        argv_append=appended,
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
