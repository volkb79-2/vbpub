"""`nyxloom extract --successor-brief`: ONE markdown document that primes a
FRESH agent from a stopped (or lost) Claude Code subagent's transcript.

Section order (nyxloom-SUCCESSOR, operator decision 2026-10-06):
  1. the ORIGINAL BRIEF -- the transcript's first user record, verbatim; or,
     above `brief_max_chars`, its path + sha256 (so it is never retyped);
  2. the extract (successor defaults: --profile all, --tool-calls
     intent-or-call, --tool-errors show), with the brief's own event omitted
     (it is section 1);
  3. the whole-session ledger incl. external effects;
  4. the Stop state;
  5. "Your order" -- from `--order TEXT|@FILE`, only when given.

No redaction happens (operator ruling): the document reproduces what the
transcript already contains.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .ledger import Ledger
from .stopstate import StopState, read_meta

DEFAULT_BRIEF_MAX_CHARS = 6000


def first_user_record(path: Path) -> tuple[str, str] | None:
    """(uuid, text) of the first `user` record with real text content -- the
    dispatch prompt of an Agent-tool subagent -- or None."""
    with Path(path).open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user":
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                continue
            if text.strip():
                return rec.get("uuid") or "", text
    return None


def read_order(spec: str) -> str:
    """`--order TEXT` or `--order @FILE`."""
    if spec.startswith("@"):
        return Path(spec[1:]).read_text(encoding="utf-8")
    return spec


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def brief_section(path: Path, text: str, max_chars: int) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if len(text) > max_chars:
        return (
            "## Original brief (not inlined: longer than "
            f"{max_chars} chars)\n"
            f"The predecessor's complete original instructions are the FIRST user record "
            f"(line 1) of `{Path(path).absolute()}`; {len(text)} chars, sha256 `{digest}`. "
            "Read them there in full before acting -- they still bind you except where "
            "'Your order' amends them.\n"
        )
    fence = _fence(text)
    return (
        "## Original brief (verbatim)\n"
        f"sha256 `{digest}` ({len(text)} chars) -- the predecessor's complete original "
        "instructions; they still bind you except where 'Your order' amends them.\n\n"
        f"{fence}text\n{text}\n{fence}\n"
    )


def assemble(
    path: Path,
    brief_text: str,
    extract_text: str,
    session_ledger: Ledger,
    stop_state: StopState,
    order: str | None,
    brief_max_chars: int = DEFAULT_BRIEF_MAX_CHARS,
    omitted_brief_note: bool = True,
) -> str:
    path = Path(path)
    meta = read_meta(path) or {}
    header = [f"# Successor brief for agent `{path.stem.removeprefix('agent-')}`"]
    facts = []
    for key in ("description", "agentType", "model", "stoppedByUser"):
        if key in meta:
            facts.append(f"{key}: {meta[key]}")
    facts.append(f"transcript: {path.absolute()}")
    header.append("\n".join(f"- {f}" for f in facts))
    note = (
        " The original brief is omitted from the extract (it is the section above)."
        if omitted_brief_note else ""
    )
    parts = [
        "\n".join(header) + "\n",
        brief_section(path, brief_text, brief_max_chars),
        "## Extract of the predecessor's work\n"
        "Mechanical, no LLM. Tool RESULTS are not shown except failures; each `[gap: N records "
        "omitted]` marks omitted records. Claims below are the predecessor's own, not verified."
        + note + "\n\n" + extract_text.rstrip("\n") + "\n",
        "## Ledger\n" + session_ledger.render_session().split("\n", 1)[1] + "\n",
        "## Stop state\n" + stop_state.render().split("\n", 1)[1] + "\n",
    ]
    if order is not None:
        parts.append("## Your order\n" + order.strip() + "\n")
    return "\n".join(parts)
