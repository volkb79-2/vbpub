"""``cli.plan_jobs`` and the plan-row identity inputs (B108 phase 1, W9 O16a)."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath

import pytest
from conftest import GitRepo

from assay import cli, isolation, mutation, runner
from assay.adapters import sql as sql_adapter_module
from assay.adapters.sql import SqlAdapter
from assay.candidate_identity import candidate_id_from_fields
from assay.cli import main, plan_jobs
from assay.config import LaneFile, load_lane_file
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.verdict import VERDICT_SCHEMA_VERSION

IDENTITY_KEYS = {
    "path",
    "source_sha256",
    "start_byte",
    "end_byte",
    "mutated_file_sha256",
    "operator",
}
ROW_KEYS = IDENTITY_KEYS | {"id", "lineno", "description"}

_SOURCE = (
    "def flag(a, b, c, d):\n"
    "    return a > 0 and b < 1 and c >= 2 and d <= 3\n"
)

_SEQUENTIAL_ARGV = f'["{sys.executable}", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]'


def _seed(repo: GitRepo, *, argv: str | None = None) -> Path:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(a, b, c, d):\n    return 0\n")
    repo.write("tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    repo.write("notes.txt", "one\n")
    base = repo.commit_all("seed")
    repo.write("src/mod.py", _SOURCE)
    repo.commit_all("add compare sites")
    path = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = {argv if argv is not None else f'["{sys.executable}", "-c", "pass"]'}
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 20
operators = ["python:compare-swap"]
""",
    )
    repo.commit_all("add assay.toml")
    return path


def _seed_sql(repo: GitRepo, *, modified_source: str | None = None) -> Path:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write(
        "src/schema.sql",
        "CREATE TABLE item (value integer CHECK (value > 0));\n",
    )
    base = repo.commit_all("seed SQL target")
    repo.write(
        "src/schema.sql",
        modified_source
        if modified_source is not None
        else "CREATE TABLE item (value integer CHECK (value >= 0));\n",
    )
    repo.commit_all("change SQL target")
    path = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "sql"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 20
