from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import argparse
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from assay.adapters.python import PythonAdapter
from assay.mutation import (
    MUTATION_BUCKETS,
    MUTATION_STATE_SCHEMA_VERSION,
    MutationTarget,
    candidate_id,
    candidate_identity_fields,
    collect_mutation_sites,
    plan_sha256,
    valid_hung_resource_evidence,
)
from gate.tests.support import PROJECT_ROOT


TOOLS = PROJECT_ROOT / "tools"
CHECKER_PATH = TOOLS / "b110_pilot_report_check.py"
SELECTOR_PATH = TOOLS / "b110_pilot_select.py"
HOST_CHECK_PATH = TOOLS / "b110_pilot_host_check.py"
GO_PATH = "assay/src/assay/adapters/go.py"
BOOL_PATH = "assay/src/assay/adapters/pilot_bool_fixture.py"
CONFIG_PATH = "assay/assay.toml"
WHEEL_SHA256 = "c" * 64
ZERO_RESOURCE_EVIDENCE = {
    "cgroup_version": 2,
    "pids_events": {"max": {"before": 0, "after": 0, "delta": 0}},
    "memory_events": {
        "max": {"before": 0, "after": 0, "delta": 0},
        "oom": {"before": 0, "after": 0, "delta": 0},
        "oom_kill": {"before": 0, "after": 0, "delta": 0},
        "oom_group_kill": {"before": 0, "after": 0, "delta": 0},
    },
}


def _complete_hung_resource_trace() -> dict:
    snapshot = {
        "schema_version": 1,
        "status": "available",
        "cgroup_identity": "fixture-cgroup",
        "host_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
    }
    last_elapsed = 31.0
    samples = [
        {
            "wall_elapsed_s": float(elapsed),
            "eligible_elapsed_s": float(elapsed),
            "eligible_interval_s": 0.0 if elapsed == 0 else 1.0,
            "candidate_cpu_s": 3.0,
            "event_count": 0,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "resource_interval": "unknown" if elapsed == 0 else "clear",
            "resource_deltas": {},
            **({"previous_resources": None} if elapsed == 0 else {}),
            "resources": snapshot,
        }
        for elapsed in range(32)
    ]
    return {
        "schema_version": 1,
        "policy": "pressure-adjusted-idle-v1",
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": last_elapsed,
        "eligible_elapsed_s": last_elapsed,
        "idle_eligible_s": last_elapsed,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": 30.0,
        "required_cpu_growth_floor_s": 1.0,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "samples": samples,
    }


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    tools_path = str(TOOLS)
    added_tools_path = tools_path not in sys.path
    if added_tools_path:
        sys.path.insert(0, tools_path)
    try:
        spec.loader.exec_module(module)
    finally:
        if added_tools_path:
            sys.path.remove(tools_path)
    return module


