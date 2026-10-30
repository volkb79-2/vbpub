"""Close the last campaign.py line/branch gaps: pilot-selection refusals, state-store
guards, the library-only argument checks and the bounded refusal renderer."""

from __future__ import annotations

import argparse
import hashlib
import json

import pytest

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign import (
    _fixture_document,
    _install_plan,
    _invoke,
    _plan_rows,
    _repository,
    _validate,
    _write_pilot_inputs,
    _write_pilot_progress,
)
from analysis.tests.test_analysis_campaign_oracles import _analyze, _synthetic
from analysis.tests.test_analysis_campaign_refusals import _REAL_LANE_PLAN
from assay.config import load_lane_file
from assay_analysis import campaign as campaign_api

SCHEMA_R2 = "assay-analysis-r2-pilot-selection/1"
J_X = support.JUDGE_A
NO_VALUE = object()


# ---- _lane_plan success path ---------------------------------------------------


def test_lane_plan_returns_the_planner_rows_and_the_resolved_base(tmp_path, monkeypatch):
    root, _head, rows = _synthetic(tmp_path, monkeypatch, 2)
    lane_file = load_lane_file(root / "assay.toml")
    monkeypatch.setattr("assay.cli.plan_jobs", lambda *_a, **_k: rows)
    declared = []
    monkeypatch.setattr(
        "assay.runner.resolve_declared_base",
        lambda project_root, declaration: declared.append(declaration) or "f" * 40,
    )
    plan = _REAL_LANE_PLAN(lane_file, "package", None)
    assert plan == {"status": "ok", "candidates": rows, "_resolved_base": "f" * 40}
    assert len(declared) == 1


# ---- pilot selection refusals --------------------------------------------------


