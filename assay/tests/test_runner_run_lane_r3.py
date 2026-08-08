"""``runner.run_lane``'s R3 orchestration (P19): an isolated canary proof
delegated whole to :func:`assay.canary.run_isolated_python_canary` -- the
consumer's own repository is copied into independently-owned scratch state
and never staged, committed, or written to.

Real git state materialised under ``tmp_path`` (the established P02/P17/P18
pattern) and the REAL :class:`~assay.adapters.python.PythonAdapter` plus a
genuine ``pytest`` subprocess. ``tests/test_canary_python_pipeline.py``
already proves per-mechanism cause sensitivity against
:func:`~assay.canary.run_python_canary` directly and in full; this module
proves the NEW P19 orchestration layered on top of it: the scratch copy
and its ``.git``, :func:`~assay.canary._project_prefix` respelling for a
project in a subdirectory of its repo (A-145), the two prerequisite
refusals, and ``run_lane``'s own claims/judgment/ended wiring.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from conftest import GitRepo, fixed_clock, make_lane, make_r3_judge

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import CanaryConfig, CoverageConfig, JudgeConfig, MutationConfig
from assay.errors import Outcome, ReasonCode

MOMENT_A = datetime(2026, 8, 8, 11, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 8, 8, 11, 0, 1, tzinfo=timezone.utc)
MOMENT_C = datetime(2026, 8, 8, 11, 0, 2, tzinfo=timezone.utc)
MOMENT_D = datetime(2026, 8, 8, 11, 0, 3, tzinfo=timezone.utc)
MOMENT_E = datetime(2026, 8, 8, 11, 0, 4, tzinfo=timezone.utc)
MOMENT_F = datetime(2026, 8, 8, 11, 0, 5, tzinfo=timezone.utc)
MOMENT_G = datetime(2026, 8, 8, 11, 0, 6, tzinfo=timezone.utc)

_ENV = {"PYTHONDONTWRITEBYTECODE": "1"}


def _seed_pytest_package(repo: GitRepo, *, root: str = "") -> None:
    prefix = f"{root}/" if root else ""
    repo.write(f"{prefix}pkg/__init__.py", "")
    repo.write(f"{prefix}pkg/mod.py", "def f():\n    return 1\n")
    repo.write(
        f"{prefix}tests/test_mod.py",
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    )
    repo.commit_all("add pkg")


# --- a real, isolated R3 pass --------------------------------------------


def test_r3_alone_proves_the_declared_canary_through_run_lane(git_repo: GitRepo):
    _seed_pytest_package(git_repo)
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(
        rigor=("R0", "R3"),
        judge=judge,
        argv=(sys.executable, "-m", "pytest", "tests", "-q"),
        env=_ENV,
        env_passthrough=("PATH",),
    )
    head_before = git_repo.head()

    verdict = runner.run_lane(
        lane,
        commit=head_before,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    assert [c.rigor for c in verdict.claims] == ["R0", "R3"]
    r3_claim = verdict.claims[1]
    assert r3_claim.status is Outcome.PASS
    assert r3_claim.canary is not None
    assert r3_claim.canary.mechanism == "import-break"
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL
    assert r3_claim.canary.observed_reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.judgment is not None
    assert verdict.judgment.r3 is not None
    assert verdict.judgment.r3.mechanism == "import-break"
    assert verdict.judgment.r3.target == "pkg/mod.py"
    assert verdict.judgment.r1 is None
    assert verdict.judgment.r2 is None
    # O2: the consumer's own repository is untouched by any of it.
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_r3_reports_canary_survived_when_the_transform_is_never_actually_caught(
    git_repo: GitRepo,
):
    """The command that decides pass/fail never inspects the target file at
    all, so BOTH halves genuinely PASS -- the transform is never caught,
    which is exactly what CANARY_SURVIVED reports (never a silent PASS)."""
    _seed_pytest_package(git_repo)
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="uncovered-line", target="pkg/mod.py"),
    )
    lane = make_lane(
        rigor=("R0", "R3"), judge=judge, argv=("/bin/sh", "-c", "exit 0")
    )

    verdict = runner.run_lane(
        lane,
        commit="a" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.FAIL
    r3_claim = verdict.claims[1]
    assert r3_claim.status is Outcome.FAIL
    assert r3_claim.reason_code is ReasonCode.CANARY_SURVIVED
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.PASS
    assert verdict.judgment.r3 == runner.JudgmentR3(
        mechanism="uncovered-line", target="pkg/mod.py"
    )


# --- the two prerequisite refusals become payload-free claims ----------------


def test_r3_refuses_a_test_path_target_as_a_payload_free_claim(git_repo: GitRepo):
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/test_helper.py", "def f():\n    return 1\n")
    git_repo.commit_all("add pkg")
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/test_helper.py"),
    )
    lane = make_lane(rigor=("R0", "R3"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit="b" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    r3_claim = verdict.claims[1]
    assert r3_claim.status is Outcome.ERROR
    assert r3_claim.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert r3_claim.canary is None
    assert verdict.judgment is None
    # The consumer's own repository is untouched: the refusal is a
    # prerequisite check, before anything is even copied.
    assert git_repo.git("status", "--porcelain") == ""


def test_r3_refuses_on_a_dirty_tree_the_commands_own_side_effects_created(
    git_repo: GitRepo,
):
    """The identical shape R2's own review already established one rigor
    level over (``test_r2_without_r1_refuses_on_dirty_tree_the_command_
    itself_created``): R0's own command runs BEFORE R3's own guard, so an
    untracked file it leaves behind under a declared source root is what
    trips this -- not anything present before the run started."""
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f():\n    return 1\n")
    git_repo.commit_all("add pkg")
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(
        rigor=("R0", "R3"),
        judge=judge,
        argv=("/bin/sh", "-c", "touch pkg/leftover.pyc"),
    )

    verdict = runner.run_lane(
        lane,
        commit="c" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    r3_claim = verdict.claims[1]
    assert r3_claim.status is Outcome.NO_MEASUREMENT
    assert r3_claim.reason_code is ReasonCode.DIRTY_TREE
    assert r3_claim.canary is None
    assert verdict.judgment is None


# --- the project is a SUBDIRECTORY of its repository (A-145) -----------------


def test_r3_proves_a_canary_for_a_project_in_a_subdirectory_of_its_repo(
    git_repo: GitRepo,
):
    """A-145, applied to R3: the project lives in ``sub/``, a SUBDIRECTORY
    of its repository -- assay's own layout inside ``vbpub``.
    ``judge.canary.target`` is declared PROJECT-relative (``pkg/mod.py``),
    never repo-relative (``sub/pkg/mod.py``) -- proving the scratch copy's
    own :func:`assay.canary._project_prefix` respelling locates it
    correctly relative to ``sub/``, not relative to the repo top."""
    project_root = git_repo.path / "sub"
    _seed_pytest_package(git_repo, root="sub")
    judge = make_r3_judge(
        source_root_paths=(project_root / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(
        rigor=("R0", "R3"),
        judge=judge,
        argv=(sys.executable, "-m", "pytest", "tests", "-q"),
        env=_ENV,
        env_passthrough=("PATH",),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=project_root,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    r3_claim = verdict.claims[1]
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL
    assert git_repo.git("status", "--porcelain") == ""


# --- ended covers R3's own completion -----------------------------------------


def test_ended_covers_r3s_own_completion_not_only_r0s(git_repo: GitRepo):
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f():\n    return 1\n")
    git_repo.commit_all("add pkg")
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(rigor=("R0", "R3"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit="d" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        # R0 (started/ended) + the control run (started/ended) + the
        # transformed run (started/ended) + run_lane's own final `ended`
        # read -- seven clock() calls total, none of which
        # `run_isolated_python_canary`/`run_python_canary` add beyond
        # `execute_command`'s own two per invocation (evaluate_r1 -- unused
        # here, R1 is not declared -- takes no clock at all).
        clock=fixed_clock(
            MOMENT_A, MOMENT_B, MOMENT_C, MOMENT_D, MOMENT_E, MOMENT_F, MOMENT_G
        ),
    )

    assert verdict.started == "2026-08-08T11:00:00+00:00", "R0's own start, unchanged"
    assert verdict.ended == "2026-08-08T11:00:06+00:00", "covers R3's own completion"


# --- R1, R2 and R3 declared together: no interference between levels --------


def test_r1_r2_and_r3_together_each_render_their_own_independent_claim(
    git_repo: GitRepo,
):
    """R3 never reuses R0's own :class:`~assay.runner.CommandResult` the way
    R2 does (it cannot: its own control/transform runs happen in an
    isolated copy) -- proving R1 and R2's own established behaviour is
    unaffected by R3 being declared alongside them."""
    # cov.json must be git-ignored (A-140): R3's own whole-tree dirty check
    # (unscoped, like `run_lane`'s own pre-execution one -- unlike R2's
    # post-execution check, which is scoped to `judge.source_root_paths`)
    # would otherwise see R1's own freshly-written artifact as pollution.
    git_repo.write(".gitignore", "cov.json\n.coverage\n")
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "tests/test_mod.py", "from pkg.mod import f\n\n\ndef test_f():\n    assert f(1)\n"
    )
    git_repo.commit_all("add test")

    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(git_repo.path / "pkg",),
        fail_under=0.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=MutationConfig(jobs=1, operators=("compare-swap",)),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
        base=base_rev,
    )
    lane = make_lane(
        rigor=("R0", "R1", "R2", "R3"),
        judge=judge,
        argv=(
            sys.executable, "-m", "pytest", "tests", "-q",
            "--cov=pkg", "--cov-report=json:cov.json",
        ),
        env=_ENV,
        env_passthrough=("PATH",),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert [c.rigor for c in verdict.claims] == ["R0", "R1", "R2", "R3"]
    r1_claim, r2_claim, r3_claim = verdict.claims[1], verdict.claims[2], verdict.claims[3]
    assert r1_claim.coverage is not None
    assert r2_claim.mutation is not None
    assert r3_claim.canary is not None
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL
    assert verdict.judgment.r1 is not None
    assert verdict.judgment.r2 is not None
    assert verdict.judgment.r3 is not None
