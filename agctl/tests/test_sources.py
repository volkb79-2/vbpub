"""The non-git sources: `/proc/locks`, gate results, peer prose and handovers — temp trees only.

`/proc/locks` is exercised through `parse_proc_locks` with recorded text, and through a real
flock this test takes on a temp file (so the device+inode match is proved against the kernel,
not asserted); no live gate lock is opened, and nothing here writes outside `tmp_path`.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from agctl import sources as ag_sources


def test_proc_locks_lists_flocks_and_posix_locks_by_device_and_inode():
    text = (
        "8: FLOCK  ADVISORY  WRITE 50775 fd:00:26224784 0 EOF\n"
        "9: POSIX  ADVISORY  READ 389 fd:00:18119660 128 128\n"
        "6: FLOCK  ADVISORY  WRITE 1123520 00:79:17 0 EOF\n"
    )

    entries = ag_sources.parse_proc_locks(text)

    assert entries[0].kind == "FLOCK"
    assert entries[0].pid == 50775
    # The device field is hex `major:minor`, so `fd:00` is (0xfd, 0x00) and the inode follows
    # after its own colon. The key is the triple; matching on the inode alone would confuse two
    # files on different filesystems.
    assert entries[0].device == (0xFD, 0x00)
    assert entries[0].inode == 26224784
    assert (entries[0].device[0], entries[0].device[1], entries[0].inode) == entries[0].key
    assert entries[2].device == (0x00, 0x79)
    assert entries[2].inode == 17


def test_a_short_or_odd_proc_locks_line_is_skipped_not_guessed():
    entries = ag_sources.parse_proc_locks(
        "not a lock line\n8: FLOCK ADVISORY WRITE 0 fd:00:x 0 EOF\n8: FLOCK ADVISORY WRITE notapid fd:00:1 0 EOF\n"
    )

    assert entries == ()


def test_an_unreadable_proc_locks_answers_unknown_never_free(tmp_path):
    table = ag_sources.read_proc_locks(str(tmp_path / "absent"))

    assert table.readable is False
    assert table.held == ()
    assert "cannot read" in table.detail
    state, pid, detail = table.state_of(tmp_path / "anything")
    assert (state, pid) == (ag_sources.UNKNOWN, None)
    assert "cannot read" in detail


def test_a_lock_this_test_holds_is_reported_held_and_a_free_one_free(tmp_path):
    held_path = tmp_path / "held.lock"
    free_path = tmp_path / "free.lock"
    held_path.touch()
    free_path.touch()
    with held_path.open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The table is read INSIDE the lock: /proc/locks is a snapshot of the moment it is read,
        # so a table taken before the lock would not contain it.
        table = ag_sources.read_proc_locks()
        state, pid, _ = table.state_of(held_path)
        other, _, _ = table.state_of(free_path)

    assert state == ag_sources.HELD
    assert pid == os.getpid()
    assert other == ag_sources.FREE


def test_a_lock_file_that_cannot_be_stat_ed_is_unknown(tmp_path):
    table = ag_sources.read_proc_locks()

    state, pid, detail = table.state_of(tmp_path / "absent")

    assert (state, pid) == (ag_sources.UNKNOWN, None)
    assert "cannot stat" in detail


def test_a_lock_slot_name_splits_into_host_and_kind():
    assert ag_sources.split_lock_name("r1002.admit") == ("r1002", "admit")
    assert ag_sources.split_lock_name("v1001.slot3") == ("v1001", "slot")
    assert ag_sources.split_lock_name(".admit") is None
    assert ag_sources.split_lock_name("v1001.slotx") is None
    assert ag_sources.split_lock_name("v1001.slot") is None
    assert ag_sources.split_lock_name("r1002.admit.bak") is None


def test_gate_slots_report_each_lock_file_with_its_own_state(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir()
    (locks / "r1002.admit").touch()
    (locks / "v1001.slot1").touch()
    free = locks / "v1001.slot2"
    free.touch()
    config = _config(tmp_path)
    with (locks / "r1002.admit").open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        slots = ag_sources.gate_slots(config, ag_sources.read_proc_locks())

    assert [slot.name for slot in slots] == ["r1002.admit", "v1001.slot1", "v1001.slot2"]
    assert slots[0].state == ag_sources.HELD and slots[0].kind == "admit"
    assert slots[1].state == ag_sources.FREE and slots[1].kind == "slot"
    assert "r1002" in slots[0].text()
    assert slots[0].as_dict()["holder_pid"] == os.getpid()
    assert slots[1].as_dict() == {"host": "v1001", "name": "v1001.slot1", "kind": "slot", "state": "free", "path": str(locks / "v1001.slot1")}


def test_an_absent_lock_directory_has_no_slots(tmp_path):
    assert ag_sources.gate_slots(_config(tmp_path), ag_sources.read_proc_locks()) == ()


def test_a_non_file_inside_the_lock_directory_is_skipped(tmp_path):
    locks = tmp_path / "locks"
    (locks / "r1002.slot1").mkdir(parents=True)

    assert ag_sources.gate_slots(_config(tmp_path), ag_sources.read_proc_locks()) == ()


def _config(tmp_path: Path, **overrides) -> object:
    from agctl import config as ag_config

    values = dict(
        home=tmp_path / "home",
        runs_dirs=(),
        state_dirs=(),
        repo_state_dir=None,
        peers_dir=tmp_path / "peers",
        gate_lock_dir=tmp_path / "locks",
        remote_gate_out=tmp_path / "gates",
        toml_path=tmp_path / "agctl.toml",
        toml_state="absent",
        toml_legacy_dirs=(),
    )
    values.update(overrides)
    return ag_config.Config(**values)


# --- gate results -------------------------------------------------------------------------------


def _gate_dir(tmp_path: Path, name: str, *, meta: str | None, result: str | None, lane_log: str | None) -> Path:
    directory = tmp_path / "gates" / name
    directory.mkdir(parents=True)
    if meta is not None:
        (directory / "meta.json").write_text(meta, encoding="utf-8")
    if result is not None:
        (directory / "result.json").write_text(result, encoding="utf-8")
    if lane_log is not None:
        (directory / "lane.log").write_text(lane_log, encoding="utf-8")
    return directory


def test_a_gate_result_prefers_run_gates_own_verdict_line(tmp_path):
    _gate_dir(
        tmp_path, "rgw-tests-1",
        meta='{"host": "v1001", "project_dir": "cmru", "lane": "tests", "sha": "5e02d731b", "started_utc": "2026-11-25T21:22:23Z"}',
        result='{"lane": "tests", "verdict": "FAIL", "exit_code": 1}',
        lane_log="noise\nrun-gate: lane 'tests' verdict PASS; exit_code 0\n",
    )

    (result,) = ag_sources.read_results(_config(tmp_path))

    assert result.verdict == "PASS"
    assert result.verdict_source.endswith("lane.log: run-gate verdict line")
    assert result.host == "v1001" and result.project == "cmru" and result.lane == "tests"
    assert result.sha == "5e02d731b"
    assert "tests PASS v1001 5e02d731b" in result.text()
    assert result.as_dict()["run"] == "rgw-tests-1"


def test_a_verdict_line_for_another_lane_is_not_this_lanes_verdict(tmp_path):
    _gate_dir(
        tmp_path, "rgw-tests-2",
        meta='{"host": "v1001", "project_dir": "cmru", "lane": "tests"}',
        result='{"lane": "tests", "verdict": "PASS", "exit_code": 0}',
        lane_log="run-gate: lane 'suite' verdict FAIL; exit_code 1\n",
    )

    (result,) = ag_sources.read_results(_config(tmp_path))

    assert result.verdict == "PASS"
    assert result.verdict_source == "result.json"


def test_a_run_without_a_readable_meta_file_is_stated_unknown(tmp_path):
    _gate_dir(tmp_path, "rgw-odd", meta=None, result=None, lane_log=None)

    (result,) = ag_sources.read_results(_config(tmp_path))

    assert result.host == ag_sources.UNKNOWN
    assert result.project == ag_sources.UNKNOWN
    assert result.lane == ag_sources.UNKNOWN
    assert result.verdict == ag_sources.UNKNOWN
    assert result.verdict_source == ""
    assert "meta.json" in result.detail


def test_a_run_with_no_verdict_anywhere_is_unknown(tmp_path):
    _gate_dir(tmp_path, "rgw-partial", meta='{"host": "v1001", "project_dir": "assay", "lane": "r0-r1"}', result='{"verdict": 7}', lane_log="")

    (result,) = ag_sources.read_results(_config(tmp_path))

    assert result.verdict == ag_sources.UNKNOWN
    assert result.sha == ""


def test_results_are_newest_first_by_directory_mtime(tmp_path):
    older = _gate_dir(tmp_path, "rgw-a", meta='{"lane": "lint"}', result='{"verdict": "PASS"}', lane_log="")
    newer = _gate_dir(tmp_path, "rgw-b", meta='{"lane": "lint"}', result='{"verdict": "PASS"}', lane_log="")
    os.utime(older, (time.time() - 3600, time.time() - 3600))

    results = ag_sources.read_results(_config(tmp_path))

    assert [result.name for result in results] == ["rgw-b", "rgw-a"]


def test_an_absent_results_directory_has_no_results(tmp_path):
    assert ag_sources.read_results(_config(tmp_path)) == ()


def test_a_file_in_the_results_directory_is_skipped_and_a_meta_stat_failure_is_tolerated(tmp_path):
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "stray").write_text("x", encoding="utf-8")

    results = ag_sources.read_results(_config(tmp_path))

    assert results == ()


# --- peer state and handovers --------------------------------------------------------------------


def test_a_state_md_package_line_is_reported_unverified_with_its_line_number(tmp_path):
    peers = tmp_path / "state" / "platform"
    peers.mkdir(parents=True)
    (peers / "STATE.md").write_text(
        "# platform\n\n- P-plat-22c | wt .worktrees/x | DONE\n- nothing here\n", encoding="utf-8"
    )
    (peers / "HANDOFF.md").write_text("handover\n", encoding="utf-8")

    (peer,) = ag_sources.read_peers(_config(tmp_path, state_dirs=(tmp_path / "state",)))

    assert peer.peer == "platform"
    assert peer.state_state == "present"
    assert peer.handover_mtime is not None
    (line,) = peer.lines
    assert line.package == "plat-22c"
    assert line.token == "P-plat-22c"
    assert line.line == 3
    assert line.text.startswith("- P-plat-22c")


def test_a_peer_without_a_state_file_reports_missing_not_empty(tmp_path):
    peers = tmp_path / "state" / "core"
    peers.mkdir(parents=True)
    (peers / "HANDOFF.md").write_text("h\n", encoding="utf-8")

    (peer,) = ag_sources.read_peers(_config(tmp_path, state_dirs=(tmp_path / "state",)))

    assert peer.state_state == "missing"
    assert peer.lines == ()
    assert peer.state_mtime is None


def test_state_md_larger_than_the_bounded_read_is_unknown_not_prose(tmp_path):
    peers = tmp_path / "state" / "big"
    peers.mkdir(parents=True)
    (peers / "STATE.md").write_text("x" * (ag_sources.STATE_LIMIT + 1), encoding="utf-8")

    (peer,) = ag_sources.read_peers(_config(tmp_path, state_dirs=(tmp_path / "state",)))

    assert peer.state_state == ag_sources.UNKNOWN
    assert "bounded read" in peer.state_detail


def test_an_absent_state_directory_yields_no_peers(tmp_path):
    assert ag_sources.read_peers(_config(tmp_path, state_dirs=(tmp_path / "none",))) == ()


def test_a_file_in_the_state_directory_is_not_a_peer(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "stray").write_text("x", encoding="utf-8")

    assert ag_sources.read_peers(_config(tmp_path, state_dirs=(state,))) == ()


def test_the_same_peer_name_in_two_directories_is_read_once(tmp_path):
    for container in ("a", "b"):
        directory = tmp_path / container / "platform"
        directory.mkdir(parents=True)
        (directory / "STATE.md").write_text("- P-plat-1 | x\n", encoding="utf-8")

    peers = ag_sources.read_peers(_config(tmp_path, state_dirs=(tmp_path / "a", tmp_path / "b")))

    assert [peer.peer for peer in peers] == ["platform"]


def test_the_own_handover_age_comes_from_the_repository_state_dir(tmp_path):
    state = tmp_path / ".controller-state"
    state.mkdir()
    (state / "HANDOFF.md").write_text("h\n", encoding="utf-8")

    path, mtime, detail = ag_sources.read_own_handover(_config(tmp_path, repo_state_dir=state))

    assert path == str(state / "HANDOFF.md")
    assert mtime is not None
    assert detail == ""


def test_an_unconfigured_or_absent_own_handover_is_stated(tmp_path):
    path, mtime, detail = ag_sources.read_own_handover(_config(tmp_path))

    assert (path, mtime) == ("", None)
    assert "no CONTROLLER_REPO" in detail

    missing = tmp_path / ".controller-state"
    path, mtime, detail = ag_sources.read_own_handover(_config(tmp_path, repo_state_dir=missing))

    assert path == str(missing / "HANDOFF.md")
    assert mtime is None
    assert "not there" in detail


def test_parse_lane_verdict_requires_the_four_six_and_this_lane(tmp_path):
    assert ag_sources.parse_lane_verdict("run-gate: lane 'tests' verdict PASS; exit_code 0", "tests") == "PASS"
    assert ag_sources.parse_lane_verdict("padding run-gate: lane 'tests' verdict PASS", "tests") == ""
    assert ag_sources.parse_lane_verdict("run-gate: lane 'other' verdict PASS", "tests") == ""
    assert ag_sources.parse_lane_verdict(None, "tests") == ""
