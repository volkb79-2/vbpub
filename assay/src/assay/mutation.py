"""Mutant identity, and the byte-offset arithmetic every mutation engine
shares — P11: **each mutant assay generates is a valid single changed-line
experiment, not arbitrary broken text.**

This module owns exactly two things, both deliberately language-free so a
future second mutation engine (were one ever built — none is, for Go, per
A-042) would not need to duplicate them:

* :class:`Mutant` — the typed result of one construction (A-092/A-114).
  Lives here, not in ``adapters/base.py``, because ``verdict.py`` is
  forbidden to this package and ``Mutant`` needs a home the frozen protocol
  module can import from for its own type hint.
* :func:`byte_offset` / :func:`line_for_offset` — the ``(lineno,
  col_offset) <-> absolute byte index`` conversions a byte-exact splice
  needs. ``ast``'s own ``col_offset``/``end_col_offset`` are **UTF-8 byte**
  offsets within a physical line (verified empirically against a non-ASCII
  fixture while authoring this package: a multi-byte character before a
  node shifts its ``col_offset`` by its own BYTE width, not by 1), so any
  splice arithmetic done in Python ``str`` character units would silently
  misalign the moment source contains a non-ASCII character. Operating in
  ``bytes`` throughout removes that whole class of bug rather than trusting
  ASCII-only fixtures to hide it.

**Not here, deliberately**: any AST walk, any node-type catalogue, any
splice construction. Those are PYTHON syntax knowledge (A-097's own
adapter-surface split: a source language's own syntax lives only in its
adapter) and stay in ``adapters/python.py``, mirroring where P07 put
``_statement_spans`` — the shared TYPE (``StatementSpan``) lives beside the
protocol, the language-specific WALK stays in the language's own adapter
file. ``Mutant`` is the identical split, one module over, forced sideways
into its own module only because ``verdict.py`` (where ``StatementSpan``'s
sibling types live) is off-limits here.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MUTATION_OPERATORS", "Mutant", "byte_offset", "line_for_offset"]

#: The closed, four-value mutation catalogue (A-112/A-114), adopted verbatim
#: from ``/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`` and
#: DESIGN-GUIDE §11's own TOML example (``operators =
#: ["compare-swap","boolop-swap","bool-const-flip","falsy-swap"]``).
#: ``Mutant.operator`` is validated against this set as a plain ``str``, not
#: an enum — matching :class:`~assay.verdict.CanaryResult`'s own
#: ``mechanism: str`` precedent for a package-local closed vocabulary
#: (A-114).
MUTATION_OPERATORS: frozenset[str] = frozenset(
    {"compare-swap", "boolop-swap", "bool-const-flip", "falsy-swap"}
)


@dataclass(frozen=True, kw_only=True)
class Mutant:
    """One valid, single-site mutation of a source file (A-092/A-114).

    ``mutated_text`` is the FULL text with exactly ONE construct changed —
    the byte-exact splice result, never a whole-file reprint (A-112's own
    "not ``ast.unparse`` on a tree copy" ruling). Every byte of
    ``mutated_text`` outside the one changed span is identical to the input
    ``text`` :meth:`~assay.adapters.python.PythonAdapter.generate_mutants`
    was called with, including every newline, comment, and quote style —
    that byte-preservation property is O1's own claim and is proven at the
    test layer (a hand-derived one-diff assertion per fixture), not
    re-asserted structurally here: this dataclass has no way to see the
    original ``text`` to compare against, by design (A-114 lists exactly
    four fields; carrying the pre-mutation text as a fifth would let a
    caller diff against the WRONG original after a mutant crosses a
    boundary, e.g. a JSON round-trip, where only ``mutated_text`` survives).

    :attr:`identity` is **derived**, not stored (A-114): it mirrors
    :class:`~assay.verdict.EvidenceDeclaration.identity` /
    :class:`~assay.verdict.Evidence.identity`'s own "a property built from
    already-present fields" shape. ``(lineno, operator, description)`` alone
    is not always unique — a 3+-operand boolean chain (A-115) produces
    several sites sharing an identical ``(lineno, operator, description)``
    triple (e.g. two ``"And->Or"`` sites on the same line, for ``a and b
    and c``) — so ``mutated_text`` is folded into the tuple too: two
    genuinely different splices always produce two different full texts
    (the changed span sits at a different byte offset), which is exactly
    the "stable identity" O1 asks for without inventing a fifth stored
    field (a byte-offset column) nothing else in the return contract needs.
    """

    lineno: int
    operator: str
    description: str
    mutated_text: str

    def __post_init__(self) -> None:
        if isinstance(self.lineno, bool) or not isinstance(self.lineno, int):
            raise ValueError(f"Mutant.lineno must be an integer, got {self.lineno!r}")
        if self.lineno < 1:
            raise ValueError(f"Mutant.lineno must be >= 1, got {self.lineno}")
        if self.operator not in MUTATION_OPERATORS:
            raise ValueError(
                f"Mutant.operator must be one of {sorted(MUTATION_OPERATORS)}, "
                f"got {self.operator!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"Mutant.description must be a non-empty string, got "
                f"{self.description!r}"
            )
        if not isinstance(self.mutated_text, str) or not self.mutated_text:
            raise ValueError(
                f"Mutant.mutated_text must be a non-empty string, got "
                f"{self.mutated_text!r}"
            )

    @property
    def identity(self) -> tuple[int, str, str, str]:
        return (self.lineno, self.operator, self.description, self.mutated_text)


def byte_offset(text_bytes: bytes, lineno: int, col_offset: int) -> int:
    """Convert a 1-based ``lineno`` and a 0-based UTF-8 byte ``col_offset``
    — the exact units ``ast`` reports on every node's own ``lineno``/
    ``col_offset``/``end_lineno``/``end_col_offset`` — into an absolute byte
    index into ``text_bytes`` (``text.encode("utf-8")``).

    Assumes ``\\n`` alone terminates a physical line, matching how every
    fixture this project commits is written (and how ``ast`` itself counts
    ``lineno`` for LF-terminated source); a ``\\r\\n`` file would shift
    every offset after the first line by the accumulated ``\\r`` count,
    which this project's own fixtures never exercise.
    """
    offset = 0
    remaining = lineno - 1
    while remaining > 0:
        newline_index = text_bytes.index(b"\n", offset)
        offset = newline_index + 1
        remaining -= 1
    return offset + col_offset


def line_for_offset(text_bytes: bytes, offset: int) -> int:
    """The 1-based physical line containing byte index ``offset`` in
    ``text_bytes`` — the inverse direction of :func:`byte_offset`, used to
    give a mutation SITE (an operator token's own location, which may sit
    on a different physical line than the AST node that owns it — A-115's
    own boolean-chain-wrapping case) its own precise ``lineno``, rather than
    inheriting the enclosing node's ``lineno``.
    """
    return text_bytes.count(b"\n", 0, offset) + 1
