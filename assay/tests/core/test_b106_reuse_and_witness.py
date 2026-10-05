"""B106's serialized evidence and current-run pytest witness behavior."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    TESTS_ROOT,
    make_lane,
    make_r2_judge,
    native_mutation,
    native_outcome,
    zero_resource_limit_evidence_dict,
)

from assay import candidate_identity, mutation, runner, verdict
from assay.adapters.python import PythonAdapter
from assay.candidate_identity import candidate_id_from_fields
from assay.config import MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Mutation, MutationExecution, MutationWitnessReceipt
from assay.mutation_witness import (
    MAX_INTERNAL_RECEIPT_BYTES,
    read_internal_receipt,
    replay_witness_from_receipt,
    supports_sequential_pytest,
)
from assay.reuse import classify_candidate, load_reuse_source, prior_only_candidates
from assay import verify as raw_verify
from assay.verify import verify_document

FIXTURES = TESTS_ROOT / "fixtures" / "verdicts"


def _v14_killed_source() -> dict:
    document = json.loads((FIXTURES / "r2_pass.json").read_text(encoding="utf-8"))
    document["schema_version"] = 14
    mutation = document["claims"][1]["mutation"]
    candidate_ids = []
    for index, item in enumerate(mutation["killed"]):
        source_digest = hashlib.sha256(f"source-{index}".encode()).hexdigest()
        mutated_digest = hashlib.sha256(f"mutated-{index}".encode()).hexdigest()
        candidate = candidate_id_from_fields(
            path=item["path"],
            source_sha256=source_digest,
            start_byte=item["start_byte"],
            end_byte=item["end_byte"],
            mutated_file_sha256=mutated_digest,
            operator=item["operator"],
        )
        item.update(
            {
                "candidate_id": candidate,
                "source_sha256": source_digest,
                "mutated_file_sha256": mutated_digest,
                "resource_limit_evidence": zero_resource_limit_evidence_dict(),
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
        candidate_ids.append(candidate)
    mutation["candidate_ids"] = candidate_ids
    assert verify_document(document) == []
    return document


@pytest.mark.parametrize("version", [12, 13])
def test_v12_and_v13_are_bounded_cold_starts_without_candidate_inspection(
    tmp_path: Path, version: int
):
    path = tmp_path / "old.json"
    path.write_text(
        f'{{"schema_version":{version},"claims":"not inspected",'
        '"mutation":{"candidate_ids":["invented"]}}',
        encoding="utf-8",
    )

    source = load_reuse_source(path)

    assert source.cold_start is True
    assert source.document is None
    assert source.complete_unsharded_native is False
    assert source.candidate_ids == frozenset()
    assert source.outcomes == {}
    assert verify_document(json.loads(path.read_text()))[0].startswith(
        f"schema_version {version} is not this verifier's version 14"
    )


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        ('{"schema_version":14,"schema_version":14}', "duplicate object key"),
        ('{"schema_version":15}', "unsupported"),
        ('{"schema_version":true}', "must be an integer"),
        ('{"schema_version":NaN}', "invalid JSON"),
    ],
)
def test_reuse_source_rejects_unreadable_or_foreign_envelopes(
    tmp_path: Path, content: str, detail: str
):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AssayError) as caught:
        load_reuse_source(path)

    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert detail in str(caught.value)


def test_complete_v14_campaign_exposes_only_killed_witnesses(tmp_path: Path):
    document = _v14_killed_source()
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    source = load_reuse_source(path)

    assert source.complete_unsharded_native is True
    assert len(source.candidate_ids) == 2
    eligible = sorted(source.candidate_ids)
    assert classify_candidate(
        source, eligible[0], sequential_pytest_supported=True
    ) == ("witness-replay", "tests/test_checks.py::test_boundary")
    assert classify_candidate(
        source, "0" * 64, sequential_pytest_supported=True
    ) == ("new-candidate", None)
    assert prior_only_candidates(source, [eligible[0]]) == [eligible[1]]


def test_current_v14_verifier_rejects_incomplete_candidate_inventory():
    document = _v14_killed_source()
    document["claims"][1]["mutation"]["candidate_ids"].pop()

    failures = verify_document(document)

    assert any("candidate_ids does not equal" in failure for failure in failures)


def _raw_failures(function, *args):
    failures = []
    function(*args, failures)
    return failures


def test_raw_b106_execution_checks_full_and_witness_prefix_modes():
    assert _raw_failures(raw_verify._check_b106_execution, "killed", {"mode": "full"}) == []
    assert any("execution must be an object" in item for item in _raw_failures(
        raw_verify._check_b106_execution, "killed", None
    ))
    assert any("witness-prefix fields" in item for item in _raw_failures(
        raw_verify._check_b106_execution, "killed", {"mode": "full", "prior_node_id": "x"}
    ))
    assert any("kill witness" in item for item in _raw_failures(
        raw_verify._check_b106_execution,
        "survived",
        {"mode": "full", "witness": {"node_id": "x"}},
    ))
    assert any("unknown execution mode" in item for item in _raw_failures(
        raw_verify._check_b106_execution, "killed", {"mode": "partial"}
    ))

    receipt = {
        "node_id": "tests/test_checks.py::test_boundary",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    prefix = {
        "mode": "witness-prefix",
        "witness": receipt,
        "prior_verdict_sha256": "a" * 64,
        "prior_node_id": receipt["node_id"],
        "current_node_id": receipt["node_id"],
    }
    assert _raw_failures(raw_verify._check_b106_execution, "killed", prefix) == []
    malformed = {**prefix, "extra": True}
    assert any("exactly its prior digest" in item for item in _raw_failures(
        raw_verify._check_b106_execution, "killed", malformed
    ))
    assert any("only a killed" in item for item in _raw_failures(
        raw_verify._check_b106_execution, "survived", prefix
    ))
    assert any("digest is malformed" in item for item in _raw_failures(
        raw_verify._check_b106_execution,
        "killed",
        {**prefix, "prior_verdict_sha256": "z" * 64},
    ))
    assert any("node ID is malformed" in item for item in _raw_failures(
        raw_verify._check_b106_execution,
        "killed",
        {**prefix, "current_node_id": "bad\ud800"},
    ))
    assert any("must be identical" in item for item in _raw_failures(
        raw_verify._check_b106_execution,
        "killed",
        {**prefix, "current_node_id": "other"},
    ))


def test_raw_b106_receipt_refuses_malformed_and_non_killed_witnesses():
    receipt = {
        "node_id": "tests/test_checks.py::test_boundary",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    assert _raw_failures(raw_verify._check_b106_receipt, receipt, "killed") == []
    assert any("must be an object" in item for item in _raw_failures(
        raw_verify._check_b106_receipt, None, "killed"
    ))
    invalid = {**receipt, "extra": True, "node_id": "bad\ud800", "when": "setup", "session_exit_status": True}
    failures = _raw_failures(raw_verify._check_b106_receipt, invalid, "survived")
    assert any("missing or unknown fields" in item for item in failures)
    assert any("node ID is malformed" in item for item in failures)
    assert any("failed call-phase" in item for item in failures)
    assert any("session_exit_status must equal 1" in item for item in failures)
    assert any("legal only on a killed" in item for item in failures)


def test_raw_b106_mutation_provenance_accepts_a_complete_native_record():
    document = _v14_killed_source()

    assert _raw_failures(raw_verify._check_b106_mutation_provenance, document) == []


def test_raw_b106_mutation_provenance_checks_inventory_and_entry_binding():
    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    mutation["candidate_ids"] = ["bad"]
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("malformed digest" in item for item in failures)
    assert any("does not equal" in item for item in failures)

    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    mutation["killed"][0]["source_sha256"] = "bad"
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("malformed B106 digest" in item for item in failures)

    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    mutation["killed"][0]["candidate_id"] = "f" * 64
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("does not match its recorded identity inputs" in item for item in failures)

    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    mutation["killed"][0]["execution"] = {}
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("unknown execution mode" in item for item in failures)


def test_raw_b106_mutation_provenance_rejects_duplicate_inventory_and_outcome_ids(
    monkeypatch,
):
    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    mutation["candidate_ids"] = [mutation["candidate_ids"][0]] * 2
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("candidate_ids contains a duplicate" in item for item in failures)

    document = _v14_killed_source()
    mutation = document["claims"][1]["mutation"]
    same_candidate = "c" * 64
    monkeypatch.setattr(raw_verify, "candidate_id_from_fields", lambda **_kwargs: same_candidate)
    for entry in mutation["killed"]:
        entry["candidate_id"] = same_candidate
    mutation["candidate_ids"] = [same_candidate]
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("outcomes contain a duplicate candidate ID" in item for item in failures)


def test_raw_b106_mutation_provenance_handles_ingested_and_limit_sentinel_shapes():
    document = _v14_killed_source()
    document["judgment"]["r2"]["producer"] = "ingested"
    document["claims"][1]["mutation"]["candidate_ids"] = []
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, document)
    assert any("ingested mutation payload carries the native candidate inventory" in item for item in failures)

    sentinel = {
        "claims": [{
            "rigor": "R2",
            "reason_code": "MUTANT_LIMIT_EXCEEDED",
            "mutation": {"candidate_count": 51, "total": 0, "candidate_ids": []},
        }],
        "judgment": {"r2": {"producer": "native", "max_mutants": 50}},
    }
    failures = _raw_failures(raw_verify._check_b106_mutation_provenance, sentinel)
    assert any("pre-submission mutant-limit sentinel carries candidate_ids" in item for item in failures)


def test_raw_node_id_bound_rejects_unencodable_text():
    assert raw_verify._is_bounded_node_id("tests/test_x.py::test_x")
    assert not raw_verify._is_bounded_node_id("bad\ud800")


def _valid_mutation_witness(node_id: str = "tests/test_checks.py::test_boundary"):
    return MutationWitnessReceipt(
        node_id=node_id,
        when="call",
        outcome="failed",
        session_exit_status=1,
        process_exit_status=1,
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"node_id": ""}, "node_id must be a non-empty string"),
        ({"node_id": "x" * 4097}, "node_id exceeds 4096"),
        ({"when": "setup"}, "failed call-phase"),
        ({"outcome": "passed"}, "failed call-phase"),
        ({"session_exit_status": True}, "session_exit_status must equal 1"),
        ({"process_exit_status": 0}, "process_exit_status must equal 1"),
    ],
)
def test_mutation_witness_receipt_rejects_non_witness_facts(changes, message):
    fields = {
        "node_id": "tests/test_checks.py::test_boundary",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    fields.update(changes)

    with pytest.raises(ValueError, match=message):
        MutationWitnessReceipt(**fields)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"mode": "partial"}, "mode must be"),
        ({"mode": "full", "witness": "receipt"}, "witness has the wrong type"),
        ({"mode": "full", "prior_node_id": "test::node"}, "full execution cannot"),
        ({"mode": "witness-prefix"}, "requires a witness receipt"),
        (
            {
                "mode": "witness-prefix",
                "witness": _valid_mutation_witness(),
                "prior_verdict_sha256": "x" * 64,
                "prior_node_id": "same",
                "current_node_id": "same",
            },
            "prior_verdict_sha256 must be a SHA-256",
        ),
        (
            {
                "mode": "witness-prefix",
                "witness": _valid_mutation_witness(),
                "prior_verdict_sha256": "a" * 64,
                "prior_node_id": "",
                "current_node_id": "",
            },
            "prior_node_id must be a non-empty string",
        ),
        (
            {
                "mode": "witness-prefix",
                "witness": _valid_mutation_witness(),
                "prior_verdict_sha256": "a" * 64,
                "prior_node_id": "x" * 4097,
                "current_node_id": "same",
            },
            "prior_node_id exceeds 4096",
        ),
        (
            {
                "mode": "witness-prefix",
                "witness": _valid_mutation_witness("same"),
                "prior_verdict_sha256": "a" * 64,
                "prior_node_id": "prior",
                "current_node_id": "current",
            },
            "prior_node_id and current_node_id must match",
        ),
        (
            {
                "mode": "witness-prefix",
                "witness": _valid_mutation_witness("receipt-node"),
                "prior_verdict_sha256": "a" * 64,
                "prior_node_id": "current",
                "current_node_id": "current",
            },
            "receipt node_id must match current_node_id",
        ),
    ],
)
def test_mutation_execution_rejects_incomplete_or_mismatched_provenance(
    fields, message
):
    with pytest.raises(ValueError, match=message):
        MutationExecution(**fields)


def _valid_native_outcome(path: str = "a.py"):
    return native_outcome(
        path=path,
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="b" * 64,
        operator="python:compare-swap",
        description="changed comparison",
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"discard_reason": "invented"}, "discard_reason must be one of"),
        ({"candidate_id": None}, "fields must be present together"),
        ({"source_sha256": "bad"}, "source_sha256 must be a lowercase SHA-256"),
        ({"execution": "full"}, "execution has the wrong type"),
    ],
)
def test_native_mutant_outcome_requires_a_complete_valid_execution_identity(
    changes, message
):
    with pytest.raises(ValueError, match=message):
        replace(_valid_native_outcome(), **changes)


def test_mutation_rejects_witness_execution_outside_the_killed_bucket():
    outcome = replace(
        _valid_native_outcome(),
        execution=MutationExecution(mode="full", witness=_valid_mutation_witness()),
    )

    with pytest.raises(ValueError, match="cannot carry a kill witness"):
        native_mutation(
            candidate_count=1,
            total=1,
            survived=(outcome,),
        )


def test_mutation_rejects_duplicate_candidate_ids_across_distinct_outcomes(
    monkeypatch,
):
    constant_identity = lambda **_kwargs: "c" * 64
    monkeypatch.setattr(candidate_identity, "candidate_id_from_fields", constant_identity)
    monkeypatch.setattr(verdict, "candidate_id_from_fields", constant_identity)
    killed = _valid_native_outcome("a.py")
    survived = _valid_native_outcome("b.py")

    with pytest.raises(ValueError, match="candidate_id .* appears in both"):
        Mutation(
            candidate_count=2,
            total=2,
            killed=(killed,),
            survived=(survived,),
            candidate_ids=None,
        )


def test_internal_witness_receipts_are_bounded_strict_and_not_boolean_statuses(
    tmp_path: Path,
):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text('{"session_exit_status":1,"session_exit_status":1}')
    assert read_internal_receipt(receipt_path) is None

    receipt_path.write_bytes(b" " * (MAX_INTERNAL_RECEIPT_BYTES + 1))
    assert read_internal_receipt(receipt_path) is None

    receipt = {
        "unsupported": False,
        "target_node_id": "tests/test_x.py::test_x",
        "target_count": True,
        "earlier_failure": False,
        "auxiliary_failure": False,
        "witness_node_id": "tests/test_x.py::test_x",
        "witness_when": "call",
        "witness_outcome": "failed",
        "session_exit_status": 1,
        "stopped_at_target": True,
    }
    assert replay_witness_from_receipt(
        receipt,
        process_exit_status=1,
        target_node_id="tests/test_x.py::test_x",
    ) is None

    receipt["unexpected"] = "not accepted"
    assert replay_witness_from_receipt(
        receipt,
        process_exit_status=1,
        target_node_id="tests/test_x.py::test_x",
    ) is None


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "tests", "-q"),
        ("/venv/bin/pytest", "tests", "-q"),
        (sys.executable, "-m", "pytest", "tests", "-q"),
    ],
)
def test_direct_pytest_command_is_replayable(argv: tuple[str, ...]):
    assert supports_sequential_pytest(argv, env={})


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "-n", "2", "tests"),
        ("pytest", "-n2", "tests"),
        ("pytest", "--numprocesses=2", "tests"),
        ("pytest", "--dist", "load", "tests"),
        ("python", "-c", "import pytest", "-m", "pytest"),
        ("sh", "-c", "pytest tests"),
    ],
)
def test_parallel_or_wrapped_pytest_command_is_not_replayable(
    argv: tuple[str, ...],
):
    assert not supports_sequential_pytest(argv, env={})


def test_pytest_xdist_from_ini_addopts_is_not_plan_replayable(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -n 2\n",
        encoding="utf-8",
    )

    assert not supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"),
        cwd=tmp_path,
        env={},
    )


def test_pytest_parallelism_from_effective_environment_is_not_replayable():
    assert not supports_sequential_pytest(
        (sys.executable, "-m", "pytest", "tests"),
        env={"PYTEST_ADDOPTS": "-n 2"},
    )


def test_plan_refuses_to_preview_witness_reuse_when_pytest_ini_enables_xdist(
    tmp_path: Path, monkeypatch
):
    from assay.cli import main

    project = tmp_path / "project"
    project.mkdir()
    repo = GitRepo(path=project)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    repo.write(
        "assay.toml",
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "tests", "-q"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
""",
    )
    repo.write("pytest.ini", "[pytest]\naddopts = -n 2\n")
    repo.commit_all("seed plan project")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    repo.write("tests/test_behavior.py", "def test_flag():\n    assert True\n")
    head = repo.commit_all("add current candidate")
    assert repo.head() == head

    prior = tmp_path / "prior-v14.json"
    prior.write_text(json.dumps(_v14_killed_source()), encoding="utf-8")
    out = io.StringIO()
    exit_code = main(
        [
            "plan",
            "package",
            "--file",
            str(project / "assay.toml"),
            "--reuse-from",
            str(prior),
        ],
        stdout=out,
    )

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["reuse_from"]["sequential_pytest_supported"] is False
    assert payload["reuse_from"]["classification_counts"] == {"new-candidate": 1}

    def command_plan_unavailable(*_args, **_kwargs):
        raise AssayError(
            "the effective plan cannot be resolved in a preview",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )

    monkeypatch.setattr(runner, "resolve_command_plan", command_plan_unavailable)
    out = io.StringIO()
    exit_code = main(
        [
            "plan",
            "package",
            "--file",
            str(project / "assay.toml"),
            "--reuse-from",
            str(prior),
        ],
        stdout=out,
    )
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["reuse_from"]["sequential_pytest_supported"] is False

    monkeypatch.setattr(mutation, "collect_mutation_sites", lambda *_a, **_k: mutation.UNSUPPORTED)
    out = io.StringIO()
    exit_code = main(
        [
            "plan",
            "package",
            "--file",
            str(project / "assay.toml"),
            "--reuse-from",
            str(prior),
        ],
        stdout=out,
    )
    assert exit_code == 0
    assert json.loads(out.getvalue())["status"] == "unsupported"


