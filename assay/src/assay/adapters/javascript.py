"""The JavaScript/TypeScript :class:`~assay.adapters.base.LanguageAdapter`
(B036) — the FOURTH real adapter, and the second (after Go, P08) to be added
without touching one line of the frozen protocol
(:mod:`assay.adapters.base`), the language-free core (:mod:`assay.evaluate`)
or the registry (:mod:`assay.registry`).

**One adapter for the whole ``.js``/``.jsx``/``.ts``/``.tsx`` family, named
``"javascript"`` (A-340).** ``judge.language`` names a language the way
``"python"`` and ``"go"`` already do — not a dialect, a runtime, or a file
extension. TypeScript is JavaScript's own superset and JSX/TSX are syntax
extensions of the two; all four compile to the same runtime language, are
measured by the same coverage tools into the same artifact
(``coverage-final.json``, whose records make no distinction between them), and
share every rule this adapter states. Splitting them would force a lane
touching one ``.ts`` file and one ``.tsx`` file to declare two languages for
one measurement.

**``requires_span_attribution = False``, settled by a real probe, not assumed
(A-342).** ``adapters/go.py``'s own header records that its identical claim
was only settled after A-172's probe found the premise behind it wrong once
already; this one was measured before it was written. Real
``vitest run --coverage`` output was generated for both of Vitest's coverage
providers against ``tests/fixtures/coverage/probe-js`` (see that directory's
own provenance section):

* ``@vitest/coverage-v8`` (Vitest 3) emits one single-line ``statementMap``
  entry per executable physical line;
* ``@vitest/coverage-istanbul`` emits REAL statement extents, several of them
  multi-line (``format.ts``'s own ``[24, 32]`` array literal and ``[33, 37]``
  loop), which is exactly ``coverage.py``'s multi-line-statement gap in a
  different format.

The second shape is closed by :mod:`assay.coverage_parsers.coverage_istanbul_json`
itself, which expands each statement's own extent across its lines with
innermost-wins resolution — a fact the ARTIFACT carries, not one this adapter
would have to re-derive. :meth:`JavaScriptAdapter.statement_spans` therefore
returns ``None`` unconditionally, existing only to satisfy the protocol's
structural shape (A-101), exactly as Go's does. The alternative —
``True`` plus a real ``statement_spans`` — would require a TypeScript/JSX
parser written in Python from scratch, which is the categorically larger
undertaking B037's own scope boundary exists to rule on.

**The guarantee this rests on, stated exactly (A-342, corrected by the
round-1 review's M2).** It is NOT "no line of a measured file is
unattributed" — that is false, and measured false: in the committed
``@vitest/coverage-istanbul`` artifact 23 non-comment lines across six files
sit in neither ``executed`` nor ``missing``, because that instrumenter
records no statement for a function's own signature line, for a
function-level closing brace, or for a ``const x =`` line whose recorded
statement is its initialiser. Those lines take ``evaluate.py``'s rule 4 and
leave the denominator, exactly as an untracked line does for every other
format in this registry. The real guarantee is narrower and is what
``requires_span_attribution = False`` actually needs: **every line any
statement extent covers is classified, so no line is left unattributed that
this artifact carried the information to attribute.** Span attribution
(rule 3b) exists to recover interior lines of a multi-line construct from a
re-parse; the extent expansion already recovers precisely those, from the
artifact, which is why a ``statement_spans`` implementation would have
nothing left to do here.

**``has_executable_code`` answers ONE narrow question and refuses the moment
its confidence runs out (A-343).** Consulted only for a changed, considered,
non-test source file with NO entry at all in the coverage artifact. Two cases
are decided, both measured:

* a ``.d.ts``/``.d.mts``/``.d.cts`` **declaration file** has zero executable
  code by TypeScript's own grammar (a declaration file may only declare
  types), and neither provider reports one at all — ``probe-js/src/types.d.ts``
  is absent from both real artifacts. This is the NoCode case, an empty
  ``__init__.py``'s exact analogue, and it is decided from the path alone
  because the language's grammar decides it, not the file's contents;
* a file whose text is EMPTY, or contains nothing but whitespace and comments,
  likewise has nothing to instrument.

Everything else answers ``True``, fail-closed (srdm's asymmetry: a wrong
``False`` is a silent excuse; a wrong ``True`` is at worst a visible false
failure). In particular a **type-only ``.ts`` module** (``export type`` /
``interface`` only, no runtime value) is deliberately NOT claimed as
code-free: deciding it needs real TypeScript type-erasure semantics — a
hand-written TS parser, the same overreach the paragraph above declines — and
under the provider this build is wired for it never arises, because
``@vitest/coverage-v8`` DOES report such a module, with an empty
``statementMap`` (measured: ``probe-js/src/typesonly.ts``, zero statements),
which reaches evaluation as a real record contributing zero executable lines
rather than as an absent one. Under ``@vitest/coverage-istanbul`` the same
module is absent, and a changed type-only module would then be reported as
missing coverage — the visible direction, and B038's own follow-up.

**``is_test_path`` follows Vitest's own default ``include`` glob.** Vitest's
documented default is ``['**/*.{test,spec}.?(c|m)[jt]s?(x)']``, so a filename
carrying a ``.test.``/``.spec.`` segment before its extension is a test file;
``__tests__/`` is added as the one directory convention the ecosystem shares
with Jest's own default ``testMatch``. Both are anchored: the directory rule
fires only on a whole path segment (``(^|/)__tests__/``), never on a
``my__tests__helpers/`` prefix, the same boundary discipline
:data:`assay.adapters.python._TEST_FILE_RE` applies to ``tests/``. Nothing
beyond those two is invented (DESIGN-GUIDE §5): a project whose tests live in
a plain ``tests/`` directory with ordinary filenames is not covered by any
convention this adapter can cite, and would have to keep them out of its
declared source roots.

**``excluded_dir_names`` names three directories, each with a real source.**
``node_modules`` is npm's own universal, gitignored dependency tree (and the
literal example ``adapters/base.py``'s own protocol docstring gives for this
attribute); ``dist`` is Vite's documented default ``build.outDir``, confirmed
directly against the first real consumer's own ``vite.config.ts``
(``applications/webapp-ui-react``, which sets it explicitly); ``coverage`` is
Vitest's own default ``coverage.reportsDirectory``. All three hold GENERATED
output that a coverage artifact can never meaningfully measure. Nothing else
is added — ``build``, ``out``, ``.next`` and friends are real defaults of
OTHER toolchains that no source read for this adapter uses, and inventing a
reasonable-sounding default no cited source actually has is precisely the
hazard DESIGN-GUIDE §5 exists to forbid.

**``normalize_coverage_key`` returns its key unchanged, and that is the
finding, not an omission (A-341).** Istanbul keys every record by ABSOLUTE
filesystem path — a different mismatch shape from Go's package-qualified
import path. It needs no adapter-side strip, because
:func:`assay.evaluate._to_repo_relative_key` already resolves an absolute
coverage key against ``repo_top`` and returns its repo-relative identity
(B006/A-145's own absolute-key branch, added for real ``coverage.py``'s
absolute-path fallback). That is the universal prefix-BOUNDARY reconciliation
DESIGN-GUIDE §11 puts in the core; there is no LANGUAGE-specific prefix here
for an adapter to strip, so this method does what the protocol says an adapter
with nothing to strip does: returns *key* unchanged.

**Canary injection: both mechanisms are plain trailing appends (A-345).**
JS/TS has an executable module top level like Python, so
``inject_import_break`` could reproduce nyxloom's insert-after-the-leading-
prologue shape — but an ES module's ``import`` declarations are HOISTED and
its imported modules evaluated before any of its own body runs, so a
``throw`` appended at the end of the file still fires during module
evaluation, before any test can touch a single export. Appending is therefore
exactly as faithful to the contract ("reliably tripped by merely
importing/loading the module") while needing none of Python's
docstring/``__future__`` insertion-point logic — the same reason
``adapters/go.py`` appends both of its own. Both snippets are written in the
subset that is valid in ``.js``, ``.jsx``, ``.ts`` and ``.tsx`` alike, because
these methods receive only *text* and never a path: no type annotations (a
``.js`` file would be a syntax error), and the canary function's parameter
carries a DEFAULT rather than a type, so TypeScript infers ``number`` and
``noImplicitAny`` has nothing to complain about. The function is ``export``ed
for the same reason: an unexported, never-referenced declaration is what
``noUnusedLocals`` flags, and the protocol asks for a lint-clean addition.

**``generate_mutation_sites`` is unconditionally ``"UNSUPPORTED"`` (A-183's
own marker, Go's precedent).** Whether JS/TS mutation should be native or
should ingest an external producer's evidence was **B037**, and the ruling is
now MADE: JavaScript R2 is INGESTED, and **B046** implements it -- the lane's
own argv runs Stryker inside the private snapshot and assay judges the report
it wrote. So this method staying ``"UNSUPPORTED"`` is not an open question
waiting on one; it is what makes this the ingested path, and
``test_the_registry_does_not_open_the_NATIVE_r2_path`` holds the line. An
absent NATIVE capability still renders payload-free
``INCONCLUSIVE``/``MUTATION_UNSUPPORTED`` rather than a green mutation claim
or a ``NO_MUTANTS`` that would assert an analysis ran, for any lane that does
not declare ``judge.mutation.format`` -- presence of that key, and nothing
else, selects ingestion. ``external_tools = ()``
for the same reason it is empty for Go: this module never shells out, never
imports ``subprocess``, and does no work a toolchain could be required for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..mutation import MutationSite
from .base import StatementSpan

__all__ = ["JavaScriptAdapter"]

#: Vitest's own default ``include`` glob (``**/*.{test,spec}.?(c|m)[jt]s?(x)``)
#: plus Jest's own ``__tests__`` directory convention, as one anchored regex.
#: The directory branch is anchored with ``(^|/)`` so a directory merely
#: CONTAINING the characters (``x__tests__/``) never matches, the identical
#: boundary defence :data:`assay.adapters.python._TEST_FILE_RE` applies to its
#: own ``tests/`` branch. The filename branch requires a real ``.`` before
#: ``test``/``spec`` so ``latest.ts`` and ``respec.ts`` cannot match, and
#: accepts the ``.cjs``/``.mjs``/``.cts``/``.mts`` spellings Vitest's own glob
#: accepts even though :attr:`JavaScriptAdapter.source_globs` does not yet
#: name them -- a test-path rule that is broader than the source globs can
#: only ever exclude MORE, never mis-include.
_TEST_FILE_RE = re.compile(
    r"(^|/)(__tests__/|[^/]+\.(test|spec)\.(c|m)?[jt]sx?$)"
)

#: TypeScript declaration-file suffixes. A declaration file may contain only
#: ambient declarations by TypeScript's own grammar, so it has zero
#: instrumentable code -- the NoCode case, decided from the path alone.
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")

#: P09's canary snippets, ported to the JS/TS module top level (A-345 -- see
#: this module's own docstring for why both are appends and why neither
#: carries a type annotation).
_IMPORT_BREAK_SNIPPET = (
    '\n\nthrow new Error("assay-canary-import-break")\n'
)
_UNCOVERED_CANARY_FUNC = "_assayCanaryUnreached"
_UNCOVERED_CANARY_SNIPPET = (
    f"\n\nexport function {_UNCOVERED_CANARY_FUNC}(value = 0) {{\n"
    "  const doubled = value * 2 // assay-canary: executed by no test\n"
    "  return doubled\n"
    "}\n"
)


def _strip_comments(text: str) -> str | None:
    """*text* with every ``//`` line comment and ``/* */`` block comment
    replaced by same-length whitespace (real newlines preserved), or ``None``
    when a block comment is never closed.

    Narrower than :func:`assay.adapters.go._strip_comments_and_literals` on
    purpose: its ONE caller (:func:`_has_executable_code`) only asks whether
    any non-comment, non-whitespace character survives, and a string or
    template literal's own CONTENT is such a character regardless of what it
    spells. Masking literals too would therefore change no answer, while
    requiring this module to carry JS's template-literal-with-``${}``-
    substitution grammar -- real parsing work with no question depending on
    it. Comment delimiters appearing INSIDE a literal (``const u = "http://x"``)
    are the one shape this narrowness mis-reads, and it mis-reads them in the
    fail-closed direction: the masked tail is blanked, and a file whose only
    other content is that literal still answers ``True`` because the literal's
    own opening quote survives ahead of the mask.

    An unterminated block comment returns ``None`` and the caller answers
    ``True`` (A-087's own fail-closed direction, adopted verbatim): a file
    this scan cannot finish reading is never waved through as code-free.
    """
    chars = list(text)
    i = 0
    n = len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            end = text.find("\n", i)
            if end == -1:
                end = n
        elif two == "/*":
            close = text.find("*/", i + 2)
            if close == -1:
                return None
            end = close + 2
        else:
            i += 1
            continue
        for index in range(i, end):
            if chars[index] != "\n":
                chars[index] = " "
        i = end
    return "".join(chars)


#: The two source spellings that can carry TypeScript type syntax without
#: being a declaration file. ``.js``/``.jsx`` are deliberately absent: neither
#: `interface` nor `type X = ...` is JavaScript, so a `.js` file containing
#: them is not a type-only module -- it is a file that will not run, and
#: calling it code-free would hide that.
_TYPE_ONLY_SUFFIXES = (".ts", ".tsx")

#: The exact statement openings B038(b) enumerates, and nothing else. Each is
#: matched as a literal PREFIX of a top-level statement, and each carries a
#: TRAILING SPACE for a reason found by probing rather than by reading: without
#: it, `export type` is a prefix of `export typeGuard = 1`, and a runtime
#: assignment would have been classified as a type declaration -- a fail-OPEN
#: answer, the one direction this lexer must never give. The trailing space is
#: the whole of the word-boundary rule; `type ` likewise cannot match `typeof`
#: or an identifier named `types`.
#:
#: A declaration separating its keywords by anything other than one space
#: (`export  type X`, or a newline between them) is not recognised and answers
#: "has code", per the fail-closed rule.
_TYPE_ONLY_STATEMENT_PREFIXES = (
    "import type ",
    "export type ",
    "export interface ",
    "type ",
    "interface ",
    "declare ",
)

_QUOTES = "'\"`"
_OPENERS = {"{": "}", "(": ")", "[": "]"}
_CLOSERS = {"}", ")", "]"}


def _has_executable_code(rel_path: str, text: str) -> bool:
    """The whole narrow question :meth:`JavaScriptAdapter.has_executable_code`
    answers (this module's own docstring): ``False`` for a TypeScript
    declaration file, for text that is empty once comments and whitespace are
    removed, and — B038(b)/B045 — for a ``.ts``/``.tsx`` module whose every
    top-level statement is a TYPE declaration. ``True`` for everything else,
    including anything this scan cannot finish reading."""
    if rel_path.endswith(_DECLARATION_SUFFIXES):
        return False
    masked = _strip_comments(text)
    if masked is None:
        return True
    if not masked.strip():
        return False
    if rel_path.endswith(_TYPE_ONLY_SUFFIXES) and _is_type_only(masked):
        return False
    return True


def _is_type_only(masked: str) -> bool:
    """Whether comment-masked *masked* is a TypeScript module that declares
    only types (B038(b)).

    **The problem this exists to close, measured.** A ``.ts`` module holding
    nothing but ``import type``/``interface``/``type`` declarations is erased
    entirely by the TypeScript compiler, so no instrumenter emits a record
    for it and it is absent from every coverage artifact. Under
    ``evaluate.py``'s rule 4 an absent CHANGED file whose adapter says it has
    executable code is a measurement FAILURE -- correct for a file the tests
    forgot, and a false failure for a file that cannot be executed at all.
    ``tests/fixtures/coverage/probe-js/src/typesonly.ts`` is the witness;
    ``orphan.ts``, one runtime ``export function`` and otherwise identical in
    shape, is the control that must keep answering ``True``.

    **This is a lexer, not a TypeScript parser (A-104, Go's own
    ``has_executable_code`` discipline).** It splits *masked* into top-level
    statements -- at ``;`` and at newlines, both only where brace/paren/
    bracket depth is zero and no string literal is open -- and answers
    ``True`` (has code) unless EVERY non-empty statement begins with one of
    :data:`_TYPE_ONLY_STATEMENT_PREFIXES`. Every construct it does not
    recognise therefore answers "has code", which is the fail-closed
    direction: a type-only module wrongly called executable becomes a VISIBLE
    unmeasured-file failure a consumer can read and act on, while a runtime
    module wrongly called code-free would silently vanish from both the
    numerator and the denominator -- srdm's silent-excuse direction, the one
    this project refuses.

    **Its known limitation, stated rather than hidden.** A single declaration
    spread over several top-level lines::

        export type Mode =
          | 'read'
          | 'write'

    splits into three statements, of which the last two begin with ``|``, so
    the file answers ``True``. Recovering it needs to know where a type
    expression ENDS, which is the TypeScript grammar B038(b) explicitly
    declines to implement. The cost is one visible false failure that a
    consumer fixes by joining the lines or excluding the file; the benefit is
    that this function has no grammar to be wrong about.
    """
    statements = _top_level_statements(masked)
    if statements is None:
        return False
    for statement in statements:
        if not statement.startswith(_TYPE_ONLY_STATEMENT_PREFIXES):
            return False
    return True


def _top_level_statements(masked: str) -> list[str] | None:
    """Comment-masked *masked* split into stripped, non-empty statements at
    depth-zero ``;`` and newlines, or ``None`` when the scan cannot lex it.

    String and template literals are skipped so that a bracket inside one
    (``type Brace = '{'``) cannot unbalance the depth count.

    ``None`` -- which the caller turns into "has code" -- is returned for
    every input this scan cannot follow to the end: an unterminated literal, a
    template literal carrying a ``${`` substitution, more closing brackets
    than opening ones, or a file that ends with a bracket still open.
    Returning a best-effort split for those was the FIRST version of this
    function and it was fail-OPEN, found by probing rather than by reading:
    ``export type A = `x${'y'}z` `` followed by a real ``console.log(1)``
    swallowed the runtime statement into the type declaration's own segment,
    and the file came back code-free. There is no partial credit here -- if
    the scan loses the structure anywhere, it has no basis for a claim about
    any statement.
    """
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    length = len(masked)
    while index < length:
        char = masked[index]
        if char in _QUOTES:
            end = _skip_literal(masked, index)
            if end is None:
                return None
            current.append(masked[index:end])
            index = end
            continue
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth -= 1
            if depth < 0:
                return None
        if depth == 0 and (char == ";" or char == "\n"):
            statements.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if depth != 0:
        return None
    statements.append("".join(current).strip())
    return [statement for statement in statements if statement]


def _skip_literal(masked: str, start: int) -> int | None:
    """The index just past the string/template literal opening at *start*, or
    ``None`` if it is unterminated or is a template carrying a ``${``
    substitution."""
    quote = masked[start]
    index = start + 1
    length = len(masked)
    while index < length:
        char = masked[index]
        if char == "\\":
            index += 2
            continue
        if quote == "`" and masked[index : index + 2] == "${":
            return None
        if char == quote:
            return index + 1
        if char == "\n" and quote != "`":
            # An ordinary JS string cannot span a newline; this is not the
            # literal it looked like.
            return None
        index += 1
    return None


def _append_snippet(text: str, snippet: str) -> str:
    """*text* plus *snippet*, guaranteeing exactly one trailing newline on
    *text* first -- a clean append boundary, never a whole-file reformat
    (:func:`assay.adapters.go._append_snippet`'s own shape)."""
    body = text
    if body and not body.endswith("\n"):
        body += "\n"
    return body + snippet


def _inject_import_break(text: str) -> tuple[str, str]:
    """Append :data:`_IMPORT_BREAK_SNIPPET` -- a top-level ``throw`` -- to
    *text*. Pure: returns the transformed text and a description; never
    touches a filesystem (A-010)."""
    return _append_snippet(text, _IMPORT_BREAK_SNIPPET), (
        "appended a module-top-level `throw new Error(...)`; an ES module's "
        "imports are hoisted, so this fires during module evaluation for any "
        "test that imports the file (A-345)"
    )


def _inject_uncovered_line(text: str) -> tuple[str, str]:
    """Append :data:`_UNCOVERED_CANARY_SNIPPET` -- a never-called, exported,
    side-effect-free top-level function -- to *text*. Pure, same contract as
    :func:`_inject_import_break`."""
    return _append_snippet(text, _UNCOVERED_CANARY_SNIPPET), (
        f"appended never-called `export function {_UNCOVERED_CANARY_FUNC}` "
        "(2 uncovered lines) at end of file"
    )


@dataclass(frozen=True, kw_only=True)
class JavaScriptAdapter:
    """The JavaScript/TypeScript :class:`~assay.adapters.base.LanguageAdapter`
    (B036): five attributes and seven methods against the protocol frozen by
    A-097 and extended by P07/A-101, P09/A-105 and P21/A-183 -- this adapter
    modifies none of them.
    """

    name: str = "javascript"
    #: ``.js``/``.jsx``/``.ts``/``.tsx`` -- the four spellings B036 names and
    #: the four the first real consumer actually has. ``.mjs``/``.cjs``/
    #: ``.mts``/``.cts`` are real, legal spellings no source read for this
    #: adapter uses, so they are deliberately absent rather than guessed in
    #: (DESIGN-GUIDE §5); adding one is a one-line change the day a consumer
    #: has one. A ``.d.ts`` file matches ``*.ts`` here and is classified as
    #: code-free by :meth:`has_executable_code` rather than being made
    #: invisible: a declaration file IS this adapter's source, it simply has
    #: no executable content, and those are different facts.
    source_globs: tuple[str, ...] = ("*.js", "*.jsx", "*.ts", "*.tsx")
    #: Generated trees, each with a real cited source -- see this module's own
    #: docstring for which, and for what was deliberately NOT added.
    excluded_dir_names: frozenset[str] = frozenset(
        {"node_modules", "dist", "coverage"}
    )
    #: Measured, not assumed (A-342): the istanbul parser expands each
    #: statement's own extent, so a measured file leaves no unattributed line
    #: for rule 3b to resolve. See this module's own docstring for the probe.
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()

    def is_test_path(self, rel_path: str) -> bool:
        return bool(_TEST_FILE_RE.search(rel_path))

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return _has_executable_code(rel_path, text)

    def normalize_coverage_key(self, key: str) -> str:
        """*key* unchanged. Istanbul's absolute-path keys are reconciled by
        the CORE's own :func:`assay.evaluate._to_repo_relative_key`, not here
        -- this module's docstring states why there is no language-specific
        prefix to strip at all."""
        return key

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
        """Unconditionally ``"UNSUPPORTED"`` (A-183's marker, Go's own
        precedent at ``adapters/go.py``). No argument is consulted: there is
        no partial JS/TS mutation engine to fall back to, because **B037's
        ruling is MADE and it went the other way** -- JavaScript R2 is
        INGESTED, and **B046** implements it (the lane's own argv runs Stryker
        inside the private snapshot and assay judges the report it wrote). So
        this method is not waiting on that ruling: staying ``"UNSUPPORTED"``
        is what MAKES this the ingested path, and
        ``test_the_registry_does_not_open_the_NATIVE_r2_path`` holds the line.
        For a lane that declares no ``judge.mutation.format`` -- presence of
        that key, and nothing else, selects ingestion -- this renders
        payload-free ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED``, never a green
        mutation claim and never ``NO_MUTANTS``, which would assert that a
        supported analysis ran and observed nothing mutable."""
        return "UNSUPPORTED"
