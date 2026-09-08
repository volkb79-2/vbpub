"""B067 -- ``budget = "unbounded"``: admissible ONLY where every unit of the
lane's work carries its own bound.

The entry's own three acceptance boxes, in order:

1. ``unbounded`` with a missing unit bound refuses AT LOAD, naming the unit;
2. an unbounded R2 lane with ``budget_per_candidate`` runs to completion with
   no ``LANE_TIMEOUT`` path reachable (measured on a real lane -- a real Git
   repository, real P22 snapshots, a real mutation sweep);
3. CONSUMERS' worked mutation lane shows the recommended shape (docs, not a
   test).

The mechanism half -- an unbounded :class:`~assay.runner.LaneDeadline`
reporting ``math.inf``, and each boundary that turns a remainder into a real
child timeout converting it to "no timeout" -- is pinned here too, because an
infinity that reaches :mod:`selectors` raises ``OverflowError`` rather than
waiting forever, and that failure would look like a lane bug rather than a
missing conversion.
"""

from __future__ import annotations

import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, make_lane, make_plan, make_r2_judge

from assay import git as git_module
from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import (
    UNBOUNDED_BUDGET,
    CanaryConfig,
    LaneConfigError,
    MutationConfig,
    load_lane_file,
)
from assay.errors import AssayError, Outcome, ReasonCode
from assay.runner import LaneDeadline, execute_plan


MOMENT = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return MOMENT


_SOURCE = "def flip(a, b):\n    return a > b\n"


def _project(tmp_path: Path, toml: str) -> Path:
    """A real project directory whose ``assay.toml`` is *toml*, with the one
    source file every lane below declares."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "flags.py").write_text(_SOURCE, encoding="utf-8")
    path = root / "assay.toml"
    path.write_text(toml, encoding="utf-8")
    return path


_HEADER = """\
schema_version = 2

[lanes.only]
scope = "S1"
rigor = {rigor}
enforcement = "gate"
argv = ["pytest", "-q"]
env = {{ MOCK_MODE = "true" }}
env_passthrough = ["PATH"]
budget = "{budget}"
allow_argv_append = false

[lanes.only.isolation]
snapshot_selection = "repository"

[lanes.only.judge]
language = "python"
source_roots = ["pkg"]
fail_under = 0.0
allow_excluded = false
require_branch = false
mode = "whole_target"
targets = ["pkg/flags.py"]

[lanes.only.judge.coverage]
format = "cobertura"
artifact = "cov.xml"
"""

_NATIVE_MUTATION = """\

