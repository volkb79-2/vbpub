"""B108 campaign closeout: joined evidence must stay plan- and verdict-bound."""

from __future__ import annotations

import io
import hashlib
import json
import subprocess
import sys
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
from tests.core.test_b110_pilot_select import _valid_fixture as _valid_selector_fixture


def _repository(tmp_path: Path, document: dict) -> tuple[Path, str, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ("init", "-q"),
        ("config", "maintenance.auto", "false"),
        ("config", "gc.autoDetach", "false"),
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


def test_failed_baseline_verdict_does_not_establish_native_r2_inventory(tmp_path):
    declared_native = _fixture_document("r2_pass")
    root, head, verdict_path = _repository(tmp_path, declared_native)
    verdict = _fixture_document("r2_error_exec_failed_baseline_crashed")
    verdict["commit"] = head
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    started = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    progress = tmp_path / "failed-baseline-progress.jsonl"
    records = [
        {
            "event": "run",
            "commit": head,
            "lane": "package",
            "rigor": ["R0", "R2"],
            "started": started.isoformat(),
        },
        {
            "event": "verdict_written",
            "outcome": verdict["outcome"],
            "exit_code": verdict["exit_code"],
            "reason_code": verdict["reason_code"],
            "emitted_at": (started + timedelta(seconds=1)).isoformat(),
            "elapsed_s": 1.0,
        },
    ]
    progress.write_text("".join(json.dumps(record) + "\n" for record in records))

    code, output, error = _invoke(
        root, head, verdict_path, progress, command_exit=verdict["exit_code"]
    )

    result = json.loads(output)
    _validate(result)
    assert (code, error) == (3, "")
    assert result["status"] == "incomplete"
    assert result["qualifying"] is False
    assert result["complete_blockers"] == ["inventory_not_exhausted"]
    assert result["plan"]["status"] == "not_supported"
    assert result["campaign"]["planned_total"] is None
    assert result["campaign"]["completed_total"] is None


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


def _write_pilot_inputs(
    tmp_path: Path,
    *,
    root: Path,
    head: str,
    rows: list[dict],
    schema: str,
) -> tuple[Path, Path, list[str], str]:
    tree = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan_document = {
        "status": "ok",
        "lane": "package",
        "commit": head,
        "tree": tree,
        "candidate_count": len(rows),
        "shard": None,
        "candidates": rows,
    }
    plan_bytes = (json.dumps(plan_document, sort_keys=True, indent=2) + "\n").encode()
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(plan_bytes)
    selected_ids = [rows[0]["id"]]
    selection_sha256 = campaign_api.mutation.plan_sha256(selected_ids)
    common = {
        "plan_commit": head,
        "plan_tree": tree,
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "plan_candidate_count": len(rows),
        "selected_ids": selected_ids,
        "selection_sha256": selection_sha256,
        "seed": 42,
        "size": len(selected_ids),
        "files": sorted({row["path"] for row in rows}),
        "operators": sorted({row["operator"] for row in rows}),
    }
    if schema == "b110-pilot-selection/1":
        selection = {
            "schema": schema,
            **common,
            "candidates_file_sha256": "a" * 64,
            "stratified": [],
            "known_hard": [],
            "overlap": [],
            "import_time_total": 0,
        }
    else:
        selection = {
            "schema": schema,
            "lane": "package",
            **common,
            "targets": ["pkg"],
            "selected": [rows[0]],
            "selected_by_file": {},
            "selected_by_operator": {},
            "candidate_file_sha256": "b" * 64,
            "rare_operator_census": "python:falsy-swap",
        }
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection, sort_keys=True) + "\n")
    return plan_path, selection_path, selected_ids, selection_sha256


