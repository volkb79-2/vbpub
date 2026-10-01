"""O1/A-107 -- Python's FULL real-pipeline cause-sensitive canary: a genuine
``pytest`` subprocess run (R0, via :func:`~assay.runner.execute_command`)
plus, when it passes, the real :func:`~assay.runner.evaluate_r1` -- both
halves (control, then transformed) run through the identical
:func:`assay.canary._run_pipeline` engine, against a real, disposable git
repository materialised under ``tmp_path`` (the established P02 pattern),
seeded from the COMMITTED Python canary fixture
(``tests/fixtures/canary/python/pkg/greet.py`` +
``tests/test_greet.py``) -- never a hand-rolled fake pipeline.

No mocked subprocess anywhere in this module: AUTHORING.md §3b.A's
"no wall-clock wait decides pass/fail" is honoured because nothing here
waits on TIME -- every assertion is on the real, already-completed process's
outcome, and a generous real budget (2 minutes) is a failsafe against a
genuinely hung process, never the thing that decides the result.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import MappingProxyType

from conftest import PROJECT_ROOT, GitRepo, why_invalid
from jsonschema import Draft202012Validator

from assay import canary
from assay.adapters.python import PythonAdapter
from assay.config import CoverageConfig, IsolationConfig, JudgeConfig, Lane
from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    Judgment,
    JudgmentR3,
    JudgmentResolved,
    SnapshotPolicy,
    Verdict,
)

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "canary" / "python"
assert (FIXTURE_DIR / "pkg" / "greet.py").is_file(), (
    f"expected the committed Python canary fixture at {FIXTURE_DIR}"
)

TARGET_PATH = "pkg/greet.py"

#: pytest writes .pyc caches under pkg/__pycache__ unless told not to --
#: those are untracked files UNDER the declared source root, which would
#: otherwise trip check_dirty_tree's own guard between the control commit
#: and the R1 diff it measures.
_ENV = MappingProxyType({"PYTHONDONTWRITEBYTECODE": "1"})


def _materialize_control(repo: GitRepo) -> str:
    """Copy the committed fixture into *repo* and commit it as the
    KNOWN-GOOD control. Returns the ANCESTOR commit (repo's HEAD before the
    fixture was added) -- :func:`~assay.canary.run_python_canary`'s own
    ``base_commit``."""
    base_commit = repo.head()
    shutil.copytree(FIXTURE_DIR / "pkg", repo.path / "pkg")
    shutil.copytree(FIXTURE_DIR / "tests", repo.path / "tests")
    repo.commit_all("add control fixture")
    return base_commit


def _lane(repo_path: Path, rigor: tuple[str, ...]) -> Lane:
    judge = None
    isolation = None
    if "R1" in rigor:
        judge = JudgeConfig(
            language="python",
            source_roots=("pkg",),
            source_root_paths=(repo_path / "pkg",),
            fail_under=100.0,
            allow_excluded=False,
            coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
            mutation=None,
            canary=None,
            base="main",
        )
        # B006a/A-269, schema v2: an R1+ lane requires an explicit isolation
        # policy. This module's own subject (the real canary pipeline) is not
        # an omission test, so the migration rule gives it repository mode.
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


# --- O1: the real pipeline catches each mechanism for its SPECIFIC reason ----


def test_import_break_control_passes_and_the_real_transform_fails_command_failed(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0", "R1"))

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_IMPORT_BREAK,
    )

    assert result.attempts[0].control_outcome is Outcome.PASS
    assert result.attempts[0].transformed_outcome is Outcome.FAIL
    assert result.attempts[0].expected_reason_code is ReasonCode.COMMAND_FAILED
    assert result.attempts[0].observed_reason_code is ReasonCode.COMMAND_FAILED

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None

    # O2: an independently written schema-valid artifact -- the REAL claim,
    # validated against the shipped schema (not only the hand-built fixture).
    from assay.verdict import Claim

    r0_claim = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)
    verdict = Verdict(
        lane="package",
        commit="c" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T12:00:00+00:00",
        ended="2026-08-07T12:00:01+00:00",
        assay_version="0.1.0",
        declared_rigor=("R0", "R3"),
        declared_evidence=(),
        argv_declared=("pytest", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "-q"),
        env_declared={},
        env_effective={},
        scope="S1",
        enforcement="gate",
        judgment=Judgment(
            # P33/A-223a: an R0,R3 judgment records no comparison base.
            resolved=JudgmentResolved(language="python", source_roots=("pkg",)),
            r3=JudgmentR3(
                mechanism=canary.MECHANISM_IMPORT_BREAK, targets=(TARGET_PATH,)
            ),
        ),
        claims=(r0_claim, claim),
        snapshot_policy=SnapshotPolicy(selection="repository"),
    )
    document = __import__("json").loads(verdict.to_json())
    assert why_invalid(validator, document) == []


def test_uncovered_line_control_passes_and_the_real_transform_fails_uncovered_lines(
    git_repo: GitRepo,
):
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0", "R1"))

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )

    assert result.attempts[0].control_outcome is Outcome.PASS
    assert result.attempts[0].transformed_outcome is Outcome.FAIL
    assert result.attempts[0].expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert result.attempts[0].observed_reason_code is ReasonCode.UNCOVERED_LINES

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None


# --- O3/A-109: a bad case that unexpectedly PASSES survives -------------------


def test_an_r0_only_lane_never_catches_the_uncovered_line_transform_and_survives(
    git_repo: GitRepo,
):
    """The concrete case O1's own negative names: a lane that runs tests but
    never checks changed-line coverage sails right past a valid,
    test-neutral, uncovered addition -- proving R1 is what catches it, not
    merely 'the tests ran'."""
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0",))  # no R1 declared

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )

    assert result.attempts[0].control_outcome is Outcome.PASS
    assert result.attempts[0].transformed_outcome is Outcome.PASS  # unexpectedly passed
    assert result.attempts[0].observed_reason_code is None

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.CANARY_SURVIVED


# --- O1/O3: a transformed run that fails for the WRONG reason survives --------


class _MislabeledAdapter(PythonAdapter):
    """A deliberately mislabeled adapter: its ``inject_import_break`` does
    not actually break the import at all -- it performs the UNCOVERED-LINE
    transform instead. Proves :func:`assay.canary.judge_canary` checks the
    SPECIFIC observed reason against the SPECIFIC expected one, rather than
    accepting any real FAIL as success (O1's own negative)."""

    def inject_import_break(self, text: str) -> tuple[str, str]:
        return self.inject_uncovered_line(text)


def test_a_mechanism_that_fails_for_the_wrong_reason_survives(git_repo: GitRepo):
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0", "R1"))

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=_MislabeledAdapter(),
        mechanism=canary.MECHANISM_IMPORT_BREAK,  # expects COMMAND_FAILED
    )

    assert result.attempts[0].control_outcome is Outcome.PASS
    # The mislabeled transform never actually breaks R0 -- it is caught by
    # R1 instead, for a DIFFERENT reason than the mechanism claims.
    assert result.attempts[0].transformed_outcome is Outcome.FAIL
    assert result.attempts[0].expected_reason_code is ReasonCode.COMMAND_FAILED
    assert result.attempts[0].observed_reason_code is ReasonCode.UNCOVERED_LINES

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.CANARY_SURVIVED


