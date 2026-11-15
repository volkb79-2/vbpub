"""Merge-time shard identity: same commit, same image, same plan, complete set."""

from __future__ import annotations

import json

import pytest

from fleetd.cli import main
from fleetd.contracts import ContractError
from fleetd.merge import (
    IDENTITY_SCHEMA,
    IDENTITY_SET_SCHEMA,
    identity_document,
    identity_from_plan,
    load_shard_identity,
    validate_shard_identity,
    validate_shard_set,
)
from fleetd.scope import EXIT_STALE_CURSOR

COMMIT = "b" * 40
IMAGE = "sha256:" + "c" * 64


def _plan(commit=COMMIT):
    return {
        "schema": "fleet-plan/1",
        "lane": "assay-r2",
        "execution": {"schema": "fleet-plan-execution/1", "commit": commit, "project": "assay"},
        "hosts": [],
    }


def _identity_args(**overrides):
    values = {
        "lane": "assay-r2",
        "host": "runner-a",
        "commit": COMMIT,
        "image_digest": IMAGE,
        "plan_sha256": "d" * 64,
        "shard_index": 0,
        "shard_count": 2,
    }
    values.update(overrides)
    return values


def _identity(**overrides):
    return identity_document(**_identity_args(**overrides))


def test_identity_document_validates_every_field():
    document = _identity()
    assert document == {
        "schema": IDENTITY_SCHEMA,
        "lane": "assay-r2",
        "host": "runner-a",
        "commit": COMMIT,
        "image_digest": IMAGE,
        "plan_sha256": "d" * 64,
        "shard": {"index": 0, "count": 2},
    }
    assert validate_shard_identity(document) == document


def test_identity_document_refuses_a_placeholder_or_a_misspelled_field():
    good = _identity()
    cases = (
        (({**good, "lane": ""}), None, "expected non-empty text"),
        (({**good, "host": " "}), None, "expected non-empty text"),
        (None, {"commit": "b" * 39}, "commit"),
        (None, {"commit": "B" * 40}, "commit"),
        (None, {"image_digest": "sha256:" + "c" * 63}, "image_digest"),
        (None, {"image_digest": "runner-image:latest"}, "image_digest"),
        (None, {"plan_sha256": "nope"}, "plan_sha256"),
        (None, {"shard_count": 2, "shard_index": 2}, "below the shard count"),
        (({**good, "shard": {"index": -1, "count": 2}}), None, "at least 0"),
        (({**good, "shard": {"index": True, "count": 2}}), None, "at least 0"),
        (None, {"shard_count": 0}, "at least 1"),
        (None, {"shard_count": "2"}, "at least 1"),
    )
    for document, overrides, message in cases:
        with pytest.raises(ContractError, match=message):
            if document is not None:
                validate_shard_identity(document)
            else:
                identity_document(**{**_identity_args(), **overrides})


def test_identity_document_refuses_unknown_and_missing_fields():
    for document, message in (
        ({**_identity(), "surplus": 1}, "unknown field"),
        ({key: value for key, value in _identity().items() if key != "image_digest"}, "missing field"),
        ({**_identity(), "shard": {"index": 0}}, r"shard: missing field"),
        ({**_identity(), "shard": {"index": 0, "count": 2, "kind": "x"}}, r"shard: unknown field"),
        ({**_identity(), "shard": "nope"}, r"shard: expected an object"),
        ([], "expected a JSON object"),
        ({**_identity(), "schema": "other/1"}, "expected fleet-shard-identity/1"),
    ):
        with pytest.raises(ContractError, match=message):
            validate_shard_identity(document)


def test_identity_from_plan_derives_the_commit_and_the_plan_digest():
    document = identity_from_plan(
        _plan(), host="runner-a", image_digest=IMAGE, shard_index=1, shard_count=3
    )
    assert document["commit"] == COMMIT
    assert document["lane"] == "assay-r2"
    assert document["shard"] == {"index": 1, "count": 3}
    assert len(document["plan_sha256"]) == 64
    with pytest.raises(ContractError, match="plan.execution"):
        identity_from_plan({"schema": "fleet-plan/1", "lane": "r2"}, host="h", image_digest=IMAGE,
                           shard_index=0, shard_count=1)
    with pytest.raises(ContractError, match="plan.lane"):
        identity_from_plan({"schema": "fleet-plan/1", "execution": {}, "lane": ""},
                           host="h", image_digest=IMAGE, shard_index=0, shard_count=1)
    with pytest.raises(ContractError, match="fleet-plan/1"):
        identity_from_plan("nope", host="h", image_digest=IMAGE, shard_index=0, shard_count=1)
    with pytest.raises(ContractError, match="commit"):
        identity_from_plan(
            {"schema": "fleet-plan/1", "lane": "r2",
             "execution": {"commit": "b" * 39, "project": "."}},
            host="h", image_digest=IMAGE, shard_index=0, shard_count=1,
        )


