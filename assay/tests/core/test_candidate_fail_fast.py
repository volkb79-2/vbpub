"""WP1: ``judge.mutation.candidate_fail_fast`` -- candidate-only ``-x`` and no coverage.

Covers the lane key (load, echo, refusals), the candidate command transform,
the judge identity, the collect-only preflight, the runner wiring, and the
bucket-identity property: a real pytest fixture project yields the SAME
killed/survived sets with and without the key.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    R0_LANE,
    GitRepo,
    Project,
    completed_process,
    make_deadline,
    make_lane,
    make_plan,
    make_r2_judge,
    prepared_snapshot,
    set_key,
)

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig, load_lane_file
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.mutation import (
    CANDIDATE_FAIL_FAST_APPENDED,
    MutationTarget,
    candidate_fail_fast_plan,
    judge_sha256,
    run_mutation,
)
from assay.r2_command import UnrecognizedCoverageOption

_ISOLATION = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
_JUDGE = '\n[lanes.package.judge]\nlanguage = "python"\nsource_roots = ["src"]\nbase = "main"\n'
_PYTEST_ARGV = '["python", "-m", "pytest", "tests", "-q", "--cov=src", "--cov-branch", "--cov-report=json:cov.json"]'


def _lane_text(extra: str, *, argv: str = _PYTEST_ARGV, language: str = "python") -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    body = set_key(body, "argv", argv)
    judge = _JUDGE.replace('language = "python"', f'language = "{language}"')
    return (
        body
        + _ISOLATION
        + judge
        + '\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n'
        'operators = ["python:compare-swap"]\n'
        + extra
    )


# --- the lane key ------------------------------------------------------------


def test_the_key_defaults_to_off_and_is_absent_from_the_declared_echo(project: Project):
    mutation = load_lane_file(project.write(_lane_text(""))).lane("package").judge.mutation
    assert mutation.candidate_fail_fast is False
    assert "candidate_fail_fast" not in mutation.as_declared()


def test_the_key_loads_and_is_echoed(project: Project):
    path = project.write(_lane_text("candidate_fail_fast = true\n"))
    mutation = load_lane_file(path).lane("package").judge.mutation
    assert mutation.candidate_fail_fast is True
    assert mutation.as_declared()["candidate_fail_fast"] is True


@pytest.mark.parametrize(
    "extra,argv,language,message",
    [
        ('candidate_fail_fast = "yes"\n', _PYTEST_ARGV, "python", "must be a boolean"),
        (
            "candidate_fail_fast = true\n",
            '["make", "test"]',
            "python",
            "literally invokes pytest",
        ),
        (
            "candidate_fail_fast = true\n",
            '["pytest", "--cov-fail-under=90"]',
            "python",
            "cannot remove the coverage option '--cov-fail-under=90'",
        ),
    ],
)
def test_the_key_refusals(project: Project, extra, argv, language, message):
    path = project.write(_lane_text(extra, argv=argv, language=language))
    with pytest.raises(LaneConfigError, match=message):
        load_lane_file(path)


# --- the candidate command ---------------------------------------------------


def test_candidate_plan_drops_coverage_and_appends_fail_fast_only_for_candidates():
    lane = make_lane(
        argv=("python", "-m", "pytest", "tests", "--cov=src", "--cov-branch",
              "--cov-report=json:cov.json", "-q")
    )
    plan = make_plan(lane, argv_append=("--tb=short",))
    candidate = candidate_fail_fast_plan(plan, extra=("--collect-only",))
    assert candidate.argv_declared == ("python", "-m", "pytest", "tests", "-q")
    assert candidate.argv_appended == (
        "--tb=short", *CANDIDATE_FAIL_FAST_APPENDED, "--collect-only"
    )
    assert candidate.argv_effective == candidate.argv_declared + candidate.argv_appended
    # the shared baseline plan is a different, untouched object
    assert plan.argv_effective[-2:] == ("-q", "--tb=short")
    assert "--cov=src" in plan.argv_effective


def test_candidate_plan_refuses_an_unrecognized_coverage_option():
    plan = make_plan(make_lane(argv=("pytest", "--cov-fail-under=9")))
    with pytest.raises(UnrecognizedCoverageOption):
        candidate_fail_fast_plan(plan)


# --- the judge identity ------------------------------------------------------


def test_judge_identity_changes_only_when_the_key_is_on():
    plan = make_plan(make_lane(argv=("pytest", "-q")))
    kwargs = dict(tree_sha256="a" * 64, plan=plan, tool_version="1")
    off = judge_sha256(**kwargs)
    assert judge_sha256(**kwargs, candidate_fail_fast=False) == off
    on = judge_sha256(**kwargs, candidate_fail_fast=True)
    assert on != off
    assert judge_sha256(**kwargs, candidate_fail_fast=True) == on


# --- the sweep: candidates change, the baseline does not ----------------------

_TEXT = "def is_a(x):\n    if x > 0:\n        return True\n    return False\n"
_DECLARED = ("pytest", "tests", "-q", "--cov=pkg", "--cov-report=json:cov.json")


class _Recorder:
    def __init__(self) -> None:
        self.argvs: list[tuple[str, ...]] = []

    def __call__(self, argv, *, env, cwd, timeout):
        self.argvs.append(tuple(argv))
        return completed_process(list(argv), returncode=0, stdout="", stderr="")


def _seed(tmp_path: Path, files: dict[str, str]) -> GitRepo:
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir(parents=True)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    for name, text in files.items():
        repo.write(name, text)
    repo.commit_all("seed")
    return repo


def _sweep(tmp_path: Path, *, fail_fast: bool, runner_: _Recorder):
    repo = _seed(tmp_path, {"pkg/a.py": _TEXT})
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    plan = make_plan(make_lane(argv=_DECLARED))
    baseline = runner.execute_plan(
        plan, cwd=repo.path, timeout=60.0, process_runner=runner_
    )
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        return run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=make_deadline(),
            targets=(MutationTarget(path="pkg/a.py", text=_TEXT, lines=frozenset({2})),),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=("python:compare-swap",),
            process_runner=runner_,
            clock=lambda: datetime.now(timezone.utc),
            candidate_fail_fast=fail_fast,
        )


def test_only_candidate_commands_get_fail_fast_and_lose_coverage(tmp_path: Path):
    recorder = _Recorder()
    mutation = _sweep(tmp_path, fail_fast=True, runner_=recorder)
    assert mutation.total == 1
    baseline_argv, candidate_argv = recorder.argvs
    assert baseline_argv == _DECLARED
    assert candidate_argv[:3] == ("pytest", "tests", "-q")
    assert not any(token.startswith("--cov") for token in candidate_argv)
    assert "-x" in candidate_argv
    # the receipt plugin is injected first; the fail-fast tokens follow it
    assert candidate_argv[-5:] == ("-p", "assay_mutation_witness_plugin", "-x", "-p", "no:pytest_cov")


def test_without_the_key_candidates_keep_the_declared_argv(tmp_path: Path):
    recorder = _Recorder()
    _sweep(tmp_path, fail_fast=False, runner_=recorder)
    assert recorder.argvs[1][: len(_DECLARED)] == _DECLARED
    assert "-x" not in recorder.argvs[1]


# --- the runner: preflight and wiring ----------------------------------------


def _plan_for_preflight():
    return make_plan(make_lane(argv=("pytest", "tests", "--cov=pkg")))


def _run_preflight(tmp_path: Path, returncode: int, calls: list):
    repo = _seed(tmp_path, {"pkg/a.py": _TEXT})
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)

    def process_runner(argv, *, env, cwd, timeout):
        calls.append(tuple(argv))
        return completed_process(list(argv), returncode=returncode, stdout="", stderr="")

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        runner._candidate_fail_fast_preflight(
            prepared=prepared,
            plan=_plan_for_preflight(),
            deadline=make_deadline(),
            process_runner=process_runner,
            clock=lambda: datetime.now(timezone.utc),
        )


def test_preflight_runs_the_candidate_command_collect_only(tmp_path: Path):
    calls: list = []
    _run_preflight(tmp_path, 0, calls)
    assert calls == [
        ("pytest", "tests", "-x", "-p", "no:pytest_cov", "--collect-only", "-q")
    ]


def test_preflight_refuses_a_command_that_cannot_collect(tmp_path: Path):
    with pytest.raises(AssayError, match="false kill") as caught:
        _run_preflight(tmp_path, 4, [])
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


_RUN_ARGV = ("pytest", "tests", "-q", "--cov=src")


def _seed_run_lane(git_repo: GitRepo):
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    return base_rev, git_repo.commit_all("introduce a compare-swap site")


def test_run_lane_applies_the_key_after_a_collect_only_preflight(git_repo: GitRepo):
    base_rev, head_rev = _seed_run_lane(git_repo)
    calls: list[tuple[str, ...]] = []

    def decide(argv, *, env, cwd, timeout):
        calls.append(tuple(argv))
        text = (Path(cwd) / "src" / "mod.py").read_text(encoding="utf-8")
        return completed_process(
            list(argv), returncode=0 if "x > 0" in text else 1, stdout="", stderr=""
        )

    mutation = MutationConfig(
        max_mutants=50,
        operators=("python:compare-swap",),
        candidate_fail_fast=True,
        liveness="false",
    )
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",), base=base_rev, mutation=mutation
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, argv=_RUN_ARGV)
    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=decide,
    )
    assert verdict.outcome is Outcome.PASS
    assert len(verdict.claims[1].mutation.killed) == 1
    baseline, preflight, candidate = calls
    assert baseline == _RUN_ARGV
    assert preflight[-2:] == ("--collect-only", "-q")
    assert "-x" in candidate and "--collect-only" not in candidate
    assert not any(token.startswith("--cov") for token in candidate)


# --- the bucket identity: a real pytest project -------------------------------

_MOD = (
    "def sign(x):\n"
    "    if x > 0:\n"
    "        return 1\n"
    "    if x < 0:\n"
    "        return -1\n"
    "    return 0\n"
    "\n"
    "def untested(x):\n"
    "    return x >= 10\n"
)
_TESTS = (
    "from pkg.mod import sign\n"
    "\n"
    "def test_pos():\n"
    "    assert sign(3) == 1\n"
    "\n"
    "def test_neg():\n"
    "    assert sign(-3) == -1\n"
    "\n"
    "def test_zero():\n"
    "    assert sign(0) == 0\n"
)


def _real_sweep(tmp_path: Path, *, fail_fast: bool):
    repo = _seed(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": _MOD,
            "tests/test_mod.py": _TESTS,
            ".gitignore": "cov.json\n__pycache__/\n.coverage\n.pytest_cache/\n",
        },
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    lane = make_lane(
        argv=(
            sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
            "--cov=pkg", "--cov-branch", "--cov-report=json:cov.json",
        ),
        env_passthrough=("PATH", "HOME"),
    )
    plan = make_plan(lane, passthrough_source=dict(__import__("os").environ))
    baseline = runner.execute_plan(plan, cwd=repo.path, timeout=120.0)
    assert baseline.outcome is Outcome.PASS
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=make_deadline(budget_seconds=240.0),
            targets=(
                MutationTarget(path="pkg/mod.py", text=_MOD, lines=frozenset({2, 4, 9})),
            ),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=("python:compare-swap",),
            process_runner=runner.default_process_runner,
            clock=lambda: datetime.now(timezone.utc),
            candidate_fail_fast=fail_fast,
        )
    return mutation


def _identity(mutation) -> dict[str, set[tuple[int, str]]]:
    return {
        bucket: {(m.lineno, m.description) for m in getattr(mutation, bucket)}
        for bucket in ("killed", "survived", "crashed", "budget_exceeded")
    }


def test_buckets_are_identical_with_and_without_fail_fast(tmp_path: Path):
    plain = _real_sweep(tmp_path / "plain", fail_fast=False)
    fast = _real_sweep(tmp_path / "fast", fail_fast=True)
    assert _identity(fast) == _identity(plain)
    assert plain.total == fast.total >= 3
    # the fixture really exercises both outcomes
    assert plain.killed and plain.survived


def test_cold_witness_ignores_the_key_and_says_so():
    import io

    stream = io.StringIO()
    assert runner._resolve_candidate_fail_fast(True, cold_witness=True, diagnostics=stream) is False
    assert "ignored under --cold-witness" in stream.getvalue()
    assert runner._resolve_candidate_fail_fast(True, cold_witness=True, diagnostics=None) is False
    assert runner._resolve_candidate_fail_fast(True, cold_witness=False, diagnostics=stream) is True
    assert runner._resolve_candidate_fail_fast(False, cold_witness=True, diagnostics=stream) is False
    assert runner._resolve_candidate_fail_fast(False, cold_witness=False, diagnostics=None) is False
