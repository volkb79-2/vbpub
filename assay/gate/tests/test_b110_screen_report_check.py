"""B110 screen completion requires a full current-plan mutation payload."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT
from assay.verify import verify_document
from assay._mutation_inventory import verify_complete_mutation_inventory


def _checker():
    path = PROJECT_ROOT / "tools" / "b110_screen_report_check.py"
    spec = importlib.util.spec_from_file_location("b110_screen_report_check_tests", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMIT = "a" * 40
TREE = "b" * 40


def _documents():
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"
    verdict = json.loads((fixture_root / "r2_fail_mutants_survived.json").read_text(encoding="utf-8"))
    r1_pass = json.loads((fixture_root / "r1_pass.json").read_text(encoding="utf-8"))
    verdict["declared_rigor"] = ["R0", "R1", "R2"]
    verdict["claims"].insert(
        1,
        deepcopy(next(claim for claim in r1_pass["claims"] if claim["rigor"] == "R1")),
    )
    verdict["judgment"]["r1"] = deepcopy(r1_pass["judgment"]["r1"])
    verdict["lane"] = "self-qualification"
    verdict["commit"] = COMMIT
    r2 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R2")
    mutation = r2["mutation"]
    candidate_ids = mutation["candidate_ids"]
    plan = {
        "status": "ok",
        "lane": "self-qualification",
        "commit": COMMIT,
        "tree": TREE,
        "candidate_count": len(candidate_ids),
        "candidates": [{"id": identity} for identity in candidate_ids],
        "shard": None,
    }
    return plan, verdict


def _verify(plan, verdict, *, expected_exit_code=None):
    _checker().verify_screen(
        plan,
        verdict,
        expected_commit=COMMIT,
        expected_tree=TREE,
        expected_exit_code=verdict["exit_code"] if expected_exit_code is None else expected_exit_code,
    )


def test_complete_survivor_screen_is_verified_even_with_nonzero_exit():
    plan, verdict = _documents()

    assert verify_document(verdict) == []
    _verify(plan, verdict)


def test_verdict_snapshot_is_checked_by_assay_verifier_inside_screen_checker():
    plan, verdict = _documents()
    verdict["schema_version"] = 1

    with pytest.raises(ValueError, match="Assay verifier rejected the parsed verdict snapshot"):
        _verify(plan, verdict)


def test_verdict_exit_code_must_match_the_assay_run_process_status():
    plan, verdict = _documents()

    with pytest.raises(ValueError, match="differs from the assay run exit status"):
        _verify(plan, verdict, expected_exit_code=0)


def test_r0_failure_with_nonempty_verdict_is_not_a_screen_completion():
    plan, verdict = _documents()
    verdict["claims"][0]["status"] = "FAIL"

    with pytest.raises(ValueError, match="R0 claim"):
        _verify(plan, verdict)


def test_verifier_valid_r0_failure_is_still_not_screen_completion():
    fixture = PROJECT_ROOT / "tests" / "fixtures" / "verdicts" / "r0_fail_command_failed.json"
    verdict = json.loads(fixture.read_text(encoding="utf-8"))
    verdict["lane"] = "self-qualification"
    verdict["commit"] = COMMIT
    plan, _survivor_verdict = _documents()

    assert verify_document(verdict) == []
    with pytest.raises(ValueError, match="R0 claim"):
        _verify(plan, verdict)


def test_r1_failure_is_not_a_screen_completion():
    plan, verdict = _documents()
    verdict["claims"][1]["status"] = "FAIL"

    with pytest.raises(ValueError, match="R1 claim"):
        _verify(plan, verdict)


def test_missing_r2_payload_is_refused():
    plan, verdict = _documents()
    verdict["claims"][2].pop("mutation")

    with pytest.raises(ValueError, match="payload is missing"):
        _verify(plan, verdict)


@pytest.mark.parametrize("field", ["commit", "tree"])
def test_wrong_plan_source_is_refused(field):
    plan, verdict = _documents()
    plan[field] = "c" * 40

    with pytest.raises(ValueError, match="plan commit/tree"):
        _verify(plan, verdict)


def test_wrong_verdict_commit_is_refused():
    plan, verdict = _documents()
    verdict["commit"] = "c" * 40

    with pytest.raises(ValueError, match="verdict commit"):
        _verify(plan, verdict)


def test_incomplete_or_duplicate_candidate_inventory_is_refused():
    plan, verdict = _documents()
    candidate_ids = verdict["claims"][2]["mutation"]["candidate_ids"]
    verdict["claims"][2]["mutation"]["candidate_ids"] = candidate_ids[:1]
    with pytest.raises(ValueError, match="differ from the ordered full plan"):
        _verify(plan, verdict)

    plan, verdict = _documents()
    verdict["claims"][2]["mutation"]["candidate_ids"] = [candidate_ids[0], candidate_ids[0]]
    with pytest.raises(ValueError, match="contains duplicates"):
        _verify(plan, verdict)


def test_each_full_plan_candidate_must_appear_once_in_a_terminal_bucket():
    plan, verdict = _documents()
    mutation = verdict["claims"][2]["mutation"]
    mutation["killed"][0]["candidate_id"] = mutation["survived"][0]["candidate_id"]

    with pytest.raises(ValueError, match="bucket outcomes contain duplicate candidate_ids"):
        _verify(plan, verdict)


def test_complete_inventory_check_rejects_bucket_duplicate_and_partial_total():
    plan, verdict = _documents()
    mutation = verdict["claims"][2]["mutation"]
    planned_ids = plan["candidates"]
    mutation["killed"][0]["candidate_id"] = mutation["survived"][0]["candidate_id"]
    with pytest.raises(ValueError, match="bucket outcomes contain duplicate candidate_ids"):
        verify_complete_mutation_inventory(
            mutation,
            [row["id"] for row in planned_ids],
            context="test",
        )

    plan, verdict = _documents()
    mutation = verdict["claims"][2]["mutation"]
    mutation["total"] = 1
    with pytest.raises(ValueError, match="total differs from the complete candidate_count"):
        verify_complete_mutation_inventory(
            mutation,
            [row["id"] for row in plan["candidates"]],
            context="test",
        )


def test_r2_lane_timeout_is_refused_even_with_complete_inventory():
    plan, verdict = _documents()
    verdict["claims"][2]["reason_code"] = "LANE_TIMEOUT"

    with pytest.raises(ValueError, match="LANE_TIMEOUT"):
        _verify(plan, verdict)


def test_verifier_valid_r2_error_cannot_certify_a_complete_screen(tmp_path: Path, capsys):
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"
    verdict = json.loads(
        (fixture_root / "r2_error_exec_failed_mutant_crashed.json").read_text(
            encoding="utf-8"
        )
    )
    r1_pass = json.loads((fixture_root / "r1_pass.json").read_text(encoding="utf-8"))
    verdict["declared_rigor"] = ["R0", "R1", "R2"]
    verdict["claims"].insert(
        1,
        deepcopy(next(claim for claim in r1_pass["claims"] if claim["rigor"] == "R1")),
    )
    verdict["judgment"]["r1"] = deepcopy(r1_pass["judgment"]["r1"])
    verdict["lane"] = "self-qualification"
    verdict["commit"] = COMMIT
    candidate_ids = verdict["claims"][2]["mutation"]["candidate_ids"]
    plan = {
        "status": "ok",
        "lane": "self-qualification",
        "commit": COMMIT,
        "tree": TREE,
        "candidate_count": len(candidate_ids),
        "candidates": [{"id": identity} for identity in candidate_ids],
        "shard": None,
    }

    assert verify_document(verdict) == []
    plan_path = tmp_path / "plan.json"
    verdict_path = tmp_path / "verdict.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    code = _checker().main([
        "--plan", str(plan_path),
        "--verdict", str(verdict_path),
        "--expected-commit", COMMIT,
        "--expected-tree", TREE,
        "--expected-exit-code", str(verdict["exit_code"]),
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not a completed PASS or FAIL" in captured.err


def test_reader_rejects_symlink_and_duplicate_json_keys(tmp_path: Path):
    checker = _checker()
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        checker._read_json(link, max_bytes=1024)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"status":"ok","status":"bad"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        checker._read_json(duplicate, max_bytes=1024)


def test_cli_emits_completion_marker_only_after_check(tmp_path: Path, capsys):
    checker = _checker()
    plan, verdict = _documents()
    plan_path = tmp_path / "plan.json"
    verdict_path = tmp_path / "verdict.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    code = checker.main([
        "--plan", str(plan_path),
        "--verdict", str(verdict_path),
        "--expected-commit", COMMIT,
        "--expected-tree", TREE,
        "--expected-exit-code", str(verdict["exit_code"]),
    ])

    assert code == 0
    assert capsys.readouterr().out.strip() == "B110_SCREEN_VERIFIED=1"
