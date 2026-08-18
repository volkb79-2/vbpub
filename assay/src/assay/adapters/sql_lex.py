"""A stdlib-only, two-level bounded DDL lexer (P34/W1, carve
``W3-CARVE-P34-sql-adapter.md`` §3.1, A-281/A-283).

This module builds a **code mask**: a ``bytes`` object of IDENTICAL length
to the source it was built from, in which every byte the lexer classifies as
CODE keeps its original value and every other byte (comment, string
literal, quoted identifier, dollar-quote delimiter/body) becomes ``0x20``
(space) -- except a literal ``\\n``, which is always preserved so line
arithmetic over the mask agrees with the source (:func:`assay.mutation.
line_for_offset`, used by the adapter, needs this). Mask OFFSETS ARE SOURCE
OFFSETS: :class:`~assay.adapters.sql.SqlAdapter` never needs a translation
table between the two.

**Bytes, not characters, throughout (A-005's own precedent applied here).**
:meth:`~assay.adapters.base.LanguageAdapter.generate_mutation_sites` receives
``text: str``, but :class:`~assay.mutation.MutationSite` carries BYTE
offsets, and :func:`assay.mutation._validate_sites` refuses any span that
splits a UTF-8 character. The caller (:mod:`assay.adapters.sql`) encodes to
UTF-8 exactly once and hands this module ``bytes``; every function here
operates on ``bytes`` and returns byte offsets, so there is no
``str``-index/byte-index conversion anywhere to get subtly wrong.

**Two phases, and phase 2 is not recursive beyond one level.** Phase 1
(:func:`_lex_once`) classifies six non-code constructs in one forward pass:
``--`` line comments, ``/* ... */`` block comments (NESTING -- PostgreSQL,
unlike SQL-92), ``'...'`` string literals (``''`` is an embedded quote, a
backslash is never an escape -- ``standard_conforming_strings = on``),
``"..."`` quoted identifiers (``""`` is an embedded quote), ``$tag$...$tag$``
dollar-quoted strings (tag is ``$$`` or ``$`` + identifier + ``$``, body ends
at the IDENTICAL tag), and ``E'...'``-prefixed strings (as a plain string,
PLUS backslash escapes: ``\\'`` does not terminate). ``U&'...'`` needs no
special case at all: its ``U&`` prefix is two ordinary code bytes, and the
plain ``'...'`` that follows is caught by the ordinary string rule --
``UESCAPE`` only affects decoding, never termination.

Phase 1 also records every dollar-quoted BODY span it finds (excluding the
tag delimiters themselves). :func:`lex_sql`, the public entry point, then
re-lexes each recorded body with the SAME phase-1 routine and splices the
result back into the outer mask at that exact byte range -- this is what
recovers code (and, symmetrically, comments/strings) living inside a
``DO $$ ... $$`` block, which real DDL uses for idempotent migrations
(the carve's own M3/M4: both of the corpus's only two
``ON DELETE RESTRICT`` foreign keys live there). A body found INSIDE a body
is deliberately not recursed into again -- the inner call's own dollar-body
list is discarded rather than fed back through :func:`lex_sql` a second
time (A-281 carries this bound forward unargued; the carve's own §8.3 records
the one-level limit).

**Phase 3 -- fail closed (A-124/A-131's own asymmetry).** An unterminated
string, quoted identifier, dollar quote, or block comment raises
:class:`~assay.mutation.MutationDiscoveryError` immediately. This is not
decoration: silently masking out the rest of a malformed file as one giant
non-code span would produce mutation sites for the file's own VALID PREFIX,
while real PostgreSQL refuses the whole file outright. A wrong ``False``
here (silently accepting malformed DDL) is a measurement gap dressed as a
clean run; a wrong raise on genuinely valid DDL is at worst a false failure
-- srdm's own asymmetry lesson, reproduced at the lexical layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..mutation import MutationDiscoveryError

__all__ = ["LexResult", "lex_sql"]

_SPACE = 0x20
_NEWLINE = 0x0A
_DASH = 0x2D
_SLASH = 0x2F
_STAR = 0x2A
_SINGLE_QUOTE = 0x27
_DOUBLE_QUOTE = 0x22
_DOLLAR = 0x24
_BACKSLASH = 0x5C
_UNDERSCORE = 0x5F


def _is_ident_start_byte(b: int) -> bool:
    """A byte legal as the FIRST character of a dollar-quote tag identifier
    -- ASCII letter or underscore. PostgreSQL identifiers may contain other
    bytes (extended/multibyte letters), but the corpus and every fixture
    this project commits are ASCII-tag dollar quotes (``$$``, ``$body$``,
    ``$fn$``); a byte outside this narrow set simply fails to open a tag
    here, which is the same fail-narrow direction as everywhere else in this
    module's tag recognition (a byte that isn't a valid tag opener is just
    an ordinary code byte, e.g. the ``1`` in a ``$1`` positional parameter)."""
    return b == _UNDERSCORE or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A


def _is_ident_continue_byte(b: int) -> bool:
    return _is_ident_start_byte(b) or 0x30 <= b <= 0x39


def _is_word_byte(b: int) -> bool:
    """Used only for the ``E'``/``e'`` prefix's own leading word-boundary
    check (a bare identifier ending in ``e`` immediately before an
    unrelated string must not be misread as an escape-string prefix)."""
    return _is_ident_continue_byte(b)


def _blank(mask: bytearray, start: int, end: int) -> None:
    """Overwrite ``mask[start:end]`` with spaces, except any real ``\\n``,
    which is left untouched so a masked construct never fuses two source
    lines into one and never turns a genuine line break into something it
    wasn't (mirrors :mod:`assay.adapters.go`'s own ``_blank``, one module
    over, operating on ``bytes`` here instead of ``str``)."""
    for i in range(start, end):
        if mask[i] != _NEWLINE:
            mask[i] = _SPACE


def _scan_quoted(
    source: bytes, start: int, quote: int, *, backslash_escapes: bool
) -> int | None:
    """``source[start] == quote``, the OPENING delimiter of a ``'...'``
    string, ``"..."`` quoted identifier, or ``E'...'`` escape string.
    Returns the index just past the CLOSING delimiter, or ``None`` if the
    construct reaches end-of-file first (unterminated).

    A doubled delimiter (``''`` or ``""``) is always an embedded literal
    delimiter, never a terminator -- checked first, so a ``\\'`` immediately
    followed by another ``'`` in an escape string is read as "an escaped
    backslash, then a doubled quote", never confused with the doubling rule
    consuming past a real backslash escape (the backslash branch below is
    checked before the doubling branch every iteration, so this ordering is
    exact). When *backslash_escapes* is true (``E'...'`` only -- A-283's own
    corpus note: neither ``E'`` nor a bare backslash occurs in the pinned
    corpus, so this branch is carried on PostgreSQL's contract, not a
    consumer's measured need) a backslash unconditionally consumes the next
    byte too, so ``\\'`` never ends the literal early.
    """
    n = len(source)
    i = start + 1
    while i < n:
        b = source[i]
        if backslash_escapes and b == _BACKSLASH:
            i += 2
            continue
        if b == quote:
            if i + 1 < n and source[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return None


def _find_dollar_tag_end(source: bytes, start: int) -> int | None:
    """``source[start] == '$'``. Returns the index just past the CLOSING
    ``$`` of the tag (so the whole delimiter is ``source[start:returned]``),
    or ``None`` if this ``$`` does not open a valid dollar-quote tag at all
    -- the empty tag ``$$``, or ``$`` + an identifier (letter/underscore
    then letters/digits/underscores) + ``$``. A ``$`` that opens neither
    (``$1``, a positional parameter; a trailing bare ``$``) is simply an
    ordinary code byte to the caller, which is exactly right: PostgreSQL's
    own tag grammar cannot start with a digit either, so a positional
    parameter is never mistaken for a dollar-quote opener."""
    n = len(source)
    i = start + 1
    if i < n and source[i] == _DOLLAR:
        return i + 1
    j = i
    if j < n and _is_ident_start_byte(source[j]):
        j += 1
        while j < n and _is_ident_continue_byte(source[j]):
            j += 1
        if j < n and source[j] == _DOLLAR:
            return j + 1
    return None


def _lex_once(source: bytes) -> tuple[bytearray, list[tuple[int, int]]]:
    """ONE forward pass (phase 1): classify every byte of *source* as code
    or not, returning a mutable mask (code bytes verbatim, everything else
    blanked per :func:`_blank`) and every dollar-quoted BODY span found
    (``(body_start, body_end)``, excluding the tag delimiters) -- recorded,
    never recursed into here. :func:`lex_sql` is the only caller that
    recurses, and only one level deep.

    Raises :class:`~assay.mutation.MutationDiscoveryError` the instant an
    unterminated string, quoted identifier, dollar quote, or block comment
    is found -- phase 3, fail closed."""
    n = len(source)
    mask = bytearray(source)
    dollar_bodies: list[tuple[int, int]] = []
    i = 0
    while i < n:
        b = source[i]

        # -- line comment: to the next '\n' or EOF.
        if b == _DASH and i + 1 < n and source[i + 1] == _DASH:
            end = source.find(b"\n", i)
            end = n if end == -1 else end
            _blank(mask, i, end)
            i = end
            continue

        # /* block comment */ -- nests.
        if b == _SLASH and i + 1 < n and source[i + 1] == _STAR:
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if source[j] == _SLASH and j + 1 < n and source[j + 1] == _STAR:
                    depth += 1
                    j += 2
                    continue
                if source[j] == _STAR and j + 1 < n and source[j + 1] == _SLASH:
                    depth -= 1
                    j += 2
                    continue
                j += 1
            if depth != 0:
                raise MutationDiscoveryError(
                    "sql mutation discovery: unterminated block comment "
                    f"starting at byte {i}"
                )
            _blank(mask, i, j)
            i = j
            continue

        # E'...'/e'...' escape string -- word-boundary-guarded prefix, then
        # a plain-string scan with backslash escapes turned on.
        if (
            b in (0x45, 0x65)
            and i + 1 < n
            and source[i + 1] == _SINGLE_QUOTE
            and (i == 0 or not _is_word_byte(source[i - 1]))
        ):
            end = _scan_quoted(
                source, i + 1, _SINGLE_QUOTE, backslash_escapes=True
            )
            if end is None:
                raise MutationDiscoveryError(
                    "sql mutation discovery: unterminated escape string "
                    f"starting at byte {i}"
                )
            _blank(mask, i, end)
            i = end
            continue

        # '...' string literal (also the tail half of U&'...', which needs
        # no special case: 'U&' is two ordinary code bytes ahead of it).
        if b == _SINGLE_QUOTE:
            end = _scan_quoted(source, i, _SINGLE_QUOTE, backslash_escapes=False)
            if end is None:
                raise MutationDiscoveryError(
                    f"sql mutation discovery: unterminated string starting at byte {i}"
                )
            _blank(mask, i, end)
            i = end
            continue

        # "..." quoted identifier.
        if b == _DOUBLE_QUOTE:
            end = _scan_quoted(source, i, _DOUBLE_QUOTE, backslash_escapes=False)
            if end is None:
                raise MutationDiscoveryError(
                    "sql mutation discovery: unterminated quoted identifier "
                    f"starting at byte {i}"
                )
            _blank(mask, i, end)
            i = end
            continue

        # $tag$...$tag$ dollar-quoted string.
        if b == _DOLLAR:
            tag_end = _find_dollar_tag_end(source, i)
            if tag_end is not None:
                delimiter = source[i:tag_end]
                close = source.find(delimiter, tag_end)
                if close == -1:
                    raise MutationDiscoveryError(
                        "sql mutation discovery: unterminated dollar-quoted "
                        f"string starting at byte {i}"
                    )
                dollar_bodies.append((tag_end, close))
                _blank(mask, i, close + len(delimiter))
                i = close + len(delimiter)
                continue
            i += 1
            continue

        i += 1

    return mask, dollar_bodies


@dataclass(frozen=True, kw_only=True)
class LexResult:
    """The fully-recursed (phase 1 + phase 2) lex of one source file.

    ``mask`` is the same length as the source it was built from; code bytes
    are verbatim, everything else is a space except a preserved ``\\n``.
    ``dollar_bodies`` is every TOP-LEVEL dollar-quoted body span
    (``(body_start, body_end)``, excluding the tag delimiters, in the SAME
    byte coordinates as ``mask``/the source) that phase 2 recursed into --
    :class:`~assay.adapters.sql.SqlAdapter` uses it to scope constructs whose
    rule is keyed to "inside a dollar body" (the ``DECLARE``-section
    exclusion, A-283; ``sql:drop-trigger``'s own dollar-body exclusion).
    A body nested inside another body is NOT included here (one level of
    recursion only) -- only the outermost bodies phase 1 found.
    """

    mask: bytes
    dollar_bodies: tuple[tuple[int, int], ...]


def lex_sql(source: bytes) -> LexResult:
    """The public entry point: phase 1 over *source*, then phase 2 -- each
    recorded dollar-quoted body is re-lexed by the SAME phase-1 routine and
    its result spliced back into the outer mask at that exact byte range,
    exactly once (the inner call's own dollar-body findings are discarded,
    never fed through this function again).

    Raises :class:`~assay.mutation.MutationDiscoveryError` for a source that
    does not lex cleanly at either level -- an unterminated construct
    anywhere, including inside a dollar-quoted body, fails the whole file
    closed.
    """
    mask, dollar_bodies = _lex_once(source)
    for start, end in dollar_bodies:
        inner_mask, _ = _lex_once(source[start:end])
        mask[start:end] = inner_mask
    return LexResult(mask=bytes(mask), dollar_bodies=tuple(dollar_bodies))
