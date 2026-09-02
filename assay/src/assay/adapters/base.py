"""The ``LanguageAdapter`` protocol — the ONLY place a source language's own
syntax is allowed to live (DESIGN-GUIDE §11, A-097).

:mod:`assay.evaluate` never imports ``ast``, never checks a ``.py``
extension, and never special-cases a language by name. Everything it needs
to know about "is this changed line code" comes through this protocol's
attributes and methods — nothing more. A-097 fixed the P05 surface at five
attributes and three methods; P07 added the ONE deliberate post-P05
extension reserved for it (A-084/A-097's own note):
:meth:`LanguageAdapter.statement_spans` and the new :class:`StatementSpan`
type. P09 (this package, A-084/A-105) adds the second deliberate extension:
:meth:`LanguageAdapter.inject_import_break` and
:meth:`LanguageAdapter.inject_uncovered_line`, the pure ``(text) -> (text,
description)`` transforms :mod:`assay.canary` drives to build its
cause-sensitive canary (A-010 — nyxloom's own versions of these two write the
file directly; these do not touch a filesystem). P11 (this package,
A-084/A-112/A-114) adds the THIRD and final deliberate extension DESIGN-GUIDE
§11 sketches: mutation construction — construction only; P12 consumes the
result to actually run tests against it (A-114's own "construct, never
execute" boundary). **P21 (A-180/A-183) replaces that third extension's
shape**: :meth:`LanguageAdapter.generate_mutation_sites` returns bounded
:class:`~assay.mutation.MutationSite` descriptors (or the adapter-wide
``"UNSUPPORTED"`` marker) instead of a tuple of full mutated source files,
because the old return contract made a candidate cap unenforceable by
construction.

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

**The one narrow amendment** (A-397, the P27 re-carve):
:meth:`LanguageAdapter.statement_blocks` additionally receives ``repo_top``
as its own named parameter. The path STRINGS it takes and returns are still
repo-relative in exactly the spelling above — the amendment is that this one
method, which is also the only one permitted to shell out, gets the absolute
anchor that makes those strings readable, from the core that owns it, rather
than resolving one itself. The pre-existing sentence "an adapter never sees
an absolute path" was written when no method needed to read a file; it is
restated here as "an adapter never spells a path differently from ``git
diff``, and never resolves a root of its own", which is the invariant that
was actually load-bearing.

**The fifth and last extension** (A-404, the same re-carve's DA-8):
:meth:`LanguageAdapter.for_project` receives ``repo_top`` and
``project_root`` and returns the adapter to use for THAT project. It is the
one member whose return value is another adapter, and it exists so a fact
living in the project's own layout — Go's module path, in its ``go.mod`` —
can be DERIVED rather than declared (DESIGN-GUIDE §5) without the core
learning what a ``go.mod`` is. Every adapter but Go returns ``self``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Literal, Mapping, Protocol, Sequence

from ..mutation import MutationSite
from ..statement_attribution import StatementBlock

__all__ = [
    "HelperInvocation",
    "LanguageAdapter",
    "StatementBlockReport",
    "StatementSpan",
]


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


#: Seconds left in the lane's own budget, sampled on demand — structurally
#: identical to :data:`assay.git.Remaining` and deliberately re-declared here
#: rather than imported, because :mod:`assay.adapters.base` is the frozen
#: protocol and must not acquire a dependency on the git/process layer to
#: state a type. A-212's rule is that ONE deadline governs a lane; an adapter
#: that shells out samples it through this callable rather than inventing a
#: timeout of its own.
Remaining = Callable[[], float]


@dataclass(frozen=True, kw_only=True)
class HelperInvocation:
    """Which external program actually produced a helper's answer, recorded
    at the moment it was run (A-397).

    Deliberately NOT :class:`assay.verdict.Helper`. That type is the WIRE
    shape, validated against the verdict schema and paired with a judged
    claim per role (``verdict.py``'s own ``_check_helpers``); this one is the
    adapter-layer fact, and the runner is what maps one to the other. An
    adapter importing :mod:`assay.verdict` to answer "which toolchain did I
    just run" would invert the layering this protocol exists to keep flat:
    ``adapters/`` never reaches the wire.

    ``identity`` is a MEASURED string, never a formatted guess — the version
    the helper itself reported at the moment it ran (A-334: a value assay
    handed itself is not evidence about an external system). ``resolved_path``
    is where the tool was actually found on the effective PATH, which is the
    other half of "which toolchain": two ``go`` binaries reporting the same
    version can still be different installations.
    """

    tool: str
    resolved_path: str
    identity: str

    def __post_init__(self) -> None:
        for name in ("tool", "resolved_path", "identity"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"HelperInvocation.{name} must be a non-empty string, "
                    f"got {value!r}"
                )


@dataclass(frozen=True, kw_only=True)
class StatementBlockReport:
    """One :meth:`LanguageAdapter.statement_blocks` answer: every requested
    path's own blocks, plus the identity of whatever produced them.

    ``blocks_by_path`` is keyed by the SAME repo-relative path strings the
    caller passed in — this protocol's path contract, unchanged. Every
    requested path appears exactly once; an adapter that cannot derive blocks
    for one of them raises rather than omitting it, because a silently absent
    key would reach :func:`assay.statement_attribution.attribute_statements`
    as "this file has no statement positions", which that function refuses on
    anyway (A-391) but with a message naming the wrong cause.
    """

    blocks_by_path: Mapping[str, tuple[StatementBlock, ...]]
    helper: HelperInvocation

    def __post_init__(self) -> None:
        if not isinstance(self.blocks_by_path, MappingProxyType):
            object.__setattr__(
                self, "blocks_by_path", MappingProxyType(dict(self.blocks_by_path))
            )


class LanguageAdapter(Protocol):
    """A language's contribution to changed-line coverage evaluation, plus
    (P09) to the cause-sensitive canary and (P11) to mutant construction.

    SIX attributes and NINE methods after the P27 re-carve
    (A-097/A-101/A-105/A-114/A-397/A-404) — reached only because each of the
    FIVE post-P05 extensions (:meth:`statement_spans`, the ``inject_*`` pair,
    :meth:`generate_mutation_sites`, :attr:`requires_statement_attribution` +
    :meth:`statement_blocks`, and now :meth:`for_project`) was added by the
    package that first proved the need for it (A-084), never spec'd in ahead
    of that need.
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

    #: Declares whether this adapter's coverage format reports STATEMENT
    #: truth on its own (``False``) or reports something coarser that must
    #: be corrected by :meth:`statement_blocks` before any verdict is
    #: computed from it (``True``). Go's coverprofile is the case that
    #: exists: it records a block's positional EXTENT and a statement
    #: CARDINALITY, never the statements' own positions, so expanding an
    #: extent into lines attributes function signatures, closing braces and
    #: ``case`` labels as executable code (A-217, A-239).
    #:
    #: Deliberately NOT the same axis as :attr:`requires_span_attribution`,
    #: which gates :meth:`statement_spans` — that hook RESCUES lines no
    #: bucket claimed, and is called only for unattributed lines (A-097/
    #: A-101, frozen). This one DEMOTES lines a bucket claimed wrongly, and
    #: applies to the whole profile before evaluation. The two gate opposite
    #: directions and an adapter may declare either, both, or neither.
    #:
    #: **This is a guard, not a hint.** When ``True``,
    #: :func:`assay.evaluate.evaluate_coverage` and
    #: :func:`~assay.evaluate.evaluate_targets` REFUSE a profile whose
    #: :attr:`~assay.coverage_parsers.model.CoverageProfile.
    #: statement_attributed` is ``False`` (A-392) — an uncorrected
    #: block-based profile is a wrong answer that looks exactly like a right
    #: one, which is AGENTS.md's masked-default anti-pattern in its purest
    #: form, so the correction is enforced rather than remembered.
    requires_statement_attribution: bool

    #: Names of external tools this adapter shells out to (A-013), declared
    #: up front so a lane's prerequisites are checkable before anything
    #: runs. Empty for an adapter that is pure Python text/AST processing
    #: with no subprocess boundary.
    external_tools: tuple[str, ...]

    def for_project(
        self, *, repo_top: Path, project_root: Path
    ) -> LanguageAdapter:
        """The adapter to use for the project rooted at *project_root* inside
        *repo_top* — the FIFTH and final deliberate post-P05 protocol
        extension (A-404, ruled by DA-8).

        Called by the core ONCE per lane, at the one point where it holds both
        anchors and before anything reads the profile, so that every
        downstream consumer of the adapter (the key join, the statement
        oracle, both evaluate modes) is handed the SAME object. An adapter
        with nothing to learn from a project's layout returns ``self``; that
        is the answer for every adapter but Go today, and it is the reason
        this member can exist without the core knowing what any of them do
        with it.

        **Why the protocol grew a member rather than the core growing a
        field.** A Go coverage profile keys its records by import path, so the
        module path is needed before a key can be joined to a diff path — and
        it is a fact of the project's own ``go.mod``, not of the lane's argv,
        which DESIGN-GUIDE §5 requires assay to READ rather than accept as a
        declared literal (covergate's ``-module srdm`` is that section's own
        anti-pattern #1). The core cannot read it: doing so would put a
        language's file format in :mod:`assay.evaluate`, which is the one
        thing this protocol exists to prevent. So the core hands the adapter
        the anchors and takes back an adapter.

        **Returning a NEW object, never mutating.** Every adapter in this
        project is a frozen dataclass and the registry holds one instance per
        language for the whole process (``cli._built_in_registry``); binding
        by mutation would make one lane's derived value visible to the next,
        which is the shared-singleton bug this shape exists to avoid. An
        adapter that derives something returns ``dataclasses.replace(self,
        ...)``; the registry's own instance is never touched.

        Raises :class:`~assay.errors.AssayError` when the project's layout
        contradicts the lane — never returns a partially-bound adapter. There
        is no fail-closed direction to guess in: an unresolved prefix does not
        refuse, it silently matches no file, and a lane whose coverage matches
        nothing measures 0/0 and PASSES (DESIGN-GUIDE §5's laundering-gate
        test, one layer down).
        """
        ...

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

    def statement_blocks(
        self,
        repo_top: Path,
        rel_paths: Sequence[str],
        *,
        remaining: Remaining | None = None,
    ) -> StatementBlockReport | None:
        """Where every coverage block over *rel_paths* begins its own
        statements — the FOURTH deliberate post-P05 protocol extension
        (A-239 item 2, signature ruled by A-397).

        *rel_paths* are repo-relative path strings, this protocol's own path
        contract, and *repo_top* is the single absolute anchor that makes
        them readable. **This is the one method that receives a filesystem
        root, and deliberately so:** it is also the one method allowed to
        shell out (that is what :attr:`external_tools` declares), and a
        program that re-derives statement positions from SOURCE must be able
        to read the source. The path STRINGS stay repo-relative, so the
        adapter still never spells a path a different way from ``git diff``;
        the anchor arrives separately, named, from the core that owns it.

        *remaining* is the lane's own deadline (A-212). An adapter that
        launches a subprocess samples it rather than inventing a timeout;
        ``None`` stays legal only for a genuine non-lane caller, exactly as
        it is for :func:`assay.git.run`.

        Two return shapes, the convention A-101 established for
        :meth:`statement_spans`:

        * ``None`` — this adapter performs no statement attribution at all,
          paired with :attr:`requires_statement_attribution` being ``False``.
          Every line-based format is here: coverage.py, lcov and cobertura
          already report statement truth, so there is nothing to correct.
        * :class:`StatementBlockReport` — one entry per requested path, plus
          the identity of the helper that produced it.

        **Never overloads** :meth:`statement_spans`, and A-239 rejected doing
        so explicitly: that method's contract is "called ONLY for
        unattributed lines", frozen by A-097/A-101, which gates in the wrong
        direction (it rescues gaps; this demotes fabrications), and its type
        is line-only with no columns — while the whole reason this hook
        exists is that ``28.22,29.2`` and ``29.2,31.3`` are two different
        blocks a line-only key would fuse.

        Raises :class:`~assay.errors.AssayError` rather than returning a
        partial answer when the helper cannot run or its output cannot be
        read. There is no safe direction to guess in: attributing lines from
        a source the oracle could not read would publish a verdict about
        lines that are not the lines that ran (A-391).
        """
        ...

    def inject_import_break(self, text: str) -> tuple[str, str]:
        """A MINIMAL, valid-as-source, known-bad transform of *text* that
        should make any test importing/loading it fail outright (P09,
        A-010/A-105) — proves a gate rejects broken code AT ALL, the R0-level
        half of the cause-sensitive canary.

        Pure: returns ``(transformed_text, description)`` and never touches
        a filesystem. *description* is a short, human-readable account of
        what changed (e.g. "inserted `raise AssertionError(...)` at line
        4"), carried into :class:`~assay.verdict.CanaryResult` for
        diagnosis — never parsed back apart by any caller.

        The mechanism is deliberately language-specific (this is the one
        place a language's own rule for "where can a side effect legally go"
        matters): Python has an executable module top level, so nyxloom's
        own mechanism — insert a bare ``raise`` statement after any leading
        docstring/``__future__`` import — ports directly (A-105). A language
        with no executable top level (Go) cannot reuse that shape and must
        pick its own equivalent within the SAME contract: minimal, additive,
        never a whole-file reformat, and reliably tripped by merely
        importing/loading the module.
        """
        ...

    def inject_uncovered_line(self, text: str) -> tuple[str, str]:
        """A MINIMAL, valid, lint-clean, test-neutral addition to *text*
        whose lines are never executed by any test (P09, A-010/A-105) —
        proves a gate specifically enforces a changed-line-coverage floor,
        the R1-level half of the cause-sensitive canary: a tests-only gate
        (one that runs the suite but never checks coverage) sails past this
        transform, while a gate enforcing changed-line coverage rejects it.

        Pure, same ``(transformed_text, description)`` contract as
        :meth:`inject_import_break`. nyxloom's own mechanism — append a
        never-called, typed, side-effect-free module-level function — ports
        directly for every language with a top-level function-declaration
        form (A-105): the function's own signature line is reached (and so
        counted executed, where a format even attributes signature lines at
        all) merely by the module loading, but its BODY is never called by
        anything, so the body's own lines are changed, executable, and
        uncovered — exactly the axis this canary needs to isolate.
        """
        ...

    def generate_mutation_sites(
        self,
        text: str,
        lines: set[int],
        *,
        operators: tuple[str, ...],
        limit: int,
    ) -> tuple[MutationSite, ...] | Literal["UNSUPPORTED"]:
        """At most *limit* candidate mutation SITES in *text* whose own
        location sits on one of *lines* and whose operator is in *operators*
        (P21, A-180/A-183) — construction only; nothing here runs a test or
        judges a survivor.

        **This replaces P11's ``generate_mutants``.** That method returned a
        tuple of full mutated source files, which made a candidate cap
        unenforceable by construction: every candidate's entire text had to
        exist before anything could count them, so a limit applied afterwards
        bounded neither memory nor work (A-180). An implementation must
        retain at most *limit* descriptors WHILE walking — a bounded
        selection or heap — never append every candidate and slice at the
        end, which reintroduces exactly the unbounded retention this shape
        exists to remove. Parsing or scanning the (already bounded) source is
        permitted; retaining more than *limit* descriptors, or any full
        mutated file, is not.

        *operators* is the caller's ORDERED, already-validated selection, and
        a site outside it must never be produced — filtering afterwards would
        make the cap count candidates the lane never selected. *limit* is the
        REMAINING capacity across all files, so an adapter that returns fewer
        sites than *limit* is telling the collector it exhausted this file.

        Three results, and the distinction between them is load-bearing
        (A-183):

        * ``tuple[MutationSite, ...]`` — a supported analysis ran. An empty
          tuple is a real measurement: this file has no eligible site.
        * ``"UNSUPPORTED"`` — the ADAPTER has no mutation implementation at
          all, for any input. Whole-adapter-call, never per-construct
          (A-114's discipline, retained): an individual construct outside
          the catalogue simply contributes zero sites. For
          :class:`~assay.adapters.go.GoAdapter` this is unconditional until
          P29, because no Go toolchain exists anywhere to prove a generated
          mutant is even valid Go syntax (A-042). Rendered as payload-free
          ``INCONCLUSIVE/MUTATION_UNSUPPORTED``, never green.
        * :class:`~assay.mutation.MutationDiscoveryError` — a discovery
          boundary FAILED. For :class:`~assay.adapters.python.PythonAdapter`
          this is source that does not parse; P29 adds the helper-protocol
          failures. **Python never returns ``"UNSUPPORTED"``** — it has an
          engine, so its failures are failures, not absence.

        A boolean chain's ``N - 1`` textual operator occurrences (``a and b
        and c`` is ONE ``ast.BoolOp`` node but TWO ``and`` tokens) are
        ``N - 1`` independently-targetable sites, each flipping exactly one
        token (A-115) — never the shared AST node's ``.op`` reassigned
        wholesale, which would conceptually flip every occurrence at once.
        """
        ...
