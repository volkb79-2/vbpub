"""
    STALE PREMISE, TRACKED (A-234/A-217). The prose below states
    ``requires_span_attribution = False`` is "settled, not assumed" and that the
    alternative is "structurally impossible". A-172's probe disproved the premise
    that a cover block's extent equals its statement lines, A-O19 ruled it a
    product decision, and A-217 ruled **option 2**: Go needs a real source-side
    statement-position oracle. Read A-217, A-218 and A-235 before trusting any
    span-attribution statement in this file. A-235 additionally records that
    ``statement_spans`` currently has no seam through which that oracle would ever
    be invoked, because this parser leaves no unattributed line.
The Go :class:`~assay.adapters.base.LanguageAdapter` -- the SECOND real
adapter (P08), proving the boundary P05 built is genuinely additive: adding
Go requires ZERO changes to the frozen protocol (``adapters/base.py``), the
language-free core (``evaluate.py``), or the registry (``registry.py``) --
O3's whole claim. Go is the deliberately chosen second language because its
own coverage format is structurally DIFFERENT from Python's coverage.py
(block-based, not line-based -- P07's own successor brief, confirmed
directly against ``coverage_parsers/go_cover.py``'s already-proven parser),
which is what makes "additive" a real claim rather than a coincidence of two
similar tools. (P09, A-105/A-107) adds this adapter's two canary injection
methods, ``inject_import_break``/``inject_uncovered_line``. (P11,
A-042/A-114, migrated to the bounded seam by P21/A-183) adds
:meth:`GoAdapter.generate_mutation_sites`, unconditionally
``"UNSUPPORTED"`` -- this devcontainer has no Go toolchain anywhere (A-042),
so nothing here can prove a generated mutant is even valid Go syntax, and
O2's own negative is explicit that a text-guessed mutant standing in for
that proof is exactly the failure to avoid. ``UNSUPPORTED`` renders
payload-free ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED`` (A-183), never a
green mutation claim -- and deliberately never ``NO_MUTANTS``, which would
assert that a supported analysis ran and observed nothing mutable.

**P09's canary mechanisms, and why they are BOTH pure appends (unlike
Python's).** Go has no executable top-level statement -- a bare
``raise``-equivalent cannot be written at package level the way nyxloom's
Python mechanism does it (A-105's own citation). The Go-native equivalent of
"a side effect that runs merely by the package loading" is ``func init()``,
which the Go runtime calls automatically before any test in the package
runs; appending ``func init() { panic(...) }`` reproduces
``inject_import_break``'s own contract (any test that imports/loads the
package fails) without needing an insertion point inside the existing
source at all. ``inject_uncovered_line`` needs no such adjustment -- a
never-called top-level function, nyxloom's own mechanism, is a plain append
in Go exactly as it is in Python. Both are structurally valid, best-effort
implementations satisfying the protocol's shape (A-107): P09's own R1-only
Go canary exercises ONLY ``inject_uncovered_line`` (an inserted ``init()``
panic prevents `go test` from ever writing a coverprofile at all, the same
way a Python import-break prevents ``pytest-cov`` from writing one --
verified empirically while authoring this package -- so there is no
committed-coverprofile shape that could represent import-break's own
R0-level rejection; that half of the claim is Python's alone, A-107).

**No Go toolchain, anywhere (A-087).** This module never shells out, never
imports ``subprocess``, and is proven entirely from committed text
(``tests/fixtures/go/**``) -- this devcontainer has no Go toolchain to shell
out to even if it wanted to (DESIGN-GUIDE §10). ``external_tools = ()``: the
narrow semantic question ``has_executable_code`` answers ("does this file
declare a function with a real body") is answered by a small, deterministic,
conservative lexer ported from the REASONING (not the code -- there is
nothing to port literally, Go and Python share no syntax) of
``shared-ramdisk-depot-manager/tools/covergate/hascode.go``'s own
``HasExecutableCode``: that function parses with ``go/parser`` and walks
``f.Decls`` for a ``*ast.FuncDecl`` whose ``Body != nil``, failing closed
(``true``) on any read or parse error. This module answers the identical
semantic question -- "is there a top-level function declaration with a real
body" -- without a real Go parser, by lexing far enough to be confident and
refusing (returning ``True``, the same fail-closed direction) the moment it
is not.

**requires_span_attribution = False (A-102), settled, not assumed.** Go's
coverprofile format is BLOCK-based: every physical line in a block's
``[startLine, endLine]`` range gets its own real ``executed``/``missing``
entry (confirmed directly against ``coverage_parsers/go_cover.py``, itself
ported from ``covergate/profile.go``'s own ``ParseCoverProfile``). Python's
gap -- a multi-line statement's INTERIOR lines missing from both
``executed_lines`` and ``missing_lines`` entirely, because coverage.py
attributes a trace hit to a statement's own first physical line only -- is
structurally impossible for a format that already expands every line in a
block individually. So this adapter never needs
:meth:`~assay.adapters.base.LanguageAdapter.statement_spans` to resolve
anything, and ``evaluate.py``'s own rule 3b (gated on
``requires_span_attribution``, P07) is simply never reached for Go --
:meth:`GoAdapter.statement_spans` returns ``None`` unconditionally, costing
nothing at runtime (never called), existing only to satisfy the protocol's
structural shape (A-101).

**``has_executable_code``'s lexer, precisely (A-104).** Go source is masked
in one forward pass -- every ``//`` line comment, ``/* */`` block comment,
interpreted ``"..."`` string (with backslash escapes, no bare newline
inside -- Go's own grammar rule), raw `` `...` `` string (CAN span physical
lines, no escape processing inside), and rune ``'...'`` literal (escapes,
no bare newline inside) is replaced with same-length whitespace (preserving
real newline characters, never collapsing them) -- never simply deleted,
because deleting could fuse two real tokens that were separated only by a
comment into one accidental identifier. An unterminated comment or literal
(reaches EOF, or for a ``"``/``'`` literal a bare newline, before its own
closing delimiter) makes the WHOLE file "lexically malformed": masking
returns ``None`` and :meth:`has_executable_code` returns ``True`` (A-087's
own fail-closed direction), never a guess at what the rest of the file
might have contained.

Against the MASKED text, a single scan tracks brace depth (``{``/``}``
only) to know when it is at the file's own TOP LEVEL -- matching
:meth:`~assay.adapters.python.PythonAdapter.has_executable_code`'s own
``tree.body``-only convention (A-104's own instruction): a ``func`` word
found while nested inside any braces (a struct/interface body, another
function's own body -- including a closure literal defined INSIDE it) is
never inspected, the same way Python's top-level-only walk never descends
into a nested ``def``. At brace depth 0, every word-boundary ``func`` token
is offered to :func:`_scan_signature_for_body`, which walks the rest of
ONE declaration's signature -- an optional receiver, name, an optional
generics type-parameter list, the parameter list, optional result
type(s) -- tracking a single combined nesting counter for ``(``/``)`` and
``[``/``]`` together (A-104's own generics case: ``func f[T any](...)`` is
symmetric with the parameter list for this purpose; neither is mistaken for
the other, and neither is mistaken for the body-opening brace, which can
only be the ``{`` that arrives once this counter has returned to zero).
Finding that ``{`` is an unconditional, immediate ``True`` -- Go's own
``go/parser``-based reference only asks ``fn.Body != nil``, an empty body
still counts, and this lexer never needs to find the MATCHING close brace
at all because it stops looking the instant it has its answer. Failing to
find one before the next top-level keyword (``func``, ``type``, ``var``,
``const``, ``package``, ``import``) or end-of-file, with the nesting
counter back at exactly zero, is a real bodyless declaration -- srdm's own
assembly/cgo stub case (``func fastPath(x uint64) uint64``, no ``{`` at
all). Reaching end-of-file with the nesting counter still open, or meeting
an unmatched closing ``)``/``]``/``}`` at any point, is "structurally
unknown" and also renders ``True`` -- the same fail-closed direction as an
unterminated comment or literal, chosen for the identical reason: a
half-parsed signature is not evidence of "no code", it is evidence this
narrow lexer's confidence has run out.

**``is_test_path`` and ``normalize_coverage_key``.** Go's own test
convention is a single, already boundary-safe SUFFIX rule -- unlike
Python's ``tests/`` directory convention (dstdns's own ``(^|/)`` anchor
hazard, ``python.py``'s own module docstring), a literal
``str.endswith("_test.go")`` has no midword-collision hazard to guard
against: ``"_test.go"`` is specific enough that no ordinary source file
name ends with it by accident (confirmed directly against
``covergate/main.go``'s own ``isGoTestFile``, adopted verbatim).
``normalize_coverage_key`` mirrors
:attr:`~assay.adapters.python.PythonAdapter.coverage_key_prefix`'s own
boundary-safe prefix strip (DESIGN-GUIDE §11's own worked example is this
exact case: "the language-specific prefix STRIP -- Go's module path,
srdm's ``stripModulePrefix``"), confirmed directly against
``covergate/main.go``'s own ``stripModulePrefix``: a Go cover profile
names files by full import path (``srdm/internal/store/x.go``) while
``git diff`` names the same file relative to the repo top
(``internal/store/x.go``); :attr:`GoAdapter.module_path` names the known,
declared module-path segment to strip, firing only at an exact
``module_path + "/"`` path-segment boundary so a sibling module that
merely shares the prefix's characters (``"srdm_legacy/..."``) is never
mis-stripped.

**``excluded_dir_names`` is the empty set, not an invented ``vendor``/
``testdata`` default.** Real Go tooling does special-case both by bare
name, but ``covergate`` -- the one reference implementation this package
has for Go -- excludes neither (confirmed by grep across every ``.go`` file
in ``shared-ramdisk-depot-manager/tools/covergate/``: zero hits for either
name). DESIGN-GUIDE §5's defaults doctrine applies here exactly as
``python.py``'s own module docstring already applies it to Python: the
genuine union of the one cited reference's own behaviour is the empty set,
and inventing a "reasonable-sounding" default that no cited source actually
has is precisely the hazard §5 exists to forbid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..mutation import MutationSite
from .base import StatementSpan

__all__ = ["GoAdapter"]

#: Go identifier "word" characters -- ASCII letters, digits, and underscore.
#: Real Go also allows unicode letters in identifiers; this narrow lexer
#: only needs word-BOUNDARY safety around its own six fixed ASCII keyword
#: spellings (below), so restricting the boundary-character test to ASCII
#: is sufficient and does not need a unicode category lookup.
_WORD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)

#: Every keyword that can start a Go top-level declaration. Deliberately
#: includes ``func`` itself: a func signature scan (below) must stop at the
#: NEXT top-level ``func`` exactly the same way it stops at ``type``/``var``/
#: etc., or two adjacent bodyless declarations would fuse into one scan.
_TOP_LEVEL_KEYWORDS = ("func", "type", "var", "const", "package", "import")


def _is_word_char(ch: str) -> bool:
    return ch in _WORD_CHARS


def _top_level_keyword_at(text: str, index: int) -> str | None:
    """The member of :data:`_TOP_LEVEL_KEYWORDS` starting EXACTLY at
    *index*, word-boundary matched on both sides, or ``None``.

    Shared by the top-level scan and the signature scan (below) so both
    apply the identical boundary rule -- the same defence P06's own
    ``is_test_path`` regex anchors for Python, reproduced here at the
    character-scan level because this module has no ``re`` to anchor with:
    a bare substring search for ``"func"`` would misfire inside
    ``"myfunction"`` (before-boundary) and ``"funcName"`` (after-boundary)
    alike, the literal negative O1 defends against.
    """
    n = len(text)
    for keyword in _TOP_LEVEL_KEYWORDS:
        end = index + len(keyword)
        if text[index:end] != keyword:
            continue
        before_ok = index == 0 or not _is_word_char(text[index - 1])
        after_ok = end >= n or not _is_word_char(text[end])
        if before_ok and after_ok:
            return keyword
    return None


def _scan_quoted_literal(text: str, start: int, quote: str) -> int | None:
    """*start* indexes the OPENING *quote* character of a ``"..."`` string
    or ``'...'`` rune literal. Returns the index just past the CLOSING
    quote, or ``None`` if the literal is unterminated -- reaches an
    unescaped, literal newline or end-of-file before its own closing quote,
    Go's own grammar rule for both forms (only a raw `` `...` `` string may
    contain a bare newline; see :func:`_scan_raw_string`).

    A backslash unconditionally consumes the NEXT character too, so
    ``\\"``/``\\'`` never end the literal early and a trailing lone
    backslash at end-of-file is itself unterminated (the loop simply runs
    out without finding the closing quote).
    """
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            return None
        if ch == quote:
            return i + 1
        i += 1
    return None


def _scan_raw_string(text: str, start: int) -> int | None:
    """*start* indexes the opening `` ` `` of a raw string literal. Returns
    the index just past the closing `` ` ``, or ``None`` if end-of-file is
    reached first. Unlike :func:`_scan_quoted_literal`, a raw string may
    span physical lines and has no escape processing at all (a backslash is
    an ordinary character inside one) -- so this is a plain, unescaped
    search for the next backtick.
    """
    end = text.find("`", start + 1)
    return None if end == -1 else end + 1


def _blank(chars: list[str], start: int, end: int) -> None:
    """Overwrite ``chars[start:end]`` with spaces, except any REAL newline
    character, which is left untouched -- so a masked comment or literal
    never fuses two lines into one and never turns a genuine line break
    into a token separator it wasn't."""
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "


