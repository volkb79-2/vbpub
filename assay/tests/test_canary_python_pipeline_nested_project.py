"""B006's own regression proof, changed_lines mode -- the automated,
suite-resident counterpart of WI-5's real-world finding
(``nyxloom-trove/reports/W1-WI5-CMRU-qualification.md``) and of
``e2e_nested.sh``'s own whole-target differential.

``test_canary_python_pipeline.py`` already proves the real, subprocess-driven
R0+R1 canary pipeline (:func:`assay.canary.run_python_canary`) for a
ROOT-level project (``project_root == repo``). This module runs the
IDENTICAL fixture, lane, and mechanism through the IDENTICAL pipeline for a
project whose ``project_root`` sits ONE level down in its own repository --
exactly CMRU's own shape, and exactly what B006 exists to serve. The only
variable between the two tests below is *where* ``project_root`` sits
relative to ``repo``; everything else (fixture bytes, lane declaration,
canary mechanism, coverage command) is identical, mirroring the root-vs-
nested differential ``e2e_nested.sh`` already uses for ``whole_target`` mode.

**Why this is a real reproduction, not a re-confirmation of a fabricated
fixture.** :func:`assay.canary.run_python_canary` shells out to a genuine
``python -m pytest --cov=pkg --cov-report=json:cov.json`` subprocess
(:func:`assay.runner.default_process_runner`) with ``cwd=project_root`` --
the coverage artifact's own keys are whatever real ``coverage.py`` writes,
never hand-authored. Before B006's fix (``evaluate.py``'s
``_normalized_profile_files`` gaining a ``project_prefix`` join), the nested
test below failed even its CONTROL half: ``control_outcome`` came back
``FAIL``/``UNCOVERED_LINES`` on genuinely fully-covered code, because the
real subprocess's project-relative coverage key (``"pkg/greet.py"``) was
never reconciled against ``git diff``'s repo-relative one
(``"proj/pkg/greet.py"``) -- WI-5's own real finding, reproduced here
independently of that report's own heavier CMRU/tester-unified harness.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import MappingProxyType

from conftest import PROJECT_ROOT, GitRepo

from assay import canary
from assay.adapters.python import PythonAdapter
from assay.config import CoverageConfig, IsolationConfig, JudgeConfig, Lane
from assay.errors import Outcome, ReasonCode

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "canary" / "python"
assert (FIXTURE_DIR / "pkg" / "greet.py").is_file(), (
    f"expected the committed Python canary fixture at {FIXTURE_DIR}"
)

TARGET_PATH = "pkg/greet.py"

#: Same rationale as `test_canary_python_pipeline.py`'s own `_ENV`: pytest
#: writes .pyc caches under pkg/__pycache__ unless told not to, which would
#: otherwise be untracked dirt UNDER the declared source root.
_ENV = MappingProxyType({"PYTHONDONTWRITEBYTECODE": "1"})


def _materialize_control(repo: GitRepo, *, under: str) -> str:
    """Copy the committed fixture into *repo*, under *under* (``"."`` for
    the root-level control, ``"proj"`` for the nested subject), and commit
    it as the KNOWN-GOOD control. Returns the ANCESTOR commit -- the same
    shape `test_canary_python_pipeline.py`'s own helper of this name uses,
    parameterised only by WHERE the fixture lands."""
    base_commit = repo.head()
    dest = repo.path if under == "." else repo.path / under
    shutil.copytree(FIXTURE_DIR / "pkg", dest / "pkg")
    shutil.copytree(FIXTURE_DIR / "tests", dest / "tests")
    repo.commit_all(f"add control fixture under {under!r}")
    return base_commit


def _lane(project_root: Path, rigor: tuple[str, ...]) -> Lane:
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(project_root / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=None,
        canary=None,
        base="main",
    )
    isolation = IsolationConfig(
        snapshot_selection="repository", unsafe_symlink_omissions=()
    )
    return Lane(
        name="package",
        scope="S1",
        rigor=rigor,
        enforcement="gate",
        argv=(
            sys.executable, "-m", "pytest", "tests", "-q",
            "--cov=pkg", "--cov-report=json:cov.json",
        ),
        env=_ENV,
        env_passthrough=("PATH",),
        budget="2m",
        budget_seconds=120.0,
        allow_argv_append=False,
        judge=judge,
        where=None,
        isolation=isolation,
    )


def _run_uncovered_line_canary(repo: GitRepo, *, under: str) -> canary.CanaryResult:
    """The real R0+R1 pipeline, both canary halves, for a fixture rooted at
    *under* -- ``"."`` (repo root) or ``"proj"`` (nested one level down)."""
    project_root = repo.path if under == "." else repo.path / under
    base_commit = _materialize_control(repo, under=under)
    lane = _lane(project_root, ("R0", "R1"))
    return canary.run_python_canary(
        lane,
        repo=repo.path,
        project_root=project_root,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )


def test_a_root_level_project_passes_the_real_uncovered_line_canary(git_repo: GitRepo):
    """The must-succeed CONTROL for the differential below: identical
    fixture, identical lane, identical mechanism, ``project_root == repo``
    -- the shape `test_canary_python_pipeline.py`'s own
    ``test_uncovered_line_control_passes_and_the_real_transform_fails_
    uncovered_lines`` already proves; restated here, in the SAME module as
    the nested subject, so both halves of the differential run side by side
    under one shared helper rather than being spread across two files that
    could silently drift apart."""
    result = _run_uncovered_line_canary(git_repo, under=".")
    assert result.control_outcome is Outcome.PASS
    assert result.transformed_outcome is Outcome.FAIL
    assert result.expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert result.observed_reason_code is ReasonCode.UNCOVERED_LINES


def test_a_nested_project_passes_the_real_uncovered_line_canary_identically(
    git_repo: GitRepo,
):
    """B006's own shape (CMRU: ``assay.toml`` a subdirectory of its own
    repository) -- same fixture, same lane, same mechanism as the control
    above, ``project_root`` one level down at ``proj/``. Before B006's fix
    this failed even the CONTROL half (a real ``FAIL``/``UNCOVERED_LINES``
    on genuinely 100%-covered code), because the real ``pytest --cov``
    subprocess (``cwd=project_root``) writes a project-relative coverage key
    (``"pkg/greet.py"``) that ``evaluate_coverage`` compared directly
    against ``git diff``'s repo-relative one (``"proj/pkg/greet.py"``) and
    never matched."""
    result = _run_uncovered_line_canary(git_repo, under="proj")
    assert result.control_outcome is Outcome.PASS
    assert result.transformed_outcome is Outcome.FAIL
    assert result.expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert result.observed_reason_code is ReasonCode.UNCOVERED_LINES
