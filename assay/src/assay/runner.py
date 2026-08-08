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
* :func:`evaluate_r1` (P05, widened P17) — the R1 step: P02's two
  measurability guards, then P03's ``EMPTY_COVERAGE`` guard, then reading
  and diffing against the resolved base, short-circuiting on the first
  :class:`~assay.errors.AssayError` any of them raises with a matching
  R1 :class:`~assay.verdict.Claim` (A-090, widened by P17 work item 6 to
  every ``AssayError`` this guard sequence can raise, not only the three
  ``NO_MEASUREMENT`` causes — see its own docstring). Like
  :func:`execute_command`, this never raises for a judged outcome; only a
  genuine programmer error (not an ``AssayError`` at all) propagates.
* :func:`assemble_verdict` — final verdict construction (A-023's rollup,
  A-036's transparency fields, A-024's one-claim-per-declared-rigor). Takes
  the *whole* ``claims`` tuple, not just R0's, so a caller appends the R1
  claim to the tuple it passes rather than needing a different function.
  P10 threads *evidence*/*declared_evidence* through the same way (both
  default to empty, so every earlier caller is unaffected); P12 adds one
  more optional parameter, *mutation_claim* (A-119), for the R2 claim
  :func:`assay.mutation.build_mutation_claim` produces — appended to
  *claims* here rather than requiring every caller to remember to fold it
  in by hand. P17 adds *ended* (default ``None``, so every earlier caller
  is unaffected) — see its own docstring.
* :func:`run_lane` (P17) — the real pipeline :mod:`assay.cli` calls: ties
  the four functions above together into one commit-bound operation, plus
  the prerequisite checks (whole-tree cleanliness, coverage-artifact
  safety) none of them owns alone. See its own docstring for the full
  ordering.

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
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence, TextIO

from . import coverage, diff, git, measurability, mutation
from .adapters.base import LanguageAdapter
from .config import Lane
from .errors import AssayError, Outcome, ReasonCode
from .evaluate import evaluate_coverage
from .verdict import (
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    Verdict,
    iso_utc,
    rollup,
)

__all__ = [
    "CommandPlan",
    "CommandResult",
    "ProcessRunner",
    "assemble_verdict",
    "build_r0_claim",
    "default_process_runner",
    "evaluate_r1",
    "execute_command",
    "refuse_lane",
    "resolve_command_plan",
    "run_lane",
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
    #: PASS, FAIL, ERROR or BUDGET_EXCEEDED for :func:`execute_command`'s
    #: own output -- never NO_MEASUREMENT or INCONCLUSIVE, those are not
    #: R0-producible outcomes in this package. :func:`run_lane` (P17) MAY
    #: construct a :class:`CommandResult` with ``ERROR`` or
    #: ``NO_MEASUREMENT`` directly, WITHOUT calling
    #: :func:`execute_command` at all, to represent "a prerequisite failed
    #: and the command never launched" (work items 3/5) -- the plan and
    #: timing still need somewhere honest to live, and this is that
    #: somewhere; :func:`build_r0_claim` is never called on one of these
    #: (:func:`run_lane` builds the refusal claims by hand instead), so no
    #: caller reads this field back out as if it were R0's own judgement.
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
    on_base_resolved: Callable[[str], None] | None = None,
    on_added_resolved: Callable[[diff.AddedLines], None] | None = None,
) -> Claim:
    """The R1 step (A-090, A-094): P02's two measurability guards, then
    P03's ``EMPTY_COVERAGE`` guard, short-circuiting on the first that trips
    -- then the four-way union (:func:`assay.evaluate.evaluate_coverage`).

    Mirrors :func:`execute_command`'s own contract: never raises for a
    JUDGED outcome. Every :class:`~assay.errors.AssayError` this function's
    own guard sequence can raise -- P02's two measurability guards, P03's
    ``EMPTY_COVERAGE`` guard, a broken coverage artifact
    (``FORMAT_MISMATCH``, ``UNREADABLE_ARTIFACT``), or a git failure
    resolving the base or diffing it (``GIT_FAILED``) -- is caught and
    returned as a complete R1 :class:`~assay.verdict.Claim` carrying that
    exact status and reason_code (P17 work item 6, closing three of sol's
    "permanently unreachable" pairs, A-O15/STATE.md). NO ``coverage``
    payload accompanies any of them -- omitted, not zeroed (A-025 for the
    three ``NO_MEASUREMENT`` causes; A-136 for the three ``ERROR`` ones,
    which are payload-free by construction regardless of cause. Only a
    non-:class:`AssayError` exception (a genuine programmer error) still
    propagates uncaught -- this function never invents a generic PASS/ERROR
    fallback for one.

    *lane.judge* must be fully resolved for R1 (every field
    ``JUDGE_FIELDS_BY_RIGOR["R1"]`` names) -- guaranteed by
    :mod:`assay.config`'s loader for any lane that actually declares R1
    rigor, which is the only way a caller should reach this function.
    *base* is the declared comparison ref, exactly as ``judge.base``
    declares it (a caller resolves nothing before passing it in).

    *on_base_resolved* (P17), if given, is called EXACTLY ONCE with the
    resolved full base commit (:attr:`~assay.measurability.ResolvedBase.
    base_rev`) the moment :func:`assay.measurability.check_base_is_head`
    produces it -- never on a path where that guard itself trips. This is
    how :func:`run_lane` builds ``judgment.r1.base`` (P16's "the FULL
    resolved comparison commit, never the lane's own possibly-symbolic
    ``base`` ref") WITHOUT this function's own signature or return type
    changing: :mod:`assay.canary` calls this function directly today and
    expects a bare :class:`~assay.verdict.Claim` back, so a second return
    value is not available here, and re-resolving the base a second time
    the caller's own side would violate O2's "the comparison base resolves
    once" -- an additive, default-``None`` callback is the only channel
    that satisfies both constraints simultaneously.

    *on_added_resolved* (P18), the identical mechanism one field over: if
    given, called EXACTLY ONCE with the resolved
    :class:`~assay.diff.AddedLines` the moment
    :func:`assay.diff.parse_added_lines` produces it -- never on a path
    where an earlier guard (including the coverage-artifact-specific ones
    this function alone owns) trips first. This is how :func:`run_lane`
    lets a DECLARED R2 reuse R1's own resolved diff instead of diffing the
    same ``base``..``HEAD`` pair a second time, for the identical
    frozen-signature reason *on_base_resolved* exists: :mod:`assay.canary`
    already depends on this function returning a bare :class:`Claim`.
    """
    judge = lane.judge
    try:
        measurability.check_dirty_tree(repo, judge.source_root_paths)
        resolved = measurability.check_base_is_head(repo, base)
        if on_base_resolved is not None:
            on_base_resolved(resolved.base_rev)
        artifact_path = _resolve_artifact_path(judge.coverage.artifact, project_root)
        profile = coverage.read_coverage_artifact(
            artifact_path, declared_format=judge.coverage.format
        )
        coverage.check_empty_coverage(profile)
        repo_top = git.repo_top(repo)
        diff_text = git.run(
            repo, "diff", "--unified=0", resolved.base_rev, resolved.head_rev
        )
    except AssayError as exc:
        return Claim(
            rigor="R1",
            source="computed",
            status=exc.outcome,
            verified_by_assay=True,
            reason_code=exc.reason_code,
        )

    added = diff.parse_added_lines(diff_text)
    if on_added_resolved is not None:
        on_added_resolved(added)

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
            excluded_lines=result.excluded_lines,
            files_with_excluded_lines=result.files_with_excluded_lines,
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
    mutation_claim: Claim | None = None,
    judgment: Judgment | None = None,
    ended: str | None = None,
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

    *mutation_claim* (P12, A-119) is the R2 wiring, the same shape one more
    level over: an optional :class:`~assay.verdict.Claim` -- built by
    :func:`assay.mutation.build_mutation_claim`, this function never
    constructs one itself, matching how *evidence*/*declared_evidence* are
    threaded through without this function owning their internals -- that,
    when given, is appended to *claims* before every check below runs, so a
    caller building ``claims=(r0_claim, r1_claim)`` does not also have to
    remember to fold the R2 claim into that tuple by hand. Passing a
    *mutation_claim* whose own ``rigor`` already appears in *claims* is
    refused the same way a duplicate rigor level is refused anywhere else in
    this project (:class:`~assay.verdict.Verdict`'s own
    ``_check_claims_cover_declared_rigor``) -- constructing the combined
    tuple and letting that existing check catch it, rather than a bespoke
    second one here.

    *judgment* (P16) is the resolved judge policy behind whichever claims
    rendered a real computed judgment -- built entirely by the CALLER (this
    function does not resolve source roots, coverage format, or a
    comparison commit itself) and passed straight through. ``scope`` and
    ``enforcement`` need no such parameter: both are already static *lane*
    attributes, so this function derives them from *lane* on every call,
    the same way ``argv_declared``/``env_declared`` already come from
    *result.plan*. When *claims* (after folding in *mutation_claim*)
    contains an R1 claim carrying a ``coverage`` payload but *judgment*
    supplies no ``r1`` policy, this function refuses
    (``ERROR``/``BAD_LANE_CONFIG``) before construction -- the identical
    reasoning as the missing-claims/missing-evidence guards above: a bare
    ``ValueError`` from :class:`~assay.verdict.Verdict` itself is not an
    ``AssayError`` and no caller catches it.

    The verdict's own ``outcome`` and ``reason_code`` are otherwise DERIVED
    from ``claims`` AND ``evidence`` together via :func:`~assay.verdict.rollup`
    (A-023), never chosen independently -- :class:`~assay.verdict.Verdict`
    would refuse a mismatch anyway, but deriving it here means this function
    cannot construct one. An ``ERROR``/``UNREADABLE_ARTIFACT`` evidence entry
    (a broken attestation, A-110) outranks every claim's own status the same
    way it would if it were a claim -- ``rollup`` does not distinguish which
    array a status came from, matching :class:`~assay.verdict.Verdict`'s own
    ``_check_outcome_agrees_with_rollup``.

    *ended* (P17, O2) overrides ``result.ended`` for the verdict's own
    ``ended`` timestamp -- ``started`` always stays ``result.started``.
    Every caller through P16 leaves it ``None`` (``result.ended`` is R0's
    own, and R0 was the only work done), so behaviour is unchanged for all
    of them. :func:`run_lane` passes the real timestamp taken AFTER R1
    evaluation completes: "verdict timing encloses command plus R1
    judgment" (O2) means the window a consumer sees must cover BOTH steps,
    not just R0's, and R1 has no started/ended of its own in schema v3 to
    carry that separately (there is exactly one ``started``/``ended`` pair
    per verdict) -- widening the ONE pair this function already emits is
    the only way to make that honest without a schema change P16 already
    closed the book on.
    """
    claims = claims if mutation_claim is None else (*claims, mutation_claim)
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
    r1_claim = next((claim for claim in claims if claim.rigor == "R1"), None)
    r1_judged = r1_claim is not None and r1_claim.coverage is not None
    judgment_r1 = None if judgment is None else judgment.r1
    if r1_judged and judgment_r1 is None:
        raise AssayError(
            f"lane {lane.name!r} rendered an R1 claim carrying a coverage "
            f"payload, but no judgment.r1 policy was supplied -- an "
            f"independent consumer cannot re-derive R1's status from "
            f"coverage alone. Refusing before constructing an incomplete "
            f"verdict.",
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
        ended=result.ended if ended is None else ended,
        assay_version=assay_version,
        declared_rigor=lane.rigor,
        declared_evidence=declared_evidence,
        argv_declared=plan.argv_declared,
        argv_appended=plan.argv_appended,
        argv_effective=plan.argv_effective,
        env_declared=plan.env_declared,
        env_effective=plan.env_effective,
        scope=lane.scope,
        enforcement=lane.enforcement,
        judgment=judgment,
        claims=claims,
        evidence=evidence,
    )


def _is_unsafe_coverage_artifact(repo: Path, artifact_path: Path) -> bool:
    """True when *artifact_path* -- the resolved ``judge.coverage.artifact``
    a lane is about to write to and read back -- must be refused outright
    (P17 work item 3, closing sol finding 6's coverage-artifact half): a
    symlink (could point anywhere, read silently by
    :func:`assay.coverage.read_coverage_artifact`'s own ``read_text``), an
    existing path that is not a regular file (a directory left where a
    file belongs), or a path already tracked by git (measurement output
    has no business being committed, and this run is about to overwrite
    it). Checked BEFORE the command runs, so a bad declaration is refused
    before anything executes rather than discovered after -- ``is_symlink``
    is checked first because it never raises for a non-existent path,
    unlike a naive existence check that follows the link first.
    """
    if artifact_path.is_symlink():
        return True
    if artifact_path.exists() and not artifact_path.is_file():
        return True
    tracked = git.run(repo, "ls-files", "--", str(artifact_path))
    return bool(tracked.strip())


def _remove_stale_coverage_artifact(artifact_path: Path) -> None:
    """Remove an existing coverage artifact before the lane's command runs
    (work item 4): a command that exits 0 without rewriting it must not let
    a PRIOR run's output stand in for this one's measurement. Only reached
    once :func:`_is_unsafe_coverage_artifact` has already cleared the path
    AND the whole worktree has been found clean (A-140), so this is always
    a plain, untracked, git-IGNORED, non-symlinked regular file -- or
    nothing at all, which is a no-op. Both of those facts are what make
    this the only ``unlink`` in the module that cannot destroy something a
    consumer still wanted: anything else at that path would have refused
    the run one step earlier, with the file still there.
    """
    if artifact_path.exists():
        artifact_path.unlink()


def refuse_lane(
    lane: Lane,
    *,
    commit: str,
    status: Outcome,
    reason_code: ReasonCode,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
    assay_version: str,
    clock: Clock = _utc_now,
) -> Verdict:
    """Refuse the WHOLE invocation before the lane's own command ever
    starts (work items 3/5): the command's own :class:`CommandPlan` is
    still resolved and recorded (A-036 -- a refused run is not an
    unrecorded one), but *process_runner* is never called. EVERY declared
    rigor level renders the exact SAME ``(status, reason_code)``: one root
    cause stopped the whole run, never several independently-derived
    stories about the same fact -- and none carries a payload, which a
    ``NO_MEASUREMENT`` claim (A-025) and an ``ERROR`` claim (A-136) both
    already require unconditionally.

    The claim set is built from ``lane.rigor`` itself rather than from a
    "which levels did this build wire up" flag (A-139): the ONE thing
    :func:`assemble_verdict` demands is a claim per DECLARED level, so
    deriving the tuple from the declaration is what makes this function
    total -- it can honestly refuse an ``R2``/``R3`` lane this build
    cannot evaluate, which is exactly the terminal path :mod:`assay.cli`
    used to let escape as a bare :class:`~assay.errors.AssayError` with no
    artifact at all even though ``HEAD`` was already known (work item 3's
    "every later terminal path must emit a complete artifact").

    Public, unlike the rest of this module's helpers, because
    :mod:`assay.cli` is the caller that owns the registry-capability
    refusals (work item 2) and must render them as artifacts here rather
    than re-deriving the shape itself.
    """
    plan = resolve_command_plan(
        lane, argv_append=argv_append, passthrough_source=passthrough_source
    )
    started = clock()
    ended = clock()
    rigor_levels = tuple(lane.rigor)
    claims = tuple(
        Claim(
            rigor=level,
            source="computed",
            status=status,
            verified_by_assay=True,
            reason_code=reason_code,
        )
        for level in rigor_levels
    )
    result = CommandResult(
        plan=plan,
        outcome=status,
        reason_code=reason_code,
        returncode=None,
        started=iso_utc(started),
        ended=iso_utc(ended),
    )
    return assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=claims,
        assay_version=assay_version,
    )


def run_lane(
    lane: Lane,
    *,
    commit: str,
    repo: Path,
    project_root: Path,
    adapter: LanguageAdapter | None,
    assay_version: str,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> Verdict:
    """``assay run``'s real pipeline (P17, R2 wiring P18): resolve
    prerequisites, run the lane's command AT MOST once, judge R1/R2 when
    declared, and assemble ONE verdict that encloses all of it.

    *adapter* is the ALREADY-RESOLVED :class:`~assay.adapters.base.
    LanguageAdapter` for ``lane.judge.language`` at whichever of R1/R2 is
    declared -- this function knows nothing about a
    :class:`~assay.registry.Registry`; resolving which adapter (or
    refusing an unsupported language/rigor pairing outright, before
    anything here runs at all) is :mod:`assay.cli`'s own job (work item
    2). The SAME adapter object serves both R1's coverage evaluation and
    R2's mutation generation (one adapter per language, not per rigor
    level -- :class:`~assay.registry.RegistryEntry` already pairs them
    this way). *adapter* is ``None`` exactly when NEITHER ``"R1"`` nor
    ``"R2"`` is in ``lane.rigor`` -- this function never dereferences it
    otherwise.

    Ordering (work items 2-7), all before the command runs, and in exactly
    the handoff's own order -- 3 (refuse) strictly before 4 (mutate):

    1. If R1 is declared, the coverage artifact path is VALIDATED
       (:func:`_is_unsafe_coverage_artifact`) -- a pure check that reads
       the filesystem and git's index and writes nothing.
    2. The WHOLE git worktree/index -- not merely the declared source
       roots -- must be clean (sol finding 6, live in the R0 path before
       this package: "every assay run invocation records HEAD and runs the
       live tree regardless of rigor level"). Either failure refuses the
       ENTIRE invocation via :func:`refuse_lane` before
       :func:`execute_command` is ever called.
    3. ONLY THEN is an existing coverage artifact removed
       (:func:`_remove_stale_coverage_artifact`) -- work item 4's "cannot
       consume prior output" made true by construction: whatever exists at
       that path once the command finishes was newly written BY this run,
       because nothing else could still be there.

    **Step 3 comes last on purpose (A-140).** Removing first made the
    cleanliness guard at step 2 judge a tree this function had itself
    just modified, and made a run that refuses to do anything nonetheless
    DELETE a file -- reproduced against the installed console script: a
    lane declaring any untracked regular file as its ``artifact`` had that
    file destroyed before ``DIRTY_TREE`` was ever reported. The cost of
    the correct order is a real requirement on the consumer, stated here
    because nothing else states it: **the declared coverage artifact must
    be git-ignored (or absent)**, otherwise it is itself untracked
    worktree state and step 2 refuses -- loudly, and with the artifact
    intact, which is the trade this project's own defaults policy
    (AGENTS.md 4.2a) asks for over silently deleting to make the tree
    look clean.

    Then the command runs exactly once, R0's claim is built, and -- if R1
    is declared -- :func:`evaluate_r1` runs UNCONDITIONALLY afterward,
    regardless of R0's own outcome: a coverage artifact a real test command
    wrote is a fact about what executed, not about whether its own
    assertions passed, so an R0 FAIL does not itself make R1 vacuous (and
    an R0 EXEC_FAILED/BUDGET_EXCEEDED still lets R1 render honestly too --
    work item 4's own pre-run removal means an artifact that was never
    rewritten is simply absent, and :func:`assay.coverage.
    read_coverage_artifact` renders that ``ERROR``/``UNREADABLE_ARTIFACT``
    on its own; no special-casing "did R0 pass" is needed here at all).
    ``judgment.r1`` is built if and only if the rendered R1 claim carries a
    ``coverage`` payload (P16's own invariant, :meth:`~assay.verdict.
    Verdict._check_judgment_matches_claims`) -- using the base
    :func:`evaluate_r1` itself already resolved, surfaced through
    *on_base_resolved* rather than re-resolved a second time (O2: "the
    comparison base resolves once"). Final ``ended`` covers R1's own
    completion, not merely R0's own (O2: "verdict timing encloses command
    plus R1 judgment").

    **P18: if R2 is declared, mutation testing is attempted after R1** (and
    regardless of whether R1 was even declared), using R0's OWN
    :class:`CommandResult` (*result*) as :func:`assay.mutation.
    run_mutation`'s mandatory *baseline* -- the lane's command still runs
    only ONCE per invocation (sol finding 11). When *result* did not
    ``PASS``, R2's own prerequisite chain is never even consulted: R2's
    baseline gate is strictly stricter than R1's (a failed baseline makes
    everything downstream of it moot), so the claim is built directly via
    :func:`assay.mutation.build_mutation_claim(result, None)
    <assay.mutation.build_mutation_claim>`, which propagates *result*'s own
    ``(outcome, reason_code)`` verbatim -- the identical answer resolving
    targets and calling :func:`~assay.mutation.run_mutation` anyway would
    have produced, without the wasted diff/target work. When *result* DID
    ``PASS``, R2 needs the same resolved diff R1 uses: if R1 was declared
    and its own :func:`evaluate_r1` call reached the diff (surfaced via
    ``on_added_resolved``, mirroring *on_base_resolved*), R2 reuses that
    :class:`~assay.diff.AddedLines` rather than diffing ``base``..``HEAD``
    a second time; otherwise (R1 not declared, or R1's own coverage-
    specific guards tripped before reaching the diff) R2 resolves its own
    prerequisite chain -- the identical ``check_dirty_tree``/
    ``check_base_is_head`` guards R1 uses, since R1 and R2 read the exact
    same ``judge.source_root_paths``/``judge.base`` -- and refuses
    (``NO_MEASUREMENT``) on the SAME two causes R1 would if it hit them.
    :func:`~assay.mutation.resolve_mutation_targets` then builds the
    per-file candidate list, and mutation runs inside a fresh, self-
    cleaning scratch directory (:func:`tempfile.TemporaryDirectory`) this
    function owns end to end. ``repo_top`` is handed to
    :func:`~assay.mutation.run_mutation` alongside *project_root* because
    those two are NOT the same directory for a project living in a
    subdirectory of its repository (A-145) — every target path is relative
    to the former, while each mutant's scratch copy is a copy of the
    latter. Reaching :func:`~assay.mutation.run_mutation`
    at all in this branch already proves *result* PASSED, which is
    `run_mutation`'s own only reason to return ``None`` for a caller-
    supplied baseline (:func:`~assay.mutation.run_mutation`'s own
    docstring) -- so the R2 claim built here always carries a ``mutation``
    payload, and ``judgment.r2`` is populated unconditionally alongside it.
    Final ``ended`` is extended again to cover R2's own completion too.

    **P19: if R3 is declared, an isolated canary run is attempted AFTER R1
    and R2, unconditionally** -- never gated on *result*'s own outcome the
    way R2 is (R2 reuses *result* AS its baseline; R3 never reuses it at
    all, so there is nothing of *result*'s to gate on). Delegated whole to
    :func:`assay.canary.run_isolated_canary` (a deferred import --
    see the call site's own comment for why), which owns its OWN
    prerequisite refusals (a test-path target, a dirty *repo*) and its own
    copy-and-run pipeline against *repo*/*project_root* -- never against a
    tree this function has itself already validated clean for R1/R2's sake
    only, since an isolated canary's cleanliness requirement (the copy IS
    the control) is a genuinely different fact from R1/R2's "the diff being
    measured is committed" one, even though both currently happen to
    require the same clean-tree precondition. An :class:`~assay.errors.
    AssayError` it raises (a config or prerequisite refusal) becomes a
    payload-free R3 :class:`Claim` here, the identical shape R1/R2's own
    guard sequences already use; otherwise :func:`assay.canary.
    build_canary_claim` builds the real one and ``judgment.r3`` is
    populated alongside it, unconditionally, the same "this function's own
    discipline keeps the two in step" reasoning R2's ``judgment.r2``
    already relies on (:class:`~assay.verdict.JudgmentR2`/:class:`~assay.
    verdict.JudgmentR3` now ALSO carry their own construction-time
    correspondence check against :class:`Claim` -- P19 work item 9/A-148 --
    so this function's discipline and that check now agree rather than one
    being the only witness). Final ``ended`` is extended a third time to
    cover R3's own completion.
    """
    r1_declared = "R1" in lane.rigor
    artifact_path: Path | None = None
    if r1_declared:
        artifact_path = _resolve_artifact_path(
            lane.judge.coverage.artifact, project_root
        )
        if _is_unsafe_coverage_artifact(repo, artifact_path):
            return refuse_lane(
                lane,
                commit=commit,
                status=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
                argv_append=argv_append,
                passthrough_source=passthrough_source,
                assay_version=assay_version,
                clock=clock,
            )

    if git.dirty_paths(repo):
        return refuse_lane(
            lane,
            commit=commit,
            status=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.DIRTY_TREE,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            assay_version=assay_version,
            clock=clock,
        )

    if artifact_path is not None:
        _remove_stale_coverage_artifact(artifact_path)

    r2_declared = "R2" in lane.rigor
    r3_declared = "R3" in lane.rigor

    result = execute_command(
        lane,
        argv_append=argv_append,
        cwd=project_root,
        passthrough_source=passthrough_source,
        process_runner=process_runner,
        clock=clock,
    )
    r0_claim = build_r0_claim(result)
    claims: tuple[Claim, ...] = (r0_claim,)
    judgment_r1: JudgmentR1 | None = None
    judgment_r2: JudgmentR2 | None = None
    judgment_r3: JudgmentR3 | None = None
    ended: str | None = None
    added_holder: list[diff.AddedLines] = []

    if r1_declared:
        judge = lane.judge
        resolved_base: list[str] = []
        r1_claim = evaluate_r1(
            lane,
            repo=repo,
            project_root=project_root,
            base=judge.base,
            adapter=adapter,
            on_base_resolved=resolved_base.append,
            on_added_resolved=added_holder.append,
        )
        claims += (r1_claim,)
        ended = iso_utc(clock())
        if r1_claim.coverage is not None:
            judgment_r1 = JudgmentR1(
                language=judge.language,
                source_roots=judge.source_roots,
                coverage_format=judge.coverage.format,
                coverage_artifact=judge.coverage.artifact,
                fail_under=judge.fail_under,
                allow_excluded=judge.allow_excluded,
                base=resolved_base[0],
            )

    if r2_declared:
        judge = lane.judge
        if result.outcome is not Outcome.PASS:
            # R2's baseline gate is strictly stricter than R1's -- a
            # non-PASS R0 makes mutation testing moot regardless of what
            # R2's own prerequisite chain would say, so that chain is
            # never even consulted (module docstring).
            r2_claim = mutation.build_mutation_claim(result, None)
            claims += (r2_claim,)
            ended = iso_utc(clock())
        else:
            if added_holder:
                added = added_holder[0]
            else:
                try:
                    measurability.check_dirty_tree(repo, judge.source_root_paths)
                    resolved = measurability.check_base_is_head(repo, judge.base)
                    diff_text = git.run(
                        repo,
                        "diff",
                        "--unified=0",
                        resolved.base_rev,
                        resolved.head_rev,
                    )
                    added = diff.parse_added_lines(diff_text)
                except AssayError as exc:
                    claims += (
                        Claim(
                            rigor="R2",
                            source="computed",
                            status=exc.outcome,
                            verified_by_assay=True,
                            reason_code=exc.reason_code,
                        ),
                    )
                    ended = iso_utc(clock())
                    added = None

            if added is not None:
                repo_top = git.repo_top(repo)

                def _read_source_text(path: str) -> str:
                    return (repo_top / path).read_text(encoding="utf-8")

                targets = mutation.resolve_mutation_targets(
                    added,
                    repo_top=repo_top,
                    source_root_paths=judge.source_root_paths,
                    adapter=adapter,
                    read_source_text=_read_source_text,
                )
                with tempfile.TemporaryDirectory(prefix="assay-r2-") as scratch:
                    mutation_result = mutation.run_mutation(
                        lane,
                        baseline=result,
                        project_root=project_root,
                        repo_top=repo_top,
                        scratch_root=Path(scratch),
                        targets=targets,
                        adapter=adapter,
                        jobs=judge.mutation.jobs,
                        operators=judge.mutation.operators,
                        process_runner=process_runner,
                        clock=clock,
                    )
                # `mutation_result` is NEVER `None` here: this branch is
                # only reached when `result.outcome is Outcome.PASS`, and
                # `run_mutation` returns `None` for exactly ONE reason --
                # `baseline.outcome is not Outcome.PASS` -- which cannot be
                # true for `baseline=result` in this branch. So
                # `r2_claim.mutation` (literally `mutation_result`,
                # unconditionally) is unconditionally present too, and
                # `judgment_r2` is built the same "iff a payload rendered"
                # way `judgment_r1` is, without a branch that could never
                # take the other arm (AUTHORING.md §3b.D).
                r2_claim = mutation.build_mutation_claim(result, mutation_result)
                claims += (r2_claim,)
                ended = iso_utc(clock())
                judgment_r2 = JudgmentR2(
                    jobs=judge.mutation.jobs,
                    operators=judge.mutation.operators,
                )

    if r3_declared:
        judge = lane.judge
        canary_cfg = judge.canary
        # Deferred, not module-level: `assay.canary` already imports
        # `evaluate_r1`/`execute_command`/... from THIS module at its own
        # module level, so a module-level import here would close a
        # genuine cycle (runner -> canary -> runner) -- the identical
        # reasoning `assay.mutation`'s own module docstring gives for
        # resolving `execute_command` from a function body one claim tier
        # over.
        from .canary import build_canary_claim, run_isolated_canary

        try:
            canary_result = run_isolated_canary(
                lane,
                repo=repo,
                project_root=project_root,
                mechanism=canary_cfg.mechanism,
                target=canary_cfg.target,
                adapter=adapter,
                process_runner=process_runner,
                clock=clock,
            )
        except AssayError as exc:
            claims += (
                Claim(
                    rigor="R3",
                    source="computed",
                    status=exc.outcome,
                    verified_by_assay=True,
                    reason_code=exc.reason_code,
                ),
            )
        else:
            claims += (build_canary_claim(canary_result),)
            judgment_r3 = JudgmentR3(
                mechanism=canary_cfg.mechanism, target=canary_cfg.target
            )
        ended = iso_utc(clock())

    judgment: Judgment | None = None
    if judgment_r1 is not None or judgment_r2 is not None or judgment_r3 is not None:
        judgment = Judgment(r1=judgment_r1, r2=judgment_r2, r3=judgment_r3)

    return assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=claims,
        assay_version=assay_version,
        judgment=judgment,
        ended=ended,
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