def _strip_comments_and_literals(text: str) -> str | None:
    """*text* with every ``//`` line comment, ``/* */`` block comment,
    ``"..."`` interpreted string, `` `...` `` raw string, and ``'...'``
    rune literal replaced by same-length whitespace (real newlines
    preserved) -- so none of their CONTENTS (a stray ``func`` word, a stray
    brace) can be mistaken for real Go structure by the scan that follows.

    Returns ``None`` when any comment or literal is unterminated -- the
    file is lexically malformed and nothing past that point can be trusted
    (A-087's fail-closed direction; the caller returns ``True``).
    """
    n = len(text)
    chars = list(text)
    i = 0
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            end = text.find("\n", i)
            if end == -1:
                end = n
            _blank(chars, i, end)
            i = end
            continue
        if two == "/*":
            close = text.find("*/", i + 2)
            if close == -1:
                return None
            end = close + 2
            _blank(chars, i, end)
            i = end
            continue
        ch = text[i]
        if ch == '"':
            end = _scan_quoted_literal(text, i, '"')
            if end is None:
                return None
            _blank(chars, i, end)
            i = end
            continue
        if ch == "'":
            end = _scan_quoted_literal(text, i, "'")
            if end is None:
                return None
            _blank(chars, i, end)
            i = end
            continue
        if ch == "`":
            end = _scan_raw_string(text, i)
            if end is None:
                return None
            _blank(chars, i, end)
            i = end
            continue
        i += 1
    return "".join(chars)


