"""Mutation-site identity, and the byte-offset arithmetic every mutation
engine shares — P11: **each mutant assay generates is a valid single
changed-line experiment, not arbitrary broken text**, and P21: **and the
declared cap on how many of them exist is TRUE.**

This module owns exactly two things, both deliberately language-free so a
future second mutation engine (were one ever built — none is, for Go, until
P29) would not need to duplicate them:

* :class:`MutationSite` — the BOUNDED descriptor of one candidate
  (A-180), plus :class:`MutantJob` and :class:`MutationDiscoveryError`.
  Lives here, not in ``adapters/base.py``, because the frozen protocol
  module imports it for its own type hint.

  **P21 replaced P11's ``Mutant``**, which carried ``mutated_text``: a full
  copy of the mutated file, per candidate. That shape made ``max_mutants``
  unenforceable by construction — every candidate's entire source had to
  exist before anything could count them, so a cap applied afterwards
  bounded neither memory nor work. The old ``Mutant``,
  ``generate_mutants``, ``collect_mutants`` and full-text identity are
  DELETED rather than kept as compatibility surfaces; ``go.py`` was
  migrated with them (A-183) and still returns the adapter-wide
  ``"UNSUPPORTED"`` marker until P29.
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
file. :class:`MutationSite` is the identical split, one module over.

**P12 adds execution** (A-119): baseline-gated, isolated, ``jobs``-bounded
mutation running lives HERE too, mirroring ``canary.py``'s own precedent of
holding one claim-tier's entire orchestration in a single dedicated module.
The surface is :func:`run_mutation` (the entry point: a bounded executor
fan-out over :func:`collect_mutation_sites`'s job list, then deterministic
aggregation into a :class:`~assay.verdict.Mutation`),
:func:`judge_mutation` / :func:`build_mutation_claim` (the pure outcome
mapping and R2 :class:`~assay.verdict.Claim` wiring, A-117), and
:func:`collect_mutation_sites` (the bounded cross-file aggregation).

**P21 makes the cap a WORK bound** (A-180/A-183): each adapter call receives
the REMAINING capacity, later files are not called once it is exhausted, and
the one full replacement file per mutant is materialised inside its own
worker. :func:`collect_mutation_sites` distinguishes three results that must
never collapse into each other — an adapter with no engine
(``"UNSUPPORTED"`` -> payload-free ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED``),
a failed discovery boundary (:class:`MutationDiscoveryError` ->
``ERROR``/``MUTATION_DISCOVERY_FAILED``), and a supported analysis that
observed nothing (``()`` -> ``INCONCLUSIVE``/``NO_MUTANTS`` with its exact
zero/zero payload).

**P18 wires this into the installed CLI** (work items 2-5): the baseline
:func:`run_mutation` gates on is no longer run BY this module — it is now
a required *baseline* parameter, the exact :class:`~assay.runner.
CommandResult` R0 already produced, so the lane's command runs at most
once per ``assay run`` invocation (sol finding 11). :func:`
resolve_mutation_targets` builds the per-file candidate list from the same
resolved diff R1 measures against, and *operators* is the lane's own
declared, closed selection. (P21 moved that selection from a post-hoc
filter over collected jobs INTO the adapter call itself: filtering
afterwards meant the adapter had already built candidates the lane never
selected, so the cap would have been counting work that could never run.)
Wiring it also fixed the
path spelling two of these surfaces had never had to agree on before
(A-145): a target's ``path`` is REPO-relative, while each mutant's scratch
tree is a copy of the PROJECT root — see :func:`project_prefix`.

**A circular-import note, since it shapes this module's own imports below**:
``assay.runner`` imports ``assay.adapters.base``, which imports THIS module
for :class:`MutationSite`. A module-level ``from .runner import execute_command``
here would therefore be a genuine cycle (``mutation -> runner -> adapters.
base -> mutation``), not merely a style preference — verified empirically
while authoring this package: it breaks for whichever entry point happens
to import ``assay.adapters.base`` (or ``assay.runner``) BEFORE
``assay.mutation`` finishes loading, which is common (most adapter tests
trigger it). :func:`run_mutation` resolves ``execute_command`` /
``default_process_runner`` with a DEFERRED (function-body-local) import
instead — safe because by the time the function is actually CALLED, module
loading for the whole program has long finished, regardless of which module
was imported first. The ``ProcessRunner``/``CommandResult``/
``LanguageAdapter`` type hints below reference their real homes (
``assay.runner``, ``assay.adapters.base``) by bare name, never imported —
safe under this module's own ``from __future__ import annotations``, which
defers every annotation to a string never evaluated at runtime (verified
empirically), and avoids re-opening the same cycle purely for a type hint.
"""

from __future__ import annotations

import fnmatch
import shutil
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import hashlib
from typing import Literal

