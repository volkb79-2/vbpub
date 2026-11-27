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
  over :func:`collect_mutation_sites`'s job list, then deterministic
  aggregation into a :class:`~assay.verdict.Mutation`; native R2 currently
  requires one worker so cgroup-event windows cannot overlap),
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

**B566 boundary**: the persisted mutation state lives in
``mutation_store.py``. The import direction is one way: this runner imports
the store; the store's references back here are annotation-only.

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

import collections
import fnmatch
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import threading
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager, nullcontext
from dataclasses import replace as _dataclass_replace
from datetime import datetime
from functools import partial, wraps
from pathlib import Path, PurePosixPath
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    Sequence,
)

from . import _durable
from . import _judge_projection
from . import _limit_ledger
from . import _schedule
from . import git, liveness, observer_windows, safeio
from . import mutation_store
from .candidate_identity import candidate_id_from_fields
from .diff import AddedLines
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode
from .guards import is_finite_positive, is_int_at_least, is_strict_int
from .isolation import SnapshotRepository, netstring
from .mutation_parsers import MutationFormatSpec
from .mutation_parsers.model import IngestedMutationReport
from .mutation_store import (
    MUTATION_STATE_SCHEMA_VERSION,
    RecordContext,
    Clock,
    FlexOptions,
    InvalidCandidateSelectionError,
    InvalidRejudgeIdError,
    JUDGE_GENERATIONS_DIR,
    MutationStateError,
    SHARD_MANIFEST_NAME,
    ShardStoreMergeError,
    _MutantRun,
    _locus_fields_for_job,
    _reject_unknown_rejudge_ids,
    _tool_version,
    _write_shard_manifest,
    candidate_id,
    candidate_identity_fields,
    default_state_root,
    execution_from_state_record,
    locus_index_for_records,
    merge_mutation_shards,
    merge_mutations,
    mutation_state_record_name,
    mutation_state_record_path,
    plan_sha256,
    record_effective_judge,
    record_provenance,
    select_mutation_shard,
    terminal_record_judges,
    valid_hung_resource_evidence,
)

from .r2_command import transform_argv
from .mutation_witness import (
    ReceiptCapture,
    ReceiptFacts,
    cold_witness_from_receipt,
    declared_failure_proof_ok,
    receipt_facts,
    supports_sequential_pytest,
    survivor_proof_ok,
)
from .mutation_witness import inject_witness_plugin as _inject_witness_plugin
from .mutation_witness import make_attempt_plan as _make_witness_attempt_plan
from .mutation_witness import (
    replay_witness_from_receipt as _replay_witness_from_receipt,
)
from .mutation_witness import witness_from_receipt as _witness_from_receipt
from .process_tree import (
    ProcessTreeError,
    ProcessTreeEvidence,
    ProcessTreeProcessRunner,
)
from .records import record
from .redaction import redact_passthrough_text as _redact_passthrough_text
from .resource_limits import (
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
    read_candidate_resource_counters,
)
from .verdict import (
    CANDIDATE_OUTCOME_ERROR_MAX_BYTES,
    MAX_CANDIDATE_CEILING,
    MAX_SHARD_COUNT,
    MUTATION_BUCKETS,
    PRODUCER_VERSION_UNOBSERVED,
    Claim,
    CandidateOutcomeError,
    MutantEvidence,
    MutantOutcome,
    Mutation,
    MutationExecution,
    MutationProducerTool,
    MutationWitnessReceipt,
    UnderlyingOutcome,
    SourcePosition,
    iso_utc,
)
from .vocabulary import (
    CARGO_MUTANTS_OPERATOR_NAMESPACE,
    CARGO_MUTANTS_REPORT_SCHEMA,
    MUTATION_OPERATORS,
)


class _UnclassifiedLivenessWallStop(AssayError):
    """A candidate wall backstop ends the sweep without a stored outcome."""


if TYPE_CHECKING:  # pragma: no cover -- annotation-only imports; importing at runtime creates cycles
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
    "InvalidRejudgeIdError",
    "InvalidCandidateSelectionError",
    "CampaignPlanMismatchError",
    "MutationSite",
    "MutationTarget",
    "PROGRESS_EVENTS",
    "ProgressStream",
    "ProgressWriter",
    "MutationStateError",
    "build_mutation_claim",
    "byte_offset",
    "collect_mutation_sites",
    "candidate_id",
    "candidate_identity_fields",
    "plan_sha256",
    # (B088) The judge half of a resume record's identity -- public for the
    # same reason `candidate_id` is: a consumer inspecting a state directory
    # must be able to re-derive what it is looking at.
    "judge_sha256",
    "judge_mutation",
    "line_for_offset",
    "merge_mutations",
    "merge_mutation_shards",
    # (Round-1 N6) Both are cross-module API -- `runner` composes the default
    # store from `default_state_root`, and `mutation_state_record_name` is
    # the identity-to-filename rule `--state-dir` addresses records by.
    "default_state_root",
    # (B527/B530/B531/B561) flexible campaigns: generations, carry-over,
    # carry-forward, and targeted sets.
    "FlexOptions",
    "JUDGE_GENERATIONS_DIR",
    "SHARD_MANIFEST_NAME",
    "ShardStoreMergeError",
    "record_effective_judge",
    "record_provenance",
    "locus_index_for_records",
    "terminal_record_judges",
    "mutation_state_record_name",
    "mutation_state_record_path",
    "resolve_mutation_targets",
    "run_mutation",
    "select_mutation_shard",
    # CD18: public for assay_analysis.
    "execution_from_state_record",
    "valid_hung_resource_evidence",
]

#: (P21/A-183) the adapter-wide capability sentinel, retained from the old
#: ``generate_mutants`` union. It means the adapter has NO mutation
#: implementation at all -- never a parse failure, never an unrecognised
#: individual construct, never a valid analysis that found nothing.
UNSUPPORTED = "UNSUPPORTED"

#: (B088) The serialization label folded into :func:`judge_sha256`, so a
#: future change to WHAT the judge identity covers yields visibly different
#: digests instead of silently comparable ones.
#: Advanced to /14 by B206/B208 (assay 12, verdict v18): a state record now
#: carries its worker count (B203), and a killed record its first failing test
#: (B205). Advanced to /15 by B251: completed-leak state and candidate-local
#: outcome recovery have new durable semantics, so /14 records are re-judged.
_JUDGE_DIGEST_LABEL = "assay-judge-identity/15"

#: The public name of the label above, for consumers that state which judge identity a result was produced under
#: (`assay analyze export`). One value, so the exported identity can never drift from the digest's own label.
JUDGE_IDENTITY_LABEL = _JUDGE_DIGEST_LABEL

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


class R2CommandProofError(AssayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, outcome=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG)


class R2ManifestWriteError(AssayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, outcome=Outcome.ERROR, reason_code=ReasonCode.OUTPUT_WRITE_FAILED)


class R2BaselineTimeoutError(AssayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, outcome=Outcome.BUDGET_EXCEEDED, reason_code=ReasonCode.LANE_TIMEOUT)


class CampaignPlanMismatchError(AssayError):
    """The current full candidate plan differs from a persisted campaign."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )


@record
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
            if not is_strict_int(value):
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


@record
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
            if not is_int_at_least(line, 1):
                raise ValueError(
                    f"MutationTarget.lines must contain only positive line "
                    f"numbers, got {line!r}"
                )


def resolve_mutation_targets(
    added: AddedLines,
    *,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    source_root_files: Sequence[str] = (),
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
    root (directory containment or exact lexical file path), not inside one
    of the adapter's own excluded directories, and
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
        in_directory = any(
            not root.is_file() and abs_path.is_relative_to(root)
            for root in source_root_paths
        )
        lexical_path = (repo_top / path).absolute()
        in_file = path in source_root_files or (
            not source_root_files
            and any(
                root.is_file() and lexical_path == root
                for root in source_root_paths
            )
        )
        if not (in_directory or in_file):
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


@record
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
    context_sources: Iterable[str | tuple[str, str]] | None = None,
    deadline_check: Callable[[], float] | None = None,
) -> tuple[MutantJob, ...] | Literal["UNSUPPORTED"]:
    """Aggregate bounded candidate sites across possibly many changed files
    into ONE deterministic job list, or report adapter-wide capability
    absence (P21/A-180/A-183).

    Targets are visited in ``path`` order, and each per-file discovery call
    receives only the REMAINING capacity — so the total number of candidate
    descriptors this function ever holds is bounded by *limit* regardless of
    how many candidates the files actually contain. An adapter with
    cross-file rules may do one metadata pre-scan over *context_sources*;
    when omitted, selected target texts remain the context. That scan may
    not materialize candidate descriptors. Once capacity is exhausted, later files are not
    sent to per-file site discovery, which keeps candidate generation bounded
    rather than merely truncating the report.

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
    if not is_strict_int(limit):
        raise ValueError(f"collect_mutation_sites limit must be an integer, got {limit!r}")
    if not 1 <= limit <= MAX_CANDIDATE_CEILING:
        raise ValueError(
            f"collect_mutation_sites limit must be in 1..{MAX_CANDIDATE_CEILING}, "
            f"got {limit}"
        )
    _check_operator_policy(operators)

    ordered_targets = sorted(targets, key=lambda item: item.path)
    if not ordered_targets:
        if deadline_check is not None:
            deadline_check()
        return ()
    source_context_preparer = getattr(
        adapter, "prepare_mutation_context_with_sources", None
    )
    source_contextual_generator = getattr(
        adapter, "generate_mutation_sites_with_context_for_source", None
    )
    bounded_source_contextual_generator = getattr(
        adapter, "generate_mutation_sites_with_context_for_source_bounded", None
    )
    if callable(source_context_preparer) and callable(source_contextual_generator):
        target_source_identities = frozenset(
            target.path for target in ordered_targets
        )

        def identified_sources() -> Iterator[tuple[str, str]]:
            # Keep only compact digests while de-duplicating. Retaining every
            # context string here kept the full source tree alive while SQL
            # also built encoded and masked copies for its cross-file scan.
            seen: set[tuple[str, bytes]] = set()

            def emit(identity: str, text: str) -> Iterator[tuple[str, str]]:
                digest = hashlib.sha256(text.encode("utf-8")).digest()
                key = (identity, digest)
                if key not in seen:
                    seen.add(key)
                    yield identity, text

            for index, item in enumerate(context_sources or ()):
                if deadline_check is not None:
                    deadline_check()
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[0], str)
                    and isinstance(item[1], str)
                ):
                    identity, text = item
                    if identity in target_source_identities:
                        # The selected target carries the current candidate
                        # text. A context snapshot for the same path can be
                        # stale, so use the target version below.
                        continue
                elif isinstance(item, str):
                    # Legacy context callers keep each external source
                    # distinct even when the contents happen to be equal.
                    identity, text = f"external-context:{index}", item
                else:
                    raise TypeError(
                        "mutation context sources must be text or (path, text)"
                    )
                yield from emit(identity, text)

            for target in ordered_targets:
                if deadline_check is not None:
                    deadline_check()
                yield from emit(target.path, target.text)

        # This is a one-pass stream: the adapter consumes each context source
        # before the next full source string is retained by this function.
        identified_source_stream = identified_sources()
        bounded_target_context_preparer = getattr(
            adapter,
            "prepare_mutation_context_with_sources_for_targets_bounded",
            None,
        )
        target_context_preparer = getattr(
            adapter, "prepare_mutation_context_with_sources_for_targets", None
        )
        bounded_source_context_preparer = getattr(
            adapter, "prepare_mutation_context_with_sources_bounded", None
        )
        if (
            callable(bounded_target_context_preparer)
            and deadline_check is not None
        ):
            mutation_context = bounded_target_context_preparer(
                identified_source_stream,
                remaining=deadline_check,
                target_source_identities=target_source_identities,
            )
        elif callable(bounded_source_context_preparer) and deadline_check is not None:
            mutation_context = bounded_source_context_preparer(
                identified_source_stream, remaining=deadline_check
            )
        else:
            if deadline_check is not None:
                deadline_check()
            if callable(target_context_preparer):
                mutation_context = target_context_preparer(
                    identified_source_stream,
                    target_source_identities=target_source_identities,
                )
            else:
                mutation_context = source_context_preparer(identified_source_stream)
            if deadline_check is not None:
                deadline_check()
    else:
        context_preparer = getattr(adapter, "prepare_mutation_context", None)

        def context_texts() -> Iterator[str]:
            for item in context_sources or ():
                if deadline_check is not None:
                    deadline_check()
                yield item[1] if isinstance(item, tuple) else item
            yield from (target.text for target in ordered_targets)

        if callable(context_preparer):
            if deadline_check is not None:
                deadline_check()
            mutation_context = context_preparer(context_texts())
            if deadline_check is not None:
                deadline_check()
        else:
            mutation_context = None
    contextual_generator = getattr(adapter, "generate_mutation_sites_with_context", None)

    jobs: list[MutantJob] = []
    remaining = limit
    saw_supported = False
    for target in ordered_targets:
        if deadline_check is not None:
            deadline_check()
        if remaining <= 0:
            break
        if (
            mutation_context is not None
            and callable(bounded_source_contextual_generator)
            and deadline_check is not None
        ):
            result = bounded_source_contextual_generator(
                target.text,
                set(target.lines),
                operators=operators,
                limit=remaining,
                context=mutation_context,
                source_identity=target.path,
                remaining=deadline_check,
            )
        elif (
            mutation_context is not None
            and callable(source_contextual_generator)
            and callable(source_context_preparer)
        ):
            result = source_contextual_generator(
                target.text,
                set(target.lines),
                operators=operators,
                limit=remaining,
                context=mutation_context,
                source_identity=target.path,
            )
        elif mutation_context is not None and callable(contextual_generator):
            result = contextual_generator(
                target.text,
                set(target.lines),
                operators=operators,
                limit=remaining,
                context=mutation_context,
            )
        else:
            result = adapter.generate_mutation_sites(
                target.text,
                set(target.lines),
                operators=operators,
                limit=remaining,
            )
        if deadline_check is not None:
            deadline_check()
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
    if deadline_check is not None:
        deadline_check()
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


class _CandidateEventBuffer:
    """Emit candidate progress in index order despite concurrent completion."""

    def __init__(self, total: int) -> None:
        self._resolved = [False] * total
        self._events: dict[int, Mapping[str, Any]] = {}
        self._cursor = 0

    def stage(self, position: int, event: Mapping[str, Any]) -> None:
        if self._resolved[position] or position in self._events:
            raise ValueError(f"candidate position {position} was already resolved")
        self._events[position] = event
        self._resolved[position] = True

    def resolve_without_event(self, position: int) -> None:
        if self._resolved[position] or position in self._events:
            raise ValueError(f"candidate position {position} was already resolved")
        self._resolved[position] = True

    def drain_contiguous(self) -> list[Mapping[str, Any]]:
        ready: list[Mapping[str, Any]] = []
        while self._cursor < len(self._resolved) and self._resolved[self._cursor]:
            event = self._events.pop(self._cursor, None)
            if event is not None:
                ready.append(event)
            self._cursor += 1
        return ready

    def drain_all_ascending(self) -> list[Mapping[str, Any]]:
        ready = [self._events[position] for position in sorted(self._events)]
        self._events.clear()
        return ready


@contextmanager
def progress_writer(
    path: Path,
    *,
    parent_guard: Callable[[int], None] | None = None,
    open_guard: Callable[[int, int], None] | None = None,
) -> Iterator[ProgressWriter]:
    """Append one compact JSON object per line and flush every record.

    Round-2 review (blocker 2): a bad destination -- an existing directory,
    or an empty ``--progress ""`` (which resolves to ``.``, the CWD, itself
    a directory) -- used to raise a bare ``IsADirectoryError``/``OSError``
    here. That escaped uncaught past this function, up through
    :func:`run_mutation`, and got caught by ``runner.run_lane``'s broad
    ``except OSError:`` far up the call stack, which relabels ANY escaped
    OSError as ``ERROR``/``GIT_FAILED`` -- a cause that has nothing to do
    with what actually happened. That is exactly the mislabelled-cause
    class B032 was filed to close, reopened here on the new ``--progress``
    flag: A-320 claims ``--progress`` behaves "exactly like
    ``--verdict-json``'s" destination handling, but ``--verdict-json`` gives
    an honest named refusal (``ERROR``/``OUTPUT_WRITE_FAILED``) for the
    identical mistake and ``--progress`` did not. Both now raise the same
    typed :class:`AssayError`, naming the path.
    """
    expanded = Path(
        os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path))))
    )
    try:
        expanded.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AssayError(
            f"cannot create the parent directory of the progress "
            f"destination {str(expanded)!r}: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.OUTPUT_WRITE_FAILED,
        ) from exc
    parent_fd: int | None = None
    descriptor: int | None = None
    try:
        parent_fd = os.open(
            expanded.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        if parent_guard is not None:
            parent_guard(parent_fd)
        descriptor = os.open(
            expanded.name,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
            0o666,
            dir_fd=parent_fd,
        )
        if open_guard is not None:
            open_guard(parent_fd, descriptor)
        stream = os.fdopen(descriptor, "a", encoding="utf-8")
        descriptor = None
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)
        raise AssayError(
            f"cannot open the progress destination {str(path)!r} for "
            f"appending: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.OUTPUT_WRITE_FAILED,
        ) from exc
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)
        raise
    profile_name = _profile_sibling_name(expanded.name)
    profile_stream: Any = None
    try:
        with stream:

            def write(event: Mapping[str, Any]) -> None:
                nonlocal profile_stream
                try:
                    stream.write(
                        json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
                    )
                    stream.flush()
                    profile = (
                        event.get("profile") if event.get("event") == "candidate" else None
                    )
                    if isinstance(profile, Mapping):
                        # B206: one `resource-profile/1` line per candidate,
                        # next to the progress stream, opened on the first one
                        # so a lane without R2 candidates creates no file.
                        if profile_stream is None:
                            profile_descriptor = os.open(
                                profile_name,
                                os.O_WRONLY
                                | os.O_APPEND
                                | os.O_CREAT
                                | os.O_CLOEXEC
                                | os.O_NOFOLLOW,
                                0o666,
                                dir_fd=parent_fd,
                            )
                            profile_stream = os.fdopen(
                                profile_descriptor, "a", encoding="utf-8"
                            )
                        profile_stream.write(
                            json.dumps(profile, separators=(",", ":"), sort_keys=True)
                            + "\n"
                        )
                        profile_stream.flush()
                except OSError as exc:
                    raise AssayError(
                        f"cannot write to the progress destination "
                        f"{str(path)!r}: {exc}",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.OUTPUT_WRITE_FAILED,
                    ) from exc

            try:
                yield write
            finally:
                if profile_stream is not None:
                    profile_stream.close()
    except OSError as exc:
        # `stream`'s own `__exit__` (flush + close) can still raise, e.g. a
        # filesystem that only surfaces ENOSPC on close.
        raise AssayError(
            f"cannot close the progress destination {str(path)!r}: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.OUTPUT_WRITE_FAILED,
        ) from exc
    finally:
        _close_if_open(parent_fd)


def _profile_sibling_name(progress_name: str) -> str:
    """The per-candidate profile file next to a progress file:
    ``progress-<lane>.jsonl`` becomes ``profile-<lane>.jsonl``; any other name
    gets ``.profile`` before its extension."""
    if progress_name.startswith("progress-"):
        return "profile-" + progress_name[len("progress-"):]
    stem, dot, extension = progress_name.rpartition(".")
    if not dot:
        return progress_name + ".profile.jsonl"
    return f"{stem}.profile.{extension}"


def _close_if_open(descriptor: int | None) -> None:
    """Close *descriptor* when one was opened (a failed open leaves ``None``)."""
    if descriptor is not None:
        os.close(descriptor)


#: (B064) The CLOSED progress vocabulary. Every record the stream carries
#: names its own phase through this table, and
#: :meth:`ProgressStream.emit` refuses any other name outright -- "the phase
#: vocabulary is CLOSED and identical across tiers" is B064's own acceptance
#: criterion, and a table nothing enforces is a comment, not a vocabulary.
#:
#: The R0/R1 phase stream and the R2 mutation sweep draw from ONE table: an
#: R0/R1 lane simply emits far fewer of these names, because it has no
#: per-unit to iterate. A phase that did not happen is never emitted -- most
#: pointedly, the DIRECT R0-only path (``runner.run_lane``'s live-tree
#: branch) has no snapshot at all and therefore never emits
#: ``snapshot_materialized``. Naming a phase that did not occur would be a
#: lie told to the one reader the artifact exists for.
PROGRESS_EVENTS = frozenset(
    {
        # --- shared header/terminal ---------------------------------------
        "run",  # first record of a run; attributes every later record
        "verdict_written",  # R0/R1 terminal (B064)
        "end",  # mutation-sweep terminal, with bucket counts (B065)
        # --- R0/R1 phase boundaries (B064) --------------------------------
        "snapshot_materialized",
        "command_started",
        "command_running",  # the time-based heartbeat tick
        "command_finished",
        "coverage_parsed",
        # --- mutation sweep (B031/A-320, pre-existing) --------------------
        "candidates",
        "shard",
        "resume",
        "baseline",
        "candidate",
        "candidate_retry",
        "resume_merged",
        # --- flexible campaigns (B530/B531) -------------------------------
        "carry_over",  # records reused across a judge change, with provenance
        "carry_forward",  # prior-judge lookup by id, locus or kill window
        "selection",  # --only/--exclude-candidates: what runs, what is left out
        "shard_merge",  # --merge-shard-store: the strict per-shard proof (RG-265 review B1)
        "merge_manifest",  # --merge-manifest: owner proof and materialization (B544)
        # --- mutation sweep, judged by progress not by time (B091/D-23) ---
        "plan",  # the sweep's own first record: what the baseline measured
        # --- per-test liveness detail, BASELINE only (B091/D-23, P7 A4) ---
        "test",  # one baseline test's own outcome, forwarded verbatim
    }
)