def _scan_signature_for_body(masked: str, start: int) -> tuple[int, bool]:
    """From *start* (immediately after a top-level ``func`` keyword) scan
    the rest of ONE declaration's signature for the body-opening ``{``.

    *masked* has already had every comment and literal blanked (this
    function never re-derives that). A single combined counter tracks
    ``(``/``)`` and ``[``/``]`` nesting together (A-104: a receiver, a
    generics type-parameter list, the parameter list, and a parenthesised
    multi-value result all use one or the other, and none may be mistaken
    for the body brace, which can only be the ``{`` found once this counter
    reads exactly zero).

    Returns ``(new_index, True)`` the instant a body is found OR the
    signature turns out structurally unknown (an unmatched closing
    ``)``/``]``/``}``, or the counter never returns to zero before
    end-of-file) -- *new_index* is never consulted in this branch, because
    the caller short-circuits its own scan the moment it sees ``True``.
    Returns ``(new_index, False)`` at the next top-level keyword, or at
    end-of-file with the counter back at zero: a genuinely bodiless
    declaration (srdm's assembly/cgo stub case). *new_index* there is the
    UNCONSUMED position the caller's own scan resumes from -- the keyword
    itself, never past it.
    """
    depth = 0
    i = start
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch in "([":
            depth += 1
            i += 1
            continue
        if ch in ")]":
            depth -= 1
            if depth < 0:
                return i + 1, True  # unmatched closer -- structurally unknown
            i += 1
            continue
        if ch == "{" and depth == 0:
            return i + 1, True
        if ch == "}" and depth == 0:
            return i + 1, True  # stray closer -- structurally unknown
        if depth == 0 and ch.isalpha() and _top_level_keyword_at(masked, i):
            return i, False
        i += 1
    return n, depth != 0  # EOF: bodiless only if the signature ever closed