def test_shard_set_accepts_a_complete_matching_set():
    document = validate_shard_set([_identity(), _identity(host="runner-b", shard_index=1)])
    assert document["status"] == "ok"
    assert document["shard_count"] == 2
    assert [shard["host"] for shard in document["shards"]] == ["runner-a", "runner-b"]
    assert document["problems"] == []
    assert document["schema"] == IDENTITY_SET_SCHEMA


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"commit": "e" * 40}, "commit"),
        ({"image_digest": "sha256:" + "f" * 64}, "image_digest"),
        ({"plan_sha256": "e" * 64}, "plan_sha256"),
        ({"lane": "other-lane"}, "lane"),
    ],
)
def test_shard_set_refuses_a_shard_that_judged_another_identity(overrides, field):
    document = validate_shard_set([_identity(), _identity(host="runner-b", shard_index=1, **overrides)])
    assert document["status"] == "mismatched"
    problem = document["problems"][0]
    assert problem["field"] == field
    assert problem["shard"] == "runner-b"
    assert "the reference shard has" in problem["reason"]


def test_shard_set_refuses_a_count_mismatch_and_a_duplicate_index():
    count = validate_shard_set([
        _identity(), _identity(host="runner-b", shard_index=1, shard_count=3),
    ])
    assert count["status"] == "mismatched"
    assert count["problems"][0]["field"] == "shard.count"
    duplicate = validate_shard_set([_identity(), _identity(host="runner-b", shard_index=0)])
    assert duplicate["status"] == "mismatched"
    problem = next(item for item in duplicate["problems"] if item["field"] == "shard.index")
    assert "claimed twice" in problem["reason"]
    assert problem["shard"] == "runner-a,runner-b"


def test_shard_set_reports_a_missing_shard_and_only_refuses_with_require_complete():
    incomplete = validate_shard_set([_identity(shard_count=3)])
    assert incomplete["status"] == "incomplete"
    problem = incomplete["problems"][0]
    assert problem["field"] == "shard.index"
    assert "missing from the set: 1, 2" in problem["reason"]
    assert "--require-complete" in problem["reason"]
    refused = validate_shard_set([_identity(shard_count=3)], require_complete=True)
    assert refused["status"] == "incomplete"
    assert "--require-complete" not in refused["problems"][0]["reason"]
    # A mismatch still wins over an incompleteness report.
    mixed = validate_shard_set([
        _identity(shard_count=3),
        identity_document(
            lane="assay-r2", host="runner-b", commit="f" * 40, image_digest=IMAGE,
            plan_sha256="d" * 64, shard_index=1, shard_count=3,
        ),
    ])
    assert mixed["status"] == "mismatched"
    assert {problem["field"] for problem in mixed["problems"]} == {"commit", "shard.index"}


def test_shard_set_refuses_an_empty_set():
    with pytest.raises(ContractError, match="at least one shard"):
        validate_shard_set([])


def test_load_shard_identity_reads_and_labels_its_source(tmp_path):
    path = tmp_path / "shard-0.json"
    path.write_text(json.dumps(_identity()), encoding="utf-8")
    assert load_shard_identity(path) == _identity()
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid JSON"):
        load_shard_identity(tmp_path / "bad.json")
    with pytest.raises(ContractError, match="cannot be read"):
        load_shard_identity(tmp_path / "absent.json")
    (tmp_path / "invalid.json").write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    with pytest.raises(ContractError, match="invalid.json: shard identity.schema: expected"):
        load_shard_identity(tmp_path / "invalid.json")


# ---------------------------------------------------------------------- CLI
def test_merge_check_cli_reports_ok_for_a_matching_set(tmp_path, capsys):
    paths = []
    for index, document in enumerate((_identity(), _identity(host="runner-b", shard_index=1))):
        target = tmp_path / f"shard-{index}.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        paths.append(str(target))
    assert main(["merge-check", *paths]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_merge_check_cli_refuses_a_mixed_image_or_a_missing_shard(tmp_path, capsys):
    mixed = []
    for index, document in enumerate((
        _identity(),
        _identity(host="runner-b", shard_index=1, image_digest="sha256:" + "e" * 64),
    )):
        target = tmp_path / f"mixed-{index}.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        mixed.append(str(target))
    assert main(["merge-check", *mixed]) == EXIT_STALE_CURSOR
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "mismatched"

    single = tmp_path / "single.json"
    single.write_text(json.dumps(_identity()), encoding="utf-8")
    assert main(["merge-check", str(single), "--require-complete"]) == EXIT_STALE_CURSOR
    assert json.loads(capsys.readouterr().out)["status"] == "incomplete"


def test_merge_check_cli_refuses_an_unreadable_identity(tmp_path, capsys):
    assert main(["merge-check", str(tmp_path / "absent.json")]) == 2
    assert "cannot be read" in capsys.readouterr().err
