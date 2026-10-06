"""Post-selection collapse of tool-call events (`--edit-calls collapse`,
`--read-calls collapse`). Operates on the KEPT event list, so it works for
every output mode and never changes selection.

The Claude Code adapter tags each TOOL_CALL event it emits (meta `tool_kind`
edit|read|effect|other, `tool_file`, `tool_intent`); this module merges a
CONSECUTIVE run into one line:

  - edits to the same file -> `[edited F x3: intent a; intent b]` (the
    distinct intents, if any, are kept: that is what the Edit/Write
    `Intent:` convention is for),
  - read-only tools / read-only Bash -> `[oriented: N reads]`.

Any other event (assistant text, an error line, an operator turn, an effect
call) ends the run. The merged event keeps the FIRST event's timestamp and
marker and the LAST event's `gap_after`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from . import toolresult
from .events import EventKind, NormalizedEvent

_INTENT_LIMIT = 200

_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)+)"')
_SCRATCH_RE = re.compile(r"/tmp/claude-\d+/[^/\s\"'\\]+/[^/\s\"'\\]+/scratchpad")
_ALIAS_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORKTREES = "/.worktrees/"


def detect_aliases(path: Path) -> dict[str, str]:
    """`--path-aliases auto`: from the transcript text itself.
    `REPO` = the part of the first `cwd` before `/.worktrees/` (else the
    `cwd`); `WT` = the worktree root when the `cwd` is inside one;
    `SCRATCH` = the most frequent `/tmp/claude-<uid>/<proj>/<session>/
    scratchpad` path named anywhere in the file."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    found: dict[str, str] = {}
    m = _CWD_RE.search(text)
    if m is not None:
        cwd = m.group(1)
        idx = cwd.find(_WORKTREES)
        if idx >= 0:
            found["REPO"] = cwd[:idx]
            found["WT"] = cwd[: idx + len(_WORKTREES)] + cwd[idx + len(_WORKTREES):].split("/")[0]
        else:
            found["REPO"] = cwd
    scratch = Counter(_SCRATCH_RE.findall(text)).most_common(1)
    if scratch:
        found["SCRATCH"] = scratch[0][0]
    return found


def resolve_aliases(spec: str | None, path: Path) -> tuple[tuple[str, str], ...]:
    """`--path-aliases SPEC`: comma-separated `auto`, `none`, `NAME=/path`.
    Later tokens override earlier ones (`auto,WT=/other`). Raises ValueError
    on a malformed token."""
    if not spec:
        return ()
    found: dict[str, str] = {}
    for token in (t.strip() for t in spec.split(",")):
        if token == "auto":
            found.update(detect_aliases(path))
        elif token == "none":
            found.clear()
        elif "=" in token:
            name, _, value = token.partition("=")
            name = name.lstrip("$")
            if not _ALIAS_NAME_RE.fullmatch(name) or not value:
                raise ValueError(f"--path-aliases: bad NAME=/path entry {token!r}")
            found[name] = value
        else:
            raise ValueError(f"--path-aliases: {token!r} is not auto, none or NAME=/path")
    return tuple(found.items())


def _group(ev: NormalizedEvent, edit_calls: str, read_calls: str) -> tuple[str, str] | None:
    if ev.kind is not EventKind.TOOL_CALL or ev.meta.get("tool_error"):
        return None
    kind = ev.meta.get("tool_kind")
    if kind == "edit" and edit_calls == "collapse":
        return ("edit", ev.meta.get("tool_file", ""))
    if kind == "read" and read_calls == "collapse":
        return ("read", "")
    return None


def _merge(run: list[NormalizedEvent], group: tuple[str, str]) -> NormalizedEvent:
    first, last = run[0], run[-1]
    count = len(run)
    if group[0] == "edit":
        text = f"[edited {group[1] or '(unknown file)'}"
        if count > 1:
            text += f" x{count}"
        intents = list(dict.fromkeys(e.meta.get("tool_intent", "") for e in run if e.meta.get("tool_intent")))
        if intents:
            text += ": " + toolresult.one_line("; ".join(intents), _INTENT_LIMIT)
        text += "]"
    else:
        text = f"[oriented: {count} read{'' if count == 1 else 's'}]"
    merged = replace(first, text=text, meta=dict(first.meta))
    merged.meta["collapsed"] = str(count)
    if "gap_after" in last.meta:
        merged.meta["gap_after"] = last.meta["gap_after"]
    else:
        merged.meta.pop("gap_after", None)
    return merged


def collapse_tool_calls(
    events: list[NormalizedEvent], edit_calls: str, read_calls: str,
) -> list[NormalizedEvent]:
    """Merge consecutive same-group tool-call events (see module docstring).
    A no-op unless one of the two options is `collapse`."""
    if edit_calls != "collapse" and read_calls != "collapse":
        return events
    out: list[NormalizedEvent] = []
    run: list[NormalizedEvent] = []
    run_group: tuple[str, str] | None = None
    for ev in events:
        group = _group(ev, edit_calls, read_calls)
        if run and group != run_group:
            assert run_group is not None
            out.append(_merge(run, run_group))
            run = []
        if group is None:
            out.append(ev)
        else:
            run.append(ev)
            run_group = group
    if run:
        assert run_group is not None
        out.append(_merge(run, run_group))
    return out
