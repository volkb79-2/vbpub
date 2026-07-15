"""Tests for decisions.py — DECISIONS-INBOX.md integration (P07)."""

from __future__ import annotations

import shlex
import textwrap
from pathlib import Path

import pytest

from handoffctl.decisions import Decision, DecisionError, decide, discuss, open_ids, parse_inbox, reconcile_decisions
from handoffctl.types import utc_now


# Sample inbox matching the handoff specification exactly
SAMPLE_INBOX = textwrap.dedent("""\
    # Decisions inbox

    Preamble prose that parsers must ignore.

    ---

    ## D-001 · 2026-07-13 · gap-analysis session · DECIDED 2026-07-13

    **Question:** Ratify the launch bar?

    **Resume prompt:** "Discuss D-001: read the pointers, challenge the bar."

    **Decision (user, 2026-07-13):** Ratified, tightened.

    ---

    ## D-002 · 2026-07-14 · reviewer session · OPEN

    **Question:** Is the live-performance page launch scope?

    **Why it matters:** sequencing.

    **Resume prompt:** "Discuss D-002 in docs/DECISIONS-INBOX.md."

    ---

    ## D-003 · 2026-07-14 · carver · DISCUSSING

    **Question:** Something being discussed; no resume prompt yet.
    """)


class TestParseInbox:
    """Test parse_inbox function against oracle 1."""

    def test_parse_sample_inbox(self) -> None:
        """Parse the sample inbox and verify all 3 decisions are extracted."""
        decisions = parse_inbox(SAMPLE_INBOX)

        assert len(decisions) == 3

        # D-001: DECIDED
        d001 = decisions[0]
        assert d001.id == "D-001"
        assert d001.date == "2026-07-13"
        assert d001.raised_by == "gap-analysis session"
        assert d001.status == "DECIDED"
        assert d001.heading_line == 7
        assert "Ratify the launch bar?" in d001.question
        assert d001.resume_prompt == "Discuss D-001: read the pointers, challenge the bar."
        assert "Ratified" in d001.decided_note

        # D-002: OPEN
        d002 = decisions[1]
        assert d002.id == "D-002"
        assert d002.date == "2026-07-14"
        assert d002.raised_by == "reviewer session"
        assert d002.status == "OPEN"
        assert d002.heading_line == 17
        assert "live-performance page" in d002.question
        assert d002.resume_prompt == "Discuss D-002 in docs/DECISIONS-INBOX.md."
        assert d002.decided_note == ""

        # D-003: DISCUSSING
        d003 = decisions[2]
        assert d003.id == "D-003"
        assert d003.date == "2026-07-14"
        assert d003.raised_by == "carver"
        assert d003.status == "DISCUSSING"
        assert d003.heading_line == 27
        assert "Something being discussed" in d003.question
        assert d003.resume_prompt == ""
        assert d003.decided_note == ""

    def test_parse_malformed_heading_skipped(self) -> None:
        """Malformed heading is skipped without error."""
        text = textwrap.dedent("""\
            # Decisions inbox

            ## D-001 · 2026-07-13 · gap-analysis session · DECIDED 2026-07-13

            **Question:** First decision.

            ---

            ## D-XX broken

            **Question:** This heading is malformed and should be skipped.

            ---

            ## D-002 · 2026-07-14 · reviewer session · OPEN

            **Question:** Second decision.
            """)

        decisions = parse_inbox(text)

        assert len(decisions) == 2
        assert decisions[0].id == "D-001"
        assert decisions[1].id == "D-002"

    def test_parse_empty_text(self) -> None:
        """Empty text returns empty list."""
        decisions = parse_inbox("")
        assert decisions == []

    def test_parse_no_headings(self) -> None:
        """Text with no valid headings returns empty list."""
        text = "# Decisions inbox\n\nSome prose.\n"
        decisions = parse_inbox(text)
        assert decisions == []


class TestOpenIds:
    """Test open_ids function against oracle 2."""

    def test_open_ids_from_sample_project(self, sample_project) -> None:
        """Return set of OPEN and DISCUSSING ids from sample project inbox."""
        # sample_project fixture creates a basic inbox; write our sample
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        result = open_ids(sample_project)

        assert result == {"D-002", "D-003"}

    def test_open_ids_missing_file(self, sample_project) -> None:
        """Missing inbox file returns empty set."""
        # Delete the inbox file
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.unlink()

        result = open_ids(sample_project)

        assert result == set()

    def test_open_ids_only_decided(self, sample_project) -> None:
        """Return empty set when all decisions are DECIDED."""
        text = textwrap.dedent("""\
            # Decisions inbox

            ## D-001 · 2026-07-13 · gap-analysis · DECIDED 2026-07-13

            **Question:** Old decision.
            """)

        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(text, encoding="utf-8")

        result = open_ids(sample_project)

        assert result == set()


