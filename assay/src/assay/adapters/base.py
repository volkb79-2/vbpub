"""The ``LanguageAdapter`` protocol — the ONLY place a source language's own
syntax is allowed to live (DESIGN-GUIDE §11, A-097).

:mod:`assay.evaluate` never imports ``ast``, never checks a ``.py``
extension, and never special-cases a language by name. Everything it needs
to know about "is this changed line code" comes through this protocol's five
attributes and three methods — nothing more. A-097 fixes this surface for
the whole series: P07 adds ``statement_spans``, P09 adds
``inject_import_break``/``inject_uncovered_line``, P11 adds
``generate_mutants``, each only in the package that first proves the need
(A-084). Adding any of those here, in P05, would be P05 speculatively
building capability nothing in this package's own oracles exercises — the
same "capability with no scoped consumer" defect A-090/A-093/A-094 already
found and fixed twice at earlier package seams.

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

from typing import Protocol

__all__ = ["LanguageAdapter"]


class LanguageAdapter(Protocol):
    """A language's contribution to changed-line coverage evaluation.

    EXACTLY five attributes and three methods (A-097) — deliberately not the
    full seven-capability list DESIGN-GUIDE §11 sketches for the whole
    series. Do not add ``statement_spans``, ``inject_import_break``,
    ``inject_uncovered_line`` or ``generate_mutants`` here.
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
    #: its own (``False``) or needs a later package's multi-line statement
    #: attribution to resolve interior lines of a multi-line construct
    #: (``True``). P05's own evaluation never branches on this — it exists
    #: so a later package (P07) can tell, without inspecting behaviour,
    #: which adapters it must extend.
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
