"""The declared operator policy is applied AT DISCOVERY, not after it
(P21, A-180).

P18 filtered a collected job list with a private ``_filter_by_operators``.
That is deleted: filtering after collection means the adapter has already
built candidates the lane never selected, so ``max_mutants`` would be
counting -- and bounding work against -- sites that can never run. The
selection is now an INPUT to the adapter, and this module's claim is that
an undeclared operator produces no candidate at any stage.

The end-to-end assertion is still a process-boundary call count, not a
result count: a filter that ran the mutant and discarded the result would
satisfy a result-only assertion while doubling the lane's real work.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationTarget, collect_mutation_sites, run_mutation
from assay.runner import execute_command

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

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


def _collect(operators: tuple[str, ...], limit: int = 10):
    return collect_mutation_sites(
        _TARGETS, adapter=PythonAdapter(), operators=operators, limit=limit
    )


def test_the_fixture_itself_offers_both_operator_types():
    """The fixture's own precondition, checked directly: with BOTH operators
    selected, two real distinct sites exist -- if this failed, every
    assertion below would be proving nothing."""
    jobs = _collect(("compare-swap", "bool-const-flip"))

    assert {job.site.operator for job in jobs} == {"compare-swap", "bool-const-flip"}


def test_an_undeclared_operator_yields_no_candidate_at_all():
    """Not "collected then dropped" -- never collected. This is what makes
    the candidate count a count of work the lane actually authorised."""
    jobs = _collect(("compare-swap",))

    assert [job.site.operator for job in jobs] == ["compare-swap"]


def test_selecting_the_other_operator_selects_the_other_site():
    """The mirror case, so the test above cannot pass by an implementation
    that simply always returns the first site."""
    jobs = _collect(("bool-const-flip",))

    assert [job.site.operator for job in jobs] == ["bool-const-flip"]


def test_an_operator_the_file_has_no_site_for_yields_nothing():
    """A supported analysis that observed no eligible candidate -- distinct
    from an adapter with no engine, which cannot happen for Python."""
    assert _collect(("falsy-swap",)) == ()


def test_the_selection_is_applied_before_the_cap_not_after(tmp_path: Path):
    """A-180's real point: with a cap of 1 and only `bool-const-flip`
    selected, the retained candidate is the bool-const-flip site -- NOT the
    compare-swap site that sorts earlier by byte offset. A collector that
    gathered everything, capped, and then filtered would retain the
    compare-swap site and report zero candidates."""
    jobs = _collect(("bool-const-flip",), limit=1)

    assert [job.site.operator for job in jobs] == ["bool-const-flip"]


def test_run_mutation_never_submits_an_undeclared_operators_mutant(tmp_path: Path):
    """The end-to-end proof at the public surface: the ``bool-const-flip``
    site is never EXECUTED. A call-count check at the process boundary, so
    a filter that ran the mutant and discarded its result would still fail
    here."""
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/mod.py", _TEXT)
    repo.commit_all("add pkg")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    lane = make_lane(argv=("pytest", "-q"))

    submitted: list[str] = []

    def _completed(returncode: int):
        return subprocess.CompletedProcess(args=["pytest"], returncode=returncode)

    def mutant_runner(argv, *, cwd, env, timeout):
        submitted.append(
            (Path(cwd) / "pkg" / "mod.py").read_text(encoding="utf-8")
        )
        return _completed(1)  # the suite caught it -> killed

    baseline = execute_command(
        lane,
        cwd=repo.path,
        process_runner=lambda *a, **k: _completed(0),
    )
    plan = make_plan(lane)
    deadline = make_deadline()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("compare-swap",),
            process_runner=mutant_runner,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert result.candidate_count == 1
    assert result.total == 1
    assert [outcome.operator for outcome in result.killed] == ["compare-swap"]
    # Exactly one mutant command ran, and the file it ran against carries the
    # compare-swap splice -- never the `True` -> `False` one.
    assert len(submitted) == 1
    assert "x >= 0" in submitted[0]
    assert "y = True" in submitted[0]
