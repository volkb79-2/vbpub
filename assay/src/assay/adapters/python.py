"""The Python :class:`~assay.adapters.base.LanguageAdapter` — the first REAL
adapter, proving the language-free core (P05) is faithful to real Python
semantics (DESIGN-GUIDE §11, A-097/A-098/A-099), (P07) recovering real
``coverage.py``'s multi-line-statement gap via :meth:`PythonAdapter.statement_spans`
(A-084/A-100/A-101), and (P09) supplying the two canary injection mechanisms
:mod:`assay.canary` drives — :meth:`PythonAdapter.inject_import_break` and
:meth:`PythonAdapter.inject_uncovered_line` (A-010/A-084/A-105).

**P09's own addition.** ``inject_import_break``/``inject_uncovered_line``
port the MECHANISM (never the file-write) of
``/workspaces/vbpub/nyxloom/src/nyxloom/gate_canary.py``'s own
``inject_import_break``/``inject_uncovered_line`` (A-105): nyxloom's versions
call ``path.write_text`` directly, which A-010 forbids here — these are pure
``(text) -> (text, description)`` functions, and the caller
(:mod:`assay.canary`'s own orchestration) does the real file I/O against a
disposable ``tmp_path``. The insertion-point logic (skip past a leading
module docstring and/or ``from __future__ import ...`` line before inserting
the canary statement) and the appended-function snippet shape are adopted
verbatim from nyxloom's own, already-proven implementation.

**Scope, precisely (A-098, and P07's own extension of it).** Real
``coverage.py`` has a well-documented gap: a trace hit is attributed to a
STATEMENT's own first physical line only, so a multi-line statement's
interior lines (a multi-line dict literal, a call spanning several lines,
...) never appear in ``executed_lines`` NOR ``missing_lines`` at all.
:meth:`has_executable_code` — and every construct family it inspects
(decorators, async/compound-statement headers, docstrings, comments, pragma
tokens) — resolves from a SINGLE reported line, never a span; that was P06's
whole, deliberately narrower, job. Recovering the INTERIOR lines needs an AST
statement-span walk correlated back against the coverage artifact:
:meth:`statement_spans` (P07's one deliberate addition to the frozen
protocol, A-084/A-097) is that walk. ``requires_span_attribution = True``
below is this adapter's own honest declaration that real Python has this
gap; :mod:`assay.evaluate` consults the flag before ever calling
:meth:`statement_spans`, so a hypothetical adapter declaring ``False`` incurs
no cost from implementing it.

**The union, and where each reference actually diverges (Work item 2).**
Three sibling gates were read in full for this package: dstdns
(``scripts/coverage_gate.py``), topos (``tools/coverage_gate.py``), nyxloom
(``src/nyxloom/coverage_gate.py``).

* ``is_test_path`` — dstdns is the SOLE holder
  (``_TEST_FILE_RE``/``_is_test_path``); topos and nyxloom have no
  equivalent at all (confirmed by grep — neither file mentions a test-path
  concept anywhere). The union is therefore dstdns's rule verbatim, adopted
  below as :data:`_TEST_FILE_RE`.
* Pragma/exclusion handling — all three agree line-for-line: a changed line
  the coverage artifact reports in ``excluded_lines`` fails the gate unless
  ``--allow-excluded``/``allow_excluded`` was passed. This is entirely
  ``evaluate.py``'s rule 3 (already proven in P05) plus the coverage-format
  parser's own ``excluded`` field (already proven in P03) — nothing for this
  adapter to compute; the pragma TOKEN itself never needs adapter-side
  recognition.
* ``statement_spans``/decorator+match-case recovery — only dstdns has this;
  A-098 reserved it for P07, which is this file's own current addition
  (below), adopted from dstdns's ``statement_spans``/``attribute_line``
  mechanism with one shape change (A-101): spans are ``StatementSpan``
  instances, never dstdns's bare ``(lineno, end_lineno)`` tuples.
* Directory-name exclusion — NONE of the three references excludes any
  directory by bare name (confirmed by grep across all three for
  ``__pycache__``/``.venv``/``vendor``/``node_modules``/etc. — zero hits).
  Per DESIGN-GUIDE §5's defaults doctrine ("never invent" a fact no source
  actually supplies), :data:`PythonAdapter.excluded_dir_names` is the
  genuine union of three empty sets: ``frozenset()``. In practice this
  costs nothing — ``__pycache__``/``.venv`` are gitignored in every cited
  project, so ``git diff`` can never surface a changed file under them
  regardless of what this set contains.

**``has_executable_code`` — the one place this adapter classifies file
CONTENT.** Consulted by ``evaluate.py`` ONLY for a considered, adapter-
recognised, non-test file with NO entry at all in the coverage artifact
(srdm's NoCode distinction, restated in ``adapters/base.py``'s own
docstring). Decided with ``ast.parse`` per the P05 brief's own guidance:
a real ``SyntaxError``/``ValueError`` (the latter for e.g. embedded NUL
bytes, the same pair dstdns's own ``statement_spans`` catches) is treated as
``True`` — conservatively assume there IS code rather than silently excusing
an unparseable file, srdm's own asymmetry lesson (a wrong ``False`` is a
silent excuse; a wrong ``True`` is at worst a false failure, and a file this
adapter cannot even parse is never something to wave through as
"expectedly code-free"). A bare docstring expression statement — module,
class, or (in principle) a stray string literal used as an inline comment —
is walked past exactly as ``coverage.py`` itself never traces one: a
docstring is a real ``ast.Expr`` node with a real line, but it is documented
here as belonging to the SAME "not executable" category as a ``#`` comment
or a blank line, matching :mod:`assay.evaluate`'s own rule 4. Only TOP-LEVEL
module statements are inspected (``tree.body``, not a full ``ast.walk``):
a module whose top level is empty-or-docstring-only cannot have nested
executable code either — nesting requires a top-level statement (a ``def``,
``class``, ``if``, ...) to contain it, and any such top-level statement is
itself no longer a bare docstring and already trips this method to
``True`` without needing to look inside it.

**``normalize_coverage_key`` — the language-specific prefix STRIP (A-099).**
DESIGN-GUIDE §11 draws this split explicitly: the prefix-BOUNDARY
reconciliation (is this path under a declared source root) is universal and
lives in ``evaluate.py``'s ``_is_considered``
(``Path.is_relative_to`` — this package has no scope to touch it and does
not re-test it, A-099's own reading); the language-specific prefix STRIP —
Go's module path, srdm's ``stripModulePrefix`` — is an adapter hook. The
real-world Python analogue: this protocol's own repo-top spelling is fixed
by ``git diff``'s own cwd (DESIGN-GUIDE's path contract), but a coverage
artifact consumed alongside it can come from a WIDER cwd than that repo
top — e.g. a shared CI job ran ``coverage run`` from a monorepo checkout
one level above a project that adopts assay standalone with that project's
own directory as ITS repo top (DESIGN-GUIDE §9's self-hosting pattern; this
very estate's own gate invocations already `cd` into a project directory
before running ``pytest``/``coverage`` — see DESIGN-GUIDE §4's own quoted
``cd {worktree}/ciu && pytest`` example, the mirror-image offset). In that
shape coverage.py's own JSON key still carries the project directory's own
name on the front (``"myapp/src/foo.py"``) while ``git diff``, computed
relative to the narrower repo top, has already dropped it
(``"src/foo.py"``) — the key is the LONGER spelling, the diff is the
SHORTER one, and reconciling them is a strip, never an addition.
:attr:`PythonAdapter.coverage_key_prefix` names that known, declared
directory segment (mirroring Go's own module path, which is likewise a
declared fact from ``go.mod``, never derived from the key string alone).
The strip is boundary-safe: it only fires when *key* starts with
``coverage_key_prefix + "/"`` — an EXACT path-segment match — never a bare
``str.removeprefix``/``str.startswith`` on the prefix text alone, which
would also (wrongly) fire on a sibling directory that merely shares the
prefix's characters (e.g. configuring ``coverage_key_prefix="myapp"`` must
strip ``"myapp/pkg/mod.py"`` but must NOT touch
``"myapp_legacy/pkg/mod.py"`` — a different, unrelated project directory
that happens to start with the same letters). An adapter with nothing to
strip (the default, ``coverage_key_prefix=""``) returns *key* unchanged,
exactly as ``adapters/base.py``'s own docstring specifies.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .base import StatementSpan

__all__ = ["PythonAdapter"]

#: Adopted verbatim from dstdns/scripts/coverage_gate.py's own
#: ``_TEST_FILE_RE`` (the sole holder among the three cited reference gates
#: — topos and nyxloom have no test-path concept at all). A path is a test
#: path when it sits under a ``tests/`` segment at any depth, OR its own
#: filename starts with ``test_`` and ends in ``.py``, OR its filename is
#: exactly ``conftest.py``. The ``(^|/)`` anchor on the ``tests/`` branch is
#: what keeps a sibling directory like ``tests_data/`` (or a file
#: ``mytests/thing.py``) from mismatching — the same boundary discipline
#: ``evaluate.py``'s own source-root check applies one layer up, reproduced
#: here at the string level because this regex is this adapter's own, not a
#: retest of that already-proven mechanism (A-099's own reasoning, applied
#: to this file's other boundary-sensitive rule).
_TEST_FILE_RE = re.compile(r"(^|/)(tests/|test_[^/]*\.py$|conftest\.py$)")


def _is_bare_string_statement(node: ast.stmt) -> bool:
    """True for a bare string-literal expression statement — Python's own
    docstring convention (module/class/function/method), or a stray string
    literal used as an inline comment. ``coverage.py`` never traces one of
    these as executable at all, the same as a ``#`` comment; without this
    exclusion a docstring-only module would incorrectly register as "has
    executable code" merely because it contains a real, line-numbered AST
    node.

    SHARED between :meth:`PythonAdapter.has_executable_code` (P06, "does
    this module have ANY code") and :meth:`PythonAdapter.statement_spans`
    (P07, "which lines does this span cover") — deliberately, rather than
    re-derived (per ``assay-P06-BRIEF.md``'s own open question). Both
    questions rest on the exact same AST predicate — a bare docstring
    ``Expr`` is never coverage-tracked and never a span anchor — and P07
    found no reason for the two answers to diverge; a second, independently
    maintained copy would only risk drifting from this one with no
    behavioural benefit.
    """
    if not isinstance(node, ast.Expr):
        return False
    value = node.value
    return isinstance(value, ast.Constant) and isinstance(value.value, str)


#: Node types that can hold a NESTED block of further statements — their own
#: claimed span must stop at the first body statement, never swallow it.
#: Adopted from dstdns's own ``_COMPOUND_STATEMENT_TYPES`` (the sole holder
#: of this recovery among the three cited reference gates), with one change:
#: dstdns looks ``TryStar``/``Match`` up dynamically via ``getattr(ast, ...,
#: None)`` because it supports older Python than this project does.
#: ``pyproject.toml`` sets ``requires-python = ">=3.11"``, and both
#: ``ast.Match`` (3.10+) and ``ast.TryStar`` (3.11+) are unconditionally
#: present on every Python version this project can even install under —
#: the dynamic lookup would be a defensive branch this project's own gate
#: could never exercise either arm of as dead (AUTHORING.md §3b.D: "if a
#: line is genuinely unreachable, restructure so it does not exist").
_COMPOUND_STATEMENT_TYPES: tuple[type, ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.Match,
)


def _first_nested_line(node: ast.AST) -> int:
    """First physical line of the first NESTED statement inside a compound
    node — i.e. where that node's own header stops.

    ``body`` covers almost everything, but ``ast.Match`` has no ``body`` at
    all: its children live in ``cases``, and ``ast.match_case`` carries no
    ``lineno`` of its own, so the line must come from each case's
    ``pattern`` — always present; a ``case`` clause with no pattern is not
    syntax Python's grammar can produce. Adopted from dstdns's own helper of
    the same name; every member of :data:`_COMPOUND_STATEMENT_TYPES` REQUIRES
    a non-empty body (or, for ``Match``, a non-empty ``cases``) by Python's
    own grammar — an empty one is a ``SyntaxError`` that never reaches this
    function at all — so *candidates* is never empty here and this never
    guesses at a missing line the way returning ``None`` would invite a
    caller to.
    """
    candidates: list[int] = []
    body = getattr(node, "body", None)
    if body:
        candidates.extend(statement.lineno for statement in body)
    for case in getattr(node, "cases", None) or []:
        candidates.append(case.pattern.lineno)
        candidates.extend(statement.lineno for statement in case.body)
    return min(candidates)


def _statement_spans(text: str) -> tuple[StatementSpan, ...] | None:
    """Every statement's own physical line span in *text* (P07,
    A-084/A-101), adopted from dstdns's own ``statement_spans`` with one
    shape change: entries are :class:`~assay.adapters.base.StatementSpan`
    instances, never dstdns's bare ``(lineno, end_lineno)`` tuples.

    Returns ``None`` when *text* cannot be parsed at all (a real
    ``SyntaxError``, or a ``ValueError`` for e.g. an embedded NUL byte — the
    same pair :meth:`PythonAdapter.has_executable_code` catches) — callers
    must treat that as "cannot attribute anything in this file", never
    guess. A full :func:`ast.walk` (not just ``tree.body``, unlike
    :meth:`PythonAdapter.has_executable_code`'s own top-level-only walk)
    because an unattributed line can be the interior of a statement nested
    arbitrarily deep, not only a module-top-level one.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    spans: list[StatementSpan] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.stmt, ast.ExceptHandler)):
            continue
        if _is_bare_string_statement(node):
            continue  # docstring — never coverage-tracked, see the helper above
        # `lineno`/`end_lineno` are guaranteed present on every `ast.stmt`/
        # `ast.ExceptHandler` a successful `ast.parse()` can produce.
        lineno = node.lineno
        end_lineno = node.end_lineno
        if isinstance(node, _COMPOUND_STATEMENT_TYPES):
            first_child_line = _first_nested_line(node)
            # A one-line compound statement (`if x: return 1`) has its only
            # body statement on the SAME line as the header — clamp back up
            # to a single-line span rather than let this produce a
            # nonsensical end-before-start range.
            end_lineno = min(end_lineno, first_child_line - 1)
            if end_lineno < lineno:
                end_lineno = lineno
        spans.append(StatementSpan(start_line=lineno, end_line=end_lineno))

        # DECORATORS: a decorator is an EXPRESSION, not an `ast.stmt`, so it
        # never enters `spans` on its own above. `coverage.py` tracks only a
        # decorator's FIRST line exactly as it does a multi-line literal, so
        # a multi-line decorator's continuation lines are untracked AND
        # (without this) unattributed.
        for decorator in getattr(node, "decorator_list", None) or []:
            spans.append(
                StatementSpan(
                    start_line=decorator.lineno, end_line=decorator.end_lineno
                )
            )

        # MATCH-CASE PATTERNS: a `case` line is coverage-tracked, but a
        # pattern spanning several physical lines leaves its continuation
        # lines untracked. Giving the pattern its own span lets those
        # inherit their OWN case's status rather than the enclosing
        # `match`'s (essentially always executed), which would otherwise
        # mark a line in a never-taken branch as covered.
        for case in getattr(node, "cases", None) or []:
            spans.append(
                StatementSpan(
                    start_line=case.pattern.lineno, end_line=case.pattern.end_lineno
                )
            )
    return tuple(spans)