def test_host_check_hash_and_copy_enforce_file_limits(tmp_path: Path):
    module = _load_module("b110_host_check_bounded_files_test", HOST_CHECK_PATH)
    source_path = tmp_path / "source"
    source_path.write_bytes(b"x" * 32)
    destination_path = tmp_path / "destination"
    source_fd = os.open(source_path, os.O_RDONLY)
    destination_fd = os.open(destination_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with pytest.raises(ValueError, match="exceeds the 16-byte limit"):
            module._hash_fd(source_fd, label="test source", limit=16)
        with pytest.raises(ValueError, match="exceeds the 16-byte limit"):
            module._copy_descriptor(
                source_fd,
                destination_fd,
                expected_digest=hashlib.sha256(b"x" * 32).hexdigest(),
                label="test source",
                limit=16,
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert destination_path.stat().st_size <= 16


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c", "maintenance.auto=false",
            "-c", "maintenance.autoDetach=false",
            "-c", "gc.autoDetach=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "user.name=B110 report checker test",
            "-c", "user.email=b110-report-checker@example.invalid",
            "-C", str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _producer_progress_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Model ProgressStream's timestamp enrichment for every emitted event."""
    for index, event in enumerate(events):
        event.setdefault("emitted_at", "2026-10-09T00:00:00Z")
        event.setdefault("elapsed_s", index / 10)
    return events


def _fixture(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    go_source = (
        b"def _scan_raw_string():\n"
        b"    return None if end == -1 else end + 1\n"
        b"def _strip_comments_and_literals():\n"
        b'    if two == "//":\n'
        b"        return None\n"
        b"    if end == -1:\n"
        b"        return None\n"
        b"    if close == -1:\n"
        b"        return None\n"
        b"    return None\n"
    )
    bool_source = b"FLAG_A = True\nFLAG_B = False\nFLAG_C = True\n"
    for relative, content in (
        (GO_PATH, go_source),
        (BOOL_PATH, bool_source),
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    config_path = repo / CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """schema_version = 2

[lanes.self-qualification]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "tests", "-q"]
env = {}
env_passthrough = ["PATH"]
budget = "5h"
allow_argv_append = false

[lanes.self-qualification.isolation]
snapshot_selection = "repository"

[lanes.self-qualification.judge]
language = "python"
source_roots = ["src/assay"]
mode = "whole_target"
targets = ["src/assay/adapters/go.py", "src/assay/adapters/pilot_bool_fixture.py"]

[lanes.self-qualification.judge.mutation]
jobs = 3
max_mutants = 128
operators = ["python:compare-swap", "python:boolop-swap", "python:bool-const-flip", "python:falsy-swap"]
""",
        encoding="utf-8",
    )
    targets = []
    for path, raw_source in ((GO_PATH, go_source), (BOOL_PATH, bool_source)):
        text = raw_source.decode("utf-8")
        line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        targets.append(
            MutationTarget(path=path, text=text, lines=frozenset(range(1, line_count + 1)))
        )
    jobs = collect_mutation_sites(
        targets,
        adapter=PythonAdapter(),
        operators=(
            "python:compare-swap",
            "python:boolop-swap",
            "python:bool-const-flip",
            "python:falsy-swap",
        ),
        limit=128,
    )
    assert isinstance(jobs, tuple)
    rows: list[dict[str, object]] = []
    replacement_sha256_by_id: dict[str, str] = {}
    for job in jobs:
        identity = candidate_id(job)
        identity_fields = candidate_identity_fields(job)
        rows.append(
            {
                "id": identity,
                "path": job.path,
                "operator": job.site.operator,
                "start_byte": job.site.start_byte,
                "end_byte": job.site.end_byte,
                "lineno": job.site.lineno,
                "description": job.site.description,
                "source_sha256": identity_fields["source_sha256"],
                "mutated_file_sha256": identity_fields["mutated_file_sha256"],
            }
        )
        replacement_sha256_by_id[identity] = job.site.replacement_sha256

    _git(repo, "init", "--quiet")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "source-bound report fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")

    project_assay = repo / "assay"
    artifact_dir = project_assay / ".assay"
    state_dir = artifact_dir / "b110-pilot-state"
    state_dir.mkdir(parents=True)
    plan_doc = {
        "status": "ok",
        "lane": "self-qualification",
        "commit": commit,
        "tree": tree,
        "candidate_count": len(rows),
        "shard": None,
        "candidates": rows,
    }
    plan_raw = (json.dumps(plan_doc, sort_keys=True) + "\n").encode()
    (artifact_dir / "b110-pilot-plan.json").write_bytes(plan_raw)
    selector = _load_module("b110_pilot_select", SELECTOR_PATH)
    selector.main(
        [
            "--plan", str(artifact_dir / "b110-pilot-plan.json"),
            "--repo-root", str(repo),
            "--out", str(artifact_dir / "b110-pilot-candidates.txt"),
            "--report", str(artifact_dir / "b110-pilot-selection.json"),
        ]
    )
    selection_doc = json.loads((artifact_dir / "b110-pilot-selection.json").read_text())
    selected_ids = selection_doc["selected_ids"]
    selection_sha256 = selection_doc["selection_sha256"]
    candidate_sha256 = hashlib.sha256(
        (artifact_dir / "b110-pilot-candidates.txt").read_bytes()
    ).hexdigest()

    deadline_path = artifact_dir / f"campaign-deadline-b110-pilot-{commit[:12]}.json"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deadline_doc = {
        "schema": "assay-campaign-deadline/1",
        "campaign": f"b110-pilot-{commit[:12]}",
        "commit": commit,
        "git_tree": tree,
        "lanes": ["self-qualification"],
        "assay_version": "8.0.0",
        "wheel_sha256": WHEEL_SHA256,
        "plan_sha256": {
            "self-qualification": plan_sha256([row["id"] for row in rows]),
        },
        "created_at_utc": (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    deadline_raw = (json.dumps(deadline_doc, sort_keys=True, indent=2) + "\n").encode()
    deadline_path.write_bytes(deadline_raw)
    deadline_sha256 = hashlib.sha256(deadline_raw).hexdigest()
    attempt_started_ns = time.time_ns()
    attempt_window = {
        "schema": "assay-b110-pilot-attempt-window/1",
        "campaign": f"b110-pilot-{commit[:12]}",
        "commit": commit,
        "started_at_epoch_ns": attempt_started_ns,
        "expires_at_epoch_ns": attempt_started_ns + 90 * 60 * 1_000_000_000,
    }
    attempt_window_raw = (json.dumps(attempt_window, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (artifact_dir / "b110-pilot-attempt-window.json").write_bytes(attempt_window_raw)

    plan_by_id = {row["id"]: row for row in rows}
    candidate_rows = []
    buckets = {name: 0 for name in MUTATION_BUCKETS}
    for identity in selected_ids:
        row = plan_by_id[identity]
        candidate_rows.append(
            {
                "id": identity,
                "path": row["path"],
                "operator": row["operator"],
                "bucket": "survived",
                "execution_mode": "full",
            }
        )
        buckets["survived"] += 1
        record = {
            "schema_version": MUTATION_STATE_SCHEMA_VERSION,
            "candidate_id": identity,
            "path": row["path"],
            "operator": row["operator"],
            "start_byte": row["start_byte"],
            "end_byte": row["end_byte"],
            "lineno": row["lineno"],
            "description": row["description"],
            "source_sha256": row["source_sha256"],
            "mutated_file_sha256": row["mutated_file_sha256"],
            "replacement_sha256": replacement_sha256_by_id[identity],
            "judge_sha256": "b" * 64,
            "outcome_bucket": "survived",
            "terminal_result": {
                "outcome": "PASS",
                "reason_code": None,
                "returncode": 0,
            },
            "execution": {"mode": "full"},
            "evidence": {
                "command": "r2",
                "collection_count": 1,
                "collection_sha256": "d" * 64,
                "hook_fingerprint_sha256": "e" * 64,
                "started_count": None,
                "failed_call_index": None,
            },
            "campaign_deadline_sha256": deadline_sha256,
            "resource_limit_evidence": ZERO_RESOURCE_EVIDENCE,
        }
        (state_dir / f"{identity}.json").write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
        )
    sentinel = {
        "schema": "assay-pilot-state/1",
        "selection_sha256": selection_sha256,
        "lane": "self-qualification",
    }
    (state_dir / "PILOT-STATE").write_text(json.dumps(sentinel) + "\n", encoding="utf-8")
    summary = {
        "schema": "assay-pilot-summary/1",
        "qualifying": False,
        "completed": True,
        "lane": "self-qualification",
        "commit": commit,
        "jobs": 3,
        "requested": len(selected_ids),
        "selection_sha256": selection_sha256,
        "judge_sha256": "b" * 64,
        "candidates_file_sha256": candidate_sha256,
        "state_dir": str(state_dir.resolve()),
        "r0": "PASS",
        "r1": "PASS",
        "r2": {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"},
        "r3": "not-run: pilot",
        "buckets": buckets,
        "candidates": candidate_rows,
        "unresolved": [],
    }
    (artifact_dir / "b110-pilot-summary.json").write_text(
        json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8"
    )
    (artifact_dir / "b110-pilot-run.log").write_text("", encoding="utf-8")
    events = [
        {
            "event": "run",
            "lane": "self-qualification",
            "commit": commit,
            "rigor": ["R0", "R1", "R2"],
        },
        {
            "event": "candidates",
            "candidate_total": len(rows),
            "selected_total": len(selected_ids),
            "pending_total": len(selected_ids),
            "commit": commit,
            "judge_sha256": "b" * 64,
            "selection_sha256": selection_sha256,
        },
        {
            "candidate_index": -1,
            "candidate_total": len(rows),
            "event": "baseline",
            "path": ".",
            "operator": "baseline",
            "start_byte": 0,
            "end_byte": 0,
            "mutated_file_sha256": "",
            "emitted_at": "2026-10-09T00:00:00Z",
            "elapsed_s": 0.1,
        },
        *[
            {
                "event": "candidate",
                "candidate_index": index,
                "candidate_total": len(selected_ids),
                "candidate_id": row["id"],
                "path": plan_by_id[row["id"]]["path"],
                "operator": plan_by_id[row["id"]]["operator"],
                "start_byte": plan_by_id[row["id"]]["start_byte"],
                "end_byte": plan_by_id[row["id"]]["end_byte"],
                "lineno": plan_by_id[row["id"]]["lineno"],
                "description": plan_by_id[row["id"]]["description"],
                "outcome_bucket": row["bucket"],
                "execution_mode": row["execution_mode"],
                "mutated_file_sha256": plan_by_id[row["id"]]["mutated_file_sha256"],
                "resource_limit_evidence": ZERO_RESOURCE_EVIDENCE,
            }
            for index, row in enumerate(candidate_rows)
        ],
        {
            "event": "end", "candidate_total": len(selected_ids), "buckets": buckets,
            "reason": None, "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.9,
        },
        {
            "event": "verdict_written",
            "outcome": "FAIL",
            "reason_code": "MUTANTS_SURVIVED",
            "exit_code": 6,
            "destination": None,
            "emitted_at": "2026-10-09T00:00:00Z",
            "elapsed_s": 1.0,
        },
    ]
    events = _producer_progress_events(events)
    (artifact_dir / "progress-b110-pilot.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return {
        "repo": repo,
        "project": project_assay,
        "artifact_dir": artifact_dir,
        "state_dir": state_dir,
        "commit": commit,
        "tree": tree,
        "selected_ids": selected_ids,
        "plan_rows": rows,
        "replacement_sha256_by_id": replacement_sha256_by_id,
        "selection_sha256": selection_sha256,
        "deadline_sha256": deadline_sha256,
        "summary": summary,
        "deadline": deadline_path.relative_to(project_assay),
        "attempt_window": artifact_dir / "b110-pilot-attempt-window.json",
    }


def _verify_fixture_with_first_hung(
    fixture: dict[str, object], *, include_trace: bool
) -> tuple[list[str], dict[str, str], dict[str, dict[str, object]]]:
    checker = _load_module("b110_pilot_report_check_hung_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity]["bucket"] = "hung"
    state_path = fixture["state_dir"] / f"{identity}.json"
    state_record = json.loads(state_path.read_text(encoding="utf-8"))
    state_record["outcome_bucket"] = "hung"
    state_record["terminal_result"] = {
        "outcome": "BUDGET_EXCEEDED",
        "reason_code": "CANDIDATE_HUNG",
        "returncode": None,
    }
    if include_trace:
        trace = _complete_hung_resource_trace()
        assert valid_hung_resource_evidence(trace)
        state_record["liveness_resource_evidence"] = trace
    state_path.write_text(json.dumps(state_record, sort_keys=True) + "\n", encoding="utf-8")
    return checker._verify_state(
        fixture["state_dir"],
        selected_ids=fixture["selected_ids"],
        rows=fixture["plan_rows"],
        dispositions=dispositions,
        selection_sha256=fixture["selection_sha256"],
        deadline_sha256=fixture["deadline_sha256"],
        expected_judge_sha256="b" * 64,
        replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
    )


def test_pilot_state_rejects_hung_candidate_without_liveness_trace(tmp_path: Path):
    fixture = _fixture(tmp_path)

    with pytest.raises(ValueError, match="valid time-aligned hang resource trace"):
        _verify_fixture_with_first_hung(fixture, include_trace=False)


def test_pilot_state_accepts_hung_candidate_with_complete_liveness_trace(tmp_path: Path):
    fixture = _fixture(tmp_path)

    _verify_fixture_with_first_hung(fixture, include_trace=True)


def test_pilot_state_requires_collection_evidence_for_a_full_kill(tmp_path: Path):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_full_kill_evidence_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity]["bucket"] = "killed"
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record["outcome_bucket"] = "killed"
    record["terminal_result"] = {
        "outcome": "FAIL",
        "reason_code": "COMMAND_FAILED",
        "returncode": 1,
    }
    record["execution"]["witness"] = {
        "node_id": "test_contract",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    del record["evidence"]
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required collection evidence"):
        checker._verify_state(
            fixture["state_dir"],
            selected_ids=fixture["selected_ids"],
            rows=fixture["plan_rows"],
            dispositions=dispositions,
            selection_sha256=fixture["selection_sha256"],
            deadline_sha256=fixture["deadline_sha256"],
            expected_judge_sha256="b" * 64,
            replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
        )


def test_pilot_state_rejects_r2_collection_evidence_for_a_full_kill(tmp_path: Path):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_full_kill_command_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity]["bucket"] = "killed"
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record["outcome_bucket"] = "killed"
    record["terminal_result"] = {
        "outcome": "FAIL",
        "reason_code": "COMMAND_FAILED",
        "returncode": 1,
    }
    record["execution"]["witness"] = {
        "node_id": "test_contract",
        "when": "call",
        "outcome": "failed",
        "session_exit_status": 1,
        "process_exit_status": 1,
    }
    assert record["execution"]["mode"] == "full"
    assert record["evidence"]["command"] == "r2"
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="collection evidence command does not match execution mode"):
        checker._verify_state(
            fixture["state_dir"],
            selected_ids=fixture["selected_ids"],
            rows=fixture["plan_rows"],
            dispositions=dispositions,
            selection_sha256=fixture["selection_sha256"],
            deadline_sha256=fixture["deadline_sha256"],
            expected_judge_sha256="b" * 64,
            replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
        )


@pytest.mark.parametrize(
    ("mode", "command", "started_count", "failed_call_index"),
    [
        ("witness-cold", "r2", 1, 0),
        ("full", "declared", None, None),
    ],
)
def test_pilot_state_rejects_non_kill_execution_witness(
    tmp_path: Path,
    mode: str,
    command: str,
    started_count: int | None,
    failed_call_index: int | None,
):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_survivor_mode_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity]["execution_mode"] = mode
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record["execution"] = {
        "mode": mode,
        "witness": {
            "node_id": "test_contract",
            "when": "call",
            "outcome": "failed",
            "session_exit_status": 1,
            "process_exit_status": 1,
        },
    }
    record["evidence"].update({
        "command": command,
        "started_count": started_count,
        "failed_call_index": failed_call_index,
    })
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-kill must use full execution without a witness"):
        checker._verify_state(
            fixture["state_dir"],
            selected_ids=fixture["selected_ids"],
            rows=fixture["plan_rows"],
            dispositions=dispositions,
            selection_sha256=fixture["selection_sha256"],
            deadline_sha256=fixture["deadline_sha256"],
            expected_judge_sha256="b" * 64,
            replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
        )


def test_pilot_state_rejects_witness_cold_kill_without_failed_prefix(
    tmp_path: Path,
):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_cold_kill_prefix_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity].update({
        "bucket": "killed",
        "execution_mode": "witness-cold",
    })
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record["outcome_bucket"] = "killed"
    record["terminal_result"] = {
        "outcome": "FAIL",
        "reason_code": "COMMAND_FAILED",
        "returncode": 1,
    }
    record["execution"] = {
        "mode": "witness-cold",
        "witness": {
            "node_id": "test_contract",
            "when": "call",
            "outcome": "failed",
            "session_exit_status": 1,
            "process_exit_status": 1,
        },
    }
    record["evidence"].update({
        "command": "r2",
        "started_count": None,
        "failed_call_index": None,
    })
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cold-witness kill lacks R2 failed-prefix evidence"):
        checker._verify_state(
            fixture["state_dir"],
            selected_ids=fixture["selected_ids"],
            rows=fixture["plan_rows"],
            dispositions=dispositions,
            selection_sha256=fixture["selection_sha256"],
            deadline_sha256=fixture["deadline_sha256"],
            expected_judge_sha256="b" * 64,
            replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
        )


def test_pilot_state_accepts_witness_cold_kill_with_failed_prefix(
    tmp_path: Path,
):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_cold_kill_accept_test", CHECKER_PATH)
    identity = fixture["selected_ids"][0]
    dispositions = {
        row["id"]: dict(row) for row in fixture["summary"]["candidates"]
    }
    dispositions[identity].update({
        "bucket": "killed",
        "execution_mode": "witness-cold",
    })
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record["outcome_bucket"] = "killed"
    record["terminal_result"] = {
        "outcome": "FAIL",
        "reason_code": "COMMAND_FAILED",
        "returncode": 1,
    }
    record["execution"] = {
        "mode": "witness-cold",
        "witness": {
            "node_id": "test_contract",
            "when": "call",
            "outcome": "failed",
            "session_exit_status": 1,
            "process_exit_status": 1,
        },
    }
    record["evidence"].update({
        "command": "r2",
        "started_count": 1,
        "failed_call_index": 0,
    })
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    _names, _hashes, records = checker._verify_state(
        fixture["state_dir"],
        selected_ids=fixture["selected_ids"],
        rows=fixture["plan_rows"],
        dispositions=dispositions,
        selection_sha256=fixture["selection_sha256"],
        deadline_sha256=fixture["deadline_sha256"],
        expected_judge_sha256="b" * 64,
        replacement_sha256_by_id=fixture["replacement_sha256_by_id"],
    )
    assert records[identity]["execution"]["mode"] == "witness-cold"


def test_hung_progress_must_carry_the_validated_liveness_trace(tmp_path: Path):
    fixture = _fixture(tmp_path)
    _checked_ids, _state_hashes, records = _verify_fixture_with_first_hung(
        fixture, include_trace=True
    )
    identity = fixture["selected_ids"][0]
    record = records[identity]
    checker = _load_module("b110_pilot_report_check_hung_progress_test", CHECKER_PATH)
    event = {
        "outcome_bucket": "hung",
        "execution_mode": record["execution"]["mode"],
        "mutated_file_sha256": record["mutated_file_sha256"],
        "resource_limit_evidence": record["resource_limit_evidence"],
    }
    progress_facts = {
        "resumed_ids": set(),
        "resumed_total": 0,
        "previous_candidates": {},
        "current_candidates": {identity: event},
    }
    dispositions = {
        identity: {"bucket": "hung", "execution_mode": record["execution"]["mode"]}
    }

    with pytest.raises(ValueError, match="progress trace differs"):
        checker._verify_resumed_progress(
            progress_facts,
            state_records={identity: record},
            dispositions=dispositions,
        )


def _run_checker(fixture: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--repo-root", str(fixture["repo"]),
            "--project-root", str(fixture["project"]),
            "--deadline", str(fixture["deadline"]),
            "--campaign", f"b110-pilot-{str(fixture['commit'])[:12]}",
            "--expected-commit", str(fixture["commit"]),
            "--expected-tree", str(fixture["tree"]),
            "--expected-wheel-sha256", WHEEL_SHA256,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{TOOLS}:{os.environ.get('PYTHONPATH', '')}",
        },
    )


def test_report_checker_accepts_inherited_assay_fd_and_emits_project_paths(
    tmp_path: Path,
):
    fixture = _fixture(tmp_path)
    project = Path(fixture["project"])
    assay_dir = project / ".assay"
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assay_info = os.fstat(assay_fd)
        pinned_assay = f"/proc/{os.getpid()}/fd/{assay_fd}"
        pinned_state = f"{pinned_assay}/b110-pilot-state"
        summary_path = assay_dir / "b110-pilot-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["state_dir"] = pinned_state
        summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
        wrong_state = tmp_path / "wrong-root" / "b110-pilot-state"
        wrong_state.parent.mkdir()
        wrong_state.mkdir()
        wrong_arguments = [
            sys.executable,
            str(CHECKER_PATH),
            "--repo-root", str(fixture["repo"]),
            "--project-root", str(project),
            "--artifact-dir", pinned_assay,
            "--state-dir", str(wrong_state),
            "--deadline", f"{pinned_assay}/{Path(fixture['deadline']).name}",
            "--assay-fd", str(assay_fd),
            "--expected-assay-device", str(assay_info.st_dev),
            "--expected-assay-inode", str(assay_info.st_ino),
            "--campaign", f"b110-pilot-{str(fixture['commit'])[:12]}",
            "--expected-commit", str(fixture["commit"]),
            "--expected-tree", str(fixture["tree"]),
            "--expected-wheel-sha256", WHEEL_SHA256,
        ]
        refused = subprocess.run(
            wrong_arguments,
            capture_output=True,
            text=True,
            timeout=30,
            pass_fds=(assay_fd,),
            env={
                **os.environ,
                "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{TOOLS}:{os.environ.get('PYTHONPATH', '')}",
            },
        )
        assert refused.returncode == 2
        assert "not a child of the pinned .assay path" in refused.stderr
        completed = subprocess.run(
            [
                sys.executable,
                str(CHECKER_PATH),
                "--repo-root", str(fixture["repo"]),
                "--project-root", str(project),
                "--artifact-dir", pinned_assay,
                "--state-dir", pinned_state,
                "--deadline", f"{pinned_assay}/{Path(fixture['deadline']).name}",
                "--assay-fd", str(assay_fd),
                "--expected-assay-device", str(assay_info.st_dev),
                "--expected-assay-inode", str(assay_info.st_ino),
                "--campaign", f"b110-pilot-{str(fixture['commit'])[:12]}",
                "--expected-commit", str(fixture["commit"]),
                "--expected-tree", str(fixture["tree"]),
                "--expected-wheel-sha256", WHEEL_SHA256,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            pass_fds=(assay_fd,),
            env={
                **os.environ,
                "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{TOOLS}:{os.environ.get('PYTHONPATH', '')}",
            },
        )
    finally:
        os.close(assay_fd)
    assert completed.returncode == 0, completed.stderr
    manifest = (assay_dir / "b110-pilot-artifacts.sha256").read_text(encoding="utf-8")
    assert ".assay/b110-pilot-plan.json" in manifest
    assert ".assay/b110-pilot-state/PILOT-STATE" in manifest


def test_report_checker_rejects_pinned_state_replacement_after_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fixture = _fixture(tmp_path)
    checker = _load_module("b110_pilot_report_check_state_swap_test", CHECKER_PATH)
    project = Path(fixture["project"])
    assay_dir = project / ".assay"
    original_state = assay_dir / "b110-pilot-state"
    archived_state = assay_dir / "b110-pilot-state-before-swap"
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    assay_info = os.fstat(assay_fd)
    pinned_assay = f"/proc/{os.getpid()}/fd/{assay_fd}"
    pinned_state = f"{pinned_assay}/b110-pilot-state"
    summary_path = assay_dir / "b110-pilot-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["state_dir"] = pinned_state
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    args = argparse.Namespace(
        repo_root=Path(fixture["repo"]),
        project_root=project,
        artifact_dir=Path(pinned_assay),
        state_dir=Path(pinned_state),
        deadline=Path(f"{pinned_assay}/{Path(fixture['deadline']).name}"),
        assay_fd=assay_fd,
        expected_assay_device=assay_info.st_dev,
        expected_assay_inode=assay_info.st_ino,
        campaign=f"b110-pilot-{str(fixture['commit'])[:12]}",
        expected_commit=str(fixture["commit"]),
        expected_tree=str(fixture["tree"]),
        expected_wheel_sha256=WHEEL_SHA256,
    )
    real_stat = os.stat
    swapped = False

    def replace_state_before_supplied_path_stat(path, *stat_args, **stat_kwargs):
        nonlocal swapped
        if os.fspath(path) == pinned_state and not swapped:
            os.rename(original_state, archived_state)
            original_state.mkdir()
            swapped = True
        return real_stat(path, *stat_args, **stat_kwargs)

    try:
        monkeypatch.setattr(os, "stat", replace_state_before_supplied_path_stat)
        with pytest.raises(
            ValueError,
            match="supplied pilot state path does not name the pinned state directory",
        ):
            checker.verify_pilot(args)
        assert swapped
        assert not (assay_dir / "b110-pilot-artifacts.sha256").exists()
    finally:
        os.close(assay_fd)
        if swapped:
            os.rmdir(original_state)
            os.rename(archived_state, original_state)


def test_b110_safe_output_opens_leaf_without_following_symlinks(tmp_path: Path):
    project = tmp_path / "project"
    assay_dir = project / ".assay"
    assay_dir.mkdir(parents=True)
    outside = tmp_path / "outside.log"
    outside.write_text("keep me\n", encoding="utf-8")
    (assay_dir / "attempt.log").symlink_to(outside)
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    assay_info = os.fstat(assay_fd)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "b110_pilot_safe_output.py"),
                "--assay-fd", str(assay_fd),
                "--expected-assay-device", str(assay_info.st_dev),
                "--expected-assay-inode", str(assay_info.st_ino),
                "--output", "attempt.log",
                "--", sys.executable, "-c", "print('must not run')",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            pass_fds=(assay_fd,),
        )
    finally:
        os.close(assay_fd)
    assert completed.returncode == 2
    assert "File exists" in completed.stderr or "Too many levels of symbolic links" in completed.stderr
    assert outside.read_text(encoding="utf-8") == "keep me\n"


def test_b110_safe_output_reader_refuses_leaf_symlink(tmp_path: Path):
    project = tmp_path / "project"
    assay_dir = project / ".assay"
    assay_dir.mkdir(parents=True)
    outside = tmp_path / "outside.log"
    outside.write_text("B110_PILOT_COMPLETED=1\n", encoding="utf-8")
    (assay_dir / "attempt.log").symlink_to(outside)
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    assay_info = os.fstat(assay_fd)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "b110_pilot_safe_output.py"),
                "--assay-fd", str(assay_fd),
                "--expected-assay-device", str(assay_info.st_dev),
                "--expected-assay-inode", str(assay_info.st_ino),
                "--read", "attempt.log",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            pass_fds=(assay_fd,),
        )
    finally:
        os.close(assay_fd)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Too many levels of symbolic links" in completed.stderr