from .config import Lane
from .diff import AddedLines
from .errors import AssayError, Outcome, ReasonCode
from .verdict import MAX_CANDIDATE_CEILING, Claim, Mutation, MutantOutcome
from .vocabulary import MUTATION_OPERATORS

__all__ = [
    "MAX_CANDIDATE_CEILING",
    "MUTATION_OPERATORS",
    "Clock",
    "ExecutorFactory",
    "MutantJob",
    "MutationDiscoveryError",
    "MutationSite",
    "MutationTarget",
    "build_mutation_claim",
    "byte_offset",
    "collect_mutation_sites",
    "judge_mutation",
    "line_for_offset",
    "project_prefix",
    "resolve_mutation_targets",
    "run_mutation",
]

#: (P21/A-183) the adapter-wide capability sentinel, retained from the old
#: ``generate_mutants`` union. It means the adapter has NO mutation
#: implementation at all -- never a parse failure, never an unrecognised
#: individual construct, never a valid analysis that found nothing.
UNSUPPORTED = "UNSUPPORTED"

#: The injectable clock, identical in shape to ``assay.runner.Clock`` --
#: duplicated rather than imported (this module's own circular-import note,
#: above): a plain function type alias costs nothing to restate and needs
#: no deferral, unlike the runtime-called ``execute_command``.
Clock = Callable[[], datetime]