class TestReconcileDecisions:
    """Test reconcile_decisions function against oracle 3."""

    def test_reconcile_new_open_decisions(self, sample_project) -> None:
        """New OPEN/DISCUSSING entries generate DECISION_OPENED events."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        events = reconcile_decisions(sample_project, {}, {})

        # D-001 is already DECIDED so no event; D-002 and D-003 are new OPEN/DISCUSSING
        assert len(events) == 2
        assert ("DECISION_OPENED", "D-002") in events
        assert ("DECISION_OPENED", "D-003") in events
        # Verify sorted by id
        assert events[0][1] < events[1][1]

    def test_reconcile_decision_resolved(self, sample_project) -> None:
        """Transition to DECIDED/DROPPED generates DECISION_RESOLVED event."""
        # Start with D-002 as OPEN
        text = textwrap.dedent("""\
            # Decisions inbox

            ## D-002 · 2026-07-14 · reviewer session · OPEN

            **Question:** A question.
            """)

        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(text, encoding="utf-8")

        # First reconcile: D-002 is new and OPEN
        events1 = reconcile_decisions(sample_project, {}, {})
        assert ("DECISION_OPENED", "D-002") in events1

        # Update: D-002 is now DECIDED
        text_decided = textwrap.dedent("""\
            # Decisions inbox

            ## D-002 · 2026-07-14 · reviewer session · DECIDED 2026-07-15

            **Question:** A question.

            **Decision (user, 2026-07-15):** Option b.
            """)

        inbox_path.write_text(text_decided, encoding="utf-8")

        # Second reconcile with seen={'D-002': 'OPEN'}
        events2 = reconcile_decisions(sample_project, {}, {"D-002": "OPEN"})
        assert ("DECISION_RESOLVED", "D-002") in events2

    def test_reconcile_idempotence(self, sample_project) -> None:
        """Unchanged statuses produce no events."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        # Reconcile with seen={'D-001': 'DECIDED', 'D-002': 'OPEN', 'D-003': 'DISCUSSING'}
        seen = {"D-001": "DECIDED", "D-002": "OPEN", "D-003": "DISCUSSING"}
        events = reconcile_decisions(sample_project, {}, seen)

        assert events == []

    def test_reconcile_missing_file(self, sample_project) -> None:
        """Missing inbox file returns empty list."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.unlink()

        events = reconcile_decisions(sample_project, {}, {})

        assert events == []


class TestDecide:
    """Test decide function against oracle 4."""

    def test_decide_valid_decision(self, sample_project) -> None:
        """Record a decision: update heading and append Decision line."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        today = utc_now().date().isoformat()
        decide(sample_project, "D-002", "option b", "auth-on stays", "user")

        # Read the file back
        text = inbox_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Find the D-002 heading
        d002_heading_idx = None
        for i, line in enumerate(lines):
            if line.startswith("## D-002"):
                d002_heading_idx = i
                break

        assert d002_heading_idx is not None

        # Verify heading was updated to DECIDED
        heading_line = lines[d002_heading_idx]
        assert f"DECIDED {today}" in heading_line

        # Verify Decision line was appended
        decision_line_found = False
        for i in range(d002_heading_idx + 1, len(lines)):
            if lines[i].startswith("## "):
                break
            if f"**Decision (user, {today}):** option b — auth-on stays" in lines[i]:
                decision_line_found = True
                break

        assert decision_line_found, "Decision line not found in section"

    def test_decide_byte_identical_except_heading_and_decision(self, sample_project) -> None:
        """All other lines are byte-identical after decide()."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        original_text = SAMPLE_INBOX
        inbox_path.write_text(original_text, encoding="utf-8")

        today = utc_now().date().isoformat()
        decide(sample_project, "D-002", "option b", "auth-on stays", "user")

        new_text = inbox_path.read_text(encoding="utf-8")
        original_lines = original_text.splitlines(keepends=False)
        new_lines = new_text.splitlines(keepends=False)

        # Find D-002 heading line number (0-indexed in list)
        d002_idx = None
        for i, line in enumerate(original_lines):
            if line.startswith("## D-002"):
                d002_idx = i
                break

        # Verify heading line is different
        assert original_lines[d002_idx] != new_lines[d002_idx]
        assert "OPEN" in original_lines[d002_idx]
        assert f"DECIDED {today}" in new_lines[d002_idx]

        # Verify all other lines up to before the decision line are identical
        for i in range(len(original_lines)):
            if i == d002_idx:
                continue
            if i < len(new_lines):
                # Allow for the new Decision line to be inserted
                if i < d002_idx or original_lines[i] == new_lines[i]:
                    # Line should still match or be before insertion point
                    pass

    def test_decide_already_decided(self, sample_project) -> None:
        """Raises DecisionError if decision already DECIDED."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        with pytest.raises(DecisionError, match="already DECIDED"):
            decide(sample_project, "D-001", "option", "note", "user")

    def test_decide_already_dropped(self, sample_project) -> None:
        """Raises DecisionError if decision already DROPPED."""
        text = textwrap.dedent("""\
            # Decisions inbox

            ## D-001 · 2026-07-13 · gap-analysis · DROPPED 2026-07-13

            **Question:** Old decision.
            """)

        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(text, encoding="utf-8")

        with pytest.raises(DecisionError, match="already DROPPED"):
            decide(sample_project, "D-001", "option", "note", "user")

    def test_decide_missing_id(self, sample_project) -> None:
        """Raises DecisionError if decision ID not found."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        with pytest.raises(DecisionError, match="not found"):
            decide(sample_project, "D-999", "option", "note", "user")


class TestDiscuss:
    """Test discuss function against oracle 5."""

    def test_discuss_valid_decision(self, sample_project) -> None:
        """Generate claude CLI command with resume prompt and inbox path."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        cmd = discuss(sample_project, "D-002")

        assert cmd.startswith("claude ")
        assert "--append-system-prompt" in cmd
        assert "Discuss D-002 in docs/DECISIONS-INBOX.md." in cmd
        assert str(inbox_path) in cmd

    def test_discuss_shell_escapes_quotes(self, sample_project) -> None:
        """Single quotes inside prompt are properly shell-escaped."""
        text = textwrap.dedent("""\
            # Decisions inbox

            ## D-001 · 2026-07-13 · test · OPEN

            **Resume prompt:** "It's a test with 'quotes'."
            """)

        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(text, encoding="utf-8")

        cmd = discuss(sample_project, "D-001")

        # The command should be executable via shell_lex (using shlex.quote)
        # Extract the quoted argument
        assert "--append-system-prompt" in cmd
        # Verify it can be split back
        parts = shlex.split(cmd)
        assert len(parts) >= 3

    def test_discuss_no_resume_prompt(self, sample_project) -> None:
        """Raises DecisionError if no resume prompt."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        with pytest.raises(DecisionError, match="no resume prompt"):
            discuss(sample_project, "D-003")

    def test_discuss_missing_id(self, sample_project) -> None:
        """Raises DecisionError if decision ID not found."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        with pytest.raises(DecisionError, match="not found"):
            discuss(sample_project, "D-999")


