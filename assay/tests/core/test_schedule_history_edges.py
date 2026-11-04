"""B207: how ``parse_history`` reads the wall clock of a history/2 candidate."""

from __future__ import annotations

import json

from assay import _schedule


def _history(item: dict) -> bytes:
    return json.dumps(
        {"schema": _schedule.HISTORY_SCHEMA, "candidates": {"c1": item}}
    ).encode("utf-8")


def _profile(wall: float | None) -> dict:
    return {
        "status": "MEASURED",
        "reason": None,
        "wall_seconds": wall,
        "metrics": {"rss_peak_bytes": 4096},
    }


def test_a_candidates_own_wall_clock_wins_over_its_profiles():
    entries, reason = _schedule.parse_history(
        _history({"path": "src/a.py", "wall_seconds": 2.5, "profile": _profile(9.0)})
    )
    assert reason is None
    assert entries["c1"] == _schedule.HistoryEntry("src/a.py", 2.5, 4096, "MEASURED", None)


def test_a_profile_supplies_the_wall_clock_when_the_candidate_has_none():
    entries, _ = _schedule.parse_history(
        _history({"path": "src/a.py", "wall_seconds": None, "profile": _profile(9.0)})
    )
    assert entries["c1"].wall_seconds == 9.0
    assert entries["c1"].rss_peak_bytes == 4096