# --- O1's negative: a broken baseline is caught, not silently passed ---------


def test_a_broken_control_renders_inconclusive_not_a_silent_pass(git_repo: GitRepo):
    base_commit = git_repo.head()
    git_repo.write("pkg/__init__.py", "")
    git_repo.write(
        "pkg/greet.py",
        "def greet(name: str) -> str:\n    return f'Hello, {name}!'\n",
    )
    git_repo.write(
        "tests/test_greet.py",
        "from pkg.greet import greet\n\n\n"
        "def test_greet():\n    assert greet('world') == 'THIS IS WRONG'\n",
    )
    git_repo.commit_all("add a BROKEN control fixture")

    lane = _lane(git_repo.path, ("R0", "R1"))
    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )

    assert result.attempts[0].control_outcome is Outcome.FAIL

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


# --- O3/A-109: malformed and no-op transforms are INCONCLUSIVE ----------------


def test_an_unrecognised_mechanism_is_inconclusive_after_a_real_control_run(
    git_repo: GitRepo,
):
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0", "R1"))

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism="not-a-real-mechanism",
    )

    assert result.attempts[0].control_outcome is Outcome.PASS  # the control still ran
    assert result.attempts[0].transformed_outcome is None
    assert result.attempts[0].expected_reason_code is None

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


class _NoOpAdapter(PythonAdapter):
    """Deliberately returns the SAME text unchanged -- proves the real
    no-op detection path in :func:`assay.canary.run_python_canary` (never
    naturally true of the real ``PythonAdapter``, which always appends
    something)."""

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        return text, "no-op: nothing changed"


def test_a_transform_that_produces_no_change_is_inconclusive(git_repo: GitRepo):
    base_commit = _materialize_control(git_repo)
    lane = _lane(git_repo.path, ("R0", "R1"))

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=_NoOpAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )

    assert result.attempts[0].control_outcome is Outcome.PASS
    assert result.attempts[0].transformed_outcome is None
    assert "nothing to judge" in result.attempts[0].description

    claim = canary.build_canary_claim(result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE


# --- the transformed commit isolates EXACTLY the injected change --------------


def test_the_transformed_commit_only_diffs_by_the_injected_lines(git_repo: GitRepo):
    """Proves the orchestration's own git plumbing: the transformed state is
    committed ON TOP of the control (never replacing it), so the R1 diff the
    transformed pipeline measures is scoped to the mechanism's own change."""
    base_commit = _materialize_control(git_repo)
    control_commit = git_repo.head()
    lane = _lane(git_repo.path, ("R0", "R1"))

    canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path=TARGET_PATH,
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_UNCOVERED_LINE,
    )

    diff_text = git_repo.git(
        "diff", "--unified=0", control_commit, "HEAD", "--", TARGET_PATH
    )
    added_lines = [line for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    # 2 blank lines + `def ...:` + 2 body statements = 5 added lines total.
    assert len(added_lines) == 5
    assert any("_assay_canary_unreached" in line for line in added_lines)
