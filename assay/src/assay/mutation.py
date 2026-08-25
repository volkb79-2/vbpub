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
trigger it). :func:`run_mutation` resolves ``execute_plan`` with a DEFERRED
(function-body-local) import instead — safe because by the time the
function is actually CALLED, module loading for the whole program has long
finished, regardless of which module was imported first. The
``ProcessRunner``/``CommandResult``/``LanguageAdapter``/``CommandPlan``/
``LaneDeadline`` type hints below reference their real home (
``assay.runner``) and ``SnapshotRepository`` references its real home
(``assay.isolation``), all by bare name, never imported — safe under this
module's own ``from __future__ import annotations``, which defers every
annotation to a string never evaluated at runtime (verified empirically),
and avoids re-opening the same cycle purely for a type hint.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import tempfile
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace as _dataclass_replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Literal, Mapping, Sequence

from . import git, safeio
from .diff import AddedLines
from .errors import AssayError, Outcome, ReasonCode
from .isolation import SnapshotRepository
from .verdict import (
    MUTATION_BUCKETS,
    MAX_CANDIDATE_CEILING,
    MAX_SHARD_COUNT,
    Claim,
    Mutation,
    MutantOutcome,
    iso_utc,
)
from .vocabulary import MUTATION_OPERATORS

if TYPE_CHECKING:
    # Annotation-only: `.adapters.base` imports `MutationSite` from this
    # module and `.runner` imports this module for command execution, so
    # importing either back at runtime would be circular. `from __future__
    # import annotations` (above) already defers every annotation's
    # evaluation; this block exists only so a type checker (and pyflakes)
    # can resolve these names, never runs.
    from .adapters.base import LanguageAdapter
    from .runner import CommandPlan, CommandResult, LaneDeadline, ProcessRunner

__all__ = [
    "MAX_CANDIDATE_CEILING",
    "MAX_SHARD_COUNT",
    "MAX_EQUIVALENCE_ARTIFACT_BYTES",
    "MAX_KILL_SIGNAL_BYTES",
    "MUTATION_OPERATORS",
    "Clock",
    "ExecutorFactory",
    "MutantJob",
    "MutationDiscoveryError",
    "MutationSite",
    "MutationTarget",
    "ProgressWriter",
    "MutationStateError",
    "build_mutation_claim",
    "byte_offset",
    "collect_mutation_sites",
    "candidate_id",
    "judge_mutation",
    "line_for_offset",
    "merge_mutations",
    "merge_mutation_shards",
    "mutation_state_record_path",
    "resolve_mutation_targets",
    "run_mutation",
    "select_mutation_shard",
]

#: (P21/A-183) the adapter-wide capability sentinel, retained from the old
#: ``generate_mutants`` union. It means the adapter has NO mutation
#: implementation at all -- never a parse failure, never an unrecognised
#: individual construct, never a valid analysis that found nothing.
UNSUPPORTED = "UNSUPPORTED"

_CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{64}")

MUTATION_STATE_RECORD_LIMIT = 1024 * 1024
MUTATION_STATE_SCHEMA_VERSION = 1

#: (P34/§3.6) the equivalence artifact's own byte ceiling -- a schema dump
#: is comparable measurement output to a coverage artifact, so this reuses
#: :data:`~assay.coverage.MAX_COVERAGE_ARTIFACT_BYTES`'s own value rather
#: than inventing a second unrelated one, without importing that module (no
#: cycle risk either way, but this module's own byte-arithmetic identity
#: has no other reason to depend on :mod:`assay.coverage` at all).
MAX_EQUIVALENCE_ARTIFACT_BYTES = 16 * 1024 * 1024

#: (P34/§3.6) a kill signal is a short mechanism string -- the database's
#: own error text, never a payload -- so it is bounded well below the
#: artifact ceiling above; 64 KiB is generous headroom over any single
#: PostgreSQL error message.
MAX_KILL_SIGNAL_BYTES = 64 * 1024

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


class MutationStateError(AssayError):
    """A persisted mutation record or shard summary cannot be trusted."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
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

ProgressWriter = Callable[[Mapping[str, Any]], None]


def _default_executor_factory(jobs: int) -> Executor:
    return ThreadPoolExecutor(max_workers=jobs)


@contextmanager
def progress_writer(path: Path) -> Iterator[ProgressWriter]:
    """Append one compact JSON object per line and flush every record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:

        def write(event: Mapping[str, Any]) -> None:
            stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
            stream.flush()

        yield write


def _progress_event(
    *, candidate_index: int, candidate_total: int, job: MutantJob
) -> dict[str, Any]:
    original_bytes = job.original_text.encode("utf-8")
    replacement_bytes = job.site.apply(original_bytes)
    return {
        "candidate_id": candidate_id(job),
        "candidate_index": candidate_index,
        "candidate_total": candidate_total,
        "description": job.site.description,
        "lineno": job.site.lineno,
        "path": job.path,
        "operator": job.site.operator,
        "start_byte": job.site.start_byte,
        "end_byte": job.site.end_byte,
        # B031/A-320: `replacement_bytes` here is `site.apply(original)` --
        # the WHOLE mutated file -- while the verdict's
        # `MutantOutcome.replacement_sha256` is the digest of the replacement
        # TEXT alone. Two different digests under one field name, for the
        # same candidate, defeats the one thing a progress artifact is for.
        # The progress stream names what it actually hashes.
        "mutated_file_sha256": hashlib.sha256(replacement_bytes).hexdigest(),
    }


