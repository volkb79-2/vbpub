"""assay's own ``assay.toml`` loads under the loader assay ships.

This is O2's negative made mechanical. The ordinary release lane remains
R0-only; B105 adds a separate full-source R0-R3 qualification lane. This
module keeps both lane declarations loadable and checks that their distinct
roles do not drift.

It also cross-checks the two files that have to agree about assay's own gate:
``assay.toml`` (WHAT) and ``nyxloom-trove/nyxloom.toml`` (WHERE). Reading the
second is deliberate — a lane whose budget silently disagrees with the gate that
enforces it is a fact split across two files with nothing holding it together.
"""

from __future__ import annotations

import io
import json
import tomllib

import pytest

from conftest import PROJECT_ROOT, pytest_sessionfinish as archive_b105_coverage

from assay.cli import main
from assay.config import load_lane_file

SELF_LANE_FILE = PROJECT_ROOT / "assay.toml"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"
GATE_ID = "tester-unified"
QUALIFICATION_ID = "self-qualification"
PREFLIGHT_ID = "self-qualification-preflight"
RUN_GATE_TOML = PROJECT_ROOT / "run-gate.toml"


def test_assays_own_lane_file_loads():
    lane_file = load_lane_file(SELF_LANE_FILE)

    assert lane_file.schema_version == 2
    assert lane_file.project_root == PROJECT_ROOT
    assert list(lane_file.lanes) == [GATE_ID, QUALIFICATION_ID, PREFLIGHT_ID]


def test_ordinary_release_lane_stays_r0_only_with_no_judge_table():
    # A-046/A-133 still govern the ordinary release gate. B105's new lane is
    # the explicit place where self-qualification policy is declared.
    lane = load_lane_file(SELF_LANE_FILE).lane(GATE_ID)

    assert lane.scope == "S1"
    assert lane.rigor == ("R0",)
    assert lane.enforcement == "gate"
    assert lane.judge is None
    assert lane.argv[0] == "python"
    assert "PATH" in lane.env_passthrough


def test_assays_own_lane_declares_all_eight_required_fields():
    declared = load_lane_file(SELF_LANE_FILE).lane(GATE_ID).as_declared()
    raw = tomllib.loads(SELF_LANE_FILE.read_text(encoding="utf-8"))

    assert declared == raw["lanes"][GATE_ID]


def test_lane_name_matches_the_gate_id_p11_requires():
    gates = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"]

    assert GATE_ID in gates
    assert GATE_ID in load_lane_file(SELF_LANE_FILE).lanes


def test_lane_budget_agrees_with_the_gate_timeout():
    # READ, do not invent (§4.2a): the budget is the gate's own
    # timeout_seconds, so the two files cannot drift apart unnoticed.
    gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"][GATE_ID]
    lane = load_lane_file(SELF_LANE_FILE).lane(GATE_ID)

    assert lane.budget_seconds == float(gate["timeout_seconds"])


def test_assay_lanes_lists_assays_own_lane(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)
    out, err = io.StringIO(), io.StringIO()

    code = main(["lanes"], stdout=out, stderr=err)

    assert code == 0
    assert err.getvalue() == ""
    assert GATE_ID in out.getvalue()
    assert QUALIFICATION_ID in out.getvalue()
    assert PREFLIGHT_ID in out.getvalue()
    assert "judge=none" in out.getvalue()


