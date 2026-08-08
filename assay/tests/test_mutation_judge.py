"""O4 -- :func:`~assay.mutation.judge_mutation`'s pure outcome mapping
(A-117), tested directly at the function level for every terminal case,
including the precedence among SIMULTANEOUSLY non-empty buckets; then
:func:`~assay.mutation.run_mutation` end to end, reaching all FOUR
buckets in one real run through a fake ``process_runner``, proving
``total`` really is the sum of every attempted identity.

The negative this defends (O4, verbatim): *dropping unattempted
identities, treating crashes as killed, or universal PASS differs from
the complete expected artifact.* The precedence tests below are what
catch a "crashed mutants are silently ignored because survived is
checked first" class of bug -- ``crashed`` must win even when
``survived``/``budget_exceeded`` are ALSO non-empty in the same run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import make_lane

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationTarget, build_mutation_claim, judge_mutation, run_mutation
from assay.runner import CommandPlan, CommandResult, execute_command

_PLAN = CommandPlan(
    argv_declared=("pytest", "-q"),
    argv_appended=(),
    argv_effective=("pytest", "-q"),
    env_declared={},
    env_effective={},
)


def _baseline(outcome: Outcome, reason_code: ReasonCode | None) -> CommandResult:
    return CommandResult(
        plan=_PLAN,
        outcome=outcome,
        reason_code=reason_code,
        returncode=0 if outcome is Outcome.PASS else 1,
        started="2026-08-07T12:00:00+00:00",
        ended="2026-08-07T12:00:01+00:00",
    )


# --- judge_mutation: every A-117 terminal case, at the function level -------


def test_a_none_mutation_reuses_the_baselines_own_outcome_and_reason():
    baseline = _baseline(Outcome.FAIL, ReasonCode.COMMAND_FAILED)
    assert judge_mutation(baseline, None) == (Outcome.FAIL, ReasonCode.COMMAND_FAILED)


def test_zero_total_is_inconclusive_no_mutants():
    from assay.verdict import Mutation

    baseline = _baseline(Outcome.PASS, None)
    mutation = Mutation(total=0, killed=0)
    assert judge_mutation(baseline, mutation) == (Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS)


def test_crashed_outranks_survived_and_budget_exceeded_when_all_three_are_present():
    """The precedence claim, made concrete: a run with ALL THREE non-empty
    buckets must still render crashed's own outcome -- checking survived
    or budget_exceeded FIRST would silently launder a real crash."""
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, operator="compare-swap", description="Lt->LtE")
    crashed = MutantOutcome(path="pkg/mod.py", lineno=2, operator="compare-swap", description="Lt->LtE")
    stopped = MutantOutcome(path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE")
    mutation = Mutation(
        total=3, killed=0, survived=(survivor,), crashed=(crashed,), budget_exceeded=(stopped,)
    )
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.ERROR, ReasonCode.EXEC_FAILED)


def test_budget_exceeded_outranks_survived_when_both_are_present():
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, operator="compare-swap", description="Lt->LtE")
    stopped = MutantOutcome(path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE")
    mutation = Mutation(total=2, killed=0, survived=(survivor,), budget_exceeded=(stopped,))
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT)


def test_survived_alone_is_fail_mutants_survived():
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, operator="compare-swap", description="Lt->LtE")
    mutation = Mutation(total=1, killed=0, survived=(survivor,))
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED)


def test_every_bucket_empty_with_a_positive_total_is_pass():
    from assay.verdict import Mutation

    mutation = Mutation(total=3, killed=3)
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.PASS, None)


# --- build_mutation_claim: the R2 Claim wiring -------------------------------


def test_build_mutation_claim_wires_status_reason_and_the_mutation_payload():
    from assay.verdict import Mutation

    mutation = Mutation(total=2, killed=2)
    baseline = _baseline(Outcome.PASS, None)

    claim = build_mutation_claim(baseline, mutation)

    assert claim.rigor == "R2"
    assert claim.source == "computed"
    assert claim.verified_by_assay is True
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None
    assert claim.mutation is mutation


# --- run_mutation end to end: all four buckets in ONE real run -------------

_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    c = True\n"
    "    d = True\n"
    "    return a, b, c, d\n"
)
_TARGETS = (
    MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3, 4, 5})),
)


def _decide(project_root: Path):
    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == project_root:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")
        if "b = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        if "c = False" in text:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if "d = False" in text:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        raise AssertionError(f"unexpected content: {text!r}")

    return decide


def test_run_mutation_reaches_all_four_buckets_and_total_accounts_for_every_one(
    tmp_path: Path,
):
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    scratch_root.mkdir()
    decide = _decide(project_root)

    baseline = execute_command(lane, cwd=project_root, process_runner=decide)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("bool-const-flip",),
        process_runner=decide,
    )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert mutation.killed == 1
    assert len(mutation.survived) == 1
    assert len(mutation.crashed) == 1
    assert len(mutation.budget_exceeded) == 1
    assert mutation.total == (
        mutation.killed
        + len(mutation.survived)
        + len(mutation.crashed)
        + len(mutation.budget_exceeded)
    )

    claim = build_mutation_claim(baseline, mutation)
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.EXEC_FAILED