def _scan_for_top_level_func_body(masked: str) -> bool:
    """``True`` iff *masked* (already comment/literal-stripped) contains at
    least one TOP-LEVEL ``func`` declaration with a real body.

    Tracks brace depth (``{``/``}`` only) to know when it is at the file's
    own top level: a ``func`` word found while brace-nested (inside a
    struct/interface body, or inside another function's own body --
    including a closure literal defined there) is never inspected at all,
    matching :meth:`~assay.adapters.python.PythonAdapter.has_executable_code`'s
    own ``tree.body``-only convention (A-104). Short-circuits ``True`` the
    moment any qualifying declaration is found -- correctness never
    requires walking the rest of the file once the answer is known.
    """
    depth = 0
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth < 0:
                return True  # unmatched closer -- structurally unknown
            i += 1
            continue
        if depth == 0 and ch.isalpha():
            keyword = _top_level_keyword_at(masked, i)
            if keyword == "func":
                new_index, has_body = _scan_signature_for_body(masked, i + 4)
                if has_body:
                    return True
                i = new_index
                continue
        i += 1
    return False


def _has_top_level_func_body(text: str) -> bool:
    """The whole narrow question :meth:`GoAdapter.has_executable_code`
    answers, isolated as a pure function of *text* alone (no ``rel_path``
    dependency, matching :meth:`~assay.adapters.python.PythonAdapter.
    has_executable_code`'s own shape).

    An embedded NUL byte is treated the same way
    :meth:`~assay.adapters.python.PythonAdapter.has_executable_code` treats
    one (a real, if unusual, unreadable-text signal) -- fail closed,
    ``True``, before any lexing is attempted at all.
    """
    if "\x00" in text:
        return True
    masked = _strip_comments_and_literals(text)
    if masked is None:
        return True
    return _scan_for_top_level_func_body(masked)