[lanes.only.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
"""

_INGESTED_MUTATION = """\

[lanes.only.judge.mutation]
format = "mutation-report-json"
artifact = "mutants.json"
fail_under = 80.0
"""

_CANARY = """\

[lanes.only.judge.canary]
mechanism = "import-break"
target = "pkg/flags.py"
"""


def _toml(*, rigor: str, budget: str = UNBOUNDED_BUDGET, tail: str = "") -> str:
    return _HEADER.format(rigor=rigor, budget=budget) + tail


# --- box 1: the load-time refusals, each naming its own unit -----------------


def test_an_R0_R1_lane_refuses_unbounded_by_name(tmp_path):
    """The entry's own headline refusal: an R0/R1 lane is ONE command, whose
    only liveness bound IS `budget`. There is no sub-unit to require a bound
    of, so `unbounded` would leave the lane with no bound at all -- which is
    the opposite of what the declaration means.
    """
    path = _project(tmp_path, _toml(rigor='["R0", "R1"]'))
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    assert "an R0/R1 lane is ONE command" in message
    assert "no per-unit bound to require" in message
    assert "['R0', 'R1']" in message


def test_a_native_R2_lane_without_budget_per_candidate_names_the_missing_bound(
    tmp_path,
):
    path = _project(tmp_path, _toml(rigor='["R0", "R1", "R2"]', tail=_NATIVE_MUTATION))
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    assert "judge.mutation.budget_per_candidate (one mutant command)" in message
    assert "every unit of the lane's work to carry its own bound" in message


def test_an_ingested_R2_lane_refuses_unbounded_as_the_one_command_it_is(tmp_path):
    """An INGESTED R2 lane's argv runs the foreign mutation tool itself, so
    it is one command exactly like an R0/R1 lane -- and
    `budget_per_candidate` is already refused there (assay chose none of that
    run's execution policy). The refusal says so in its own words rather than
    demanding a key the loader would then reject.
    """
    path = _project(
        tmp_path, _toml(rigor='["R0", "R1", "R2"]', tail=_INGESTED_MUTATION)
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    assert "an ingested R2 lane is ONE command" in str(excinfo.value)


def test_declaring_R3_does_not_exempt_an_R0_R1_lane_from_needing_a_bound(tmp_path):
    """(Round-1 blocker B1.) The refusal used to be conditioned on the
    ABSENCE of R3 -- `if (not r2 and not r3) or (ingested_r2 and not r3)` --
    so declaring a canary switched it off entirely.

    `judge.canary.budget_per_attempt` bounds one canary PROBE. It does not
    bound the lane's own top-level command, which is what produces the R0
    status and the R1 coverage artifact. So an R0/R1+R3 lane under
    `unbounded` ran its own evidence-producing command with no bound at all
    -- exactly the state the refusal's own message calls impossible. The
    reviewer reproduced it end to end: `child timeout=None` on the lane's
    own argv, beside two properly-bounded canary halves.
    """
    path = _project(
        tmp_path,
        _toml(
            rigor='["R0", "R1", "R3"]',
            tail=_CANARY + 'budget_per_attempt = "30s"\n',
        ),
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    assert "an R0/R1 lane is ONE command" in message
    # The refusal must say WHY the declared per-attempt bound does not save
    # it, or an author reads it as a contradiction of the lane they wrote.
    assert "budget_per_attempt" in message
    assert "['R0', 'R1', 'R3']" in message


def test_declaring_R3_does_not_exempt_an_ingested_R2_lane_either(tmp_path):
    """The same hole, on the worse of the two shapes: an ingested R2 lane's
    ENTIRE R2 evidence comes from that one unbounded command."""
    path = _project(
        tmp_path,
        _toml(
            rigor='["R0", "R1", "R2", "R3"]',
            tail=_INGESTED_MUTATION + _CANARY + 'budget_per_attempt = "30s"\n',
        ),
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    assert "an ingested R2 lane is ONE command" in message
    assert "budget_per_attempt" in message


def test_the_one_admissible_shape_is_a_native_R2_sweep(tmp_path):
    """The whole table, in one place -- because B1 got through precisely
    because every tier was tested in ISOLATION and no test asked what a
    COMBINATION does.

    A native R2 sweep is the one shape where the unguessable bulk of the work
    is bounded per unit; the single command it leaves unbounded (the
    baseline) is B076, filed and reasoned. Every other shape's only
    evidence-producing work IS that one command.
    """
    admitted: dict[str, bool] = {}
    for label, rigor, tail in (
        ("R0/R1", '["R0", "R1"]', ""),
        (
            "R0/R1+R3",
            '["R0", "R1", "R3"]',
            _CANARY + 'budget_per_attempt = "30s"\n',
        ),
        ("ingested R2", '["R0", "R1", "R2"]', _INGESTED_MUTATION),
        (
            "ingested R2+R3",
            '["R0", "R1", "R2", "R3"]',
            _INGESTED_MUTATION + _CANARY + 'budget_per_attempt = "30s"\n',
        ),
        (
            "native R2",
            '["R0", "R1", "R2"]',
            _NATIVE_MUTATION + 'budget_per_candidate = "45s"\n',
        ),
        (
            "native R2+R3",
            '["R0", "R1", "R2", "R3"]',
            _NATIVE_MUTATION
            + 'budget_per_candidate = "45s"\n'
            + _CANARY
            + 'budget_per_attempt = "30s"\n',
        ),
    ):
        project = _project(tmp_path / label.replace("/", "-"), _toml(rigor=rigor, tail=tail))
        try:
            load_lane_file(project)
        except LaneConfigError:
            admitted[label] = False
        else:
            admitted[label] = True

    assert admitted == {
        "R0/R1": False,
        "R0/R1+R3": False,
        "ingested R2": False,
        "ingested R2+R3": False,
        "native R2": True,
        "native R2+R3": True,
    }


def test_no_child_of_an_unbounded_R0_R1_R3_lane_ever_gets_no_timeout(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    """(Round-1 blocker B1.) The reviewer's own repro, pinned at the process
    boundary rather than at the loader.

    A load-time assertion alone would not have caught the ORIGINAL defect any
    better than the tests that missed it: what makes this one binding is that
    it instruments the actual `subprocess.run` call and asserts no child of
    this lane was ever launched with `timeout=None`. Before the fix the
    lane's own argv ran exactly that way.
    """
    recorded: list[object] = []
    real_run = subprocess.run

    def record(*args, **kwargs):
        recorded.append(kwargs.get("timeout", "MISSING"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)

    git_repo.write(
        "assay.toml",
        _toml(
            rigor='["R0", "R1", "R3"]',
            tail=_CANARY + 'budget_per_attempt = "30s"\n',
        ).replace('argv = ["pytest", "-q"]', 'argv = ["/bin/sh", "-c", "exit 0"]'),
    )
    git_repo.write("pkg/flags.py", _SOURCE)
    git_repo.commit_all("unbounded R0/R1/R3 lane")

    from assay.cli import main

    exit_code = main(["run", "only", "--file", str(git_repo.path / "assay.toml")])

    assert exit_code != 0, "the lane must be refused, not run"
    assert None not in recorded, (
        "a child of an unbounded R0/R1+R3 lane was launched with no timeout at "
        f"all: {recorded}"
    )


def test_an_R3_lane_without_budget_per_attempt_names_the_missing_bound(tmp_path):
    path = _project(
        tmp_path,
        _toml(rigor='["R0", "R1", "R2", "R3"]', tail=_NATIVE_MUTATION + _CANARY),
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    # BOTH units are named -- this lane declares neither bound, and a reader
    # fixing one at a time would otherwise need two round trips.
    assert "judge.mutation.budget_per_candidate (one mutant command)" in message
    assert "judge.canary.budget_per_attempt (one canary probe)" in message


def test_an_unbounded_lane_with_every_unit_bound_loads_and_carries_no_seconds(
    tmp_path,
):
    path = _project(
        tmp_path,
        _toml(
            rigor='["R0", "R1", "R2", "R3"]',
            tail=(
                _NATIVE_MUTATION
                + 'budget_per_candidate = "45s"\n'
                + _CANARY
                + 'budget_per_attempt = "3m"\n'
            ),
        ),
    )
    lane = load_lane_file(path).lanes["only"]
    assert lane.budget == UNBOUNDED_BUDGET
    # `None` -- and only `None` -- is "unbounded". Never 0, never a large
    # finite stand-in a reader could mistake for a real declaration.
    assert lane.budget_seconds is None
    assert lane.judge.canary.budget_per_attempt == "3m"
    assert lane.judge.canary.budget_per_attempt_seconds == 180.0
    # A-052's own round-trip proof: `as_declared` reproduces what was
    # WRITTEN, so `budget = "unbounded"` and the new canary key both ride
    # back out verbatim.
    declared = lane.as_declared()
    assert declared["budget"] == UNBOUNDED_BUDGET
    assert declared["judge"]["canary"]["budget_per_attempt"] == "3m"
    assert declared["judge"]["mutation"]["budget_per_candidate"] == "45s"


def test_a_numeric_budget_lane_is_untouched_by_B067(tmp_path):
    """The regression guard the entry's own "the lane-wide deadline machinery
    is unchanged for a NUMERIC budget" claim needs: an R0/R1 lane with a
    duration still loads, still parses, and its `as_declared` still omits the
    canary key it never wrote.
    """
    path = _project(tmp_path, _toml(rigor='["R0", "R1"]', budget="5m"))
    lane = load_lane_file(path).lanes["only"]
    assert (lane.budget, lane.budget_seconds) == ("5m", 300.0)
    assert "budget_per_attempt" not in lane.as_declared()["judge"].get("canary", {})


def test_budget_per_attempt_is_validated_as_a_duration(tmp_path):
    path = _project(
        tmp_path,
        _toml(
            rigor='["R0", "R1", "R3"]',
            budget="5m",
            tail=_CANARY + 'budget_per_attempt = "nonsense"\n',
        ),
    )
    with pytest.raises(LaneConfigError, match="budget_per_attempt"):
        load_lane_file(path)


def test_budget_per_attempt_defaults_absent_and_round_trips_absent(tmp_path):
    """B007/A-432's own byte-unchanged rule, extended: an R3 lane written
    before this field existed must still render exactly what it wrote."""
    path = _project(
        tmp_path, _toml(rigor='["R0", "R1", "R3"]', budget="5m", tail=_CANARY)
    )
    canary = load_lane_file(path).lanes["only"].judge.canary
    assert canary.budget_per_attempt is None
    assert canary.budget_per_attempt_seconds is None
    assert canary.as_declared() == {
        "mechanism": "import-break",
        "target": "pkg/flags.py",
    }


def test_a_hand_built_CanaryConfig_keeps_its_existing_invariants():
    """`budget_per_attempt` is additive: the target/targets and aggregation
    invariants `__post_init__` states are unchanged by it."""
    with pytest.raises(ValueError, match="exactly one of target/targets"):
        CanaryConfig(mechanism="import-break", budget_per_attempt="30s")


# --- the deadline mechanism -------------------------------------------------


def test_an_unbounded_deadline_reports_infinity_and_never_expires():
    """`remaining()` returns the honest answer rather than raising, and
    rather than a large finite number pretending to be one."""
    ticks = iter([0.0, 1.0, 10_000_000.0])
    deadline = LaneDeadline.start(budget_seconds=None, monotonic=lambda: next(ticks))
    assert deadline.unbounded is True
    assert deadline.remaining() == math.inf
    assert deadline.remaining() == math.inf


def test_a_numeric_deadline_still_expires_exactly_as_before():
    ticks = iter([0.0, 5.0])
    deadline = LaneDeadline.start(budget_seconds=1.0, monotonic=lambda: next(ticks))
    assert deadline.unbounded is False
    with pytest.raises(AssayError) as excinfo:
        deadline.remaining()
    assert (excinfo.value.outcome, excinfo.value.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )


def test_tightened_narrows_an_unbounded_deadline_to_the_unit_bound():
    now = [0.0]
    deadline = LaneDeadline.start(budget_seconds=None, monotonic=lambda: now[0])
    unit = deadline.tightened(30.0)
    assert unit.unbounded is False
    assert unit.remaining() == pytest.approx(30.0)
    # ...and the lane deadline it came from is untouched: `tightened` returns
    # a NEW deadline, so one attempt's bound cannot leak into the next.
    assert deadline.remaining() == math.inf


def test_tightened_never_widens_a_numeric_deadline():
    now = [0.0]
    deadline = LaneDeadline.start(budget_seconds=10.0, monotonic=lambda: now[0])
    assert deadline.tightened(600.0).remaining() == pytest.approx(10.0)
    # `None` is the pre-B067 call site: the SAME object back, so every lane
    # that declares no per-unit bound behaves byte-identically.
    assert deadline.tightened(None) is deadline


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan, True, "30s"])
def test_tightened_refuses_anything_that_is_not_a_positive_finite_bound(bad):
    deadline = LaneDeadline.start(budget_seconds=10.0, monotonic=lambda: 0.0)
    with pytest.raises(ValueError, match="positive finite number or None"):
        deadline.tightened(bad)


# --- the boundaries that turn a remainder into a child timeout ---------------


def test_execute_plan_hands_a_child_no_timeout_at_all_when_unbounded(git_repo):
    """The conversion that makes an unbounded lane RUN. `subprocess.run`
    spells "no timeout" as `None`; an infinity handed to it instead raises
    `OverflowError` deep inside `selectors`, which would surface as an
    unrelated crash rather than as a lane that waits.
    """
    seen: list[object] = []

    def record(argv, *, env, cwd, timeout):
        seen.append(timeout)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    lane = make_lane(argv=("check",), budget=UNBOUNDED_BUDGET, budget_seconds=None)
    result = execute_plan(
        make_plan(lane),
        cwd=git_repo.path,
        timeout=math.inf,
        process_runner=record,
        clock=_clock,
    )
    assert result.outcome is Outcome.PASS
    assert seen == [None]


def test_execute_plan_still_forwards_a_finite_timeout_verbatim(git_repo):
    seen: list[object] = []

    def record(argv, *, env, cwd, timeout):
        seen.append(timeout)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    lane = make_lane(argv=("check",))
    execute_plan(
        make_plan(lane),
        cwd=git_repo.path,
        timeout=12.5,
        process_runner=record,
        clock=_clock,
    )
    assert seen == [12.5]


@pytest.mark.parametrize("bad", [0.0, -1.0, -math.inf, math.nan, True])
def test_execute_plan_still_refuses_every_other_non_positive_timeout(git_repo, bad):
    lane = make_lane(argv=("check",))
    with pytest.raises(ValueError, match="positive finite number or math.inf"):
        execute_plan(
            make_plan(lane),
            cwd=git_repo.path,
            timeout=bad,
            process_runner=lambda *a, **k: None,
            clock=_clock,
        )


def test_git_collapses_an_infinite_remainder_to_no_timeout():
    """`_sample_remaining` is the ONE place a lane remainder becomes a
    `selectors`/`Popen.wait` timeout for a Git child. `None` already means
    "wait, unbounded" to every call site below it, which is exactly what
    `math.inf` denotes -- so the two collapse here rather than at each site.
    """
    assert git_module._sample_remaining(None) is None
    assert git_module._sample_remaining(lambda: math.inf) is None
    assert git_module._sample_remaining(lambda: 4.0) == 4.0


def test_the_P22_deadline_reports_no_timeout_when_constructed_unbounded():
    unbounded = git_module._P22Deadline(math.inf)
    assert unbounded.remaining("materialising") is None
    bounded = git_module._P22Deadline(30.0)
    assert 0.0 < bounded.remaining("materialising") <= 30.0


# --- box 2: a REAL unbounded R2 lane runs to completion ----------------------


def _seed(repo: GitRepo) -> tuple[str, str]:
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("base")
    repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    head_rev = repo.commit_all("introduce sites")
    return base_rev, head_rev


def test_a_real_unbounded_R2_lane_runs_every_candidate_with_no_lane_timeout(
    git_repo, tmp_path
):
    """Box 2, measured rather than asserted: a real Git repository, real P22
    snapshots, the real mutation sweep, and `budget = "unbounded"` with
    `budget_per_candidate` declared.

    Two facts are checked, and the second is the one that matters:

    * the sweep completes -- every candidate lands in a real bucket, and NO
      identity is `budget_exceeded`, which is the only bucket a
      `LANE_TIMEOUT` could produce here;
    * every mutant's child was launched with the PER-CANDIDATE bound, never
      with `None`. An unbounded lane must not hand an unbounded timeout to
      the units it promised were individually bounded -- that would make the
      declaration a lie, and it is precisely the trap `min(remaining,
      per_candidate)` is there to avoid now that `remaining` can be infinite.
    """
    base_rev, head_rev = _seed(git_repo)
    timeouts: list[object] = []

    def record(argv, *, env, cwd, timeout):
        timeouts.append(timeout)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    lane = make_lane(
        rigor=("R0", "R2"),
        judge=make_r2_judge(
            language="python",
            source_root_paths=(git_repo.path / "pkg",),
            base=base_rev,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=20,
                operators=("python:compare-swap",),
                budget_per_candidate="45s",
            ),
        ),
        argv=("check",),
        budget=UNBOUNDED_BUDGET,
        budget_seconds=None,
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=record,
        clock=_clock,
    )

    r0, r2 = verdict.claims[0], verdict.claims[1]
    assert r0.status is Outcome.PASS
    assert r2.reason_code is not ReasonCode.LANE_TIMEOUT
    assert r2.mutation is not None
    assert r2.mutation.total > 0
    assert r2.mutation.budget_exceeded == (), "no identity was budget-stopped"
    # Every candidate reached a real, decided bucket -- the sweep RAN to the
    # end rather than being cut short. (`record` passes everything, so a
    # mutant that changes behaviour is not caught: these all `survived`,
    # which is a decided outcome, not a budget stop.)
    decided = (
        len(r2.mutation.killed)
        + len(r2.mutation.survived)
        + len(r2.mutation.crashed)
        + len(r2.mutation.equivalent)
    )
    assert decided == r2.mutation.total

    # The baseline ran unbounded (it is the lane's own single command, and
    # `budget_per_candidate` is a PER-MUTANT bound -- a baseline runs the
    # whole suite, so tightening it to the per-mutant value would refuse
    # healthy lanes); every mutant after it ran under the declared bound.
    assert timeouts[0] is None
    assert timeouts[1:] == [45.0] * (len(timeouts) - 1)
    assert len(timeouts) == r2.mutation.total + 1
