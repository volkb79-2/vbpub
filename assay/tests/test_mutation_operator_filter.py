"""P18 work items 3/8 — operator filtering, proven in isolation from
target scoping and every other R2 concern this package's own oracle O1
names separately: a fixture with TWO different mutation sites (one
``compare-swap``, one ``bool-const-flip``) on the SAME diff, a lane
declaring only ONE of the two operators, and a check that the
undeclared-operator mutant is never even SUBMITTED to the process
boundary -- not merely "the final total excludes it", which a filter bug
that instead excluded the WRONG site could satisfy by accident (a hollow
proof: two operators, one declared, one survivor -- correct counts for
the wrong reason).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import make_lane

from assay.adapters.python import PythonAdapter
from assay.mutation import MutationTarget, _filter_by_operators, collect_mutants, run_mutation
from assay.runner import execute_command

#: line 2 is a `compare-swap` site (`x > 0` -> `x >= 0`); line 3 is an
#: UNRELATED `bool-const-flip` site (`True` -> `False`) -- two different
#: operators, two different lines, in the SAME target.
_TEXT = (
    "def f(x, y):\n"
    "    if x > 0:\n"
    "        y = True\n"
    "    return y\n"
)
_TARGETS = (
    MutationTarget(path="pkg/mod.py", text=_TEXT, lines=frozenset({2, 3})),
)


def test_the_fixture_itself_generates_both_operator_types_before_filtering():
    """The fixture's own precondition, checked directly: without any
    filtering, BOTH sites are real, distinct, collectible mutants -- if
    this failed, every assertion below would be proving nothing."""
    jobs = collect_mutants(_TARGETS, adapter=PythonAdapter())
    assert {job.mutant.operator for job in jobs} == {"compare-swap", "bool-const-flip"}


def test_filter_by_operators_keeps_only_the_declared_operator():
    jobs = collect_mutants(_TARGETS, adapter=PythonAdapter())

    filtered = _filter_by_operators(jobs, frozenset({"compare-swap"}))

    assert [job.mutant.operator for job in filtered] == ["compare-swap"]


def test_filter_by_operators_with_every_operator_declared_keeps_everything():
    jobs = collect_mutants(_TARGETS, adapter=PythonAdapter())

    filtered = _filter_by_operators(
        jobs, frozenset({"compare-swap", "bool-const-flip"})
    )

    assert filtered == jobs


def test_filter_by_operators_with_an_empty_set_keeps_nothing():
    jobs = collect_mutants(_TARGETS, adapter=PythonAdapter())

    assert _filter_by_operators(jobs, frozenset()) == ()


def test_run_mutation_never_submits_an_undeclared_operators_mutant(tmp_path: Path):
    """The end-to-end proof, at the public surface: the ``bool-const-
    flip`` site is never even EXECUTED -- a process-boundary CALL-COUNT
    check, not merely a result-count check, so a filter that ran the
    wrong mutant and then mis-reported the total could not pass this."""
    lane = make_lane(argv=("pytest", "-q"))
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    (project_root / "pkg").mkdir(parents=True)
    (project_root / "pkg" / "mod.py").write_text(_TEXT, encoding="utf-8")
    scratch_root.mkdir()

    calls: list[Path] = []

    def recorder(argv, *, env, cwd, timeout):
        calls.append(Path(cwd))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    baseline = execute_command(lane, cwd=project_root, process_runner=recorder)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=1,
        operators=("compare-swap",),
        process_runner=recorder,
    )

    assert mutation is not None
    assert mutation.total == 1
    assert mutation.killed == 0 and len(mutation.survived) == 1
    survivor = mutation.survived[0]
    assert survivor.operator == "compare-swap"
    # the baseline call PLUS exactly ONE mutant call -- if the
    # bool-const-flip mutant were ALSO submitted, this would be 3.
    assert len(calls) == 2