#: P09's canary statement (A-105/A-010) — adopted verbatim from nyxloom's own
#: ``_ASSERT_STMT``, renamed only to name this project rather than nyxloom's.
#: Inserted as the first real statement of the module body (past any leading
#: docstring/``__future__`` import, see :func:`_insertion_index_after_future`)
#: so any test that imports the module fails on import.
_IMPORT_BREAK_STATEMENT = 'raise AssertionError("assay-canary-import-break")\n'

#: P09's uncovered-canary function name/snippet (A-105/A-010) — adopted
#: verbatim from nyxloom's own ``_UNCOVERED_CANARY_FUNC``/
#: ``_UNCOVERED_CANARY_SNIPPET``, renamed only to name this project. A
#: never-called, typed, side-effect-free module-level function: its own
#: ``def`` line is reached (and so counted executed) merely by the module
#: loading, but the two body statements are executed by NO test.
_UNCOVERED_CANARY_FUNC = "_assay_canary_unreached"
_UNCOVERED_CANARY_SNIPPET = (
    f"\n\ndef {_UNCOVERED_CANARY_FUNC}(value: int) -> int:\n"
    "    doubled = value * 2  # assay-canary: executed by no test\n"
    "    return doubled\n"
)


def _insertion_index_after_future(source: str) -> int:
    """1-based line count to skip before inserting the canary statement —
    past any leading module docstring and/or ``from __future__ import ...``
    line(s), so the injected line never trips ``SyntaxError: from __future__
    imports must occur at the beginning of the file``.

    Adopted verbatim (mechanism only, A-105/A-010) from nyxloom's
    ``gate_canary._insertion_index_after_future``. Falls back to 0 (prepend)
    on anything that is not a clean docstring/future-import prefix, and on
    an unparseable source (the insertion still happens; a syntax-broken
    source is not this function's problem — it is exactly the kind of input
    :meth:`PythonAdapter.inject_import_break` is allowed to receive and
    still return something for, pure and total).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return 0
    idx = 0
    for i, node in enumerate(tree.body):
        if (
            i == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            idx = node.end_lineno
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            idx = node.end_lineno
            continue
        break
    return idx


def _inject_import_break(text: str) -> tuple[str, str]:
    """Insert :data:`_IMPORT_BREAK_STATEMENT` as the first real statement of
    *text*'s module body (MINIMAL one-line insertion — never a whole-file
    reformat). Pure: returns the transformed text and a description; never
    touches a filesystem (A-010 — nyxloom's own version calls
    ``path.write_text`` directly)."""
    lines = text.splitlines(keepends=True)
    idx = _insertion_index_after_future(text)
    lines.insert(idx, _IMPORT_BREAK_STATEMENT)
    description = f"inserted `{_IMPORT_BREAK_STATEMENT.strip()}` at line {idx + 1}"
    return "".join(lines), description


def _inject_uncovered_line(text: str) -> tuple[str, str]:
    """Append :data:`_UNCOVERED_CANARY_SNIPPET` to *text* — MINIMAL,
    additive, never a reformat. Pure, same contract as
    :func:`_inject_import_break`. A leading blank-line gap (and a
    guaranteed trailing newline on the original) keeps the appended block a
    clean addition to the file's existing content, mirroring nyxloom's own
    ``inject_uncovered_line``."""
    body = text
    if body and not body.endswith("\n"):
        body += "\n"
    description = (
        f"appended never-called `def {_UNCOVERED_CANARY_FUNC}` "
        "(2 uncovered lines) at end of file"
    )
    return body + _UNCOVERED_CANARY_SNIPPET, description


@dataclass(frozen=True, kw_only=True)
class PythonAdapter:
    """The Python union of dstdns/topos/nyxloom's changed-line coverage
    gates, implemented against the frozen :class:`~assay.adapters.base.
    LanguageAdapter` protocol (A-097, extended by P07/A-101 and P09/A-105)
    — its five attributes and six methods, plus one adapter-private
    constructor field (:attr:`coverage_key_prefix`) that the protocol does
    not name and does not need to: a concrete adapter is free to carry
    extra state beyond the protocol's structural surface, the same way
    P05's own ``FakeAdapter`` (``tests/conftest.py``) carries
    ``key_prefix``/``test_marker``/``no_code_marker`` alongside the five
    required attributes.
    """

    name: str = "python"
    source_globs: tuple[str, ...] = ("*.py",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = True
    external_tools: tuple[str, ...] = ()
    #: A declared, known directory-segment prefix a coverage artifact's own
    #: keys carry that the diff's spelling does not (or vice versa is never
    #: this method's job — only THIS direction, stripping, matches
    #: ``adapters/base.py``'s own "prefix STRIP" framing). Empty means
    #: nothing to strip — the common case where coverage.py's own cwd
    #: already matches the diff's own repo top.
    coverage_key_prefix: str = ""

    def is_test_path(self, rel_path: str) -> bool:
        return bool(_TEST_FILE_RE.search(rel_path))

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            # Fail CLOSED (srdm's asymmetry, DESIGN-GUIDE §11): an
            # unparseable file is never silently excused as "expectedly
            # code-free" — the worst case here is a false failure the
            # author can see and fix, not a silent measurement gap.
            return True
        for node in tree.body:
            if _is_bare_string_statement(node):
                continue
            return True
        return False

    def normalize_coverage_key(self, key: str) -> str:
        prefix = self.coverage_key_prefix
        if not prefix or not key.startswith(prefix + "/"):
            return key
        return key[len(prefix) + 1 :]

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        return _statement_spans(text)

    def inject_import_break(self, text: str) -> tuple[str, str]:
        return _inject_import_break(text)

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        return _inject_uncovered_line(text)