def _host_args(fixture: dict[str, object]) -> argparse.Namespace:
    manifest = Path(fixture["artifact_dir"]) / "b110-pilot-artifacts.sha256"
    project_info = os.stat(Path(fixture["project"]), follow_symlinks=False)
    return argparse.Namespace(
        project_root=Path(fixture["project"]),
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        campaign=f"b110-pilot-{str(fixture['commit'])[:12]}",
        commit=str(fixture["commit"]),
        tree=str(fixture["tree"]),
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        container_exit=0,
        verify_published_snapshot=False,
        snapshot_sha256=None,
    )


def _load_host_checker():
    return _load_module("b110_pilot_host_check_tests", HOST_CHECK_PATH)


def _write_resumed_progress(
    fixture: dict[str, object], *, corrupt_end_buckets: bool = False, omit_prior_candidate: bool = False
) -> None:
    artifact_dir = fixture["artifact_dir"]
    plan_doc = json.loads((artifact_dir / "b110-pilot-plan.json").read_text())
    summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
    rows_by_id = {row["id"]: row for row in plan_doc["candidates"]}
    dispositions = {row["id"]: row for row in summary["candidates"]}
    selected = list(fixture["selected_ids"])
    resumed_id = selected[0]
    pending_ids = selected[1:]
    pending_buckets = {name: 0 for name in MUTATION_BUCKETS}
    for identity in pending_ids:
        pending_buckets[dispositions[identity]["bucket"]] += 1
    end_buckets = dict(pending_buckets)
    if corrupt_end_buckets:
        end_buckets["survived"] = max(0, end_buckets["survived"] - 1)
    events = [
        {
            "event": "run", "lane": "self-qualification", "commit": fixture["commit"],
            "rigor": ["R0", "R1", "R2"],
        },
        {
            "event": "candidates", "candidate_total": len(selected),
            "selected_total": len(selected), "pending_total": len(selected),
            "commit": fixture["commit"], "judge_sha256": "b" * 64,
            "selection_sha256": fixture["selection_sha256"],
        },
        {
            "candidate_index": -1, "candidate_total": len(selected),
            "event": "baseline", "path": ".", "operator": "baseline",
            "start_byte": 0, "end_byte": 0, "mutated_file_sha256": "",
            "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.1,
        },
    ]
    if not omit_prior_candidate:
        record = json.loads((fixture["state_dir"] / f"{resumed_id}.json").read_text())
        row = rows_by_id[resumed_id]
        events.append(
            {
                "event": "candidate", "candidate_index": 0, "candidate_total": len(selected),
                "candidate_id": resumed_id, "path": row["path"],
                "operator": row["operator"], "start_byte": row["start_byte"],
                "end_byte": row["end_byte"], "lineno": row["lineno"],
                "description": row["description"],
                "outcome_bucket": record["outcome_bucket"],
                "execution_mode": record["execution"]["mode"],
                "mutated_file_sha256": record["mutated_file_sha256"],
                "resource_limit_evidence": record["resource_limit_evidence"],
            }
        )
    events.extend(
        [
            {
                "event": "run", "lane": "self-qualification", "commit": fixture["commit"],
                "rigor": ["R0", "R1", "R2"],
            },
            {
                "event": "resume", "candidate_total": len(selected),
                "resumed_total": 1, "rejected_total": 0, "rejudged_total": 0,
            },
        {
            "event": "candidates", "candidate_total": len(selected),
            "selected_total": len(selected), "pending_total": len(pending_ids),
            "commit": fixture["commit"], "judge_sha256": "b" * 64,
            "selection_sha256": fixture["selection_sha256"],
            },
            {
                "candidate_index": -1, "candidate_total": len(selected),
                "event": "baseline", "path": ".", "operator": "baseline",
                "start_byte": 0, "end_byte": 0, "mutated_file_sha256": "",
                "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.1,
            },
        ]
    )
    events.extend(
        {
            "event": "candidate", "candidate_index": index, "candidate_total": len(pending_ids),
            "candidate_id": identity, "path": rows_by_id[identity]["path"],
            "operator": rows_by_id[identity]["operator"],
            "start_byte": rows_by_id[identity]["start_byte"],
            "end_byte": rows_by_id[identity]["end_byte"],
            "lineno": rows_by_id[identity]["lineno"],
            "description": rows_by_id[identity]["description"],
            "outcome_bucket": dispositions[identity]["bucket"],
            "execution_mode": dispositions[identity]["execution_mode"],
            "mutated_file_sha256": rows_by_id[identity]["mutated_file_sha256"],
            "resource_limit_evidence": ZERO_RESOURCE_EVIDENCE,
        }
        for index, identity in enumerate(pending_ids)
    )
    events.extend(
        [
            {
                "event": "end", "candidate_total": len(pending_ids),
                "buckets": end_buckets, "reason": None,
                "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.9,
            },
            {"event": "resume_merged", "resumed_total": 1},
            {
                "event": "verdict_written",
                "outcome": "FAIL",
                "reason_code": "MUTANTS_SURVIVED",
                "exit_code": 6,
                "destination": None,
                "emitted_at": "2026-10-09T00:00:00Z",
                "elapsed_s": 1.0,
            },
        ]
    )
    events = _producer_progress_events(events)
    (artifact_dir / "progress-b110-pilot.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_b110_pilot_checker_attests_a_complete_source_bound_run(tmp_path: Path):
    fixture = _fixture(tmp_path)

    result = _run_checker(fixture)

    assert result.returncode == 0, result.stderr
    assert "B110_PILOT_VERIFIED=1" in result.stdout
    assert "B110_PILOT_ATTESTATION_SHA256=" in result.stdout
    assert (fixture["artifact_dir"] / "b110-pilot-artifacts.sha256").is_file()


def test_progress_candidate_total_is_the_selected_pilot_size_not_full_plan(tmp_path: Path):
    checker = _load_module("b110_pilot_report_check_selected_total_test", CHECKER_PATH)
    identities = ["a" * 64, "c" * 64]
    commit = "d" * 40
    selection_digest = plan_sha256(identities)
    rows = [
        {
            "id": identity,
            "path": "assay/src/example.py",
            "lineno": index + 1,
            "operator": "python:compare-swap",
            "description": f"candidate {index}",
            "start_byte": index,
            "end_byte": index + 1,
            "mutated_file_sha256": f"{index + 1:064x}",
        }
        for index, identity in enumerate(identities)
    ]
    buckets = {name: 0 for name in MUTATION_BUCKETS}
    buckets["survived"] = 2
    events = [
        {
            "event": "run", "lane": "self-qualification", "commit": commit,
            "rigor": ["R0", "R1", "R2"],
        },
        {
            "event": "candidates", "commit": commit, "candidate_total": 2,
            "selected_total": 2, "pending_total": 2, "judge_sha256": "b" * 64,
            "selection_sha256": selection_digest,
        },
        {
            "candidate_index": -1, "candidate_total": 2, "event": "baseline",
            "path": ".", "operator": "baseline", "start_byte": 0,
            "end_byte": 0, "mutated_file_sha256": "",
            "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.1,
        },
        *[
            {
                "event": "candidate", "candidate_id": row["id"],
                "candidate_index": index, "candidate_total": 2,
                "path": row["path"], "lineno": row["lineno"],
                "operator": row["operator"], "description": row["description"],
                "start_byte": row["start_byte"], "end_byte": row["end_byte"],
                "mutated_file_sha256": row["mutated_file_sha256"],
                "outcome_bucket": "survived", "execution_mode": "full",
                "resource_limit_evidence": ZERO_RESOURCE_EVIDENCE,
            }
            for index, row in enumerate(rows)
        ],
        {
            "event": "end", "candidate_total": 2, "buckets": buckets,
            "reason": None,
            "emitted_at": "2026-10-09T00:00:00Z", "elapsed_s": 0.9,
        },
        {
            "event": "verdict_written", "outcome": "FAIL",
            "reason_code": "MUTANTS_SURVIVED", "exit_code": 6,
            "destination": None, "emitted_at": "2026-10-09T00:00:00Z",
            "elapsed_s": 1.0,
        },
    ]
    progress_path = tmp_path / "progress.jsonl"
    events = _producer_progress_events(events)
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    facts = checker._verify_progress(
        progress_path,
        expected_commit=commit,
        selection_sha256=selection_digest,
        selected_ids=set(identities),
        plan_rows=rows,
        plan_total=100,
        expected_judge_sha256="b" * 64,
        dispositions={
            identity: {"bucket": "survived", "execution_mode": "full"}
            for identity in identities
        },
        summary={"r2": {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}},
    )

    assert facts["pending_total"] == 2


def test_progress_requires_the_baseline_event(tmp_path: Path):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    events = [event for event in events if event.get("event") != "baseline"]
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "exactly one baseline event" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("emitted_at", "not-a-timestamp"), ("elapsed_s", -1.0)],
)
def test_progress_baseline_requires_valid_timing_fields(
    tmp_path: Path, field: str, replacement: object
):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    baseline = next(event for event in events if event.get("event") == "baseline")
    baseline[field] = replacement
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "baseline event is missing, malformed, or out of order" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