#: P09's canary snippets (A-105/A-107). Both are plain trailing appends --
#: see this module's own docstring for why Go's ``inject_import_break`` must
#: differ in SHAPE from Python's ``inject_import_break`` (no executable
#: top-level statement exists to insert one into) while still matching the
#: SAME contract nyxloom's own mechanism established: a side effect that
#: fires merely by the package loading/being imported.
_IMPORT_BREAK_SNIPPET = (
    '\n\nfunc init() {\n\tpanic("assay-canary-import-break")\n}\n'
)
_UNCOVERED_CANARY_FUNC = "_assayCanaryUnreached"
_UNCOVERED_CANARY_SNIPPET = (
    f"\n\nfunc {_UNCOVERED_CANARY_FUNC}(value int) int {{\n"
    "\tdoubled := value * 2 // assay-canary: executed by no test\n"
    "\treturn doubled\n"
    "}\n"
)


def _append_snippet(text: str, snippet: str) -> str:
    """*text* plus *snippet*, guaranteeing exactly one trailing newline on
    *text* first (a guaranteed-clean append boundary, mirroring
    :mod:`~assay.adapters.python`'s own ``_inject_uncovered_line``) -- never
    a whole-file reformat."""
    body = text
    if body and not body.endswith("\n"):
        body += "\n"
    return body + snippet