operators = ["sql:drop-check"]
equivalence_artifact = ".assay/schema-dump.sql"
""",
    )
    repo.commit_all("add SQL lane")
    return path


def _load(toml: Path) -> tuple[LaneFile, object]:
    lane_file = load_lane_file(toml)
    return lane_file, lane_file.lane("package")


def _plan_payload(toml: Path, *extra: str) -> dict:
    out = io.StringIO()
    code = main(["plan", "--json", "package", "--file", str(toml), *extra], stdout=out, stderr=io.StringIO())
    assert code == 0
    return json.loads(out.getvalue())


def test_o16a_i_every_row_reproduces_its_id_from_its_identity_inputs(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    rows = plan_jobs(lane_file, lane)
    assert rows != mutation.UNSUPPORTED and len(rows) == 4
    for row in rows:
        assert set(row) == ROW_KEYS
        identity = {key: row[key] for key in IDENTITY_KEYS}
        assert candidate_id_from_fields(**identity) == row["id"]
        assert len(row["source_sha256"]) == 64 and len(row["mutated_file_sha256"]) == 64
    assert len({row["mutated_file_sha256"] for row in rows}) == 4


def test_assay_plan_does_not_read_selected_sql_targets_again_as_context(
    git_repo: GitRepo, monkeypatch
):
    lane_file, lane = _load(_seed_sql(git_repo))
    original = isolation.SnapshotRepository.read_regular_file
    reads: list[PurePosixPath] = []

    def tracked_read(self, path, *, timeout):
        reads.append(PurePosixPath(path))
        return original(self, path, timeout=timeout)

    monkeypatch.setattr(
        isolation.SnapshotRepository, "read_regular_file", tracked_read
    )

    jobs = plan_jobs(lane_file, lane)

    assert jobs != mutation.UNSUPPORTED
    assert len(jobs) == 1
    assert reads.count(PurePosixPath("src/schema.sql")) == 1


def test_o16a_the_identity_key_set_is_exactly_the_helpers_keyword_set(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    adapter = cli._resolve_declared_adapters(lane)
    discovered = cli._discover_plan_jobs(
        lane_file,
        lane,
        adapter=adapter,
        base_declaration=runner.resolve_base_declaration(lane, None),
        operators=lane.judge.mutation.operators,
        allow_dirty=False,
        resolve_reuse_command=False,
    )
    job = discovered.jobs[0]
    assert set(mutation.candidate_identity_fields(job)) == IDENTITY_KEYS
    assert mutation.candidate_id(job) == candidate_id_from_fields(
        **mutation.candidate_identity_fields(job)
    )


def test_plan_row_identity_work_checks_the_lane_deadline(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    discovered = cli._discover_plan_jobs(
        lane_file,
        lane,
        adapter=cli._resolve_declared_adapters(lane),
        base_declaration=runner.resolve_base_declaration(lane, None),
        operators=lane.judge.mutation.operators,
        allow_dirty=False,
        resolve_reuse_command=False,
    )
    checks = 0
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if checks >= 2 else 0.0,
        honors_termination=False,
    )

    def check_deadline():
        nonlocal checks
        checks += 1
        return deadline.remaining()

    with pytest.raises(AssayError) as caught:
        cli._plan_rows_from_jobs(
            discovered.jobs, deadline_check=check_deadline
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert checks == 2


def test_shard_candidate_identity_work_checks_the_lane_deadline(
    git_repo: GitRepo,
):
    lane_file, lane = _load(_seed(git_repo))
    discovered = cli._discover_plan_jobs(
        lane_file,
        lane,
        adapter=cli._resolve_declared_adapters(lane),
        base_declaration=runner.resolve_base_declaration(lane, None),
        operators=lane.judge.mutation.operators,
        allow_dirty=False,
        resolve_reuse_command=False,
    )
    checks = 0
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if checks >= 2 else 0.0,
        honors_termination=False,
    )

    def check_deadline():
        nonlocal checks
        checks += 1
        return deadline.remaining()

    with pytest.raises(AssayError) as caught:
        cli._select_plan_jobs(
            discovered.jobs,
            "0/1",
            deadline_check=check_deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert checks == 2


def test_unsharded_plan_selection_skips_candidate_identity_hashing(
    git_repo: GitRepo, monkeypatch
):
    lane_file, lane = _load(_seed(git_repo))
    discovered = cli._discover_plan_jobs(
        lane_file,
        lane,
        adapter=cli._resolve_declared_adapters(lane),
        base_declaration=runner.resolve_base_declaration(lane, None),
        operators=lane.judge.mutation.operators,
        allow_dirty=False,
        resolve_reuse_command=False,
    )
    monkeypatch.setattr(
        mutation,
        "candidate_id",
        lambda _job: pytest.fail("unsharded selection must not hash candidates"),
    )

    assert cli._select_plan_jobs(discovered.jobs, None) == discovered.jobs


@pytest.mark.parametrize("expiration_phase", ("context", "generation"))
def test_assay_plan_uses_the_lane_deadline_for_sql_context_discovery(
    git_repo: GitRepo, monkeypatch, expiration_phase
):
    large_check = "CREATE TABLE item (value integer CHECK (" + (
        "value > 0 AND " * 1000
    )
    lane_file, lane = _load(
        _seed_sql(git_repo, modified_source=large_check)
    )
    original_prepare = SqlAdapter.prepare_mutation_context_with_sources_bounded
    original_generate = (
        SqlAdapter.generate_mutation_sites_with_context_for_source_bounded
    )
    original_lex = sql_adapter_module.lex_sql
    expired = False
    generation_started = False
    deadlines = []
    seen_deadlines = []

    def start(cls, *, budget_seconds, monotonic):
        deadline = runner.LaneDeadline(
            expires_at=1.0,
            monotonic=lambda: 2.0 if expired else 0.0,
            honors_termination=False,
        )
        deadlines.append(deadline)
        return deadline

    def observe_prepare(self, sources, *, remaining):
        seen_deadlines.append(remaining)
        return original_prepare(
            self,
            sources,
            remaining=remaining,
        )

    def observe_lex(source, *, check_deadline=None):
        nonlocal expired
        if expiration_phase == "context":
            assert check_deadline is not None
            expired = True
        return original_lex(source, check_deadline=check_deadline)

    def observe_generate(
        self,
        text,
        lines,
        *,
        operators,
        limit,
        context,
        source_identity,
        remaining,
    ):
        nonlocal expired, generation_started
        seen_deadlines.append(remaining)
        if expiration_phase == "generation":
            # Context discovery completed; expire when target generation
            # begins to prove the same deadline reaches both phases.
            expired = True
        generation_started = True
        return original_generate(
            self,
            text,
            lines,
            operators=operators,
            limit=limit,
            context=context,
            source_identity=source_identity,
            remaining=remaining,
        )

    monkeypatch.setattr(
        SqlAdapter,
        "prepare_mutation_context_with_sources_bounded",
        observe_prepare,
    )
    monkeypatch.setattr(
        SqlAdapter,
        "generate_mutation_sites_with_context_for_source_bounded",
        observe_generate,
    )
    monkeypatch.setattr(sql_adapter_module, "lex_sql", observe_lex)
    monkeypatch.setattr(
        runner.LaneDeadline, "start", classmethod(start)
    )

    with pytest.raises(AssayError) as caught:
        plan_jobs(lane_file, lane)

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert len(deadlines) == 1
    if expiration_phase == "context":
        assert not generation_started
        assert len(seen_deadlines) == 1
    else:
        assert generation_started
        assert len(seen_deadlines) == 2
        assert seen_deadlines[0] == seen_deadlines[1]
    assert all(callback.__self__ is deadlines[0] for callback in seen_deadlines)
    assert all(
        callback.__func__ is runner.LaneDeadline.remaining
        for callback in seen_deadlines
    )


def test_o16a_ii_an_unsupported_lane_returns_the_sentinel(git_repo: GitRepo, monkeypatch):
    lane_file, lane = _load(_seed(git_repo))
    monkeypatch.setattr(mutation, "collect_mutation_sites", lambda *_a, **_k: mutation.UNSUPPORTED)
    assert plan_jobs(lane_file, lane) == "UNSUPPORTED"


def test_o16a_iii_the_discovery_is_the_full_pre_shard_tuple(git_repo: GitRepo):
    toml = _seed(git_repo)
    lane_file, lane = _load(toml)
    adapter = cli._resolve_declared_adapters(lane)
    discovered = cli._discover_plan_jobs(
        lane_file,
        lane,
        adapter=adapter,
        base_declaration=runner.resolve_base_declaration(lane, None),
        operators=lane.judge.mutation.operators,
        allow_dirty=False,
        resolve_reuse_command=False,
    )
    ids = [mutation.candidate_id(job) for job in discovered.jobs]
    index = next(
        candidate
        for candidate in (0, 1)
        if len(mutation.select_mutation_shard(ids, index=candidate, count=2)) < len(ids)
    )
    shorter = len(mutation.select_mutation_shard(ids, index=index, count=2))
    assert len(discovered.jobs) == len(ids)
    assert [row["id"] for row in plan_jobs(lane_file, lane)] == ids
    payload = _plan_payload(toml, "--shard", f"{index}/2")
    assert payload["candidate_count"] == shorter < len(ids)
    assert discovered.commit == git_repo.head()
    assert discovered.tree == git_repo.git("rev-parse", "HEAD^{tree}").strip()
    assert discovered.reuse_command_plan is None and discovered.reuse_command_cwd is None


def test_a_file_source_root_keeps_same_directory_siblings_out_of_the_plan(
    git_repo: GitRepo,
):
    git_repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    git_repo.write("src/shared/owned.py", "def owned(a, b):\n    return 0\n")
    git_repo.write("src/shared/other_package.py", "def other(a, b):\n    return 0\n")
    base = git_repo.commit_all("seed both packages")
    git_repo.write(
        "src/shared/owned.py",
        "def owned(a, b):\n    return a > 0 and b < 1\n",
    )
    git_repo.write(
        "src/shared/other_package.py",
        "def other(a, b):\n    return a > 0 and b < 1\n",
    )
    git_repo.commit_all("change both packages")
    toml = git_repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/shared/owned.py"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 20
operators = ["python:compare-swap"]
""",
    )
    git_repo.commit_all("configure exact file scope")

    payload = _plan_payload(toml)

    assert payload["by_file"] == {"src/shared/owned.py": 2}


