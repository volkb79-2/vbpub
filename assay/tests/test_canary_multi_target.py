"""B007/A-432 — the ORDERED multi-target canary loop, end to end.

`tests/test_canary_result.py` already proves the MODEL (the attempts array,
the closed dispositions) and the JUDGEMENT (`judge_attempt`/`judge_canary`)
over hand-built payloads. This module proves the thing neither of those can:
that a real run of the shipped substrate PRODUCES those payloads — the
short circuit under `any`, the deliberate 2N materialisation under `all`
(DA-R19), the terminal INCONCLUSIVE, and the budget exhaustion that stays
its own terminal with the untried targets still visible.

Everything here runs the real `assay.runner.run_lane` / `assay.canary.
run_isolated_canaries` against a real git repository through real P22
snapshots. The one injected seam is the `process_runner` the whole suite
already injects (`assay.runner.ProcessRunner` is a declared boundary, not a
stand-in for an external system): the runner used below is a REAL gate in
miniature — it reads the snapshot's own bytes off disk and fails exactly
when the import-break marker is present, which is the behaviour a genuine
`pytest` exhibits and the reason `_pass_everything` alone cannot produce a
CAUGHT probe.

Every document these tests build is passed through `assay.verify`, so the
producer and the independent hand-transcribed re-derivation (A-182) are
proven to agree about each shape rather than assumed to.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    make_r3_judge,
    prepared_snapshot,
)

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.canary import run_isolated_canaries
from assay.config import CanaryConfig
from assay.errors import Outcome, ReasonCode
from assay.verify import verify_document

MOMENT = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

#: The marker `PythonAdapter.inject_import_break` writes into a transformed
#: file. Read from the adapter itself rather than restated, so the "gate"
#: below cannot drift from the transform it is supposed to catch.
_MARKER = PythonAdapter().inject_import_break("x = 1\n")[0].splitlines()[0]

TARGETS = ("pkg/greet.py", "pkg/farewell.py")


def _clock() -> datetime:
    return MOMENT


class CountingMonotonic:
    """The suite's own deterministic monotonic clock (copied verbatim from
    `test_canary_p23_isolated_edges.py`, which introduced it)."""

    def __init__(self, *, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        observed = self.value
        self.value += self.step
        return observed


def _seed(repo: GitRepo) -> str:
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/greet.py", "def greet():\n    return 'hello'\n")
    repo.write("pkg/farewell.py", "def farewell():\n    return 'goodbye'\n")
    return repo.commit_all("seed two canary targets")


def _lane(
    repo: GitRepo,
    *,
    targets: tuple[str, ...] = TARGETS,
    aggregation: str | None = "any",
    mechanism: str = "import-break",
):
    canary = (
        CanaryConfig(mechanism=mechanism, target=targets[0])
        if aggregation is None
        else CanaryConfig(
            mechanism=mechanism, targets=targets, aggregation=aggregation
        )
    )
    return make_lane(
        rigor=("R0", "R3"),
        judge=make_r3_judge(
            language="python",
            source_root_paths=(repo.path / "pkg",),
            canary=canary,
        ),
        argv=("check",),
    )


class _Gate:
    """A real gate in miniature: it fails the run iff the tree it was pointed
    at actually contains a broken import. Records every working directory it
    saw, so a test can count materialisations rather than infer them."""

    def __init__(self, *, catches: bool = True) -> None:
        self.cwds: list[Path] = []
        self.catches = catches

    def __call__(self, argv, *, env, cwd, timeout):
        self.cwds.append(Path(cwd))
        broken = self.catches and any(
            _MARKER in (Path(cwd) / name).read_text()
            for name in TARGETS
            if (Path(cwd) / name).exists()
        )
        return subprocess.CompletedProcess(list(argv), 1 if broken else 0, "", "")


class _NoOpTransformAdapter(PythonAdapter):
    """A real adapter that decides this file has nothing to break -- the
    established "nothing to judge" shape `test_canary_p23_isolated_edges.py`
    already documents, reused here to reach the TERMINAL branch."""

    def inject_import_break(self, text: str) -> tuple[str, str]:
        return text, "injected nothing at all"


def _run(repo: GitRepo, lane, *, adapter=None, gate=None):
    gate = gate if gate is not None else _Gate()
    verdict = runner.run_lane(
        lane,
        commit=repo.head(),
        repo=repo.path,
        project_root=repo.path,
        adapter=adapter if adapter is not None else PythonAdapter(),
        assay_version="0.1.0",
        process_runner=gate,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    return verdict, gate


def _r3(verdict):
    return next(claim for claim in verdict.claims if claim.rigor == "R3")


def _accepted(verdict) -> None:
    """A-182: the producer and the independently transcribed verifier must
    agree about the document, or the payload is not evidence of anything."""
    assert verify_document(verdict.to_dict()) == []


# --- `any`: the first caught probe answers the question ----------------------


def test_any_short_circuits_after_the_first_caught_probe(git_repo: GitRepo):
    """A-432's whole economic argument: each further target costs a measured
    ~2.76 s of materialisation plus two full command runs, so once one probe
    is caught an `any` lane stops -- and SAYS it stopped, in the closed
    vocabulary, rather than leaving the array short."""
    _seed(git_repo)
    verdict, gate = _run(git_repo, _lane(git_repo, aggregation="any"))

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (Outcome.PASS, None)
    assert claim.canary is not None
    attempts = claim.canary.attempts
    assert [a.target for a in attempts] == list(TARGETS)
    assert [a.disposition for a in attempts] == ["attempted", "not_attempted"]
    assert attempts[0].transformed_outcome is Outcome.FAIL
    assert attempts[0].observed_reason_code is ReasonCode.COMMAND_FAILED
    assert attempts[1].not_attempted_reason == "short_circuited"
    assert attempts[1].control_outcome is None
    # The baseline, then the first target's control and transform -- and
    # nothing for the second target, which is the saving the short circuit
    # exists to make.
    assert len(gate.cwds) == 3
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.targets == TARGETS
    assert verdict.judgment.r3.aggregation == "any"
    _accepted(verdict)


def test_any_fails_only_after_every_probe_survived(git_repo: GitRepo):
    """The other half of `any`: nothing short-circuits, every declared probe
    is attempted, and the claim is FAIL/CANARY_SURVIVED once the list is
    exhausted without a catch."""
    _seed(git_repo)
    verdict, gate = _run(
        git_repo, _lane(git_repo, aggregation="any"), gate=_Gate(catches=False)
    )

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )
    assert claim.canary is not None
    assert [a.disposition for a in claim.canary.attempts] == [
        "attempted",
        "attempted",
    ]
    assert len(gate.cwds) == 5, "the baseline plus two control/transform pairs"
    _accepted(verdict)


# --- `all`: DA-R19's deliberate 2N bound -------------------------------------


def test_all_attempts_every_probe_even_after_one_survives(git_repo: GitRepo):
    """DA-R19, affirmed: `all` does NOT short-circuit on a FAIL. Naming EVERY
    surviving probe is the whole reason a lane declares several, so the
    second target is attempted after the first survived -- and the claim is
    still FAIL/CANARY_SURVIVED."""
    _seed(git_repo)
    verdict, gate = _run(
        git_repo, _lane(git_repo, aggregation="all"), gate=_Gate(catches=False)
    )

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )
    assert claim.canary is not None
    assert [a.disposition for a in claim.canary.attempts] == [
        "attempted",
        "attempted",
    ]
    assert [a.transformed_outcome for a in claim.canary.attempts] == [
        Outcome.PASS,
        Outcome.PASS,
    ], "both bad cases unexpectedly passed -- both survived"
    assert len(gate.cwds) == 5
    _accepted(verdict)


def test_all_passes_only_when_every_probe_was_caught(git_repo: GitRepo):
    _seed(git_repo)
    verdict, gate = _run(git_repo, _lane(git_repo, aggregation="all"))

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (Outcome.PASS, None)
    assert claim.canary is not None
    assert [a.disposition for a in claim.canary.attempts] == [
        "attempted",
        "attempted",
    ]
    assert len(gate.cwds) == 5
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.aggregation == "all"
    _accepted(verdict)


# --- the terminals ------------------------------------------------------------


def test_an_inconclusive_probe_ends_the_run_in_both_modes(git_repo: GitRepo):
    """An INCONCLUSIVE attempt is TERMINAL, not aggregated: nothing further
    can be concluded, so the loop stops and every later target is recorded
    `earlier_target_terminal` -- which is exactly what `verify.py`'s
    bookkeeping check re-derives."""
    _seed(git_repo)
    verdict, gate = _run(
        git_repo, _lane(git_repo, aggregation="all"), adapter=_NoOpTransformAdapter()
    )

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert claim.canary is not None
    attempts = claim.canary.attempts
    assert [a.disposition for a in attempts] == ["attempted", "not_attempted"]
    assert attempts[1].not_attempted_reason == "earlier_target_terminal"
    assert len(gate.cwds) == 2, "the baseline and one control; no transform ran"
    _accepted(verdict)


def test_a_broken_lane_baseline_ends_the_claim_before_any_probe(git_repo: GitRepo):
    """The lane's own command did not PASS, so no probe has a known-good
    control. The FIRST declared target carries the baseline's outcome (which
    judges INCONCLUSIVE, terminal) and every later one is recorded
    `earlier_target_terminal` -- never truncated out of the document, which
    would hide what the lane declared."""
    _seed(git_repo)

    def failing(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), 1, "", "")

    verdict, _ = _run(
        git_repo, _lane(git_repo, aggregation="any"), gate=failing
    )

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert claim.canary is not None
    attempts = claim.canary.attempts
    assert [a.target for a in attempts] == list(TARGETS)
    assert [a.disposition for a in attempts] == ["attempted", "not_attempted"]
    assert attempts[0].control_outcome is Outcome.FAIL
    assert attempts[1].not_attempted_reason == "earlier_target_terminal"
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.targets == TARGETS, (
        "the declaration is reported in full even when nothing ran"
    )
    _accepted(verdict)


class _ExpiringMonotonic:
    """A monotonic clock that runs out of budget the moment *expired* says
    so -- the injected-expiry seam `make_deadline` documents.

    The predicate the budget test supplies is "the first target's two runs
    have both happened", so the budget is gone from somewhere inside the
    first target's own tail onwards -- which is where a real lane runs out,
    and the shape the loop has to record honestly.
    """

    def __init__(self, *, expired, budget: float) -> None:
        self.expired = expired
        self.budget = budget
        self.value = 0.0

    def __call__(self) -> float:
        if self.expired():
            return self.budget + 1.0
        observed = self.value
        self.value += 0.01
        return observed


def test_budget_exhaustion_is_its_own_terminal_and_keeps_the_payload(
    git_repo: GitRepo, tmp_path: Path
):
    """A-432: budget exhaustion is NEVER folded into the aggregation. The
    claim is BUDGET_EXCEEDED/LANE_TIMEOUT, and the untried targets stay
    VISIBLE in the payload it carries -- which is why `verify.py` re-derives
    the aggregation for JUDGED statuses only."""
    head_rev = _seed(git_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = _lane(git_repo, aggregation="all")
    gate = _Gate()
    # The budget survives the first target's control run and its transform
    # run -- the two entries the gate records -- and is gone from the next
    # sample onwards, so the SECOND target is never entered at all.
    deadline = make_deadline(
        budget_seconds=60.0,
        monotonic=_ExpiringMonotonic(
            expired=lambda: len(gate.cwds) >= 2, budget=60.0
        ),
    )

    with prepared_snapshot(
        git_repo, commit=head_rev, scratch_root=scratch
    ) as prepared:
        result, terminal = run_isolated_canaries(
            lane,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=deadline,
            project_root=git_repo.path,
            resolved_base=None,
            mechanism="import-break",
            targets=TARGETS,
            aggregation="all",
            adapter=PythonAdapter(),
            process_runner=gate,
            clock=_clock,
        )

    assert terminal is not None
    assert (terminal.outcome, terminal.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert [a.target for a in result.attempts] == list(TARGETS)
    assert [a.not_attempted_reason for a in result.attempts] == [
        None,
        "budget_exhausted",
    ] or [a.not_attempted_reason for a in result.attempts] == [
        "budget_exhausted",
        "budget_exhausted",
    ], "the probe the deadline cut short, and every one after it"
    assert result.attempts[-1].disposition == "not_attempted"
    assert result.attempts[-1].target == TARGETS[1]
    assert "deadline" in result.attempts[-1].description
    assert len(gate.cwds) == 2, "the second target was never entered"


def test_a_budget_exhausted_r3_claim_reaches_the_wire_with_its_payload(
    git_repo: GitRepo,
):
    """The same terminal, through `run_lane` and out to a whole document:
    the claim is BUDGET_EXCEEDED/LANE_TIMEOUT with the refusing sentence
    (B053/A-428) AND the canary payload, and the independent verifier
    accepts it -- which is the pair `verify.py`'s judged-statuses-only
    re-derivation exists to permit."""
    _seed(git_repo)
    gate = _Gate()
    lane = _lane(git_repo, aggregation="all")
    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=gate,
        clock=_clock,
        # The lane's own baseline plus the first target's two runs survive;
        # the budget is gone before the second target is entered.
        monotonic=_ExpiringMonotonic(
            expired=lambda: len(gate.cwds) >= 3, budget=300.0
        ),
    )

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert claim.canary is not None, "the untried targets must stay visible"
    assert [a.target for a in claim.canary.attempts] == list(TARGETS)
    assert claim.canary.attempts[-1].not_attempted_reason == "budget_exhausted"
    assert claim.detail is not None, "a refusal carries its own sentence"
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.targets == TARGETS
    _accepted(verdict)


# --- the singular spelling is unchanged --------------------------------------


def test_one_declared_target_is_the_v9_run_verbatim(git_repo: GitRepo):
    """The compatibility statement the whole additive design rests on: a lane
    that declared the singular `target` produces a one-element attempts
    array, records NO aggregation, and runs exactly one probe."""
    _seed(git_repo)
    verdict, gate = _run(git_repo, _lane(git_repo, aggregation=None))

    claim = _r3(verdict)
    assert (claim.status, claim.reason_code) == (Outcome.PASS, None)
    assert claim.canary is not None
    assert [a.target for a in claim.canary.attempts] == [TARGETS[0]]
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.targets == (TARGETS[0],)
    assert verdict.judgment.r3.aggregation is None
    assert len(gate.cwds) == 3
    _accepted(verdict)


# --- the verifier refuses what the producer cannot write ----------------------


def test_a_short_circuit_the_aggregation_cannot_explain_is_refused(
    git_repo: GitRepo,
):
    """The bookkeeping half of A-182's re-derivation, proven against a REAL
    document rather than a hand-built one: take the short-circuiting `any`
    verdict this module produces and relabel its aggregation `all`. Only
    `any` short-circuits, so the document now contradicts itself."""
    _seed(git_repo)
    verdict, _ = _run(git_repo, _lane(git_repo, aggregation="any"))
    document = verdict.to_dict()
    assert verify_document(document) == []

    document["judgment"]["r3"]["aggregation"] = "all"
    failures = verify_document(document)
    assert any("short_circuited" in failure for failure in failures), failures


def test_a_payload_under_a_refusal_other_than_the_budget_is_refused(
    git_repo: GitRepo,
):
    """The carve-out is exactly one status wide. `BUDGET_EXCEEDED` may carry
    the payload because the untried targets are the point; every other
    refusal means no probe produced a record to report at all."""
    _seed(git_repo)
    verdict, _ = _run(git_repo, _lane(git_repo, aggregation="any"))
    document = verdict.to_dict()

    for claim in document["claims"]:
        if claim["rigor"] == "R3":
            claim["status"] = "ERROR"
            claim["reason_code"] = "BAD_LANE_CONFIG"
    document["outcome"] = "ERROR"
    document["reason_code"] = "BAD_LANE_CONFIG"
    document["exit_code"] = 2
    failures = verify_document(document)
    assert any(
        "only non-judged status that may is BUDGET_EXCEEDED" in failure
        for failure in failures
    ), failures


@pytest.mark.parametrize("aggregation", ["any", "all"])
def test_the_declared_order_is_the_attempted_order(
    git_repo: GitRepo, aggregation: str
):
    """P21/A-152's single equality, generalised: a reordered array would let
    a SURVIVING probe be reported under a caught probe's name."""
    _seed(git_repo)
    reversed_targets = tuple(reversed(TARGETS))
    verdict, _ = _run(
        git_repo,
        _lane(git_repo, targets=reversed_targets, aggregation=aggregation),
        gate=_Gate(catches=False),
    )

    claim = _r3(verdict)
    assert claim.canary is not None
    assert [a.target for a in claim.canary.attempts] == list(reversed_targets)
    assert verdict.judgment is not None and verdict.judgment.r3 is not None
    assert verdict.judgment.r3.targets == reversed_targets
    _accepted(verdict)