def _inject_import_break(text: str) -> tuple[str, str]:
    """Append :data:`_IMPORT_BREAK_SNIPPET` -- a package-``init()`` that
    panics -- to *text*. Pure: returns the transformed text and a
    description; never touches a filesystem (A-010). Structurally valid,
    best-effort (A-107): not exercised by this package's own R1-only Go
    canary (see this module's docstring)."""
    return _append_snippet(text, _IMPORT_BREAK_SNIPPET), (
        "appended a package `func init() { panic(...) }` -- Go has no "
        "executable top-level statement, so import-break is modelled as a "
        "panicking init() rather than nyxloom's bare `raise` (A-105)"
    )


def _inject_uncovered_line(text: str) -> tuple[str, str]:
    """Append :data:`_UNCOVERED_CANARY_SNIPPET` -- a never-called top-level
    function -- to *text*. Pure, same contract as :func:`_inject_import_break`."""
    return _append_snippet(text, _UNCOVERED_CANARY_SNIPPET), (
        f"appended never-called `func {_UNCOVERED_CANARY_FUNC}` "
        "(2 uncovered lines) at end of file"
    )


@dataclass(frozen=True, kw_only=True)
class GoAdapter:
    """The Go :class:`~assay.adapters.base.LanguageAdapter` (P08), the
    second real adapter implemented against the frozen protocol (A-097,
    extended by P07/A-101, P09/A-105 and P11/A-114 -- this package modifies
    none of those extensions, only adds the methods each later package
    reserved for it).
    """

    name: str = "go"
    source_globs: tuple[str, ...] = ("*.go",)
    excluded_dir_names: frozenset[str] = frozenset()
    #: Go's coverprofile format is block-based and structurally lacks
    #: Python's multi-line-statement gap (A-102) -- see this module's own
    #: docstring for the confirmation against the real parser.
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    #: A declared, known module-path segment a coverage artifact's own keys
    #: carry that the diff's spelling does not (Go's own analogue of
    #: :attr:`~assay.adapters.python.PythonAdapter.coverage_key_prefix`,
    #: mirroring ``covergate/main.go``'s own ``stripModulePrefix``). Empty
    #: means nothing to strip.
    module_path: str = ""

    def is_test_path(self, rel_path: str) -> bool:
        return rel_path.endswith("_test.go")

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return _has_top_level_func_body(text)

    def normalize_coverage_key(self, key: str) -> str:
        prefix = self.module_path
        if not prefix or not key.startswith(prefix + "/"):
            return key
        return key[len(prefix) + 1 :]

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        return None

    def inject_import_break(self, text: str) -> tuple[str, str]:
        return _inject_import_break(text)

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        return _inject_uncovered_line(text)

    def generate_mutation_sites(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """Unconditionally ``"UNSUPPORTED"`` (A-042/A-183) -- see this
        module's own docstring. No argument is consulted: there is no
        partial, best-effort Go mutation engine to fall back to, the same
        "no Go toolchain, ever" boundary that already makes this adapter's
        ``external_tools`` empty rather than declaring one it could never
        satisfy in this devcontainer.

        **P21 migrates only the signature and the imported type** (A-183).
        The behaviour is unchanged and deliberately so: the marker now
        renders as payload-free ``INCONCLUSIVE/MUTATION_UNSUPPORTED``
        instead of being collapsed into ``NO_MUTANTS``, which is what keeps
        "assay cannot analyse Go" distinguishable from "assay analysed Go
        and found nothing mutable". P29 replaces this return with real
        bounded sites; P30 makes that capability reachable through the
        registry. Until then Go is not registered for R2 at all.
        """
        return "UNSUPPORTED"
