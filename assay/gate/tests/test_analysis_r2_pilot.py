"""Behavioral oracles for B131's analysis-only R2 pilot evidence."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assay.candidate_identity import candidate_id_from_fields
from assay.mutation import MUTATION_BUCKETS, MUTATION_STATE_SCHEMA_VERSION
from gate.tests.support import PROJECT_ROOT


TOOLS = PROJECT_ROOT / "tools"
_tool_path = str(TOOLS)
if _tool_path not in sys.path:
    sys.path.insert(0, _tool_path)
import analysis_r2_pilot_select as selector  # noqa: E402
import analysis_r2_pilot_check as checker  # noqa: E402
from assay.resource_limits import CounterDelta, ResourceLimitEvidence  # noqa: E402

if sys.path[0] == _tool_path:
    sys.path.pop(0)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "B131 test")
    _git(root, "config", "user.email", "b131@example.invalid")
    _git(root, "config", "maintenance.auto", "false")
    _git(root, "config", "gc.autoDetach", "false")

    assay_dir = root / "assay"
    assay_dir.mkdir()
    shutil.copyfile(PROJECT_ROOT / "assay.toml", assay_dir / "assay.toml")
    for relative in selector.TARGETS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT.parent / relative, destination)

    _git(root, "add", "--", "assay")
    _git(root, "commit", "-qm", "analysis R2 fixture")
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    return root, commit, tree


def _rows(
    root: Path,
    specifications: list[tuple[str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, (path, operator) in enumerate(specifications, 1):
        source = (root / path).read_bytes()
        fields: dict[str, object] = {
            "path": path,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "start_byte": 0,
            "end_byte": 1,
            "mutated_file_sha256": hashlib.sha256(f"mutant-{index}".encode()).hexdigest(),
            "operator": operator,
        }
        rows.append({
            "id": candidate_id_from_fields(**fields),
            **fields,
            "lineno": 1,
            "description": f"synthetic mutation {index}",
        })
    return rows


def _plan(root: Path, commit: str, tree: str, rows: list[dict[str, object]]) -> dict:
    return {
        "status": "ok",
        "lane": selector.LANE,
        "commit": commit,
        "tree": tree,
        "shard": None,
        "candidate_count": len(rows),
        "jobs": 1,
        "resource_observation": {"available": True},
        "cold_witness": {"eligible": True},
        "by_file": dict(sorted(Counter(str(row["path"]) for row in rows).items())),
        "by_operator": dict(sorted(Counter(str(row["operator"]) for row in rows).items())),
        "candidates": rows,
    }


def _write_selection(
    root: Path,
    tmp_path: Path,
    plan: dict,
    *,
    size: int,
) -> tuple[bytes, bytes]:
    plan_path = tmp_path / "plan.json"
    candidates_path = tmp_path / "candidates.txt"
    selection_path = tmp_path / "selection.json"
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(candidates_path),
            "--report", str(selection_path),
            "--size", str(size),
        ])
    assert status == 0
    return candidates_path.read_bytes(), selection_path.read_bytes()


def _zero_resources() -> dict:
    zero = CounterDelta(before=0, after=0, delta=0)
    return ResourceLimitEvidence(
        pids_events_max=zero,
        memory_events_max=zero,
        memory_events_oom=zero,
        memory_events_oom_kill=zero,
        memory_events_oom_group_kill=zero,
    ).to_dict()


def _valid_pilot(tmp_path: Path) -> dict:
    root, commit, tree = _repository(tmp_path)
    path = selector.TARGETS[1]
    rows = _rows(root, [
        (path, "python:compare-swap"),
        (path, "python:compare-swap"),
    ])
    plan = _plan(root, commit, tree, rows)
    plan_raw = (json.dumps(plan, sort_keys=True) + "\n").encode()
    candidates_raw, selection_raw = _write_selection(root, tmp_path, plan, size=1)
    selection = json.loads(selection_raw)
    assert len(selection["selected_ids"]) == 1
    identity = selection["selected_ids"][0]
    row = next(row for row in rows if row["id"] == identity)
    wheel_sha256 = "a" * 64
    created = datetime.now(timezone.utc).replace(microsecond=0)

    deadline = {
        "schema": "assay-campaign-deadline/1",
        "campaign": f"analysis-r2-pilot-{commit[:12]}",
        "commit": commit,
        "git_tree": tree,
        "lanes": [selector.LANE],
        "assay_version": checker.ASSAY_VERSION,
        "wheel_sha256": wheel_sha256,
        "plan_sha256": {
            selector.LANE: selector.common._plan_sha256([str(row["id"]) for row in rows])
        },
        "created_at_utc": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": (created + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    deadline_raw = (json.dumps(deadline, sort_keys=True) + "\n").encode()
    deadline_sha256 = hashlib.sha256(deadline_raw).hexdigest()

    state_dir = tmp_path / "analysis-r2-pilot-state"
    state_dir.mkdir()
    (state_dir / "PILOT-STATE").write_text(json.dumps({
        "schema": "assay-pilot-state/1",
        "selection_sha256": selector.common._plan_sha256([identity]),
        "lane": selector.LANE,
    }), encoding="utf-8")
    resources = _zero_resources()
    record = {
        "candidate_id": identity,
        "path": row["path"],
        "operator": row["operator"],
        "source_sha256": row["source_sha256"],
        "mutated_file_sha256": row["mutated_file_sha256"],
        "start_byte": row["start_byte"],
        "end_byte": row["end_byte"],
        "lineno": row["lineno"],
        "description": row["description"],
        "outcome_bucket": "killed",
        "campaign_deadline_sha256": deadline_sha256,
        "schema_version": MUTATION_STATE_SCHEMA_VERSION,
        "judge_sha256": "b" * 64,
        "execution": {
            "mode": "full",
            "witness": {
                "node_id": "test_contract",
                "when": "call",
                "outcome": "failed",
                "session_exit_status": 1,
                "process_exit_status": 1,
            },
        },
        "evidence": {
            "command": "declared",
            "collection_count": 1,
            "collection_sha256": "c" * 64,
            "hook_fingerprint_sha256": "d" * 64,
            "started_count": None,
            "failed_call_index": None,
        },
        "terminal_result": {
            "outcome": "FAIL",
            "reason_code": "COMMAND_FAILED",
            "returncode": 1,
        },
        "resource_limit_evidence": resources,
    }
    (state_dir / f"{identity}.json").write_text(json.dumps(record), encoding="utf-8")

    buckets = {name: 0 for name in MUTATION_BUCKETS}
    buckets["killed"] = 1
    summary = {
        "schema": "assay-pilot-summary/1",
        "qualifying": False,
        "completed": True,
        "lane": selector.LANE,
        "commit": commit,
        "jobs": 1,
        "requested": 1,
        "selection_sha256": selection["selection_sha256"],
        "candidates_file_sha256": hashlib.sha256(candidates_raw).hexdigest(),
        "state_dir": str(state_dir.resolve()),
        "r0": "PASS",
        "r1": "PASS",
        "r2": {"status": "PASS", "reason_code": None},
        "r3": "not-run: pilot",
        "unresolved": [],
        "buckets": buckets,
        "candidates": [{
            "id": identity,
            "path": row["path"],
            "operator": row["operator"],
            "bucket": "killed",
            "execution_mode": "full",
        }],
    }
    summary_raw = (json.dumps(summary, sort_keys=True) + "\n").encode()
    progress_events = [
        {"event": "run", "lane": selector.LANE, "commit": commit, "rigor": ["R0", "R1", "R2"]},
        {
            "event": "candidates",
            "commit": commit,
            "candidate_total": 1,
            "selected_total": 1,
            "pending_total": 1,
            "selection_sha256": selection["selection_sha256"],
            "judge_sha256": "b" * 64,
        },
        {
            "event": "baseline",
            "candidate_index": -1,
            "candidate_total": 1,
            "path": ".",
            "operator": "baseline",
            "start_byte": 0,
            "end_byte": 0,
            "mutated_file_sha256": "",
            "emitted_at": "2026-10-09T10:00:01Z",
            "elapsed_s": 1.0,
        },
        {
            "event": "candidate",
            "candidate_id": identity,
            "candidate_index": 0,
            "candidate_total": 1,
            "path": row["path"],
            "operator": row["operator"],
            "lineno": row["lineno"],
            "description": row["description"],
            "start_byte": row["start_byte"],
            "end_byte": row["end_byte"],
            "mutated_file_sha256": row["mutated_file_sha256"],
            "outcome_bucket": "killed",
            "execution_mode": "full",
            "resource_limit_evidence": resources,
        },
        {
            "event": "end",
            "candidate_total": 1,
            "buckets": buckets,
            "reason": None,
            "emitted_at": "2026-10-09T10:01:00Z",
            "elapsed_s": 60.0,
        },
        {
            "event": "verdict_written",
            "outcome": "PASS",
            "reason_code": None,
            "exit_code": 6,
            "destination": None,
            "emitted_at": "2026-10-09T10:01:01Z",
            "elapsed_s": 61.0,
        },
    ]
    progress_raw = ("".join(json.dumps(event, sort_keys=True) + "\n" for event in progress_events)).encode()
    return {
        "plan_raw": plan_raw,
        "selection_raw": selection_raw,
        "candidates_raw": candidates_raw,
        "summary_raw": summary_raw,
        "progress_raw": progress_raw,
        "deadline_raw": deadline_raw,
        "state_dir": state_dir,
        "expected_commit": commit,
        "expected_tree": tree,
        "expected_wheel_sha256": wheel_sha256,
        "expected_exit_code": 6,
        "repo_root": root,
    }


def test_analysis_selector_covers_each_file_operator_stratum_and_rare_operator(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [
        (selector.TARGETS[1], "python:compare-swap"),
        (selector.TARGETS[1], "python:compare-swap"),
        (selector.TARGETS[2], "python:boolop-swap"),
        (selector.TARGETS[3], "python:falsy-swap"),
        (selector.TARGETS[3], "python:falsy-swap"),
        (selector.TARGETS[4], "python:compare-swap"),
    ])
    selected, by_file, by_operator = selector._select(
        rows, seed="analysis-r2-test", size=5
    )
    selected_again, _files_again, _operators_again = selector._select(
        rows, seed="analysis-r2-test", size=5
    )

    assert [row["id"] for row in selected] == [row["id"] for row in selected_again]
    assert len(selected) == 5
    assert by_file == {
        selector.TARGETS[1]: 1,
        selector.TARGETS[2]: 1,
        selector.TARGETS[3]: 2,
        selector.TARGETS[4]: 1,
    }
    assert by_operator == {
        "python:boolop-swap": 1,
        "python:compare-swap": 2,
        "python:falsy-swap": 2,
    }
    with pytest.raises(selector.common.SelectionError, match="cannot cover"):
        selector._select(rows, seed="analysis-r2-test", size=4)


def test_analysis_selector_refuses_wrong_lane_and_preserves_prior_pair(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [(selector.TARGETS[1], "python:compare-swap")])
    plan = _plan(root, commit, tree, rows)
    candidates, selection = _write_selection(root, tmp_path, plan, size=1)
    plan["lane"] = "analysis"
    plan_path = tmp_path / "wrong-lane.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(tmp_path / "candidates.txt"),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert (tmp_path / "candidates.txt").read_bytes() == candidates
    assert (tmp_path / "selection.json").read_bytes() == selection


def test_analysis_selector_refuses_dirty_checkout(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [(selector.TARGETS[1], "python:compare-swap")])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(root, commit, tree, rows)), encoding="utf-8")
    (root / "unexpected.txt").write_text("dirty tree\n", encoding="utf-8")

    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(tmp_path / "candidates.txt"),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert not (tmp_path / "candidates.txt").exists()
    assert not (tmp_path / "selection.json").exists()


def test_analysis_selector_refuses_an_empty_candidate_plan(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(root, commit, tree, []), sort_keys=True), encoding="utf-8"
    )

    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(tmp_path / "candidates.txt"),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert not (tmp_path / "candidates.txt").exists()
    assert not (tmp_path / "selection.json").exists()


def test_analysis_selector_refuses_plan_source_hash_mismatch(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [(selector.TARGETS[1], "python:compare-swap")])
    rows[0]["source_sha256"] = "f" * 64
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(root, commit, tree, rows), sort_keys=True), encoding="utf-8"
    )

    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(tmp_path / "candidates.txt"),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert not (tmp_path / "candidates.txt").exists()
    assert not (tmp_path / "selection.json").exists()


def test_analysis_selector_refuses_plan_output_collision(tmp_path: Path):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [(selector.TARGETS[1], "python:compare-swap")])
    plan_path = tmp_path / "plan.json"
    plan_raw = (json.dumps(_plan(root, commit, tree, rows), sort_keys=True) + "\n").encode()
    plan_path.write_bytes(plan_raw)

    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(plan_path),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert plan_path.read_bytes() == plan_raw
    assert not (tmp_path / "selection.json").exists()


def test_analysis_selector_partial_publication_leaves_no_report_commit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, commit, tree = _repository(tmp_path)
    rows = _rows(root, [(selector.TARGETS[1], "python:compare-swap")])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(root, commit, tree, rows), sort_keys=True), encoding="utf-8"
    )
    original_replace = selector.common.os.replace

    def fail_selection_publish(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        if destination == "selection.json":
            raise OSError("injected report publish failure")
        return original_replace(
            source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr(selector.common.os, "replace", fail_selection_publish)
    with contextlib.redirect_stderr(io.StringIO()):
        status = selector.main([
            "--plan", str(plan_path),
            "--repo-root", str(root),
            "--out", str(tmp_path / "candidates.txt"),
            "--report", str(tmp_path / "selection.json"),
            "--size", "1",
        ])

    assert status == 2
    assert (tmp_path / "candidates.txt").is_file()
    assert not (tmp_path / "selection.json").exists()


def test_analysis_pilot_accepts_consistent_complete_evidence(tmp_path: Path):
    checker.verify_pilot(**_valid_pilot(tmp_path))


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong-version", "assay_version differs"),
        ("wrong-duration", "not the active two-hour pilot campaign"),
        ("future-created", "not the active two-hour pilot campaign"),
        ("expired", "not the active two-hour pilot campaign"),
    ],
)
def test_analysis_pilot_rejects_deadline_that_is_not_the_active_exact_source_campaign(
    tmp_path: Path,
    case: str,
    message: str,
):
    evidence = _valid_pilot(tmp_path)
    _plan_doc, _commit, _tree, rows, _targets = selector._parse_bound_plan(
        evidence["plan_raw"], repo_root=evidence["repo_root"]
    )
    _plan_by_id, plan_ids = checker._plan_index(rows)
    deadline = json.loads(evidence["deadline_raw"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if case == "wrong-version":
        deadline["assay_version"] = "not-this-wheel"
    elif case == "wrong-duration":
        deadline["expires_at_utc"] = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif case == "future-created":
        created = now + timedelta(minutes=2)
        deadline["created_at_utc"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        deadline["expires_at_utc"] = (created + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif case == "expired":
        created = now - timedelta(hours=3)
        deadline["created_at_utc"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        deadline["expires_at_utc"] = (created + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence["deadline_raw"] = (json.dumps(deadline, sort_keys=True) + "\n").encode()

    with pytest.raises(ValueError, match=message):
        checker._check_deadline(
            evidence["deadline_raw"],
            expected_commit=evidence["expected_commit"],
            expected_tree=evidence["expected_tree"],
            plan_ids=plan_ids,
            expected_wheel_sha256=evidence["expected_wheel_sha256"],
        )


def test_analysis_pilot_accepts_resumed_candidate_with_complete_resume_accounting(
    tmp_path: Path,
):
    evidence = _valid_pilot(tmp_path)
    prior_run = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    current_run = [
        {
            "event": "run",
            "lane": selector.LANE,
            "commit": evidence["expected_commit"],
            "rigor": ["R0", "R1", "R2"],
        },
        {
            "event": "resume",
            "candidate_total": 1,
            "resumed_total": 1,
            "rejected_total": 0,
            "rejudged_total": 0,
            "emitted_at": "2026-10-09T10:02:00Z",
            "elapsed_s": 0.1,
        },
        {
            "event": "candidates",
            "commit": evidence["expected_commit"],
            "candidate_total": 1,
            "selected_total": 1,
            "pending_total": 0,
            "selection_sha256": json.loads(evidence["summary_raw"])["selection_sha256"],
            "judge_sha256": "b" * 64,
        },
        {
            "event": "baseline",
            "candidate_index": -1,
            "candidate_total": 1,
            "path": ".",
            "operator": "baseline",
            "start_byte": 0,
            "end_byte": 0,
            "mutated_file_sha256": "",
            "emitted_at": "2026-10-09T10:02:00Z",
            "elapsed_s": 0.2,
        },
        {
            "event": "end",
            "candidate_total": 0,
            "buckets": {name: 0 for name in MUTATION_BUCKETS},
            "reason": None,
            "emitted_at": "2026-10-09T10:02:01Z",
            "elapsed_s": 1.0,
        },
        {
            "event": "resume_merged",
            "resumed_total": 1,
            "emitted_at": "2026-10-09T10:02:02Z",
            "elapsed_s": 1.1,
        },
        {
            "event": "verdict_written",
            "outcome": "PASS",
            "reason_code": None,
            "exit_code": 6,
            "destination": None,
            "emitted_at": "2026-10-09T10:02:03Z",
            "elapsed_s": 1.2,
        },
    ]
    evidence["progress_raw"] = (
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in prior_run + current_run)
    ).encode()

    checker.verify_pilot(**evidence)


def test_analysis_pilot_rejects_resume_accounting_that_does_not_partition_selection(
    tmp_path: Path,
):
    evidence = _valid_pilot(tmp_path)
    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    candidates = next(event for event in events if event["event"] == "candidates")
    candidates["pending_total"] = 0
    evidence["progress_raw"] = (
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
    ).encode()

    with pytest.raises(ValueError, match="omits resume accounting"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_rejects_summary_and_terminal_status_that_contradict_the_bucket(
    tmp_path: Path,
):
    evidence = _valid_pilot(tmp_path)
    summary = json.loads(evidence["summary_raw"])
    summary["r2"] = {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}
    evidence["summary_raw"] = (json.dumps(summary) + "\n").encode()
    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    events[-1]["outcome"] = "FAIL"
    events[-1]["reason_code"] = "MUTANTS_SURVIVED"
    evidence["progress_raw"] = ("".join(json.dumps(event) + "\n" for event in events)).encode()

    with pytest.raises(ValueError, match="R2 result differs"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_killed_full_candidate_requires_declared_command_evidence(
    tmp_path: Path,
):
    evidence = _valid_pilot(tmp_path)
    identity = json.loads(evidence["summary_raw"])["candidates"][0]["id"]
    state_path = evidence["state_dir"] / f"{identity}.json"
    state_record = json.loads(state_path.read_text(encoding="utf-8"))
    state_record["evidence"]["command"] = "r2"
    state_path.write_text(json.dumps(state_record), encoding="utf-8")

    with pytest.raises(ValueError, match="lacks declared-command evidence"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_rejects_progress_end_bucket_mismatch(tmp_path: Path):
    evidence = _valid_pilot(tmp_path)
    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    events[-2]["buckets"]["killed"] = 0
    evidence["progress_raw"] = ("".join(json.dumps(event) + "\n" for event in events)).encode()

    with pytest.raises(ValueError, match="end event.*buckets"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_requires_the_r2_baseline_before_candidate_events(tmp_path: Path):
    evidence = _valid_pilot(tmp_path)
    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    events.remove(next(event for event in events if event["event"] == "baseline"))
    evidence["progress_raw"] = (
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
    ).encode()

    with pytest.raises(ValueError, match="candidates/baseline milestone"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_hung_progress_must_match_the_validated_state_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    evidence = _valid_pilot(tmp_path)
    # This test exercises progress-to-state binding; the state validator's
    # complete B107 trace semantics are independently exercised by Assay tests.
    monkeypatch.setattr(checker, "_valid_hung_resource_evidence", lambda value: bool(value))
    summary = json.loads(evidence["summary_raw"])
    summary["candidates"][0]["bucket"] = "hung"
    summary["buckets"]["killed"] = 0
    summary["buckets"]["hung"] = 1
    summary["r2"] = {"status": "BUDGET_EXCEEDED", "reason_code": "CANDIDATE_HUNG"}
    evidence["summary_raw"] = (json.dumps(summary) + "\n").encode()

    identity = summary["candidates"][0]["id"]
    state_path = evidence["state_dir"] / f"{identity}.json"
    state_record = json.loads(state_path.read_text(encoding="utf-8"))
    state_record["outcome_bucket"] = "hung"
    state_record["terminal_result"] = {
        "outcome": "BUDGET_EXCEEDED",
        "reason_code": "CANDIDATE_HUNG",
        "returncode": None,
    }
    state_record["liveness_resource_evidence"] = {"trace": "validated state"}
    state_path.write_text(json.dumps(state_record), encoding="utf-8")

    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    next(event for event in events if event["event"] == "candidate")["outcome_bucket"] = "hung"
    next(event for event in events if event["event"] == "candidate")[
        "liveness_resource_evidence"
    ] = {"trace": "different progress trace"}
    end = next(event for event in events if event["event"] == "end")
    end["buckets"]["killed"] = 0
    end["buckets"]["hung"] = 1
    terminal = events[-1]
    terminal["outcome"] = "BUDGET_EXCEEDED"
    terminal["reason_code"] = "CANDIDATE_HUNG"
    evidence["progress_raw"] = ("".join(json.dumps(event) + "\n" for event in events)).encode()

    with pytest.raises(ValueError, match="latest progress liveness evidence differs from state"):
        checker.verify_pilot(**evidence)


def test_analysis_pilot_rejects_extra_state_files_and_resource_disagreement(tmp_path: Path):
    evidence = _valid_pilot(tmp_path)
    (evidence["state_dir"] / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="state inventory"):
        checker.verify_pilot(**evidence)

    evidence["state_dir"].joinpath("unexpected.json").unlink()
    events = [json.loads(line) for line in evidence["progress_raw"].splitlines()]
    events[3]["resource_limit_evidence"]["pids_events"]["max"]["before"] = 1
    events[3]["resource_limit_evidence"]["pids_events"]["max"]["after"] = 1
    evidence["progress_raw"] = ("".join(json.dumps(event) + "\n" for event in events)).encode()
    with pytest.raises(ValueError, match="resource evidence differs"):
        checker.verify_pilot(**evidence)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"event":"run"}', "partial record"),
        (b'{"event":"run"}\r\n', "LF-only framing"),
        (b"\n", "valid unique-key"),
    ],
)
def test_analysis_pilot_progress_requires_bounded_lf_records(raw: bytes, message: str):
    with pytest.raises(ValueError, match=message):
        checker._progress_events(raw)


def test_analysis_pilot_progress_rejects_oversized_lines():
    raw = b'{"event":"run","padding":"' + b"x" * checker._MAX_PROGRESS_LINE_BYTES + b'"}\n'
    with pytest.raises(ValueError, match="line 1 exceeds"):
        checker._progress_events(raw)
