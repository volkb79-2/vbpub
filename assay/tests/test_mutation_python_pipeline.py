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
import sys
from pathlib import Path
from types import MappingProxyType

from conftest import PROJECT_ROOT, make_lane

from assay.adapters.python import PythonAdapter
from assay.config import Lane
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationTarget, build_mutation_claim, run_mutation
from assay.runner import execute_command

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "mutation_exec" / "python"
assert (FIXTURE_DIR / "pkg" / "checks.py").is_file(), (
    f"expected the committed P12 mutation-execution fixture at {FIXTURE_DIR}"
)

_TARGET_PATH = "pkg/checks.py"
_KILLED_LINE = 23  # `if age >= 18:` in is_adult
_SURVIVED_LINE = 30  # `if code == 200:` in is_valid_status


def _lane() -> Lane:
    return make_lane(
        argv=(sys.executable, "-m", "pytest", "tests", "-q"),
        env=MappingProxyType({}),
        env_passthrough=("PATH",),
        budget="2m",
        budget_seconds=120.0,
    )


def test_a_real_pytest_run_produces_a_genuine_killed_and_a_genuine_survived_mutant(
    tmp_path: Path,
):
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    shutil.copytree(FIXTURE_DIR, project_root)
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
    baseline = execute_command(lane, cwd=project_root)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=targets,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("compare-swap",),
    )

    assert baseline.outcome is Outcome.PASS, baseline
    assert mutation is not None
    assert mutation.total == 2
    assert mutation.killed == 1
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
    # only the disposable tmp_path COPY (`project_root`) and its own
    # per-mutant scratch copies are ever written to.
    assert (FIXTURE_DIR / "pkg" / "checks.py").read_text(encoding="utf-8") == original_text
    # nor is the tmp_path project copy itself mutated in place.
    assert (project_root / "pkg" / "checks.py").read_text(encoding="utf-8") == original_text
    assert list(scratch_root.iterdir()) == []


def test_a_real_broken_baseline_stops_before_any_real_mutant_run(tmp_path: Path):
    """O1's own negative, proven against a REAL subprocess this time: a
    genuinely red baseline (a real assertion failure in the committed
    fixture, introduced here) stops before any mutant is even generated."""
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    shutil.copytree(FIXTURE_DIR, project_root)
    scratch_root.mkdir()
    # break the fixture's own control test for real.
    broken_test = (
        "from pkg.checks import is_adult\n\n\n"
        "def test_is_adult_at_the_boundary():\n"
        "    assert is_adult(18) is False  # deliberately wrong\n"
    )
    (project_root / "tests" / "test_checks.py").write_text(broken_test, encoding="utf-8")

    original_text = (project_root / "pkg" / "checks.py").read_text(encoding="utf-8")
    targets = (
        MutationTarget(
            path=_TARGET_PATH, text=original_text, lines=frozenset({_KILLED_LINE})
        ),
    )

    lane = _lane()
    baseline = execute_command(lane, cwd=project_root)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=targets,
        adapter=PythonAdapter(),
        jobs=2,
        operators=("compare-swap",),
    )

    assert baseline.outcome is Outcome.FAIL
    assert baseline.reason_code is ReasonCode.COMMAND_FAILED
    assert mutation is None
    assert list(scratch_root.iterdir()) == []
