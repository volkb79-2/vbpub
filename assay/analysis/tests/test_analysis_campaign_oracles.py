"""B108 oracles (W9): the observable behaviour of ``assay analyze campaign``.

Each test names the P8/W9 oracle it pins. Evidence is synthetic (plan rows whose
ids reproduce, progress streams and state records built by ``campaign_support``)
or verdict-seeded (``test_analysis_campaign`` helpers).
"""

from __future__ import annotations

import json
import math
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, ValidationError

from analysis.tests import campaign_support as support
from analysis.tests.test_analysis_campaign import (
    _complete_fixture,
    _fixture_document,
    _install_plan,
    _invoke,
    _plan_rows,
    _repository,
    _rewrite_progress,
    _validate,
    _write_progress,
)

def _synthetic(tmp_path, monkeypatch, count):
    """A lane repository with ``count`` synthetic plan rows installed as the plan."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    document = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, document)
    rows = support.make_rows(count)
    _install_plan(monkeypatch, document, rows)
    return root, head, rows


def _analyze(root, head, progress, **options):
    """Run the CLI without a verdict; returns ``(exit code, stderr, document)``."""
    code, out, err = _invoke(root, head, None, progress, **options)
    document = json.loads(out)
    _validate(document)
    return code, err, document


# ---- O1 -------------------------------------------------------------------

def test_o1_a_pass_campaign_without_command_exit_is_incomplete(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    code, out, err = _invoke(root, head, verdict, progress)
    assert (code, err) == (3, "")
    document = json.loads(out)
    _validate(document)
    assert document["status"] == "incomplete"
    assert document["complete_blockers"] == ["command_exit_not_observed"]
    assert document["verdict"]["observed_command_exit"] is None
    assert document["verdict"]["command_exit_matches"] is False


# ---- O3 -------------------------------------------------------------------

def test_o3_without_a_verdict_the_result_is_never_complete(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows))
    code, err, document = _analyze(root, head, progress)
    assert (code, err) == (3, "")
    assert document["status"] == "incomplete"
    assert document["verdict"] is None
    assert document["qualifying"] is True
    assert document["campaign"]["completed_total"] == 3
    assert document["campaign"]["pending_total"] == 0
    assert "no_verdict" in document["complete_blockers"]


# ---- O2 -------------------------------------------------------------------

def test_o2_appended_resume_runs_count_each_candidate_once(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 8)
    ids = [row["id"] for row in rows]
    progress = tmp_path / "progress.jsonl"
    started = support.RUN_START
    # Run 1 executes 5 of 8 and is cut; run 2 resumes those 5 and finishes 3;
    # run 3 resumes 7, re-judges the last candidate (a second execution of it) and ends.
    run1 = support.run_records(
        head, rows, started=started, executed=ids[:5], end=False, terminal=False
    )
    run2 = support.run_records(
        head, rows, started=started.replace(hour=13), resumed=5, resume=True
    )
    run3 = support.run_records(
        head, rows, started=started.replace(hour=14), resumed=7, rejudged=1, resume=True
    )
    support.write_records(progress, run1 + run2 + run3)
    code, err, document = _analyze(root, head, progress)
    assert (code, err) == (3, "")
    campaign = document["campaign"]
    assert campaign["completed_total"] == 8
    assert campaign["pending_total"] == 0
    assert campaign["per_run_resumed_total"] == [0, 5, 7]
    assert campaign["per_run_rejudged_total"] == [0, 0, 1]
    assert campaign["resumed_total"] == 7 and campaign["rejudged_total"] == 1
    assert [run["fresh_candidate_events"] for run in document["runs"]] == [5, 3, 1]
    assert sum(run["fresh_candidate_events"] for run in document["runs"]) == 9
    assert campaign["outcomes"]["killed"] == 8


# ---- O6 -------------------------------------------------------------------

def test_o6_a_candidate_reclassified_between_runs_is_counted_once_as_its_latest_bucket(
    tmp_path, monkeypatch
):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    flipped = rows[2]["id"]
    progress = tmp_path / "progress.jsonl"
    run1 = support.run_records(head, rows, buckets={flipped: "survived"}, terminal=False, end=False)
    run2 = support.run_records(
        head, rows, started=support.RUN_START.replace(hour=13), resumed=2, resume=True
    )
    support.write_records(progress, run1 + run2)
    code, _err, document = _analyze(root, head, progress)
    assert code == 3
    first, second = (run["run_id"] for run in document["runs"])
    assert document["reclassified"] == [{
        "candidate_id": flipped,
        "source": "progress_runs",
        "runs": [
            {"run_id": first, "bucket": "survived"},
            {"run_id": second, "bucket": "killed"},
        ],
        "state_bucket": None,
    }]
    assert document["campaign"]["completed_total"] == 3
    assert document["campaign"]["outcomes"]["killed"] == 3
    assert document["campaign"]["outcomes"]["survived"] == 0


# ---- O7 -------------------------------------------------------------------

def _cut_stream(tmp_path, monkeypatch, *, samples):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 25)
    progress = tmp_path / "progress.jsonl"
    ids = [row["id"] for row in rows]
    support.write_records(progress, support.run_records(
        head, rows, executed=ids[:samples], end=False, terminal=False,
        elapsed=lambda index: float(index + 1),
    ))
    return _analyze(root, head, progress)


def test_o7_nineteen_samples_give_no_eta(tmp_path, monkeypatch):
    code, _err, document = _cut_stream(tmp_path, monkeypatch, samples=19)
    assert code == 3
    timing = document["timing"]
    assert timing["eta"] is None
    assert timing["eta_reason"] == "insufficient_sample"
    assert (timing["sample_count"], timing["minimum_sample_count"]) == (19, 20)


def test_o7_twenty_samples_use_nearest_rank_percentiles(tmp_path, monkeypatch):
    code, _err, document = _cut_stream(tmp_path, monkeypatch, samples=20)
    assert code == 3
    timing = document["timing"]
    assert timing["eta_reason"] is None
    assert timing["sample_count"] == 20
    eta = timing["eta"]
    assert (eta["p50_candidate_s"], eta["p90_candidate_s"]) == (10.0, 18.0)
    assert eta["pending"] == 5
    assert eta["remaining_seconds_p50"] == round(5 * 10.0 / eta["jobs"], 1)
    assert eta["remaining_seconds_p90"] == round(5 * 18.0 / eta["jobs"], 1)
    first, last = timing["measurement_window"]["first"], timing["measurement_window"]["last"]
    assert first < last
    assert not math.isnan(eta["p50_candidate_s"])


# ---- O10 ------------------------------------------------------------------

def test_o10_the_adverse_page_reports_its_total_and_next_offset(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 5)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(
        head, rows, buckets={row["id"]: "survived" for row in rows}
    ))
    code, _err, document = _analyze(root, head, progress, extra=("--limit", "2"))
    assert code == 3
    page = document["adverse"]["survived"]
    assert (len(page["candidates"]), page["matching_total"], page["next_offset"]) == (2, 5, 2)
    assert {item["candidate_id"] for item in page["candidates"]} <= {row["id"] for row in rows}
    details = document["candidate_details"]
    assert (len(details["candidates"]), details["matching_total"], details["next_offset"]) == (2, 5, 2)
    _code, _err, second = _analyze(root, head, progress, extra=("--limit", "2", "--offset", "4"))
    assert second["candidate_details"]["next_offset"] is None
    assert len(second["candidate_details"]["candidates"]) == 1


# ---- O12 ------------------------------------------------------------------

def test_o12_a_torn_final_record_is_incomplete_without_a_verdict(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 3)
    progress = tmp_path / "progress.jsonl"
    records = support.run_records(head, rows, end=False, terminal=False)
    support.write_records(progress, records)
    progress.write_text(progress.read_text() + '{"event": "candidate", "candid')
    code, err, document = _analyze(root, head, progress)
    assert (code, err) == (3, "")
    assert document["status"] == "incomplete"
    assert document["torn_final_record"] is True
    assert document["campaign"]["completed_total"] == 3


def test_o12_a_torn_final_record_is_an_evidence_error_with_a_verdict(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    progress.write_text(progress.read_text() + '{"event": "verd')
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert code == 2
    document = json.loads(out)
    _validate(document)
    assert document["errors"] == [{
        "source": "progress",
        "message": "progress artifact has a torn final record (missing newline)",
    }]
    assert err.startswith("assay analyze campaign: progress artifact has a torn final record")


# ---- O13 ------------------------------------------------------------------

def test_o13_the_schema_rejects_documents_that_break_its_closed_shape(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path / "v", monkeypatch, "r2_pass")
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=0, extra=("--project", "--project-jobs", "2"))
    assert code == 0
    complete = json.loads(out)
    _validate(complete)
    assert complete["projection"] is not None

    def rejected(document):
        with pytest.raises(ValidationError):
            _validate(document)

    unqualified = json.loads(json.dumps(complete))
    unqualified["qualifying"] = False
    rejected(unqualified)

    blocked = json.loads(json.dumps(complete))
    blocked["complete_blockers"] = ["no_verdict"]
    rejected(blocked)

    extra_timing = json.loads(json.dumps(complete))
    extra_timing["timing"]["seconds_per_gremlin"] = 1.0
    rejected(extra_timing)

    extra_projection = json.loads(json.dumps(complete))
    extra_projection["projection"]["unexpected"] = 1
    rejected(extra_projection)

    root2, head2, rows = _synthetic(tmp_path / "s", monkeypatch, 25)
    stream = tmp_path / "s" / "progress.jsonl"
    ids = [row["id"] for row in rows]
    support.write_records(stream, support.run_records(
        head2, rows, executed=ids[:20], end=False, terminal=False
    ))
    _code, _err, with_eta = _analyze(root2, head2, stream)
    assert with_eta["timing"]["eta"] is not None
    short = json.loads(json.dumps(with_eta))
    short["timing"]["sample_count"] = 19
    rejected(short)


# ---- O16b -----------------------------------------------------------------

@pytest.mark.parametrize(
    "tamper",
    [{"source_sha256": "f" * 64}, {"source_sha256": None}, {"start_byte": -1}],
    ids=["other-digest", "digest-absent", "byte-span-invalid"],
)
def test_o16b_a_plan_row_whose_identity_inputs_do_not_reproduce_its_id_is_refused(
    tmp_path, monkeypatch, tamper
):
    document = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    tampered = rows[0]["id"]
    rows[0] = {**rows[0], **tamper}
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=document, plan_rows=rows)
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=0)
    assert code == 2
    result = json.loads(out)
    _validate(result)
    assert result["errors"] == [{
        "source": "plan",
        "message": f"plan row {tampered}: identity inputs do not reproduce its id",
    }]


# ---- O24 ------------------------------------------------------------------

def test_o24_the_pilot_candidates_file_option_does_not_exist(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    with pytest.raises(SystemExit) as exit_info:
        _invoke(root, head, verdict, progress, command_exit=0, extra=("--candidates-file", "x"))
    assert exit_info.value.code == 2


def test_o24_a_pilot_selection_in_the_progress_stream_is_an_evidence_error(tmp_path, monkeypatch):
    root, head, rows = _synthetic(tmp_path, monkeypatch, 2)
    progress = tmp_path / "progress.jsonl"
    support.write_records(progress, support.run_records(head, rows))

    def add_selection(records):
        next(item for item in records if item["event"] == "candidates")["selection_sha256"] = "c" * 64

    _rewrite_progress(progress, add_selection)
    code, _err, document = _analyze(root, head, progress)
    assert code == 2
    assert document["errors"] == [
        {"source": "progress", "message": "pilot selection unsupported before P7"}
    ]


# ---- O26 ------------------------------------------------------------------

def test_o26_verdict_and_no_verdict_documents_validate_against_the_schema(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path / "v", monkeypatch, "r2_pass")
    _code, out, _err = _invoke(root, head, verdict, progress, command_exit=0)
    with_verdict = json.loads(out)
    _code, out, _err = _invoke(root, head, None, progress)
    without_verdict = json.loads(out)
    schema = json.loads(
        files("assay").joinpath("schemas/analysis-campaign.schema.json").read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(with_verdict)
    validator.validate(without_verdict)
    assert with_verdict["verdict"] is not None
    assert without_verdict["verdict"] is None
