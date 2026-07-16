"""Tests for backlog_items.py — backlog.md light schema + typed auto-tick
on merge (P28). Each oracle (O1-O4) is a test case."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import backlog_items, cli, lint
from nyxloom.types import TaskState, TaskStateFile, utc_now


@pytest.fixture()
def make_statefile():
    """Factory for TaskStateFile objects (local copy of test_cli.py's)."""
    def _make(**kwargs):
        defaults = {
            "schema_version": 1,
            "task_id": "demo-P01-test",
            "project": "demo",
            "state": TaskState.ACTIVE,
            "since": utc_now(),
            "paused": False,
        }
        defaults.update(kwargs)
        return TaskStateFile(**defaults)
    return _make


VALID_BACKLOG = textwrap.dedent("""\
    # backlog

    - **B1 — legacy un-headered item.** Predates the schema; must stay valid
      with no header at all.
    - **B2 — headered, open, no links.** Just adopted the schema.
      <!-- nyxloom:backlog id=B2 status=open priority=3 -->
    - **B3 — headered, carved, linked to a handoff.** Waiting on merge.
      <!-- nyxloom:backlog id=B3 status=carved priority=1 carved_handoff=demo-P01-test decisions=D-001,D-002 -->
    """)

BAD_STATUS_BACKLOG = textwrap.dedent("""\
    - **B1 — bad status.** body.
      <!-- nyxloom:backlog id=B1 status=bogus -->
    """)

BAD_PRIORITY_BACKLOG = textwrap.dedent("""\
    - **B1 — bad priority.** body.
      <!-- nyxloom:backlog id=B1 status=open priority=high -->
    """)

MISSING_ID_BACKLOG = textwrap.dedent("""\
    - **B1 — missing id in header.** body.
      <!-- nyxloom:backlog status=open -->
    """)


# ----- O1: schema valid/invalid -----

class TestO1Schema:
    def test_parse_valid_backlog_all_items(self):
        items = backlog_items._parse_text(VALID_BACKLOG)
        assert [i.id for i in items] == ["B1", "B2", "B3"]

    def test_legacy_unheadered_item_defaults_open_no_links(self):
        items = backlog_items._parse_text(VALID_BACKLOG)
        b1 = items[0]
        assert b1.status == "open"
        assert b1.header_line is None
        assert b1.carved_handoff is None
        assert b1.raw_header is None

    def test_headered_item_parses_typed_fields(self):
        items = backlog_items._parse_text(VALID_BACKLOG)
        b3 = items[2]
        assert b3.status == "carved"
        assert b3.priority == 1
        assert b3.carved_handoff == "demo-P01-test"
        assert b3.decisions == ["D-001", "D-002"]

    def test_valid_backlog_yields_zero_findings(self):
        items = backlog_items._parse_text(VALID_BACKLOG)
        findings = backlog_items.validate(items, path="backlog.md")
        assert findings == []

    def test_bad_status_is_blocking_finding(self):
        items = backlog_items._parse_text(BAD_STATUS_BACKLOG)
        findings = backlog_items.validate(items, path="backlog.md")
        assert len(findings) == 1
        assert findings[0].rule == "BLG1"
        assert findings[0].severity == "error"

    def test_bad_priority_is_blocking_finding(self):
        items = backlog_items._parse_text(BAD_PRIORITY_BACKLOG)
        findings = backlog_items.validate(items, path="backlog.md")
        assert len(findings) == 1
        assert findings[0].rule == "BLG1"
        assert findings[0].severity == "error"

    def test_missing_id_is_blocking_finding(self):
        items = backlog_items._parse_text(MISSING_ID_BACKLOG)
        findings = backlog_items.validate(items, path="backlog.md")
        assert len(findings) == 1
        assert "id" in findings[0].message

    def test_lint_project_folds_in_backlog_findings(self, sample_project):
        backlog_path = sample_project.root / "nyxloom-trove" / "backlog.md"
        backlog_path.parent.mkdir(parents=True, exist_ok=True)
        backlog_path.write_text(BAD_STATUS_BACKLOG)

        results = lint.lint_project(sample_project)
        rel = str(backlog_path.relative_to(sample_project.root))
        assert rel in results
        assert any(f.rule == "BLG1" for f in results[rel])

    def test_lint_project_clean_backlog_zero_findings(self, sample_project):
        backlog_path = sample_project.root / "nyxloom-trove" / "backlog.md"
        backlog_path.parent.mkdir(parents=True, exist_ok=True)
        backlog_path.write_text(VALID_BACKLOG)

        results = lint.lint_project(sample_project)
        rel = str(backlog_path.relative_to(sample_project.root))
        assert results[rel] == []


# ----- O2: tick on merge -----

class TestO2TickOnMerge:
    def test_tick_merged_updates_linked_item(self, tmp_path):
        path = tmp_path / "backlog.md"
        path.write_text(VALID_BACKLOG)

        changed = backlog_items.tick_merged(path, "demo-P01-test", "abc1234")
        assert changed is True

        items = backlog_items.parse(path)
        b3 = next(i for i in items if i.id == "B3")
        assert b3.status == "merged"
        assert b3.merge_commit == "abc1234"

    def test_cli_merge_ticks_linked_backlog_item(self, sample_project, tmp_state, make_statefile):
        from nyxloom import storage

        backlog_path = sample_project.root / "nyxloom-trove" / "backlog.md"
        backlog_path.parent.mkdir(parents=True, exist_ok=True)
        backlog_path.write_text(VALID_BACKLOG)

        tsf = make_statefile(state=TaskState.MERGE_READY)
        storage.save_state(tsf)

        explicit = "b" * 40
        exit_code = cli.main(["merge", "demo", "demo-P01-test", "--commit", explicit])
        assert exit_code == 0

        items = backlog_items.parse(backlog_path)
        b3 = next(i for i in items if i.id == "B3")
        assert b3.status == "merged"
        assert b3.merge_commit == explicit


# ----- O3: typed-only, prose + siblings untouched -----

class TestO3TypedOnly:
    def test_prose_and_siblings_byte_identical_after_tick(self, tmp_path):
        path = tmp_path / "backlog.md"
        path.write_text(VALID_BACKLOG)

        backlog_items.tick_merged(path, "demo-P01-test", "abc1234")

        new_text = path.read_text()
        old_lines = VALID_BACKLOG.splitlines()
        new_lines = new_text.splitlines()

        # Only the B3 header-comment line may differ; everything else,
        # including B3's own prose line and every line of B1/B2, is
        # byte-identical.
        assert len(old_lines) == len(new_lines)
        for i, (old, new) in enumerate(zip(old_lines, new_lines)):
            if old != new:
                assert "nyxloom:backlog" in old
                assert "id=B3" in old
                assert "status=carved" in old
            else:
                continue

        items = backlog_items.parse(path)
        b1, b2 = items[0], items[1]
        assert b1.status == "open" and b1.header_line is None
        assert b2.status == "open" and b2.priority == 3


# ----- O4: unlinked merge is a clean no-op -----

class TestO4UnlinkedNoop:
    def test_tick_merged_no_match_returns_false_no_write(self, tmp_path):
        path = tmp_path / "backlog.md"
        path.write_text(VALID_BACKLOG)
        before = path.read_text()

        changed = backlog_items.tick_merged(path, "no-such-task", "abc1234")
        assert changed is False
        assert path.read_text() == before

    def test_cli_merge_unlinked_task_leaves_backlog_untouched(self, sample_project, tmp_state, make_statefile):
        from nyxloom import storage

        backlog_path = sample_project.root / "nyxloom-trove" / "backlog.md"
        backlog_path.parent.mkdir(parents=True, exist_ok=True)
        backlog_path.write_text(VALID_BACKLOG)
        before = backlog_path.read_text()

        # No backlog item links this task id (VALID_BACKLOG's B3 links
        # 'demo-P01-test'), so the merge must be a clean backlog no-op.
        tsf = make_statefile(task_id="demo-P01-unlinked", state=TaskState.MERGE_READY)
        storage.save_state(tsf)

        exit_code = cli.main(["merge", "demo", "demo-P01-unlinked", "--commit", "c" * 40])
        assert exit_code == 0
        assert backlog_path.read_text() == before

    def test_missing_backlog_file_is_noop(self, tmp_path):
        path = tmp_path / "does-not-exist.md"
        assert backlog_items.tick_merged(path, "anything", "abc1234") is False
        assert backlog_items.parse(path) == []
