"""The cause-sensitive canary (P09, DESIGN-GUIDE §12's R3): proves the
COMPLETE gate demonstrably rejects valid known-bad input for the INTENDED
reason, not merely that it rejects something.

The claim this module exists to defend, verbatim from the handoff: **a
known-good control must PASS through the real pipeline, and a deliberately
minimal, known-bad transform of the SAME input must FAIL for the SPECIFIC
expected reason** — not just any failure (O1's own negative: *"accepting
any non-zero (or any reason_code) as success lets an unrelated syntax/config
error, or a failure for the wrong cause, satisfy the oracle."*).

**The two halves are structurally different, and both are real (A-107).**

* :func:`run_python_canary` proves the FULL real pipeline: a genuine
  :func:`~assay.runner.execute_command` ``pytest`` subprocess run (R0) plus,
  when it passes, :func:`~assay.runner.evaluate_r1`. Both the control and
  the transformed half are built as real, disposable git commits (mirroring
  the established "git-fixture materialised under ``tmp_path``" pattern from
  P02 onward) and run through the identical code path — :func:`_run_pipeline`
  is the ONE engine both halves share, so neither can be judged by different
  rules.
* :func:`run_go_canary` proves R1 (coverage-evaluation) sensitivity ONLY:
  two committed, pre-generated coverprofiles (a clean control, and one with
  the transform's specific line(s) newly marked missing) fed through the
  real :func:`~assay.evaluate.evaluate_coverage` with the real
  :class:`~assay.adapters.go.GoAdapter` — no Go toolchain exists anywhere in
  this devcontainer (A-042/A-087), and scripting a fake R0 result would
  substitute a hand-picked value for a genuine measurement (A-107's own
  reasoning). Only :data:`MECHANISM_UNCOVERED_LINE` is provable this way —
  see :data:`_GO_R1_MECHANISMS`'s own docstring for why import-break is not.

**The judgement, A-109.** :func:`judge_canary` maps onto the two existing,
previously-unused reason codes: a malformed (unrecognised) mechanism or a
transform that changed nothing to judge both render
``INCONCLUSIVE``/``CANARY_INCONCLUSIVE`` (nothing was proven either way); a
bad case that unexpectedly PASSED, or one that failed for a reason OTHER
than the configured expected one, both render ``FAIL``/``CANARY_SURVIVED``
— "survived" means the specific intended defect was never proven caught,
whichever of the two ways that happened. A broken CONTROL (the known-good
half itself did not pass) also renders ``INCONCLUSIVE``: nothing about the
transform's own cause can be concluded when the baseline it is compared
against is not itself known-good.

**P19 closes the gap A-106 deliberately left open.** ``config.py``'s loader
now parses ``judge.canary`` as a CLOSED table (:class:`~assay.config.
CanaryConfig`: exactly ``mechanism`` and ``target``) instead of an opaque
mapping — :func:`run_isolated_canary`, below, is the first real reader
of it. The two module-level functions above this point
(:func:`run_python_canary`/:func:`run_go_canary`) are UNCHANGED: they still
take *mechanism*/*target_path* as plain arguments and know nothing about
``assay.toml`` at all, exactly as P09 built them — this package's own
orchestration is a NEW caller layered on top, not a rewrite of the
mechanism underneath it.

**P23's own addition: the installed CLI proves one declared canary from two
independent P22 committed snapshots, never a live-tree copy.**
:func:`run_isolated_canary` is the CLI-facing entry point
:mod:`assay.runner`'s ``run_lane`` calls for a higher-rigor lane (a deferred
import there, for the identical circular-import reason :mod:`assay.mutation`'s
own module docstring already gives for resolving ``execute_plan`` from a
function body: this module already imports ``Lane`` from ``config.py`` and
several names from ``runner.py`` at module level, so either of those
importing THIS module back at their own module level would close a genuine
cycle). It materialises the CONTROL half as *prepared*'s own base snapshot
(:meth:`~assay.isolation.SnapshotRepository.materialize`) — so the control
commit already IS the exact commit the lane's baseline itself ran, no extra
"build the control commit" step needed — and, only for a valid non-no-op
transform, the TRANSFORMED half as an independent
:meth:`~assay.isolation.SnapshotRepository.materialize_replacement` of the
SAME prepared seed. Neither half shares an inode, a coverage reservation, or
a Git object store with the other or with the consumer's real repository;
:func:`~assay.runner._execute_snapshot_unit` is the ONE shared engine both
halves and :mod:`assay.runner`'s own baseline run through (never a second,
independently-written copy of the reserve/arm/execute/dirt-check/consume
sequence). The consumer's real worktree is never staged, committed, read
from, or written to by this module at all — every byte either half reads or
runs against comes from *prepared*.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping

from . import git
from .adapters.base import LanguageAdapter
from .config import Lane
from .coverage_parsers.model import CoverageProfile
from .diff import AddedLines
from .errors import AssayError, Outcome, ReasonCode
from .evaluate import evaluate_coverage
from .runner import (
    Clock,
    ProcessRunner,
    build_r0_claim,
    default_process_runner,
    evaluate_r1,
    execute_command,
    _execute_snapshot_unit,
    _relocate_source_roots,
)
from .verdict import CanaryResult, Claim

__all__ = [
    "CANARY_MECHANISMS",
    "MECHANISM_IMPORT_BREAK",
    "MECHANISM_UNCOVERED_LINE",
    "build_canary_claim",
    "judge_canary",
    "run_go_canary",
    "run_isolated_canary",
    "run_python_canary",
]

#: The two canary mechanisms P09 ships (A-105/A-010), named identically to
#: nyxloom's own ``gate_canary.MECHANISM_IMPORT_BREAK``/
#: ``MECHANISM_UNCOVERED_LINE`` — import-break proves a gate rejects broken
#: code AT ALL (R0); uncovered-line proves a gate specifically enforces a
#: changed-line-coverage floor (R1).
MECHANISM_IMPORT_BREAK = "import-break"
MECHANISM_UNCOVERED_LINE = "uncovered-line"

#: The SPECIFIC reason each mechanism is expected to produce when a gate
#: correctly enforces the axis it probes — never derived from what a run
#: happened to observe; this is the "expected", declared half of the
#: comparison :func:`judge_canary` makes against the "observed" half.
_EXPECTED_REASON_BY_MECHANISM: Mapping[str, ReasonCode] = MappingProxyType(
    {
        MECHANISM_IMPORT_BREAK: ReasonCode.COMMAND_FAILED,
        MECHANISM_UNCOVERED_LINE: ReasonCode.UNCOVERED_LINES,
    }
)

#: (P19) the closed vocabulary :mod:`assay.config`'s loader cross-checks
#: ``judge.canary.mechanism`` against, imported from there via a DEFERRED
#: import (this module's own docstring explains why: importing ``Lane``
#: from ``config.py`` at module level here already means the reverse
#: import must not also be module-level) -- the same "vocabulary imported
#: from its own owner, never duplicated" discipline
#: :data:`assay.mutation.MUTATION_OPERATORS` already established for
#: ``judge.mutation.operators`` one field over. Derived from this
#: dictionary's own keys rather than restated, so the two can never drift.
CANARY_MECHANISMS: frozenset[str] = frozenset(_EXPECTED_REASON_BY_MECHANISM)

#: Go's R1-only canary (A-107) can prove ONLY uncovered-line. An inserted
#: ``init()`` panic (:meth:`~assay.adapters.go.GoAdapter.inject_import_break`)
#: prevents ``go test -coverprofile`` from ever writing a coverprofile at all
#: — verified empirically against the equivalent Python behaviour while
#: authoring this package (a real ``pytest --cov`` run against an
#: import-broken package produces no ``cov.json`` either) — so there is no
#: committed-coverprofile shape that could represent import-break's own
#: R0-level rejection. Requesting it here is therefore treated exactly like
#: an unrecognised mechanism: INCONCLUSIVE, nothing to judge.
_GO_R1_MECHANISMS: frozenset[str] = frozenset({MECHANISM_UNCOVERED_LINE})

#: An arbitrary, never-resolved absolute path standing in for "the repo top"
#: in :func:`run_go_canary`'s calls to :func:`~assay.evaluate.evaluate_coverage`
#: — Go's canary never touches a real filesystem (A-107: no toolchain, no
#: git), so no real repository root exists to name. Matches the same
#: convention ``tests/test_adapters_go_registration.py`` already established
#: for a filesystem-free ``evaluate_coverage`` call.
_GO_REPO_TOP = Path("/repo")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_mechanism(
    adapter: LanguageAdapter, mechanism: str, text: str
) -> tuple[str, str] | None:
    """Apply *mechanism* to *text* via *adapter*'s own injector method, or
    ``None`` for an unrecognised mechanism name — O3's "malformed transform"
    case, caught here BEFORE anything is run."""
    if mechanism == MECHANISM_IMPORT_BREAK:
        return adapter.inject_import_break(text)
    if mechanism == MECHANISM_UNCOVERED_LINE:
        return adapter.inject_uncovered_line(text)
    return None


def _run_pipeline(
    lane: Lane,
    *,
    repo: Path,
    project_root: Path,
    base_commit: str,
    adapter: LanguageAdapter,
    process_runner: ProcessRunner,
    clock: Clock,
) -> tuple[Outcome, ReasonCode | None]:
    """The shared engine BOTH the control and the transformed half of
    :func:`run_python_canary` run through — the real R0 step
    (:func:`~assay.runner.execute_command`), then (only when R0 passed, and
    only when *lane* declares R1) the real R1 step
    (:func:`~assay.runner.evaluate_r1`) against *repo*'s CURRENT ``HEAD``.

    R1 is skipped whenever R0 did not pass: a genuinely broken R0 run (the
    import-break mechanism's own target) commonly never produces a coverage
    artifact at all (verified empirically while authoring this package —
    ``pytest --cov`` writes no ``cov.json`` when collection itself errors),
    so attempting R1 there would not observe a real measurement, it would
    observe :func:`~assay.coverage.read_coverage_artifact` failing to find a
    file — a DIFFERENT, unrelated cause from either mechanism's own
    intended one. The R0 outcome is reported instead, honestly.
    """
    result = execute_command(
        lane, cwd=project_root, process_runner=process_runner, clock=clock
    )
    r0_claim = build_r0_claim(result)
    if r0_claim.status is not Outcome.PASS or "R1" not in lane.rigor:
        return r0_claim.status, r0_claim.reason_code
    r1_claim = evaluate_r1(
        lane, repo=repo, project_root=project_root, base=base_commit, adapter=adapter
    )
    return r1_claim.status, r1_claim.reason_code


def run_python_canary(
    lane: Lane,
    *,
    repo: Path,
    project_root: Path,
    base_commit: str,
    target_path: str,
    adapter: LanguageAdapter,
    mechanism: str,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CanaryResult:
    """Python's full-pipeline canary (A-107): the caller has ALREADY
    committed the known-good control fixture as *repo*'s current ``HEAD``
    (mirroring the established git-fixture-under-``tmp_path`` pattern) —
    *base_commit* is the ancestor commit the control's own R1 diff is
    measured against. This function:

    1. Runs the control through :func:`_run_pipeline` (a mandatory check —
       O1's own "a known-good control must PASS through the real pipeline").
    2. Reads *target_path*'s CURRENT text and applies *mechanism* via
       *adapter*'s own ``inject_*`` method (pure — A-010). An unrecognised
       *mechanism*, or one that produces no change to the text, is reported
       WITHOUT running anything further (O3: nothing to judge).
    3. Otherwise WRITES the transformed text to *target_path* (the one
       place this orchestration touches a real, disposable filesystem
       location, matching the Process section's own instruction) and
       commits it as a new, real git commit on top of the control — so the
       transformed half's own R1 diff (when reached) isolates EXACTLY the
       injected change, nothing else.
    4. Runs the transformed state through the SAME :func:`_run_pipeline`.

    Returns the raw evidence as a :class:`~assay.verdict.CanaryResult`;
    :func:`judge_canary`/:func:`build_canary_claim` derive the actual
    verdict from it.
    """
    control_outcome, _ = _run_pipeline(
        lane,
        repo=repo,
        project_root=project_root,
        base_commit=base_commit,
        adapter=adapter,
        process_runner=process_runner,
        clock=clock,
    )
    control_commit = git.head_rev(repo)

    expected_reason = _EXPECTED_REASON_BY_MECHANISM.get(mechanism)
    target = project_root / target_path
    original_text = target.read_text(encoding="utf-8")
    applied = _apply_mechanism(adapter, mechanism, original_text)
    if applied is None:
        return CanaryResult(
            mechanism=mechanism,
            target=target_path,
            description=(
                f"{mechanism!r} is not a known canary mechanism -- expected "
                f"one of {sorted(_EXPECTED_REASON_BY_MECHANISM)}; nothing to "
                f"judge"
            ),
            control_outcome=control_outcome,
        )

    transformed_text, description = applied
    if transformed_text == original_text:
        return CanaryResult(
            mechanism=mechanism,
            target=target_path,
            description=(
                f"{mechanism} produced no change to {target_path} -- "
                f"nothing to judge"
            ),
            control_outcome=control_outcome,
            expected_reason_code=expected_reason,
        )

    target.write_text(transformed_text, encoding="utf-8")
    git.run(repo, "add", "-A")
    git.run(repo, "commit", "-q", "-m", f"assay canary: {description}")

    transformed_outcome, observed_reason_code = _run_pipeline(
        lane,
        repo=repo,
        project_root=project_root,
        base_commit=control_commit,
        adapter=adapter,
        process_runner=process_runner,
        clock=clock,
    )
    return CanaryResult(
        mechanism=mechanism,
        target=target_path,
        description=description,
        control_outcome=control_outcome,
        transformed_outcome=transformed_outcome,
        expected_reason_code=expected_reason,
        observed_reason_code=observed_reason_code,
    )


def _judge_unit(
    lane: Lane,
    unit: SnapshotUnitResult,
    *,
    repo: Path,
    project_root: Path,
    scratch_project_root: Path,
    base: str | None,
    adapter: LanguageAdapter,
) -> tuple[Outcome, ReasonCode | None]:
    """The R0-then-R1 judgement one already-EXECUTED snapshot unit renders --
    the identical two-step :func:`_run_pipeline` performs for
    :func:`run_python_canary`'s own halves, given *unit* (an
    :class:`~assay.runner.SnapshotUnitResult`) instead of re-executing the
    command a second time. *unit*'s own dirt/HEAD-drift ('post_reason') is
    the caller's problem, not this function's -- checked before this is ever
    called (O2's "fresh unit" attack: a unit that no longer names its own
    commit is never silently judged as if it did).
    """
    r0_claim = build_r0_claim(unit.result)
    if r0_claim.status is not Outcome.PASS or "R1" not in lane.rigor:
        return r0_claim.status, r0_claim.reason_code
    if unit.profile_error is not None:
        return unit.profile_error.outcome, unit.profile_error.reason_code
    relocated_lane = _relocate_source_roots(
        lane, project_root=project_root.resolve(), scratch_project_root=scratch_project_root
    )
    r1_claim = evaluate_r1(
        relocated_lane,
        repo=repo,
        project_root=scratch_project_root,
        base=base,
        adapter=adapter,
        profile=unit.profile,
    )
    return r1_claim.status, r1_claim.reason_code


def run_isolated_canary(
    lane: Lane,
    *,
    prepared: SnapshotRepository,
    plan: CommandPlan,
    deadline: LaneDeadline,
    project_root: Path,
    resolved_base: str | None,
    mechanism: str,
    target: str,
    adapter: LanguageAdapter,
    process_runner: ProcessRunner,
    clock: Clock,
) -> CanaryResult:
    """The installed CLI's own R3 entry point (P23 exact reexecution): proves
    *mechanism* against *target* from TWO INDEPENDENT P22 snapshots of
    *prepared*'s own prepared seed -- never a live-tree copy, and never a
    second config/lane read (A-O SB-P22-04: one normalized project-to-repo
    target conversion, done once below).

    One prerequisite refusal before anything is materialised: *target* must
    not be a test path -- *adapter*'s own
    :meth:`~assay.adapters.base.LanguageAdapter.is_test_path`, the one piece
    of this table :mod:`assay.config`'s adapter-agnostic loader cannot check
    itself. (The consumer-repository cleanliness/commit-identity prerequisite
    that the old copy-based orchestration owned here is now checked ONCE, by
    the caller, before any snapshot -- including this one -- is prepared.)

    The CONTROL half is *prepared*'s own base snapshot
    (:meth:`~assay.isolation.SnapshotRepository.materialize`) -- so the
    control commit already IS the exact commit the lane's baseline itself
    ran, byte-identical, never re-derived. *target*'s current bytes are read
    ONCE from the prepared seed (:meth:`~assay.isolation.SnapshotRepository.
    read_regular_file`), never from either materialized snapshot's own disk
    copy. Only a VALID, non-no-op transform proceeds to materialise a second,
    independent TRANSFORMED half
    (:meth:`~assay.isolation.SnapshotRepository.materialize_replacement`) of
    the identical seed -- a malformed mechanism or a no-op keeps the existing
    ``CANARY_INCONCLUSIVE`` shape without ever entering it (O3). Both halves
    run through the identical shared unit engine
    (:func:`~assay.runner._execute_snapshot_unit`) the lane's own baseline
    uses, each with its OWN fresh coverage reservation when ``"R1"`` is
    declared (O2: no cross-unit profile reuse) -- the control's R1 diff
    compares ``judge.base..seed commit``, while the transform's compares
    ``seed commit..its own deterministic child commit``, mirroring exactly
    what two real, sequential commits would mean in the old live-copy
    orchestration.

    Either half leaving Git-visible dirt or a moved ``HEAD`` behind is fatal
    to the WHOLE R3 claim (A-195): raised here as the unchanged
    ``NO_MEASUREMENT``/``DIRTY_TREE`` or ``HEAD_CHANGED`` pair, letting the
    caller (:func:`~assay.runner.run_lane`) render it as a payload-free R3
    claim the same way it already renders any other P22 structural failure.
    """
    if adapter.is_test_path(target):
        raise AssayError(
            f"judge.canary.target {target!r} is a test path (per the "
            f"{adapter.name!r} adapter's own convention) -- a canary "
            f"transforms real source, never a test file",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )

    prefix = prepared.spec.project_prefix
    canary_repo_path = (
        PurePosixPath(target) if str(prefix) == "." else prefix / PurePosixPath(target)
    )
    wants_coverage = "R1" in lane.rigor
    coverage_artifact = lane.judge.coverage.artifact if wants_coverage else None
    coverage_format = lane.judge.coverage.format if wants_coverage else None

    with prepared.materialize(timeout=deadline.remaining()) as control_snapshot:
        control_unit = _execute_snapshot_unit(
            plan=plan,
            snapshot=control_snapshot,
            deadline=deadline,
            wants_coverage=wants_coverage,
            coverage_artifact=coverage_artifact,
            coverage_format=coverage_format,
            process_runner=process_runner,
            clock=clock,
        )
        if control_unit.post_reason is not None:
            raise AssayError(
                "the canary control's own snapshot left Git-visible state "
                "behind after the command ran; the result no longer "
                "represents its own commit",
                outcome=Outcome.NO_MEASUREMENT,
                reason_code=control_unit.post_reason,
            )
        control_outcome, _ = _judge_unit(
            lane,
            control_unit,
            repo=control_snapshot.root,
            project_root=project_root,
            scratch_project_root=control_snapshot.project_root,
            base=resolved_base,
            adapter=adapter,
        )
        original_bytes = prepared.read_regular_file(
            canary_repo_path, timeout=deadline.remaining()
        )

    expected_reason = _EXPECTED_REASON_BY_MECHANISM.get(mechanism)
    try:
        original_text = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"canary target {target!r} is not valid UTF-8: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc

    applied = _apply_mechanism(adapter, mechanism, original_text)
    if applied is None:
        return CanaryResult(
            mechanism=mechanism,
            target=target,
            description=(
                f"{mechanism!r} is not a known canary mechanism -- expected "
                f"one of {sorted(_EXPECTED_REASON_BY_MECHANISM)}; nothing to "
                f"judge"
            ),
            control_outcome=control_outcome,
        )

    transformed_text, description = applied
    if transformed_text == original_text:
        return CanaryResult(
            mechanism=mechanism,
            target=target,
            description=(
                f"{mechanism} produced no change to {target} -- "
                f"nothing to judge"
            ),
            control_outcome=control_outcome,
            expected_reason_code=expected_reason,
        )

    replacement_bytes = transformed_text.encode("utf-8")
    with prepared.materialize_replacement(
        path=canary_repo_path,
        expected=original_bytes,
        replacement=replacement_bytes,
        timeout=deadline.remaining(),
    ) as transform_snapshot:
        transform_unit = _execute_snapshot_unit(
            plan=plan,
            snapshot=transform_snapshot,
            deadline=deadline,
            wants_coverage=wants_coverage,
            coverage_artifact=coverage_artifact,
            coverage_format=coverage_format,
            process_runner=process_runner,
            clock=clock,
        )
        if transform_unit.post_reason is not None:
            raise AssayError(
                "the canary transform's own snapshot left Git-visible state "
                "behind after the command ran; the result no longer "
                "represents its own commit",
                outcome=Outcome.NO_MEASUREMENT,
                reason_code=transform_unit.post_reason,
            )
        transformed_outcome, observed_reason_code = _judge_unit(
            lane,
            transform_unit,
            repo=transform_snapshot.root,
            project_root=project_root,
            scratch_project_root=transform_snapshot.project_root,
            base=prepared.spec.commit,
            adapter=adapter,
        )

    return CanaryResult(
        mechanism=mechanism,
        target=target,
        description=description,
        control_outcome=control_outcome,
        transformed_outcome=transformed_outcome,
        expected_reason_code=expected_reason,
        observed_reason_code=observed_reason_code,
    )


def _evaluate_go(
    *,
    adapter: LanguageAdapter,
    source_text: str,
    target_path: str,
    profile: CoverageProfile,
    added_lines: Iterable[int],
    fail_under: float,
    allow_excluded: bool,
) -> tuple[Outcome, ReasonCode | None]:
    """One R1-only :func:`~assay.evaluate.evaluate_coverage` call, shared by
    both the control and the transformed half of :func:`run_go_canary`."""
    added = AddedLines(
        by_file=MappingProxyType({target_path: frozenset(added_lines)})
    )
    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=_GO_REPO_TOP,
        source_root_paths=(_GO_REPO_TOP,),
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        read_source_text=lambda _: source_text,
    )
    return result.outcome, result.reason_code


def _appended_line_range(original: str, transformed: str) -> frozenset[int]:
    """The 1-based line range added when *transformed* is *original* plus a
    PURE TRAILING APPEND — every physical line index beyond *original*'s own
    line count. Valid only for a pure-append transform: both of
    :class:`~assay.adapters.go.GoAdapter`'s own canary mechanisms are pure
    appends (see that module's docstring for why -- Go has no executable
    top-level statement to insert an import-break into, unlike Python's).
    """
    original_count = len(original.splitlines())
    transformed_count = len(transformed.splitlines())
    return frozenset(range(original_count + 1, transformed_count + 1))


def run_go_canary(
    *,
    adapter: LanguageAdapter,
    mechanism: str,
    control_source: str,
    target_path: str,
    control_profile: CoverageProfile,
    transformed_profile: CoverageProfile,
    fail_under: float = 100.0,
    allow_excluded: bool = False,
) -> CanaryResult:
    """Go's R1-ONLY canary (A-107): *control_profile* and
    *transformed_profile* are two COMMITTED, pre-generated coverprofiles
    (``tests/fixtures/canary/go/**``) — a clean control, and one with the
    transform's specific line(s) newly marked missing. No process runs, no
    git repo is touched: this is a pure function over already-loaded
    :class:`~assay.coverage_parsers.model.CoverageProfile` values, run
    through the real :func:`~assay.evaluate.evaluate_coverage` with the real
    *adapter* (:class:`~assay.adapters.go.GoAdapter`).

    *control_source* is *target_path*'s own committed control text; the
    transformed text is derived from it via *adapter*'s real
    ``inject_uncovered_line`` (never a second committed ``.go`` file, so the
    fixture cannot drift from the adapter under test — the fixture's
    independence lives in *transformed_profile* instead, hand-authored
    against exactly where that real transform's output lands).

    A *mechanism* outside :data:`_GO_R1_MECHANISMS` (import-break included,
    see that constant's own docstring) is reported WITHOUT evaluating
    anything for the transformed half — the control still runs, matching
    :func:`run_python_canary`'s own "a mandatory known-good control" rule.
    """
    control_lines = range(1, len(control_source.splitlines()) + 1)
    control_outcome, _ = _evaluate_go(
        adapter=adapter,
        source_text=control_source,
        target_path=target_path,
        profile=control_profile,
        added_lines=control_lines,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
    )

    expected_reason = _EXPECTED_REASON_BY_MECHANISM.get(mechanism)
    if mechanism not in _GO_R1_MECHANISMS:
        return CanaryResult(
            mechanism=mechanism,
            target=target_path,
            description=(
                f"{mechanism!r} is not provable by an R1-only Go canary "
                f"(A-107) -- only {sorted(_GO_R1_MECHANISMS)} changes "
                f"changed-line coverage at all; nothing to judge"
            ),
            control_outcome=control_outcome,
        )

    transformed_source, description = adapter.inject_uncovered_line(control_source)
    if transformed_source == control_source:
        return CanaryResult(
            mechanism=mechanism,
            target=target_path,
            description=(
                f"{mechanism} produced no change to {target_path} -- "
                f"nothing to judge"
            ),
            control_outcome=control_outcome,
            expected_reason_code=expected_reason,
        )

    added_lines = _appended_line_range(control_source, transformed_source)
    transformed_outcome, observed_reason_code = _evaluate_go(
        adapter=adapter,
        source_text=transformed_source,
        target_path=target_path,
        profile=transformed_profile,
        added_lines=added_lines,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
    )
    return CanaryResult(
        mechanism=mechanism,
        target=target_path,
        description=description,
        control_outcome=control_outcome,
        transformed_outcome=transformed_outcome,
        expected_reason_code=expected_reason,
        observed_reason_code=observed_reason_code,
    )


def judge_canary(result: CanaryResult) -> tuple[Outcome, ReasonCode | None]:
    """The judgement A-109 maps onto the two existing reason codes:

    * a broken control, or a transform that never genuinely ran (a
      malformed mechanism or a no-op) — ``INCONCLUSIVE``/``CANARY_INCONCLUSIVE``
      (nothing proven either way);
    * the bad case unexpectedly PASSED, or it failed for a reason OTHER
      than the configured expected one — both ``FAIL``/``CANARY_SURVIVED``
      ("survived" means the specific intended defect was never proven
      caught, whichever way);
    * otherwise — a PASSING control and a transformed run that failed for
      EXACTLY the expected reason — ``PASS``.
    """
    if result.control_outcome is not Outcome.PASS:
        return Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE
    if result.transformed_outcome is None:
        return Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE
    if result.transformed_outcome is Outcome.PASS:
        return Outcome.FAIL, ReasonCode.CANARY_SURVIVED
    if result.observed_reason_code != result.expected_reason_code:
        return Outcome.FAIL, ReasonCode.CANARY_SURVIVED
    return Outcome.PASS, None


def build_canary_claim(result: CanaryResult) -> Claim:
    """The R3 :class:`~assay.verdict.Claim` from a
    :class:`~assay.verdict.CanaryResult` (A-108) — the exact mapping
    :func:`~assay.runner.build_r0_claim` performs for R0, one level up."""
    status, reason_code = judge_canary(result)
    return Claim(
        rigor="R3",
        source="computed",
        status=status,
        verified_by_assay=True,
        reason_code=reason_code,
        canary=result,
    )
