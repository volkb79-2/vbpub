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

**The five lexical context rules (three from the carve's own §3.2, two more
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
from bisect import bisect_right
from ..records import positional_record, record
from pathlib import Path
from typing import Iterable, Literal, NamedTuple, Sequence

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


def _find_matching_close_paren(masked: bytes, open_index: int) -> int | None:
    """``masked[open_index] == '('``. Returns the index of its matching
    ``)`` (nested parens counted), or ``None`` if the source runs out first
    -- a malformed/unbalanced construct a scanner simply skips (an honest
    gap, never a crash) rather than raising: an unmatched paren is not one
    of :mod:`assay.adapters.sql_lex`'s own phase-3 failure constructs, so it
    is not this module's place to fail the whole file closed over it."""
    depth = 1
    i = open_index + 1
    n = len(masked)
    while i < n:
        b = masked[i]
        if b == _OPEN_PAREN:
            depth += 1
        elif b == _CLOSE_PAREN:
            depth -= 1
            if depth == 0:
                return i
        i += 1
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


def _split_top_level(region: bytes) -> list[tuple[int, int]]:
    """Byte ranges (relative to *region*) of each comma-separated member at
    PAREN DEPTH ZERO within *region* -- a comma inside a nested call's own
    parens is never treated as a list separator. *region* is expected to be
    the MASKED slice between an ``IN``-list's own parens, so a comma inside
    a string literal member is already invisible (blanked to a space) and
    never mistaken for a separator either."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for i, b in enumerate(region):
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
    """Cross-file facts needed to avoid splitting linked SQL constructs."""

    attached_indexes: frozenset[tuple[bytes | None, bytes]]
    unresolved_index_attachment: bool
    altered_columns: frozenset[tuple[tuple[bytes | None, bytes], bytes]]
    declared_column_types: frozenset[
        tuple[tuple[bytes | None, bytes], bytes, bytes]
    ]
    collated_columns: frozenset[tuple[tuple[bytes | None, bytes], bytes]]


# --- shared regex fragments ---------------------------------------------------

_CHECK_OPEN_RE = re.compile(rb"\bCHECK\b\s*\(", re.IGNORECASE)
_NOT_NULL_RE = re.compile(rb"\bNOT\s+NULL\b", re.IGNORECASE)
_FOREIGN_KEY_RE = re.compile(rb"\bFOREIGN\s+KEY\b", re.IGNORECASE)
_REFERENCES_RE = re.compile(rb"\bREFERENCES\b", re.IGNORECASE)
_REFERENCED_TARGET_RE = re.compile(
    rb"REFERENCES\s+[A-Za-z_][A-Za-z0-9_.]*(?:\s*\([^()]*\))?", re.IGNORECASE
)
_ON_ACTION_RE = re.compile(
    rb"\s*ON\s+(?:DELETE|UPDATE)\s+(?:NO\s+ACTION|RESTRICT|CASCADE|SET\s+NULL(?:\s*\([^()]*\))?|SET\s+DEFAULT)",
    re.IGNORECASE,
)
_FK_MODIFIER_RE = re.compile(
    rb"\s*(?:MATCH\s+(?:FULL|PARTIAL|SIMPLE)|(?:NOT\s+)?DEFERRABLE|"
    rb"INITIALLY\s+(?:DEFERRED|IMMEDIATE)|NOT\s+VALID)\b",
    re.IGNORECASE,
)
_UNIQUE_NULLS_RE = re.compile(rb"\s+NULLS\s+(?:NOT\s+)?DISTINCT\b", re.IGNORECASE)
_UNIQUE_INCLUDE_RE = re.compile(rb"\s*INCLUDE\b", re.IGNORECASE)
_UNIQUE_WITH_RE = re.compile(rb"\s*WITH\b", re.IGNORECASE)
_UNIQUE_DEFERRABILITY_RE = re.compile(
    rb"\s*(?:NOT\s+)?DEFERRABLE\b|\s*INITIALLY\s+(?:DEFERRED|IMMEDIATE)\b",
    re.IGNORECASE,
)
_SQL_IDENTIFIER = rb'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_UNIQUE_USING_INDEX_RE = re.compile(
    rb"\s*USING\s+INDEX\s+"
    + _SQL_IDENTIFIER
    + rb"(?:\s*\.\s*"
    + _SQL_IDENTIFIER
    + rb")?",
    re.IGNORECASE,
)
_UNIQUE_INDEX_TABLESPACE_RE = re.compile(
    rb"\s*USING\s+INDEX\s+TABLESPACE\s+" + _SQL_IDENTIFIER,
    re.IGNORECASE,
)
_INDEX_ATTACHMENT_RE = re.compile(
    rb"\b(?:UNIQUE|PRIMARY\s+KEY)\b\s+USING\s+INDEX\b(?!\s+TABLESPACE\b)",
    re.IGNORECASE,
)
_USING_INDEX_ATTACHMENT_RE = re.compile(
    rb"\s*USING\s+INDEX\b(?!\s+TABLESPACE\b)", re.IGNORECASE
)
_WEAKEN_DELETE_RE = re.compile(rb"\bON\s+DELETE\s+(RESTRICT|NO\s+ACTION)\b", re.IGNORECASE)
_CREATE_TRIGGER_RE = re.compile(
    rb"\bCREATE\b(?:\s+OR\s+REPLACE)?(?:\s+CONSTRAINT)?\s+TRIGGER\b", re.IGNORECASE
)
_CREATE_TABLE_RE = re.compile(rb"\bCREATE\s+TABLE\b", re.IGNORECASE)
_ALTER_TABLE_RE = re.compile(rb"\bALTER\s+TABLE\b", re.IGNORECASE)
_TABLE_NAME_AFTER_CREATE_RE = re.compile(
    rb"\s*(?:IF\s+NOT\s+EXISTS\s+)?"
    rb"(?:(?P<schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<table>" + _SQL_IDENTIFIER + rb")",
    re.IGNORECASE,
)
_TABLE_NAME_AFTER_ALTER_RE = re.compile(
    rb"\s*(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"
    rb"(?:(?P<schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<table>" + _SQL_IDENTIFIER + rb")",
    re.IGNORECASE,
)
_ALTER_COLUMN_TYPE_RE = re.compile(
    rb"\bALTER\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?"
    rb"(?P<column>" + _SQL_IDENTIFIER + rb")\s+"
    rb"(?:TYPE|SET\s+DATA\s+TYPE)\s+"
    rb"(?:(?P<type_schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<type>" + _SQL_IDENTIFIER + rb")(?![A-Za-z0-9_$])",
    re.IGNORECASE,
)
_ENUM_TYPE_RE = re.compile(
    rb"\bCREATE\s+TYPE\s+(?:(?P<schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<type>" + _SQL_IDENTIFIER + rb")\s+AS\s+ENUM\b",
    re.IGNORECASE,
)
_ENUM_COLUMN_DECL_RE = re.compile(
    rb"\s*(?P<column>" + _SQL_IDENTIFIER + rb")\s+"
    rb"(?:(?P<type_schema>" + _SQL_IDENTIFIER + rb")\s*\.\s*)?"
    rb"(?P<type>" + _SQL_IDENTIFIER + rb")(?![A-Za-z0-9_$])",
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
_SAFE_STRING_COLUMN_TYPES = frozenset(
    {b"text", b"varchar", b"character", b"char", b"bpchar", b"name"}
)
_CREATE_UNIQUE_INDEX_RE = re.compile(rb"\bCREATE\s+UNIQUE\s+INDEX\b", re.IGNORECASE)
_UNIQUE_RE = re.compile(rb"\bUNIQUE\b", re.IGNORECASE)
_IN_OPEN_RE = re.compile(rb"\bIN\b\s*\(", re.IGNORECASE)
_STRING_MEMBER_RE = re.compile(rb"'(?:[^']|'')*'")
_INT_MEMBER_RE = re.compile(rb"[0-9]+")
_DECLARE_RE = re.compile(rb"\bDECLARE\b", re.IGNORECASE)
_BEGIN_RE = re.compile(rb"\bBEGIN\b", re.IGNORECASE)


def _extend_references_clause(masked: bytes, start: int) -> int | None:
    """*start* indexes the ``R`` of ``REFERENCES``. Returns the end offset
    of the whole clause: the target (optionally schema-qualified) table
    name, an optional parenthesized column list, and any trailing
    ``ON DELETE``/``ON UPDATE`` actions in either order (either or both
    absent) -- or ``None`` if there is not even a target table name
    (malformed; the caller skips it rather than guessing a span)."""
    target = _REFERENCED_TARGET_RE.match(masked, start)
    if target is None:
        return None
    end = target.end()
    while True:
        action = _ON_ACTION_RE.match(masked, end)
        if action is not None:
            end = action.end()
            continue
        modifier = _FK_MODIFIER_RE.match(masked, end)
        if modifier is None:
            return end
        end = modifier.end()


# --- sql:drop-check ------------------------------------------------------


def _scan_drop_check(source: bytes, masked: bytes) -> "list[_Site]":
    sites = []
    for m in _CHECK_OPEN_RE.finditer(masked):
        open_paren = m.end() - 1
        close = _find_matching_close_paren(masked, open_paren)
        if close is None:
            continue
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-check",
                description="CHECK (...) -> CHECK (true)",
                start=m.start(),
                end=close + 1,
                replacement=b"CHECK (true)",
            )
        )
    return sites


# --- sql:drop-unique -------------------------------------------------------


def _unique_constraint_end(masked: bytes, keyword_end: int) -> int | None:
    """Return the full UNIQUE constraint span end, including its index and
    deferrability options, or ``None`` for an unbalanced option list."""
    cursor = keyword_end
    included_end = keyword_end
    nulls = _UNIQUE_NULLS_RE.match(masked, cursor)
    if nulls is not None:
        cursor = nulls.end()
        included_end = cursor

    while cursor < len(masked) and masked[cursor : cursor + 1].isspace():
        cursor += 1
    if cursor < len(masked) and masked[cursor] == _OPEN_PAREN:
        close = _find_matching_close_paren(masked, cursor)
        if close is None:
            return None
        cursor = close + 1
        included_end = cursor

    while True:
        option_start = cursor
        while cursor < len(masked) and masked[cursor : cursor + 1].isspace():
            cursor += 1
        option = _UNIQUE_INDEX_TABLESPACE_RE.match(masked, option_start)
        if option is None:
            option = _UNIQUE_USING_INDEX_RE.match(masked, option_start)
        if option is not None:
            cursor = option.end()
            included_end = cursor
            continue
        option = _UNIQUE_DEFERRABILITY_RE.match(masked, option_start)
        if option is not None:
            cursor = option.end()
            included_end = cursor
            continue
        option = _UNIQUE_INCLUDE_RE.match(masked, option_start)
        if option is not None:
            cursor = option.end()
            while cursor < len(masked) and masked[cursor : cursor + 1].isspace():
                cursor += 1
            if cursor >= len(masked) or masked[cursor] != _OPEN_PAREN:
                return None
            close = _find_matching_close_paren(masked, cursor)
            if close is None:
                return None
            cursor = close + 1
            included_end = cursor
            continue
        option = _UNIQUE_WITH_RE.match(masked, option_start)
        if option is not None:
            cursor = option.end()
            while cursor < len(masked) and masked[cursor : cursor + 1].isspace():
                cursor += 1
            if cursor >= len(masked) or masked[cursor] != _OPEN_PAREN:
                return None
            close = _find_matching_close_paren(masked, cursor)
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


def _skip_sql_trivia(source: bytes, pos: int) -> int | None:
    """Advance past SQL whitespace, line comments and nested block comments."""
    while pos < len(source):
        if source[pos] in b" \t\r\n\f\v":
            pos += 1
            continue
        if source.startswith(b"--", pos):
            newline = source.find(b"\n", pos + 2)
            return len(source) if newline < 0 else _skip_sql_trivia(source, newline + 1)
        if source.startswith(b"/*", pos):
            depth = 1
            cursor = pos + 2
            while cursor < len(source) and depth:
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


def _read_sql_identifier(source: bytes, pos: int) -> tuple[bytes, int] | None:
    """Read one ASCII or double-quoted SQL identifier after trivia."""
    pos = _skip_sql_trivia(source, pos)
    if pos is None or pos >= len(source):
        return None
    if source[pos] == ord('"'):
        cursor = pos + 1
        while cursor < len(source):
            if source[cursor] == ord('"'):
                if cursor + 1 < len(source) and source[cursor + 1] == ord('"'):
                    cursor += 2
                    continue
                return source[pos : cursor + 1], cursor + 1
            cursor += 1
        return None
    match = re.match(rb"[A-Za-z_][A-Za-z0-9_$]*", source[pos:])
    if match is None:
        return None
    return match.group(0), pos + match.end()


def _read_qualified_sql_identifier_details(
    source: bytes, pos: int
) -> tuple[bytes | None, bytes, int] | None:
    first = _read_sql_identifier(source, pos)
    if first is None:
        return None
    first_token, cursor = first
    after_first = _skip_sql_trivia(source, cursor)
    if after_first is None:
        return None
    if after_first < len(source) and source[after_first] == ord('.'):
        second = _read_sql_identifier(source, after_first + 1)
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
    source: bytes, pos: int
) -> tuple[bytes | None, bytes, int] | None:
    """Read a CREATE INDEX name and its end, including optional modifiers."""
    name_pos = pos
    word = _read_sql_identifier(source, pos)
    if word is None:
        return None
    raw_word, cursor = word
    if not raw_word.startswith(b'"') and raw_word.upper() == b"CONCURRENTLY":
        name_pos = cursor
        word = _read_sql_identifier(source, cursor)
        if word is None:
            return None
        raw_word, cursor = word
    if not raw_word.startswith(b'"') and raw_word.upper() == b"IF":
        not_word = _read_sql_identifier(source, cursor)
        if not_word is None or not_word[0].upper() != b"NOT":
            return None
        exists_word = _read_sql_identifier(source, not_word[1])
        if exists_word is None or exists_word[0].upper() != b"EXISTS":
            return None
        cursor = exists_word[1]
        name_pos = cursor
    return _read_qualified_sql_identifier_details(source, name_pos)


def _read_created_index_identity(
    source: bytes, masked: bytes, pos: int
) -> tuple[bytes | None, bytes] | None:
    parsed = _read_create_index_name_details(source, pos)
    if parsed is None:
        return None
    index_schema, index_name, index_end = parsed
    on_clause = _CREATE_INDEX_ON_RE.match(masked, index_end)
    if on_clause is None:
        return index_schema, index_name
    table = _read_qualified_sql_identifier_details(source, on_clause.end())
    if index_schema is None and table is not None:
        index_schema = table[0]
    return index_schema, index_name


def _attachment_index_reference(
    source: bytes, masked: bytes, attachment: re.Match[bytes]
) -> tuple[bytes | None, bytes] | None:
    reference = _read_qualified_sql_identifier_details(source, attachment.end())
    if reference is None:
        return None
    schema, name, _ = reference
    if schema is not None:
        return schema, name
    statement_start = masked.rfind(b";", 0, attachment.start()) + 1
    alters = tuple(_ALTER_TABLE_RE.finditer(masked, statement_start, attachment.start()))
    if alters:
        table_name = _TABLE_NAME_AFTER_ALTER_RE.match(masked, alters[-1].end())
        if table_name is not None:
            table_schema, _ = _table_key_from_match(table_name)
            if table_schema is not None:
                return table_schema, name
    return None, name


def _table_key_from_match(match: re.Match[bytes]) -> tuple[bytes | None, bytes]:
    schema = match.group("schema")
    return (
        None if schema is None else _normalize_sql_identifier(schema),
        _normalize_sql_identifier(match.group("table")),
    )


def _type_name_from_match(match: re.Match[bytes]) -> bytes:
    name = _normalize_sql_identifier(match.group("type"))
    schema = match.group("type_schema")
    if schema is not None and _normalize_sql_identifier(schema) != b"pg_catalog":
        return _normalize_sql_identifier(schema) + b"." + name
    return name


def _collect_altered_columns(masked: bytes) -> frozenset[tuple[tuple[bytes | None, bytes], bytes]]:
    altered_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    for alter in _ALTER_TABLE_RE.finditer(masked):
        table_name = _TABLE_NAME_AFTER_ALTER_RE.match(masked, alter.end())
        if table_name is None:
            continue
        table_key = _table_key_from_match(table_name)
        statement_end = masked.find(b";", table_name.end())
        if statement_end < 0:
            statement_end = len(masked)
        for changed in _ALTER_COLUMN_TYPE_RE.finditer(masked, table_name.end(), statement_end):
            altered_columns.add(
                (table_key, _normalize_sql_identifier(changed.group("column")))
            )
    return frozenset(altered_columns)


def _collect_table_column_metadata(
    masked: bytes,
) -> tuple[
    frozenset[tuple[tuple[bytes | None, bytes], bytes, bytes]],
    frozenset[tuple[tuple[bytes | None, bytes], bytes]],
]:
    declared_types: set[tuple[tuple[bytes | None, bytes], bytes, bytes]] = set()
    collated: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    for table_match in _CREATE_TABLE_RE.finditer(masked):
        table_name = _TABLE_NAME_AFTER_CREATE_RE.match(masked, table_match.end())
        table_open = masked.find(b"(", table_match.end())
        if table_name is None or table_open == -1:
            continue
        table_close = _find_matching_close_paren(masked, table_open)
        if table_close is None:
            continue
        table_key = _table_key_from_match(table_name)
        table_body = masked[table_open + 1 : table_close]
        for start, end in _split_top_level(table_body):
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


def _collect_sql_mutation_context(sources: Iterable[str]) -> _SqlMutationContext:
    """Find linked indexes, type changes and explicit collations in context."""
    attached_indexes: set[tuple[bytes | None, bytes]] = set()
    unresolved_index_attachment = False
    altered_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    declared_column_types: set[tuple[tuple[bytes | None, bytes], bytes, bytes]] = set()
    collated_columns: set[tuple[tuple[bytes | None, bytes], bytes]] = set()
    for text in sources:
        source = text.encode("utf-8")
        masked = lex_sql(source).mask
        for attachment in _INDEX_ATTACHMENT_RE.finditer(masked):
            reference = _attachment_index_reference(source, masked, attachment)
            if reference is None:
                unresolved_index_attachment = True
            else:
                attached_indexes.add(reference)
        altered_columns.update(_collect_altered_columns(masked))
        declared, collated = _collect_table_column_metadata(masked)
        declared_column_types.update(declared)
        collated_columns.update(collated)
    return _SqlMutationContext(
        frozenset(attached_indexes),
        unresolved_index_attachment,
        frozenset(altered_columns),
        frozenset(declared_column_types),
        frozenset(collated_columns),
    )


def _scan_drop_unique(
    source: bytes,
    masked: bytes,
    *,
    context: _SqlMutationContext | None = None,
) -> "list[_Site]":
    sites = []
    index_form_spans: list[tuple[int, int]] = []
    attached_indexes: set[tuple[bytes | None, bytes]] = set()
    unresolved_attachment = False
    for attachment in _INDEX_ATTACHMENT_RE.finditer(masked):
        reference = _attachment_index_reference(source, masked, attachment)
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
        index_form_spans.append((m.start(), m.end()))
        index_name_parts = _read_created_index_identity(source, masked, m.end())
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
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-unique",
                description="CREATE UNIQUE INDEX -> CREATE INDEX",
                start=m.start(),
                end=m.end(),
                replacement=b"CREATE INDEX",
            )
        )
    for m in _UNIQUE_RE.finditer(masked):
        if _inside_any(index_form_spans, m.start()):
            continue
        if _USING_INDEX_ATTACHMENT_RE.match(masked, m.end()) is not None:
            # The existing index and its constraint form one semantic unit;
            # a single-site replacement cannot remove both safely.
            continue
        end = _unique_constraint_end(masked, m.end())
        if end is None:
            continue
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-unique",
                description="UNIQUE -> CHECK (true)",
                start=m.start(),
                end=end,
                replacement=b"CHECK (true)",
            )
        )
    return sites


# --- sql:drop-not-null -----------------------------------------------------


def _declare_section_spans(
    masked: bytes, dollar_bodies: tuple[tuple[int, int], ...]
) -> list[tuple[int, int]]:
    """(A-283, rule 5) The ``[DECLARE, BEGIN)`` byte range of every
    top-level dollar-quoted body's own leading declaration section, if it
    has one. Only ever computed over ``dollar_bodies`` -- exactly the
    spans phase 2 recursed into -- because this grammar (a plpgsql
    ``DECLARE`` section) is only in scope at all because that recursion
    brought it there."""
    spans = []
    for body_start, body_end in dollar_bodies:
        body = masked[body_start:body_end]
        declare = _DECLARE_RE.search(body)
        if declare is None:
            continue
        begin = _BEGIN_RE.search(body, declare.end())
        declare_end = body_end if begin is None else body_start + begin.start()
        spans.append((body_start + declare.start(), declare_end))
    return spans


def _scan_drop_not_null(
    source: bytes, masked: bytes, declare_spans: list[tuple[int, int]]
) -> "list[_Site]":
    sites = []
    for m in _NOT_NULL_RE.finditer(masked):
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
            span_start = m.start()
            replacement = b"NULL"
            description = "NOT NULL -> NULL"
        if _inside_any(declare_spans, span_start):  # rule 5 (A-283)
            continue
        sites.append(
            _Site(
                lineno=line_for_offset(source, span_start),
                operator="sql:drop-not-null",
                description=description,
                start=span_start,
                end=m.end(),
                replacement=replacement,
            )
        )
    return sites


# --- sql:drop-foreign-key ---------------------------------------------------


def _scan_drop_foreign_key(source: bytes, masked: bytes) -> "list[_Site]":
    sites = []
    claimed: set[int] = set()
    n = len(masked)
    for m in _FOREIGN_KEY_RE.finditer(masked):
        j = m.end()
        while j < n and masked[j : j + 1].isspace():
            j += 1
        if j >= n or masked[j] != _OPEN_PAREN:
            continue
        close = _find_matching_close_paren(masked, j)
        if close is None:
            continue
        j = close + 1
        while j < n and masked[j : j + 1].isspace():
            j += 1
        ref_match = _REFERENCES_RE.match(masked, j)
        if ref_match is None:
            continue
        end = _extend_references_clause(masked, ref_match.start())
        if end is None:
            continue
        claimed.add(ref_match.start())
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-foreign-key",
                description="FOREIGN KEY (...) REFERENCES ... -> CHECK (true)",
                start=m.start(),
                end=end,
                replacement=b"CHECK (true)",
            )
        )
    for m in _REFERENCES_RE.finditer(masked):
        if m.start() in claimed:
            continue
        stmt_start = _statement_start(masked, m.start())
        if _first_word(masked, stmt_start) in (b"GRANT", b"REVOKE"):  # A-283
            continue
        end = _extend_references_clause(masked, m.start())
        if end is None:
            continue
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-foreign-key",
                description="REFERENCES ... -> CHECK (true)",
                start=m.start(),
                end=end,
                replacement=b"CHECK (true)",
            )
        )
    return sites


# --- sql:weaken-delete-action ------------------------------------------------


def _scan_weaken_delete_action(source: bytes, masked: bytes) -> "list[_Site]":
    sites = []
    for m in _WEAKEN_DELETE_RE.finditer(masked):
        action_start, action_end = m.start(1), m.end(1)
        action_text = source[action_start:action_end].decode("ascii", "replace")
        sites.append(
            _Site(
                lineno=line_for_offset(source, action_start),
                operator="sql:weaken-delete-action",
                description=f"ON DELETE {action_text} -> ON DELETE CASCADE",
                start=action_start,
                end=action_end,
                replacement=b"CASCADE",
            )
        )
    return sites


# --- sql:drop-trigger --------------------------------------------------------


def _scan_drop_trigger(
    source: bytes, masked: bytes, dollar_bodies: tuple[tuple[int, int], ...]
) -> "list[_Site]":
    sites = []
    for m in _CREATE_TRIGGER_RE.finditer(masked):
        if _inside_any(dollar_bodies, m.start()):  # the carve's own scope reduction
            continue
        semicolon = masked.find(b";", m.end())
        if semicolon == -1:
            continue
        sites.append(
            _Site(
                lineno=line_for_offset(source, m.start()),
                operator="sql:drop-trigger",
                description="CREATE TRIGGER ...; -> SELECT 1;",
                start=m.start(),
                end=semicolon + 1,
                replacement=b"SELECT 1;",
            )
        )
    return sites


# --- sql:widen-check-in ------------------------------------------------------


def _scan_widen_check_in(
    source: bytes,
    masked: bytes,
    *,
    context: _SqlMutationContext | None = None,
) -> "list[_Site]":
    sites = []
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
    table_spans: list[tuple[int, int, tuple[bytes | None, bytes]]] = []
    declared_column_types, local_collated_columns = _collect_table_column_metadata(masked)
    all_declared_column_types = set(declared_column_types)
    collated_columns = set(local_collated_columns)
    altered_columns = set(_collect_altered_columns(masked))
    if context is not None:
        all_declared_column_types.update(context.declared_column_types)
        altered_columns.update(context.altered_columns)
        collated_columns.update(context.collated_columns)
    for table_match in _CREATE_TABLE_RE.finditer(masked):
        table_name = _TABLE_NAME_AFTER_CREATE_RE.match(masked, table_match.end())
        table_open = masked.find(b"(", table_match.end())
        if table_name is None or table_open == -1:
            continue
        table_close = _find_matching_close_paren(masked, table_open)
        if table_close is None:
            continue
        table_key = _table_key_from_match(table_name)
        table_spans.append((table_open, table_close, table_key))
    table_spans.sort(key=lambda item: item[0])
    table_starts = [span[0] for span in table_spans]

    types_by_table: dict[
        tuple[bytes | None, bytes], dict[bytes, set[bytes]]
    ] = {}
    types_by_name: dict[bytes, dict[bytes, set[bytes]]] = {}
    for table_key, column_name, type_name in all_declared_column_types:
        by_column = types_by_table.setdefault(table_key, {})
        by_column.setdefault(column_name, set()).add(type_name)
        by_name_column = types_by_name.setdefault(table_key[1], {})
        by_name_column.setdefault(column_name, set()).add(type_name)

    collated_by_table: dict[tuple[bytes | None, bytes], set[bytes]] = {}
    collated_by_name: dict[bytes, set[bytes]] = {}
    for table_key, column_name in collated_columns:
        collated_by_table.setdefault(table_key, set()).add(column_name)
        collated_by_name.setdefault(table_key[1], set()).add(column_name)

    altered_by_table: dict[tuple[bytes | None, bytes], set[bytes]] = {}
    altered_by_name: dict[bytes, set[bytes]] = {}
    for table_key, column_name in altered_columns:
        altered_by_table.setdefault(table_key, set()).add(column_name)
        altered_by_name.setdefault(table_key[1], set()).add(column_name)

    semicolons = [match.start() for match in re.finditer(rb";", masked)]
    alter_spans: list[tuple[int, int, tuple[bytes | None, bytes]]] = []
    for alter in _ALTER_TABLE_RE.finditer(masked):
        table_name = _TABLE_NAME_AFTER_ALTER_RE.match(masked, alter.end())
        if table_name is None:
            continue
        semicolon_index = bisect_right(semicolons, alter.start())
        statement_end = (
            semicolons[semicolon_index]
            if semicolon_index < len(semicolons)
            else len(masked)
        )
        alter_spans.append(
            (alter.start(), statement_end, _table_key_from_match(table_name))
        )
    alter_spans.sort(key=lambda item: item[0])
    alter_starts = [span[0] for span in alter_spans]

    def table_for_check(check_start: int) -> tuple[bytes | None, bytes] | None:
        table_index = bisect_right(table_starts, check_start) - 1
        if table_index >= 0:
            start, end, table_key = table_spans[table_index]
            if start < check_start < end:
                return table_key
        alter_index = bisect_right(alter_starts, check_start) - 1
        if alter_index >= 0:
            start, end, table_key = alter_spans[alter_index]
            if start < check_start < end:
                return table_key
        return None

    def columns_for_check(
        check_start: int,
    ) -> tuple[dict[bytes, bytes], frozenset[bytes]]:
        table_key = table_for_check(check_start)
        if table_key is None:
            return {}, frozenset()
        if table_key[0] is None:
            raw_types = types_by_name.get(table_key[1], {})
            collated = collated_by_name.get(table_key[1], set())
            altered = altered_by_name.get(table_key[1], set())
        else:
            raw_types = {}
            for candidate_key in ((None, table_key[1]), table_key):
                for column_name, values in types_by_table.get(candidate_key, {}).items():
                    raw_types.setdefault(column_name, set()).update(values)
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
        return result, frozenset(collated)

    for check_m in _CHECK_OPEN_RE.finditer(masked):
        open_paren = check_m.end() - 1
        check_close = _find_matching_close_paren(masked, open_paren)
        if check_close is None:
            continue
        region = masked[open_paren + 1 : check_close]
        region_offset = open_paren + 1
        for in_m in _IN_OPEN_RE.finditer(region):
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
            in_close = _find_matching_close_paren(masked, in_open)
            member_spans = _split_top_level(masked[in_open + 1 : in_close])
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
            column_types, explicitly_collated = columns_for_check(check_m.start())
            column_name = (
                None
                if column_match is None
                else _normalize_sql_identifier(column_match.group(1))
            )
            if all_strings and not is_not_in:
                column_type = (
                    None
                    if column_name is None
                    else column_types.get(column_name)
                )
                if (
                    column_type not in _SAFE_STRING_COLUMN_TYPES
                    or column_type in enum_types
                    or column_name in explicitly_collated
                ):
                    # Unknown/user-defined types can reject an otherwise
                    # well-formed new literal (notably enum labels), while
                    # an explicit collation may equate a new spelling with
                    # an existing member. Keep the supported branch on
                    # known built-in character types with default collation.
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
                sites.append(
                    _Site(
                        lineno=line_for_offset(source, removal_start),
                        operator="sql:widen-check-in",
                        description="NOT IN (...) widened by removing one unique member",
                        start=removal_start,
                        end=removal_end,
                        replacement=source[replacement_start:replacement_end],
                    )
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
                bounds = _INTEGER_TYPE_BOUNDS.get(column_type)
                if bounds is None:
                    continue
                values = [int(member) for member in members]
                if any(value < bounds[0] or value > bounds[1] for value in values):
                    continue
                upper = max(values)
                new_int = upper + 1 if upper < bounds[1] else bounds[0]
                if new_int in values:
                    # The fallback is the lower bound. Existing members are
                    # unsigned, so this is outside the member set and within
                    # the declared integer type.
                    continue
                replacement = f", {new_int})".encode("ascii")
                description = f"IN (...) widened with {new_int}"
            else:
                continue  # rule 3: anything else -> no site
            sites.append(
                _Site(
                    lineno=line_for_offset(source, in_close),
                    operator="sql:widen-check-in",
                    description=description,
                    start=in_close,
                    end=in_close + 1,
                    replacement=replacement,
                )
            )
    return sites


def _all_candidate_sites(
    source: bytes,
    masked: bytes,
    dollar_bodies: tuple[tuple[int, int], ...],
    declare_spans: list[tuple[int, int]],
    context: _SqlMutationContext | None = None,
) -> "list[_Site]":
    """Every candidate from every scanner, in the operator table's own
    order (§3.2) -- never itself filtered by *lines*/*operators*/*limit*;
    :meth:`SqlAdapter.generate_mutation_sites` does that centrally, in a
    single bounded heap, mirroring
    :func:`assay.adapters.python._generate_python_sites` (A-281)."""
    sites: list[_Site] = []
    sites.extend(_scan_drop_check(source, masked))
    sites.extend(_scan_drop_unique(source, masked, context=context))
    sites.extend(_scan_drop_not_null(source, masked, declare_spans))
    sites.extend(_scan_drop_foreign_key(source, masked))
    sites.extend(_scan_weaken_delete_action(source, masked))
    sites.extend(_scan_drop_trigger(source, masked, dollar_bodies))
    sites.extend(_scan_widen_check_in(source, masked, context=context))
    return sites


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
        return _collect_sql_mutation_context(sources)

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
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """Generate bounded sites using cross-file DDL facts when available.

        Never ``"UNSUPPORTED"`` -- SQL has an engine (the lexer), so an
        input it cannot lex is a discovery failure
        (:class:`~assay.mutation.MutationDiscoveryError`, raised by
        :func:`~assay.adapters.sql_lex.lex_sql` and left to propagate
        uncaught), never capability absence."""
        source = text.encode("utf-8")
        lex = lex_sql(source)
        selected = set(operators)
        declare_spans = _declare_section_spans(lex.mask, lex.dollar_bodies)

        heap: list[tuple[_Worst, int, _Site]] = []
        seen = 0
        for site in _all_candidate_sites(
            source, lex.mask, lex.dollar_bodies, declare_spans, context=context
        ):
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
        return tuple(
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