def _write_pilot_progress(
    path: Path,
    *,
    head: str,
    document: dict,
    rows: list[dict],
    selected_ids: list[str],
    selection_sha256: str,
    prior_selection_sha256: str | None = None,
    shard: dict | None = None,
) -> None:
    started = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    records = []
    if prior_selection_sha256 is not None:
        records.extend([
            {
                "event": "run",
                "commit": head,
                "lane": "package",
                "rigor": ["R0", "R2"],
                "started": (started - timedelta(minutes=2)).isoformat(),
            },
            {
                "event": "candidates",
                "commit": head,
                "candidate_total": len(rows),
                "selected_total": len(selected_ids),
                "pending_total": len(selected_ids),
                "selection_sha256": prior_selection_sha256,
            },
            {"event": "end", "candidate_total": len(rows),
             "buckets": {bucket: 0 for bucket in MUTATION_BUCKETS},
             "reason": "interrupted"},
        ])
    records.append({
        "event": "run",
        "commit": head,
        "lane": "package",
        "rigor": ["R0", "R2"],
        "started": started.isoformat(),
    })
    if shard is not None:
        records.append(shard)
    records.append({
            "event": "candidates",
            "commit": head,
            "candidate_total": len(rows),
            "selected_total": len(selected_ids),
            "pending_total": len(selected_ids),
            "selection_sha256": selection_sha256,
        })
    records.append({"event": "baseline", "candidate_index": -1, "candidate_total": len(rows)})
    outcome_by_id = {
        outcome["candidate_id"]: bucket
        for bucket in MUTATION_BUCKETS
        for outcome in next(
            claim["mutation"] for claim in document["claims"] if claim["rigor"] == "R2"
        ).get(bucket, [])
    }
    selected_rows = [row for row in rows if row["id"] in set(selected_ids)]
    buckets = {bucket: 0 for bucket in MUTATION_BUCKETS}
    for index, row in enumerate(selected_rows):
        bucket = outcome_by_id[row["id"]]
        buckets[bucket] += 1
        records.append({
            "event": "candidate",
            "candidate_id": row["id"],
            "candidate_index": index,
            "candidate_total": len(selected_ids),
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
    records.append({
        "event": "end",
        "candidate_total": len(rows),
        "buckets": buckets,
        "reason": None,
    })
    path.write_text("".join(json.dumps(item) + "\n" for item in records))


@pytest.mark.parametrize(
    "selection_schema",
    ["b110-pilot-selection/1", "assay-analysis-r2-pilot-selection/1"],
)
def test_pilot_selection_summary_is_plan_bound_and_never_qualifying(
    tmp_path, monkeypatch, selection_schema
):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, selection_sha256 = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema=selection_schema,
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection_sha256,
    )

    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=(
            "--plan-json", str(plan_path),
            "--selection-file", str(selection_path),
            "--project", "--project-jobs", "1",
        ),
    )

    assert (code, error) == (3, "")
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "incomplete"
    assert result["qualifying"] is False
    assert result["verdict"] is None
    assert "no_verdict" in result["complete_blockers"]
    assert result["campaign"]["planned_total"] == len(rows)
    assert result["campaign"]["selected_total"] == 1
    assert result["campaign"]["completed_total"] == 1
    assert result["campaign"]["pending_total"] == 0
    assert result["campaign"]["selection_sha256"] == selection_sha256
    assert result["runs"][0]["selection_sha256"] == selection_sha256
    assert result["evidence"]["plan_json"]["sha256"] == hashlib.sha256(
        plan_path.read_bytes()
    ).hexdigest()
    assert result["evidence"]["selection_file"]["sha256"] == hashlib.sha256(
        selection_path.read_bytes()
    ).hexdigest()


def test_b110_analysis_accepts_output_from_the_real_selector(tmp_path, monkeypatch):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    # The real selector is specific to Assay's own scanner source, so the plan it
    # reads is the selector test-suite's committed fixture, not the verdict
    # fixture's synthetic rows.
    rows, _files = _valid_selector_fixture(root)
    for number, row in enumerate(rows):
        # The campaign analysis re-derives every plan row's identity.
        row["mutated_file_sha256"] = hashlib.sha256(f"mutant-{number}".encode()).hexdigest()
        row["id"] = candidate_id_from_fields(**{key: row[key] for key in (
            "path", "source_sha256", "start_byte", "end_byte",
            "mutated_file_sha256", "operator")})
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "selector fixture"], check=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _install_plan(monkeypatch, fixture, rows)
    plan_path, _synthetic_selection, _ids, _digest = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="b110-pilot-selection/1",
    )
    selector = Path(__file__).resolve().parents[2] / "tools" / "b110_pilot_select.py"
    candidate_path = tmp_path / "b110-candidates.txt"
    selection_path = tmp_path / "b110-selection.json"
    selected = subprocess.run(
        [
            sys.executable, str(selector),
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(candidate_path),
            "--report", str(selection_path),
            "--seed", "b110-analysis-golden",
            "--size", "9",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert selected.returncode == 0, selected.stderr
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["schema"] == "b110-pilot-selection/1"
    selected_ids = selection["selected_ids"]
    assert candidate_path.read_text(encoding="utf-8").splitlines()[1:] == selected_ids

    progress = tmp_path / "b110-pilot-progress.jsonl"
    outcomes = {"claims": [{
        "rigor": "R2",
        "mutation": {"killed": [{"candidate_id": row["id"]} for row in rows]},
    }]}
    _write_pilot_progress(
        progress,
        head=head,
        document=outcomes,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection["selection_sha256"],
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=(
            "--plan-json", str(plan_path),
            "--selection-file", str(selection_path),
            "--project", "--project-jobs", "1",
        ),
    )
    assert (code, error) == (3, "")
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "incomplete"
    assert result["qualifying"] is False
    assert result["campaign"]["selection_sha256"] == selection["selection_sha256"]
    assert result["campaign"]["selected_total"] == len(selected_ids)


def test_pilot_selection_rejects_a_progress_digest_from_another_sample(
    tmp_path, monkeypatch
):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, selection_sha256 = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="assay-analysis-r2-pilot-selection/1",
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256="f" * 64,
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "evidence_error"
    assert result["errors"][0]["message"] == (
        "progress selection_sha256 differs from the bound selection file"
    )
    assert error.endswith("progress selection_sha256 differs from the bound selection file\n")


@pytest.mark.parametrize(
    "tamper, message",
    [
        ("plan-digest", "selection does not bind the exact source plan bytes"),
        ("unknown-id", "selection contains a candidate outside the current plan"),
        ("out-of-order", "selection candidate order differs from the current plan"),
    ],
)
def test_pilot_selection_refuses_false_plan_or_inventory_bindings(
    tmp_path, monkeypatch, tamper, message
):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, _digest = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="assay-analysis-r2-pilot-selection/1",
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if tamper == "plan-digest":
        selection["plan_sha256"] = "e" * 64
    elif tamper == "unknown-id":
        unknown = "f" * 64
        assert unknown not in {row["id"] for row in rows}
        selection["selected_ids"] = [unknown]
        selection["selection_sha256"] = campaign_api.mutation.plan_sha256([unknown])
    else:
        reordered = [row["id"] for row in rows[:2]][::-1]
        assert len(reordered) == 2
        selection["selected_ids"] = reordered
        selection["size"] = len(reordered)
        selection["selection_sha256"] = campaign_api.mutation.plan_sha256(reordered)
        selected_ids = reordered
    selection_path.write_text(json.dumps(selection, sort_keys=True) + "\n")
    selection_digest = selection["selection_sha256"]
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection_digest,
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "evidence_error"
    assert result["errors"][0]["message"] == message
    assert error.endswith(message + "\n")


def test_pilot_selection_rejects_a_prior_run_with_another_selection_digest(
    tmp_path, monkeypatch
):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, selection_sha256 = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="assay-analysis-r2-pilot-selection/1",
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection_sha256,
        prior_selection_sha256="f" * 64,
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["errors"][0]["message"] == (
        "progress selection_sha256 differs from the bound selection file"
    )
    assert error.endswith("progress selection_sha256 differs from the bound selection file\n")


def test_pilot_selection_cannot_be_combined_with_shard_or_verdict(
    tmp_path, monkeypatch
):
    fixture = _fixture_document("r2_pass")
    root, head, verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, selection_sha256 = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="b110-pilot-selection/1",
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection_sha256,
        shard={"event": "shard", "shard_index": 0, "shard_count": 2,
               "selected_total": 1},
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["errors"][0]["message"] == "pilot selection cannot be combined with a shard"
    assert error.endswith("pilot selection cannot be combined with a shard\n")

    with pytest.raises(SystemExit) as exit_info:
        _invoke(
            root,
            head,
            verdict,
            progress,
            extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path)),
        )
    assert exit_info.value.code == 2


