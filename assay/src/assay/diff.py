"""New-side changed-line extraction from a ``git diff --unified=0`` text.

:func:`parse_added_lines` is pure text parsing — it never shells out and knows
nothing about source roots, which is deliberately :mod:`assay.measurability`'s
job instead.

**P15 rewrite (A-067 finding 3, sol's post-series review).** The original
parser recognised a ``+++ ``/``--- `` file header by checking
``line.startswith("+++ ")`` UNCONDITIONALLY on every line, including deep
inside a hunk body. Two real inputs broke that: a genuinely added source line
whose own content begins ``++`` becomes the literal patch byte sequence
``+++ value`` once diffed (the ``+`` marker plus content starting ``++``),
which the old code misread as a NEW FILE HEADER — silently losing the real
added line and corrupting every line number that followed it in that file.
The old code's ``line.startswith("--- ")`` check had the identical unconditional
flaw — a REMOVED line beginning ``-- `` becomes ``--- value`` and was equally
misread as an old-side file header rather than a deletion — but that specific
collision happened to be harmless in the old parser's own output, since a
deletion contributes no recorded line either way (the header branch and the
deletion branch were both bare ``continue``). Relying on that coincidence
instead of a real state machine is exactly the kind of fragility this rewrite
removes; nothing about the fix below depends on which content shape happens
to be observably safe. Git's own ``\\ No newline at end of file`` marker was
not recognised at all and fell into the generic "context line" branch,
advancing the new-side counter as though it were real content.

The fix is a real state machine, not a patched regex: this parser always
knows whether it is AWAITING a hunk header/file header
(:attr:`_State.AWAITING`) or INSIDE a hunk body with a known number of
old-side and new-side lines still to consume (:attr:`_State.IN_HUNK`). A
hunk's ``-old_count``/``+new_count`` from its
own ``@@`` header are the ONLY thing that ever closes a hunk — never "the next
line that happens to look like a header" — so a body line's own CONTENT can
never be misread as a header, deletion marker, or hunk boundary, regardless
of what bytes it starts with. Only while genuinely awaiting a header are
``+++ ``/``--- ``/``@@ ... @@`` recognised at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping

__all__ = ["AddedLines", "parse_added_lines"]

#: A unified-diff hunk header: ``@@ -<old start>[,<old count>] +<new
#: start>[,<new count>] @@[ trailing context text]``. All four counts/starts
#: are captured; either count is OPTIONAL (omitted means 1) and only the two
#: counts and the new-side start are used by this module. ``.match`` (not
#: ``.fullmatch``) is used deliberately: some git configurations append the
#: enclosing function's signature after the closing ``@@``, and that trailing
#: text is not part of what this regex needs to reject.
_HUNK_RE = re.compile(
    r"^@@ -\d+(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

#: The named escapes git's C-style path quoting uses for control characters
#: it always escapes (independent of ``core.quotePath``, which only governs
#: bytes ``>= 0x80`` — git.py's :func:`~assay.git.run` disables that half, so
#: an octal escape reaching this module only ever encodes one of these bytes,
#: a bare ``\\``/``\"``, or a control byte with no single-letter name).
_NAMED_ESCAPES: Mapping[str, int] = MappingProxyType(
    {
        "a": 0x07,
        "b": 0x08,
        "f": 0x0C,
        "n": 0x0A,
        "r": 0x0D,
        "t": 0x09,
        "v": 0x0B,
        "\\": 0x5C,
        '"': 0x22,
    }
)
_OCTAL_ESCAPE_RE = re.compile(r"[0-7]{3}")


class _State(Enum):
    """Which kind of line this parser is prepared to recognise next."""

    #: Between hunks (or before the first one): a ``+++ ``/``--- `` file
    #: header or a ``@@ ... @@`` hunk header may appear next; anything else
    #: (``diff --git``, ``index``, mode/similarity/rename lines, or the
    #: trailing no-newline marker of the hunk just closed) is inert and
    #: skipped.
    AWAITING = auto()
    #: Inside a hunk body with old/new lines still to consume. Every line's
    #: CONTENT is irrelevant to state transitions here — only its leading
    #: marker byte (or the no-newline marker) is read, and the hunk's own
    #: declared counts (never a content pattern) decide when it closes.
    IN_HUNK = auto()


@dataclass(frozen=True, kw_only=True)
class AddedLines:
    """New-side added line numbers from a diff, keyed by new-side path.

    A bare ``dict[str, set[int]]`` — what all three cited sibling gates
    return — is deliberately not used here (A-091): P05 consumes this as a
    typed value, and a frozen dataclass around an immutable mapping-of-
    frozensets means neither the mapping nor any per-file line set can be
    mutated out from under a caller that holds one.

    A file that changed only by deletion (every touched line was a ``-``, no
    ``+``) is simply **absent** from ``by_file`` — not present with an empty
    set — because it never contributed a new-side line number to record.
    """

    by_file: Mapping[str, frozenset[int]]


def _unquote_git_path(spelled: str) -> str:
    """Reverse git's C-style quoting of a path shown in a diff header line.

    A path containing a double quote, a backslash, or a byte below ``0x20``
    (a control character — including a literal embedded newline) is ALWAYS
    wrapped by git in double quotes with those bytes backslash-escaped,
    regardless of ``core.quotePath`` — that setting only governs whether
    bytes ``>= 0x80`` (non-ASCII) get a SEPARATE octal-escaped quoting, which
    :func:`assay.git.run` disables for every invocation. So an escape
    sequence reaching this function only ever spells one of: a literal
    backslash/quote, a control byte with a one-letter C name, or a control
    byte named only by its ``\\NNN`` octal value.

    A SPACE is not itself escape-worthy, so it never *causes* quoting — but
    whenever the header line git prints contains one, git appends exactly one
    literal trailing tab to that line (``strchr(line, ' ') ? "\\t" : ""`` in
    its own ``diff.c``), the one disambiguation a ``---``/``+++`` line can
    carry, since nothing else about the line marks where the path ends.

    That marker is appended to the printed line, **not** to the path, so it
    lands AFTER the closing double quote when some *other* byte in the same
    path forced quoting as well — a path containing both a space and a quote,
    a backslash, a tab, or an embedded newline prints as
    ``+++ "b/a \\"b c.py"<TAB>``. It is therefore removed here BEFORE asking
    whether what remains is a quoted spelling; asking first (P15's original
    shape) left the whole quoted form unrecognised, and the raw spelling
    ``"b/a \\"b c.py"`` — quotes, escapes, un-stripped ``b/`` prefix and all —
    became the file's recorded identity (controller repair, A-134,
    reproduced against a real git binary). A trailing tab can never be part
    of the real path in either branch: a genuine trailing tab is a control
    byte, which forces quoting, and so would appear as the escape ``\\t``
    INSIDE the closing quote rather than after it.

    Raises :class:`ValueError` on a truncated or unrecognised escape rather
    than guessing: a path identity this function cannot decode with
    confidence must not silently become a different path.
    """
    if spelled.endswith("\t"):
        spelled = spelled[:-1]
    if len(spelled) < 2 or spelled[0] != '"' or spelled[-1] != '"':
        return spelled
    inner = spelled[1:-1]
    out = bytearray()
    index = 0
    length = len(inner)
    while index < length:
        char = inner[index]
        if char != "\\":
            out.extend(char.encode("utf-8"))
            index += 1
            continue
        if index + 1 >= length:
            raise ValueError(f"truncated escape sequence in quoted path {spelled!r}")
        escape = inner[index + 1]
        if escape in _NAMED_ESCAPES:
            out.append(_NAMED_ESCAPES[escape])
            index += 2
            continue
        octal_candidate = inner[index + 1 : index + 4]
        if _OCTAL_ESCAPE_RE.fullmatch(octal_candidate):
            out.append(int(octal_candidate, 8))
            index += 4
            continue
        raise ValueError(f"unrecognised escape '\\{escape}' in quoted path {spelled!r}")
    return out.decode("utf-8")


def parse_added_lines(diff_text: str) -> AddedLines:
    """Walk ``git diff --unified=0`` output and return new-side added lines.

    Only new-side additions count:

    * a ``+`` body line is an added or edited line at the running new-side
      line number, which then advances;
    * a ``-`` body line is a pure deletion — it advances nothing on the new
      side and contributes no line number;
    * a deleted file (``+++ /dev/null``) contributes no added lines at all:
      every body line under it is skipped, because there is no new-side file
      for a line number to belong to;
    * a plain context line (only possible with wider context than ``-U0``,
      which every caller in this codebase uses, but handled for any caller
      that passes this function text from elsewhere) advances the new-side
      counter without recording anything;
    * git's ``\\ No newline at end of file`` marker (any line beginning with
      a literal backslash is this kind of metadata — the only such line a
      unified diff ever emits) advances NEITHER side; it describes the line
      immediately before it, never a line of its own.

    Paths keep git's ``a/``/``b/`` prefix stripped from the ``+++`` header,
    and tolerate its absence (``git diff --no-prefix``). A path git quoted
    (C-style, double-quoted with backslash escapes — always applied to a
    path containing a quote, backslash, or control byte including a literal
    newline) is unquoted back to its real bytes before the prefix strip. The
    ``---`` header is skipped unconditionally — the old-side path is never a
    source path this function reports, since everything it records is
    new-side.

    A hunk's own declared ``-old_count``/``+new_count`` (from its ``@@``
    header) is what determines where its body ends — never the CONTENT of a
    body line. This is what makes an added or removed line whose own text
    happens to start ``++``/``--`` safe: while inside a hunk, this parser is
    not looking for header-shaped text at all, only for a line's single
    leading marker byte.

    A unified diff is delimited by ``\\n`` and by nothing else, so that is the
    only separator this function splits on — see the comment below for why
    ``str.splitlines()`` is the wrong tool here even though it reads like the
    obvious one.
    """
    by_file: dict[str, set[int]] = {}
    current: str | None = None
    new_lineno = 0
    state = _State.AWAITING
    remaining_old = 0
    remaining_new = 0

    # Split on ``\n`` and NOTHING else. ``str.splitlines()`` also breaks on a
    # bare ``\r``, a form feed, a vertical tab, ``\x1c``-``\x1e``, ``\x85``,
    # U+2028 and U+2029 — every one of which occurs in ordinary source
    # content (a form feed is the conventional page separator in Python and
    # Lisp sources; a bare ``\r`` sits inside string literals; U+2028 turns up
    # in JavaScript and JSON). Each splits ONE diff body line into two, and
    # against a counts-driven state machine that does not merely shift the
    # following line numbers: the phantom second half consumes a slot, the
    # hunk closes early, and every remaining added line in it is silently
    # DROPPED — under-measurement, the direction that turns into a false PASS
    # (controller repair, A-134; reproduced through a real ``git diff`` of a
    # real Python file). :func:`assay.git.run` deliberately does not enable
    # universal-newline translation either, so a ``\r`` in content is still a
    # ``\r`` by the time it arrives here rather than a second ``\n``.
    #
    # Git's own diff output is ``\n``-terminated, so the only artefact of a
    # plain split is one trailing empty field after the final newline. No real
    # unified-diff line is ever empty — every body line carries a marker byte
    # and every header line is non-empty — so dropping it is unambiguous, and
    # ``str.split`` always returns at least one element, so indexing is safe.
    lines = diff_text.split("\n")
    if lines[-1] == "":
        lines.pop()

    for line in lines:
        if state is _State.IN_HUNK and (remaining_old > 0 or remaining_new > 0):
            if line.startswith("\\"):
                # The no-newline marker (or any future backslash-prefixed
                # metadata line): describes the previous line, advances
                # neither side, and does not close the hunk by itself.
                continue
            if line.startswith("+"):
                if current is not None:
                    by_file.setdefault(current, set()).add(new_lineno)
                new_lineno += 1
                remaining_new -= 1
            elif line.startswith("-"):
                remaining_old -= 1
            else:
                # A context line (marker " ", only possible with wider
                # context than -U0) or any other body byte this parser does
                # not specifically classify: advances both sides like real
                # unified-diff context does, never records anything.
                new_lineno += 1
                remaining_old -= 1
                remaining_new -= 1
            if remaining_old <= 0 and remaining_new <= 0:
                state = _State.AWAITING
            continue

        # AWAITING: a file header or hunk header may appear next; anything
        # else here (diff --git, index, mode/rename/similarity lines, or a
        # trailing no-newline marker right after a hunk closed) is inert.
        if line.startswith("\\"):
            continue
        if line.startswith("+++ "):
            target = _unquote_git_path(line[4:])
            current = None if target == "/dev/null" else target.removeprefix("b/")
            continue
        if line.startswith("--- "):
            continue
        match = _HUNK_RE.match(line)
        if match:
            new_lineno = int(match.group("new_start"))
            remaining_old = int(match.group("old_count") or 1)
            remaining_new = int(match.group("new_count") or 1)
            state = _State.IN_HUNK
            continue
        # diff --git / index / mode / rename / similarity lines: not a
        # header this function needs, and never body content either.

    return AddedLines(
        by_file=MappingProxyType(
            {path: frozenset(lines) for path, lines in by_file.items()}
        )
    )