def _seed_pytest_mutation(repo: GitRepo) -> tuple[str, str]:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n"
        "    assert not flag(0)\n",
    )
    base = repo.commit_all("seed test project")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    head = repo.commit_all("add one compare-swap mutation site")
    return base, head


def test_replay_requires_a_current_kill_and_falls_back_to_a_full_run(
    git_repo: GitRepo, tmp_path: Path
):
    base, first_head = _seed_pytest_mutation(git_repo)
    mutation = MutationConfig(
        jobs=1,
        max_mutants=10,
        operators=("python:compare-swap",),
        liveness="false",
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=mutation,
        ),
        budget="2m",
        budget_seconds=120,
    )

    original = runner.run_lane(
        lane,
        commit=first_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert original.outcome is Outcome.PASS
    first_outcome = original.claims[1].mutation.killed[0]
    assert first_outcome.execution.mode == "full"
    assert first_outcome.execution.witness.node_id == "tests/test_behavior.py::test_behavior"
    assert verify_document(original.to_dict()) == []
    prior = tmp_path / "prior-v14.json"
    prior.write_text(json.dumps(original.to_dict()), encoding="utf-8")

    git_repo.write(
        "tests/test_a_pass_before_witness.py",
        "def test_before_witness():\n    assert 2 + 2 == 4\n",
    )
    second_head = git_repo.commit_all("add a passing test before the witness")
    replayed = runner.run_lane(
        lane,
        commit=second_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        reuse_from=prior,
    )

    assert replayed.outcome is Outcome.PASS
    replay = replayed.claims[1].mutation.killed[0].execution
    assert replay.mode == "witness-prefix"
    assert replay.prior_node_id == replay.current_node_id
    assert replay.witness.node_id == "tests/test_behavior.py::test_behavior"
    assert verify_document(replayed.to_dict()) == []

    git_repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n",
    )
    third_head = git_repo.commit_all("remove the assertion that killed the mutant")
    calls: list[tuple[str, ...]] = []

    def tracked_process_runner(argv, *, env, cwd, timeout):
        calls.append(tuple(argv))
        return runner.default_process_runner(argv, env=env, cwd=cwd, timeout=timeout)

    stale_witness = runner.run_lane(
        lane,
        commit=third_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=tracked_process_runner,
        reuse_from=prior,
    )

    assert stale_witness.outcome is Outcome.FAIL
    assert len(calls) == 3, (
        stale_witness.outcome,
        [
            (
                claim.rigor,
                claim.status,
                claim.reason_code,
                None if claim.mutation is None else claim.mutation.total,
            )
            for claim in stale_witness.claims
        ],
        calls,
    )
    assert stale_witness.claims[1].mutation.survived
    assert stale_witness.claims[1].mutation.survived[0].execution.mode == "full"
    assert verify_document(stale_witness.to_dict()) == []


