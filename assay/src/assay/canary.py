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

**Why ``config.py`` is not needed here (A-106).**
:attr:`~assay.config.JudgeConfig.canary` already exists as an opaque
``Mapping[str, Any] | None`` and the loader already fully round-trips it —
confirmed by the already-passing ``CANARY_TABLE`` fixture in
``tests/test_config_rigor.py``. Nothing in this module reads that mapping
directly (this package's own tests construct :class:`~assay.config.Lane`/
:class:`~assay.config.JudgeConfig` objects directly, the established
``make_lane``/``make_r1_judge`` house pattern, bypassing ``assay.toml``
parsing entirely) — a project's own ``[lanes.X.judge.canary]`` table is data
a FUTURE caller (wiring this module into the CLI) would interpret; this
package's own oracles do not require that wiring to exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from . import git
from .adapters.base import LanguageAdapter
from .config import Lane
from .coverage_parsers.model import CoverageProfile
from .diff import AddedLines
from .errors import Outcome, ReasonCode
from .evaluate import evaluate_coverage
from .runner import (
    Clock,
    ProcessRunner,
    build_r0_claim,
    default_process_runner,
    evaluate_r1,
    execute_command,
)
from .verdict import CanaryResult, Claim

__all__ = [
    "MECHANISM_IMPORT_BREAK",
    "MECHANISM_UNCOVERED_LINE",
    "build_canary_claim",
    "judge_canary",
    "run_go_canary",
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
