"""Execute one declared command (R0), and build the verdict that describes it.

The claim this module exists to defend: **the command that actually ran is
recorded honestly on every terminal path.** assay is not a test runner (§7):
it executes exactly one declared ``argv`` and never discovers, selects,
orders, or retries anything. Flags may be *appended by the caller*, never
*derived* by assay (A-036), and an append the lane did not permit is refused
before the process starts (A-095).

Four functions are the module's real surface, deliberately kept as separate
steps rather than one run-and-build-verdict function (A-094):

* :func:`execute_command` — the R0 step. Resolves what will run (a
  :class:`CommandPlan`), then actually runs it (or refuses to, or fails to),
  and returns a :class:`CommandResult` on every terminal path. This is the
  exact seam P05's :func:`evaluate_r1` is called after, before handing an
  expanded ``claims`` tuple to :func:`assemble_verdict`.
* :func:`build_r0_claim` — the R0 :class:`~assay.verdict.Claim` from a
  :class:`CommandResult`. A one-line pure mapping, kept separate so nothing
  has to reach into :func:`execute_command`'s internals to get it.
* :func:`evaluate_r1` (P05) — the R1 step: P02's two measurability guards,
  then P03's ``EMPTY_COVERAGE`` guard, short-circuiting on any of the three
  with a ``NO_MEASUREMENT`` claim (A-090); otherwise runs
  :func:`assay.evaluate.evaluate_coverage`'s four-way union and builds the
  R1 :class:`~assay.verdict.Claim`. Like :func:`execute_command`, this never
  raises for a judged outcome — a guard tripping is a returned ``Claim``,
  not an exception; only a genuinely structural failure (an unreadable
  artifact, a git failure) propagates.
* :func:`assemble_verdict` — final verdict construction (A-023's rollup,
  A-036's transparency fields, A-024's one-claim-per-declared-rigor). Takes
  the *whole* ``claims`` tuple, not just R0's, so a caller appends the R1
  claim to the tuple it passes rather than needing a different function.

**The injectable process/budget boundary** (work item 1): the real
``subprocess`` is :func:`default_process_runner`, the seam's default: a
test-supplied replacement raising ``FileNotFoundError`` (an executable that
cannot be started) or ``subprocess.TimeoutExpired`` (a budget that expired)
proves :func:`execute_command`'s ``EXEC_FAILED``/``BUDGET_EXCEEDED`` mapping
without a real missing binary or a real wall-clock wait — AUTHORING.md §3b.A's
constraint on timing-dependent oracles. The default runner still exists and is
exercised directly (not only mocked away) by the environment-isolation
proof (O2): ``subprocess.run`` never merges a non-``None`` ``env`` with the
parent's, so passing exactly :attr:`CommandPlan.env_effective` is what makes
"no ambient leak" true by construction, not by convention.

What :func:`execute_command` never does, on purpose: it does not read
``judge`` config, does not parse coverage, does not compute R1-R3.
``NO_MEASUREMENT`` is not a status it can ever return — that outcome is
:func:`evaluate_r1`'s alone to produce, and :func:`execute_command` has no
path that reaches it.

Every rejection here raises :class:`~assay.errors.AssayError` directly; there
is no locally-defined exception type (A-092 — ``errors.py`` is outside this
package's ``scope.touch``).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence, TextIO

from . import coverage, diff, git, measurability
from .adapters.base import LanguageAdapter
from .config import Lane
from .errors import AssayError, Outcome, ReasonCode
from .evaluate import evaluate_coverage
from .verdict import Claim, Coverage, Evidence, EvidenceDeclaration, Verdict, iso_utc, rollup

__all__ = [
    "CommandPlan",
    "CommandResult",
    "ProcessRunner",
    "assemble_verdict",
    "build_r0_claim",
    "default_process_runner",
    "evaluate_r1",
    "execute_command",
    "resolve_command_plan",
    "write_verdict",
]


class ProcessRunner(Protocol):
    """The injectable process boundary. :func:`default_process_runner` is the
    real implementation; a test substitutes a callable with this same shape.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