def test_witness_capture_works_with_the_existing_liveness_plugin(git_repo: GitRepo):
    base, head = _seed_pytest_mutation(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    assert verdict.judgment.r2.liveness["active"] is True
    killed = verdict.claims[1].mutation.killed[0]
    assert killed.execution.witness.node_id == "tests/test_behavior.py::test_behavior"


def test_custom_sessionfinish_hook_forces_full_suite_fallback(
    git_repo: GitRepo, tmp_path: Path
):
    base, first_head = _seed_pytest_mutation(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
                liveness="false",
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )
    original = runner.run_lane(
        lane,
        commit=first_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )
    assert original.outcome is Outcome.PASS
    assert original.claims[1].mutation.killed[0].execution.witness is not None
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps(original.to_dict()), encoding="utf-8")
    prior_source = load_reuse_source(prior)
    assert prior_source.complete_unsharded_native is True
    assert original.claims[1].mutation.killed[0].candidate_id in prior_source.candidate_ids

    git_repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n",
    )
    git_repo.write(
        "tests/conftest.py",
        "import importlib.util\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "def pytest_configure(config):\n"
        "    module_name = 'assay_mutation_witness_plugin_extra'\n"
        "    module_path = Path(__file__).with_name(module_name + '.py')\n"
        "    spec = importlib.util.spec_from_file_location(module_name, module_path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[module_name] = module\n"
        "    spec.loader.exec_module(module)\n"
        "    config.pluginmanager.register(module, name=module_name)\n",
    )
    git_repo.write(
        "tests/assay_mutation_witness_plugin_extra.py",
        "import os\n"
        "import pytest\n\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_runtest_logreport(report):\n"
        "    target = os.environ.get('ASSAY_MUTATION_WITNESS_TARGET')\n"
        "    if (target and report.nodeid == target and report.when == 'call'\n"
        "            and report.outcome == 'passed'):\n"
        "        report.outcome = 'failed'\n\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    if os.environ.get('ASSAY_MUTATION_WITNESS_TARGET'):\n"
        "        session.exitstatus = 1\n",
    )
    second_head = git_repo.commit_all(
        "add a lookalike witness plugin and weaken the test"
    )
    observed_targets: list[str | None] = []
    observed_commands: list[tuple[str, ...]] = []

    def tracked_process_runner(argv, *, env, cwd, timeout):
        observed_targets.append(env.get("ASSAY_MUTATION_WITNESS_TARGET"))
        observed_commands.append(tuple(argv))
        return runner.default_process_runner(argv, env=env, cwd=cwd, timeout=timeout)

    current = runner.run_lane(
        lane,
        commit=second_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=tracked_process_runner,
        reuse_from=prior,
    )

    assert current.outcome is Outcome.FAIL
    assert current.claims[1].mutation.survived
    assert current.claims[1].mutation.survived[0].execution.mode == "full"
    assert observed_targets.count("tests/test_behavior.py::test_behavior") == 1, (
        observed_targets,
        observed_commands,
    )
    assert verify_document(current.to_dict()) == []