def test_progress_baseline_requires_emitted_timestamp(tmp_path: Path):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    baseline = next(event for event in events if event.get("event") == "baseline")
    del baseline["emitted_at"]
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "baseline event is missing, malformed, or out of order" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


def test_progress_rejects_a_conflicting_second_terminal_verdict(tmp_path: Path):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    duplicate = dict(events[-1])
    duplicate["outcome"] = "PASS"
    duplicate["reason_code"] = None
    events.insert(len(events) - 1, duplicate)
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "exactly one terminal verdict event" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


@pytest.mark.parametrize(
    "damage", ["missing-emitted-at", "malformed-emitted-at", "negative-elapsed"]
)
def test_progress_end_requires_valid_timing_fields(tmp_path: Path, damage: str):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    end = next(event for event in events if event.get("event") == "end")
    if damage == "missing-emitted-at":
        del end["emitted_at"]
    elif damage == "malformed-emitted-at":
        end["emitted_at"] = "not-a-timestamp"
    else:
        end["elapsed_s"] = -1.0
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "progress end event" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("path", "assay/src/other.py"),
        ("operator", "python:boolop-swap"),
        ("start_byte", 999999),
        ("end_byte", 999999),
        ("lineno", 999999),
        ("description", "a different mutation site"),
    ],
)
def test_progress_candidate_site_metadata_must_match_plan(
    tmp_path: Path, field: str, replacement: object
):
    fixture = _fixture(tmp_path)
    progress_path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in progress_path.read_text().splitlines()]
    candidate = next(event for event in events if event.get("event") == "candidate")
    candidate[field] = replacement
    progress_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "differs from the source plan" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("start_byte", 999999),
        ("end_byte", 999999),
        ("lineno", 999999),
        ("description", "a different mutation site"),
    ],
)
def test_state_record_site_metadata_must_match_plan(
    tmp_path: Path, field: str, replacement: object
):
    fixture = _fixture(tmp_path)
    identity = fixture["selected_ids"][0]
    state_path = fixture["state_dir"] / f"{identity}.json"
    record = json.loads(state_path.read_text(encoding="utf-8"))
    record[field] = replacement
    state_path.write_text(json.dumps(record), encoding="utf-8")

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "stale or disagrees with the source plan or summary" in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout


def test_host_publishes_and_reverifies_an_authoritative_snapshot(tmp_path: Path):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)

    snapshot_sha256 = host.verify_and_publish(args)

    snapshot = Path(fixture["artifact_dir"]) / "b110-pilot-evidence"
    assert (snapshot / "snapshot.sha256").is_file()
    assert (snapshot / "b110-pilot-state" / "PILOT-STATE").is_file()
    args.snapshot_sha256 = snapshot_sha256
    assert host.verify_published_snapshot(args) == snapshot_sha256

    # The live resume files are not the retained evidence once the snapshot
    # has been published; changing one cannot change the verified snapshot.
    (Path(fixture["artifact_dir"]) / "b110-pilot-summary.json").write_text("changed live file\n")
    assert host.verify_published_snapshot(args) == snapshot_sha256

    summary_path = snapshot / "b110-pilot-summary.json"
    assert summary_path.stat().st_mode & 0o222 == 0
    summary_path.chmod(0o600)
    summary_path.write_text("changed snapshot\n")
    with pytest.raises(ValueError, match="evidence is not owner-only read-only"):
        host.verify_published_snapshot(args)


def test_host_snapshot_publication_does_not_replace_a_racing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    original_rename = host._rename_noreplace
    published_path = Path(fixture["artifact_dir"]) / "b110-pilot-evidence"
    raced_identity: tuple[int, int] | None = None
    raced = False

    def create_competing_directory_before_publish(source, destination, **kwargs):
        nonlocal raced_identity, raced
        if (
            isinstance(source, str)
            and source.startswith(".b110-pilot-evidence.stage.")
            and destination == host._SNAPSHOT_NAME
            and not raced
        ):
            parent_fd = kwargs["dst_dir_fd"]
            os.mkdir(destination, 0o700, dir_fd=parent_fd)
            info = os.stat(destination, dir_fd=parent_fd, follow_symlinks=False)
            raced_identity = (info.st_dev, info.st_ino)
            raced = True
        return original_rename(source, destination, **kwargs)

    monkeypatch.setattr(host, "_rename_noreplace", create_competing_directory_before_publish)
    with pytest.raises(FileExistsError):
        host.verify_and_publish(args)

    assert raced and raced_identity is not None
    published_info = published_path.stat()
    assert (published_info.st_dev, published_info.st_ino) == raced_identity
    assert list(published_path.iterdir()) == []
    assert not list(Path(fixture["artifact_dir"]).glob(".b110-pilot-evidence.stage.*"))
    assert not (Path(fixture["artifact_dir"]) / "b110-pilot-evidence.receipt.json").exists()


