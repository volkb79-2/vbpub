"""`nyxloom extract-debug` -- a colored diff between the full lossless base
(`lossless.py`'s own "dumb, independent" dump, the ground truth of
everything a session COULD have kept) and what a given `extract()` call
--profile/--max-words/etc. actually kept, so a reader can see EXACTLY what a
profile/budget threw away, in place, instead of trusting a bare word count
or re-deriving it by eye. Operator ask, verbatim: "it compares our dumb
lossless extraction output with a given profile/parameter -- identical how
we would call `extract`."

Deliberately a TEXT-level diff (`difflib.SequenceMatcher` over each side's
own rendered blocks, normalized only enough to strip each side's own
formatting -- lossless.py's `===[i | ts | TAG]===` header, render.py's bare
`OPERATOR: ` prefix), not a marker-level one: `lossless.py` is an
independent raw-record reader that deliberately does NOT share code with
the adapters (see its own module docstring, point 1 -- "an unbiased ground
truth" for judging the smart classifier), so it has no marker convention in
common with `select()`'s kept-event set to align against directly. A text
diff sidesteps that mismatch entirely and is arguably the more honest
comparison anyway: it shows what's in the final output vs not, regardless
of which stage (the adapter's own upfront filtering, or `select()`'s
downstream windowing) did the cutting -- both are real information loss
from the reader's point of view.

Color scheme (operator's own spec):
- **white** (no color code): identical in both -- verbatim lossless content
  that also survived into the extract output.
- **grey**: lossless-only -- content the extract run dropped. A CONTIGUOUS
  run of dropped lossless blocks gets ONE bracketing note, not one per
  block (matches render.py's own gap-note philosophy: aggregate per
  contiguous run, not per record).
- **cyan**: nyxloom's own commentary -- the `>>> ... <<<` gap-count wrapper
  this module adds around a dropped run, AND any extract-only insertion
  that's a bracketed nyxloom-authored note (`[gap: ...]`, `[older session
  content...]`) rather than real transcript content.
- **green**: real added value -- an E-012 ledger line (`[files read: ...]`
  etc., see `ledger.py`) that has no lossless counterpart because it's
  synthesized, not verbatim transcript.

This module's own gap count ("N lossless blocks dropped") is intentionally
a SEPARATE number from render.py's own `[gap: N records omitted]` note
(which may also appear here, colored cyan, as an extract-only insertion) --
they count different units (lossless.py's own block granularity vs the
adapter's raw seq-unit granularity) and conflating them into one number
would be a wrong, not just imprecise, label. Showing both, distinctly
labeled, is more honest than merging them.
"""

from __future__ import annotations

import re

_LOSSLESS_HEADER_RE = re.compile(r"^===\[[^\]]*\]===\n", re.MULTILINE)
_OPERATOR_PREFIX = "OPERATOR: "
_MARKER_FOOTER_RE = re.compile(r"\n<!-- nyxloom-extract: format=\S+ marker=\S+ -->\n$")

_RESET = "\x1b[0m"
_GREY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"

# Prefixes render.py/ledger.py themselves use for nyxloom-authored bracketed
# notes -- kept in sync by hand (small, stable set); see each module's own
# note-formatting code (render.py's _gap_note/_STOP_REASON_TEXT,
# ledger.py's Ledger.render()).
_CYAN_NOTE_PREFIXES = ("[gap:", "[older session content")
_GREEN_NOTE_PREFIXES = ("[files read:", "[files edited:", "[commits created:", "[branches involved:", "[tests:")


def _lossless_blocks(text: str) -> list[str]:
    # lossless.py's own block-join separator is exactly "\n\n" between two
    # "===[...]===" headers -- split right before each header, matching it.
    # (`={3}` spelled as a repetition, not 4 stacked literal "=" chars,
    # since "(?===\[)" is only 3 "=" total -- one too few, and silently
    # matches nothing rather than raising, which is what "\n\n===[" needs.)
    parts = re.split(r"\n\n(?=={3}\[)", text.strip())
    return [p for p in parts if p.strip()]


def _extract_blocks(text: str) -> list[str]:
    text = _MARKER_FOOTER_RE.sub("", text.strip())
    parts = text.split("\n\n---\n\n")
    return [p for p in parts if p.strip()]


def _normalize_lossless(block: str) -> str:
    return _LOSSLESS_HEADER_RE.sub("", block, count=1).strip()


def _normalize_extract(block: str) -> str:
    b = block.strip()
    return b[len(_OPERATOR_PREFIX):] if b.startswith(_OPERATOR_PREFIX) else b


def _note_color(block: str) -> str | None:
    b = block.strip()
    if any(b.startswith(p) for p in _CYAN_NOTE_PREFIXES):
        return _CYAN
    if any(b.startswith(p) for p in _GREEN_NOTE_PREFIXES):
        return _GREEN
    return None


def render_debug(lossless_text: str, extract_text: str, use_color: bool) -> str:
    """Pure function: both inputs are already-rendered strings (the caller
    -- cmd_extract_debug -- owns calling `lossless.dump_*`/`extract()` with
    matching path/fmt/session args, "identical how we would call extract").
    """
    import difflib

    lossless_raw = _lossless_blocks(lossless_text)
    extract_raw = _extract_blocks(extract_text)
    lossless_norm = [_normalize_lossless(b) for b in lossless_raw]
    extract_norm = [_normalize_extract(b) for b in extract_raw]

    def paint(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if use_color and code else text

    out: list[str] = []
    sm = difflib.SequenceMatcher(None, lossless_norm, extract_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                out.append(paint("", lossless_raw[k]))
            continue

        if tag in ("delete", "replace"):
            dropped = lossless_raw[i1:i2]
            n = len(dropped)
            unit = "block" if n == 1 else "blocks"
            grey_body = "\n\n".join(paint(_GREY, b) for b in dropped)
            out.append(paint(_CYAN, f">>> [gap: {n} lossless {unit} dropped]"))
            out.append(f"---\n{grey_body}\n---")
            out.append(paint(_CYAN, "<<<"))

        if tag in ("insert", "replace"):
            for k in range(j1, j2):
                block = extract_raw[k]
                color = _note_color(block)
                out.append(paint(color, block) if color else block)

    return "\n\n".join(out) + "\n"
