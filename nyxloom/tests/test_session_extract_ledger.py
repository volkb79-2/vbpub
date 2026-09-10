"""ledger.py (E-012): mechanically-extracted files-touched/commits/branches/
test-run ledger, aggregated per boundary marker. Fixture style mirrors
test_session_extract_stats.py's own small synthetic Claude Code records.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyxloom.session_extract import ledger


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return base


def _tool_use(tool_id, name, **input_kw):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": input_kw}


def _tool_result(tool_id, content):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]}


def _write_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "fix the flaky test"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu1", "Read", file_path="/repo/tests/test_flaky.py"),
             ]}),
        _rec(type="user", uuid="ur1", timestamp="2026-01-01T00:00:02Z",
             message=_tool_result("tu1", "file contents here")),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu2", "Edit", file_path="/repo/tests/test_flaky.py"),
             ]}),
        _rec(type="user", uuid="ur2", timestamp="2026-01-01T00:00:04Z",
             message=_tool_result("tu2", "edited")),
        _rec(type="assistant", uuid="a3", timestamp="2026-01-01T00:00:05Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu3", "Bash", command="pytest tests/test_flaky.py -q"),
             ]}),
        _rec(type="user", uuid="ur3", timestamp="2026-01-01T00:00:06Z",
             message=_tool_result("tu3", "1 passed in 0.02s")),
        _rec(type="assistant", uuid="a4", timestamp="2026-01-01T00:00:07Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu4", "Bash", command='git commit -m "fix flaky test"'),
             ]}),
        _rec(type="user", uuid="ur4", timestamp="2026-01-01T00:00:08Z",
             message=_tool_result("tu4", "[main abc1234] fix flaky test\n 1 file changed")),
        _rec(type="assistant", uuid="a5", timestamp="2026-01-01T00:00:09Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu5", "Bash", command="git checkout -b feature/flaky-fix"),
             ]}),
        _rec(type="user", uuid="ur5", timestamp="2026-01-01T00:00:10Z",
             message=_tool_result("tu5", "Switched to a new branch 'feature/flaky-fix'")),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:20Z",
             message={"role": "user", "content": "great, what's next?"}),
        _rec(type="assistant", uuid="a6", timestamp="2026-01-01T00:00:21Z",
             message={"role": "assistant", "content": [
                 _tool_use("tu6", "Read", file_path="/repo/README.md"),
             ]}),
        _rec(type="user", uuid="ur6", timestamp="2026-01-01T00:00:22Z",
             message=_tool_result("tu6", "readme contents")),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_build_ledger_groups_files_and_commits_under_the_right_boundary(tmp_path):
    fp = _write_fixture(tmp_path)
    ledgers = ledger.build_ledger_claude_code(fp, boundary_markers={"u1", "u2"})

    u1 = ledgers["u1"]
    assert u1.files_read == ["/repo/tests/test_flaky.py"]
    assert u1.files_edited == ["/repo/tests/test_flaky.py"]
    assert u1.commits == ["abc1234"]
    assert u1.branches == ["feature/flaky-fix"]
    assert u1.tests == ["1 passed"]

    u2 = ledgers["u2"]
    assert u2.files_read == ["/repo/README.md"]
    assert u2.files_edited == []
    assert u2.commits == []


def test_build_ledger_dedups_repeated_file_touches(tmp_path):
    # tu1 (Read) and tu2 (Edit) both touch the SAME path -- read and edited
    # are tracked in separate buckets, but a bucket itself never repeats an
    # entry (E-012's own framing: "[files read: asd, sdf, fdg]", not a
    # per-tool-call log).
    fp = _write_fixture(tmp_path)
    ledgers = ledger.build_ledger_claude_code(fp, boundary_markers={"u1", "u2"})
    assert len(ledgers["u1"].files_read) == 1
    assert len(ledgers["u1"].files_edited) == 1


def test_ledger_render_only_shows_nonempty_categories():
    entry = ledger.Ledger(files_read=["a.py"], files_edited=[], commits=["deadbeef"], branches=[], tests=[])
    text = entry.render()
    assert text == "[files read: a.py] [commits created: deadbeef]"
    assert "files edited" not in text
    assert "branches" not in text
    assert "tests" not in text


def test_ledger_render_shows_branches_and_tests_too():
    entry = ledger.Ledger(branches=["feature/x"], tests=["3 passed, 1 failed"])
    text = entry.render()
    assert "[branches involved: feature/x]" in text
    assert "[tests: 3 passed, 1 failed]" in text


def test_build_ledger_skips_blank_malformed_lines_and_non_tool_result_content(tmp_path):
    fp = tmp_path / "session.jsonl"
    lines = [
        "",
        "   ",
        "not json at all",
        json.dumps(_rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
                         message={"role": "user", "content": "hi"})),
        json.dumps(_rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
                         message={"role": "assistant", "content": [
                             _tool_use("tu1", "Bash", command="git status"),
                         ]})),
        # A user record whose content list mixes a real text block (not a
        # tool_result) alongside the actual tool_result -- the non-result
        # block must be skipped without erroring.
        json.dumps(_rec(type="user", uuid="ur1", timestamp="2026-01-01T00:00:02Z",
                         message={"role": "user", "content": [
                             {"type": "text", "text": "a stray text block"},
                             {"type": "tool_result", "tool_use_id": "tu1", "content": "clean"},
                         ]})),
        # A tool_result whose content is a LIST, not a string -- exercises
        # the json.dumps fallback.
        json.dumps(_rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:03Z",
                         message={"role": "assistant", "content": [
                             _tool_use("tu2", "Bash", command="git commit -m x"),
                         ]})),
        json.dumps(_rec(type="user", uuid="ur2", timestamp="2026-01-01T00:00:04Z",
                         message={"role": "user", "content": [
                             {"type": "tool_result", "tool_use_id": "tu2",
                              "content": [{"type": "text", "text": "[main deadbee1] x"}]},
                         ]})),
    ]
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledgers = ledger.build_ledger_claude_code(fp, boundary_markers={"u1"})
    assert ledgers["u1"].commits == ["deadbee1"]


def test_ledger_is_empty():
    assert ledger.Ledger().is_empty() is True
    assert ledger.Ledger(files_read=["a.py"]).is_empty() is False


def test_build_ledger_unsupported_format_raises_not_implemented(tmp_path):
    import pytest

    fp = tmp_path / "x.jsonl"
    fp.write_text("", encoding="utf-8")
    with pytest.raises(NotImplementedError):
        ledger.build_ledger(fp, "codex", boundary_markers=set())


def test_build_ledger_tool_activity_before_any_boundary_lands_in_unbounded(tmp_path):
    # A resumed/continued session can open with tool activity before the
    # first real boundary this run ever sees -- that shouldn't silently
    # attach to whatever boundary marker happens to be iterated first.
    fp = _write_fixture(tmp_path)
    ledgers = ledger.build_ledger_claude_code(fp, boundary_markers=set())
    assert ledger.UNBOUNDED in ledgers
    assert ledgers[ledger.UNBOUNDED].files_read == ["/repo/tests/test_flaky.py", "/repo/README.md"]