def test_prior_output_cleanup_does_not_follow_a_replaced_assay_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = _load_host_checker()
    project = tmp_path / "assay"
    project.mkdir()
    state = project / ".assay"
    state.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    for name in ("verdict-b110-screen.json", "b110-screen-plan.json", "b110-screen-run.log"):
        (state / name).write_text("old attempt\n", encoding="utf-8")
        (outside / name).write_text("unrelated file\n", encoding="utf-8")

    project_info = project.stat()
    state_info = state.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_assay_device=state_info.st_dev,
        expected_assay_inode=state_info.st_ino,
        expected_assay_absent=False,
        clear_prior_outputs="b110-screen",
    )
    original_check = host._check_same_directory_path
    replaced = False

    def replace_state_after_path_check(parent_fd, name, descriptor, original, **kwargs):
        nonlocal replaced
        original_check(parent_fd, name, descriptor, original, **kwargs)
        if name == ".assay" and not replaced:
            os.rename(".assay", ".assay-held", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.symlink(outside, ".assay", dir_fd=parent_fd)
            replaced = True

    monkeypatch.setattr(host, "_check_same_directory_path", replace_state_after_path_check)
    with pytest.raises(ValueError, match=r"directory path changed during host verification: \.assay"):
        host.clear_prior_outputs(args)

    assert replaced
    assert not (project / ".assay-held" / "verdict-b110-screen.json").exists()
    assert all((outside / name).read_text(encoding="utf-8") == "unrelated file\n" for name in (
        "verdict-b110-screen.json",
        "b110-screen-plan.json",
        "b110-screen-run.log",
    ))


def test_prior_output_cleanup_prepares_a_private_assay_directory_when_absent(tmp_path: Path):
    host = _load_host_checker()
    project = tmp_path / "assay"
    project.mkdir()
    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_assay_device=None,
        expected_assay_inode=None,
        expected_assay_absent=True,
        clear_prior_outputs="b110-pilot",
    )

    removed, device, inode = host.clear_prior_outputs(args)

    state_info = (project / ".assay").stat(follow_symlinks=False)
    assert removed == 0
    assert stat.S_ISDIR(state_info.st_mode)
    assert stat.S_IMODE(state_info.st_mode) == 0o700
    assert (device, inode) == (state_info.st_dev, state_info.st_ino)


def test_prior_output_cleanup_refuses_a_directory_created_before_atomic_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    host = _load_host_checker()
    project = tmp_path / "assay"
    project.mkdir()
    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_assay_device=None,
        expected_assay_inode=None,
        expected_assay_absent=True,
        clear_prior_outputs="b110-pilot",
    )
    original_rename = host._rename_noreplace
    appeared = False

    def race_state_path(source, destination, *, src_dir_fd, dst_dir_fd):
        nonlocal appeared
        if destination == ".assay" and not appeared:
            os.mkdir(".assay", 0o700, dir_fd=dst_dir_fd)
            appeared = True
        return original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(host, "_rename_noreplace", race_state_path)
    with pytest.raises(FileExistsError):
        host.clear_prior_outputs(args)

    assert appeared
    assert (project / ".assay").is_dir()
    assert not list(project.glob(".assay.b110-init.*"))


def test_host_reverification_rejects_a_same_uid_write_between_hash_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    snapshot_sha256 = host.verify_and_publish(args)
    args.snapshot_sha256 = snapshot_sha256

    summary_path = Path(fixture["artifact_dir"]) / "b110-pilot-evidence" / "b110-pilot-summary.json"
    summary_info = summary_path.stat()
    original_hash = host._hash_fd
    raced = False
    writer_errors: list[BaseException] = []

    def race_writer_after_first_hash(descriptor: int, *, label: str) -> str:
        nonlocal raced
        digest = original_hash(descriptor, label=label)
        current = os.fstat(descriptor)
        if not raced and (current.st_dev, current.st_ino) == (summary_info.st_dev, summary_info.st_ino):
            raced = True

            def overwrite_from_same_uid() -> None:
                try:
                    summary_path.chmod(0o600)
                    summary_path.write_text("concurrent same-uid mutation\n", encoding="utf-8")
                    summary_path.chmod(0o400)
                except BaseException as exc:
                    writer_errors.append(exc)

            writer = threading.Thread(target=overwrite_from_same_uid)
            writer.start()
            writer.join(timeout=2)
            assert not writer.is_alive(), "evidence writer did not finish"

        return digest

    monkeypatch.setattr(host, "_hash_fd", race_writer_after_first_hash)
    with pytest.raises(ValueError, match="snapshot changed during host verification"):
        host.verify_published_snapshot(args)
    assert raced
    assert not writer_errors


def test_host_reverification_rejects_a_snapshot_mode_change_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    summary_path = Path(fixture["artifact_dir"]) / "b110-pilot-evidence" / "b110-pilot-summary.json"
    original_hash = host._hash_fd
    changed = False

    def make_summary_writable_after_hash(descriptor: int, *, label: str) -> str:
        nonlocal changed
        digest = original_hash(descriptor, label=label)
        if label == "snapshot artifact b110-pilot-summary.json" and not changed:
            os.fchmod(descriptor, 0o600)
            changed = True
        return digest

    monkeypatch.setattr(host, "_hash_fd", make_summary_writable_after_hash)
    with pytest.raises(ValueError, match="owner-only read-only"):
        host.verify_published_snapshot(args)
    assert changed
    assert summary_path.stat().st_mode & 0o200


