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

  This build evaluates **R0, R1, R2 and R3**, for Python only (P19 closes
  sol finding 1 in full): ``_built_in_registry`` is the CLI's own closed
  capability declaration (work item 2, widened by every rigor-wiring
  package since) — Python is registered at R1, R2 and R3 and nothing else,
  so a lane declaring ``judge.language`` as anything but ``"python"``, or a
  rigor level for a language this registry does not know (Go, at any
  level — P22), is refused (``ERROR``/``BAD_LANE_CONFIG``) before the
  lane's command ever runs. A declared R3 lane's own canary run happens in
  an independently-owned scratch copy of the consumer's repository
  (:func:`assay.canary.run_isolated_canary`, via
  :func:`assay.runner.run_lane`) — the consumer's real worktree is never
  staged, committed, or written to.

  :func:`assay.runner.assemble_verdict`'s own "a declared rigor level has
  no claim" guard stays where it is as the library-level backstop for a
  caller that is not this CLI; it is simply no longer the thing a real
  ``assay run`` reaches first.
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
from .adapters.base import LanguageAdapter
from .adapters.python import PythonAdapter
from .config import Lane, LaneFile, find_lane_file, load_lane_file
from .errors import AssayError, Outcome
from .output import VerdictOutput, reserve_verdict_output
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
            "order or retry anything. This build evaluates R0, Python R1, "
            "Python R2 and Python R3."
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
            return _cmd_run(args, appended, out, err)
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
    """This CLI's own closed capability declaration (P17 work item 2,
    widened P18): a fresh :class:`~assay.registry.Registry`, built on
    every call rather than once at import time -- an adapter carries no
    state a test could leak between calls (AUTHORING.md §3b.B), so there
    is nothing a shared, module-level instance would buy beyond a mutable
    global to guard.

    Python is registered at R1, R2 AND R3 and nothing else: adding ``"R3"``
    to this ONE existing entry's ``rigor`` set is the whole registry change
    a Python R3 CLI pipeline needs (P18's own carried-in note, one level
    further) -- Go (``adapters/go.py`` ships, DESIGN-GUIDE §10/§11's
    fixture-based proof) has no producer path wired in at any rigor level
    yet (P22). Naming a capability this build does not actually reach is
    exactly the failure the whole v1.1 repair series exists to remove one
    level up (the post-series review's own finding 1) -- this is that
    discipline applied to the registry itself.
    """
    return registry.new_registry(
        registry.RegistryEntry(
            adapter=PythonAdapter(), rigor=frozenset({"R1", "R2", "R3"})
        ),
    )


#: The rigor levels THIS module resolves an adapter for, in the order tried
#: (P18, widened P19): the FIRST one a lane declares wins the lookup below,
#: but since `_built_in_registry`'s single entry per language returns the
#: identical adapter OBJECT for any of the three, which one wins is never
#: observable -- this exists only to give the tail lookup a level string to
#: pass.
_ADAPTER_BEARING_LEVELS: tuple[str, ...] = ("R1", "R2", "R3")


def _resolve_declared_adapters(lane: Lane) -> LanguageAdapter | None:
    """Check EVERY declared rigor level above R0 against this build's own
    registry, and return the adapter :func:`assay.runner.run_lane` needs
    for whichever of R1/R2 the lane declares (``None`` when neither is
    declared).

    Work item 2's "reject declared rigor above that entry's capability"
    (A-139). Checking only the literal levels this build reaches -- as
    this function's first version did for ``"R1"`` alone -- left the
    registry gate DEAD for the levels it exists to guard: a lane declaring
    ``rigor = ["R0", "R3"]`` never consulted the registry at all, so its
    command ran to completion and only THEN did
    :func:`assay.runner.assemble_verdict` refuse it for a missing R3
    claim, with the side effects already committed and no artifact
    emitted. The loop is over ``lane.rigor`` itself so a level this build
    cannot reach is refused BEFORE anything executes, whichever level it
    is.

    ``R0`` is skipped, not looked up: it needs no adapter, and
    :class:`~assay.registry.RegistryEntry` refuses to name it for exactly
    that reason.

    The adapter itself is fetched by a SECOND, explicit lookup rather than
    captured inside the loop -- capturing it there would need branching on
    which level resolved successfully, which the loop's own job (refuse or
    continue) has no other reason to do. R1 is tried before R2 before R3 in
    :data:`_ADAPTER_BEARING_LEVELS` merely for a deterministic, stable
    choice when a lane declares more than one; :func:`~assay.registry.
    get_adapter` returns the SAME adapter object regardless (one entry per
    language, not per rigor level), so this ordering is never itself
    observable.
    """
    built_in = _built_in_registry()
    for level in lane.rigor:
        if level != "R0":
            registry.get_adapter(built_in, lane.judge.language, level)
    for level in _ADAPTER_BEARING_LEVELS:
        if level in lane.rigor:
            return registry.get_adapter(built_in, lane.judge.language, level)
    return None


def _cmd_run(
    args: argparse.Namespace, appended: list[str], out: TextIO, err: TextIO
) -> int:
    lane_file = _resolve_lane_file(args.file)
    lane: Lane = lane_file.lane(args.lane)
    # P21 work item 8 / A-181. The order is the contract:
    #
    #   lane config -> OUTPUT RESERVATION -> HEAD -> adapter -> command
    #
    # Lane-config failure stays earliest (a lane that will not load has no
    # destination to reserve). Everything AFTER the reservation is consumer
    # work, and a requested artifact that physically cannot exist must not be
    # discovered only once the lane's command has already run -- which is
    # what `--verdict-json <unwritable>` did before this package: a bare
    # `OSError` and exit 1, i.e. a tooling failure a consumer reads as FAIL,
    # with the side effects already committed (A-O14).
    #
    # `None` is A-028's deliberate no-artifact mode and reserves nothing:
    # the exit code alone still gates correctly, so a caller that never asked
    # for a file is never refused on account of one.
    destination: VerdictOutput | None = None
    if args.verdict_json is not None:
        destination = reserve_verdict_output(args.verdict_json, stdout=out)
    try:
        return _run_reserved(args, lane, lane_file, appended, destination, out, err)
    finally:
        if destination is not None:
            destination.close()


def _run_reserved(
    args: argparse.Namespace,
    lane: Lane,
    lane_file: LaneFile,
    appended: list[str],
    destination: "VerdictOutput | None",
    out: TextIO,
    err: TextIO,
) -> int:
    commit = git.head_rev(lane_file.project_root)
    try:
        adapter = _resolve_declared_adapters(lane)
    except AssayError as exc:
        # A-139: HEAD is already resolved above, so this is one of work
        # item 3's "later terminal paths" and MUST emit a complete
        # artifact. Letting the typed error reach main()'s handler would
        # give a consumer the right exit code and nothing to read -- the
        # exact shape of un-auditable refusal P17 exists to remove.
        verdict = runner.refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            argv_append=appended,
            assay_version=__version__,
        )
        print(f"assay: {exc.outcome}/{exc.reason_code}: {exc}", file=err)
    else:
        verdict = runner.run_lane(
            lane,
            commit=commit,
            repo=lane_file.project_root,
            project_root=lane_file.project_root,
            adapter=adapter,
            assay_version=__version__,
            argv_append=appended,
        )
    if destination is not None:
        # Exactly once, and the summary is printed only after it succeeded:
        # a run that could not deliver the artifact it was asked for must not
        # also print a line that reads like a completed run (A-181).
        runner.write_verdict(verdict, destination)
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
