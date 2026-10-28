"""The SQL/DDL :class:`~assay.adapters.base.LanguageAdapter` (P34/W2, carve
``W3-CARVE-P34-sql-adapter.md`` §3.2/§3.3, A-281/A-283) -- route (i), a
stdlib-only lexer, never an external tool (``external_tools = ()``): §4.1's
own measurement is that no SQL parser exists anywhere in this devcontainer
or in ``tester-unified:local``, and the seven operators below are keyword
patterns plus parenthesis matching, not grammar -- what makes them hard is
knowing which bytes are code, which is :mod:`assay.adapters.sql_lex`'s own
job.

**SQL is R2-only (A-242's own sentence).** ``cli._built_in_registry``
registers this adapter's ``RegistryEntry.rigor`` as ``{"R2"}`` only (W6,
outside this package's scope), which makes :meth:`SqlAdapter.
has_executable_code`, :meth:`~SqlAdapter.normalize_coverage_key`,
:meth:`~SqlAdapter.statement_spans`, :meth:`~SqlAdapter.inject_import_break`
and :meth:`~SqlAdapter.inject_uncovered_line` provably UNREACHABLE through
the CLI before any of them is ever called -- so each RAISES
``NotImplementedError`` naming that reachability fact, rather than
implementing an R0/R1-only capability SQL never gets asked for. Never a bare
``...`` body: ``coverage.py`` auto-excludes those from a branch report, and
this project's own gate would read the exclusion as a pragma dodge (the
``ciu`` Protocol-stub trap).

**Bytes throughout, encoded exactly once (A-005's own byte-offset
precedent, carried from :mod:`assay.adapters.sql_lex`).**
:meth:`generate_mutation_sites` receives ``text: str`` but returns
:class:`~assay.mutation.MutationSite` descriptors carrying BYTE offsets;
this module encodes to UTF-8 once at the top of that method and every
scanner below operates on the resulting ``bytes`` (and the same-length code
mask :func:`~assay.adapters.sql_lex.lex_sql` returns over it), never on
``text`` itself. All seven operator scanners locate their STRUCTURE
(keywords, matching parentheses, statement boundaries) against the MASKED
bytes -- so a keyword or paren living inside a comment, string, or quoted
identifier can never be mistaken for real code -- and read their actual
VALUE content (an ``IN``-list member's literal text, a replacement's own
constructed bytes) from the ORIGINAL, unmasked source, since the mask has
blanked exactly the string/comment content a value needs.

**Retention is A-281's bounded worst-eviction heap, not the carve's own
struck sentence.** ``MutationSite.identity`` is ``(start_byte, end_byte,
replacement_sha256, operator)`` -- position-major, operator last -- so a
per-operator walk that stops at *limit* cannot be "in identity order", and
:func:`assay.mutation._validate_sites` refuses a batch that is not. Every
scanner below is instead just a candidate SOURCE; :meth:`generate_mutation_sites`
feeds every candidate from every scanner into ONE heap bounded at *limit* and
keyed by identity, mirroring :func:`assay.adapters.python._generate_python_sites`
(``python.py:742-763``) exactly, and sorts by identity once at return.

**The six lexical context rules (three from the carve's own §3.2, three more
from A-283), each with its own scanner-level exclusion:**

1. No ``sql:drop-not-null`` site where ``NOT NULL`` is immediately preceded
   by ``IS`` -- ``IS NOT NULL`` is a predicate, not a constraint.
2. Where ``NOT NULL`` is immediately preceded by ``SET``, the span widens to
   ``SET NOT NULL`` and the replacement is ``DROP NOT NULL`` (``SET NULL``
   is a syntax error).
3. ``sql:widen-check-in`` is literal-shape-aware: all-string ``IN`` members
   widen only on a declared built-in character column; all-unsigned-integer
   members widen only on a declared bounded integer column, using an unused
   in-range value. Anything else (mixed shapes, casts, column references,
   calls, subqueries, or unknown types) emits no site at all.
4. (A-283) No ``sql:drop-not-null`` site where ``NOT NULL`` is immediately
   preceded by ``DROP`` -- the mirror of rule 2, and the very text rule 2's
   own replacement emits; ``DROP NULL`` is a syntax error.
5. (A-283) No ``sql:drop-not-null`` site inside a dollar-quoted body's
   ``DECLARE`` section (between a leading ``DECLARE`` and the first
   ``BEGIN``) -- a plpgsql declaration's ``NOT NULL`` is not a column
   constraint, and phase 2's own recursion is what brings that grammar into
   scope at all.
6. When the parsed column type is a ``serial`` pseudo-type, the column has an
   identity clause, or its inline constraint is ``PRIMARY KEY``, remove the
   explicit ``NOT NULL`` instead of replacing it with ``NULL``. These columns
   are inherently non-null. Named ``NOT NULL`` constraints and ``NO INHERIT``
   forms are refused because removing only part would leave invalid SQL.

Plus (A-283) ``sql:drop-foreign-key``'s bare column-level ``REFERENCES``
recogniser excludes any statement beginning ``GRANT``/``REVOKE`` --
``GRANT CHECK (true) ON …`` is a syntax error -- and the carve's own scope
reduction: ``sql:drop-trigger`` emits no site inside a dollar-quoted body
(``SELECT 1;`` is invalid inside a plpgsql body).

**Replacement invariants, and where this module's own reading diverges from
the carve's literal text.** The carve states the constructed replacement
must contain no ``$``, ``'``, ``"``, ``--``, ``/*``, and no ``;`` except the
trigger form's own -- but ``sql:widen-check-in``'s own all-string branch
(rule 3) necessarily constructs a NEW string literal, which cannot be
written without a delimiting quote pair. Read completely literally, the
invariant would make that entire branch dead code -- unreachable from any
input, which A-124/A-131 treat as a defect, not thoroughness. This module
therefore reads the invariant the same way it already reads its own named
exception (the trigger form's ``;``): ``sql:widen-check-in``'s replacement
may carry EXACTLY the one balanced pair of ``'`` characters delimiting its
own newly-constructed literal (never an odd count, never any other
forbidden byte), and every other operator's replacement is held to the
invariant exactly as written. :func:`_replacement_is_safe` is the one place
this reading lives, and it is checked before every candidate site is
returned, defensively, even though every replacement below is fixed,
controlled construction that should never actually trip it.
"""

from __future__ import annotations

import hashlib
import heapq
import re
from bisect import bisect_left, bisect_right
from ..records import positional_record, record
from pathlib import Path
from typing import Iterable, Iterator, Literal, NamedTuple, Sequence

from ..mutation import MutationSite, line_for_offset
from .base import Remaining, StatementBlockReport, StatementSpan
from .sql_lex import lex_sql

__all__ = ["SqlAdapter"]

# --- byte-level constants and small shared helpers --------------------------

_OPEN_PAREN = 0x28
_CLOSE_PAREN = 0x29
_COMMA = 0x2C


def _is_word_byte(b: int) -> bool:
    return b == 0x5F or 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A