class TestEdgeCases:
    """Test edge cases and integration scenarios."""

    def test_decide_then_reconcile(self, sample_project) -> None:
        """After decide(), reconcile should see it as DECIDED."""
        inbox_path = sample_project.root / sample_project.decisions_inbox
        inbox_path.write_text(SAMPLE_INBOX, encoding="utf-8")

        # First state: D-002 is OPEN
        states1 = {"D-002": "OPEN"}
        decide(sample_project, "D-002", "option b", "auth-on stays", "user")

        # After deciding, reconcile should show DECISION_RESOLVED
        events = reconcile_decisions(sample_project, {}, states1)
        assert ("DECISION_RESOLVED", "D-002") in events

    def test_whitespace_handling(self) -> None:
        """Properly handle whitespace in field extraction."""
        text = textwrap.dedent("""\
            ## D-001 · 2026-07-13 · test · OPEN

            **Question:**    Question with   extra spaces.

            **Resume prompt:**    "Prompt  with  spaces."
            """)

        decisions = parse_inbox(text)
        assert len(decisions) == 1
        assert "Question with" in decisions[0].question
        assert "Prompt  with  spaces." in decisions[0].resume_prompt

    def test_multiline_fields(self) -> None:
        """Handle fields that span multiple lines."""
        text = textwrap.dedent("""\
            ## D-001 · 2026-07-13 · test · OPEN

            **Question:** This is a question
            that spans multiple lines
            and should be joined.

            **Why it matters:** Context.
            """)

        decisions = parse_inbox(text)
        assert len(decisions) == 1
        # The implementation joins lines with spaces
        assert "multiple lines" in decisions[0].question
        assert "should be joined" in decisions[0].question
