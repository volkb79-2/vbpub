"""Locked P23 acceptance for exact committed-snapshot reexecution.

Expected documents and ledgers are carver-authored.  The suite exercises the
landed P20/P21/P22 boundaries through P23's production entry points; it never
generates its own expected artifact through Assay's serializer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

import pytest
from jsonschema import Draft202012Validator

from assay import mutation, runner, verify as verify_module
from assay.adapters.python import PythonAdapter
from assay.config import (
    CanaryConfig,
    CoverageConfig,
    JudgeConfig,
    Lane,
    MutationConfig,
    load_lane_file,
)
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.mutation import MutationSite, MutationTarget, line_for_offset
from assay.verdict import load_schema
from assay.verify import verify_document

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "fixture-manifest.json").read_text(encoding="utf-8"))
EXPECTED_LEDGER = json.loads(
    (HERE / "expected/process-ledger.json").read_text(encoding="utf-8")
)
EXPECTED_SNAPSHOT_LIMIT = HERE / "expected/r0-snapshot-limit-v4.json"
ORDINARY_SNAPSHOT_LIMIT = (
    HERE.parents[2]
    / "tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json"
)
CONFORMANCE_SOURCE = HERE.parents[2] / "tests/test_verdict_conformance.py"
GIT = shutil.which("git")
assert GIT is not None, "the real gate must provide git"

MOMENT = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
FIXED_IDENTITY = {
    "GIT_AUTHOR_NAME": "Assay P23 Fixture",
    "GIT_AUTHOR_EMAIL": "p23@assay.invalid",
    "GIT_COMMITTER_NAME": "Assay P23 Fixture",
    "GIT_COMMITTER_EMAIL": "p23@assay.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _clock() -> datetime:
    return MOMENT


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **FIXED_IDENTITY,
    }


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        [GIT, "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_git_env(),
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr}"
        )
    return completed.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class FixtureRepo:
    root: Path
    project: Path
    base: str
    head: str
    original_source: str


def _fixture_repo(
    tmp_path: Path,
    *,
    source: str = "def check(x):\n    return x > 0\n",
    absolute_symlink: bool = False,
) -> FixtureRepo:
    repo = tmp_path / "consumer"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, ".gitignore", "cov.json\n.coverage\ncache/\n")
    _write(repo, "apps/p/src/__init__.py", "")
    _write(repo, "apps/p/src/mod.py", "def check(x):\n    return 0\n")
    _write(repo, "shared/input.txt", MANIFEST["tracked_sibling"])
    base = _commit(repo, "base")
    _write(repo, "apps/p/src/mod.py", source)
    if absolute_symlink:
        os.symlink("/assay-p23-forbidden-host-path", repo / "absolute-link")
    head = _commit(repo, "changed source")
    _write(repo, "apps/p/cov.json", MANIFEST["stale_coverage"])
    _write(repo, "cache/ignored.txt", "ignored consumer cache\n")
    return FixtureRepo(
        root=repo.resolve(),
        project=(repo / "apps/p").resolve(),
        base=base,
        head=head,
        original_source=source,
    )


def _judge_r2(fixture: FixtureRepo, *, max_mutants: int = 20) -> JudgeConfig:
    return JudgeConfig(
        language="python",
        source_roots=("src",),
        source_root_paths=(fixture.project / "src",),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(
            jobs=1, max_mutants=max_mutants, operators=("compare-swap",)
        ),
        canary=None,
        base=fixture.base,
    )


def _judge_r3(fixture: FixtureRepo, *, mechanism: str) -> JudgeConfig:
    uncovered = mechanism == "uncovered-line"
    return JudgeConfig(
        language="python",
        source_roots=("src",),
        source_root_paths=(fixture.project / "src",),
        fail_under=100.0 if uncovered else None,
        allow_excluded=False if uncovered else None,
        coverage=(
            CoverageConfig(format="coverage-py-json", artifact="cov.json")
            if uncovered
            else None
        ),
        mutation=None,
        canary=CanaryConfig(mechanism=mechanism, target="src/mod.py"),
        base=fixture.base,
    )


def _lane(
    *,
    rigor: tuple[str, ...] = ("R0",),
    judge: JudgeConfig | None = None,
    budget_seconds: float = 100.0,
) -> Lane:
    return Lane(
        name="package",
        scope="S1",
        rigor=rigor,
        enforcement="gate",
        argv=("assay-fixture", "--declared"),
        env=MappingProxyType({"FIXED": "declared"}),
        env_passthrough=("TOKEN", "ABSENT"),
        budget=f"{budget_seconds}s",
        budget_seconds=budget_seconds,
        allow_argv_append=True,
        judge=judge,
        where=None,
    )


class CountingMonotonic:
    def __init__(self, *, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        observed = self.value
        self.value += self.step
        return observed


def _normalise_record(
    argv: tuple[str, ...], env: Mapping[str, str], cwd: Path, source: str
) -> dict[str, object]:
    return {
        "argv": list(argv),
        "env": dict(env),
        "cwd_suffix": PurePosixPath(*cwd.parts[-2:]).as_posix(),
        "tracked_sibling": (cwd / "../../shared/input.txt").read_text(encoding="utf-8"),
        "stale_coverage_present": (cwd / "cov.json").exists(),
        "unit": "baseline" if source == MANIFEST["head_source"] else "replacement",
    }


def _raw_verifier_failures(document: dict) -> list[str]:
    failures: list[str] = []
    outcome = verify_module._check_outcome_and_reason_code(document, failures)
    verify_module._check_exit_code(document, outcome, failures)
    verify_module._check_interval_is_ordered(document, failures)
    verify_module._check_lane_resolved_group(document, failures)
    verify_module._check_claims_cover_declared_rigor(document, failures)
    verify_module._check_evidence_covers_declared_evidence(document, failures)
    verify_module._check_outcome_agrees_with_rollup(document, outcome, failures)
    verify_module._check_judgment_matches_claims(document, failures)
    verify_module._check_mutation_payload_shapes(document, failures)
    return failures


def test_mechanical_plan_and_deadline_contract(tmp_path: Path) -> None:
    lane = _lane()
    plan = runner.resolve_command_plan(
        lane,
        argv_append=("--focus", "changed"),
        passthrough_source={"TOKEN": "captured", "AMBIENT": "forbidden"},
        project_prefix=PurePosixPath("apps/p"),
    )
    assert plan.argv_effective == (
        "assay-fixture",
        "--declared",
        "--focus",
        "changed",
    )
    assert dict(plan.env_effective) == {"FIXED": "declared", "TOKEN": "captured"}
    assert plan.env_passthrough == ("TOKEN", "ABSENT")
    assert plan.allow_argv_append is True
    assert plan.budget_seconds == 100.0
    assert plan.project_prefix == PurePosixPath("apps/p")

    calls: list[tuple[tuple[str, ...], float]] = []

    def process(argv, *, env, cwd, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    result = runner.execute_plan(
        plan, cwd=tmp_path, timeout=7.5, process_runner=process, clock=_clock
    )
    assert result.plan is plan
    assert result.outcome is Outcome.PASS
    assert calls == [(plan.argv_effective, 7.5)]
    for bad in (True, 0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="timeout"):
            runner.execute_plan(plan, cwd=tmp_path, timeout=bad)

    values = iter((10.0, 11.0, 15.0))
    deadline = runner.LaneDeadline.start(budget_seconds=5.0, monotonic=lambda: next(values))
    assert deadline.remaining() == 4.0
    with pytest.raises(AssayError) as caught:
        deadline.remaining()
    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def _lane_toml(*, rigor: str, canary: str | None = None) -> str:
    needs_r1 = "R1" in rigor
    needs_r2 = "R2" in rigor
    needs_r3 = "R3" in rigor
    judge_lines: list[str] = []
    if needs_r1 or needs_r2 or needs_r3:
        judge_lines.extend(['language = "python"', 'source_roots = ["src"]'])
    if needs_r1:
        judge_lines.extend(
            [
                "fail_under = 100.0",
                "allow_excluded = false",
                'coverage = { format = "coverage-py-json", artifact = "cov.json" }',
                'base = "HEAD~1"',
            ]
        )
    elif needs_r2:
        judge_lines.append('base = "HEAD~1"')
    if needs_r2:
        judge_lines.append(
            'mutation = { jobs = 1, max_mutants = 3, operators = ["compare-swap"] }'
        )
    if needs_r3:
        mechanism = "import-break" if canary is None else canary
        judge_lines.append(
            f'canary = {{ mechanism = "{mechanism}", target = "src/mod.py" }}'
        )
    judge = "" if not judge_lines else "\n[lanes.package.judge]\n" + "\n".join(judge_lines)
    return f'''schema_version = 1
[lanes.package]
scope = "S1"
rigor = {rigor}
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = []
budget = "10s"
allow_argv_append = false
{judge}
'''


@pytest.mark.parametrize(
    ("rigor", "canary", "fragment"),
    [
        ('["R2"]', None, "R0-led ordered subsequence"),
        ('["R0", "R3", "R2"]', None, "R0-led ordered subsequence"),
        ('["R0", "R3"]', "uncovered-line", "requires declared R1"),
    ],
)
def test_invalid_rigor_is_a_load_time_refusal(
    tmp_path: Path, rigor: str, canary: str | None, fragment: str
) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src/mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    lane_file = project / "assay.toml"
    lane_file.write_text(_lane_toml(rigor=rigor, canary=canary), encoding="utf-8")
    with pytest.raises(LaneConfigError, match=fragment):
        load_lane_file(lane_file)


def test_valid_ordered_r0_r2_loads(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    lane_file = project / "assay.toml"
    lane_file.write_text(_lane_toml(rigor='["R0", "R2"]'), encoding="utf-8")
    assert load_lane_file(lane_file).lane("package").rigor == ("R0", "R2")


def test_nested_plan_is_identical_for_baseline_and_mutant_and_source_is_unchanged(
    tmp_path: Path,
) -> None:
    fixture = _fixture_repo(tmp_path)
    lane = _lane(rigor=("R0", "R2"), judge=_judge_r2(fixture))
    records: list[dict[str, object]] = []
    timeouts: list[float] = []

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        assert cwd != fixture.project
        source = (cwd / "src/mod.py").read_text(encoding="utf-8")
        records.append(_normalise_record(tuple(argv), env, cwd, source))
        timeouts.append(timeout)
        return subprocess.CompletedProcess(
            list(argv), 0 if source == fixture.original_source else 1, "", ""
        )

    before_head = _git(fixture.root, "rev-parse", "HEAD")
    before_tree = _git(fixture.root, "write-tree")
    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        argv_append=("--focus", "changed"),
        passthrough_source={"TOKEN": "captured", "AMBIENT": "forbidden"},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert records == EXPECTED_LEDGER["records"]
    assert len(timeouts) == 2 and timeouts[0] > timeouts[1] > 0
    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].status is Outcome.PASS
    assert verdict.claims[1].mutation.total == 1
    assert _git(fixture.root, "rev-parse", "HEAD") == before_head
    assert _git(fixture.root, "write-tree") == before_tree
    assert _git(fixture.root, "status", "--porcelain=v1") == ""
    assert (fixture.project / "src/mod.py").read_text(encoding="utf-8") == fixture.original_source


class FourSiteAdapter:
    name = "four-site"
    source_globs = ("*.py",)
    excluded_dir_names = frozenset()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        raw = text.encode("utf-8")
        offsets = [index for index, value in enumerate(raw) if value == ord("<")]
        sites = tuple(
            MutationSite(
                start_byte=offset,
                end_byte=offset + 1,
                replacement=b"<=",
                lineno=line_for_offset(raw, offset),
                operator="compare-swap",
                description="< to <=",
            )
            for offset in offsets
        )
        return sites[:limit]


@pytest.mark.parametrize("site_count", [2, 3], ids=["max-minus-one", "max"])
def test_max_minus_one_and_max_execute_every_discovered_site(
    tmp_path: Path, site_count: int
) -> None:
    conditions = " and ".join(f"x < {value}" for value in range(site_count))
    source = f"def check(x):\n    return {conditions}\n"
    fixture = _fixture_repo(tmp_path, source=source)
    lane = _lane(
        rigor=("R0", "R2"), judge=_judge_r2(fixture, max_mutants=3)
    )
    calls: list[str] = []

    def process(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "src/mod.py").read_text(encoding="utf-8")
        calls.append("baseline" if text == source else "mutant")
        return subprocess.CompletedProcess(
            list(argv), 0 if text == source else 1, "", ""
        )

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=FourSiteAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert calls == ["baseline", *("mutant" for _ in range(site_count))]
    r2 = verdict.claims[1]
    assert r2.status is Outcome.PASS
    assert r2.mutation.candidate_count == site_count
    assert r2.mutation.total == site_count
    assert len(r2.mutation.killed) == site_count


class ExplodingPrepared:
    def materialize_replacement(self, **kwargs):
        raise AssertionError("max+1 must not materialize a snapshot")


def test_max_plus_one_refuses_before_executor_snapshot_or_process() -> None:
    lane = _lane(rigor=("R0", "R2"), judge=None)
    plan = runner.resolve_command_plan(
        lane, passthrough_source={}, project_prefix=PurePosixPath("apps/p")
    )
    baseline = runner.CommandResult(
        plan=plan,
        outcome=Outcome.PASS,
        reason_code=None,
        returncode=0,
        started=MOMENT.isoformat(),
        ended=MOMENT.isoformat(),
    )
    target = MutationTarget(
        path="apps/p/src/mod.py",
        text="a<a\na<a\na<a\na<a\n",
        lines=frozenset({1, 2, 3, 4}),
    )

    def explode_executor(_: int):
        raise AssertionError("max+1 must not construct an executor")

    def explode_process(*args, **kwargs):
        raise AssertionError("max+1 must not launch a process")

    deadline = runner.LaneDeadline.start(budget_seconds=10.0, monotonic=lambda: 0.0)
    result = mutation.run_mutation(
        baseline=baseline,
        prepared=ExplodingPrepared(),
        plan=plan,
        deadline=deadline,
        targets=(target,),
        adapter=FourSiteAdapter(),
        jobs=1,
        max_mutants=3,
        operators=("compare-swap",),
        process_runner=explode_process,
        clock=_clock,
        executor_factory=explode_executor,
    )
    assert result.candidate_count == 4
    assert result.total == 0
    assert result.is_limit_sentinel


def test_injected_expiry_after_one_mutant_launches_no_next_unit(tmp_path: Path) -> None:
    source = "def check(x):\n    return x > 0 and x < 10\n"
    fixture = _fixture_repo(tmp_path, source=source)
    lane = _lane(rigor=("R0", "R2"), judge=_judge_r2(fixture), budget_seconds=100.0)
    expired = False
    calls: list[str] = []

    def monotonic() -> float:
        return 101.0 if expired else 0.0

    def process(argv, *, env, cwd, timeout):
        nonlocal expired
        text = (Path(cwd) / "src/mod.py").read_text(encoding="utf-8")
        unit = "baseline" if text == source else "mutant"
        calls.append(unit)
        if unit == "mutant":
            expired = True
        return subprocess.CompletedProcess(list(argv), 0 if unit == "baseline" else 1, "", "")

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=monotonic,
    )
    assert calls == ["baseline", "mutant"]
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert r2.mutation.total == 2
    assert len(r2.mutation.killed) == 1
    assert len(r2.mutation.budget_exceeded) == 1


def test_snapshot_support_write_is_not_laundered_and_consumer_stays_clean(
    tmp_path: Path,
) -> None:
    fixture = _fixture_repo(tmp_path)
    lane = _lane(rigor=("R0", "R2"), judge=_judge_r2(fixture))

    def process(argv, *, env, cwd, timeout):
        (Path(cwd) / "../../shared/input.txt").write_text("command changed it\n", encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert verdict.claims[0].status is Outcome.PASS
    assert (verdict.claims[1].status, verdict.claims[1].reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.DIRTY_TREE,
    )
    assert (fixture.root / "shared/input.txt").read_text(encoding="utf-8") == MANIFEST[
        "tracked_sibling"
    ]
    assert _git(fixture.root, "status", "--porcelain=v1") == ""


def test_absolute_symlink_is_direct_r0_exception_but_never_higher_rigor_fallback(
    tmp_path: Path,
) -> None:
    direct = _fixture_repo(tmp_path / "direct", absolute_symlink=True)
    direct_calls: list[Path] = []

    def direct_process(argv, *, env, cwd, timeout):
        direct_calls.append(Path(cwd))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    r0 = runner.run_lane(
        _lane(),
        commit=direct.head,
        repo=direct.root,
        project_root=direct.project,
        adapter=None,
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=direct_process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert r0.outcome is Outcome.PASS
    assert direct_calls == [direct.project]

    hostile = _fixture_repo(tmp_path / "higher", absolute_symlink=True)
    higher_calls: list[Path] = []

    def higher_process(argv, *, env, cwd, timeout):
        higher_calls.append(Path(cwd))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    refused = runner.run_lane(
        _lane(rigor=("R0", "R2"), judge=_judge_r2(hostile)),
        commit=hostile.head,
        repo=hostile.root,
        project_root=hostile.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=higher_process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert refused.outcome is Outcome.ERROR
    assert all(claim.reason_code is ReasonCode.GIT_FAILED for claim in refused.claims)
    assert higher_calls == []


def test_canary_halves_do_not_reuse_control_or_consumer_coverage(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    judge = JudgeConfig(
        language="python",
        source_roots=("src",),
        source_root_paths=(fixture.project / "src",),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=None,
        canary=CanaryConfig(mechanism="uncovered-line", target="src/mod.py"),
        base=fixture.base,
    )
    lane = _lane(rigor=("R0", "R1", "R3"), judge=judge)
    original = fixture.original_source
    calls: list[str] = []
    records: list[dict[str, object]] = []
    timeouts: list[float] = []

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        assert not (cwd / "cov.json").exists(), "each unit must start artifact-absent"
        source = (cwd / "src/mod.py").read_text(encoding="utf-8")
        transformed = source != original
        unit = "transform" if transformed else ("baseline" if not calls else "control")
        calls.append(unit)
        record = _normalise_record(tuple(argv), env, cwd, source)
        record["unit"] = unit
        records.append(record)
        timeouts.append(timeout)
        if not transformed:
            document = {
                "files": {
                    "apps/p/src/mod.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [],
                        "excluded_lines": [],
                    }
                }
            }
            (cwd / "cov.json").write_text(json.dumps(document), encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        argv_append=("--focus", "changed"),
        passthrough_source={"TOKEN": "captured", "AMBIENT": "forbidden"},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert calls == ["baseline", "control", "transform"]
    assert records == EXPECTED_LEDGER["canary_records"]
    assert len(timeouts) == 3 and timeouts[0] > timeouts[1] > timeouts[2] > 0
    r3 = verdict.claims[2]
    assert (r3.status, r3.reason_code) == (Outcome.FAIL, ReasonCode.CANARY_SURVIVED)
    assert r3.canary.observed_reason_code is ReasonCode.EMPTY_COVERAGE
    assert _git(fixture.root, "status", "--porcelain=v1") == ""


def test_failed_r0_is_a_complete_inconclusive_r3_control_without_extra_unit(
    tmp_path: Path,
) -> None:
    fixture = _fixture_repo(tmp_path)
    lane = _lane(
        rigor=("R0", "R3"), judge=_judge_r3(fixture, mechanism="import-break")
    )
    calls = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(list(argv), 7, "", "")

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert calls == 1
    assert (verdict.claims[0].status, verdict.claims[0].reason_code) == (
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    )
    r3 = verdict.claims[1]
    assert (r3.status, r3.reason_code) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert r3.canary is not None
    assert r3.canary.control_outcome is Outcome.FAIL
    assert r3.canary.transformed_outcome is None
    assert verdict.judgment is not None and verdict.judgment.r3 is not None


def test_failed_baseline_r1_is_an_inconclusive_uncovered_control_without_reuse(
    tmp_path: Path,
) -> None:
    fixture = _fixture_repo(tmp_path)
    lane = _lane(
        rigor=("R0", "R1", "R3"),
        judge=_judge_r3(fixture, mechanism="uncovered-line"),
    )
    calls = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal calls
        calls += 1
        assert not (Path(cwd) / "cov.json").exists()
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )
    assert calls == 1
    assert verdict.claims[0].status is Outcome.PASS
    assert (verdict.claims[1].status, verdict.claims[1].reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.EMPTY_COVERAGE,
    )
    r3 = verdict.claims[2]
    assert (r3.status, r3.reason_code) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert r3.canary is not None
    assert r3.canary.control_outcome is Outcome.NO_MEASUREMENT
    assert r3.canary.transformed_outcome is None
    assert verdict.judgment is not None and verdict.judgment.r3 is not None


class EnterFails:
    def __enter__(self):
        raise OSError("injected scratch-root entry failure")

    def __exit__(self, exc_type, exc, traceback):
        return False


class ExitFails:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self) -> Path:
        return self.root

    def __exit__(self, exc_type, exc, traceback):
        raise OSError("injected scratch-root cleanup failure")


def test_scratch_entry_failure_refuses_all_claims_before_process(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    process_calls = 0

    def process(*args, **kwargs):
        nonlocal process_calls
        process_calls += 1
        raise AssertionError("scratch entry failure must precede every process")

    verdict = runner.run_lane(
        _lane(rigor=("R0", "R2"), judge=_judge_r2(fixture)),
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
        scratch_root_factory=lambda: EnterFails(),
    )
    assert process_calls == 0
    assert all(
        (claim.status, claim.reason_code) == (Outcome.ERROR, ReasonCode.GIT_FAILED)
        for claim in verdict.claims
    )
    assert all(claim.mutation is None for claim in verdict.claims)
    assert verdict.judgment is None


def test_scratch_exit_failure_replaces_only_highest_higher_rigor_claim(
    tmp_path: Path,
) -> None:
    fixture = _fixture_repo(tmp_path)
    scratch = (tmp_path / "owned-scratch").resolve()
    scratch.mkdir()
    process_calls = 0

    def process(argv, *, env, cwd, timeout):
        nonlocal process_calls
        process_calls += 1
        source = (Path(cwd) / "src/mod.py").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            list(argv), 0 if source == fixture.original_source else 1, "", ""
        )

    verdict = runner.run_lane(
        _lane(rigor=("R0", "R2"), judge=_judge_r2(fixture)),
        commit=fixture.head,
        repo=fixture.root,
        project_root=fixture.project,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        passthrough_source={},
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
        scratch_root_factory=lambda: ExitFails(scratch),
    )
    assert process_calls == 2
    assert verdict.claims[0].status is Outcome.PASS
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (Outcome.ERROR, ReasonCode.GIT_FAILED)
    assert r2.mutation is None
    assert verdict.judgment is None
    assert _git(fixture.root, "status", "--porcelain=v1") == ""


def test_snapshot_limit_artifact_closes_ordinary_raw_and_schema_audits() -> None:
    expected_bytes = EXPECTED_SNAPSHOT_LIMIT.read_bytes()
    assert ORDINARY_SNAPSHOT_LIMIT.read_bytes() == expected_bytes
    document = json.loads(expected_bytes)
    assert list(Draft202012Validator(load_schema()).iter_errors(document)) == []
    assert _raw_verifier_failures(document) == []
    assert verify_document(document) == []
    source = CONFORMANCE_SOURCE.read_text(encoding="utf-8")
    assert '("BUDGET_EXCEEDED", "SNAPSHOT_LIMIT_EXCEEDED")' not in source.split(
        "EXCLUDED_ENTIRELY", 1
    )[1].split("REQUIRED_PAIRS", 1)[0]


def test_locked_inputs_have_the_recorded_hashes() -> None:
    for relative, expected in MANIFEST["locked_sha256"].items():
        path = HERE / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
