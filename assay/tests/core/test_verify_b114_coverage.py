"""Public raw-verifier regressions for v15 native R2 witness bindings."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest
from conftest import PROJECT_ROOT

from assay.r2_command import R2_APPENDED, R2_TRANSFORM_ID, transform_argv
from assay.verdict import R2BaselineFacts, R2Command
from assay.verify import verify_document


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
NODE = "tests/test_pkg.py::test_case"


def _fixture(name: str) -> dict:
    document = json.loads(
        (PROJECT_ROOT / "tests/fixtures/verdicts" / name).read_text(encoding="utf-8")
    )
    assert verify_document(document) == [], name
    return document


def _r2_mutation(document: dict) -> dict:
    return next(claim for claim in document["claims"] if claim["rigor"] == "R2")["mutation"]


def _receipt() -> dict:
    return {
        "node_id": NODE,
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }


def _baseline(*, r2: bool) -> R2BaselineFacts:
    return R2BaselineFacts(
        collection_count=1,
        collection_sha256=SHA_A,
        duplicates=0,
        hook_fingerprint_sha256=SHA_B,
        hook_count=1,
        runtime_fingerprint_sha256=SHA_C,
        wall_s=0.25 if r2 else None,
    )


def _cold_command(document: dict) -> dict:
    argv = tuple(document["argv_declared"])
    return R2Command(
        transform=R2_TRANSFORM_ID,
        argv_declared=argv,
        argv_transformed=transform_argv(argv),
        appended=R2_APPENDED,
        cwd=".",
        config_sha256=None,
        coverage_baseline=_baseline(r2=False),
        r2_baseline=_baseline(r2=True),
    ).to_dict()


def _evidence(*, command: str, prefix: bool) -> dict:
    return {
        "command": command,
        "collection_count": 1,
        "collection_sha256": SHA_A,
        "hook_fingerprint_sha256": SHA_B,
        "started_count": 1 if prefix else None,
        "failed_call_index": 0 if prefix else None,
    }


def _cold_document(*, survivor: bool = False) -> dict:
    document = _fixture(
        "r2_fail_mutants_survived.json" if survivor else "r2_pass.json"
    )
    policy = document["judgment"]["r2"]
    policy["cold_witness_kills"] = True
    policy["r2_command"] = _cold_command(document)
    mutation = _r2_mutation(document)
    for entry in mutation["killed"]:
        entry["execution"] = {"mode": "witness-cold", "witness": _receipt()}
        entry["evidence"] = _evidence(command="r2", prefix=True)
    for entry in mutation["survived"]:
        entry["evidence"] = _evidence(command="declared", prefix=False)
    assert verify_document(document) == []
    return document


def _full_kill_document() -> dict:
    document = _cold_document()
    for entry in _r2_mutation(document)["killed"]:
        entry["execution"] = {"mode": "full", "witness": _receipt()}
        entry["evidence"] = _evidence(command="declared", prefix=False)
    assert verify_document(document) == []
    return document


def _prefix_document() -> dict:
    document = _cold_document()
    for entry in _r2_mutation(document)["killed"]:
        entry["execution"] = {
            "mode": "witness-prefix",
            "witness": _receipt(),
            "prior_verdict_sha256": SHA_A,
            "prior_node_id": NODE,
            "current_node_id": NODE,
        }
        entry["evidence"] = _evidence(command="r2", prefix=False)
    assert verify_document(document) == []
    return document


def _must_refuse(control: dict, changed: dict, expected: str) -> None:
    assert verify_document(control) == [], "the positive control must verify"
    failures = verify_document(changed)
    assert any(expected in failure for failure in failures), failures


def test_valid_v15_legacy_cold_prefix_ledger_and_campaign_bindings_verify():
    legacy = _fixture("r2_pass.json")
    cold = _cold_document()
    prefix = _prefix_document()
    ledger = _fixture("r2_pass_equivalence_ledger.json")
    with_campaign = deepcopy(cold)
    with_campaign["campaign"] = {
        "name": "b110.phase-1",
        "deadline_sha256": SHA_C,
        "created_at_utc": "2026-10-07T00:00:00Z",
        "expires_at_utc": "2026-10-07T01:00:00Z",
    }
    for document in (legacy, cold, prefix, ledger, with_campaign):
        assert verify_document(document) == []


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("collection_count", True, "collection_count must be an integer >= 0"),
        ("collection_sha256", "wrong", "collection_sha256 must be a SHA-256"),
        ("hook_fingerprint_sha256", "wrong", "hook_fingerprint_sha256 must be a SHA-256"),
        ("started_count", None, "started_count and failed_call_index must appear together"),
        ("started_count", 0, "started_count must be an integer >= 1"),
        ("failed_call_index", True, "failed_call_index must be an integer"),
        ("failed_call_index", 1, "failed_call_index must end the started prefix"),
    ],
)
def test_cold_kill_evidence_refuses_false_collection_or_prefix_fact(field, value, diagnosis):
    control = _cold_document()
    changed = deepcopy(control)
    _r2_mutation(changed)["killed"][0]["evidence"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("command", "declared", "only R2 evidence may record a failed call prefix"),
        ("collection_count", 0, "started_count exceeds collection_count"),
        ("hook_fingerprint_sha256", SHA_C, "differs from r2_baseline"),
    ],
)
def test_cold_kill_evidence_cannot_claim_a_different_command_or_baseline(
    field, value, diagnosis
):
    control = _cold_document()
    changed = deepcopy(control)
    _r2_mutation(changed)["killed"][0]["evidence"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("command", "r2", "full kill evidence must use the 'declared' command"),
        ("started_count", 1, "full kill evidence cannot carry started-prefix facts"),
        ("collection_sha256", SHA_C, "differs from coverage_baseline"),
    ],
)
def test_full_kill_evidence_uses_the_declared_baseline_without_prefix(
    field, value, diagnosis
):
    control = _full_kill_document()
    changed = deepcopy(control)
    _r2_mutation(changed)["killed"][0]["evidence"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("command", "bad", "mutant evidence command must be 'r2' or 'declared'"),
        ("started_count", 1, "survivor evidence cannot carry started-prefix facts"),
        ("collection_count", 2, "differs from coverage_baseline"),
    ],
)
def test_survivor_evidence_cannot_borrow_a_kill_prefix_or_unsupported_collection(
    field, value, diagnosis
):
    control = _cold_document(survivor=True)
    changed = deepcopy(control)
    _r2_mutation(changed)["survived"][0]["evidence"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("prior_verdict_sha256", "wrong", "prior verdict digest is malformed"),
        ("prior_node_id", "wrong-node", "prior, current and receipt node IDs must be identical"),
        ("current_node_id", "wrong-node", "prior, current and receipt node IDs must be identical"),
    ],
)
def test_witness_prefix_execution_binds_prior_and_current_witness(
    field, value, diagnosis
):
    control = _prefix_document()
    changed = deepcopy(control)
    _r2_mutation(changed)["killed"][0]["execution"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("node_id", "", "mutation witness node ID is malformed or oversized"),
        ("when", "setup", "mutation witness must be a failed call-phase report"),
        ("outcome", "passed", "mutation witness must be a failed call-phase report"),
        ("session_exit_status", True, "mutation witness session_exit_status must equal 1"),
        ("process_exit_status", 0, "mutation witness process_exit_status must equal 1"),
    ],
)
def test_witness_receipt_refuses_nonfailed_call_or_untrusted_exit(field, value, diagnosis):
    control = _cold_document()
    changed = deepcopy(control)
    _r2_mutation(changed)["killed"][0]["execution"]["witness"][field] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("path", "value", "diagnosis"),
    [
        (("argv_transformed",), ["pytest"], "argv_transformed differs from declared transform"),
        (("appended",), ["-p"], "appended argv is not the pinned"),
        (("cwd",), "../tests", "cwd must be normalized and project-relative"),
        (("config_sha256",), "wrong", "config_sha256 must be a SHA-256"),
        (("coverage_baseline", "duplicates"), 1, "coverage_baseline.duplicates must equal 0"),
        (("coverage_baseline", "collection_count"), 2, "baseline collection_count values differ"),
        (("r2_baseline", "wall_s"), -0.1, "r2_baseline.wall_s must be finite"),
        (("r2_baseline", "hook_count"), True, "r2_baseline.hook_count must be a non-negative integer"),
    ],
)
def test_command_binding_refuses_unreproducible_transform_or_baseline(path, value, diagnosis):
    control = _cold_document()
    changed = deepcopy(control)
    current = changed["judgment"]["r2"]["r2_command"]
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value
    _must_refuse(control, changed, diagnosis)


@pytest.mark.parametrize(
    ("field", "value", "diagnosis"),
    [
        ("path", "../ledger.toml", "equivalence_ledger path is not normalized and relative"),
        ("sha256", "wrong", "equivalence_ledger digests must be SHA-256"),
        ("entry_count", 2, "entry_count differs from equivalent outcomes"),
        ("audit_sha256", "wrong", "equivalence_ledger digests must be SHA-256"),
    ],
)
def test_ledger_policy_requires_a_bounded_matching_audit(field, value, diagnosis):
    control = _fixture("r2_pass_equivalence_ledger.json")
    changed = deepcopy(control)
    changed["judgment"]["r2"]["equivalence_ledger"][field] = value
    _must_refuse(control, changed, diagnosis)


def test_ledger_cannot_accompany_legacy_equivalence_artifact():
    control = _fixture("r2_pass_equivalence_ledger.json")
    changed = deepcopy(control)
    changed["judgment"]["r2"]["equivalence_artifact"] = "audit.toml"
    _must_refuse(control, changed, "equivalence_ledger cannot accompany equivalence_artifact")


def test_ledger_outcome_cannot_claim_per_execution_resource_evidence():
    control = _fixture("r2_pass_equivalence_ledger.json")
    changed = deepcopy(control)
    equivalent = _r2_mutation(changed)["equivalent"][0]
    equivalent["resource_limit_evidence"] = deepcopy(
        _r2_mutation(changed)["killed"][0]["resource_limit_evidence"]
    )
    _must_refuse(control, changed, "ledger execution cannot carry per-execution")


def test_campaign_binds_exact_utc_deadline_and_rejects_expiry_at_creation():
    control = _cold_document()
    control["campaign"] = {
        "name": "b110.phase-1",
        "deadline_sha256": SHA_C,
        "created_at_utc": "2026-10-07T00:00:00Z",
        "expires_at_utc": "2026-10-07T01:00:00Z",
    }
    changed = deepcopy(control)
    changed["campaign"]["expires_at_utc"] = changed["campaign"]["created_at_utc"]
    _must_refuse(control, changed, "campaign.expires_at_utc must be later")