def test_native_pilot_projection_must_use_declared_worker_count(tmp_path, monkeypatch):
    fixture = _fixture_document("r2_pass")
    root, head, _verdict = _repository(tmp_path, fixture)
    rows = _plan_rows(fixture)
    _install_plan(monkeypatch, fixture, rows)
    plan_path, selection_path, selected_ids, selection_sha256 = _write_pilot_inputs(
        tmp_path,
        root=root,
        head=head,
        rows=rows,
        schema="assay-analysis-r2-pilot-selection/1",
    )
    progress = tmp_path / "pilot-progress.jsonl"
    _write_pilot_progress(
        progress,
        head=head,
        document=fixture,
        rows=rows,
        selected_ids=selected_ids,
        selection_sha256=selection_sha256,
    )
    code, output, error = _invoke(
        root,
        head,
        None,
        progress,
        extra=("--plan-json", str(plan_path), "--selection-file", str(selection_path),
               "--project", "--project-jobs", "2"),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "evidence_error"
    assert result["errors"][0]["message"] == (
        "native R2 projection jobs must equal the lane's declared mutation jobs"
    )
    assert error.endswith(
        "native R2 projection jobs must equal the lane's declared mutation jobs\n"
    )


def test_native_r2_campaign_projection_rejects_a_parallel_worker_assumption(
        tmp_path, monkeypatch):
    root, head, _verdict, progress = _complete_fixture(
        tmp_path, monkeypatch, "r2_pass"
    )
    code, output, error = _invoke(
        root, head, None, progress,
        extra=("--project", "--project-jobs", "2"),
    )
    assert code == 2
    result = json.loads(output)
    _validate(result)
    assert result["status"] == "evidence_error"
    assert result["errors"][0]["message"] == (
        "native R2 projection jobs must equal the lane's declared mutation jobs"
    )
    assert error.endswith(
        "native R2 projection jobs must equal the lane's declared mutation jobs\n"
    )


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


def test_cd55_q12_command_exit_without_a_verdict_is_an_evidence_error(tmp_path, monkeypatch):
    root, head, _verdict, progress = _complete_fixture(tmp_path, monkeypatch, "r2_pass")
    code, out, err = _invoke(root, head, None, progress, command_exit=0)
    assert code == 2
    document = json.loads(out)
    _validate(document)
    assert document["status"] == "evidence_error"
    assert document["errors"] == [
        {"source": "arguments", "message": "--command-exit requires --verdict"}
    ]
    assert err == "assay analyze campaign: --command-exit requires --verdict\n"


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