def test_host_completion_boundary_rechecks_child_modes_after_receipt_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    summary_path = Path(fixture["artifact_dir"]) / "b110-pilot-evidence" / "b110-pilot-summary.json"
    original_verify = host._verify_snapshot_directory
    verifications = 0
    changed = False

    def change_mode_after_first_snapshot_pass(*verify_args, **kwargs):
        nonlocal verifications, changed
        verifications += 1
        result = original_verify(*verify_args, **kwargs)
        if verifications == 1:
            summary_path.chmod(0o600)
            changed = True
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", change_mode_after_first_snapshot_pass)
    with pytest.raises(ValueError, match="owner-only read-only"):
        host.verify_published_snapshot(args)

    assert changed and verifications == 2
    assert summary_path.stat().st_mode & 0o200


def test_host_completion_boundary_rechecks_assay_identity_after_final_child_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    assay_dir = Path(fixture["project"]) / ".assay"
    original_verify = host._verify_snapshot_directory
    verifications = 0

    def replace_assay_after_final_child_pass(*verify_args, **kwargs):
        nonlocal verifications
        result = original_verify(*verify_args, **kwargs)
        verifications += 1
        if verifications == 2:
            assay_dir.rename(assay_dir.with_name(".assay-original"))
            assay_dir.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", replace_assay_after_final_child_pass)
    with pytest.raises(ValueError, match=r"pilot directory path changed during host verification: \.assay"):
        host.verify_published_snapshot(args)

    assert verifications == 2


def test_host_completion_boundary_rechecks_snapshot_name_after_final_child_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    snapshot_path = Path(fixture["artifact_dir"]) / "b110-pilot-evidence"
    original_verify = host._verify_snapshot_directory
    verifications = 0
    replaced = False

    def replace_snapshot_after_final_child_pass(*verify_args, **kwargs):
        nonlocal verifications, replaced
        result = original_verify(*verify_args, **kwargs)
        verifications += 1
        if verifications == 2:
            snapshot_path.rename(snapshot_path.with_name(".b110-pilot-evidence-original"))
            snapshot_path.mkdir(mode=0o500)
            replaced = True
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", replace_snapshot_after_final_child_pass)
    with pytest.raises(
        ValueError,
        match=r"pilot directory path changed during host verification: b110-pilot-evidence",
    ):
        host.verify_published_snapshot(args)

    assert replaced and verifications == 2


def test_host_completion_boundary_rechecks_source_after_final_child_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    config_path = Path(fixture["project"]) / "assay.toml"
    original_verify = host._verify_snapshot_directory
    verifications = 0
    changed = False

    def change_source_after_final_child_pass(*verify_args, **kwargs):
        nonlocal verifications, changed
        result = original_verify(*verify_args, **kwargs)
        verifications += 1
        if verifications == 2:
            config_path.write_text(config_path.read_text(encoding="utf-8") + "# changed\n")
            changed = True
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", change_source_after_final_child_pass)
    with pytest.raises(
        ValueError,
        match="selected worktree became dirty during final B110 host verification",
    ):
        host.verify_published_snapshot(args)

    assert changed and verifications == 2


@pytest.mark.parametrize(
    ("target", "expected_error"),
    [
        ("snapshot", r"pilot directory path changed during host verification: b110-pilot-evidence"),
        ("receipt", r"pilot artifact path changed during host verification: b110-pilot-evidence\.receipt\.json"),
    ],
)
def test_host_completion_rechecks_public_paths_after_git_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_error: str,
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    artifact_dir = Path(fixture["artifact_dir"])
    snapshot_path = artifact_dir / "b110-pilot-evidence"
    receipt_path = artifact_dir / "b110-pilot-evidence.receipt.json"
    original_source_check = host._verify_current_source_identity
    changed = False

    def replace_public_path_after_git_check(project: Path, commit: str, tree: str):
        nonlocal changed
        result = original_source_check(project, commit, tree)
        if target == "snapshot":
            snapshot_path.rename(snapshot_path.with_name(".b110-pilot-evidence-original"))
            snapshot_path.mkdir(mode=0o500)
        else:
            receipt_path.rename(receipt_path.with_name(".b110-pilot-evidence.receipt-original"))
            receipt_path.write_bytes(b"replacement receipt\n")
            receipt_path.chmod(0o400)
        changed = True
        return result

    monkeypatch.setattr(host, "_verify_current_source_identity", replace_public_path_after_git_check)
    with pytest.raises(ValueError, match=expected_error):
        host.verify_published_snapshot(args)

    assert changed


def test_host_completion_boundary_rechecks_deadline_after_final_child_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    args.snapshot_sha256 = host.verify_and_publish(args)
    window = json.loads(Path(fixture["attempt_window"]).read_text(encoding="utf-8"))
    original_verify = host._verify_snapshot_directory
    verifications = 0

    def expire_after_final_child_pass(*verify_args, **kwargs):
        nonlocal verifications
        result = original_verify(*verify_args, **kwargs)
        verifications += 1
        if verifications == 2:
            monkeypatch.setattr(host.time, "time_ns", lambda: window["expires_at_epoch_ns"])
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", expire_after_final_child_pass)
    with pytest.raises(ValueError, match="90-minute pilot attempt"):
        host.verify_published_snapshot(args)

    assert verifications == 2


def test_host_rejects_project_replacement_before_it_opens_the_root(tmp_path: Path):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    args = _host_args(fixture)
    project = Path(fixture["project"])
    project.rename(project.with_name("assay-original"))
    project.mkdir()

    with pytest.raises(ValueError, match="pre-container identity"):
        host.verify_and_publish(args)


