"""E-012 (design-context-lifecycle-experiments.md): a structured, mechanically-
extracted ledger of files touched, commits created, branches involved, and
test-run results -- aggregated PER PROMPT BOUNDARY (an OPERATOR_TEXT/QA_PAIR/
LIFECYCLE_MARKER event that actually survived `select()`), not per tool call
(2026-09-10, operator framing: "the file touch ledger seems like an optional
thing to include, but only in aggregated form between major events").

Every fact here is a raw JSON field read or a regex over already-present raw
text -- zero LLM calls, the same mechanical-core guarantee the rest of this
package holds (config.py's own module docstring). This is a real-data-driven
answer to E-012's own inventory of what's currently stranded in tool_use/
tool_result records this package otherwise drops entirely:

- **Files read/edited** -- every `Edit`/`Write`/`NotebookEdit`/`Read` tool_use
  carries a clean `file_path` field, nearly free to collect (E-012 item 2,
  confirmed 139 Edit + 9 Write + 123 Read calls in the real dstdns replay,
  every one trivially path-bearing).
- **Commits created** -- a `Bash` tool_use whose `command` matches `git
  commit` is tracked by its own `tool_use.id`; the real commit hash is only
  known once the matching `tool_result` comes back (git's own `[branch
  abc1234] message` first line), so this is a two-step correlation, not a
  single-record read.
- **Branches involved** -- `git checkout -b <name>` already carries the
  branch name in the COMMAND itself, no result correlation needed.
- **Test runs** -- a narrow regex over `tool_result` text for a
  `"N passed[, M failed]"`/`"M failed"`-shaped string (2026-09-10, operator's
  own framing: "do we want to copy the whole argv?" reads as its own answer
  -- this keeps the RESULT, never the invocation command).

Claude Code only, for now -- whether Codex/opencode expose tool_use/
tool_result the same way is the same open question E-012 raised for prose,
not yet checked for tool calls either.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_FILE_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
_READ_TOOLS = {"Read"}

# git's own commit-summary first line: "[branch-name abc1234] message" (or
# "[branch-name (root-commit) abc1234] message" for a repo's first commit).
_COMMIT_RE = re.compile(r"\[\S+(?:\s+\([^)]*\))?\s+([0-9a-f]{7,40})\]")
_BRANCH_CHECKOUT_RE = re.compile(r"git\s+checkout\s+-b\s+(\S+)")
_TEST_RESULT_RE = re.compile(r"\b\d+\s+passed\b(?:,\s*\d+\s+failed)?|\b\d+\s+failed\b")

# The unbounded synthetic key for tool activity before the first real
# boundary this session ever sees (rare -- most sessions open with an
# operator turn -- but a resumed/continued session can start mid-stream).
UNBOUNDED = "__unbounded__"


@dataclass
class Ledger:
    files_read: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.files_read or self.files_edited or self.commits or self.branches or self.tests)

    def render(self) -> str:
        parts = []
        if self.files_read:
            parts.append(f"[files read: {', '.join(self.files_read)}]")
        if self.files_edited:
            parts.append(f"[files edited: {', '.join(self.files_edited)}]")
        if self.commits:
            parts.append(f"[commits created: {', '.join(self.commits)}]")
        if self.branches:
            parts.append(f"[branches involved: {', '.join(self.branches)}]")
        if self.tests:
            parts.append(f"[tests: {'; '.join(self.tests)}]")
        return " ".join(parts)


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def build_ledger_claude_code(path: Path, boundary_markers: set[str]) -> dict[str, Ledger]:
    """One `Ledger` per marker in `boundary_markers` (pass the `.marker` of
    every OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER event that survived
    `select()` -- render.py's caller already has this list). Raw tool
    activity accumulates onto whichever boundary marker most recently
    preceded it in raw file order -- exactly `stats.py`'s `build_blocks`
    grouping, but over tool_use/tool_result content instead of usage, and
    keyed to the SURVIVING boundary set rather than every raw one, so
    activity following a boundary `select()` dropped folds into whichever
    kept boundary is still "current" -- consistent with how `select.py`'s
    own `gap_after` already treats intervening dropped content as belonging
    to the surviving span before it.
    """
    ledgers: dict[str, Ledger] = {UNBOUNDED: Ledger()}
    current = UNBOUNDED
    # tool_use_id -> the boundary marker owning it, for a Bash call whose
    # OWN command looked like a commit (the hash only appears in the RESULT).
    pending_commits: dict[str, str] = {}

    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            uuid = rec.get("uuid")
            if uuid and uuid in boundary_markers:
                current = uuid
                ledgers.setdefault(current, Ledger())
                continue

            rtype = rec.get("type")

            if rtype == "assistant":
                content = rec.get("message", {}).get("content", []) or []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    tinput = block.get("input") or {}
                    if name in _FILE_EDIT_TOOLS or name in _READ_TOOLS:
                        fp = tinput.get("file_path")
                        if fp:
                            bucket = ledgers[current].files_read if name in _READ_TOOLS \
                                else ledgers[current].files_edited
                            bucket.append(fp)
                    elif name == "Bash":
                        command = tinput.get("command", "")
                        if re.search(r"git\s+commit\b", command):
                            tool_id = block.get("id")
                            if tool_id:
                                pending_commits[tool_id] = current
                        m = _BRANCH_CHECKOUT_RE.search(command)
                        if m:
                            ledgers[current].branches.append(m.group(1))
                continue

            if rtype == "user":
                content = rec.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    result_text = block.get("content")
                    if not isinstance(result_text, str):
                        result_text = json.dumps(result_text)

                    tool_use_id = block.get("tool_use_id")
                    owner = pending_commits.pop(tool_use_id, None) if tool_use_id else None
                    if owner is not None:
                        m = _COMMIT_RE.search(result_text)
                        if m:
                            ledgers[owner].commits.append(m.group(1))

                    m = _TEST_RESULT_RE.search(result_text)
                    if m:
                        ledgers[current].tests.append(m.group(0))

    for ledger in ledgers.values():
        ledger.files_read = _dedup_preserve_order(ledger.files_read)
        ledger.files_edited = _dedup_preserve_order(ledger.files_edited)
        ledger.commits = _dedup_preserve_order(ledger.commits)
        ledger.branches = _dedup_preserve_order(ledger.branches)

    return ledgers


def build_ledger(path: Path, fmt: str, boundary_markers: set[str]) -> dict[str, Ledger]:
    """Dispatches on `fmt` -- see module docstring for what's implemented."""
    if fmt == "claude-code":
        return build_ledger_claude_code(Path(path), boundary_markers)
    raise NotImplementedError(
        f"the E-012 ledger does not support {fmt!r} yet -- see ledger.py's module docstring"
    )
