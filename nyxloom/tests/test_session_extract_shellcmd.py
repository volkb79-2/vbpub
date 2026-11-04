"""shellcmd.py detector oracles for backlog NL-35 (measured misses and false
positives from the SUCCESSOR round-2 corpus run, decision D4)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nyxloom.session_extract import ledger, shellcmd
from nyxloom.session_extract.shellcmd import effect_segments, unresolved_head_segments


# --- measured misses: a real effect must be reported ------------------------

@pytest.mark.parametrize("command", [
    "sudo -u deploy git push origin main",
    "sudo -E -u root -g wheel systemctl restart nginx",
    "timeout -s KILL 30 git push origin main",
    "timeout -k 5 -s TERM 1.5 docker rm x",
    "A=1 B=2 C=3 D=4 E=5 F=6 G=7 H=8 I=9 J=10 K=11 L=12 M=13 N=14 git push",
    'echo "$(git push origin main)"',
    'echo "x $(sudo -u a systemctl stop db) y"',
])
def test_measured_misses_are_now_effects(command):
    assert effect_segments(command), command


@pytest.mark.parametrize("command", [
    "git tag v1.2.3",
    "git tag -a v1 -m msg",
    "git tag -d v1",
    "git tag -f v1 abc123",
    "git tag --delete v1",
])
def test_git_tag_creation_and_deletion_are_effects(command):
    assert effect_segments(command), command


# --- measured false positives: a non-effect must stay silent ----------------

@pytest.mark.parametrize("command", [
    "git tag",
    "git tag -n",
    "git tag -l 'v1*'",
    "git tag --list",
    "git tag --contains abc123",
    "git tag --sort=-creatordate",
    'curl -H "a -d b" https://example.invalid/x',
    "curl -H 'x: -X POST' https://example.invalid/x",
    'echo "$(git status)"',
    "echo '$(git push)'",
])
def test_measured_false_positives_are_not_effects(command):
    assert effect_segments(command) == [], command


@pytest.mark.parametrize("command", [
    'curl -H "a -d b" -d x https://example.invalid/x',
    'curl -H "a: b" -X DELETE https://example.invalid/x',
    "curl --json '{}' https://example.invalid/x",
])
def test_real_curl_mutations_still_detected_next_to_quoted_flags(command):
    assert effect_segments(command), command


def test_curl_quote_skipper_is_linear_on_adversarial_input():
    # many quoted strings, no mutating flag: must finish promptly (no backtracking blow-up)
    command = "curl " + " ".join(f'-H "h{i}: v -x {i}"' for i in range(400)) + " https://example.invalid/"
    assert effect_segments(command) == []
    assert effect_segments('curl -H "unterminated -d x https://example.invalid/') == []


# --- NL-35 contract (b): unresolved heads are visible, never silent --------

def test_variable_indirection_is_reported_as_unresolved():
    assert unresolved_head_segments('NC="python3 nc.py"; $NC snapshots delete 1') == ["$NC snapshots delete 1"]
    assert unresolved_head_segments("$SSH host rm -rf /x") == ["$SSH host rm -rf /x"]
    assert unresolved_head_segments("ssh host 'sudo $CMD'") == ["$CMD"]


def test_resolvable_commands_are_not_marked_unresolved():
    assert unresolved_head_segments("git push origin main") == []
    assert unresolved_head_segments("ls $HOME") == []
    assert unresolved_head_segments("ssh host uname -r") == []


def _session(tmp_path: Path, command: str) -> Path:
    records = [
        {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "s",
         "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z", "sessionId": "s",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}},
    ]
    fp = tmp_path / "s.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_ledger_prints_the_unresolved_marker_instead_of_silence(tmp_path):
    fp = _session(tmp_path, 'NC="python3 nc.py"; $NC snapshots delete 1')
    ledgers = ledger.build_ledger_claude_code(fp, boundary_markers={"u1"})
    effects = ledgers["u1"].external_effects
    assert len(effects) == 1
    assert "[possible effect, head unresolved] $NC snapshots delete 1" in effects[0]


def test_ledger_does_not_double_list_a_resolved_effect(tmp_path):
    fp = _session(tmp_path, "sudo -u deploy git push origin main")
    effects = ledger.build_ledger_claude_code(fp, boundary_markers={"u1"})["u1"].external_effects
    assert len(effects) == 1 and "unresolved" not in effects[0]


def test_controlled_wrong_implementation_that_drops_unresolved_heads_fails(tmp_path, monkeypatch):
    """The oracle has teeth: if the unresolved reporter returned nothing, the ledger
    would be silent again and the marker assertion above would fail."""
    monkeypatch.setattr(ledger, "unresolved_head_segments", lambda *a, **k: [])
    fp = _session(tmp_path, "$NC snapshots delete 1")
    effects = ledger.build_ledger_claude_code(fp, boundary_markers={"u1"})["u1"].external_effects
    assert not any(re.search("head unresolved", e) for e in effects)  # silent => the real test would fail


def test_read_only_classification_sees_quoted_substitutions():
    assert shellcmd.is_read_only('echo "$(ls)"') is True
    assert shellcmd.is_read_only('echo "$(git push)"') is False