def test_host_rechecks_the_canonical_project_path_during_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    project = Path(fixture["project"])
    original_verify = host._verify_snapshot_directory

    def replace_project_after_snapshot_verification(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        project.rename(project.with_name("assay-original"))
        project.mkdir()
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", replace_project_after_snapshot_verification)
    with pytest.raises(ValueError, match="pilot project path changed"):
        host.verify_and_publish(_host_args(fixture))


def test_host_rejects_a_late_extra_live_state_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(tmp_path)
    checked = _run_checker(fixture)
    assert checked.returncode == 0, checked.stderr
    host = _load_host_checker()
    original_verify = host._verify_snapshot_directory

    def add_extra_state_after_snapshot_verification(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        (Path(fixture["state_dir"]) / ("f" * 64 + ".json")).write_text("late\n")
        return result

    monkeypatch.setattr(host, "_verify_snapshot_directory", add_extra_state_after_snapshot_verification)
    with pytest.raises(ValueError, match="live pilot state inventory differs"):
        host.verify_and_publish(_host_args(fixture))


@pytest.mark.parametrize(
    ("damage", "expected_error"),
    [
        ("summary-refusal", "missing or unknown fields"),
        ("summary-incomplete", "complete source-bound"),
        ("summary-r0-fail", "complete source-bound"),
        ("r2-pass-with-survivor", "R2 result differs from the validated candidate outcomes"),
        ("missing-state", "pilot state inventory differs"),
        ("missing-execution", "has no execution evidence"),
        ("missing-replacement-hash", "replacement_sha256 must be"),
        ("wrong-replacement-hash", "different replacement identity"),
        ("wrong-mutated-hash", "stale or disagrees with the source plan or summary"),
        ("omitted-configured-target", "complete committed whole-target lane"),
        ("wrong-job-count", "registered three-job pilot"),
        ("coherent-wrong-judge", "differs from the in-memory summary"),
        ("current-selection-digest", "selection digest differs"),
        ("current-resource-mismatch", "resource evidence differs from its state record"),
        ("state-wrong-judge", "belongs to a different judge"),
        ("progress-wrong-judge", "differs from the in-memory summary"),
        ("resource-limit-hit", "records a process or memory limit event"),
        ("progress-exit", "terminal exit-6 verdict"),
        ("progress-destination", "terminal exit-6 verdict"),
        ("progress-wrong-verdict-outcome", "terminal verdict differs from the validated summary"),
        ("progress-wrong-verdict-reason", "terminal verdict differs from the validated summary"),
        ("progress-end-before-candidate", "mutation end event precedes its candidate dispositions"),
        ("progress-too-many-events", "more than 50000 events"),
        ("progress-line-too-long", "exceeds 65536 bytes"),
        ("wrong-wheel", "campaign deadline does not bind"),
        ("wrong-campaign-duration", "not the active two-hour pilot campaign"),
        ("boolean-bucket-count", "bucket counts do not match"),
        ("empty-summary", "valid UTF-8 JSON"),
    ],
)
def test_b110_pilot_checker_rejects_exit_six_without_complete_evidence(
    tmp_path: Path, damage: str, expected_error: str
):
    fixture = _fixture(tmp_path)
    artifact_dir = fixture["artifact_dir"]
    if damage == "summary-refusal":
        summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
        summary["refusal"] = {"status": "ERROR", "reason_code": "R0_FAILED"}
        (artifact_dir / "b110-pilot-summary.json").write_text(json.dumps(summary))
    elif damage in {"summary-incomplete", "summary-r0-fail"}:
        summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
        if damage == "summary-incomplete":
            summary["completed"] = False
        else:
            summary["r0"] = "FAIL"
        (artifact_dir / "b110-pilot-summary.json").write_text(json.dumps(summary))
    elif damage == "r2-pass-with-survivor":
        summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
        summary["r2"] = {"status": "PASS", "reason_code": None}
        (artifact_dir / "b110-pilot-summary.json").write_text(json.dumps(summary))
    elif damage == "missing-state":
        identity = fixture["selected_ids"][0]
        (fixture["state_dir"] / f"{identity}.json").unlink()
    elif damage in {
        "missing-execution", "missing-replacement-hash", "wrong-replacement-hash",
        "wrong-mutated-hash", "state-wrong-judge"
    }:
        identity = fixture["selected_ids"][0]
        path = fixture["state_dir"] / f"{identity}.json"
        record = json.loads(path.read_text())
        if damage == "missing-execution":
            del record["execution"]
        elif damage == "missing-replacement-hash":
            del record["replacement_sha256"]
        elif damage == "wrong-replacement-hash":
            record["replacement_sha256"] = "0" * 64
        elif damage == "wrong-mutated-hash":
            record["mutated_file_sha256"] = "0" * 64
        else:
            record["judge_sha256"] = "a" * 64
        path.write_text(json.dumps(record))
    elif damage == "resource-limit-hit":
        identity = fixture["selected_ids"][0]
        path = fixture["state_dir"] / f"{identity}.json"
        record = json.loads(path.read_text())
        record["resource_limit_evidence"]["pids_events"]["max"]["delta"] = 1
        record["resource_limit_evidence"]["pids_events"]["max"]["after"] = 1
        path.write_text(json.dumps(record))
    elif damage == "progress-exit":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        lines[-1]["exit_code"] = 4
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage == "progress-destination":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        lines[-1]["destination"] = ".assay/verdict.json"
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage in {"progress-wrong-verdict-outcome", "progress-wrong-verdict-reason"}:
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        if damage == "progress-wrong-verdict-outcome":
            lines[-1]["outcome"] = "PASS"
        else:
            lines[-1]["reason_code"] = "R0_FAIL"
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage == "progress-end-before-candidate":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        end = next(line for line in lines if line["event"] == "end")
        lines.remove(end)
        candidates_index = next(
            index for index, line in enumerate(lines) if line["event"] == "candidates"
        )
        lines.insert(candidates_index + 1, end)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage == "progress-too-many-events":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        path.write_bytes(b'{"event":"prior"}\n' * 50001)
    elif damage == "progress-line-too-long":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        path.write_bytes(b'{"event":"run","padding":"' + b"x" * 65536 + b'"}\n')
    elif damage == "progress-wrong-judge":
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        next(line for line in lines if line["event"] == "candidates")["judge_sha256"] = "a" * 64
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage == "omitted-configured-target":
        path = artifact_dir / "b110-pilot-plan.json"
        plan = json.loads(path.read_text())
        plan["candidates"] = [
            row for row in plan["candidates"] if row["path"] != BOOL_PATH
        ]
        plan["candidate_count"] = len(plan["candidates"])
        path.write_text(json.dumps(plan, sort_keys=True) + "\n")
    elif damage == "wrong-job-count":
        summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
        summary["jobs"] = 1
        (artifact_dir / "b110-pilot-summary.json").write_text(json.dumps(summary))
    elif damage == "coherent-wrong-judge":
        for path in fixture["state_dir"].glob("*.json"):
            if path.name == "PILOT-STATE":
                continue
            record = json.loads(path.read_text())
            record["judge_sha256"] = "a" * 64
            path.write_text(json.dumps(record))
        progress_path = artifact_dir / "progress-b110-pilot.jsonl"
        events = [json.loads(line) for line in progress_path.read_text().splitlines()]
        next(event for event in events if event.get("event") == "candidates")[
            "judge_sha256"
        ] = "a" * 64
        progress_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    elif damage in {"current-selection-digest", "current-resource-mismatch"}:
        path = artifact_dir / "progress-b110-pilot.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        if damage == "current-selection-digest":
            next(line for line in lines if line["event"] == "candidates")["selection_sha256"] = "0" * 64
        else:
            event = next(line for line in lines if line["event"] == "candidate")
            event["resource_limit_evidence"]["pids_events"]["max"] = {
                "before": 1, "after": 1, "delta": 0,
            }
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    elif damage == "wrong-wheel":
        path = next(artifact_dir.glob("campaign-deadline-b110-pilot-*.json"))
        deadline = json.loads(path.read_text())
        deadline["wheel_sha256"] = "d" * 64
        path.write_text(json.dumps(deadline))
    elif damage == "wrong-campaign-duration":
        path = next(artifact_dir.glob("campaign-deadline-b110-pilot-*.json"))
        deadline = json.loads(path.read_text())
        created = datetime.strptime(deadline["created_at_utc"], "%Y-%m-%dT%H:%M:%SZ")
        deadline["expires_at_utc"] = (created + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(json.dumps(deadline))
    elif damage == "boolean-bucket-count":
        summary = json.loads((artifact_dir / "b110-pilot-summary.json").read_text())
        summary["buckets"]["dead"] = False
        (artifact_dir / "b110-pilot-summary.json").write_text(json.dumps(summary))
    else:
        (artifact_dir / "b110-pilot-summary.json").write_text("")

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert "B110_PILOT_VERIFIED=1" not in result.stdout
    assert not (artifact_dir / "b110-pilot-artifacts.sha256").exists()


def test_b110_pilot_checker_accepts_resume_only_with_same_judge_progress(tmp_path: Path):
    fixture = _fixture(tmp_path)
    _write_resumed_progress(fixture)

    result = _run_checker(fixture)

    assert result.returncode == 0, result.stderr
    assert "B110_PILOT_VERIFIED=1" in result.stdout


@pytest.mark.parametrize(
    "damage", ["pending-buckets", "missing-prior-candidate", "wrong-prior-selection"]
)
def test_b110_pilot_checker_rejects_resume_progress_that_cannot_support_summary(
    tmp_path: Path, damage: str
):
    fixture = _fixture(tmp_path)
    _write_resumed_progress(
        fixture,
        corrupt_end_buckets=damage == "pending-buckets",
        omit_prior_candidate=damage == "missing-prior-candidate",
    )
    if damage == "wrong-prior-selection":
        path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        next(
            event for event in events
            if event["event"] == "candidates"
        )["selection_sha256"] = "0" * 64
        path.write_text("".join(json.dumps(event) + "\n" for event in events))

    result = _run_checker(fixture)

    assert result.returncode == 2
    if damage == "pending-buckets":
        assert "pending totals disagree" in result.stderr
    else:
        assert "no same-selection progress disposition" in result.stderr


@pytest.mark.parametrize(
    ("event_name", "field", "damage"),
    [
        ("resume", "emitted_at", "missing"),
        ("resume", "elapsed_s", "negative"),
        ("candidate", "emitted_at", "missing"),
    ],
)
def test_b110_pilot_checker_rejects_progress_events_without_producer_timing(
    tmp_path: Path, event_name: str, field: str, damage: str
):
    fixture = _fixture(tmp_path)
    _write_resumed_progress(fixture)
    path = fixture["artifact_dir"] / "progress-b110-pilot.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    event = next(item for item in events if item["event"] == event_name)
    if damage == "missing":
        del event[field]
    else:
        event[field] = -1.0
    path.write_text("".join(json.dumps(item) + "\n" for item in events))

    result = _run_checker(fixture)

    assert result.returncode == 2
    assert "malformed event timing" in result.stderr
