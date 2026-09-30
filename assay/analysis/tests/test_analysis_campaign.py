"""B108 campaign closeout: joined evidence must stay plan- and verdict-bound."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from analysis.tests.analysis_support import JUDGE_VERDICT_FIXTURES
from assay.candidate_identity import candidate_id_from_fields
from assay.cli import main
from assay.verdict import MUTATION_BUCKETS
from assay_analysis import campaign as campaign_api


def _repository(tmp_path: Path, document: dict) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Campaign Test"),
        ("config", "user.email", "campaign@example.invalid"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / ".gitignore").write_text(".assay/\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "checks.py").write_text("def check(value):\n    return value < 3\n")
    policy = document["judgment"]["r2"]
    operators = ", ".join(json.dumps(item) for item in policy["operators"])
    (root / "assay.toml").write_text(
        "schema_version = 2\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R2"]\n'
        'enforcement = "gate"\n'
        'argv = ["pytest", "-q"]\n'
        'env = {}\n'
        'env_passthrough = ["PATH"]\n'
        'budget = "unbounded"\n'
        'allow_argv_append = false\n\n'
        "[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n\n'
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["pkg"]\n'
        'base = "main"\n'
        'mode = "changed_lines"\n\n'
        "[lanes.package.judge.mutation]\n"
        f"jobs = {policy['jobs']}\n"
        f"max_mutants = {policy['max_mutants']}\n"
        f"operators = [{operators}]\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    verdict_path = tmp_path / "verdict.json"
    document = json.loads(json.dumps(document))
    document["commit"] = head
    verdict_path.write_text(json.dumps(document, indent=2) + "\n")
    return root, head, verdict_path


def _plan_rows(document: dict) -> list[dict]:
    mutation = next(
        claim["mutation"]
        for claim in document["claims"]
        if claim["rigor"] == "R2" and "mutation" in claim
    )
    rows = []
    for bucket in MUTATION_BUCKETS:
        for outcome in mutation.get(bucket, []):
            rows.append({
                "id": outcome["candidate_id"],
                **{
                    field: outcome[field]
                    for field in (
                        "path", "lineno", "operator", "description", "start_byte", "end_byte",
                        "source_sha256", "mutated_file_sha256",
                    )
                },
            })
    return rows


def _write_progress(
    path: Path,
    *,
    head: str,
    document: dict,
    plan_rows: list[dict],
    reported_ids: set[str] | None = None,
    include_candidates: bool = True,
    include_end: bool = True,
    include_terminal: bool = True,
    end_reason: str | None = None,
    end_total: int | None = None,
) -> None:
    started = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    records = [{
        "event": "run",
        "commit": head,
        "lane": "package",
        "rigor": ["R0", "R2"],
        "started": started.isoformat(),
    }]
    mutation = next(
        (claim.get("mutation") for claim in document["claims"] if claim["rigor"] == "R2"),
        None,
    )
    total = 0 if mutation is None else mutation["candidate_count"]
    outcomes_by_id = {}
    buckets = {bucket: 0 for bucket in MUTATION_BUCKETS}
    if mutation is not None:
        for bucket in MUTATION_BUCKETS:
            for outcome in mutation.get(bucket, []):
                outcomes_by_id[outcome["candidate_id"]] = (bucket, outcome)
                buckets[bucket] += 1
    if include_candidates:
        records.append({
            "event": "candidates",
            "commit": head,
            "candidate_total": total,
            "selected_total": len(plan_rows),
            "pending_total": len(plan_rows),
        })
        records.append({"event": "baseline", "candidate_index": -1, "candidate_total": total})
        selected = (
            [row for row in plan_rows if row["id"] in reported_ids]
            if reported_ids is not None
            else plan_rows
        )
        for index, row in enumerate(selected):
            bucket, _outcome = outcomes_by_id[row["id"]]
            records.append({
                "event": "candidate",
                "candidate_id": row["id"],
                "candidate_index": index,
                "candidate_total": len(plan_rows),
                "path": row["path"],
                "lineno": row["lineno"],
                "operator": row["operator"],
                "description": row["description"],
                "start_byte": row["start_byte"],
                "end_byte": row["end_byte"],
                "outcome_bucket": bucket,
                "elapsed_seconds": 7.0 + index,
                "emitted_at": (started + timedelta(seconds=10 + index)).isoformat(),
            })
    if include_end:
        records.append({
            "event": "end",
            "candidate_total": total if end_total is None else end_total,
            "buckets": buckets,
            "reason": end_reason,
        })
    if include_terminal:
        records.append({
            "event": "verdict_written",
            "outcome": document["outcome"],
            "exit_code": document["exit_code"],
            "reason_code": document.get("reason_code"),
            "emitted_at": (started + timedelta(seconds=15)).isoformat(),
            "elapsed_s": 15.0,
        })
    path.write_text("".join(json.dumps(item) + "\n" for item in records))


def _invoke(
    root: Path,
    head: str,
    verdict: Path | None,
    progress: Path,
    *,
    command_exit: int | None = None,
    output_format: str = "json",
    lane: str = "package",
    extra: tuple[str, ...] = (),
) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    arguments = [
        "analyze", "campaign", lane,
        "--worktree", str(root),
        "--file", str(root / "assay.toml"),
        "--expected-commit", head,
        "--progress", str(progress),
        "--format", output_format,
    ]
    if verdict is not None:
        arguments += ["--verdict", str(verdict)]
    if command_exit is not None:
        arguments += ["--command-exit", str(command_exit)]
    code = main([*arguments, *extra], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _install_plan(monkeypatch, document: dict, rows: list[dict] | None = None) -> None:
    selected = _plan_rows(document) if rows is None else rows
    resolved_base = document["judgment"]["resolved"]["base"]
    monkeypatch.setattr(
        campaign_api,
        "_lane_plan",
        lambda *_args: {
            "status": "ok",
            "candidates": selected,
            "_resolved_base": resolved_base,
        },
    )


def _fixture_document(name: str) -> dict:
    return json.loads((JUDGE_VERDICT_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_complete_campaign_json_and_text_bind_plan_progress_and_verdict(tmp_path, monkeypatch):
    document = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=document, plan_rows=rows)

    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert (code, err) == (0, "")
    result = json.loads(out)
    schema = json.loads(files("assay").joinpath("schemas/analysis-campaign.schema.json").read_text())
    Draft202012Validator(schema).validate(result)
    assert result["status"] == "complete"
    assert result["campaign"]["planned_total"] == 2
    assert result["campaign"]["completed_total"] == 2
    assert result["campaign"]["pending_total"] == 0
    assert result["candidate_details"]["matching_total"] == 2
    assert result["timing"]["eta_reason"] == "no_remaining_work"

    code, out, err = _invoke(
        root, head, verdict, progress, command_exit=0, output_format="text"
    )
    assert (code, err) == (0, "")
    assert "package: complete" in out
    assert "planned=2 completed=2 pending=0" in out


def test_terminal_lane_timeout_leaves_never_started_candidates_unresolved(
    tmp_path, monkeypatch
):
    document = _fixture_document("r2_budget_exceeded_lane_timeout")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    first_kill = document["claims"][1]["mutation"]["killed"][0]["candidate_id"]
    _write_progress(
        progress,
        head=head,
        document=document,
        plan_rows=rows,
        reported_ids={first_kill},
    )

    code, out, err = _invoke(root, head, verdict, progress, command_exit=4)
    assert (code, err) == (3, "")
    result = json.loads(out)
    _validate(result)
    assert result["status"] == "incomplete"
    assert result["complete_blockers"] == ["lane_timeout_or_unstarted"]
    assert result["unresolved"] == {"matching_total": 1, "candidates": [
        next(
            row["id"] for row in rows
            if row["id"] != first_kill
        )
    ]}
    assert result["campaign"]["outcomes"]["budget_exceeded"] == 1
    assert result["campaign"]["outcomes"]["killed"] == 1
    assert (result["campaign"]["completed_total"], result["campaign"]["pending_total"]) == (1, 1)
    assert result["timing"]["eta_reason"] == "insufficient_sample"


def test_interrupted_progress_produces_an_incomplete_closeout(tmp_path, monkeypatch):
    document = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(
        progress,
        head=head,
        document=document,
        plan_rows=rows,
        reported_ids={rows[0]["id"]},
        include_end=False,
        include_terminal=False,
    )

    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert (code, err) == (3, "")
    result = json.loads(out)
    assert result["status"] == "incomplete"
    assert result["runs"][-1]["terminal"] is None
    # The verified verdict resolves both candidates even though the stream stopped early.
    assert result["timing"]["eta_reason"] == "no_remaining_work"
    assert result["complete_blockers"] == ["inventory_not_exhausted", "terminal_disagrees"]


def test_mutant_limit_sentinel_is_not_reported_as_an_empty_complete_campaign(
    tmp_path, monkeypatch
):
    document = _fixture_document("r2_budget_exceeded_mutant_limit_exceeded")
    root, head, verdict = _repository(tmp_path, document)
    rows = []
    for index in range(1, 5):
        identity = {
            "path": "pkg/checks.py",
            "source_sha256": f"{index:064x}",
            "start_byte": index,
            "end_byte": index + 1,
            "mutated_file_sha256": f"{index + 100:064x}",
            "operator": "python:compare-swap",
        }
        rows.append({
            "id": candidate_id_from_fields(**identity),
            "lineno": index,
            "description": "Lt->LtE",
            **identity,
        })
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(
        progress,
        head=head,
        document=document,
        plan_rows=rows,
        include_candidates=False,
        end_reason="over_candidate_cap",
        end_total=4,
    )

    code, out, err = _invoke(root, head, verdict, progress, command_exit=4)
    assert (code, err) == (3, "")
    result = json.loads(out)
    assert result["status"] == "incomplete"
    assert result["plan"]["planned_total"] == 4
    assert result["campaign"]["completed_total"] == 0
    assert result["campaign"]["pending_total"] == 4


def test_progress_and_verdict_outcomes_must_agree(tmp_path, monkeypatch):
    document = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=document, plan_rows=rows)
    records = [json.loads(line) for line in progress.read_text().splitlines()]
    candidate = next(item for item in records if item["event"] == "candidate")
    candidate["outcome_bucket"] = "survived"
    progress.write_text("".join(json.dumps(item) + "\n" for item in records))

    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert code == 2
    assert "progress and verdict disagree" in err
    error = json.loads(out)
    assert error["status"] == "evidence_error"
    assert "progress and verdict disagree" in error["errors"][0]["message"]


def _complete_fixture(tmp_path, monkeypatch, name: str) -> tuple[Path, str, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    document = _fixture_document(name)
    root, head, verdict = _repository(tmp_path, document)
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=document, plan_rows=rows)
    return root, head, verdict, progress


def _validate(document: dict) -> None:
    schema = json.loads(files("assay").joinpath("schemas/analysis-campaign.schema.json").read_text())
    Draft202012Validator(schema).validate(document)


def test_o1_exit_mapping_follows_the_status_table(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path / "pass", monkeypatch, "r2_pass")
    code, out, err = _invoke(root, head, verdict, progress, command_exit=0)
    assert (code, err) == (0, "")
    assert json.loads(out)["status"] == "complete"

    root, head, verdict, progress = _complete_fixture(
        tmp_path / "fail", monkeypatch, "r2_fail_mutants_survived"
    )
    code, out, err = _invoke(root, head, verdict, progress, command_exit=1)
    assert (code, err) == (1, "")
    document = json.loads(out)
    assert document["status"] == "complete"
    assert document["verdict"]["outcome"] == "FAIL"
    assert document["qualifying"] is True
    _validate(document)


def test_o1_a_command_exit_that_differs_from_the_verdict_is_an_evidence_error(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    code, out, err = _invoke(root, head, verdict, progress, command_exit=1)
    assert code == 2
    document = json.loads(out)
    _validate(document)
    assert set(document) == {
        "schema_version", "kind", "status", "lane", "expected_commit", "errors", "errors_truncated",
    }
    assert (document["kind"], document["status"], document["lane"]) == (
        "assay-campaign-analysis", "evidence_error", "package",
    )
    assert document["expected_commit"] == head
    assert document["errors_truncated"] is False
    [error] = document["errors"]
    assert error["message"] == "observed command exit 1 differs from verified verdict exit 0"
    assert err == f"assay analyze campaign: {error['message']}\n"


def test_an_evidence_error_document_is_json_even_for_the_text_format(tmp_path, monkeypatch):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=1, output_format="text")
    assert code == 2
    assert json.loads(out)["status"] == "evidence_error"


def _rewrite_progress(progress: Path, change) -> None:
    records = [json.loads(line) for line in progress.read_text().splitlines()]
    change(records)
    progress.write_text("".join(json.dumps(item) + "\n" for item in records))


def _refuse_duplicate_plan_row(tmp_path, monkeypatch, root, head, verdict, progress):
    document = _fixture_document("r2_pass")
    rows = _plan_rows(document)
    _install_plan(monkeypatch, document, rows + rows[:1])
    return {}


def _refuse_bad_limit(tmp_path, monkeypatch, root, head, verdict, progress):
    return {"extra": ("--limit", "0")}


def _refuse_unknown_lane(tmp_path, monkeypatch, root, head, verdict, progress):
    return {"lane": "ghost"}


def _refuse_missing_verdict(tmp_path, monkeypatch, root, head, verdict, progress):
    return {"verdict": tmp_path / "absent-verdict.json"}


def _refuse_garbage_progress(tmp_path, monkeypatch, root, head, verdict, progress):
    progress.write_text("not json\n")
    return {}


def _refuse_terminal_disagreement(tmp_path, monkeypatch, root, head, verdict, progress):
    def change(records):
        records[-1]["outcome"] = "FAIL"

    _rewrite_progress(progress, change)
    return {}


def _refuse_coverage_without_declaration(tmp_path, monkeypatch, root, head, verdict, progress):
    return {"extra": ("--coverage", str(tmp_path / "coverage.json"))}


SOURCE_CASES = (
    ("arguments", _refuse_bad_limit),
    ("lane", _refuse_unknown_lane),
    ("verdict", _refuse_missing_verdict),
    ("plan", _refuse_duplicate_plan_row),
    ("progress", _refuse_garbage_progress),
    ("input", _refuse_terminal_disagreement),
    ("coverage", _refuse_coverage_without_declaration),
)


@pytest.mark.parametrize(("source", "mutate"), SOURCE_CASES, ids=[case[0] for case in SOURCE_CASES])
def test_cd51_an_error_source_names_the_refused_input(tmp_path, monkeypatch, source, mutate):
    root, head, verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    options = {"verdict": verdict, "command_exit": 0, **mutate(
        tmp_path, monkeypatch, root, head, verdict, progress
    )}
    code, out, _err = _invoke(root, head, options.pop("verdict"), progress, **options)
    assert code == 2
    document = json.loads(out)
    _validate(document)
    assert [error["source"] for error in document["errors"]] == [source]
    assert source in campaign_api.ERROR_SOURCES
