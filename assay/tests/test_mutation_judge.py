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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationTarget, build_mutation_claim, judge_mutation, run_mutation
from assay.runner import CommandPlan, CommandResult, execute_command

#: sha256(b"<="), hand-computed rather than read back from the code (A-067).
_SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"

_PLAN = CommandPlan(
    argv_declared=("pytest", "-q"),
    argv_appended=(),
    argv_effective=("pytest", "-q"),
    env_declared={},
    env_effective={},
    env_passthrough=(),
    allow_argv_append=False,
    budget_seconds=60.0,
    project_prefix=PurePosixPath("."),
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
    mutation = Mutation(candidate_count=0, total=0)
    assert judge_mutation(baseline, mutation) == (Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS)


def test_crashed_outranks_survived_and_budget_exceeded_when_all_three_are_present():
    """The precedence claim, made concrete: a run with ALL THREE non-empty
    buckets must still render crashed's own outcome -- checking survived
    or budget_exceeded FIRST would silently launder a real crash."""
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, start_byte=10, end_byte=11, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    crashed = MutantOutcome(path="pkg/mod.py", lineno=2, start_byte=20, end_byte=21, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    stopped = MutantOutcome(path="pkg/mod.py", lineno=3, start_byte=30, end_byte=31, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    mutation = Mutation(
        candidate_count=3,
        total=3,
        survived=(survivor,),
        crashed=(crashed,),
        budget_exceeded=(stopped,),
    )
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.ERROR, ReasonCode.EXEC_FAILED)


def test_budget_exceeded_outranks_survived_when_both_are_present():
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, start_byte=10, end_byte=11, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    stopped = MutantOutcome(path="pkg/mod.py", lineno=3, start_byte=30, end_byte=31, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    mutation = Mutation(
        candidate_count=2, total=2, survived=(survivor,), budget_exceeded=(stopped,)
    )
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT)


def test_survived_alone_is_fail_mutants_survived():
    from assay.verdict import Mutation, MutantOutcome

    survivor = MutantOutcome(path="pkg/mod.py", lineno=1, start_byte=10, end_byte=11, replacement_sha256=_SHA_LTE, operator="python:compare-swap", description="Lt->LtE")
    mutation = Mutation(candidate_count=1, total=1, survived=(survivor,))
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED)


def test_every_bucket_empty_with_a_positive_total_is_pass():
    from assay.verdict import Mutation

    from assay.verdict import MutantOutcome

    killed = tuple(
        MutantOutcome(
            path="pkg/mod.py",
            lineno=index,
            start_byte=index * 10,
            end_byte=index * 10 + 1,
            replacement_sha256=_SHA_LTE,
            operator="python:compare-swap",
            description="Lt->LtE",
        )
        for index in (1, 2, 3)
    )
    mutation = Mutation(candidate_count=3, total=3, killed=killed)
    baseline = _baseline(Outcome.PASS, None)
    assert judge_mutation(baseline, mutation) == (Outcome.PASS, None)


# --- build_mutation_claim: the R2 Claim wiring -------------------------------


def test_build_mutation_claim_wires_status_reason_and_the_mutation_payload():
    from assay.verdict import Mutation

    from assay.verdict import MutantOutcome

    killed = tuple(
        MutantOutcome(
            path="pkg/mod.py",
            lineno=index,
            start_byte=index * 10,
            end_byte=index * 10 + 1,
            replacement_sha256=_SHA_LTE,
            operator="python:compare-swap",
            description="Lt->LtE",
        )
        for index in (1, 2)
    )
    mutation = Mutation(candidate_count=2, total=2, killed=killed)
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
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _decide(repo.path)
    plan = make_plan(lane)
    deadline = make_deadline()

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation.total == 4
    assert len(mutation.killed) == 1
    assert len(mutation.survived) == 1
    assert len(mutation.crashed) == 1
    assert len(mutation.budget_exceeded) == 1
    assert mutation.total == (
        len(mutation.killed)
        + len(mutation.survived)
        + len(mutation.crashed)
        + len(mutation.budget_exceeded)
    )
    # P21/A-180: every attempted mutant is now an identity, killed included,
    # and no identity may appear twice across the four buckets.
    identities = [
        entry.identity
        for bucket in (
            mutation.killed,
            mutation.survived,
            mutation.crashed,
            mutation.budget_exceeded,
        )
        for entry in bucket
    ]
    assert len(set(identities)) == 4
    assert mutation.candidate_count == 4

    claim = build_mutation_claim(baseline, mutation)
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.EXEC_FAILED
