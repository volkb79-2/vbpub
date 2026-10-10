"""``assay`` command line entry point.

CLI entry points:

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

  This build evaluates **R0, R1, R2 and R3 for Python, R2 for SQL, R1, R2
  and R3 for JavaScript/TypeScript, and R1 for Go** (P19 closes sol finding
  1 in full for Python; P34/W6 adds SQL at R2 only; B036/B046/B087 wire
  JavaScript at R1/R2/R3; the P27 re-carve adds Go at R1 only, A-394):
  ``_built_in_registry`` is the CLI's own closed capability declaration
  (work item 2, widened by every rigor-wiring package since) — Python is
  registered at R1, R2 and R3, SQL at R2 only, JavaScript at R1 and R2
  (B046, the ingested path only) and R3 (B087, the qualified canary path),
  Go at R1 only (A-394, the P27 re-carve), and nothing else. An unknown
  language, SQL at R1/R3, Go at R2/R3, or a rigor this build does not reach
  is refused with ``ERROR``/``BAD_LANE_CONFIG`` before the lane's command
  runs. This summary follows the registry directly; it previously went
  stale after B046's R2 registration and B087's R3 registration.
  A declared R3 lane's own canary run happens in
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
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import signal
import stat
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from .records import record
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from typing import Any, Callable, Literal, Mapping, Sequence, TextIO, TypedDict

from . import __version__
from . import (
    adjudication,
    attestation,
    diff,
    failure_summary,
    git,
    isolation,
    liveness,
    measurability,
    mutation,
    provenance,
    registry,
    resource_limits,
    runner,
    safeio,
)
from .adapters.base import LanguageAdapter
from .adapters.go import GoAdapter
from .adapters.javascript import JavaScriptAdapter
from .adapters.python import PythonAdapter
from .adapters.sql import SqlAdapter
from .config import (
    MUTATION_BUDGET_PER_CANDIDATE_AUTO,
    MUTATION_BUDGET_PER_CANDIDATE_NONE,
    Lane,
    LaneFile,
    find_lane_file,
    load_lane_file,
    parse_duration,
)


def cli_headline() -> str:
    return f"ASSAY {__version__} — declared-lane judge"


class AssayArgumentParser(argparse.ArgumentParser):
    """Make help, usage and parse failures identify the assay build."""

    def add_subparsers(self, **kwargs):
        """Use the headline-aware parser for every nested command."""
        kwargs.setdefault("parser_class", type(self))
        return super().add_subparsers(**kwargs)

    def format_help(self) -> str:
        return f"{cli_headline()}\n\n{argparse.ArgumentParser.format_help(self)}"

    def format_usage(self) -> str:
        return f"{cli_headline()}\n{argparse.ArgumentParser.format_usage(self)}"

    def error(self, message: str) -> None:
        self._print_message(f"{cli_headline()}\n", sys.stderr)
        self._print_message(argparse.ArgumentParser.format_usage(self), sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(2)
from .errors import EXIT_CODES, AssayError, LaneConfigError, Outcome, ReasonCode
from .output import (
    VerdictOutput,
    reserve_verdict_output,
    resolve_state_directory,
    validate_progress_destination,
)
from .verdict import (
    MUTATION_BUCKETS,
    CampaignBinding,
    Evidence,
    EvidenceDeclaration,
    Verdict,
)
from .vocabulary import MUTATION_OPERATORS, WITHDRAWN_MUTATION_OPERATORS
from .verify import build_verify_parser, cmd_verify

__all__ = ["build_parser", "main", "plan_jobs", "resolve_declared_adapters"]


#: (B028/DA-R13, A-425) The bound on the ONE Git call assay makes *after* a
#: lane's own budget has already expired: the commit label the
#: ``LANE_TIMEOUT`` refusal verdict is written under
#: (:func:`_run_reserved`).
#:
#: **Why a grace at all, rather than the spent deadline or no deadline.** The
#: spent deadline cannot be reused -- it has, by construction, zero left, so
#: passing it would mean no verdict is ever written for the very case
#: ``--verdict-json`` was reserved for. No deadline at all is what A-420
#: shipped, and DA-R13 ruled it out: an unbounded ``git rev-parse`` after the
#: budget is gone contradicts the budget's single purpose -- assay never
#: hangs -- because a repository on a stalled network mount would hang the
#: refusal itself, the one code path whose whole job is to terminate.
#:
#: **Why two seconds.** This is a documented policy constant, not a
#: measurement (DESIGN-GUIDE §5 forbids inventing the latter, not stating the
#: former), and it is the same kind of decision as DA-D2's 2048-byte
#: ``detail`` bound. On a healthy repository ``git rev-parse HEAD`` completes
#: in milliseconds -- it reads one ref and exits -- so two seconds is three
#: orders of magnitude of headroom for a label read: large enough that it can
#: never be confused with "the lane's budget was too small", small enough
#: that "git is unavailable" is answered promptly rather than waited out.
#: Exceeding it is therefore evidence about Git, not about the lane.
LABEL_GRACE_SECONDS = 2.0


def _add_request_base_argument(subparser: argparse.ArgumentParser) -> None:
    """(B019/A-328) ``--request-base``, on both verbs that resolve one.

    Named for its OWNER, not for the value: ``--base`` would read as an
    override of ``judge.base`` and this is not one -- it is the other side of
    a lane's own ``judge.base_source = "request"`` declaration, and supplying
    it to a lane that did not delegate is a refusal, never a precedence
    contest. ``run`` and ``plan`` both take it because ``plan`` performs the
    identical merge-base resolution before discovering candidates, and a plan
    that silently scoped itself differently from the run it predicts would be
    worse than no plan.
    """
    subparser.add_argument(
        "--request-base",
        default=None,
        metavar="REF",
        help=(
            "the comparison base THIS gate request judges against: a ref or "
            "an already-resolved commit, resolved through the same merge-base "
            "contract judge.base uses and recorded in the verdict as "
            "judgment.resolved.base (B019). Required by a lane declaring "
            "judge.base_source = 'request', which requires changed-line "
            "judging but delegates the base identity to its invoker; refused "
            "on any other lane, because one of the two declarations would "
            "then be inert. Its absence on a delegating lane is a refusal, "
            "never a fallback to HEAD."
        ),
    )


def _rejudge_outcome_help() -> str:
    """Build the ``--rejudge-outcome`` bucket text from its owner (B096).

    The import is kept at the construction boundary so the help reads the
    current owner tuple, including when a caller is inspecting a vocabulary
    extension in-process.  ``error`` is deliberately described separately:
    it is a CLI convenience alias for the canonical ``crashed`` bucket, not a
    member of :data:`assay.verdict.MUTATION_BUCKETS`.
    """
    from .verdict import MUTATION_BUCKETS

    return (
        f"One of {', '.join(MUTATION_BUCKETS)}; the CLI-only convenience "
        "alias 'error' is accepted for the canonical 'crashed' bucket; a "
        "union with --rejudge when both are given. Requires --resume."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = AssayArgumentParser(
        prog="assay",
        description="assay — judge a change against a project's declared lanes",
    )
    parser.add_argument("--version", action="version", version=f"assay {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("analyze", help="collect and inspect existing review artifacts")

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
    lanes.add_argument(
        "--json",
        action="store_true",
        help=(
            "write one machine-readable inventory document to stdout instead "
            "of the human-readable listing (B044): every declared lane's "
            "scope/rigor/enforcement, the coverage/mutation/canary shape, "
            "which rigor levels THIS build actually reaches for its "
            "language, and the facts a gate tool needs to preflight an "
            "environment without re-parsing assay.toml itself. Runs nothing, "
            "exactly like the text form; a lane file that fails to load "
            "exits 2 with no JSON on stdout."
        ),
    )

    run = subparsers.add_parser(
        "run",
        help="execute a declared lane's argv and emit its verdict",
        description=(
            "Execute the named lane's declared argv (plus anything appended "
            "after a literal `--`, if the lane permits it) and emit a verdict. "
            "Ordinary runs execute the command once and do not discover, "
            "select, order or retry anything; the explicit non-qualifying "
            "--candidates-file pilot mode selects a bounded native R2 subset "
            "and emits no verdict. This build evaluates R0, Python R1, "
            "Python R2, Python R3, JavaScript R1, JavaScript R2 by evidence "
            "ingestion, JavaScript R3, Go R1, and SQL R2."
        ),
    )
    run.add_argument("lane", help="the lane name to run, as declared in assay.toml")
    run.add_argument(
        "--candidates-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "run a non-qualifying native R2 pilot over the listed candidate IDs; "
            "requires --state-dir and never writes a verdict"
        ),
    )
    run.add_argument(
        "--pilot-jobs",
        default=None,
        metavar="N",
        help="pilot-only worker override (1..8); requires --candidates-file",
    )
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
    run.add_argument("--resume", action="store_true")
    run.add_argument(
        "--cold-witness",
        action="store_true",
        help=(
            "for native Python R2, prove mutation kills with a no-coverage "
            "cold attempt and a trusted pytest receipt"
        ),
    )
    run.add_argument(
        "--r2-manifest",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "write the verified ordered no-coverage R2 collection manifest "
            "atomically to PATH; requires --cold-witness"
        ),
    )
    run.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "for snapshot lanes only, admit unignored dirty paths and record "
            "them in the v15 verdict; project isolation.dirty_ignore paths "
            "are recorded separately. R0 lanes remain strict. This flag is "
            "independent of run-gate's own --allow-dirty policy."
        ),
    )
    run.add_argument("--operators", default=None)
    run.add_argument("--shard", default=None, metavar="INDEX/COUNT")
    run.add_argument(
        "--reuse-from",
        type=Path,
        default=None,
        metavar="VERDICT",
        help=(
            "reuse only current pytest kill witnesses from a verified complete "
            "native v15 verdict; v12-v14 start cold, and every uncertain candidate "
            "runs fully. Cannot be combined with --shard."
        ),
    )
    run.add_argument(
        "--rejudge",
        default=None,
        metavar="ID[,ID...]",
        help=(
            "(B091/D-23) with --resume: drop these mutation candidate "
            "ids' resume records before the store is consulted, so each "
            "re-executes against the current judging suite instead of "
            "replaying its prior verdict. Refused, before any work, if an "
            "id does not match any of this run's own current candidate "
            "identities -- unknown, or the mutant's own source bytes "
            "changed since the id was recorded (B088). Requires --resume."
        ),
    )
    run.add_argument(
        "--rejudge-outcome",
        default=None,
        metavar="BUCKET[,BUCKET...]",
        help=(
            "(B091/D-23) with --resume: the same drop as --rejudge, "
            "selected by a resumed record's own persisted outcome bucket "
            "rather than by explicit id -- e.g. "
            "'hung,budget_exceeded,error' re-executes every previously "
            "hung/budget-exceeded/crashed candidate. "
            f"{_rejudge_outcome_help()}"
        ),
    )
    _add_request_base_argument(run)

    plan = subparsers.add_parser(
        "plan",
        help="report a mutation lane's candidate plan without executing it",
        description=(
            "Discover the named mutation lane's candidates, print total and "
            "grouped counts with deterministic identities, and estimate serial "
            "and wall-clock runtime. Runs no lane command and creates no "
            "mutant snapshots."
        ),
    )
    plan.add_argument("lane", help="the mutation lane name to inspect")
    plan.add_argument("--operators", default=None)
    plan.add_argument(
        "--cold-witness",
        action="store_true",
        help="statically check whether this lane can use native Python R2 cold witnesses",
    )
    plan.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "apply the same snapshot dirty-tree policy as assay run; the plan "
            "does not judge the dirty tree and reports no verdict"
        ),
    )
    plan.add_argument("--shard", default=None, metavar="INDEX/COUNT")
    plan.add_argument(
        "--reuse-from",
        type=Path,
        default=None,
        metavar="VERDICT",
        help=(
            "classify planned candidates against a bounded, verified prior "
            "verdict without executing the lane"
        ),
    )
    _add_request_base_argument(plan)
    plan.add_argument(
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
    run.add_argument(
        "--progress",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "append the R2 mutation progress NDJSON stream to PATH, one "
            "compact JSON object per line, flushed per event (B031/A-320). "
            "Opt-in and consumer-directed, exactly like --verdict-json: "
            "assay never chooses this location itself, and omitting the flag "
            "writes no progress file at all. Point it OUTSIDE the repository "
            "(or at a gitignored path) -- a progress file inside the work "
            "tree makes the next run of the same lane refuse "
            "NO_MEASUREMENT/DIRTY_TREE. (B064) Every rigor tier writes to "
            "it now, not only R2: an R0/R1 lane emits its own phase "
            "boundaries (run, command_started, command_running, "
            "command_finished, coverage_parsed, verdict_written)."
        ),
    )
    run.add_argument(
        "--progress-heartbeat",
        type=str,
        default=None,
        metavar="SECONDS",
        help=(
            "(B064) emit a command_running tick every SECONDS while the "
            f"lane's own command runs. Default "
            f"{runner.PROGRESS_HEARTBEAT_DEFAULT_SECONDS:g}s, floor "
            f"{runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS:g}s (a smaller value "
            "is refused by name, so a misconfigured interval cannot flood "
            "the file). A pure time-based tick: it never reads the child's "
            "output, counts bytes, or knows anything about the tool being "
            "run. No-op without --progress."
        ),
    )
    run.add_argument(
        "--state-dir",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "(B066) keep mutation resume records in PATH instead of "
            "<project-root>/.assay/mutation-state/. Created on demand. A "
            "run whose worktree is ephemeral -- a fresh checkout per run -- "
            "carries its default store away with it, so --resume is inert "
            "there; point this at a durable directory and --resume works "
            "across worktrees. Candidate ids fold the source file's exact "
            "bytes, span, replacement and operator, so a shared store is "
            "safe by construction: a record either matches or is ignored. "
            "A PATH inside the judged tree that git can see is refused "
            "before any work, because it would make the lane's next run "
            "NO_MEASUREMENT/DIRTY_TREE."
        ),
    )
    run.add_argument(
        "--campaign-deadline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "bind this run and its resume records to a persisted campaign "
            "deadline created by `assay campaign init`"
        ),
    )
    run.add_argument(
        "--require-judge-provenance",
        action="store_true",
        help=(
            "refuse, before any work, unless this assay can identify the "
            "build artifact it was installed from and record its sha256 in "
            "the verdict as judge_provenance (B018). Without this flag an "
            "unidentifiable invocation -- a source checkout, an editable "
            "install -- still runs, emits no judge_provenance at all, and "
            "says so on stderr; assay never invents a digest either way. A "
            "gate that binds its evidence to a verified judge binary passes "
            "this flag."
        ),
    )

    campaign = subparsers.add_parser(
        "campaign",
        help="manage persisted mutation-campaign deadlines",
    )
    campaign_actions = campaign.add_subparsers(
        dest="campaign_action", required=True
    )
    campaign_init = campaign_actions.add_parser(
        "init",
        help="create a deadline bound to the current commit and full lane plans",
    )
    campaign_init.add_argument("--campaign", required=True, metavar="NAME")
    campaign_init.add_argument(
        "--lane", action="append", required=True, metavar="LANE"
    )
    expires = campaign_init.add_mutually_exclusive_group(required=True)
    expires.add_argument("--hours", type=float, metavar="H")
    expires.add_argument("--expires-at", metavar="ISO8601Z")
    campaign_init.add_argument("--file", type=Path, default=None, metavar="PATH")
    campaign_init.add_argument(
        "--state-dir", action="append", default=[], metavar="DIR"
    )
    campaign_init.add_argument("--wheel-sha256", default=None, metavar="HEX")
    campaign_init.add_argument("--out", type=Path, default=None, metavar="PATH")

    build_verify_parser(subparsers)

    return parser


def _run_analyze(argv: list[str], stdout: TextIO, stderr: TextIO) -> int:
    """(A-478) The only place the judge names assay_analysis; imported only for `analyze`."""
    from assay_analysis.cli import main as analyze_main

    return analyze_main(argv, stdout=stdout, stderr=stderr)


def _termination_signal_handler(signum: int, frame: object) -> None:
    """Mark termination first, then kill children without raising in signal context."""
    del frame
    try:
        first_request = runner.request_termination()
    except BaseException:
        first_request = True
    if not first_request:
        return
    try:
        liveness.terminate_live_process_groups()
    except BaseException:
        pass
    try:
        os.write(
            2,
            (
                f"assay: termination requested (signal {signum}); "
                "stopping and writing an incomplete verdict\n"
            ).encode("ascii"),
        )
    except BaseException:
        pass


def _install_termination_handlers() -> Callable[[], None]:
    """Install run-only handlers on the main thread and return their restorer."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {signum: signal.getsignal(signum) for signum in signals}
    installed: list[int] = []
    try:
        for signum in signals:
            signal.signal(signum, _termination_signal_handler)
            installed.append(signum)
    except BaseException:
        for signum in reversed(installed):
            signal.signal(signum, previous[signum])
        raise
    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    return restore


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
    # Analysis owns its record-command separator. Lane argv appending belongs
    # to the existing lane commands and must not consume the recorded command.
    if raw[:1] == ["analyze"]:
        return _run_analyze(raw[1:], out, err)
    cli_argv, appended = _split_appended_argv(raw)
    args = build_parser().parse_args(cli_argv)
    try:
        if args.command == "lanes":
            lane_file = _resolve_lane_file(args.file)
            if args.json:
                _render_lanes_json(lane_file, out)
            else:
                _render_lanes(lane_file, out)
        elif args.command == "run":
            restore_handlers = _install_termination_handlers()
            try:
                return _cmd_run(args, appended, out, err)
            finally:
                restore_handlers()
        elif args.command == "plan":
            return _cmd_plan(args, out, err)
        elif args.command == "campaign" and args.campaign_action == "init":
            return _cmd_campaign_init(args, out, err)
        elif args.command == "verify":
            return cmd_verify(args.path, stdin=inp, stderr=err)
        else:
            raise AssertionError(f"unhandled command {args.command!r}")
    except AssayError as exc:
        # (B053/A-409) The same one emitter every internal conversion site
        # now calls -- this print is where its format came from, and keeping
        # a second spelling of it here is exactly how the two would drift.
        runner.announce_refusal(exc, diagnostics=err)
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


_CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_PILOT_RECORD_NAME_RE = re.compile(r"[0-9a-f]{64}\.json\Z")
_PILOT_CANDIDATE_FILE_LIMIT = 1024 * 1024
_PILOT_STATE_LIMIT = 4096


