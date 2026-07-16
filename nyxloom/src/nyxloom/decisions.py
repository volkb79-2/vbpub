"""DECISIONS-INBOX.md integration (ARCHITECTURE §8). PACKAGE P07.

The md file stays the durable, human-owned record (files-first); this module
parses it, reconciles status changes into events, and provides the CLI verbs.
It NEVER rewrites entry prose — decide() only appends a decision record
block and flips the status token on the entry's heading line.

ENTRY FORMAT (existing convention, dstdns/topos):
    ## D-001 · 2026-07-13 · gap-analysis session · DECIDED 2026-07-13
    **Question:** ...
    **Why it matters:** ...
    **Options:** ...
    **Recommendation:** ...
    **Context pointers:** ...
    **Resume prompt:** "..."
    **Decision (user, 2026-07-13):** ...        (present when decided)
Status token on the heading line is one of OPEN / DISCUSSING / DECIDED /
DROPPED (DECIDED/DROPPED may carry a trailing date). Heading regex:
    ^##\\s+(D-\\d+)\\s+·\\s+(\\S+)\\s+·\\s+(.+?)\\s+·\\s+(OPEN|DISCUSSING|DECIDED|DROPPED)\\b

INTERFACE CONTRACT (frozen):

- Decision dataclass fields: id, date, raised_by, status, heading_line
  (1-based), question (first **Question:** paragraph, ''-default),
  resume_prompt (''-default), decided_note (''-default).
- parse_inbox(text) -> list[Decision]; malformed headings are skipped (a
  DoctorFinding is the place to complain, not an exception).
- open_ids(cfg) -> set[str]: OPEN + DISCUSSING ids from the project inbox
  (read from cfg.root / cfg.decisions_inbox; missing file -> empty set).
- reconcile_decisions(cfg, states, seen: dict[str, str]) -> list of
  (event_type, decision_id) the caller should emit: for each parsed entry
  compare with seen (id -> last status): new-or-was-closed OPEN/DISCUSSING
  -> DECISION_OPENED once; transition to DECIDED/DROPPED ->
  DECISION_RESOLVED once. Caller (daemon) persists `seen` in daemon memory
  and dedupes across restarts by scanning prior DECISION_* events.
- decide(cfg, decision_id, choice, note, authority) -> None:
  Rewrites the inbox file: heading status token -> DECIDED <today>, and
  appends under the entry:
      **Decision (<authority>, <today>):** <choice> — <note>
  Raises DecisionError if the id is missing or already DECIDED/DROPPED.
  (Event emission is the CLI's job via storage.append_event with
  decision_id set — this module only edits the file.)
- discuss(cfg, decision_id) -> str: a ready-to-run command string
      claude --append-system-prompt '<resume prompt + entry heading +
      inbox path>' (single-quoted, quote-escaped)
  Raises DecisionError when the entry has no resume prompt.
- open_decision(cfg, question, resume_prompt, raised_by='intake-agent') ->
  str: the inverse of decide() -- appends a brand-new OPEN entry (next
  D-<NNN>, zero-padded 3 digits) with **Question:**/**Resume prompt:**
  paragraphs, and returns the allocated id. PACKAGE P29 (feature-intake
  agent): lets the intake interview file a product call for the operator
  without guessing. File-write only, like decide() -- event emission (if
  any) is the caller's job; in practice the next daemon reconcile tick
  picks up the new OPEN entry and emits DECISION_OPENED itself via
  reconcile_decisions, so no double-write here.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig
from .types import TaskStateFile, utc_now


class DecisionError(Exception):
    pass


@dataclass
class Decision:
    id: str
    date: str
    raised_by: str
    status: str
    heading_line: int
    question: str = ""
    resume_prompt: str = ""
    decided_note: str = ""


def parse_inbox(text: str) -> list[Decision]:
    """Parse markdown text containing decision entries.

    Extracts decisions based on the heading regex, skipping malformed headings.
    Returns a list of Decision objects with all fields populated from the text.
    """
    heading_pattern = re.compile(
        r"^##\s+(D-\d+)\s+·\s+(\S+)\s+·\s+(.+?)\s+·\s+(OPEN|DISCUSSING|DECIDED|DROPPED)\b"
    )

    lines = text.splitlines()
    decisions: list[Decision] = []
    current_entry: dict | None = None
    current_line_num = 0

    for line_num, line in enumerate(lines, start=1):
        match = heading_pattern.match(line)

        if match:
            # Save previous entry if exists
            if current_entry is not None:
                decisions.append(_finalize_decision(current_entry))

            # Start new entry
            decision_id, date, raised_by, status = match.groups()
            current_entry = {
                "id": decision_id,
                "date": date,
                "raised_by": raised_by,
                "status": status,
                "heading_line": line_num,
                "question": "",
                "resume_prompt": "",
                "decided_note": "",
                "lines_after": [],
            }
            current_line_num = line_num
        elif current_entry is not None:
            # Collect lines after heading until next heading
            current_entry["lines_after"].append(line)

    # Finalize last entry if exists
    if current_entry is not None:
        decisions.append(_finalize_decision(current_entry))

    return decisions


def _finalize_decision(entry: dict) -> Decision:
    """Extract question, resume_prompt, and decided_note from entry lines."""
    lines = entry["lines_after"]

    # Extract question: first **Question:** paragraph
    question = _extract_field(lines, "Question")

    # Extract resume_prompt: **Resume prompt:** "..."
    resume_prompt = _extract_field(lines, "Resume prompt")
    # Strip quotes if present
    if resume_prompt.startswith('"') and resume_prompt.endswith('"'):
        resume_prompt = resume_prompt[1:-1]

    # Extract decided_note: **Decision (...):** line
    decided_note = _extract_field(lines, "Decision")

    return Decision(
        id=entry["id"],
        date=entry["date"],
        raised_by=entry["raised_by"],
        status=entry["status"],
        heading_line=entry["heading_line"],
        question=question,
        resume_prompt=resume_prompt,
        decided_note=decided_note,
    )


def _extract_field(lines: list[str], key: str) -> str:
    """Extract text following **Key:** pattern until blank line.

    Handles patterns like **Key:** or **Key (...):** by matching ** and **
    around the key, allowing optional content in parentheses.
    """
    # Match **Key:** or **Key (...):**
    # For Decision field specifically, allow parentheses with content
    pattern = re.compile(f"^\\*\\*{re.escape(key)}[^:]*:\\*\\*\\s*(.*)")
    result = []
    found = False

    for line in lines:
        if not found:
            match = pattern.match(line)
            if match:
                found = True
                remainder = match.group(1).strip()
                if remainder:
                    result.append(remainder)
        else:
            # Continue accumulating until blank line
            if line.strip() == "":
                break
            result.append(line)

    return " ".join(result).strip()


def open_ids(cfg: ProjectConfig) -> set[str]:
    """Return set of OPEN and DISCUSSING decision IDs from the project inbox.

    Missing inbox file returns empty set.
    """
    inbox_path = cfg.root / cfg.decisions_inbox

    if not inbox_path.exists():
        return set()

    text = inbox_path.read_text(encoding="utf-8")
    decisions = parse_inbox(text)

    return {d.id for d in decisions if d.status in ("OPEN", "DISCUSSING")}


def reconcile_decisions(cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        seen: dict[str, str]) -> list[tuple[str, str]]:
    """Reconcile decision status changes and generate events.

    Compare parsed decisions with previously seen statuses:
    - New entries with OPEN/DISCUSSING status -> DECISION_OPENED
    - Entries transitioning to DECIDED/DROPPED -> DECISION_RESOLVED
    - Unchanged statuses -> no event
    """
    inbox_path = cfg.root / cfg.decisions_inbox

    if not inbox_path.exists():
        return []

    text = inbox_path.read_text(encoding="utf-8")
    decisions = parse_inbox(text)

    events: list[tuple[str, str]] = []

    for decision in decisions:
        prev_status = seen.get(decision.id)

        # New entry or status changed from not-open to open/discussing
        if prev_status is None and decision.status in ("OPEN", "DISCUSSING"):
            events.append(("DECISION_OPENED", decision.id))
        # Transition to terminal status
        elif prev_status is not None and prev_status not in ("DECIDED", "DROPPED"):
            if decision.status in ("DECIDED", "DROPPED"):
                events.append(("DECISION_RESOLVED", decision.id))

    # Sort by id for determinism
    events.sort(key=lambda x: x[1])

    return events


def decide(cfg: ProjectConfig, decision_id: str, choice: str, note: str,
           authority: str) -> None:
    """Record a decision in the inbox file.

    Updates the heading line status to DECIDED with today's date, and
    appends a Decision line. Raises DecisionError if the ID is missing or
    already decided.
    """
    inbox_path = cfg.root / cfg.decisions_inbox

    if not inbox_path.exists():
        raise DecisionError(f"Inbox file not found: {inbox_path}")

    text = inbox_path.read_text(encoding="utf-8")
    decisions = parse_inbox(text)

    # Find the decision
    decision = None
    for d in decisions:
        if d.id == decision_id:
            decision = d
            break

    if decision is None:
        raise DecisionError(f"Decision {decision_id} not found")

    if decision.status in ("DECIDED", "DROPPED"):
        raise DecisionError(f"Decision {decision_id} is already {decision.status}")

    # Get today's date in ISO format
    today = utc_now().date().isoformat()

    # Update the heading line
    lines = text.splitlines(keepends=False)
    heading_pattern = re.compile(
        r"^(##\s+D-\d+\s+·\s+\S+\s+·\s+.+?\s+·\s+)(OPEN|DISCUSSING|DECIDED|DROPPED)\b(.*)$"
    )

    heading_line_idx = decision.heading_line - 1
    heading_line = lines[heading_line_idx]

    match = heading_pattern.match(heading_line)
    if not match:
        raise DecisionError(f"Cannot parse heading line {decision.heading_line}")

    # Replace heading line with DECIDED status and today's date
    new_heading = f"{match.group(1)}DECIDED {today}{match.group(3)}"
    lines[heading_line_idx] = new_heading

    # Find where to insert the Decision line (after the heading, before next heading or EOF)
    insert_idx = decision.heading_line
    for i in range(decision.heading_line, len(lines)):
        # Stop at next heading or if we find another decision entry
        if i > decision.heading_line - 1 and lines[i].startswith("## "):
            insert_idx = i
            break
    else:
        # No next heading found, append at end
        insert_idx = len(lines)

    # Create the Decision line
    decision_line = f"**Decision ({authority}, {today}):** {choice} — {note}"

    # Insert the line (handling trailing newlines correctly)
    lines.insert(insert_idx, decision_line)

    # Write back to file
    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"

    inbox_path.write_text(new_text, encoding="utf-8")


def discuss(cfg: ProjectConfig, decision_id: str) -> str:
    """Generate a claude CLI command to discuss a decision.

    Returns a command string with the resume prompt and inbox path.
    Raises DecisionError if the decision has no resume prompt.
    """
    inbox_path = cfg.root / cfg.decisions_inbox

    if not inbox_path.exists():
        raise DecisionError(f"Inbox file not found: {inbox_path}")

    text = inbox_path.read_text(encoding="utf-8")
    decisions = parse_inbox(text)

    # Find the decision
    decision = None
    for d in decisions:
        if d.id == decision_id:
            decision = d
            break

    if decision is None:
        raise DecisionError(f"Decision {decision_id} not found")

    if not decision.resume_prompt:
        raise DecisionError(f"Decision {decision_id} has no resume prompt")

    # Build the prompt content: resume prompt + entry heading + inbox path
    heading_pattern = re.compile(
        r"^(##\s+D-\d+\s+·\s+\S+\s+·\s+.+?\s+·\s+(OPEN|DISCUSSING|DECIDED|DROPPED)\b.*)$"
    )

    # Find the heading line from the original text
    lines = text.splitlines()
    heading_line = lines[decision.heading_line - 1] if decision.heading_line <= len(lines) else ""

    # Build the prompt
    prompt_parts = [
        decision.resume_prompt,
        heading_line,
        str(inbox_path),
    ]
    prompt_text = "\n".join(prompt_parts)

    # Shell-escape the prompt using shlex.quote
    quoted_prompt = shlex.quote(prompt_text)

    # Build the command
    cmd = f"claude --append-system-prompt {quoted_prompt}"

    return cmd


def open_decision(cfg: ProjectConfig, question: str, resume_prompt: str,
                  raised_by: str = "intake-agent") -> str:
    """Append a brand-new OPEN decision entry (the inverse of decide()).

    Allocates the next D-<NNN> id (max existing + 1, zero-padded 3 digits;
    D-001 if the inbox has none yet), appends a well-formed entry carrying
    **Question:**/**Resume prompt:** paragraphs, and returns the new id.
    Creates the inbox file (with a minimal header) if it does not exist yet.
    """
    inbox_path = cfg.root / cfg.decisions_inbox

    existing = []
    if inbox_path.exists():
        existing = parse_inbox(inbox_path.read_text(encoding="utf-8"))

    next_n = max((int(d.id[2:]) for d in existing if d.id[2:].isdigit()), default=0) + 1
    new_id = f"D-{next_n:03d}"
    today = utc_now().date().isoformat()

    entry = (
        f"## {new_id} · {today} · {raised_by} · OPEN\n\n"
        f"**Question:** {question}\n\n"
        f'**Resume prompt:** "{resume_prompt}"\n'
    )

    if inbox_path.exists():
        text = inbox_path.read_text(encoding="utf-8")
    else:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        text = "# decisions inbox — product calls awaiting the user (D-<NNN>).\n"

    text = text.rstrip("\n") + "\n\n" + entry
    inbox_path.write_text(text, encoding="utf-8")
    return new_id
