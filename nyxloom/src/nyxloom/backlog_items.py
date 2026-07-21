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
  None if un-headered — schema validation is skipped for those items),
  detail (str, PACKAGE P41: the item's prose, from just after its `**id —
  title.**` bold segment through any indented continuation lines, BUT
  cut off before the header-comment line for a headered item — body prose
  never lives after the header).
- is_briefed(item) -> bool: PACKAGE P41. True iff header_line is not None
  AND detail is non-empty. An un-headered legacy bullet is NEVER briefed,
  regardless of how much body prose it carries — only create()'s
  ALWAYS-headered items (or a hand-authored header) can carry a brief.
- brief_detail(cfg, item_id) -> str | None: PACKAGE P41. Looks up item_id
  in `<cfg.root>/nyxloom-trove/backlog.md`; returns its detail iff
  is_briefed(item), else None (including "no such item").
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
- create(path, title, detail, priority=None, decisions=None) -> str: PACKAGE
  P29 (feature-intake agent) addition -- appends a brand-new, ALWAYS-headered
  item (status=open) in the same bullet+header-comment shape as above,
  allocates the next B<N> id (max existing + 1, B1 if none yet), and returns
  it. `detail` is prose (embedded newlines are re-indented as bullet
  continuation lines, never a new `- **B<N>` line). Creates the file (with a
  minimal title line) if it does not exist yet. Never touches any other
  item's line.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from .config import ProjectConfig
from .log import get_logger
from .types import LintFinding

log = get_logger("backlog_items")

DEFAULT_RELPATH = "nyxloom-trove/backlog.md"

_ITEM_RE = re.compile(r"^-\s+\*\*(B\d+)\b")
_HEADER_RE = re.compile(r"^\s*<!--\s*nyxloom:backlog\s+(.*?)\s*-->\s*$")
_FIELD_RE = re.compile(r"(\w+)=(\S+)")
_TITLE_TRAILING_RE = re.compile(r"^-\s+\*\*.*?\*\*\s*(.*)$")


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
    detail: str = ""


def resolve_path(cfg: ProjectConfig) -> Path:
    return cfg.root / DEFAULT_RELPATH


def parse(path: Path) -> list[BacklogItem]:
    """Parse a backlog.md file into typed items. Missing file -> []."""
    if not path.exists():
        return []
    items = _parse_text(path.read_text(encoding="utf-8"))
    log.debug("backlog parsed", path=str(path), count=len(items))
    return items


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


def _extract_detail(item_lines: list[str], header_offset: int | None) -> str:
    """The item's prose: the bullet line's trailing text (after its bold
    `**id — title.**` segment) plus any continuation lines, stopping BEFORE
    the header-comment line (never after -- see module docstring)."""
    end = header_offset if header_offset is not None else len(item_lines)
    body_lines = item_lines[:end]
    if not body_lines:
        return ""
    m = _TITLE_TRAILING_RE.match(body_lines[0])
    parts = [m.group(1).strip() if m else ""]
    parts.extend(ln.strip() for ln in body_lines[1:])
    return "\n".join(p for p in parts if p)


def _build_item(start_line: int, item_lines: list[str]) -> BacklogItem:
    bullet_id = _ITEM_RE.match(item_lines[0]).group(1)

    header_line: int | None = None
    header_offset: int | None = None
    fields: dict[str, str] | None = None
    for offset, line in enumerate(item_lines[1:], start=1):
        m = _HEADER_RE.match(line)
        if m:
            header_line = start_line + offset
            header_offset = offset
            fields = dict(_FIELD_RE.findall(m.group(1)))
            break

    detail = _extract_detail(item_lines, header_offset)

    if fields is None:
        return BacklogItem(id=bullet_id, status="open", line=start_line, detail=detail)

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
        detail=detail,
    )


def _load_schema() -> dict:
    text = importlib.resources.files("nyxloom.schemas").joinpath(
        "backlog-item.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(text)


def is_briefed(item: BacklogItem) -> bool:
    """True iff `item` carries a P29 intake brief: header-comment present
    AND non-empty detail. Detail alone is NOT enough -- an un-headered
    legacy bullet's continuation prose is ordinary body text, never a
    brief, no matter how much of it there is."""
    return item.header_line is not None and bool(item.detail.strip())


def brief_detail(cfg: ProjectConfig, item_id: str) -> str | None:
    """The intake-brief detail for `item_id`, or None if no such item or it
    is not briefed (gates on is_briefed, not raw detail)."""
    items = parse(resolve_path(cfg))
    item = next((it for it in items if it.id == item_id), None)
    if item is None or not is_briefed(item):
        return None
    return item.detail


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

    log.debug("backlog validated", item_count=len(items), finding_count=len(findings))
    return findings


def _set_field(fields_str: str, key: str, value: str) -> str:
    """Set `key=value` among a header's tokens: rewrite the token in place
    when present, append it when absent. Every other token is untouched.
    Absent-means-append matters for `status`: an in-place-only rewrite would
    silently leave a linked-but-status-less header un-ticked."""
    pattern = rf"\b{re.escape(key)}=\S+"
    if re.search(pattern, fields_str):
        # lambda replacement: `value` is data, never a backreference template.
        return re.sub(pattern, lambda _: f"{key}={value}", fields_str, count=1)
    return f"{fields_str} {key}={value}"


def tick_merged(path: Path, task_id: str, commit: str) -> bool:
    """Typed-only auto-tick: set status=merged + merge_commit=<commit> on
    the item whose carved_handoff == task_id. No-op (no write) if none
    match. Edits ONLY that item's header-comment line."""
    if not path.exists():
        return False

    # newline="" disables universal-newline translation on the way in and out;
    # with keepends below, every line the tick does not target survives
    # byte-for-byte, so a CRLF file is not silently reflowed to LF.
    with path.open("r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    items = _parse_text(text)
    target = next((it for it in items if it.carved_handoff == task_id), None)
    if target is None or target.header_line is None:
        log.debug("backlog tick: no linked item", task_id=task_id)
        return False

    lines = text.splitlines(keepends=True)
    idx = target.header_line - 1
    raw = lines[idx]
    m = _HEADER_RE.match(raw)
    if not m:
        return False

    fields_str = m.group(1)
    fields_str = _set_field(fields_str, "status", "merged")
    fields_str = _set_field(fields_str, "merge_commit", commit)

    stripped = raw.rstrip("\r\n")
    ending = raw[len(stripped):]
    indent = stripped[:len(stripped) - len(stripped.lstrip())]
    lines[idx] = f"{indent}<!-- nyxloom:backlog {fields_str} -->{ending}"

    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    log.info("backlog item ticked merged", id=target.id, task_id=task_id, commit=commit)
    return True


def create(path: Path, title: str, detail: str, *, priority: int | None = None,
           decisions: list[str] | None = None) -> str:
    """Append a brand-new, headered item (status=open); return its B<N> id.

    Allocates the next id (max existing B<N> + 1, B1 if the file is empty
    or missing). `detail` prose is re-indented onto continuation lines so it
    can never itself be mistaken for a new item's bullet line."""
    items = parse(path) if path.exists() else []
    existing_ns = [int(it.id[1:]) for it in items if it.id[1:].isdigit()]
    new_id = f"B{max(existing_ns, default=0) + 1}"

    tokens = [f"id={new_id}", "status=open"]
    if priority is not None:
        tokens.append(f"priority={priority}")
    if decisions:
        tokens.append(f"decisions={','.join(decisions)}")

    detail_indented = detail.strip().replace("\n", "\n  ")
    block = (
        f"- **{new_id} — {title}.** {detail_indented}\n"
        f"  <!-- nyxloom:backlog {' '.join(tokens)} -->\n"
    )

    text = path.read_text(encoding="utf-8") if path.exists() else "# backlog\n"
    text = text.rstrip("\n") + "\n\n" + block

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("backlog item created", id=new_id, title=title)
    return new_id