def test_resume_preserves_witness_prefix_execution_provenance(
    git_repo: GitRepo, tmp_path: Path
):
    base, first_head = _seed_pytest_mutation(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
                liveness="false",
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )
    state_dir = tmp_path / "state"
    original = runner.run_lane(
        lane,
        commit=first_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        resume=True,
        state_dir=state_dir,
    )
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps(original.to_dict()), encoding="utf-8")
    git_repo.write(
        "tests/test_a_pass_before_witness.py",
        "def test_before_witness():\n    assert 2 + 2 == 4\n",
    )
    second_head = git_repo.commit_all("add a passing test before the witness")
    replayed = runner.run_lane(
        lane,
        commit=second_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        resume=True,
        state_dir=state_dir,
        reuse_from=prior,
    )
    assert replayed.outcome is Outcome.PASS
    assert replayed.claims[1].mutation.killed[0].execution.mode == "witness-prefix"

    calls: list[tuple[str, ...]] = []

    def tracked_process_runner(argv, *, env, cwd, timeout):
        calls.append(tuple(argv))
        return runner.default_process_runner(argv, env=env, cwd=cwd, timeout=timeout)

    resumed = runner.run_lane(
        lane,
        commit=second_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=tracked_process_runner,
        resume=True,
        state_dir=state_dir,
    )
    assert resumed.outcome is Outcome.PASS
    assert resumed.claims[1].mutation.killed[0].execution.mode == "witness-prefix"
    assert verify_document(resumed.to_dict()) == []
    assert len(calls) == 1  # The prior candidate resumed; only current baseline ran.


