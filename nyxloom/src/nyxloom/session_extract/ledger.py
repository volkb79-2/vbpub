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

WHOLE-SESSION LEDGER + EXTERNAL EFFECTS (nyxloom-SUCCESSOR, 2026-10-06,
operator decision): the per-boundary lines above only print after a KEPT
operator boundary, so a single-brief agent (one OPERATOR turn, hundreds of
tool calls) showed little or nothing useful. `session_ledger()` merges every
boundary's ledger into one whole-session `Ledger`, and the new
`external_effects` bucket lists Bash commands that change the world outside
the worktree (git push/merge/tag/rebase, ssh, mutating curl, netcup
snapshot/install verbs, docker rm/stop/run, systemctl, apt). It is the
"already done -- verify by state, never repeat" list a successor needs. The
patterns are heuristic regexes over each shell SEGMENT (heredoc bodies
dropped; `;`, `&&`, `||`, `|` and newlines split; leading `VAR=x`, sudo,
nice/ionice/timeout/time wrappers stripped) and are configurable
(`--effect-pattern`, `--no-default-effect-patterns`). A command whose result
failed or was rejected by the harness is annotated, because a rejected push
did NOT happen.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import toolresult

# Default external-effect patterns, matched (re.search, case-insensitive)
# against each normalised shell segment. See the module docstring.
DEFAULT_EFFECT_PATTERNS: tuple[str, ...] = (
    r"^git(?:\s+-C\s+\S+)?\s+(?:push|merge|rebase)\b",
    r"^git(?:\s+-C\s+\S+)?\s+tag\b(?!.*\s(?:-l|--list)\b)",
    r"^(?:ssh|scp)(?:\s|$)",
    r"^curl\b.*\s(?:-X|--request)\s*=?\s*(?:POST|PUT|PATCH|DELETE)\b",
    r"^curl\b.*\s(?:-d|--data\S*|-F|--form\S*|-T|--upload-file)\b",
    r"\bnc\.py\b.*\bsnapshots?\b.*\b(?:create|delete|revert|rollback)\b",
    r"\bnc\.py\b.*\binstall-host\b",
    r"\bnc\.py\b.*\bscp-api\b.*\b(?:create|delete|install|reinstall|start|stop|shutdown|poweron|poweroff|reset|rescue)\b",
    r"^docker\s+(?:rm|stop|run)\b",
    r"^systemctl\b",
    r"^(?:apt|apt-get)\b",
)

_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.DOTALL)
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
_LEADING_KEYWORD_RE = re.compile(r"^(?:do|then|else|elif|if|while|!|\(|\{)\s+")
_LEADING_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
_WRAPPER_RES = (
    re.compile(r"^sudo(?:\s+-\S+)*\s+"),
    re.compile(r"^nice(?:\s+-n\s*\d+|\s+-\d+)?\s+"),
    re.compile(r"^ionice(?:\s+-c\s*\d+)?(?:\s+-n\s*\d+)?\s+"),
    re.compile(r"^timeout(?:\s+-\S+)*\s+\d+[smhd]?\s+"),
    re.compile(r"^time\s+"),
)


def _segments(command: str) -> list[str]:
    command = _HEREDOC_RE.sub("", command)
    out = []
    for seg in _SEGMENT_SPLIT_RE.split(command):
        seg = seg.strip()
        changed = True
        while changed and seg:
            changed = False
            for rx in (_LEADING_KEYWORD_RE, _LEADING_ASSIGN_RE, *_WRAPPER_RES):
                new = rx.sub("", seg, count=1)
                if new != seg:
                    seg, changed = new.strip(), True
        if seg:
            out.append(seg)
    return out


def effect_segments(command: str, patterns: Iterable[str] = DEFAULT_EFFECT_PATTERNS) -> list[str]:
    """The shell segments of `command` that match an external-effect
    pattern (empty = not an external effect)."""
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    return [seg for seg in _segments(command) if any(rx.search(seg) for rx in compiled)]


