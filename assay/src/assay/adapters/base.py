"""The ``LanguageAdapter`` protocol — the ONLY place a source language's own
syntax is allowed to live (DESIGN-GUIDE §11, A-097).

:mod:`assay.evaluate` never imports ``ast``, never checks a ``.py``
extension, and never special-cases a language by name. Everything it needs
to know about "is this changed line code" comes through this protocol's
attributes and methods — nothing more. A-097 fixed the P05 surface at five
attributes and three methods; P07 makes the ONE deliberate post-P05
extension the series carves out for it (A-084/A-097's own note): it adds
:meth:`LanguageAdapter.statement_spans` and the new :class:`StatementSpan`
type, and no later adapter package may modify this protocol again. P09 adds
``inject_import_break``/``inject_uncovered_line``, P11 adds
``generate_mutants``, each only in the package that first proves the need.
Adding any of those here, now, would be P07 speculatively building capability
nothing in this package's own oracles exercises — the same "capability with
no scoped consumer" defect A-090/A-093/A-094 already found and fixed at
earlier package seams.

**Path contract**, binding on every method below and on
:func:`assay.evaluate.evaluate_coverage`'s own use of ``source_globs``: every
path string an adapter receives or returns is spelled exactly the way
``git diff`` spells a new-side path — forward-slash separated, relative to
the repository's top level, no ``./`` prefix (the same spelling
:class:`assay.diff.AddedLines.by_file` already uses). An adapter never sees
an absolute path and never needs to resolve one; the core (not the adapter)
is what reconciles a source root's boundary against the filesystem
(DESIGN-GUIDE §11: "the prefix-boundary reconciliation... is universal and
lives in the core").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["LanguageAdapter", "StatementSpan"]


@dataclass(frozen=True, kw_only=True)
class StatementSpan:
    """The physical line range of one statement an adapter located by its own
    (language-specific) means — P07's one deliberate protocol extension
    (A-101). ``start_line``/``end_line`` are both 1-based and inclusive, and
    ``end_line >= start_line`` always: a span is never empty and never
    backwards.

    Deliberately a NEW frozen ``kw_only`` dataclass, never dstdns's bare
    ``list[tuple[int, int]]`` (A-092's project-wide convention overrides the
    cited sibling's literal shape). Malformed construction attempts —
    non-integer, non-positive, or ``end_line < start_line`` — raise
    ``ValueError`` immediately, matching :class:`~assay.verdict.Coverage`'s
    own construction-time discipline: a span that could never describe a
    real statement is refused before it can enter attribution at all, rather
    than surfacing as a downstream ambiguity.

    For a COMPOUND (body-holding) statement — an ``if``/``for``/``def``/...
    — the span is trimmed to the HEADER only, stopping before the first
    nested body statement's own line, so a nested statement's lines are
    never double-claimed by an enclosing one.
    """

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        for name in ("start_line", "end_line"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"StatementSpan.{name} must be an integer, got {value!r}"
                )
            if value < 1:
                raise ValueError(f"StatementSpan.{name} must be >= 1, got {value}")
        if self.end_line < self.start_line:
            raise ValueError(
                f"StatementSpan.end_line ({self.end_line}) must be >= "
                f"start_line ({self.start_line})"
            )


class LanguageAdapter(Protocol):
    """A language's contribution to changed-line coverage evaluation.

    Five attributes and four methods after P07 (A-097/A-101) — deliberately
    not the full seven-capability list DESIGN-GUIDE §11 sketches for the
    whole series. Do not add ``inject_import_break``, ``inject_uncovered_line``
    or ``generate_mutants`` here; those remain P09's and P11's to add, each
    only in the package that first proves the need (A-084).
    """

    #: A short, stable, unique identifier — the exact string a lane's
    #: ``judge.language`` declares and :mod:`assay.registry` keys adapters
    #: by (e.g. ``"python"``, ``"go"``). Never used for branching logic
    #: inside :mod:`assay.evaluate`; it exists for the registry lookup and
    #: for error messages naming which adapter is in play.
    name: str

    #: ``fnmatch``-style glob patterns (matched against the full repo-relative
    #: path — a bare ``*`` matches ``/`` too, so ``"*.py"`` matches
    #: ``src/pkg/mod.py`` at any depth) identifying which changed files this
    #: adapter considers ITS source at all. A file matching no pattern is
    #: invisible to coverage evaluation, the same way coverage.py can never
    #: measure a ``.json`` fixture living beside Python source.
    source_globs: tuple[str, ...]

    #: Directory name components (not paths — bare segment names such as
    #: ``"node_modules"``, ``"__pycache__"``, ``"vendor"``) that exclude a
    #: changed file from consideration entirely when any path segment
    #: matches, even though the file might otherwise satisfy
    #: ``source_globs`` and sit under a declared source root.
    excluded_dir_names: frozenset[str]

    #: Declares whether this adapter's classification is line-granular on
    #: its own (``False``) or needs :meth:`statement_spans` to resolve
    #: interior lines of a multi-line construct (``True``).
    #: :mod:`assay.evaluate` calls :meth:`statement_spans` if and only if
    #: this is ``True`` — an adapter declaring ``False`` is never asked for
    #: spans at all, and its own :meth:`statement_spans` (if it even
    #: implements one beyond the protocol's own ``None``-returning default
    #: shape) is simply never reached.
    requires_span_attribution: bool

    #: Names of external tools this adapter shells out to (A-013), declared
    #: up front so a lane's prerequisites are checkable before anything
    #: runs. Empty for an adapter that is pure Python text/AST processing
    #: with no subprocess boundary.
    external_tools: tuple[str, ...]

    def is_test_path(self, rel_path: str) -> bool:
        """Is *rel_path* a test file — never obligated to cover itself?

        A changed line inside a test file contributes to neither the
        numerator nor the denominator of changed-line coverage, mirroring
        every cited sibling gate's ``_is_test_path`` skip.
        """
        ...

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        """Does *text* (the current content of *rel_path*) contain ANY
        instrumentable code at all?

        Consulted by :mod:`assay.evaluate` ONLY for a changed, adapter-
        recognised, non-test source file that has **no entry whatsoever** in
        the coverage artifact — srdm's NoCode distinction (DESIGN-GUIDE
        §2/§11): a file with genuinely zero instrumentable statements (an
        empty ``__init__.py``, a pure re-export module) is expected to be
        silent in the coverage report, and its absence there is not a
        measurement gap. Returning ``True`` when the file really has no code
        produces a **false failure**; returning ``False`` when it does
        produces a **silent excuse** — srdm's own asymmetry, learned the
        hard way. Never consulted for a file the coverage artifact DID
        measure: that file's own ``executed``/``missing`` sets are already
        the authoritative answer.
        """
        ...

    def normalize_coverage_key(self, key: str) -> str:
        """Map *key* — a path exactly as the coverage artifact's own format
        spells it (coverage.py JSON's dict key, an lcov ``SF:`` path, a Go
        package-qualified import path, ...) — to the SAME spelling
        ``git diff`` uses for that file (this protocol's path contract,
        above).

        This is the one adapter hook in DESIGN-GUIDE §11's path-
        normalisation split: the prefix-BOUNDARY reconciliation (is this
        path under a declared source root) is universal and lives in the
        evaluation core; the language-specific prefix STRIP (Go's module
        path, srdm's ``stripModulePrefix``) is what this method exists to
        do. An adapter with nothing to strip returns *key* unchanged.
        """
        ...

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        """Every statement's own physical line span in *text*, or ``None``.

        P07's one deliberate protocol extension (A-084/A-101) — never
        modified by any later adapter package. :mod:`assay.evaluate` calls
        this ONLY for a changed, considered, coverage-measured line that
        fell into neither ``executed``, ``missing`` nor ``excluded`` (an
        "unattributed" line) on an adapter whose :attr:`requires_span_attribution`
        is ``True``; it is never called otherwise, so an adapter declaring
        ``False`` may implement this to always return ``None`` without
        consequence.

        Two return shapes, both legal:

        * ``None`` means "this adapter performs no span attribution at
          all" — paired with :attr:`requires_span_attribution` being
          ``False``. (When ``True``, ``None`` instead means "this
          particular *text* could not be parsed at all" — e.g. Python's own
          real ``SyntaxError`` — and every unattributed line in that file is
          then genuinely unattributable, never guessed at.)
        * ``tuple[StatementSpan, ...]`` — possibly empty — one entry per
          statement :mod:`assay.evaluate`'s pure attribution function may
          use to resolve an unattributed line to its enclosing statement's
          own (already coverage-tracked) status. Spans MAY nest (an inner
          statement's span sits inside an outer compound statement's own
          trimmed header span) — nesting is resolved deterministically by
          containment, innermost wins. Spans that merely OVERLAP without
          nesting (neither contains the other) are a defensive case
          :mod:`assay.evaluate` treats as its own adapter-analysis-is-
          inconsistent ambiguity (A-100/A-101) — never naturally produced by
          a correct adapter walking correctly-nested source, so an adapter
          author does not need to guard against producing one deliberately.
        """
        ...
