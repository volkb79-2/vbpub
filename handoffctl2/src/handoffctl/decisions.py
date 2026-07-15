"""DECISIONS-INBOX.md integration (ARCHITECTURE §8). PACKAGE P07.

The md file stays the durable, human-owned record (files-first); this module
parses it, reconciles status changes into events, and provides the CLI verbs.
It NEVER rewrites entry prose — decide() only appends a decision record
block and flips the status token on the entry's heading line.

ENTRY FORMAT (existing convention, dstdns/groop):
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
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ProjectConfig
from .types import TaskStateFile


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
    raise NotImplementedError


def open_ids(cfg: ProjectConfig) -> set[str]:
    raise NotImplementedError


def reconcile_decisions(cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        seen: dict[str, str]) -> list[tuple[str, str]]:
    raise NotImplementedError


def decide(cfg: ProjectConfig, decision_id: str, choice: str, note: str,
           authority: str) -> None:
    raise NotImplementedError


def discuss(cfg: ProjectConfig, decision_id: str) -> str:
    raise NotImplementedError