def test_self_qualification_is_full_source_r0_through_r3():
    lane_file = load_lane_file(SELF_LANE_FILE)
    lane = lane_file.lane(QUALIFICATION_ID)
    assert lane.scope == "S1"
    assert lane.rigor == ("R0", "R1", "R2", "R3")
    assert lane.enforcement == "gate"
    assert lane.judge is not None
    assert lane.judge.mode == "whole_target"
    assert lane.isolation is not None
    assert lane.isolation.snapshot_history == "full"
    assert lane.judge.require_branch is True
    assert lane.judge.fail_under == 100.0
    assert lane.judge.mutation is not None
    assert lane.judge.mutation.jobs == 1
    assert lane.judge.mutation.max_mutants == 10000
    assert lane.judge.mutation.shard_index is None
    assert lane.judge.mutation.shard_count is None
    assert lane.judge.mutation.budget_per_candidate == "auto"
    assert lane.judge.mutation.liveness == "true"
    assert lane.judge.canary is not None
    assert lane.judge.canary.mechanism == "import-break"
    assert "--override-ini=pythonpath=src" in lane.argv
    assert "--override-ini=pythonpath=" not in lane.argv
    deselected = {
        argument.removeprefix("--deselect=")
        for argument in lane.argv
        if argument.startswith("--deselect=")
    }
    assert deselected == set()
    assert not any(a == "--deselect" or a.startswith("--deselect") for a in lane.argv)

    declared = set(lane.judge.targets or ())
    discovered = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src" / "assay").rglob("*.py")
        if path.is_file()
    }
    assert declared == discovered
    assert tuple(lane.judge.targets or ()) == tuple(sorted(declared))


def test_self_qualification_run_gate_uses_tester_unified_and_verifies_report():
    run_gate = tomllib.loads(RUN_GATE_TOML.read_text(encoding="utf-8"))
    lane = run_gate["lanes"][QUALIFICATION_ID]
    assert lane["kind"] == "command"
    assert lane["environment"] == "tester-unified"
    assert lane["clean_tree"] is True
    assert lane["argv"] == [
        "timeout",
        "--verbose",
        "--signal=TERM",
        "--kill-after=30s",
        "7h30m",
        "bash",
        "{worktree}/assay/tools/self-qualification-gate.sh",
        "{worktree}",
        "self-qualification",
    ]
    assert lane["resources"]["cpus"] == "3"
    assert lane["artifacts"] == [
        ".assay/verdict-self-qualification.json",
        ".assay/progress-self-qualification.jsonl",
        ".assay/verdict-self-qualification-preflight.json",
        ".assay/progress-self-qualification-preflight.jsonl",
        ".assay/coverage-self-qualification-preflight-snapshots",
    ]

    preflight = run_gate["lanes"][PREFLIGHT_ID]
    assert preflight["kind"] == "command"
    assert preflight["environment"] == "tester-unified"
    assert preflight["clean_tree"] is True
    assert preflight["budget"] == "60m"
    assert PREFLIGHT_ID in preflight["argv"][-1]
    assert preflight["artifacts"] == [
        ".assay/verdict-self-qualification-preflight.json",
        ".assay/progress-self-qualification-preflight.jsonl",
        ".assay/coverage-self-qualification-preflight-snapshots",
    ]

    script = (PROJECT_ROOT / "tools" / "self-qualification-gate.sh").read_text(
        encoding="utf-8"
    )
    assert "--resume" in script
    assert 'local progress_path=".assay/progress-$lane.jsonl"' in script
    assert "git clone --no-local --no-checkout" in script
    assert "--require-hashes" in script
    assert "--require-judge-provenance" in script
    assert 'export ASSAY_B105_COVERAGE_SOURCE=".assay/coverage-$lane.json"' in script
    assert 'coverage_archive_root="$project/.assay/coverage-self-qualification-preflight-snapshots"' in script
    assert 'export ASSAY_B105_COVERAGE_ARCHIVE_DIR="$coverage_archive_attempt"' in script
    assert 'coverage_archive_attempt="$(mktemp -d "$coverage_archive_root/attempt.XXXXXXXX")"' in script
    assert "B105_COVERAGE_ARCHIVE=$coverage_archive_attempt/coverage-self-qualification-preflight-snapshot-$source_commit-$source_tree.json" in script
    assert "unset ASSAY_B105_COVERAGE_SOURCE ASSAY_B105_COVERAGE_ARCHIVE_DIR" in script
    assert 'git status --porcelain --untracked-files=all' in script
    assert '"$scratch/source/assay/tools/b105_report_check.py"' in script
    assert '--repo-root "$scratch/source"' in script
    assert "tools/b105_report_check.py" in script
    assert '"$assay_bin" verify "$verdict_path"' in script
    assert "run_and_verify_lane self-qualification-preflight" in script
    assert "B105_STOPPED_BEFORE_R2=preflight-failed" in script
    assert "ensure_source_unchanged" in script
    assert "ASSAY_SELF_QUALIFICATION_VERIFIED=1" in script