def _find_matching_close_paren(
    masked: bytes,
    open_index: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """``masked[open_index] == '('``. Returns the index of its matching
    ``)`` (nested parens counted), or ``None`` if the source runs out first
    -- a malformed/unbalanced construct a scanner simply skips (an honest
    gap, never a crash) rather than raising: an unmatched paren is not one
    of :mod:`assay.adapters.sql_lex`'s own phase-3 failure constructs, so it
    is not this module's place to fail the whole file closed over it."""
    depth = 1
    i = open_index + 1
    n = len(masked)
    next_deadline_check = i + 4096
    if check_deadline is not None:
        check_deadline()
    while i < n:
        if check_deadline is not None and i >= next_deadline_check:
            check_deadline()
            next_deadline_check = i + 4096
        b = masked[i]
        if b == _OPEN_PAREN:
            depth += 1
        elif b == _CLOSE_PAREN:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _find_flat_close_paren(
    masked: bytes,
    open_index: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """Match the flat reference-column-list shape used by this scanner."""
    cursor = open_index + 1
    next_deadline_check = cursor + 4096
    if check_deadline is not None:
        check_deadline()
    while cursor < len(masked):
        if check_deadline is not None and cursor >= next_deadline_check:
            check_deadline()
            next_deadline_check = cursor + 4096
        byte = masked[cursor]
        if byte == _OPEN_PAREN:
            return None
        if byte == _CLOSE_PAREN:
            return cursor
        cursor += 1
    return None


def _preceding_word(masked: bytes, pos: int) -> tuple[bytes, int] | None:
    """The whitespace-separated WORD token (uppercased) immediately before
    *pos* in *masked*, plus its own start index -- or ``None`` if *pos* is
    not preceded by a bare word at all (start of file, or the preceding
    non-space run is punctuation/a masked boundary). Read from the MASKED
    bytes, never the source, so a word that is only "there" inside a
    blanked-out comment or string can never be mistaken for a real
    preceding keyword."""
    j = pos
    while j > 0 and masked[j - 1 : j].isspace():
        j -= 1
    end = j
    while j > 0 and _is_word_byte(masked[j - 1]):
        j -= 1
    if j == end:
        return None
    return masked[j:end].upper(), j


def _first_word(masked: bytes, pos: int) -> bytes:
    """The uppercased word starting EXACTLY at *pos*, or ``b""`` if *pos*
    is not itself the start of a word."""
    j = pos
    n = len(masked)
    while j < n and _is_word_byte(masked[j]):
        j += 1
    return masked[pos:j].upper()


def _statement_start(masked: bytes, pos: int) -> int:
    """The byte offset *pos*'s own enclosing statement begins at: right
    after the nearest preceding top-level ``;`` (or 0), skipping leading
    whitespace. Searches ``masked``, so a ``;`` inside a comment or string
    -- already blanked -- can never be mistaken for a statement boundary."""
    semicolon = masked.rfind(b";", 0, pos)
    start = 0 if semicolon == -1 else semicolon + 1
    n = len(masked)
    while start < n and masked[start : start + 1].isspace():
        start += 1
    return start


def _inside_any(spans: tuple[tuple[int, int], ...] | list[tuple[int, int]], pos: int) -> bool:
    return any(start <= pos < end for start, end in spans)


def _split_top_level(
    region: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> list[tuple[int, int]]:
    """Byte ranges (relative to *region*) of each comma-separated member at
    PAREN DEPTH ZERO within *region* -- a comma inside a nested call's own
    parens is never treated as a list separator. *region* is expected to be
    the MASKED slice between an ``IN``-list's own parens, so a comma inside
    a string literal member is already invisible (blanked to a space) and
    never mistaken for a separator either."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    next_deadline_check = 4096
    if check_deadline is not None:
        check_deadline()
    for i, b in enumerate(region):
        if check_deadline is not None and i >= next_deadline_check:
            check_deadline()
            next_deadline_check = i + 4096
        if b == _OPEN_PAREN:
            depth += 1
        elif b == _CLOSE_PAREN:
            depth -= 1
        elif b == _COMMA and depth == 0:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(region)))
    return spans


def _decode_string_literal(member: bytes) -> bytes:
    """``member`` is a full ``'...'`` string literal (outer quotes
    included); returns its decoded VALUE -- outer quotes stripped, doubled
    ``''`` un-escaped to a single ``'`` -- used only to build the set of
    EXISTING values a new widened literal must avoid colliding with."""
    return member[1:-1].replace(b"''", b"'")


def _unused_string_literal(existing: set[bytes]) -> bytes:
    """A literal (bare value, no quotes) that is not already a member of
    *existing* -- ``__assay_widened__``, or that with a numeric suffix if
    a consumer's own list already happens to contain it."""
    base = b"__assay_widened__"
    if base not in existing:
        return base
    n = 2
    while True:
        candidate = base + str(n).encode("ascii")
        if candidate not in existing:
            return candidate
        n += 1


#: Adopted from :mod:`assay.adapters.python`'s own ``_TEST_FILE_RE`` shape
#: (A-099's split: the language-specific rule stays this adapter's own),
#: widened per the carve's own spec to accept BOTH ``tests/`` and ``test/``
#: as a recognised directory segment (dstdns's own DDL layout uses neither
#: convention consistently, so both are carried).
_TEST_FILE_RE = re.compile(r"(^|/)(tests?/|test_[^/]*\.sql$|[^/]*_test\.sql$)")


# --- replacement invariants --------------------------------------------------

_FORBIDDEN_ALWAYS = (b"$", b'"', b"--", b"/*")


def _replacement_is_safe(operator: str, replacement: bytes) -> bool:
    """The module docstring's own reading of the carve's replacement
    invariant: no ``$``/``"``/``--``/``/*`` ever, no ``;`` except the
    trigger form's own single terminator, and no bare ``'`` except
    ``sql:widen-check-in``'s own newly-constructed literal (exactly a
    balanced pair, never an odd count, never present for its integer-widen
    branch at all)."""
    for token in _FORBIDDEN_ALWAYS:
        if token in replacement:
            return False
    if operator == "sql:drop-trigger":
        return replacement.count(b";") == 1 and replacement.endswith(b";")
    if operator == "sql:widen-check-in":
        if b";" in replacement:
            return False
        return replacement.count(b"'") in (0, 2)
    return b";" not in replacement and b"'" not in replacement


# --- the candidate site shape -------------------------------------------------


class _Site(NamedTuple):
    """One candidate mutation site, before line-eligibility filtering and
    before it becomes a :class:`~assay.mutation.MutationSite`. ``start``/
    ``end`` are absolute UTF-8 byte offsets into the encoded source."""

    lineno: int
    operator: str
    description: str
    start: int
    end: int
    replacement: bytes


class _SqlMutationContext(NamedTuple):
    """Cross-file facts needed to avoid unsafe or stale SQL mutations."""

    attached_indexes: frozenset[tuple[bytes | None, bytes]]
    unresolved_index_attachment: bool
    altered_columns: frozenset[tuple[tuple[bytes | None, bytes], bytes]]
    declared_column_types: frozenset[
        tuple[tuple[bytes | None, bytes], bytes, bytes]
    ]
    declared_type_names: frozenset[tuple[bytes | None, bytes]]
    search_path_may_shadow_catalog_sources: tuple[str | None, ...]
    declared_column_type_sources: tuple[
        tuple[
            str | None,
            frozenset[tuple[tuple[bytes | None, bytes], bytes, bytes]],
        ],
        ...,
    ]
    collated_columns: frozenset[tuple[tuple[bytes | None, bytes], bytes]]
    dropped_tables: frozenset[tuple[bytes | None, bytes]]
    temporary_relation_names: frozenset[bytes]
    relocated_tables: frozenset[tuple[bytes | None, bytes]]


# --- shared regex fragments ---------------------------------------------------

_CHECK_OPEN_RE = re.compile(rb"\bCHECK\b\s*\(", re.IGNORECASE)
_NOT_NULL_RE = re.compile(rb"\bNOT\s+NULL\b", re.IGNORECASE)
_FOREIGN_WORD_RE = re.compile(rb"\bFOREIGN\b", re.IGNORECASE)
_REFERENCES_RE = re.compile(rb"\bREFERENCES\b", re.IGNORECASE)
_REFERENCED_TARGET_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.]*", re.IGNORECASE)
_UNIQUE_NULLS_WORD_RE = re.compile(rb"NULLS\b", re.IGNORECASE)
_UNIQUE_NOT_WORD_RE = re.compile(rb"NOT\b", re.IGNORECASE)
_UNIQUE_DISTINCT_WORD_RE = re.compile(rb"DISTINCT\b", re.IGNORECASE)
_SQL_IDENTIFIER = rb'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_INDEX_ATTACHMENT_PREFIX_RE = re.compile(rb"\b(?:UNIQUE|PRIMARY)\b", re.IGNORECASE)
_ON_WORD_RE = re.compile(rb"\bON\b", re.IGNORECASE)
_CREATE_TRIGGER_RE = re.compile(
    rb"\bCREATE\b(?:\s+OR\s+REPLACE)?(?:\s+CONSTRAINT)?\s+TRIGGER\b", re.IGNORECASE
)
_CREATE_TABLE_RE = re.compile(rb"\bCREATE\s+TABLE\b", re.IGNORECASE)
_CREATE_SCHEMA_RE = re.compile(rb"\bCREATE\s+SCHEMA\b", re.IGNORECASE)
_DROP_SCHEMA_RE = re.compile(rb"\bDROP\s+SCHEMA\b", re.IGNORECASE)
_ALTER_SCHEMA_RE = re.compile(rb"\bALTER\s+SCHEMA\b", re.IGNORECASE)
_CREATE_VIEW_RE = re.compile(
    rb"\bCREATE\s+(?:(?:OR\s+REPLACE)\s+)?"
    rb"(?:(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP|TEMPORARY)\s+)?"
    rb"(?:RECURSIVE\s+)?(?:MATERIALIZED\s+)?VIEW\b",
    re.IGNORECASE,
)
_CREATE_TEMP_VIEW_RE = re.compile(
    rb"\bCREATE\s+(?:(?:OR\s+REPLACE)\s+)?"
    rb"(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP|TEMPORARY)\s+"
    rb"(?:RECURSIVE\s+)?VIEW\b",
    re.IGNORECASE,
)
_FROM_OR_JOIN_RE = re.compile(rb"\b(?:FROM|JOIN)\b", re.IGNORECASE)
_FROM_CLAUSE_END_KEYWORDS = frozenset(
    {
        b"join",
        b"where",
        b"group",
        b"order",
        b"having",
        b"limit",
        b"offset",
        b"fetch",
        b"union",
        b"intersect",
        b"except",
        b"window",
    }
)
_CREATE_TABLE_DEFINITION_RE = re.compile(
    rb"\bCREATE\s+(?:(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP|TEMPORARY)\s+|UNLOGGED\s+)?TABLE\b",
    re.IGNORECASE,
)
_CREATE_TEMP_TABLE_RE = re.compile(
    rb"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP|TEMPORARY)\s+TABLE\b",
    re.IGNORECASE,
)
_SELECT_INTO_TEMP_RE = re.compile(
    rb"\bINTO\s+(?:TEMP|TEMPORARY)\s+(?:TABLE\s+)?", re.IGNORECASE
)
_INTO_RE = re.compile(rb"\bINTO\b", re.IGNORECASE)
_ALTER_TABLE_RE = re.compile(rb"\bALTER\s+TABLE\b", re.IGNORECASE)
_ALTER_RELATION_RE = re.compile(rb"\bALTER\s+(?:TABLE|VIEW)\b", re.IGNORECASE)
_ALTER_TYPE_RE = re.compile(rb"\bALTER\s+TYPE\b", re.IGNORECASE)
_DROP_TABLE_RE = re.compile(rb"\bDROP\s+TABLE\b", re.IGNORECASE)
_RESET_WORD_RE = re.compile(rb"\bRESET\b", re.IGNORECASE)
_DISCARD_WORD_RE = re.compile(rb"\bDISCARD\b", re.IGNORECASE)
_TABLE_NAME_AFTER_CREATE_RE = re.compile(
    rb"\s*(?:IF\s+NOT\s+EXISTS\s+)?"
    rb"(?:(?P<schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<table>" + _SQL_IDENTIFIER + rb")",
    re.IGNORECASE,
)
_ENUM_TYPE_RE = re.compile(
    rb"\bCREATE\s+TYPE\s+(?:(?P<schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<type>" + _SQL_IDENTIFIER + rb")\s+AS\s+ENUM\b",
    re.IGNORECASE,
)
_CREATE_TYPE_STMT_RE = re.compile(
    rb"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b", re.IGNORECASE
)
_CREATE_DOMAIN_STMT_RE = re.compile(rb"\bCREATE\s+DOMAIN\b", re.IGNORECASE)
_SET_WORD_RE = re.compile(rb"\bSET\b", re.IGNORECASE)
_EXECUTE_WORD_RE = re.compile(rb"\bEXECUTE\b", re.IGNORECASE)
_ROUTINE_DDL_RE = re.compile(
    rb"\b(?P<operation>CREATE|ALTER)\s+"
    rb"(?:(?:OR\s+REPLACE)\s+)?"
    rb"(?P<object>FUNCTION|PROCEDURE|ROUTINE)\b",
    re.IGNORECASE,
)
_ALTER_GUC_DDL_RE = re.compile(
    rb"\bALTER\s+(?:ROLE|USER|DATABASE|SYSTEM)\b", re.IGNORECASE
)
_SET_CONFIG_NAME_RE = re.compile(
    rb"(?<![A-Za-z0-9_$])(?:set_config|\"set_config\")(?![A-Za-z0-9_$])",
    re.IGNORECASE,
)
_UPDATE_WORD_RE = re.compile(rb"\bUPDATE\b", re.IGNORECASE)
_ENUM_COLUMN_DECL_RE = re.compile(
    rb"\s*(?P<column>" + _SQL_IDENTIFIER + rb")\s+"
    rb"(?:(?P<type_schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<type>" + _SQL_IDENTIFIER + rb")"
    rb"(?P<type_modifier>\s*\([^()]*\))?"
    rb"(?P<array_suffix>(?:(?:\s*ARRAY\b(?:\s*\[\s*[0-9]*\s*\])?)|"
    rb"(?:\s*\[\s*[0-9]*\s*\]))*)"
    rb"(?![A-Za-z0-9_$])",
    re.IGNORECASE,
)
_SIMPLE_IDENT_RE = re.compile(rb"\s*(" + _SQL_IDENTIFIER + rb")\s*")
_EXPLICIT_COLLATE_RE = re.compile(rb"\bCOLLATE\b", re.IGNORECASE)
_CREATE_INDEX_ON_RE = re.compile(rb"\s*ON\b\s*(?:ONLY\b\s*)?", re.IGNORECASE)
_INTEGER_TYPE_BOUNDS = {
    b"smallint": (-(2**15), 2**15 - 1),
    b"int2": (-(2**15), 2**15 - 1),
    b"smallserial": (-(2**15), 2**15 - 1),
    b"integer": (-(2**31), 2**31 - 1),
    b"int": (-(2**31), 2**31 - 1),
    b"int4": (-(2**31), 2**31 - 1),
    b"serial": (-(2**31), 2**31 - 1),
    b"bigint": (-(2**63), 2**63 - 1),
    b"int8": (-(2**63), 2**63 - 1),
    b"bigserial": (-(2**63), 2**63 - 1),
}
_INTEGER_TYPE_BOUNDS.update(
    {
        b"pg_catalog.int2": _INTEGER_TYPE_BOUNDS[b"int2"],
        b"pg_catalog.int4": _INTEGER_TYPE_BOUNDS[b"int4"],
        b"pg_catalog.int8": _INTEGER_TYPE_BOUNDS[b"int8"],
    }
)
_SAFE_STRING_COLUMN_BASE_TYPES = frozenset({b"text", b"varchar", b"name"})
_SAFE_STRING_COLUMN_TYPES = _SAFE_STRING_COLUMN_BASE_TYPES | frozenset(
    b"pg_catalog." + type_name for type_name in _SAFE_STRING_COLUMN_BASE_TYPES
)
_BUILTIN_COLUMN_TYPE_NAMES = _SAFE_STRING_COLUMN_BASE_TYPES | frozenset(
    name for name in _INTEGER_TYPE_BOUNDS if b"." not in name
)
_CREATE_UNIQUE_INDEX_RE = re.compile(rb"\bCREATE\s+UNIQUE\s+INDEX\b", re.IGNORECASE)
_UNIQUE_RE = re.compile(rb"\bUNIQUE\b", re.IGNORECASE)
_IN_OPEN_RE = re.compile(rb"\bIN\b\s*\(", re.IGNORECASE)
_STRING_MEMBER_RE = re.compile(rb"'(?:[^']|'')*'")
_INT_MEMBER_RE = re.compile(rb"[0-9]+")
_DECLARE_RE = re.compile(rb"\bDECLARE\b", re.IGNORECASE)
_BEGIN_RE = re.compile(rb"\bBEGIN\b", re.IGNORECASE)


def _reference_action_end(
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    on_end = _sql_keyword_end(masked, cursor, b"ON")
    if on_end is None:
        return None
    direction_start = _skip_sql_whitespace(
        masked, on_end, check_deadline=check_deadline
    )
    if direction_start == on_end:
        return None
    direction_end = _sql_keyword_end(masked, direction_start, b"DELETE")
    if direction_end is None:
        direction_end = _sql_keyword_end(masked, direction_start, b"UPDATE")
    if direction_end is None:
        return None

    action_start = _skip_sql_whitespace(
        masked, direction_end, check_deadline=check_deadline
    )
    if action_start == direction_end:
        return None
    for keyword in (b"RESTRICT", b"CASCADE"):
        action_end = _sql_keyword_end(masked, action_start, keyword)
        if action_end is not None:
            return action_end

    no_end = _sql_keyword_end(masked, action_start, b"NO")
    if no_end is not None:
        action_end = _sql_keyword_after_whitespace(
            masked, no_end, b"ACTION", check_deadline=check_deadline
        )
        if action_end is not None:
            return action_end

    set_end = _sql_keyword_end(masked, action_start, b"SET")
    if set_end is None:
        return None
    value_start = _skip_sql_whitespace(
        masked, set_end, check_deadline=check_deadline
    )
    if value_start == set_end:
        return None
    null_end = _sql_keyword_end(masked, value_start, b"NULL")
    if null_end is not None:
        columns_start = _skip_sql_whitespace(
            masked, null_end, check_deadline=check_deadline
        )
        if columns_start < len(masked) and masked[columns_start] == _OPEN_PAREN:
            close = _find_flat_close_paren(
                masked, columns_start, check_deadline=check_deadline
            )
            return None if close is None else close + 1
        return null_end
    return _sql_keyword_end(masked, value_start, b"DEFAULT")


def _reference_modifier_end(
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    match_end = _sql_keyword_end(masked, cursor, b"MATCH")
    if match_end is not None:
        value_start = _skip_sql_whitespace(
            masked, match_end, check_deadline=check_deadline
        )
        if value_start == match_end:
            return None
        for keyword in (b"FULL", b"PARTIAL", b"SIMPLE"):
            end = _sql_keyword_end(masked, value_start, keyword)
            if end is not None:
                return end
        return None

    not_end = _sql_keyword_end(masked, cursor, b"NOT")
    if not_end is not None:
        deferrable_end = _sql_keyword_after_whitespace(
            masked, not_end, b"DEFERRABLE", check_deadline=check_deadline
        )
        if deferrable_end is not None:
            return deferrable_end
        valid_end = _sql_keyword_after_whitespace(
            masked, not_end, b"VALID", check_deadline=check_deadline
        )
        if valid_end is not None:
            return valid_end

    deferrable_end = _sql_keyword_end(masked, cursor, b"DEFERRABLE")
    if deferrable_end is not None:
        return deferrable_end
    initially_end = _sql_keyword_end(masked, cursor, b"INITIALLY")
    if initially_end is None:
        return None
    deferred_end = _sql_keyword_after_whitespace(
        masked, initially_end, b"DEFERRED", check_deadline=check_deadline
    )
    if deferred_end is not None:
        return deferred_end
    return _sql_keyword_after_whitespace(
        masked, initially_end, b"IMMEDIATE", check_deadline=check_deadline
    )


def _extend_references_clause(
    masked: bytes,
    start: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """*start* indexes the ``R`` of ``REFERENCES``. Returns the end offset
    of the whole clause: the target (optionally schema-qualified) table
    name, an optional parenthesized column list, and any trailing
    ``ON DELETE``/``ON UPDATE`` actions in either order (either or both
    absent) -- or ``None`` if there is not even a target table name
    (malformed; the caller skips it rather than guessing a span)."""
    keyword = _REFERENCES_RE.match(masked, start)
    if keyword is None:
        return None
    target_start = _skip_sql_whitespace(
        masked, keyword.end(), check_deadline=check_deadline
    )
    if target_start == keyword.end():
        return None
    target = _REFERENCED_TARGET_RE.match(masked, target_start)
    if target is None:
        return None
    end = target.end()
    columns_start = _skip_sql_whitespace(
        masked, end, check_deadline=check_deadline
    )
    if columns_start < len(masked) and masked[columns_start] == _OPEN_PAREN:
        close = _find_flat_close_paren(
            masked, columns_start, check_deadline=check_deadline
        )
        if close is None:
            return None
        end = close + 1
    while True:
        if check_deadline is not None:
            check_deadline()
        action_start = _skip_sql_whitespace(
            masked, end, check_deadline=check_deadline
        )
        action_end = _reference_action_end(
            masked, action_start, check_deadline=check_deadline
        )
        if action_end is not None:
            end = action_end
            continue
        modifier_end = _reference_modifier_end(
            masked, action_start, check_deadline=check_deadline
        )
        if modifier_end is None:
            return end
        end = modifier_end


# --- sql:drop-check ------------------------------------------------------


def _scan_drop_check(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    for m in _CHECK_OPEN_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        open_paren = m.end() - 1
        close = _find_matching_close_paren(
            masked, open_paren, check_deadline=check_deadline
        )
        if close is None:
            continue
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-check",
            description="CHECK (...) -> CHECK (true)",
            start=m.start(),
            end=close + 1,
            replacement=b"CHECK (true)",
        )


# --- sql:drop-unique -------------------------------------------------------


def _skip_sql_whitespace(
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int:
    """Skip SQL whitespace while keeping long runs inside the lane budget."""
    next_check = cursor
    while cursor < len(masked) and masked[cursor : cursor + 1].isspace():
        if check_deadline is not None and cursor >= next_check:
            check_deadline()
            next_check = cursor + 256
        cursor += 1
    return cursor


def _sql_keyword_end(masked: bytes, cursor: int, keyword: bytes) -> int | None:
    end = cursor + len(keyword)
    if masked[cursor:end].upper() != keyword:
        return None
    if end < len(masked) and _is_word_byte(masked[end]):
        return None
    return end


def _sql_keyword_after_whitespace(
    masked: bytes,
    cursor: int,
    keyword: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    next_cursor = _skip_sql_whitespace(
        masked, cursor, check_deadline=check_deadline
    )
    if next_cursor == cursor:
        return None
    return _sql_keyword_end(masked, next_cursor, keyword)


def _unique_deferrability_end(
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    not_end = _sql_keyword_end(masked, cursor, b"NOT")
    if not_end is not None:
        deferrable_end = _sql_keyword_after_whitespace(
            masked, not_end, b"DEFERRABLE", check_deadline=check_deadline
        )
        if deferrable_end is not None:
            return deferrable_end

    deferrable_end = _sql_keyword_end(masked, cursor, b"DEFERRABLE")
    if deferrable_end is not None:
        return deferrable_end

    initially_end = _sql_keyword_end(masked, cursor, b"INITIALLY")
    if initially_end is None:
        return None
    return _sql_keyword_after_whitespace(
        masked, initially_end, b"DEFERRED", check_deadline=check_deadline
    ) or _sql_keyword_after_whitespace(
        masked, initially_end, b"IMMEDIATE", check_deadline=check_deadline
    )


def _unique_using_index_end(
    source: bytes,
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    using_end = _sql_keyword_end(masked, cursor, b"USING")
    if using_end is None:
        return None
    index_end = _sql_keyword_after_whitespace(
        masked, using_end, b"INDEX", check_deadline=check_deadline
    )
    if index_end is None:
        return None
    next_cursor = _skip_sql_whitespace(
        masked, index_end, check_deadline=check_deadline
    )
    if next_cursor == index_end:
        return None
    tablespace_end = _sql_keyword_end(masked, next_cursor, b"TABLESPACE")
    if tablespace_end is not None:
        name_start = _skip_sql_trivia(
            source, tablespace_end, check_deadline=check_deadline
        )
        if name_start is None or name_start == tablespace_end:
            return None
        identifier = _read_sql_identifier(
            source, name_start, check_deadline=check_deadline
        )
        return None if identifier is None else identifier[1]

    identifier = _read_qualified_sql_identifier_details(
        source, next_cursor, check_deadline=check_deadline
    )
    return None if identifier is None else identifier[2]


def _using_index_attachment_end(
    masked: bytes,
    cursor: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """Return the INDEX keyword end for an attachment, excluding TABLESPACE."""
    using_end = _sql_keyword_after_whitespace(
        masked, cursor, b"USING", check_deadline=check_deadline
    )
    if using_end is None:
        return None
    index_end = _sql_keyword_after_whitespace(
        masked, using_end, b"INDEX", check_deadline=check_deadline
    )
    if index_end is None:
        return None
    after_index = _skip_sql_whitespace(
        masked, index_end, check_deadline=check_deadline
    )
    if after_index > index_end and _sql_keyword_end(
        masked, after_index, b"TABLESPACE"
    ) is not None:
        return None
    return index_end


def _iter_index_attachment_spans(
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> Iterator[tuple[int, int]]:
    """Find UNIQUE/PRIMARY KEY USING INDEX without regex whitespace scans."""
    for prefix in _INDEX_ATTACHMENT_PREFIX_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        cursor = prefix.end()
        if prefix.group().upper() == b"PRIMARY":
            key_end = _sql_keyword_after_whitespace(
                masked, cursor, b"KEY", check_deadline=check_deadline
            )
            if key_end is None:
                continue
            cursor = key_end
        index_end = _using_index_attachment_end(
            masked, cursor, check_deadline=check_deadline
        )
        if index_end is not None:
            yield prefix.start(), index_end


def _unique_constraint_end(
    source: bytes,
    masked: bytes,
    keyword_end: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """Return the full UNIQUE constraint span end, including its index and
    deferrability options, or ``None`` for an unbalanced option list."""
    cursor = keyword_end
    included_end = keyword_end
    cursor = _skip_sql_whitespace(
        masked, cursor, check_deadline=check_deadline
    )
    nulls = _UNIQUE_NULLS_WORD_RE.match(masked, cursor)
    if nulls is not None:
        option_cursor = _skip_sql_whitespace(
            masked, nulls.end(), check_deadline=check_deadline
        )
        nulls_end = None
        if option_cursor > nulls.end():
            not_word = _UNIQUE_NOT_WORD_RE.match(masked, option_cursor)
            if not_word is None:
                distinct = _UNIQUE_DISTINCT_WORD_RE.match(masked, option_cursor)
            else:
                distinct_cursor = _skip_sql_whitespace(
                    masked, not_word.end(), check_deadline=check_deadline
                )
                distinct = (
                    _UNIQUE_DISTINCT_WORD_RE.match(masked, distinct_cursor)
                    if distinct_cursor > not_word.end()
                    else None
                )
            if distinct is not None:
                nulls_end = distinct.end()
        if nulls_end is not None:
            cursor = nulls_end
            included_end = cursor

    cursor = _skip_sql_whitespace(
        masked, cursor, check_deadline=check_deadline
    )
    if cursor < len(masked) and masked[cursor] == _OPEN_PAREN:
        close = _find_matching_close_paren(
            masked, cursor, check_deadline=check_deadline
        )
        if close is None:
            return None
        cursor = close + 1
        included_end = cursor

    while True:
        cursor = _skip_sql_whitespace(
            masked, cursor, check_deadline=check_deadline
        )
        option_start = cursor
        using_index_end = _unique_using_index_end(
            source,
            masked,
            option_start,
            check_deadline=check_deadline,
        )
        if using_index_end is not None:
            cursor = using_index_end
            included_end = cursor
            continue
        deferrability_end = _unique_deferrability_end(
            masked, option_start, check_deadline=check_deadline
        )
        if deferrability_end is not None:
            cursor = deferrability_end
            included_end = cursor
            continue
        include_end = _sql_keyword_end(masked, option_start, b"INCLUDE")
        if include_end is not None:
            cursor = include_end
            cursor = _skip_sql_whitespace(
                masked, cursor, check_deadline=check_deadline
            )
            if cursor >= len(masked) or masked[cursor] != _OPEN_PAREN:
                return None
            close = _find_matching_close_paren(
                masked, cursor, check_deadline=check_deadline
            )
            if close is None:
                return None
            cursor = close + 1
            included_end = cursor
            continue
        with_end = _sql_keyword_end(masked, option_start, b"WITH")
        if with_end is not None:
            cursor = with_end
            cursor = _skip_sql_whitespace(
                masked, cursor, check_deadline=check_deadline
            )
            if cursor >= len(masked) or masked[cursor] != _OPEN_PAREN:
                return None
            close = _find_matching_close_paren(
                masked, cursor, check_deadline=check_deadline
            )
            if close is None:
                return None
            cursor = close + 1
            included_end = cursor
            continue
        return included_end


def _normalize_sql_identifier(token: bytes) -> bytes:
    """Normalize one parsed unqualified SQL identifier for source-local
    index linking. Unquoted names fold to lower case; quoted names preserve
    case and unescape doubled quotes."""
    if token.startswith(b'"') and token.endswith(b'"'):
        return token[1:-1].replace(b'""', b'"')
    return token.lower()


def _skip_sql_trivia(
    source: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """Advance past SQL whitespace, line comments and nested block comments."""
    next_deadline_check = pos
    if check_deadline is not None:
        check_deadline()
        next_deadline_check = pos + 4096

    while pos < len(source):
        if check_deadline is not None and pos >= next_deadline_check:
            check_deadline()
            next_deadline_check = pos + 4096
        if source[pos] in b" \t\r\n\f\v":
            pos += 1
            continue
        if source.startswith(b"--", pos):
            if check_deadline is None:
                newline = source.find(b"\n", pos + 2)
                if newline < 0:
                    return len(source)
                pos = newline + 1
                continue
            cursor = pos + 2
            while cursor < len(source) and source[cursor] != ord("\n"):
                if cursor >= next_deadline_check:
                    check_deadline()
                    next_deadline_check = cursor + 4096
                cursor += 1
            if cursor >= len(source):
                return len(source)
            pos = cursor + 1
            continue
        if source.startswith(b"/*", pos):
            depth = 1
            cursor = pos + 2
            while cursor < len(source) and depth:
                if (
                    check_deadline is not None
                    and cursor >= next_deadline_check
                ):
                    check_deadline()
                    next_deadline_check = cursor + 4096
                if source.startswith(b"/*", cursor):
                    depth += 1
                    cursor += 2
                elif source.startswith(b"*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            if depth:
                return None
            pos = cursor
            continue
        break
    return pos


def _read_sql_identifier(
    source: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes, int] | None:
    """Read one ASCII or double-quoted SQL identifier after trivia."""
    pos = _skip_sql_trivia(source, pos, check_deadline=check_deadline)
    if pos is None or pos >= len(source):
        return None
    if source[pos] == ord('"'):
        cursor = pos + 1
        next_deadline_check = cursor + 4096
        while cursor < len(source):
            if check_deadline is not None and cursor >= next_deadline_check:
                check_deadline()
                next_deadline_check = cursor + 4096
            if source[cursor] == ord('"'):
                if cursor + 1 < len(source) and source[cursor + 1] == ord('"'):
                    cursor += 2
                    continue
                return source[pos : cursor + 1], cursor + 1
            cursor += 1
        return None
    if check_deadline is not None:
        first = source[pos]
        if not (
            first == ord("_")
            or ord("A") <= first <= ord("Z")
            or ord("a") <= first <= ord("z")
        ):
            return None
        cursor = pos + 1
        next_deadline_check = cursor + 4096
        while cursor < len(source):
            if cursor >= next_deadline_check:
                check_deadline()
                next_deadline_check = cursor + 4096
            byte = source[cursor]
            if not (
                byte == ord("_")
                or byte == ord("$")
                or ord("0") <= byte <= ord("9")
                or ord("A") <= byte <= ord("Z")
                or ord("a") <= byte <= ord("z")
            ):
                break
            cursor += 1
        return source[pos:cursor], cursor
    match = re.match(rb"[A-Za-z_][A-Za-z0-9_$]*", source[pos:])
    if match is None:
        return None
    return match.group(0), pos + match.end()


def _read_sql_single_quoted_string(
    source: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes, int] | None:
    """Read a standard SQL single-quoted string, decoding doubled quotes."""
    pos = _skip_sql_trivia(source, pos, check_deadline=check_deadline)
    if pos is None or pos >= len(source) or source[pos] != ord("'"):
        return None
    value = bytearray()
    cursor = pos + 1
    next_deadline_check = cursor + 4096
    while cursor < len(source):
        if check_deadline is not None and cursor >= next_deadline_check:
            check_deadline()
            next_deadline_check = cursor + 4096
        byte = source[cursor]
        if byte == ord("'"):
            if cursor + 1 < len(source) and source[cursor + 1] == ord("'"):
                value.append(byte)
                cursor += 2
                continue
            return bytes(value), cursor + 1
        value.append(byte)
        cursor += 1
    return None


def _set_schema_value(
    source: bytes,
    masked: bytes,
    value_start: int | None,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bool, bytes | None]:
    """Return whether an offset is a SET SCHEMA value and its static name."""
    if check_deadline is not None:
        check_deadline()
    if value_start is None or re.search(
        rb"\bSCHEMA\s*$", masked[:value_start], re.IGNORECASE
    ) is None:
        return False, None
    if check_deadline is not None:
        check_deadline()
    string_value = _read_sql_single_quoted_string(
        source, value_start, check_deadline=check_deadline
    )
    if string_value is None:
        # PostgreSQL's SET SCHEMA grammar takes a string literal. A bare
        # identifier is not a schema name; leave malformed or unsupported
        # forms unresolved instead of treating them as valid evidence.
        return True, None
    name, value_end = string_value
    statement_end = masked.find(b";", value_end)
    if statement_end < 0:
        statement_end = len(masked)
    trailing = _skip_sql_trivia(
        source, value_end, check_deadline=check_deadline
    )
    if trailing is None or trailing < statement_end:
        return True, None
    return True, name


def _read_search_path_schemas(
    source: bytes,
    masked: bytes,
    value_start: int | None,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes, ...] | None:
    """Read a static comma-separated search_path value.

    Values involving expressions or string-form GUC syntax are deliberately
    unresolved. Callers must treat those as unknown instead of guessing what
    schema PostgreSQL will use.
    """
    if value_start is None:
        return None
    statement_end = masked.find(b";", value_start)
    if statement_end < 0:
        statement_end = len(masked)
    cursor = value_start
    schemas: list[bytes] = []
    while True:
        cursor = _skip_sql_trivia(source, cursor, check_deadline=check_deadline)
        if cursor is None or cursor >= statement_end:
            break
        if source[cursor : cursor + 5].lower() == b"$user":
            item = (b"$user", cursor + 5)
        else:
            item = _read_sql_identifier(
                source, cursor, check_deadline=check_deadline
            )
        if item is None:
            return None
        schemas.append(_normalize_sql_identifier(item[0]))
        cursor = _skip_sql_trivia(
            source, item[1], check_deadline=check_deadline
        )
        if cursor is None or cursor >= statement_end:
            break
        if masked[cursor : cursor + 1] != b",":
            return None
        cursor += 1
    return tuple(schemas) if schemas else None


def _search_path_schemas_may_create_in_temp_schema(
    schemas: tuple[bytes, ...] | None, known_schemas: frozenset[bytes]
) -> bool:
    """Resolve a path's possible creation schema from source-proven schemas."""
    if schemas is None:
        return True
    for schema in schemas:
        if _is_temporary_schema(schema):
            return True
        if schema in known_schemas:
            return False
    return False


def _schema_name_created_by_statement(
    source: bytes, masked: bytes, statement: re.Match[bytes]
) -> bytes | None:
    cursor = statement.end()
    keyword = _next_sql_keyword(source, masked, cursor)
    if_not_exists = False
    if keyword is not None and keyword[0] == b"IF":
        not_keyword = _next_sql_keyword(source, masked, keyword[1])
        exists_keyword = (
            None
            if not_keyword is None or not_keyword[0] != b"NOT"
            else _next_sql_keyword(source, masked, not_keyword[1])
        )
        if exists_keyword is None or exists_keyword[0] != b"EXISTS":
            return None
        if_not_exists = True
        cursor = exists_keyword[1]
    token = _read_sql_identifier(source, cursor)
    if token is None:
        return None
    if if_not_exists:
        # IF NOT EXISTS can leave a pre-existing schema and its privileges
        # untouched. It does not prove this session has USAGE on the schema.
        return None
    if not token[0].startswith(b'"') and _normalize_sql_identifier(
        token[0]
    ).lower() == b"authorization":
        return None
    trailing = _next_sql_keyword(source, masked, token[1])
    if trailing is not None and trailing[0] == b"AUTHORIZATION":
        return None
    return _normalize_sql_identifier(token[0])


def _schema_names_dropped_by_statement(
    source: bytes, masked: bytes, statement: re.Match[bytes]
) -> tuple[bytes, ...] | None:
    cursor = statement.end()
    keyword = _next_sql_keyword(source, masked, cursor)
    if keyword is not None and keyword[0] == b"IF":
        exists_keyword = _next_sql_keyword(source, masked, keyword[1])
        if exists_keyword is None or exists_keyword[0] != b"EXISTS":
            return None
        cursor = exists_keyword[1]
    names: list[bytes] = []
    while True:
        token = _read_sql_identifier(source, cursor)
        if token is None:
            return None
        raw_name, cursor = token
        names.append(_normalize_sql_identifier(raw_name))
        cursor = _skip_sql_trivia(source, cursor)
        if cursor is None or masked[cursor : cursor + 1] != b",":
            break
        cursor += 1
    return tuple(names)


def _schema_renamed_by_statement(
    source: bytes, masked: bytes, statement: re.Match[bytes]
) -> tuple[bytes, bytes] | None:
    old = _read_sql_identifier(source, statement.end())
    if old is None:
        return None
    rename = _next_sql_keyword(source, masked, old[1])
    if rename is None or rename[0] != b"RENAME":
        return None
    to = _next_sql_keyword(source, masked, rename[1])
    if to is None or to[0] != b"TO":
        return None
    new = _read_sql_identifier(source, to[1])
    if new is None:
        return None
    return _normalize_sql_identifier(old[0]), _normalize_sql_identifier(new[0])


def _schema_altered_by_statement(
    source: bytes, masked: bytes, statement: re.Match[bytes]
) -> bytes | None:
    schema = _read_sql_identifier(source, statement.end())
    return None if schema is None else _normalize_sql_identifier(schema[0])


def _schemas_with_revoked_usage_by_statement(
    source: bytes, masked: bytes, statement_start: int, statement_end: int
) -> tuple[tuple[bytes, ...], tuple[bytes, ...]] | None:
    """Read schema targets where REVOKE may remove the session's USAGE."""
    first = _next_sql_keyword(source, masked, statement_start)
    if first is None or first[0] != b"REVOKE":
        return ((), ())
    cursor = first[1]
    grant = _next_sql_keyword(source, masked, cursor)
    if grant is not None and grant[0] == b"GRANT":
        option = _next_sql_keyword(source, masked, grant[1])
        for_keyword = None
        if option is not None and option[0] == b"OPTION":
            for_keyword = _next_sql_keyword(source, masked, option[1])
        if for_keyword is not None and for_keyword[0] == b"FOR":
            # Revoking a grant option can cascade to downstream grantees, but
            # it does not remove the ordinary USAGE privilege from this role.
            return ((), ())
    privileges: set[bytes] = set()
    while True:
        keyword = _next_sql_keyword(source, masked, cursor)
        if keyword is None or keyword[1] > statement_end:
            return ((), ())
        if keyword[0] == b"ON":
            if not privileges.intersection({b"USAGE", b"ALL"}):
                return ((), ())
            object_kind = _next_sql_keyword(source, masked, keyword[1])
            if object_kind is None or object_kind[1] > statement_end:
                return None
            if object_kind[0] != b"SCHEMA":
                return ((), ())
            cursor = object_kind[1]
            schemas: list[bytes] = []
            while True:
                token = _read_sql_identifier(source, cursor)
                if token is None:
                    return None
                schemas.append(_normalize_sql_identifier(token[0]))
                after_name = _skip_sql_trivia(source, token[1])
                if after_name is None or after_name >= statement_end:
                    return None
                if masked[after_name : after_name + 1] != b",":
                    from_keyword = _next_sql_keyword(source, masked, after_name)
                    if from_keyword is None or from_keyword[0] != b"FROM":
                        return None
                    cursor = from_keyword[1]
                    grantees: list[bytes] = []
                    while True:
                        grantee_keyword = _next_sql_keyword(source, masked, cursor)
                        if grantee_keyword is not None and grantee_keyword[0] == b"GROUP":
                            cursor = grantee_keyword[1]
                        grantee = _read_sql_identifier(source, cursor)
                        if grantee is None:
                            return None
                        grantees.append(_normalize_sql_identifier(grantee[0]))
                        after_grantee = _skip_sql_trivia(source, grantee[1])
                        if (
                            after_grantee is None
                            or after_grantee >= statement_end
                            or masked[after_grantee : after_grantee + 1] != b","
                        ):
                            return tuple(schemas), tuple(grantees)
                        cursor = after_grantee + 1
                cursor = after_name + 1
        privileges.add(keyword[0])
        cursor = keyword[1]


def _role_context_change_events(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, str, bytes | None], ...]:
    """Find session-scoped and transaction-local role identity changes."""
    events: list[tuple[int, str, bytes | None]] = []
    for start, end in _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        if check_deadline is not None:
            check_deadline()
        first = _next_sql_keyword(
            source, masked, start, check_deadline=check_deadline
        )
        if first is None or first[1] > end:
            continue
        if first[0] == b"RESET":
            words = tuple(
                _iter_sql_keywords(
                    source,
                    masked,
                    first[1],
                    end,
                    check_deadline=check_deadline,
                )
            )
            if words and (
                words[0][0] == b"ROLE"
                or (
                    words[0][0] == b"SESSION"
                    and len(words) > 1
                    and words[1][0] == b"AUTHORIZATION"
                )
            ):
                events.append((start, "session", None))
            continue
        if first[0] != b"SET":
            continue
        words = tuple(
            _iter_sql_keywords(
                source,
                masked,
                first[1],
                end,
                check_deadline=check_deadline,
            )
        )
        if not words:
            continue
        scope = "session"
        command = words
        if words[0][0] == b"LOCAL":
            scope = "local"
            command = words[1:]
        elif (
            words[0][0] == b"SESSION"
            and len(words) > 1
            and words[1][0] == b"ROLE"
        ):
            command = words[1:]
        if command and command[0][0] == b"ROLE":
            role_keyword = command[0]
        elif (
            len(command) > 1
            and command[0][0] == b"SESSION"
            and command[1][0] == b"AUTHORIZATION"
        ):
            role_keyword = command[1]
        else:
            continue
        role = _read_sql_identifier(
            source, role_keyword[1], check_deadline=check_deadline
        )
        role_name = None if role is None else _normalize_sql_identifier(role[0])
        if role_name in {b"none", b"default", b"current_user", b"current_role", b"session_user"}:
            role_name = None
        events.append((start, scope, role_name))
    return tuple(events)


def _role_membership_change_offsets(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[int, ...]:
    """Find role membership changes that can alter inherited schema USAGE."""
    offsets: list[int] = []
    for start, end in _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        if check_deadline is not None:
            check_deadline()
        first = _next_sql_keyword(
            source, masked, start, check_deadline=check_deadline
        )
        if first is None or first[1] > end:
            continue
        keyword = first[0]
        if keyword in {b"GRANT", b"REVOKE"}:
            # Role-membership GRANT/REVOKE has no ON clause. Object grants are
            # handled separately; either can change inherited schema USAGE.
            if not any(
                candidate[0] == b"ON"
                for candidate in _iter_sql_keywords(
                    source,
                    masked,
                    first[1],
                    end,
                    check_deadline=check_deadline,
                )
            ):
                offsets.append(start)
    return tuple(offsets)


def _iter_sql_keywords(
    source: bytes,
    masked: bytes,
    start: int,
    end: int,
    *,
    check_deadline: Remaining | None = None,
) -> Iterator[tuple[bytes, int]]:
    cursor = start
    while True:
        if check_deadline is not None:
            check_deadline()
        keyword = _next_sql_keyword(
            source, masked, cursor, check_deadline=check_deadline
        )
        if keyword is None or keyword[1] > end:
            return
        yield keyword
        cursor = keyword[1]


def _transaction_control_events(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, str], ...]:
    """Return transaction boundaries relevant to source-known schema facts."""
    events: list[tuple[int, str]] = []
    for start, end in _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        if check_deadline is not None:
            check_deadline()
        first = _next_sql_keyword(
            source, masked, start, check_deadline=check_deadline
        )
        if first is None or first[1] > end:
            continue
        keyword = first[0]
        second = _next_sql_keyword(
            source, masked, first[1], check_deadline=check_deadline
        )
        if keyword == b"BEGIN" or (
            keyword == b"START" and second is not None and second[0] == b"TRANSACTION"
        ):
            events.append((start, "begin"))
            continue
        if keyword == b"SAVEPOINT" or keyword == b"RELEASE":
            events.append((start, "uncertain-transaction"))
            continue
        if keyword == b"PREPARE" and second is not None and second[0] == b"TRANSACTION":
            events.append((start, "prepared"))
            continue
        if keyword not in {b"COMMIT", b"END", b"ABORT", b"ROLLBACK"}:
            continue
        suffix = second
        if suffix is not None and suffix[0] in {b"WORK", b"TRANSACTION"}:
            suffix = _next_sql_keyword(
                source, masked, suffix[1], check_deadline=check_deadline
            )
        if keyword == b"ROLLBACK" and suffix is not None and suffix[0] == b"TO":
            events.append((start, "uncertain-transaction"))
            continue
        chained = False
        if suffix is not None and suffix[0] == b"AND":
            chain = _next_sql_keyword(
                source, masked, suffix[1], check_deadline=check_deadline
            )
            no_chain = False
            if chain is not None and chain[0] == b"NO":
                no_chain = True
                chain = _next_sql_keyword(
                    source, masked, chain[1], check_deadline=check_deadline
                )
            chained = chain is not None and chain[0] == b"CHAIN" and not no_chain
        prefix = "rollback" if keyword in {b"ABORT", b"ROLLBACK"} else "commit"
        events.append((start, f"{prefix}-chain" if chained else prefix))
    return tuple(events)


def _read_qualified_sql_identifier_details(
    source: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes | None, bytes, int] | None:
    first = _read_sql_identifier(source, pos, check_deadline=check_deadline)
    if first is None:
        return None
    first_token, cursor = first
    after_first = _skip_sql_trivia(
        source, cursor, check_deadline=check_deadline
    )
    if after_first is None:
        return None
    if after_first < len(source) and source[after_first] == ord('.'):
        second = _read_sql_identifier(
            source, after_first + 1, check_deadline=check_deadline
        )
        if second is None:
            return None
        second_token, second_end = second
        return (
            _normalize_sql_identifier(first_token),
            _normalize_sql_identifier(second_token),
            second_end,
        )
    return None, _normalize_sql_identifier(first_token), cursor


def _read_create_index_name_details(
    source: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes | None, bytes | None, int] | None:
    """Read a CREATE INDEX name and its end, including optional modifiers."""
    name_pos = pos
    word = _read_sql_identifier(source, pos, check_deadline=check_deadline)
    if word is None:
        return None
    raw_word, cursor = word
    if not raw_word.startswith(b'"') and raw_word.upper() == b"CONCURRENTLY":
        name_pos = cursor
        word = _read_sql_identifier(source, cursor, check_deadline=check_deadline)
        if word is None:
            return None
        raw_word, cursor = word
    if not raw_word.startswith(b'"') and raw_word.upper() == b"IF":
        not_word = _read_sql_identifier(
            source, cursor, check_deadline=check_deadline
        )
        if not_word is None or not_word[0].upper() != b"NOT":
            return None
        exists_word = _read_sql_identifier(
            source, not_word[1], check_deadline=check_deadline
        )
        if exists_word is None or exists_word[0].upper() != b"EXISTS":
            return None
        cursor = exists_word[1]
        name_pos = cursor
    name = _read_sql_identifier(source, name_pos, check_deadline=check_deadline)
    if name is not None and not name[0].startswith(b'"') and name[0].upper() == b"ON":
        # PostgreSQL permits omitting the index name. The next token is the
        # ON clause, and PostgreSQL generates an identity that cannot be
        # derived safely from this local syntax alone.
        return None, None, name_pos
    return _read_qualified_sql_identifier_details(
        source, name_pos, check_deadline=check_deadline
    )


def _read_created_index_identity(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes | None, bytes | None] | None:
    parsed = _read_create_index_name_details(
        source, pos, check_deadline=check_deadline
    )
    if parsed is None:
        return None
    index_schema, index_name, index_end = parsed
    on_clause = _CREATE_INDEX_ON_RE.match(masked, index_end)
    if on_clause is None:
        return index_schema, index_name
    table = _read_qualified_sql_identifier_details(
        source, on_clause.end(), check_deadline=check_deadline
    )
    if index_schema is None and table is not None:
        index_schema = table[0]
    return index_schema, index_name


def _attachment_index_reference(
    source: bytes,
    masked: bytes,
    attachment_start: int,
    attachment_end: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes | None, bytes] | None:
    reference = _read_qualified_sql_identifier_details(
        source, attachment_end, check_deadline=check_deadline
    )
    if reference is None:
        return None
    schema, name, _ = reference
    if schema is not None:
        return schema, name
    statement_start = masked.rfind(b";", 0, attachment_start) + 1
    alter = None
    for match in _ALTER_TABLE_RE.finditer(
        masked, statement_start, attachment_start
    ):
        if check_deadline is not None:
            check_deadline()
        alter = match
    if alter is not None:
        table = _read_alter_table_identity(
            source,
            masked,
            alter.end(),
            check_deadline=check_deadline,
        )
        if table is not None:
            table_schema, _ = table[0]
            if table_schema is not None:
                return table_schema, name
    return None, name


def _table_key_from_match(match: re.Match[bytes]) -> tuple[bytes | None, bytes]:
    schema = match.group("schema")
    return (
        None if schema is None else _normalize_sql_identifier(schema),
        _normalize_sql_identifier(match.group("table")),
    )


def _create_table_column_list(
    masked: bytes,
    table_match: re.Match[bytes],
    *,
    check_deadline: Remaining | None = None,
) -> tuple[re.Match[bytes], int, int] | None:
    """Return this statement's parenthesized CREATE TABLE column list.

    ``CREATE TABLE AS`` has no declared column types. Searching for the next
    opening parenthesis can cross its semicolon and misattribute a later table's
    columns to the earlier relation, so the header and matching close must both
    stay inside this statement.
    """
    statement_end = masked.find(b";", table_match.end())
    if statement_end < 0:
        statement_end = len(masked)
    table_name = _TABLE_NAME_AFTER_CREATE_RE.match(
        masked, table_match.end(), statement_end
    )
    if table_name is None:
        return None
    table_open = table_name.end()
    while table_open < statement_end and masked[table_open] in b" \t\r\n\f\v":
        table_open += 1
    if table_open >= statement_end or masked[table_open] != _OPEN_PAREN:
        return None
    table_close = _find_matching_close_paren(
        masked, table_open, check_deadline=check_deadline
    )
    if table_close is None or table_close >= statement_end:
        return None
    return table_name, table_open, table_close


def _type_name_from_match(match: re.Match[bytes]) -> bytes:
    name = _normalize_sql_identifier(match.group("type"))
    schema = match.group("type_schema")
    if schema is not None:
        name = _normalize_sql_identifier(schema) + b"." + name
    modifier = match.group("type_modifier")
    if modifier is not None:
        # Preserve typmods in the context identity. A bounded varchar cannot
        # safely accept the qualification sentinel when its length is shorter
        # than that sentinel; treating varchar(5) as bare varchar invents a
        # widening mutation that changes no accepted value.
        name += re.sub(rb"\s+", b"", modifier)
    if match.group("array_suffix"):
        # Array types have distinct literal parsing and equality rules. Keep
        # the suffix so they cannot inherit scalar string/integer widening.
        name += b"[]"
    return name


def _next_sql_keyword(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes, int] | None:
    """Read the next unquoted code keyword from a masked SQL source."""
    cursor = _skip_sql_trivia(source, pos, check_deadline=check_deadline)
    if cursor is None:
        return None
    if check_deadline is not None and cursor < len(masked):
        first = masked[cursor]
        if not (
            first == ord("_")
            or ord("A") <= first <= ord("Z")
            or ord("a") <= first <= ord("z")
        ):
            check_deadline()
            return None
        end = cursor + 1
        next_deadline_check = end + 4096
        while end < len(masked):
            if end >= next_deadline_check:
                check_deadline()
                next_deadline_check = end + 4096
            byte = masked[end]
            if not (
                byte == ord("_")
                or byte == ord("$")
                or ord("0") <= byte <= ord("9")
                or ord("A") <= byte <= ord("Z")
                or ord("a") <= byte <= ord("z")
            ):
                break
            end += 1
        check_deadline()
        return masked[cursor:end].upper(), end
    match = re.match(rb"[A-Za-z_][A-Za-z0-9_$]*", masked[cursor:])
    if check_deadline is not None:
        check_deadline()
    if match is None:
        return None
    return match.group(0).upper(), cursor + match.end()


def _create_table_is_if_not_exists(
    source: bytes,
    masked: bytes,
    table_match: re.Match[bytes],
    *,
    check_deadline: Remaining | None = None,
) -> bool:
    """Whether this CREATE TABLE declaration can be a no-op.

    When the relation already exists, PostgreSQL ignores the declared column
    list. Its types therefore cannot establish facts about the relation that
    will actually be used by later DDL.
    """
    keyword = _next_sql_keyword(
        source, masked, table_match.end(), check_deadline=check_deadline
    )
    if keyword is None or keyword[0] != b"IF":
        return False
    not_keyword = _next_sql_keyword(
        source, masked, keyword[1], check_deadline=check_deadline
    )
    if not_keyword is None or not_keyword[0] != b"NOT":
        return False
    exists_keyword = _next_sql_keyword(
        source, masked, not_keyword[1], check_deadline=check_deadline
    )
    return exists_keyword is not None and exists_keyword[0] == b"EXISTS"


def _read_alter_table_identity(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[bytes | None, bytes], int] | None:
    """Read ALTER TABLE's possibly quoted name using lexer-validated offsets."""
    cursor = pos
    keyword = _next_sql_keyword(
        source, masked, cursor, check_deadline=check_deadline
    )
    if keyword is not None and keyword[0] == b"IF":
        exists = _next_sql_keyword(
            source, masked, keyword[1], check_deadline=check_deadline
        )
        if exists is None or exists[0] != b"EXISTS":
            return None
        cursor = exists[1]
        keyword = _next_sql_keyword(
            source, masked, cursor, check_deadline=check_deadline
        )
    if keyword is not None and keyword[0] == b"ONLY":
        cursor = keyword[1]
    parsed = _read_qualified_sql_identifier_details(
        source, cursor, check_deadline=check_deadline
    )
    if parsed is None:
        return None
    schema, table, end = parsed
    # PostgreSQL accepts `name *` to include inheritance descendants. The
    # marker belongs to the table reference, so action parsing must start
    # after it rather than treating the first action as an unknown clause.
    after_table = _skip_sql_trivia(
        source, end, check_deadline=check_deadline
    )
    if (
        after_table is not None
        and after_table < len(masked)
        and masked[after_table] == ord("*")
    ):
        end = after_table + 1
    return (schema, table), end


def _read_create_table_identity(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes | None, bytes, int] | None:
    """Read CREATE TABLE's optional IF NOT EXISTS and possibly quoted name."""
    cursor = pos
    keyword = _next_sql_keyword(
        source, masked, cursor, check_deadline=check_deadline
    )
    if keyword is not None and keyword[0] == b"IF":
        not_keyword = _next_sql_keyword(
            source, masked, keyword[1], check_deadline=check_deadline
        )
        exists_keyword = (
            None
            if not_keyword is None or not_keyword[0] != b"NOT"
            else _next_sql_keyword(
                source, masked, not_keyword[1], check_deadline=check_deadline
            )
        )
        if exists_keyword is None or exists_keyword[0] != b"EXISTS":
            return None
        cursor = exists_keyword[1]
    return _read_qualified_sql_identifier_details(
        source, cursor, check_deadline=check_deadline
    )


def _read_action_identifier(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[bytes, int] | None:
    """Read a column identifier only when the masked source says it is code."""
    cursor = _skip_sql_trivia(source, pos, check_deadline=check_deadline)
    if cursor is None:
        return None
    parsed = _read_sql_identifier(source, cursor, check_deadline=check_deadline)
    if parsed is None:
        return None
    token, end = parsed
    # A quoted identifier is intentionally blank in the mask. An unquoted
    # identifier must remain visible there, which excludes strings/comments.
    if not token.startswith(b'"') and masked[cursor:end] != token:
        return None
    return _normalize_sql_identifier(token), end


def _collect_dropped_tables(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> frozenset[tuple[bytes | None, bytes]]:
    """Return table identities removed by DROP TABLE statements.

    The SQL context scan does not resolve ordered DDL. Any drop therefore
    invalidates all CREATE TABLE type facts for that table, including facts
    from later statements and other source files.
    """
    dropped: set[tuple[bytes | None, bytes]] = set()
    for statement in _DROP_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        cursor = statement.end()
        while True:
            if check_deadline is not None:
                check_deadline()
            table = _read_alter_table_identity(
                source, masked, cursor, check_deadline=check_deadline
            )
            if table is None:
                break
            table_key, table_end = table
            dropped.add(table_key)
            after_table = _skip_sql_trivia(
                source, table_end, check_deadline=check_deadline
            )
            if (
                after_table is None
                or after_table >= len(masked)
                or masked[after_table] != ord(",")
            ):
                break
            cursor = after_table + 1
    return frozenset(dropped)


def _collect_temporary_relation_facts(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
) -> tuple[
    frozenset[bytes],
    tuple[tuple[bytes, bytes], ...],
    tuple[tuple[bytes | None, bytes, bytes], ...],
]:
    """Collect temporary relation seeds and source-independent graph edges.

    Names may propagate through ALTER renames and views that read a temporary
    relation. Returning those edges separately lets context preparation
    resolve cross-file chains without rescanning every source per name.
    """
    names: set[bytes] = set()
    renames: set[tuple[bytes, bytes]] = set()
    view_dependencies: set[tuple[bytes | None, bytes, bytes]] = set()
    temporary_schema_events = _search_path_temporary_creation_events(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    for statement in _CREATE_TEMP_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_create_table_identity(
            source,
            masked,
            statement.end(),
            check_deadline=check_deadline,
        )
        if table is not None:
            names.add(table[1])
    for statement in _CREATE_TEMP_VIEW_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        relation = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if relation is not None:
            names.add(relation[1])
    for statement in _SELECT_INTO_TEMP_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if table is not None:
            names.add(table[1])
    for statement in _INTO_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if table is not None and _is_temporary_schema(table[0]):
            names.add(table[1])
    for statement in _CREATE_TABLE_DEFINITION_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_create_table_identity(
            source,
            masked,
            statement.end(),
            check_deadline=check_deadline,
        )
        if table is None:
            continue
        if _is_temporary_schema(table[0]) or (
            table[0] is None
            and _search_path_may_be_temporary_at(
                temporary_schema_events, statement.start()
            )
        ):
            names.add(table[1])
    for statement in _CREATE_VIEW_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        relation = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if relation is None:
            continue
        schema, view_name, relation_end = relation
        if _is_temporary_schema(relation[0]) or (
            relation[0] is None
            and _search_path_may_be_temporary_at(
                temporary_schema_events, statement.start()
            )
        ):
            names.add(view_name)
        view_end = _view_statement_end(masked, relation_end, check_deadline)
        if view_end < 0:
            view_end = len(masked)
        view_dependencies.update(
            (referenced_schema, referenced_name, view_name)
            for referenced_schema, referenced_name in _view_relation_references(
                source,
                masked,
                relation_end,
                view_end,
                check_deadline=check_deadline,
            )
        )
    for alter in _ALTER_RELATION_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_alter_table_identity(
            source, masked, alter.end(), check_deadline=check_deadline
        )
        if table is None:
            continue
        old_key, table_end = table
        if old_key[0] is not None and not _is_temporary_schema(old_key[0]):
            continue
        statement_end = masked.find(b";", table_end)
        if statement_end < 0:
            statement_end = len(masked)
        for start, end in _split_top_level(
            masked[table_end:statement_end], check_deadline=check_deadline
        ):
            action_start = table_end + start
            action_end = table_end + end
            change = re.match(
                rb"\s*RENAME\b", masked[action_start:action_end], re.I
            )
            if change is None:
                continue
            next_keyword = _next_sql_keyword(
                source, masked, action_start + change.end()
            )
            if next_keyword is None or next_keyword[0] != b"TO":
                continue
            new_name = _read_sql_identifier(source, next_keyword[1])
            if new_name is not None:
                renames.add(
                    (old_key[1], _normalize_sql_identifier(new_name[0]))
                )
    return (
        frozenset(names),
        tuple(sorted(renames)),
        tuple(sorted(view_dependencies, key=lambda edge: (edge[0] or b"", edge[1], edge[2]))),
    )


def _collect_temporary_relation_names(
    source: bytes,
    masked: bytes,
    known_temporary_names: Iterable[bytes] = (),
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> frozenset[bytes]:
    """Return temporary names reachable within this source and its context."""
    seeds, renames, view_dependencies = _collect_temporary_relation_facts(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    names = set(seeds)
    names.update(known_temporary_names)
    edges: dict[bytes, set[bytes]] = {}
    for old_name, new_name in renames:
        edges.setdefault(old_name, set()).add(new_name)
    for schema, referenced_name, view_name in view_dependencies:
        if schema is None or _is_temporary_schema(schema):
            edges.setdefault(referenced_name, set()).add(view_name)
    pending = list(names)
    while pending:
        old_name = pending.pop()
        for new_name in edges.get(old_name, ()):
            if new_name not in names:
                names.add(new_name)
                pending.append(new_name)
    return frozenset(names)


def _is_temporary_schema(schema: bytes | None) -> bool:
    return schema == b"pg_temp" or (
        schema is not None
        and re.fullmatch(rb"pg_temp_[0-9]+", schema) is not None
    )


def _view_statement_end(
    masked: bytes, start: int, check_deadline: Remaining | None
) -> int:
    if check_deadline is None:
        return masked.find(b";", start)
    cursor = start
    while cursor < len(masked):
        check_deadline()
        chunk_end = min(cursor + 4096, len(masked))
        found = masked.find(b";", cursor, chunk_end)
        if found >= 0:
            return found
        cursor = chunk_end
    return -1


def _view_relation_references(
    source: bytes,
    masked: bytes,
    query_start: int,
    statement_end: int,
    *,
    check_deadline: Remaining | None = None,
) -> frozenset[tuple[bytes | None, bytes]]:
    """Return relation identities found in FROM/JOIN relation positions."""

    def relation_after(start: int) -> tuple[
        tuple[bytes | None, bytes, int] | None, int | None
    ]:
        cursor = start
        keyword = _next_sql_keyword(
            source, masked, cursor, check_deadline=check_deadline
        )
        while keyword is not None and keyword[0] in {b"ONLY", b"LATERAL"}:
            cursor = keyword[1]
            keyword = _next_sql_keyword(
                source, masked, cursor, check_deadline=check_deadline
            )
        cursor = _skip_sql_trivia(
            source, cursor, check_deadline=check_deadline
        )
        if cursor is None:
            return None, None
        if masked[cursor : cursor + 1] == b"(":
            close = _find_matching_close_paren(
                masked, cursor, check_deadline=check_deadline
            )
            return None, None if close is None else close + 1
        parsed = _read_qualified_sql_identifier_details(
            source, cursor, check_deadline=check_deadline
        )
        return (None, None) if parsed is None else (parsed, parsed[2])

    def clauses() -> Iterator[re.Match[bytes]]:
        if check_deadline is None:
            yield from _FROM_OR_JOIN_RE.finditer(
                masked, query_start, statement_end
            )
            return
        chunk_start = query_start
        while chunk_start < statement_end:
            check_deadline()
            chunk_end = min(chunk_start + 4096, statement_end)
            search_end = min(chunk_end + 4, statement_end)
            for clause in _FROM_OR_JOIN_RE.finditer(
                masked, chunk_start, search_end
            ):
                if clause.start() >= chunk_end:
                    break
                yield clause
            chunk_start = chunk_end

    references: set[tuple[bytes | None, bytes]] = set()
    for clause in clauses():
        if check_deadline is not None:
            check_deadline()
        relation, cursor = relation_after(clause.end())
        if cursor is None:
            continue
        if relation is not None:
            schema, name, _ = relation
            references.add((schema, name))

        # Also inspect comma-separated relation lists at this FROM level.
        # Parenthesized subqueries and function arguments are skipped, and
        # clause keywords end the list so their expression commas are ignored.
        depth = 0
        next_deadline_check = cursor + 4096
        while cursor < statement_end:
            if check_deadline is not None and cursor >= next_deadline_check:
                check_deadline()
                next_deadline_check = cursor + 4096
            byte = masked[cursor]
            if source[cursor : cursor + 1] == b'"':
                identifier = _read_sql_identifier(
                    source, cursor, check_deadline=check_deadline
                )
                if identifier is not None:
                    cursor = identifier[1]
                    continue
            if byte == _OPEN_PAREN:
                depth += 1
            elif byte == _CLOSE_PAREN:
                depth = max(0, depth - 1)
            elif depth == 0:
                if (
                    byte == ord("_")
                    or ord("A") <= byte <= ord("Z")
                    or ord("a") <= byte <= ord("z")
                ):
                    token = _read_sql_identifier(
                        source, cursor, check_deadline=check_deadline
                    )
                    if token is not None:
                        token_word = _normalize_sql_identifier(token[0]).lower()
                        if token_word in _FROM_CLAUSE_END_KEYWORDS:
                            break
                        cursor = token[1]
                        continue
                if byte == _COMMA:
                    following, next_end = relation_after(cursor + 1)
                    if next_end is not None:
                        if following is None:
                            cursor = next_end
                            continue
                        next_schema, next_name, _ = following
                        references.add((next_schema, next_name))
                        cursor = next_end
                        continue
            cursor += 1
    if check_deadline is not None:
        check_deadline()
    return frozenset(references)


def _collect_relocated_tables(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> frozenset[tuple[bytes | None, bytes]]:
    """Return table identities changed by rename or schema-move DDL."""
    relocated: set[tuple[bytes | None, bytes]] = set()
    for alter in _ALTER_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_alter_table_identity(
            source, masked, alter.end(), check_deadline=check_deadline
        )
        if table is None:
            continue
        old_key, table_end = table
        statement_end = masked.find(b";", table_end)
        if statement_end < 0:
            statement_end = len(masked)
        actions = masked[table_end:statement_end]
        for start, end in _split_top_level(
            actions, check_deadline=check_deadline
        ):
            action_start = table_end + start
            action_end = table_end + end
            change = re.match(
                rb"\s*(RENAME|SET)\b", masked[action_start:action_end], re.I
            )
            if change is None:
                continue
            verb = change.group(1).upper()
            next_keyword = _next_sql_keyword(
                source,
                masked,
                action_start + change.end(),
                check_deadline=check_deadline,
            )
            if verb == b"RENAME":
                if next_keyword is None or next_keyword[0] != b"TO":
                    continue
                new_name = _read_sql_identifier(
                    source, next_keyword[1], check_deadline=check_deadline
                )
                if new_name is None:
                    continue
                relocated.add(old_key)
                relocated.add((old_key[0], _normalize_sql_identifier(new_name[0])))
            elif next_keyword is not None and next_keyword[0] == b"SCHEMA":
                new_schema = _read_sql_identifier(
                    source, next_keyword[1], check_deadline=check_deadline
                )
                if new_schema is None:
                    continue
                relocated.add(old_key)
                relocated.add(
                    (_normalize_sql_identifier(new_schema[0]), old_key[1])
                )
    return frozenset(relocated)


def _collect_altered_columns(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> frozenset[tuple[tuple[bytes | None, bytes], bytes]]:
    """Return columns whose final type cannot be inferred from CREATE TABLE.

    This scanner does not resolve ordered DDL. Type changes, column removal,
    addition, and rename therefore invalidate the affected names, including
    when the statement is in another source file.
    """
    altered_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    for alter in _ALTER_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_alter_table_identity(
            source, masked, alter.end(), check_deadline=check_deadline
        )
        if table is None:
            continue
        table_key, table_end = table
        statement_end = masked.find(b";", table_end)
        if statement_end < 0:
            statement_end = len(masked)
        actions = masked[table_end:statement_end]
        for action_start, action_end in _split_top_level(
            actions, check_deadline=check_deadline
        ):
            if check_deadline is not None:
                check_deadline()
            action_start += table_end
            action_end += table_end
            action = re.match(
                rb"\s*(ALTER|DROP|ADD|RENAME)\b", masked[action_start:action_end], re.I
            )
            if action is None:
                continue
            verb = action.group(1).upper()
            cursor = action_start + action.end()
            first_keyword = _next_sql_keyword(
                source, masked, cursor, check_deadline=check_deadline
            )
            if verb == b"DROP":
                if first_keyword is not None and first_keyword[0] == b"COLUMN":
                    cursor = first_keyword[1]
                elif first_keyword is not None and first_keyword[0] == b"CONSTRAINT":
                    continue
                optional = _next_sql_keyword(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if optional is not None and optional[0] == b"IF":
                    exists = _next_sql_keyword(
                        source, masked, optional[1], check_deadline=check_deadline
                    )
                    if exists is None or exists[0] != b"EXISTS":
                        continue
                    cursor = exists[1]
                after_optional = _next_sql_keyword(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if after_optional is not None and after_optional[0] == b"CONSTRAINT":
                    continue
                column = _read_action_identifier(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if column is not None:
                    altered_columns.add((table_key, column[0]))
            elif verb == b"ADD":
                if first_keyword is not None and first_keyword[0] == b"COLUMN":
                    cursor = first_keyword[1]
                optional = _next_sql_keyword(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if optional is not None and optional[0] == b"IF":
                    not_word = _next_sql_keyword(
                        source, masked, optional[1], check_deadline=check_deadline
                    )
                    exists = (
                        None
                        if not_word is None
                        else _next_sql_keyword(
                            source,
                            masked,
                            not_word[1],
                            check_deadline=check_deadline,
                        )
                    )
                    if (
                        not_word is None
                        or not_word[0] != b"NOT"
                        or exists is None
                        or exists[0] != b"EXISTS"
                    ):
                        continue
                    cursor = exists[1]
                next_keyword = _next_sql_keyword(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if next_keyword is not None and next_keyword[0] in {
                    b"CONSTRAINT", b"CHECK", b"FOREIGN", b"PRIMARY", b"UNIQUE", b"EXCLUDE"
                }:
                    continue
                column = _read_action_identifier(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if column is not None:
                    altered_columns.add((table_key, column[0]))
            elif verb == b"ALTER":
                if first_keyword is not None and first_keyword[0] == b"COLUMN":
                    cursor = first_keyword[1]
                optional = _next_sql_keyword(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if optional is not None and optional[0] == b"IF":
                    exists = _next_sql_keyword(
                        source, masked, optional[1], check_deadline=check_deadline
                    )
                    if exists is None or exists[0] != b"EXISTS":
                        continue
                    cursor = exists[1]
                column = _read_action_identifier(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if column is None:
                    continue
                type_keyword = _next_sql_keyword(
                    source, masked, column[1], check_deadline=check_deadline
                )
                changes_type = type_keyword is not None and type_keyword[0] == b"TYPE"
                if type_keyword is not None and type_keyword[0] == b"SET":
                    data_keyword = _next_sql_keyword(
                        source,
                        masked,
                        type_keyword[1],
                        check_deadline=check_deadline,
                    )
                    type_after_data = (
                        None
                        if data_keyword is None
                        else _next_sql_keyword(
                            source,
                            masked,
                            data_keyword[1],
                            check_deadline=check_deadline,
                        )
                    )
                    changes_type = (
                        data_keyword is not None
                        and data_keyword[0] == b"DATA"
                        and type_after_data is not None
                        and type_after_data[0] == b"TYPE"
                    )
                if changes_type:
                    altered_columns.add((table_key, column[0]))
            else:  # RENAME
                if first_keyword is not None and first_keyword[0] == b"COLUMN":
                    cursor = first_keyword[1]
                elif first_keyword is not None and first_keyword[0] in {
                    b"TO", b"CONSTRAINT"
                }:
                    continue
                old = _read_action_identifier(
                    source, masked, cursor, check_deadline=check_deadline
                )
                if old is None:
                    continue
                to_keyword = _next_sql_keyword(
                    source, masked, old[1], check_deadline=check_deadline
                )
                if to_keyword is None or to_keyword[0] != b"TO":
                    continue
                new = _read_action_identifier(
                    source, masked, to_keyword[1], check_deadline=check_deadline
                )
                if new is not None:
                    altered_columns.add((table_key, old[0]))
                    altered_columns.add((table_key, new[0]))
    return frozenset(altered_columns)


def _table_column_declarations(
    source: bytes,
    masked: bytes,
    table_key: tuple[bytes | None, bytes],
    table_open: int,
    table_close: int,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[
    frozenset[tuple[tuple[bytes | None, bytes], bytes, bytes]],
    frozenset[tuple[tuple[bytes | None, bytes], bytes]],
]:
    declared_types: set[tuple[tuple[bytes | None, bytes], bytes, bytes]] = set()
    collated: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    table_body = masked[table_open + 1 : table_close]
    for start, end in _split_top_level(
        table_body, check_deadline=check_deadline
    ):
        if check_deadline is not None:
            check_deadline()
        clause = table_body[start:end]
        column = _ENUM_COLUMN_DECL_RE.match(clause)
        if column is None:
            continue
        if (
            not column.group("column").startswith(b'"')
            and _normalize_sql_identifier(column.group("column")).upper()
            in {
                b"CONSTRAINT",
                b"PRIMARY",
                b"UNIQUE",
                b"CHECK",
                b"FOREIGN",
                b"EXCLUDE",
            }
        ):
            continue
        column_name = _normalize_sql_identifier(column.group("column"))
        declared_types.add((table_key, column_name, _type_name_from_match(column)))
        if _EXPLICIT_COLLATE_RE.search(clause) is not None:
            collated.add((table_key, column_name))
    return frozenset(declared_types), frozenset(collated)


def _collect_table_column_metadata(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[
    frozenset[tuple[tuple[bytes | None, bytes], bytes, bytes]],
    frozenset[tuple[tuple[bytes | None, bytes], bytes]],
]:
    declared_types: set[tuple[tuple[bytes | None, bytes], bytes, bytes]] = set()
    collated: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    for table_match in _CREATE_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _create_table_is_if_not_exists(
            source,
            masked,
            table_match,
            check_deadline=check_deadline,
        ):
            continue
        column_list = _create_table_column_list(
            masked, table_match, check_deadline=check_deadline
        )
        if column_list is None:
            continue
        table_name, table_open, table_close = column_list
        table_key = _table_key_from_match(table_name)
        table_types, table_collations = _table_column_declarations(
            source,
            masked,
            table_key,
            table_open,
            table_close,
            check_deadline=check_deadline,
        )
        declared_types.update(table_types)
        collated.update(table_collations)
    return frozenset(declared_types), frozenset(collated)


def _collect_declared_type_names(
    source: bytes,
    masked: bytes,
    known_temporary_types: Iterable[tuple[bytes, bytes]] = (),
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> frozenset[tuple[bytes | None, bytes]]:
    """Collect source-visible types plus renames reachable from temporary types."""
    names, renames = _collect_declared_type_facts(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    result = set(names)
    temporary_types = {
        (schema, name)
        for schema, name in names
        if schema is not None and _is_temporary_schema(schema)
    }
    temporary_types.update(known_temporary_types)
    explicit_edges: dict[tuple[bytes, bytes], set[bytes]] = {}
    unqualified_edges: dict[bytes, set[bytes]] = {}
    for schema, old_name, new_name in renames:
        if schema is None:
            unqualified_edges.setdefault(old_name, set()).add(new_name)
        else:
            explicit_edges.setdefault((schema, old_name), set()).add(new_name)
    pending = list(temporary_types)
    while pending:
        if check_deadline is not None:
            check_deadline()
        schema, old_name = pending.pop()
        new_names = set(explicit_edges.get((schema, old_name), ()))
        new_names.update(unqualified_edges.get(old_name, ()))
        for new_name in new_names:
            renamed = (schema, new_name)
            if renamed not in result:
                result.add(renamed)
                temporary_types.add(renamed)
                pending.append(renamed)
    return frozenset(result)


def _collect_declared_type_facts(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[
    frozenset[tuple[bytes | None, bytes]],
    tuple[tuple[bytes | None, bytes, bytes], ...],
]:
    """Collect declared types and possible temporary-type rename edges."""
    names: set[tuple[bytes | None, bytes]] = set()
    for pattern in (_CREATE_TYPE_STMT_RE, _CREATE_DOMAIN_STMT_RE):
        for statement in pattern.finditer(masked):
            if check_deadline is not None:
                check_deadline()
            parsed = _read_qualified_sql_identifier_details(
                source, statement.end(), check_deadline=check_deadline
            )
            if parsed is None:
                continue
            schema, name, _ = parsed
            names.add((schema, name))
    names.update(
        (b"pg_temp", name)
        for name in _unqualified_type_names_created_in_temp_schema(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    for statement in _CREATE_TABLE_DEFINITION_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        relation = _read_create_table_identity(
            source,
            masked,
            statement.end(),
            check_deadline=check_deadline,
        )
        if relation is not None:
            names.add((relation[0], relation[1]))
    for statement in _CREATE_VIEW_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        relation = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if relation is not None:
            names.add((relation[0], relation[1]))
    renames: set[tuple[bytes | None, bytes, bytes]] = set()
    for alter in _ALTER_TYPE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        relation = _read_qualified_sql_identifier_details(
            source, alter.end(), check_deadline=check_deadline
        )
        if relation is None:
            continue
        schema, name, relation_end = relation
        if schema is not None and not _is_temporary_schema(schema):
            old_types = set()
        else:
            old_types = {(schema, name)}
        rename = _next_sql_keyword(
            source, masked, relation_end, check_deadline=check_deadline
        )
        if rename is None or rename[0] != b"RENAME":
            continue
        to = _next_sql_keyword(
            source, masked, rename[1], check_deadline=check_deadline
        )
        if to is None or to[0] != b"TO":
            continue
        new_type = _read_sql_identifier(
            source, to[1], check_deadline=check_deadline
        )
        if new_type is None:
            continue
        normalized_new_name = _normalize_sql_identifier(new_type[0])
        for temp_schema, old_name in old_types:
            renames.add(
                (temp_schema, old_name, normalized_new_name)
            )
            # An explicit pg_temp-qualified ALTER is itself evidence that the
            # resulting type is temporary, even when its CREATE lives outside
            # this source inventory.
            if temp_schema is not None:
                names.add((temp_schema, normalized_new_name))
    return frozenset(names), tuple(sorted(renames, key=lambda edge: (edge[0] or b"", edge[1], edge[2])))


def _unqualified_type_names_created_in_temp_schema(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> frozenset[bytes]:
    """Find unqualified named types whose source-visible current schema may be temporary.

    An unknown connection default is not proof of a permanent current schema.
    Explicit path settings narrow that state; an unresolved writer restores the
    conservative possibility until a later static setting resolves it.
    """
    path_events = _search_path_temporary_creation_events(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    statements = sorted(
        (
            statement.start(),
            statement,
        )
        for pattern in (_CREATE_TYPE_STMT_RE, _CREATE_DOMAIN_STMT_RE)
        for statement in pattern.finditer(masked)
    )
    names: set[bytes] = set()
    for statement_offset, statement in statements:
        if check_deadline is not None:
            check_deadline()
        if not _search_path_may_be_temporary_at(path_events, statement_offset):
            continue
        parsed = _read_qualified_sql_identifier_details(
            source, statement.end(), check_deadline=check_deadline
        )
        if parsed is not None and parsed[0] is None:
            names.add(parsed[1])
    return frozenset(names)


def _search_path_temporary_creation_events(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, bool], ...]:
    """Track temporary creation possibility as path/schema facts change."""
    events: list[tuple[int, str, object]] = []
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    events.extend(
        (offset, "transaction", kind)
        for offset, kind in _transaction_control_events(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    events.extend(
        (offset, "role-change", (scope, role_name))
        for offset, scope, role_name in _role_context_change_events(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    for offset, value_start, untrusted_scope in _search_path_value_starts(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        is_schema, schema_name = _set_schema_value(
            source,
            masked,
            value_start,
            check_deadline=check_deadline,
        )
        if untrusted_scope:
            path_schemas = None
        elif is_schema:
            path_schemas = None if schema_name is None else (schema_name,)
        else:
            path_schemas = _read_search_path_schemas(
                source,
                masked,
                value_start,
                check_deadline=check_deadline,
            )
        events.append((offset, "path", path_schemas))
    events.extend(
        (offset, "unknown", None)
        for offset in (
            *_set_config_search_path_writes(
                source, masked, check_deadline=check_deadline
            ),
            *_dynamic_sql_execute_offsets(masked, dollar_body_ranges),
            *_search_path_reset_offsets(
                source,
                masked,
                dollar_body_ranges=dollar_body_ranges,
                check_deadline=check_deadline,
            ),
        )
    )
    events.extend(
        (offset, "clear-schemas", None)
        for offset in (
            *_dynamic_sql_execute_offsets(masked, dollar_body_ranges),
            *_role_membership_change_offsets(
                source,
                masked,
                dollar_body_ranges=dollar_body_ranges,
                check_deadline=check_deadline,
            ),
        )
    )
    for statement in _CREATE_SCHEMA_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _inside_any(dollar_body_ranges, statement.start()):
            events.append((statement.start(), "clear-schemas", None))
            continue
        schema = _schema_name_created_by_statement(source, masked, statement)
        if schema is not None:
            events.append((statement.start(), "add-schema", schema))
    for statement in _DROP_SCHEMA_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _inside_any(dollar_body_ranges, statement.start()):
            events.append((statement.start(), "clear-schemas", None))
            continue
        schemas = _schema_names_dropped_by_statement(source, masked, statement)
        events.append((statement.start(), "forget-schemas", schemas))
    for statement in _ALTER_SCHEMA_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _inside_any(dollar_body_ranges, statement.start()):
            events.append((statement.start(), "clear-schemas", None))
            continue
        rename = _schema_renamed_by_statement(source, masked, statement)
        if rename is not None:
            events.append((statement.start(), "rename-schema", rename))
        else:
            altered = _schema_altered_by_statement(source, masked, statement)
            events.append((statement.start(), "forget-schemas", None if altered is None else (altered,)))
    for statement_start, statement_end in _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        if check_deadline is not None:
            check_deadline()
        first = _next_sql_keyword(
            source, masked, statement_start, check_deadline=check_deadline
        )
        if first is None or first[0] != b"REVOKE":
            continue
        revocation = _schemas_with_revoked_usage_by_statement(
            source, masked, statement_start, statement_end
        )
        if revocation is None:
            events.append((statement_start, "clear-schemas", None))
        elif revocation[0]:
            events.append((statement_start, "revoke-schema-usage", revocation))
    events.sort(key=lambda event: event[0])

    known_schemas = {b"pg_catalog"}
    schema_owners: dict[bytes, bytes | None] = {}
    session_role_name: bytes | None = None
    current_role_name: bytes | None = None
    local_role_active = False
    current_path: tuple[bytes, ...] | None = None
    transaction_baseline: set[bytes] | None = None
    transaction_owner_baseline: dict[bytes, bytes | None] | None = None
    transaction_role_baseline: bytes | None = None
    transaction_path_baseline: tuple[bytes, ...] | None = None
    transaction_uncertain = False
    path_events: list[tuple[int, bool]] = []
    for offset, kind, value in events:
        if kind == "path":
            current_path = value  # type: ignore[assignment]
            # SET SCHEMA is an alias for SET search_path. Missing or
            # inaccessible schemas are silently ignored, so the setting does
            # not prove that its name is a usable path entry.
        elif kind == "unknown":
            current_path = None
        elif kind == "add-schema":
            schema_name = value  # type: ignore[assignment]
            known_schemas.add(schema_name)
            schema_owners[schema_name] = current_role_name
        elif kind == "forget-schemas":
            if value is None:
                known_schemas = {b"pg_catalog"}
                schema_owners.clear()
            else:
                forgotten = value  # type: ignore[assignment]
                known_schemas.difference_update(forgotten)
                for schema_name in forgotten:
                    schema_owners.pop(schema_name, None)
        elif kind == "rename-schema":
            old_name, new_name = value  # type: ignore[misc]
            if old_name in known_schemas:
                known_schemas.remove(old_name)
                known_schemas.add(new_name)
                schema_owners[new_name] = schema_owners.pop(old_name, None)
        elif kind == "revoke-schema-usage":
            schemas, grantees = value  # type: ignore[misc]
            sensitive_grantees = {
                b"current_user",
                b"current_role",
                b"session_user",
            }
            for schema_name in schemas:
                if schema_name == b"pg_catalog" or schema_name not in known_schemas:
                    continue
                owner = schema_owners.get(schema_name)
                if owner is None:
                    # PUBLIC is a separate grant target and cannot remove the
                    # creator's direct owner privilege. A named role may be
                    # the unknown creator, so keep that case conservative.
                    affects_owner = any(grantee != b"public" for grantee in grantees)
                else:
                    affects_owner = any(
                        grantee in sensitive_grantees or grantee == owner
                        for grantee in grantees
                    )
                if affects_owner:
                    known_schemas.discard(schema_name)
                    schema_owners.pop(schema_name, None)
        elif kind == "clear-schemas":
            known_schemas = {b"pg_catalog"}
            schema_owners.clear()
        elif kind == "role-change":
            scope, role_name = value  # type: ignore[misc]
            if scope == "local":
                # SET LOCAL has no effect outside an explicit transaction.
                if transaction_baseline is not None:
                    current_role_name = role_name
                    local_role_active = True
                    known_schemas = {b"pg_catalog"}
                    schema_owners.clear()
            else:
                session_role_name = role_name
                current_role_name = role_name
                local_role_active = False
                known_schemas = {b"pg_catalog"}
                schema_owners.clear()
        else:  # kind == "transaction": the last of the event kinds
            if value == "begin":
                if transaction_baseline is None:
                    transaction_baseline = set(known_schemas)
                    transaction_owner_baseline = dict(schema_owners)
                    transaction_role_baseline = session_role_name
                    transaction_path_baseline = current_path
                    transaction_uncertain = False
            elif value in {"rollback", "rollback-chain"}:
                if transaction_baseline is not None:
                    known_schemas = set(transaction_baseline)
                    schema_owners = dict(transaction_owner_baseline or {})
                    session_role_name = transaction_role_baseline
                    current_role_name = session_role_name
                    local_role_active = False
                    current_path = transaction_path_baseline
                transaction_baseline = (
                    set(known_schemas) if value == "rollback-chain" else None
                )
                transaction_owner_baseline = (
                    dict(schema_owners) if value == "rollback-chain" else None
                )
                transaction_role_baseline = (
                    session_role_name if value == "rollback-chain" else None
                )
                transaction_path_baseline = (
                    current_path if value == "rollback-chain" else None
                )
                transaction_uncertain = False
            elif value in {"commit", "commit-chain"}:
                if transaction_uncertain:
                    known_schemas = {b"pg_catalog"}
                    schema_owners.clear()
                if local_role_active:
                    # Transaction-local role changes expire at COMMIT. Schema
                    # facts accumulated under that role cannot certify the
                    # restored session role's access.
                    known_schemas = {b"pg_catalog"}
                    schema_owners.clear()
                current_role_name = session_role_name
                local_role_active = False
                transaction_baseline = (
                    set(known_schemas) if value == "commit-chain" else None
                )
                transaction_owner_baseline = (
                    dict(schema_owners) if value == "commit-chain" else None
                )
                transaction_role_baseline = (
                    session_role_name if value == "commit-chain" else None
                )
                transaction_path_baseline = (
                    current_path if value == "commit-chain" else None
                )
                transaction_uncertain = False
            elif value in {"uncertain-transaction", "prepared"}:
                known_schemas = {b"pg_catalog"}
                schema_owners.clear()
                current_path = None
                session_role_name = None
                current_role_name = None
                local_role_active = False
                if value == "uncertain-transaction":
                    transaction_uncertain = True
                else:
                    transaction_baseline = None
                    transaction_owner_baseline = None
                    transaction_role_baseline = None
                    transaction_path_baseline = None
                    transaction_uncertain = False
        path_events.append(
            (
                offset,
                _search_path_schemas_may_create_in_temp_schema(
                    current_path, frozenset(known_schemas)
                ),
            )
        )
    return tuple(path_events)


def _search_path_may_be_temporary_at(
    path_events: tuple[tuple[int, bool], ...], offset: int
) -> bool:
    """Return the latest source-known current-schema state before an offset."""
    may_be_temporary = True
    for event_offset, event_state in path_events:
        if event_offset >= offset:
            break
        may_be_temporary = event_state
    return may_be_temporary


def _search_path_value_starts(
    source: bytes,
    masked: bytes,
    *,
    configuration_ranges: tuple[tuple[int, int], ...] | None = None,
    transaction_ranges: tuple[tuple[int, int], ...] | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, int | None, bool], ...]:
    """Locate explicit search_path values, including quoted GUC names.

    ``None`` records ``SET search_path FROM CURRENT``: it changes the path,
    but its value comes from connection configuration and cannot be resolved
    from this source. The third field marks a setting that cannot establish
    session-path evidence: SET LOCAL, a SET inside an explicit transaction, or
    a SET found inside a dollar-quoted body.
    """
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    if configuration_ranges is None:
        configuration_ranges = _non_session_configuration_ranges(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    if transaction_ranges is None:
        transaction_ranges = _explicit_transaction_ranges(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    statement_ranges = _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    statement_starts = tuple(start for start, _ in statement_ranges)
    dollar_body_starts = tuple(start for start, _ in dollar_body_ranges)
    starts: list[tuple[int, int | None, bool]] = []
    for statement in _SET_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        body_index = bisect_right(dollar_body_starts, statement.start()) - 1
        inside_dollar_body = (
            body_index >= 0
            and dollar_body_ranges[body_index][0] <= statement.start()
            < dollar_body_ranges[body_index][1]
        )
        inside_configuration = any(
            start <= statement.start() < end for start, end in configuration_ranges
        )
        if inside_configuration and not inside_dollar_body:
            # Routine and role/database/system SET clauses change routine or
            # future-session configuration, not the migration session.
            continue
        if not inside_dollar_body:
            statement_index = bisect_right(statement_starts, statement.start()) - 1
            if statement_index < 0:
                continue
            first = _next_sql_keyword(
                source,
                masked,
                statement_ranges[statement_index][0],
                check_deadline=check_deadline,
            )
            if first is None or first[0] != b"SET":
                # UPDATE ... SET column=... and other SET clauses are not
                # session configuration statements.
                continue
        else:
            body_start = dollar_body_ranges[body_index][0]
            previous_semicolon = masked.rfind(b";", body_start, statement.start())
            statement_prefix = masked[previous_semicolon + 1 : statement.start()]
            if re.search(rb"\bUPDATE\b", statement_prefix, re.IGNORECASE):
                # A SET target inside UPDATE (including a PL/pgSQL body) is a
                # column assignment, not a session GUC statement.
                continue
        cursor = _skip_sql_trivia(
            source, statement.end(), check_deadline=check_deadline
        )
        if cursor is None:
            continue
        parameter = _read_sql_identifier(
            source, cursor, check_deadline=check_deadline
        )
        if parameter is None:
            continue
        untrusted_scope = False
        if (
            not parameter[0].startswith(b'"')
            and _normalize_sql_identifier(parameter[0]) in {b"local", b"session"}
        ):
            untrusted_scope = _normalize_sql_identifier(parameter[0]) == b"local"
            parameter = _read_sql_identifier(
                source, parameter[1], check_deadline=check_deadline
            )
            if parameter is None:
                continue
        parameter_name = _normalize_sql_identifier(parameter[0]).lower()
        cursor = _skip_sql_trivia(
            source, parameter[1], check_deadline=check_deadline
        )
        if cursor is None:
            continue
        untrusted_scope = untrusted_scope or any(
            start <= statement.start() < end for start, end in transaction_ranges
        ) or inside_dollar_body
        if parameter_name == b"schema":
            # SET SCHEMA is equivalent to a one-schema search_path. Preserve
            # the value so implicit pg_catalog-first semantics can be proved.
            starts.append(
                (
                    statement.start(),
                    _skip_sql_trivia(
                        source, cursor, check_deadline=check_deadline
                    ),
                    untrusted_scope,
                )
            )
            continue
        if parameter_name != b"search_path":
            continue
        if masked[cursor : cursor + 1] == b"=":
            starts.append((statement.start(), cursor + 1, untrusted_scope))
            continue
        assignment = _read_sql_identifier(
            source, cursor, check_deadline=check_deadline
        )
        if assignment is None or assignment[0].startswith(b'"'):
            continue
        assignment_name = _normalize_sql_identifier(assignment[0])
        if assignment_name == b"to":
            starts.append((statement.start(), assignment[1], untrusted_scope))
        elif assignment_name == b"from":
            current = _read_sql_identifier(
                source, assignment[1], check_deadline=check_deadline
            )
            if (
                current is not None
                and not current[0].startswith(b'"')
                and _normalize_sql_identifier(current[0]) == b"current"
            ):
                starts.append((statement.start(), None, untrusted_scope))
    starts.extend(
        (offset, None, untrusted_scope)
        for offset, untrusted_scope in _pg_settings_search_path_updates(
            source,
            masked,
            transaction_ranges=transaction_ranges,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    return tuple(starts)


def _pg_settings_search_path_updates(
    source: bytes,
    masked: bytes,
    *,
    transaction_ranges: tuple[tuple[int, int], ...] | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, bool], ...]:
    """Find UPDATEs that can change pg_settings.search_path.

    A simple exact equality to a different setting name is harmless. More
    complex predicates are treated as unknown and invalidate path evidence.
    """
    if transaction_ranges is None:
        transaction_ranges = _explicit_transaction_ranges(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    body_starts = tuple(start for start, _ in dollar_body_ranges)
    updates: list[tuple[int, bool]] = []
    for update in _UPDATE_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        cursor = _skip_sql_trivia(
            source, update.end(), check_deadline=check_deadline
        )
        target = _read_sql_identifier(
            source,
            update.end() if cursor is None else cursor,
            check_deadline=check_deadline,
        )
        if (
            target is not None
            and not target[0].startswith(b'"')
            and _normalize_sql_identifier(target[0]) == b"only"
        ):
            cursor = target[1]
        target_relation = (
            None
            if cursor is None
            else _read_qualified_sql_identifier_details(
                source, cursor, check_deadline=check_deadline
            )
        )
        if (
            target_relation is None
            or target_relation[0] not in {None, b"pg_catalog"}
            or target_relation[1] != b"pg_settings"
        ):
            continue
        target_end = target_relation[2]
        statement_end = masked.find(b";", update.start())
        if statement_end < 0:
            statement_end = len(masked)
        where = re.search(rb"\bWHERE\b", masked[target_end:statement_end], re.I)
        set_clause_end = (
            target_end + where.start()
            if where is not None
            else statement_end
        )
        set_clause = masked[target_end:set_clause_end]
        set_keyword = re.search(rb"\bSET\b", set_clause, re.I)
        if set_keyword is None:
            continue
        assignments_start = target_end + set_keyword.end()
        assignments = masked[assignments_start:set_clause_end]
        writes_setting = False
        for start, end in _split_top_level(
            assignments, check_deadline=check_deadline
        ):
            assignment_start = assignments_start + start
            assignment_end = assignments_start + end
            left_start = _skip_sql_trivia(
                source, assignment_start, check_deadline=check_deadline
            )
            if left_start is None:
                continue
            if masked[left_start : left_start + 1] == b"(":
                left_close = _find_matching_close_paren(
                    masked, left_start, check_deadline=check_deadline
                )
                if left_close is None or left_close >= assignment_end:
                    continue
                fields = _split_top_level(
                    masked[left_start + 1 : left_close],
                    check_deadline=check_deadline,
                )
                has_setting = False
                for field_start, field_end in fields:
                    if check_deadline is not None:
                        check_deadline()
                    field_start += left_start + 1
                    field_end += left_start + 1
                    field = _read_qualified_sql_identifier_details(
                        source, field_start, check_deadline=check_deadline
                    )
                    field_tail = (
                        None
                        if field is None
                        else _skip_sql_trivia(
                            source, field[2], check_deadline=check_deadline
                        )
                    )
                    if (
                        field is not None
                        and field[1] == b"setting"
                        and field_tail == field_end
                    ):
                        has_setting = True
                        break
                equals = _skip_sql_trivia(
                    source, left_close + 1, check_deadline=check_deadline
                )
            else:
                left_hand = _read_qualified_sql_identifier_details(
                    source, left_start, check_deadline=check_deadline
                )
                has_setting = (
                    left_hand is not None and left_hand[1] == b"setting"
                )
                equals = (
                    None
                    if left_hand is None
                    else _skip_sql_trivia(
                        source, left_hand[2], check_deadline=check_deadline
                    )
                )
            if (
                has_setting
                and equals is not None
                and masked[equals : equals + 1] == b"="
            ):
                writes_setting = True
                break
        if not writes_setting:
            continue
        if where is not None:
            where_start = target_end + where.end()
            where_source = source[where_start : statement_end].strip().rstrip(b";").strip()
            exact_name = re.fullmatch(
                rb"(?:(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\")\s*\.\s*)?"
                rb"(?:name|\"name\")\s*=\s*'((?:[^']|'')*)'",
                where_source,
                re.I,
            )
            if exact_name is not None and exact_name.group(1).lower() != b"search_path":
                continue
        body_index = bisect_right(body_starts, update.start()) - 1
        in_body = (
            body_index >= 0
            and dollar_body_ranges[body_index][0] <= update.start()
            < dollar_body_ranges[body_index][1]
        )
        in_transaction = any(
            start <= update.start() < end for start, end in transaction_ranges
        )
        updates.append((update.start(), in_body or in_transaction))
    return tuple(updates)


def _set_config_search_path_writes(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> tuple[int, ...]:
    """Return calls that can change search_path or whose setting is unknown."""
    writes: list[int] = []
    for call in _SET_CONFIG_NAME_RE.finditer(source):
        if check_deadline is not None:
            check_deadline()
        opening = _skip_sql_trivia(
            source, call.end(), check_deadline=check_deadline
        )
        if opening is None or masked[opening : opening + 1] != b"(":
            # The spelling occurred in a comment, literal or non-call name.
            # A quoted identifier is blanked in `masked`, but its following
            # code parenthesis remains visible.
            continue
        argument = _skip_sql_trivia(
            source, opening + 1, check_deadline=check_deadline
        )
        if argument is None or source[argument : argument + 1] != b"'":
            # Dynamic, escaped, or otherwise unfamiliar setting names cannot
            # prove that set_config leaves search_path alone.
            writes.append(call.start())
            continue
        cursor = argument + 1
        scanned = 0
        value = bytearray()
        while cursor < len(source):
            if check_deadline is not None and scanned % 256 == 0:
                check_deadline()
            scanned += 1
            if source[cursor] == ord("'"):
                if cursor + 1 < len(source) and source[cursor + 1] == ord("'"):
                    value.append(ord("'"))
                    cursor += 2
                    continue
                break
            value.append(source[cursor])
            cursor += 1
        else:
            writes.append(call.start())
            continue
        after_name = _skip_sql_trivia(
            source, cursor + 1, check_deadline=check_deadline
        )
        if (
            after_name is None
            or after_name >= len(masked)
            or masked[after_name] != _COMMA
            or b"\\" in value
        ):
            # Concatenation, operators, and escape-sensitive literals do not
            # establish the complete setting name from this first fragment.
            writes.append(call.start())
        elif bytes(value).lower() == b"search_path":
            writes.append(call.start())
    if check_deadline is not None:
        check_deadline()
    return tuple(writes)


def _dynamic_sql_execute_offsets(
    masked: bytes,
    dollar_body_ranges: tuple[tuple[int, int], ...],
) -> tuple[int, ...]:
    """Find executable dynamic SQL whose command string cannot be resolved."""
    return tuple(
        execute.start()
        for body_start, body_end in dollar_body_ranges
        for execute in _EXECUTE_WORD_RE.finditer(masked, body_start, body_end)
    )


def _sql_statement_ranges(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, int], ...]:
    """Statement spans split only at semicolons outside dollar-quoted bodies."""
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    body_index = 0
    statement_start = 0
    ranges: list[tuple[int, int]] = []
    for semicolon in re.finditer(rb";", masked):
        if check_deadline is not None:
            check_deadline()
        while (
            body_index < len(dollar_body_ranges)
            and dollar_body_ranges[body_index][1] <= semicolon.start()
        ):
            body_index += 1
        if (
            body_index < len(dollar_body_ranges)
            and dollar_body_ranges[body_index][0] <= semicolon.start()
            < dollar_body_ranges[body_index][1]
        ):
            continue
        end = semicolon.end()
        ranges.append((statement_start, end))
        statement_start = end
    if statement_start < len(masked):
        ranges.append((statement_start, len(masked)))
    return tuple(ranges)


def _explicit_transaction_ranges(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, int], ...]:
    """Find explicit transaction spans for path-setting conservatism."""
    ranges: list[tuple[int, int]] = []
    active_start: int | None = None
    for statement_start, statement_end in _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        if check_deadline is not None:
            check_deadline()
        first = _next_sql_keyword(
            source, masked, statement_start, check_deadline=check_deadline
        )
        if first is None or first[1] > statement_end:
            continue
        keyword = first[0].upper()
        second = _next_sql_keyword(
            source, masked, first[1], check_deadline=check_deadline
        )
        if keyword == b"BEGIN" or (
            keyword == b"START" and second is not None and second[0] == b"TRANSACTION"
        ):
            if active_start is None:
                active_start = statement_start
            continue
        if active_start is None:
            continue
        if keyword == b"PREPARE" and second is not None and second[0] == b"TRANSACTION":
            ranges.append((active_start, statement_start))
            active_start = None
            continue
        if keyword not in {b"ABORT", b"COMMIT", b"END", b"ROLLBACK"}:
            continue
        suffix = second
        if suffix is not None and suffix[0] in {b"WORK", b"TRANSACTION"}:
            suffix = _next_sql_keyword(
                source, masked, suffix[1], check_deadline=check_deadline
            )
        if keyword == b"ROLLBACK" and suffix is not None and suffix[0] == b"TO":
            continue
        if suffix is not None and suffix[0] == b"AND":
            chain = _next_sql_keyword(
                source, masked, suffix[1], check_deadline=check_deadline
            )
            no_chain = chain is not None and chain[0] == b"NO"
            if no_chain:
                chain = _next_sql_keyword(
                    source, masked, chain[1], check_deadline=check_deadline
                )
            if chain is not None and chain[0] == b"CHAIN" and not no_chain:
                # AND CHAIN finishes this transaction and immediately opens
                # another one. Path changes in the new transaction remain
                # transaction-local until its own end.
                continue
        ranges.append((active_start, statement_start))
        active_start = None
    if active_start is not None:
        ranges.append((active_start, len(masked)))
    return tuple(ranges)


def _search_path_reset_offsets(
    source: bytes,
    masked: bytes,
    *,
    configuration_ranges: tuple[tuple[int, int], ...] | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[int, ...]:
    """Locate RESET/DISCARD path resets, including quoted GUC identifiers."""
    if configuration_ranges is None:
        configuration_ranges = _non_session_configuration_ranges(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    offsets: list[int] = []
    for reset in _RESET_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        target = _read_sql_identifier(source, reset.end())
        if target is None:
            continue
        if (
            not target[0].startswith(b'"')
            and _normalize_sql_identifier(target[0]) in {b"session", b"local"}
        ):
            target = _read_sql_identifier(source, target[1])
            if target is None:
                continue
        target_name = _normalize_sql_identifier(target[0]).lower()
        is_path_reset = target_name == b"search_path"
        is_all_reset = not target[0].startswith(b'"') and target_name == b"all"
        if not (is_path_reset or is_all_reset):
            continue
        in_configuration = any(
            start <= reset.start() < end for start, end in configuration_ranges
        )
        in_executable_body = any(
            start <= reset.start() < end for start, end in dollar_body_ranges
        )
        if not in_configuration or in_executable_body:
            offsets.append(reset.start())
    for discard in _DISCARD_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        target = _read_sql_identifier(source, discard.end())
        if (
            target is None
            or target[0].startswith(b'"')
            or _normalize_sql_identifier(target[0]) != b"all"
        ):
            continue
        in_configuration = any(
            start <= discard.start() < end for start, end in configuration_ranges
        )
        in_executable_body = any(
            start <= discard.start() < end for start, end in dollar_body_ranges
        )
        if not in_configuration or in_executable_body:
            offsets.append(discard.start())
    return tuple(offsets)


def _search_path_may_shadow_catalog(
    source: bytes,
    masked: bytes,
    path_settings: tuple[tuple[int, int | None, bool], ...] | None = None,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> bool:
    """Whether source can put a non-catalog schema before pg_catalog.

    PostgreSQL searches pg_catalog first only when it is first in the explicit
    path. If a user schema comes first, an unqualified built-in spelling can
    resolve to a same-named user type. Across source files, execution order is
    not known, so any such SET is enough to make the path unsafe. A
    ``set_config('search_path', ...)`` call has the same effect and is unsafe
    unless its setting name is a different, statically known literal.
    """
    if _set_config_search_path_writes(
        source, masked, check_deadline=check_deadline
    ):
        return True
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    if _dynamic_sql_execute_offsets(masked, dollar_body_ranges):
        return True
    if path_settings is None:
        path_settings = _search_path_value_starts(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    for _, value_start, untrusted_scope in path_settings:
        if check_deadline is not None:
            check_deadline()
        if untrusted_scope:
            # Its effective state depends on a transaction or a procedural
            # body. Refuse the source rather than let a rolled-back or
            # transaction-local path certify a later declaration.
            return True
        is_schema, schema_name = _set_schema_value(
            source,
            masked,
            value_start,
            check_deadline=check_deadline,
        )
        if is_schema:
            # SET SCHEMA creates a one-schema path. PostgreSQL implicitly
            # searches pg_catalog first unless the schema is pg_temp.
            if schema_name is None or _is_temporary_schema(schema_name):
                return True
            continue
        if value_start is None:
            return True
        first = _read_sql_identifier(
            source, value_start, check_deadline=check_deadline
        )
        if first is None or _normalize_sql_identifier(first[0]) != b"pg_catalog":
            return True
    if _search_path_reset_offsets(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    ):
        # RESET and DISCARD ALL return to connection-specific defaults;
        # source cannot prove those schemas are harmless. A reset inside
        # executable routine text can also persist when that routine runs.
        return True
    return False


def _search_path_explicitly_orders_temp_after_catalog(
    source: bytes,
    masked: bytes,
    value_start: int | None,
    *,
    check_deadline: Remaining | None = None,
) -> bool:
    schemas = _read_search_path_schemas(
        source,
        masked,
        value_start,
        check_deadline=check_deadline,
    )
    if schemas is None:
        return False
    try:
        return schemas.index(b"pg_catalog") < schemas.index(b"pg_temp")
    except ValueError:
        # Without an explicit pg_temp position PostgreSQL searches the temp
        # schema first for types, even when pg_catalog is listed first.
        return False


def _search_path_explicitly_starts_with_catalog(
    source: bytes,
    masked: bytes,
    value_start: int | None,
    *,
    check_deadline: Remaining | None = None,
) -> bool:
    if value_start is None:
        return False
    is_schema, schema_name = _set_schema_value(
        source, masked, value_start, check_deadline=check_deadline
    )
    if is_schema:
        return schema_name is not None and not _is_temporary_schema(schema_name)
    first = _read_sql_identifier(
        source, value_start, check_deadline=check_deadline
    )
    return first is not None and _normalize_sql_identifier(first[0]) == b"pg_catalog"


def _non_session_configuration_ranges(
    source: bytes,
    masked: bytes,
    *,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> tuple[tuple[int, int], ...]:
    """Spans whose SET/RESET clauses do not change the current session."""
    statements = _sql_statement_ranges(
        source,
        masked,
        dollar_body_ranges=dollar_body_ranges,
        check_deadline=check_deadline,
    )
    ranges: list[tuple[int, int]] = []
    for pattern in (_ROUTINE_DDL_RE, _ALTER_GUC_DDL_RE):
        for ddl in pattern.finditer(masked):
            if check_deadline is not None:
                check_deadline()
            for start, end in statements:
                if start <= ddl.start() < end:
                    ranges.append((start, end))
                    break
    return tuple(ranges)


_SearchPathTimeline = tuple[
    tuple[int, ...], tuple[bool, ...], tuple[bool, ...], tuple[bool, ...]
]


def _build_search_path_timeline(
    source: bytes,
    masked: bytes,
    *,
    path_settings: tuple[tuple[int, int | None, bool], ...] | None = None,
    configuration_ranges: tuple[tuple[int, int], ...] | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> _SearchPathTimeline:
    """Build search-path facts once for constant-time candidate lookups.

    The three final fields at each event are, respectively, whether the temp
    schema may shadow pg_catalog, whether pg_catalog-first has been proven,
    and whether a path change may put a user schema ahead of pg_catalog.
    A source scan can contain thousands of CHECK candidates; rebuilding and
    sorting these events for every candidate made path analysis quadratic.
    """
    if path_settings is None:
        path_settings = _search_path_value_starts(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    if configuration_ranges is None:
        configuration_ranges = _non_session_configuration_ranges(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    if dollar_body_ranges is None:
        dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    events: list[tuple[int, bool, bool, bool]] = [
        (
            statement_start,
            untrusted_scope
            or not _search_path_explicitly_orders_temp_after_catalog(
                source,
                masked,
                value_start,
                check_deadline=check_deadline,
            ),
            not untrusted_scope
            and _search_path_explicitly_starts_with_catalog(
                source,
                masked,
                value_start,
                check_deadline=check_deadline,
            ),
            untrusted_scope
            or value_start is None
            or not _search_path_explicitly_starts_with_catalog(
                source,
                masked,
                value_start,
                check_deadline=check_deadline,
            ),
        )
        for statement_start, value_start, untrusted_scope in path_settings
    ]
    events.extend(
        (offset, True, False, True)
        for offset in _set_config_search_path_writes(
            source, masked, check_deadline=check_deadline
        )
    )
    events.extend(
        (offset, True, False, True)
        for offset in _dynamic_sql_execute_offsets(masked, dollar_body_ranges)
    )
    events.extend(
        (offset, True, False, True)
        for offset in _search_path_reset_offsets(
            source,
            masked,
            configuration_ranges=configuration_ranges,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    events.sort(key=lambda event: event[0])
    offsets: list[int] = []
    temporary_may_shadow: list[bool] = []
    catalog_first_proven: list[bool] = []
    catalog_may_shadow: list[bool] = []
    for offset, may_shadow, proven, path_may_shadow in events:
        if check_deadline is not None:
            check_deadline()
        offsets.append(offset)
        temporary_may_shadow.append(may_shadow)
        catalog_first_proven.append(proven)
        catalog_may_shadow.append(path_may_shadow)
    return (
        tuple(offsets),
        tuple(temporary_may_shadow),
        tuple(catalog_first_proven),
        tuple(catalog_may_shadow),
    )


def _search_path_facts_at(
    timeline: _SearchPathTimeline, before_offset: int
) -> tuple[bool, bool, bool]:
    """Return temp shadow, catalog-first proof and path shadow at an offset."""
    (
        offsets,
        temporary_may_shadow,
        catalog_first_proven,
        catalog_may_shadow,
    ) = timeline
    event_index = bisect_left(offsets, before_offset) - 1
    if event_index < 0:
        return True, False, False
    return (
        temporary_may_shadow[event_index],
        catalog_first_proven[event_index],
        catalog_may_shadow[event_index],
    )


def _is_unshadowed_builtin_type(
    type_name: bytes,
    declared_type_names: frozenset[tuple[bytes | None, bytes]],
    search_path_may_shadow_catalog: bool,
    temporary_relation_names: set[bytes],
    temporary_schema_may_shadow_catalog: bool,
    catalog_first_path_proven: bool,
) -> bool:
    """Resolve a builtin spelling only when source facts support that claim."""
    if type_name.startswith(b"pg_catalog."):
        return type_name in _SAFE_STRING_COLUMN_TYPES or type_name in _INTEGER_TYPE_BOUNDS
    if type_name not in _BUILTIN_COLUMN_TYPE_NAMES or search_path_may_shadow_catalog:
        return False
    # PostgreSQL searches the temporary schema first for data type names,
    # except when pg_temp is explicitly placed after pg_catalog.
    if temporary_schema_may_shadow_catalog and type_name in temporary_relation_names:
        return False
    # A same-named user type in a later schema cannot shadow pg_catalog when
    # an earlier session SET explicitly puts pg_catalog first at this
    # declaration. Without that position-specific proof, preserve the
    # conservative refusal.
    for schema, name in declared_type_names:
        if name != type_name or schema == b"pg_catalog":
            continue
        if _is_temporary_schema(schema):
            if temporary_schema_may_shadow_catalog:
                return False
            continue
        if catalog_first_path_proven:
            continue
        return False
    return True


def _collect_sql_mutation_context(
    sources: Iterable[tuple[str | None, str]],
    *,
    check_deadline: Remaining | None = None,
) -> _SqlMutationContext:
    """Find linked indexes, type changes and explicit collations in context."""
    attached_indexes: set[tuple[bytes | None, bytes]] = set()
    unresolved_index_attachment = False
    altered_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    declared_column_types: set[tuple[tuple[bytes | None, bytes], bytes, bytes]] = set()
    declared_type_names: set[tuple[bytes | None, bytes]] = set()
    search_path_may_shadow_sources: list[str | None] = []
    declared_column_type_sources: list[
        tuple[
            str | None,
            frozenset[tuple[tuple[bytes | None, bytes], bytes, bytes]],
        ]
    ] = []
    collated_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    dropped_tables: set[tuple[bytes | None, bytes]] = set()
    temporary_relation_names: set[bytes] = set()
    relocated_tables: set[tuple[bytes | None, bytes]] = set()
    relation_edges: dict[bytes, set[bytes]] = {}
    type_rename_edges: dict[tuple[bytes | None, bytes], set[bytes]] = {}
    temporary_type_names: set[tuple[bytes, bytes]] = set()
    def lexed_sources() -> Iterator[
        tuple[str | None, bytes, bytes, tuple[tuple[int, int], ...]]
    ]:
        for source_identity, text in sources:
            if check_deadline is not None:
                check_deadline()
            source = text.encode("utf-8")
            lexed = lex_sql(source, check_deadline=check_deadline)
            if check_deadline is not None:
                check_deadline()
            yield (
                source_identity,
                source,
                lexed.mask,
                lexed.dollar_bodies,
            )

    # Scan each source before reading and lexing the next one. Cross-file
    # relationships are accumulated as compact facts below, so keeping every
    # encoded source and mask alive is unnecessary and can exceed a 2 GiB gate
    # on a legal 512 MiB source tree. Selected targets are lexed again one at
    # a time during candidate generation; this trades bounded CPU for bounded
    # memory instead of retaining a tree of target masks in the context.
    for source_identity, source, masked, dollar_body_ranges in lexed_sources():
        if check_deadline is not None:
            check_deadline()
        for attachment_start, attachment_end in _iter_index_attachment_spans(
            masked, check_deadline=check_deadline
        ):
            reference = _attachment_index_reference(
                source,
                masked,
                attachment_start,
                attachment_end,
                check_deadline=check_deadline,
            )
            if reference is None:
                unresolved_index_attachment = True
            else:
                attached_indexes.add(reference)
        altered_columns.update(
            _collect_altered_columns(
                source, masked, check_deadline=check_deadline
            )
        )
        dropped_tables.update(
            _collect_dropped_tables(source, masked, check_deadline=check_deadline)
        )
        relation_seeds, relation_renames, view_dependencies = (
            _collect_temporary_relation_facts(
                source,
                masked,
                check_deadline=check_deadline,
                dollar_body_ranges=dollar_body_ranges,
            )
        )
        temporary_relation_names.update(relation_seeds)
        for old_name, new_name in relation_renames:
            relation_edges.setdefault(old_name, set()).add(new_name)
        for schema, referenced_name, view_name in view_dependencies:
            if schema is None or _is_temporary_schema(schema):
                relation_edges.setdefault(referenced_name, set()).add(view_name)
        relocated_tables.update(
            _collect_relocated_tables(
                source, masked, check_deadline=check_deadline
            )
        )
        declared, collated = _collect_table_column_metadata(
            source, masked, check_deadline=check_deadline
        )
        declared_column_types.update(declared)
        declared_column_type_sources.append((source_identity, declared))
        collated_columns.update(collated)
        declared, type_renames = _collect_declared_type_facts(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
        declared_type_names.update(declared)
        temporary_type_names.update(
            (schema, name)
            for schema, name in declared
            if schema is not None and _is_temporary_schema(schema)
        )
        for schema, old_name, new_name in type_renames:
            type_rename_edges.setdefault((schema, old_name), set()).add(new_name)
        source_path_may_shadow = _search_path_may_shadow_catalog(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )
        if source_path_may_shadow:
            search_path_may_shadow_sources.append(source_identity)
    if check_deadline is not None:
        check_deadline()
    # Resolve cross-file relation and implicit row-type dependencies in
    # source-independent graph walks. Each source is parsed once; a reversed
    # chain of renames therefore costs O(files + edges), not one full source
    # rescan per discovered name.
    pending_relations = list(temporary_relation_names)
    while pending_relations:
        if check_deadline is not None:
            check_deadline()
        old_name = pending_relations.pop()
        for new_name in relation_edges.get(old_name, ()):
            if check_deadline is not None:
                check_deadline()
            if new_name not in temporary_relation_names:
                temporary_relation_names.add(new_name)
                pending_relations.append(new_name)

    pending_types = list(temporary_type_names)
    while pending_types:
        if check_deadline is not None:
            check_deadline()
        schema, old_name = pending_types.pop()
        renamed_names = set(type_rename_edges.get((schema, old_name), ()))
        renamed_names.update(type_rename_edges.get((None, old_name), ()))
        for new_name in renamed_names:
            if check_deadline is not None:
                check_deadline()
            renamed = (schema, new_name)
            if renamed not in declared_type_names:
                declared_type_names.add(renamed)
                temporary_type_names.add(renamed)
                pending_types.append(renamed)
    return _SqlMutationContext(
        frozenset(attached_indexes),
        unresolved_index_attachment,
        frozenset(altered_columns),
        frozenset(declared_column_types),
        frozenset(declared_type_names),
        tuple(search_path_may_shadow_sources),
        tuple(declared_column_type_sources),
        frozenset(collated_columns),
        frozenset(dropped_tables),
        frozenset(temporary_relation_names),
        frozenset(relocated_tables),
    )


def _scan_drop_unique(
    source: bytes,
    masked: bytes,
    *,
    context: _SqlMutationContext | None = None,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    index_form_spans: list[tuple[int, int]] = []
    attached_indexes: set[tuple[bytes | None, bytes]] = set()
    unresolved_attachment = False
    for attachment_start, attachment_end in _iter_index_attachment_spans(
        masked, check_deadline=check_deadline
    ):
        reference = _attachment_index_reference(
            source,
            masked,
            attachment_start,
            attachment_end,
            check_deadline=check_deadline,
        )
        if reference is None:
            # If the attachment target cannot be parsed, suppress all unique
            # index sites conservatively below.  The constraint half is
            # already excluded by the USING INDEX check.
            unresolved_attachment = True
            continue
        attached_indexes.add(reference)
    if context is not None:
        attached_indexes.update(context.attached_indexes)
        unresolved_attachment = unresolved_attachment or context.unresolved_index_attachment
    attached_names = {name for _, name in attached_indexes}
    attached_any_schema = {
        name for schema, name in attached_indexes if schema is None
    }
    attached_by_schema: dict[bytes, set[bytes]] = {}
    for schema, name in attached_indexes:
        if schema is not None:
            attached_by_schema.setdefault(schema, set()).add(name)
    for m in _CREATE_UNIQUE_INDEX_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        index_form_spans.append((m.start(), m.end()))
        index_name_parts = _read_created_index_identity(
            source, masked, m.end(), check_deadline=check_deadline
        )
        index_schema, index_name = index_name_parts if index_name_parts is not None else (None, None)
        linked = index_name is not None and (
            index_name in attached_any_schema
            or (
                index_schema is None and index_name in attached_names
            )
            or (
                index_schema is not None
                and index_name in attached_by_schema.get(index_schema, set())
            )
        )
        if unresolved_attachment or (attached_indexes and index_name is None) or linked:
            # Mutating either half of an attached pair is unsafe as one site:
            # changing the index alone makes USING INDEX refuse the DDL, while
            # changing the constraint alone leaves the unique index enforcing
            # the same rule. Refuse the linked index site.
            continue
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-unique",
            description="CREATE UNIQUE INDEX -> CREATE INDEX",
            start=m.start(),
            end=m.end(),
            replacement=b"CREATE INDEX",
        )
    for m in _UNIQUE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _inside_any(index_form_spans, m.start()):
            continue
        if (
            _using_index_attachment_end(
                masked, m.end(), check_deadline=check_deadline
            )
            is not None
        ):
            # The existing index and its constraint form one semantic unit;
            # a single-site replacement cannot remove both safely.
            continue
        end = _unique_constraint_end(
            source, masked, m.end(), check_deadline=check_deadline
        )
        if end is None:
            continue
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-unique",
            description="UNIQUE -> CHECK (true)",
            start=m.start(),
            end=end,
            replacement=b"CHECK (true)",
        )


# --- sql:drop-not-null -----------------------------------------------------


_INLINE_PRIMARY_KEY_RE = re.compile(rb"\bPRIMARY\s+KEY\b", re.IGNORECASE)
_IDENTITY_CLAUSE_RE = re.compile(
    rb"\bGENERATED\s+(?:ALWAYS|BY\s+DEFAULT)\s+AS\s+IDENTITY"
    rb"(?:\s*\([^()]*\))?",
    re.IGNORECASE,
)
_NO_INHERIT_AFTER_NOT_NULL_RE = re.compile(rb"\s*NO\s+INHERIT\b", re.IGNORECASE)
_TABLE_LEVEL_NOT_NULL_PREFIX_RE = re.compile(
    rb"^\s*(?:ADD\s+)?"
    rb"(?:CONSTRAINT(?:\s+" + _SQL_IDENTIFIER + rb")?\s+)?"
    rb"NOT\s+NULL\b",
    re.IGNORECASE,
)
_NAMED_NOT_NULL_CONSTRAINT_PREFIX_RE = re.compile(
    rb"\bCONSTRAINT(?:\s+" + _SQL_IDENTIFIER + rb")?\s*$", re.IGNORECASE
)

_DECLARATION_SEARCH_WINDOW_BYTES = 64 * 1024
_DECLARATION_SEARCH_OVERLAP_BYTES = 16


def _search_declaration_keyword(
    masked: bytes,
    start: int,
    end: int,
    pattern: re.Pattern[bytes],
    *,
    check_deadline: Remaining | None,
) -> re.Match[bytes] | None:
    """Search a declaration body in bounded windows with its byte offsets."""
    cursor = start
    while cursor < end:
        if check_deadline is not None:
            check_deadline()
        boundary = min(end, cursor + _DECLARATION_SEARCH_WINDOW_BYTES)
        search_end = min(end, boundary + _DECLARATION_SEARCH_OVERLAP_BYTES)
        match = pattern.search(masked, cursor, search_end)
        if check_deadline is not None:
            check_deadline()
        if match is not None and match.start() < boundary:
            return match
        cursor = boundary
    if check_deadline is not None:
        check_deadline()
    return None


def _declare_section_spans(
    masked: bytes,
    dollar_bodies: tuple[tuple[int, int], ...],
    *,
    check_deadline: Remaining | None = None,
) -> list[tuple[int, int]]:
    """(A-283, rule 5) The ``[DECLARE, BEGIN)`` byte range of every
    top-level dollar-quoted body's own leading declaration section, if it
    has one. Only ever computed over ``dollar_bodies`` -- exactly the
    spans phase 2 recursed into -- because this grammar (a plpgsql
    ``DECLARE`` section) is only in scope at all because that recursion
    brought it there."""
    spans = []
    for body_start, body_end in dollar_bodies:
        if check_deadline is not None:
            check_deadline()
        declare = _search_declaration_keyword(
            masked,
            body_start,
            body_end,
            _DECLARE_RE,
            check_deadline=check_deadline,
        )
        if declare is None:
            continue
        begin = _search_declaration_keyword(
            masked,
            declare.end(),
            body_end,
            _BEGIN_RE,
            check_deadline=check_deadline,
        )
        declare_end = body_end if begin is None else begin.start()
        spans.append((declare.start(), declare_end))
        if check_deadline is not None:
            check_deadline()
    return spans


def _column_definition_clauses(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> list[tuple[int, int]]:
    """Return CREATE TABLE items and ALTER TABLE ADD actions.

    These bounded clauses let the NOT NULL scanner inspect sibling options
    without letting an identity declaration in a previous column affect this
    one. Table-level constraints are included so they can be refused rather
    than mistaken for column-level NOT NULL clauses.
    """
    clauses: list[tuple[int, int]] = []
    for table_match in _CREATE_TABLE_DEFINITION_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table_open = _read_create_table_columns_open(
            source,
            masked,
            table_match.end(),
            check_deadline=check_deadline,
        )
        if table_open is None:
            continue
        table_close = _find_matching_close_paren(
            masked, table_open, check_deadline=check_deadline
        )
        if table_close is None:
            continue
        body_start = table_open + 1
        body = masked[body_start:table_close]
        for start, end in _split_top_level(
            body, check_deadline=check_deadline
        ):
            absolute_start = body_start + start
            absolute_end = body_start + end
            clauses.append((absolute_start, absolute_end))

    for alter in _ALTER_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_alter_table_identity(
            source, masked, alter.end(), check_deadline=check_deadline
        )
        if table is None:
            continue
        _, table_end = table
        statement_end = masked.find(b";", table_end)
        if statement_end < 0:
            statement_end = len(masked)
        actions = masked[table_end:statement_end]
        for start, end in _split_top_level(
            actions, check_deadline=check_deadline
        ):
            absolute_start = table_end + start
            absolute_end = table_end + end
            action = masked[absolute_start:absolute_end]
            add = re.match(rb"\s*ADD\s+(?:COLUMN\s+)?", action, re.IGNORECASE)
            if add is None:
                continue
            clauses.append((absolute_start, absolute_end))

    clauses.sort(key=lambda clause: clause[0])
    return clauses


def _read_create_table_columns_open(
    source: bytes,
    masked: bytes,
    pos: int,
    *,
    check_deadline: Remaining | None = None,
) -> int | None:
    """Read a CREATE TABLE header and return its column/constraint list.

    The lexer blanks quoted identifiers, so the table name must be consumed
    from the source with the source-aware identifier reader. Accept the
    documented IF NOT EXISTS, OF, PARTITION OF, and USING header forms, then
    require the opening parenthesis at the syntactic position instead of
    searching ahead into a later clause.
    """
    table = _read_create_table_identity(
        source, masked, pos, check_deadline=check_deadline
    )
    if table is None:
        return None
    cursor = table[2]

    modifier = _next_sql_keyword(
        source, masked, cursor, check_deadline=check_deadline
    )
    if modifier is not None and modifier[0] == b"PARTITION":
        of_keyword = _next_sql_keyword(
            source, masked, modifier[1], check_deadline=check_deadline
        )
        if of_keyword is None or of_keyword[0] != b"OF":
            return None
        parent = _read_qualified_sql_identifier_details(
            source, of_keyword[1], check_deadline=check_deadline
        )
        if parent is None:
            return None
        cursor = parent[2]
    elif modifier is not None and modifier[0] == b"OF":
        parent_type = _read_qualified_sql_identifier_details(
            source, modifier[1], check_deadline=check_deadline
        )
        if parent_type is None:
            return None
        cursor = parent_type[2]
    elif modifier is not None and modifier[0] == b"USING":
        method = _read_qualified_sql_identifier_details(
            source, modifier[1], check_deadline=check_deadline
        )
        if method is None:
            return None
        cursor = method[2]

    table_open = _skip_sql_trivia(
        source, cursor, check_deadline=check_deadline
    )
    if table_open is None or table_open >= len(masked):
        return None
    if masked[table_open] != _OPEN_PAREN:
        return None
    return table_open


def _implicit_not_null_reason(
    source: bytes,
    masked: bytes,
    clause_start: int,
    clause: bytes | None,
) -> str | None:
    """Why omitting an explicit NOT NULL remains non-null and valid SQL."""
    if clause is None:
        return None
    column_start = clause_start
    first_keyword = _next_sql_keyword(source, masked, column_start)
    if first_keyword is not None and first_keyword[0] == b"ADD":
        column_start = first_keyword[1]
        next_keyword = _next_sql_keyword(source, masked, column_start)
        if next_keyword is not None and next_keyword[0] == b"COLUMN":
            column_start = next_keyword[1]
            next_keyword = _next_sql_keyword(source, masked, column_start)
        if next_keyword is not None and next_keyword[0] == b"IF":
            not_keyword = _next_sql_keyword(source, masked, next_keyword[1])
            exists_keyword = (
                None
                if not_keyword is None or not_keyword[0] != b"NOT"
                else _next_sql_keyword(source, masked, not_keyword[1])
            )
            if exists_keyword is not None and exists_keyword[0] == b"EXISTS":
                column_start = exists_keyword[1]
    column = _read_action_identifier(source, masked, column_start)
    if column is not None:
        declared_type = _next_sql_keyword(source, masked, column[1])
        serial_types = {
            b"smallserial",
            b"serial",
            b"bigserial",
            b"serial2",
            b"serial4",
            b"serial8",
        }
        if declared_type is not None and declared_type[0].lower() in serial_types:
            return f"{declared_type[0].decode('ascii').upper()} implies NOT NULL"
    if _IDENTITY_CLAUSE_RE.search(clause) is not None:
        return "IDENTITY implies NOT NULL"
    if _INLINE_PRIMARY_KEY_RE.search(clause) is not None:
        return "PRIMARY KEY implies NOT NULL"
    return None


def _scan_drop_not_null(
    source: bytes,
    masked: bytes,
    declare_spans: list[tuple[int, int]],
    *,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    column_clauses = _column_definition_clauses(
        source, masked, check_deadline=check_deadline
    )
    for m in _NOT_NULL_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        preceding = _preceding_word(masked, m.start())
        word = None if preceding is None else preceding[0]
        if word == b"IS":  # rule 1
            continue
        if word == b"DROP":  # rule 4 (A-283)
            continue
        if word == b"SET":  # rule 2
            span_start = preceding[1]
            replacement = b"DROP NOT NULL"
            description = "SET NOT NULL -> DROP NOT NULL"
        else:
            clause = None
            clause_start = None
            local_start = local_end = 0
            clause_index = bisect_right(
                column_clauses, (m.start(), len(masked) + 1)
            ) - 1
            if clause_index >= 0:
                clause_start, clause_end = column_clauses[clause_index]
                if clause_start <= m.start() < clause_end:
                    clause = masked[clause_start:clause_end]
                    local_start = m.start() - clause_start
                    local_end = m.end() - clause_start
            if clause is not None:
                table_not_null = _TABLE_LEVEL_NOT_NULL_PREFIX_RE.match(clause)
                if table_not_null is not None and clause_start is not None:
                    table_column_start = _skip_sql_trivia(
                        source, clause_start + table_not_null.end()
                    )
                    if (
                        table_column_start is not None
                        and _read_sql_identifier(source, table_column_start)
                        is not None
                    ):
                        continue
                if _NO_INHERIT_AFTER_NOT_NULL_RE.match(
                    clause[local_end:]
                ) is not None:
                    continue
                if _NAMED_NOT_NULL_CONSTRAINT_PREFIX_RE.search(
                    clause[:local_start]
                ) is not None:
                    continue
            reason = _implicit_not_null_reason(
                source, masked, clause_start or 0, clause
            )
            span_start = m.start()
            if reason is not None:
                replacement = b" "
                description = f"NOT NULL -> omitted ({reason})"
            else:
                replacement = b"NULL"
                description = "NOT NULL -> NULL"
        if _inside_any(declare_spans, span_start):  # rule 5 (A-283)
            continue
        yield _Site(
            lineno=line_for_offset(source, span_start),
            operator="sql:drop-not-null",
            description=description,
            start=span_start,
            end=m.end(),
            replacement=replacement,
        )


# --- sql:drop-foreign-key ---------------------------------------------------


def _scan_drop_foreign_key(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    claimed: set[int] = set()
    n = len(masked)
    for m in _FOREIGN_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        key_end = _sql_keyword_after_whitespace(
            masked, m.end(), b"KEY", check_deadline=check_deadline
        )
        if key_end is None:
            continue
        j = key_end
        j = _skip_sql_whitespace(masked, j, check_deadline=check_deadline)
        if j >= n or masked[j] != _OPEN_PAREN:
            continue
        close = _find_matching_close_paren(
            masked, j, check_deadline=check_deadline
        )
        if close is None:
            continue
        j = close + 1
        j = _skip_sql_whitespace(masked, j, check_deadline=check_deadline)
        ref_match = _REFERENCES_RE.match(masked, j)
        if ref_match is None:
            continue
        end = _extend_references_clause(
            masked, ref_match.start(), check_deadline=check_deadline
        )
        if end is None:
            continue
        claimed.add(ref_match.start())
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-foreign-key",
            description="FOREIGN KEY (...) REFERENCES ... -> CHECK (true)",
            start=m.start(),
            end=end,
            replacement=b"CHECK (true)",
        )
    for m in _REFERENCES_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if m.start() in claimed:
            continue
        stmt_start = _statement_start(masked, m.start())
        if _first_word(masked, stmt_start) in (b"GRANT", b"REVOKE"):  # A-283
            continue
        end = _extend_references_clause(
            masked, m.start(), check_deadline=check_deadline
        )
        if end is None:
            continue
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-foreign-key",
            description="REFERENCES ... -> CHECK (true)",
            start=m.start(),
            end=end,
            replacement=b"CHECK (true)",
        )


# --- sql:weaken-delete-action ------------------------------------------------


def _scan_weaken_delete_action(
    source: bytes,
    masked: bytes,
    *,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    for m in _ON_WORD_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        delete_end = _sql_keyword_after_whitespace(
            masked, m.end(), b"DELETE", check_deadline=check_deadline
        )
        if delete_end is None:
            continue
        action_start = _skip_sql_whitespace(
            masked, delete_end, check_deadline=check_deadline
        )
        if action_start == delete_end:
            continue
        action_end = _sql_keyword_end(masked, action_start, b"RESTRICT")
        if action_end is None:
            no_end = _sql_keyword_end(masked, action_start, b"NO")
            if no_end is None:
                continue
            action_end = _sql_keyword_after_whitespace(
                masked, no_end, b"ACTION", check_deadline=check_deadline
            )
            if action_end is None:
                continue
        action_text = source[action_start:action_end].decode("ascii", "replace")
        yield _Site(
            lineno=line_for_offset(source, action_start),
            operator="sql:weaken-delete-action",
            description=f"ON DELETE {action_text} -> ON DELETE CASCADE",
            start=action_start,
            end=action_end,
            replacement=b"CASCADE",
        )


# --- sql:drop-trigger --------------------------------------------------------


def _scan_drop_trigger(
    source: bytes,
    masked: bytes,
    dollar_bodies: tuple[tuple[int, int], ...],
    *,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    for m in _CREATE_TRIGGER_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        if _inside_any(dollar_bodies, m.start()):  # the carve's own scope reduction
            continue
        semicolon = masked.find(b";", m.end())
        if semicolon == -1:
            continue
        yield _Site(
            lineno=line_for_offset(source, m.start()),
            operator="sql:drop-trigger",
            description="CREATE TRIGGER ...; -> SELECT 1;",
            start=m.start(),
            end=semicolon + 1,
            replacement=b"SELECT 1;",
        )


# --- sql:widen-check-in ------------------------------------------------------


def _scan_widen_check_in(
    source: bytes,
    masked: bytes,
    *,
    context: _SqlMutationContext | None = None,
    source_identity: str | None = None,
    dollar_body_ranges: tuple[tuple[int, int], ...] | None = None,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    if check_deadline is not None:
        check_deadline()
    enum_types = {
        (
            _normalize_sql_identifier(match.group("schema"))
            + b"."
            + _normalize_sql_identifier(match.group("type"))
            if match.group("schema") is not None
            else _normalize_sql_identifier(match.group("type"))
        )
        for match in _ENUM_TYPE_RE.finditer(masked)
    }
    table_spans: list[
        tuple[
            int,
            int,
            tuple[bytes | None, bytes],
            dict[bytes, set[bytes]],
            frozenset[bytes],
            dict[bytes, frozenset[int]],
        ]
    ] = []
    declared_column_types, local_collated_columns = _collect_table_column_metadata(
        source, masked, check_deadline=check_deadline
    )
    path_dollar_body_ranges = dollar_body_ranges
    if path_dollar_body_ranges is None:
        path_dollar_body_ranges = lex_sql(
            source, check_deadline=check_deadline
        ).dollar_bodies
    declared_type_names = set(
        _collect_declared_type_names(
            source,
            masked,
            dollar_body_ranges=path_dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    path_configuration_ranges = _non_session_configuration_ranges(
        source,
        masked,
        dollar_body_ranges=path_dollar_body_ranges,
        check_deadline=check_deadline,
    )
    path_transaction_ranges = _explicit_transaction_ranges(
        source,
        masked,
        dollar_body_ranges=path_dollar_body_ranges,
        check_deadline=check_deadline,
    )
    search_path_settings = _search_path_value_starts(
        source,
        masked,
        configuration_ranges=path_configuration_ranges,
        transaction_ranges=path_transaction_ranges,
        dollar_body_ranges=path_dollar_body_ranges,
        check_deadline=check_deadline,
    )
    search_path_timeline = _build_search_path_timeline(
        source,
        masked,
        path_settings=search_path_settings,
        configuration_ranges=path_configuration_ranges,
        dollar_body_ranges=path_dollar_body_ranges,
        check_deadline=check_deadline,
    )
    context_search_path_may_shadow_catalog = False
    all_declared_column_types = set(declared_column_types)
    context_column_keys: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    collated_columns = set(local_collated_columns)
    altered_columns = set(
        _collect_altered_columns(source, masked, check_deadline=check_deadline)
    )
    dropped_tables = set(
        _collect_dropped_tables(source, masked, check_deadline=check_deadline)
    )
    temporary_relation_names = set(
        _collect_temporary_relation_names(
            source,
            masked,
            dollar_body_ranges=path_dollar_body_ranges,
            check_deadline=check_deadline,
        )
    )
    relocated_tables = set(
        _collect_relocated_tables(source, masked, check_deadline=check_deadline)
    )
    if context is not None:
        all_declared_column_types.update(context.declared_column_types)
        context_column_keys.update(
            (table_key, column_name)
            for context_identity, source_facts in context.declared_column_type_sources
            if source_identity is None or context_identity != source_identity
            for table_key, column_name, _ in source_facts
        )
        declared_type_names.update(context.declared_type_names)
        context_search_path_may_shadow_catalog = any(
            source_identity is None or context_identity != source_identity
            for context_identity in context.search_path_may_shadow_catalog_sources
        )
        altered_columns.update(context.altered_columns)
        collated_columns.update(context.collated_columns)
        dropped_tables.update(context.dropped_tables)
        temporary_relation_names.update(context.temporary_relation_names)
        relocated_tables.update(context.relocated_tables)
    type_declaration_offsets_by_table: dict[
        tuple[bytes | None, bytes], dict[bytes, set[int]]
    ] = {}
    for table_match in _CREATE_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        column_list = _create_table_column_list(
            masked, table_match, check_deadline=check_deadline
        )
        if column_list is None:
            continue
        table_name, table_open, table_close = column_list
        table_key = _table_key_from_match(table_name)
        table_types: dict[bytes, set[bytes]] = {}
        table_collations: frozenset[bytes] = frozenset()
        column_type_offsets: dict[bytes, set[int]] = {}
        if not _create_table_is_if_not_exists(
            source,
            masked,
            table_match,
            check_deadline=check_deadline,
        ):
            declarations, collations = _table_column_declarations(
                source,
                masked,
                table_key,
                table_open,
                table_close,
                check_deadline=check_deadline,
            )
            offsets_by_column = type_declaration_offsets_by_table.setdefault(
                table_key, {}
            )
            for _, column_name, type_name in declarations:
                table_types.setdefault(column_name, set()).add(type_name)
                column_type_offsets.setdefault(column_name, set()).add(
                    table_match.start()
                )
                offsets_by_column.setdefault(column_name, set()).add(
                    table_match.start()
                )
            table_collations = frozenset(column_name for _, column_name in collations)
        table_spans.append(
            (
                table_open,
                table_close,
                table_key,
                table_types,
                table_collations,
                {
                    name: frozenset(offsets)
                    for name, offsets in column_type_offsets.items()
                },
            )
        )
    table_spans.sort(key=lambda item: item[0])
    table_starts = [span[0] for span in table_spans]

    types_by_table: dict[
        tuple[bytes | None, bytes], dict[bytes, set[bytes]]
    ] = {}
    for table_key, column_name, type_name in all_declared_column_types:
        by_column = types_by_table.setdefault(table_key, {})
        by_column.setdefault(column_name, set()).add(type_name)

    collated_by_table: dict[tuple[bytes | None, bytes], set[bytes]] = {}
    for table_key, column_name in collated_columns:
        collated_by_table.setdefault(table_key, set()).add(column_name)

    altered_by_table: dict[tuple[bytes | None, bytes], set[bytes]] = {}
    for table_key, column_name in altered_columns:
        altered_by_table.setdefault(table_key, set()).add(column_name)

    semicolons = [match.start() for match in re.finditer(rb";", masked)]
    alter_spans: list[tuple[int, int, tuple[bytes | None, bytes]]] = []
    for alter in _ALTER_TABLE_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        table = _read_alter_table_identity(
            source, masked, alter.end(), check_deadline=check_deadline
        )
        if table is None:
            continue
        semicolon_index = bisect_right(semicolons, alter.start())
        statement_end = (
            semicolons[semicolon_index]
            if semicolon_index < len(semicolons)
            else len(masked)
        )
        alter_spans.append(
            (alter.start(), statement_end, table[0])
        )
    alter_spans.sort(key=lambda item: item[0])
    alter_starts = [span[0] for span in alter_spans]
    known_type_names = frozenset(declared_type_names)

    def table_for_check(
        check_start: int,
    ) -> tuple[
        tuple[bytes | None, bytes],
        bool,
        dict[bytes, set[bytes]],
        frozenset[bytes],
        dict[bytes, frozenset[int]],
    ] | None:
        table_index = bisect_right(table_starts, check_start) - 1
        if table_index >= 0:
            (
                start,
                end,
                table_key,
                table_types,
                table_collations,
                column_type_offsets,
            ) = table_spans[table_index]
            if start < check_start < end:
                return (
                    table_key,
                    True,
                    table_types,
                    table_collations,
                    column_type_offsets,
                )
        alter_index = bisect_right(alter_starts, check_start) - 1
        if alter_index >= 0:
            start, end, table_key = alter_spans[alter_index]
            if start < check_start < end:
                return table_key, False, {}, frozenset(), {}
        return None

    def columns_for_check(
        check_start: int,
    ) -> tuple[
        dict[bytes, bytes],
        frozenset[bytes],
        dict[bytes, tuple[bool, bool, bool]],
    ]:
        table_info = table_for_check(check_start)
        if table_info is None:
            return {}, frozenset(), {}
        (
            table_key,
            inline_create_table_check,
            inline_types,
            inline_collations,
            inline_column_type_offsets,
        ) = table_info
        if _is_temporary_schema(table_key[0]):
            # Explicit pg_temp references never denote an ordinary table in
            # the scanned source roots, even if no temp declaration was found.
            return {}, frozenset(), {}
        if (
            table_key[0] is None
            and table_key[1] in temporary_relation_names
            and not inline_create_table_check
        ):
            # A temp relation with this name can win unqualified resolution,
            # while the permanent relation's type is the one in source context.
            return {}, frozenset(), {}
        # An unqualified table reference has an unknown search_path, so any
        # dropped table with that name invalidates its type facts. A qualified
        # reference is invalidated only by the exact drop or an unqualified
        # drop that could resolve to it.
        table_was_dropped = (
            table_key in dropped_tables
            or (None, table_key[1]) in dropped_tables
            or (
                table_key[0] is None
                and any(name == table_key[1] for _, name in dropped_tables)
            )
        )
        table_was_relocated = (
            table_key in relocated_tables
            or (None, table_key[1]) in relocated_tables
            or (
                table_key[0] is None
                and any(name == table_key[1] for _, name in relocated_tables)
            )
        )
        if table_was_dropped or table_was_relocated:
            return {}, frozenset(), {}
        if not inline_create_table_check and table_key[0] is None:
            # Unqualified ALTER TABLE names depend on session search_path.
            # Cross-file source order cannot prove which same-named table is
            # altered, so no declared type facts are safe for this check.
            return {}, frozenset(), {}
        if inline_create_table_check:
            # An inline CHECK is bound by its own CREATE TABLE statement.
            # Use only that source's declaration facts; another file's
            # same-named table cannot establish its type.
            raw_types = inline_types
            collated = inline_collations
            altered = altered_by_table.get(table_key, set())
        else:
            # An unqualified CREATE TABLE's schema depends on search_path.
            # It cannot establish the type of a qualified relation with the
            # same name; only an exact schema-qualified declaration can.
            raw_types = types_by_table.get(table_key, {})
            collated = collated_by_table.get((None, table_key[1]), set()) | collated_by_table.get(
                table_key, set()
            )
            altered = altered_by_table.get((None, table_key[1]), set()) | altered_by_table.get(
                table_key, set()
            )
        result = {
            column_name: next(iter(values)) if len(values) == 1 else b"<ambiguous>"
            for column_name, values in raw_types.items()
        }
        for column_name in altered:
            result[column_name] = b"<altered>"
        column_path_facts: dict[bytes, tuple[bool, bool, bool]] = {}
        if inline_create_table_check:
            for column_name in result:
                offsets = inline_column_type_offsets.get(column_name, frozenset())
                declaration_facts = [
                    _search_path_facts_at(search_path_timeline, offset)
                    for offset in offsets
                ]
                if declaration_facts:
                    column_path_facts[column_name] = (
                        any(facts[0] for facts in declaration_facts),
                        all(facts[1] for facts in declaration_facts),
                        any(facts[2] for facts in declaration_facts),
                    )
                else:
                    column_path_facts[column_name] = (True, False, True)
        else:
            offsets_by_column = type_declaration_offsets_by_table.get(
                table_key, {}
            )
            for column_name in result:
                offsets = offsets_by_column.get(column_name, set())
                if (table_key, column_name) in context_column_keys or not offsets:
                    # Cross-file declarations have no comparable source
                    # offset. A known context path writer or same-named type
                    # still blocks the claim; otherwise leave the path-risk
                    # bit to the source and candidate timelines.
                    column_path_facts[column_name] = (True, False, False)
                    continue
                declaration_facts = [
                    _search_path_facts_at(search_path_timeline, offset)
                    for offset in offsets
                ]
                column_path_facts[column_name] = (
                    any(facts[0] for facts in declaration_facts),
                    all(facts[1] for facts in declaration_facts),
                    any(facts[2] for facts in declaration_facts),
                )
        return (
            result,
            frozenset(collated),
            column_path_facts,
        )

    for check_m in _CHECK_OPEN_RE.finditer(masked):
        if check_deadline is not None:
            check_deadline()
        open_paren = check_m.end() - 1
        check_close = _find_matching_close_paren(
            masked, open_paren, check_deadline=check_deadline
        )
        if check_close is None:
            continue
        region = masked[open_paren + 1 : check_close]
        region_offset = open_paren + 1
        for in_m in _IN_OPEN_RE.finditer(region):
            if check_deadline is not None:
                check_deadline()
            in_open = region_offset + in_m.end() - 1
            is_not_in = re.search(rb"\bNOT\s*$", region[: in_m.start()], re.IGNORECASE) is not None
            # `in_open` is a "(" strictly inside the already-balanced span
            # `(open_paren, check_close)` -- checked non-None above -- so a
            # fresh bracket-match from `in_open` is GUARANTEED to find its
            # own close at or before `check_close` (a well-formed bracket
            # sequence nests: any "(" inside a balanced span has its match
            # inside that same span too). Never `None`, never past
            # `check_close`; no defensive check here, because there is no
            # input that could make one fire.
            in_close = _find_matching_close_paren(
                masked, in_open, check_deadline=check_deadline
            )
            member_spans = _split_top_level(
                masked[in_open + 1 : in_close],
                check_deadline=check_deadline,
            )
            members = [
                source[in_open + 1 + s : in_open + 1 + e].strip()
                for s, e in member_spans
            ]
            # `_split_top_level` always returns at least one span (an empty
            # `IN ()` yields one empty member), so `members` is never itself
            # empty -- only an EMPTY member (`IN ()`, or a stray `IN (1,)`
            # trailing comma) can make this fire.
            if any(not member for member in members):
                continue
            all_strings = all(_STRING_MEMBER_RE.fullmatch(member) for member in members)
            all_unsigned_integers = all(
                _INT_MEMBER_RE.fullmatch(member) for member in members
            )
            lhs = region[: in_m.start()]
            if is_not_in:
                lhs = re.sub(rb"\bNOT\s*$", b"", lhs, flags=re.IGNORECASE)
            column_match = _SIMPLE_IDENT_RE.fullmatch(lhs)
            (
                column_types,
                explicitly_collated,
                column_path_facts,
            ) = columns_for_check(check_m.start())
            column_name = (
                None
                if column_match is None
                else _normalize_sql_identifier(column_match.group(1))
            )
            (
                temp_schema_may_shadow_catalog,
                catalog_first_path_proven,
                declaration_path_may_shadow_catalog,
            ) = column_path_facts.get(column_name, (True, False, True))
            _, _, candidate_path_may_shadow = _search_path_facts_at(
                search_path_timeline, check_m.start()
            )
            search_path_may_shadow_catalog = (
                declaration_path_may_shadow_catalog
                or candidate_path_may_shadow
                or context_search_path_may_shadow_catalog
            )
            if all_strings and not is_not_in:
                column_type = (
                    None
                    if column_name is None
                    else column_types.get(column_name)
                )
                if (
                    column_type not in _SAFE_STRING_COLUMN_TYPES
                    or not _is_unshadowed_builtin_type(
                        column_type,
                        known_type_names,
                        search_path_may_shadow_catalog,
                        temporary_relation_names,
                        temp_schema_may_shadow_catalog,
                        catalog_first_path_proven,
                    )
                    or column_type in enum_types
                    or column_name in explicitly_collated
                ):
                    # Unknown/user-defined types can reject an otherwise
                    # well-formed new literal (notably enum labels), while
                    # an explicit collation may equate a new spelling with
                    # an existing member. Keep the supported branch on
                    # supported variable-length built-in character types with
                    # default collation. Fixed-length CHAR pads trailing
                    # spaces during equality, so a source-distinct literal
                    # may be equivalent to an existing member.
                    continue
            if is_not_in:
                # String equality depends on PostgreSQL's resolved column type
                # and collation (for example, CHAR(n) ignores trailing spaces),
                # so source-distinct strings need not be comparison-distinct.
                # Only widen a homogeneous integer list on a declared bounded
                # integer type whose equality cannot collapse two values.
                if len(members) < 2 or not all_unsigned_integers or column_name is None:
                    continue
                column_type = column_types.get(column_name)
                if column_type is None or not _is_unshadowed_builtin_type(
                    column_type,
                    known_type_names,
                    search_path_may_shadow_catalog,
                    temporary_relation_names,
                    temp_schema_may_shadow_catalog,
                    catalog_first_path_proven,
                ):
                    continue
                bounds = _INTEGER_TYPE_BOUNDS.get(column_type)
                if bounds is None:
                    continue
                values = [int(member) for member in members]
                if any(value < bounds[0] or value > bounds[1] for value in values):
                    continue
                counts: dict[int, int] = {}
                for value in values:
                    counts[value] = counts.get(value, 0) + 1
                unique_indexes = [
                    index for index, value in enumerate(values) if counts[value] == 1
                ]
                if not unique_indexes:
                    continue
                remove_index = unique_indexes[-1]
                replacement_index = next(
                    index for index in range(len(members)) if index != remove_index
                )

                def literal_span(index: int) -> tuple[int, int]:
                    member_start, member_end = member_spans[index]
                    raw = source[
                        in_open + 1 + member_start : in_open + 1 + member_end
                    ]
                    leading = len(raw) - len(raw.lstrip())
                    trailing = len(raw) - len(raw.rstrip())
                    return (
                        in_open + 1 + member_start + leading,
                        in_open + 1 + member_end - trailing,
                    )

                removal_start, removal_end = literal_span(remove_index)
                replacement_start, replacement_end = literal_span(replacement_index)
                yield _Site(
                    lineno=line_for_offset(source, removal_start),
                    operator="sql:widen-check-in",
                    description="NOT IN (...) widened by removing one unique member",
                    start=removal_start,
                    end=removal_end,
                    replacement=source[replacement_start:replacement_end],
                )
                continue
            if all_strings:
                existing = {_decode_string_literal(member) for member in members}
                new_value = _unused_string_literal(existing)
                replacement = b", '" + new_value + b"')"
                description = f"IN (...) widened with '{new_value.decode('ascii')}'"
            elif all_unsigned_integers:
                if column_match is None:
                    continue
                column_type = column_types.get(
                    _normalize_sql_identifier(column_match.group(1))
                )
                if column_type is None or not _is_unshadowed_builtin_type(
                    column_type,
                    known_type_names,
                    search_path_may_shadow_catalog,
                    temporary_relation_names,
                    temp_schema_may_shadow_catalog,
                    catalog_first_path_proven,
                ):
                    continue
                bounds = _INTEGER_TYPE_BOUNDS.get(column_type)
                if bounds is None:
                    continue
                values = [int(member) for member in members]
                if any(value < bounds[0] or value > bounds[1] for value in values):
                    continue
                upper = max(values)
                new_int = upper + 1 if upper < bounds[1] else bounds[0]
                # The fallback is the (negative) lower bound and existing members
                # are unsigned, so `new_int` is outside the member set and within
                # the declared integer type.
                replacement = f", {new_int})".encode("ascii")
                description = f"IN (...) widened with {new_int}"
            else:
                continue  # rule 3: anything else -> no site
            yield _Site(
                lineno=line_for_offset(source, in_close),
                operator="sql:widen-check-in",
                description=description,
                start=in_close,
                end=in_close + 1,
                replacement=replacement,
            )


def _all_candidate_sites(
    source: bytes,
    masked: bytes,
    dollar_bodies: tuple[tuple[int, int], ...],
    declare_spans: list[tuple[int, int]],
    context: _SqlMutationContext | None = None,
    source_identity: str | None = None,
    check_deadline: Remaining | None = None,
) -> "Iterator[_Site]":
    """Every candidate from every scanner, in the operator table's own
    order (§3.2) -- never itself filtered by *lines*/*operators*/*limit*;
    stream candidates into :meth:`SqlAdapter.generate_mutation_sites`, which
    applies those filters while retaining only its identity-bounded heap
    (A-281)."""
    if check_deadline is not None:
        check_deadline()
    yield from _scan_drop_check(source, masked, check_deadline=check_deadline)
    if check_deadline is not None:
        check_deadline()
    yield from _scan_drop_unique(
        source,
        masked,
        context=context,
        check_deadline=check_deadline,
    )
    if check_deadline is not None:
        check_deadline()
    yield from _scan_drop_not_null(
        source,
        masked,
        declare_spans,
        check_deadline=check_deadline,
    )
    if check_deadline is not None:
        check_deadline()
    yield from _scan_drop_foreign_key(
        source, masked, check_deadline=check_deadline
    )
    if check_deadline is not None:
        check_deadline()
    yield from _scan_weaken_delete_action(
        source, masked, check_deadline=check_deadline
    )
    if check_deadline is not None:
        check_deadline()
    yield from _scan_drop_trigger(
        source,
        masked,
        dollar_bodies,
        check_deadline=check_deadline,
    )
    if check_deadline is not None:
        check_deadline()
    yield from _scan_widen_check_in(
        source,
        masked,
        context=context,
        source_identity=source_identity,
        dollar_body_ranges=dollar_bodies,
        check_deadline=check_deadline,
    )
    if check_deadline is not None:
        check_deadline()


# --- A-281: the bounded worst-eviction heap, mirroring python.py's own ------


@positional_record
class _Worst:
    """Inverted ordering so :mod:`heapq`'s min-heap retains the identity-
    SMALLEST candidates (evicting the identity-largest, i.e. worst, first) --
    the exact shape :func:`assay.adapters.python._generate_python_sites`
    already uses, duplicated here rather than imported (this module has no
    other reason to import a sibling adapter's private name)."""

    key: tuple[int, int, str, str]

    def __lt__(self, other: "_Worst") -> bool:
        return other.key < self.key


_UNREACHABLE = (
    "SqlAdapter.{name} is unreachable: cli._built_in_registry registers "
    "SQL's RegistryEntry.rigor as {{'R2'}} only, so no build ever calls "
    "{name} on a SQL lane -- SQL never runs R0/R1 coverage classification, "
    "the canary, or span attribution."
)


@record
class SqlAdapter:
    """The SQL union of the carve's seven ``sql:*`` mutation operators
    (A-097/A-101/A-105/A-114 protocol, A-180/A-183 bounded discovery,
    A-281/A-283 this package's own rulings) -- implementing exactly
    :meth:`is_test_path` and :meth:`generate_mutation_sites`; every other
    protocol method raises, because SQL lanes are R0,R2-only (§3.3)."""

    name: str = "sql"
    source_globs: tuple[str, ...] = ("*.sql",)
    excluded_dir_names: frozenset[str] = frozenset({"node_modules", "vendor", ".venv"})
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    #: ``False``, and for SQL it is not even the "already statement-granular"
    #: reason the other adapters give: there is no SQL coverage tool at all
    #: (this module's own §4.1 measurement), so no SQL profile exists to
    #: correct. Declared explicitly rather than omitted, because
    #: :func:`assay.evaluate._check_statement_attribution` reads this
    #: attribute directly and a missing one is a broken adapter (A-397).
    requires_statement_attribution: bool = False

    def for_project(self, *, repo_top: Path, project_root: Path) -> "SqlAdapter":
        """Unreachable, exactly as :meth:`normalize_coverage_key` is, and for
        the identical reason: A-404 places the ONE call to this member at the
        top of :func:`assay.runner.evaluate_r1`, and a SQL lane is R0,R2-only
        (§3.3, A-242), so it never gets there.

        The A-404 ruling's own default for a non-Go adapter is ``return
        self``, and it is deliberately NOT taken here. This module's rule is
        that a method no SQL lane can reach raises rather than answering, so
        that a future caller who routes SQL through R1 finds out loudly; a
        ``return self`` would instead be a line no test could exercise
        honestly, which is the vacuity this file's own negative tests exist
        to prevent.
        """
        raise NotImplementedError(_UNREACHABLE.format(name="for_project"))

    def is_test_path(self, rel_path: str) -> bool:
        return bool(_TEST_FILE_RE.search(rel_path))

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        raise NotImplementedError(_UNREACHABLE.format(name="has_executable_code"))

    def normalize_coverage_key(self, key: str) -> str:
        raise NotImplementedError(_UNREACHABLE.format(name="normalize_coverage_key"))

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        raise NotImplementedError(_UNREACHABLE.format(name="statement_spans"))

    def statement_blocks(
        self,
        repo_top: Path,
        rel_paths: Sequence[str],
        *,
        remaining: Remaining | None = None,
        sensitive_values: Sequence[str] = (),
    ) -> StatementBlockReport | None:
        raise NotImplementedError(_UNREACHABLE.format(name="statement_blocks"))

    def inject_import_break(self, text: str) -> tuple[str, str]:
        raise NotImplementedError(_UNREACHABLE.format(name="inject_import_break"))

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        raise NotImplementedError(_UNREACHABLE.format(name="inject_uncovered_line"))

    def prepare_mutation_context(self, sources: Iterable[str]) -> _SqlMutationContext:
        """Pre-scan declared SQL context for cross-file DDL dependencies."""
        return _collect_sql_mutation_context((None, source) for source in sources)

    def prepare_mutation_context_with_sources(
        self, sources: Iterable[tuple[str, str]]
    ) -> _SqlMutationContext:
        """Pre-scan context with path identities for same-file exclusions."""
        return _collect_sql_mutation_context(sources)

    def prepare_mutation_context_with_sources_bounded(
        self,
        sources: Iterable[tuple[str, str]],
        *,
        remaining: Remaining,
    ) -> _SqlMutationContext:
        """Pre-scan SQL context while honoring the lane's one deadline."""
        return _collect_sql_mutation_context(sources, check_deadline=remaining)

    def generate_mutation_sites(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        return self.generate_mutation_sites_with_context(
            text,
            lines,
            operators=operators,
            limit=limit,
            context=None,
        )

    def generate_mutation_sites_with_context(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
        context: _SqlMutationContext | None,
        source_identity: str | None = None,
        check_deadline: Remaining | None = None,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """Generate bounded sites using cross-file DDL facts when available.

        Never ``"UNSUPPORTED"`` -- SQL has an engine (the lexer), so an
        input it cannot lex is a discovery failure
        (:class:`~assay.mutation.MutationDiscoveryError`, raised by
        :func:`~assay.adapters.sql_lex.lex_sql` and left to propagate
        uncaught), never capability absence."""
        if check_deadline is not None:
            check_deadline()
        source = text.encode("utf-8")
        lex = lex_sql(source, check_deadline=check_deadline)
        masked = lex.mask
        dollar_bodies = lex.dollar_bodies
        if check_deadline is not None:
            check_deadline()
        selected = set(operators)
        declare_spans = _declare_section_spans(
            masked, dollar_bodies, check_deadline=check_deadline
        )

        heap: list[tuple[_Worst, int, _Site]] = []
        seen = 0
        for site in _all_candidate_sites(
            source,
            masked,
            dollar_bodies,
            declare_spans,
            context=context,
            source_identity=source_identity,
            check_deadline=check_deadline,
        ):
            if check_deadline is not None:
                check_deadline()
            if site.lineno not in lines or site.operator not in selected:
                continue
            if not _replacement_is_safe(site.operator, site.replacement):
                continue
            key = (
                site.start,
                site.end,
                hashlib.sha256(site.replacement).hexdigest(),
                site.operator,
            )
            seen += 1
            entry = (_Worst(key), seen, site)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif key < heap[0][0].key:
                heapq.heapreplace(heap, entry)

        retained = sorted(heap, key=lambda entry: entry[0].key)
        result = tuple(
            MutationSite(
                start_byte=site.start,
                end_byte=site.end,
                replacement=site.replacement,
                lineno=site.lineno,
                operator=site.operator,
                description=site.description,
            )
            for _, _, site in retained
        )
        if check_deadline is not None:
            check_deadline()
        return result

    def generate_mutation_sites_with_context_for_source(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
        context: _SqlMutationContext | None,
        source_identity: str,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """Generate sites while excluding this path's own context facts."""
        return self.generate_mutation_sites_with_context(
            text,
            lines,
            operators=operators,
            limit=limit,
            context=context,
            source_identity=source_identity,
        )

    def generate_mutation_sites_with_context_for_source_bounded(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
        context: _SqlMutationContext | None,
        source_identity: str,
        remaining: Remaining,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """Generate target sites under the plan or run's lane deadline."""
        return self.generate_mutation_sites_with_context(
            text,
            lines,
            operators=operators,
            limit=limit,
            context=context,
            source_identity=source_identity,
            check_deadline=remaining,
        )