def external_effect(command: str, patterns: Iterable[str] = DEFAULT_EFFECT_PATTERNS) -> bool:
    """Whether any shell segment of `command` matches an external-effect
    pattern."""
    return bool(effect_segments(command, patterns))

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
    # "[HH:MM:SS] <command>" lines, chronological, NOT de-duplicated (a
    # repeated push is information). Rendered only by render_session().
    external_effects: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Empty for the PER-BOUNDARY line (external effects are a
        whole-session notion and never part of it)."""
        return not (self.files_read or self.files_edited or self.commits or self.branches or self.tests)

    def session_is_empty(self) -> bool:
        return self.is_empty() and not self.external_effects

    def render_session(self) -> str:
        """The whole-session ledger as a short multi-line block."""
        lines = ["[session ledger -- whole session]"]
        if self.files_read:
            lines.append(f"files read ({len(self.files_read)}): {', '.join(self.files_read)}")
        if self.files_edited:
            lines.append(f"files edited ({len(self.files_edited)}): {', '.join(self.files_edited)}")
        if self.commits:
            lines.append(f"commits created: {', '.join(self.commits)}")
        if self.branches:
            lines.append(f"branches involved: {', '.join(self.branches)}")
        if self.tests:
            lines.append(f"tests: {'; '.join(self.tests)}")
        if self.external_effects:
            lines.append(
                f"external effects ({len(self.external_effects)}) -- already done; verify by "
                "state, never repeat:"
            )
            lines.extend(f"  {e}" for e in self.external_effects)
        if self.session_is_empty():
            lines.append("(no files, commits, branches, tests or external effects recorded)")
        elif not self.external_effects:
            lines.append("external effects: none detected")
        return "\n".join(lines)

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


def _relativize(fp: str, repo_root: Path) -> str:
    """Best-effort: an absolute `file_path` becomes relative to `repo_root`
    (via `os.path.relpath`, which -- unlike `Path.relative_to` -- never
    raises for a path outside the root; it just grows a `../` prefix, which
    is still a meaningfully shorter and more portable rendering than the raw
    absolute path). A non-absolute `fp` is returned unchanged.
    """
    if not os.path.isabs(fp):
        return fp
    return os.path.relpath(fp, repo_root)


def session_ledger(ledgers: dict[str, Ledger]) -> Ledger:
    """Merge every boundary's ledger into one whole-session ledger, in
    boundary (= chronological) order. Files/commits/branches are
    de-duplicated; tests and external effects keep every occurrence."""
    merged = Ledger()
    for entry in ledgers.values():
        merged.files_read.extend(entry.files_read)
        merged.files_edited.extend(entry.files_edited)
        merged.commits.extend(entry.commits)
        merged.branches.extend(entry.branches)
        merged.tests.extend(entry.tests)
        merged.external_effects.extend(entry.external_effects)
    merged.files_read = _dedup_preserve_order(merged.files_read)
    merged.files_edited = _dedup_preserve_order(merged.files_edited)
    merged.commits = _dedup_preserve_order(merged.commits)
    merged.branches = _dedup_preserve_order(merged.branches)
    return merged


def _hms(ts: str) -> str:
    return f"[{ts[11:19]}]" if len(ts) >= 19 else "[--:--:--]"


def build_ledger_claude_code(
    path: Path, boundary_markers: set[str], repo_root: Path | None = None,
    effect_patterns: Iterable[str] = DEFAULT_EFFECT_PATTERNS,
) -> dict[str, Ledger]:
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

    `files_read`/`files_edited` entries are rendered relative to `repo_root`
    (default: the process's own CWD, i.e. run this from within the repo the
    session worked in) -- `tool_use.input.file_path` is always absolute at
    the source, and the raw absolute form is a wall of repeated `/workspaces/
    <repo>/...` noise across every entry with nothing repo-relative paths
    don't already say just as precisely.
    """
    root = repo_root if repo_root is not None else Path.cwd()
    ledgers: dict[str, Ledger] = {UNBOUNDED: Ledger()}
    current = UNBOUNDED
    # tool_use_id -> the boundary marker owning it, for a Bash call whose
    # OWN command looked like a commit (the hash only appears in the RESULT).
    pending_commits: dict[str, str] = {}
    # tool_use_id -> (the ledger holding the effect line, its index), so the
    # result can annotate a failed/rejected command.
    pending_effects: dict[str, tuple[Ledger, int]] = {}
    effect_patterns = tuple(effect_patterns)

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
                            bucket.append(_relativize(fp, root))
                    elif name == "Bash":
                        command = tinput.get("command", "")
                        if re.search(r"git\s+commit\b", command):
                            tool_id = block.get("id")
                            if tool_id:
                                pending_commits[tool_id] = current
                        m = _BRANCH_CHECKOUT_RE.search(command)
                        if m:
                            ledgers[current].branches.append(m.group(1))
                        hits = effect_segments(command, effect_patterns) if isinstance(command, str) else []
                        if hits:
                            effects = ledgers[current].external_effects
                            effects.append(
                                f"{_hms(rec.get('timestamp', ''))} "
                                f"{toolresult.one_line(' ; '.join(hits), 200)}"
                            )
                            if block.get("id"):
                                pending_effects[block["id"]] = (ledgers[current], len(effects) - 1)
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
                    pending = pending_effects.pop(tool_use_id, None) if tool_use_id else None
                    if pending is not None:
                        eff_ledger, eff_idx = pending
                        if toolresult.is_denial(result_text, rec):
                            eff_ledger.external_effects[eff_idx] += " [REJECTED by harness: not executed]"
                        elif toolresult.is_failed(block, rec):
                            eff_ledger.external_effects[eff_idx] += " [FAILED]"
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


def build_ledger(
    path: Path, fmt: str, boundary_markers: set[str], repo_root: Path | None = None,
    effect_patterns: Iterable[str] = DEFAULT_EFFECT_PATTERNS,
) -> dict[str, Ledger]:
    """Dispatches on `fmt` -- see module docstring for what's implemented."""
    if fmt == "claude-code":
        return build_ledger_claude_code(Path(path), boundary_markers, repo_root, effect_patterns)
    raise NotImplementedError(
        f"the E-012 ledger does not support {fmt!r} yet -- see ledger.py's module docstring"
    )
