"""B108 step 3a (W9): the state-directory refusals and the caps that shape a state report.

Every test reads a state store built with ``campaign_support`` through the CLI (no
verdict), so the store is the only input under test. Sources of the refusal rows are
listed in the W9 LOG map from ``raise`` line to case id.
"""

from __future__ import annotations

import json

import pytest

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign_oracles import _analyze, _synthetic
from assay_analysis import campaign as campaign_api

J_X = support.JUDGE_A


def _campaign(tmp_path, monkeypatch, count=3):
    root, head, rows = _synthetic(tmp_path, monkeypatch, count)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, judge=J_X))
    return root, head, rows, progress


def _valid(row, **extra):
    return support.state_record(row, "killed", J_X, **extra)


def _refuse(tmp_path, monkeypatch, build):
    """Run with the store *build* makes; return the document of an exit-2 refusal."""
    root, head, rows, progress = _campaign(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    built = build(state, rows)
    extra = built if isinstance(built, tuple) and built[:1] == ("--state-dir",) else ("--state-dir", str(state))
    code, err, document = _analyze(root, head, progress, extra=extra)
    assert code == 2
    assert err.count("\n") == 1
    return document, rows


def _one_record(mutate):
    """A store holding one shape-broken record for the first plan row."""

    def build(state, rows):
        record = _valid(rows[0])
        mutate(record)
        (state / f"{rows[0]['id']}.json").write_text(json.dumps(record))

    return build


STATE_CASES = (
    ("schema-version", _one_record(lambda r: r.update(schema_version=2)), "schema_version is not 1"),
    ("bucket-unknown", _one_record(lambda r: r.update(outcome_bucket="weird")), "outcome_bucket is unknown"),
    (
        "execution-not-object",
        _one_record(lambda r: r.update(execution="x")),
        "execution provenance is invalid: execution must be an object",
    ),
    (
        "execution-mode-unknown",
        _one_record(lambda r: r.update(execution={"mode": "bogus"})),
        "execution provenance is invalid: execution mode is unknown",
    ),
    ("judge-not-digest", _one_record(lambda r: r.update(judge_sha256="zz")), "judge_sha256 is not a SHA-256 digest"),
    (
        "record-not-object",
        lambda state, rows: (state / f"{rows[0]['id']}.json").write_text("[]"),
        "record is not a JSON object",
    ),
    (
        "record-symlink",
        lambda state, rows: (
            (state.parent / "elsewhere.json").write_text(json.dumps(_valid(rows[0]))),
            (state / f"{rows[0]['id']}.json").symlink_to(state.parent / "elsewhere.json"),
        ),
        "cannot open state record artifact",
    ),
    (
        "directory-missing",
        lambda state, rows: ("--state-dir", str(state.parent / "absent-state")),
        "state directory is not an existing directory",
    ),
    (
        "directory-is-a-file",
        lambda state, rows: (
            (state.parent / "state-file").write_text("x"),
            ("--state-dir", str(state.parent / "state-file")),
        )[1],
        "state directory is not an existing directory",
    ),
)


@pytest.mark.parametrize(("case_id", "build", "fragment"), STATE_CASES, ids=[c[0] for c in STATE_CASES])
def test_every_state_directory_refusal_names_the_state_source(tmp_path, monkeypatch, case_id, build, fragment):
    document, _rows = _refuse(tmp_path, monkeypatch, build)
    assert fragment in document["errors"][0]["message"], (case_id, document["errors"])
    assert document["errors"][0]["source"] == "state"


def test_a_state_record_over_the_size_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign_api.mutation, "MUTATION_STATE_RECORD_LIMIT", 10)
    document, rows = _refuse(
        tmp_path, monkeypatch,
        lambda state, rows: (state / f"{rows[0]['id']}.json").write_text(json.dumps(_valid(rows[0]))),
    )
    assert document["errors"] == [{
        "source": "state",
        "message": (
            f"state record {rows[0]['id']}.json: state record artifact exceeds 10 bytes: "
            f"{tmp_path / 'state' / (rows[0]['id'] + '.json')}"
        ),
    }]


def test_files_that_are_not_state_record_names_are_ignored(tmp_path, monkeypatch):
    root, head, rows, progress = _campaign(tmp_path, monkeypatch)
    state = support.write_state(tmp_path / "state", [_valid(row) for row in rows])
    (state / "README.txt").write_text("not a record")
    (state / f"{rows[0]['id'].upper()}.json").write_text("{")
    (state / f"{rows[0]['id']}.json.tmp").write_text("{")
    code, err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert (code, err) == (3, "")
    assert document["state"]["counted"] == 3


def test_more_than_ten_state_failures_are_truncated_with_the_flag(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 11)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows, judge=J_X))
    state = support.write_state(tmp_path / "state", [
        {**_valid(row), "schema_version": 2} for row in rows
    ])
    code, _err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert code == 2
    assert len(document["errors"]) == campaign_api.MAX_ERRORS == 10
    assert document["errors_truncated"] is True
    assert [error["message"] for error in document["errors"]] == sorted(
        f"state record {row['id']}.json: schema_version is not 1" for row in rows
    )[:10]


def test_a_state_sample_lists_at_most_ten_sorted_ids_and_the_full_count(tmp_path, monkeypatch):
    root, head, rows, progress = _campaign(tmp_path, monkeypatch)
    foreign = support.make_rows(12, path="pkg/foreign.py")
    state = support.write_state(
        tmp_path / "state", [_valid(row) for row in rows] + [_valid(row) for row in foreign]
    )
    code, _err, document = _analyze(root, head, progress, extra=("--state-dir", str(state)))
    assert code == 3
    assert document["state"]["foreign"] == {
        "count": 12,
        "sample_ids": sorted(row["id"] for row in foreign)[: campaign_api.MAX_SAMPLE_IDS],
    }
    assert len(document["state"]["foreign"]["sample_ids"]) == 10