def test_rejudge_outcome_disables_prior_witness_replay(
    git_repo: GitRepo, tmp_path: Path
):
    base, first_head = _seed_pytest_mutation(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
                liveness="false",
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )
    state_dir = tmp_path / "state"
    original = runner.run_lane(
        lane,
        commit=first_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        resume=True,
        state_dir=state_dir,
    )
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps(original.to_dict()), encoding="utf-8")
    record_path = next(state_dir.glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["outcome_bucket"] = "hung"
    record["execution"] = {"mode": "full"}
    resource_snapshot = {
        "schema_version": 1,
        "status": "available",
        "cgroup_identity": "fixture-cgroup",
        "host_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
    }
    record["liveness_resource_evidence"] = {
        "schema_version": 1,
        "policy": "pressure-adjusted-idle-v1",
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": 31.0,
        "eligible_elapsed_s": 31.0,
        "idle_eligible_s": 31.0,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": 30.0,
        "required_cpu_growth_floor_s": 1.0,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "samples": [
            {
                "wall_elapsed_s": float(elapsed),
                "eligible_elapsed_s": float(elapsed),
                "eligible_interval_s": 0.0 if elapsed == 0 else 1.0,
                "candidate_cpu_s": 3.0,
                "event_count": 0,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "resource_interval": "unknown" if elapsed == 0 else "clear",
                "resource_deltas": {},
                **({"previous_resources": None} if elapsed == 0 else {}),
                "resources": resource_snapshot,
            }
            for elapsed in range(32)
        ],
    }
    record_path.write_text(json.dumps(record), encoding="utf-8")

    observed_targets: list[str | None] = []

    def tracked_process_runner(argv, *, env, cwd, timeout):
        observed_targets.append(env.get("ASSAY_MUTATION_WITNESS_TARGET"))
        return runner.default_process_runner(argv, env=env, cwd=cwd, timeout=timeout)

    rejudged = runner.run_lane(
        lane,
        commit=first_head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=tracked_process_runner,
        resume=True,
        state_dir=state_dir,
        rejudge_outcome="hung",
        reuse_from=prior,
    )

    assert rejudged.outcome is Outcome.PASS
    assert rejudged.claims[1].mutation.killed[0].execution.mode == "full"
    assert "tests/test_behavior.py::test_behavior" not in observed_targets
    assert verify_document(rejudged.to_dict()) == []
