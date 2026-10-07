"""Merge-condition tests for LT-UPG/LT-EARLY (reviewer ACCEPT conditions C1, N1, N2).

Tests only: they pin behaviour of the REAL installer methods with recording fakes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from debian_install_v2.installer import Installer
from debian_install_v2.tests import test_mattermost_notifications as mm
from debian_install_v2.tests import test_r1_coverage as r1
from debian_install_v2.tests.test_lt_apt import TIMERS, _failing_installer, _RecordingActions
from debian_install_v2.tests.test_lt_early_fix1 import (
    AUTH, CURRENT, EXISTING, LOOKALIKE, OPERATOR, UNMARKED,
)

ENABLE = ("/usr/bin/systemctl", "enable", "--now", *TIMERS)


# --- C1: the real hold records apt_timers_held, a NEW process restores from it ---------

def test_real_hold_records_the_state_step_and_a_new_process_restores_from_it(tmp_path):
    stage1 = _failing_installer(tmp_path)
    stage1._hold_apt_timers()  # the REAL method, recording actions fake
    steps = stage1.state.load()["steps"]
    assert steps["apt_timers_held"]["status"] == "success"
    assert ("/usr/bin/systemctl", "disable", "--now", *TIMERS) in stage1.actions.calls

    # stage2 = a new process: a fresh Installer on the same state dir, no in-memory flag.
    stage2 = Installer(stage1.config, _RecordingActions(), inspect_host=False)
    assert stage2._apt_timers_held is False
    stage2._restore_apt_timers_after_failure()
    assert ENABLE in stage2.actions.calls


# --- N1: D2 controller-key cleanup writes a newline-terminated file with mode 0600 ----

def _cleanup_writes(tmp_path, monkeypatch, *, retain):
    installer = mm._installer(tmp_path, controller_ssh_pubkey=CURRENT, retain_controller_ssh_key=retain)
    mm._live(installer, monkeypatch)
    writes: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        installer.actions, "write_file",
        lambda path, content, mode=0o644: writes.append((path, content, mode)),
    )
    monkeypatch.setattr(Path, "is_file", r1._fake_is_file(True))
    monkeypatch.setattr(Path, "read_text", r1._fake_read_text(EXISTING))
    if retain:
        installer._prune_stale_controller_keys()
    else:
        installer._remove_controller_ssh_key()
    return writes


@pytest.mark.parametrize("retain", [True, False], ids=["prune-path", "remove-path"])
def test_key_cleanup_write_ends_with_newline_and_requests_mode_0600(tmp_path, monkeypatch, retain):
    writes = _cleanup_writes(tmp_path, monkeypatch, retain=retain)
    assert len(writes) == 1
    path, content, mode = writes[0]
    assert path == AUTH
    assert content.endswith("\n") and not content.endswith("\n\n")
    assert mode == 0o600
    expected = [OPERATOR, CURRENT, UNMARKED, LOOKALIKE] if retain else [OPERATOR, UNMARKED, LOOKALIKE]
    assert content.splitlines() == expected


# --- N2: failure-tail truncation, each cap killed by its own test ----------------------

HEADER = "action failed (1): install-time unattended-upgrade (full)"


def test_a_single_overlong_line_is_cut_by_the_per_line_cap_only(tmp_path):
    inst = _failing_installer(tmp_path)
    cap = Installer._TAIL_LINE_MAX
    text = inst._failure_tail(RuntimeError(HEADER + "\n" + "A" * 500))
    # total stays well under _TAIL_MAX, so only _TAIL_LINE_MAX can have cut it
    assert len(HEADER) + 3 + cap < Installer._TAIL_MAX
    assert text == f"{HEADER} | " + "A" * (cap - 1) + "…"


def test_many_long_lines_are_cut_by_the_total_cap_only(tmp_path):
    inst = _failing_installer(tmp_path)
    line_len = Installer._TAIL_LINE_MAX - 10  # each line under _TAIL_LINE_MAX
    lines = [chr(ord("a") + i) * line_len for i in range(Installer._TAIL_LINES)]
    text = inst._failure_tail(RuntimeError(HEADER + "\n" + "\n".join(lines)))
    untruncated = " | ".join([HEADER, *lines])
    assert len(untruncated) > Installer._TAIL_MAX  # only the total cap applies
    assert len(text) == Installer._TAIL_MAX and text.endswith("…")
    assert text == untruncated[: Installer._TAIL_MAX - 1] + "…"
    assert all("…" not in part for part in text[:-1].split(" | "))  # no line was cut individually
