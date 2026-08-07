"""New-side changed-line extraction from a ``git diff --unified=0`` text.

:func:`parse_added_lines` is pure text parsing — it never shells out and knows
nothing about source roots, which is deliberately :mod:`assay.measurability`'s
job instead. Ported from the union of the three cited sibling gates, all three
of which agree line-for-line on this algorithm (only the return shape
differs — see :class:`AddedLines`'s docstring for why that changes here).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = ["AddedLines", "parse_added_lines"]

#: A unified-diff hunk header: ``@@ -<old start>[,<old count>] +<new
#: start>[,<new count>] @@[ trailing context text]``. Both counts are
#: OPTIONAL — omitted means 1 — and only the new-side start is ever used, so
#: it is the only capturing group. ``.match`` (not ``.fullmatch``) is used
#: deliberately: some git configurations append the enclosing function's
#: signature after the closing ``@@``, and that trailing text is not part of
#: what this regex needs to reject.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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
      counter without recording anything.

    Paths keep git's ``a/``/``b/`` prefix stripped from the ``+++`` header,
    and tolerate its absence (``git diff --no-prefix``). The ``---`` header
    is skipped unconditionally — the old-side path is never a source path
    this function reports, since everything it records is new-side.
    """
    by_file: dict[str, set[int]] = {}
    current: str | None = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target.removeprefix("b/")
            continue
        if line.startswith("--- "):
            continue
        match = _HUNK_RE.match(line)
        if match:
            new_lineno = int(match.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            by_file.setdefault(current, set()).add(new_lineno)
            new_lineno += 1
        elif line.startswith("-"):
            continue
        else:
            new_lineno += 1
    return AddedLines(
        by_file=MappingProxyType(
            {path: frozenset(lines) for path, lines in by_file.items()}
        )
    )
