"""backlog.md light schema + typed auto-tick on merge. PACKAGE P28.

nyxloom-trove/backlog.md stays the durable, human-owned record (files-first,
same doctrine as decisions.py): items are free prose bullets
(`- **B<N> — title.** body...`). This module adds an OPTIONAL machine-readable
header per item — an HTML-comment line immediately following the item's
bullet line:

    - **B9 — feature-intake exploration agent....** body prose...
      <!-- nyxloom:backlog id=B9 status=open priority=3 carved_handoff=P29-x decisions=D-001,D-002 merge_commit=abc1234 -->

Un-headered items (today's entire backlog.md) parse as status=open with no
links and are NOT schema-checked — no lossy migration required. tick_merged()
is the only write path and edits SOLELY the header line's typed tokens
(status, merge_commit); it never rewrites prose or touches sibling items,
mirroring decisions.py's in-place, typed-fields-only discipline.

INTERFACE CONTRACT (frozen):

- BacklogItem dataclass: id, status, line (1-based, the bullet's start
  line), header_line (1-based header-comment line, None if un-headered),
  priority, carved_handoff, decisions (list[str]), merge_commit, raw_header
  (schema-ready dict built from the header tokens, coerced to schema types;
  None if un-headered — schema validation is skipped for those items).
- parse(path) -> list[BacklogItem]; missing file -> [].
- validate(items, path='') -> list[LintFinding], rule namespace BLG1: schema
  violation on a PRESENT header (missing id, bad status, non-int priority,
  ...) against schemas/backlog-item.schema.json.
- resolve_path(cfg) -> Path: `<cfg.root>/nyxloom-trove/backlog.md` (the
  fixed trove convention; config.py is out of scope for this package so this
  is not sourced from ProjectConfig).
- tick_merged(path, task_id, commit) -> bool: find the item whose
  carved_handoff == task_id (only a headered item can carry that link);
  on match, surgically rewrite ONLY that item's header-comment line, setting
  status=merged and merge_commit=<commit> (every other token, and every
  other line in the file, is left byte-identical), and return True. No
  match (including an un-headered/unlinked backlog, or a missing file) ->
  no write, return False.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from .config import ProjectConfig
from .types import LintFinding

DEFAULT_RELPATH = "nyxloom-trove/backlog.md"

_ITEM_RE = re.compile(r"^-\s+\*\*(B\d+)\b")
_HEADER_RE = re.compile(r"^\s*<!--\s*nyxloom:backlog\s+(.*?)\s*-->\s*$")
_FIELD_RE = re.compile(r"(\w+)=(\S+)")


@dataclass
class BacklogItem:
    id: str
    status: str
    line: int
    header_line: int | None = None
    priority: int | None = None
    carved_handoff: str | None = None
    decisions: list[str] = field(default_factory=list)
    merge_commit: str | None = None
    raw_header: dict | None = None


def resolve_path(cfg: ProjectConfig) -> Path:
    return cfg.root / DEFAULT_RELPATH


def parse(path: Path) -> list[BacklogItem]:
    """Parse a backlog.md file into typed items. Missing file -> []."""
    if not path.exists():
        return []
    return _parse_text(path.read_text(encoding="utf-8"))


def _parse_text(text: str) -> list[BacklogItem]:
    lines = text.splitlines()
    items: list[BacklogItem] = []
    start: int | None = None

    def flush(end: int) -> None:
        if start is not None:
            items.append(_build_item(start + 1, lines[start:end]))

    for i, line in enumerate(lines):
        if _ITEM_RE.match(line):
            flush(i)
            start = i
    flush(len(lines))

    return items


def _build_item(start_line: int, item_lines: list[str]) -> BacklogItem:
    bullet_id = _ITEM_RE.match(item_lines[0]).group(1)

    header_line: int | None = None
    fields: dict[str, str] | None = None
    for offset, line in enumerate(item_lines[1:], start=1):
        m = _HEADER_RE.match(line)
        if m:
            header_line = start_line + offset
            fields = dict(_FIELD_RE.findall(m.group(1)))
            break

    if fields is None:
        return BacklogItem(id=bullet_id, status="open", line=start_line)

    priority: int | None = None
    if "priority" in fields:
        try:
            priority = int(fields["priority"])
        except ValueError:
            priority = None

    decisions = [d for d in fields.get("decisions", "").split(",") if d]

    schema_doc: dict = dict(fields)
    if "priority" in schema_doc:
        try:
            schema_doc["priority"] = int(schema_doc["priority"])
        except ValueError:
            pass  # left as a string; the schema's integer check flags it
    if "decisions" in schema_doc:
        schema_doc["decisions"] = decisions

    return BacklogItem(
        id=fields.get("id", bullet_id),
        status=fields.get("status", "open"),
        line=start_line,
        header_line=header_line,
        priority=priority,
        carved_handoff=fields.get("carved_handoff"),
        decisions=decisions,
        merge_commit=fields.get("merge_commit"),
        raw_header=schema_doc,
    )


def _load_schema() -> dict:
    text = importlib.resources.files("nyxloom.schemas").joinpath(
        "backlog-item.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(text)


def validate(items: list[BacklogItem], path: str = "") -> list[LintFinding]:
    """Schema findings (rule BLG1) for every headered item. Un-headered
    (legacy, schema not applied) items are skipped."""
    findings: list[LintFinding] = []
    validator = jsonschema.Draft202012Validator(_load_schema())

    for item in items:
        if item.raw_header is None:
            continue
        for error in sorted(validator.iter_errors(item.raw_header),
                             key=lambda e: list(e.absolute_path)):
            json_path = ".".join(str(p) for p in error.absolute_path) or "$"
            findings.append(LintFinding(
                rule="BLG1",
                severity="error",
                message=f"{item.id} {json_path}: {error.message}",
                path=path,
                line=item.header_line,
            ))

    return findings


def tick_merged(path: Path, task_id: str, commit: str) -> bool:
    """Typed-only auto-tick: set status=merged + merge_commit=<commit> on
    the item whose carved_handoff == task_id. No-op (no write) if none
    match. Edits ONLY that item's header-comment line."""
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    items = _parse_text(text)
    target = next((it for it in items if it.carved_handoff == task_id), None)
    if target is None or target.header_line is None:
        return False

    lines = text.splitlines()
    idx = target.header_line - 1
    m = _HEADER_RE.match(lines[idx])
    if not m:
        return False

    fields_str = m.group(1)
    fields_str = re.sub(r"\bstatus=\S+", "status=merged", fields_str)
    if re.search(r"\bmerge_commit=\S+", fields_str):
        fields_str = re.sub(r"\bmerge_commit=\S+", f"merge_commit={commit}", fields_str)
    else:
        fields_str = f"{fields_str} merge_commit={commit}"

    indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
    lines[idx] = f"{indent}<!-- nyxloom:backlog {fields_str} -->"

    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    return True