def test_self_qualification_gate_budget_matches_nyxloom_timeout():
    gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"][
        QUALIFICATION_ID
    ]
    run_gate = tomllib.loads(RUN_GATE_TOML.read_text(encoding="utf-8"))["lanes"][
        QUALIFICATION_ID
    ]
    assert gate["timeout_seconds"] == 8 * 60 * 60
    assert run_gate["budget"] == "8h"
    assert run_gate["argv"][:5] == [
        "timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "7h30m"
    ]

    assay_lane = load_lane_file(SELF_LANE_FILE).lane(QUALIFICATION_ID)
    assert assay_lane.budget == "5h"

    preflight_gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))[
        "gates"
    ][PREFLIGHT_ID]
    preflight_run_gate = tomllib.loads(RUN_GATE_TOML.read_text(encoding="utf-8"))[
        "lanes"
    ][PREFLIGHT_ID]
    assert preflight_gate["timeout_seconds"] == 3600
    assert preflight_run_gate["budget"] == "60m"


def test_preflight_measures_the_same_complete_source_inventory_before_r2():
    lane_file = load_lane_file(SELF_LANE_FILE)
    qualification = lane_file.lane(QUALIFICATION_ID)
    preflight = lane_file.lane(PREFLIGHT_ID)

    assert preflight.rigor == ("R0", "R1")
    assert preflight.judge is not None
    assert qualification.judge is not None
    assert preflight.judge.mode == "whole_target"
    assert preflight.judge.fail_under == qualification.judge.fail_under == 100.0
    assert preflight.judge.require_branch is True
    assert preflight.judge.targets == qualification.judge.targets
    assert tuple(
        argument.replace(
            "self-qualification-preflight", "self-qualification"
        )
        for argument in preflight.argv
    ) == qualification.argv
    assert preflight.isolation == qualification.isolation
    assert not any(a == "--deselect" or a.startswith("--deselect") for a in preflight.argv)
    assert preflight.judge.mutation is None
    assert preflight.judge.canary is None
    b105_coverage_env = {
        "ASSAY_B105_COVERAGE_SOURCE",
        "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
        "ASSAY_B105_SOURCE_COMMIT",
        "ASSAY_B105_SOURCE_TREE",
    }
    assert b105_coverage_env.isdisjoint(qualification.env_passthrough)
    assert b105_coverage_env.issubset(preflight.env_passthrough)


