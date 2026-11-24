"""A deferred-mutation (KI-79) gate is provisional evidence, never a full pass.

These tests drive the real gate driver and read the REAL snapshot manifest and
marker it writes, for three outcomes: a finished provisional gate, a gate whose
final canary fails, and a full gate that must clear any earlier deferral.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_release_gate import _git, _probing, _recording_gate, run_release_gate

POSTPONE = "KI-79"
NON_MUTATION_LANES = [
    "release-preconditions", "ref-probe", "installed-wheel-live", "assay",
]


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
    # The native whole-target lane is the one surviving R2 path (KI-199).
    assert deferred["follow_up_lane"] == "assay-first-release-r2"
    assert [item["id"] for item in deferred["follow_up_prerequisites"]] == [
        "release-tag-branch", "cgprofile-observer", "d4-capacity-evidence",
    ]
    marker = json.loads(_paths(repo)[1].read_text(encoding="utf-8"))
    assert marker["result"] == "provisional" and marker["status"] == "postponed"
    assert marker["follow_up_lane"] == "assay-first-release-r2"
    assert marker["follow_up_prerequisites"] == deferred["follow_up_prerequisites"]
    assert marker["candidate_commit"] == _git(repo, "rev-parse", "HEAD") == manifest["source_commit"]
    report = capsys.readouterr().err
    assert "PROVISIONAL PASS" in report and "NOT an R2 pass" in report
    assert "mutation lane is DEFERRED (KI-79)" in report


@pytest.mark.parametrize("first_release", [False, True], ids=["tagged", "no-tag"])
def test_every_deferral_records_the_whole_target_r2_follow_up_lane(
    tmp_path, monkeypatch, first_release,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    postpone = POSTPONE
    if first_release:
        # A release repository with no published stable tag: same follow-up lane.
        (repo / "cmru" / "CHANGES.md").write_text(
            "# Changelog\n\n## [0.1.0] - 2026-10-15\n\n- first release\n", encoding="utf-8",
        )
        _git(repo, "add", "cmru/CHANGES.md")
        _git(repo, "commit", "--quiet", "-m", "prepare first CMRU release")
        monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", _git(repo, "rev-parse", "HEAD"))
        original_load = run_release_gate._load_components

        def load_without_published_stable_tag():
            git, baseline = original_load()
            baseline.published_release_state = lambda _root, *, git_auth: (None, {})
            return git, baseline

        monkeypatch.setattr(
            run_release_gate, "_load_components", load_without_published_stable_tag,
        )
        postpone = run_release_gate.FIRST_RELEASE_POSTPONEMENT_ID

    assert _gate(repo, private_root, postpone=postpone) == 0

    assert "mutation" not in calls  # the deferral is recorded, never read back by the gate
    (deferred,) = _manifest(repo)["deferred_lanes"]
    marker = json.loads(_paths(repo)[1].read_text(encoding="utf-8"))
    assert deferred["follow_up_lane"] == marker["follow_up_lane"] == "assay-first-release-r2"
    assert "run-gate assay-first-release-r2" in deferred["reason"]


def test_the_marker_is_written_only_after_the_final_assay_lane_succeeds(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    marker = _paths(repo)[1]
    seen_at_assay = []

    def observe(lane):
        if lane == "assay":
            seen_at_assay.append(marker.exists())

    _fail_lane(monkeypatch, calls, failing="", observe=observe)

    assert _gate(repo, private_root, postpone=POSTPONE) == 0

    assert seen_at_assay == [False]  # nothing success-shaped existed while Assay ran
    assert marker.is_file()


@pytest.mark.parametrize("failing", ["assay", "installed-wheel-live", "ref-probe"])
def test_a_failed_lane_leaves_no_provisional_success_record(
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


# --- cleanup failure after every lane passed must not leave success-shaped evidence ---

def _assert_no_success_shaped_evidence(repo) -> None:
    assert not _paths(repo)[1].exists()
    manifest = _manifest(repo)
    assert manifest["state"] == "failed"
    assert manifest["deferred_lanes"] == []


@pytest.mark.parametrize("postpone", [POSTPONE, None], ids=["provisional", "full"])
def test_a_failed_canary_removal_after_all_lanes_pass_fails_the_gate_without_success_evidence(
    tmp_path, monkeypatch, postpone,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    from cmru import gate_snapshot

    def tampered(_snapshot):
        raise RuntimeError("release-gate source canary path changed during the live lane probe")

    monkeypatch.setattr(gate_snapshot, "remove_source_canary", tampered)

    with pytest.raises(RuntimeError, match="source canary path changed"):
        _gate(repo, private_root, postpone=postpone)

    assert calls[-1] == ("assay" if postpone else "mutation")  # every required lane did pass
    _assert_no_success_shaped_evidence(repo)


@pytest.mark.parametrize("postpone", [POSTPONE, None], ids=["provisional", "full"])
def test_a_failed_snapshot_close_after_all_lanes_pass_fails_the_gate_without_success_evidence(
    tmp_path, monkeypatch, postpone,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    from cmru import gate_snapshot
    real_close = gate_snapshot.close_gate_snapshot

    def failing_close(snapshot):
        real_close(snapshot)
        raise RuntimeError("lease release failed")

    monkeypatch.setattr(gate_snapshot, "close_gate_snapshot", failing_close)

    with pytest.raises(RuntimeError, match="lease release failed"):
        _gate(repo, private_root, postpone=postpone)

    assert calls[-1] == ("assay" if postpone else "mutation")
    _assert_no_success_shaped_evidence(repo)


def test_main_returns_failure_when_cleanup_fails_after_a_provisional_run(
    tmp_path, monkeypatch, capsys,
):
    repo, private_root, _calls = _recording_gate(tmp_path, monkeypatch)
    from cmru import gate_snapshot
    original = run_release_gate.run_release_gate
    monkeypatch.setattr(
        run_release_gate, "run_release_gate",
        lambda worktree, *, postpone_mutation=None: original(
            worktree, temp_parent=private_root, postpone_mutation=postpone_mutation,
        ),
    )
    monkeypatch.setattr(
        gate_snapshot, "remove_source_canary",
        lambda _snapshot: (_ for _ in ()).throw(RuntimeError("canary replaced")),
    )

    assert run_release_gate.main(["--worktree", str(repo), "--postpone-mutation", POSTPONE]) == 1

    assert "canary replaced" in capsys.readouterr().err
    _assert_no_success_shaped_evidence(repo)


def test_both_cleanup_steps_run_even_when_the_first_fails(tmp_path, monkeypatch):
    repo, private_root, _calls = _recording_gate(tmp_path, monkeypatch)
    from cmru import gate_snapshot
    closed = []
    real_close = gate_snapshot.close_gate_snapshot
    monkeypatch.setattr(
        gate_snapshot, "remove_source_canary",
        lambda _snapshot: (_ for _ in ()).throw(RuntimeError("canary replaced")),
    )
    monkeypatch.setattr(
        gate_snapshot, "close_gate_snapshot",
        lambda snapshot: (closed.append(True), real_close(snapshot))[1],
    )

    with pytest.raises(RuntimeError, match="canary replaced"):
        _gate(repo, private_root, postpone=POSTPONE)

    assert closed == [True]  # the lease was still released


# --- an invalid invocation preserves the previous valid run's evidence ---

def _evidence_bytes(repo) -> tuple[bytes, bytes]:
    manifest, marker = _paths(repo)
    return manifest.read_bytes(), marker.read_bytes()


def test_an_invocation_refused_before_a_new_gate_starts_preserves_the_previous_evidence(
    tmp_path, monkeypatch,
):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    assert _gate(repo, private_root, postpone=POSTPONE) == 0
    before = _evidence_bytes(repo)
    calls.clear()

    # An unauthorized tracking id is refused by input validation.
    with pytest.raises(RuntimeError, match="is not authorized for a release"):
        _gate(repo, private_root, postpone="KI-62")

    assert calls == []  # no lane ran
    assert _evidence_bytes(repo) == before  # nothing was deleted or rewritten


def test_a_resume_refused_for_a_different_run_gate_identity_preserves_the_previous_evidence(
    tmp_path, monkeypatch,
):
    from tests.test_release_gate import _base_run_gate_identity

    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    assert _gate(repo, private_root, postpone=POSTPONE) == 0
    before = _evidence_bytes(repo)
    calls.clear()
    monkeypatch.setattr(
        run_release_gate, "_check_run_gate_identity",
        lambda _root: _base_run_gate_identity("23.12.0"),
    )

    with pytest.raises(RuntimeError, match="judged by a different Run-Gate identity"):
        _gate(repo, private_root, postpone=POSTPONE)

    assert calls == []
    assert _evidence_bytes(repo) == before


def test_a_valid_new_gate_clears_stale_success_state_before_any_lane_runs(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    assert _gate(repo, private_root, postpone=POSTPONE) == 0
    manifest_path, marker = _paths(repo)
    seen_at_first_lane = []

    def observe(lane):
        if lane == "ref-probe":
            seen_at_first_lane.append((marker.exists(), json.loads(
                manifest_path.read_text(encoding="utf-8"))["state"]))

    _fail_lane(monkeypatch, calls, failing="", observe=observe)

    assert _gate(repo, private_root, postpone=POSTPONE) == 0

    # The first lane saw neither the old marker nor a success-shaped manifest.
    assert seen_at_first_lane == [(False, "running")]


def _prepare_release_heading(repo, version):
    (repo / "cmru" / "CHANGES.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-10-21\n\n- prepared release\n", encoding="utf-8",
    )
    # the gate judges an exact commit and refuses a dirty tracked tree
    _git(repo, "add", "cmru/CHANGES.md")
    _git(repo, "commit", "--quiet", "-m", f"prepare {version}")


def test_ki79_refuses_a_non_strict_prepared_heading_instead_of_reading_an_older_one(tmp_path, monkeypatch):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    _prepare_release_heading(repo, "7.0.0")
    changes = repo / "cmru" / "CHANGES.md"
    changes.write_text(
        changes.read_text(encoding="utf-8").replace("## [7.0.0]", "## [7.2.0rc1] - 2026-10-28\n\n- rc\n\n## [7.0.0]", 1),
        encoding="utf-8",
    )
    _git(repo, "add", "cmru/CHANGES.md")
    _git(repo, "commit", "--quiet", "-m", "prepare 7.2.0rc1")

    with pytest.raises(RuntimeError, match=r"not a strict X\.Y\.Z version.*7\.2\.0rc1"):
        _gate(repo, private_root, postpone=POSTPONE)

    assert calls == []


def test_there_is_no_per_version_window_table_or_window_text_helper():
    # KI-79/KI-159 (operator decision 2026-11-18): the standing release rule replaced the
    # per-minor allowlist; nothing in the gate driver may enumerate or consult a window.
    assert not hasattr(run_release_gate, "POSTPONEMENT_RELEASE_VERSIONS")
    assert not hasattr(run_release_gate, "_postponement_window_text")
    assert not hasattr(run_release_gate, "_version_tuple")
    assert "window" not in run_release_gate.POSTPONE_REASON.lower()
    assert "asynchronously on the latest release" in run_release_gate.POSTPONE_REASON
    assert "operator-approved" not in run_release_gate.POSTPONE_REASON


def test_ki141_backlog_names_the_latest_release_r2_ledger_close_condition():
    backlog = Path(__file__).resolve().parents[1] / "KNOWN_ISSUES_TODO_BACKLOG.md"
    text = backlog.read_text(encoding="utf-8")
    entry = text.split("## KI-141 -", 1)[1].split("## KI-142 -", 1)[0]

    assert (
        "**Fix.** Finish the owed whole-source R2 as the T3 lane `r2` on the latest release tag, "
        "using the controller-selected base (currently `cmru-v6.1.0` when no newer R2 PASS row exists); "
        "the copyable lane command is `run-gate --base cmru-v6.1.0 r2` when that base is selected. "
        "Kill the survivors of KI-129/133/134/135/138; an unkillable (equivalent) survivor leaves "
        "the existing operator-decision branch of Close when."
    ) in entry
    assert (
        "**Constraint.** Under the standing release rule (operator decision 2026-11-18: a release "
        "needs R1 full branch coverage, R3 canary and an independent review, and R2 runs "
        "asynchronously), R2 is owed on the latest release at all times; no version window or "
        "upper bound exists any more."
    ) in entry
    assert (
        "**Close when.** A T3 ledger PASS row records cmru `r2` at the latest release tag, "
        "with a base at or before the last R2-covered tag (`cmru-v6.1.0`) and every candidate "
        "killed with no survivors; the campaign judges the latest released source and closes the "
        "accumulated R2 debt of every provisional release up to it."
    ) in entry
    assert "KI-159" in entry and "FIXED" in entry


@pytest.mark.parametrize(
    "version",
    [
        "5.9.9", "6.1.9", "6.2.0", "6.2.10", "6.3.0", "7.0.0", "7.3.9",
        "7.4.0", "7.5.0", "7.5.1", "8.0.0", "10.0.0",
    ],
)
def test_the_standing_rule_applies_identically_to_every_release_version(tmp_path, monkeypatch, version):
    repo, private_root, calls = _recording_gate(tmp_path, monkeypatch)
    _prepare_release_heading(repo, version)

    assert _gate(repo, private_root, postpone=POSTPONE) == 0

    assert calls == NON_MUTATION_LANES
    marker = json.loads(_paths(repo)[1].read_text(encoding="utf-8"))
    assert marker["target_version"] == version and marker["tracking_id"] == POSTPONE
    assert "asynchronously on the latest release" in marker["reason"]