def _pilot(tmp_path, monkeypatch, *, plan=None, selection=None, raw_plan=None,
           raw_selection=None, progress_digest=None):
    """Write a valid pilot run, apply one tamper, run the CLI; return (code, document, stderr)."""
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, digest = _write_pilot_inputs(
        tmp_path, root=root, head=head, rows=rows, schema=SCHEMA_R2
    )
    if plan is not None:
        document = json.loads(plan_path.read_text())
        plan(document)
        plan_path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")
        bound = json.loads(selection_path.read_text())
        bound["plan_sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        selection_path.write_text(json.dumps(bound, sort_keys=True) + "\n")
    if raw_plan is not None:
        plan_path.write_bytes(raw_plan)
    if selection is not None:
        document = json.loads(selection_path.read_text())
        selection(document)
        selection_path.write_text(json.dumps(document, sort_keys=True) + "\n")
    if raw_selection is not None:
        selection_path.write_bytes(raw_selection)
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress, head=head, document=fixture, rows=rows, selected_ids=selected_ids,
        selection_sha256=digest if progress_digest is NO_VALUE else progress_digest or digest,
    )
    code, out, err = _invoke(
        root, head, None, progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    return code, json.loads(out), err


def _set(**changes):
    return lambda document: document.update(changes)


PILOT_CASES = (
    ("plan-not-utf8", {"raw_plan": b"\xff\xfe"}, "source plan is not unique-key UTF-8 JSON: "),
    ("plan-not-object", {"raw_plan": b"[]"}, "source plan is not an object"),
    (
        "plan-not-ok",
        {"plan": _set(status="unsupported")},
        "source plan is not the current complete unsharded inventory for this lane",
    ),
    ("selection-not-utf8", {"raw_selection": b"\xff\xfe"}, "selection is not unique-key UTF-8 JSON: "),
    ("selection-not-object", {"raw_selection": b"[]"}, "selection is not an object"),
    (
        "selection-schema",
        {"selection": _set(schema="bogus/1")},
        "selection has an unsupported schema or unexpected fields",
    ),
    ("selection-lane", {"selection": _set(lane="other")}, "selection names a different lane"),
    (
        "selection-commit",
        {"selection": _set(plan_commit="0" * 40)},
        "selection commit, tree, or candidate count differs from the current plan",
    ),
    (
        "selection-plan-sha-shape",
        {"selection": _set(plan_sha256="zz")},
        "selection plan_sha256 is not a SHA-256 digest",
    ),
    (
        "selection-empty-ids",
        {"selection": _set(selected_ids=[])},
        "selection selected_ids must be a non-empty unique list of candidate ids",
    ),
    (
        "selection-digest-mismatch",
        {"selection": _set(selection_sha256="d" * 64)},
        "selection selection_sha256 does not match selected_ids",
    ),
)


@pytest.mark.parametrize(
    "case_id,options,message", PILOT_CASES, ids=[case[0] for case in PILOT_CASES]
)
def test_pilot_selection_inputs_are_refused_with_their_named_reason(
    tmp_path, monkeypatch, case_id, options, message
):
    code, document, err = _pilot(tmp_path, monkeypatch, **options)
    assert code == 2, case_id
    _validate(document)
    assert document["status"] == "evidence_error"
    assert document["errors"][0]["message"].startswith(message), document["errors"]
    assert err.startswith("assay analyze campaign: " + message)


def test_a_selection_file_needs_the_progress_to_carry_a_selection_digest(tmp_path, monkeypatch):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, _digest = _write_pilot_inputs(
        tmp_path, root=root, head=head, rows=rows, schema=SCHEMA_R2
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress, head=head, document=fixture, rows=rows, selected_ids=selected_ids,
        selection_sha256=None,
    )
    code, out, _err = _invoke(
        root, head, None, progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    document = json.loads(out)
    assert code == 2
    assert document["errors"][0]["message"] == (
        "selection file was supplied but progress has no pilot selection digest"
    )


# ---- a valid resume_merged milestone ------------------------------------------------


def test_a_resume_merged_milestone_matching_the_resume_count_is_accepted(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    records = support.run_records(head, rows, judge=J_X, resume=True, terminal=False)
    position = next(i for i, record in enumerate(records) if record["event"] == "candidates")
    records.insert(position + 1, {"event": "resume_merged", "resumed_total": 0})
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, records)
    code, err, document = _analyze(root, head, progress)
    assert (code, err) == (3, "")
    assert document["status"] == "incomplete"
    assert document["campaign"]["planned_total"] == 3


# ---- library-only argument checks ---------------------------------------------------


def _library_call(tmp_path, **options):
    arguments = dict(
        worktree=tmp_path, lane_file_path=tmp_path / "assay.toml", lane_name="package",
        expected_commit="0" * 40, verdict_path=None, progress_path=tmp_path / "p.jsonl",
        command_exit=None,
    )
    arguments.update(options)
    return campaign_api.campaign(**arguments)


def test_the_library_entry_needs_plan_json_and_selection_file_together(tmp_path):
    with pytest.raises(ValueError, match="--plan-json and --selection-file must be supplied together"):
        _library_call(tmp_path, plan_json_path=tmp_path / "plan.json")


def test_the_library_entry_refuses_pilot_selection_with_a_verdict(tmp_path):
    with pytest.raises(ValueError, match="pilot selection analysis cannot be combined with --verdict"):
        _library_call(
            tmp_path, plan_json_path=tmp_path / "plan.json",
            selection_file_path=tmp_path / "selection.json", verdict_path=tmp_path / "v.json",
        )


def test_the_command_entry_rejects_plan_json_without_selection_file(tmp_path, capsys):
    parser = argparse.ArgumentParser(prog="campaign")
    args = argparse.Namespace(
        project=False, project_jobs=None, plan_json=tmp_path / "plan.json",
        selection_file=None, verdict=None, _campaign_parser=parser,
    )
    with pytest.raises(SystemExit) as exit_info:
        campaign_api.run_campaign_command(args, stdout=None, stderr=None)
    assert exit_info.value.code == 2
    assert "--plan-json and --selection-file must be given together" in capsys.readouterr().err


# ---- the bounded refusal renderer ----------------------------------------------------


class _SourcedError(ValueError):
    source = "not-a-known-source"


def test_an_unknown_error_source_is_reported_as_input():
    args = argparse.Namespace(lane="package", expected_commit="0" * 40)
    document = campaign_api._evidence_error_document(args, _SourcedError("boom"))
    assert document["errors"] == [{"source": "input", "message": "boom"}]
    assert "not-a-known-source" not in campaign_api.ERROR_SOURCES


def test_an_oversized_refusal_collapses_to_one_minimal_truncated_row(monkeypatch):
    monkeypatch.setattr(campaign_api, "MAX_ERROR_OUTPUT_BYTES", 10)
    args = argparse.Namespace(lane="package", expected_commit="0" * 40)
    rendered = json.loads(campaign_api._render_evidence_error(args, ValueError("detail")))
    assert rendered["errors"] == [{
        "source": "input",
        "message": "analysis refused the evidence; refusal details were truncated",
    }]
    assert rendered["errors_truncated"] is True
    assert rendered["status"] == "evidence_error"


# ---- state directory guards -----------------------------------------------------------


def test_a_state_directory_that_cannot_be_inspected_is_refused(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ValueError, match="cannot inspect state directory"):
        campaign_api._read_state_dir(blocker / "state")


def test_a_state_directory_scan_failure_is_refused(tmp_path, monkeypatch):
    def broken(_path):
        raise PermissionError("scan denied")

    monkeypatch.setattr(campaign_api.os, "scandir", broken)
    with pytest.raises(ValueError, match="cannot scan state directory .*scan denied"):
        campaign_api._read_state_dir(tmp_path)


def test_a_record_that_vanishes_before_inspection_is_a_state_failure(tmp_path, monkeypatch):
    name = "a" * 64 + ".json"

    class _Child:
        pass

    child = _Child()
    child.name = name

    class _Scan:
        def __enter__(self):
            return iter([child])

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(campaign_api.os, "scandir", lambda _path: _Scan())
    with pytest.raises(Exception) as caught:
        campaign_api._read_state_dir(tmp_path)
    errors = getattr(caught.value, "errors", None)
    assert errors is not None
    assert errors[0]["source"] == "state"
    assert errors[0]["message"].startswith(f"cannot inspect state record {name}: ")


def _one_state_file(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / ("a" * 64 + ".json")).write_text("{}")
    return state


def test_a_read_that_overshoots_the_aggregate_budget_is_refused(tmp_path, monkeypatch):
    state = _one_state_file(tmp_path, monkeypatch)
    monkeypatch.setattr(campaign_api, "MAX_STATE_DIRECTORY_BYTES", 100)
    monkeypatch.setattr(
        campaign_api, "_read_artifact",
        lambda path, *, limit, label: (b"{}", {"path": str(path), "sha256": "0" * 64, "bytes": 101}),
    )
    with pytest.raises(ValueError, match="^state records exceed 100 aggregate bytes$"):
        campaign_api._read_state_dir(state)


def test_a_record_larger_than_the_remaining_budget_is_an_aggregate_refusal(tmp_path, monkeypatch):
    state = _one_state_file(tmp_path, monkeypatch)
    monkeypatch.setattr(campaign_api, "MAX_STATE_DIRECTORY_BYTES", 100)

    def over_the_remainder(path, *, limit, label):
        assert limit == 100
        raise ValueError(f"{label} artifact exceeds {limit} bytes: {path}")

    monkeypatch.setattr(campaign_api, "_read_artifact", over_the_remainder)
    with pytest.raises(ValueError, match="^state records exceed 100 aggregate bytes$"):
        campaign_api._read_state_dir(state)
