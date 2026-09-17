"""Session/agent-family discovery -- `nyxloom extract-sessions`.

Answers a different question than everything else in this package: not
"what's in this session" but "what sessions/sub-agents EXIST, and how do
they relate" -- the thing you need before you can point `extract` /
`extract-lossless` / `extract-report` at a specific one. Identified as a
real, adapter-generic gap while redesigning Claude Code sub-agent
targeting (docs/design-context-lifecycle-experiments.md E-015): all three
CLIs support some form of parent/child session structure, but nothing
listed it without already knowing a child id.

Each discovery-capable adapter's own `list_agents(path)` returns the real,
format-specific answer (see adapters/claude_code.py, adapters/codex.py,
adapters/opencode.py for how each CLI actually stores this -- verified against
real local data, not guessed); Reasonix is intentionally extraction-only here
because its supplied chat-file shape does not establish parent/child lineage.
This module only defines the shared SessionNode shape and
renders it. `path` for Claude Code or Codex may be a top-level session
file, a specific sub-agent's own file (either way, the whole family it
belongs to is shown, rooted at the top-level session), OR a DIRECTORY
holding many top-level sessions -- a Claude Code project directory
(~/.claude/projects/<project>/) or a Codex sessions root
(the Codex ``$CODEX_HOME/sessions/`` root, default ``~/.codex/sessions/``) -- in which case every family found is combined into
one forest (see `_list_agents_in_directory`, added 2026-09-11 after an
operator hit exactly this: the natural "what's in this project" invocation
failing outright). For opencode, `path` is always the whole SQLite store
(a file or its containing directory), and every root session (plus its
descendants) is shown -- there is no single-file scoping concept there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionNode:
    """One session or sub-agent, generic across adapters.

    `id`/`parent_id` are each adapter's own native identifier (a file path
    string for Claude Code, a thread id for Codex, a `session.id` row value
    for opencode) -- never re-derived or normalized across adapters, since
    "the id you'd pass to --opencode-session or as path" is the whole point.
    `parent_id` is None for a root/top-level session. `path` is what to
    actually hand to `extract`/`extract-lossless`/`extract-report` to
    target this exact node (== `id` for Claude Code/Codex, since a file IS
    the target there; the store path for opencode, paired with `id` as
    --opencode-session).
    """

    id: str
    parent_id: str | None
    depth: int
    label: str
    path: str
    record_count: int | None = None
    first_ts: str | None = None
    last_ts: str | None = None


def list_agents(path: Path, fmt: str | None = None) -> list[SessionNode]:
    """path may be a single session-log FILE (the normal case, dispatched
    to the detected/forced adapter's own list_agents -- see each adapter's
    module docstring) OR a DIRECTORY holding MANY top-level sessions --
    added 2026-09-11 after a real operator hit exactly this: pointing
    extract-sessions at a Claude Code PROJECT directory
    (~/.claude/projects/<project>/, which holds one top-level *.jsonl per
    session) failed with "could not detect a session-log format" (an
    error about ONE file's content, on a path that isn't one). That's the
    single most natural "what exists in this project" invocation for the
    whole discovery command's own premise, so it needs to work.

    opencode's SQLite store already accepts a directory (`_db_path()`
    looks for an `opencode.db` inside it) -- that case is dispatched
    exactly as before. Any OTHER directory is treated as a project/
    sessions root: every top-level Claude Code *.jsonl file found directly
    in it, or (if none) every Codex rollout-*.jsonl found anywhere under
    it, contributes its own family to one combined forest.
    """
    from .adapters import detect, get_adapter

    if path.is_dir():
        from .adapters import opencode as opencode_adapter

        if fmt in (None, "opencode") and opencode_adapter._db_path(path) is not None:
            return opencode_adapter.list_agents(path)
        if fmt in (None, "claude-code", "codex"):
            return _list_agents_in_directory(path, fmt)
        raise ValueError(f"extract-sessions does not support {fmt!r} for a directory")

    adapter = get_adapter(fmt) if fmt else detect(path)
    if not hasattr(adapter, "list_agents"):
        raise ValueError(f"extract-sessions does not support {adapter.name!r} yet")
    return adapter.list_agents(path)


def _list_agents_in_directory(path: Path, fmt: str | None) -> list[SessionNode]:
    from .adapters import claude_code, codex

    nodes: list[SessionNode] = []

    if fmt in (None, "claude-code"):
        for f in sorted(path.glob("*.jsonl")):
            if claude_code.sniff(f):
                nodes.extend(claude_code.list_agents(f))

    if not nodes and fmt in (None, "codex"):
        # A Codex sessions root ($CODEX_HOME/sessions/, default ~/.codex/sessions/)
        # nests rollout files
        # under YYYY/MM/DD/ -- list_agents() per file already walks every
        # sibling sharing that file's own root session_id, so calling it
        # once per already-seen root (not once per file) avoids re-
        # deriving (and re-printing) the same family N times.
        seen_roots: set[str] = set()
        for f in sorted(path.glob("**/rollout-*.jsonl")):
            if not codex.sniff(f):
                continue
            meta = codex._first_session_meta(f)
            root_id = meta.get("session_id") if meta else None
            if root_id is None or root_id in seen_roots:
                continue
            seen_roots.add(root_id)
            nodes.extend(codex.list_agents(f))

    if not nodes:
        hint = _one_level_down_hint(path)
        raise ValueError(
            f"{path} is a directory, but no Claude Code (*.jsonl directly in it) or "
            f"Codex (rollout-*.jsonl anywhere under it) sessions were found"
            + (f" -- did you mean one of its subdirectories, e.g. {hint}?" if hint else
               " -- pass a specific session-log file instead, or --format to force a reading")
        )
    return nodes


def _one_level_down_hint(path: Path) -> str | None:
    """path itself had no sessions, but this is the single most likely real
    mistake: pointing extract-sessions at the PARENT of every project
    (~/.claude/projects itself) instead of one project's own directory
    (~/.claude/projects/<project>/) -- operator-reported real repro
    (2026-09-11). Cheap, one level deep only -- not a recursive search."""
    try:
        children = sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return None
    matches = [c.name for c in children if any(c.glob("*.jsonl"))]
    if not matches:
        return None
    shown = ", ".join(repr(str(path / m)) for m in matches[:3])
    more = f" (+{len(matches) - 3} more)" if len(matches) > 3 else ""
    return shown + more


def render_tree(nodes: list[SessionNode]) -> str:
    """Indented tree, one line per node, roots first (each root's family
    grouped together, roots ordered by first_ts/id for stable output),
    children depth-first under their parent. A node whose parent_id isn't
    in the node set (shouldn't happen, but a real adapter bug or a
    mid-query deletion could produce one) is treated as its own root
    rather than silently dropped -- discovery output hiding a real node
    would defeat the whole point of this command.
    """
    by_parent: dict[str | None, list[SessionNode]] = {}
    by_id = {n.id: n for n in nodes}
    for n in nodes:
        parent = n.parent_id if (n.parent_id is not None and n.parent_id in by_id) else None
        by_parent.setdefault(parent, []).append(n)
    for children in by_parent.values():
        children.sort(key=lambda n: (n.first_ts or "", n.id))

    lines: list[str] = []

    def _walk(node: SessionNode, level: int) -> None:
        # Indent by ACTUAL tree position (this recursion's level), not
        # node.depth -- node.depth is the adapter's own self-reported value
        # (e.g. Claude Code's spawnDepth), which stays correct even when a
        # parent EDGE couldn't be resolved and the node ends up rendered as
        # its own root; using node.depth for indentation there would draw
        # it indented while positioned among top-level roots, a visually
        # broken tree. node.depth is still shown in the label when it
        # matters (each adapter's own list_agents() embeds it there).
        indent = "  " * level
        bits = [f"{indent}- {node.label}  [{node.id}]"]
        extra = []
        if node.record_count is not None:
            extra.append(f"{node.record_count} records")
        if node.first_ts:
            extra.append(f"first {node.first_ts}")
        if node.last_ts:
            extra.append(f"last {node.last_ts}")
        if extra:
            bits.append(f"{indent}  ({', '.join(extra)})")
        lines.append("\n".join(bits))
        for child in by_parent.get(node.id, []):
            _walk(child, level + 1)

    for root in by_parent.get(None, []):
        _walk(root, 0)

    if not lines:
        return "(no sessions found)\n"
    return "\n".join(lines) + "\n"