def test_plan_refuses_an_ignored_untracked_exact_file_source_root(
    git_repo: GitRepo,
):
    git_repo.write(".gitignore", "__pycache__/\nsrc/owned.py\n")
    git_repo.write("src/other.py", "def other():\n    return 0\n")
    toml = git_repo.write(
        "assay.toml",
        """\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/true"]
env = {}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/owned.py"]
base = "HEAD^"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 20
operators = ["python:compare-swap"]
""",
    )
    git_repo.commit_all("seed lane with an exact file root")
    git_repo.write("src/other.py", "def other():\n    return 1\n")
    git_repo.commit_all("change a sibling source file")
    git_repo.write("src/owned.py", "def owned():\n    return True\n")
    lane_file, lane = _load(toml)

    with pytest.raises(AssayError) as caught:
        plan_jobs(lane_file, lane)
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["plan", "--json", "package", "--file", str(toml)],
        stdout=out,
        stderr=err,
    )
    assert code == Outcome.ERROR.exit_code
    assert out.getvalue() == ""
    assert "exact-file source root 'src/owned.py'" in err.getvalue()


def test_o16a_iv_a_lane_without_an_adapter_raises(git_repo: GitRepo, monkeypatch):
    lane_file, lane = _load(_seed(git_repo))
    monkeypatch.setattr(cli, "_resolve_declared_adapters", lambda _lane: None)
    with pytest.raises(LaneConfigError, match="resolves no mutation adapter"):
        plan_jobs(lane_file, lane)


