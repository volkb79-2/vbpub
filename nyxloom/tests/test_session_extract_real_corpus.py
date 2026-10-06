"""Real-corpus checks: records copied VERBATIM from a Claude Code 2.1.289
controller transcript (`fixtures/real_interview_2_1_289.jsonl` = physical lines
308 and 314 of that session: an AskUserQuestion batch and its answer record).

The opt-in smoke test runs `--preset successor` and `--preset watch --jsonl`
over a whole real transcript named by $NYXLOOM_REAL_TRANSCRIPT."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from nyxloom.cli_harness import main as harness_main

FIXTURE = Path(__file__).parent / "fixtures" / "real_interview_2_1_289.jsonl"

# Same pattern family as the secret scan used when the fixture was selected.
STRICT = re.compile(
    r'ghp_|gho_|ghs_|github_pat_|xox[abposr]-|hooks/|hooks\.|Bearer|PRIVATE KEY|BEGIN [A-Z ]*KEY|webhook'
    r'|AKIA[0-9A-Z]{12}|sk-[A-Za-z0-9]{16}|://[^\s"/@]+:[^\s"/@]+@|authorization'
    r'|(?:api[_-]?key|passw(?:or)?d|secret|token)["\']?\s*[:=]', re.I)

Q1 = "With C, how should pip find cli-extended, given that the bare name on PyPI is a dependency-confusion risk?"
Q2 = ("The Netcup scripts run from a checkout, and debian-install-v2 copies itself onto fresh remote Debian "
      "hosts. Under C, how do they get cli-extended?")


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_fixture_is_two_verbatim_records_and_passes_the_secret_scan():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["version"] for line in lines] == ["2.1.289", "2.1.289"]
    assert STRICT.search(FIXTURE.read_text(encoding="utf-8")) is None


def test_watch_renders_the_real_interview_compactly(capsys):
    code, out, err = _run(capsys, FIXTURE, "--preset", "watch", "--no-color")
    assert code == 0, err
    assert out == (f"[01:22:27] {Q1}\n\n"
                   f"[01:22:27] {Q2}\n\n"
                   "[01:46:12] OPERATOR: GH Releases + --no-index (Recommended)\n\n"
                   "OPERATOR: Venv + pinned wheel (Recommended)\n\n")
    # questions are single lines: no option text, header or description leaks in
    assert "Header:" not in out and "INTERVIEW" not in out and "Release cli-extended like" not in out


def test_watch_jsonl_renders_the_real_interview_with_the_version_key(capsys):
    code, out, err = _run(capsys, FIXTURE, "--preset", "watch", "--jsonl")
    assert code == 0, err
    rows = [json.loads(line) for line in out.splitlines()]
    assert [(r["v"], r["role"]) for r in rows] == [(1, "assistant"), (1, "assistant"), (1, "operator"), (1, "operator")]
    assert rows[0]["text"] == Q1 and rows[2]["text"] == "GH Releases + --no-index (Recommended)"
    assert all(list(r) == ["v", "ts", "role", "text"] for r in rows)  # a main-session file has no `agent`


@pytest.mark.skipif(not os.environ.get("NYXLOOM_REAL_TRANSCRIPT"), reason="set NYXLOOM_REAL_TRANSCRIPT to run")
def test_smoke_over_a_whole_real_transcript(capsys):
    path = os.environ["NYXLOOM_REAL_TRANSCRIPT"]
    code, out, err = _run(capsys, path, "--preset", "watch", "--jsonl")
    assert code == 0, err
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows and {r["role"] for r in rows} <= {"operator", "assistant"}
    assert not any("<system-reminder>" in r["text"] or "<task-notification>" in r["text"] for r in rows)
    code, out, err = _run(capsys, path, "--preset", "successor")
    assert code == 0, err
    assert out.strip()
