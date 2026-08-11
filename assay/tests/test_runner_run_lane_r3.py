"""``runner.run_lane``'s R3 orchestration: an isolated canary proof
delegated whole to :func:`assay.canary.run_isolated_canary` -- the consumer's
own repository is never staged, committed, read from, or written to.

Real git state materialised under ``tmp_path`` (the established P02/P17/P18
pattern) and the REAL :class:`~assay.adapters.python.PythonAdapter` plus a
genuine ``pytest`` subprocess. ``tests/test_canary_python_pipeline.py``
already proves per-mechanism cause sensitivity against
:func:`~assay.canary.run_python_canary` directly and in full; this module
proves the orchestration layered on top of it.

**P23**: the consumer's repository is no longer copied at all --
``run_isolated_canary`` now materialises both the control and the
transformed half from two INDEPENDENT P22 committed snapshots of the SAME
prepared seed :func:`~assay.runner.run_lane`'s own baseline already used, so
this module proves the real P22-composed orchestration: two independent
snapshots, a project in a subdirectory of its repo (A-145) via
``prepared.spec.project_prefix``, the one remaining prerequisite refusal, and
``run_lane``'s own claims/judgment/ended wiring.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

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
    which is exactly what CANARY_SURVIVED reports (never a silent PASS).

    P23/A-192: ``uncovered-line`` now requires R1 declared alongside R3 at
    LOAD time (a real ``assay.toml`` would refuse the old ``rigor=("R0",
    "R3")`` shape outright), so this proof now uses ``import-break``
    instead -- the identical "the command never inspects the file, so
    nothing is ever caught" property, for the ONE mechanism that genuinely
    does not require R1.
    """
    _seed_pytest_package(git_repo)
    judge = make_r3_judge(
        source_root_paths=(git_repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(
        rigor=("R0", "R3"), judge=judge, argv=("/bin/sh", "-c", "exit 0")
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
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
        mechanism="import-break", target="pkg/mod.py"
    )


# --- the SECOND mechanism, proved for its OWN reason (O3, A-149) ------------


def _seed_covered_package(git_repo: GitRepo) -> str:
    """A real two-commit history whose HEAD commit ADDS ``pkg/mod.py`` and
    whose tests genuinely cover every line of it, with ``cov.json``
    git-ignored (A-140). Returns the base revision -- so ``base..HEAD``
    really has changed source lines to measure, in the copy as well as in
    the consumer."""
    git_repo.write(".gitignore", "cov.json\n.coverage\n")
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("tests/__init__.py", "")
    base_rev = git_repo.commit_all("add package skeleton")
    git_repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.write(
        "tests/test_mod.py",
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f(1)\n",
    )
    git_repo.commit_all("add mod.py and its test")
    return base_rev


_COV_ARGV = (
    sys.executable, "-m", "pytest", "tests", "-q",
    "--cov=pkg", "--cov-report=json:cov.json",
)


def _r1_r3_judge(
    git_repo: GitRepo, *, base_rev: str, mechanism: str, target: str
) -> JudgeConfig:
    return JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(git_repo.path / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=None,
        canary=CanaryConfig(mechanism=mechanism, target=target),
        base=base_rev,
    )


def test_r3_proves_the_uncovered_line_canary_for_its_own_reason_when_r1_is_declared(
    git_repo: GitRepo,
):
    """O3's second half, and the ONE configuration in which it is reachable
    at all: ``uncovered-line``'s expected reason is ``UNCOVERED_LINES``,
    which only R1 can ever produce -- so the mechanism can only be PROVED
    by a lane that declares R1 alongside R3. Every other R3 test in this
    module declares R0+R3 alone, where the canary's own transform can only
    ever be reported as having SURVIVED.

    This is also A-149's regression oracle. ``judge.source_root_paths`` are
    absolute and rooted at the CONSUMER's project; the run happens in a
    scratch COPY. Hand the copy the consumer's own roots and every changed
    file falls outside every root, ``considered`` is 0, and R1 PASSes
    having measured nothing -- so the transformed half PASSes too and this
    same canary reports ``CANARY_SURVIVED``, vacuously, forever.
    """
    base_rev = _seed_covered_package(git_repo)
    lane = make_lane(
        rigor=("R0", "R1", "R3"),
        judge=_r1_r3_judge(
            git_repo, base_rev=base_rev, mechanism="uncovered-line", target="pkg/mod.py"
        ),
        argv=_COV_ARGV,
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

    r3_claim = verdict.claims[-1]
    assert r3_claim.status is Outcome.PASS
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL
    assert r3_claim.canary.expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert r3_claim.canary.observed_reason_code is ReasonCode.UNCOVERED_LINES
    # The lane's R1 half really measures this history (one considered
    # file, fully covered) -- so the copy's own control half, running the
    # identical evaluation against the identical bytes, is not being
    # graded 0/0 either. Under A-149 both halves scored a vacuous 0/0
    # PASS and the assertions above read CANARY_SURVIVED instead.
    assert verdict.claims[1].coverage.considered == 1
    assert verdict.claims[1].coverage.changed_executable == 2
    assert git_repo.git("status", "--porcelain") == ""


def test_r3_reports_a_real_wrong_cause_as_survived_with_the_unmocked_adapter(
    git_repo: GitRepo,
):
    """A transformed half that genuinely FAILS, for a genuinely DIFFERENT
    reason than the mechanism declares -- with the real
    :class:`~assay.adapters.python.PythonAdapter`, no mislabeled subclass.

    ``import-break`` expects ``COMMAND_FAILED``. Break a module the lane's
    own tests never import and R0 keeps passing; R1 then catches the
    injected line as uncovered, so the observed reason is
    ``UNCOVERED_LINES``. Accepting any transformed non-PASS (O3's own
    negative) makes this green.
    """
    git_repo.write(".gitignore", "cov.json\n.coverage\n")
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.write("pkg/other.py", "def g():\n    return 2\n")
    base_rev = git_repo.commit_all("add pkg")
    git_repo.write(
        "tests/test_other.py",
        "from pkg.other import g\n\n\ndef test_g():\n    assert g() == 2\n",
    )
    git_repo.commit_all("add test")
    lane = make_lane(
        rigor=("R0", "R1", "R3"),
        judge=_r1_r3_judge(
            git_repo, base_rev=base_rev, mechanism="import-break", target="pkg/mod.py"
        ),
        argv=_COV_ARGV,
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

    r3_claim = verdict.claims[-1]
    assert r3_claim.status is Outcome.FAIL
    assert r3_claim.reason_code is ReasonCode.CANARY_SURVIVED
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL, "it DID fail"
    assert r3_claim.canary.expected_reason_code is ReasonCode.COMMAND_FAILED
    assert r3_claim.canary.observed_reason_code is ReasonCode.UNCOVERED_LINES
    assert git_repo.git("status", "--porcelain") == ""


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
        commit=git_repo.head(),
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
    """R0's own command runs before any R3 work, so an untracked file it
    leaves behind is what trips this -- not anything present before the run
    started.

    Reviewer note (P20/A-175): the guard that now answers this is
    ``run_lane``'s own post-command WHOLE-REPOSITORY check, which refuses
    before ``run_isolated_canary`` is ever entered; previously the same
    assertions were satisfied one layer down, by the canary's own dirty-tree
    refusal. The observable contract asserted here is unchanged, but it is no
    longer an oracle for that inner guard -- which is why
    ``test_run_isolated_canary_refuses_a_dirty_repository_directly`` below
    now covers it directly. Left in place because the outer refusal is a real
    contract in its own right: dirt anywhere, not merely under a source root,
    must stop R3 before it starts."""
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
        commit=git_repo.head(),
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
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        # R0 (started/ended) + the control run (started/ended) + the
        # transformed run (started/ended) + run_lane's own final `ended`
        # read -- seven clock() calls total, none of which
        # `run_isolated_canary`/`run_python_canary` add beyond
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
        mutation=MutationConfig(jobs=1, max_mutants=50, operators=("python:compare-swap",)),
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