#: The injectable clock. :func:`execute_command` calls this twice (before and
#: after the child runs) to timestamp the claim -- never ``time.sleep`` or an
#: elapsed-time comparison, so a test can supply a fixed sequence of moments
#: instead of waiting on a real one (AUTHORING.md §3b.A).
Clock = Callable[[], datetime]


def default_process_runner(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """The real boundary: an actual child process, the seam's default.

    ``env`` REPLACES the child's environment completely -- ``subprocess.run``
    never merges a non-``None`` ``env`` with the parent's -- which is what
    makes "the child receives exactly lane env plus declared passthrough, and
    no ambient sentinel" true by construction rather than by convention (O2).
    """
    return subprocess.run(
        list(argv),
        env=dict(env),
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, kw_only=True)
class CommandPlan:
    """What WILL run, resolved before anything executes.

    ``argv_effective`` is always ``argv_declared + argv_appended`` -- even on
    a path where the append is later refused -- because :class:`~assay.verdict.Verdict`
    itself enforces that equality (A-036); recording the attempted effective
    argv is what lets a consumer see *what was asked for*, not only what ran.
    """

    argv_declared: tuple[str, ...]
    argv_appended: tuple[str, ...]
    argv_effective: tuple[str, ...]
    #: exactly ``lane.env``, verbatim (A-019: declared-only).
    env_declared: Mapping[str, str]
    #: ``env_declared`` plus whichever ``env_passthrough`` names were actually
    #: present in the passthrough source. Nothing else -- no ambient merge.
    env_effective: Mapping[str, str]


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    """The real outcome of the R0 step -- append rejected, executable
    missing, budget exceeded, command failed, or command passed. Exactly what
    A-094 requires as separable output: everything final verdict assembly
    needs to build the R0 claim and the transparency fields, with nothing
    about R1 evaluation baked in.
    """

    plan: CommandPlan
    #: one of PASS, FAIL, ERROR, BUDGET_EXCEEDED -- never NO_MEASUREMENT or
    #: INCONCLUSIVE; those are not R0-producible outcomes in this package.
    outcome: Outcome
    reason_code: ReasonCode | None
    #: ``None`` when the process never started (append refused, exec failed,
    #: or the budget expired before it returned).
    returncode: int | None
    started: str
    ended: str


def resolve_command_plan(
    lane: Lane,
    *,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
) -> CommandPlan:
    """Resolve what will run. Pure: never raises, never launches anything.

    *passthrough_source* is the ambient environment to read
    ``lane.env_passthrough`` names FROM -- ``os.environ`` by default, but
    injectable so a test proves "no ambient leak" without mutating real
    process-global state (AUTHORING.md §3b.B). Only names the lane actually
    declared in ``env_passthrough`` AND that are present in the source are
    carried into ``env_effective``; everything else in the source is invisible
    to the child, matching A-019's "declared-only" env contract.
    """
    source = os.environ if passthrough_source is None else passthrough_source
    env_effective: dict[str, str] = dict(lane.env)
    for name in lane.env_passthrough:
        if name in source:
            env_effective[name] = source[name]
    argv_declared = tuple(lane.argv)
    argv_appended = tuple(argv_append)
    return CommandPlan(
        argv_declared=argv_declared,
        argv_appended=argv_appended,
        argv_effective=argv_declared + argv_appended,
        env_declared=lane.env,
        env_effective=MappingProxyType(env_effective),
    )


def execute_command(
    lane: Lane,
    *,
    argv_append: Sequence[str] = (),
    cwd: Path,
    passthrough_source: Mapping[str, str] | None = None,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CommandResult:
    """The R0 step (A-094): resolve the plan, then run it, and return a
    :class:`CommandResult` on every terminal path.

    Ordering, all before anything launches:

    1. :func:`resolve_command_plan` -- pure, cannot fail.
    2. the append-permission gate (A-095): if *argv_append* is non-empty and
       ``lane.allow_argv_append`` is false, this returns
       ``ERROR``/``EXEC_FAILED`` WITHOUT calling *process_runner* at all --
       the process never starts, which is A-073's own definition of
       ``EXEC_FAILED``.

    Then *process_runner* is called with the plan's effective argv and
    environment. ``OSError`` (the parent of ``FileNotFoundError`` --
    "no such file", "not executable", "not a directory": every shape of
    "the process could not be started") maps to ``ERROR``/``EXEC_FAILED``.
    ``subprocess.TimeoutExpired`` maps to ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``.
    Otherwise the exit code decides: 0 is ``PASS``; anything else is
    ``FAIL``/``COMMAND_FAILED`` (A-073 -- an ordinary non-zero exit is a
    judged R0 FAIL, never ``EXEC_FAILED``, and never a universal PASS).
    """
    plan = resolve_command_plan(
        lane, argv_append=argv_append, passthrough_source=passthrough_source
    )
    started_at = clock()

    if plan.argv_appended and not lane.allow_argv_append:
        ended_at = clock()
        return CommandResult(
            plan=plan,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            started=iso_utc(started_at),
            ended=iso_utc(ended_at),
        )

    try:
        proc = process_runner(
            plan.argv_effective,
            env=plan.env_effective,
            cwd=cwd,
            timeout=lane.budget_seconds,
        )
    except subprocess.TimeoutExpired:
        ended_at = clock()
        return CommandResult(
            plan=plan,
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
            returncode=None,
            started=iso_utc(started_at),
            ended=iso_utc(ended_at),
        )
    except OSError:
        ended_at = clock()
        return CommandResult(
            plan=plan,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            started=iso_utc(started_at),
            ended=iso_utc(ended_at),
        )

    ended_at = clock()
    if proc.returncode == 0:
        return CommandResult(
            plan=plan,
            outcome=Outcome.PASS,
            reason_code=None,
            returncode=proc.returncode,
            started=iso_utc(started_at),
            ended=iso_utc(ended_at),
        )
    return CommandResult(
        plan=plan,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=proc.returncode,
        started=iso_utc(started_at),
        ended=iso_utc(ended_at),
    )


def build_r0_claim(result: CommandResult) -> Claim:
    """The R0 :class:`~assay.verdict.Claim` from a :class:`CommandResult`.

    A pure, one-line mapping -- kept separate from :func:`execute_command` so
    a caller (P05) that already has a :class:`CommandResult` never has to
    reconstruct this by hand.
    """
    return Claim(
        rigor="R0",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
    )


def _resolve_artifact_path(artifact: str, project_root: Path) -> Path:
    """*artifact* resolves against *project_root* -- the same directory the
    lane's own argv ran in (:func:`execute_command` is always called with
    ``cwd=project_root``), so a relative ``--cov-report=json:cov.json``
    names the same file here that the lane's own command just wrote."""
    candidate = Path(artifact)
    return candidate if candidate.is_absolute() else project_root / candidate


def evaluate_r1(
    lane: Lane,
    *,
    repo: Path,
    project_root: Path,
    base: str,
    adapter: LanguageAdapter,
) -> Claim:
    """The R1 step (A-090, A-094): P02's two measurability guards, then
    P03's ``EMPTY_COVERAGE`` guard, short-circuiting on the first that trips
    -- then the four-way union (:func:`assay.evaluate.evaluate_coverage`).

    Mirrors :func:`execute_command`'s own contract: never raises for a
    JUDGED outcome. A guard tripping with ``NO_MEASUREMENT`` is caught and
    returned as an R1 :class:`~assay.verdict.Claim` carrying that status and
    reason_code and NO ``coverage`` payload -- omitted, not zeroed (A-025),
    exactly what :class:`~assay.verdict.Claim` itself would refuse to
    construct otherwise. Any OTHER :class:`~assay.errors.AssayError` (a
    genuinely broken coverage artifact -- ``FORMAT_MISMATCH``,
    ``UNREADABLE_ARTIFACT`` -- or a git failure) is a structural failure,
    not a judged outcome, and propagates uncaught, the same way
    ``git.head_rev`` failing propagates out of ``cli.py``'s call chain today.

    *lane.judge* must be fully resolved for R1 (every field
    ``JUDGE_FIELDS_BY_RIGOR["R1"]`` names) -- guaranteed by
    :mod:`assay.config`'s loader for any lane that actually declares R1
    rigor, which is the only way a caller should reach this function.
    *base* is the declared comparison ref (no lane-config field or CLI flag
    names it yet -- a caller supplies it directly, the same way P02's own
    tests do).
    """
    judge = lane.judge
    try:
        measurability.check_dirty_tree(repo, judge.source_root_paths)
        resolved = measurability.check_base_is_head(repo, base)
        artifact_path = _resolve_artifact_path(judge.coverage.artifact, project_root)
        profile = coverage.read_coverage_artifact(
            artifact_path, declared_format=judge.coverage.format
        )
        coverage.check_empty_coverage(profile)
    except AssayError as exc:
        if exc.outcome is Outcome.NO_MEASUREMENT:
            return Claim(
                rigor="R1",
                source="computed",
                status=exc.outcome,
                verified_by_assay=True,
                reason_code=exc.reason_code,
            )
        raise

    repo_top = git.repo_top(repo)
    diff_text = git.run(repo, "diff", "--unified=0", resolved.base_rev, resolved.head_rev)
    added = diff.parse_added_lines(diff_text)

    def read_source_text(path: str) -> str:
        return (repo_top / path).read_text(encoding="utf-8")

    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=repo_top,
        source_root_paths=judge.source_root_paths,
        fail_under=judge.fail_under,
        allow_excluded=judge.allow_excluded,
        read_source_text=read_source_text,
    )
    return Claim(
        rigor="R1",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
        coverage=Coverage(
            covered=result.covered,
            changed_executable=result.changed_executable,
            pct=result.pct,
            considered=result.considered,
            missing_lines=result.missing_lines,
            files_missing_coverage=result.files_missing_coverage,
            unclassified_lines=result.unclassified_lines,
            files_with_unclassified_lines=result.files_with_unclassified_lines,
        ),
    )


def assemble_verdict(
    *,
    lane: Lane,
    commit: str,
    result: CommandResult,
    claims: tuple[Claim, ...],
    assay_version: str,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
) -> Verdict:
    """Final verdict assembly (A-094): separable from :func:`execute_command`.

    Takes the WHOLE ``claims`` tuple (not just R0's), timing and the resolved
    plan from *result*. P05 inserts its own R1 evaluation between calling
    :func:`execute_command`/:func:`build_r0_claim` and calling this function,
    and passes ``claims=(r0_claim, r1_claim)`` -- no restructuring of this
    function required (A-094's whole point). P10 (this addition) is the same
    shape one level over: *evidence* and *declared_evidence* default to empty
    tuples, so every existing caller (through P09) is unaffected, and a
    caller that has resolved attested evidence (:func:`assay.attestation.
    load_attested_evidence`) passes it straight through without this
    function needing to know anything about HOW it was produced.

    Refuses (``ERROR``/``BAD_LANE_CONFIG``) BEFORE constructing a
    :class:`~assay.verdict.Verdict` if *claims* does not cover every level in
    ``lane.rigor``, or if *evidence* does not cover *declared_evidence*
    exactly -- this build (through P04) only ever passes an R0 claim, so a
    lane declaring ``rigor = ["R0", "R1"]`` is refused here rather than
    reaching :class:`~assay.verdict.Verdict`'s own internal invariant, which
    raises a bare ``ValueError`` no caller catches; the identical reasoning
    applies to a caller that declares evidence it never resolved (or resolves
    evidence it never declared). Deliberately placed here rather than in
    ``cli.py``: this module is in every later producer package's
    ``scope.touch`` (P05, P08, P09, P10...), so the guard self-obsoletes --
    once a package supplies the missing claim or evidence, ``missing`` is
    empty and this never fires for that identity again, with no file only
    this package could touch left to update.

    The verdict's own ``outcome`` and ``reason_code`` are otherwise DERIVED
    from ``claims`` AND ``evidence`` together via :func:`~assay.verdict.rollup`
    (A-023), never chosen independently -- :class:`~assay.verdict.Verdict`
    would refuse a mismatch anyway, but deriving it here means this function
    cannot construct one. An ``ERROR``/``UNREADABLE_ARTIFACT`` evidence entry
    (a broken attestation, A-110) outranks every claim's own status the same
    way it would if it were a claim -- ``rollup`` does not distinguish which
    array a status came from, matching :class:`~assay.verdict.Verdict`'s own
    ``_check_outcome_agrees_with_rollup``.
    """
    covered = {claim.rigor for claim in claims}
    missing = [level for level in lane.rigor if level not in covered]
    if missing:
        raise AssayError(
            f"lane {lane.name!r} declares rigor {list(lane.rigor)} but this "
            f"assay build only computed claims for {sorted(covered)} -- "
            f"{missing} evaluation lands in a later package. Refusing before "
            f"constructing an incomplete verdict.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    declared_identities = [item.identity for item in declared_evidence]
    resolved_identities = [item.identity for item in evidence]
    missing_evidence = [
        identity for identity in declared_identities if identity not in resolved_identities
    ]
    surplus_evidence = [
        identity for identity in resolved_identities if identity not in declared_identities
    ]
    if missing_evidence or surplus_evidence:
        raise AssayError(
            f"declared_evidence {declared_identities} and evidence "
            f"{resolved_identities} do not cover each other exactly -- "
            f"missing: {missing_evidence}, surplus: {surplus_evidence}. "
            f"Refusing before constructing an incomplete verdict.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    statuses = [claim.status for claim in claims]
    statuses.extend(item.status for item in evidence)
    outcome = rollup(statuses)
    reason_code = None
    if outcome is not Outcome.PASS:
        reason_code = next(
            item.reason_code for item in (*claims, *evidence) if item.status is outcome
        )
    plan = result.plan
    return Verdict(
        lane=lane.name,
        commit=commit,
        outcome=outcome,
        reason_code=reason_code,
        started=result.started,
        ended=result.ended,
        assay_version=assay_version,
        declared_rigor=lane.rigor,
        declared_evidence=declared_evidence,
        argv_declared=plan.argv_declared,
        argv_appended=plan.argv_appended,
        argv_effective=plan.argv_effective,
        env_declared=plan.env_declared,
        env_effective=plan.env_effective,
        claims=claims,
        evidence=evidence,
    )


def write_verdict(
    verdict: Verdict,
    target: str,
    *,
    stdout: TextIO,
    replace: Callable[[str, str], None] = os.replace,
) -> None:
    """Emit *verdict* to *target* (A-028): a path, written atomically, or
    ``"-"`` for *stdout*.

    The atomic write is: serialise to a sibling temp file in the SAME
    directory, then ``replace`` it onto the real path in one step. *replace*
    is injectable so a test can prove the failure mode without corrupting a
    real filesystem: when it raises, the temp file is removed and the
    exception propagates -- the target path, if it already held a prior
    artifact, is untouched, because the failing step never wrote to it.
    """
    if target == "-":
        stdout.write(verdict.to_json())
        return
    path = Path(target)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(verdict.to_json(), encoding="utf-8")
    try:
        replace(str(tmp_path), str(path))
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