class PilotStateError(LaneConfigError):
    """A pilot state store cannot be used for the requested run."""


def _parse_candidates_file(path: Path, *, max_mutants: int) -> tuple[frozenset[str], str]:
    """Read a bounded pilot selection file and return IDs plus its raw digest."""
    absolute = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))
    try:
        raw = safeio.read_bounded_input(
            absolute.parent,
            absolute.name,
            limit=_PILOT_CANDIDATE_FILE_LIMIT,
        )
    except AssayError as exc:
        raise LaneConfigError(
            f"cannot read --candidates-file {absolute}: {exc}"
        ) from exc
    if raw is None:
        raise LaneConfigError(f"--candidates-file does not exist: {absolute}")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise LaneConfigError(
            f"--candidates-file {absolute} is not valid UTF-8"
        ) from exc
    selected: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if line == "" or line.startswith("#"):
            continue
        if _CANDIDATE_ID_RE.fullmatch(line) is None:
            raise LaneConfigError(
                f"--candidates-file line {number} is not a 64-hex candidate id"
            )
        if line in seen:
            raise LaneConfigError(
                f"--candidates-file candidate id {line} appears more than once"
            )
        seen.add(line)
        selected.append(line)
        if len(selected) > max_mutants:
            raise LaneConfigError(
                f"--candidates-file selects more than judge.mutation.max_mutants ({max_mutants}) candidates"
            )
    if not selected:
        raise LaneConfigError("--candidates-file selects no candidates")
    return frozenset(selected), hashlib.sha256(raw).hexdigest()


def _parse_pilot_jobs(raw: str | None, *, candidates_file: bool) -> int | None:
    if raw is None:
        return None
    if not candidates_file:
        raise LaneConfigError("--pilot-jobs requires --candidates-file")
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise LaneConfigError("--pilot-jobs must be an integer from 1 through 8")
    normalized = raw.lstrip("0")
    if normalized not in {"1", "2", "3", "4", "5", "6", "7", "8"}:
        raise LaneConfigError("--pilot-jobs must be an integer from 1 through 8")
    return int(normalized)


def _read_pilot_state_document(
    root: Path,
    root_fd: int,
    *,
    require_sentinel_for_records: bool,
) -> dict[str, Any] | None:
    try:
        descriptor = os.open(
            "PILOT-STATE",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raw = None
        else:
            raise PilotStateError(f"PILOT-STATE cannot be read safely: {exc}") from exc
    else:
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PilotStateError(
                    "PILOT-STATE is not a single-link regular file"
                )
            if info.st_size > _PILOT_STATE_LIMIT:
                raise PilotStateError(
                    f"PILOT-STATE exceeds the {_PILOT_STATE_LIMIT}-byte limit"
                )
            chunks = bytearray()
            while len(chunks) <= _PILOT_STATE_LIMIT:
                piece = os.read(
                    descriptor,
                    min(64 * 1024, _PILOT_STATE_LIMIT + 1 - len(chunks)),
                )
                if not piece:
                    break
                chunks.extend(piece)
            if len(chunks) > _PILOT_STATE_LIMIT:
                raise PilotStateError(
                    f"PILOT-STATE exceeds the {_PILOT_STATE_LIMIT}-byte limit"
                )
            raw = bytes(chunks)
        except PilotStateError:
            raise
        except OSError as exc:
            raise PilotStateError(f"PILOT-STATE cannot be read safely: {exc}") from exc
        finally:
            os.close(descriptor)
    if raw is None:
        existing_records = sorted(
            name for name in os.listdir(root_fd) if _PILOT_RECORD_NAME_RE.fullmatch(name)
        )
        if require_sentinel_for_records and existing_records:
            raise PilotStateError(
                "PILOT-STATE is absent but the state directory already contains "
                f"mutation records (for example {existing_records[0]!r}); use a new --state-dir"
            )
        return None
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PilotStateError(f"PILOT-STATE is malformed: {exc}") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "selection_sha256", "lane"}
        or document.get("schema") != "assay-pilot-state/1"
        or not isinstance(document.get("selection_sha256"), str)
        or _CANDIDATE_ID_RE.fullmatch(document["selection_sha256"]) is None
        or not isinstance(document.get("lane"), str)
    ):
        raise PilotStateError("PILOT-STATE is malformed")
    return document


def _acquire_state_path_lock(raw_root: Path) -> int:
    """Lock a normalized requested state path in a shared per-user directory.

    The path lock survives replacement of the state directory's parent.
    Directory-inode locks alone split in that case: the old run retains the
    moved inode while a second run can lock the replacement inode at the same
    requested path. The separate inode lock in
    :func:`_acquire_state_directory_lock` still coordinates symlink aliases
    that name the same store.
    """
    lock_parent_fd: int | None = None
    lock_root_fd: int | None = None
    lock_fd: int | None = None
    try:
        # `gettempdir()` honors TMPDIR, which can differ between processes
        # sharing a state path. A path lock in each process's private temp root
        # would split if the requested path's parent were replaced. Use the
        # canonical system temp root so those processes contend on one file.
        temp_root = Path("/tmp").resolve(strict=True)
        lock_parent_fd = os.open(
            temp_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        lock_root_name = f"assay-state-locks-{os.geteuid()}"
        try:
            os.mkdir(lock_root_name, 0o700, dir_fd=lock_parent_fd)
        except FileExistsError:
            pass
        lock_root_fd = os.open(
            lock_root_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=lock_parent_fd,
        )
        root_stat = os.fstat(lock_root_fd)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.geteuid()
            or stat.S_IMODE(root_stat.st_mode) != 0o700
        ):
            raise PilotStateError(
                f"state lock directory {temp_root / lock_root_name} is not a private directory owned by this user"
            )
        lock_name = hashlib.sha256(os.fsencode(raw_root)).hexdigest() + ".lock"
        lock_fd = os.open(
            lock_name,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=lock_root_fd,
        )
        lock_stat = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.geteuid()
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_nlink != 1
        ):
            raise PilotStateError(
                f"state path lock {temp_root / lock_root_name / lock_name} is not a private regular file owned by this user"
            )
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except BlockingIOError as exc:
        if lock_fd is not None:
            os.close(lock_fd)
        raise PilotStateError(
            f"mutation state directory {raw_root} is already in use by another "
            "assay run; retry after it finishes"
        ) from exc
    except PilotStateError:
        if lock_fd is not None:
            os.close(lock_fd)
        raise
    except OSError as exc:
        if lock_fd is not None:
            os.close(lock_fd)
        raise PilotStateError(
            f"cannot lock requested mutation state path {raw_root}: {exc}"
        ) from exc
    finally:
        if lock_root_fd is not None:
            os.close(lock_root_fd)
        if lock_parent_fd is not None:
            os.close(lock_parent_fd)