class ProgressStream:
    """(B064/B065) The ONE place a progress record is enriched and written.

    Wraps :func:`progress_writer`'s raw callable so that ``emitted_at`` and
    ``elapsed_s`` are added centrally, to EVERY record, instead of at each
    of the call sites scattered across :mod:`assay.cli`,
    :mod:`assay.runner` and this module. That is what makes B065 nearly free
    once B064's sites exist, and it is what makes ``elapsed_s`` mean one
    thing everywhere: **monotonic seconds since this stream was opened**,
    which is the ``run`` header's own instant.

    The heartbeat (B064) writes from a background thread while the lane's
    command runs, and the mutation sweep writes from N concurrent worker
    threads, so the write itself is serialised under a lock. The lock covers
    only the ``write`` call; the enrichment is computed outside it.

    *clock* is the same UTC clock the rest of the run uses, so ``emitted_at``
    is in this project's existing ``iso_utc(clock())`` convention rather
    than a second, independently-skewed time source.

    This is diagnostic output. ``assay verify`` never reads it, no verdict
    field derives from it, and nothing here is evidence.
    """

    def __init__(
        self,
        write: ProgressWriter,
        *,
        clock: Clock,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._write = write
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._started_monotonic = monotonic()
        self._header_emitted = False

    @property
    def started_monotonic(self) -> float:
        """The monotonic instant ``elapsed_s`` is measured from."""
        return self._started_monotonic

    def elapsed(self) -> float:
        return self._monotonic() - self._started_monotonic

    def emit(self, event: Mapping[str, Any]) -> None:
        name = event.get("event")
        if name not in PROGRESS_EVENTS:
            # A programming error, never a consumer's: the vocabulary is
            # closed and every producer is in this repository.
            raise ValueError(
                f"progress event {name!r} is not in the closed progress "
                f"vocabulary {sorted(PROGRESS_EVENTS)}"
            )
        # (Round-1 N1) The two timestamps are computed INSIDE the lock, not
        # before it. Outside it, the heartbeat thread and a `jobs`-way
        # concurrent sweep could stamp in one order and win the lock in the
        # other, so a reader tailing the file would see `elapsed_s` go
        # backwards between consecutive lines -- and run-gate's own
        # `_rate_per_min` reads that field off the newest candidate record.
        # Under the lock, stamp order and write order are the same order by
        # construction. The cost is two clock reads inside a critical
        # section that already does a `write` and a `flush`.
        with self._lock:
            self._write(
                {
                    **event,
                    "emitted_at": iso_utc(self._clock()),
                    "elapsed_s": round(self.elapsed(), 3),
                }
            )

    #: A :class:`ProgressStream` IS a :data:`ProgressWriter`, so every
    #: pre-existing ``write_progress(...)`` call site keeps working and is
    #: enriched by construction rather than by remembering to be.
    __call__ = emit

    def emit_run_header(
        self,
        *,
        commit: str | None,
        lane: str | None = None,
        rigor: Sequence[str] | None = None,
        budget_s: float | None = None,
        budget_per_candidate_s: float | None = None,
        candidate_total: int | None = None,
        jobs: int | None = None,
        attribution: Literal["serial", "overlap-conservative"] | None = None,
    ) -> None:
        """Emit the stream's ``run`` header, at most once.

        The header is the FIRST record of a run, because the file is opened
        for APPEND and never truncated: without it a tailing monitor cannot
        attribute a later record to a run at all (B031/A-320's original
        reason). That ordering is also why ``candidate_total`` is ``null``
        on every lane-driven run -- the total cannot be known before a
        snapshot exists and the sites have been collected, and a header
        deferred until it IS known is a header that arrives after the
        records it is supposed to attribute. The real total arrives, the
        moment it is known, on the ``candidates`` event (B065), and rides on
        ``baseline`` and every ``candidate`` record as it always has.

        ``budget_s`` is ``null`` for a lane declaring ``budget =
        "unbounded"`` (B067). ``lane`` is ``null`` only when the header was
        emitted by a direct :func:`run_mutation` library call, which has no
        :class:`~assay.config.Lane` in view; a ``null`` ``lane`` is
        therefore the reader's signal that the lane-level bounds in this
        header are UNKNOWN rather than unbounded.
        """
        if self._header_emitted:
            return
        if jobs is not None and (type(jobs) is not int or jobs < 1):
            raise ValueError("progress run header jobs must be a positive integer")
        if attribution is not None and attribution not in {
            "serial",
            "overlap-conservative",
        }:
            raise ValueError("progress run header attribution is invalid")
        if (jobs is None) != (attribution is None):
            raise ValueError(
                "progress run header jobs and attribution must be supplied together"
            )
        if jobs is not None and attribution != (
            "serial" if jobs == 1 else "overlap-conservative"
        ):
            raise ValueError(
                "progress run header attribution must match the worker count"
            )
        self._header_emitted = True
        self.emit(
            {
                "event": "run",
                "lane": lane,
                "commit": commit,
                "rigor": list(rigor) if rigor is not None else None,
                "budget_s": budget_s,
                "budget_per_candidate_s": budget_per_candidate_s,
                "candidate_total": candidate_total,
                **({"jobs": jobs, "attribution": attribution} if jobs is not None else {}),
                "started": iso_utc(self._clock()),
            }
        )


def _progress_event(
    *,
    candidate_index: int,
    candidate_total: int,
    job: MutantJob,
    locus_fields: Mapping[str, object] | None = None,
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
        **(
            _locus_fields_for_job(job)
            if locus_fields is None
            else locus_fields
        ),
    }

#: (WP1) The candidate-only command transform behind
#: ``judge.mutation.candidate_fail_fast``. Folded into the judge identity.
CANDIDATE_FAIL_FAST_ID: Final = "assay-candidate-fail-fast/1"
CANDIDATE_FAIL_FAST_APPENDED: Final[tuple[str, ...]] = ("-x", "-p", "no:pytest_cov")


def candidate_fail_fast_plan(plan: "CommandPlan") -> "CommandPlan":
    """(WP1) Return *plan* as a CANDIDATE runs it under ``candidate_fail_fast``.

    The recognized pytest-cov options are removed (the shared
    :func:`assay.r2_command.transform_argv`, which REFUSES any coverage option
    it cannot remove without guessing) and ``-x -p no:pytest_cov`` is appended,
    so a mutant stops at its first failing test and pays no coverage cost. The
    baseline never goes through this function.
    """
    declared = transform_argv(plan.argv_declared)
    appended = (
        *transform_argv(plan.argv_appended),
        *CANDIDATE_FAIL_FAST_APPENDED,
    )
    # Assay's own tokens are not the lane's CLI-appended argv, so they must not
    # trip the `allow_argv_append` consent check (`cli_argv_appended` scopes it).
    cli_only = (
        plan.argv_appended if plan.cli_argv_appended is None else plan.cli_argv_appended
    )
    return _dataclass_replace(
        plan,
        argv_declared=declared,
        argv_appended=appended,
        argv_effective=declared + appended,
        cli_argv_appended=cli_only,
    )


def candidate_fail_fast_preflight_plan(
    plan: "CommandPlan",
    *,
    plugin_dir: Path,
    liveness_plugin_path: Path | None = None,
) -> "CommandPlan":
    """(WP1) The EXACT command a candidate runs, for the unmutated preflight.

    Mirrors ``_run_one``: the witness plugin is injected on the declared plan
    when the command supports it, the fail-fast transform is applied, and the
    witness attempt environment is added (the receipt goes to a file in
    *plugin_dir* instead of a descriptor).
    """
    injection = _inject_witness_plugin(
        plan, plugin_dir=plugin_dir, liveness_plugin_path=liveness_plugin_path
    )
    candidate = candidate_fail_fast_plan(injection.plan)
    if not injection.active:
        return candidate
    return _make_witness_attempt_plan(
        candidate,
        receipt_path=plugin_dir / "preflight-receipt.json",
        target_node_id=None,
    )


def judge_sha256(
    *,
    tree_sha256: str,
    plan: "CommandPlan",
    link_paths: Sequence[str] = (),
    tool_version: str | None = None,
    cold_witness_kills: bool = False,
    r2_transform: str | None = None,
    r2_collection_sha256: str | None = None,
    r2_hook_fingerprint_sha256: str | None = None,
    runtime_fingerprint_sha256: str | None = None,
    coverage_hook_fingerprint_sha256: str | None = None,
    coverage_runtime_fingerprint_sha256: str | None = None,
    equivalence_ledger_sha256: str | None = None,
    candidate_fail_fast: bool = False,
    _component_digests: dict[str, Any] | None = None,
) -> str:
    """(B088) Return the identity of what JUDGES a mutant.

    :func:`candidate_id` answers "is this the same mutation" -- path, source
    bytes, span, replacement, operator. That is the right question for
    skipping re-GENERATION work, and B066 relies on it to make one shared
    ``--state-dir`` safe across worktrees. It is the wrong question for
    skipping re-EXECUTION, and conflating the two is B088: a mutant's source
    bytes are the same bytes whether the suite that judges them just gained a
    new assertion or not, so ``--resume`` replayed the prior ``survived``
    verdict for a mutant a real, test-only fix had just killed -- precisely
    the "looks tested, isn't" gap mutation testing exists to close, and the
    tooling manufactured it.

    This is the missing half of the identity. It folds:

    * *tree_sha256* -- the content digest of the whole judged tree
      (:func:`assay.isolation._manifest_sha256`), which is where a changed
      test file, a changed ``conftest.py``, a changed fixture or a changed
      non-mutated source module all show up. Content-addressed via Git object
      ids, so a touched-but-unchanged file does NOT invalidate anything;
    * ``plan.argv_effective`` -- the command as it will actually run,
      including any appended argv. A lane that narrows its selection (``-k``,
      a different test-file list) judges differently with the same tree;
    * ``plan.env_declared`` -- the lane's OWN ``env`` table, by name AND
      value. It is committed configuration, so a verdict produced under a
      different declared ``PYTHONPATH`` really is evidence about a different
      judge, and the value is reproducible across invocations;
    * the NAMES of everything else in ``plan.env_effective`` -- the
      ``env_passthrough`` names that were actually present, and any B013
      infrastructure fact injected at plan resolution. For a present
      ``env_passthrough`` name, its SHA-256 value fingerprint is folded in
      too. B142 makes those values explicit verifier evidence, and mutation
      resume must not combine candidate outcomes judged under one DSN or
      credential with a verdict describing another. The fingerprint keeps
      the value itself out of state identity material, while retaining the
      same low-entropy guessing limit documented for verdict fingerprints.
      Changed per-worktree paths and interactive ``TERM`` values therefore
      cause a safe cache miss. For ``PYTHONPATH``, assay fingerprints the
      value captured before its per-run liveness and cold-witness plugin
      paths are prepended; those injected paths remain in the child
      environment but do not change identity. The consumer's own
      ``PYTHONPATH`` value remains part of the fingerprint. Infrastructure
      values remain name-only under B088's existing rule;
    * ``plan.cwd_declared`` and ``plan.project_prefix`` -- WHERE it runs,
      which decides what relative paths in argv even resolve to;
    * *link_paths* -- the lane's declared ``[isolation] link_paths``
      (B041(b)). These name directories linked into the snapshot from the
      invoking checkout, so declaring, dropping or re-pointing one changes
      what judges the mutant just as surely as a changed test file does;
    * *tool_version* -- assay's own version. The code that CLASSIFIES a
      result is as much a part of the judge as the suite is, and since B088
      deliberately does not bump ``MUTATION_STATE_SCHEMA_VERSION`` there
      would otherwise be NO lever that invalidates records across an assay
      upgrade that changed ``_bucket_for_run`` or bucket semantics
      (round-1 review finding 6). Consumers pin a released zipapp, so this
      moves on upgrade and not otherwise.

    Three known residuals, named rather than left implicit:

    * the CONTENT of a linked directory is outside this identity -- it is
      untracked by construction (a `node_modules` closure, a mounted cache),
      so there is no commit-addressed digest of it to fold in. This does not
      widen an existing guarantee: CONSUMERS.md already states that a lane
      declaring ``link_paths`` is only as reproducible as the linked
      directory;
    * the VALUES of B013 infrastructure names remain outside this identity
      under B088's existing rule. A lane whose judgment depends on one of
      those facts still needs a future explicit identity policy;
    * the per-candidate budget is not folded in. Raising a budget and
      resuming still replays a stale ``budget_exceeded``; that is a real,
      separate defect of the same family, noted as a residual on B088 rather
      than silently widened into this change.

    Injective unconditionally: every variable-length field is
    netstring-encoded (:func:`assay.isolation.netstring`) and every section
    carries its own count, so nothing a caller can put in an argv element, an
    environment value or a path can impersonate a separator or a neighbouring
    field. Pairs and lists are sorted, so a mapping's iteration order never
    reaches the digest.
    """
    # B577: only use the captured PYTHONPATH when the recorded assay insertions
    # exactly reproduce the child plan's current value. An unrecorded rewrite
    # falls back to that current value. Child execution always keeps the full
    # `plan.env_effective` below the identity boundary.
    identity_env_effective = dict(plan.env_effective)
    pre_injection_env = getattr(plan, "pre_injection_env_effective", None)
    if isinstance(pre_injection_env, Mapping):
        original_has_pythonpath = "PYTHONPATH" in pre_injection_env
        original_pythonpath = pre_injection_env.get("PYTHONPATH")
        insertions = getattr(plan, "assay_pythonpath_insertions", ())
        insertions_are_valid = isinstance(insertions, tuple) and all(
            isinstance(path, str) and path for path in insertions
        )
        if insertions_are_valid and (
            not original_has_pythonpath or isinstance(original_pythonpath, str)
        ):
            if insertions:
                expected_pythonpath = os.pathsep.join(insertions)
                if original_pythonpath:
                    expected_pythonpath = os.pathsep.join(
                        (expected_pythonpath, original_pythonpath)
                    )
                expected_has_pythonpath = True
            else:
                expected_pythonpath = original_pythonpath
                expected_has_pythonpath = original_has_pythonpath
            current_has_pythonpath = "PYTHONPATH" in plan.env_effective
            current_pythonpath = plan.env_effective.get("PYTHONPATH")
            if (
                current_has_pythonpath == expected_has_pythonpath
                and (
                    not expected_has_pythonpath
                    or current_pythonpath == expected_pythonpath
                )
            ):
                if original_has_pythonpath:
                    identity_env_effective["PYTHONPATH"] = original_pythonpath
                else:
                    identity_env_effective.pop("PYTHONPATH", None)

    parts: list[str] = [_JUDGE_DIGEST_LABEL, tree_sha256]
    parts.append(netstring("" if tool_version is None else tool_version))
    argv = tuple(plan.argv_effective)
    parts.append(str(len(argv)))
    parts.extend(netstring(element) for element in argv)
    declared = sorted(dict(plan.env_declared).items())
    parts.append(str(len(declared)))
    for name, value in declared:
        parts.extend((netstring(name), netstring(value)))
    ambient = sorted(set(identity_env_effective) - set(plan.env_declared))
    parts.append(str(len(ambient)))
    passthrough = set(plan.env_passthrough)
    passthrough_value_digests: dict[str, str] = {}
    for name in ambient:
        parts.append(netstring(name))
        if name in passthrough:
            value_sha256 = hashlib.sha256(
                identity_env_effective[name].encode(
                    "utf-8", errors="surrogateescape"
                )
            ).hexdigest()
            passthrough_value_digests[name] = value_sha256
            parts.extend((netstring("passthrough-value"), netstring(value_sha256)))
        else:
            parts.append(netstring("name-only"))
    parts.append(netstring("" if plan.cwd_declared is None else str(plan.cwd_declared)))
    parts.append(
        netstring("" if plan.project_prefix is None else str(plan.project_prefix))
    )
    links = sorted(link_paths)
    parts.append(str(len(links)))
    parts.extend(netstring(path) for path in links)
    parts.append(netstring("1" if cold_witness_kills else "0"))
    parts.extend(
        netstring("" if value is None else value)
        for value in (
            r2_transform,
            r2_collection_sha256,
            r2_hook_fingerprint_sha256,
            runtime_fingerprint_sha256,
            coverage_hook_fingerprint_sha256,
            coverage_runtime_fingerprint_sha256,
            equivalence_ledger_sha256,
        )
    )
    if candidate_fail_fast:
        # (WP1) Candidate runs stop at the first failing test and skip
        # coverage collection, so they judge a different command than the
        # declared one. Folded ONLY when on: every pre-WP1 identity (and every
        # record resumed under it) is byte-identical.
        parts.append(netstring(CANDIDATE_FAIL_FAST_ID))
        parts.extend(netstring(token) for token in CANDIDATE_FAIL_FAST_APPENDED)
    if _component_digests is not None:
        def component_digest(fields: Sequence[str]) -> str:
            encoded = "\0".join(netstring(field) for field in fields)
            return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        passthrough_names = sorted(passthrough_value_digests)
        special_passthrough_values = {
            name: digest
            for name, digest in sorted(passthrough_value_digests.items())
            if name in {"PATH", "PYTHONPATH"}
        }
        other_passthrough = [
            (name, identity_env_effective[name])
            for name in passthrough_names
            if name not in {"PATH", "PYTHONPATH"}
        ]
        other_passthrough_digest = component_digest(
            (
                "assay-passthrough-values/1",
                str(len(other_passthrough)),
                *(field for pair in other_passthrough for field in pair),
            )
        )
        other_environment_names = sorted(name for name in ambient if name not in passthrough)
        fingerprint_values = (
            r2_transform,
            r2_collection_sha256,
            r2_hook_fingerprint_sha256,
            runtime_fingerprint_sha256,
            coverage_hook_fingerprint_sha256,
            coverage_runtime_fingerprint_sha256,
            equivalence_ledger_sha256,
        )
        candidate_fail_fast_fields = (
            ("enabled", *CANDIDATE_FAIL_FAST_APPENDED)
            if candidate_fail_fast
            else ("disabled",)
        )
        _component_digests.update(
            {
                "tree": tree_sha256,
                "argv": component_digest((str(len(argv)), *argv)),
                "declared_env": component_digest(
                    (str(len(declared)), *(field for pair in declared for field in pair))
                ),
                "environment_names": component_digest(
                    (str(len(other_environment_names)), *other_environment_names)
                ),
                "passthrough_names_sha256": component_digest(
                    (str(len(passthrough_names)), *passthrough_names)
                ),
                "passthrough_names": passthrough_names,
                # Per-name value digests are restricted to the two path
                # variables. Other passthrough values share one digest over
                # sorted name/value pairs, encoded as counted netstrings.
                "passthrough_values": special_passthrough_values,
                "passthrough_other_values_sha256": other_passthrough_digest,
                "cwd": component_digest(
                    ("" if plan.cwd_declared is None else str(plan.cwd_declared),)
                ),
                "project_prefix": component_digest(
                    ("" if plan.project_prefix is None else str(plan.project_prefix),)
                ),
                "link_paths": component_digest((str(len(links)), *links)),
                "tool_version": component_digest(
                    ("" if tool_version is None else tool_version,)
                ),
                "tool_fingerprints": component_digest(
                    tuple("" if value is None else value for value in fingerprint_values)
                ),
                "cold_witness_kills": component_digest(
                    ("1" if cold_witness_kills else "0",)
                ),
                "candidate_fail_fast": component_digest(candidate_fail_fast_fields),
            }
        )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


MAX_JUDGE_MISMATCH_GENERATIONS = 32
MAX_JUDGE_MISMATCH_DIAGNOSTIC_BYTES = 4096


def _judge_component_differences(
    current: Mapping[str, Any],
    previous_by_judge: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Describe bounded judge differences without exposing env digests."""
    unavailable = {
        "components_available": False,
        "components": [],
        "passthrough_names": [],
        "passthrough_name_candidates": [],
        "passthrough_name_diagnostic": "",
    }
    if (
        not previous_by_judge
        or len(previous_by_judge) > MAX_JUDGE_MISMATCH_GENERATIONS
        or any(not isinstance(previous, Mapping) for previous in previous_by_judge.values())
    ):
        return unavailable
    changed: set[str] = set()
    exact_passthrough_names: set[str] = set()
    passthrough_name_candidates: set[str] = set()
    for previous in previous_by_judge.values():
        for component in (
            "tree",
            "argv",
            "declared_env",
            "environment_names",
            "cwd",
            "project_prefix",
            "link_paths",
            "tool_version",
            "tool_fingerprints",
            "cold_witness_kills",
            "candidate_fail_fast",
        ):
            if previous.get(component) != current.get(component):
                changed.add(component)
        old_names = set(previous.get("passthrough_names", ()))
        new_names = set(current.get("passthrough_names", ()))
        if old_names != new_names:
            changed.add("passthrough_names")
            exact_passthrough_names.update(old_names ^ new_names)
        old_values = previous.get("passthrough_values", {})
        new_values = current.get("passthrough_values", {})
        if not isinstance(old_values, Mapping) or not isinstance(new_values, Mapping):
            return unavailable
        special_value_names = {
            name
            for name in {"PATH", "PYTHONPATH"}
            if old_values.get(name) != new_values.get(name)
        }
        if special_value_names:
            changed.add("passthrough_values")
            exact_passthrough_names.update(special_value_names)
        old_other_names = old_names - {"PATH", "PYTHONPATH"}
        new_other_names = new_names - {"PATH", "PYTHONPATH"}
        old_other_digest = previous.get("passthrough_other_values_sha256")
        new_other_digest = current.get("passthrough_other_values_sha256")
        if old_other_digest != new_other_digest:
            changed.add("passthrough_values")
            if old_other_names == new_other_names and len(old_other_names) == 1:
                exact_passthrough_names.update(old_other_names)
            else:
                passthrough_name_candidates.update(
                    old_other_names & new_other_names
                )
    exact_names = sorted(exact_passthrough_names)
    candidates = sorted(passthrough_name_candidates)
    name_details = []
    if len(exact_names) == 1:
        name_details.append(f"passthrough name: {exact_names[0]}")
    elif exact_names:
        name_details.append(f"passthrough names: {', '.join(exact_names)}")
    if candidates:
        name_details.append(
            "passthrough value changed for one or more of: "
            f"{', '.join(candidates)}"
        )
    result = {
        "components_available": True,
        "components": sorted(changed),
        "passthrough_names": exact_names,
        "passthrough_name_candidates": candidates,
        "passthrough_name_diagnostic": "; ".join(name_details),
    }
    encoded_size = len(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    if encoded_size > MAX_JUDGE_MISMATCH_DIAGNOSTIC_BYTES:
        return unavailable
    return result


def _crash_diagnostic_tails(
    outcome_bucket: str, result: "CommandResult"
) -> dict[str, str]:
    """(B071) The captured subprocess output a ``crashed`` candidate's state
    record carries, keyed by the names :mod:`assay.verdict` already defines
    (``result_stdout_tail``/``result_stderr_tail``, ``verdict.py:3778-3779``,
    round-tripped by ``verify.py`` for other claim types) — so a future
    verdict-level surfacing, if it is ever wanted, needs no new vocabulary.

    **``crashed`` only.** ``killed`` and ``survived`` are already fully
    carried by the pass/fail split, so a tail there would be bulk with no
    question it answers; ``budget_exceeded`` holds whatever was captured
    before the timeout, genuinely useful but lower priority and not in this
    change's scope. ``crashed`` is the one bucket where the tail is the
    ENTIRE diagnostic trail: B071 was filed after a real
    ``uq_work_units_id_operation`` candidate landed ``crashed`` with nothing
    retained but its identity fields, and recovering the actual reason
    (``ERROR: there is no unique constraint matching given keys for
    referenced table "work_units"``) took a manual by-hand re-run of the exact
    byte-range mutation — text ``execute_command`` had already captured
    during the ORIGINAL run and then dropped when ``run.result`` went out of
    scope.

    Emitted whenever the tail EXISTS, including as the empty string: ``""``
    means "the stream was empty", which is itself a diagnosis, and is not the
    same fact as a record written by a build that had no tails at all.

    Size: both tails are already bounded at ``COMMAND_TAIL_BYTES`` (64 KiB of
    encoded bytes each, ``runner.py``), so no new bound is introduced here.
    The worst case still fits the reader's own
    :data:`MUTATION_STATE_RECORD_LIMIT` (1 MiB): ``json.dump`` defaults to
    ``ensure_ascii=True``, whose most expensive escape is 6 bytes per
    character, and 65,536 bytes can decode to at most 65,536 characters, so
    two maximal tails cost at most ~768 KiB alongside a few hundred bytes of
    identity fields. Pinned by a test rather than left as arithmetic in a
    comment.

    This file is diagnostic state, NOT a verified artifact: nothing about
    ``assay verify``, the verdict wire format, or resume validation changes.
    Exact present passthrough values are masked before the record is written,
    while older records without these fields still resume exactly as before.
    """
    if outcome_bucket != "crashed":
        return {}
    tails: dict[str, str] = {}
    passthrough_values = tuple(
        result.plan.env_effective[name]
        for name in result.plan.env_passthrough
        if name in result.plan.env_effective
    )
    if result.stdout_tail is not None:
        tails["result_stdout_tail"] = _redact_passthrough_text(
            result.stdout_tail, passthrough_values
        )
    if result.stderr_tail is not None:
        tails["result_stderr_tail"] = _redact_passthrough_text(
            result.stderr_tail, passthrough_values
        )
    return tails


def _read_candidate_resource_counters() -> ResourceLimitCounters:
    """Fail closed when the lane cannot expose exact cgroup v2 counters."""
    try:
        return read_candidate_resource_counters(
            observer_socket_path=os.environ.get("ASSAY_RESOURCE_OBSERVER_SOCKET")
        )
    except ResourceLimitObservationError as exc:
        raise AssayError(
            "cannot observe the required cgroup v2 process and memory limit "
            f"events for native R2 candidate execution: {exc}",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.CGROUP_OBSERVATION_UNAVAILABLE,
        ) from exc


def _candidate_resource_limit_evidence(
    before: ResourceLimitCounters, after: ResourceLimitCounters
) -> tuple[ResourceLimitEvidence | None, str | None, tuple[str, ...]]:
    """Bind one candidate window, preserving comparison failures as telemetry."""
    try:
        evidence, changes = ResourceLimitEvidence.between(before, after)
        return evidence, None, changes
    except (ResourceLimitObservationError, ValueError) as exc:
        return None, str(exc), ()


def _read_candidate_resource_counters_during_campaign(
) -> tuple[ResourceLimitCounters | None, str | None]:
    """Read optional candidate telemetry after the required preflight probe."""
    try:
        return _read_candidate_resource_counters(), None
    except AssayError as exc:
        return None, str(exc)


def _zero_window_resource_limit_evidence(
) -> tuple[ResourceLimitEvidence | None, str | None]:
    """Record an optional zero-duration sample for a candidate that never started."""
    sample, reason = _read_candidate_resource_counters_during_campaign()
    if sample is None:
        return None, reason
    evidence, reason, _changes = _candidate_resource_limit_evidence(sample, sample)
    return evidence, reason


def _append_unselected(
    result: Mutation, unselected: Sequence[MutantJob]
) -> Mutation:
    """(B531) Report left-out, uncovered candidates as never started.

    The same representation a campaign-deadline remainder already has:
    ``budget_exceeded`` with not-started process-tree evidence and a
    ``NO_MEASUREMENT/NOT_STARTED`` profile count. It keeps every bucket and
    telemetry sum consistent and makes the claim non-PASS.
    """
    evidence, _reason = _zero_window_resource_limit_evidence()
    added = [
        _outcome_of(
            job,
            resource_limit_evidence=evidence,
            process_tree_evidence=ProcessTreeEvidence.not_started(),
        )
        for job in unselected
    ]
    buckets = {name: list(getattr(result, name)) for name in MUTATION_BUCKETS}
    buckets["budget_exceeded"].extend(added)
    telemetry = dict(result.r2_telemetry or {})
    profile = dict(telemetry.get("profile") or {})
    statuses = collections.Counter(profile.get("status_counts") or {})
    reasons = collections.Counter(profile.get("reason_counts") or {})
    statuses[observer_windows.STATUS_NO_MEASUREMENT] += len(added)
    reasons[observer_windows.NOT_STARTED] += len(added)
    profile["status_counts"] = dict(statuses)
    profile["reason_counts"] = dict(reasons)
    profile.setdefault("modes", [])
    telemetry["profile"] = profile
    telemetry["unselected_total"] = len(added)
    candidate_ids = result.candidate_ids
    return _dataclass_replace(
        result,
        candidate_count=result.candidate_count + len(added),
        total=result.total + len(added),
        r2_telemetry=telemetry,
        candidate_ids=candidate_ids,
        **{
            name: tuple(sorted(buckets[name], key=lambda item: item.identity))
            for name in MUTATION_BUCKETS
        },
    )


def _fold_resumed_telemetry(
    executed: Mapping[str, Any] | None, records: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Add the resumed state records' profiles and worker counts (B203) to the
    telemetry of the candidates this invocation executed. Every current record
    states its ``worker_count`` and ``profile`` (the loader refuses one that
    does not), so the campaign totals cover every candidate in the verdict."""
    executed = executed or {}
    profile_part = executed.get("profile") or {}
    statuses: collections.Counter = collections.Counter(
        profile_part.get("status_counts") or {}
    )
    reasons: collections.Counter = collections.Counter(
        profile_part.get("reason_counts") or {}
    )
    modes: set[str] = set(profile_part.get("modes") or ())
    worker_counts: collections.Counter = collections.Counter(
        executed.get("worker_counts") or {}
    )
    for entry in records:
        worker_counts[str(entry["worker_count"])] += 1
        profile = entry["profile"]
        statuses[profile["status"]] += 1
        if profile["reason"] is not None:
            reasons[profile["reason"]] += 1
        mode = profile["observer"].get("mode")
        if mode is not None:
            modes.add(mode)
    return {
        "profile": {
            "status_counts": dict(statuses),
            "reason_counts": dict(reasons),
            "modes": sorted(modes),
        },
        "worker_counts": dict(worker_counts),
        # (B524) Carried through unchanged: only killed/survived records are
        # resumed or carried over (a budget/hung/crashed record re-executes),
        # so a resumed candidate is never one of the masked budget positions
        # the sweep itself left unattempted.
        "budget_exceeded_not_started": (
            executed.get("budget_exceeded_not_started") or 0
        ),
        "schedule": executed.get("schedule"),
    }


def _profile_rss_bytes(profile: Mapping[str, Any]) -> int | None:
    """The profile's peak RSS as an integer, or ``None`` when the observer
    produced no number for it."""
    value = profile["metrics"]["rss_peak_bytes"]
    return None if value is None else int(value)


def _result_rss_bytes(result: "observer_windows.WindowResult | None") -> int | None:
    """Peak RSS of one finished observer window (the scheduler's prediction
    input), or ``None`` when there was no reply."""
    if result is None or result.reply is None:
        return None
    value = result.reply["metrics"]["rss_peak_bytes"]
    return None if value is None else int(value)


def _startup_seconds(events_path: Path) -> dict[str, float | None]:
    """``startup_seconds`` for one liveness candidate, from its sidecar's spawn
    time and its events file: Assay's own clock, not a resource measurement.
    Anything absent, negative, or ill-typed is ``None``: diagnostics never
    fail a candidate."""
    sidecar = liveness.read_resource_sidecar(events_path) or {}
    spawned_at = liveness._finite_number(sidecar.get("spawned_at"))
    session_start, first_test = liveness.first_event_times(events_path)
    to_session_start = (
        None if session_start is None or spawned_at is None else session_start - spawned_at
    )
    to_first_test = (
        None if first_test is None or spawned_at is None else first_test - spawned_at
    )
    return {
        "to_session_start": (
            to_session_start
            if to_session_start is not None and to_session_start >= 0
            else None
        ),
        "to_first_test": (
            to_first_test
            if to_first_test is not None and to_first_test >= 0
            else None
        ),
    }


def _process_tree_evidence_from_result(
    result: CommandResult,
) -> ProcessTreeEvidence | None:
    raw = result.process_tree_evidence
    if isinstance(raw, ProcessTreeEvidence):
        return raw
    if isinstance(raw, Mapping):
        return ProcessTreeEvidence.from_dict(raw)
    return None


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


def _outcome_of(
    job: MutantJob,
    *,
    resource_limit_evidence: ResourceLimitEvidence | None,
    kill_signal: str | None = None,
    first_failing_test: str | None = None,
    execution: MutationExecution | None = None,
    evidence: MutantEvidence | None = None,
    process_tree_evidence: ProcessTreeEvidence | None = None,
    underlying_outcome: UnderlyingOutcome | None = None,
    candidate_error: CandidateOutcomeError | None = None,
) -> MutantOutcome:
    """The artifact projection of one attempted mutant (A-180).

    Built DIRECTLY from the validated site, so the wire identity and the
    experiment that produced it cannot disagree — never reconstructed by
    diffing the mutated file against the original, which does not recover a
    syntax site at all. *kill_signal* (P34) is attached ONLY by the caller
    that already knows this job landed in the ``killed`` bucket -- passing
    it for any other bucket would violate :class:`~assay.verdict.Mutation`'s
    own ``kill_signal``-is-``killed``-only invariant (A-223e).
    """
    original_bytes = job.original_text.encode("utf-8")
    mutated_bytes = job.site.apply(original_bytes)
    source_sha256 = hashlib.sha256(original_bytes).hexdigest()
    mutated_file_sha256 = hashlib.sha256(mutated_bytes).hexdigest()
    return MutantOutcome(
        path=job.path,
        lineno=job.site.lineno,
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        replacement_sha256=job.site.replacement_sha256,
        operator=job.site.operator,
        description=job.site.description,
        kill_signal=kill_signal,
        first_failing_test=first_failing_test,
        candidate_id=candidate_id_from_fields(
            path=job.path,
            source_sha256=source_sha256,
            start_byte=job.site.start_byte,
            end_byte=job.site.end_byte,
            mutated_file_sha256=mutated_file_sha256,
            operator=job.site.operator,
        ),
        source_sha256=source_sha256,
        mutated_file_sha256=mutated_file_sha256,
        execution=execution or MutationExecution(mode="full"),
        resource_limit_evidence=resource_limit_evidence,
        process_tree_evidence=process_tree_evidence,
        underlying_outcome=underlying_outcome,
        evidence=evidence,
        candidate_error=candidate_error,
    )


def _candidate_outcome_error(error: ValueError) -> CandidateOutcomeError:
    message = f"{type(error).__name__}: {' '.join(str(error).split())}"
    encoded = message.encode("utf-8", "replace")
    if len(encoded) > CANDIDATE_OUTCOME_ERROR_MAX_BYTES:
        message = encoded[:CANDIDATE_OUTCOME_ERROR_MAX_BYTES].decode(
            "utf-8", "ignore"
        )
    return CandidateOutcomeError(
        reason_code=ReasonCode.EXEC_FAILED,
        message=message,
    )


def _minimal_candidate_outcome(
    job: MutantJob,
    *,
    process_tree_evidence: ProcessTreeEvidence | None,
    underlying_outcome: UnderlyingOutcome | None,
    resource_limit_evidence: ResourceLimitEvidence | None,
    candidate_error: CandidateOutcomeError,
) -> MutantOutcome:
    """Build the conservative native crashed shape if validation keeps failing.

    This last-resort path uses only the already-validated mutation job and
    typed worker evidence. It bypasses ``MutantOutcome.__post_init__`` because
    that is the failing boundary; every required identity and bucket field is
    still populated from the canonical constructors and the candidate is
    never dropped from the aggregate.
    """
    original_bytes = job.original_text.encode("utf-8")
    mutated_bytes = job.site.apply(original_bytes)
    source_sha256 = hashlib.sha256(original_bytes).hexdigest()
    mutated_file_sha256 = hashlib.sha256(mutated_bytes).hexdigest()
    process_tree = (
        process_tree_evidence
        if isinstance(process_tree_evidence, ProcessTreeEvidence)
        else ProcessTreeEvidence.not_started()
    )
    completed_leak = (
        process_tree.leader_status == "exited"
        and process_tree.outliving_descendants > 0
    )
    values = {
        "path": job.path,
        "lineno": job.site.lineno,
        "start_byte": job.site.start_byte,
        "end_byte": job.site.end_byte,
        "replacement_sha256": job.site.replacement_sha256,
        "operator": job.site.operator,
        "description": job.site.description,
        "kill_signal": None,
        "first_failing_test": None,
        "discard_reason": None,
        "candidate_id": candidate_id_from_fields(
            path=job.path,
            source_sha256=source_sha256,
            start_byte=job.site.start_byte,
            end_byte=job.site.end_byte,
            mutated_file_sha256=mutated_file_sha256,
            operator=job.site.operator,
        ),
        "source_sha256": source_sha256,
        "mutated_file_sha256": mutated_file_sha256,
        "execution": MutationExecution(mode="full"),
        "resource_limit_evidence": (
            resource_limit_evidence
            if isinstance(resource_limit_evidence, ResourceLimitEvidence)
            else None
        ),
        "process_tree_evidence": process_tree,
        "underlying_outcome": (
            underlying_outcome
            if completed_leak and isinstance(underlying_outcome, UnderlyingOutcome)
            else None
        ),
        "candidate_error": candidate_error,
        "evidence": None,
    }
    outcome = object.__new__(MutantOutcome)
    for name, value in values.items():
        object.__setattr__(outcome, name, value)
    return outcome


def _snapshot_left_dirt(
    job: MutantJob, snapshot, *, remaining: git.Remaining | None = None
) -> AssayError | None:
    """P23/A-195: a mutant's own snapshot must still name its own commit
    after the command runs, exactly like every other snapshot unit. Checked
    HERE, inside the worker, so a dirty/committing mutant is caught before
    its context closes and before any sibling is affected.

    *remaining* (P26/A-212) bounds this check's own Git children by the one
    lane deadline; :func:`run_mutation` passes ``deadline.remaining``, so a
    hung ``status``/``rev-parse`` can never outlive the lane budget. The
    candidate remains unclassified unless this integrity check completes; a
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


def baseline_wall_seconds(baseline: CommandResult) -> float:
    """(B091/D-23) The baseline command's own measured wall time, in
    seconds -- ``baseline.ended - baseline.started``, the two ISO-8601
    instants every :class:`CommandResult` already carries. Never negative
    (P21/A-182's own ``ended >= started`` invariant is enforced upstream of
    every :class:`CommandResult` this function is ever handed; clamped to
    ``0.0`` here anyway rather than trusting that at a distance, because a
    formula downstream -- :func:`auto_budget_per_candidate_seconds` -- would
    otherwise silently shrink a derived bound from a negative input).
    """
    started = datetime.fromisoformat(baseline.started)
    ended = datetime.fromisoformat(baseline.ended)
    return max(0.0, (ended - started).total_seconds())


def auto_budget_per_candidate_seconds(baseline_wall: float) -> float:
    """(B091/D-23) The auto per-candidate bound: ``max(3 x baseline,
    baseline + 60s)``.

    D-17's layer-3 "derived ceiling" -- safety only, computed on the host at
    run time from an ACTUAL measurement rather than guessed ahead of one, so
    it scales with the hardware the lane happens to be running on instead of
    failing every candidate on a slower host than whoever picked a fixed
    number was thinking of. ``3x`` gives a fast baseline (sub-second unit
    tests) enough headroom that ordinary variance never trips it; ``+60s``
    keeps a near-instant baseline (a handful of asserts) from deriving a
    near-instant bound that would flag the very next candidate whose own
    mutation cost it one extra import. Never a detector of "the mutant is
    slow" on its own -- it is the ceiling B090 measured missing entirely,
    not a replacement for the liveness/cadence layers D-17 puts ABOVE it
    (A2/A3/A4 in this same package).
    """
    # (P4/S1) ONE formula, shared with the CPU-without-progress bound
    # (`assay.liveness.derived_bound_from_baseline`): the wall budget and the
    # CPU bound are the same policy applied to two different clocks, so there
    # is no second `60.0`/`3.0` pair to drift out of step with it.
    return liveness.derived_bound_from_baseline(baseline_wall)


def _native_r2_parallel_checked(function):
    """Require copy-on-write snapshots before enabling parallel candidates."""

    @wraps(function)
    def checked(*args, **kwargs):
        jobs = kwargs.get("jobs")
        prepared = kwargs.get("prepared")
        if is_strict_int(jobs) and jobs > 1 and not getattr(
            prepared, "cheap_clone", False
        ):
            raise LaneConfigError(
                "native R2 --jobs greater than 1 requires a cheap_clone "
                "snapshot workspace; run with --jobs 1 or configure btrfs CoW"
            )
        return function(*args, **kwargs)

    return checked


def _budget_exceeded_split(counted: int, unstarted: int) -> tuple[int, int]:
    """(B524) ``(dispatched, never started)`` for the ``end`` progress record.

    ``buckets.budget_exceeded`` counts ONE closed-vocabulary bucket that stands
    for two different situations, and only ``_execute_mutation_jobs`` (plus the
    selection frame in :func:`run_mutation`) can tell them apart. This is the
    one place the two halves are checked against the bucket's own count, so a
    producer bug fails loud instead of publishing an inverted split: more
    never-started candidates than the bucket holds would claim work the
    verdict never recorded, and a negative remainder would claim candidates
    that do not exist.
    """
    for name, value in (("counted", counted), ("unstarted", unstarted)):
        if not is_strict_int(value) or value < 0:
            raise MutationStateError(
                f"the end record's budget_exceeded split has an invalid "
                f"{name} count {value!r}"
            )
    if unstarted > counted:
        raise MutationStateError(
            "the end record's budget_exceeded split is inconsistent: "
            f"{unstarted} never started of {counted} recorded"
        )
    return counted - unstarted, unstarted


@_native_r2_parallel_checked
def run_mutation(
    *,
    baseline: CommandResult,
    prepared: SnapshotRepository,
    plan: CommandPlan,
    deadline: LaneDeadline,
    targets: Iterable[MutationTarget],
    context_sources: Iterable[str] | None = None,
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
    #: (B091/D-23) Whether *budget_per_candidate_seconds* should be DERIVED
    #: from the measured baseline (:func:`auto_budget_per_candidate_seconds`)
    #: rather than taken as given -- the "auto" default `config.py` now
    #: applies to an omitted/``"auto"``-spelled ``judge.mutation.
    #: budget_per_candidate``. The caller (:mod:`assay.runner`) resolves
    #: WHICH of the three closed shapes (auto / an explicit duration /
    #: ``"none"``) the lane declared; this function is where the one that
    #: actually needs a measurement -- auto -- gets it, because this is the
    #: only place both the resolved baseline and the per-candidate
    #: enforcement live together. *budget_per_candidate_seconds* is ignored
    #: (and must be ``None``) when this is ``True``: the two are mutually
    #: exclusive so there is exactly one source for the number that both
    #: bounds every candidate AND is reported back (the ``plan`` progress
    #: event, and the caller's own verdict field) -- never two independent
    #: derivations of it (B088's own "how a reader and a writer drift
    #: apart").
    budget_per_candidate_auto: bool = False,
    #: (B091/RW-36) The liveness record `assay.liveness.inject_liveness_plugin`
    #: already computed, BEFORE this function was even called (the caller,
    #: `runner._run_prepared_lane`, injects the plugin into the shared plan
    #: ahead of the baseline run) -- passed straight through, never
    #: recomputed, so the `plan` progress event and `judgment.r2.liveness`
    #: (built by the caller from the SAME `LivenessInjection`) can never
    #: disagree. `False`/`None`/`None` for every non-native, non-python, or
    #: liveness-off lane -- byte-identical to a pre-RW-36 `plan` event's
    #: absence of the key would have been, except the key is always present
    #: now (additive: a reader that does not know it yet simply ignores it).
    liveness_active: bool = False,
    liveness_reason: str | None = None,
    liveness_plugin: str | None = None,
    #: (B091/D-23, P7 A4) The BASELINE's own liveness events file (never a
    #: candidate's) -- `None` for every non-liveness lane, exactly like the
    #: three fields just above. When given, every `test` event it carries
    #: is forwarded onto the progress stream, verbatim, right after the
    #: `plan` record: RW-33's own "test events reach the progress stream
    #: for the BASELINE only" instruction, and `assay.liveness.
    #: baseline_test_events` is the ONE place that translation happens.
    liveness_baseline_events_path: Path | None = None,
    #: (B091/D-23, P7 A4) The SAME two measurements the caller
    #: (`runner._run_prepared_lane`) already derived from
    #: *liveness_baseline_events_path* via `assay.liveness.
    #: baseline_slowest_test_s`/`compute_expect_next_event_within_s` --
    #: read back onto the `plan` progress event here, never recomputed a
    #: second, independent way (B088's own "two derivations drift"
    #: lesson). Both `None` for every non-liveness lane.
    liveness_slowest_test_s: float | None = None,
    liveness_expect_next_event_within_s: float | None = None,
    #: (B091 round-1 B2) The two figures RW-49/D3's calibration (a) adds,
    #: from the SAME one `assay.liveness.compute_liveness_calibration` call
    #: as the two above. *liveness_worst_gap_s* is the raw measurement
    #: `expect_next_event_within_s` is now derived from (the baseline's
    #: worst observed inter-event gap, session brackets included) --
    #: `slowest_test_s` keeps its pre-B2 meaning, the slowest `call`-phase
    #: duration, which is NO LONGER what the bound comes from.
    #: *liveness_pre_first_event_within_s* is the separate, tighter bound
    #: governing a candidate that has produced no event at all yet.
    liveness_worst_gap_s: float | None = None,
    liveness_pre_first_event_within_s: float | None = None,
    liveness_cpu_window_s: float | None = None,
    liveness_idle_floor_s: float | None = None,
    #: (P4/S1,S2,S3) The CPU progress bounds and watch regime as disclosed on
    #: the `plan` progress record -- `None`/`{}` for a run whose watch is not
    #: armed, which is exactly what the record then says.
    liveness_cpu_without_progress_bound_s: float | None = None,
    liveness_per_test_baseline_s: Mapping[str, float] | None = None,
    #: (B091/D-23, P7 A4) The lane's own PERSISTENT
    #: `.assay/liveness/candidates/` directory -- `None` for every
    #: non-liveness lane. When given, each candidate's own `tests_completed`
    #: progress-event field is read back from the events file
    #: `assay.liveness.candidate_events_path` names for that candidate's own
    #: execution `cwd`, via `assay.liveness.count_test_events` -- never a
    #: second, independent count.
    liveness_events_dir: Path | None = None,
    #: (P4/S3) Whether the no-progress watch is ARMED for this run -- the
    #: caller's own per-run decision (`runner._run_prepared_lane`: liveness
    #: injected, the baseline produced plugin events, the candidate's CPU is
    #: readable). Armed, `budget_per_candidate` is advisory while candidate CPU
    #: is readable and the event stream intact; it binds if CPU becomes unreadable
    #: or the stream breaks (see `assay.liveness.LivenessRunner.advisory_budget_s`).
    #: CPU/per-test/no-progress bounds stop an intact watched run; NOT armed, it
    #: binds as before -- one switch names the regime for this run.
    liveness_watch_armed: bool = False,
    #: (P4/S3) The machine-readable spelling of WHY the watch is or is not
    #: armed, disclosed on the `plan` progress record beside the fact. `None`
    #: when the caller passed no wait at all (a non-liveness lane records
    #: `liveness-not-injected:...` itself).
    liveness_watch_reason: str | None = None,
    progress_artifact: Path | str | None = None,
    #: (B064) An already-open, lane-wide :class:`ProgressStream`. The lane
    #: runner opens the stream once, high up, so the R0/R1 phase records and
    #: the ``verdict_written`` terminal share ONE file, ONE ``elapsed_s``
    #: origin and ONE header with this sweep's own records. When it is
    #: given, *progress_artifact* is not opened a second time and no second
    #: ``run`` header is emitted. *progress_artifact* remains for the direct
    #: library caller, which owns the whole run by itself.
    progress_stream: "ProgressStream | None" = None,
    #: (B066) The DIRECTORY resume records live in -- the caller's
    #: authoritative store, never an ephemeral snapshot's. It used to be
    #: named ``state_project_root`` and the ``.assay/mutation-state/`` tail
    #: was joined on inside, which is exactly why ``--resume`` was inert in a
    #: fresh-worktree-per-run consumer (cmru's release transaction, dstdns
    #: Mode-B instances): the store went away with the worktree. It is a
    #: root, not a project root, and it is named for what it is.
    state_root: Path | str | None = None,
    state_root_fd: int | None = None,
    state_root_guard: Callable[[], None] | None = None,
    #: (B092) The native-R2 lane's explicitly resolved identity filter. `None`
    #: preserves the legacy B088 whole-tree digest; an empty tuple is an
    #: explicit filtered identity domain and must remain distinct from it.
    identity_exclude: Sequence[str] | None = None,
    resume: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
    candidate_selection: frozenset[str] | None = None,
    expected_selection_sha256: str | None = None,
    #: (B091/D-23, P7 A5) ``--rejudge <id>[,...]``: candidate ids to drop
    #: from the resume store BEFORE it is consulted, so each re-executes
    #: instead of replaying a possibly-stale verdict, exactly like a
    #: candidate with no record at all. Empty (never `None` -- the caller
    #: resolves an omitted flag to the empty frozenset) for every run that
    #: never asked for one. Requires *resume*; refused otherwise (a
    #: rejudge with nothing to resume FROM is not a meaningful request).
    rejudge_ids: frozenset[str] = frozenset(),
    #: (B091/D-23, P7 A5) ``--rejudge-outcome hung,budget_exceeded,...``:
    #: the SAME drop, selected by a resumed record's own persisted
    #: ``outcome_bucket`` (one of :data:`~assay.verdict.MUTATION_BUCKETS`)
    #: rather than by explicit id. The two selections are a UNION, never
    #: mutually exclusive -- a candidate matching either is dropped.
    rejudge_outcomes: frozenset[str] = frozenset(),
    #: B106 witnesses from one verifier-accepted prior verdict. Each entry
    #: maps the canonical candidate ID to (prior verdict digest, pytest node).
    reuse_witnesses: Mapping[str, tuple[str, str]] | None = None,
    expected_plan_sha256: str | None = None,
    campaign_deadline_sha256: str | None = None,
    cold_witness: bool = False,
    declared_plan: CommandPlan | None = None,
    r2_facts: ReceiptFacts | None = None,
    coverage_facts: ReceiptFacts | None = None,
    r2_baseline_s: float | None = None,
    equivalence_ledger_sha256: str | None = None,
    manifest_node_ids: Sequence[str] = (),
    #: (B207) Candidate scheduling inputs for ``jobs`` greater than 1: the
    #: admission budget, class factors and operator prediction medians.
    #: Only dispatch ORDER and admission depend on it, never a result.
    schedule: _schedule.ScheduleOptions | None = None,
    #: (WP1) ``judge.mutation.candidate_fail_fast``: candidate commands (never
    #: the baseline) stop at the first failing test and skip coverage.
    candidate_fail_fast: bool = False,
    #: (B529-B531/B561) Resource projection, carry-over/forward and targeted
    #: candidate sets; ``None`` keeps every pre-B529 behaviour byte-identical.
    flex: FlexOptions | None = None,
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

    *jobs* is validated BEFORE the executor boundary (work item 5): this
    function is a public surface a caller may invoke directly, so
    :mod:`assay.config`'s load-time discipline for a real ``assay.toml``
    cannot be assumed to have already run here. Native R2 also requires
    exactly one worker (B157); overlapping candidates would share cgroup
    event counters and make resource-event attribution ambiguous.

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

    Submission uses a bounded continuous work queue with at most *jobs*
    futures in flight (``executor_factory(jobs)`` called exactly once with
    the declared value, never a derived value, A-082/A-122). The public
    native-R2 entry point pins that value to one; the queue remains bounded
    for future use if candidates gain independent cgroup evidence. A completed
    future frees a slot immediately, so a slow earlier candidate does not
    hold a whole batch at a barrier. On deadline expiry or a fatal worker
    error, submission stops and every already-submitted future is drained so
    its snapshot context closes. Results stay POSITION-aligned with the
    pending job list, candidate progress is emitted in DISPATCH order (B207:
    canonical index order for a serial run or without predictions; with
    ``jobs > 1`` the two-ended-matching order of :mod:`assay._schedule`, whose
    only effect is which candidate starts next), and the three non-killed
    buckets remain identity-ordered without a re-sort.
    When several fatal worker errors are observed, the lowest candidate
    position wins; the lane's outcome, not only its reason code, can depend
    on that choice. An expiry marks that position and every unsubmitted
    identity ``budget_exceeded`` while completed identities remain evidence.

    **P34/A-279/§3.6.** *equivalence_artifact*/*kill_signal_artifact* are
    ``None`` for every lane that does not declare them (every Python/Go
    lane through this build, A-227) -- their absence is exactly what routes
    every mutant through the shared :func:`_bucket_for_run` table. When
    *equivalence_artifact* IS declared, *baseline_equivalence* must be the
    caller's own already-resolved baseline artifact bytes --
    never ``None``, which this function refuses outright rather than
    silently comparing every mutant's bytes against it (a comparison that
    can never be equal, so no mutant could ever be recorded ``equivalent``,
    hiding exactly the fault the carve's own A-279 finding was about).
    """
    # The CLI path also wraps native R2's baseline in this runner from
    # `_run_prepared_lane`. Direct library callers still need candidate-tree
    # isolation when they pass Assay's ordinary subprocess runner.
    from . import runner as _runner_module

    if not is_strict_int(jobs):
        raise ValueError(f"run_mutation jobs must be an integer, got {jobs!r}")
    if jobs < 1:
        raise ValueError(f"run_mutation jobs must be >= 1, got {jobs}")
    if not is_strict_int(max_mutants):
        raise ValueError(
            f"run_mutation max_mutants must be an integer, got {max_mutants!r}"
        )
    if not 1 <= max_mutants <= 10_000:
        raise ValueError(
            f"run_mutation max_mutants must be in 1..10,000, got {max_mutants}"
        )
    if not isinstance(cold_witness, bool):
        raise ValueError("run_mutation cold_witness must be a boolean")
    if cold_witness:
        if declared_plan is None or r2_facts is None or coverage_facts is None:
            raise ValueError(
                "run_mutation cold_witness requires declared_plan, r2_facts, "
                "and coverage_facts"
            )
        if (
            r2_facts.runtime_fingerprint_sha256 is None
            or coverage_facts.runtime_fingerprint_sha256 is None
        ):
            raise ValueError("run_mutation cold_witness requires runtime fingerprints")
        if (
            r2_baseline_s is None
            or not isinstance(r2_baseline_s, (int, float))
            or not math.isfinite(r2_baseline_s)
            or r2_baseline_s < 0
        ):
            raise ValueError(
                "run_mutation cold_witness requires a finite non-negative "
                "R2 baseline duration"
            )
        if any(not isinstance(node_id, str) for node_id in manifest_node_ids):
            raise ValueError("run_mutation manifest_node_ids must contain strings")
    elif any(
        value is not None
        for value in (declared_plan, r2_facts, coverage_facts, r2_baseline_s)
    ) or manifest_node_ids:
        raise ValueError("run_mutation cold-witness facts require cold_witness=True")
    if equivalence_ledger_sha256 is not None and re.fullmatch(
        r"[0-9a-f]{64}", equivalence_ledger_sha256
    ) is None:
        raise ValueError(
            "run_mutation equivalence_ledger_sha256 must be a SHA-256 digest or None"
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
    if budget_per_candidate_seconds is not None and not is_finite_positive(
        budget_per_candidate_seconds
    ):
        raise ValueError(
            "run_mutation budget_per_candidate_seconds must be a positive "
            f"finite number or None, got {budget_per_candidate_seconds!r}"
        )
    if not isinstance(budget_per_candidate_auto, bool):
        raise ValueError(
            "run_mutation budget_per_candidate_auto must be a boolean, got "
            f"{budget_per_candidate_auto!r}"
        )
    if budget_per_candidate_auto and budget_per_candidate_seconds is not None:
        raise ValueError(
            "run_mutation budget_per_candidate_auto=True derives its own "
            "budget_per_candidate_seconds from the measured baseline; "
            "passing both is two sources for the one number that bounds "
            "every candidate, and the caller must pick exactly one"
        )
    if not isinstance(resume, bool):
        raise ValueError(f"run_mutation resume must be a boolean, got {resume!r}")
    shard_specified = shard_index is not None or shard_count is not None
    selection_specified = candidate_selection is not None
    if selection_specified:
        if not isinstance(candidate_selection, frozenset) or not candidate_selection:
            raise ValueError("run_mutation candidate_selection must be a non-empty frozenset")
        if any(
            not isinstance(candidate, str)
            or re.fullmatch(r"[0-9a-f]{64}", candidate) is None
            for candidate in candidate_selection
        ):
            raise ValueError("run_mutation candidate_selection must contain SHA-256 candidate IDs")
        if shard_specified:
            raise ValueError("run_mutation candidate_selection cannot be combined with sharding")
        if len(candidate_selection) > max_mutants:
            raise ValueError(
                "run_mutation candidate_selection exceeds max_mutants"
            )
        if expected_selection_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", expected_selection_sha256
        ) is None:
            raise ValueError("run_mutation expected_selection_sha256 must be a SHA-256 digest")
        if state_root is None:
            raise ValueError(
                "run_mutation candidate_selection requires the caller's authoritative state_root"
            )
    elif expected_selection_sha256 is not None:
        raise ValueError(
            "run_mutation expected_selection_sha256 requires candidate_selection"
        )
    merge_manifest_root_refusal = (
        isinstance(flex, FlexOptions)
        and flex.merge_manifest is not None
    )
    if (
        state_root is None
        and (resume or shard_specified)
        and not merge_manifest_root_refusal
    ):
        raise ValueError(
            "run_mutation resume requires the caller's authoritative "
            "state_root; an ephemeral snapshot cannot own resume evidence"
        )
    if shard_specified and (shard_index is None or shard_count is None):
        raise ValueError("run_mutation requires both shard_index and shard_count")
    if shard_specified:
        select_mutation_shard((), index=shard_index, count=shard_count)
    # (B091/D-23, P7 A5) A rejudge selection with nothing to resume FROM
    # is not a meaningful request -- every candidate would execute anyway,
    # and the caller almost certainly meant `--resume --rejudge ...`
    # together.
    if (rejudge_ids or rejudge_outcomes) and not resume:
        raise ValueError(
            "run_mutation rejudge_ids/rejudge_outcomes requires resume=True "
            "-- rejudging drops matching records from the resume store "
            "before it is consulted, which is meaningless when nothing is "
            "being resumed"
        )
    if flex is not None:
        if not isinstance(flex, FlexOptions):
            raise ValueError("run_mutation flex must be FlexOptions or None")
        legacy_store_reads = bool(
            flex.carry_over_sources
            or flex.carry_forward_sources
            or flex.merge_sources
            or flex.shard_stores
        )
        if legacy_store_reads and not resume:
            raise ValueError(
                "run_mutation carry-over/carry-forward/merge requires resume=True"
            )
        if flex.merge_manifest is not None and not resume:
            raise ValueError("run_mutation --merge-manifest requires resume=True")
        if (legacy_store_reads or flex.selection_active) and state_root is None:
            raise ValueError(
                "run_mutation carry-over and candidate selection require the "
                "caller's authoritative state_root (carry-forward also requires it)"
            )
        if flex.merge_manifest is not None and state_root is None:
            raise ValueError(
                "run_mutation --merge-manifest requires the caller's "
                "authoritative state_root"
            )
        if flex.selection_active and selection_specified:
            raise ValueError(
                "run_mutation only/exclude candidates cannot be combined with "
                "a pilot candidate_selection"
            )
        if flex.shard_stores and (shard_specified or selection_specified):
            raise ValueError(
                "run_mutation shard_stores is the whole-lane merge; it cannot be "
                "combined with a shard or a pilot candidate_selection"
            )
        if flex.merge_manifest is not None and (
            shard_specified or selection_specified
        ):
            raise ValueError(
                "run_mutation merge_manifest is the whole-lane merge; it cannot "
                "be combined with a shard or a pilot candidate_selection"
            )
    unknown_rejudge_outcomes = rejudge_outcomes - frozenset(MUTATION_BUCKETS)
    if unknown_rejudge_outcomes:
        raise ValueError(
            f"run_mutation rejudge_outcomes named unknown bucket(s) "
            f"{sorted(unknown_rejudge_outcomes)}; expected a subset of "
            f"{sorted(MUTATION_BUCKETS)}"
        )
    if state_root is not None:
        if state_root_guard is not None:
            state_root_guard()
        if state_root_fd is None:
            Path(state_root).mkdir(parents=True, exist_ok=True)

    if baseline.outcome is not Outcome.PASS:
        return None

    # Built only once a candidate run is certain: a non-PASS baseline needs no
    # process runner (and a caller then has no use for a plan).
    if process_runner is _runner_module.default_process_runner:
        process_runner = ProcessTreeProcessRunner(
            sensitive_values=tuple(
                plan.env_effective[name]
                for name in plan.env_passthrough
                if name in plan.env_effective
            )
        )

    # (B091/D-23) Resolved HERE, once, the instant the baseline is known
    # good and before any candidate is submitted: the derived ceiling is a
    # function of the SAME measurement every candidate is about to be
    # compared against, and this is the earliest point that measurement
    # exists. `budget_per_candidate_derived` rides on the return value
    # (below) and on the `plan` progress event so a caller/reader learns the
    # resolved number from exactly one place, never a second computation of
    # it (B088's own "how a reader and a writer drift apart").
    measured_baseline_wall = baseline_wall_seconds(baseline)
    budget_per_candidate_derived = budget_per_candidate_auto
    if budget_per_candidate_auto:
        budget_per_candidate_seconds = auto_budget_per_candidate_seconds(
            measured_baseline_wall
        )

    # (B064) The stream is opened HERE only when nobody upstream owns one --
    # the direct library-caller path. `runner.run_lane` opens it for the
    # whole lane, so an `assay run --progress` sweep arrives with
    # *progress_stream* already carrying the R0/R1 phase records that
    # preceded it.
    progress_context = (
        progress_writer(Path(progress_artifact))
        if progress_artifact is not None and progress_stream is None
        else nullcontext[ProgressWriter | None](None)
    )

    with progress_context as raw_write:
        write_progress: ProgressStream | None = progress_stream
        if write_progress is None and raw_write is not None:
            write_progress = ProgressStream(raw_write, clock=clock)
            write_progress.emit_run_header(
                commit=prepared.spec.commit,
                lane=None,
                budget_per_candidate_s=budget_per_candidate_seconds,
                jobs=jobs,
                attribution=(
                    "overlap-conservative" if jobs > 1 else "serial"
                ),
            )
        if write_progress is not None:
            # (B091/D-23) The sweep's own first record: what the baseline
            # actually measured, and the per-candidate bound it produced --
            # printed here (never guessed ahead of the measurement) so a
            # reader of ONLY this stream, before ANY candidate line has
            # arrived, already knows what "no progress" is being judged
            # against. `derived: false` names an explicit duration or the
            # `"none"` opt-out equally; the value each of those two carries
            # in `budget_per_candidate_s` -- a real number, or `None` for
            # `"none"` -- already tells the two apart.
            write_progress(
                {
                    "event": "plan",
                    "baseline_s": round(measured_baseline_wall, 3),
                    "budget_per_candidate_s": budget_per_candidate_seconds,
                    "derived": budget_per_candidate_derived,
                    # (B091/RW-36) so a reader of ONLY this stream, before
                    # any candidate line has arrived, already knows whether
                    # the os._exit wrapper/hung detection ran for this lane
                    # and why -- the same three fields judgment.r2.liveness
                    # carries on the verdict, from the same computation.
                    "liveness": {
                        "active": liveness_active,
                        "reason": liveness_reason,
                        "plugin": liveness_plugin,
                    },
                    # (B091/D-23, P7 A4) The measurement `expect_next_
                    # event_within_s` (LivenessRunner's own idle threshold,
                    # already governing every candidate by the time this
                    # line is written) was DERIVED from -- `None`/`None` for
                    # every liveness-off lane, matching the `liveness` block
                    # just above.
                    #
                    # (B091 round-1 B2) `slowest_test_s` keeps its name AND
                    # its exact pre-B2 meaning -- the slowest `call`-phase
                    # duration -- so no consumer reading it finds the key
                    # silently measuring something else. What CHANGED is
                    # that it is no longer the input to the bound;
                    # `worst_gap_s` is, and it is disclosed beside it.
                    "slowest_test_s": liveness_slowest_test_s,
                    "worst_gap_s": liveness_worst_gap_s,
                    "expect_next_event_within_s": liveness_expect_next_event_within_s,
                    "pre_first_event_within_s": liveness_pre_first_event_within_s,
                    "cpu_window_s": liveness_cpu_window_s,
                    "idle_floor_s": liveness_idle_floor_s,
                    # (P4/S3) WHICH BUDGET REGIME applied to this run, decided
                    # per run and stated on the stream a consumer actually
                    # tails: `armed` = the CPU/per-test progress bounds are
                    # the stop and `judge.mutation.budget_per_candidate` is
                    # advisory (one WARN when first exceeded); `binding` =
                    # the budget still ends a candidate, exactly as before.
                    "progress_bound": {
                        "armed": liveness_watch_armed,
                        "reason": (
                            liveness_watch_reason
                            if liveness_watch_reason is not None
                            else (
                                "armed"
                                if liveness_watch_armed
                                else "liveness-not-injected"
                            )
                        ),
                        "cpu_without_progress_s": (
                            liveness_cpu_without_progress_bound_s
                            if liveness_watch_armed
                            else None
                        ),
                        "per_test_baseline_tests": (
                            len(liveness_per_test_baseline_s)
                            if liveness_watch_armed
                            else None
                        ),
                        "budget_per_candidate_role": (
                            "advisory" if liveness_watch_armed else "binding"
                        ),
                    },
                    "cold_witness": cold_witness,
                    "r2_baseline_s": r2_baseline_s,
                }
            )
            if liveness_baseline_events_path is not None:
                # (B091/D-23, P7 A4, RW-33) "test events reach the progress
                # stream for the BASELINE only" -- every candidate's own
                # test events stay in that candidate's own side file,
                # summarised only as `tests_completed` on its `candidate`
                # record below, never forwarded one-by-one (a `jobs`-way
                # concurrent sweep would otherwise interleave N candidates'
                # worth of `test` events with nothing on the wire to tell
                # them apart). Right after `plan`, before any candidate
                # line, so a reader already has the baseline's own per-test
                # detail before judging any mutant against it.
                for test_event, phase_durations in zip(
                    liveness.baseline_test_events(liveness_baseline_events_path),
                    liveness.baseline_phase_durations(liveness_baseline_events_path),
                ):
                    write_progress(
                        {
                            "event": "test",
                            "phase": "baseline",
                            **test_event,
                            **phase_durations,
                        }
                    )
        # (B091 round-1 B3) `resolve_run_cwd` travels the SAME lazy-import-
        # then-parameter path `execute_plan` already does (a module-level
        # `from .runner import ...` is circular -- `runner` imports
        # `mutation` at module level), so `_run_one` can re-root the
        # candidate's `cwd` through THE one join rather than a seventh
        # hand-rederivation of `project_root / lane.cwd` (A-367).
        from .runner import execute_plan, resolve_run_cwd

        collected = collect_mutation_sites(
            targets,
            adapter=adapter,
            operators=operators,
            limit=max_mutants + 1,
            context_sources=context_sources,
            deadline_check=(
                deadline.remaining if deadline is not None else None
            ),
        )
        if expected_plan_sha256 is not None:
            if (
                not isinstance(expected_plan_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256) is None
            ):
                raise CampaignPlanMismatchError(
                    "campaign deadline carries an invalid plan_sha256 for this lane"
                )
            if collected == UNSUPPORTED:
                raise CampaignPlanMismatchError(
                    "campaign deadline binds an R2 plan, but the current adapter "
                    "cannot discover that plan"
                )
            observed_plan_sha256 = plan_sha256(
                [candidate_id(job) for job in collected]
            )
            if observed_plan_sha256 != expected_plan_sha256:
                raise CampaignPlanMismatchError(
                    "the full mutation plan differs from the campaign deadline: "
                    f"expected {expected_plan_sha256}, observed {observed_plan_sha256}"
                )
        # (Round-1 N4) Each of the three returns below ends the sweep without
        # running one candidate, and each used to emit NOTHING. Under the
        # lane path `verdict_written` still terminated the stream, but a
        # DIRECT `run_mutation` library caller has no `verdict_written` at
        # all -- so those runs were indistinguishable from a process that
        # died mid-sweep, which is the exact question `end` was added to
        # answer. `end` is now the sweep's terminal on every path out, and
        # `reason` says which one: `null` for a sweep that actually ran.
        def _end(
            buckets: Mapping[str, int],
            *,
            reason: str | None,
            total: int,
            extra: Mapping[str, Any] | None = None,
            budget_exceeded_unstarted: int = 0,
        ) -> None:
            """The sweep's terminal record.

            **(B524) `buckets.budget_exceeded` is NOT one fact.** The bucket
            holds two different situations -- a candidate that got a worker
            and then hit its own or the lane's deadline, and a candidate the
            sweep never reached because submission had already stopped -- and
            the remedy differs: one is a measurement that ran out of time,
            the other is work that was never attempted. CMRu's s4 attempt A
            read `budget_exceeded = 23` off the terminal record with ONE
            candidate that had started; the other 22 had not. The bucket
            keeps its closed verdict vocabulary (the schema,
            `judge_mutation` and `verify` are unchanged); only this diagnostic
            record gains the split, as two ADDITIVE keys a reader that does
            not know them can ignore.

            **`dispatched` is the honest word, not `attempted`.** A
            candidate killed by the lane deadline is dispatched but
            deliberately UNCLASSIFIED (`_run_attempt` raises rather than
            record an outcome, so it is retried on resume) and its verdict
            record therefore still carries not-started process-tree
            evidence. "Dispatched" is the fact both statements agree on;
            "attempted" would be a claim the candidate's own record denies.
            """
            dispatched, unstarted = _budget_exceeded_split(
                buckets.get("budget_exceeded", 0), budget_exceeded_unstarted
            )
            if write_progress is not None:
                write_progress(
                    {
                        "event": "end",
                        "candidate_total": total,
                        "buckets": dict(buckets),
                        "reason": reason,
                        "budget_exceeded_dispatched": dispatched,
                        "budget_exceeded_unstarted": unstarted,
                        **(dict(extra) if extra else {}),
                    }
                )

        _no_buckets = {name: 0 for name in MUTATION_BUCKETS}
        if collected == UNSUPPORTED:
            _end(_no_buckets, reason="unsupported", total=0)
            return UNSUPPORTED

        job_list = collected
        candidate_count = len(job_list)
        if candidate_count > max_mutants:
            _end(_no_buckets, reason="over_candidate_cap", total=candidate_count)
            return Mutation(candidate_count=candidate_count, total=0)
        total = candidate_count
        if total == 0:
            # A source edit can remove the last mutation site. Its former id
            # is still invalid input; do not turn the request into NO_MUTANTS.
            if selection_specified:
                raise InvalidCandidateSelectionError(
                    "candidate selection contains IDs absent from the current mutation plan: "
                    + ", ".join(sorted(candidate_selection or ()))
                )
            if rejudge_ids:
                _reject_unknown_rejudge_ids(rejudge_ids, set())
            _end(_no_buckets, reason="no_candidates", total=0)
            return Mutation(candidate_count=0, total=0, candidate_ids=())

        # C23: discovery → campaign digest (full list) → ledger placement → selection/shard → resume.
        current_ids = [candidate_id(job) for job in job_list]
        if selection_specified:
            assert candidate_selection is not None
            unknown = candidate_selection - set(current_ids)
            if unknown:
                raise InvalidCandidateSelectionError(
                    "candidate selection contains IDs absent from the current mutation plan: "
                    + ", ".join(sorted(unknown))
                )
            selected_indices = [
                index for index, identity in enumerate(current_ids)
                if identity in candidate_selection
            ]
            selection_digest = plan_sha256(
                [current_ids[index] for index in selected_indices]
            )
            if (
                expected_selection_sha256 is not None
                and selection_digest != expected_selection_sha256
            ):
                raise InvalidCandidateSelectionError(
                    "candidate selection digest differs from the planned selection: "
                    f"expected {expected_selection_sha256}, observed {selection_digest}"
                )
            total = len(selected_indices)
        else:
            selected_indices = list(range(total))
        if shard_specified:
            assert shard_index is not None and shard_count is not None
            selected_indices = select_mutation_shard(
                current_ids,
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
        # (B088) ONE judge identity for this whole sweep, resolved once here
        # and handed to BOTH sides -- the reader below and the writer inside
        # `_execute_mutation_jobs`. Two independent derivations of the same
        # digest is exactly how a reader and a writer drift apart, and a
        # drifted judge identity fails in the safe direction (never resumes)
        # loudly enough to look like a different bug entirely. Computed only
        # when a store is in play: a sweep with no `state_root` neither reads
        # nor writes a record, and hashing the whole judged tree for it would
        # be pure cost.
        # (B529) Execution-resource settings of the loaded lane file and its
        # sibling run-gate.toml are projected out of the tree digest; their
        # values reach `resource_sha256` instead. Only when the caller named
        # its lane file (the CLI always does): a direct library caller keeps
        # the legacy whole-tree digest.
        projection: _judge_projection.JudgeProjection | None = None
        if (
            state_root is not None
            and flex is not None
            and flex.lane_file_name is not None
        ):

            def _read_config(path: PurePosixPath) -> bytes | None:
                if not prepared.has_regular_file(path):
                    return None
                return prepared.read_regular_file(
                    path,
                    timeout=deadline.remaining() if deadline is not None else 60.0,
                )

            projection = _judge_projection.config_projection(
                _read_config,
                project_prefix=plan.project_prefix,
                lane_file_name=flex.lane_file_name,
                jobs=jobs,
                memory_budget_bytes=(
                    schedule.memory_budget_bytes if schedule is not None else None
                ),
            )
        judge_components: dict[str, Any] = {}
        judge = (
            judge_sha256(
                tree_sha256=(
                    prepared.tree_sha256_projected(
                        identity_exclude=identity_exclude,
                        content_overrides=projection.content_overrides,
                    )
                    if projection is not None
                    else prepared.tree_sha256
                    if identity_exclude is None
                    else prepared.tree_sha256_for_identity_exclude(identity_exclude)
                ),
                plan=plan,
                link_paths=prepared.spec.snapshot_policy.link_paths,
                tool_version=_tool_version(),
                cold_witness_kills=cold_witness,
                r2_transform=("assay-r2-pytest-nocov/1" if cold_witness else None),
                r2_collection_sha256=(
                    r2_facts.collection_sha256 if cold_witness and r2_facts else None
                ),
                r2_hook_fingerprint_sha256=(
                    r2_facts.hook_fingerprint_sha256 if cold_witness and r2_facts else None
                ),
                runtime_fingerprint_sha256=(
                    r2_facts.runtime_fingerprint_sha256 if cold_witness and r2_facts else None
                ),
                coverage_hook_fingerprint_sha256=(
                    coverage_facts.hook_fingerprint_sha256
                    if cold_witness and coverage_facts
                    else None
                ),
                coverage_runtime_fingerprint_sha256=(
                    coverage_facts.runtime_fingerprint_sha256
                    if cold_witness and coverage_facts
                    else None
                ),
                equivalence_ledger_sha256=equivalence_ledger_sha256,
                candidate_fail_fast=candidate_fail_fast,
                _component_digests=judge_components,
            )
            if state_root is not None
            else None
        )
        if shard_specified and judge is not None and flex is not None:
            assert state_root is not None and shard_index is not None
            assert shard_count is not None
            _write_shard_manifest(
                Path(state_root),
                state_root_fd=state_root_fd,
                shard_index=shard_index,
                shard_count=shard_count,
                judge=judge,
                shard_ids=[candidate_id(job) for job in selected_jobs],
            )
        record_context = RecordContext(
            cold_witness=cold_witness,
            equivalence_artifact=equivalence_artifact,
            baseline_equivalence_sha256=(
                hashlib.sha256(baseline_equivalence).hexdigest()
                if baseline_equivalence is not None
                else None
            ),
            kill_signal_artifact=kill_signal_artifact,
        )
        combined = mutation_store.combine(
            () if flex is None else flex.store_sources(),
            mutation_store.CombineRequest(
                jobs=selected_jobs,
                judge=judge,
                state_root=Path(state_root) if state_root is not None else None,
                state_root_fd=state_root_fd,
                state_root_guard=state_root_guard,
                campaign_deadline_sha256=campaign_deadline_sha256,
                context=record_context,
                resume=resume,
                rejudge_ids=rejudge_ids,
                rejudge_outcomes=rejudge_outcomes,
                resource_sha256=(
                    projection.resource_sha256 if projection is not None else None
                ),
                carry_over_reason=None if flex is None else flex.carry_over_reason,
                clock=clock,
                deadline_check=deadline.remaining,
                write_progress=write_progress,
                total=total,
            ),
        )
        resumed_records = list(combined.records)
        rejudged_candidate_ids = set(combined.rejudged_candidate_ids)
        rejected_total = combined.rejected_total
        rejudged_total = combined.rejudged_total
        resource_rejudged_total = combined.resource_rejudged_total
        promoted_total = combined.promoted_total
        imported_total = combined.imported_total
        locus_unavailable_total = combined.locus_unavailable_total
        judge_mismatch_diagnostic: dict[str, Any] | None = None
        if combined.rejected_judges and judge is not None and state_root is not None:
            previous_components = {}
            if len(combined.rejected_judges) <= MAX_JUDGE_MISMATCH_GENERATIONS:
                previous_components = {
                    previous_judge: mutation_store.read_judge_components(
                        Path(state_root),
                        judge=previous_judge,
                        state_root_fd=state_root_fd,
                    )
                    for previous_judge in sorted(combined.rejected_judges)
                }
            judge_mismatch_diagnostic = _judge_component_differences(
                judge_components, previous_components
            )
        if resume:
            resumed_ids = {record["candidate_id"] for record in resumed_records}
            pending_jobs = tuple(
                job for job in selected_jobs if candidate_id(job) not in resumed_ids
            )
            # (B088) Emitted when anything was resumed, REFUSED, or
            # REJUDGED. Before A5, this fired only on a successful resume
            # or a refusal, so a run that rejudged every one of its records
            # (a real, intentional event) would have looked identical to
            # one where nothing happened at all.
            if write_progress is not None and (
                resumed_records
                or rejected_total
                or rejudged_total
                or resource_rejudged_total
            ):
                resume_event = {
                    "candidate_total": total,
                    "event": "resume",
                    "resumed_total": len(resumed_records),
                    "rejected_total": rejected_total,
                    "rejudged_total": rejudged_total,
                    # (B529/B527/B530) additive diagnostics; `resumed_total`
                    # includes promoted and carried-over records.
                    "resource_rejudged_total": resource_rejudged_total,
                    "promoted_total": promoted_total,
                    "imported_total": imported_total,
                    "locus_unavailable_total": locus_unavailable_total,
                }
                if judge_mismatch_diagnostic is not None:
                    resume_event.update(
                        {
                            "judge_mismatch_components_available": (
                                judge_mismatch_diagnostic["components_available"]
                            ),
                            "judge_mismatch_components": (
                                judge_mismatch_diagnostic["components"]
                            ),
                            "judge_mismatch_passthrough_names": (
                                judge_mismatch_diagnostic["passthrough_names"]
                            ),
                            "judge_mismatch_passthrough_name_candidates": (
                                judge_mismatch_diagnostic[
                                    "passthrough_name_candidates"
                                ]
                            ),
                            "judge_mismatch_passthrough_name_diagnostic": (
                                judge_mismatch_diagnostic[
                                    "passthrough_name_diagnostic"
                                ]
                            ),
                        }
                    )
                write_progress(resume_event)
        else:
            pending_jobs = selected_jobs
        if judge_mismatch_diagnostic is not None:
            if judge_mismatch_diagnostic["components_available"]:
                changed = ", ".join(judge_mismatch_diagnostic["components"])
                if not changed:
                    changed = "no differing component was identified"
                passthrough_name_diagnostic = judge_mismatch_diagnostic[
                    "passthrough_name_diagnostic"
                ]
                passthrough_detail = (
                    f"; {passthrough_name_diagnostic}"
                    if passthrough_name_diagnostic
                    else ""
                )
                sys.stderr.write(
                    "assay: resume found a judge mismatch; differing components: "
                    f"{changed}{passthrough_detail}\n"
                )
            else:
                sys.stderr.write(
                    "assay: resume found a judge mismatch; judge component "
                    "digests are unavailable\n"
                )

        # (B531) `--only-candidates`/`--exclude-candidates`: execute a subset of
        # what is still pending. A left-out candidate that no resumed or
        # carried-over record covers is reported as never started, so the
        # claim cannot be a PASS and names how many were left out.
        unselected_jobs: tuple[MutantJob, ...] = ()
        if flex is not None and flex.selection_active:
            known = set(current_ids)
            named = (flex.only_candidates or frozenset()) | (
                flex.exclude_candidates or frozenset()
            )
            unknown_selection = named - known
            if unknown_selection:
                raise InvalidCandidateSelectionError(
                    "--only-candidates/--exclude-candidates name IDs absent from "
                    "the current mutation plan: "
                    + ", ".join(sorted(unknown_selection)[:20])
                    + (" ..." if len(unknown_selection) > 20 else "")
                )

            def _chosen(job: MutantJob) -> bool:
                identity = candidate_id(job)
                if flex.only_candidates is not None and identity not in flex.only_candidates:
                    return False
                return not (
                    flex.exclude_candidates is not None
                    and identity in flex.exclude_candidates
                )

            unselected_jobs = tuple(job for job in pending_jobs if not _chosen(job))
            pending_jobs = tuple(job for job in pending_jobs if _chosen(job))
            if write_progress is not None:
                write_progress(
                    {
                        "event": "selection",
                        "candidate_total": total,
                        "only_total": (
                            None
                            if flex.only_candidates is None
                            else len(flex.only_candidates)
                        ),
                        "exclude_total": (
                            None
                            if flex.exclude_candidates is None
                            else len(flex.exclude_candidates)
                        ),
                        "executing_total": len(pending_jobs),
                        "unselected_total": len(unselected_jobs),
                    }
                )

        if write_progress is not None:
            # (B065) The `run` header is emitted at stream OPEN and carries
            # `candidate_total: null`, because the total cannot be known
            # before a snapshot exists and the sites have been collected --
            # see `ProgressStream.emit_run_header`. This is where it IS
            # known, so this is where it is announced, together with what
            # sharding and resume did to it: `selected_total` is this
            # shard's assignment domain and `pending_total` is what actually
            # gets executed, so a reader computes rate and ETA against the
            # work that remains rather than against the whole plan.
            write_progress(
                {
                    "event": "candidates",
                    "candidate_total": total,
                    "selected_total": len(selected_jobs),
                    "pending_total": len(pending_jobs),
                    "commit": prepared.spec.commit,
                    **({"judge_sha256": judge} if judge is not None else {}),
                    **(
                        {"resource_sha256": projection.resource_sha256}
                        if projection is not None
                        else {}
                    ),
                    **({"imported_total": imported_total} if imported_total else {}),
                    **(
                        {"unselected_total": len(unselected_jobs)}
                        if flex is not None and flex.selection_active
                        else {}
                    ),
                    **(
                        {"selection_sha256": selection_digest}
                        if selection_specified
                        else {}
                    ),
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

        # Keep the executor's partial-campaign fact separate from its outcome
        # buckets: a terminal per-candidate timeout belongs in
        # `budget_exceeded` without making the campaign incomplete.
        result_payload, complete_campaign = _execute_mutation_jobs(
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
            resolve_run_cwd=resolve_run_cwd,
            write_progress=write_progress,
            total=len(pending_jobs),
            candidate_count=len(pending_jobs),
            state_root=state_root,
            state_root_fd=state_root_fd,
            state_root_guard=state_root_guard,
            judge=judge,
            resource_sha256=(
                projection.resource_sha256 if projection is not None else None
            ),
            liveness_watch_armed=liveness_watch_armed,
            liveness_events_dir=liveness_events_dir,
            liveness_plugin_path=(
                Path(liveness_plugin) if liveness_plugin is not None else None
            ),
            reuse_witnesses={
                candidate: witness
                for candidate, witness in (reuse_witnesses or {}).items()
                if candidate not in rejudge_ids
                and candidate not in rejudged_candidate_ids
            },
            campaign_deadline_sha256=campaign_deadline_sha256,
            cold_witness=cold_witness,
            declared_plan=declared_plan,
            r2_facts=r2_facts,
            coverage_facts=coverage_facts,
            manifest_node_ids=manifest_node_ids,
            schedule=schedule,
            candidate_fail_fast=candidate_fail_fast,
        )

        # The component sidecar is diagnostic only. Remember whether this
        # invocation has any durable current-judge records before adding
        # unselected, never-started outcomes below; the sidecar belongs in the
        # same store but outside the candidate and generation namespaces.
        has_judge_records = bool(resumed_records) or any(
            getattr(result_payload, bucket) for bucket in MUTATION_BUCKETS
        )

        if resumed_records:
            executed_telemetry = result_payload.r2_telemetry
            result_payload = merge_mutations(result_payload, resumed_records)
            result_payload = _dataclass_replace(
                result_payload,
                r2_telemetry=_fold_resumed_telemetry(
                    executed_telemetry, resumed_records
                ),
            )
        if complete_campaign:
            # `_execute_mutation_jobs` has returned by now; resume merging can
            # still take time and must remain inside the persisted deadline.
            deadline.remaining()
        if write_progress is not None and resumed_records:
            write_progress({"event": "resume_merged", "resumed_total": len(resumed_records)})
            if complete_campaign:
                deadline.remaining()
        if unselected_jobs:
            result_payload = _append_unselected(result_payload, unselected_jobs)
        if selected_jobs or not shard_specified:
            #
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
        else:
            # An empty shard is a submitted scope with no assignments. Its
            # empty inventory is distinct from the pre-submission cap
            # sentinel, which returns before reaching this point.
            result_payload = _dataclass_replace(result_payload, candidate_ids=())
        if budget_per_candidate_derived:
            # (B091/D-23) Rides on the return value exactly like
            # `candidate_ids` above: the ONE computation, from measured
            # baseline_wall_seconds up in this same function, read back by
            # the caller (`assay.runner._build_judgment_r2`) rather than
            # recomputed. `None` -- the dataclass default -- for every other
            # path (explicit duration, `"none"`, or a legacy caller that
            # passed neither new kwarg at all), matching `budget_per_
            # candidate_s: null` on the `plan` progress event above.
            result_payload = _dataclass_replace(
                result_payload,
                budget_per_candidate_derived_s=budget_per_candidate_seconds,
            )
        if complete_campaign:
            deadline.remaining()
        if write_progress is not None:
            # (B065) The sweep's own terminal. Without it a reader cannot
            # tell a finished sweep from one whose process died between two
            # candidates -- both look like "records stopped arriving" -- and
            # the whole point of the artifact is to answer that question
            # WITHOUT reading the verdict. The counts are the same
            # vocabulary the verdict's own `judgment.r2` uses, so a reader
            # that later sees the verdict finds them agreeing rather than
            # having to reconcile two spellings.
            #
            # `reason: null` -- a sweep that actually ran. The three early
            # returns above name their own reason instead (round-1 N4).
            _end(
                {
                    name: len(getattr(result_payload, name))
                    for name in MUTATION_BUCKETS
                },
                reason=None,
                total=total,
                extra=(
                    {"unselected_total": len(unselected_jobs)}
                    if flex is not None and flex.selection_active
                    else None
                ),
                # (B524) Read back from the sweep's own telemetry rather than
                # recomputed here: `_execute_mutation_jobs` is the only place
                # that can distinguish "dispatched, then hit the deadline"
                # from "still in the queue when submission stopped". A
                # candidate left out by `--only-candidates`/`--exclude` was
                # never dispatched either, so it counts as unstarted here
                # too -- the same sentence, two causes.
                budget_exceeded_unstarted=(
                    int(
                        (result_payload.r2_telemetry or {}).get(
                            "budget_exceeded_not_started"
                        )
                        or 0
                    )
                    # (B531) A candidate left out by a selection was never
                    # dispatched either, so it is the same sentence as a
                    # campaign-deadline remainder -- counted here, not in the
                    # engine, because only this frame knows the selection.
                    + len(unselected_jobs)
                ),
            )
            if complete_campaign:
                deadline.remaining()
        if state_root is not None and judge is not None and has_judge_records:
            try:
                mutation_store.write_judge_components(
                    Path(state_root),
                    judge=judge,
                    components=judge_components,
                    state_root_fd=state_root_fd,
                )
            except Exception:
                # Diagnostic metadata must never affect record durability,
                # rollback, deadline checks, or the campaign result.
                pass
        return result_payload


# The private implementation remains available to executor-focused unit tests;
# runtime and consumer code must use the validated public entry point.
_run_mutation_impl = run_mutation.__wrapped__


def _read_schedule_history(
    state_root: Path | str | None, state_root_fd: int | None
) -> tuple[dict[str, _schedule.HistoryEntry], str | None]:
    """(B207) The previous campaign's per-candidate measurements, or an empty
    history. Predictions only order work, so an unreadable side file is
    reported and ignored, never raised."""
    if state_root is None:
        return {}, None
    name = _schedule.HISTORY_FILE_NAME
    try:
        if state_root_fd is None:
            raw = safeio.read_bounded_input(
                Path(state_root), name, limit=_schedule.HISTORY_LIMIT_BYTES
            )
        else:
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
                    dir_fd=state_root_fd,
                )
            except FileNotFoundError:
                return {}, None
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    return {}, "schedule history is not a regular file"
                if info.st_size > _schedule.HISTORY_LIMIT_BYTES:
                    return {}, "schedule history exceeds its size limit"
                raw = stream.read()
    except (AssayError, OSError) as exc:
        return {}, f"schedule history is unreadable: {exc}"
    return _schedule.parse_history(raw)


def _write_state_side_file(
    state_root: Path, state_root_fd: int | None, name: str, payload: bytes
) -> None:
    """Atomically replace one non-record file in the state directory."""
    temporary_name = f".state-{os.getpid()}-{os.urandom(8).hex()}.tmp"
    if state_root_fd is None:
        Path(state_root).mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            Path(state_root) / temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    else:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=state_root_fd,
        )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            _durable.fsync(stream.fileno())
        if state_root_fd is None:
            os.replace(Path(state_root) / temporary_name, Path(state_root) / name)
        else:
            os.replace(
                temporary_name, name, src_dir_fd=state_root_fd, dst_dir_fd=state_root_fd
            )
    except BaseException:
        try:
            if state_root_fd is None:
                os.unlink(Path(state_root) / temporary_name)
            else:
                os.unlink(temporary_name, dir_fd=state_root_fd)
        except FileNotFoundError:
            pass
        raise


def _persist_schedule_side_files(
    state_root: Path | str | None,
    state_root_fd: int | None,
    *,
    history: Mapping[str, _schedule.HistoryEntry],
    updates: Mapping[str, _schedule.HistoryEntry],
    scheduler: _schedule.TemScheduler | None,
    errors: Mapping[int, Mapping[str, Any]],
    history_reason: str | None,
) -> None:
    """(B207) Write ``.schedule-history.v1`` (what this run measured, merged
    over the previous history) and ``schedule-summary.v1`` (the run's policy and its
    predicted-versus-actual summary). Both are advisory side files: a failure
    to write one is an advisory on stderr, never a campaign failure."""
    if state_root is None:
        return
    root = Path(state_root)
    try:
        if updates:
            _write_state_side_file(
                root,
                state_root_fd,
                _schedule.HISTORY_FILE_NAME,
                _schedule.render_history({**history, **updates}),
            )
        if scheduler is not None:
            summary = scheduler.summary(errors=errors)
            summary["input_sha256"] = hashlib.sha256(
                _schedule.render_history(history)
            ).hexdigest()
            if history_reason is not None:
                summary["history_reason"] = history_reason
            _write_state_side_file(
                root,
                state_root_fd,
                _schedule.SCHEDULE_FILE_NAME,
                (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            )
    except OSError as exc:
        sys.stderr.write(
            f"Assay advisory: could not write the candidate schedule side files: {exc}\n"
        )


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
    #: (B091 round-1 B3) `runner.resolve_run_cwd` -- THE one join from a
    #: project root plus a `CommandPlan` to the directory that plan actually
    #: runs in (B043/A-367). Threaded in as a parameter for the same reason
    #: *execute_plan* is: `runner` imports this module at module level, so
    #: the reverse import can only be lazy, and the lazy import belongs at
    #: the ONE `run_mutation` seam rather than inside this per-candidate
    #: hot path. `_run_one` uses it to name the events side file the
    #: candidate's own `LivenessRunner` wrote, which is keyed on the
    #: RESOLVED `cwd` `execute_plan` hands the runner -- not on the
    #: pre-resolution `snapshot.project_root`, which is a different
    #: directory for every lane declaring `cwd`.
    resolve_run_cwd: Callable[[Path, CommandPlan], Path],
    total: int,
    candidate_count: int,
    state_root: Path | str | None = None,
    state_root_fd: int | None = None,
    state_root_guard: Callable[[], None] | None = None,
    #: (B088) This sweep's judge identity, resolved ONCE by the caller and
    #: written onto every record. Required whenever *state_root* is set --
    #: a record written without one would be indistinguishable from a
    #: pre-B088 record on the next read, except that its `schema_version`
    #: would claim it is new enough to be checkable.
    judge: str | None = None,
    #: (B529) the run's resource digest, stored on every new record.
    resource_sha256: str | None = None,
    #: (B091/D-23, P7 A4) The lane's own persistent `.assay/liveness/
    #: candidates/` directory, passed straight through from `run_mutation`
    #: -- `None` for every non-liveness lane, in which case `_run_one`
    #: never touches `assay.liveness` at all.
    liveness_events_dir: Path | None = None,
    #: (P4/S3) The arming decision, threaded to the ONE place that builds each
    #: candidate's `command_deadline`. Armed: the per-candidate budget no
    #: longer caps that deadline (the runner holds it as advisory) and only the
    #: lane deadline remains. Not armed: the unchanged pre-P4 arithmetic.
    liveness_watch_armed: bool = False,
    liveness_plugin_path: Path | None = None,
    reuse_witnesses: Mapping[str, tuple[str, str]] | None = None,
    campaign_deadline_sha256: str | None = None,
    cold_witness: bool = False,
    declared_plan: CommandPlan | None = None,
    r2_facts: ReceiptFacts | None = None,
    coverage_facts: ReceiptFacts | None = None,
    manifest_node_ids: Sequence[str] = (),
    schedule: _schedule.ScheduleOptions | None = None,
    candidate_fail_fast: bool = False,
) -> tuple[Mutation, bool]:

    if job_list:
        _read_candidate_resource_counters()

    witness_supported = supports_sequential_pytest(
        plan.argv_effective, env=plan.env_effective
    )
    declared_supported = bool(
        cold_witness
        and declared_plan is not None
        and supports_sequential_pytest(
            declared_plan.argv_effective, env=declared_plan.env_effective
        )
    )
    witness_temp = (
        tempfile.TemporaryDirectory(prefix="assay-mutation-witness-")
        if witness_supported and (not cold_witness or declared_supported)
        else None
    )
    witness_injection = (
        _inject_witness_plugin(
            plan,
            plugin_dir=Path(witness_temp.name),
            liveness_plugin_path=liveness_plugin_path,
        )
        if witness_temp is not None
        else None
    )
    witness_capture_active = bool(
        witness_injection is not None and witness_injection.active
    )
    declared_injection = (
        _inject_witness_plugin(
            declared_plan,
            plugin_dir=Path(witness_temp.name),
            liveness_plugin_path=liveness_plugin_path,
        )
        if cold_witness
        and declared_plan is not None
        and witness_temp is not None
        else None
    )
    if cold_witness and (
        not witness_capture_active
        or declared_injection is None
        or not declared_injection.active
    ):
        raise R2CommandProofError(
            "cold witness: the candidate command could not install its trusted "
            "receipt plugin"
        )
    candidate_reuse = dict(reuse_witnesses or {}) if witness_capture_active else {}

    def _candidate_plan(base: CommandPlan) -> CommandPlan:
        # (WP1) Applied at execution, AFTER the witness decisions above were
        # made on the declared command: the receipt plugin is sound under -x
        # (a failed call ends the started prefix), and the baseline never
        # reaches here.
        return candidate_fail_fast_plan(base) if candidate_fail_fast else base

    attempt_receipts: dict[tuple[int, str], Mapping[str, Any] | None] = {}
    worker_index_lock = threading.Lock()
    worker_indices: dict[int, int] = {}
    candidate_workers: dict[int, int] = {}
    # B206: the cgprofile observer is the only source of per-candidate numbers.
    # Run-wide policy (ask contract support once, stop asking after three
    # consecutive failures) lives here, in the coordinator.
    profile_collector = observer_windows.ProfileCollector(
        socket_path=os.environ.get(observer_windows.OBSERVER_SOCKET_ENV) or None,
        jobs=jobs,
    )

    def _worker_index_for_current_thread() -> int:
        identity = threading.get_ident()
        with worker_index_lock:
            index = worker_indices.get(identity)
            if index is None:
                index = len(worker_indices)
                worker_indices[identity] = index
            return index

    window_host_run: Callable[..., CommandResult] | None = None
    from . import _window_host
    from . import runner as runner_module
    coordinator_thread_id = threading.get_ident()

    if (
        execute_plan is runner_module.execute_plan
        and _window_host.can_host(process_runner, clock)
    ):
        window_host_run = _window_host.run_window
    limit_broker: _limit_ledger.LimitBoundaryBroker | None = None
    limit_ledger: _limit_ledger.LimitLedger | None = None
    if jobs > 1:
        if prepared is None or not prepared.cheap_clone:
            raise LaneConfigError(
                "native R2 --jobs greater than 1 requires a cheap_clone "
                "snapshot workspace; set up the supported btrfs CoW workspace "
                "or run with --jobs 1"
            )
        if window_host_run is None:
            raise LaneConfigError(
                "native R2 --jobs greater than 1 requires the isolated PR1a "
                "window host; the configured runner or clock cannot be hosted"
            )
        limit_ledger = _limit_ledger.LimitLedger(
            _read_candidate_resource_counters_during_campaign
        )
        limit_broker = _limit_ledger.LimitBoundaryBroker(limit_ledger)

    def _start_limit_window() -> int | None:
        if limit_ledger is None or limit_broker is None:
            return None
        if threading.get_ident() == coordinator_thread_id:
            return limit_ledger.snapshot()
        return limit_broker.start_window()

    def _end_limit_window(start_sequence: int) -> _limit_ledger.LimitWindow:
        assert limit_ledger is not None and limit_broker is not None
        if threading.get_ident() == coordinator_thread_id:
            end_sequence = limit_ledger.snapshot()
            return limit_ledger.limits_for(start_sequence, end_sequence)
        return limit_broker.end_window(start_sequence)
    bucket_for_run = partial(
        mutation_store._bucket_for_run,
        equivalence_artifact=equivalence_artifact,
        baseline_equivalence=baseline_equivalence,
        kill_signal_artifact=kill_signal_artifact,
    )

    def _run_attempt(
        index: int,
        *,
        target_node_id: str | None,
        prior_verdict_sha256: str | None,
        attempt_name: str,
        cold: bool = False,
        variant: Literal["r2", "declared"] = "r2",
    ) -> _MutantRun | None:
        job = job_list[index]
        original_bytes = job.original_text.encode("utf-8")
        replacement_bytes = job.site.apply(original_bytes)
        materialize_timeout = deadline.remaining()
        started_monotonic = time.monotonic()
        attempt_plan = _candidate_plan(plan)
        active_injection = witness_injection
        if cold_witness and variant == "declared":
            active_injection = declared_injection
        receipt: Mapping[str, Any] | None = None
        with prepared.materialize_replacement(
            path=PurePosixPath(job.path),
            expected=original_bytes,
            replacement=replacement_bytes,
            timeout=materialize_timeout,
        ) as snapshot:
            entered_monotonic = time.monotonic()
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

            def _close_attempt_reservations() -> None:
                if equivalence_reservation is not None:
                    equivalence_reservation.close()
                if kill_signal_reservation is not None:
                    kill_signal_reservation.close()

            try:
                lane_remaining = deadline.remaining()
                lane_bound = (
                    budget_per_candidate_seconds is None
                    or budget_per_candidate_seconds >= lane_remaining
                )
                command_deadline = lane_remaining
                # (P4/S3) ARMED: the per-candidate budget is advisory and the
                # runner holds it as such, so it must not shorten the command
                # deadline here -- a candidate that keeps making progress runs
                # until the LANE budget or a progress bound ends it. NOT
                # armed: the pre-P4 arithmetic, unchanged.
                if (
                    not liveness_watch_armed
                    and budget_per_candidate_seconds is not None
                    and budget_per_candidate_seconds < command_deadline
                ):
                    command_deadline = budget_per_candidate_seconds
                command_started_monotonic = time.monotonic()
                resource_limits_before: ResourceLimitCounters | None = None
                resource_limits_before_error: str | None = None
                window_start_sequence = _start_limit_window()
                if limit_broker is None:
                    (
                        resource_limits_before,
                        resource_limits_before_error,
                    ) = _read_candidate_resource_counters_during_campaign()
                execution_error: BaseException | None = None
                result: CommandResult | None = None
                resource_limits_after: ResourceLimitCounters | None = None
                resource_limits_after_error: str | None = None
                resource_limit_evidence: ResourceLimitEvidence | None = None
                resource_observation_reason: str | None = None
                resource_limit_changes: tuple[str, ...] = ()
                receipt_capture = None
                profile_slot: observer_windows.WindowSlot | None = None
                try:
                    if witness_capture_active and active_injection is not None:
                        receipt_capture = ReceiptCapture()
                        attempt_plan = _make_witness_attempt_plan(
                            _candidate_plan(active_injection.plan),
                            receipt_fd=receipt_capture.write_fd,
                            target_node_id=target_node_id,
                            cold=cold,
                        )
                    # B206: the candidate's observer window is opened by the
                    # process that spawns the root and its result comes back
                    # through the slot; a refused or absent observer leaves a
                    # NO_MEASUREMENT reason, never a different execution.
                    with observer_windows.activate(
                        profile_collector.config()
                    ) as profile_slot:
                        if window_host_run is None:
                            result = execute_plan(
                                attempt_plan,
                                cwd=snapshot.project_root,
                                timeout=command_deadline,
                                process_runner=process_runner,
                                clock=clock,
                            )
                        else:
                            result = window_host_run(
                                attempt_plan,
                                cwd=snapshot.project_root,
                                timeout=command_deadline,
                                process_runner=process_runner,
                                clock=clock,
                                **(
                                    {}
                                    if profile_slot is None or not profile_slot.active
                                    else {"profile": profile_slot}
                                ),
                            )
                except BaseException as exc:
                    execution_error = exc
                finally:
                    if profile_slot is not None:
                        profile_collector.record(profile_slot.result)
                    if limit_broker is None:
                        (
                            resource_limits_after,
                            resource_limits_after_error,
                        ) = _read_candidate_resource_counters_during_campaign()
                    else:
                        assert window_start_sequence is not None
                        limit_window = _end_limit_window(window_start_sequence)
                        resource_limit_evidence = limit_window.evidence
                        resource_observation_reason = limit_window.reason
                        resource_limit_changes = limit_window.changes
                    if receipt_capture is not None:
                        receipt = receipt_capture.finish()
                # A process-tree proof failure means this attempt's cleanup
                # is unknown. Do not let an unrelated cgroup event replace
                # that lane-fatal infrastructure failure with a candidate
                # attribution result.
                failure: BaseException | None = execution_error
                process_tree_failure = False
                while failure is not None:
                    if isinstance(failure, ProcessTreeError):
                        process_tree_failure = True
                        break
                    failure = failure.__cause__ or failure.__context__
                if process_tree_failure:
                    raise AssayError(
                        "native R2 process-tree cleanup could not prove an empty command window",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.EXEC_FAILED,
                    ) from execution_error

                # The pre-candidate capability probe remains fail-closed.
                # Once a candidate starts, however, resource windows are
                # telemetry: either missing endpoint or an uncomparable pair
                # records an unobserved window and leaves termination to WP1.
                if limit_broker is None:
                    if resource_limits_before is None or resource_limits_after is None:
                        reasons = []
                        if resource_limits_before_error is not None:
                            reasons.append(f"before: {resource_limits_before_error}")
                        if resource_limits_after_error is not None:
                            reasons.append(f"after: {resource_limits_after_error}")
                        resource_observation_reason = "; ".join(reasons)
                    else:
                        (
                            resource_limit_evidence,
                            resource_observation_reason,
                            resource_limit_changes,
                        ) = _candidate_resource_limit_evidence(
                            resource_limits_before, resource_limits_after
                        )
                if execution_error is not None:
                    if (
                        resource_limit_evidence is not None
                        and resource_limit_evidence.limit_hit
                        and target_node_id is None
                    ):
                        raise AssayError(
                            "cgroup resource-limit event during native R2 attempt; "
                            "refusing to classify this candidate",
                            outcome=Outcome.ERROR,
                            reason_code=ReasonCode.EXEC_FAILED,
                        ) from execution_error
                    if isinstance(
                        execution_error, liveness.LivenessNoProgressWallExpired
                    ):
                        raise _UnclassifiedLivenessWallStop(
                            f"native R2 candidate {candidate_id(job)} stopped "
                            f"after {execution_error.wall_seconds:.1f} wall "
                            f"seconds and {execution_error.eligible_seconds:.1f} "
                            f"eligible seconds since the last re-date (a "
                            f"completed event, session_finish or spawn; bound "
                            f"{execution_error.wall_bound_s:.1f}s in "
                            f"{'pressure-credited eligible time' if execution_error.pressure_credit_active else 'wall time before comparable pressure data'}); the attempt "
                            "is unclassified and will be retried on resume",
                            outcome=Outcome.BUDGET_EXCEEDED,
                            reason_code=ReasonCode.LANE_TIMEOUT,
                        ) from execution_error
                    raise execution_error
                assert result is not None
                if result.process_tree_evidence is None:
                    raise AssayError(
                        "native R2 command runner returned no process-tree proof; "
                        "refusing to classify the candidate",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.EXEC_FAILED,
                    )
            except BaseException:
                _close_attempt_reservations()
                raise
            if (
                lane_bound
                and result.outcome is Outcome.BUDGET_EXCEEDED
                and result.reason_code is ReasonCode.LANE_TIMEOUT
            ):
                _close_attempt_reservations()
                raise AssayError(
                    "native R2 candidate reached the lane or campaign deadline; "
                    "the attempt is unclassified and will be retried on resume",
                    outcome=Outcome.BUDGET_EXCEEDED,
                    reason_code=ReasonCode.LANE_TIMEOUT,
                )
            command_finished_monotonic = time.monotonic()
            elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
            # (B091/D-23, P7 A4) Read back HERE, while this candidate's own
            # snapshot root is still in scope -- the events file lives at
            # the lane's PERSISTENT `liveness_events_dir`, so it would
            # still be readable after this `with` block exits, but the
            # *path* to it is keyed by the execution `cwd`, which only this
            # scope still has.
            #
            # (B091 round-1 B3) That key is the RESOLVED run cwd -- what
            # `execute_plan` hands `LivenessRunner` (the writer) one line
            # above, i.e. `resolve_run_cwd(snapshot.project_root, plan)`,
            # NOT `snapshot.project_root` itself. The two coincide only
            # when the lane declares no `cwd`; on a lane that declares one
            # they are different directories, the reader missed the file
            # the writer wrote, and `count_test_events` returned `0` --
            # precisely the value this field's own contract (below, and
            # CONSUMERS' `candidate` row) defines as "the plugin ran and
            # genuinely saw no test". Routed through THE one join for the
            # same reason `runner.py`'s two other re-rootings are (A-367:
            # "agrees today" is not a property an edit to either side
            # preserves).
            tests_completed: int | None = None
            startup_seconds: dict[str, float | None] | None = None
            first_failing_test: str | None = None
            if liveness_events_dir is not None:
                candidate_events = liveness.candidate_events_path(
                    liveness_events_dir,
                    resolve_run_cwd(snapshot.project_root, plan),
                )
                tests_completed = liveness.count_test_events(candidate_events)
                startup_seconds = _startup_seconds(candidate_events)
                first_failing_test = liveness.first_failing_test(candidate_events)
            integrity_started_monotonic = time.monotonic()
            # P26/A-212: the ONE lane deadline IS forwarded here, so this
            # check's own Git children are bounded by the same budget as
            # every other lane-owned call. `remaining=None` would leave them
            # genuinely unbounded -- `git._run_bounded` then waits in
            # `selector.select(None)`/`proc.wait()` with no timeout -- so a
            # single hung `status`/`rev-parse` could outlive the entire lane.
            # If the deadline or termination request fires before this
            # integrity proof completes, propagate it: a completed command is
            # not a classified candidate until its snapshot is proven intact.
            equivalence_bytes: bytes | None = None
            kill_signal_text: str | None = None
            decode_error: AssayError | None = None
            # (B049/DA-D1, SF-4) `consume()` can now RAISE -- B049 gave it the
            # replaced-parent refusal -- and before this `try`/`finally` that
            # raise skipped both `close()` calls below, leaking
            # `kill_signal_reservation`'s parent descriptor. Bounded at one
            # descriptor per run (the raise is fatal to the whole R2 claim),
            # so it was never a leak that grows; it is still a descriptor this
            # function opened and owes.
            try:
                dirt = _snapshot_left_dirt(
                    job, snapshot, remaining=deadline.remaining
                )
                # The integrity helper's last Git child can return just as
                # the lane expires or termination arrives. Recheck at the
                # classification boundary before reading artifacts or
                # allowing this candidate to acquire a bucket.
                deadline.remaining()
                integrity_finished_monotonic = time.monotonic()
                if dirt is None:
                    if equivalence_reservation is not None:
                        equivalence_bytes = equivalence_reservation.consume()
                    if kill_signal_reservation is not None:
                        try:
                            kill_signal_text = _read_kill_signal(
                                kill_signal_reservation
                            )
                        except AssayError as exc:
                            decode_error = exc
            finally:
                if equivalence_reservation is not None:
                    equivalence_reservation.close()
                if kill_signal_reservation is not None:
                    kill_signal_reservation.close()
            if dirt is not None:
                raise dirt
            if decode_error is not None:
                raise decode_error
        # Snapshot teardown owns filesystem cleanup and may outlive the last
        # in-snapshot deadline check. A result is not eligible for a terminal
        # bucket until cleanup has completed within the lane budget.
        deadline.remaining()
        teardown_finished_monotonic = time.monotonic()
        run = _MutantRun(
            result=result,
            equivalence_bytes=equivalence_bytes,
            kill_signal=kill_signal_text,
            elapsed_seconds=elapsed_seconds,
            tests_completed=tests_completed,
            liveness_resource_evidence=result.liveness_resource_evidence,
            profile_result=profile_slot.result if profile_slot is not None else None,
            first_failing_test=first_failing_test,
            phase_seconds={
                "materialize": entered_monotonic - started_monotonic,
                "command": command_finished_monotonic - command_started_monotonic,
                "integrity": integrity_finished_monotonic - integrity_started_monotonic,
                "teardown": teardown_finished_monotonic - integrity_finished_monotonic,
            },
            startup_seconds=startup_seconds,
            resource_limit_evidence=resource_limit_evidence,
            resource_limit_changes=resource_limit_changes,
            resource_observation_reason=resource_observation_reason,
            process_tree_evidence=_process_tree_evidence_from_result(result),
        )
        attempt_receipts[(index, attempt_name)] = receipt
        if target_node_id is not None:
            # A prefix replay is only an optimization when its result can be
            # verified against the saved witness. If a cgroup limit event
            # happened during that command, the replay is infrastructure-
            # affected and must not be discarded before `_run_one` falls back
            # to a fresh full command. A replay event therefore invalidates
            # only this optimization attempt; the full command gets its own
            # resource window.
            if (
                run.resource_limit_evidence is not None
                and run.resource_limit_evidence.limit_hit
            ):
                sys.stderr.write(
                    "Assay advisory: cgroup resource-limit event during native "
                    "R2 witness-prefix replay; falling back to the full command\n"
                )
                return None
            witness = _replay_witness_from_receipt(
                receipt,
                process_exit_status=result.returncode,
                target_node_id=target_node_id,
                expected_facts=r2_facts if cold_witness else None,
            )
            if witness is None or prior_verdict_sha256 is None:
                return None
            execution = MutationExecution(
                mode="witness-prefix",
                witness=MutationWitnessReceipt(**witness),
                prior_verdict_sha256=prior_verdict_sha256,
                prior_node_id=target_node_id,
                current_node_id=target_node_id,
            )
            report_run = _dataclass_replace(run, execution=execution)
            if bucket_for_run(report_run) != "killed":
                return None
        else:
            witness = (
                _witness_from_receipt(
                    receipt,
                    process_exit_status=result.returncode,
                )
                if result.outcome is Outcome.FAIL
                else None
            )
            report_witness = (
                MutationWitnessReceipt(**witness) if witness is not None else None
            )
            report_run = _dataclass_replace(
                run,
                execution=MutationExecution(mode="full", witness=report_witness),
            )
            full_witness = (
                report_witness
                if report_witness is not None
                and bucket_for_run(report_run) == "killed"
                else None
            )
            execution = MutationExecution(mode="full", witness=full_witness)
        return _dataclass_replace(run, execution=execution)

    def _run_one(index: int, *, worker_index: int | None = None) -> _MutantRun:
        selected_worker_index = (
            _worker_index_for_current_thread()
            if worker_index is None
            else worker_index
        )
        with worker_index_lock:
            candidate_workers[index] = selected_worker_index
        job = job_list[index]
        started_total = time.monotonic()
        prior = candidate_reuse.get(candidate_id(job))
        if prior is not None:
            prior_verdict_sha256, target_node_id = prior
            replay = _run_attempt(
                index,
                target_node_id=target_node_id,
                prior_verdict_sha256=prior_verdict_sha256,
                attempt_name="replay",
            )
            if replay is not None and bucket_for_run(replay) == "killed":
                if cold_witness:
                    facts = receipt_facts(attempt_receipts.pop((index, "replay"), None))
                    if facts is not None and facts.duplicates == 0:
                        replay = _dataclass_replace(
                            replay,
                            evidence=MutantEvidence(
                                command="r2",
                                collection_count=facts.collection_count,
                                collection_sha256=facts.collection_sha256,
                                hook_fingerprint_sha256=facts.hook_fingerprint_sha256,
                            ),
                        )
                return _dataclass_replace(
                    replay,
                    elapsed_seconds=max(0.0, time.monotonic() - started_total),
                )
        if not cold_witness:
            full = _run_attempt(
                index,
                target_node_id=None,
                prior_verdict_sha256=None,
                attempt_name="full",
            )
            assert full is not None
            return _dataclass_replace(
                full,
                elapsed_seconds=max(0.0, time.monotonic() - started_total),
            )

        assert r2_facts is not None and coverage_facts is not None

        def evidence_for(
            receipt: Mapping[str, Any] | None,
            *,
            command: Literal["r2", "declared"],
            started_count: int | None = None,
            failed_call_index: int | None = None,
        ) -> MutantEvidence | None:
            facts = receipt_facts(receipt)
            if facts is None or facts.duplicates != 0:
                return None
            if command == "r2" and facts.collection_sha256 != r2_facts.collection_sha256:
                return None
            if command == "declared" and facts.collection_sha256 != coverage_facts.collection_sha256:
                return None
            return MutantEvidence(
                command=command,
                collection_count=facts.collection_count,
                collection_sha256=facts.collection_sha256,
                hook_fingerprint_sha256=facts.hook_fingerprint_sha256,
                started_count=started_count,
                failed_call_index=failed_call_index,
            )

        cold = _run_attempt(
            index,
            target_node_id=None,
            prior_verdict_sha256=None,
            attempt_name="cold",
            cold=True,
            variant="r2",
        )
        assert cold is not None
        cold_receipt = attempt_receipts.pop((index, "cold"), None)
        cold_evidence = evidence_for(cold_receipt, command="r2")
        if cold.result.outcome is Outcome.FAIL:
            # A signalled or limit-affected R2 command is infrastructure
            # evidence, not a test failure to retry against the declared
            # plan. WP1's classifier already marks that candidate crashed.
            if bucket_for_run(cold) == "crashed":
                return _dataclass_replace(
                    cold,
                    evidence=cold_evidence,
                    elapsed_seconds=max(0.0, time.monotonic() - started_total),
                )
            proof = cold_witness_from_receipt(
                cold_receipt,
                process_exit_status=cold.result.returncode,
                expected=r2_facts,
            )
            if proof is not None:
                witness, started_count, failed_call_index = proof
                if bucket_for_run(cold) != "killed":
                    return _dataclass_replace(
                        cold,
                        evidence=cold_evidence,
                        elapsed_seconds=max(0.0, time.monotonic() - started_total),
                    )
                return _dataclass_replace(
                    cold,
                    execution=MutationExecution(
                        mode="witness-cold",
                        witness=MutationWitnessReceipt(**witness),
                    ),
                    evidence=evidence_for(
                        cold_receipt,
                        command="r2",
                        started_count=started_count,
                        failed_call_index=failed_call_index,
                    ),
                    elapsed_seconds=max(0.0, time.monotonic() - started_total),
                )
        elif cold.result.outcome is Outcome.PASS:
            if survivor_proof_ok(
                cold_receipt,
                process_exit_status=cold.result.returncode,
                expected=r2_facts,
                command="r2",
            ):
                return _dataclass_replace(
                    cold,
                    evidence=cold_evidence,
                    elapsed_seconds=max(0.0, time.monotonic() - started_total),
                )
        else:
            return _dataclass_replace(
                cold,
                evidence=cold_evidence,
                elapsed_seconds=max(0.0, time.monotonic() - started_total),
            )

        declared = _run_attempt(
            index,
            target_node_id=None,
            prior_verdict_sha256=None,
            attempt_name="full",
            variant="declared",
        )
        assert declared is not None
        declared_receipt = attempt_receipts.pop((index, "full"), None)
        declared_evidence = evidence_for(declared_receipt, command="declared")
        if declared.result.outcome is Outcome.PASS:
            proven = survivor_proof_ok(
                declared_receipt,
                process_exit_status=declared.result.returncode,
                expected=coverage_facts,
                command="declared",
            )
            declared = _dataclass_replace(
                declared,
                evidence=declared_evidence,
                forced_bucket=None if proven else "crashed",
            )
        elif declared.result.outcome is Outcome.FAIL:
            proven = declared_failure_proof_ok(
                declared_receipt,
                process_exit_status=declared.result.returncode,
                expected=coverage_facts,
                manifest_node_ids=manifest_node_ids,
            )
            witness = None
            normal_bucket = bucket_for_run(declared)
            if (
                proven
                and normal_bucket == "killed"
                and isinstance(declared_receipt, Mapping)
            ):
                witness = MutationWitnessReceipt(
                    node_id=declared_receipt["witness_node_id"],
                    when="call",
                    outcome="failed",
                    session_exit_status=1,
                    process_exit_status=1,
                )
            declared = _dataclass_replace(
                declared,
                evidence=declared_evidence,
                forced_bucket=(
                    None if proven else "crashed"
                ),
                execution=(
                    MutationExecution(mode="full", witness=witness)
                    if witness is not None
                    else MutationExecution(mode="full")
                ),
            )
        else:
            declared = _dataclass_replace(declared, evidence=declared_evidence)
        return _dataclass_replace(
            declared,
            elapsed_seconds=max(0.0, time.monotonic() - started_total),
        )

    projected_outcomes: dict[int, tuple[str, MutantOutcome]] = {}

    def _project_candidate_outcome(
        position: int, run: _MutantRun, outcome_bucket: str
    ) -> tuple[str, MutantOutcome]:
        """Validate a candidate's final bucket before its state record is published."""
        job = job_list[position]
        assert run.process_tree_evidence is not None
        kill_signal = run.kill_signal if outcome_bucket == "killed" else None
        try:
            return outcome_bucket, _outcome_of(
                job,
                kill_signal=kill_signal,
                first_failing_test=(
                    run.first_failing_test if outcome_bucket == "killed" else None
                ),
                execution=run.execution,
                resource_limit_evidence=run.resource_limit_evidence,
                evidence=run.evidence,
                process_tree_evidence=run.process_tree_evidence,
                underlying_outcome=run.result.underlying_outcome,
            )
        except ValueError as exc:
            candidate_error = _candidate_outcome_error(exc)
            process_tree = run.process_tree_evidence
            underlying = run.result.underlying_outcome
            completed_leak = (
                isinstance(process_tree, ProcessTreeEvidence)
                and process_tree.leader_status == "exited"
                and process_tree.outliving_descendants > 0
            )
            try:
                outcome = _outcome_of(
                    job,
                    resource_limit_evidence=(
                        run.resource_limit_evidence
                        if isinstance(run.resource_limit_evidence, ResourceLimitEvidence)
                        else None
                    ),
                    execution=MutationExecution(mode="full"),
                    process_tree_evidence=(
                        process_tree
                        if isinstance(process_tree, ProcessTreeEvidence)
                        else ProcessTreeEvidence.not_started()
                    ),
                    underlying_outcome=(
                        underlying
                        if completed_leak and isinstance(underlying, UnderlyingOutcome)
                        else None
                    ),
                    candidate_error=candidate_error,
                )
            except ValueError as recovery_exc:
                candidate_error = _candidate_outcome_error(
                    ValueError(
                        f"initial projection failed: {exc}; recovery projection "
                        f"failed: {recovery_exc}"
                    )
                )
                outcome = _minimal_candidate_outcome(
                    job,
                    process_tree_evidence=process_tree,
                    underlying_outcome=underlying,
                    resource_limit_evidence=run.resource_limit_evidence,
                    candidate_error=candidate_error,
                )
            sys.stderr.write(
                "Assay advisory: candidate outcome validation failed; "
                "recorded candidate as crashed/EXEC_FAILED: "
                f"{candidate_error.message}\n"
            )
            return "crashed", outcome

    def _record_candidate(
        position: int, run: _MutantRun, *, slot: int | None = None
    ) -> Mapping[str, Any]:
        # Futures can finish before the deadline and sit ready while the
        # main thread is busy draining earlier results. Check at the actual
        # classification boundary, not only in the worker.
        deadline.remaining()
        # The same classifier decides whether a witness is reusable and which
        # terminal bucket this complete campaign records.
        outcome_bucket, outcome = _project_candidate_outcome(
            position, run, bucket_for_run(run)
        )
        projected_outcomes[position] = (outcome_bucket, outcome)
        # Classification itself is main-thread work and can cross the lane
        # boundary. Refuse before staging a candidate progress event or state.
        deadline.remaining()
        # B206: the candidate's resource profile. Every number in it comes from
        # the cgprofile observer; without one it is an explicit
        # NO_MEASUREMENT. `wall_seconds` is Assay's own clock.
        profile = observer_windows.build_profile(
            run.profile_result,
            subject={
                "tool": "assay",
                "lane": "r2",
                "kind": "r2-candidate",
                "id": candidate_id(job_list[position]),
                "worker_index": candidate_workers.get(position),
                "workers": jobs,
                "commit": None,
                "tree": None,
            },
            wall_seconds=round(run.elapsed_seconds, 3),
        )
        profile_collector.finish(candidate_id(job_list[position]), profile)
        # B111/AP-6: diagnostic evidence only (never a classification input),
        # built once so progress and state carry identical values. Assay's own
        # clock only (B206): phase split and startup gaps.
        resources = {
            "phase_seconds": (
                {name: round(value, 3) for name, value in run.phase_seconds.items()}
                if run.phase_seconds is not None
                else None
            ),
            "startup_seconds": (
                dict(run.startup_seconds) if run.startup_seconds is not None else None
            ),
        }
        resource_limit_evidence = outcome.resource_limit_evidence
        if run.resource_limit_changes:
            one_line_changes = "; ".join(
                " ".join(change.split()) for change in run.resource_limit_changes
            )
            sys.stderr.write(
                "Assay advisory: cgroup limit values changed during candidate "
                f"{candidate_id(job_list[position])}: {one_line_changes}\n"
            )
        resource_observation = None
        if resource_limit_evidence is None:
            # The projection (including B251's recovery) keeps the run's
            # ResourceLimitEvidence whenever the run has one -- the worker
            # refuses any other type -- so a missing value here is the run's
            # own unobserved window and must carry its reason.
            reason = run.resource_observation_reason
            if not isinstance(reason, str) or not reason.strip():
                raise MutationStateError(
                    "unobserved candidate resource-limit evidence lacks a reason"
                )
            resource_observation = {"status": "unobserved", "reason": reason}
            one_line_reason = " ".join(reason.split())
            sys.stderr.write(
                "Assay advisory: resource-limit window unobserved for "
                f"candidate {candidate_id(job_list[position])}: {one_line_reason}\n"
            )
        if state_root is not None:
            # A store in play without a judge identity would write records that
            # claim to be checkable and are not. Resolve one whenever a store
            # is passed, and fail rather than persist unverifiable evidence.
            assert judge is not None, (
                "a mutation state record cannot be written without "
                "the sweep's judge identity"
            )
            if state_root_guard is not None:
                state_root_guard()
            equivalence_record_evidence = None
            if equivalence_artifact is not None:
                if baseline_equivalence is None:
                    raise MutationStateError(
                        "equivalence artifact lacks its baseline bytes"
                    )
                baseline_digest = hashlib.sha256(baseline_equivalence).hexdigest()
                equivalence_record_evidence = {
                    "artifact": equivalence_artifact,
                    "baseline_sha256": baseline_digest,
                    "observed_sha256": (
                        None
                        if run.equivalence_bytes is None
                        else hashlib.sha256(run.equivalence_bytes).hexdigest()
                    ),
                }
                if outcome_bucket == "equivalent" and (
                    run.equivalence_bytes is None
                    or run.equivalence_bytes != baseline_equivalence
                ):
                    raise MutationStateError(
                        "equivalent candidate lacks matching declared artifact evidence"
                    )
            elif outcome_bucket == "equivalent":
                raise MutationStateError(
                    "equivalent candidate lacks a declared equivalence artifact"
                )
            kill_signal_record_evidence = (
                {
                    "artifact": kill_signal_artifact,
                    "present": run.kill_signal is not None,
                }
                if kill_signal_artifact is not None
                else None
            )
            locus_fields = _locus_fields_for_job(job_list[position])
            record = {
                **_progress_event(
                    candidate_index=position,
                    candidate_total=total,
                    job=job_list[position],
                    locus_fields=locus_fields,
                ),
                "schema_version": MUTATION_STATE_SCHEMA_VERSION,
                "judge_sha256": judge,
                **(
                    {"resource_sha256": resource_sha256}
                    if resource_sha256 is not None
                    else {}
                ),
                "source_sha256": hashlib.sha256(
                    job_list[position].original_text.encode("utf-8")
                ).hexdigest(),
                "replacement_sha256": job_list[position].site.replacement_sha256,
                "lineno": job_list[position].site.lineno,
                "description": job_list[position].site.description,
                "outcome_bucket": outcome_bucket,
                **(
                    {"candidate_error": outcome.candidate_error.to_dict()}
                    if outcome.candidate_error is not None
                    else {}
                ),
                # (B203) The worker count that produced this record, so a
                # resumed campaign can state the maximum and per-count totals.
                "worker_count": jobs,
                "profile": profile,
                **(
                    {"first_failing_test": outcome.first_failing_test}
                    if outcome_bucket == "killed"
                    else {}
                ),
                **(
                    {"forced_bucket": run.forced_bucket}
                    if run.forced_bucket is not None
                    else {}
                ),
                "terminal_result": {
                    "outcome": run.result.outcome.value,
                    "reason_code": (
                        run.result.reason_code.value
                        if run.result.reason_code is not None
                        else None
                    ),
                    "returncode": run.result.returncode,
                },
                "execution": outcome.execution.to_dict(),
                "evidence": outcome.evidence.to_dict() if outcome.evidence is not None else None,
                "resources": resources,
                **(
                    {
                        "resource_limit_evidence": resource_limit_evidence.to_dict()
                    }
                    if resource_limit_evidence is not None
                    else {"resource_observation": resource_observation}
                ),
                **(
                    {"process_tree_evidence": outcome.process_tree_evidence.to_dict()}
                    if outcome.process_tree_evidence is not None
                    else {}
                ),
                **(
                    {"underlying_outcome": outcome.underlying_outcome.to_dict()}
                    if outcome.underlying_outcome is not None
                    else {}
                ),
                **(
                    {"liveness_resource_evidence": run.liveness_resource_evidence}
                    if run.liveness_resource_evidence is not None
                    else {}
                ),
                **(
                    {"campaign_deadline_sha256": campaign_deadline_sha256}
                    if campaign_deadline_sha256 is not None
                    else {}
                ),
                **(
                    {"equivalence_evidence": equivalence_record_evidence}
                    if equivalence_record_evidence is not None
                    else {}
                ),
                **(
                    {"kill_signal_evidence": kill_signal_record_evidence}
                    if kill_signal_record_evidence is not None
                    else {}
                ),
                **_crash_diagnostic_tails(outcome_bucket, run.result),
            }
            # Keep the last check directly at the persistence boundary. If
            # deadline expiry or termination arrived while the record was
            # assembled, leave this candidate unclassified for resume.
            deadline.remaining()
            mutation_store._write_mutation_state_record(
                Path(state_root),
                record,
                state_root_fd=state_root_fd,
                commit_guard=deadline.remaining,
            )
        # What this candidate measured feeds the next campaign's predictions:
        # its own wall clock and the profile's peak RSS (none when the observer
        # produced no number).
        profile_rss = _profile_rss_bytes(profile)
        if state_root is not None and run.elapsed_seconds >= 0:
            history_updates[candidate_id(job_list[position])] = _schedule.HistoryEntry(
                job_list[position].path,
                round(run.elapsed_seconds, 3),
                profile_rss,
                profile_status=profile["status"],
                profile_reason=profile["reason"],
            )
        schedule_keys: dict[str, Any] = {}
        if scheduler is not None and slot is not None:
            prediction = scheduler.prediction(position)
            error = scheduler.error(
                position,
                wall_seconds=run.elapsed_seconds,
                rss_peak_bytes=profile_rss,
                profile_reason=profile["reason"],
            )
            schedule_errors[position] = error
            schedule_keys = {
                "dispatch_position": slot,
                "prediction": {
                    "rss_peak_bytes": prediction.rss_peak_bytes,
                    "wall_seconds": prediction.wall_seconds,
                    "class": scheduler.predicted_class(position),
                    "source": prediction.source,
                },
                "prediction_error": error,
            }
        # Candidate state is staged only by the coordinator after the worker
        # result reaches its dispatch-ordered commit boundary.
        return {
            # (B064) This explicit event name replaced the old unnamed record.
            "event": "candidate",
            **_progress_event(
                candidate_index=position,
                candidate_total=total,
                job=job_list[position],
                locus_fields=(
                    locus_fields
                    if state_root is not None
                    else None
                ),
            ),
            **schedule_keys,
            "worker_index": candidate_workers[position],
            "outcome_bucket": outcome_bucket,
            "execution_mode": run.execution.mode,
            "elapsed_seconds": round(run.elapsed_seconds, 3),
            "tests_completed": run.tests_completed,
            "resource_limit_changes": list(run.resource_limit_changes),
            "worker_count": jobs,
            "profile": profile,
            **(
                {"first_failing_test": run.first_failing_test}
                if outcome_bucket == "killed"
                else {}
            ),
            **{
                key: resources[key]
                for key in ("phase_seconds", "startup_seconds")
            },
            **(
                {"resource_limit_evidence": resource_limit_evidence.to_dict()}
                if resource_limit_evidence is not None
                else {"resource_observation": resource_observation}
            ),
            **(
                {"process_tree_evidence": run.process_tree_evidence.to_dict()}
                if run.process_tree_evidence is not None
                else {}
            ),
            **(
                {"underlying_outcome": run.result.underlying_outcome.to_dict()}
                if run.result.underlying_outcome is not None
                else {}
            ),
            **(
                {"liveness_resource_evidence": run.liveness_resource_evidence}
                if run.liveness_resource_evidence is not None
                else {}
            ),
        }

    def _emit_ready(events: Sequence[Mapping[str, Any]]) -> None:
        if write_progress is not None:
            for event in events:
                write_progress(event)

    results: list[_MutantRun | None] = [None] * total
    executed_runs: dict[int, _MutantRun] = {}
    budget_exceeded_mask = [False] * total
    #: (B524) How many of the MASKED positions a worker was actually
    #: DISPATCHED for, rather than left in the queue when submission stopped.
    #: The two are different sentences -- "this candidate got a worker and
    #: hit the lane deadline" versus "the sweep never reached this one" --
    #: and the mask alone cannot tell them apart, which is exactly what the
    #: `end` progress record used to get wrong (CMRu's s4 attempt A reported
    #: 23 budget failures for 1 started candidate and 22 pending). Read back
    #: by `run_mutation`'s terminal event; a leftover position never
    #: increments it.
    budget_exceeded_dispatched = 0
    fatals: dict[int, AssayError] = {}
    stop_submitting = False
    event_buffer = _CandidateEventBuffer(total)
    #: future -> dispatch slot. A slot is a candidate's place in dispatch
    #: order (B207); `dispatch_order[slot]` is its canonical index. Progress
    #: and state drain by slot, so a late-dispatched low canonical index never
    #: holds back candidates that were dispatched, and finished, earlier.
    in_flight: dict[Future, int] = {}
    dispatch_order: list[int] = []
    completed_runs: dict[int, _MutantRun] = {}
    resolved_without_event: set[int] = set()
    record_cursor = 0
    next_canonical = 0

    candidate_pairs = [
        (candidate_id(job_list[index]), job_list[index].path)
        for index in range(total)
    ]
    schedule_options = schedule if schedule is not None else _schedule.ScheduleOptions()
    history, history_reason = _read_schedule_history(state_root, state_root_fd)
    history_updates: dict[str, _schedule.HistoryEntry] = {}
    scheduler: _schedule.TemScheduler | None = None
    schedule_errors: dict[int, Mapping[str, Any]] = {}
    if jobs > 1 and total > 0:
        scheduler = _schedule.TemScheduler(
            candidate_pairs,
            _schedule.resolve_predictions(
                candidate_pairs,
                history,
                operator_wall_seconds=schedule_options.prediction_wall_seconds,
                operator_rss_bytes=schedule_options.prediction_rss_bytes,
            ),
            budget_bytes=schedule_options.memory_budget_bytes,
            workers=jobs,
            high_factor=schedule_options.high_factor,
            low_factor=schedule_options.low_factor,
        )

    def _pick_candidate() -> int | None:
        nonlocal next_canonical
        if scheduler is not None:
            return scheduler.next_candidate(time.monotonic())
        # The dispatch loop only asks while `len(dispatch_order) < total`.
        next_canonical += 1
        return next_canonical - 1

    def _record_ready_in_order() -> None:
        nonlocal record_cursor
        while record_cursor < total:
            run = completed_runs.pop(record_cursor, None)
            if run is not None:
                event_buffer.stage(
                    record_cursor,
                    _record_candidate(
                        dispatch_order[record_cursor], run, slot=record_cursor
                    ),
                )
            elif record_cursor in resolved_without_event:
                event_buffer.resolve_without_event(record_cursor)
            else:
                break
            record_cursor += 1
        _emit_ready(event_buffer.drain_contiguous())

    try:
        with executor_factory(jobs) as pool:
            try:
                while True:
                    while (
                        not stop_submitting
                        and len(dispatch_order) < total
                        and len(in_flight) < jobs
                    ):
                        position = _pick_candidate()
                        if position is None:
                            break
                        slot = len(dispatch_order)
                        dispatch_order.append(position)
                        try:
                            future = pool.submit(_run_one, position)
                        except RuntimeError as exc:
                            fatals[position] = AssayError(
                                "could not start a native R2 candidate worker; the "
                                "candidate sweep stopped before all mutants were run "
                                f"({exc})",
                                outcome=Outcome.ERROR,
                                reason_code=ReasonCode.EXEC_FAILED,
                            )
                            resolved_without_event.add(slot)
                            stop_submitting = True
                            break
                        in_flight[future] = slot

                    if not in_flight:
                        break
                    if limit_broker is not None:
                        if runner_module.termination_requested():
                            deadline.remaining()
                        limit_broker.service_pending()
                    if limit_broker is None:
                        done, _ = wait(
                            tuple(in_flight), return_when=FIRST_COMPLETED
                        )
                    else:
                        done, _ = wait(
                            tuple(in_flight),
                            timeout=0.05,
                            return_when=FIRST_COMPLETED,
                        )
                    if not done:
                        continue
                    for future in sorted(done, key=in_flight.__getitem__):
                        slot = in_flight.pop(future)
                        position = dispatch_order[slot]
                        try:
                            results[position] = future.result()
                        except AssayError as exc:
                            if isinstance(exc, _UnclassifiedLivenessWallStop):
                                # Unlike an elapsed lane deadline that can
                                # leave a `budget_exceeded` summary, the wall
                                # backstop has no candidate result at all.
                                # Keep it on the fatal path so resume retries
                                # this identity without a bucket.
                                fatals[position] = exc
                            elif (
                                exc.outcome is Outcome.BUDGET_EXCEEDED
                                and exc.reason_code is ReasonCode.LANE_TIMEOUT
                            ):
                                budget_exceeded_mask[position] = True
                                budget_exceeded_dispatched += 1
                            else:
                                fatals[position] = exc
                            stop_submitting = True
                        run = results[position]
                        if run is None:
                            resolved_without_event.add(slot)
                        else:
                            executed_runs[position] = run
                            completed_runs[slot] = run
                        if scheduler is not None:
                            scheduler.complete(
                                position,
                                time.monotonic(),
                                wall_seconds=(
                                    None if run is None else run.elapsed_seconds
                                ),
                                rss_peak_bytes=(
                                    None
                                    if run is None
                                    else _result_rss_bytes(run.profile_result)
                                ),
                            )
                    _record_ready_in_order()
            except BaseException as exc:
                if limit_broker is not None:
                    limit_broker.cancel_pending(exc)
                    liveness.terminate_live_window_hosts()
                raise

        if stop_submitting:
            if scheduler is not None:
                leftovers = scheduler.remaining_indices()
            else:
                leftovers = list(range(next_canonical, total))
            for leftover in leftovers:
                budget_exceeded_mask[leftover] = True
                resolved_without_event.add(len(dispatch_order))
                dispatch_order.append(leftover)
        _record_ready_in_order()
    finally:
        # An interruption may leave a hole. Only the completed dispatch prefix
        # is publishable; skipping a hole would reorder candidate progress/state.
        _emit_ready(event_buffer.drain_contiguous())
        _persist_schedule_side_files(
            state_root,
            state_root_fd,
            history=history,
            updates=history_updates,
            scheduler=scheduler,
            errors=schedule_errors,
            history_reason=history_reason,
        )

    # A-195: a mutant that left Git-visible dirt/HEAD drift is never folded
    # into `crashed`. After all submitted workers drain, choose the fatal at
    # the lowest observed candidate position so completion order cannot pick
    # the lane's terminal cause.
    if fatals:
        raise fatals[min(fatals)]

    # (B179/AP-5) Let every candidate get its first attempt before spending any
    # remaining campaign time on infrastructure outcomes. A campaign-deadline
    # mask means the sweep already reached its boundary, so it cannot open a
    # retry window. `executed_runs` contains only candidates executed in this
    # invocation; resumed records never enter this list.
    if not any(budget_exceeded_mask):
        for position in sorted(executed_runs):
            first_run = executed_runs[position]
            first_bucket = bucket_for_run(first_run)
            if first_bucket not in {"crashed", "budget_exceeded", "hung"}:
                continue

            remaining = deadline.remaining()
            if (
                not liveness_watch_armed
                and budget_per_candidate_seconds is not None
                and remaining < budget_per_candidate_seconds
            ):
                # (P4/S3) ARMED: a tail retry is not gated on the (advisory)
                # per-candidate budget; the lane deadline is the only gate.
                break
            retry_run = _run_one(position, worker_index=0)
            results[position] = retry_run
            # Persist the retry through the same schema and boundary as the
            # first run. Do not duplicate the first `candidate` event; the
            # additive retry event below names the changed classification.
            _record_candidate(position, retry_run)
            final_bucket = projected_outcomes[position][0]
            _emit_ready(
                (
                    {
                        "event": "candidate_retry",
                        "candidate_id": candidate_id(job_list[position]),
                        "first_bucket": first_bucket,
                        "final_bucket": final_bucket,
                        "worker_index": candidate_workers[position],
                        **(
                            {"underlying_outcome": retry_run.result.underlying_outcome.to_dict()}
                            if retry_run.result.underlying_outcome is not None
                            else {}
                        ),
                    },
                )
            )

    # (B091/RW-33, P7 A3) Built generically from `MUTATION_BUCKETS`, matching
    # the ingested path's own construction a few hundred lines down --
    # A-228's own root cause was a hand-written literal list reaching some
    # of the sites that must agree with the closed vocabulary and not
    # others, and this is exactly the site that used to be a five-entry
    # literal missing the sixth (`hung`) bucket entirely, which would have
    # KeyError'd the moment the shared classifier returned it.
    buckets: dict[str, list[MutantOutcome]] = {name: [] for name in MUTATION_BUCKETS}
    unattempted_evidence: ResourceLimitEvidence | None = None
    if any(budget_exceeded_mask):
        unattempted_evidence, unattempted_observation_reason = (
            _zero_window_resource_limit_evidence()
        )
        if unattempted_observation_reason is not None:
            sys.stderr.write(
                "Assay advisory: zero-duration resource-limit sample "
                "unobserved for unstarted candidates: "
                f"{' '.join(unattempted_observation_reason.split())}\n"
            )
    complete_campaign = not any(budget_exceeded_mask)
    for position, job in enumerate(job_list):
        # Results are consumed POSITION-ALIGNED with the submitted job list,
        # and `collect_mutation_sites` guarantees that list is
        # identity-ordered already (per file by `_validate_sites`, across
        # files by path order, and `MutantOutcome.identity` leads with
        # `path`) -- appending in that order leaves every bucket
        # identity-ordered without a second sort.
        if budget_exceeded_mask[position]:
            buckets["budget_exceeded"].append(
                _outcome_of(
                    job,
                    resource_limit_evidence=unattempted_evidence,
                    process_tree_evidence=ProcessTreeEvidence.not_started(),
                )
            )
            continue
        if complete_campaign:
            deadline.remaining()
        projected = projected_outcomes.get(position)
        if projected is None:
            raise MutationStateError(
                f"candidate {candidate_id(job)} has no validated outcome projection"
            )
        bucket, outcome = projected
        buckets[bucket].append(outcome)

    statuses, reasons, modes = profile_collector.counters()
    never_started = sum(1 for flagged in budget_exceeded_mask if flagged)
    if never_started:
        # A candidate that never ran has no window: NO_MEASUREMENT/NOT_STARTED.
        statuses[observer_windows.STATUS_NO_MEASUREMENT] += never_started
        reasons[observer_windows.NOT_STARTED] += never_started
    telemetry: dict[str, Any] = {
        "profile": {
            "status_counts": dict(statuses),
            "reason_counts": dict(reasons),
            "modes": sorted(modes),
        },
        "worker_counts": {str(jobs): len(executed_runs)} if executed_runs else {},
        # (B524) Internal run fact, not wire state: how many of the masked
        # `budget_exceeded` positions the sweep never even dispatched. The
        # rest of the mask was dispatched -- a worker started, and the
        # candidate hit the lane deadline. `run_mutation`'s terminal `end`
        # event reads this back to split the bucket's count into dispatched
        # and never-started, rather than recomputing a second, independent
        # way.
        "budget_exceeded_not_started": (
            sum(1 for flagged in budget_exceeded_mask if flagged)
            - budget_exceeded_dispatched
        ),
        "schedule": (
            scheduler.verdict_summary(errors=schedule_errors)
            if scheduler is not None
            else _schedule.canonical_verdict_summary(
                jobs=jobs, memory_budget_bytes=schedule_options.memory_budget_bytes
            )
        ),
    }
    mutation = Mutation(
        candidate_count=candidate_count,
        total=total,
        r2_telemetry=telemetry,
        **{name: tuple(buckets[name]) for name in MUTATION_BUCKETS},
    )
    if complete_campaign:
        deadline.remaining()
    return mutation, complete_campaign


#: (B245 U3a) The status -> bucket map and the discarded-status table are
#: PER FORMAT now: `MutationFormatSpec.status_buckets` and
#: `MutationFormatSpec.discarded_statuses`, passed to
#: `ingest_mutation_report` as `spec`. Stryker's own tables moved UNCHANGED to
#: `mutation_parsers.mutation_report_json` (`STATUS_BUCKETS`,
#: `DISCARDED_STATUSES`), their B046/B070/A-122/A-377 rationale with them.


@record
class IngestedMutationResult:
    """(B046) What :func:`ingest_mutation_report` produces: the R2 payload
    plus the four ingested-only facts ``judgment.r2`` records beside it.

    A separate type from :class:`Mutation` because the payload and the
    judgment are two different objects on the wire with two different owners
    -- and because bundling `discarded` into `Mutation` would put a SIXTH
    bucket in the CLAIM for mutants that were never attempted, which is not
    what the buckets mean.

    **B070 (schema v11): `discarded` is now the mutants, not a count of
    them.** The count could never be a difference OF anything, which is why it
    spent two schema versions declared-not-verified; the list is auditable
    against the very payload it sits beside. `Mutation._check_arithmetic`
    stays the keeper of the CLAIM's own totals -- what it no longer does is
    forbid a residual, because the residual is now exactly this list's length
    and `Verdict._check_discarded_disposition` is what proves it.
    """

    mutation: Mutation
    producer_tool: MutationProducerTool
    survived_uncovered: tuple[SourcePosition, ...]
    #: (B070, schema v11) The discarded mutants THEMSELVES, sorted by identity,
    #: not a count of them. See :attr:`assay.verdict.JudgmentR2.discarded`.
    discarded: tuple[MutantOutcome, ...]
    lines_without_candidates: tuple[SourcePosition, ...]
    #: (B245 U5c, schema v20) ``True`` only for the synthesised zero-mutant
    #: result (:func:`synthesize_zero_mutant_result`): the tool wrote no
    #: report because it ran zero mutants, so ``judgment.r2.report_absent``
    #: and the ``unobserved`` producer version are recorded.
    report_absent: bool = False


def _under_source_roots(
    repo_path: str, *, repo_top: Path, source_root_paths: Sequence[Path]
) -> bool:
    """Whether the repo-top-relative *repo_path* lies under a declared source
    root (a directory root by containment, a file root by equality)."""
    absolute = Path(repo_top) / repo_path
    return any(absolute.is_relative_to(Path(root)) for root in source_root_paths)


def synthesize_zero_mutant_result(
    added: AddedLines,
    *,
    repo_top: Path,
    source_root_paths: Sequence[Path],
) -> IngestedMutationResult:
    """(B245 U5c) The R2 result of a cargo-mutants run that wrote NO report.

    cargo-mutants prints "No mutants to filter" / "Diff file is empty" and
    writes no ``mutants.out`` when ``--in-diff`` selects nothing (design 1.4,
    D3): zero mutants ran. The payload is a ``Mutation`` with every bucket
    empty and ``candidate_count`` 0, so ``judge_mutation`` yields
    ``INCONCLUSIVE``/``NO_MUTANTS`` -- never a PASS (operator decision, D3).
    ``producer_tool.version`` is the literal ``unobserved`` (Q1): assay read
    no report, so it observed no version.

    ``lines_without_candidates`` (Q3) is the diff's added lines within the
    declared source roots, so "nothing to mutate" stays reviewable: the
    consumer sees which changed lines the tool never produced a mutant for.
    """
    return IngestedMutationResult(
        mutation=Mutation(
            candidate_count=0,
            total=0,
            **{name: () for name in MUTATION_BUCKETS},
        ),
        producer_tool=MutationProducerTool(
            name=CARGO_MUTANTS_OPERATOR_NAMESPACE,
            version=PRODUCER_VERSION_UNOBSERVED,
            report_schema_version=CARGO_MUTANTS_REPORT_SCHEMA,
        ),
        survived_uncovered=(),
        discarded=(),
        lines_without_candidates=tuple(
            sorted(
                (
                    SourcePosition(path=path, lineno=lineno)
                    for path, lines in added.by_file.items()
                    if _under_source_roots(
                        path, repo_top=repo_top, source_root_paths=source_root_paths
                    )
                    for lineno in lines
                ),
                key=lambda position: position.sort_key,
            )
        ),
        report_absent=True,
    )


def ingest_mutation_report(
    report: IngestedMutationReport,
    *,
    run_cwd: Path,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    source_root_files: Sequence[str],
    mode: str,
    added: AddedLines | None,
    targets: Sequence[str] | None,
    repository: SnapshotRepository,
    read_timeout: float,
    spec: MutationFormatSpec,
    content_tier: str | None = None,
) -> IngestedMutationResult:
    """Turn a parsed foreign mutation report into assay's own R2 payload.

    *content_tier* (B245 U5a) is ``None`` for a report that carries its own
    ``projectRoot`` and embedded ``source`` (Stryker): both are checked. For
    ``"per-mutant-diff"`` (cargo-mutants) the parser already bound each
    mutant's content to the committed blobs, so the project-root and
    source-matches-commit checks are skipped: filling ``sources`` from blobs
    and comparing them with the same blobs would be a tautology.

    *spec* is the declared format's registry entry (B245 U3a): its
    ``status_buckets`` and ``discarded_statuses`` say where each of the
    parser's statuses lands, so this function holds no per-tool table.

    **Scope is assay's computation, not the tool's** (B046's own "Judgment"
    section). Stryker mutated whatever its configuration told it to; which of
    those mutants COUNTS is decided here, by the same rule native R2 uses:
    under ``changed_lines`` a mutant counts iff its start line is an added
    line of the resolved diff; under ``whole_target`` iff its file is a
    declared target. Reading the tool's own score instead would be judging by
    a scope assay never declared.

    *run_cwd* is the directory the lane's command actually ran in inside the
    snapshot -- ``snapshot.project_root`` joined with the lane's ``cwd``
    (B043). It is what the report's own ``projectRoot`` must equal and what
    its relative ``files`` keys are anchored at. This is the coupling that
    made B043 a prerequisite for this item rather than a sibling of it: with
    a monorepo lane (the shape B046's own worked example uses) the report's
    keys are relative to the app directory, and resolving them to
    repository-relative paths is impossible without knowing which directory
    that was.

    Every path on the wire is REPO-TOP-relative, exactly as a natively
    generated :class:`MutantOutcome` already is -- one spelling, so an
    ingested and a native verdict can be read by the same consumer code.

    *repository* and *read_timeout* carry B052/DA-D5's **content** tier, the
    third and last non-repudiation question this function asks. Identity
    (:func:`_check_report_project_root`) and anchoring
    (:func:`_resolve_report_paths`) establish that the report is about this
    checkout; content establishes that it is about this COMMIT, by reading
    each measured file's blob back through
    :meth:`~assay.isolation.SnapshotRepository.read_regular_file` and
    comparing it with the report's own embedded ``source``. Both parameters
    are REQUIRED and have no default, deliberately: a default would make the
    strongest of the three tiers the one a caller can forget, and there is
    exactly one caller (:func:`assay.runner._ingest_r2_report`), which already
    holds the repository the lane's command ran against.
    """
    if content_tier is not None and content_tier != "per-mutant-diff":
        raise ValueError(
            f"content_tier must be None or 'per-mutant-diff', not {content_tier!r}"
        )
    producer_tool = MutationProducerTool(
        name=report.producer.name,
        version=report.producer.version,
        report_schema_version=report.producer.report_schema_version,
    )

    per_mutant_diff = content_tier == "per-mutant-diff"
    if not per_mutant_diff:
        _check_report_project_root(report, run_cwd=run_cwd)
    wire_paths = _resolve_report_paths(
        report,
        run_cwd=run_cwd,
        repo_top=repo_top,
        source_root_paths=source_root_paths,
        source_root_files=source_root_files,
    )
    # (B052/DA-D5) Tier three, CONTENT, in tier order and before a single
    # mutant is bucketed. It runs third because it depends on the second: the
    # committed blob can only be read once the report's own file key has been
    # resolved to its repo-top-relative spelling. It runs BEFORE the bucketing
    # loop because everything that loop computes -- byte spans, line numbers,
    # `lines_without_candidates` -- is derived from the very text this check
    # is about, so judging first and checking after would build a payload out
    # of text assay is in the middle of deciding not to trust.
    if not per_mutant_diff:
        _check_report_source_matches_commit(
            report,
            wire_paths=wire_paths,
            repository=repository,
            timeout=read_timeout,
        )
    in_scope = _in_scope_predicate(mode=mode, added=added, targets=targets)

    buckets: dict[str, list[MutantOutcome]] = {name: [] for name in MUTATION_BUCKETS}
    # A SET of positions, not a list per mutant. `survived_uncovered` is
    # documented as "listed BY POSITION so a consumer sees the untested line",
    # and the model enforces uniqueness -- correctly: one line with nine
    # NoCoverage mutants on it is still one untested line, and repeating it
    # nine times would turn a list of places into a disguised count of
    # mutants. The real committed report has exactly this shape (ten
    # NoCoverage mutants share `src/format.ts` line 34).
    survived_uncovered: set[tuple[str, int]] = set()
    # (B070, schema v11) A LIST of the discarded mutants, not a count. Through
    # v10 this loop `continue`d past a `CompileError`/`RuntimeError` mutant
    # with no record of it anywhere in the document, which is precisely why
    # `judgment.r2.discarded` had nothing to be checked against.
    discarded: list[MutantOutcome] = []
    mutated_lines: set[tuple[str, int]] = set()

    for mutant in report.mutants:
        wire_path = wire_paths[mutant.path]
        if not in_scope(wire_path, mutant.lineno):
            continue
        if mutant.status == "Ignored":
            # A-377: no v9 field can state this fact, and both alternatives
            # misstate it -- dropping it launders a suppressed mutant into
            # nothing, and folding it into `discarded` would report a
            # deliberately ignored mutant as one that failed to COMPILE,
            # which the schema's own description of that field forbids.
            raise AssayError(
                f"mutation report marks an IN-SCOPE mutant "
                f"{wire_path}:{mutant.lineno} ({mutant.operator}) as "
                f"'Ignored'. The tool was told to skip it -- by "
                f"`mutator.excludedMutations`, or by a `// Stryker disable` "
                f"comment -- and the v9 verdict has no field that can say so: "
                f"`discarded` means a mutant that failed to COMPILE, and "
                f"reporting a suppressed mutant there would be a false "
                f"statement about why it did not run. Assay will not silently "
                f"drop it either, because a mutant suppressed inside the "
                f"tool's own config is exactly how a gate is made green "
                f"without being passed. Remove the suppression, or move the "
                f"line out of the lane's declared scope.",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )
        mutated_lines.add((wire_path, mutant.lineno))
        outcome = MutantOutcome(
            path=wire_path,
            lineno=mutant.lineno,
            start_byte=mutant.start_byte,
            end_byte=mutant.end_byte,
            replacement_sha256=mutant.replacement_sha256,
            operator=mutant.operator,
            description=mutant.description,
            discard_reason=spec.discarded_statuses.get(mutant.status),
        )
        if mutant.status in spec.discarded_statuses:
            # B070: RECORDED, not dropped. The record is built above, before
            # the fork, so a discarded mutant carries byte-for-byte the same
            # identity a bucketed one does -- which is what lets
            # `Verdict._check_discarded_disposition` ask whether the two sets
            # overlap at all.
            discarded.append(outcome)
            continue
        bucket = spec.status_buckets[mutant.status]
        buckets[bucket].append(outcome)
        if mutant.status == "NoCoverage":
            survived_uncovered.add((wire_path, mutant.lineno))

    for name in MUTATION_BUCKETS:
        buckets[name].sort(key=lambda item: item.identity)
    discarded.sort(key=lambda item: item.identity)
    attempted = sum(len(items) for items in buckets.values())
    try:
        payload = Mutation(
            # B070 (schema v11): `candidate_count` is "the candidate site
            # descriptors discovery actually observed", and an invalid mutant
            # WAS observed -- the report listed it, with a full identity. So
            # the in-scope candidates the report listed is `attempted +
            # discarded`, and the difference between the two fields is the
            # quantity `judgment.r2.discarded` is checked against. Through v10
            # both were `attempted`, which is what made an inflated count
            # undetectable (A-437).
            candidate_count=attempted + len(discarded),
            total=attempted,
            **{name: tuple(buckets[name]) for name in MUTATION_BUCKETS},
        )
    except ValueError as exc:
        # The one shape the model refuses that a real report can produce: two
        # mutants with the identical (path, span, replacement, operator). A
        # bare ValueError escaping here would surface as a crash rather than a
        # judged terminal.
        raise AssayError(
            f"mutation report does not yield a well-formed R2 payload: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc

    return IngestedMutationResult(
        mutation=payload,
        producer_tool=producer_tool,
        survived_uncovered=tuple(
            SourcePosition(path=path, lineno=lineno)
            for path, lineno in sorted(survived_uncovered)
        ),
        discarded=tuple(discarded),
        lines_without_candidates=_lines_without_candidates(
            report,
            wire_paths=wire_paths,
            in_scope=in_scope,
            mutated_lines=mutated_lines,
        ),
    )


def _check_report_project_root(
    report: IngestedMutationReport, *, run_cwd: Path
) -> None:
    """B046 non-repudiation (iii), first half: the report must describe THIS
    run's own directory, not some other checkout the same tool ran in.

    Compared through :func:`os.path.realpath` on both sides rather than by
    string: a snapshot lives under a caller-supplied scratch root, and on a
    great many systems ``/tmp`` is itself a symlink, so two spellings of one
    directory is the normal case rather than the suspicious one.
    """
    if report.project_root is None:
        # (B245 U3a) A format whose document carries no projectRoot (cargo-
        # mutants) is bound to the commit by CONTENT instead; there is no
        # root to compare.
        return
    declared = os.path.realpath(report.project_root)
    actual = os.path.realpath(run_cwd)
    if declared != actual:
        raise AssayError(
            f"mutation report declares projectRoot {report.project_root!r}, "
            f"which is not the directory this lane's command ran in "
            f"({run_cwd}). This report was produced somewhere else -- a stale "
            f"artifact committed into the repository, or one copied in from "
            f"another run -- and a judgment computed from it would be about a "
            f"tree this verdict does not name.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )


def _resolve_report_paths(
    report: IngestedMutationReport,
    *,
    run_cwd: Path,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    source_root_files: Sequence[str],
) -> Mapping[str, str]:
    """B046 non-repudiation (iii), second half: every ``files`` key belongs
    to a declared source root, and resolves to its repo-top-relative wire
    spelling. Directory roots use resolved containment; exact-file roots
    compare the report's lexical path before following symlinks, so a sibling
    alias cannot widen the declared file.

    A key that does not is ``ERROR``/``UNREADABLE_ARTIFACT`` -- "an artifact
    from elsewhere" -- rather than a file quietly skipped. The distinction is
    the whole point: skipping would let a report about a DIFFERENT project be
    judged as though it were about this one and simply score zero mutants,
    which is a PASS-shaped answer to a question that was never asked.
    """
    resolved: dict[str, str] = {}
    for key in sorted(report.sources):
        if PurePosixPath(key).is_absolute() or ".." in PurePosixPath(key).parts:
            raise AssayError(
                f"mutation report names file {key!r}; keys must be relative "
                f"to the report's own projectRoot, with no '..' component",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )
        lexical_absolute = (run_cwd / key).absolute()
        absolute = lexical_absolute.resolve()
        try:
            lexical_repo_path = lexical_absolute.relative_to(
                Path(repo_top).absolute()
            ).as_posix()
        except ValueError:
            lexical_repo_path = None
        under_declared_file = (
            lexical_repo_path in source_root_files
            or (
                not source_root_files
                and any(
                    Path(root).is_file() and lexical_absolute == Path(root)
                    for root in source_root_paths
                )
            )
        )
        under_declared_directory = any(
            not Path(root).is_file()
            and absolute.is_relative_to(Path(root).resolve())
            for root in source_root_paths
        )
        if not (under_declared_file or under_declared_directory):
            raise AssayError(
                f"mutation report names file {key!r}, which resolves to "
                f"{absolute} -- not under any declared judge.source_roots "
                f"entry. Assay judges the sources the lane declared; a report "
                f"describing files outside them is an artifact from "
                f"elsewhere, not a measurement of this lane.",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )
        try:
            relative = absolute.relative_to(Path(repo_top).resolve())
        except ValueError as exc:
            raise AssayError(
                f"mutation report names file {key!r}, which resolves outside "
                f"the snapshot repository at {repo_top}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            ) from exc
        resolved[key] = relative.as_posix()
    return resolved


#: (B052/DA-D5) The normalisation the content tier compares under, STATED
#: rather than implied, because a comparison's normalisation is its contract:
#: what it folds away is what it has decided not to be evidence about.
#:
#: Two folds, and only two:
#:
#: 1. **line endings are folded to ``\n``** -- CRLF and a lone CR alike. A
#:    consumer whose `.gitattributes` checks files out CRLF gets a report whose
#:    ``source`` is CRLF and a blob that is LF, and refusing that pair would
#:    refuse a correct lane over a checkout setting (B052's cause 4). Assay's
#:    own mutant positions are LINE numbers and byte spans WITHIN the report's
#:    text, so a line-ending difference cannot move a mutant to a different
#:    line or a different statement;
#: 2. **one trailing newline is ignored** -- exactly one, at the very end.
#:    Editors and tools disagree about the final newline, and a file that
#:    differs from the commit only by having or lacking it differs in no line's
#:    content.
#:
#: Everything else is BYTE-EXACT. Leading and interior whitespace, indentation,
#: blank lines, trailing whitespace on a line, encoding of any non-ASCII
#: character: all of it is compared, because all of it can change what a line
#: says and what a byte span covers. In particular a formatter's rewrite is a
#: MISMATCH by design (B052's cause 2, refused under DA-D5), and so is a
#: second trailing newline.
_CONTENT_TIER_NORMALISATION = (
    "line endings folded to \\n, one trailing newline ignored, everything "
    "else byte-exact"
)


def _normalise_source_for_compare(data: bytes) -> bytes:
    """Apply :data:`_CONTENT_TIER_NORMALISATION` to *data*.

    Operates on BYTES rather than ``str`` for the reason the whole module
    does: the report's ``source`` is decoded UTF-8 text and the commit's blob
    is raw bytes, and re-encoding the former is the only conversion that keeps
    a non-ASCII character comparable to the bytes git actually stored.
    """
    folded = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if folded.endswith(b"\n"):
        folded = folded[:-1]
    return folded


def _check_report_source_matches_commit(
    report: IngestedMutationReport,
    *,
    wire_paths: Mapping[str, str],
    repository: SnapshotRepository,
    timeout: float,
) -> None:
    """(B052/DA-D5) Non-repudiation tier THREE: **content**.

    B046 established two tiers, and they answer weaker questions than this
    one. ``_check_report_project_root`` establishes *identity* -- the report
    is about the directory the lane's command ran in. ``_resolve_report_paths``
    establishes *anchoring* -- every file it names resolves under a declared
    source root, inside this snapshot. Neither establishes that the report is
    about **this commit's content**, which is the property assay's own
    committed-object snapshot exists to make checkable and the only one that
    closes "an artifact from an earlier state of the same tree".

    **Why it matters more here than anywhere else in the ingest path.** Assay
    does not merely quote the report's ``source``; it COMPUTES from it. Every
    mutant's byte span comes from ``_line_byte_offsets(source)``
    (``mutation_parsers/mutation_report_json.py``), and
    ``lines_without_candidates`` walks that same text line by line -- so a
    report whose text is not the commit's produces positions that are correct
    about a file assay never had and wrong about the one it judged. The
    positions still LOOK like this commit's, because they are spelled with
    this commit's paths.

    So the committed blob is read back through
    :meth:`assay.isolation.SnapshotRepository.read_regular_file` -- the same
    prepared commit the lane's own command ran against -- and compared under
    :data:`_CONTENT_TIER_NORMALISATION`.

    **A mismatch is ``ERROR``/``UNREADABLE_ARTIFACT``, with no warning mode,
    no opt-out key and no wire field** (DA-D5). Each of those was rejected for
    its own reason: a warning is a fact nobody reads; an opt-out is the gate
    being switched off from inside the lane file it is judging; and recording
    it on the wire would archive a document assay has already decided not to
    trust. The refusal names the file and the three causes B052 enumerated,
    because a bare "content differs" leaves the consumer to guess which of
    them they are in.

    **Cause (2), a tool that rewrote the source in flight, is REFUSED by
    design and this is the ruling rather than an oversight.** Evidence whose
    text is not the commit's is not evidence about the commit: the mutants
    were applied to transpiled or reformatted text, and the line numbers they
    carry are that text's, not the committed file's. A formatter that writes
    back into the working tree would trip ``DIRTY_TREE`` on the next run
    anyway, so refusing here is consistent with what assay already does one
    layer up rather than a new severity.

    **A path the commit does not track at all is the same refusal**, not a
    ``GIT_FAILED``: "the commit has no such content" is the strongest
    possible content mismatch, and it is B052's cause 3 (a genuinely foreign
    report) in its most literal form. Surfacing git's own "not a regular
    tracked file" wording here would report a repository failure for a report
    defect.
    """
    for key in sorted(report.sources):
        wire_path = wire_paths[key]
        try:
            committed = repository.read_regular_file(
                PurePosixPath(wire_path), timeout=timeout
            )
        except AssayError as exc:
            raise AssayError(
                f"mutation report measures {wire_path}, which the judged "
                f"commit does not carry as a regular tracked file ({exc}). "
                f"An ingested report is evidence about THIS commit's content; "
                f"a file the commit does not have is an artifact from "
                f"elsewhere. Run the mutation tool inside the lane, against "
                f"the committed tree.",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            ) from exc
        reported = report.sources[key].encode("utf-8")
        if _normalise_source_for_compare(reported) == _normalise_source_for_compare(
            committed
        ):
            continue
        raise AssayError(
            f"mutation report's embedded source for {wire_path} does not "
            f"match the bytes the judged commit carries for that file "
            f"(compared with {_CONTENT_TIER_NORMALISATION}; report "
            f"{len(reported):,} bytes, commit {len(committed):,} bytes). "
            f"Assay derives every mutant's line and byte span from the "
            f"report's own text, so a report about different text produces "
            f"positions that are wrong about the file it judged. Three "
            f"causes: the report is STALE (the tool ran before the last "
            f"edit); the tool REWROTE the source before mutating it "
            f"(transpilation, or a formatter inside the test command) -- and "
            f"a report about rewritten text is not evidence about the commit, "
            f"so this is refused deliberately; or the report is FOREIGN (it "
            f"describes another checkout). Remedy in all three: run the "
            f"mutation tool inside the lane, against the committed tree, so "
            f"the report assay reads is the one this commit produced.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )


def _in_scope_predicate(
    *, mode: str, added: AddedLines | None, targets: Sequence[str] | None
) -> Callable[[str, int], bool]:
    """The SAME scope rule native R2 applies, expressed per (path, line).

    Under ``whole_target`` a declared target's every line is in scope, and
    membership is by repo-top-relative path. Under ``changed_lines`` a line is
    in scope iff the resolved diff added it -- which is why an ingested lane
    needs the diff at all, and why it is threaded in from the caller rather
    than re-derived (P18's own "R2 target selection must consume the same
    measurement, not invoke Git independently with a second base").
    """
    if mode == "whole_target":
        declared = frozenset(targets or ())

        def by_target(path: str, _lineno: int) -> bool:
            return path in declared

        return by_target

    by_file = added.by_file if added is not None else {}

    def by_added_line(path: str, lineno: int) -> bool:
        return lineno in by_file.get(path, frozenset())

    return by_added_line


def _lines_without_candidates(
    report: IngestedMutationReport,
    *,
    wire_paths: Mapping[str, str],
    in_scope: Callable[[str, int], bool],
    mutated_lines: set[tuple[str, int]],
) -> tuple[SourcePosition, ...]:
    """(B046) In-scope source lines the foreign tool produced NO mutant for.

    **"Executable" is approximated, and the approximation is stated rather
    than hidden** (A-378). Assay has no per-line executability oracle for an
    ingested language: `generate_mutation_sites` is `UNSUPPORTED` for
    JavaScript by design (that is what makes this the ingested path), and the
    type-only lexer B038(b) shipped answers a question about a whole FILE, not
    a line. So the set here is: every in-scope line of a file the report
    measured, minus blank lines, minus the lines a mutant already starts on.

    The approximation errs toward OVER-reporting, which is the safe
    direction: an entry says "the tool declined to mutate this line", and that
    is a true statement about the tool for an import line or a closing brace
    just as it is for a genuinely untested expression. Under-reporting would
    be the dangerous direction -- it would hide the case the field exists for,
    a changed line the mutation tool never touched at all.

    Lines come from the report's OWN `source` text, not from the snapshot:
    that is the text the tool actually read, so a line number here means the
    same thing the tool's own line numbers mean.
    """
    positions: list[SourcePosition] = []
    for key, source in sorted(report.sources.items()):
        wire_path = wire_paths[key]
        for index, line in enumerate(source.split("\n"), start=1):
            if not line.strip():
                continue
            if not in_scope(wire_path, index):
                continue
            if (wire_path, index) in mutated_lines:
                continue
            positions.append(SourcePosition(path=wire_path, lineno=index))
    return tuple(sorted(positions, key=lambda position: position.sort_key))


def mutation_pct(mutation: Mutation) -> float:
    """(B046) The mutation score: ``killed / (killed + survived)``, percent.

    The denominator is deliberately NOT ``total``. ``crashed`` says the
    candidate did not produce a valid test result, ``budget_exceeded`` says
    the experiment did not finish, ``hung`` (B091) says a `LivenessRunner`
    stopped it because it had gone idle -- another way of saying the
    experiment did not finish -- and ``equivalent`` says the mutant could
    never have been caught. All four are reported mutation buckets, but none
    is evidence about the tests, so including any of them would move the
    score for reasons that have nothing to do with what the suite does.
    (B098: the arithmetic already excluded these buckets; the public
    enumeration had simply omitted ``crashed``.)

    A zero denominator is ``0.0``, never ``100.0``: this is A-026/A-035's
    0/0-is-100% bug, and the only caller reaches this function on a branch
    where ``survived`` is already non-empty, so the guard is defence rather
    than live arithmetic.
    """
    denominator = len(mutation.killed) + len(mutation.survived)
    if denominator == 0:
        return 0.0
    return 100.0 * len(mutation.killed) / denominator


def judge_mutation(
    baseline: CommandResult,
    mutation: Mutation | Literal["UNSUPPORTED"] | None,
    *,
    fail_under: float = 100.0,
    discarded: int = 0,
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

    **``fail_under`` is the mutation-score FLOOR, and B050/A-427/DA-R22 is
    what made it expressible.** Under v9 this parameter could not exist. It
    had been written here once and removed (A-379), because a floor below 100
    would have broken the one property :class:`~assay.verdict.JudgmentR2`'s
    own docstring promises -- *"an independent consumer can already re-derive
    the R2 claim's status from* :class:`~assay.verdict.Mutation`'s *own bucket
    fields alone"*. The v9 wire had no ``judgment.r2`` field that could record
    WHICH floor was applied, so a lane judging at 90% would have emitted a
    PASS beside recorded survivors with nothing in the document explaining it,
    and :func:`assay.verify._check_r2_rederivation` -- which reuses THIS
    function -- would have correctly called that document a lie.

    v10 records the floor (``judgment.r2.fail_under``, REQUIRED under
    ``producer = "ingested"`` and FORBIDDEN under ``"native"``), so the
    re-derivation reads it FROM the document instead of assuming it. The
    property is preserved, not weakened: the status is still re-derivable
    with no external policy input, because the policy is now IN the artifact.

    **The floor is applied on the ``survived`` branch and nowhere else**
    (DA-R22): a non-empty ``survived`` is ``FAIL``/``MUTANTS_SURVIVED`` iff
    :func:`mutation_pct` is below the floor, otherwise the branch falls
    through to the terminals below it, unchanged. There is exactly ONE
    formula for the score in this package and it is :func:`mutation_pct`;
    the verifier calls these same two functions rather than restating either
    (a second formula is how the two would drift).

    **The default ``100.0`` keeps every existing outcome byte-identical, and
    that is the regression witness.** Any survivor at all makes the score
    strictly less than 100, so a native lane -- which never passes this
    parameter, and whose ``judgment.r2`` is FORBIDDEN from carrying a floor
    -- reaches exactly the terminal it reached before. Only an INGESTED lane,
    which now declares its floor on the wire, can take the fall-through.
    ``config._load_ingested_mutation`` used to refuse any value but ``100.0``
    for precisely this reason; that refusal is gone with the wire field that
    replaced it, and only the ``0.0 <= fail_under <= 100.0`` range check
    remains.

    **``discarded`` is B070/v11's fifth disposition, and it is here for one
    branch only.** It is the length of ``judgment.r2.discarded`` — zero for
    every native lane, which has no discard concept at all — and it is
    subtracted from ``candidate_count`` before the limit-sentinel question is
    asked. Without it, an ingested report whose in-scope mutants were ALL
    invalid (``total 0``, five empty buckets, ``candidate_count`` equal to the
    discard count) reaches the identical bytes a native pre-submission refusal
    has, and would be judged ``BUDGET_EXCEEDED``/``MUTANT_LIMIT_EXCEEDED`` —
    a flat lie about a lane that declared no candidate cap and declined
    nothing. With it that document falls through to
    ``INCONCLUSIVE``/``NO_MUTANTS``, which is what a run that attempted no
    mutant honestly is, and a native document is byte-unchanged because its
    ``discarded`` is zero by contract.
    """
    if mutation is None:
        return baseline.outcome, baseline.reason_code
    # A-183: capability absence, before any payload arithmetic. There is no
    # `Mutation` to read here at all, which is exactly the point -- a
    # zero/zero payload would assert an analysis that never ran.
    if mutation == UNSUPPORTED:
        return Outcome.INCONCLUSIVE, ReasonCode.MUTATION_UNSUPPORTED
    if mutation.total == 0 and mutation.candidate_count - discarded > 0:
        # A-163: the refusal happened BEFORE submission, so this is not a
        # budget the run exhausted while working -- it is one it declined to
        # start against. `LANE_TIMEOUT` would misname it.
        #
        # B070/v11: `- discarded` is what keeps this branch NATIVE. See the
        # docstring; `Mutation.is_limit_sentinel` is the same test with the
        # subtraction fixed at zero, which is correct for every caller that
        # can only see the payload.
        return Outcome.BUDGET_EXCEEDED, ReasonCode.MUTANT_LIMIT_EXCEEDED
    if mutation.total == 0:
        return Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS
    if mutation.crashed:
        return Outcome.ERROR, ReasonCode.EXEC_FAILED
    if mutation.budget_exceeded:
        return Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT
    if mutation.hung:
        # (B091/RW-33, P7 A3) Ranked immediately beside `budget_exceeded`
        # (a genuine elapsed-time expiry still takes precedence when BOTH
        # are non-empty -- an arbitrary but stable tie-break, never load-
        # bearing since `judge_mutation` returns on the first non-empty
        # bucket it finds either way): a `hung` candidate is, like
        # `budget_exceeded`, evidence the run did not finish, never
        # evidence about what the suite caught. `mutation_pct` never reads
        # this bucket (only `killed`/`survived`), so scoring is unaffected
        # by construction -- this is the OUTCOME precedence only.
        return Outcome.BUDGET_EXCEEDED, ReasonCode.CANDIDATE_HUNG
    if mutation.survived and mutation_pct(mutation) < fail_under:
        # B050/A-427/DA-R22. At the default floor of 100.0 this is the v9
        # branch verbatim -- any survivor puts the score below 100 -- so a
        # native lane's outcome is unchanged. An ingested lane that declared
        # a lower floor, and met it, falls through to the terminals below,
        # and the floor it met is on the wire for the verifier to read.
        #
        # `discarded` is a COUNT beside the payload (DA-D4) and never enters
        # `Mutation`'s buckets, so the denominator here is unaffected by
        # construction rather than by an exclusion rule someone must
        # remember.
        return Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED
    if not mutation.killed and not mutation.survived and mutation.equivalent:
        # A-223d. `killed + survived == 0` and something was proven inert:
        # the run attempted real mutants and none of them could ever have
        # been caught, so there is no evidence about the tests here to pass
        # on.
        #
        # (B050) `not mutation.survived` is now stated rather than inherited.
        # Up to v9 the branch above returned on ANY non-empty `survived`, so
        # emptiness was implied here and the condition A-223d actually
        # specifies -- "`killed + survived == 0` with a non-empty
        # `equivalent`", its own words -- could be written as half of itself.
        # A declared floor the run MET now falls through this far with
        # survivors recorded, and calling that "all mutants were equivalent"
        # would be false about the payload. This restores A-223d's stated
        # guard; it does not narrow it.
        return Outcome.INCONCLUSIVE, ReasonCode.ALL_MUTANTS_EQUIVALENT
    return Outcome.PASS, None


def build_mutation_claim(
    baseline: CommandResult,
    mutation: Mutation | Literal["UNSUPPORTED"] | None,
    *,
    fail_under: float = 100.0,
    discarded: int = 0,
) -> Claim:
    """The R2 :class:`~assay.verdict.Claim` from :func:`run_mutation`'s own
    return — the exact mapping ``assay.runner.build_r0_claim`` /
    ``assay.canary.build_canary_claim`` perform, one level over.

    The capability marker attaches NO payload (A-183): ``MUTATION_UNSUPPORTED``
    says no candidate analysis happened, and :class:`~assay.verdict.Claim`
    itself refuses the pairing if a payload is attached anyway.

    ``fail_under`` is passed straight to :func:`judge_mutation` (B050/A-427);
    it defaults to ``100.0``, so a native call site that omits it is the v9
    behaviour exactly. The only caller that supplies it is the INGESTED
    branch of :func:`assay.runner._run_prepared_lane`, which reads it from
    the same ``lane.judge.mutation.fail_under`` that
    ``runner._build_ingested_judgment_r2`` writes onto the wire -- one value,
    one read, so the document cannot record a floor other than the one that
    judged it.

    ``discarded`` (B070/v11) travels the same one-value-one-read path: it is
    ``len(IngestedMutationResult.discarded)``, supplied by the same ingested
    branch that writes the list onto ``judgment.r2``, and it defaults to zero
    so every native call site is byte-unchanged.
    """
    status, reason_code = judge_mutation(
        baseline, mutation, fail_under=fail_under, discarded=discarded
    )
    payload = None if mutation == UNSUPPORTED else mutation
    # (B531) A run that left candidates out (`--only-candidates` /
    # `--exclude-candidates`) reports them never started, which makes the
    # status non-PASS by construction; the detail says why, so the partial
    # run cannot be mistaken for a lane that ran out of time.
    unselected = (
        int((payload.r2_telemetry or {}).get("unselected_total") or 0)
        if isinstance(payload, Mutation)
        else 0
    )
    detail = (
        f"PARTIAL: {unselected} of {payload.total} candidates in scope were "
        "left out by --only-candidates/--exclude-candidates and are not "
        "covered by a resumed or carried-over record; they are reported as "
        "never started (budget_exceeded), so this is not a complete R2 "
        "measurement"
        if unselected and status is not Outcome.PASS and isinstance(payload, Mutation)
        else None
    )
    return Claim(
        rigor="R2",
        source="computed",
        status=status,
        verified_by_assay=True,
        reason_code=reason_code,
        mutation=payload,
        **({"detail": detail, "detail_dropped_bytes": 0} if detail else {}),
    )