def candidate_id(job: MutantJob) -> str:
    """Return the stable digest identity shared by plans, state and shards."""
    original_bytes = job.original_text.encode("utf-8")
    replacement_bytes = job.site.apply(original_bytes)
    identity = "\0".join(
        (
            job.path,
            hashlib.sha256(original_bytes).hexdigest(),
            str(job.site.start_byte),
            str(job.site.end_byte),
            hashlib.sha256(replacement_bytes).hexdigest(),
            job.site.operator,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def mutation_state_record_path(candidate: str) -> str:
    """Return the canonical project-relative state-file spelling."""
    if not isinstance(candidate, str) or _CANDIDATE_ID_RE.fullmatch(candidate) is None:
        raise ValueError(
            f"candidate id must be a 64-character hexadecimal digest, got {candidate!r}"
        )
    return f".assay/mutation-state/{candidate.lower()}.json"


def _write_mutation_state_record(project_root: Path, payload: Mapping[str, Any]) -> None:
    relative_path = PurePosixPath(mutation_state_record_path(payload["candidate_id"]))
    parent = project_root.joinpath(*relative_path.parts[:-1])
    destination = parent / relative_path.parts[-1]
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _load_validated_state_record(
    project_root: Path, job: MutantJob
) -> Mapping[str, Any] | None:
    identity = candidate_id(job)
    raw = safeio.read_bounded_input(
        project_root,
        mutation_state_record_path(identity),
        limit=MUTATION_STATE_RECORD_LIMIT,
    )
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MutationStateError(f"mutation-state record {identity} is not valid UTF-8 JSON") from exc
    required = {
        "schema_version": MUTATION_STATE_SCHEMA_VERSION,
        "candidate_id": identity,
        "path": job.path,
        "replacement_sha256": hashlib.sha256(job.site.replacement).hexdigest(),
        "operator": job.site.operator,
        "source_sha256": hashlib.sha256(job.original_text.encode("utf-8")).hexdigest(),
    }
    for key, expected in required.items():
        if key not in payload:
            raise MutationStateError(f"mutation-state record {identity} is missing {key}")
        if payload[key] == expected:
            continue
        if key == "schema_version":
            # (B021) `schema_version` is the ONE required key not folded
            # into `identity` (mutation.py's own filename derivation hashes
            # path/source/span/replacement/operator, never the schema
            # version) -- so it is the only key that can legitimately
            # mismatch without the record being corrupt: a routine bump of
            # MUTATION_STATE_SCHEMA_VERSION. Treating it as absent (silent
            # rerun) instead of failing the whole lane means an old-format
            # cache is a cache miss, not an outage, for every consumer's
            # next `--resume` after an upgrade.
            return None
        # Every OTHER key (candidate_id, path, operator, replacement_sha256,
        # source_sha256) IS folded into `identity`, and therefore into this
        # record's own filename -- a mismatch here means the record on disk
        # contradicts the identity it is filed under, which is evidence of
        # corruption or hand-editing, not a routine cache event. Silently
        # treating that as "absent" (the pre-B021 disposition for
        # source_sha256 specifically) would discard exactly the evidence
        # this check exists to surface.
        raise MutationStateError(f"mutation-state record {identity} has stale {key}")
    if "outcome_bucket" not in payload:
        raise MutationStateError(f"mutation-state record {identity} is missing outcome_bucket")
    if payload["outcome_bucket"] not in MUTATION_BUCKETS:
        raise MutationStateError(
            f"mutation-state record {identity} has unknown outcome bucket "
            f"{payload['outcome_bucket']!r}"
        )
    return payload


def merge_mutations(current: Mutation, records: Iterable[Mapping[str, Any]]) -> Mutation:
    """Fold already-validated resumed records into a completed shard run."""
    buckets: dict[str, list[MutantOutcome]] = {
        name: list(getattr(current, name)) for name in MUTATION_BUCKETS
    }
    for record in records:
        buckets[record["outcome_bucket"]].append(_outcome_from_record(record))
    identities = [
        outcome.identity for name in MUTATION_BUCKETS for outcome in buckets[name]
    ]
    duplicate_count = len(identities) - len(set(identities))
    normalized = {
        name: tuple(sorted(buckets[name], key=lambda item: item.identity))
        for name in MUTATION_BUCKETS
    }
    payload = Mutation(
        candidate_count=len(identities),
        total=len(identities),
        killed=normalized["killed"],
        survived=normalized["survived"],
        crashed=normalized["crashed"],
        budget_exceeded=normalized["budget_exceeded"],
        equivalent=normalized["equivalent"],
    )
    if duplicate_count:
        raise MutationStateError(
            f"resumed records repeat {duplicate_count} candidate identity"
        )
    return payload


def _outcome_from_record(record: Mapping[str, Any]) -> MutantOutcome:
    try:
        return MutantOutcome(
            path=record["path"],
            lineno=int(record["lineno"]),
            start_byte=int(record["start_byte"]),
            end_byte=int(record["end_byte"]),
            replacement_sha256=record["replacement_sha256"],
            operator=record["operator"],
            description=record["description"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MutationStateError(f"invalid resumed mutation record field: {exc}") from exc


def select_mutation_shard(candidates: Sequence[str], *, index: int, count: int) -> list[int]:
    """Return deterministic positions whose opaque IDs assign to *index*."""
    if isinstance(index, bool) or isinstance(count, bool):
        raise ValueError("shard index and count must be integers")
    if not isinstance(index, int) or not isinstance(count, int):
        raise ValueError("shard index and count must be integers")
    if not 1 <= count <= MAX_SHARD_COUNT:
        raise ValueError(
            f"shard count must be in 1..{MAX_SHARD_COUNT}, got {count}"
        )
    if not 0 <= index < count:
        raise ValueError(f"shard index {index} is outside 0..{count - 1}")
    return [
        position
        for position, candidate in enumerate(candidates)
        if int.from_bytes(
            hashlib.blake2b(candidate.encode("ascii"), digest_size=4).digest(), "big"
        )
        % count
        == index
    ]


_SHARD_REQUIRED_KEYS = frozenset(
    {"schema_version", "lane", "commit", "shard_index", "shard_count", "candidate_ids"}
)
_SHARD_OPTIONAL_KEYS = frozenset({"operators", "jobs"})


def merge_mutation_shards(documents: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Validate exact shard coverage and return their duplicate-free union."""
    documents = list(documents)
    if not documents:
        raise MutationStateError("cannot merge zero mutation shards")
    seen_candidates: set[str] = set()
    lanes: set[str] = set()
    commits: set[str] = set()
    covered_pairs: set[tuple[int, int]] = set()
    merged_candidates: list[str] = []
    for document in documents:
        keys = set(document)
        missing = sorted(_SHARD_REQUIRED_KEYS - keys)
        unknown = sorted(keys - _SHARD_REQUIRED_KEYS - _SHARD_OPTIONAL_KEYS)
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing keys {missing}")
            if unknown:
                details.append(f"unknown keys {unknown}")
            raise MutationStateError(f"invalid shard summary: {'; '.join(details)}")
        version = document["schema_version"]
        lane = document["lane"]
        commit = document["commit"]
        shard_index = document["shard_index"]
        shard_count = document["shard_count"]
        if version != MUTATION_STATE_SCHEMA_VERSION:
            raise MutationStateError(
                f"unsupported shard schema_version {version!r}; expected "
                f"{MUTATION_STATE_SCHEMA_VERSION}"
            )
        if not isinstance(lane, str) or not lane or not isinstance(commit, str) or not commit:
            raise MutationStateError("shard lane and commit must be non-empty strings")
        if (
            isinstance(shard_index, bool)
            or isinstance(shard_count, bool)
            or not isinstance(shard_index, int)
            or not isinstance(shard_count, int)
        ):
            raise MutationStateError("shard index and count must be integers")
        if not 1 <= shard_count <= MAX_SHARD_COUNT or not 0 <= shard_index < shard_count:
            raise MutationStateError(f"invalid shard pair ({shard_index}, {shard_count})")
        candidate_ids = document["candidate_ids"]
        if not isinstance(candidate_ids, list):
            raise MutationStateError("shard candidate_ids must be an array")
        for candidate in candidate_ids:
            try:
                normalized_path = mutation_state_record_path(candidate)
            except ValueError as exc:
                raise MutationStateError(str(exc)) from exc
            # Recompute the SAME deterministic assignment `select_mutation_
            # shard` uses, against the candidate's OWN id -- a document
            # cannot claim a shard index its listed candidates do not
            # actually hash to. Without this, one shard's document could
            # list another shard's real candidates (or any well-formed id)
            # and merge as if it had done that work.
            assigned = (
                int.from_bytes(
                    hashlib.blake2b(candidate.encode("ascii"), digest_size=4).digest(),
                    "big",
                )
                % shard_count
            )
            if assigned != shard_index:
                raise MutationStateError(
                    f"candidate {candidate!r} hashes to shard {assigned}/{shard_count}, "
                    f"not the claimed {shard_index}/{shard_count}"
                )
            if normalized_path in seen_candidates:
                raise MutationStateError(
                    f"non-disjoint shard input repeats candidate {normalized_path}"
                )
            seen_candidates.add(normalized_path)
            merged_candidates.append(candidate.lower())
        lanes.add(lane)
        commits.add(commit)
        pair = (shard_index, shard_count)
        if pair in covered_pairs:
            # (B012 remediation, N-merge-F) `covered_pairs` is a set, so two
            # documents claiming the same shard index used to merge
            # silently -- each individually valid, together not proof of
            # "exactly one document per shard". Their candidate ids are
            # already required to be disjoint (checked above); this closes
            # the remaining gap: the SAME index cannot be filed twice.
            raise MutationStateError(
                f"shard {shard_index}/{shard_count} is present in more than one document"
            )
        covered_pairs.add(pair)
    if len(lanes) > 1 or len(commits) > 1:
        raise MutationStateError("all shards must share exactly one lane and commit")
    counts = {pair[1] for pair in covered_pairs}
    if len(counts) != 1:
        raise MutationStateError("all shards must declare the same shard_count")
    declared_count = next(iter(counts))
    required_pairs = {(index, declared_count) for index in range(declared_count)}
    missing_pairs = sorted(required_pairs - covered_pairs)
    if missing_pairs:
        rendered = ", ".join(f"{index}/{declared_count}" for index, _ in missing_pairs)
        raise MutationStateError(f"non-exhaustive shard input is missing {rendered}")
    extra_pairs = sorted(covered_pairs - required_pairs)
    if extra_pairs:
        raise MutationStateError(f"inconsistent shard pairs present: {extra_pairs}")
    if not merged_candidates:
        # A-278: a check with nothing to check is not a passing check. Every
        # required (index, count) pair being present says only that a
        # document was filed for each slot, never that any of them did work
        # -- three empty shards must not merge into "complete coverage of
        # zero candidates".
        raise MutationStateError(
            "shard merge covers zero candidates across all required shards"
        )
    return tuple(merged_candidates)


@dataclass(frozen=True, kw_only=True)
class _MutantRun:
    """(P34) one mutant's own full attempt record: the command's
    :class:`~assay.runner.CommandResult` plus whatever the lane's own
    declared artifacts held after it ran. Kept separate from
    :class:`~assay.verdict.MutantOutcome` (the WIRE identity) because
    classification needs the raw bytes/signal BEFORE the bucket is decided,
    and ``MutantOutcome`` cannot legally carry a ``kill_signal`` until the
    bucket it belongs to (``killed``) is already known (A-223e).
    """

    result: CommandResult
    equivalence_bytes: bytes | None = None
    kill_signal: str | None = None
    elapsed_seconds: float = 0.0


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


def _classify_mutant_result_with_equivalence(
    result: CommandResult,
    *,
    equivalence_bytes: bytes | None,
    baseline_equivalence: bytes,
) -> str:
    """(P34/A-279, carve §3.6) the SAME four-outcome split widened into a
    function of ``(outcome, artifact-present, artifact-bytes)`` -- reachable
    ONLY when the lane declares ``judge.mutation.equivalence_artifact``. The
    caller (:func:`run_mutation`) decides BEFORE either classifier is
    invoked which one applies, so the undeclared lane is never routed
    through here at all (O8's own inertness: :func:`_classify_mutant_result`
    is not merely equivalent on that path, it is the exact same call).

    ``PASS`` and ``FAIL`` both split the identical way: an ABSENT artifact
    means the mutated schema was never measured at all -- the lane declared
    one and its own command did not write it, so nothing was observed and
    this is ``crashed``, never a kill and never a survival (§9 M11's own
    reachable case: invalid DDL that still exits non-zero looks like a kill
    under the old exit-status-only mapping). Byte-EQUAL to the baseline's
    own artifact means the mutant provably changed nothing -- ``equivalent``,
    evidence about the MUTATION rather than about the tests. Anything else
    is the ordinary ``PASS``->``survived`` / ``FAIL``->``killed`` mapping.
    ``ERROR``/``BUDGET_EXCEEDED`` do not consult the artifact at all --
    unchanged from :func:`_classify_mutant_result`.
    """
    if result.outcome is Outcome.PASS:
        if equivalence_bytes is None:
            return "crashed"
        return "equivalent" if equivalence_bytes == baseline_equivalence else "survived"
    if result.outcome is Outcome.FAIL:
        if equivalence_bytes is None:
            return "crashed"
        return "equivalent" if equivalence_bytes == baseline_equivalence else "killed"
    if result.outcome is Outcome.BUDGET_EXCEEDED:
        return "budget_exceeded"
    return "crashed"  # Outcome.ERROR -- unchanged.


def _arm_artifact_reservation(
    project_root: Path, artifact: str, *, limit: int
) -> safeio.OutputReservation:
    """Reserve *artifact* under *project_root* and arm it BEFORE the
    mutant's own command runs (P34/§3.6's "why stale bytes cannot leak"):
    ``arm()`` unlinks any pre-existing regular file at that path, so a later
    absent read is a genuine fact about THIS run, never a stale copy left
    over from anything else. The identical reserve-then-unlink discipline
    :func:`~assay.runner._execute_snapshot_unit` already applies to the
    coverage artifact -- existing, proven machinery (A-180); this is P34's
    own call site for it, not a second mechanism.

    ``create_missing_parents=True``: every caller of this function runs
    inside an ephemeral, assay-owned P22 replacement snapshot -- never the
    consumer's real worktree -- exactly the condition
    :func:`safeio.reserve_output`'s own contract requires before that
    default may be overridden.
    """
    reservation = safeio.reserve_output(
        project_root, artifact, limit=limit, create_missing_parents=True
    )
    reservation.arm()
    return reservation


def _read_kill_signal(reservation: safeio.OutputReservation) -> str | None:
    """Consume an already-armed *reservation* and decode the killed
    mutant's own mechanism string (P34/carve §3.6): bounded by the
    reservation's own limit, UTF-8, stripped, and recorded VERBATIM -- assay
    never parses or interprets it (the module docstring's own "assay never
    shells out" discipline one level over: it never reads meaning INTO a
    consumer's own string either).

    ``None`` both when the artifact was never written (``consume()``'s own
    "missing output" contract) and when it reads as empty after stripping --
    both are "no signal", and the caller reclassifies a killed mutant with
    no signal to ``crashed`` under declared attribution (A-223e): an empty
    file names no mechanism any more than a missing one does.
    """
    raw = reservation.consume()
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"kill_signal_artifact {reservation.artifact!r} is not valid "
            f"UTF-8: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    return text or None


def _outcome_of(job: MutantJob, *, kill_signal: str | None = None) -> MutantOutcome:
    """The artifact projection of one attempted mutant (A-180).

    Built DIRECTLY from the validated site, so the wire identity and the
    experiment that produced it cannot disagree — never reconstructed by
    diffing the mutated file against the original, which does not recover a
    syntax site at all. *kill_signal* (P34) is attached ONLY by the caller
    that already knows this job landed in the ``killed`` bucket -- passing
    it for any other bucket would violate :class:`~assay.verdict.Mutation`'s
    own ``kill_signal``-is-``killed``-only invariant (A-223e).
    """
    return MutantOutcome(
        path=job.path,
        lineno=job.site.lineno,
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        replacement_sha256=job.site.replacement_sha256,
        operator=job.site.operator,
        description=job.site.description,
        kill_signal=kill_signal,
    )


def _snapshot_left_dirt(
    job: MutantJob, snapshot, *, remaining: git.Remaining | None = None
) -> AssayError | None:
    """P23/A-195: a mutant's own snapshot must still name its own commit
    after the command runs, exactly like every other snapshot unit. Checked
    HERE, inside the worker, so a dirty/committing mutant is caught before
    its context closes and before any sibling is affected.

    *remaining* (P26/A-212) bounds this check's own Git children by the one
    lane deadline; :func:`run_mutation` passes ``deadline.remaining``, so a
    hung ``status``/``rev-parse`` can never outlive the lane budget. Because
    this check runs after a mutant is already decided, that call site absorbs
    exactly ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` (see its own comment) so an
    already-decisive result is never discarded for a partial sample; a
    legacy/library caller may still omit *remaining*.
    """
    if git.dirty_paths(snapshot.root, remaining=remaining):
        return AssayError(
            f"mutant {job.path}:{job.site.identity} left the snapshot's "
            f"tracked/support state dirty; the result no longer represents "
            f"its own commit",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.DIRTY_TREE,
        )
    if git.head_rev(snapshot.root, remaining=remaining) != snapshot.commit:
        return AssayError(
            f"mutant {job.path}:{job.site.identity} committed inside its "
            f"snapshot; the result no longer represents its own commit",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.HEAD_CHANGED,
        )
    return None


def run_mutation(
    *,
    baseline: CommandResult,
    prepared: SnapshotRepository,
    plan: CommandPlan,
    deadline: LaneDeadline,
    targets: Iterable[MutationTarget],
    adapter: LanguageAdapter,
    jobs: int,
    max_mutants: int,
    operators: tuple[str, ...],
    process_runner: ProcessRunner,
    clock: Clock,
    executor_factory: ExecutorFactory = _default_executor_factory,
    equivalence_artifact: str | None = None,
    kill_signal_artifact: str | None = None,
    baseline_equivalence: bytes | None = None,
    budget_per_candidate_seconds: float | None = None,
    progress_artifact: Path | str | None = None,
    state_project_root: Path | str | None = None,
    resume: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> Mutation | Literal["UNSUPPORTED"] | None:
    """The R2 execution entry point (P23 exact reexecution): every mutant is
    a FRESH, INDEPENDENT P22 replacement snapshot of the same prepared seed
    *prepared* materialises baseline from — never a ``shutil.copytree`` of a
    live project directory. *baseline* is the exact R0 result the caller's
    own snapshot unit already produced against this same prepared seed
    (never re-executed here, A-119/sol finding 11). A baseline that did not
    PASS stops HERE, before :func:`collect_mutation_sites` is even called,
    let alone anything submitted to an executor (A-116's baseline-
    conditional presence rule) — this function returns ``None``.

    *jobs* is validated (a positive, non-boolean integer) BEFORE the
    executor boundary (work item 5): this function is a public surface a
    caller may invoke directly, so :mod:`assay.config`'s load-time
    discipline for a real ``assay.toml`` cannot be assumed to have already
    run here.

    Only when the baseline PASSES: collects every candidate site across
    *targets* at ``limit=max_mutants + 1`` (A-180) — the exact max+1
    sentinel refuses BEFORE this function ever touches *prepared*,
    *executor_factory*, or *process_runner* (O4). Each attempted mutant then
    builds its ONE full replacement blob from its job's immutable original
    bytes and site splice, and enters
    ``prepared.materialize_replacement(path=PurePosixPath(job.path),
    expected=..., replacement=..., timeout=deadline.remaining())`` — *path*
    is ``job.path`` UNCHANGED, because P22's replacement path is already
    repo-top-relative, the identical spelling a :class:`MutationTarget`
    carries (never a project-relative respelling, A-145's old shape). The
    frozen *plan* runs at the returned ``snapshot.project_root`` with a
    FRESH ``deadline.remaining()`` (A-160/A-193); the snapshot is then
    checked for dirt/HEAD drift before its context closes (A-195) — a
    mutant that leaves Git-visible state stops the whole claim (payload-free
    NO_MEASUREMENT/DIRTY_TREE or HEAD_CHANGED, no partial credit, no later
    unit), never folded into ``crashed``.

    Submission proceeds in WAVES of at most *jobs* concurrent mutants
    (``executor_factory(jobs)`` called with EXACTLY *jobs*, never a derived
    value, A-082/A-122); each wave is fully joined — every submitted future
    closes its own snapshot context — before the next wave is considered, so
    a deadline expiry or a fatal dirt/HEAD result observed inside one wave
    launches no mutant in a later wave. Expiry (``deadline.remaining()``
    raising BUDGET_EXCEEDED/LANE_TIMEOUT from inside a worker, before that
    worker's own snapshot or process exists) marks that identity and every
    later, unsubmitted identity ``budget_exceeded`` — completed identities
    remain evidence, never discarded for a partial sample. Results are
    collected POSITION-ALIGNED with the submitted job list (each future
    awaited by its own index), and the three non-killed buckets are
    naturally identity-ordered already (never re-sorted, matching
    :func:`collect_mutation_sites`'s own already-ordered batches).

    **P34/A-279/§3.6.** *equivalence_artifact*/*kill_signal_artifact* are
    ``None`` for every lane that does not declare them (every Python/Go
    lane through this build, A-227) -- their absence is exactly what routes
    every mutant through the UNCHANGED :func:`_classify_mutant_result`
    rather than :func:`_classify_mutant_result_with_equivalence` (O8's own
    inertness). When *equivalence_artifact* IS declared, *baseline_equivalence*
    must be the caller's own already-resolved baseline artifact bytes --
    never ``None``, which this function refuses outright rather than
    silently comparing every mutant's bytes against it (a comparison that
    can never be equal, so no mutant could ever be recorded ``equivalent``,
    hiding exactly the fault the carve's own A-279 finding was about).
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
    if equivalence_artifact is not None and baseline_equivalence is None:
        raise ValueError(
            "run_mutation equivalence_artifact is declared but "
            "baseline_equivalence is None; comparing a mutant's artifact "
            "bytes against None can never be equal, so no mutant could ever "
            "be recorded equivalent -- the caller must resolve the "
            "baseline's own artifact bytes (or its own EXEC_FAILED refusal, "
            "when the baseline never wrote it) before calling this function"
        )
    if budget_per_candidate_seconds is not None and (
        isinstance(budget_per_candidate_seconds, bool)
        or not isinstance(budget_per_candidate_seconds, (int, float))
        or not math.isfinite(budget_per_candidate_seconds)
        or budget_per_candidate_seconds <= 0
    ):
        raise ValueError(
            "run_mutation budget_per_candidate_seconds must be a positive "
            f"finite number or None, got {budget_per_candidate_seconds!r}"
        )
    if not isinstance(resume, bool):
        raise ValueError(f"run_mutation resume must be a boolean, got {resume!r}")
    shard_specified = shard_index is not None or shard_count is not None
    if state_project_root is None and (resume or shard_specified):
        raise ValueError(
            "run_mutation resume requires the caller's authoritative "
            "state_project_root; an ephemeral snapshot cannot own resume evidence"
        )
    if shard_specified and (shard_index is None or shard_count is None):
        raise ValueError("run_mutation requires both shard_index and shard_count")
    if shard_specified:
        select_mutation_shard((), index=shard_index, count=shard_count)
    if state_project_root is not None:
        Path(state_project_root).mkdir(parents=True, exist_ok=True)

    if baseline.outcome is not Outcome.PASS:
        return None

    progress_context = (
        progress_writer(Path(progress_artifact))
        if progress_artifact is not None
        else nullcontext[ProgressWriter | None](None)
    )

    with progress_context as write_progress:
        from .runner import execute_plan

        collected = collect_mutation_sites(
            targets, adapter=adapter, operators=operators, limit=max_mutants + 1
        )
        if collected == UNSUPPORTED:
            return UNSUPPORTED

        job_list = collected
        candidate_count = len(job_list)
        if candidate_count > max_mutants:
            return Mutation(candidate_count=candidate_count, total=0)
        total = candidate_count
        if total == 0:
            return Mutation(candidate_count=0, total=0)

        selected_indices = list(range(total))
        if shard_specified:
            assert shard_index is not None and shard_count is not None
            selected_indices = select_mutation_shard(
                [candidate_id(job) for job in job_list],
                index=shard_index,
                count=shard_count,
            )
            if write_progress is not None:
                write_progress(
                    {
                        "candidate_total": total,
                        "event": "shard",
                        "selected_total": len(selected_indices),
                        "shard_index": shard_index,
                        "shard_count": shard_count,
                    }
                )

        selected_jobs = tuple(job_list[index] for index in selected_indices)
        resumed_records: list[Mapping[str, Any]] = []
        if resume:
            assert isinstance(state_project_root, Path)
            for job in selected_jobs:
                record = _load_validated_state_record(state_project_root, job)
                if record is not None:
                    resumed_records.append(record)
            resumed_ids = {record["candidate_id"] for record in resumed_records}
            pending_jobs = tuple(
                job for job in selected_jobs if candidate_id(job) not in resumed_ids
            )
            if write_progress is not None and resumed_records:
                write_progress(
                    {
                        "candidate_total": total,
                        "event": "resume",
                        "resumed_total": len(resumed_records),
                    }
                )
        else:
            pending_jobs = selected_jobs

        if write_progress is not None:
            # B031/A-320: the stream is opened for APPEND and never
            # truncated, so successive runs share one file. Without this
            # header a tailing monitor cannot tell which run a
            # `candidate_index: 0` belongs to -- there was no run id, commit
            # or timestamp anywhere in the artifact.
            write_progress(
                {
                    "candidate_total": total,
                    "commit": prepared.spec.commit,
                    "event": "run",
                    "started": iso_utc(clock()),
                }
            )
            write_progress(
                {
                    "candidate_index": -1,
                    "candidate_total": total,
                    "event": "baseline",
                    "path": ".",
                    "operator": "baseline",
                    "start_byte": 0,
                    "end_byte": 0,
                    "mutated_file_sha256": "",
                }
            )

        result_payload = _execute_mutation_jobs(
            job_list=pending_jobs,
            deadline=deadline,
            jobs=jobs,
            prepared=prepared,
            plan=plan,
            process_runner=process_runner,
            clock=clock,
            executor_factory=executor_factory,
            equivalence_artifact=equivalence_artifact,
            kill_signal_artifact=kill_signal_artifact,
            baseline_equivalence=baseline_equivalence,
            budget_per_candidate_seconds=budget_per_candidate_seconds,
            execute_plan=execute_plan,
            write_progress=write_progress,
            total=len(pending_jobs),
            candidate_count=len(pending_jobs),
            state_project_root=state_project_root,
        )

        if resumed_records:
            result_payload = merge_mutations(result_payload, resumed_records)
        if write_progress is not None and resumed_records:
            write_progress({"event": "resume_merged", "resumed_total": len(resumed_records)})
        if shard_specified:
            # B031/A-320: `mutation.candidate_ids` has existed in the
            # dataclass and the schema since `7a4f6333` with NO producer --
            # a sharded verdict recorded `shard_index`/`shard_count` and
            # nothing about WHICH candidates that shard actually covered, so
            # "this shard was clean" and "this shard selected nothing it
            # should have" were indistinguishable from the artifact alone.
            # `selected_jobs` is exactly the shard's assignment domain
            # (resumed candidates included), which is what
            # `merge_mutation_shards`' own manifest proof compares.
            result_payload = _dataclass_replace(
                result_payload,
                candidate_ids=tuple(candidate_id(job) for job in selected_jobs),
            )
        return result_payload


def _execute_mutation_jobs(
    *,
    job_list: Sequence[MutantJob],
    deadline: LaneDeadline,
    jobs: int,
    prepared: SnapshotRepository,
    plan: CommandPlan,
    process_runner: ProcessRunner,
    clock: Clock,
    executor_factory: ExecutorFactory = _default_executor_factory,
    equivalence_artifact: str | None = None,
    kill_signal_artifact: str | None = None,
    baseline_equivalence: bytes | None = None,
    budget_per_candidate_seconds: float | None = None,
    write_progress: ProgressWriter | None,
    execute_plan: Callable[..., CommandResult],
    total: int,
    candidate_count: int,
    state_project_root: Path | str | None = None,
) -> Mutation:

    def _run_one(index: int) -> _MutantRun:
        job = job_list[index]
        original_bytes = job.original_text.encode("utf-8")
        replacement_bytes = job.site.apply(original_bytes)
        materialize_timeout = deadline.remaining()
        started_monotonic = time.monotonic()
        with prepared.materialize_replacement(
            path=PurePosixPath(job.path),
            expected=original_bytes,
            replacement=replacement_bytes,
            timeout=materialize_timeout,
        ) as snapshot:
            # P34/§3.6: reserved and ARMED before the command runs, exactly
            # like the coverage artifact one level up in
            # `runner._execute_snapshot_unit` -- `arm()` unlinks anything
            # pre-existing at either path BEFORE `execute_plan` starts, so a
            # later absent read can never be a stale copy leaking through
            # (the carve's own "why stale bytes cannot leak").
            equivalence_reservation = (
                _arm_artifact_reservation(
                    snapshot.project_root,
                    equivalence_artifact,
                    limit=MAX_EQUIVALENCE_ARTIFACT_BYTES,
                )
                if equivalence_artifact is not None
                else None
            )
            kill_signal_reservation = (
                _arm_artifact_reservation(
                    snapshot.project_root,
                    kill_signal_artifact,
                    limit=MAX_KILL_SIGNAL_BYTES,
                )
                if kill_signal_artifact is not None
                else None
            )
            command_deadline = deadline.remaining()
            if (
                budget_per_candidate_seconds is not None
                and budget_per_candidate_seconds < command_deadline
            ):
                command_deadline = budget_per_candidate_seconds
            result = execute_plan(
                plan,
                cwd=snapshot.project_root,
                timeout=command_deadline,
                process_runner=process_runner,
                clock=clock,
            )
            elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
            try:
                # P26/A-212: the ONE lane deadline IS forwarded here, so this
                # check's own Git children are bounded by the same budget as
                # every other lane-owned call. `remaining=None` would leave
                # them genuinely unbounded -- `git._run_bounded` then waits in
                # `selector.select(None)`/`proc.wait()` with no timeout -- so a
                # single hung `status`/`rev-parse` could outlive the entire
                # lane budget from inside a worker.
                dirt = _snapshot_left_dirt(
                    job, snapshot, remaining=deadline.remaining
                )
            except AssayError as exc:
                # ...but this check runs AFTER the mutant's own process already
                # produced a decisive result, so an expiry observed HERE must
                # not retroactively reclassify a COMPLETED identity: this
                # function's own bucket rule is "completed identities remain
                # evidence, never discarded for a partial sample". Absorbing
                # exactly that pair keeps the bucket semantics unchanged while
                # still refusing to start an unbounded child. Every other
                # AssayError -- a real Git failure, and A-195's own returned
                # DIRTY_TREE/HEAD_CHANGED below -- still stops the whole claim.
                # A NOT-YET-STARTED mutant is unaffected: it is budget-stopped
                # earlier, at `materialize_timeout`/`execute_plan`'s samples.
                if not (
                    exc.outcome is Outcome.BUDGET_EXCEEDED
                    and exc.reason_code is ReasonCode.LANE_TIMEOUT
                ):
                    raise
                dirt = None
            equivalence_bytes: bytes | None = None
            kill_signal_text: str | None = None
            decode_error: AssayError | None = None
            if dirt is None:
                if equivalence_reservation is not None:
                    equivalence_bytes = equivalence_reservation.consume()
                if kill_signal_reservation is not None:
                    try:
                        kill_signal_text = _read_kill_signal(kill_signal_reservation)
                    except AssayError as exc:
                        decode_error = exc
            if equivalence_reservation is not None:
                equivalence_reservation.close()
            if kill_signal_reservation is not None:
                kill_signal_reservation.close()
            if dirt is not None:
                raise dirt
            if decode_error is not None:
                raise decode_error
        return _MutantRun(
            result=result,
            equivalence_bytes=equivalence_bytes,
            kill_signal=kill_signal_text,
            elapsed_seconds=elapsed_seconds,
        )

    results: list[_MutantRun | None] = [None] * total
    budget_exceeded_mask = [False] * total
    fatal: AssayError | None = None

    with executor_factory(jobs) as pool:
        index = 0
        while index < total and fatal is None:
            wave = list(range(index, min(index + jobs, total)))
            futures = {pool.submit(_run_one, position): position for position in wave}
            wave_stopped = False
            for future, position in futures.items():
                try:
                    results[position] = future.result()
                except AssayError as exc:
                    # The handoff's own wording: "catch ONLY that exact
                    # BUDGET_EXCEEDED/LANE_TIMEOUT from deadline.remaining()".
                    # Reviewer repair (phase 2): matching on the OUTCOME alone
                    # also swallowed P22's `BUDGET_EXCEEDED`/
                    # `SNAPSHOT_LIMIT_EXCEEDED`, which is a policy REFUSAL, not
                    # a lane that ran out of time -- it would have been
                    # relabelled `LANE_TIMEOUT` and reported as a per-identity
                    # budget stop with the other identities still counted as
                    # evidence, instead of propagating unchanged as the
                    # payload-free R2 terminal the table reserves for a P22
                    # worker failure.
                    if (
                        exc.outcome is Outcome.BUDGET_EXCEEDED
                        and exc.reason_code is ReasonCode.LANE_TIMEOUT
                    ):
                        budget_exceeded_mask[position] = True
                        wave_stopped = True
                    elif fatal is None:
                        fatal = exc
                run = results[position]
                if run is None:
                    continue
                if equivalence_artifact is None:
                    outcome_bucket = _classify_mutant_result(run.result)
                else:
                    assert baseline_equivalence is not None
                    outcome_bucket = _classify_mutant_result_with_equivalence(
                        run.result,
                        equivalence_bytes=run.equivalence_bytes,
                        baseline_equivalence=baseline_equivalence,
                    )
                if write_progress is not None:
                    write_progress(
                        {
                            **_progress_event(
                                candidate_index=position,
                                candidate_total=total,
                                job=job_list[position],
                            ),
                            "outcome_bucket": outcome_bucket,
                            "elapsed_seconds": round(run.elapsed_seconds, 3),
                        }
                    )
                if state_project_root is not None:
                    _write_mutation_state_record(
                        Path(state_project_root),
                        {
                            **_progress_event(
                                candidate_index=position,
                                candidate_total=total,
                                job=job_list[position],
                            ),
                                "schema_version": MUTATION_STATE_SCHEMA_VERSION,
                                "source_sha256": hashlib.sha256(
                                    job_list[position].original_text.encode("utf-8")
                                ).hexdigest(),
                                "replacement_sha256": job_list[
                                    position
                                ].site.replacement_sha256,
                                "lineno": job_list[position].site.lineno,
                                "description": job_list[position].site.description,
                                "outcome_bucket": outcome_bucket,
                            },
                        )
            index = wave[-1] + 1
            if fatal is not None or wave_stopped:
                for leftover in range(index, total):
                    budget_exceeded_mask[leftover] = True
                break

    # A-195: a mutant that left Git-visible dirt/HEAD drift is never folded
    # into `crashed` -- the whole R2 claim becomes the unchanged payload-free
    # pair, exactly like a P22 structural failure elsewhere in the lane.
    if fatal is not None:
        raise fatal

    buckets: dict[str, list[MutantOutcome]] = {
        "killed": [],
        "survived": [],
        "crashed": [],
        "budget_exceeded": [],
        "equivalent": [],
    }
    for position, job in enumerate(job_list):
        # Results are consumed POSITION-ALIGNED with the submitted job list,
        # and `collect_mutation_sites` guarantees that list is
        # identity-ordered already (per file by `_validate_sites`, across
        # files by path order, and `MutantOutcome.identity` leads with
        # `path`) -- appending in that order leaves every bucket
        # identity-ordered without a second sort.
        if budget_exceeded_mask[position]:
            buckets["budget_exceeded"].append(_outcome_of(job))
            continue
        run = results[position]
        assert run is not None
        if equivalence_artifact is None:
            # O8's own inertness: the UNDECLARED lane takes the EXISTING
            # path, unchanged -- never a new path that happens to agree.
            buckets[_classify_mutant_result(run.result)].append(_outcome_of(job))
            continue
        assert baseline_equivalence is not None  # refused above otherwise
        bucket = _classify_mutant_result_with_equivalence(
            run.result,
            equivalence_bytes=run.equivalence_bytes,
            baseline_equivalence=baseline_equivalence,
        )
        kill_signal = run.kill_signal if bucket == "killed" else None
        if bucket == "killed" and kill_signal_artifact is not None and kill_signal is None:
            # §3.6's own kill-signal rule: `kill_attribution` derives to
            # `declared` from `kill_signal_artifact`'s own presence, and the
            # model then requires a signal on EVERY killed entry -- so a
            # mutant that would land here with no signal file did not meet
            # the lane's own declared contract, and is `crashed` instead.
            bucket = "crashed"
        buckets[bucket].append(_outcome_of(job, kill_signal=kill_signal))

    return Mutation(
        candidate_count=candidate_count,
        total=total,
        killed=tuple(buckets["killed"]),
        survived=tuple(buckets["survived"]),
        crashed=tuple(buckets["crashed"]),
        budget_exceeded=tuple(buckets["budget_exceeded"]),
        equivalent=tuple(buckets["equivalent"]),
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
    non-empty ``survived`` -> ``FAIL``/``MUTANTS_SURVIVED``; else
    ``killed + survived == 0`` with a non-empty ``equivalent`` ->
    ``INCONCLUSIVE``/``ALL_MUTANTS_EQUIVALENT``; else ``PASS``.
    This precedence (crashed > budget_exceeded > survived) matches the
    existing cross-claim ``ROLLUP_PRECEDENCE`` applied one level down.

    **P33/A-223d: the all-inert terminal, ranked after ``survived``.**
    Without it, ``killed 0, survived 0, equivalent 3`` walks straight to
    ``PASS`` — A-026/A-035's 0/0-is-100% bug one layer down, reintroduced
    inside the change that exists to fix a lossiness problem. Every mutant
    was proven to change nothing, so nothing the suite could have caught was
    ever at risk and the run says nothing about the tests. Ranking is
    order-insensitive given the ``killed + survived == 0`` guard (a non-empty
    ``survived`` has already returned by then); it sits here for readability,
    not because the position is load-bearing.
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
    if not mutation.killed and mutation.equivalent:
        # A-223d. `killed + survived == 0` and something was proven inert:
        # the run attempted real mutants and none of them could ever have
        # been caught, so there is no evidence about the tests here to pass
        # on. `survived` is already known empty on this branch.
        return Outcome.INCONCLUSIVE, ReasonCode.ALL_MUTANTS_EQUIVALENT
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