class MutationDiscoveryError(AssayError):
    """A syntax-aware discovery BOUNDARY failed (P21/A-171).

    Always ``ERROR``/``MUTATION_DISCOVERY_FAILED``, so no call site can pair
    it with a different outcome. Reachable in P21 for source the adapter
    genuinely cannot parse, and for an adapter that claims supported
    discovery and then contradicts itself mid-collection; P29 adds the
    helper-protocol failures.

    Deliberately NOT the same thing as either neighbour: ``UNSUPPORTED``
    means no engine exists to fail, and an empty tuple means the engine ran
    and found nothing. Collapsing any pair of the three turns an inability
    to measure into apparent evidence, which is the failure this whole
    project exists to remove.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.MUTATION_DISCOVERY_FAILED,
        )


@dataclass(frozen=True, kw_only=True)
class MutationSite:
    """One candidate mutation, as a BOUNDED descriptor (P21/A-180).

    This replaces P11's ``Mutant``, whose ``mutated_text`` carried a full
    copy of the mutated file. That shape made ``max_mutants`` unenforceable
    by construction: the adapter had to materialise every candidate's entire
    source before any cap could look at the collection, so a limit applied
    afterwards bounded neither memory nor work. A site is instead the
    smallest thing that still names the experiment exactly — a byte span and
    its replacement — and the full replacement text is materialised only
    inside a submitted worker, at most ``jobs`` of them alive at once.

    ``start_byte``/``end_byte`` are zero-based, half-open, absolute UTF-8
    byte offsets into the source the adapter was handed. Operating in bytes
    rather than ``str`` units is not a style choice: ``ast``'s own
    ``col_offset`` is a UTF-8 BYTE offset within a physical line, so any
    arithmetic done in character units silently misaligns the moment source
    contains a non-ASCII character (the locked site manifest opens with
    ``π`` precisely to keep that honest).
    """

    start_byte: int
    end_byte: int
    replacement: bytes
    lineno: int
    operator: str
    description: str

    def __post_init__(self) -> None:
        for name in ("start_byte", "end_byte", "lineno"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"MutationSite.{name} must be an integer, got {value!r}"
                )
        if self.start_byte < 0:
            raise ValueError(
                f"MutationSite.start_byte must be >= 0, got {self.start_byte}"
            )
        if self.end_byte <= self.start_byte:
            raise ValueError(
                f"MutationSite byte span [{self.start_byte}, {self.end_byte}) is "
                f"empty or reversed; a site always replaces at least one byte"
            )
        if self.lineno < 1:
            raise ValueError(f"MutationSite.lineno must be >= 1, got {self.lineno}")
        if not isinstance(self.replacement, bytes) or not self.replacement:
            raise ValueError(
                f"MutationSite.replacement must be non-empty bytes, got "
                f"{self.replacement!r}"
            )
        try:
            self.replacement.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"MutationSite.replacement {self.replacement!r} is not valid "
                f"UTF-8: {exc}"
            ) from exc
        if self.operator not in MUTATION_OPERATORS:
            raise ValueError(
                f"MutationSite.operator must be one of "
                f"{list(MUTATION_OPERATORS)}, got {self.operator!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"MutationSite.description must be a non-empty string, got "
                f"{self.description!r}"
            )

    @property
    def replacement_sha256(self) -> str:
        """Lowercase SHA-256 of the REPLACEMENT BYTES ONLY (A-180).

        Never a hash of the mutated file, and never derived by diffing two
        full texts: a minimal text diff does not recover a syntax site at
        all (``<`` -> ``<=`` collapses to a zero-width insertion, ``True`` ->
        ``False`` to ``Tru`` -> ``Fals``).
        """
        return hashlib.sha256(self.replacement).hexdigest()

    @property
    def identity(self) -> tuple[int, int, str, str]:
        """The per-file ordering and uniqueness key (A-180). ``lineno`` and
        ``description`` are excluded on purpose: they diagnose a site, they
        do not distinguish two sites."""
        return (self.start_byte, self.end_byte, self.replacement_sha256, self.operator)

    def apply(self, original: bytes) -> bytes:
        """The one-site splice, materialised at execution time only."""
        return original[: self.start_byte] + self.replacement + original[self.end_byte :]


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


# --------------------------------------------------------------------------
# P12: bounded mutation execution
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, kw_only=True)
class MutationTarget:
    """One changed file to mutate: its repo-relative *path* (the same
    forward-slash, repo-top-relative spelling ``adapters/base.py``'s own
    path contract requires), its CURRENT text, and the changed line numbers
    scoping candidate sites — the exact per-file input
    ``LanguageAdapter.generate_mutation_sites(text, lines, ...)`` needs,
    paired with the *path* identity a :class:`MutationSite` itself carries
    no field for.

    Building this from a real diff/adapter/filesystem is deliberately
    OUTSIDE this package's scope — P12 owns EXECUTION, not diff-to-target
    resolution (the handoff's own "Scope / forbid" section: "Mutant
    construction and adapter capability are frozen P11 inputs. This package
    owns execution and the R2 producer only.") — a future caller (P14's CLI
    wiring) constructs these from :class:`~assay.diff.AddedLines` plus the
    real source tree, the same way
    :func:`~assay.canary.run_python_canary` receives its ``target_path``
    already resolved rather than deriving it itself.
    """

    path: str
    text: str
    lines: frozenset[int]

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError(
                f"MutationTarget.path must be a non-empty string, got {self.path!r}"
            )
        if not isinstance(self.text, str):
            raise ValueError(
                f"MutationTarget.text must be a string, got {self.text!r}"
            )
        if not isinstance(self.lines, frozenset) or not self.lines:
            raise ValueError(
                f"MutationTarget.lines must be a non-empty frozenset of line "
                f"numbers, got {self.lines!r} — a file contributing no "
                f"changed lines has nothing to mutate and should simply be "
                f"omitted from the target list"
            )
        for line in self.lines:
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise ValueError(
                    f"MutationTarget.lines must contain only positive line "
                    f"numbers, got {line!r}"
                )


def resolve_mutation_targets(
    added: AddedLines,
    *,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    adapter: LanguageAdapter,
    read_source_text: Callable[[str], str],
) -> tuple[MutationTarget, ...]:
    """Build R2's per-file candidate list from the SAME resolved diff R1
    measures against (P18, work item 2) -- *added* is
    :func:`~assay.diff.parse_added_lines`'s own output, threaded in by the
    caller (:func:`~assay.runner.run_lane`) rather than re-derived here, so
    a lane declaring both R1 and R2 diffs its base exactly once (this
    package's own carried-in note: "R2 target selection must consume the
    same measurement, not invoke Git independently with a second base").

    A changed file becomes a candidate target under the identical gates
    :func:`assay.evaluate.evaluate_coverage`'s own (private)
    ``_is_considered`` already applies for R1 -- under a declared source
    root, not inside one of the adapter's own excluded directories, and
    matching one of the adapter's own ``source_globs`` -- plus the
    adapter's own :meth:`~assay.adapters.base.LanguageAdapter.is_test_path`
    exclusion. Deliberately a SEPARATE, duplicated copy of that check
    rather than an import from :mod:`assay.evaluate`: that module sits
    outside this package's own ``scope.touch``, and two independently
    written copies that must agree is a real check one level up.

    Returned in PATH order (never *added.by_file*'s own iteration order,
    which is not itself a promised ordering) -- the same determinism
    :func:`collect_mutation_sites` already applies one level down, so a
    caller never has to sort twice. *read_source_text* is the injectable
    filesystem boundary (AUTHORING.md §3b.E), called once per considered
    file, mirroring :func:`assay.runner.evaluate_r1`'s own
    ``read_source_text`` closure.

    A file contributing no lines that survive these gates is simply
    absent from the result -- never present with an empty
    :class:`MutationTarget`, which could not construct anyway (its own
    ``lines`` field refuses empty, A-092).
    """
    targets: list[MutationTarget] = []
    for path in sorted(added.by_file):
        lines = added.by_file[path]
        abs_path = (repo_top / path).resolve()
        if not any(abs_path.is_relative_to(root) for root in source_root_paths):
            continue
        if any(part in adapter.excluded_dir_names for part in Path(path).parts[:-1]):
            continue
        if not any(fnmatch.fnmatch(path, glob) for glob in adapter.source_globs):
            continue
        if adapter.is_test_path(path):
            continue
        targets.append(
            MutationTarget(
                path=path, text=read_source_text(path), lines=frozenset(lines)
            )
        )
    return tuple(targets)


@dataclass(frozen=True, kw_only=True)
class MutantJob:
    """One ``(file, site)`` pair — the unit of work :func:`run_mutation` fans
    out over the executor.

    ``original_text`` is a SHARED REFERENCE to the target's own text, never a
    per-site copy (A-180). Python strings are immutable, so every job for one
    file points at the same object; the replacement file is built once, inside
    the worker that is about to run it, and discarded with its scratch tree.
    """

    path: str
    original_text: str
    site: MutationSite


def _check_operator_policy(operators: tuple[str, ...]) -> None:
    """The declared selection: ordered, non-empty, duplicate-free, and a
    subset of the closed catalogue. Validated HERE as well as at config load
    because :func:`collect_mutation_sites` is a public surface a caller may
    invoke directly, and an unvalidated policy would silently become the
    bound handed to every adapter."""
    if not isinstance(operators, tuple) or not operators:
        raise ValueError(
            f"collect_mutation_sites operators must be a non-empty tuple, got "
            f"{operators!r}"
        )
    if len(set(operators)) != len(operators):
        raise ValueError(
            f"collect_mutation_sites operators contains a duplicate: "
            f"{list(operators)}"
        )
    unknown = [item for item in operators if item not in MUTATION_OPERATORS]
    if unknown:
        raise ValueError(
            f"collect_mutation_sites operators names unknown operator(s) "
            f"{unknown}; the catalogue is closed: {list(MUTATION_OPERATORS)}"
        )


def _validate_sites(
    sites: tuple[MutationSite, ...],
    *,
    target: MutationTarget,
    operators: tuple[str, ...],
    remaining: int,
) -> None:
    """Every rule an adapter's returned batch must satisfy before a single
    job is built from it (A-180).

    An adapter is a language's own code and this is the boundary that keeps
    a wrong one from silently poisoning the artifact's identities: spans must
    address real bytes of the exact text handed in, on UTF-8 code-point
    boundaries; the splice must actually change those bytes; the recorded
    line must be the line the span really starts on; the operator must be one
    the caller SELECTED (not merely one the catalogue knows); the batch must
    be identity-ordered and duplicate-free; and it must respect the remaining
    capacity it was given.
    """
    if len(sites) > remaining:
        raise MutationDiscoveryError(
            f"mutation discovery for {target.path!r} returned {len(sites)} "
            f"site(s) for a remaining capacity of {remaining}; an adapter may "
            f"never exceed the limit it was handed"
        )
    source = target.text.encode("utf-8")
    selected = set(operators)
    for site in sites:
        if site.end_byte > len(source):
            raise MutationDiscoveryError(
                f"mutation site {site.identity} for {target.path!r} ends at "
                f"byte {site.end_byte}, past the {len(source)}-byte source"
            )
        for name, offset in (("start_byte", site.start_byte), ("end_byte", site.end_byte)):
            if offset < len(source) and (source[offset] & 0xC0) == 0x80:
                raise MutationDiscoveryError(
                    f"mutation site {site.identity} for {target.path!r} has "
                    f"{name} {offset} inside a UTF-8 character"
                )
        if site.replacement == source[site.start_byte : site.end_byte]:
            raise MutationDiscoveryError(
                f"mutation site {site.identity} for {target.path!r} replaces "
                f"its span with the identical bytes; a no-op is not an "
                f"experiment"
            )
        try:
            site.apply(source).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MutationDiscoveryError(
                f"mutation site {site.identity} for {target.path!r} produces "
                f"invalid UTF-8: {exc}"
            ) from exc
        expected_line = line_for_offset(source, site.start_byte)
        if site.lineno != expected_line:
            raise MutationDiscoveryError(
                f"mutation site {site.identity} for {target.path!r} records "
                f"line {site.lineno} but starts on line {expected_line}"
            )
        if site.operator not in selected:
            raise MutationDiscoveryError(
                f"mutation site {site.identity} for {target.path!r} uses "
                f"operator {site.operator!r}, outside the selected policy "
                f"{list(operators)}"
            )
    identities = [site.identity for site in sites]
    if identities != sorted(identities):
        raise MutationDiscoveryError(
            f"mutation sites for {target.path!r} are not identity-ordered: "
            f"{identities}"
        )
    if len(set(identities)) != len(identities):
        raise MutationDiscoveryError(
            f"mutation sites for {target.path!r} repeat an identity: "
            f"{identities}"
        )


def collect_mutation_sites(
    targets: Iterable[MutationTarget],
    *,
    adapter: LanguageAdapter,
    operators: tuple[str, ...],
    limit: int,
) -> tuple[MutantJob, ...] | Literal["UNSUPPORTED"]:
    """Aggregate bounded candidate sites across possibly many changed files
    into ONE deterministic job list, or report adapter-wide capability
    absence (P21/A-180/A-183).

    Targets are visited in ``path`` order, and each call receives only the
    REMAINING capacity — so the total number of descriptors this function
    ever holds is bounded by *limit* regardless of how many candidates the
    files actually contain. Once capacity is exhausted, later files are not
    called at all: that is what makes the bound a work bound and not merely
    a reporting one.

    Three results, deliberately distinguishable (A-183):

    * ``"UNSUPPORTED"`` — the FIRST adapter answer was the capability
      marker, so no language analysis happened anywhere. Returned
      immediately, with no jobs.
    * a tuple of :class:`MutantJob` — possibly empty, meaning a supported
      analysis genuinely observed that many candidates (zero included).
    * :class:`MutationDiscoveryError` — including the inconsistent case
      where an adapter first claims supported discovery (even an empty
      tuple) and then returns the marker. That adapter is contradicting
      itself about its own capability, and no partial job list from it can
      be trusted.

    With no targets at all the result is the supported empty tuple: no
    language analysis was required, so nothing can be said about capability.
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(f"collect_mutation_sites limit must be an integer, got {limit!r}")
    if not 1 <= limit <= MAX_CANDIDATE_CEILING:
        raise ValueError(
            f"collect_mutation_sites limit must be in 1..{MAX_CANDIDATE_CEILING}, "
            f"got {limit}"
        )
    _check_operator_policy(operators)

    jobs: list[MutantJob] = []
    remaining = limit
    saw_supported = False
    for target in sorted(targets, key=lambda item: item.path):
        if remaining <= 0:
            break
        result = adapter.generate_mutation_sites(
            target.text,
            set(target.lines),
            operators=operators,
            limit=remaining,
        )
        if result == UNSUPPORTED:
            if saw_supported:
                raise MutationDiscoveryError(
                    f"adapter {getattr(adapter, 'name', adapter)!r} reported "
                    f"supported mutation discovery and then returned "
                    f"{UNSUPPORTED!r} for {target.path!r}; capability is a "
                    f"property of the adapter, not of one file"
                )
            return UNSUPPORTED
        saw_supported = True
        sites = tuple(result)
        _validate_sites(
            sites, target=target, operators=operators, remaining=remaining
        )
        for site in sites:
            jobs.append(
                MutantJob(path=target.path, original_text=target.text, site=site)
            )
        remaining -= len(sites)
    return tuple(jobs)


#: The injectable executor boundary (A-082/A-119/A-122): the entry point
#: :func:`run_mutation` calls with EXACTLY the caller-declared ``jobs`` —
#: never ``os.cpu_count()`` or a mutant-count-derived value (A-122's own
#: confirmed trap in ``mutation_gate.evaluate``, not ported). The default
#: constructs the real ``ThreadPoolExecutor``; a test injects a factory that
#: RECORDS what it was called with, proving the bound at the construction
#: boundary itself (O2), never by elapsed time.
ExecutorFactory = Callable[[int], Executor]


def _default_executor_factory(jobs: int) -> Executor:
    return ThreadPoolExecutor(max_workers=jobs)


def _classify_mutant_result(result: CommandResult) -> str:
    """Map one mutant's :class:`~assay.runner.CommandResult` onto one of
    the FOUR terminal buckets A-116/A-117 name — reusing
    :func:`~assay.runner.execute_command`'s own already-correct
    PASS/FAIL/ERROR/BUDGET_EXCEEDED split, never nyxloom's collapsed
    any-non-zero-exit-is-"killed" rule (A-122, confirmed present in
    ``mutation_gate._run_is_killed*``, deliberately not ported).

    An ordinary non-zero exit (FAIL/COMMAND_FAILED) means the suite caught
    the mutant — KILLED. An exit-0 PASS means the changed behaviour was
    never asserted — SURVIVED. ERROR/EXEC_FAILED (the process itself could
    not even start) is CRASHED, never conflated with a genuine test
    failure. BUDGET_EXCEEDED is its own bucket, never silently folded into
    either kill or survival.
    """
    if result.outcome is Outcome.PASS:
        return "survived"
    if result.outcome is Outcome.FAIL:
        return "killed"
    if result.outcome is Outcome.BUDGET_EXCEEDED:
        return "budget_exceeded"
    return "crashed"  # Outcome.ERROR -- the only remaining R0-producible outcome.


def project_prefix(*, repo_top: Path, project_root: Path) -> str:
    """*project_root*'s own location inside *repo_top*, as the forward-slash
    prefix its repo-relative paths carry — ``""`` when the two are the same
    directory, ``"assay/"`` when the project is a SUBDIRECTORY of its
    repository (A-145).

    That second case is not exotic: it is assay's own layout inside the
    ``vbpub`` monorepo, and it is the difference between two spellings of
    the same file that this module has to keep straight.
    :class:`MutationTarget`'s ``path`` is repo-top-relative (its own
    documented contract, and the spelling ``git diff`` and
    :class:`~assay.verdict.Coverage`'s own keys already use, so the artifact
    stays internally consistent), while the scratch tree each mutant runs in
    is a copy of *project_root* — so a repo-relative path must have this
    prefix removed before it names anything inside that copy.
    """
    relative = project_root.resolve().relative_to(repo_top.resolve())
    return "" if relative == Path(".") else f"{relative.as_posix()}/"


def _within_project(path: str, prefix: str) -> str:
    """*path* (repo-relative) respelled relative to the project root, or a
    refusal. A target outside the project root has no counterpart inside
    the scratch copy at all, so writing it would either land outside the
    copy or fail deep inside a worker thread with a bare
    ``FileNotFoundError`` naming a path no caller ever supplied — checked
    here, once, before anything is submitted."""
    if not path.startswith(prefix):
        raise ValueError(
            f"mutation target {path!r} is outside the project root "
            f"({prefix!r}) -- it has no location inside the per-mutant copy"
        )
    return path[len(prefix) :]


def _outcome_of(job: MutantJob) -> MutantOutcome:
    """The artifact projection of one attempted mutant (A-180).

    Built DIRECTLY from the validated site, so the wire identity and the
    experiment that produced it cannot disagree — never reconstructed by
    diffing the mutated file against the original, which does not recover a
    syntax site at all.
    """
    return MutantOutcome(
        path=job.path,
        lineno=job.site.lineno,
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        replacement_sha256=job.site.replacement_sha256,
        operator=job.site.operator,
        description=job.site.description,
    )


def _run_one_mutant(
    lane: Lane,
    *,
    job: MutantJob,
    write_path: str,
    project_root: Path,
    scratch_parent: Path,
    index: int,
    execute: Callable[..., CommandResult],
    process_runner: ProcessRunner,
    clock: Clock,
) -> CommandResult:
    """Isolate *job* into a FRESH ``shutil.copytree`` scratch directory
    (A-120 — never in-place, never a ``git worktree``), write
    the ONE full replacement file (spliced here, from the job's shared
    original plus its site) over *write_path* INSIDE the copy, and run
    the SAME ``execute_command`` (*execute*) the baseline used, varying
    only ``cwd``. The scratch copy is discarded afterward; *project_root*
    itself is never opened for writing here — that is what makes "the
    shared source tree is provably unchanged" true BY CONSTRUCTION, not by
    a write-then-restore round-trip (O3).

    *write_path* is ``job.path`` respelled relative to *project_root* by
    :func:`_within_project` (A-145) — the copy's own root is
    *project_root*, not the repository top ``job.path`` is relative to, and
    those differ for any project living in a subdirectory of its repo.

    *index* names the scratch directory (``mutant-{index:06d}``) — unique
    per submitted job with no shared counter or uuid needed, because the
    job list's own length is already known up front before any job is
    submitted (A-113's "deterministic submission order").
    """
    scratch_dir = scratch_parent / f"mutant-{index:06d}"
    shutil.copytree(project_root, scratch_dir)
    try:
        # A-180: the ONE place a full replacement file exists. It is built
        # here, inside the worker that is about to run it, from the shared
        # original plus this site's own splice -- so at most `jobs` of them
        # are alive at once, rather than one per candidate at discovery time.
        (scratch_dir / write_path).write_bytes(
            job.site.apply(job.original_text.encode("utf-8"))
        )
        return execute(lane, cwd=scratch_dir, process_runner=process_runner, clock=clock)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def run_mutation(
    lane: Lane,
    *,
    baseline: CommandResult,
    project_root: Path,
    repo_top: Path,
    scratch_root: Path,
    targets: Iterable[MutationTarget],
    adapter: LanguageAdapter,
    jobs: int,
    max_mutants: int,
    operators: tuple[str, ...],
    process_runner: ProcessRunner | None = None,
    clock: Clock | None = None,
    executor_factory: ExecutorFactory = _default_executor_factory,
) -> Mutation | Literal["UNSUPPORTED"] | None:
    """The R2 execution entry point (A-119/A-120; *baseline* refactored in
    P18 work item 4). *baseline* is now a MANDATORY, ALREADY-OBTAINED
    :class:`~assay.runner.CommandResult` — the exact R0 result
    :func:`~assay.runner.run_lane` already produced for this same lane
    against this same, still-unmodified *project_root* — never re-executed
    here (sol finding 11: the old internal baseline doubled the command
    ledger). A baseline that did not PASS stops HERE, before
    :func:`collect_mutation_sites` is even called, let alone anything submitted
    to an executor (O1's own negative: "submits mutant work... when the
    original suite is already red") — and this function returns ``None``
    (A-116's baseline-conditional presence rule: mutation testing never
    started). :func:`judge_mutation`/:func:`build_mutation_claim` read the
    caller's OWN *baseline* to propagate its exact ``(outcome,
    reason_code)`` verbatim in that case, so a caller passes the identical
    object to both this function and those.

    *jobs* is validated (a positive, non-boolean integer) BEFORE the
    executor boundary (work item 5): this function is a public surface a
    caller may invoke directly — every test in this module's own suite
    does — so :mod:`assay.config`'s load-time discipline for a real
    ``assay.toml`` cannot be assumed to have already run here.

    *repo_top* is the repository top every ``targets`` path is relative to
    (A-145). It is REQUIRED, and deliberately not defaulted to
    *project_root*: the two differ for any project living in a
    subdirectory of its repository — assay's own layout — and a default
    that silently assumes they are equal is exactly how a caller ends up
    writing a mutant to a path that does not exist inside the copy. See
    :func:`project_prefix`.

    Only when the baseline PASSES: collects every candidate site across
    *targets*, passing *operators* — the lane's own declared, closed,
    order-preserving selection — INTO each adapter call, so a candidate the
    lane never selected is never built in the first place (P21 moved this
    from a post-hoc filter over already-collected jobs; filtering afterwards
    meant the cap counted work that could never run). *operators* is
    re-validated by :func:`collect_mutation_sites` itself, not merely at
    :mod:`assay.config` load time: it is a public surface a caller may
    invoke directly, and an empty, duplicated, or unknown selection raises
    ``ValueError`` there rather than silently becoming the bound handed to
    every adapter. Then fans the resulting job list out over
    ``executor_factory(jobs)`` — called with
    EXACTLY *jobs*, never a derived or machine-sourced value (A-082/
    A-122) — isolating each mutant into its own fresh scratch copy under
    *scratch_root* (A-120). Results are collected POSITION-ALIGNED with
    the submitted job list (each ``Future`` is awaited by its own index
    via the returned futures list, never via ``as_completed`` or a dict
    keyed by arrival order), and the three non-killed buckets are sorted
    by stable identity before :class:`~assay.verdict.Mutation` is built —
    so ``jobs=1`` and ``jobs=3`` render IDENTICAL records regardless of
    which thread's subprocess happens to finish first (O2/O3).

    *process_runner*/*clock* default to the real boundary
    (``assay.runner.default_process_runner`` / a real UTC clock), resolved
    with a DEFERRED import inside this function body rather than at module
    level — this module's own docstring explains why (a genuine circular
    import, not a style choice).
    """
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise ValueError(f"run_mutation jobs must be an integer, got {jobs!r}")
    if jobs < 1:
        raise ValueError(f"run_mutation jobs must be >= 1, got {jobs}")
    if isinstance(max_mutants, bool) or not isinstance(max_mutants, int):
        raise ValueError(
            f"run_mutation max_mutants must be an integer, got {max_mutants!r}"
        )
    if not 1 <= max_mutants <= 10_000:
        raise ValueError(
            f"run_mutation max_mutants must be in 1..10,000, got {max_mutants}"
        )

    if baseline.outcome is not Outcome.PASS:
        return None

    # A-180: discovery is bounded at max+1 -- one MORE than the cap, so the
    # difference between "exactly at the cap" and "more than the cap" is an
    # observation rather than a guess, and the sentinel can state a fact.
    collected = collect_mutation_sites(
        targets, adapter=adapter, operators=operators, limit=max_mutants + 1
    )
    # A-183: propagated BEFORE project-prefix arithmetic, scratch creation,
    # executor construction, or submission. There is nothing to isolate and
    # nothing to run when no analysis ever happened.
    if collected == UNSUPPORTED:
        return UNSUPPORTED

    job_list = collected
    candidate_count = len(job_list)
    if candidate_count > max_mutants:
        # The pre-submission refusal. No partial sample and no credit: the
        # sentinel records what was observed and that nothing was attempted.
        return Mutation(candidate_count=candidate_count, total=0)
    total = candidate_count
    if total == 0:
        return Mutation(candidate_count=0, total=0)

    prefix = project_prefix(repo_top=repo_top, project_root=project_root)
    write_paths = tuple(_within_project(job.path, prefix) for job in job_list)

    from .runner import default_process_runner, execute_command

    resolved_process_runner = (
        default_process_runner if process_runner is None else process_runner
    )
    resolved_clock = _utc_now if clock is None else clock

    def _run(job: MutantJob, write_path: str, index: int) -> CommandResult:
        return _run_one_mutant(
            lane,
            job=job,
            write_path=write_path,
            project_root=project_root,
            scratch_parent=scratch_root,
            index=index,
            execute=execute_command,
            process_runner=resolved_process_runner,
            clock=resolved_clock,
        )

    with executor_factory(jobs) as pool:
        futures = [
            pool.submit(_run, job, write_path, index)
            for index, (job, write_path) in enumerate(zip(job_list, write_paths))
        ]
        results = [future.result() for future in futures]

    buckets: dict[str, list[MutantOutcome]] = {
        "killed": [],
        "survived": [],
        "crashed": [],
        "budget_exceeded": [],
    }
    for job, result in zip(job_list, results):
        # A-180: killed is an identity list now, so every attempted mutant --
        # including the ones the suite caught -- is recorded and bindable to
        # the declared operator policy.
        #
        # Results are consumed POSITION-ALIGNED with the submitted job list
        # (each future awaited by its own index, never `as_completed`), and
        # `collect_mutation_sites` guarantees that list is identity-ordered:
        # per file by `_validate_sites`, across files by path order, and
        # `MutantOutcome.identity` leads with `path`. Appending in that order
        # therefore leaves every bucket identity-ordered already.
        #
        # P21 note: v3 sorted each bucket here afterwards. That sort is now
        # unreachable -- no input `collect_mutation_sites` can produce would
        # change under it -- and AUTHORING 3b.D asks for such a line to be
        # restructured away rather than kept and never exercised. The
        # invariant itself is not weakened: `Mutation` REFUSES to construct
        # an out-of-order bucket, so a future change that broke the ordering
        # would raise here rather than emit a scrambled artifact.
        buckets[_classify_mutant_result(result)].append(_outcome_of(job))

    return Mutation(
        candidate_count=candidate_count,
        total=total,
        killed=tuple(buckets["killed"]),
        survived=tuple(buckets["survived"]),
        crashed=tuple(buckets["crashed"]),
        budget_exceeded=tuple(buckets["budget_exceeded"]),
    )


def judge_mutation(
    baseline: CommandResult, mutation: Mutation | Literal["UNSUPPORTED"] | None
) -> tuple[Outcome, ReasonCode | None]:
    """A-117's outcome/reason-code mapping, using only already-existing
    ``ReasonCode``s (``errors.py`` stays forbidden, A-121): baseline
    non-PASS reuses ``execute_command``'s own ``(outcome, reason_code)``
    verbatim (*mutation* is ``None`` in that case, A-116); else
    ``mutation.total == 0`` -> ``INCONCLUSIVE``/``NO_MUTANTS``; else
    non-empty ``crashed`` -> ``ERROR``/``EXEC_FAILED``; else non-empty
    ``budget_exceeded`` -> ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``; else
    non-empty ``survived`` -> ``FAIL``/``MUTANTS_SURVIVED``; else ``PASS``.
    This precedence (crashed > budget_exceeded > survived) matches the
    existing cross-claim ``ROLLUP_PRECEDENCE`` applied one level down.
    """
    if mutation is None:
        return baseline.outcome, baseline.reason_code
    # A-183: capability absence, before any payload arithmetic. There is no
    # `Mutation` to read here at all, which is exactly the point -- a
    # zero/zero payload would assert an analysis that never ran.
    if mutation == UNSUPPORTED:
        return Outcome.INCONCLUSIVE, ReasonCode.MUTATION_UNSUPPORTED
    if mutation.is_limit_sentinel:
        # A-163: the refusal happened BEFORE submission, so this is not a
        # budget the run exhausted while working -- it is one it declined to
        # start against. `LANE_TIMEOUT` would misname it.
        return Outcome.BUDGET_EXCEEDED, ReasonCode.MUTANT_LIMIT_EXCEEDED
    if mutation.total == 0:
        return Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS
    if mutation.crashed:
        return Outcome.ERROR, ReasonCode.EXEC_FAILED
    if mutation.budget_exceeded:
        return Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT
    if mutation.survived:
        return Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED
    return Outcome.PASS, None


def build_mutation_claim(
    baseline: CommandResult, mutation: Mutation | Literal["UNSUPPORTED"] | None
) -> Claim:
    """The R2 :class:`~assay.verdict.Claim` from :func:`run_mutation`'s own
    return — the exact mapping ``assay.runner.build_r0_claim`` /
    ``assay.canary.build_canary_claim`` perform, one level over.

    The capability marker attaches NO payload (A-183): ``MUTATION_UNSUPPORTED``
    says no candidate analysis happened, and :class:`~assay.verdict.Claim`
    itself refuses the pairing if a payload is attached anyway.
    """
    status, reason_code = judge_mutation(baseline, mutation)
    payload = None if mutation == UNSUPPORTED else mutation
    return Claim(
        rigor="R2",
        source="computed",
        status=status,
        verified_by_assay=True,
        reason_code=reason_code,
        mutation=payload,
    )
