"""Direct controls for B106's provenance-safe reuse planner boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import assay.reuse as reuse
from assay.errors import AssayError
from assay.reuse import ReuseSource


def _synthetic_document(*, policy=None, mutation=None, claim=None):
    if claim is None:
        claim = {"rigor": "R2", "mutation": mutation}
    return {
        "schema_version": 13,
        "claims": [claim],
        "judgment": {"r2": policy},
    }


def _write_document(tmp_path: Path, document) -> Path:
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _source(*, complete=True, cold_start=False, outcomes=None, candidates=()):
    return ReuseSource(
        path=Path("prior.json"),
        schema_version=12 if cold_start else 13,
        sha256="a" * 64,
        document={} if not cold_start else None,
        cold_start=cold_start,
        complete_unsharded_native=complete,
        outcomes={} if outcomes is None else outcomes,
        candidate_ids=frozenset(candidates),
    )


def test_reuse_reader_refuses_missing_oversized_and_non_object_artifacts(
    tmp_path, monkeypatch
):
    missing = tmp_path / "missing.json"
    with pytest.raises(AssayError, match="cannot read file"):
        reuse.load_reuse_source(missing)

    monkeypatch.setattr(reuse, "MAX_REUSE_ARTIFACT_BYTES", 4)
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"12345")
    with pytest.raises(AssayError, match="artifact limit"):
        reuse.load_reuse_source(oversized)

    monkeypatch.setattr(reuse, "MAX_REUSE_ARTIFACT_BYTES", 1024 * 1024)
    non_object = tmp_path / "array.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(AssayError, match="top-level JSON value must be an object"):
        reuse.load_reuse_source(non_object)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"NaN",
    ],
)
def test_reuse_reader_rejects_invalid_encoding_json_constants_and_depth(
    tmp_path, raw
):
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    with pytest.raises(AssayError, match="invalid JSON"):
        reuse.load_reuse_source(path)


def test_reuse_reader_rejects_json_recursion_error(tmp_path, monkeypatch):
    path = tmp_path / "deep.json"
    path.write_text("{}", encoding="utf-8")

    def too_deep(*_args, **_kwargs):
        raise RecursionError("simulated parser depth exhaustion")

    monkeypatch.setattr(reuse.json, "loads", too_deep)
    with pytest.raises(AssayError, match="invalid JSON: simulated parser depth exhaustion"):
        reuse.load_reuse_source(path)


def test_current_schema_artifact_must_pass_the_current_verifier(tmp_path):
    path = _write_document(tmp_path, {"schema_version": 13, "claims": []})
    with pytest.raises(AssayError, match="current v13 verifier rejected"):
        reuse.load_reuse_source(path)


@pytest.mark.parametrize(
    ("policy", "mutation", "claim"),
    [
        (None, {"candidate_ids": ["a"]}, None),
        ({"producer": "external"}, {"candidate_ids": ["a"]}, None),
        ({"producer": "native"}, None, None),
        ({"producer": "native"}, {"candidate_ids": "a"}, None),
        (
            {"producer": "native", "shard_index": 0},
            {"candidate_ids": ["a"]},
            None,
        ),
        (
            {"producer": "native"},
            {"candidate_ids": ["a"], "total": 0, "candidate_count": 1},
            None,
        ),
        (None, None, {"rigor": "R0"}),
    ],
)
def test_unproven_or_partial_prior_campaigns_never_offer_reuse(
    tmp_path, monkeypatch, policy, mutation, claim
):
    import assay.verify

    monkeypatch.setattr(assay.verify, "verify_document", lambda _document: [])
    document = _synthetic_document(policy=policy, mutation=mutation, claim=claim)
    source = reuse.load_reuse_source(_write_document(tmp_path, document))
    assert not source.complete_unsharded_native
    assert source.outcomes == {}
    assert source.candidate_ids == frozenset()


def test_complete_campaign_projects_each_bucket_and_its_exact_candidate(tmp_path, monkeypatch):
    import assay.verify

    monkeypatch.setattr(assay.verify, "verify_document", lambda _document: [])
    buckets = (
        "killed",
        "survived",
        "crashed",
        "budget_exceeded",
        "equivalent",
        "hung",
    )
    mutation = {
        "candidate_ids": [f"candidate-{bucket}" for bucket in buckets],
        "total": len(buckets),
        "candidate_count": len(buckets),
    }
    mutation.update(
        {
            bucket: [{"candidate_id": f"candidate-{bucket}"}]
            for bucket in buckets
        }
    )
    document = _synthetic_document(
        policy={"producer": "native"}, mutation=mutation
    )

    source = reuse.load_reuse_source(_write_document(tmp_path, document))

    assert source.complete_unsharded_native
    assert source.candidate_ids == frozenset(mutation["candidate_ids"])
    assert {candidate: outcome[0] for candidate, outcome in source.outcomes.items()} == {
        f"candidate-{bucket}": bucket for bucket in buckets
    }


def test_classification_refuses_every_non_reusable_prior_state():
    cold = _source(complete=False, cold_start=True)
    incomplete = _source(complete=False)
    assert reuse.classify_candidate(
        cold, "a", sequential_pytest_supported=True
    )[1].startswith("v12 cold start")
    assert reuse.classify_candidate(
        incomplete, "a", sequential_pytest_supported=True
    )[1].startswith("source is not a complete")
    complete = _source(
        outcomes={
            "survivor": ("survived", {}),
            "no-witness": ("killed", {"execution": {"mode": "full"}}),
            "unsupported": (
                "killed",
                {"execution": {"witness": {"node_id": "test::node"}}},
            ),
            "malformed": (
                "killed",
                {"execution": {"witness": {"node_id": 1}}},
            ),
        }
    )
    assert reuse.classify_candidate(
        complete, "new", sequential_pytest_supported=True
    ) == ("new-candidate", None)
    assert reuse.classify_candidate(
        complete, "survivor", sequential_pytest_supported=True
    ) == ("prior-outcome-requires-full", "prior outcome was survived")
    assert reuse.classify_candidate(
        complete, "no-witness", sequential_pytest_supported=True
    )[1] == "prior kill has no witness receipt"
    assert reuse.classify_candidate(
        complete, "unsupported", sequential_pytest_supported=False
    )[1] == "current command is not supported sequential pytest"
    assert reuse.classify_candidate(
        complete, "malformed", sequential_pytest_supported=True
    )[1] == "prior witness node ID is malformed"


def test_eligible_witnesses_and_prior_only_candidates_require_complete_evidence():
    incomplete = _source(complete=False, candidates=("a",))
    assert reuse.eligible_witnesses(
        incomplete, sequential_pytest_supported=True
    ) == {}
    assert reuse.prior_only_candidates(incomplete, []) == []

    complete = _source(
        outcomes={
            "a": (
                "killed",
                {"execution": {"witness": {"node_id": "test::a"}}},
            ),
            "b": ("survived", {}),
        },
        candidates=("a", "b", "old"),
    )
    assert reuse.eligible_witnesses(
        complete, sequential_pytest_supported=False
    ) == {}
    assert reuse.eligible_witnesses(
        complete, sequential_pytest_supported=True
    ) == {"a": ("a" * 64, "test::a")}
    assert reuse.prior_only_candidates(complete, ["a"]) == ["b", "old"]