def _acquire_state_directory_lock(
    state_dir: Path,
) -> tuple[int, tuple[int, ...], Path]:
    """Lock both the requested path and its admitted store inode.

    The stable requested-path lock prevents a concurrent run from entering
    through a replacement parent at the same spelling. The directory inode
    lock coordinates independent path aliases that resolve to one store. The
    returned directory descriptor also anchors every state read and write;
    callers verify that the supplied path still names this inode before
    certifying a result.
    """
    raw_root = Path(os.path.normpath(os.path.abspath(os.fspath(state_dir))))
    path_lock_fd = _acquire_state_path_lock(raw_root)
    root_fd: int | None = None
    try:
        if safeio._is_proc_fd_root(raw_root.parent):
            # A gate may pin its admitted .assay directory and pass a child
            # below /proc/PID/fd/FD. Keep that spelling rooted at the open
            # descriptor instead of resolving it back to a renameable path.
            root = raw_root
            parent_fd = safeio._open_root(raw_root.parent)
            try:
                try:
                    os.mkdir(raw_root.name, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                root_fd = os.open(
                    raw_root.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            finally:
                os.close(parent_fd)
        else:
            raw_root.parent.mkdir(parents=True, exist_ok=True)
            root = raw_root.parent.resolve(strict=True) / raw_root.name
            if root == root.parent:
                raise PilotStateError("mutation state directory must not be a filesystem root")
            root.mkdir(parents=True, exist_ok=True)
            root_fd = os.open(
                root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
    except PilotStateError:
        if root_fd is not None:
            os.close(root_fd)
        os.close(path_lock_fd)
        raise
    except OSError as exc:
        if root_fd is not None:
            os.close(root_fd)
        os.close(path_lock_fd)
        raise PilotStateError(f"cannot open mutation state directory {raw_root}: {exc}") from exc

    assert root_fd is not None

    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return root_fd, (path_lock_fd,), root
    except BlockingIOError as exc:
        os.close(root_fd)
        os.close(path_lock_fd)
        raise PilotStateError(
            f"mutation state directory {root} is already in use by another "
            "assay run; retry after it finishes"
        ) from exc
    except PilotStateError:
        os.close(root_fd)
        os.close(path_lock_fd)
        raise
    except OSError as exc:
        os.close(root_fd)
        os.close(path_lock_fd)
        raise PilotStateError(f"cannot lock mutation state directory {root}: {exc}") from exc
    except BaseException:
        os.close(root_fd)
        os.close(path_lock_fd)
        raise


def _verify_state_directory_identity(
    state_dir: Path,
    root_fd: int,
    *,
    requested_path: Path | None = None,
) -> None:
    """Refuse a result if either the canonical or supplied path moved.

    ``_acquire_state_directory_lock`` opens the canonical store inode. Keep
    checking the original spelling too: a symlinked parent may be retargeted
    while the run continues to hold the old directory descriptor.
    """
    root_stat = os.fstat(root_fd)
    checks = [(state_dir, False)]
    if requested_path is not None and requested_path != state_dir:
        checks.append((requested_path, True))
    for path, follow_parent_alias in checks:
        try:
            path_stat = os.stat(path, follow_symlinks=follow_parent_alias)
        except OSError as exc:
            raise PilotStateError(
                f"mutation state directory {path} changed while locked: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (root_stat.st_dev, root_stat.st_ino)
        ):
            raise PilotStateError(
                f"mutation state directory {path} changed while locked; refusing to certify this run"
            )


def _release_state_directory_lock(root_fd: int, lock_fds: tuple[int, ...]) -> None:
    try:
        fcntl.flock(root_fd, fcntl.LOCK_UN)
    finally:
        try:
            for lock_fd in lock_fds:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
        finally:
            os.close(root_fd)


def _preflight_state_directory(
    state_dir: Path | None,
    *,
    lane: str,
    pilot: bool,
    lock_held: bool = False,
    locked_root_fd: int | None = None,
) -> None:
    """Refuse cross-mode state reuse before progress or lane execution."""
    if state_dir is None:
        return
    root = Path(state_dir)
    owned_lock: tuple[int, tuple[int, ...], Path] | None = None
    owns_root_fd = False
    try:
        if lock_held:
            root_fd = (
                os.dup(locked_root_fd)
                if locked_root_fd is not None
                else os.open(
                    root,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
            )
            owns_root_fd = True
        else:
            owned_lock = _acquire_state_directory_lock(root)
            root_fd, _lock_fds, root = owned_lock
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PilotStateError(f"cannot open pilot state directory {root}: {exc}") from exc
    try:
        document = _read_pilot_state_document(
            root,
            root_fd,
            require_sentinel_for_records=pilot,
        )
        if document is not None:
            if not pilot:
                raise PilotStateError(
                    "PILOT-STATE marks this directory as non-qualifying pilot state; "
                    "use a separate --state-dir for a qualifying run"
                )
            if document["lane"] != lane:
                raise PilotStateError(
                    f"PILOT-STATE belongs to lane {document['lane']!r}, not {lane!r}"
                )
    except OSError as exc:
        raise PilotStateError(
            f"cannot inspect pilot state directory {root}: {exc}"
        ) from exc
    finally:
        if owned_lock is not None:
            _release_state_directory_lock(owned_lock[0], owned_lock[1])
        elif owns_root_fd:
            os.close(root_fd)


def _ensure_pilot_state(
    state_dir: Path,
    *,
    lane: str,
    selection_sha256: str,
    lock_held: bool = False,
    locked_root_fd: int | None = None,
) -> None:
    """Validate or atomically create the pilot-only sentinel in a state root."""
    root = Path(state_dir)
    owned_lock: tuple[int, tuple[int, ...], Path] | None = None
    owns_root_fd = False
    try:
        if lock_held:
            if locked_root_fd is None:
                root.mkdir(parents=True, exist_ok=True)
                root_fd = os.open(
                    root,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
            else:
                root_fd = os.dup(locked_root_fd)
            owns_root_fd = True
        else:
            owned_lock = _acquire_state_directory_lock(root)
            root_fd, _lock_fds, root = owned_lock
    except OSError as exc:
        raise PilotStateError(f"cannot open pilot state directory {root}: {exc}") from exc
    temporary_name: str | None = None
    try:
        document = _read_pilot_state_document(
            root, root_fd, require_sentinel_for_records=True
        )
        if document is not None:
            if document["selection_sha256"] != selection_sha256:
                raise PilotStateError(
                    "PILOT-STATE names a different candidate selection; use a new --state-dir"
                )
            if document["lane"] != lane:
                raise PilotStateError(
                    f"PILOT-STATE belongs to lane {document['lane']!r}, not {lane!r}"
                )
            return

        document_bytes = (
            json.dumps(
                {
                    "schema": "assay-pilot-state/1",
                    "selection_sha256": selection_sha256,
                    "lane": lane,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        temporary_name = f".PILOT-STATE-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
        with os.fdopen(temporary_fd, "wb") as stream:
            stream.write(document_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.stat("PILOT-STATE", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PilotStateError(
                "PILOT-STATE appeared while the pilot state was being created"
            )
        os.replace(
            temporary_name,
            "PILOT-STATE",
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        temporary_name = None
        os.fsync(root_fd)
    except PilotStateError:
        raise
    except OSError as exc:
        raise PilotStateError(f"cannot create PILOT-STATE safely: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=root_fd)
            except FileNotFoundError:
                pass
        if owned_lock is not None:
            _release_state_directory_lock(owned_lock[0], owned_lock[1])
        elif owns_root_fd:
            os.close(root_fd)


def _pilot_has_budget_record(
    state_dir: Path,
    job: mutation.MutantJob,
    *,
    judge_sha256: str | None,
    campaign_deadline_sha256: str | None,
    cold_witness: bool,
    state_root_fd: int | None = None,
) -> bool:
    if judge_sha256 is None or _CANDIDATE_ID_RE.fullmatch(judge_sha256) is None:
        return False
    try:
        record = mutation._load_validated_state_record(
            state_dir,
            job,
            judge=judge_sha256,
            campaign_deadline_sha256=campaign_deadline_sha256,
            cold_witness=cold_witness,
            state_root_fd=state_root_fd,
        )
    except mutation.MutationTerminalEvidenceError:
        # A present malformed terminal record is corruption, not an ordinary
        # unresolved budget candidate. Preserve the state error so the pilot
        # refuses before printing a summary that could hide it.
        raise
    except (AssayError, OSError, TypeError, ValueError):
        return False
    expected_identity = mutation.candidate_identity_fields(job)
    terminal_result = record.get("terminal_result") if isinstance(record, dict) else None
    return (
        isinstance(record, dict)
        and record.get("outcome_bucket") == "budget_exceeded"
        and all(record.get(name) == value for name, value in expected_identity.items())
        and record.get("campaign_deadline_sha256") == campaign_deadline_sha256
        and isinstance(record.get("execution"), dict)
        and "evidence" in record
        and isinstance(terminal_result, Mapping)
        and set(terminal_result) == {"outcome", "reason_code", "returncode"}
        and terminal_result.get("outcome") == Outcome.BUDGET_EXCEEDED.value
        and terminal_result.get("reason_code") == ReasonCode.LANE_TIMEOUT.value
        and terminal_result.get("returncode") is None
    )


def _capture_pilot_judge_identity(
    write: Callable[[dict[str, Any]], None] | None,
    captured: list[str | None],
) -> Callable[[dict[str, Any]], None]:
    """Capture the current sweep digest while preserving any progress sink."""
    def observe(event: dict[str, Any]) -> None:
        if event.get("event") == "candidates":
            digest = event.get("judge_sha256")
            if not isinstance(digest, str) or _CANDIDATE_ID_RE.fullmatch(digest) is None:
                captured[0] = ""
            elif captured[0] is None:
                captured[0] = digest
            elif captured[0] != digest:
                captured[0] = ""
        if write is not None:
            write(event)

    return observe


class _DeferredProgressWriter:
    """Buffer pilot preflight events until its sentinel is accepted."""

    def __init__(
        self,
        stack: ExitStack,
        path: Path,
        *,
        parent_guard: Callable[[int], None] | None = None,
        open_guard: Callable[[int, int], None] | None = None,
    ) -> None:
        self._stack = stack
        self._path = path
        self._parent_guard = parent_guard
        self._open_guard = open_guard
        self._raw_write: Callable[[dict[str, Any]], None] | None = None
        self._pending: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        if self._raw_write is None:
            self._pending.append(dict(event))
            return
        self._raw_write(event)

    def enable(self) -> None:
        if self._raw_write is not None:
            return
        self._raw_write = self._stack.enter_context(
            mutation.progress_writer(
                self._path,
                parent_guard=self._parent_guard,
                open_guard=self._open_guard,
            )
        )
        for event in self._pending:
            self._raw_write(event)
        self._pending.clear()


def _pilot_completed(
    verdict: Verdict,
    selected_ids: frozenset[str],
    *,
    unresolved: list[str],
) -> bool:
    r2_claim = next((claim for claim in verdict.claims if claim.rigor == "R2"), None)
    if r2_claim is None or r2_claim.mutation is None:
        return False
    if r2_claim.reason_code is ReasonCode.LANE_TIMEOUT:
        return False
    bucket_ids = {
        outcome.candidate_id
        for bucket in MUTATION_BUCKETS
        for outcome in getattr(r2_claim.mutation, bucket)
    }
    return bucket_ids == set(selected_ids) and not unresolved


def _build_pilot_summary(
    verdict: Verdict,
    *,
    lane: Lane,
    selected_ids: frozenset[str],
    selection_order: tuple[str, ...],
    selection_sha256: str | None,
    candidates_file_sha256: str,
    state_dir: Path,
    pilot_jobs: dict[str, mutation.MutantJob],
    judge_sha256: str | None,
    campaign_deadline_sha256: str | None,
    cold_witness: bool,
    state_root_fd: int | None,
    completed: bool,
) -> dict[str, Any]:
    claims = {claim.rigor: claim for claim in verdict.claims}
    r2_claim = claims.get("R2")
    r2_mutation = None if r2_claim is None else r2_claim.mutation
    r2_policy = None if verdict.judgment is None else verdict.judgment.r2
    r2_command = (
        None
        if r2_policy is None or r2_policy.r2_command is None
        else r2_policy.r2_command.to_dict()
    )
    bucket_counts = (
        None
        if r2_mutation is None
        else {name: len(getattr(r2_mutation, name)) for name in MUTATION_BUCKETS}
    )
    outcome_rows: dict[str | None, dict[str, Any]] = {}
    if r2_mutation is not None:
        for bucket in MUTATION_BUCKETS:
            for outcome in getattr(r2_mutation, bucket):
                outcome_rows[outcome.candidate_id] = {
                    "id": outcome.candidate_id,
                    "path": outcome.path,
                    "operator": outcome.operator,
                    "bucket": bucket,
                    "execution_mode": outcome.execution.mode,
                }
    candidates = [
        outcome_rows[candidate]
        for candidate in selection_order
        if candidate in outcome_rows
    ]
    unresolved: list[str] = []
    for candidate in selection_order:
        row = outcome_rows.get(candidate)
        if row is None:
            unresolved.append(candidate)
        elif row["bucket"] == "budget_exceeded" and not (
            (job := pilot_jobs.get(candidate)) is not None
            and _pilot_has_budget_record(
                state_dir,
                job,
                judge_sha256=judge_sha256,
                campaign_deadline_sha256=campaign_deadline_sha256,
                cold_witness=cold_witness,
                state_root_fd=state_root_fd,
            )
        ):
            unresolved.append(candidate)
    r2_summary = (
        None
        if r2_mutation is None or r2_claim is None
        else {
            "status": r2_claim.status.value,
            "reason_code": (
                r2_claim.reason_code.value
                if r2_claim.reason_code is not None
                else None
            ),
        }
    )
    summary: dict[str, Any] = {
        "schema": "assay-pilot-summary/2",
        "qualifying": False,
        "completed": completed,
        "lane": lane.name,
        "commit": verdict.commit,
        "jobs": lane.judge.mutation.jobs,
        "requested": len(selected_ids),
        "selection_sha256": selection_sha256,
        # Captured from the current mutation sweep in memory, independently
        # of the persisted progress stream. The external pilot checker binds
        # progress and state records to this identity.
        "judge_sha256": judge_sha256,
        "candidates_file_sha256": candidates_file_sha256,
        "state_dir": str(state_dir),
        "r0": None if "R0" not in claims else claims["R0"].status.value,
        "r1": None if "R1" not in claims else claims["R1"].status.value,
        "r2": r2_summary,
        "r2_command": r2_command,
        "r3": "not-run: pilot",
        "buckets": bucket_counts,
        "candidates": candidates,
        "unresolved": unresolved,
    }
    if r2_mutation is None:
        summary["refusal"] = {
            "status": verdict.outcome.value,
            "reason_code": (
                verdict.reason_code.value if verdict.reason_code is not None else None
            ),
        }
    return summary


def _pilot_exit_code(verdict: Verdict, *, completed: bool, err: TextIO) -> int:
    if completed:
        return 6
    if verdict.exit_code == 0:
        print(
            "assay: pilot: a non-completed selection produced a PASS verdict; reporting ERROR",
            file=err,
        )
        return EXIT_CODES[Outcome.ERROR]
    return verdict.exit_code


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
    further). Naming a capability this build does not actually reach is
    exactly the failure the whole v1.1 repair series exists to remove one
    level up (the post-series review's own finding 1) -- this is that
    discipline applied to the registry itself.

    **The sentence above used to continue "-- Go has no producer path wired
    in at any rigor level yet (P22)". That is no longer true, and A-394 is
    why: ``go`` IS registered, at** ``{"R1"}`` **only.** The paragraph is
    rewritten rather than deleted because the SEQUENCING is the load-bearing
    half of that ruling, and a later reader who sees only the finished entry
    would not recover it. Registering Go was never gated on "someone got
    around to it"; it was gated on a chain that had to land FIRST, because
    :mod:`assay.coverage_parsers.go_cover` used to expand a cover block's
    whole extent into lines and call the result statement truth -- the
    conflation A-217's impossibility proof (two gofmt-clean files, one
    byte-identical profile, different statement lines) rules out
    unconditionally. A ``go`` entry added at ANY earlier point in Wave C
    would have made that reachable through this very function, which is the
    most expensive shape the A-334/A-335 honesty failure takes: a wrong
    verdict a consumer can reach by declaring a supported language. The
    chain, in the order it had to land:
    :attr:`~assay.adapters.base.LanguageAdapter.requires_statement_attribution`,
    the :meth:`~assay.adapters.base.LanguageAdapter.statement_blocks` hook
    (A-397), the :func:`assay.evaluate` refusal that makes the flag bite
    (A-392), and ``external_tools = ("go",)`` (B047 item 2). Only then this
    entry.

    **What this entry does NOT promise, and why that is not a gap.** A Go
    lane needs a real Go toolchain: the statement-position oracle is a Go
    program (A-217 -- a Python re-implementation of ``cmd/cover``'s
    segmentation is explicitly not an acceptable substitute), so an
    environment without ``go`` on PATH gets
    ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` from A-253's preflight in
    :func:`assay.runner.run_lane`, BEFORE the lane's command runs. That is
    the property that makes this entry safe everywhere rather than only
    where a toolchain happens to exist: a Go lane is either audited against
    real statement positions or cleanly refused, and there is no third state
    in which it is silently wrong. This devcontainer and the registered
    gate's own image (``tester-unified``) both have no Go and both take the
    refusal -- see ``gate/tests/qualification/`` for where the real-toolchain
    proof lives instead (DESIGN-GUIDE §10's pattern).

    **R2 and R3 stay unregistered for Go**, which the Wave C prompt's own
    NOT-IN-SCOPE list forbids changing:
    :meth:`~assay.adapters.go.GoAdapter.generate_mutation_sites` is
    unconditionally ``"UNSUPPORTED"``, so an R2 entry would advertise a
    producer path that does not exist -- the failure this docstring's first
    paragraph is about. Both refusals are asserted as controls in
    ``tests/core/test_cli_run.py``
    (``test_run_refuses_go_at_r2_the_language_is_registered_r1_only`` and its
    R3 sibling), alongside the R1 test that now inverts.

    **What those two controls do NOT cover, corrected in the round-1 fix
    round.** This paragraph used to continue that a rigor level "for a
    language this registry does not know at all" was asserted as a control
    there too. It was, by the same two Go tests -- until A-394 registered
    ``go`` and they silently became registered-at-another-rigor tests, so no
    CLI-level test exercised the unknown-language branch at all. The
    adversarial round-1 review found it; one of the two is now
    ``test_run_refuses_a_language_this_registry_does_not_know_at_all``, which
    declares ``rust`` and asserts ``rust`` really is absent from the registry
    so it cannot drift the same way. The unit-level control is
    ``test_registry.py::test_an_unregistered_language_is_refused_not_defaulted``.

    **P34/W6: SQL is registered at R2 ONLY** (A-242's own sentence,
    ``SqlAdapter``'s own module docstring). That single fact is what makes
    ``SqlAdapter.has_executable_code``/``normalize_coverage_key``/
    ``statement_spans``/``inject_import_break``/``inject_uncovered_line``
    provably unreachable through this CLI: none of R0's own path, R1, or
    R3 ever resolves an adapter for a language whose one registry entry
    names only R2, so nothing here ever calls an R0/R1/R3-only method on
    it. Route (i) (§4.1) needs no ``external_tools`` entry, so this is the
    entire wiring change -- no preflight, no new config surface, one more
    entry in this one registry.

    **B036: JavaScript/TypeScript is registered at R1 ONLY**, following
    Python's own first-ship shape rather than SQL's. R2 is not registered
    because no JS/TS mutation engine exists to reach -- whether it should be
    native or should ingest an external producer's evidence is the ruling
    **B037** exists to force (:meth:`~assay.adapters.javascript.
    JavaScriptAdapter.generate_mutation_sites` is unconditionally
    ``"UNSUPPORTED"`` until then). R3 is not registered either: the two
    canary injection methods are real implementations rather than stubs, but
    a producer path is a separate claim from a method existing
    (DESIGN-GUIDE §7), and wiring one is a fast-follow, not part of B036.

    **B046 (schema v9) RESOLVED B037, and ``javascript`` is now registered at
    ``{"R1", "R2"}`` -- through the INGESTED path only.** The paragraph above
    stands as history; what changed is which of its two options was taken.
    Neither: assay ships no JS/TS mutation engine and still does not.
    :meth:`~assay.adapters.javascript.JavaScriptAdapter.generate_mutation_sites`
    is STILL unconditionally ``"UNSUPPORTED"``, and that is not an oversight
    left standing -- it is what makes this the ingested path. The lane's own
    argv runs StrykerJS inside the private snapshot, exactly as it already
    runs Vitest for R1, and assay judges the
    ``mutation-testing-report-schema`` document it wrote.

    **The runner selects native vs ingested by ``judge.mutation.format``'s
    presence, and by nothing else** -- not by the language and not by the
    artifact's content (A-007). So this registry entry says only "a
    ``javascript`` lane may declare R2 at all"; WHICH R2 it gets is the lane
    file's own declaration.

    **Which layer refuses a NATIVE ``javascript`` R2 lane** -- still refused,
    and still by the FIRST of two independent guards:

    1. :mod:`assay.config` at load time. A native R2 lane must declare a
       non-empty ``judge.mutation.operators``, while
       :data:`assay.vocabulary.MUTATION_OPERATORS_BY_LANGUAGE` has no
       ``javascript`` entry at all -- so every operator such a lane could
       spell is FOREIGN to it, and the foreign-operator guard refuses
       ``BAD_LANE_CONFIG`` naming the language. A config-valid NATIVE
       ``javascript`` R2 lane is therefore still not constructible. An
       INGESTED one declares no operators at all (they are forbidden there,
       A-360), so it never meets this guard -- which is precisely why
       registering ``{"R1", "R2"}`` here does not reopen the native path.
    2. :func:`assay.registry.get_adapter` and this entry's own ``rigor``
       frozenset, which since B046 admits R2 and so no longer refuses on this
       axis. The guarantee that a native JS R2 lane cannot run now rests on
       (1) plus ``generate_mutation_sites`` returning ``"UNSUPPORTED"``,
       which :func:`assay.mutation.run_mutation` renders as
       ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED`` -- a stated absence of
       capability, never a PASS.

    **B087 registers JavaScript at R3 through the existing producer path.**
    Real-Vitest fixture oracles exercise both canary mechanisms. Both current
    dstdns canaries passed with retained schema-v15 verdicts and verifier
    transcripts; see ``nyxloom-trove/reports/B087-js-r3-qualification.md`` for
    scope and limits. This consumer evidence is separate from the integrated
    Assay release gate. No verdict or schema change was needed for registration.
    """
    return registry.new_registry(
        registry.RegistryEntry(
            adapter=PythonAdapter(), rigor=frozenset({"R1", "R2", "R3"})
        ),
        registry.RegistryEntry(adapter=SqlAdapter(), rigor=frozenset({"R2"})),
        # (B046) R2 admitted for the INGESTED path only -- see this
        # function's docstring for why that is a property of the lane's own
        # `judge.mutation.format` declaration rather than of this frozenset.
        registry.RegistryEntry(
            adapter=JavaScriptAdapter(), rigor=frozenset({"R1", "R2", "R3"})
        ),
        # (A-394, Wave C) R1 ONLY, and deliberately the LAST thing this wave
        # landed -- see this function's docstring for why the ordering is
        # load-bearing rather than tidy.
        registry.RegistryEntry(adapter=GoAdapter(), rigor=frozenset({"R1"})),
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
    for whichever of R1/R2/R3 the lane declares (``None`` when none is
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


# CD18: public for assay_analysis.
resolve_declared_adapters = _resolve_declared_adapters


_CAMPAIGN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CAMPAIGN_OID_RE = re.compile(r"[0-9a-f]{40}\Z")
_CAMPAIGN_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CAMPAIGN_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_CAMPAIGN_DEADLINE_KEYS = frozenset(
    {
        "schema",
        "campaign",
        "commit",
        "git_tree",
        "lanes",
        "assay_version",
        "wheel_sha256",
        "plan_sha256",
        "created_at_utc",
        "expires_at_utc",
    }
)
_CAMPAIGN_DEADLINE_LIMIT = 64 * 1024


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _campaign_timestamp(raw: Any, *, field: str) -> datetime:
    if not isinstance(raw, str) or _CAMPAIGN_TIME_RE.fullmatch(raw) is None:
        raise LaneConfigError(
            f"campaign deadline {field} must be UTC YYYY-MM-DDTHH:MM:SSZ"
        )
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise LaneConfigError(f"campaign deadline {field} is not a valid UTC time") from exc


def _parse_campaign_deadline(path: Path, *, lane: str) -> tuple[dict[str, Any], bytes, datetime]:
    absolute = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))
    try:
        raw = safeio.read_bounded_input(
            absolute.parent,
            absolute.name,
            limit=_CAMPAIGN_DEADLINE_LIMIT,
        )
    except AssayError as exc:
        raise LaneConfigError(f"cannot trust campaign deadline {absolute}: {exc}") from exc
    if raw is None:
        raise LaneConfigError(f"campaign deadline does not exist: {absolute}")
    return _parse_campaign_deadline_bytes(raw, absolute=absolute, lane=lane)


def _parse_campaign_deadline_bytes(
    raw: bytes, *, absolute: Path, lane: str
) -> tuple[dict[str, Any], bytes, datetime]:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise LaneConfigError(f"campaign deadline {absolute} is not valid unique-key JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != _CAMPAIGN_DEADLINE_KEYS:
        raise LaneConfigError(
            f"campaign deadline {absolute} must contain exactly the declared fields"
        )
    if document["schema"] != "assay-campaign-deadline/1":
        raise LaneConfigError(f"campaign deadline {absolute} has an unknown schema")
    if (
        not isinstance(document["campaign"], str)
        or _CAMPAIGN_NAME_RE.fullmatch(document["campaign"]) is None
    ):
        raise LaneConfigError(f"campaign deadline {absolute} has an invalid campaign name")
    if (
        not isinstance(document["commit"], str)
        or _CAMPAIGN_OID_RE.fullmatch(document["commit"]) is None
    ):
        raise LaneConfigError(f"campaign deadline {absolute} has an invalid commit")
    if (
        not isinstance(document["git_tree"], str)
        or _CAMPAIGN_OID_RE.fullmatch(document["git_tree"]) is None
    ):
        raise LaneConfigError(f"campaign deadline {absolute} has an invalid git_tree")
    lanes = document["lanes"]
    if (
        not isinstance(lanes, list)
        or not lanes
        or any(not isinstance(item, str) or not item for item in lanes)
        or lanes != sorted(set(lanes))
    ):
        raise LaneConfigError(f"campaign deadline {absolute} lanes must be sorted and unique")
    if lane not in lanes:
        raise LaneConfigError(f"campaign deadline {absolute} does not include lane {lane!r}")
    if not isinstance(document["assay_version"], str) or not document["assay_version"]:
        raise LaneConfigError(f"campaign deadline {absolute} has an invalid assay_version")
    wheel_sha256 = document["wheel_sha256"]
    if wheel_sha256 is not None and (
        not isinstance(wheel_sha256, str)
        or _CAMPAIGN_SHA256_RE.fullmatch(wheel_sha256) is None
    ):
        raise LaneConfigError(f"campaign deadline {absolute} has an invalid wheel_sha256")
    plan_sha256 = document["plan_sha256"]
    if not isinstance(plan_sha256, dict) or set(plan_sha256) != set(lanes):
        raise LaneConfigError(
            f"campaign deadline {absolute} plan_sha256 keys must exactly match lanes"
        )
    for lane_name, value in plan_sha256.items():
        if value is not None and (
            not isinstance(value, str) or _CAMPAIGN_SHA256_RE.fullmatch(value) is None
        ):
            raise LaneConfigError(
                f"campaign deadline {absolute} has an invalid plan_sha256 for {lane_name!r}"
            )
    created_at = _campaign_timestamp(document["created_at_utc"], field="created_at_utc")
    expires_at = _campaign_timestamp(document["expires_at_utc"], field="expires_at_utc")
    if expires_at <= created_at:
        raise LaneConfigError(f"campaign deadline {absolute} expires before it was created")
    return document, raw, expires_at


def _campaign_git_identity(lane_file: LaneFile, lane: Lane) -> tuple[str, str]:
    deadline = runner.LaneDeadline.start(
        budget_seconds=lane.budget_seconds,
        monotonic=time.monotonic,
    )
    commit = git.head_rev(lane_file.project_root, remaining=deadline.remaining)
    tree = git.run(
        lane_file.project_root,
        "rev-parse",
        f"{commit}^{{tree}}",
        remaining=deadline.remaining,
    ).strip()
    repo_top = git.repo_top(lane_file.project_root, remaining=deadline.remaining)
    try:
        runner._resolve_snapshot_worktree_integrity(
            repo=lane_file.project_root,
            repo_top=repo_top,
            project_root=lane_file.project_root,
            dirty_ignore=lane_file.dirty_ignore,
            allow_dirty=False,
            remaining=deadline.remaining,
        )
    except AssayError as exc:
        raise LaneConfigError(
            f"campaign init requires a clean worktree under the lane's declared "
            f"dirty-ignore policy: {exc}"
        ) from exc
    return commit, tree


def _campaign_state_records_match(state_dir: Path, deadline_sha256: str) -> None:
    if not state_dir.exists():
        return
    if not state_dir.is_dir():
        raise LaneConfigError(f"campaign state directory is not a directory: {state_dir}")
    try:
        names = tuple(entry.name for entry in state_dir.iterdir())
    except OSError as exc:
        raise LaneConfigError(f"cannot inspect campaign state directory {state_dir}: {exc}") from exc
    for name in names:
        if re.fullmatch(r"[0-9a-f]{64}\.json", name) is None:
            continue
        try:
            raw = safeio.read_bounded_input(
                state_dir,
                name,
                limit=mutation.MUTATION_STATE_RECORD_LIMIT,
            )
        except AssayError as exc:
            raise LaneConfigError(
                f"cannot trust state record {state_dir / name}: {exc}"
            ) from exc
        if raw is None:
            continue
        try:
            record = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, ValueError, RecursionError):
            record = None
        if (
            not isinstance(record, dict)
            or record.get("campaign_deadline_sha256") != deadline_sha256
        ):
            raise LaneConfigError(
                f"state record {state_dir / name} is not bound to the campaign "
                "deadline being initialized; move that state directory aside deliberately"
            )


def _cmd_campaign_init(
    args: argparse.Namespace, out: TextIO, err: TextIO
) -> int:
    del err
    if _CAMPAIGN_NAME_RE.fullmatch(args.campaign) is None:
        raise LaneConfigError("--campaign must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    if args.hours is not None and (
        not math.isfinite(args.hours) or not 0 < args.hours <= 24
    ):
        raise LaneConfigError("--hours must be a decimal in (0, 24]")
    if args.wheel_sha256 is not None and _CAMPAIGN_SHA256_RE.fullmatch(args.wheel_sha256) is None:
        raise LaneConfigError("--wheel-sha256 must be 64 lowercase hexadecimal characters")
    if len(args.lane) != len(set(args.lane)):
        raise LaneConfigError("--lane values must be unique")

    lane_file = _resolve_lane_file(args.file)
    lane_names = tuple(sorted(args.lane))
    lanes = tuple(lane_file.lane(name) for name in lane_names)
    first_commit, first_tree = _campaign_git_identity(lane_file, lanes[0])

    plan_digests: dict[str, str | None] = {}
    for lane in lanes:
        if "R2" not in lane.rigor:
            plan_digests[lane.name] = None
            continue
        if lane.judge is None or lane.judge.mutation is None:
            raise LaneConfigError(
                f"lane {lane.name!r} declares R2 without a mutation configuration"
            )
        adapter = _resolve_declared_adapters(lane)
        if adapter is None:
            raise LaneConfigError(f"lane {lane.name!r} resolves no mutation adapter")
        base_declaration = runner.resolve_base_declaration(lane, None)
        discovered = _discover_plan_jobs(
            lane_file,
            lane,
            adapter=adapter,
            base_declaration=base_declaration,
            operators=lane.judge.mutation.operators,
            allow_dirty=False,
            resolve_reuse_command=False,
        )
        if discovered.commit != first_commit or discovered.tree != first_tree:
            raise LaneConfigError(
                "the worktree commit or tree changed while campaign plans were discovered"
            )
        if discovered.jobs == mutation.UNSUPPORTED:
            raise LaneConfigError(
                f"lane {lane.name!r} has no supported full mutation plan to bind"
            )
        if len(discovered.jobs) > lane.judge.mutation.max_mutants:
            raise LaneConfigError(
                f"lane {lane.name!r} exceeds max_mutants, so Assay cannot bind its full plan"
            )
        plan_digests[lane.name] = mutation.plan_sha256(
            [mutation.candidate_id(job) for job in discovered.jobs]
        )

    # Re-check both tree identity and the same snapshot integrity rule after
    # planning, so a concurrent checkout edit cannot be written into a fresh
    # campaign document.
    last_commit, last_tree = _campaign_git_identity(lane_file, lanes[-1])
    if (last_commit, last_tree) != (first_commit, first_tree):
        raise LaneConfigError("the worktree changed while campaign plans were discovered")

    now = datetime.now(timezone.utc)
    if args.expires_at is not None:
        expires_at = _campaign_timestamp(args.expires_at, field="--expires-at")
    else:
        exact_expiry = now + timedelta(hours=args.hours)
        expires_at = exact_expiry.replace(microsecond=0)
        if expires_at < exact_expiry:
            expires_at += timedelta(seconds=1)
    if expires_at <= now:
        raise LaneConfigError("--expires-at must be strictly in the future")
    created_at = now.replace(microsecond=0)

    document = {
        "schema": "assay-campaign-deadline/1",
        "campaign": args.campaign,
        "commit": first_commit,
        "git_tree": first_tree,
        "lanes": list(lane_names),
        "assay_version": __version__,
        "wheel_sha256": args.wheel_sha256,
        "plan_sha256": plan_digests,
        "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    serialized = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if args.out is None:
        target = lane_file.project_root / ".assay" / f"campaign-deadline-{args.campaign}.json"
    else:
        target = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(args.out)))))

    try:
        existing_raw = safeio.read_bounded_input(
            target.parent,
            target.name,
            limit=_CAMPAIGN_DEADLINE_LIMIT,
        )
    except AssayError as exc:
        raise LaneConfigError(f"cannot trust existing campaign deadline {target}: {exc}") from exc
    same_existing = False
    if existing_raw is not None:
        existing, existing_raw, _ = _parse_campaign_deadline_bytes(
            existing_raw,
            absolute=target,
            lane=lane_names[0],
        )
        identity_keys = (
            "campaign",
            "commit",
            "git_tree",
            "lanes",
            "assay_version",
            "wheel_sha256",
            "plan_sha256",
        )
        if any(existing[key] != document[key] for key in identity_keys):
            raise LaneConfigError(
                f"campaign deadline already exists with a different identity: {target}"
            )
        same_existing = True

    state_deadline_sha256 = hashlib.sha256(
        existing_raw if same_existing else serialized
    ).hexdigest()
    state_dirs: list[Path] = []
    for raw_state_dir in args.state_dir:
        state_dir = Path(resolve_state_directory(raw_state_dir))
        for root, inside in _containments(state_dir, lane_file.project_root):
            _refuse_a_visible_store_inside_the_tree(
                raw_state_dir,
                flag="--state-dir",
                what="those records",
                root=root,
                probe=inside / f"{'0' * 64}.json",
            )
        state_dirs.append(state_dir)
    for state_dir in state_dirs:
        _campaign_state_records_match(state_dir, state_deadline_sha256)

    if not same_existing:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".campaign-deadline-", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(serialized)
                stream.flush()
            os.replace(temporary_name, target)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
    print(target, file=out)
    return Outcome.PASS.exit_code


def _cmd_run(
    args: argparse.Namespace,
    appended: list[str],
    out: TextIO,
    err: TextIO,
    *,
    label_grace_seconds: float = LABEL_GRACE_SECONDS,
) -> int:
    lane_file = _resolve_lane_file(args.file)
    lane: Lane = lane_file.lane(args.lane)
    candidates_path = getattr(args, "candidates_file", None)
    pilot_selection: frozenset[str] | None = None
    candidates_file_sha256: str | None = None
    pilot_jobs_override = _parse_pilot_jobs(
        getattr(args, "pilot_jobs", None), candidates_file=candidates_path is not None
    )
    if candidates_path is not None:
        conflicts = []
        if getattr(args, "verdict_json", None) is not None:
            conflicts.append("--verdict-json")
        if getattr(args, "shard", None) is not None:
            conflicts.append("--shard")
        if getattr(args, "reuse_from", None) is not None:
            conflicts.append("--reuse-from")
        if getattr(args, "rejudge", None) is not None:
            conflicts.append("--rejudge")
        if getattr(args, "rejudge_outcome", None) is not None:
            conflicts.append("--rejudge-outcome")
        if conflicts:
            raise LaneConfigError(
                "--candidates-file cannot be combined with " + ", ".join(conflicts)
            )
        if getattr(args, "state_dir", None) is None:
            raise LaneConfigError("--candidates-file requires --state-dir")
        if (
            "R2" not in lane.rigor
            or lane.judge is None
            or lane.judge.mutation is None
            or lane.judge.mutation.is_ingested
        ):
            raise LaneConfigError(
                "--candidates-file requires a native R2 mutation lane"
            )
        pilot_selection, candidates_file_sha256 = _parse_candidates_file(
            candidates_path,
            max_mutants=lane.judge.mutation.max_mutants,
        )
        effective_jobs = (
            lane.judge.mutation.jobs
            if pilot_jobs_override is None
            else pilot_jobs_override
        )
        pilot_mutation = replace(lane.judge.mutation, jobs=effective_jobs)
        pilot_judge = replace(lane.judge, mutation=pilot_mutation)
        lane = replace(
            lane,
            rigor=tuple(rigor for rigor in lane.rigor if rigor != "R3"),
            judge=pilot_judge,
        )
    r2_manifest = _resolve_r2_manifest(args, lane_file.project_root)
    campaign_deadline = (
        _parse_campaign_deadline(campaign_deadline_arg, lane=lane.name)
        if (campaign_deadline_arg := getattr(args, "campaign_deadline", None)) is not None
        else None
    )
    if campaign_deadline is not None:
        campaign_doc = campaign_deadline[0]
        plan_digest = campaign_doc["plan_sha256"][lane.name]
        if ("R2" in lane.rigor) != (plan_digest is not None):
            raise LaneConfigError(
                f"campaign deadline plan_sha256 for lane {lane.name!r} does not "
                "match whether the lane declares R2"
            )
    if getattr(args, "operators", None):
        requested = tuple(part.strip() for part in args.operators.split(",") if part.strip())
        # B034/A-326: the same refusal `config._load_mutation` gives a
        # DECLARED withdrawn operator. `--operators` is an override of that
        # declaration, so it has to close the same door -- otherwise the
        # withdrawal is enforced only for lanes that spell it in TOML.
        # (A-331) And it runs BEFORE the unknown check for the same reason
        # the loader's does: at the v8 cut these names left the catalogue,
        # so "unknown" would now swallow them and answer a stale-but-once-
        # legal spelling with the least useful of the two messages.
        withdrawn = tuple(
            name for name in requested if name in WITHDRAWN_MUTATION_OPERATORS
        )
        if withdrawn:
            raise LaneConfigError(
                f"withdrawn mutation operators: {', '.join(withdrawn)}; every "
                f"site they produced was already produced by "
                f"python:compare-swap at the same span with the same "
                f"replacement"
            )
        unknown = tuple(name for name in requested if name not in MUTATION_OPERATORS)
        if unknown or not requested:
            raise LaneConfigError(f"unknown mutation operators: {', '.join(unknown)}")
        mutation_config = replace(
            lane.judge.mutation, operators=requested
        )
        judge_config = replace(lane.judge, mutation=mutation_config)
        lane = replace(lane, judge=judge_config)
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
    #
    # B031/A-320 round 2 (blocker 2): `--progress` shares this same OUTPUT
    # RESERVATION step for the two mistakes visible without opening
    # anything (a directory, an empty/unparseable path) -- an unwritable
    # `--progress <destination>` used to run the whole lane and only THEN
    # surface as an unrelated `ERROR`/`GIT_FAILED`, deep inside R2
    # execution. `validate_progress_destination` does not reserve a
    # descriptor the way `reserve_verdict_output` does: the progress file is
    # opened once, later, only if the lane reaches R2, and its own writer
    # creates missing parent directories on demand -- see its docstring.
    destination: VerdictOutput | None = None
    if args.verdict_json is not None:
        destination = reserve_verdict_output(args.verdict_json, stdout=out)
    state_lock_stack = ExitStack()
    try:
        # (B064) The stream is opened HERE, around BOTH `_run_reserved`'s
        # `run_lane` call and its own `write_verdict`, because
        # `verdict_written` is the R0/R1 stream's TERMINAL record and the
        # verdict is written after `run_lane` has already returned. A writer
        # opened inside `run_lane` could never emit it, which is why B064's
        # own vocabulary would have been silently one record short.
        #
        # The heartbeat interval is resolved and refused before the file is
        # opened: a floor violation is an operator mistake, and it belongs
        # with `--progress`'s own destination check rather than surfacing
        # once the lane is already running.
        heartbeat_seconds = _resolve_progress_heartbeat(args)
        state_dir = _resolve_state_dir(args, lane_file.project_root)
        pilot_state_dir = state_dir if pilot_selection is not None else None
        preflight_state_dir = state_dir
        if preflight_state_dir is None and pilot_selection is None and (
            getattr(args, "resume", False)
            or getattr(args, "shard", None) is not None
        ):
            # These qualifying paths use mutation.default_state_root when the
            # caller omits --state-dir. Inspect the effective store too, or a
            # pilot can be resumed through that implicit spelling.
            preflight_state_dir = mutation.default_state_root(
                lane_file.project_root
            )
        state_store_path = None
        if pilot_selection is not None:
            state_store_path = pilot_state_dir
        elif state_dir is not None:
            state_store_path = state_dir
        elif getattr(args, "resume", False) or getattr(args, "shard", None) is not None:
            state_store_path = preflight_state_dir
        state_lock_held = state_store_path is not None
        state_store_requested_path = (
            Path(
                os.path.normpath(
                    os.path.abspath(
                        os.path.expanduser(os.fspath(state_store_path))
                    )
                )
            )
            if state_store_path is not None
            else None
        )
        state_lock_root_fd: int | None = None
        state_lock_file_fds: tuple[int, ...] = ()
        if state_store_path is not None:
            (
                state_lock_root_fd,
                state_lock_file_fds,
                state_store_path,
            ) = _acquire_state_directory_lock(
                state_store_path
            )
            state_lock_stack.callback(
                _release_state_directory_lock,
                state_lock_root_fd,
                state_lock_file_fds,
            )
            state_dir = state_store_path
            preflight_state_dir = state_store_path
            if pilot_selection is not None:
                pilot_state_dir = state_store_path
            _verify_state_directory_identity(
                state_store_path,
                state_lock_root_fd,
                requested_path=state_store_requested_path,
            )

        if (
            (progress_arg := getattr(args, "progress", None)) is not None
            and state_store_path is not None
            and state_lock_root_fd is not None
        ):
            _refuse_progress_state_collision(
                progress_arg,
                state_store_path,
                state_lock_root_fd,
            )

        def verify_state_store_path() -> None:
            if state_store_path is not None and state_lock_root_fd is not None:
                _verify_state_directory_identity(
                    state_store_path,
                    state_lock_root_fd,
                    requested_path=state_store_requested_path,
                )

        _preflight_state_directory(
            preflight_state_dir,
            lane=lane.name,
            pilot=pilot_selection is not None,
            lock_held=state_lock_held,
            locked_root_fd=state_lock_root_fd,
        )
        verify_state_store_path()
        pilot_judge_sha256: list[str | None] | None = None
        if pilot_selection is not None:
            assert pilot_state_dir is not None
            pilot_judge_sha256 = [None]

        def _progress_stream(raw_write=None):
            if pilot_judge_sha256 is not None:
                raw_write = _capture_pilot_judge_identity(
                    raw_write, pilot_judge_sha256
                )
            return mutation.ProgressStream(raw_write, clock=runner._utc_now)

        if (progress_arg := getattr(args, "progress", None)) is not None:
            validate_progress_destination(progress_arg)
            _refuse_a_visible_progress_destination(
                progress_arg, lane_file.project_root
            )
            progress_path = Path(progress_arg).expanduser()
            progress_parent_guard = None
            progress_open_guard = None
            if state_store_path is not None and state_lock_root_fd is not None:
                def check_progress_parent(parent_fd: int) -> None:
                    _refuse_progress_parent_in_state(
                        progress_arg,
                        state_store_path,
                        parent_fd,
                        state_lock_root_fd,
                    )

                def check_progress_open(parent_fd: int, progress_fd: int) -> None:
                    _refuse_progress_state_fd_collision(
                        progress_arg,
                        state_store_path,
                        parent_fd,
                        progress_fd,
                        state_lock_root_fd,
                    )

                progress_parent_guard = check_progress_parent
                progress_open_guard = check_progress_open

            if pilot_judge_sha256 is not None:
                with ExitStack() as stack:
                    deferred = _DeferredProgressWriter(
                        stack,
                        progress_path,
                        parent_guard=progress_parent_guard,
                        open_guard=progress_open_guard,
                    )
                    return _run_reserved(
                        args,
                        lane,
                        lane_file,
                        appended,
                        destination,
                        out,
                        err,
                        label_grace_seconds=label_grace_seconds,
                        progress_stream=_progress_stream(deferred.write),
                        progress_heartbeat_seconds=heartbeat_seconds,
                        state_dir=state_dir,
                        r2_manifest=r2_manifest,
                        campaign_deadline=campaign_deadline,
                        pilot_selection=pilot_selection,
                        candidates_file_sha256=candidates_file_sha256,
                        pilot_state_dir=pilot_state_dir,
                        pilot_judge_sha256=pilot_judge_sha256,
                        pilot_preflight_complete=deferred.enable,
                        pilot_state_lock_held=state_lock_held,
                        state_store_root_fd=state_lock_root_fd,
                        state_lock_guard=verify_state_store_path,
                    )
            with mutation.progress_writer(
                progress_path,
                parent_guard=progress_parent_guard,
                open_guard=progress_open_guard,
            ) as raw_write:
                return _run_reserved(
                    args,
                    lane,
                    lane_file,
                    appended,
                    destination,
                    out,
                    err,
                    label_grace_seconds=label_grace_seconds,
                    progress_stream=_progress_stream(raw_write),
                    progress_heartbeat_seconds=heartbeat_seconds,
                    state_dir=state_dir,
                    r2_manifest=r2_manifest,
                    campaign_deadline=campaign_deadline,
                    pilot_selection=pilot_selection,
                    candidates_file_sha256=candidates_file_sha256,
                    pilot_state_dir=pilot_state_dir,
                    pilot_judge_sha256=pilot_judge_sha256,
                    pilot_state_lock_held=state_lock_held,
                    state_store_root_fd=state_lock_root_fd,
                    state_lock_guard=verify_state_store_path,
                )
        return _run_reserved(
            args,
            lane,
            lane_file,
            appended,
            destination,
            out,
            err,
            label_grace_seconds=label_grace_seconds,
            progress_stream=(
                _progress_stream()
                if pilot_judge_sha256 is not None
                else None
            ),
            progress_heartbeat_seconds=heartbeat_seconds,
            state_dir=state_dir,
            r2_manifest=r2_manifest,
            campaign_deadline=campaign_deadline,
            pilot_selection=pilot_selection,
            candidates_file_sha256=candidates_file_sha256,
            pilot_state_dir=pilot_state_dir,
            pilot_judge_sha256=pilot_judge_sha256,
            pilot_state_lock_held=state_lock_held,
            state_store_root_fd=state_lock_root_fd,
            state_lock_guard=verify_state_store_path,
        )
    finally:
        state_lock_stack.close()
        if destination is not None:
            destination.close()


def _resolve_state_dir(
    args: argparse.Namespace, project_root: Path
) -> "Path | None":
    """(B066) Resolve and refuse ``--state-dir`` BEFORE any work starts.

    Two refusals, both cheap and both before a single command runs:

    * the destination is not a directory (:func:`output.resolve_state_directory`);
    * the destination is inside the judged tree and git can SEE it there.

    The second is the one the entry's own acceptance names, and the reason
    is measured rather than theoretical: an untracked path inside the work
    tree makes ``git.dirty_paths`` report it, which turns the NEXT run of
    the same lane into `NO_MEASUREMENT`/`DIRTY_TREE`. That is exactly the
    failure B031 measured for the progress artifact, and the same trap is
    open here the moment a consumer can choose the location. A gitignored
    path inside the tree is fine -- git cannot see it, so nothing goes
    dirty -- and so is any path outside the tree.
    """
    raw = getattr(args, "state_dir", None)
    if raw is None:
        return None
    resolved = Path(resolve_state_directory(raw))
    for root, inside in _containments(resolved, project_root):
        _refuse_a_visible_store_inside_the_tree(
            raw,
            flag="--state-dir",
            what="those records",
            root=root,
            # A representative record name, because the directory itself
            # does not exist yet -- see the helper's own docstring.
            probe=inside / f"{'0' * 64}.json",
        )
    return resolved


def _resolve_r2_manifest(
    args: argparse.Namespace, project_root: Path
) -> "Path | None":
    """Resolve and preflight the optional cold-witness manifest destination."""
    raw = getattr(args, "r2_manifest", None)
    cold_witness = getattr(args, "cold_witness", False)
    if raw is None:
        return None
    if not cold_witness:
        raise LaneConfigError("--r2-manifest requires --cold-witness")
    resolved = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
    )
    for root, relative in _containments(resolved, project_root):
        _refuse_a_visible_store_inside_the_tree(
            raw,
            flag="--r2-manifest",
            what="the R2 manifest",
            root=root,
            probe=relative,
        )
    return resolved


def _refuse_a_visible_progress_destination(raw: str, project_root: Path) -> None:
    """(Round-1 SF-5) The same git-visibility preflight `--state-dir` has.

    B064 made `--progress` write on EVERY rigor tier. Before it, a
    destination inside a non-ignored work tree was harmless on an R0/R1 lane
    -- nothing was written, because the producer only existed at R2 -- and
    after it the lane refuses ITSELF on its very first run, with a bare
    `NO_MEASUREMENT`/`DIRTY_TREE` that names neither the flag nor the fix.

    That asymmetry was created in one commit range: the same wave gave
    `--state-dir` a preflight that refuses before any work and left the flag
    it had just made universal without one. Both flags now ask the identical
    question through the identical helper.
    """
    destination = Path(os.path.normpath(os.path.abspath(os.path.expanduser(raw))))
    for root, relative in _containments(destination, project_root):
        # The progress destination IS a file, so it is its own probe --
        # never a representative sibling, which would answer the wrong
        # question for a `.gitignore` that matches by extension.
        _refuse_a_visible_store_inside_the_tree(
            raw,
            flag="--progress",
            what="the progress file",
            root=root,
            probe=relative,
        )


def _refuse_progress_state_collision(
    raw: str,
    state_dir: Path,
    state_root_fd: int,
) -> None:
    """Refuse progress output that aliases any admitted mutation-state file."""
    destination = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
    )
    state_root = state_dir.resolve(strict=True)
    resolved = _resolve_through_existing_prefix(destination)
    for candidate in {destination, resolved}:
        try:
            candidate.relative_to(state_root)
        except ValueError:
            continue
        raise LaneConfigError(
            f"--progress {raw!r} resolves inside the mutation state directory "
            f"{state_root}; progress must use a separate file"
        )

    try:
        destination_stat = os.stat(destination, follow_symlinks=True)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LaneConfigError(
            f"--progress {raw!r} cannot be checked against mutation state: {exc}"
        ) from exc
    for name in os.listdir(state_root_fd):
        try:
            state_stat = os.stat(name, dir_fd=state_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (
            stat.S_ISREG(state_stat.st_mode)
            and (destination_stat.st_dev, destination_stat.st_ino)
            == (state_stat.st_dev, state_stat.st_ino)
        ):
            raise LaneConfigError(
                f"--progress {raw!r} is another link to mutation-state file "
                f"{name!r}; progress must use a separate file"
            )


def _refuse_progress_parent_in_state(
    raw: str,
    state_dir: Path,
    progress_parent_fd: int,
    state_root_fd: int,
) -> None:
    """Reject a pinned progress parent located inside the mutation store."""
    state_stat = os.fstat(state_root_fd)
    current_fd = os.dup(progress_parent_fd)
    try:
        for _ in range(256):
            current_stat = os.fstat(current_fd)
            if (current_stat.st_dev, current_stat.st_ino) == (
                state_stat.st_dev,
                state_stat.st_ino,
            ):
                raise LaneConfigError(
                    f"--progress {raw!r} resolves inside the mutation state directory "
                    f"{state_dir}; progress must use a separate file"
                )
            parent_fd = os.open(
                "..",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            parent_stat = os.fstat(parent_fd)
            if (parent_stat.st_dev, parent_stat.st_ino) == (
                current_stat.st_dev,
                current_stat.st_ino,
            ):
                os.close(parent_fd)
                return
            os.close(current_fd)
            current_fd = parent_fd
        raise LaneConfigError(
            f"cannot prove that --progress {raw!r} is outside the mutation state directory"
        )
    except OSError as exc:
        raise LaneConfigError(
            f"cannot check the opened --progress parent for {raw!r}: {exc}"
        ) from exc
    finally:
        os.close(current_fd)


def _refuse_progress_state_fd_collision(
    raw: str,
    state_dir: Path,
    progress_parent_fd: int,
    progress_fd: int,
    state_root_fd: int,
) -> None:
    """Validate the exact progress inode after its parent and file are pinned."""
    _refuse_progress_parent_in_state(
        raw,
        state_dir,
        progress_parent_fd,
        state_root_fd,
    )
    progress_stat = os.fstat(progress_fd)
    if not stat.S_ISREG(progress_stat.st_mode):
        raise LaneConfigError(
            f"--progress {raw!r} did not open a regular file"
        )
    for name in os.listdir(state_root_fd):
        try:
            state_stat = os.stat(name, dir_fd=state_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (
            stat.S_ISREG(state_stat.st_mode)
            and (progress_stat.st_dev, progress_stat.st_ino)
            == (state_stat.st_dev, state_stat.st_ino)
        ):
            raise LaneConfigError(
                f"--progress {raw!r} is another link to mutation-state file "
                f"{name!r}; progress must use a separate file"
            )


def _containments(target: Path, project_root: Path) -> "list[tuple[Path, Path]]":
    """(Round-1 SF-1) Every way *target* can be said to land inside the
    judged tree, as ``(root, relative)`` pairs.

    The shipped check compared ONE pair, and the two halves of it came from
    two different path namespaces: `output.resolve_state_directory` is
    lexical by design (`normpath`, never `realpath`, because resolving
    against the filesystem would follow symlinks that the descriptor walk
    exists to refuse), while `project_root.resolve()` does follow them. When
    they disagreed `relative_to` raised and the caller read that as
    "outside the tree, nothing to check" -- a containment check that fails
    OPEN, reproduced twice by the reviewer with records landing inside the
    real work tree.

    Both namespaces are now asked, in both directions, and ANY of them
    saying "inside" is enough to refuse. Fail-closed is the only safe
    disposition for this question: a false refusal costs an operator one
    clear message naming the path, while a false accept costs them a work
    tree that refuses its own next run.
    """
    candidates: list[Path] = [target]
    fully_resolved = _resolve_through_existing_prefix(target)
    if fully_resolved != target:
        candidates.append(fully_resolved)
    roots: list[Path] = [project_root]
    real_root = project_root.resolve()
    if real_root != project_root:
        roots.append(real_root)

    found: list[tuple[Path, Path]] = []
    for root in roots:
        for candidate in candidates:
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            found.append((root, relative))
    return found


def _resolve_through_existing_prefix(target: Path) -> Path:
    """*target* with every symlink in its existing prefix resolved.

    `Path.resolve()` is non-strict, so a not-yet-created directory (which
    `--state-dir` and `--progress` both routinely are) resolves its existing
    ancestors and keeps the remainder verbatim -- exactly what a containment
    question needs. Wrapped so an `OSError` (a resolution loop, a permission
    failure mid-walk) leaves the lexical spelling standing rather than
    crashing a preflight.
    """
    try:
        return target.resolve()
    except OSError:
        return target


def _refuse_a_visible_store_inside_the_tree(
    raw: str, *, flag: str, what: str, root: Path, probe: Path
) -> None:
    """Refuse *raw* when git can see the file(s) it names inside the tree.

    *probe* is always a FILE that assay would actually write, never a
    directory: `git check-ignore` cannot tell that a not-yet-existing path
    is a directory, so a perfectly ordinary directory-only `.gitignore` line
    (`resume-store/`) would answer "not ignored" and refuse a
    correctly-configured consumer. `--state-dir` therefore probes a
    representative record name under the directory; `--progress` probes the
    destination itself, which already is the file.
    """
    # (Round-1 N2) `path_is_ignored` runs without `--literal-pathspecs`,
    # because `check-ignore` refuses that flag outright -- so git would parse
    # a leading `:` as pathspec magic and answer with a raw `fatal:` that
    # reached the operator as `ERROR/GIT_FAILED`, the exact shape B068 was
    # fixed to stop emitting. Named here instead, before git is asked.
    if any(component.startswith(":") for component in probe.parts):
        raise LaneConfigError(
            f"{flag} {raw!r} has a path component beginning with ':', which "
            f"git reads as pathspec magic rather than as a filename, so "
            f"assay cannot ask whether it is ignored. Choose a path whose "
            f"components do not begin with ':'"
        )
    _refuse_a_destination_reached_through_a_symlink(
        raw, flag=flag, what=what, root=root, probe=probe
    )
    if not git.path_is_ignored(root, probe.as_posix()):
        raise LaneConfigError(
            f"{flag} {raw!r} resolves inside the judged tree at "
            f"{root / probe} and is not git-ignored -- assay's own "
            f"clean-tree precondition would then report {what} as "
            f"uncommitted and refuse the NEXT run of this lane "
            f"NO_MEASUREMENT/DIRTY_TREE. Point it outside the repository, "
            f"or add {probe.as_posix()!r} to a committed .gitignore"
        )


def _refuse_a_destination_reached_through_a_symlink(
    raw: str, *, flag: str, what: str, root: Path, probe: Path
) -> None:
    """(B077) Refuse *raw* when *probe* is reached through a symlinked
    DIRECTORY inside the judged tree, naming the link and the traversal.

    Git refuses to resolve a pathspec through a symlink at all --
    ``fatal: pathspec '<path>' is beyond a symbolic link``, exit 128 -- so
    :func:`assay.git.path_is_ignored` cannot answer the ignore question here
    in either direction. Without this guard that fatal reached the operator
    verbatim as ``ERROR``/``GIT_FAILED``, a repository-failure shape for
    what is actually a destination-configuration mistake, and one a consumer
    who had gitignored the real location correctly could hit while doing
    everything right. That is the same "a real, fail-closed refusal with a
    message that does not tell a correctly-configured consumer what to do"
    shape ``_linked_worktree_gap()`` (B068) and the round-1 N2 guard
    immediately above both exist to close; this is the third instance, and
    it is answered in the same place and the same way -- BEFORE git is
    asked, rather than by dressing up git's own error afterwards.

    ``ERROR``/``BAD_LANE_CONFIG`` (via :class:`~assay.errors.
    LaneConfigError`), not ``ERROR``/``GIT_FAILED``: the backlog entry's own
    "or a more specific reason code, if one already exists" clause is
    answered by the two sibling refusals in this very function, which use it
    for exactly this class of before-any-work destination mistake. Git did
    not fail; the destination cannot be asked about.

    Only DIRECTORY components are probed (``probe.parts[:-1]``). A symlink
    in the final position is not "beyond" anything -- ``check-ignore``
    answers about the link entry itself, normally and correctly -- so
    including it would refuse a case that works today.
    """
    walked = root
    for component in probe.parts[:-1]:
        walked = walked / component
        if not walked.is_symlink():
            continue
        try:
            points_to = os.readlink(walked)
        except OSError:
            points_to = "<unreadable>"
        real = _resolve_through_existing_prefix(walked)
        raise LaneConfigError(
            f"{flag} {raw!r} is reached through the symlink {walked} -> "
            f"{points_to} inside the judged tree. Git refuses to resolve a "
            f"pathspec through a symlink at all ('fatal: pathspec ... is "
            f"beyond a symbolic link'), so assay cannot ask whether {what} "
            f"would be visible to it there -- neither answer is available, "
            f"so the destination is refused rather than guessed at. Pass the "
            f"link's own destination instead ({real}), which assay checks "
            f"normally, or point {flag} outside the repository"
        )


def _resolve_progress_heartbeat(args: argparse.Namespace) -> float | None:
    """(B064) ``--progress-heartbeat`` in seconds, or ``None``.

    ``None`` exactly when ``--progress`` was not passed: the flag is a no-op
    without a destination, and returning the default there would arm a
    thread with nowhere to write. Below the floor is refused BY NAME rather
    than clamped -- silently substituting a different interval than the one
    an operator asked for is how a configuration mistake survives to become
    a mystery in someone's log.
    """
    if getattr(args, "progress", None) is None:
        return None
    raw = getattr(args, "progress_heartbeat", None)
    if raw is None:
        return runner.PROGRESS_HEARTBEAT_DEFAULT_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        raise LaneConfigError(
            f"--progress-heartbeat must be a number of seconds, got {raw!r}"
        ) from None
    if not (seconds == seconds and seconds not in (float("inf"), float("-inf"))):
        raise LaneConfigError(
            f"--progress-heartbeat must be a finite number of seconds, got {raw!r}"
        )
    if seconds < runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS:
        raise LaneConfigError(
            f"--progress-heartbeat {raw!r} is below the "
            f"{runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS:g}s floor -- a "
            f"sub-floor interval turns a diagnostic stream into a flood "
            f"(an hour-long lane at 1s writes 3,600 records that say "
            f"nothing a 60s tick does not)"
        )
    return seconds


def _declared_evidence(lane: Lane) -> tuple[EvidenceDeclaration, ...]:
    """The lane's own ordered Tier-3 identities, converted to
    :class:`~assay.verdict.EvidenceDeclaration` (P26/A-213). Empty when the
    lane declares none at all -- no location is ever derived.
    """
    if lane.judge is None or lane.judge.evidence is None:
        return ()
    return tuple(
        EvidenceDeclaration(source=item.source, key=item.key) for item in lane.judge.evidence
    )


def _timed_out_evidence(
    declared_evidence: tuple[EvidenceDeclaration, ...], exc: AssayError
) -> tuple[Evidence, ...]:
    """Every declared evidence identity as a payload-free
    ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` entry (A-213's atomic attestation
    timeout artifact)."""
    return tuple(
        Evidence(
            source=item.source,
            key=item.key,
            status=exc.outcome,
            verified_by_assay=False,
            reason_code=exc.reason_code,
        )
        for item in declared_evidence
    )


def _unresolved_evidence(
    declared_evidence: tuple[EvidenceDeclaration, ...], exc: AssayError
) -> tuple[Evidence, ...]:
    """Bind a pre-loader refusal to every declared evidence identity."""
    return tuple(
        Evidence(
            source=item.source,
            key=item.key,
            status=exc.outcome,
            verified_by_assay=False,
            reason_code=exc.reason_code,
        )
        for item in declared_evidence
    )


def _run_reserved(
    args: argparse.Namespace,
    lane: Lane,
    lane_file: LaneFile,
    appended: list[str],
    destination: "VerdictOutput | None",
    out: TextIO,
    err: TextIO,
    *,
    label_grace_seconds: float = LABEL_GRACE_SECONDS,
    #: (B064) The already-open, lane-wide progress stream, or `None`. Opened
    #: by `_cmd_run` so it spans `run_lane` AND `write_verdict` below.
    progress_stream: "mutation.ProgressStream | None" = None,
    progress_heartbeat_seconds: float | None = None,
    #: (B066) The already-resolved, already-refused `--state-dir`, or `None`
    #: for today's `<project_root>/.assay/mutation-state/`.
    state_dir: "Path | None" = None,
    r2_manifest: "Path | None" = None,
    campaign_deadline: tuple[dict[str, Any], bytes, datetime] | None = None,
    pilot_selection: frozenset[str] | None = None,
    candidates_file_sha256: str | None = None,
    pilot_state_dir: Path | None = None,
    pilot_judge_sha256: list[str | None] | None = None,
    pilot_preflight_complete: Callable[[], None] | None = None,
    pilot_state_lock_held: bool = False,
    state_store_root_fd: int | None = None,
    state_lock_guard: Callable[[], None] | None = None,
) -> int:
    campaign_deadline_sha256: str | None = None
    campaign_binding = None
    if campaign_deadline is not None:
        campaign_document, campaign_bytes, _ = campaign_deadline
        campaign_binding = CampaignBinding(
            name=campaign_document["campaign"],
            deadline_sha256=hashlib.sha256(campaign_bytes).hexdigest(),
            created_at_utc=campaign_document["created_at_utc"],
            expires_at_utc=campaign_document["expires_at_utc"],
        )
    # P26/A-212: one LaneDeadline, started here -- before HEAD is even
    # resolved -- reaches HEAD, attestation, adapter resolution, and the
    # whole of run_lane (direct R0 or higher rigor). CLI never passes
    # `deadline=None` to run_lane; the exact sequence below is the contract:
    # lane/output already reserved -> deadline -> HEAD -> attestation ->
    # adapter -> command -> emit once.
    # B018/A-327: resolved ONCE, here, before the lane deadline even starts.
    # Identity is a fact of this process, not of the run, and a consumer that
    # demanded the binding must learn it is unavailable before assay spends a
    # budget producing evidence that consumer would refuse anyway.
    judge_provenance, unidentified = provenance.identify_judge()
    if unidentified is not None:
        if getattr(args, "require_judge_provenance", False):
            raise LaneConfigError(
                f"--require-judge-provenance: this assay cannot identify the "
                f"build artifact it is running from, so no verdict it emits "
                f"could be bound to a verified judge -- {unidentified}"
            )
        # Loud, never silent (B018's own acceptance criterion): the absence is
        # announced on the diagnostics stream every time, because a consumer
        # reading only the artifact would otherwise find a field that is
        # simply not there, with nothing saying why.
        print(
            f"assay: no judge_provenance recorded -- {unidentified}; pass "
            f"--require-judge-provenance to refuse instead of proceeding",
            file=err,
        )
    deadline = runner.LaneDeadline.start(
        budget_seconds=lane.budget_seconds, monotonic=time.monotonic
    )
    # B013: `derived:` facts read rendered CIU state at the project root
    # (A-293); ciu itself gitignores `ciu.global.toml` and only ever renders
    # it there (ciu/README.md, ciu/src/ciu/scaffold.py). A lane without any
    # `derived:` declaration never opens this path -- resolve_command_plan's
    # own `has_derived` check is what refuses a *used* but unreadable source.
    infrastructure_source = (
        lane_file.project_root / "ciu.global.toml" if lane.infrastructure else None
    )
    infrastructure_environment = os.environ if lane.infrastructure else None
    selection_order = (
        tuple(sorted(pilot_selection)) if pilot_selection is not None else ()
    )
    selection_sha256: str | None = None
    pilot_jobs_by_id: dict[str, mutation.MutantJob] = {}
    pilot_state_accepted = False
    if pilot_selection is not None and (
        candidates_file_sha256 is None or pilot_state_dir is None
    ):
        raise ValueError("pilot selection requires its file digest and resolved state directory")
    # Pure, and independent of every Git fact -- resolved BEFORE the first
    # deadline-bounded call so the timeout refusal below can render the
    # lane's declared evidence identities exactly as A-213's does.
    declared_evidence = _declared_evidence(lane)

    def _emit_run_header(resolved_commit: str) -> None:
        """(B064/B065) The stream's first record, emitted the instant the
        commit label exists -- every earlier refusal has no commit to
        attribute records to, and a header without one cannot do the one job
        a header on an append-only file has."""
        if progress_stream is not None:
            progress_stream.emit_run_header(
                commit=resolved_commit,
                lane=lane.name,
                rigor=lane.rigor,
                # (B067) `None` here means and only means `budget =
                # "unbounded"`; `budget_per_candidate_s` is then the only
                # bound the run has, which is exactly why a reader needs
                # both in one place.
                budget_s=lane.budget_seconds,
                budget_per_candidate_s=runner._declared_budget_per_candidate_seconds(
                    lane
                ),
            )

    def _emit_verdict_written(
        final: Verdict,
        *,
        effective_exit_code: int | None = None,
        pilot: bool = False,
    ) -> None:
        """(B064) The R0/R1 stream's TERMINAL record, and the reason the
        stream is opened in `_cmd_run` rather than inside `run_lane`.

        Emitted for every verdict this function returns, including the
        pre-run refusals -- a reader must be able to tell "the run ended,
        refusing" from "the process died", and those two look identical from
        a file that simply stops. `destination` is `null` when the consumer
        asked for no artifact (A-028's no-artifact mode): the verdict is
        still final, and saying WHERE it went is the honest way to report
        that nothing was written."""
        if progress_stream is None:
            return
        progress_stream.emit(
            {
                "event": "verdict_written",
                "outcome": final.outcome.value,
                "reason_code": (
                    final.reason_code.value
                    if final.reason_code is not None
                    else None
                ),
                "exit_code": (
                    final.exit_code
                    if effective_exit_code is None
                    else effective_exit_code
                ),
                "destination": (
                    getattr(args, "verdict_json", None)
                    if destination is not None and not pilot
                    else None
                ),
            }
        )

    def _finish(verdict: Verdict) -> int:
        """(B129) Write the artifact (when one was asked for), close the
        stream, print the summary, and return the exit code. Nested, because
        it closes over `destination`, `args`, `out` and `_emit_verdict_written`.
        """
        if state_lock_guard is not None:
            state_lock_guard()
        if pilot_preflight_complete is not None:
            # A lane refusal can finish before plan discovery reaches the
            # sentinel check. Keep its ordinary terminal progress record;
            # PILOT-STATE refusals bypass _finish and stay stderr-only.
            pilot_preflight_complete()
        if campaign_binding is not None and verdict.campaign is None:
            verdict = replace(verdict, campaign=campaign_binding)
        if pilot_selection is not None:
            assert candidates_file_sha256 is not None and pilot_state_dir is not None
            summary = _build_pilot_summary(
                verdict,
                lane=lane,
                selected_ids=pilot_selection,
                selection_order=selection_order,
                selection_sha256=selection_sha256,
                candidates_file_sha256=candidates_file_sha256,
                state_dir=pilot_state_dir,
                pilot_jobs=pilot_jobs_by_id,
                judge_sha256=(
                    None if pilot_judge_sha256 is None else pilot_judge_sha256[0]
                ),
                campaign_deadline_sha256=campaign_deadline_sha256,
                cold_witness=getattr(args, "cold_witness", False),
                state_root_fd=state_store_root_fd,
                completed=False,
            )
            completed = _pilot_completed(
                verdict,
                pilot_selection,
                unresolved=summary["unresolved"],
            )
            summary["completed"] = completed
            exit_code = _pilot_exit_code(verdict, completed=completed, err=err)
            if pilot_state_accepted:
                assert state_store_root_fd is not None
                sentinel = _read_pilot_state_document(
                    pilot_state_dir,
                    state_store_root_fd,
                    require_sentinel_for_records=True,
                )
                if (
                    sentinel is None
                    or sentinel["lane"] != lane.name
                    or sentinel["selection_sha256"] != selection_sha256
                ):
                    raise PilotStateError(
                        "PILOT-STATE changed while the pilot summary was being built; "
                        "refusing to publish a completion result"
                    )
            if state_lock_guard is not None:
                state_lock_guard()
            print(json.dumps(summary, indent=2, sort_keys=True), file=out)
            _emit_verdict_written(
                verdict, effective_exit_code=exit_code, pilot=True
            )
            return exit_code
        if destination is not None:
            # Exactly once, and the summary is printed only after it succeeded:
            # a run that could not deliver the artifact it was asked for must not
            # also print a line that reads like a completed run (A-181).
            runner.write_verdict(verdict, destination)
        _emit_verdict_written(verdict)
        if args.verdict_json != "-":
            _print_run_summary(verdict, out)
        return verdict.exit_code

    def _deliver_refusal(exc: AssayError) -> int:
        if isinstance(exc, PilotStateError):
            runner.announce_refusal(exc, diagnostics=err)
            return exc.exit_code
        detail = runner.announce_refusal(exc, diagnostics=err)
        refusal_evidence = (
            _timed_out_evidence(declared_evidence, exc)
            if exc.reason_code is ReasonCode.LANE_TIMEOUT
            else evidence
        )
        refusal_lane = lane
        if (
            pilot_selection is not None
            and exc.reason_code is ReasonCode.MUTATION_DISCOVERY_FAILED
        ):
            # Mutation discovery is an R2-only terminal. This preflight has
            # not run R0/R1; preserve the precise reason on an R2-only refusal
            # instead of assigning it to every declared rigor and violating
            # Claim's reason-code ownership rule.
            refusal_lane = replace(lane, rigor=("R2",))
        verdict = runner.refuse_lane(
            refusal_lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            detail=detail,
            argv_append=appended,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=__version__,
            judge_provenance=judge_provenance,
            evidence=refusal_evidence,
            declared_evidence=declared_evidence,
        )
        return _finish(verdict)

    try:
        commit = git.head_rev(lane_file.project_root, remaining=deadline.remaining)
    except AssayError as exc:
        if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
            raise
        # (B028/DA-R9, SF-1) The EARLIEST place the lane-wide deadline can
        # expire, and the place R-1's `budget = "0.001s"` probe actually
        # escaped from -- measured, with the stack captured in the REPORT:
        # `cli.py` -> `git.head_rev` -> `git._run_bounded` ->
        # `LaneDeadline.remaining`. That is UPSTREAM of `run_lane` entirely,
        # so the handler around `run_lane` below cannot see it and the
        # reserved `--verdict-json` was never written.
        #
        # **The one fact that is not yet known here is the commit label** --
        # DA-R9's own contingency ("if `refuse_lane` needs a fact that is
        # unavailable before `git.repo_top`, the verdict carries what is
        # known and the REPORT records exactly which field"). It is READ,
        # never fabricated: a commit label is an IDENTITY, not a
        # measurement, `budget` bounds the lane's work rather than the
        # artifact's production (the `write_verdict`/summary tail below
        # already runs past the deadline on every timed-out lane), and an
        # invented label would be the one thing this project must never
        # emit.
        #
        # (A-425/DA-R13) The read is BOUNDED, by its own short grace rather
        # than by the lane's spent deadline -- which has zero left by
        # construction, so reusing it would mean no timed-out lane ever gets
        # the verdict `--verdict-json` reserved. A-420 shipped this call
        # unbounded and DA-R13 ruled that out: assay never hangs, and a
        # stalled mount would otherwise hang the refusal path itself. The
        # grace is expressed through the SAME `remaining=` shape every other
        # Git call uses -- `LaneDeadline` constructed directly because its
        # `start` classmethod rejects a non-positive budget, and the
        # grace-expired test sets `label_grace_seconds = 0.0` through the
        # parameter rather than stubbing anything.
        #
        # If the grace ALSO expires, no verdict is written and the one line
        # the emitter prints says the LABEL could not be read within it --
        # the operator's next move is to look at Git, not at `budget`. Any
        # OTHER Git fault re-raises the ORIGINAL timeout unchanged: a Git
        # fault must not be renamed, and a lane that cannot be labelled at
        # all is exactly the case `main()`'s handler already owns.
        grace = runner.LaneDeadline(
            expires_at=time.monotonic() + label_grace_seconds,
            monotonic=time.monotonic,
            honors_termination=False,
        )
        try:
            commit = git.head_rev(lane_file.project_root, remaining=grace.remaining)
        except AssayError as label_exc:
            if label_exc.reason_code is ReasonCode.LANE_TIMEOUT:
                raise AssayError(
                    f"the lane-wide deadline expired, and the commit label "
                    f"the refusal verdict must carry could not be read from "
                    f"{lane_file.project_root} within the "
                    f"{label_grace_seconds}s grace allowed for it "
                    f"(assay.cli.LABEL_GRACE_SECONDS); no verdict was "
                    f"written -- git, not the lane's budget, is what did not "
                    f"answer",
                    outcome=exc.outcome,
                    reason_code=exc.reason_code,
                ) from None
            raise exc from None
        # (B053/A-428, A-439) Announced BEFORE the artifact is built, not
        # after, because the artifact now carries the announced sentence:
        # `announce_refusal` returns the bounded copy and `refuse_lane` puts
        # it on every declared level's claim. The observable order is
        # unchanged -- `refuse_lane` writes nothing to any stream.
        detail = runner.announce_refusal(exc, diagnostics=err)
        verdict = runner.refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            detail=detail,
            argv_append=appended,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=__version__,
            judge_provenance=judge_provenance,
            evidence=_timed_out_evidence(declared_evidence, exc),
            declared_evidence=declared_evidence,
        )
        _emit_run_header(commit)
        return _finish(verdict)

    # (B064) The commit label exists from here on, so the stream gets its
    # header before any further work -- attestation, adapter resolution and
    # the lane itself all now have a run to be attributed to. Idempotent, so
    # the timeout branch above having already emitted one is not a second.
    _emit_run_header(commit)

    expected_plan_sha256: str | None = None
    if campaign_deadline is not None:
        campaign_doc, campaign_raw, expires_at_utc = campaign_deadline
        expected_plan_sha256 = campaign_doc["plan_sha256"][lane.name]
        campaign_deadline_sha256 = hashlib.sha256(campaign_raw).hexdigest()
        try:
            current_tree = git.run(
                lane_file.project_root,
                "rev-parse",
                f"{commit}^{{tree}}",
                remaining=deadline.remaining,
            ).strip()
        except AssayError as exc:
            if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
                raise
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=_timed_out_evidence(declared_evidence, exc),
                declared_evidence=declared_evidence,
            )
            return _finish(verdict)

        identity_mismatches: list[str] = []
        if campaign_doc["commit"] != commit:
            identity_mismatches.append(
                f"commit expected {campaign_doc['commit']}, observed {commit}"
            )
        if campaign_doc["git_tree"] != current_tree:
            identity_mismatches.append(
                f"git_tree expected {campaign_doc['git_tree']}, observed {current_tree}"
            )
        if campaign_doc["assay_version"] != __version__:
            identity_mismatches.append(
                f"assay_version expected {campaign_doc['assay_version']}, "
                f"observed {__version__}"
            )
        if identity_mismatches:
            exc = AssayError(
                "campaign deadline identity does not match this run: "
                + "; ".join(identity_mismatches),
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=_unresolved_evidence(declared_evidence, exc),
                declared_evidence=declared_evidence,
            )
            return _finish(verdict)

        # Convert UTC to monotonic exactly once, after identity checks so an
        # expired document for a different tree remains an identity refusal.
        deadline = runner.campaign_bounded_deadline(
            deadline,
            expires_at_utc=expires_at_utc,
            wall_now=datetime.now(timezone.utc),
            monotonic_now=time.monotonic(),
        )

    # No declaration means no loader call. Otherwise each declared source's
    # own directory exists by config invariant (B004/A-430's PER-SOURCE
    # pairing rule -- up to v9 there was only one source, so one loader call
    # sufficed; from v10 a lane may declare `attested`, `adjudicated`, or
    # both). `attestation.load_attested_evidence` refuses any declaration
    # whose source is not `"attested"` (A-085), and
    # `adjudication.load_adjudicated_evidence` is its Tier-2 mirror image --
    # neither loader handles the other's identities -- so a mixed lane needs
    # ONE call to EACH loader, over its own subset, with the two result
    # tuples merged back into the lane's full DECLARED order:
    # `runner._require_evidence_bound_to_lane` requires the final `evidence`
    # tuple to equal `declared_evidence`'s identities as an ORDERED LIST
    # (list equality, not set membership), which an interleaved declaration
    # like `[attested, adjudicated, attested]` would not get from simply
    # concatenating the two loaders' own outputs.
    attested_declared = tuple(
        item for item in declared_evidence if item.source == "attested"
    )
    adjudicated_declared = tuple(
        item for item in declared_evidence if item.source == "adjudicated"
    )
    if declared_evidence:
        try:
            attested_evidence = (
                attestation.load_attested_evidence(
                    lane_file.project_root,
                    head=commit,
                    declared=attested_declared,
                    project_root=lane_file.project_root,
                    attestation_dir=lane.judge.attestation_dir,
                    remaining=deadline.remaining,
                )
                if attested_declared
                else ()
            )
            adjudicated_evidence = (
                adjudication.load_adjudicated_evidence(
                    lane_file.project_root,
                    head=commit,
                    declared=adjudicated_declared,
                    adjudication_dir=lane.judge.adjudication_dir,
                    remaining=deadline.remaining,
                )
                if adjudicated_declared
                else ()
            )
        except AssayError as exc:
            if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
                raise
            # A-213, generalised across TWO SEQUENTIAL loaders (B004): the
            # evidence deadline is atomic regardless of WHICH loader's Git
            # calls or file reads exhausted it -- whichever already ran
            # (including a first loader's already-loaded results, silently
            # discarded rather than partially reported) is superseded, and
            # every declared identity from BOTH sources becomes the SAME
            # payload-free BUDGET_EXCEEDED/LANE_TIMEOUT pair, in declared
            # order (`_timed_out_evidence` is already source-agnostic: it
            # copies `item.source` from the declaration, not from either
            # loader's result).
            # (B053/A-439) Same order, same reason, as the sibling above.
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=_timed_out_evidence(declared_evidence, exc),
                declared_evidence=declared_evidence,
            )
            return _finish(verdict)
        else:
            # Merge back into the lane's own declared order -- see the
            # comment above this block for why concatenation alone is not
            # enough once a lane interleaves sources.
            by_identity = {
                item.identity: item
                for item in (*attested_evidence, *adjudicated_evidence)
            }
            evidence = tuple(
                by_identity[item.identity] for item in declared_evidence
            )
    else:
        evidence = ()

    try:
        adapter = _resolve_declared_adapters(lane)
    except AssayError as exc:
        # A-139: HEAD is already resolved above, so this is one of work
        # item 3's "later terminal paths" and MUST emit a complete
        # artifact. Letting the typed error reach main()'s handler would
        # give a consumer the right exit code and nothing to read -- the
        # exact shape of un-auditable refusal P17 exists to remove.
        #
        # P26/A-213: adapter refusal preserves already-resolved evidence --
        # it is never permission to erase it.
        detail = runner.announce_refusal(exc, diagnostics=err)
        verdict = runner.refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            detail=detail,
            argv_append=appended,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=__version__,
            judge_provenance=judge_provenance,
            evidence=evidence,
            declared_evidence=declared_evidence,
        )
    else:
        # (B028/DA-R9, SF-1) The SECOND half of DA-D10's intent: "the reserved
        # `--verdict-json` is WRITTEN" binds wherever the lane-wide deadline
        # expires, not only where `run_lane`'s own two catches can see it.
        #
        # R-1's round-1 measurement: with `budget = "0.001s"` the deadline is
        # already spent when `run_lane` calls `git.repo_top`, which is UPSTREAM
        # of both the direct-R0 `try` and `_run_higher_rigor_lane`'s outer
        # catch. The `AssayError` reached `main()`'s handler, which prints and
        # returns the exit code having written NOTHING -- on both dispatch
        # paths, and identically on the pre-B028 build, so B028's `CHANGES.md`
        # headline was broader than what shipped.
        #
        # One handler here covers both paths, because both go through this one
        # call. Deliberately the same shape as the attestation-timeout handler
        # above (A-213): scoped to `LANE_TIMEOUT` alone -- anything else still
        # propagates, because a bug must not be laundered into a verdict --
        # and refusing through `refuse_lane`, which renders the identical
        # payload-free pair on every declared level.
        #
        # No fact is missing: `commit`, `judge_provenance`, `evidence` and
        # `declared_evidence` are all resolved ABOVE this point by the
        # P26/A-212 sequence, so this verdict carries exactly what the
        # attestation-timeout verdict carries. The one thing it cannot carry
        # is a `CommandResult` -- the command never ran, which is what
        # `NO_MEASUREMENT`/`LANE_TIMEOUT` says.
        if pilot_selection is not None:
            try:
                assert pilot_state_dir is not None
                mutation_config = lane.judge.mutation
                if mutation_config is None:
                    raise LaneConfigError(
                        "--candidates-file requires a native R2 mutation lane"
                    )
                base_declaration = runner.resolve_base_declaration(
                    lane, getattr(args, "request_base", None)
                )
                discovered = _discover_plan_jobs(
                    lane_file,
                    lane,
                    adapter=adapter,
                    base_declaration=base_declaration,
                    operators=mutation_config.operators,
                    allow_dirty=getattr(args, "allow_dirty", False),
                    resolve_reuse_command=False,
                    deadline=deadline,
                    expected_commit=commit,
                )
                if discovered.jobs == mutation.UNSUPPORTED:
                    raise LaneConfigError(
                        "the native R2 adapter cannot enumerate candidates for this pilot"
                    )
                if len(discovered.jobs) > mutation_config.max_mutants:
                    raise LaneConfigError(
                        "the full native R2 plan exceeds judge.mutation.max_mutants; "
                        "cannot select from an incomplete plan"
                    )
                plan_ids = tuple(mutation.candidate_id(job) for job in discovered.jobs)
                pilot_jobs_by_id = {
                    identity: job
                    for identity, job in zip(plan_ids, discovered.jobs, strict=True)
                }
                known_order = tuple(
                    identity for identity in plan_ids if identity in pilot_selection
                )
                unknown = pilot_selection - set(plan_ids)
                selection_order = known_order + tuple(sorted(unknown))
                if not unknown:
                    selection_sha256 = mutation.plan_sha256(known_order)
                    if state_lock_guard is not None:
                        state_lock_guard()
                    _ensure_pilot_state(
                        pilot_state_dir,
                        lane=lane.name,
                        selection_sha256=selection_sha256,
                        lock_held=pilot_state_lock_held,
                        locked_root_fd=state_store_root_fd,
                    )
                    pilot_state_accepted = True
                if pilot_preflight_complete is not None:
                    pilot_preflight_complete()
            except AssayError as exc:
                return _deliver_refusal(exc)

        if _has_native_r2(lane):
            capability = resource_limits.inspect_current_cgroup_observation()
            if not capability.available:
                try:
                    if (
                        getattr(args, "reuse_from", None) is not None
                        and getattr(args, "shard", None) is not None
                    ):
                        raise LaneConfigError(
                            "--reuse-from cannot be combined with --shard; "
                            "selective reuse produces only a complete unsharded "
                            "campaign"
                        )
                    _parse_mutation_shard(getattr(args, "shard", None))
                    base_declaration = runner.resolve_base_declaration(
                        lane, getattr(args, "request_base", None)
                    )
                    discovered = _discover_plan_jobs(
                        lane_file,
                        lane,
                        adapter=adapter,
                        base_declaration=base_declaration,
                        operators=lane.judge.mutation.operators,
                        allow_dirty=getattr(args, "allow_dirty", False),
                        resolve_reuse_command=False,
                        deadline=deadline,
                    )
                except AssayError as exc:
                    # Without visibility, candidate selection is a required
                    # precondition. A failed discovery is not evidence that
                    # the lane has no candidates, so its own typed error
                    # takes precedence and R0 remains unstarted. A native
                    # mutation-discovery code is R2-claim-only in the wire
                    # schema; keep its diagnostic in the lane-wide cgroup
                    # refusal rather than emitting a claim that cannot verify.
                    if exc.reason_code is ReasonCode.MUTATION_DISCOVERY_FAILED:
                        exc = AssayError(
                            "cannot determine whether native R2 candidates are "
                            "selected because mutation discovery failed while "
                            "cgroup observation is unavailable; "
                            f"discovery error: {exc}; cgroup observation error: "
                            f"{capability.reason}",
                            outcome=Outcome.NO_MEASUREMENT,
                            reason_code=ReasonCode.CGROUP_OBSERVATION_UNAVAILABLE,
                        )
                    return _deliver_refusal(exc)

                if discovered.commit != commit:
                    return _deliver_refusal(
                        AssayError(
                            "HEAD changed while Assay checked native R2 candidate "
                            f"selection for cgroup visibility: resolved {commit}, "
                            f"then observed {discovered.commit}; rerun against one "
                            "unchanged commit",
                            outcome=Outcome.NO_MEASUREMENT,
                            reason_code=ReasonCode.HEAD_CHANGED,
                        )
                    )
                if discovered.jobs != mutation.UNSUPPORTED:
                    mutation_config = lane.judge.mutation
                    if len(discovered.jobs) <= mutation_config.max_mutants:
                        selected_jobs = _select_plan_jobs(
                            discovered.jobs, getattr(args, "shard", None)
                        )
                        if selected_jobs:
                            return _deliver_refusal(
                                AssayError(
                                    "native R2 candidates are selected, but this "
                                    "process cannot observe the complete cgroup v2 "
                                    "ancestor hierarchy required to distinguish "
                                    "resource-limit failures from mutant kills; run "
                                    "Assay where the complete hierarchy is visible; "
                                    "configure the runner so this process can observe "
                                    "its complete cgroup v2 ancestor hierarchy. "
                                    f"Observation failed: {capability.reason}",
                                    outcome=Outcome.NO_MEASUREMENT,
                                    reason_code=(
                                        ReasonCode.CGROUP_OBSERVATION_UNAVAILABLE
                                    ),
                                )
                            )
        try:
            verdict = runner.run_lane(
                lane,
                commit=commit,
                repo=lane_file.project_root,
                project_root=lane_file.project_root,
                adapter=adapter,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                argv_append=appended,
                evidence=evidence,
                declared_evidence=declared_evidence,
                deadline=deadline,
                resume=getattr(args, "resume", False),
                shard=getattr(args, "shard", None),
                candidate_selection=pilot_selection,
                expected_selection_sha256=selection_sha256,
                rejudge=getattr(args, "rejudge", None),
                rejudge_outcome=getattr(args, "rejudge_outcome", None),
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                # B031/A-320: opt-in, consumer-named, absent by default.
                # Resolved against the invoking CWD (like every other CLI path
                # argument), never against the project root, and never derived
                # from the lane name.
                #
                # (B064) The FILE is already open -- `_cmd_run` owns it, so
                # that `verdict_written` can be emitted after `write_verdict`
                # below -- so what travels here is the stream, not the path.
                # Passing both would re-open the same destination behind the
                # stream's back and emit a second `run` header into it.
                progress_stream=progress_stream,
                progress_heartbeat_seconds=progress_heartbeat_seconds,
                state_dir=state_dir,
                state_root_fd=state_store_root_fd,
                state_root_guard=state_lock_guard,
                cold_witness=getattr(args, "cold_witness", False),
                r2_manifest=r2_manifest,
                reuse_from=getattr(args, "reuse_from", None),
                # B019/A-328: the gate request's own comparison base, threaded
                # verbatim. `run_lane` decides whether this lane delegated to
                # it, and refuses every disagreement -- the CLI does not
                # adjudicate.
                request_base=getattr(args, "request_base", None),
                expected_plan_sha256=expected_plan_sha256,
                campaign_deadline_sha256=campaign_deadline_sha256,
                snapshot_limits=lane_file.snapshot_limits,
                allow_dirty=getattr(args, "allow_dirty", False),
                dirty_ignore=lane_file.dirty_ignore,
                # B032/A-322: where the `environment_command` probe's refusal
                # message goes. `run_lane` returns a Verdict and carries no
                # free-text field for a cause (A-138/A-170), so B010's "refuse
                # with a clear message" needs a stream, not a reason code.
                diagnostics=err,
            )
        except AssayError as exc:
            if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
                raise
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=evidence,
                declared_evidence=declared_evidence,
            )
    return _finish(verdict)


def _print_run_summary(verdict: Verdict, out: TextIO) -> None:
    label = verdict.outcome.value
    if verdict.reason_code is not None:
        label = f"{label}/{verdict.reason_code.value}"
    print(f"{verdict.lane}: {label} (exit {verdict.exit_code})", file=out)
    # (B146) A later refusal (e.g. DIRTY_TREE after a failing suite dirtied the
    # tree) can make the headline NO_MEASUREMENT although the R0 command
    # measurably failed. The headline stays the verdict's own pair; this line
    # states the measured failure and its first failing test, only when the
    # retained output names one, so a genuine no-measurement is never recast.
    failing_test = None
    if verdict.outcome is not Outcome.PASS and not any(
        claim.rigor == "R0" and claim.status is Outcome.PASS
        for claim in verdict.claims
    ):
        failing_test = failure_summary.first_failing_test(
            verdict.result_stdout_tail, verdict.result_stderr_tail
        )
    if failing_test is not None:
        print(f"  R0: FAIL (first failing test: {failing_test})", file=out)
    print(f"  commit: {verdict.commit}", file=out)
    print(f"  argv: {shlex.join(verdict.argv_effective or ())}", file=out)
    if verdict.argv_modified:
        print(f"    (appended: {shlex.join(verdict.argv_appended or ())})", file=out)


def _plan_candidate_id(job: mutation.MutantJob) -> str:
    return mutation.candidate_id(job)


def _has_native_r2(lane: Lane) -> bool:
    return (
        "R2" in lane.rigor
        and lane.judge is not None
        and lane.judge.mutation is not None
        and not lane.judge.mutation.is_ingested
    )


def _parse_mutation_shard(raw: str | None) -> tuple[int, int]:
    if raw is None:
        return 0, 1
    try:
        raw_index, raw_count = raw.split("/", 1)
        index = int(raw_index)
        count = int(raw_count)
    except ValueError as exc:
        raise LaneConfigError("--shard must have the form INDEX/COUNT") from exc
    try:
        mutation.select_mutation_shard((), index=index, count=count)
    except ValueError as exc:
        raise LaneConfigError(f"--shard {raw!r}: {exc}") from exc
    return index, count


def _select_plan_jobs(
    jobs: Sequence[mutation.MutantJob], shard: str | None
) -> tuple[mutation.MutantJob, ...]:
    index, count = _parse_mutation_shard(shard)
    selected = mutation.select_mutation_shard(
        [mutation.candidate_id(job) for job in jobs], index=index, count=count
    )
    return tuple(jobs[position] for position in selected)


PLAN_ESTIMATE_HINT = (
    "assay plan: estimated_serial_seconds and estimated_wall_seconds come from the "
    "declared budget_per_candidate (a 60 s placeholder when it is omitted, auto or "
    "none), not a measurement; for a measured projection run: assay analyze "
    "plan-estimate --plan-json PLAN --progress PROGRESS [--workers N]"
)


@record
class _PlanDiscovery:
    commit: str
    tree: str
    jobs: Any  # the FULL pre-shard tuple, or mutation.UNSUPPORTED unchanged
    worktree_integrity: Any
    reuse_command_plan: Any
    reuse_command_cwd: Path | None


def _discover_plan_jobs(
    lane_file: LaneFile,
    lane: Lane,
    *,
    adapter: LanguageAdapter,
    base_declaration: str | None,
    operators: tuple[str, ...],
    allow_dirty: bool,
    resolve_reuse_command: bool,
    deadline: runner.LaneDeadline | None = None,
    expected_commit: str | None = None,
) -> _PlanDiscovery:
    """Single planner-jobs extraction (C29); P6 reuses it."""
    if deadline is None:
        deadline = runner.LaneDeadline.start(
            budget_seconds=lane.budget_seconds, monotonic=time.monotonic
        )
    observed_commit = git.head_rev(
        lane_file.project_root, remaining=deadline.remaining
    )
    if expected_commit is not None and observed_commit != expected_commit:
        raise AssayError(
            "HEAD changed while Assay prepared the pilot candidate selection: "
            f"expected {expected_commit}, observed {observed_commit}",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.HEAD_CHANGED,
        )
    commit = observed_commit
    tree = git.run(
        lane_file.project_root,
        "rev-parse",
        f"{commit}^{{tree}}",
        remaining=deadline.remaining,
    ).strip()
    repo_top = git.repo_top(lane_file.project_root, remaining=deadline.remaining)
    worktree_integrity = runner._resolve_snapshot_worktree_integrity(
        repo=lane_file.project_root,
        repo_top=repo_top,
        project_root=lane_file.project_root,
        dirty_ignore=lane_file.dirty_ignore,
        allow_dirty=allow_dirty,
        remaining=deadline.remaining,
    )
    project_prefix = runner._resolved_project_prefix(repo_top, lane_file.project_root)
    runner._require_exact_source_roots_tracked(
        repo=lane_file.project_root,
        commit=commit,
        project_root=lane_file.project_root,
        project_prefix=project_prefix,
        source_roots=lane.judge.source_roots,
        remaining=deadline.remaining,
    )
    reuse_command_plan = None
    reuse_command_cwd = None
    if resolve_reuse_command:
        try:
            reuse_command_plan = runner.resolve_command_plan(
                lane,
                passthrough_source=os.environ,
                project_prefix=project_prefix,
            )
            reuse_command_cwd = runner.resolve_run_cwd(
                lane_file.project_root,
                reuse_command_plan,
            )
        except AssayError:
            # A plan is only a preview. If the effective command environment
            # cannot be resolved here, do not promise a witness replay.
            reuse_command_plan = None
            reuse_command_cwd = None
    snapshot_policy = runner._snapshot_policy_for_lane(lane)
    assert snapshot_policy is not None
    resolved_base = runner._resolve_declared_base(
        lane_file.project_root,
        base_declaration,
        remaining=deadline.remaining,
    )

    with tempfile.TemporaryDirectory(prefix="assay-plan-seed-") as raw_seed:
        seed_root = Path(raw_seed).resolve()
        spec = isolation.SnapshotSpec(
            repo_top=repo_top,
            commit=commit,
            project_prefix=project_prefix,
            scratch_root=seed_root,
            snapshot_policy=snapshot_policy,
            resolved_base=resolved_base,
            limits=lane_file.snapshot_limits,
        )
        with isolation.prepare_snapshot(spec, timeout=deadline.remaining()) as prepared:
            # B030/A-319: source roots are NOT relocated here, on purpose.
            # `_relocate_source_roots` respells `judge.source_root_paths`
            # against a MATERIALIZED snapshot's own project root, and the two
            # target resolvers below are handed
            # `snapshot_repo_top=prepared.spec.repo_top` -- the CONSUMER's
            # real repository top, since `plan` reads blobs out of the
            # prepared seed (`_read_prepared_source_text`) and never
            # materializes a snapshot at all. Relocating against a directory
            # that does not exist made
            # `resolve_mutation_targets`'s unconditional
            # `is_relative_to(root)` containment gate unsatisfiable, so every
            # lane planned as `candidate_count: 0`; a `whole_target` lane
            # failed outright naming the phantom path. The roots the gate
            # must be compared against here are the ones the lane actually
            # declares.
            source_root_paths = lane.judge.source_root_paths
            # B019/A-328: the identical declaration `run` resolves, through
            # the identical helper -- `plan` predicts a run, so a plan scoped
            # against a different base than the run it predicts would be
            # worse than emitting none.
            if lane.judge.mode == "whole_target":
                targets = runner._mutation_targets_whole(
                    prepared=prepared,
                    snapshot_repo_top=prepared.spec.repo_top,
                    project_prefix=project_prefix,
                    deadline=deadline,
                    adapter=adapter,
                    source_root_paths=source_root_paths,
                    targets=lane.judge.targets or (),
                )
            else:
                # B101 P1: the same carried-OID guard `run`'s R2 diff uses,
                # so the plan predicts the run exactly. This runs against
                # the consumer's repository (plan never materializes a
                # snapshot), where re-resolving would give the same OID;
                # consuming it keeps "resolved once" true here as well.
                checked = measurability.check_resolved_base_is_head(
                    prepared.spec.repo_top,
                    resolved_base,
                    remaining=deadline.remaining,
                )
                diff_text = git.run(
                    prepared.spec.repo_top,
                    "diff",
                    "--unified=0",
                    checked.base_rev,
                    checked.head_rev,
                    remaining=deadline.remaining,
                )
                added = diff.parse_added_lines(diff_text)
                targets = runner._mutation_targets_from_diff(
                    added,
                    prepared=prepared,
                    deadline=deadline,
                    adapter=adapter,
                    snapshot_repo_top=prepared.spec.repo_top,
                    source_root_paths=source_root_paths,
                )
            jobs = mutation.collect_mutation_sites(
                targets,
                adapter=adapter,
                operators=operators,
                limit=lane.judge.mutation.max_mutants + 1,
            )
    return _PlanDiscovery(
        commit=commit,
        tree=tree,
        jobs=jobs,
        worktree_integrity=worktree_integrity,
        reuse_command_plan=reuse_command_plan,
        reuse_command_cwd=reuse_command_cwd,
    )


class PlanRow(TypedDict):
    id: str
    path: str
    operator: str
    start_byte: int
    end_byte: int
    lineno: int
    description: str
    source_sha256: str
    mutated_file_sha256: str


def _plan_rows_from_jobs(jobs: Any) -> list[dict[str, Any]]:
    """One row per job: today's 7 keys plus the two candidate-identity digests."""
    rows: list[dict[str, Any]] = []
    for job in jobs:
        identity = mutation.candidate_identity_fields(job)
        rows.append(
            {
                "id": _plan_candidate_id(job),
                "path": job.path,
                "operator": job.site.operator,
                "start_byte": job.site.start_byte,
                "end_byte": job.site.end_byte,
                "lineno": job.site.lineno,
                "description": job.site.description,
                "source_sha256": identity["source_sha256"],
                "mutated_file_sha256": identity["mutated_file_sha256"],
            }
        )
    return rows


def plan_jobs(
    lane_file: LaneFile,
    lane: Lane,
    *,
    request_base: str | None = None,
    allow_dirty: bool = False,
) -> list[PlanRow] | Literal["UNSUPPORTED"]:
    """The full, unsharded plan rows of an R2 mutation lane, or ``"UNSUPPORTED"``.

    The one public planner entry the analysis package uses (C13); it refuses
    exactly as ``assay plan`` does.
    """
    if lane.judge is None or lane.judge.mutation is None or "R2" not in lane.rigor:
        raise LaneConfigError(f"lane {lane.name!r} does not declare an R2 mutation judge")
    adapter = _resolve_declared_adapters(lane)
    if adapter is None:
        raise LaneConfigError(f"lane {lane.name!r} resolves no mutation adapter")
    base_declaration = runner.resolve_base_declaration(lane, request_base)
    discovered = _discover_plan_jobs(
        lane_file,
        lane,
        adapter=adapter,
        base_declaration=base_declaration,
        operators=lane.judge.mutation.operators,
        allow_dirty=allow_dirty,
        resolve_reuse_command=False,
    )
    if discovered.jobs == mutation.UNSUPPORTED:
        return mutation.UNSUPPORTED
    return _plan_rows_from_jobs(discovered.jobs)


def _cmd_plan(args: argparse.Namespace, out: TextIO, err: TextIO | None = None) -> int:
    """Report a mutation lane's plan without executing it.

    ``--operators`` and ``--shard`` are planning-only selections. They do not
    change the lane declaration, so the same file can be planned and run with
    the matching run flag without inventing a second config surface.
    """
    lane_file = _resolve_lane_file(args.file)
    lane = lane_file.lane(args.lane)
    cold_witness = getattr(args, "cold_witness", False)
    if lane.judge is None or lane.judge.mutation is None or "R2" not in lane.rigor:
        raise LaneConfigError(f"lane {lane.name!r} does not declare an R2 mutation judge")
    reuse_source = None
    if args.reuse_from is not None:
        if args.shard is not None:
            raise LaneConfigError(
                "--reuse-from cannot be combined with --shard; selective reuse "
                "produces only a complete unsharded campaign"
            )
        if lane.judge.mutation.format is not None:
            raise LaneConfigError("--reuse-from requires a native R2 mutation lane")
        from .reuse import load_reuse_source

        reuse_source = load_reuse_source(args.reuse_from)
    adapter = _resolve_declared_adapters(lane)
    if adapter is None:
        raise LaneConfigError(f"lane {lane.name!r} resolves no mutation adapter")

    # B019/A-328: decided once, before any snapshot -- exactly where
    # `run_lane` decides it, and by the same function, so `plan` refuses a
    # delegating lane with no --request-base (and a non-delegating lane with
    # one) on identical terms rather than discovering the mismatch mid-walk.
    base_declaration = runner.resolve_base_declaration(
        lane, getattr(args, "request_base", None)
    )

    operators = lane.judge.mutation.operators
    shard_index: int | None = None
    shard_count: int | None = None
    if args.operators:
        requested = tuple(part.strip() for part in args.operators.split(",") if part.strip())
        # B034/A-326: the same refusal `config._load_mutation` gives a
        # DECLARED withdrawn operator. `--operators` is an override of that
        # declaration, so it has to close the same door -- otherwise the
        # withdrawal is enforced only for lanes that spell it in TOML.
        # (A-331) And it runs BEFORE the unknown check for the same reason
        # the loader's does: at the v8 cut these names left the catalogue,
        # so "unknown" would now swallow them and answer a stale-but-once-
        # legal spelling with the least useful of the two messages.
        withdrawn = tuple(
            name for name in requested if name in WITHDRAWN_MUTATION_OPERATORS
        )
        if withdrawn:
            raise LaneConfigError(
                f"withdrawn mutation operators: {', '.join(withdrawn)}; every "
                f"site they produced was already produced by "
                f"python:compare-swap at the same span with the same "
                f"replacement"
            )
        unknown = tuple(name for name in requested if name not in MUTATION_OPERATORS)
        if unknown or not requested:
            raise LaneConfigError(f"unknown mutation operators: {', '.join(unknown)}")
        operators = requested
    if args.shard:
        try:
            raw_index, raw_count = args.shard.split("/", 1)
            shard_index = int(raw_index)
            shard_count = int(raw_count)
        except ValueError as exc:
            raise LaneConfigError("--shard must have the form INDEX/COUNT") from exc
        try:
            # Zero-based, matching config.py/the verdict schema/CONSUMERS.md
            # -- never `- 1`. This is a dry bounds check only (an empty
            # candidate tuple); its return value is discarded.
            mutation.select_mutation_shard((), index=shard_index, count=shard_count)
        except ValueError as exc:
            raise LaneConfigError(f"--shard {args.shard!r}: {exc}") from exc

    discovered = _discover_plan_jobs(
        lane_file,
        lane,
        adapter=adapter,
        base_declaration=base_declaration,
        operators=operators,
        allow_dirty=getattr(args, "allow_dirty", False),
        resolve_reuse_command=reuse_source is not None or cold_witness,
    )
    commit = discovered.commit
    tree = discovered.tree
    jobs = discovered.jobs
    worktree_integrity = discovered.worktree_integrity
    reuse_command_plan = discovered.reuse_command_plan
    reuse_command_cwd = discovered.reuse_command_cwd
    cgroup_capability = (
        resource_limits.inspect_current_cgroup_observation()
        if _has_native_r2(lane)
        else None
    )
    resource_observation_applies_to = "none"

    if jobs == mutation.UNSUPPORTED:
        mutation_format = lane.judge.mutation.format
        if mutation_format is not None:
            unsupported_reason = (
                f"lane {lane.name!r} ingests R2 evidence in format "
                f"{mutation_format!r}; assay plan cannot enumerate candidates "
                f"from a foreign mutation report"
            )
        else:
            unsupported_reason = (
                f"lane {lane.name!r} uses an R2 adapter that cannot enumerate "
                f"native mutation candidates"
            )
        payload: dict[str, Any] = {
            "status": "unsupported",
            "lane": lane.name,
            "reason_code": "MUTATION_UNSUPPORTED",
            "reason": unsupported_reason,
            "worktree_integrity": (
                None if worktree_integrity is None else worktree_integrity.to_dict()
            ),
        }
    else:
        candidate_limit_exceeded = len(jobs) > lane.judge.mutation.max_mutants
        jobs = _select_plan_jobs(jobs, args.shard)
        if (
            _has_native_r2(lane)
            and not candidate_limit_exceeded
            and jobs
        ):
            resource_observation_applies_to = "selected_candidates"
        by_operator = Counter(job.site.operator for job in jobs)
        by_file = Counter(job.path for job in jobs)
        per_candidate = lane.judge.mutation.budget_per_candidate
        # (B091/D-23) `assay plan` never executes anything, so it cannot
        # measure the baseline "auto" would derive from -- an omitted key,
        # an explicit "auto", and the explicit "none" opt-out all fall back
        # to the same 60s-per-candidate estimate an undeclared bound always
        # used, which is honestly an upper-bound GUESS either way (this
        # function's own docstring already says so). Only an explicit
        # duration is a real number to multiply by.
        if per_candidate in (
            None,
            MUTATION_BUDGET_PER_CANDIDATE_AUTO,
            MUTATION_BUDGET_PER_CANDIDATE_NONE,
        ):
            per_candidate_seconds = 60.0
        else:
            per_candidate_seconds = parse_duration(per_candidate)
        serial_estimate = len(jobs) * per_candidate_seconds
        wall_estimate = serial_estimate / max(1, lane.judge.mutation.jobs)
        candidate_rows = _plan_rows_from_jobs(jobs)
        sequential_pytest_supported = False
        if reuse_source is not None:
            from .mutation_witness import supports_sequential_pytest
            from .reuse import classify_candidate

            sequential_pytest_supported = (
                worktree_integrity is None
                and reuse_command_plan is not None
                and reuse_command_cwd is not None
                and supports_sequential_pytest(
                    reuse_command_plan.argv_effective,
                    cwd=reuse_command_cwd,
                    env=reuse_command_plan.env_effective,
                )
            )
        if reuse_source is not None:
            for row in candidate_rows:
                classification, detail = classify_candidate(
                    reuse_source,
                    row["id"],
                    sequential_pytest_supported=sequential_pytest_supported,
                )
                row["reuse"] = {
                    "classification": classification,
                    "detail": detail,
                }
        payload = {
            "status": "ok",
            "lane": lane.name,
            "commit": commit,
            "tree": tree,
            "candidate_count": len(jobs),
            "max_mutants": lane.judge.mutation.max_mutants,
            "jobs": lane.judge.mutation.jobs,
            "shard": None if shard_index is None else f"{shard_index}/{shard_count}",
            "budget_per_candidate": per_candidate,
            "estimated_serial_seconds": round(serial_estimate, 3),
            "estimated_wall_seconds": round(wall_estimate, 3),
            "by_operator": dict(sorted(by_operator.items())),
            "by_file": dict(sorted(by_file.items())),
            "candidates": candidate_rows,
            "worktree_integrity": (
                None if worktree_integrity is None else worktree_integrity.to_dict()
            ),
        }
        if reuse_source is not None:
            from .reuse import prior_only_candidates, classify_candidate

            labels = [
                classify_candidate(
                    reuse_source,
                    candidate["id"],
                    sequential_pytest_supported=sequential_pytest_supported,
                )[0]
                for candidate in candidate_rows
            ]
            payload["reuse_from"] = {
                "path": str(reuse_source.path),
                "schema_version": reuse_source.schema_version,
                "verdict_sha256": reuse_source.sha256,
                "cold_start": reuse_source.cold_start,
                "complete_unsharded_native": reuse_source.complete_unsharded_native,
                "sequential_pytest_supported": sequential_pytest_supported,
                "prior_candidate_count": len(reuse_source.candidate_ids),
                "prior_only_candidates": prior_only_candidates(
                    reuse_source,
                    [_plan_candidate_id(job) for job in jobs],
                ),
                "classification_counts": dict(sorted(Counter(labels).items())),
            }
    if cold_witness:
        payload["cold_witness"] = _cold_witness_plan_preview(
            lane,
            adapter=adapter,
            command_plan=discovered.reuse_command_plan,
            cwd=discovered.reuse_command_cwd,
        )
    payload["resource_observation"] = {
        "applies_to": resource_observation_applies_to,
        "available": (
            cgroup_capability.available if cgroup_capability is not None else None
        ),
        "reason": (
            cgroup_capability.reason if cgroup_capability is not None else None
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    if payload["status"] == "ok" and err is not None:
        print(PLAN_ESTIMATE_HINT, file=err)
    return 0


def _cold_witness_plan_preview(
    lane: Lane,
    *,
    adapter: LanguageAdapter | None,
    command_plan: Any,
    cwd: Path | None,
) -> dict[str, Any]:
    """Render the static cold-witness eligibility facts used by ``assay plan``."""
    from .mutation_witness import cold_shape_refusal, supports_sequential_pytest
    from .r2_command import (
        R2_APPENDED,
        UnrecognizedCoverageOption,
        transform_argv,
    )

    if not (
        "R2" in lane.rigor
        and adapter is not None
        and adapter.name == "python"
        and lane.judge is not None
        and lane.judge.mutation is not None
        and not lane.judge.mutation.is_ingested
    ):
        refusal = "cold witness needs a native Python R2 lane"
        transformed: list[str] | None = None
    elif command_plan is None or cwd is None:
        refusal = "command environment could not be resolved"
        transformed = None
    else:
        try:
            transformed = list(transform_argv(command_plan.argv_declared))
        except UnrecognizedCoverageOption as exc:
            refusal = f"unrecognized coverage option {exc}"
            transformed = None
        else:
            refusal = cold_shape_refusal(
                command_plan.argv_declared,
                command_plan.env_effective,
                appended=command_plan.argv_appended,
            )
            if refusal is None and not supports_sequential_pytest(
                (*transformed, *command_plan.argv_appended, *R2_APPENDED),
                cwd=cwd,
                env=command_plan.env_effective,
            ):
                refusal = "not a sequential pytest command"
    return {
        "eligible": refusal is None,
        "refusal": refusal,
        "argv_transformed": transformed,
    }


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
        # (B067) An unbounded lane has no seconds to render, so it renders
        # none -- never "0s", and never a large finite stand-in.
        budget_render = (
            lane.budget
            if lane.budget_seconds is None
            else f"{lane.budget} ({lane.budget_seconds:g}s)"
        )
        print(
            f"  {name}  scope={lane.scope}  rigor={','.join(lane.rigor)}  "
            f"enforcement={lane.enforcement}  "
            f"budget={budget_render}  "
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


#: (B044) The document's own top-level version. Bumped ONLY when an existing
#: key's MEANING changes -- adding a key (B043's `cwd`, B041's `link_paths`,
#: B045's `coverage.producer`, all `null`/`[]` here since this build does not
#: implement them yet) is additive and does not move this number, exactly as
#: `LANE_FILE_NAME`'s own `schema_version` distinguishes a meaning change from
#: an addition (A-thread in `config.py`).
LANE_INVENTORY_SCHEMA_VERSION = 1


def _render_lanes_json(lane_file: LaneFile, out: TextIO) -> None:
    """B044 -- ``assay lanes --json``: one machine-readable inventory
    document, so a gate tool (CIU stage 12, CIU-72) can learn what a project
    declared without re-parsing ``assay.toml`` itself and without asking the
    judge to run anything.

    **Every field has exactly one producer** -- the loaded ``Lane``/
    ``JudgeConfig`` (this process's own :mod:`assay.config` parse) or this
    build's own closed registry (:func:`_built_in_registry`, the identical
    object ``assay run`` resolves an adapter through) -- nothing here
    re-derives a fact from the raw TOML text a second, independent way.
    ``rigor_reachable``/``external_tools`` come from the registry entry (or
    are empty when the declared language is not registered at all -- an
    absent capability, not a refusal: unlike ``assay run``, this subcommand
    never raises for a rigor level or language this build cannot reach, so a
    gate can compare ``rigor`` against ``rigor_reachable`` itself instead of
    discovering the mismatch only when a real run refuses). Like the text
    form above, this renders nothing that would let a lane's declared argv
    run, and writes no verdict artifact (A-054).

    ``base_source`` resolves ``JudgeConfig.base_source``'s own documented
    absent-means-``"declared"`` default (A-328) rather than passing the raw
    ``None`` through -- the one place this function derives instead of
    reads, and it derives only the ALREADY-established meaning of that
    field's own absence, never a new one (A-347 records why: the whole point
    of this inventory is to let a gate tell "this lane owns its base" apart
    from "this lane delegates it" without reimplementing A-328 itself,
    which is exactly the four-copies divergence this project exists to
    close one layer up). It is ``null`` where the lane has no base concept
    at all -- no ``judge`` table, ``judge.mode == "whole_target"``, or
    neither R1 nor R2 declared -- mirroring :mod:`assay.config`'s own load
    time refusal of ``base_source`` in exactly those three shapes.

    A lane file that fails to load raises before this function is ever
    called (:func:`_resolve_lane_file` runs first in :func:`main`), so the
    existing ``except AssayError`` in :func:`main` already gives this
    subcommand its required exit 2 with an empty stdout and no partial JSON
    -- nothing below needs its own try/except for that.
    """
    built_in = _built_in_registry()
    cgroup_capability = (
        resource_limits.inspect_current_cgroup_observation()
        if any(_has_native_r2(lane) for lane in lane_file.lanes.values())
        else None
    )
    document = {
        "inventory_schema": LANE_INVENTORY_SCHEMA_VERSION,
        "assay_version": __version__,
        "lanes": [
            _lane_inventory_entry(
                lane, built_in, cgroup_capability=cgroup_capability
            )
            for lane in lane_file.lanes.values()
        ],
    }
    print(json.dumps(document, indent=2, sort_keys=True), file=out)


def _lane_inventory_entry(
    lane: Lane,
    built_in: registry.Registry,
    *,
    cgroup_capability: resource_limits.ResourceObservationCapability | None = None,
) -> dict[str, Any]:
    """One lane's own entry in :func:`_render_lanes_json`'s document."""
    judge = lane.judge
    language = judge.language if judge is not None else None
    entry = built_in.entries.get(language) if language is not None else None
    rigor_reachable = sorted(entry.rigor) if entry is not None else []
    external_tools = list(entry.adapter.external_tools) if entry is not None else []

    coverage: dict[str, Any] | None = None
    if judge is not None and judge.coverage is not None:
        coverage = {
            "format": judge.coverage.format,
            "artifact": judge.coverage.artifact,
            # (B045/schema v9) the DECLARED producer, or `null` when the
            # format allows the omission and the lane took it. Wave A shipped
            # this key as an unconditional `null` placeholder so a v9-aware
            # consumer's key set would not have to branch on which assay
            # version produced the document; Wave B wires it to the real
            # declared value. The key's MEANING is unchanged ("the declared
            # producer, or null"), so `inventory_schema` does not move
            # (A-349's own stability rule).
            "producer": judge.coverage.producer,
        }

    mutation = (
        judge.mutation.as_declared()
        if judge is not None and judge.mutation is not None
        else None
    )
    canary = (
        judge.canary.as_declared()
        if judge is not None and judge.canary is not None
        else None
    )

    base_source: str | None = None
    if (
        judge is not None
        and judge.mode != "whole_target"
        and ("R1" in lane.rigor or "R2" in lane.rigor)
    ):
        base_source = judge.base_source or "declared"

    return {
        "name": lane.name,
        "scope": lane.scope,
        "rigor": list(lane.rigor),
        "enforcement": lane.enforcement,
        "language": language,
        "rigor_reachable": rigor_reachable,
        "coverage": coverage,
        "mutation": mutation,
        "canary": canary,
        "base_source": base_source,
        "external_tools": external_tools,
        "argv0": lane.argv[0],
        "env_required": list(lane.env_required),
        "environment_command": lane.environment_command is not None,
        "infrastructure_facts": sorted(lane.infrastructure)
        if lane.infrastructure
        else [],
        "budget": lane.budget,
        # (B043/schema v9) the declared working directory, or `null` when the
        # lane declared none. `null` is the honest answer for that lane, not
        # a placeholder: `"."` would be a value the file never wrote.
        "cwd": lane.cwd,
        # (B041(b)/schema v9) the declared link_paths, `[]` when the lane
        # declared none. A non-empty list tells a gate that this lane's
        # snapshot will NOT be purely committed objects, and that the listed
        # directories must exist in the environment before the lane runs.
        "link_paths": list(lane.isolation.link_paths) if lane.isolation else [],
        "snapshot_selection": (
            lane.isolation.snapshot_selection if lane.isolation is not None else None
        ),
        "resource_observation": {
            "applies_to": (
                "conditional_native_r2_candidates"
                if _has_native_r2(lane)
                else "none"
            ),
            "available": (
                cgroup_capability.available
                if _has_native_r2(lane) and cgroup_capability is not None
                else None
            ),
            "reason": (
                cgroup_capability.reason
                if _has_native_r2(lane) and cgroup_capability is not None
                else None
            ),
        },
    }


if __name__ == "__main__":
    sys.exit(main())