def test_o16a_iv_a_non_r2_lane_raises(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    with pytest.raises(LaneConfigError, match="does not declare an R2 mutation judge"):
        plan_jobs(lane_file, dataclasses.replace(lane, rigor=("R0",)))


def test_o16a_iv_a_lane_without_a_mutation_judge_raises(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    with pytest.raises(LaneConfigError, match="does not declare an R2 mutation judge"):
        plan_jobs(lane_file, dataclasses.replace(lane, judge=None))


def test_o16a_v_the_public_names_are_the_judge_functions_themselves():
    assert cli.resolve_declared_adapters is cli._resolve_declared_adapters
    assert runner.resolve_declared_base is runner._resolve_declared_base
    assert mutation.execution_from_state_record is mutation._execution_from_state_record
    assert mutation.valid_hung_resource_evidence is mutation._valid_hung_resource_evidence
    assert cli.__all__ == ["build_parser", "main", "plan_jobs", "resolve_declared_adapters"]
    assert "resolve_declared_base" in runner.__all__
    assert {
        "candidate_identity_fields",
        "execution_from_state_record",
        "valid_hung_resource_evidence",
    } <= set(mutation.__all__)


def test_o16a_vi_digests_come_from_the_judged_commit_never_the_dirty_worktree(git_repo: GitRepo):
    lane_file, lane = _load(_seed(git_repo))
    git_repo.write("src/mod.py", _SOURCE + "# uncommitted edit\n")
    rows = plan_jobs(lane_file, lane, allow_dirty=True)
    committed = subprocess.check_output(["git", "-C", str(git_repo.path), "show", "HEAD:src/mod.py"])
    assert {row["source_sha256"] for row in rows} == {hashlib.sha256(committed).hexdigest()}
    with pytest.raises(AssayError) as caught:
        plan_jobs(lane_file, lane)
    assert caught.value.reason_code is ReasonCode.DIRTY_TREE


def test_o16a_vii_allow_dirty_reaches_the_integrity_probe(git_repo: GitRepo):
    toml = _seed(git_repo)
    git_repo.write("notes.txt", "two\n")
    payload = _plan_payload(toml, "--allow-dirty")
    assert payload["worktree_integrity"]["overridden_dirty_paths"] == ["notes.txt"]


def _v15_prior(tmp_path: Path) -> Path:
    """A current-schema verdict with killed candidates, built from r2_pass."""
    from conftest import TESTS_ROOT

    from assay.verify import verify_document

    document = json.loads((TESTS_ROOT / "fixtures" / "verdicts" / "r2_pass.json").read_text("utf-8"))
    document["schema_version"] = VERDICT_SCHEMA_VERSION
    body = document["claims"][1]["mutation"]
    ids = []
    for index, item in enumerate(body["killed"]):
        source = hashlib.sha256(f"source-{index}".encode()).hexdigest()
        mutated = hashlib.sha256(f"mutated-{index}".encode()).hexdigest()
        identifier = candidate_id_from_fields(
            path=item["path"],
            source_sha256=source,
            start_byte=item["start_byte"],
            end_byte=item["end_byte"],
            mutated_file_sha256=mutated,
            operator=item["operator"],
        )
        item.update(
            {
                "candidate_id": identifier,
                "source_sha256": source,
                "mutated_file_sha256": mutated,
                "execution": {
                    "mode": "full",
                    "witness": {
                        "node_id": "tests/test_checks.py::test_boundary",
                        "when": "call",
                        "outcome": "failed",
                        "session_exit_status": 1,
                        "process_exit_status": 1,
                    },
                },
            }
        )
        ids.append(identifier)
    body["candidate_ids"] = ids
    assert verify_document(document) == []
    prior = tmp_path / "prior-v15.json"
    prior.write_text(json.dumps(document), encoding="utf-8")
    return prior


def test_o16a_viii_the_reuse_command_is_resolved_only_when_reuse_is_requested(
    git_repo: GitRepo, tmp_path: Path
):
    toml = _seed(git_repo, argv=_SEQUENTIAL_ARGV)
    payload = _plan_payload(toml, "--reuse-from", str(_v15_prior(tmp_path)))
    assert payload["reuse_from"]["sequential_pytest_supported"] is True
    plain = _plan_payload(toml)
    assert "reuse_from" not in plain
    assert all("reuse" not in row for row in plain["candidates"])
    assert all(set(row) == ROW_KEYS for row in plain["candidates"])
