"""A deferred-mutation (KI-79) gate is provisional evidence, never a full pass.

These tests drive the real gate driver and read the REAL snapshot manifest and
marker it writes, for three outcomes: a finished provisional gate, a gate whose
final canary fails, and a full gate that must clear any earlier deferral.
"""
from __future__ import annotations

import json

import pytest

from tests.test_release_gate import _git, _probing, _recording_gate, run_release_gate

POSTPONE = "KI-79"
NON_MUTATION_LANES = ["ref-probe", "installed-wheel-live", "assay", "coverage", "canary"]


def _paths(repo):
    assay = repo / "cmru" / ".assay"
    return assay / "release-gate-snapshot.json", assay / "mutation-postponed-cmru.json"


def _manifest(repo) -> dict:
    return json.loads(_paths(repo)[0].read_text(encoding="utf-8"))


def _gate(repo, private_root, *, postpone=None) -> int:
    return run_release_gate.run_release_gate(
        repo, temp_parent=private_root, postpone_mutation=postpone,
    )


def _fail_lane(monkeypatch, calls, failing: str, *, observe=None):
    def invoke(_root, lane, _environment, _worktree):
        calls.append(lane)
        if observe is not None:
            observe(lane)
        return 7 if lane == failing else 0

    monkeypatch.setattr(run_release_gate, "_invoke_lane", _probing(invoke))


def test_a_finished_deferred_gate_is_machine_readably_provisional(tmp_path, monkeypatch, capsys):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    assert _gate(repo, private_root, postpone=POSTPONE) == 0  # exit zero is preserved

    assert calls == NON_MUTATION_LANES
    manifest = _manifest(repo)
    assert manifest["state"] == "provisional"  # not "passed"
    assert manifest["schema_version"] == 4
    assert "mutation" not in manifest["lane_exit_codes"]
    (deferred,) = manifest["deferred_lanes"]
    assert deferred["lane"] == "mutation"
    assert deferred["tracking_id"] == POSTPONE
    assert deferred["reason"] == run_release_gate.POSTPONE_REASON
    assert deferred["follow_up_lane"] == "mutation"
    assert [item["id"] for item in deferred["follow_up_prerequisites"]] == [
        "release-tag-branch", "cgprofile-observer", "d4-capacity-evidence",
    ]
    marker = json.loads(_paths(repo)[1].read_text(encoding="utf-8"))
    assert marker["result"] == "provisional" and marker["status"] == "postponed"
    assert marker["follow_up_prerequisites"] == deferred["follow_up_prerequisites"]
    assert marker["candidate_commit"] == _git(repo, "rev-parse", "HEAD") == manifest["source_commit"]
    report = capsys.readouterr().err
    assert "PROVISIONAL PASS" in report and "NOT an R2 pass" in report
    assert "mutation lane is DEFERRED (KI-79)" in report


def test_the_marker_is_written_only_after_the_final_canary_succeeds(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    marker = _paths(repo)[1]
    seen_at_canary = []

    def observe(lane):
        if lane == "canary":
            seen_at_canary.append(marker.exists())

    _fail_lane(monkeypatch, calls, failing="", observe=observe)

    assert _gate(repo, private_root, postpone=POSTPONE) == 0

    assert seen_at_canary == [False]  # nothing success-shaped existed while the canary ran
    assert marker.is_file()


@pytest.mark.parametrize("failing", ["canary", "coverage", "assay", "installed-wheel-live", "ref-probe"])
def test_a_failed_lane_or_canary_leaves_no_provisional_success_record(
    tmp_path, monkeypatch, capsys, failing,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    marker = _paths(repo)[1]
    # An earlier provisional run's record is stale the moment a new gate starts.
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"result": "provisional", "stale": True}), encoding="utf-8")
    _fail_lane(monkeypatch, calls, failing=failing)

    assert _gate(repo, private_root, postpone=POSTPONE) == 7

    assert not marker.exists()
    manifest = _manifest(repo)
    assert manifest["state"] == "failed"
    assert manifest["deferred_lanes"] == []
    assert "PROVISIONAL PASS" not in capsys.readouterr().err


def test_a_gate_that_raises_after_the_lanes_records_no_deferral(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("source HEAD moved")

    from cmru import gate_snapshot
    monkeypatch.setattr(gate_snapshot, "assert_source_head", refuse)

    with pytest.raises(RuntimeError, match="source HEAD moved"):
        _gate(repo, private_root, postpone=POSTPONE)

    assert not _paths(repo)[1].exists()
    manifest = _manifest(repo)
    assert manifest["state"] == "failed" and manifest["deferred_lanes"] == []


def test_a_full_gate_records_a_normal_pass_and_clears_stale_deferral_state(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    assert _gate(repo, private_root, postpone=POSTPONE) == 0
    assert _manifest(repo)["state"] == "provisional" and _paths(repo)[1].is_file()
    calls.clear()

    assert _gate(repo, private_root) == 0

    assert "mutation" in calls  # the full conjunction ran
    manifest = _manifest(repo)
    assert manifest["state"] == "passed"
    assert manifest["deferred_lanes"] == []
    assert "mutation" in manifest["lane_exit_codes"]
    assert not _paths(repo)[1].exists()


def test_a_full_gate_never_carries_a_deferral_field_value(tmp_path, monkeypatch):
    repo, private_root, _calls = _recording_gate(tmp_path, monkeypatch)

    assert _gate(repo, private_root) == 0

    manifest = _manifest(repo)
    assert manifest["state"] == "passed" and manifest["deferred_lanes"] == []