def test_b105_coverage_export_requires_explicit_paths_and_archives_raw_json(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "snapshot"
    source = snapshot / ".assay" / "coverage.json"
    source.parent.mkdir(parents=True)
    exclusion_map = json.loads(
        (PROJECT_ROOT / "tests/fixtures/b105-coverage-exclusions.json").read_text(
            encoding="utf-8"
        )
    )
    coverage_document = {
        "meta": {"timestamp": "2026-09-26T10:00:00.000000"},
        "files": {
            path: {"excluded_lines": entry["lines"]}
            for path, entry in exclusion_map["files"].items()
        },
        "totals": {"covered_lines": 17},
    }
    source.write_text(json.dumps(coverage_document), encoding="utf-8")
    archive_root = tmp_path / "worktree" / ".assay" / "coverage-snapshots"
    first_attempt = archive_root / "attempt.ABCDEFGH"
    first_attempt.mkdir(parents=True)
    first_archive = first_attempt / (
        "coverage-self-qualification-preflight-snapshot-"
        f"{'a' * 40}-{'b' * 40}.json"
    )

    monkeypatch.chdir(snapshot)
    monkeypatch.setenv("ASSAY_B105_COVERAGE_SOURCE", ".assay/coverage.json")
    monkeypatch.setenv("ASSAY_B105_COVERAGE_ARCHIVE_DIR", str(first_attempt))
    monkeypatch.setenv("ASSAY_B105_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("ASSAY_B105_SOURCE_TREE", "b" * 40)
    archive_b105_coverage(session=None, exitstatus=0)

    assert first_archive.read_bytes() == source.read_bytes()
    retained = first_archive.read_bytes()
    coverage_document["meta"]["timestamp"] = "2026-09-26T10:01:00.000000"
    source.write_text(json.dumps(coverage_document), encoding="utf-8")
    archive_b105_coverage(session=None, exitstatus=0)
    assert first_archive.read_bytes() == retained

    second_attempt = archive_root / "attempt.IJKLMNOP"
    second_attempt.mkdir()
    second_archive = second_attempt / (
        "coverage-self-qualification-preflight-snapshot-"
        f"{'c' * 40}-{'d' * 40}.json"
    )
    monkeypatch.setenv("ASSAY_B105_COVERAGE_ARCHIVE_DIR", str(second_attempt))
    monkeypatch.setenv("ASSAY_B105_SOURCE_COMMIT", "c" * 40)
    monkeypatch.setenv("ASSAY_B105_SOURCE_TREE", "d" * 40)
    coverage_document["totals"] = {"covered_lines": 18}
    source.write_text(json.dumps(coverage_document), encoding="utf-8")
    archive_b105_coverage(session=None, exitstatus=0)

    assert second_archive.read_bytes() == source.read_bytes()
    assert first_archive.read_bytes() == retained


def test_b105_coverage_export_refuses_to_replace_different_coverage_evidence(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "snapshot"
    source = snapshot / ".assay" / "coverage.json"
    source.parent.mkdir(parents=True)
    exclusion_map = json.loads(
        (PROJECT_ROOT / "tests/fixtures/b105-coverage-exclusions.json").read_text(
            encoding="utf-8"
        )
    )
    coverage_document = {
        "meta": {"timestamp": "2026-09-26T10:00:00.000000"},
        "files": {
            path: {"excluded_lines": entry["lines"]}
            for path, entry in exclusion_map["files"].items()
        },
        "totals": {"covered_lines": 17},
    }
    source.write_text(json.dumps(coverage_document), encoding="utf-8")
    archive_dir = tmp_path / "worktree" / ".assay" / "coverage-snapshots" / "attempt.ABCDEFGH"
    archive_dir.mkdir(parents=True)
    archive = archive_dir / (
        "coverage-self-qualification-preflight-snapshot-"
        f"{'a' * 40}-{'b' * 40}.json"
    )

    monkeypatch.chdir(snapshot)
    monkeypatch.setenv("ASSAY_B105_COVERAGE_SOURCE", ".assay/coverage.json")
    monkeypatch.setenv("ASSAY_B105_COVERAGE_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setenv("ASSAY_B105_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("ASSAY_B105_SOURCE_TREE", "b" * 40)
    archive_b105_coverage(session=None, exitstatus=0)
    retained = archive.read_bytes()

    coverage_document["totals"] = {"covered_lines": 18}
    source.write_text(json.dumps(coverage_document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="different coverage evidence"):
        archive_b105_coverage(session=None, exitstatus=0)

    assert archive.read_bytes() == retained


def test_b105_coverage_export_refuses_a_symlinked_source(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    target = tmp_path / "coverage.json"
    target.write_text("{}", encoding="utf-8")
    (snapshot / "coverage.json").symlink_to(target)
    archive_dir = tmp_path / "worktree" / ".assay" / "coverage-snapshots" / "attempt.ABCDEFGH"
    archive_dir.mkdir(parents=True)

    monkeypatch.chdir(snapshot)
    monkeypatch.setenv("ASSAY_B105_COVERAGE_SOURCE", "coverage.json")
    monkeypatch.setenv("ASSAY_B105_COVERAGE_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setenv("ASSAY_B105_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("ASSAY_B105_SOURCE_TREE", "b" * 40)
    with pytest.raises(RuntimeError, match="not a regular file"):
        archive_b105_coverage(session=None, exitstatus=0)
