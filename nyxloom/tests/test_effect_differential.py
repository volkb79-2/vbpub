"""The amendment's section 5.1 differential check, applied to CR-05a.

``tests/fixtures/effect_transcripts_v1.json`` was RECORDED by running
``effect_differential.record`` against the pre-CR-05a tree (commit `180f3c80`,
where ``Daemon._execute`` was still the isinstance ladder). This module runs
the identical scenarios against the current tree and requires the event
sequences to match exactly.

To re-record after a deliberate, explained change::

    NYXLOOM_RECORD_EFFECT_TRANSCRIPTS=1 pytest tests/test_effect_differential.py

Re-recording is a REVIEWABLE act: the fixture diff is the behavioural delta,
and the package report has to say why it moved. A silent re-record is the
failure mode this file exists to prevent, which is why the recording path is
opt-in and never runs in the gate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import effect_differential

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "effect_transcripts_v1.json")


def _record(project, cfg, monkeypatch) -> dict:
    return effect_differential.record(project, cfg, monkeypatch)


def test_moved_effects_reproduce_the_pre_cr05_event_sequences(
        tmp_state, sample_project, monkeypatch):
    """Identical action in, identical event sequence out."""
    actual = _record("demo", sample_project, monkeypatch)
    if os.environ.get("NYXLOOM_RECORD_EFFECT_TRANSCRIPTS"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
        pytest.skip("recorded a new transcript; re-run without the env var")

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert actual["version"] == expected["version"]
    assert sorted(actual["scenarios"]) == sorted(expected["scenarios"]), (
        "the differential scenario set changed; a family may only leave it "
        "when its effect leaves the daemon")
    for name in sorted(expected["scenarios"]):
        assert actual["scenarios"][name] == expected["scenarios"][name], (
            f"scenario {name!r} no longer reproduces the pre-CR-05a event "
            "sequence. Under the program stop-loss an unexplained behavioural "
            "delta stops the package -- explain it in the package report and "
            "re-record, or fix the effector.")


def test_the_differential_fails_when_an_event_sequence_changes(
        tmp_state, sample_project, monkeypatch):
    """A comparison that has never rejected anything is not known to reject.

    Corrupt one recorded sequence and require the same comparison the test
    above performs to notice -- otherwise a normalization bug could be
    flattening every payload into equality.
    """
    actual = _record("demo", sample_project, monkeypatch)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    corrupted = dict(expected["scenarios"])
    rows = [dict(row) for row in corrupted["transition-plain"]]
    assert rows, "the corruption target must have events to corrupt"
    rows[0]["payload"] = dict(rows[0]["payload"], to="completed")
    corrupted["transition-plain"] = rows
    assert actual["scenarios"]["transition-plain"] != corrupted["transition-plain"]


def test_normalization_keeps_shape_while_allowing_minted_values(
        tmp_state, sample_project, monkeypatch):
    """The wave id is minted per run, so it must normalize -- but to a
    PLACEHOLDER, not to nothing. Dropping the field would let a handler stop
    emitting a wave id entirely without the differential noticing."""
    rows = _record("demo", sample_project, monkeypatch)["scenarios"]["open-wave"]
    assert len(rows) == 1
    assert rows[0]["wave_id"] == "<wave-id>"
