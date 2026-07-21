"""Tests for `nyxloom events` -- PACKAGE SP04 (docs/plan-state-integrity.md
A.3): the greppability bridge that dumps the event store as JSONL to stdout,
restoring `| jq` / `| lnav` over the (now backend-agnostic, file or SQLite)
event log.

Run primarily against the SQLite backend (the backend SP04 exists to restore
greppability for) via the `sqlite_backend` fixture below -- mirrors
test_storage_sqlite.py's fixture of the same name/shape. The round-trip
oracle is ALSO run against the default file backend (no fixture, no env var)
to prove the command is genuinely backend-agnostic: `cmd_events` only ever
calls `storage.iter_events`, never a backend module directly.

Oracles:
  1. Round-trip: the dumped JSONL parses back to exactly the records
     `storage.iter_events` yields (same count, same seq order, same
     payloads) -- both backends.
  2. `--since SEQ` emits only events with sequence > SEQ.
  3. `--tail` follows a new append made mid-poll, then a KeyboardInterrupt
     during the poll exits 0 cleanly.
  4. An unknown/never-written project emits nothing and exits 0 (no crash)
     -- both backends.
"""

from __future__ import annotations

import argparse
import json

import pytest

from nyxloom import cli, storage
from nyxloom.types import Actor, ActorKind, EventType

ACTOR = Actor(kind=ActorKind.OPERATOR, id="test")


@pytest.fixture()
def sqlite_backend(tmp_state, monkeypatch):
    """Isolated XDG state root (tmp_state) PLUS the SQLite backend dark flag
    enabled for the duration of one test."""
    monkeypatch.setenv("NYXLOOM_STATE_BACKEND", "sqlite")
    return tmp_state


def _seed(project: str, n: int) -> None:
    """Append `n` standalone events (no task_id/projection effect -- mirrors
    test_storage_sqlite.py's use of append_event for pure event-log tests)."""
    for i in range(n):
        storage.append_event(
            project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
            payload={"units": [f"unit-{i}"]},
        )


def _parse_lines(out: str) -> list[dict]:
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Oracle 1: round-trip

def test_round_trip_sqlite_backend(sqlite_backend, capsys):
    project = "sp04-roundtrip-sqlite"
    _seed(project, 4)

    exit_code = cli.main(["events", project])
    assert exit_code == 0

    dumped = _parse_lines(capsys.readouterr().out)
    live = [ev.to_dict() for ev in storage.iter_events(project)]

    assert len(dumped) == len(live) == 4
    assert dumped == live
    assert [d["sequence"] for d in dumped] == [1, 2, 3, 4]


def test_round_trip_file_backend(tmp_state, capsys):
    """Same oracle against the DEFAULT file backend (no NYXLOOM_STATE_BACKEND
    set) -- proves the command is backend-agnostic, not SQLite-specific."""
    project = "sp04-roundtrip-file"
    _seed(project, 3)

    exit_code = cli.main(["events", project, "--json"])
    assert exit_code == 0

    dumped = _parse_lines(capsys.readouterr().out)
    live = [ev.to_dict() for ev in storage.iter_events(project)]

    assert len(dumped) == len(live) == 3
    assert dumped == live


def test_json_flag_is_explicit_alias_for_default_output(sqlite_backend, capsys):
    """--json changes nothing: the default output is already JSONL."""
    project = "sp04-json-flag"
    _seed(project, 2)

    assert cli.main(["events", project, "--json"]) == 0
    with_flag = capsys.readouterr().out

    assert cli.main(["events", project]) == 0
    without_flag = capsys.readouterr().out

    assert with_flag == without_flag
    assert len(_parse_lines(with_flag)) == 2


# ---------------------------------------------------------------------------
# Oracle 2: --since filters

def test_since_filters_to_higher_sequence(sqlite_backend, capsys):
    project = "sp04-since"
    _seed(project, 5)  # seq 1..5

    exit_code = cli.main(["events", project, "--since", "2"])
    assert exit_code == 0

    dumped = _parse_lines(capsys.readouterr().out)
    assert [d["sequence"] for d in dumped] == [3, 4, 5]


def test_since_filters_file_backend(tmp_state, capsys):
    project = "sp04-since-file"
    _seed(project, 3)  # seq 1..3

    exit_code = cli.main(["events", project, "--since", "1"])
    assert exit_code == 0

    dumped = _parse_lines(capsys.readouterr().out)
    assert [d["sequence"] for d in dumped] == [2, 3]


# ---------------------------------------------------------------------------
# Oracle 3: --tail follows a new append, then a Ctrl-C exits cleanly

def test_tail_follows_new_append_then_interrupts_cleanly(sqlite_backend, capsys, monkeypatch):
    project = "sp04-tail"
    _seed(project, 1)  # seq 1, dumped before the tail loop starts

    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate a new event arriving while tailing.
            storage.append_event(
                project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
                payload={"units": ["tailed"]},
            )
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    args = argparse.Namespace(project=project, since=None, type=None, tail=True, json=False)
    exit_code = cli.cmd_events(args)

    assert exit_code == 0
    assert calls["n"] == 2  # one real poll (found the new event), one that interrupted

    dumped = _parse_lines(capsys.readouterr().out)
    assert [d["sequence"] for d in dumped] == [1, 2]
    assert dumped[1]["payload"] == {"units": ["tailed"]}


def test_tail_with_no_new_events_still_interrupts_cleanly(sqlite_backend, capsys, monkeypatch):
    """Bare tail loop (no append in between) still exits 0 on Ctrl-C."""
    project = "sp04-tail-empty"
    _seed(project, 1)

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    args = argparse.Namespace(project=project, since=None, type=None, tail=True, json=True)
    exit_code = cli.cmd_events(args)

    assert exit_code == 0
    dumped = _parse_lines(capsys.readouterr().out)
    assert [d["sequence"] for d in dumped] == [1]


# ---------------------------------------------------------------------------
# Oracle 4: unknown/never-written project -- no crash, nothing printed, exit 0

def test_unknown_project_file_backend_emits_nothing(tmp_state, capsys):
    exit_code = cli.main(["events", "sp04-never-registered-project"])
    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_unknown_project_sqlite_backend_emits_nothing(sqlite_backend, capsys):
    exit_code = cli.main(["events", "sp04-never-registered-sqlite"])
    assert exit_code == 0
    assert capsys.readouterr().out == ""
