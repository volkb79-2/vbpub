"""A-041/A-067 -- the FULL real pipeline: a genuine ``pytest`` subprocess
run (via the REAL :func:`~assay.runner.default_process_runner`, the REAL
default ``ThreadPoolExecutor``, and the REAL
:class:`~assay.adapters.python.PythonAdapter`) against
``tests/fixtures/mutation_exec/python/`` -- assay's own committed real
pytest project, staging BOTH a genuinely well-tested line (its mutant is
genuinely KILLED) and a genuinely hollow-tested line (its mutant genuinely
SURVIVES). Neither is faked or mocked: this is the module every earlier,
fake-``process_runner``-based test in this package's suite exists to
complement, not replace (P11's own ``tests/fixtures/mutation/python/
sample.py`` does not stage for controllable kills/survivals, per its own
successor brief -- this fixture does).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from conftest import GitRepo, PROJECT_ROOT, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.config import Lane
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationTarget, build_mutation_claim, run_mutation
from assay.runner import default_process_runner, execute_command


FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "mutation_exec" / "python"
assert (FIXTURE_DIR / "pkg" / "checks.py").is_file(), (
    f"expected the committed P12 mutation-execution fixture at {FIXTURE_DIR}"
)

_TARGET_PATH = "pkg/checks.py"
_KILLED_LINE = 23  # `if age >= 18:` in is_adult
_SURVIVED_LINE = 30  # `if code == 200:` in is_valid_status


def _git_repo_from_fixture(tmp_path: Path, name: str = "repo") -> GitRepo:
    """A real, committed git repository seeded from the committed P12
    fixture (:data:`FIXTURE_DIR`) -- P23's snapshot substrate needs a real
    commit, never a bare directory copy."""
    repo = GitRepo(path=tmp_path / name)
    shutil.copytree(FIXTURE_DIR, repo.path)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.commit_all("seed from fixture")
    return repo


def _lane() -> Lane:
    return make_lane(
        argv=(sys.executable, "-m", "pytest", "tests", "-q"),
        # P23: every unit now runs inside a real, git-tracked snapshot, so a
        # `.pyc` byte-compiled cache written under a source directory is
        # real, checked Git-visible dirt (unlike the old shutil.copytree
        # scratch dir, which was never git-tracked at all) -- the same
        # `PYTHONDONTWRITEBYTECODE` convention `test_runner_run_lane_r3.py`
        # already established for exactly this reason.
        env=MappingProxyType({"PYTHONDONTWRITEBYTECODE": "1"}),
        env_passthrough=("PATH",),
        budget="2m",
        budget_seconds=120.0,
    )


def test_a_real_pytest_run_produces_a_genuine_killed_and_a_genuine_survived_mutant(
    tmp_path: Path,
):
    repo = _git_repo_from_fixture(tmp_path)
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    original_text = (FIXTURE_DIR / "pkg" / "checks.py").read_text(encoding="utf-8")
    targets = (
        MutationTarget(
            path=_TARGET_PATH,
            text=original_text,
            lines=frozenset({_KILLED_LINE, _SURVIVED_LINE}),
        ),
    )

    lane = _lane()
    baseline = execute_command(lane, cwd=repo.path)
    plan = make_plan(lane)
    deadline = make_deadline()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=targets,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=("compare-swap",),
            process_runner=default_process_runner,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert baseline.outcome is Outcome.PASS, baseline
    assert mutation is not None
    assert mutation.total == 2
    assert len(mutation.killed) == 1
    assert len(mutation.survived) == 1
    assert mutation.crashed == ()
    assert mutation.budget_exceeded == ()

    survivor = mutation.survived[0]
    assert survivor.path == _TARGET_PATH
    assert survivor.lineno == _SURVIVED_LINE
    assert survivor.operator == "compare-swap"

    claim = build_mutation_claim(baseline, mutation)
    assert claim.rigor == "R2"
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.MUTANTS_SURVIVED
    assert claim.mutation is mutation

    # the COMMITTED fixture's own source is never touched by any of this --
    # only the disposable tmp_path git repo COPY and its own per-mutant
    # snapshots are ever written to.
    assert (FIXTURE_DIR / "pkg" / "checks.py").read_text(encoding="utf-8") == original_text
    # nor is the tmp_path repo itself mutated in place.
    assert (repo.path / "pkg" / "checks.py").read_text(encoding="utf-8") == original_text
    assert repo.git("status", "--porcelain") == ""


def test_a_real_broken_baseline_stops_before_any_real_mutant_run(tmp_path: Path):
    """O1's own negative, proven against a REAL subprocess this time: a
    genuinely red baseline (a real assertion failure in the committed
    fixture, introduced here) stops before any mutant is even generated."""
    repo = GitRepo(path=tmp_path / "repo")
    shutil.copytree(FIXTURE_DIR, repo.path)
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    # break the fixture's own control test for real, BEFORE the first commit
    # -- the baseline this test proves stops before any mutant must itself
    # be a real, committed, genuinely red state.
    broken_test = (
        "from pkg.checks import is_adult\n\n\n"
        "def test_is_adult_at_the_boundary():\n"
        "    assert is_adult(18) is False  # deliberately wrong\n"
    )
    (repo.path / "tests" / "test_checks.py").write_text(broken_test, encoding="utf-8")
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.commit_all("seed with a broken control test")

    original_text = (repo.path / "pkg" / "checks.py").read_text(encoding="utf-8")
    targets = (
        MutationTarget(
            path=_TARGET_PATH, text=original_text, lines=frozenset({_KILLED_LINE})
        ),
    )

    lane = _lane()
    baseline = execute_command(lane, cwd=repo.path)
    mutation = run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=targets,
        adapter=PythonAdapter(),
        jobs=2,
        max_mutants=50,
        operators=("compare-swap",),
        process_runner=default_process_runner,
        clock=lambda: datetime.now(timezone.utc),
    )

    assert baseline.outcome is Outcome.FAIL
    assert baseline.reason_code is ReasonCode.COMMAND_FAILED
    assert mutation is None
    assert list(scratch_root.iterdir()) == []
