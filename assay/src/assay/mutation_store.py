"""Persisted mutation state for Assay.

Owns record names, generations, write/read and validation, effective judge
identity and provenance, merge/shard/carry combinators, and the terminal-bucket
classifier that record validation re-derives. This is an internal module: the
public surface remains ``assay.mutation.__all__``.

It imports nothing from :mod:`assay.mutation` at runtime. The only names from
that runner module are annotation-only, inside ``TYPE_CHECKING``; importing
them here would create a cycle.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    Iterable,
    Literal,
    Mapping,
    Sequence,
)

from . import _durable
from . import contention as _contention
from . import liveness, observer_windows, safeio
from . import termination as _termination
from .candidate_identity import candidate_id_from_fields
from .candidate_locus import LocusIndex
from .candidate_locus import from_record as _locus_from_record
from .candidate_locus import locus_for_site as _locus_for_site
from .candidate_locus import window_key_for_site as _window_key_for_site
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode
from .guards import is_nonempty_str
from .process_tree import ProcessTreeEvidence
from .records import record
from .resource_limits import CounterDelta, ResourceLimitEvidence
from .verdict import (
    MAX_SHARD_COUNT,
    MUTATION_BUCKETS,
    CandidateOutcomeError,
    MutantEvidence,
    MutantOutcome,
    Mutation,
    MutationExecution,
    MutationWitnessReceipt,
    UnderlyingOutcome,
    iso_utc,
)

if TYPE_CHECKING:  # pragma: no cover -- annotation-only imports; importing at runtime creates cycles
    from .mutation import MutantJob
    from .runner import CommandResult

_CANDIDATE_ID_RE = re.compile(r"[0-9a-f]{64}")

MERGE_MANIFEST_SCHEMA = "assay-merge-manifest/1"
MERGE_MANIFEST_LIMIT = 16 * 1024 * 1024
_MERGE_MANIFEST_KEYS = frozenset({"schema", "judge_sha256", "stores"})
_MERGE_MANIFEST_STORE_KEYS = frozenset({"path", "candidates"})

CombinePolicy = Literal[
    "same-judge",
    "strict-shard",
    "owner-manifest",
    "import-unverified",
    "carry-forward",
]
COMBINE_POLICIES: tuple[CombinePolicy, ...] = (
    "same-judge",
    "strict-shard",
    "owner-manifest",
    "import-unverified",
    "carry-forward",
)
STORE_FLAG_POLICIES: Mapping[str, str] = {
    "--resume": "same-judge",
    "--merge-state": "same-judge",
    "--merge-shard-store": "strict-shard",
    "--merge-manifest": "owner-manifest",
    "--carry-over": "import-unverified",
    "--carry-forward": "carry-forward",
    "--reuse-from": "hint",
}
ProgressWriter = Callable[[Mapping[str, Any]], None]

MUTATION_STATE_RECORD_LIMIT = 1024 * 1024
#: NOT bumped by B088, deliberately. `judge_sha256` is an ADDITIVE field
#: whose absence already has the correct meaning -- "this record predates
#: judge identity, so nothing can vouch for its verdict", which
#: `_load_validated_state_record` answers by re-executing the candidate,
#: exactly what a bump would have bought. The bump would have cost something
#: real: this one constant is also the version of the SHARD SUMMARY document
#: `merge_mutation_shards` accepts, a different schema that B088 does not
#: touch, and it refuses any other value outright -- so bumping it here
#: would have hard-failed every consumer's existing shard merge to buy a
#: distinction ("missing is corruption") that does not hold for a field
#: older writers legitimately never wrote. (That two unrelated documents
#: share one version constant is itself a latent trap; noted as a residual
#: on B088 rather than widened into this change.)
MUTATION_STATE_SCHEMA_VERSION = 1

#: B206 (assay 12): the state record's ``resources`` object keeps only
#: Assay's own clock. Per-candidate resource numbers come from the cgprofile
#: observer and live in the record's ``profile`` key.
_STATE_RESOURCE_KEYS = frozenset({"phase_seconds", "startup_seconds"})
#: Numeric keys written by assay <= 11. They are still ACCEPTED when a record
#: is read (so an old record is a routine cache miss under the new judge label,
#: never a hard error) and are never written.
_LEGACY_STATE_RESOURCE_KEYS = frozenset(
    {"cpu_seconds", "peak_rss_bytes", "write_bytes", "judge_rss_bytes"}
)
_STATE_RESOURCE_PHASE_KEYS = frozenset(
    {"materialize", "command", "integrity", "teardown"}
)
_STATE_RESOURCE_STARTUP_KEYS = frozenset({"to_session_start", "to_first_test"})

#: (B527) Default of ``_load_validated_state_record(raw_override=...)``: read
#: the flat record from the store.
_READ_FROM_STORE: Final = object()

#: (B088) Returned by :func:`_load_validated_state_record` when a record was
#: FOUND, is well-formed, and is still not evidence about this run -- its
#: `schema_version` or its `judge_sha256` says it was produced by something
#: else. Distinct from ``None`` (no record at all) for one reason: an
#: operator has to be able to tell "the cache is cold" from "the cache is
#: being rejected every single run", and before this existed both looked
#: identical -- a silent, permanent cache miss with nothing in the progress
#: stream or the verdict to explain it (round-1 review finding 2).
_RECORD_REJECTED = "REJECTED"

#: The injectable clock, identical in shape to ``assay.runner.Clock`` --
#: duplicated rather than imported (this module's own circular-import note,
#: above): a plain function type alias costs nothing to restate and needs
#: no deferral, unlike the runtime-called ``execute_command``.
Clock = Callable[[], datetime]


@record
class RecordContext:
    """(B566) The judge facts every record validation of one run shares."""

    cold_witness: bool = False
    equivalence_artifact: str | None = None
    baseline_equivalence_sha256: str | None = None
    kill_signal_artifact: str | None = None


class MutationStateError(AssayError):
    """A persisted mutation record or shard summary cannot be trusted."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )


class MutationTerminalEvidenceError(MutationStateError):
    """A present terminal record is malformed rather than an old cache miss."""


class InvalidCandidateSelectionError(AssayError):
    """A requested pilot ID is absent from the current mutation plan."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )


class InvalidRejudgeIdError(AssayError):
    """(B091/D-23, P7 A5) A rejudge selection cannot match this run's plan."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )


def _reject_unknown_rejudge_ids(
    rejudge_ids: frozenset[str], current_ids: set[str]
) -> None:
    unknown_rejudge_ids = rejudge_ids - current_ids
    if unknown_rejudge_ids:
        raise InvalidRejudgeIdError(
            "--rejudge named a candidate id not present in "
            "this run's selected candidate set (unknown, stale "
            "because source bytes changed, or excluded by the selected "
            "operators/shard -- B088's own 'an identity that "
            "no longer matches current reality must not be "
            "silently trusted' principle): "
            f"{sorted(unknown_rejudge_ids)}"
        )


class ShardStoreMergeError(InvalidCandidateSelectionError):
    """(RG-265 review B1) ``--merge-shard-store`` inputs are not exactly one
    complete store per shard of the current plan and judge."""


class MergeManifestError(InvalidCandidateSelectionError):
    """An owner manifest cannot prove the whole current plan and judge."""


@record
class MergeManifestStore:
    path: Path
    candidates: tuple[str, ...]


@record
class MergeManifest:
    path: Path
    judge_sha256: str
    stores: tuple[MergeManifestStore, ...]


@record
class _MergeManifestProblem:
    token: str
    identity: str
    detail: str


@record
class _MergeManifestResult:
    owners: int
    non_owner_duplicates: int
    unplanned_records: int
    target_replaced: int


def _merge_manifest_message(problems: Sequence[_MergeManifestProblem]) -> str:
    """Render the deterministic, bounded refusal every manifest failure uses."""
    ordered = sorted(
        problems,
        key=lambda problem: (problem.token, problem.identity, problem.detail),
    )
    rendered = "; ".join(
        f"{problem.token}: {problem.detail}" for problem in ordered[:20]
    )
    return f"merge manifest refused: {rendered}"


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key {key!r}")
        value[key] = item
    return value


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def load_merge_manifest(path: Path) -> MergeManifest:
    """Read and validate one bounded ``assay-merge-manifest/1`` document.

    The document is parsed before any lane work. Ownership is proved later,
    against the current plan and judge, because those facts do not exist yet.
    """
    manifest_path = Path(path).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = Path(os.path.abspath(manifest_path))
    identity = str(manifest_path)
    problems: list[_MergeManifestProblem] = []

    def malformed(detail: str) -> None:
        problems.append(
            _MergeManifestProblem(
                token="malformed-manifest",
                identity=identity,
                detail=detail,
            )
        )

    try:
        raw = safeio.read_bounded_input(
            manifest_path.parent,
            manifest_path.name,
            limit=MERGE_MANIFEST_LIMIT,
        )
    except AssayError as exc:
        malformed(str(exc))
        raw = None
    if raw is None:
        if not problems:
            malformed("the manifest file does not exist")
        raise LaneConfigError(_merge_manifest_message(problems))
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        malformed(f"invalid UTF-8 JSON: {exc}")
        raise LaneConfigError(_merge_manifest_message(problems)) from exc
    if not isinstance(document, dict) or set(document) != _MERGE_MANIFEST_KEYS:
        malformed(
            "the top-level object must contain exactly "
            f"{sorted(_MERGE_MANIFEST_KEYS)}"
        )
        raise LaneConfigError(_merge_manifest_message(problems))
    if (
        not isinstance(document["schema"], str)
        or document["schema"] != MERGE_MANIFEST_SCHEMA
    ):
        malformed(f"schema must be {MERGE_MANIFEST_SCHEMA!r}")
    judge = document["judge_sha256"]
    if (
        not isinstance(judge, str)
        or _CANDIDATE_ID_RE.fullmatch(judge) is None
    ):
        malformed("judge_sha256 must be a 64-character lowercase hexadecimal digest")
    stores_document = document["stores"]
    if not isinstance(stores_document, list):
        malformed("stores must be an array")
        raise LaneConfigError(_merge_manifest_message(problems))

    manifest_directory = manifest_path.parent
    stores: list[MergeManifestStore] = []
    resolved_paths: dict[Path, str] = {}
    owners: dict[str, MergeManifestStore] = {}
    for store_index, raw_store in enumerate(stores_document):
        if (
            not isinstance(raw_store, dict)
            or set(raw_store) != _MERGE_MANIFEST_STORE_KEYS
        ):
            malformed(
                f"stores[{store_index}] must contain exactly "
                f"{sorted(_MERGE_MANIFEST_STORE_KEYS)}"
            )
            continue
        spelling = raw_store["path"]
        candidates_document = raw_store["candidates"]
        if (
            not isinstance(spelling, str)
            or not spelling
            or "\0" in spelling
        ):
            malformed(f"stores[{store_index}].path must be non-empty text")
            continue
        if not isinstance(candidates_document, list):
            malformed(f"stores[{store_index}].candidates must be an array")
            continue
        candidate_path = Path(spelling)
        store_path = (
            candidate_path
            if candidate_path.is_absolute()
            else manifest_directory / candidate_path
        )
        store_path = store_path.resolve()
        if store_path in resolved_paths:
            problems.append(
                _MergeManifestProblem(
                    token="duplicate-store",
                    identity=str(store_path),
                    detail=f"{spelling}: also named by an earlier store entry",
                )
            )
            continue
        resolved_paths[store_path] = spelling
        if not store_path.is_dir():
            problems.append(
                _MergeManifestProblem(
                    token="missing-store",
                    identity=str(store_path),
                    detail=f"{spelling}: not an existing directory",
                )
            )
            continue
        candidates: list[str] = []
        for candidate_index, candidate in enumerate(candidates_document):
            if (
                not isinstance(candidate, str)
                or _CANDIDATE_ID_RE.fullmatch(candidate) is None
            ):
                malformed(
                    f"stores[{store_index}].candidates[{candidate_index}] is not "
                    "a 64-character lowercase hexadecimal candidate id"
                )
                continue
            if candidate in candidates:
                problems.append(
                    _MergeManifestProblem(
                        token="duplicate-owner",
                        identity=candidate,
                        detail=f"{store_path}: candidate is listed twice",
                    )
                )
                continue
            if candidate in owners:
                problems.append(
                    _MergeManifestProblem(
                        token="duplicate-owner",
                        identity=candidate,
                        detail=(
                            f"{owners[candidate].path}: candidate is also owned "
                            f"by {store_path}"
                        ),
                    )
                )
                continue
            candidates.append(candidate)
        store = MergeManifestStore(path=store_path, candidates=tuple(candidates))
        stores.append(store)
        for candidate in candidates:
            owners[candidate] = store

    if problems:
        raise LaneConfigError(_merge_manifest_message(problems))
    return MergeManifest(
        path=manifest_path,
        judge_sha256=str(judge),
        stores=tuple(stores),
    )


def _locus_fields_for_job(job: MutantJob) -> dict[str, object]:
    """Compute one candidate's optional locus fields for reusable consumers."""
    return _locus_fields(
        path=job.path,
        source=job.original_text.encode("utf-8"),
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        operator=job.site.operator,
        replacement_sha256=job.site.replacement_sha256,
    )


def _locus_fields(
    *,
    path: object,
    source: object,
    start_byte: object,
    end_byte: object,
    operator: object,
    replacement_sha256: object,
) -> dict[str, object]:
    """Return optional record fields for the site, never raising into a run.

    ``locus_for_site`` returning ``None`` is the exact-id fallback, so neither
    field is written. The window key is lookup-only and accompanies A'; it is
    never evidence that a prior outcome remains valid.
    """
    try:
        locus = _locus_for_site(
            path,
            source,
            start_byte,
            end_byte,
            operator,
            replacement_sha256,
        )
        if locus is None:
            return {}
        fields: dict[str, object] = {"locus": locus.to_record()}
        window_key = _window_key_for_site(
            path,
            source,
            start_byte,
            end_byte,
            operator,
            replacement_sha256,
        )
        if window_key is not None:
            fields["window_key"] = window_key
        return fields
    except Exception:  # noqa: BLE001 -- untrusted source must never fail a run
        return {}


def candidate_identity_fields(job: MutantJob) -> dict[str, object]:
    """The six inputs of :func:`candidate_id_from_fields` for *job* (C13)."""
    original_bytes = job.original_text.encode("utf-8")
    replacement_bytes = job.site.apply(original_bytes)
    return {
        "path": job.path,
        "source_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "start_byte": job.site.start_byte,
        "end_byte": job.site.end_byte,
        "mutated_file_sha256": hashlib.sha256(replacement_bytes).hexdigest(),
        "operator": job.site.operator,
    }


def candidate_id(job: MutantJob) -> str:
    """Return the stable digest identity shared by plans, state and shards."""
    return candidate_id_from_fields(**candidate_identity_fields(job))


def plan_sha256(candidate_ids: Sequence[str]) -> str:
    """Hash candidate identities in their full discovery order.

    Each identity is encoded as an ASCII netstring. The order is deliberately
    significant: the digest binds a campaign to the exact plan the executor
    will see, before shard, selection, ledger, or resume filtering.
    """
    digest = hashlib.sha256()
    for identity in candidate_ids:
        digest.update(f"{len(identity)}:{identity},".encode("ascii"))
    return digest.hexdigest()


def _tool_version() -> str:
    """(B088) assay's own version, for :func:`judge_sha256`.

    Imported lazily and defensively: ``assay.__init__`` resolves it through
    ``importlib.metadata`` and already falls back to ``"0+unknown"`` when the
    distribution is not installed, but this function must never be the reason
    a mutation sweep fails. A version that cannot be determined degrades to
    the empty string, which is a stable value -- so records stay mutually
    comparable within such a build rather than each becoming unique.
    """
    from . import __version__

    return __version__ if isinstance(__version__, str) else ""


def mutation_state_record_name(candidate: str) -> str:
    """Return one candidate's state-file NAME, with no directory at all.

    (B066) The identity-to-filename rule, split out from
    :func:`mutation_state_record_path` so the store's ROOT and a record's
    NAME are two separate decisions. The root became a caller's choice with
    ``--state-dir``; the name did not, and must not -- it folds the
    candidate's path, source bytes, span, replacement and operator, which is
    exactly what makes a SHARED store safe by construction: a record either
    matches its identity or is ignored.
    """
    if not isinstance(candidate, str) or _CANDIDATE_ID_RE.fullmatch(candidate) is None:
        raise ValueError(
            f"candidate id must be a 64-character hexadecimal digest, got {candidate!r}"
        )
    return f"{candidate.lower()}.json"


def mutation_state_record_path(candidate: str) -> str:
    """Return the canonical project-relative state-file spelling.

    (B066) The DEFAULT spelling -- what a run with no ``--state-dir`` uses.
    A run that supplies one addresses ``<state-dir>/<name>`` instead, with
    the same name from :func:`mutation_state_record_name`.
    """
    return f".assay/mutation-state/{mutation_state_record_name(candidate)}"


def default_state_root(project_root: Path) -> Path:
    """(B066) Where resume records live when no ``--state-dir`` is given."""
    return Path(project_root) / ".assay" / "mutation-state"


#: (B527) Subdirectory of a state store that keeps the records of OTHER judge
#: identities. The flat ``<store>/<id>.json`` name always holds the record of
#: the judge that wrote it last; before a different judge replaces it, the
#: previous file is hard-linked to ``<store>/judges/<its judge>/<id>.json``
#: (bytes untouched), so no generation is ever overwritten.
JUDGE_GENERATIONS_DIR = "judges"
#: (RG-265 review B1) The shard identity a `--shard K/N` run leaves in its own
#: store; not a `<id>.json` name, so no record reader ever mistakes it.
SHARD_MANIFEST_NAME = "shard.manifest"
SHARD_MANIFEST_SCHEMA = "assay-shard-store/1"
_SHARD_MANIFEST_KEYS = frozenset(
    {"schema", "shard_index", "shard_count", "judge_sha256", "plan_sha256", "candidate_total"}
)
_SHARD_MANIFEST_LIMIT = 64 * 1024

#: (B530) Buckets a carry-over may import. ``crashed``, ``hung`` and
#: ``budget_exceeded`` are unresolved outcomes that can depend on the old
#: resources and judge, so they are re-executed instead.
CARRY_OVER_IMPORTABLE_BUCKETS: frozenset[str] = frozenset({"killed", "survived"})

#: (B529) Buckets whose outcome can depend on memory, CPU, worker count or a
#: wall budget. A resumed record in one of them is re-executed when the run's
#: ``resource_sha256`` differs from the record's.
RESOURCE_SENSITIVE_BUCKETS: frozenset[str] = frozenset(
    {"crashed", "hung", "budget_exceeded"}
)

#: Schema 1 is the original exact-id carry-over block. It remains supported
#: byte-for-byte for stores written before sub-file identity.
CARRY_OVER_SCHEMA_VERSION = 1
#: Schema 2 is emitted by new carry-over imports. Prior-evidence identity
#: lives in top-level ``provenance``; this block names the import target and
#: its operator-facing facts.
CARRY_OVER_SCHEMA_VERSION_V2 = 2
CARRY_FORWARD_REASON = "carry-forward"
_CARRY_OVER_V1_KEYS = frozenset(
    {
        "schema_version",
        "from_judge_sha256",
        "into_judge_sha256",
        "source",
        "reason",
        "imported_at",
        "importer_version",
    }
)
_CARRY_OVER_V2_KEYS = frozenset(
    {
        "schema_version",
        "into_judge_sha256",
        "source",
        "reason",
        "imported_at",
        "importer_version",
    }
)
_PROVENANCE_KINDS = frozenset({"imported", "carried", "replayed"})
_PROVENANCE_MATCHES = frozenset({"exact-id", "locus", "window", "hint"})
_PROVENANCE_KEYS = frozenset(
    {"kind", "from_candidate_id", "from_judge_sha256", "match"}
)
_PROVENANCE_OPTIONAL_KEYS = frozenset({"reason", "replay_node_id"})
_CARRY_OVER_PROVENANCE_KINDS = frozenset({"imported", "carried"})
#: Bound on the operator's ``--carry-over-reason`` text.
CARRY_OVER_REASON_MAX_BYTES = 1024


@record
class FlexOptions:
    """(B529-B531) Flexible-campaign inputs of one native R2 run.

    * *lane_file_name* -- the loaded lane file's name inside the project
      directory; with ``run-gate.toml`` beside it, it is projected out of the
      judge identity (B529). ``None`` keeps the legacy whole-tree digest
      (direct library callers);
    * *carry_over_sources* / *carry_over_reason* -- state directories whose
      finished records are reused across a judge change, with provenance
      (B530). Both or neither;
    * *carry_forward_sources* -- prior-judge record stores searched by exact
      id, function locus, and kill-only window; survivors are carried and
      kills remain pending for a full run (B561/WP3a). Same-effective-judge
      exact-id records are left to resume/merge-state;
    * *merge_sources* -- other state stores (a shard's) whose records of
      THIS judge identity are copied into this run's store and resumed; no
      provenance is needed because the same judge produced them (B530);
    * *only_candidates* / *exclude_candidates* -- execute only these, or all
      but these, of the pending candidates (B531). What is left out and not
      covered by resume or carry-over is reported as never started, so the
      verdict is never a PASS;
    * *shard_stores* -- the STRICT shard merge (RG-265 review B1): exactly
      one store per shard ``0..N-1`` of the current plan, each written by
      ``--shard K/N`` under the current judge and complete. Refused, by
      name, before anything runs otherwise (:func:`_validate_shard_stores`).
    * *merge_manifest* -- the owner manifest (B544): one file designates the
      sole owner of every candidate's flat record. Its proof and owner
      materialization run before ordinary resume, so an owner record wins
      over a differing target record (D3).
    """

    lane_file_name: str | None = None
    carry_over_sources: tuple[Path, ...] = ()
    carry_over_reason: str | None = None
    merge_sources: tuple[Path, ...] = ()
    shard_stores: tuple[Path, ...] = ()
    only_candidates: frozenset[str] | None = None
    exclude_candidates: frozenset[str] | None = None
    carry_forward_sources: tuple[Path, ...] = ()
    merge_manifest: Path | None = None

    def store_sources(self) -> tuple[StoreSource, ...]:
        """The explicitly named stores in their fixed policy/CLI order."""
        return (
            *(StoreSource(path=path, policy="same-judge") for path in self.merge_sources),
            *(StoreSource(path=path, policy="strict-shard") for path in self.shard_stores),
            *(
                (StoreSource(path=self.merge_manifest, policy="owner-manifest"),)
                if self.merge_manifest is not None
                else ()
            ),
            *(
                StoreSource(path=path, policy="import-unverified")
                for path in self.carry_over_sources
            ),
            *(
                StoreSource(path=path, policy="carry-forward")
                for path in self.carry_forward_sources
            ),
        )

    def __post_init__(self) -> None:
        if self.lane_file_name is not None and (
            not is_nonempty_str(self.lane_file_name)
            or "/" in self.lane_file_name
            or self.lane_file_name in (".", "..")
        ):
            raise ValueError("FlexOptions.lane_file_name must be a plain file name")
        if bool(self.carry_over_sources) != (self.carry_over_reason is not None):
            raise ValueError(
                "carry-over sources and a carry-over reason are required together"
            )
        if not isinstance(self.carry_forward_sources, tuple) or any(
            not isinstance(path, Path) for path in self.carry_forward_sources
        ):
            raise ValueError("FlexOptions.carry_forward_sources must be a tuple of paths")
        if self.carry_over_reason is not None:
            reason = self.carry_over_reason
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("the carry-over reason must be non-empty text")
            if len(reason.encode("utf-8")) > CARRY_OVER_REASON_MAX_BYTES:
                raise ValueError(
                    f"the carry-over reason exceeds {CARRY_OVER_REASON_MAX_BYTES} bytes"
                )
        if self.shard_stores and self.selection_active:
            raise ValueError(
                "a strict shard merge cannot be combined with a candidate selection"
            )
        if self.merge_manifest is not None and not isinstance(self.merge_manifest, Path):
            raise ValueError("FlexOptions.merge_manifest must be a path or None")
        if self.merge_manifest is not None and self.shard_stores:
            raise ValueError(
                "an owner manifest cannot be combined with a strict shard merge"
            )
        if self.merge_manifest is not None and self.selection_active:
            raise ValueError(
                "an owner manifest cannot be combined with a candidate selection"
            )
        for name in ("only_candidates", "exclude_candidates"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, frozenset) or any(
                not isinstance(item, str) or _CANDIDATE_ID_RE.fullmatch(item) is None
                for item in value
            ):
                raise ValueError(f"FlexOptions.{name} must be a frozenset of candidate ids")

    @property
    def reads_other_stores(self) -> bool:
        return bool(
            self.carry_over_sources
            or self.carry_forward_sources
            or self.merge_sources
            or self.shard_stores
            or self.merge_manifest is not None
        )

    @property
    def reads_carry_over_stores(self) -> bool:
        """Whether the legacy exact-id carry-over/merge path has a source."""
        return bool(self.carry_over_sources or self.merge_sources or self.shard_stores)

    @property
    def selection_active(self) -> bool:
        return self.only_candidates is not None or self.exclude_candidates is not None


@record
class StoreSource:
    """One explicitly named mutation store and the policy that reads it."""

    path: Path
    policy: CombinePolicy


@record
class CombineRequest:
    """Every fact the four combine stages share during one mutation run."""

    jobs: Sequence[MutantJob]
    judge: str | None
    state_root: Path | None
    state_root_fd: int | None
    state_root_guard: Callable[[], None] | None
    campaign_deadline_sha256: str | None
    context: RecordContext
    resume: bool
    rejudge_ids: frozenset[str]
    rejudge_outcomes: frozenset[str]
    resource_sha256: str | None
    carry_over_reason: str | None
    clock: Clock
    deadline_check: Callable[[], float]
    write_progress: ProgressWriter | None
    total: int


@record
class Hint:
    """Prior kill evidence retained for P-assay-13's isolated replay."""

    candidate_id: str
    node_id: str
    source: Literal["verdict", "store"]
    source_ref: str
    from_candidate_id: str
    from_judge_sha256: str | None
    match: Literal["exact-id", "locus", "window"]


@record
class CombineResult:
    """The records and telemetry produced by :func:`combine`."""

    records: tuple[Mapping[str, Any], ...]
    rejected_total: int
    rejudged_total: int
    resource_rejudged_total: int
    promoted_total: int
    imported_total: int
    locus_unavailable_total: int
    rejudged_candidate_ids: frozenset[str]
    hints: tuple[Hint, ...]


def _provenance_record(
    *,
    kind: str,
    from_candidate_id: str,
    from_judge_sha256: str,
    match: str,
    reason: str | None = None,
    replay_node_id: str | None = None,
) -> dict[str, Any]:
    """The one constructor for a state record's top-level provenance object."""
    value: dict[str, Any] = {
        "kind": kind,
        "from_candidate_id": from_candidate_id,
        "from_judge_sha256": from_judge_sha256,
        "match": match,
    }
    if reason is not None:
        value["reason"] = reason
    if replay_node_id is not None:
        value["replay_node_id"] = replay_node_id
    return value


def _valid_provenance(value: Any) -> bool:
    """Whether *value* has the closed shape of a state-record provenance."""
    if not isinstance(value, Mapping):
        return False
    if set(value) - _PROVENANCE_KEYS - _PROVENANCE_OPTIONAL_KEYS:
        return False
    if not _PROVENANCE_KEYS <= set(value):
        return False
    kind = value["kind"]
    match = value["match"]
    if (
        not isinstance(kind, str)
        or kind not in _PROVENANCE_KINDS
        or not isinstance(match, str)
        or match not in _PROVENANCE_MATCHES
    ):
        return False
    for key in ("from_candidate_id", "from_judge_sha256"):
        field = value[key]
        if not isinstance(field, str) or _CANDIDATE_ID_RE.fullmatch(field) is None:
            return False
    if "reason" in value:
        reason = value["reason"]
        if not isinstance(reason, str) or not reason:
            return False
        try:
            if len(reason.encode("utf-8")) > CARRY_OVER_REASON_MAX_BYTES:
                return False
        except UnicodeEncodeError:
            return False
    if "replay_node_id" in value:
        replay_node_id = value["replay_node_id"]
        if (
            kind != "replayed"
            or not isinstance(replay_node_id, str)
            or not replay_node_id
        ):
            return False
    return True


def _valid_carry_over(value: Any) -> bool:
    """Whether *value* is a supported carry-over block.

    Schema 1 is the original exact-id import block. It remains readable and
    derives the canonical provenance object through :func:`record_provenance`.
    Schema 2 names only the import target and operator-facing facts; its
    prior-evidence identity lives in top-level ``provenance``.
    """
    if not isinstance(value, Mapping):
        return False
    version = value.get("schema_version")
    if type(version) is not int:
        return False
    if version == CARRY_OVER_SCHEMA_VERSION:
        expected_keys = _CARRY_OVER_V1_KEYS
    elif version == CARRY_OVER_SCHEMA_VERSION_V2:
        expected_keys = _CARRY_OVER_V2_KEYS
    else:
        return False
    if set(value) != expected_keys:
        return False
    digest_keys = (
        ("from_judge_sha256", "into_judge_sha256")
        if version == CARRY_OVER_SCHEMA_VERSION
        else ("into_judge_sha256",)
    )
    for key in digest_keys:
        if not isinstance(value[key], str) or _CANDIDATE_ID_RE.fullmatch(value[key]) is None:
            return False
    if not all(
        isinstance(value[key], str) and value[key]
        for key in ("source", "reason", "imported_at", "importer_version")
    ):
        return False
    return True


def record_provenance(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Read one state record's canonical provenance, or ``None``.

    A schema-2 top-level object is returned only when its shape is valid. A
    schema-1 ``carry_over`` block is read-only compatibility and is derived as
    ``{kind: imported, ..., match: exact-id}``. Fresh records have no
    provenance. This reader never raises for malformed stored JSON shapes.
    """
    if not isinstance(payload, Mapping):
        return None
    if "carry_over" in payload:
        carry = payload["carry_over"]
        if not _valid_carry_over(carry):
            return None
        if carry["schema_version"] == CARRY_OVER_SCHEMA_VERSION:
            if "provenance" in payload:
                return None
            candidate = payload.get("candidate_id")
            if (
                not isinstance(candidate, str)
                or _CANDIDATE_ID_RE.fullmatch(candidate) is None
            ):
                return None
            return _provenance_record(
                kind="imported",
                from_candidate_id=candidate,
                from_judge_sha256=carry["from_judge_sha256"],
                match="exact-id",
                reason=carry["reason"],
            )
    if "provenance" not in payload:
        return None
    value = payload["provenance"]
    if not _valid_provenance(value):
        return None
    if (
        value["match"] == "exact-id"
        and value["from_candidate_id"] != payload.get("candidate_id")
    ):
        return None
    return value


def record_effective_judge(payload: Mapping[str, Any]) -> str | None:
    """(B530) The judge identity a state record is evidence FOR.

    A record executed by judge J carries ``judge_sha256 = J`` and is evidence
    for J. A record imported or carried across a judge change keeps its
    executing judge in ``judge_sha256`` (never rewritten) and names the judge
    it was imported into in ``carry_over.into_judge_sha256``; that is the
    identity it resumes under. ``None`` for a record with no usable judge,
    invalid provenance, or a malformed ``carry_over`` block.
    """
    judge = payload.get("judge_sha256")
    if not isinstance(judge, str) or not judge:
        return None
    if "carry_over" not in payload:
        if "provenance" not in payload:
            return judge
        provenance = record_provenance(payload)
        if provenance is None or provenance["kind"] in _CARRY_OVER_PROVENANCE_KINDS:
            return None
        return judge
    carry = payload["carry_over"]
    if not _valid_carry_over(carry):
        return None
    if carry["schema_version"] == CARRY_OVER_SCHEMA_VERSION:
        if "provenance" in payload:
            return None
        if carry["from_judge_sha256"] != judge:
            return None
        return carry["into_judge_sha256"]
    provenance = record_provenance(payload)
    if (
        provenance is None
        or provenance["kind"] not in _CARRY_OVER_PROVENANCE_KINDS
        or provenance["from_judge_sha256"] != judge
    ):
        return None
    return carry["into_judge_sha256"]


def _set_carry_over_provenance(
    payload: dict[str, Any],
    *,
    kind: Literal["imported", "carried"],
    match: Literal["exact-id", "locus", "window"],
    from_candidate_id: str,
    from_judge_sha256: str,
    into_judge_sha256: str,
    source: Path,
    reason: str,
    imported_at: str,
) -> None:
    """Write the one valid schema-2 carry block and its top-level provenance."""
    payload["carry_over"] = {
        "schema_version": CARRY_OVER_SCHEMA_VERSION_V2,
        "into_judge_sha256": into_judge_sha256,
        "source": str(source),
        "reason": reason,
        "imported_at": imported_at,
        "importer_version": _tool_version() or "unknown",
    }
    payload["provenance"] = _provenance_record(
        kind=kind,
        from_candidate_id=from_candidate_id,
        from_judge_sha256=from_judge_sha256,
        match=match,
        reason=reason,
    )


def _open_state_dir_fd(state_root: Path | str, state_root_fd: int | None) -> int:
    if state_root_fd is not None:
        return os.dup(state_root_fd)
    return os.open(Path(state_root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _open_generation_dir(
    root_fd: int, judge: str, *, create: bool
) -> int | None:
    """Open ``judges/<judge>`` below *root_fd* without following links."""
    if _CANDIDATE_ID_RE.fullmatch(judge) is None:
        raise ValueError(f"judge identity must be a SHA-256 digest, got {judge!r}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current = root_fd
    opened: list[int] = []
    try:
        for name in (JUDGE_GENERATIONS_DIR, judge):
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                else:
                    # A new directory entry is durable only once its PARENT
                    # is synced. The flat record is replaced after this
                    # archive, so without it a crash could keep the new
                    # flat record and lose the path to the archived one.
                    _durable.fsync(current)
            try:
                current = os.open(name, flags, dir_fd=current)
            except FileNotFoundError:
                return None
            opened.append(current)
        result = opened.pop()
        return result
    finally:
        for descriptor in opened:
            os.close(descriptor)


def _read_generation_record_bytes(
    state_root: Path | str, identity: str, judge: str, *, state_root_fd: int | None
) -> bytes | None:
    """(B527) Read ``judges/<judge>/<id>.json`` from a store, or ``None``."""
    try:
        root_fd = _open_state_dir_fd(state_root, state_root_fd)
    except FileNotFoundError:
        return None
    try:
        if _CANDIDATE_ID_RE.fullmatch(judge) is None:
            return None
        try:
            generation_fd = _open_generation_dir(root_fd, judge, create=False)
        except NotADirectoryError:
            return None
        if generation_fd is None:
            return None
        try:
            return _read_state_record_bytes(
                Path(state_root), identity, state_root_fd=generation_fd
            )
        finally:
            os.close(generation_fd)
    finally:
        os.close(root_fd)


def _archive_foreign_generation(
    root_fd: int, destination_name: str, new_judge: str | None
) -> bool:
    """(B527) Preserve a record of another judge before it is replaced.

    Reads the current ``<id>.json``; when its effective judge differs from
    *new_judge*, hard-links it to ``judges/<its judge>/<id>.json`` (replacing
    an older copy of the same generation). The bytes are never rewritten.
    A missing, unreadable or judge-less file is left to the normal path.
    Returns whether a record was archived.
    """
    try:
        descriptor = os.open(
            destination_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
    except OSError:
        return False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MUTATION_STATE_RECORD_LIMIT:
            return False
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(MUTATION_STATE_RECORD_LIMIT + 1)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    if not isinstance(payload, dict):
        return False
    previous = record_effective_judge(payload)
    if (
        previous is None
        or previous == new_judge
        or _CANDIDATE_ID_RE.fullmatch(previous) is None
    ):
        # Only a real SHA-256 identity names a generation directory.
        return False
    generation_fd = _open_generation_dir(root_fd, previous, create=True)
    assert generation_fd is not None
    try:
        temporary = f".archive-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        os.link(
            destination_name,
            temporary,
            src_dir_fd=root_fd,
            dst_dir_fd=generation_fd,
            follow_symlinks=False,
        )
        try:
            os.replace(
                temporary,
                destination_name,
                src_dir_fd=generation_fd,
                dst_dir_fd=generation_fd,
            )
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=generation_fd)
            except FileNotFoundError:
                pass
            raise
        _durable.fsync(generation_fd)
    finally:
        os.close(generation_fd)
    return True


def terminal_record_judges(
    state_root: Path | str, identity: str
) -> tuple[tuple[str, str], ...]:
    """(B531) ``(effective judge, bucket)`` of every record for *identity*.

    Reads the flat record and every ``judges/*/`` generation of one store,
    read-only, skipping anything unreadable. Used by ``assay plan
    --list-pending`` to decide what is still to do; it never vouches for a
    record (resume and carry-over re-validate everything).
    """
    name = mutation_state_record_name(identity)
    found: list[tuple[str, str]] = []
    relatives = [name]
    generations = Path(state_root) / JUDGE_GENERATIONS_DIR
    try:
        with os.scandir(generations) as children:
            for child in children:
                if _CANDIDATE_ID_RE.fullmatch(child.name) and child.is_dir(
                    follow_symlinks=False
                ):
                    relatives.append(f"{JUDGE_GENERATIONS_DIR}/{child.name}/{name}")
    except (FileNotFoundError, NotADirectoryError):
        pass
    for relative in sorted(set(relatives)):
        try:
            raw = safeio.read_bounded_input(
                Path(state_root), relative, limit=MUTATION_STATE_RECORD_LIMIT
            )
        except AssayError:
            continue
        if raw is None:
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(payload, dict) or payload.get("candidate_id") != identity:
            continue
        judge = record_effective_judge(payload)
        bucket = payload.get("outcome_bucket")
        if judge is not None and isinstance(bucket, str):
            found.append((judge, bucket))
    return tuple(found)


def _write_mutation_state_record(
    state_root: Path,
    payload: Mapping[str, Any],
    *,
    state_root_fd: int | None = None,
    commit_guard: Callable[[], None] | None = None,
) -> None:
    """(B066) Write one record into *state_root*, whatever root that is.

    The parameter used to be the project root and the ``.assay/
    mutation-state/`` tail was joined on here, which is precisely why resume
    was inert exactly where budget-capped retries happen most: an ephemeral
    per-run worktree carries its own empty store away with it. The root is
    now the caller's, so a durable directory can be shared across worktrees;
    the record's NAME is unchanged and still folds the candidate's full
    identity, so a shared store stays safe by construction.
    """
    parent = Path(state_root)
    destination_name = mutation_state_record_name(payload["candidate_id"])
    if state_root_fd is None:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", dir=parent)
    else:
        # The CLI passes the directory descriptor admitted by its state-store
        # lock. A path may be moved and replaced after the final identity
        # check; descriptor-relative creation and replace keep this write in
        # the directory that was locked instead of following that new path.
        temporary_name = f".state-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=state_root_fd,
        )
    backup_name: str | None = None
    published = False
    commit_accepted = False

    def sync_state_directory() -> None:
        if state_root_fd is not None:
            _durable.fsync(state_root_fd)
            return
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            _durable.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def link_previous_record(name: str) -> None:
        if state_root_fd is None:
            os.link(
                parent / destination_name,
                parent / name,
                follow_symlinks=False,
            )
        else:
            os.link(
                destination_name,
                name,
                src_dir_fd=state_root_fd,
                dst_dir_fd=state_root_fd,
                follow_symlinks=False,
            )

    def replace_state_name(source: str, destination: str) -> None:
        if state_root_fd is None:
            os.replace(parent / source, parent / destination)
        else:
            os.replace(
                source,
                destination,
                src_dir_fd=state_root_fd,
                dst_dir_fd=state_root_fd,
            )

    def unlink_state_name(name: str) -> None:
        if state_root_fd is None:
            os.unlink(parent / name)
        else:
            os.unlink(name, dir_fd=state_root_fd)

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            _durable.fsync(stream.fileno())
        # The record becomes reusable at the atomic replacement below. Check
        # after serialization and fsync so a deadline or termination during
        # the write cannot commit a candidate outcome.
        if commit_guard is not None:
            commit_guard()
        # (B527) A record of ANOTHER judge identity is never replaced: it is
        # hard-linked into its own generation first, bytes untouched.
        archive_fd = _open_state_dir_fd(parent, state_root_fd)
        try:
            _archive_foreign_generation(
                archive_fd, destination_name, record_effective_judge(payload)
            )
        finally:
            os.close(archive_fd)
        backup_candidate = f".state-backup-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        try:
            link_previous_record(backup_candidate)
        except FileNotFoundError:
            pass
        else:
            backup_name = backup_candidate
        # Preserve the previous state until the replacement is durable even
        # when no deadline guard was requested. Backup creation can itself
        # take time, so guarded commits recheck immediately before replace.
        if commit_guard is not None:
            commit_guard()

        replace_state_name(temporary_name, destination_name)
        published = True
        sync_state_directory()
        if commit_guard is not None:
            commit_guard()
        commit_accepted = True

        if backup_name is not None:
            unlink_state_name(backup_name)
            backup_name = None
            sync_state_directory()
    except BaseException:
        if not commit_accepted:
            try:
                if published:
                    if backup_name is None:
                        try:
                            unlink_state_name(destination_name)
                        except FileNotFoundError:
                            pass
                    else:
                        replace_state_name(backup_name, destination_name)
                        backup_name = None
                    sync_state_directory()
                elif backup_name is not None:
                    unlink_state_name(backup_name)
                    backup_name = None
                    sync_state_directory()
            except OSError as rollback_error:
                raise MutationStateError(
                    "Assay could not roll back a candidate state record after "
                    "its commit failed"
                ) from rollback_error
        elif backup_name is not None:
            # The new record passed its deadline guard and is durable. A
            # leftover hidden hard link is not a resumable record; try to
            # remove it without undoing the accepted record.
            try:
                unlink_state_name(backup_name)
                sync_state_directory()
            except OSError:
                pass
        try:
            if state_root_fd is None:
                os.unlink(temporary_name)
            else:
                os.unlink(temporary_name, dir_fd=state_root_fd)
        except FileNotFoundError:
            pass
        raise


def _valid_hung_resource_evidence(value: Any) -> bool:
    """Whether a cached ``hung`` bucket retains a complete supporting trace.

    Mutation-state files are resumable evidence. A historical or malformed
    hang without its time-aligned trace is a cache miss, not a trusted verdict.

    (P4) TWO decisions are now accepted, from two DIFFERENT policies:

    * the idle rule's ``pressure-adjusted-idle-v1`` (``idle-hang`` /
      ``session-finish-hang``), validated below against its full
      pressure-adjusted trace; and
    * the CPU-without-progress bounds (``cpu-without-progress`` /
      ``per-test-cpu-bound``), validated by :func:`_valid_cpu_hang_evidence`
      against the CPU series they were derived from -- they never consulted
      pressure, so the idle trace's snapshot rules must not be applied to
      them (and must not be quietly relaxed for the idle ones either).
    """
    if not isinstance(value, Mapping):
        return False
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or type(value.get("candidate_pid")) is not int
        or value["candidate_pid"] <= 0
    ):
        return False
    if value.get("policy") == liveness.CPU_PROGRESS_POLICY:
        return _valid_cpu_hang_evidence(value)
    if (
        value.get("policy") != liveness.IDLE_HANG_POLICY
        or value.get("decision") not in liveness.IDLE_HANG_DECISIONS
        or value.get("candidate_cpu_source") != "process-tree-cpu-seconds"
        or value.get("trace_complete") is not True
        or value.get("trace_truncated") is not False
    ):
        return False

    def finite_number(item: Any) -> bool:
        return type(item) in (int, float) and math.isfinite(item)

    def complete_snapshot(item: Any) -> bool:
        return (
            isinstance(item, Mapping)
            and type(item.get("schema_version")) is int
            and item["schema_version"] == 1
            and item.get("status") == "available"
            and isinstance(item.get("cgroup_identity"), str)
            and bool(item["cgroup_identity"])
            and liveness.compare_resource_snapshots(item, item)[0] == "clear"
        )

    top_fields = (
        "wall_elapsed_s",
        "eligible_elapsed_s",
        "idle_eligible_s",
        "required_idle_eligible_s",
        "required_cpu_growth_window_s",
        "required_cpu_growth_floor_s",
    )
    if any(not finite_number(value.get(key)) for key in top_fields):
        return False
    if (
        value["wall_elapsed_s"] < 0
        or value["eligible_elapsed_s"] < 0
        or value["eligible_elapsed_s"] > value["wall_elapsed_s"]
        or value["idle_eligible_s"] < value["required_idle_eligible_s"]
        or value["idle_eligible_s"] > value["eligible_elapsed_s"]
        or value["required_idle_eligible_s"] <= 0
        or value["required_cpu_growth_window_s"] != liveness._HUNG_CPU_WINDOW_S
        or value["required_cpu_growth_floor_s"] != liveness._HUNG_CPU_GROWTH_FLOOR_S
    ):
        return False

    samples = value.get("samples")
    if (
        not isinstance(samples, list)
        or not samples
        or len(samples) > liveness._LIVENESS_RESOURCE_TRACE_MAX_SAMPLES
    ):
        return False
    previous_wall = -math.inf
    previous_eligible = -math.inf
    previous_progress: tuple[int, int, int] | None = None
    cpu_suffix: list[tuple[float, float]] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            return False
        wall = sample.get("wall_elapsed_s")
        eligible = sample.get("eligible_elapsed_s")
        eligible_interval = sample.get("eligible_interval_s")
        cpu = sample.get("candidate_cpu_s")
        progress = tuple(
            sample.get(key)
            for key in ("event_count", "stdout_bytes", "stderr_bytes")
        )
        if (
            not finite_number(wall)
            or not finite_number(eligible)
            or not finite_number(eligible_interval)
            or wall < 0
            or eligible < 0
            or eligible > wall + 0.0011
            or wall < previous_wall
            or eligible < previous_eligible
            or eligible_interval < 0
            or (cpu is not None and (not finite_number(cpu) or cpu < 0))
            or any(type(count) is not int or count < 0 for count in progress)
            or (
                previous_progress is not None
                and any(
                    current > previous
                    for current, previous in zip(progress, previous_progress)
                )
            )
        ):
            return False
        previous_progress = progress
        prior_wall = previous_wall
        prior_eligible = previous_eligible
        previous_wall = wall
        previous_eligible = eligible

        interval_kind = sample.get("resource_interval")
        if interval_kind not in {"unknown", "clear", "stalled"}:
            return False
        resources = sample.get("resources")
        if not complete_snapshot(resources):
            return False
        if index > 0 and interval_kind == "unknown":
            return False
        if interval_kind == "unknown":
            if "previous_resources" not in sample:
                return False
            previous_snapshot = sample["previous_resources"]
            if previous_snapshot is not None and (
                not complete_snapshot(previous_snapshot)
                or liveness.compare_resource_snapshots(
                    previous_snapshot, resources
                )[0]
                != "unknown"
            ):
                return False
            expected_deltas: Mapping[str, int] = {}
        else:
            if index == 0:
                previous_snapshot = sample.get("previous_resources")
                if not complete_snapshot(previous_snapshot):
                    return False
            else:
                if "previous_resources" in sample:
                    return False
                previous_snapshot = samples[index - 1].get("resources")
            expected_status, expected_deltas = liveness.compare_resource_snapshots(
                previous_snapshot, resources
            )
            if expected_status != interval_kind:
                return False
        if interval_kind != "clear" and eligible_interval != 0:
            # A compacted `stalled` row can summarize clear time plus a later
            # resource-counter increase. The endpoints still prove the exact
            # eligible-time delta and the aggregate pressure deltas; an
            # un-compacted stalled interval remains wholly ineligible.
            if not (
                interval_kind == "stalled"
                and value.get("trace_compacted") is True
            ):
                return False
        deltas = sample.get("resource_deltas")
        if not isinstance(deltas, Mapping):
            return False
        if any(type(delta) is not int or delta <= 0 for delta in deltas.values()):
            return False
        if (interval_kind == "clear" and deltas) or (
            interval_kind == "stalled" and not deltas
        ) or (interval_kind == "unknown" and deltas):
            return False
        if dict(deltas) != dict(expected_deltas):
            return False
        if index > 0:
            eligible_delta = eligible - prior_eligible
            wall_delta = wall - prior_wall
            if not math.isclose(
                eligible_delta,
                eligible_interval,
                rel_tol=0.0,
                abs_tol=0.0011,
            ):
                return False
            if interval_kind == "clear" and not math.isclose(
                wall_delta,
                eligible_interval,
                rel_tol=0.0,
                abs_tol=0.0011,
            ):
                return False
            if interval_kind == "stalled" and wall_delta + 0.0011 < eligible_interval:
                return False
        if interval_kind == "clear" and cpu is not None:
            # A departed descendant can lower the live process-tree total.
            # A quiet window cannot span that discontinuity, even if the
            # final reading is below the older baseline.
            if cpu_suffix and cpu < cpu_suffix[-1][1]:
                cpu_suffix.clear()
            cpu_suffix.append((eligible, float(cpu)))
        else:
            cpu_suffix.clear()

    if samples[-1].get("wall_elapsed_s") != value["wall_elapsed_s"]:
        return False
    if samples[-1].get("eligible_elapsed_s") != value["eligible_elapsed_s"]:
        return False
    # The monitor starts a new trace at each progress event or unknown
    # resource interval. Its first eligible timestamp is therefore the start
    # of the claimed idle span; a larger top-level idle value is unproven.
    if not math.isclose(
        value["idle_eligible_s"],
        value["eligible_elapsed_s"] - samples[0]["eligible_elapsed_s"],
        rel_tol=0.0,
        abs_tol=0.0011,
    ):
        return False
    finish_seen = value.get("candidate_session_finish_seen")
    finish_at = value.get("session_finish_eligible_s")
    if value["decision"] == "session-finish-hang":
        if (
            finish_seen is not True
            or not finite_number(finish_at)
            or finish_at < 0
            or finish_at > value["eligible_elapsed_s"]
        ):
            return False
    elif finish_seen is True:
        if (
            not finite_number(finish_at)
            or finish_at < 0
            or finish_at > value["eligible_elapsed_s"]
        ):
            return False
    elif finish_seen is not False or finish_at is not None:
        return False

    # Idle/CPU classification is valid only when the retained contiguous
    # clear-interval CPU samples cover the actual trailing comparison window.
    cpu_window = liveness._HUNG_CPU_WINDOW_S
    cpu_floor = liveness._HUNG_CPU_GROWTH_FLOOR_S
    if len(cpu_suffix) < 2:
        return False
    current_eligible, current_cpu = cpu_suffix[-1]
    baseline_candidates = [
        (sample_eligible, sample_cpu)
        for sample_eligible, sample_cpu in cpu_suffix[:-1]
        if current_eligible - sample_eligible >= cpu_window
    ]
    if not baseline_candidates:
        return False
    _baseline_eligible, baseline_cpu = baseline_candidates[-1]
    if current_cpu - baseline_cpu >= cpu_floor:
        return False
    if value["decision"] == "session-finish-hang":
        return (
            value["eligible_elapsed_s"] - finish_at
            >= liveness._HUNG_SESSION_FINISH_GRACE_S
            and value["required_idle_eligible_s"]
            >= liveness._HUNG_SESSION_FINISH_GRACE_S
        )
    return True


def _valid_cpu_hang_evidence(value: Mapping[str, Any]) -> bool:
    """(P4) Whether a CPU-without-progress hang carries the numbers that
    justify it: a bound, a measured value above it, and a readable CPU
    series -- plus, for the per-test decision, the nodeid and ITS baseline.

    A record whose measured CPU does not reach its own stated bound is not
    evidence of a hang (it is a writer bug or a hand-edited file), and a
    record with no bound is a claim with nothing behind it; both are cache
    misses, never trusted verdicts.
    """

    def finite_number(item: Any) -> bool:
        return (
            type(item) in (int, float)
            and math.isfinite(item)
        )

    decision = value.get("decision")
    if decision not in liveness.CPU_HANG_DECISIONS:
        return False
    if value.get("candidate_cpu_source") != liveness.CPU_PROGRESS_SOURCE:
        return False
    top = (
        "wall_elapsed_s",
        "candidate_cpu_s",
        "cpu_since_last_completed_event_s",
        "required_cpu_s",
    )
    if any(not finite_number(value.get(key)) for key in top):
        return False
    if any(value[key] < 0 for key in top):
        return False
    if value["candidate_cpu_s"] < value["cpu_since_last_completed_event_s"]:
        # The series is monotonic, so the span since the last completed event
        # can never exceed the total it was measured on.
        return False
    if value["required_cpu_s"] <= 0:
        return False
    if value["cpu_since_last_completed_event_s"] < value["required_cpu_s"]:
        return False
    completed = value.get("completed_events")
    if type(completed) is not int or completed < 0:
        return False
    if not isinstance(value.get("trace_compacted"), bool):
        return False
    if not isinstance(value.get("resources"), Mapping):
        return False
    if decision == liveness.per_test_cpu_bound_decision:
        if (
            not isinstance(value.get("test_nodeid"), str)
            or not value["test_nodeid"]
            or not finite_number(value.get("per_test_baseline_s"))
            or value["per_test_baseline_s"] < 0
            or not finite_number(value.get("per_test_cpu_s"))
            or value["per_test_cpu_s"] < 0
            or value.get("per_test_baseline_s") is None
        ):
            return False
        # The bound actually applied is the formula's, re-derived here rather
        # than trusted from the record: one formula, so the writer and the
        # validator cannot disagree about what bounded the run.
        if not math.isclose(
            value["required_cpu_s"],
            liveness.per_test_cpu_bound_from_baseline(value["per_test_baseline_s"]),
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            return False
        if value["per_test_cpu_s"] < value["required_cpu_s"]:
            return False
    return True


# CD18: public for assay_analysis.
valid_hung_resource_evidence = _valid_hung_resource_evidence


def _read_state_record_bytes(
    state_root: Path,
    identity: str,
    *,
    state_root_fd: int | None,
) -> bytes | None:
    """Read one bounded record from the admitted state directory when given."""
    name = mutation_state_record_name(identity)
    if state_root_fd is None:
        return safeio.read_bounded_input(
            Path(state_root), name, limit=MUTATION_STATE_RECORD_LIMIT
        )
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=state_root_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MutationStateError(
            f"mutation-state record {identity} could not be opened safely: {exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MutationStateError(
                f"mutation-state record {identity} is not a regular file"
            )
        if info.st_size > MUTATION_STATE_RECORD_LIMIT:
            raise MutationStateError(
                f"mutation-state record {identity} exceeds the "
                f"{MUTATION_STATE_RECORD_LIMIT}-byte limit"
            )
        chunks = bytearray()
        while len(chunks) <= MUTATION_STATE_RECORD_LIMIT:
            piece = os.read(
                descriptor,
                min(64 * 1024, MUTATION_STATE_RECORD_LIMIT + 1 - len(chunks)),
            )
            if not piece:
                break
            chunks.extend(piece)
        if len(chunks) > MUTATION_STATE_RECORD_LIMIT:
            raise MutationStateError(
                f"mutation-state record {identity} exceeds the "
                f"{MUTATION_STATE_RECORD_LIMIT}-byte limit"
            )
        return bytes(chunks)
    except MutationStateError:
        raise
    except OSError as exc:
        raise MutationStateError(
            f"mutation-state record {identity} could not be read safely: {exc}"
        ) from exc
    finally:
        os.close(descriptor)


def _terminal_result_matches_bucket(
    payload: Mapping[str, Any],
    *,
    equivalence_artifact: str | None = None,
    baseline_equivalence_sha256: str | None = None,
    kill_signal_artifact: str | None = None,
    cold_witness: bool = False,
    resource_limit_evidence: ResourceLimitEvidence | None = None,
    process_tree_evidence: ProcessTreeEvidence | None = None,
) -> bool:
    """Validate persisted terminal facts and their classified bucket.

    Missing terminal data is a legacy cache miss. Present malformed data is
    corruption and raises ``ValueError`` for the state-record boundary to
    report distinctly.
    """
    if "terminal_result" not in payload:
        return False
    terminal = payload["terminal_result"]
    if not isinstance(terminal, Mapping) or set(terminal) != {
        "outcome",
        "reason_code",
        "returncode",
    }:
        raise ValueError(
            "terminal_result must contain exactly outcome, reason_code and returncode"
        )
    if not isinstance(terminal["outcome"], str):
        raise ValueError("terminal_result.outcome must be a string")
    if terminal["reason_code"] is not None and not isinstance(
        terminal["reason_code"], str
    ):
        raise ValueError("terminal_result.reason_code must be a string or null")
    if terminal["returncode"] is not None and type(terminal["returncode"]) is not int:
        raise ValueError("terminal_result.returncode must be an integer or null")
    try:
        outcome = Outcome(terminal["outcome"])
        reason = (
            None
            if terminal["reason_code"] is None
            else ReasonCode(terminal["reason_code"])
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"terminal_result contains an unknown outcome or reason: {exc}") from exc
    returncode = terminal["returncode"]
    # NO_MEASUREMENT belongs to a preflight or observer boundary; a campaign
    # candidate record must preserve the command's real terminal outcome.
    if outcome is Outcome.NO_MEASUREMENT:
        return False
    if outcome is Outcome.BUDGET_EXCEEDED and process_tree_evidence is not None:
        allowed_leader_statuses = (
            {"timeout-killed"}
            if reason is ReasonCode.CANDIDATE_HUNG
            else {"timeout-killed", "not-started"}
        )
        if process_tree_evidence.leader_status not in allowed_leader_statuses:
            return False
    if outcome is Outcome.PASS and (
        reason is not None or type(returncode) is not int
    ):
        raise ValueError("PASS terminal_result requires a null reason and integer returncode")
    if outcome is Outcome.FAIL and (
        reason is not ReasonCode.COMMAND_FAILED
        or (
            type(returncode) is not int
            and not (
                returncode is None
                and process_tree_evidence is not None
                and process_tree_evidence.leader_status == "signaled"
            )
        )
    ):
        raise ValueError(
            "FAIL terminal_result requires COMMAND_FAILED and integer returncode, "
            "or a signaled process-tree leader with no returncode"
        )
    if outcome is Outcome.ERROR and returncode is not None:
        raise ValueError("ERROR terminal_result requires a null returncode")
    if outcome is Outcome.BUDGET_EXCEEDED and (
        reason not in {ReasonCode.LANE_TIMEOUT, ReasonCode.CANDIDATE_HUNG}
        or returncode is not None
    ):
        raise ValueError(
            "BUDGET_EXCEEDED terminal_result requires a timeout reason and null returncode"
        )

    forced_bucket = payload.get("forced_bucket")
    if "forced_bucket" in payload and forced_bucket != "crashed":
        raise ValueError("forced_bucket must be 'crashed' when present")

    equivalence_evidence = payload.get("equivalence_evidence")
    if "equivalence_evidence" in payload:
        if not isinstance(equivalence_evidence, Mapping) or set(equivalence_evidence) != {
            "artifact",
            "baseline_sha256",
            "observed_sha256",
        }:
            raise ValueError("equivalence_evidence has missing or unknown fields")
        artifact = equivalence_evidence["artifact"]
        baseline_digest = equivalence_evidence["baseline_sha256"]
        observed_digest = equivalence_evidence["observed_sha256"]
        if not isinstance(artifact, str):
            raise ValueError("equivalence_evidence.artifact must be a string")
        if (
            not isinstance(baseline_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", baseline_digest) is None
        ):
            raise ValueError("equivalence_evidence baseline digest must be SHA-256")
        if observed_digest is not None and (
            not isinstance(observed_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", observed_digest) is None
        ):
            raise ValueError(
                "equivalence_evidence observed digest must be SHA-256 or null"
            )
    else:
        artifact = baseline_digest = observed_digest = None

    kill_signal_evidence = payload.get("kill_signal_evidence")
    if "kill_signal_evidence" in payload:
        if not isinstance(kill_signal_evidence, Mapping) or set(kill_signal_evidence) != {
            "artifact",
            "present",
        }:
            raise ValueError("kill_signal_evidence has missing or unknown fields")
        if (
            not isinstance(kill_signal_evidence["artifact"], str)
            or type(kill_signal_evidence["present"]) is not bool
        ):
            raise ValueError("kill_signal_evidence has invalid field types")

    bucket = payload.get("outcome_bucket")
    if equivalence_artifact is not None and (
        equivalence_evidence is None
        or baseline_equivalence_sha256 is None
        or artifact != equivalence_artifact
        or baseline_digest != baseline_equivalence_sha256
    ):
        return False
    if equivalence_artifact is None and equivalence_evidence is not None:
        return False
    if kill_signal_artifact is not None and (
        not isinstance(kill_signal_evidence, Mapping)
        or kill_signal_evidence.get("artifact") != kill_signal_artifact
    ):
        return False
    if kill_signal_artifact is None and kill_signal_evidence is not None:
        return False

    execution = _execution_from_state_record(payload)
    baseline_equivalence = (
        bytes.fromhex(baseline_equivalence_sha256)
        if baseline_equivalence_sha256 is not None
        else None
    )
    equivalence_bytes = (
        None
        if observed_digest is None
        else bytes.fromhex(observed_digest)
    )
    # Keep this projection minimal: a new CommandResult field read in
    # _bucket_for_run will raise AttributeError until supplied here.
    run = _MutantRun(
        result=SimpleNamespace(
            outcome=outcome,
            reason_code=reason,
            returncode=returncode,
            process_tree_evidence=process_tree_evidence,
        ),
        equivalence_bytes=equivalence_bytes,
        kill_signal=(
            "present"
            if isinstance(kill_signal_evidence, Mapping)
            and kill_signal_evidence["present"]
            else None
        ),
        execution=execution,
        resource_limit_evidence=resource_limit_evidence,
        process_tree_evidence=process_tree_evidence,
        forced_bucket=forced_bucket,
    )
    expected_bucket = _bucket_for_run(
        run,
        equivalence_artifact=equivalence_artifact,
        baseline_equivalence=baseline_equivalence,
        kill_signal_artifact=kill_signal_artifact,
    )

    if cold_witness and expected_bucket == "killed":
        witness = execution.witness
        if witness is None or (
            witness.when != "call"
            or witness.outcome != "failed"
            or witness.session_exit_status != 1
            or witness.process_exit_status != 1
        ):
            return False
    if expected_bucket in {"killed", "survived", "equivalent"} and (
        process_tree_evidence is None
        or process_tree_evidence.leader_status != "exited"
        or process_tree_evidence.outliving_descendants != 0
    ):
        return False
    # A persisted candidate_error records that final model projection failed.
    # Its durable recovery outcome is deliberately crashed even when the
    # command's terminal facts would normally classify as killed/survived.
    return bucket == expected_bucket or (
        payload.get("candidate_error") is not None and bucket == "crashed"
    )


def _validate_record_locus(
    payload: dict[str, Any], *, job: MutantJob
) -> bool:
    """Drop advisory locus metadata that no longer reproduces for *job*.

    The record's exact candidate id remains the resume authority. A stored
    locus or window key that is unknown or differs from the value recomputed
    for this job is discarded, and ``True`` reports one ``locus_unavailable``
    advisory. Neither malformed input nor a mismatching value raises.
    """
    if "locus" not in payload and "window_key" not in payload:
        return False
    stored_binding_matches = all(
        payload.get(key) == getattr(job.site, key)
        for key in ("start_byte", "end_byte", "lineno")
    )
    expected = _locus_fields_for_job(job)
    if (
        stored_binding_matches
        and expected
        and payload.get("locus") == expected.get("locus")
        and payload.get("window_key") == expected.get("window_key")
    ):
        return False
    payload.pop("locus", None)
    payload.pop("window_key", None)
    return True


def locus_index_for_records(
    records: Iterable[Mapping[str, Any]],
) -> LocusIndex:
    """Build the deduplicated locus/window lookup index for prior records.

    Malformed records contribute no key. ``LocusIndex`` first de-duplicates
    the same candidate id across flat and generation spellings; a locus key
    held by two different candidate ids is then reported ambiguous and
    ``by_locus`` returns ``None`` for it. Such records are ignored for lookup,
    never deleted or treated as an error.
    """
    entries: list[tuple[str, str | None, str | None, object]] = []
    for payload in records:
        if not isinstance(payload, Mapping):
            continue
        candidate = payload.get("candidate_id")
        if (
            not isinstance(candidate, str)
            or _CANDIDATE_ID_RE.fullmatch(candidate) is None
        ):
            continue
        locus = _locus_from_record(payload.get("locus"))
        if locus is None:
            locus_key = window_key = None
        else:
            locus_key = locus.key
            raw_window_key = payload.get("window_key")
            window_key = (
                raw_window_key
                if isinstance(raw_window_key, str)
                and _CANDIDATE_ID_RE.fullmatch(raw_window_key) is not None
                else None
            )
        entries.append((candidate, locus_key, window_key, payload))
    return LocusIndex(entries)


def _load_validated_state_record(
    state_root: Path,
    job: MutantJob,
    *,
    judge: str,
    campaign_deadline_sha256: str | None = None,
    context: RecordContext = RecordContext(),
    state_root_fd: int | None = None,
    raw_override: Any = _READ_FROM_STORE,
    accept_any_judge: bool = False,
    locus_status: list[bool] | None = None,
) -> Mapping[str, Any] | str | None:
    """Return the persisted verdict for *job*, or why it cannot be used.

    (B527/B530) *raw_override* supplies the record bytes (``None`` = absent)
    instead of reading ``<state_root>/<id>.json`` -- a generation file or a
    carry-over source. *accept_any_judge* validates a record exactly as for
    resume but accepts whatever judge it is evidence for (and ignores the
    campaign-deadline binding); it is used ONLY to decide whether a record
    may be carried over, never to resume one.

    Three outcomes, deliberately three and not two:

    * a mapping -- the record is trustworthy and the candidate resumes;
    * ``None`` -- there is no record at all (a cold cache, the ordinary
      first-run case);
    * :data:`_RECORD_REJECTED` -- a record EXISTS and was refused, because
      its `schema_version` or its `judge_sha256` says it was produced by
      something that is not this run. Re-executed exactly like an absent
      record, but counted and reported separately, so "the cache is cold"
      and "the cache is rejected every single run" stop looking identical
      from the outside (round-1 review finding 2).

    *judge* is this run's :func:`judge_sha256` -- REQUIRED, never defaulted:
    a caller that could omit it would silently get back the pre-B088
    behaviour (trust any record filed under the candidate's identity), which
    is the whole defect.
    """
    identity = candidate_id(job)
    # (B066) `read_bounded_input` takes ANY root and needs no git repository
    # -- it is a descriptor walk, not a repository query -- so relocating
    # the store is a change of root plus a one-component relative name, with
    # the O_NOFOLLOW/bounded-read discipline entirely unchanged.
    raw = (
        _read_state_record_bytes(
            Path(state_root),
            identity,
            state_root_fd=state_root_fd,
        )
        if raw_override is _READ_FROM_STORE
        else raw_override
    )
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise MutationStateError(f"mutation-state record {identity} cannot be decoded as UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MutationStateError(f"mutation-state record {identity} must be a JSON object")
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
            #
            # (B088) Reported as REJECTED rather than absent: the record
            # exists and was refused, which is a different fact from "there
            # was nothing here", and the operator-visible counter is the
            # only place that difference ever shows up.
            return _RECORD_REJECTED
        # Every OTHER key (candidate_id, path, operator, replacement_sha256,
        # source_sha256) IS folded into `identity`, and therefore into this
        # record's own filename -- a mismatch here means the record on disk
        # contradicts the identity it is filed under, which is evidence of
        # corruption or hand-editing, not a routine cache event. Silently
        # treating that as "absent" (the pre-B021 disposition for
        # source_sha256 specifically) would discard exactly the evidence
        # this check exists to surface.
        raise MutationStateError(f"mutation-state record {identity} has stale {key}")
    locus_unavailable = _validate_record_locus(payload, job=job)
    if locus_status is not None:
        locus_status.append(locus_unavailable)
    legacy_tree = payload.get("process_tree_evidence")
    if (
        isinstance(legacy_tree, Mapping)
        and legacy_tree.get("protocol") == "assay-process-tree/1"
        and (not isinstance(payload.get("judge_sha256"), str)
             or payload.get("judge_sha256") != judge)
    ):
        # Verdict v16 state carries process-tree protocol /1. Its count-only
        # leak evidence cannot be upgraded into v17 descendant identity, and
        # the new judge label intentionally makes it a cache miss.
        return _RECORD_REJECTED
    if "resources" in payload:
        raw_resources = payload["resources"]
        if not isinstance(raw_resources, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} resources must be an object"
            )
        unknown_resource_keys = set(raw_resources) - (
            _STATE_RESOURCE_KEYS | _LEGACY_STATE_RESOURCE_KEYS
        )
        if unknown_resource_keys:
            raise MutationStateError(
                f"mutation-state record {identity} resources has unknown fields "
                f"{sorted(unknown_resource_keys)}"
            )
        if (
            "cpu_seconds" in raw_resources
            and raw_resources["cpu_seconds"] is not None
        ):
            cpu_seconds = liveness._finite_number(raw_resources["cpu_seconds"])
            if cpu_seconds is None or cpu_seconds < 0:
                raise MutationStateError(
                    f"mutation-state record {identity} resources.cpu_seconds "
                    "must be a non-negative number or null"
                )
        for field in ("peak_rss_bytes", "write_bytes", "judge_rss_bytes"):
            if field in raw_resources and raw_resources[field] is not None:
                value = raw_resources[field]
                if type(value) is not int or value < 0:
                    raise MutationStateError(
                        f"mutation-state record {identity} resources.{field} "
                        "must be a non-negative integer or null"
                    )
        for field, expected_keys, nullable_values in (
            ("phase_seconds", _STATE_RESOURCE_PHASE_KEYS, False),
            ("startup_seconds", _STATE_RESOURCE_STARTUP_KEYS, True),
        ):
            if field not in raw_resources or raw_resources[field] is None:
                continue
            nested = raw_resources[field]
            if not isinstance(nested, Mapping):
                raise MutationStateError(
                    f"mutation-state record {identity} resources.{field} "
                    "must be an object or null"
                )
            if set(nested) != expected_keys:
                raise MutationStateError(
                    f"mutation-state record {identity} resources.{field} "
                    "has missing or unknown fields"
                )
            for name, value in nested.items():
                if value is None and nullable_values:
                    continue
                finite_value = liveness._finite_number(value)
                if finite_value is None or finite_value < 0:
                    nullable_suffix = " or null" if nullable_values else ""
                    raise MutationStateError(
                        f"mutation-state record {identity} resources.{field}.{name} "
                        f"must be a non-negative number{nullable_suffix}"
                    )
    resource_limit_evidence: ResourceLimitEvidence | None = None
    if "resource_limit_evidence" in payload:
        raw_resource_evidence = payload["resource_limit_evidence"]
        if not isinstance(raw_resource_evidence, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} resource_limit_evidence must be an object"
            )
        try:
            if not _is_valid_legacy_b145_resource_limit_evidence(raw_resource_evidence):
                resource_limit_evidence = ResourceLimitEvidence.from_dict(
                    raw_resource_evidence
                )
        except (TypeError, ValueError) as exc:
            raise MutationStateError(
                f"mutation-state record {identity} has invalid resource-limit evidence: {exc}"
            ) from exc
    resource_observation = payload.get("resource_observation")
    if "resource_observation" in payload:
        if (
            not isinstance(resource_observation, Mapping)
            or set(resource_observation) != {"status", "reason"}
            or resource_observation.get("status") != "unobserved"
            or not isinstance(resource_observation.get("reason"), str)
            or not resource_observation["reason"].strip()
        ):
            raise MutationStateError(
                f"mutation-state record {identity} has invalid resource_observation"
            )
        if "resource_limit_evidence" in payload:
            raise MutationStateError(
                f"mutation-state record {identity} cannot carry both observed and unobserved resource evidence"
            )
    resource_observation_missing = (
        "resource_observation" not in payload and resource_limit_evidence is None
    )
    process_tree_evidence: ProcessTreeEvidence | None = None
    if "process_tree_evidence" in payload:
        raw_process_tree = payload["process_tree_evidence"]
        if not isinstance(raw_process_tree, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} process_tree_evidence must be an object"
            )
        try:
            process_tree_evidence = ProcessTreeEvidence.from_dict(raw_process_tree)
        except (TypeError, ValueError) as exc:
            raise MutationStateError(
                f"mutation-state record {identity} has invalid process-tree evidence: {exc}"
            ) from exc
    else:
        # Existing records from the previous judge identity are a routine cold
        # cache. They cannot prove cleanup and must be re-executed.
        return _RECORD_REJECTED
    underlying_outcome: UnderlyingOutcome | None = None
    if "underlying_outcome" in payload:
        raw_underlying = payload["underlying_outcome"]
        if not isinstance(raw_underlying, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} underlying_outcome must be an object"
            )
        try:
            underlying_outcome = UnderlyingOutcome.from_dict(raw_underlying)
        except (TypeError, ValueError, KeyError) as exc:
            raise MutationStateError(
                f"mutation-state record {identity} has invalid underlying_outcome: {exc}"
            ) from exc
    if "outcome_bucket" not in payload:
        raise MutationStateError(f"mutation-state record {identity} is missing outcome_bucket")
    if payload["outcome_bucket"] not in MUTATION_BUCKETS:
        raise MutationStateError(
            f"mutation-state record {identity} has unknown outcome bucket "
            f"{payload['outcome_bucket']!r}"
        )
    outcome_bucket = payload["outcome_bucket"]
    candidate_error: CandidateOutcomeError | None = None
    if "candidate_error" in payload:
        raw_candidate_error = payload["candidate_error"]
        if not isinstance(raw_candidate_error, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} candidate_error must be an object"
            )
        try:
            candidate_error = CandidateOutcomeError.from_dict(raw_candidate_error)
        except (TypeError, ValueError) as exc:
            raise MutationStateError(
                f"mutation-state record {identity} has invalid candidate_error: {exc}"
            ) from exc
        if outcome_bucket != "crashed":
            raise MutationStateError(
                f"mutation-state record {identity} has candidate_error outside crashed"
            )

    completed_leak = (
        process_tree_evidence.leader_status == "exited"
        and process_tree_evidence.outliving_descendants > 0
    )
    if (
        completed_leak
        and underlying_outcome is None
        and candidate_error is None
        and outcome_bucket == "crashed"
        and process_tree_evidence.protocol == "assay-process-tree/2"
    ):
        stored_judge = payload.get("judge_sha256")
        if (
            isinstance(stored_judge, str)
            and _CANDIDATE_ID_RE.fullmatch(stored_judge) is not None
            and stored_judge != judge
        ):
            # Before B251, /14 state could contain this exact completed-leak
            # shape because the worker protocol dropped underlying_outcome.
            # /15 makes it stale; reject it before the newer invariant so
            # resume re-executes the candidate. A current record with the
            # same shape remains corruption below.
            return _RECORD_REJECTED
    if completed_leak and underlying_outcome is None and candidate_error is None:
        raise MutationStateError(
            f"mutation-state record {identity} has inconsistent underlying_outcome "
            "for its process-tree evidence"
        )
    if not completed_leak and underlying_outcome is not None:
        raise MutationStateError(
            f"mutation-state record {identity} has inconsistent underlying_outcome "
            "for its process-tree evidence"
        )
    if underlying_outcome is not None and outcome_bucket != "crashed":
        raise MutationStateError(
            f"mutation-state record {identity} has underlying_outcome outside crashed"
        )
    try:
        _execution_from_state_record(payload)
    except (TypeError, ValueError) as exc:
        raise MutationStateError(
            f"mutation-state record {identity} has invalid execution provenance: {exc}"
        ) from exc
    try:
        terminal_matches_bucket = _terminal_result_matches_bucket(
            payload,
            equivalence_artifact=context.equivalence_artifact,
            baseline_equivalence_sha256=context.baseline_equivalence_sha256,
            kill_signal_artifact=context.kill_signal_artifact,
            cold_witness=context.cold_witness,
            resource_limit_evidence=resource_limit_evidence,
            process_tree_evidence=process_tree_evidence,
        )
    except (TypeError, ValueError) as exc:
        raise MutationTerminalEvidenceError(
            f"mutation-state record {identity} has invalid terminal evidence: {exc}"
        ) from exc
    if payload["outcome_bucket"] == "budget_exceeded":
        # A timeout is reusable only when the complete candidate record was
        # committed. Older full-run records may omit `execution`, and
        # evidence may be absent on non-timeout legacy outcomes; neither
        # compatibility shape proves a current per-candidate timeout. Treat
        # these missing fields as a cache miss so resume schedules the site
        # again instead of retaining an unresolved budget bucket forever.
        if any(
            key not in payload
            for key in ("execution", "evidence", "mutated_file_sha256")
        ):
            return _RECORD_REJECTED
        mutated_file_sha256 = payload["mutated_file_sha256"]
        if (
            not isinstance(mutated_file_sha256, str)
            or _CANDIDATE_ID_RE.fullmatch(mutated_file_sha256) is None
        ):
            raise MutationStateError(
                f"mutation-state record {identity} has invalid mutated_file_sha256"
            )
        expected_mutated_file_sha256 = candidate_identity_fields(job)[
            "mutated_file_sha256"
        ]
        if mutated_file_sha256 != expected_mutated_file_sha256:
            raise MutationStateError(
                f"mutation-state record {identity} has stale mutated_file_sha256"
            )
    if "evidence" in payload and payload["evidence"] is not None:
        raw_evidence = payload["evidence"]
        if not isinstance(raw_evidence, Mapping):
            raise MutationStateError(
                f"mutation-state record {identity} evidence must be an object or null"
            )
        try:
            if set(raw_evidence) != {
                "command", "collection_count", "collection_sha256",
                "hook_fingerprint_sha256", "started_count", "failed_call_index",
            }:
                raise ValueError("evidence has missing or unknown fields")
            MutantEvidence(**raw_evidence)
        except (TypeError, ValueError) as exc:
            raise MutationStateError(
                f"mutation-state record {identity} has invalid mutant evidence: {exc}"
            ) from exc
    if not terminal_matches_bucket:
        # Terminal evidence is required for every current record. A missing
        # field is a legacy cache miss; contradictory evidence is never
        # merged into this run's outcome. Run structural validation first so
        # malformed records remain corruption errors, not cache misses.
        return _RECORD_REJECTED
    if resource_observation_missing:
        # Older native state records without either form of B145 evidence do
        # not prove the candidate's observation state. Validate the rest of
        # the record first, then re-execute this candidate as a cache miss.
        return _RECORD_REJECTED
    # (B088) LAST, and deliberately so. Everything above asks "is this record
    # internally consistent with the identity it is filed under" -- a
    # tampered or corrupted record must still be surfaced as corruption
    # (B021's disposition) even when the judging suite ALSO moved, or a
    # routine test edit would become a way to launder a hand-edited state
    # file into a silent rerun. Only once the record is known well-formed
    # does this ask the different question: is the verdict it carries still
    # EVIDENCE about the current run?
    #
    # `judge_sha256` is not folded into `identity` (and must not be -- B066's
    # shared `--state-dir` is safe precisely because a record's FILENAME is
    # the mutation's identity, and one mutant must not scatter into a new
    # file per judging suite). So both its absence and a mismatch are routine
    # cache events, not corruption:
    #
    # * ABSENT -- the record predates B088; nothing recorded what judged it,
    #   so nothing can vouch for it;
    # * DIFFERENT -- the suite, argv, environment or working directory that
    #   produced the verdict is not the one running now. A new assertion in a
    #   test file lands here, which is the entire reason this exists: the
    #   mutant that "survived" was never re-executed against the assertion
    #   that would have killed it.
    #
    # Either way: treat as absent and re-execute. Never raise -- failing the
    # lane every time someone strengthened a test would make the fix worse
    # than the defect.
    #
    # The `isinstance` is not belt-and-braces (round-1 review finding 8).
    # `payload.get(...) != judge` alone is fail-OPEN in the one shape that
    # matters: a record with no judge, read by a caller whose own judge is
    # `None`, compares equal and is TRUSTED. That is unreachable today and
    # asserted against twice -- but `assert` disappears under `python -O`,
    # and for the one code path whose entire bug class is a cache that
    # trusts too much, "unreachable" is not the bar. A stored judge that is
    # not a string is not this run's judge, whatever `judge` happens to be.
    stored = payload.get("judge_sha256")
    if not isinstance(stored, str):
        return _RECORD_REJECTED
    # (B530) A carried-over record keeps its executing judge in
    # `judge_sha256` and resumes under the judge it was imported into.
    effective = record_effective_judge(payload)
    if effective is None:
        return _RECORD_REJECTED
    if not accept_any_judge:
        if effective != judge:
            return _RECORD_REJECTED
        if (
            campaign_deadline_sha256 is not None
            and payload.get("campaign_deadline_sha256") != campaign_deadline_sha256
        ):
            return _RECORD_REJECTED
    if payload["outcome_bucket"] == "hung" and not _valid_hung_resource_evidence(
        payload.get("liveness_resource_evidence")
    ):
        return _RECORD_REJECTED
    if context.cold_witness and (
        payload["outcome_bucket"] == "survived"
        or (
            isinstance(payload.get("execution"), Mapping)
            and payload["execution"].get("mode") == "witness-cold"
        )
    ) and payload.get("evidence") is None:
        raise MutationStateError(
            f"cold-witness mutation-state record {identity} is missing required evidence"
        )
    # (B203/B205/B206) The record is current. It must state the worker count
    # that produced it, may carry the candidate's resource profile, and names
    # the first failing test only when it was killed.
    worker_count = payload.get("worker_count")
    if type(worker_count) is not int or worker_count < 1:
        raise MutationStateError(
            f"mutation-state record {identity} worker_count must be a positive integer"
        )
    try:
        observer_windows.validate_profile(payload.get("profile"))
    except ValueError as exc:
        raise MutationStateError(
            f"mutation-state record {identity} has an invalid profile: {exc}"
        ) from exc
    first_failing = payload.get("first_failing_test")
    if first_failing is not None and (
        not isinstance(first_failing, str)
        or not first_failing
        or payload["outcome_bucket"] != "killed"
    ):
        raise MutationStateError(
            f"mutation-state record {identity} first_failing_test must be a "
            "non-empty string on a killed record, or null"
        )
    return payload


def _is_valid_legacy_b145_resource_limit_evidence(raw: Mapping[str, Any]) -> bool:
    """Validate and identify pre-current B145 state evidence for cold-start.

    B145's released-state label is deliberately changed whenever the evidence
    surface changes. Records with the old v4 (two memory counters) or v5
    (three memory counters) shape remain structurally checkable, but are never
    converted into current evidence or reused by this judge.
    """
    if set(raw) != {"cgroup_version", "pids_events", "memory_events"}:
        return False
    memory = raw["memory_events"]
    legacy_memory_shapes = (
        {"oom_kill", "oom_group_kill"},
        {"oom", "oom_kill", "oom_group_kill"},
    )
    if not isinstance(memory, Mapping) or set(memory) not in legacy_memory_shapes:
        return False
    if raw["cgroup_version"] != 2 or type(raw["cgroup_version"]) is not int:
        raise ValueError("legacy resource-limit evidence requires cgroup_version 2")
    pids = raw["pids_events"]
    if not isinstance(pids, Mapping) or set(pids) != {"max"}:
        raise ValueError("legacy pids_events must contain exactly max")

    def validate_delta(value: Any) -> None:
        if not isinstance(value, Mapping) or set(value) != {
            "before",
            "after",
            "delta",
        }:
            raise ValueError(
                "legacy resource counter must contain exactly before, after, and delta"
            )
        CounterDelta(
            before=value["before"], after=value["after"], delta=value["delta"]
        )

    validate_delta(pids["max"])
    for name in sorted(memory):
        validate_delta(memory[name])
    return True


def merge_mutations(current: Mutation, records: Iterable[Mapping[str, Any]]) -> Mutation:
    """Fold already-validated resumed records into a completed shard run."""
    buckets: dict[str, list[MutantOutcome]] = {
        name: list(getattr(current, name)) for name in MUTATION_BUCKETS
    }
    for record_entry in records:
        buckets[record_entry["outcome_bucket"]].append(_outcome_from_record(record_entry))
    identities = [
        outcome.identity for name in MUTATION_BUCKETS for outcome in buckets[name]
    ]
    duplicate_count = len(identities) - len(set(identities))
    normalized = {
        name: tuple(sorted(buckets[name], key=lambda item: item.identity))
        for name in MUTATION_BUCKETS
    }
    if duplicate_count:
        raise MutationStateError(
            f"resumed records repeat {duplicate_count} candidate identity"
        )
    return Mutation(
        candidate_count=len(identities),
        total=len(identities),
        **{name: normalized[name] for name in MUTATION_BUCKETS},
    )


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
            first_failing_test=record.get("first_failing_test"),
            candidate_id=record["candidate_id"],
            source_sha256=record["source_sha256"],
            mutated_file_sha256=record["mutated_file_sha256"],
            execution=_execution_from_state_record(record),
            evidence=(
                MutantEvidence(**record["evidence"])
                if isinstance(record.get("evidence"), Mapping)
                else None
            ),
            resource_limit_evidence=(
                ResourceLimitEvidence.from_dict(record["resource_limit_evidence"])
                if isinstance(record.get("resource_limit_evidence"), Mapping)
                else None
            ),
            process_tree_evidence=(
                ProcessTreeEvidence.from_dict(record["process_tree_evidence"])
                if isinstance(record.get("process_tree_evidence"), Mapping)
                else None
            ),
            underlying_outcome=(
                UnderlyingOutcome.from_dict(record["underlying_outcome"])
                if isinstance(record.get("underlying_outcome"), Mapping)
                else None
            ),
            candidate_error=(
                CandidateOutcomeError.from_dict(record["candidate_error"])
                if isinstance(record.get("candidate_error"), Mapping)
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MutationStateError(f"invalid resumed mutation record field: {exc}") from exc


def _execution_from_state_record(record: Mapping[str, Any]) -> MutationExecution:
    """Restore P5 execution provenance from a resume record.

    Records written before witness-prefix reuse existed could only come from a
    full candidate execution, so their missing ``execution`` field has that
    historical meaning. New records always serialize the closed execution
    object; malformed present data is corruption, never a legacy fallback.
    """
    if "execution" not in record:
        return MutationExecution(mode="full")
    raw = record["execution"]
    if not isinstance(raw, Mapping):
        raise ValueError("execution must be an object")
    mode = raw.get("mode")
    if mode == "full":
        allowed = {"mode", "witness"}
    elif mode == "witness-cold":
        allowed = {"mode", "witness"}
    elif mode == "witness-prefix":
        allowed = {
            "mode",
            "witness",
            "prior_verdict_sha256",
            "prior_node_id",
            "current_node_id",
        }
    else:
        raise ValueError("execution mode is unknown")
    if set(raw) - allowed or "mode" not in raw:
        raise ValueError("execution contains unknown fields")

    witness = None
    if "witness" in raw:
        witness_data = raw["witness"]
        if not isinstance(witness_data, Mapping) or set(witness_data) != {
            "node_id",
            "when",
            "outcome",
            "session_exit_status",
            "process_exit_status",
        }:
            raise ValueError("execution witness has an invalid shape")
        witness = MutationWitnessReceipt(**witness_data)
    return MutationExecution(
        mode=mode,
        witness=witness,
        prior_verdict_sha256=raw.get("prior_verdict_sha256"),
        prior_node_id=raw.get("prior_node_id"),
        current_node_id=raw.get("current_node_id"),
    )


# CD18: public for assay_analysis.
execution_from_state_record = _execution_from_state_record


def select_mutation_shard(
    candidates: Sequence[str],
    *,
    index: int,
    count: int,
    deadline_check: Callable[[], float] | None = None,
) -> list[int]:
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
    selected: list[int] = []
    for position, candidate in enumerate(candidates):
        if deadline_check is not None:
            deadline_check()
        shard = int.from_bytes(
            hashlib.blake2b(candidate.encode("ascii"), digest_size=4).digest(),
            "big",
        ) % count
        if shard == index:
            selected.append(position)
    if deadline_check is not None:
        deadline_check()
    return selected


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
        if not is_nonempty_str(lane) or not is_nonempty_str(commit):
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
    # Every pair was already range-checked against its own count above, and
    # all documents must share the one count used to build `required_pairs`.
    # Therefore `covered_pairs - required_pairs` is empty by construction.
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


@record
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
    #: (B091/D-23, P7 A4) How many `test` events this candidate's own
    #: liveness side file carries, read back via `assay.liveness.
    #: count_test_events` while *snapshot* (below) is still the mutant's
    #: real execution `cwd` -- `None` for every non-liveness lane, never `0`
    #: (a `0` would claim the plugin ran and genuinely saw no test, which is
    #: a different fact from "liveness never ran here at all").
    tests_completed: int | None = None
    #: B107: exact CPU/PSI evidence retained when the liveness runner stops
    #: a candidate. A resource stall never becomes a functional bucket.
    liveness_resource_evidence: Mapping[str, Any] | None = None
    #: B111/AP-6 diagnostic evidence (never a classification input): startup
    #: gaps read from the liveness sidecar and events file (`None` off a
    #: liveness lane) and the worker's own monotonic phase split (always
    #: measured). Resource numbers are NOT here: they come only from the
    #: cgprofile observer, as :attr:`profile_result` (B206).
    #: What the observer window produced for this attempt, or ``None`` when no
    #: window was requested for it (a replayed run, an in-process test seam).
    profile_result: "observer_windows.WindowResult | None" = None
    #: (B205) The first failing test id read from the liveness events file, or
    #: ``None`` when unknown (no plugin events, or no failed test reported).
    first_failing_test: str | None = None
    phase_seconds: Mapping[str, float] | None = None
    startup_seconds: Mapping[str, float | None] | None = None
    execution: MutationExecution = MutationExecution(mode="full")
    resource_limit_evidence: ResourceLimitEvidence | None = None
    #: In-memory configured-limit changes, deliberately absent from verdict
    #: and resume records; surfaced only as candidate progress telemetry.
    resource_limit_changes: tuple[str, ...] = ()
    resource_observation_reason: str | None = None
    process_tree_evidence: ProcessTreeEvidence | None = None
    evidence: MutantEvidence | None = None
    forced_bucket: str | None = None


@record
class _CarryOverResult:
    records: tuple[Mapping[str, Any], ...]
    imported_total: int
    copied_total: int
    refused: tuple[tuple[str, str], ...]
    from_judge_counts: Mapping[str, int]
    locus_unavailable: int


@record
class _OwnStoreResult:
    records: tuple[Mapping[str, Any], ...]
    rejected_total: int
    rejudged_total: int
    resource_rejudged_total: int
    promoted_total: int
    locus_unavailable_total: int
    rejudged_candidate_ids: frozenset[str]


@record
class _CarryForwardRecord:
    payload: Mapping[str, Any]
    raw: bytes
    source: Path
    relative: str
    effective_judge: str


@record
class _CarryForwardSourceIndex:
    records: tuple[_CarryForwardRecord, ...]
    invalid_records: int


@record
class _CarryForwardMatch:
    kind: Literal["exact-id", "locus", "window"]
    source: Path
    payload: Mapping[str, Any]
    raw: bytes | None = None
    relative: str = ""
    effective_judge: str | None = None


@record
class _CarryForwardResult:
    records: tuple[Mapping[str, Any], ...]
    considered_total: int
    exact_id: int
    locus: int
    window: int
    survivors_carried: int
    kills_requeued: int
    ambiguous_loci_ignored: int
    unmatched: int
    from_judges: int
    same_judge_resume: int
    invalid_records: int
    hints: tuple[Hint, ...]


def _carry_forward_record_paths(source: Path) -> tuple[str, ...]:
    """List canonical flat and generation record spellings in one source."""
    flat: list[str] = []
    try:
        with os.scandir(source) as children:
            for child in children:
                if (
                    child.name.endswith(".json")
                    and _CANDIDATE_ID_RE.fullmatch(child.name[:-5]) is not None
                    and child.is_file(follow_symlinks=False)
                ):
                    flat.append(child.name)
    except OSError:
        return ()
    relatives = sorted(flat)
    generations: list[str] = []
    try:
        with os.scandir(source / JUDGE_GENERATIONS_DIR) as children:
            generations = sorted(
                child.name
                for child in children
                if _CANDIDATE_ID_RE.fullmatch(child.name)
                and child.is_dir(follow_symlinks=False)
            )
    except OSError:
        pass
    for generation in generations:
        prefix = f"{JUDGE_GENERATIONS_DIR}/{generation}"
        try:
            with os.scandir(source / prefix) as children:
                relatives.extend(
                    f"{prefix}/{child.name}"
                    for child in children
                    if child.name.endswith(".json")
                    and _CANDIDATE_ID_RE.fullmatch(child.name[:-5]) is not None
                    and child.is_file(follow_symlinks=False)
                )
        except OSError:
            continue
    return tuple(relatives)


def carried_survivor_count(state_root: Path | str) -> int:
    """Count valid carried survivors in a store's current flat generation."""
    root = Path(state_root)
    try:
        with os.scandir(root) as scanned:
            children = tuple(scanned)
    except OSError:
        return 0
    total = 0
    for child in children:
        if not (
            child.name.endswith(".json")
            and _CANDIDATE_ID_RE.fullmatch(child.name[:-5]) is not None
            and child.is_file(follow_symlinks=False)
        ):
            continue
        try:
            raw = safeio.read_bounded_input(
                root, child.name, limit=MUTATION_STATE_RECORD_LIMIT
            )
            if raw is None:
                continue
            payload = json.loads(raw.decode("utf-8"))
        except (AssayError, OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(payload, Mapping):
            continue
        if record_effective_judge(payload) is None:
            continue
        provenance = record_provenance(payload)
        if (
            provenance is not None
            and provenance["kind"] == "carried"
            and payload.get("outcome_bucket") == "survived"
        ):
            total += 1
    return total


def _carry_forward_source_index(
    source: Path,
    *,
    deadline_check: Callable[[], float] | None = None,
) -> _CarryForwardSourceIndex:
    """Read one bounded source and retain structurally valid terminal records."""
    records: list[_CarryForwardRecord] = []
    invalid_records = 0
    for relative in _carry_forward_record_paths(source):
        if deadline_check is not None:
            deadline_check()
        try:
            raw = safeio.read_bounded_input(
                source, relative, limit=MUTATION_STATE_RECORD_LIMIT
            )
        except (AssayError, OSError):
            invalid_records += 1
            continue
        if raw is None:
            invalid_records += 1
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            invalid_records += 1
            continue
        if not isinstance(payload, dict):
            invalid_records += 1
            continue
        candidate = payload.get("candidate_id")
        stored_judge = payload.get("judge_sha256")
        effective_judge = record_effective_judge(payload)
        bucket = payload.get("outcome_bucket")
        if (
            not isinstance(candidate, str)
            or _CANDIDATE_ID_RE.fullmatch(candidate) is None
            or Path(relative).name[:-5] != candidate
            or not isinstance(stored_judge, str)
            or _CANDIDATE_ID_RE.fullmatch(stored_judge) is None
            or effective_judge is None
            or _CANDIDATE_ID_RE.fullmatch(effective_judge) is None
            or not isinstance(bucket, str)
            or bucket not in MUTATION_BUCKETS
        ):
            invalid_records += 1
            continue
        if bucket not in CARRY_OVER_IMPORTABLE_BUCKETS:
            continue
        if not _valid_carry_forward_identity(payload, candidate):
            invalid_records += 1
            continue
        record = _CarryForwardRecord(
            payload=payload,
            raw=raw,
            source=source,
            relative=relative,
            effective_judge=effective_judge,
        )
        records.append(record)
    records.sort(key=lambda item: (item.payload["candidate_id"], item.relative))
    return _CarryForwardSourceIndex(
        records=tuple(records),
        invalid_records=invalid_records,
    )


def _valid_carry_forward_identity(
    payload: Mapping[str, Any], candidate: str
) -> bool:
    """Check identity inputs and the recorded locus key without old source bytes.

    Exact-id records get the full WP2 loader check against the current job.
    For a cross-id lookup the old source blob is not stored, so its candidate
    digest and locus key must reproduce from the recorded identity inputs; a
    matching current locus is checked again before the record is used.
    """
    if payload.get("schema_version") != MUTATION_STATE_SCHEMA_VERSION:
        return False
    try:
        derived = candidate_id_from_fields(
            path=payload["path"],
            source_sha256=payload["source_sha256"],
            start_byte=payload["start_byte"],
            end_byte=payload["end_byte"],
            mutated_file_sha256=payload["mutated_file_sha256"],
            operator=payload["operator"],
        )
        replacement_sha256 = payload["replacement_sha256"]
        if (
            not isinstance(replacement_sha256, str)
            or _CANDIDATE_ID_RE.fullmatch(replacement_sha256) is None
            or derived != candidate
        ):
            return False
        if "locus" not in payload and "window_key" not in payload:
            return True
        locus = _locus_from_record(payload.get("locus"))
        window = payload.get("window_key")
        if (
            locus is None
            or not isinstance(window, str)
            or _CANDIDATE_ID_RE.fullmatch(window) is None
            or locus.rel_end - locus.rel_start
            != payload["end_byte"] - payload["start_byte"]
        ):
            return False
        expected_key = hashlib.sha256(
            "\0".join(
                (
                    "assay-locus/1",
                    payload["path"],
                    locus.qualname,
                    locus.segment_sha256,
                    str(locus.rel_start),
                    str(locus.rel_end),
                    payload["operator"],
                    replacement_sha256,
                )
            ).encode("utf-8")
        ).hexdigest()
        return locus.key == expected_key
    except (KeyError, TypeError, ValueError, UnicodeEncodeError):
        return False


def _carry_forward_match_rank(match: _CarryForwardMatch) -> tuple[Any, ...]:
    """Order conflicting claims deterministically, with any kill winning."""
    bucket = match.payload.get("outcome_bucket")
    kind_rank = {"exact-id": 0, "locus": 1, "window": 2}[match.kind]
    return (
        0 if bucket == "killed" else 1,
        kind_rank,
        str(match.payload.get("judge_sha256", "")),
        str(match.source),
        match.relative,
    )


def _carry_forward_match_candidates(
    jobs: Sequence[MutantJob],
    sources: tuple[Path, ...],
    *,
    judge: str,
    rejudge_outcomes: frozenset[str] = frozenset(),
    deadline_check: Callable[[], float] | None = None,
) -> tuple[dict[str, tuple[_CarryForwardMatch, ...]], int, int, int]:
    """Collect all record claims for each job in deterministic preference order."""
    indexes = tuple(
        _carry_forward_source_index(
            source,
            deadline_check=deadline_check,
        )
        for source in sources
    )
    all_records = tuple(record for index in indexes for record in index.records)
    job_families = {(job.path, job.site.operator) for job in jobs}
    from_judges = {
        record.payload["judge_sha256"]
        for record in all_records
        if (record.payload["path"], record.payload["operator"]) in job_families
        and record.effective_judge != judge
    }
    exact_records: dict[str, list[_CarryForwardRecord]] = collections.defaultdict(list)
    locus_records: dict[
        tuple[str, str, str], list[_CarryForwardRecord]
    ] = collections.defaultdict(list)
    window_records: dict[
        tuple[str, str, str], list[_CarryForwardRecord]
    ] = collections.defaultdict(list)
    for indexed_record in all_records:
        payload = indexed_record.payload
        exact_records[payload["candidate_id"]].append(indexed_record)
        locus = _locus_from_record(payload.get("locus"))
        if locus is not None:
            locus_key = (payload["path"], payload["operator"], locus.key)
            locus_records[locus_key].append(indexed_record)
        window = payload.get("window_key")
        if isinstance(window, str):
            window_records[(payload["path"], payload["operator"], window)].append(
                indexed_record
            )

    matches: dict[str, tuple[_CarryForwardMatch, ...]] = {}
    ambiguous = 0
    match_kind_rank = {"exact-id": 0, "locus": 1, "window": 2}
    for job in jobs:
        if deadline_check is not None:
            deadline_check()
        identity = candidate_id(job)
        fields = _locus_fields_for_job(job)
        current_locus = _locus_from_record(fields.get("locus"))
        window_key = fields.get("window_key")
        family = (job.path, job.site.operator)
        exact = tuple(exact_records.get(identity, ()))
        locus = (
            tuple(locus_records.get((*family, current_locus.key), ()))
            if current_locus is not None
            else ()
        )
        locus_is_ambiguous = len(
            {record.payload["candidate_id"] for record in locus}
        ) > 1
        has_exact_id = any(
            record.payload["path"] == job.path
            and record.payload["operator"] == job.site.operator
            for record in exact
        )
        if locus_is_ambiguous and not has_exact_id:
            ambiguous += 1

        same_judge_exact: list[_CarryForwardMatch] = []
        cross_judge: list[_CarryForwardMatch] = []
        matched_records: dict[
            tuple[Path, str],
            tuple[_CarryForwardRecord, Literal["exact-id", "locus", "window"]],
        ] = {}

        def add_records(
            candidates: Iterable[_CarryForwardRecord],
            kind: Literal["exact-id", "locus", "window"],
            *,
            current_matches: dict[
                tuple[Path, str],
                tuple[_CarryForwardRecord, Literal["exact-id", "locus", "window"]],
            ] = matched_records,
            kind_ranks: Mapping[str, int] = match_kind_rank,
        ) -> None:
            for candidate_record in candidates:
                key = (candidate_record.source, candidate_record.relative)
                previous = current_matches.get(key)
                if previous is None or kind_ranks[kind] < kind_ranks[previous[1]]:
                    current_matches[key] = (candidate_record, kind)

        add_records(exact, "exact-id")
        if current_locus is not None and not locus_is_ambiguous:
            add_records(locus, "locus")
        if isinstance(window_key, str):
            add_records(
                (
                    record
                    for record in window_records.get((*family, window_key), ())
                    if record.payload.get("outcome_bucket") == "killed"
                ),
                "window",
            )

        for matched_record, kind in matched_records.values():
            payload = matched_record.payload
            match = _CarryForwardMatch(
                kind=kind,
                source=matched_record.source,
                payload=payload,
                raw=matched_record.raw,
                relative=matched_record.relative,
                effective_judge=matched_record.effective_judge,
            )
            if kind == "exact-id" and matched_record.effective_judge == judge:
                same_judge_exact.append(match)
            elif matched_record.effective_judge != judge:
                if payload.get("outcome_bucket") in rejudge_outcomes:
                    continue
                # Window matches were filtered to kills above.
                cross_judge.append(match)
        # Keep same-judge exact records alongside older claims until WP2
        # validates them. A valid current record takes precedence in the
        # consumer; a stale one must not hide a valid earlier record.
        selected = same_judge_exact + cross_judge
        if selected:
            matches[identity] = tuple(sorted(selected, key=_carry_forward_match_rank))
    invalid = sum(index.invalid_records for index in indexes)
    return matches, ambiguous, invalid, len(from_judges)


def _carry_forward_lookup(
    jobs: Sequence[MutantJob],
    sources: tuple[Path, ...],
    *,
    judge: str,
    rejudge_outcomes: frozenset[str] = frozenset(),
    deadline_check: Callable[[], float] | None = None,
) -> tuple[dict[str, _CarryForwardMatch], int]:
    """Resolve cross-judge exact-id, A' and kill-only B matches."""
    candidates, ambiguous, _invalid, _from_judges = _carry_forward_match_candidates(
        jobs,
        sources,
        judge=judge,
        rejudge_outcomes=rejudge_outcomes,
        deadline_check=deadline_check,
    )
    matches: dict[str, _CarryForwardMatch] = {}
    for identity, options in candidates.items():
        earlier = tuple(option for option in options if option.effective_judge != judge)
        if earlier:
            matches[identity] = earlier[0]
    return matches, ambiguous


def carry_forward_match_kinds(
    jobs: Sequence[MutantJob],
    sources: tuple[Path, ...],
    *,
    judge: str,
    deadline_check: Callable[[], float] | None = None,
) -> tuple[dict[str, str], int]:
    """Return the advisory match kind for each candidate and collision count."""
    matches, ambiguous = _carry_forward_lookup(
        jobs, sources, judge=judge, deadline_check=deadline_check
    )
    return {identity: match.kind for identity, match in matches.items()}, ambiguous


def _validate_carry_forward_match(
    match: _CarryForwardMatch,
    job: MutantJob,
    *,
    judge: str,
    state_root: Path,
    campaign_deadline_sha256: str | None,
    context: RecordContext,
) -> Mapping[str, Any] | None:
    """Apply the WP2 record loader to an exact or sub-file carry candidate."""
    identity = candidate_id(job)
    source_payload = match.payload
    if source_payload.get("candidate_id") == identity:
        raw = match.raw
    else:
        fields = _locus_fields_for_job(job)
        # The historical full source is not stored, so validate the prior
        # record's own candidate/locus keys above, then project its outcome
        # fields onto this candidate's checked identity for WP2's complete
        # terminal-evidence and state-shape validation.
        normalized = dict(source_payload)
        normalized.pop("locus", None)
        normalized.pop("window_key", None)
        normalized.update(candidate_identity_fields(job))
        normalized.update(
            {
                "candidate_id": identity,
                "replacement_sha256": job.site.replacement_sha256,
                "lineno": job.site.lineno,
                "description": job.site.description,
                **fields,
            }
        )
        raw = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    try:
        validated = _load_validated_state_record(
            state_root,
            job,
            judge=judge,
            campaign_deadline_sha256=campaign_deadline_sha256,
            raw_override=raw,
            accept_any_judge=True,
            context=context,
        )
    except MutationStateError:
        return None
    return validated if isinstance(validated, Mapping) else None


def _carry_forward_records(
    flex: FlexOptions,
    *,
    jobs: Sequence[MutantJob],
    skip_candidate_ids: frozenset[str] = frozenset(),
    judge: str,
    state_root: Path,
    state_root_fd: int | None,
    state_root_guard: Callable[[], None] | None,
    campaign_deadline_sha256: str | None,
    context: RecordContext,
    rejudge_outcomes: frozenset[str],
    clock: Clock,
    deadline_check: Callable[[], float] | None = None,
) -> _CarryForwardResult:
    """Carry prior-judge survivors and leave every prior kill for full runs."""
    guard: Callable[[], None] = state_root_guard or (lambda: None)
    (
        match_candidates,
        ambiguous,
        invalid_records,
        from_judge_count,
    ) = _carry_forward_match_candidates(
        jobs,
        flex.carry_forward_sources,
        judge=judge,
        rejudge_outcomes=rejudge_outcomes,
        deadline_check=deadline_check,
    )
    imported_at = iso_utc(clock())
    records: list[Mapping[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    survivors_carried = 0
    kills_requeued = 0
    hints: list[Hint] = []
    unmatched = 0
    considered_total = 0
    same_judge_resume = 0
    invalid_match_records: set[tuple[Path, str]] = set()
    for job in jobs:
        guard()
        identity = candidate_id(job)
        candidates = match_candidates.get(identity, ())
        if not candidates:
            if identity in skip_candidate_ids:
                continue
            considered_total += 1
            unmatched += 1
            continue
        validated_matches: list[tuple[_CarryForwardMatch, Mapping[str, Any]]] = []
        for match in candidates:
            checked = _validate_carry_forward_match(
                match,
                job,
                judge=judge,
                state_root=state_root,
                campaign_deadline_sha256=campaign_deadline_sha256,
                context=context,
            )
            if checked is None:
                invalid_match_records.add((match.source, match.relative))
                continue
            validated_matches.append((match, checked))
        if not validated_matches:
            if identity in skip_candidate_ids:
                continue
            considered_total += 1
            unmatched += 1
            continue

        same_judge = [
            (match, checked)
            for match, checked in validated_matches
            if match.kind == "exact-id" and match.effective_judge == judge
        ]
        if same_judge:
            # These are already handled by ordinary --resume or --merge-state.
            # Counting them here is telemetry only; never copy them a second
            # time through carry-forward.
            same_judge_resume += 1
            considered_total += 1
            continue
        if identity in skip_candidate_ids:
            # Already supplied by the active resume/merge path. Do not
            # replace it with an older judge's outcome or count it unmatched.
            continue

        considered_total += 1

        kills = [
            (match, checked)
            for match, checked in validated_matches
            if checked.get("outcome_bucket") == "killed"
        ]
        if kills:
            # A kill claim always wins over a survivor, even when it was found
            # through a weaker key. The candidate gets a complete current run.
            match, _chosen = min(kills, key=lambda item: _carry_forward_match_rank(item[0]))
            counts[match.kind] += 1
            kills_requeued += 1
            witness = execution_from_state_record(_chosen).witness
            if witness is not None:
                stored_provenance = record_provenance(match.payload)
                hint_match = match.kind
                if stored_provenance is not None:
                    from_candidate_id = stored_provenance["from_candidate_id"]
                    from_judge_sha256 = stored_provenance["from_judge_sha256"]
                    hint_match = max(
                        stored_provenance["match"],
                        hint_match,
                        key=("exact-id", "locus", "window").index,
                    )
                else:
                    from_candidate_id = match.payload["candidate_id"]
                    from_judge_sha256 = match.payload["judge_sha256"]
                hints.append(
                    Hint(
                        candidate_id=identity,
                        node_id=witness.node_id,
                        source="store",
                        source_ref=str(match.source),
                        from_candidate_id=str(from_candidate_id),
                        from_judge_sha256=str(from_judge_sha256),
                        match=hint_match,
                    )
                )
            continue

        survivors = [
            (match, checked)
            for match, checked in validated_matches
            if checked.get("outcome_bucket") == "survived"
            and match.kind in {"exact-id", "locus"}
        ]
        if not survivors:
            unmatched += 1
            continue
        match, chosen = min(
            survivors, key=lambda item: _carry_forward_match_rank(item[0])
        )

        payload = dict(chosen)
        identity_fields = candidate_identity_fields(job)
        payload.update(identity_fields)
        payload.update(
            {
                "candidate_id": identity,
                "replacement_sha256": job.site.replacement_sha256,
                "lineno": job.site.lineno,
                "description": job.site.description,
            }
        )
        payload.pop("locus", None)
        payload.pop("window_key", None)
        payload.update(_locus_fields_for_job(job))
        stored_provenance = record_provenance(chosen)
        provenance_match = match.kind
        if stored_provenance is not None:
            from_candidate_id = stored_provenance["from_candidate_id"]
            from_judge_sha256 = stored_provenance["from_judge_sha256"]
            provenance_match = max(
                stored_provenance["match"],
                provenance_match,
                key=("exact-id", "locus", "window").index,
            )
        else:
            from_candidate_id = match.payload["candidate_id"]
            from_judge_sha256 = chosen["judge_sha256"]
        _set_carry_over_provenance(
            payload,
            kind="carried",
            match=provenance_match,
            from_candidate_id=str(from_candidate_id),
            from_judge_sha256=str(from_judge_sha256),
            into_judge_sha256=judge,
            source=match.source,
            reason=CARRY_FORWARD_REASON,
            imported_at=imported_at,
        )
        payload.pop("campaign_deadline_sha256", None)
        if campaign_deadline_sha256 is not None:
            payload["campaign_deadline_sha256"] = campaign_deadline_sha256
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        try:
            checked = _load_validated_state_record(
                state_root,
                job,
                judge=judge,
                campaign_deadline_sha256=campaign_deadline_sha256,
                raw_override=encoded,
                context=context,
            )
        except MutationStateError:
            checked = None
        if not isinstance(checked, Mapping):
            unmatched += 1
            continue
        guard()
        _write_mutation_state_record(state_root, payload, state_root_fd=state_root_fd)
        records.append(payload)
        counts[match.kind] += 1
        survivors_carried += 1

    return _CarryForwardResult(
        records=tuple(records),
        considered_total=considered_total,
        exact_id=counts["exact-id"],
        locus=counts["locus"],
        window=counts["window"],
        survivors_carried=survivors_carried,
        kills_requeued=kills_requeued,
        ambiguous_loci_ignored=ambiguous,
        unmatched=unmatched,
        from_judges=from_judge_count,
        same_judge_resume=same_judge_resume,
        invalid_records=invalid_records + len(invalid_match_records),
        hints=tuple(hints),
    )


def _write_shard_manifest(
    state_root: Path,
    *,
    state_root_fd: int | None,
    shard_index: int,
    shard_count: int,
    judge: str,
    shard_ids: Sequence[str],
) -> None:
    """(RG-265 review B1) Record which shard of which plan and judge a store holds.

    Written by every ``--shard K/N`` run into its own store before any
    candidate executes, atomically and durably, so a later strict merge can
    prove the store is the right shard rather than trusting its location.
    """
    document = {
        "schema": SHARD_MANIFEST_SCHEMA,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "judge_sha256": judge,
        "plan_sha256": plan_sha256(list(shard_ids)),
        "candidate_total": len(shard_ids),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
    root_fd = _open_state_dir_fd(state_root, state_root_fd)
    try:
        temporary = f".{SHARD_MANIFEST_NAME}-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.write(descriptor, encoded)
            _durable.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, SHARD_MANIFEST_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        _durable.fsync(root_fd)
    finally:
        os.close(root_fd)


def _read_shard_manifest(store: Path) -> Mapping[str, Any]:
    try:
        raw = safeio.read_bounded_input(store, SHARD_MANIFEST_NAME, limit=_SHARD_MANIFEST_LIMIT)
    except AssayError as exc:
        raise ShardStoreMergeError(
            f"shard store {store}: {SHARD_MANIFEST_NAME} is unreadable: {exc}"
        ) from exc
    if raw is None:
        raise ShardStoreMergeError(
            f"shard store {store} has no {SHARD_MANIFEST_NAME}: it was not written by "
            "`assay run --shard K/N` (absent, empty or mistyped store)"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ShardStoreMergeError(
            f"shard store {store}: {SHARD_MANIFEST_NAME} is not JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != _SHARD_MANIFEST_KEYS
        or document["schema"] != SHARD_MANIFEST_SCHEMA
        or any(
            type(document[key]) is not int
            for key in ("shard_index", "shard_count", "candidate_total")
        )
        or not isinstance(document["judge_sha256"], str)
        or not isinstance(document["plan_sha256"], str)
    ):
        raise ShardStoreMergeError(
            f"shard store {store}: {SHARD_MANIFEST_NAME} is not a {SHARD_MANIFEST_SCHEMA} document"
        )
    return document


def _validate_shard_stores(
    stores: Sequence[Path],
    *,
    jobs: Sequence[MutantJob],
    judge: str,
    campaign_deadline_sha256: str | None,
    load_record: Callable[[MutantJob, bytes], Any],
) -> dict[str, int]:
    """(RG-265 review B1) Prove the stores are exactly the N shards of THIS run.

    *jobs* is the whole current plan in plan order. Each store must carry a
    manifest naming a distinct shard ``K`` of ``N = len(stores)``, the current
    judge and the digest of shard K's current candidate ids, a record that
    resumes under the current judge for EVERY candidate of shard K, and no
    record of any other candidate. Anything else -- an absent, empty,
    duplicated, foreign-judge, changed-plan, incomplete or polluted store --
    refuses the run by name before a candidate executes; nothing is executed
    in place of a lost shard. Returns the record count per store.
    """
    problems: list[str] = []
    count = len(stores)
    if not 1 <= count <= MAX_SHARD_COUNT:
        raise ShardStoreMergeError(
            f"a strict shard merge needs 1..{MAX_SHARD_COUNT} stores, got {count}"
        )
    resolved: dict[Path, Path] = {}
    for store in stores:
        key = store.resolve()
        if key in resolved:
            raise ShardStoreMergeError(f"shard store {store} is named twice")
        resolved[key] = store
    current_ids = [candidate_id(job) for job in jobs]
    by_id = dict(zip(current_ids, jobs))
    owners: dict[int, Path] = {}
    counts: dict[str, int] = {}
    for store in stores:
        manifest = _read_shard_manifest(store)
        index, declared = manifest["shard_index"], manifest["shard_count"]
        if declared != count:
            problems.append(
                f"{store}: written as one of {declared} shards, but {count} stores are merged"
            )
            continue
        if not 0 <= index < count:
            problems.append(f"{store}: shard index {index} is outside 0..{count - 1}")
            continue
        if index in owners:
            problems.append(f"{store}: shard {index}/{count} is also {owners[index]}")
            continue
        owners[index] = store
        if manifest["judge_sha256"] != judge:
            problems.append(
                f"{store}: shard {index}/{count} was judged by "
                f"{manifest['judge_sha256'][:12]}, this run judges {judge[:12]} (foreign judge)"
            )
            continue
        expected = [
            current_ids[position]
            for position in select_mutation_shard(current_ids, index=index, count=count)
        ]
        if manifest["plan_sha256"] != plan_sha256(expected):
            problems.append(
                f"{store}: shard {index}/{count} was planned over a different candidate set "
                "(the plan changed since the shard ran)"
            )
            continue
        if manifest["candidate_total"] != len(expected):
            # (review F3-R2) The digest pins the ids; the total must agree
            # with them too, or the manifest was not written by this shard.
            problems.append(
                f"{store}: shard {index}/{count} manifest declares "
                f"{manifest['candidate_total']} candidate(s), its plan has {len(expected)}"
            )
            continue
        expected_set = set(expected)
        extra = sorted(
            entry.name[:-5]
            for entry in os.scandir(store)
            if re.fullmatch(r"[0-9a-f]{64}\.json", entry.name)
            and entry.name[:-5] not in expected_set
        )
        if extra:
            problems.append(
                f"{store}: holds {len(extra)} record(s) outside shard {index}/{count}, "
                f"first {extra[0]}"
            )
        lost: list[str] = []
        for identity in expected:
            try:
                raw = safeio.read_bounded_input(
                    store, mutation_state_record_name(identity), limit=MUTATION_STATE_RECORD_LIMIT
                )
            except AssayError as exc:
                problems.append(f"{store}: record {identity} is unreadable: {exc}")
                continue
            if raw is None:
                lost.append(identity)
                continue
            try:
                record = load_record(by_id[identity], raw)
            except MutationStateError as exc:
                problems.append(f"{store}: record {identity} is invalid: {exc}")
                continue
            if record is None or record is _RECORD_REJECTED or (
                campaign_deadline_sha256 is not None
                and record.get("campaign_deadline_sha256") != campaign_deadline_sha256
            ):
                problems.append(
                    f"{store}: record {identity} does not resume under this judge"
                )
        if lost:
            problems.append(
                f"{store}: shard {index}/{count} is incomplete, {len(lost)} of "
                f"{len(expected)} records missing (first {lost[0]}); finish it with "
                f"--shard {index}/{count} before merging"
            )
        counts[str(store)] = len(expected) - len(lost)
    if problems:
        raise ShardStoreMergeError(
            "strict shard merge refused: " + "; ".join(problems[:20])
            + (f"; ... {len(problems) - 20} more" if len(problems) > 20 else "")
        )
    return counts


def _carry_over_importable(bucket: object, rejudge_outcomes: frozenset[str]) -> bool:
    """(B530) Whether another judge's record of *bucket* may be imported.

    Only final, resource-independent outcomes are: ``killed`` and
    ``survived``. ``crashed``, ``hung`` and ``budget_exceeded`` can depend on
    the memory, CPU and budgets of the run that produced them, and
    ``equivalent`` is a verdict of the producing judge's own equivalence
    check, so all of them re-execute. A bucket the operator named with
    ``--rejudge-outcome`` is never imported either.
    """
    return bucket in CARRY_OVER_IMPORTABLE_BUCKETS and bucket not in rejudge_outcomes


def _carry_over_candidates(source: Path, name: str) -> list[str]:
    """Relative spellings of every record of one candidate in one source."""
    relatives = [name]
    try:
        with os.scandir(source / JUDGE_GENERATIONS_DIR) as children:
            generations = sorted(
                child.name
                for child in children
                if _CANDIDATE_ID_RE.fullmatch(child.name)
                and child.is_dir(follow_symlinks=False)
            )
    except (FileNotFoundError, NotADirectoryError):
        generations = []
    relatives.extend(f"{JUDGE_GENERATIONS_DIR}/{judge}/{name}" for judge in generations)
    return relatives


def _carry_over_records(
    flex: FlexOptions,
    *,
    jobs: Sequence[MutantJob],
    judge: str,
    state_root: Path,
    state_root_fd: int | None,
    state_root_guard: Callable[[], None] | None,
    campaign_deadline_sha256: str | None,
    context: RecordContext,
    rejudge_outcomes: frozenset[str],
    clock: Clock,
    resource_sha256: str | None = None,
) -> _CarryOverResult:
    """(B530) Reuse finished records of other judges, with provenance.

    For each candidate still pending, the sources are searched in the order
    given (flat record first, then ``judges/*/`` generations). The first
    record that is structurally valid for THIS candidate's identity under the
    current loader, whatever judge produced it, and whose bucket is
    ``killed`` or ``survived`` (and not named by ``--rejudge-outcome``) is
    taken:

    * a record already evidence for this judge (same effective judge, same
      campaign binding) is copied verbatim -- "copied", not "imported";
    * any other is imported: ``judge_sha256`` keeps the judge that EXECUTED
      it, and a ``carry_over`` block names the judge it is imported into, the
      source, the operator's reason, the time and this assay's version.

    Either way the record is written into this run's store (an older record
    of another judge there is archived first, B527), re-validated as a
    resume record of this judge, and resumed. A source record that is
    corrupt, of a refused shape, or of another bucket is not taken; its
    candidate simply executes. Nothing here can fail the lane.
    """
    guard: Callable[[], None] = state_root_guard or (lambda: None)
    taken: list[Mapping[str, Any]] = []
    refused: list[tuple[str, str]] = []
    imported_total = 0
    copied_total = 0
    locus_unavailable = 0
    from_judges: collections.Counter[str] = collections.Counter()
    imported_at = iso_utc(clock())
    # Merge sources (same judge only) first, then carry-over sources.
    sources: list[tuple[Path, bool]] = [
        *((path, True) for path in (*flex.merge_sources, *flex.shard_stores)),
        *((path, False) for path in flex.carry_over_sources),
    ]
    for job in jobs:
        guard()
        identity = candidate_id(job)
        name = mutation_state_record_name(identity)
        chosen: Mapping[str, Any] | None = None
        chosen_source: Path | None = None
        last_reason: str | None = None
        for source, same_judge_only in sources:
            for relative in _carry_over_candidates(source, name):
                try:
                    raw = safeio.read_bounded_input(
                        source, relative, limit=MUTATION_STATE_RECORD_LIMIT
                    )
                except AssayError as exc:
                    last_reason = f"unreadable in {source}: {exc}"
                    continue
                if raw is None:
                    continue
                status: list[bool] = []
                try:
                    candidate = _load_validated_state_record(
                        state_root,
                        job,
                        judge=judge,
                        raw_override=raw,
                        accept_any_judge=True,
                        context=context,
                        locus_status=status,
                    )
                except MutationStateError as exc:
                    locus_unavailable += sum(status)
                    last_reason = f"invalid in {source}: {exc}"
                    continue
                locus_unavailable += sum(status)
                if candidate is None or candidate is _RECORD_REJECTED:
                    last_reason = f"refused shape in {source}/{relative}"
                    continue
                assert not isinstance(candidate, str)
                bucket = candidate.get("outcome_bucket")
                if same_judge_only:
                    if record_effective_judge(candidate) != judge or (
                        campaign_deadline_sha256 is not None
                        and candidate.get("campaign_deadline_sha256")
                        != campaign_deadline_sha256
                    ):
                        last_reason = f"another judge in merge source {source}"
                        continue
                    if bucket in rejudge_outcomes or (
                        bucket in RESOURCE_SENSITIVE_BUCKETS
                        and resource_sha256 is not None
                        and candidate.get("resource_sha256") != resource_sha256
                    ):
                        last_reason = f"bucket {bucket} is re-executed"
                        continue
                elif not _carry_over_importable(bucket, rejudge_outcomes):
                    last_reason = f"bucket {bucket} is not carried over"
                    continue
                chosen = candidate
                chosen_source = source
                break
            if chosen is not None:
                break
        if chosen is None:
            if last_reason is not None:
                refused.append((identity, last_reason))
            continue
        assert chosen_source is not None
        same_judge = record_effective_judge(chosen) == judge and (
            campaign_deadline_sha256 is None
            or chosen.get("campaign_deadline_sha256") == campaign_deadline_sha256
        )
        if same_judge:
            payload = dict(chosen)
        else:
            payload = dict(chosen)
            assert flex.carry_over_reason is not None
            _set_carry_over_provenance(
                payload,
                kind="imported",
                match="exact-id",
                from_candidate_id=identity,
                from_judge_sha256=str(chosen["judge_sha256"]),
                into_judge_sha256=judge,
                source=chosen_source,
                reason=flex.carry_over_reason,
                imported_at=imported_at,
            )
            payload.pop("campaign_deadline_sha256", None)
            if campaign_deadline_sha256 is not None:
                payload["campaign_deadline_sha256"] = campaign_deadline_sha256
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        check = _load_validated_state_record(
            state_root,
            job,
            judge=judge,
            campaign_deadline_sha256=campaign_deadline_sha256,
            raw_override=encoded,
            context=context,
        )
        if check is None or check is _RECORD_REJECTED:
            refused.append((identity, "imported record would not resume"))
            continue
        _write_mutation_state_record(state_root, payload, state_root_fd=state_root_fd)
        taken.append(payload)
        if same_judge:
            copied_total += 1
        else:
            imported_total += 1
            from_judges[str(chosen["judge_sha256"])] += 1
    return _CarryOverResult(
        records=tuple(taken),
        imported_total=imported_total,
        copied_total=copied_total,
        refused=tuple(refused),
        from_judge_counts=dict(from_judges),
        locus_unavailable=locus_unavailable,
    )


def _canonical_state_record_bytes(payload: Mapping[str, Any]) -> bytes:
    """The exact reserialization :func:`_write_mutation_state_record` publishes."""
    return (
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _parse_flat_state_record(raw: bytes) -> Mapping[str, Any] | None:
    """Decode just enough of a flat record to classify a manifest refusal."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _scan_store(path: Path):
    """The one owner-store walk the manifest counter can be tested through."""
    return os.scandir(path)


def validate_merge_manifest(
    manifest: MergeManifest, request: CombineRequest
) -> _MergeManifestResult:
    """Prove the owner manifest and materialize its flat records into the target.

    The plan proof precedes every record read. Only after every current
    candidate has one valid owner record are those records written into the
    target, before own-store resume. Generations are deliberately never read:
    the manifest names the flat record that owns the candidate.
    """
    assert request.judge is not None
    assert request.state_root is not None
    judge = request.judge
    state_root = request.state_root
    current_ids = tuple(candidate_id(job) for job in request.jobs)
    owners = {
        candidate: store
        for store in manifest.stores
        for candidate in store.candidates
    }
    problems: list[_MergeManifestProblem] = []
    if manifest.judge_sha256 != judge:
        problems.append(
            _MergeManifestProblem(
                token="judge-mismatch",
                identity=str(manifest.path),
                detail=(
                    f"manifest judge {manifest.judge_sha256[:12]} does not match "
                    f"this run's judge {judge[:12]}"
                ),
            )
        )
    for candidate in sorted(set(owners) - set(current_ids)):
        problems.append(
            _MergeManifestProblem(
                token="unknown-candidate",
                identity=candidate,
                detail=(
                    f"manifest owns candidate {candidate} outside the current plan"
                ),
            )
        )
    for candidate in sorted(set(current_ids) - set(owners)):
        problems.append(
            _MergeManifestProblem(
                token="unowned-candidate",
                identity=candidate,
                detail=f"current candidate {candidate} has no manifest owner",
            )
        )
    if problems:
        raise MergeManifestError(_merge_manifest_message(problems))

    owned_records: list[tuple[str, MergeManifestStore, Mapping[str, Any]]] = []
    for job in request.jobs:
        request.deadline_check()
        identity = candidate_id(job)
        owner = owners[identity]
        try:
            raw = safeio.read_bounded_input(
                owner.path,
                mutation_state_record_name(identity),
                limit=MUTATION_STATE_RECORD_LIMIT,
            )
        except AssayError as exc:
            problems.append(
                _MergeManifestProblem(
                    token="invalid-owner-record",
                    identity=identity,
                    detail=f"{owner.path}: record is unreadable: {exc}",
                )
            )
            continue
        if raw is None:
            problems.append(
                _MergeManifestProblem(
                    token="missing-owner-record",
                    identity=identity,
                    detail=(
                        f"{owner.path}: no flat "
                        f"{mutation_state_record_name(identity)}"
                    ),
                )
            )
            continue
        parsed = _parse_flat_state_record(raw)
        if parsed is None:
            problems.append(
                _MergeManifestProblem(
                    token="invalid-owner-record",
                    identity=identity,
                    detail=f"{owner.path}: flat record is not a UTF-8 JSON object",
                )
            )
            continue
        effective = record_effective_judge(parsed)
        if effective is not None and effective != judge:
            problems.append(
                _MergeManifestProblem(
                    token="judge-mismatch",
                    identity=identity,
                    detail=(
                        f"{owner.path}: record is effective for {effective}, "
                        f"this run judges {judge}"
                    ),
                )
            )
            continue
        try:
            record = _load_validated_state_record(
                state_root,
                job,
                judge=judge,
                campaign_deadline_sha256=request.campaign_deadline_sha256,
                raw_override=raw,
                context=request.context,
            )
        except MutationStateError as exc:
            problems.append(
                _MergeManifestProblem(
                    token="invalid-owner-record",
                    identity=identity,
                    detail=f"{owner.path}: record is invalid: {exc}",
                )
            )
            continue
        if record is None or record is _RECORD_REJECTED:
            problems.append(
                _MergeManifestProblem(
                    token="invalid-owner-record",
                    identity=identity,
                    detail=(
                        f"{owner.path}: record does not resume under this run "
                        "and campaign"
                    ),
                )
            )
            continue
        assert not isinstance(record, str)
        owned_records.append((identity, owner, record))
    if problems:
        raise MergeManifestError(_merge_manifest_message(problems))

    # Count records the manifest deliberately ignores. The owner map, not a
    # source's age or mtime, decides which spelling is a duplicate.
    planned_ids = frozenset(current_ids)
    unplanned_records = 0
    non_owner_duplicates = 0
    for store in manifest.stores:
        request.deadline_check()
        try:
            entries = _scan_store(store.path)
        except OSError:
            # Every owner record above already proved this directory readable.
            # A second walk that loses it contributes no ignored-record counts.
            continue
        with entries:
            for entry in entries:
                if re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) is None:
                    continue
                identity = entry.name[:-5]
                if identity not in planned_ids:
                    unplanned_records += 1
                elif owners[identity] is not store:
                    non_owner_duplicates += 1

    target_replaced = 0
    for identity, _owner, record in owned_records:
        request.deadline_check()
        try:
            existing = _read_state_record_bytes(
                state_root, identity, state_root_fd=request.state_root_fd
            )
        except MutationStateError:
            existing = None
        if existing is not None:
            previous = _parse_flat_state_record(existing)
            if (
                previous is not None
                and record_effective_judge(previous) == judge
                and _canonical_state_record_bytes(previous)
                != _canonical_state_record_bytes(record)
            ):
                target_replaced += 1
        _write_mutation_state_record(
            state_root,
            record,
            state_root_fd=request.state_root_fd,
            commit_guard=request.deadline_check,
        )
        request.deadline_check()

    return _MergeManifestResult(
        owners=len(owned_records),
        non_owner_duplicates=non_owner_duplicates,
        unplanned_records=unplanned_records,
        target_replaced=target_replaced,
    )


def _resume_own_store(request: CombineRequest) -> _OwnStoreResult:
    """Apply D5's verbatim own-store resume to this run's selected jobs."""
    records: list[Mapping[str, Any]] = []
    rejudged_candidate_ids: set[str] = set()
    rejected_total = 0
    rejudged_total = 0
    resource_rejudged_total = 0
    promoted_total = 0
    locus_unavailable_total = 0
    if not request.resume:
        return _OwnStoreResult(
            records=(),
            rejected_total=0,
            rejudged_total=0,
            resource_rejudged_total=0,
            promoted_total=0,
            locus_unavailable_total=0,
            rejudged_candidate_ids=frozenset(),
        )
    assert isinstance(request.state_root, Path)
    assert request.judge is not None
    state_root = request.state_root
    judge = request.judge
    if request.state_root_guard is not None:
        request.state_root_guard()
    # (B091/D-23, P7 A5) `--rejudge <id>[,...]` refuses BEFORE any
    # record is even loaded, the moment a named id does not match
    # ANY of this run's own current candidate identities. Candidate
    # ids fold in the mutant's own source bytes by construction
    # (B066), so an id whose source has since changed simply no
    # longer appears in `current_ids` -- indistinguishable, at the
    # id level, from a typo or a stale id copied from a different
    # run. Either way, silently ignoring it (treating "not found"
    # as "nothing to do") would let a consumer believe a rejudge
    # happened when it did not -- exactly the B088 principle this
    # item cross-references: an identity that no longer matches
    # current reality must not be silently trusted.
    if request.rejudge_ids:
        _reject_unknown_rejudge_ids(
            request.rejudge_ids, {candidate_id(job) for job in request.jobs}
        )
    for job in request.jobs:
        if request.state_root_guard is not None:
            request.state_root_guard()
        flat_locus_status: list[bool] = []
        record = _load_validated_state_record(
            state_root,
            job,
            judge=judge,
            campaign_deadline_sha256=request.campaign_deadline_sha256,
            context=request.context,
            state_root_fd=request.state_root_fd,
            locus_status=flat_locus_status,
        )
        locus_unavailable_total += sum(flat_locus_status)
        if record is _RECORD_REJECTED or record is None:
            # (B527) The flat name may hold another judge's record
            # (archived generations live under `judges/<judge>/`).
            # A generation of THIS judge resumes and is promoted back
            # to the flat name; the displaced record is archived.
            generation_locus_status: list[bool] = []
            generation = _load_validated_state_record(
                state_root,
                job,
                judge=judge,
                campaign_deadline_sha256=request.campaign_deadline_sha256,
                context=request.context,
                raw_override=_read_generation_record_bytes(
                    state_root,
                    candidate_id(job),
                    judge,
                    state_root_fd=request.state_root_fd,
                ),
                locus_status=generation_locus_status,
            )
            locus_unavailable_total += sum(generation_locus_status)
            if generation is not None and generation is not _RECORD_REJECTED:
                assert not isinstance(generation, str)
                _write_mutation_state_record(
                    state_root, generation, state_root_fd=request.state_root_fd
                )
                promoted_total += 1
                record = generation
        if record is _RECORD_REJECTED:
            rejected_total += 1
        elif record is not None:
            assert not isinstance(record, str)
            # (B091/D-23, P7 A5) A record matching EITHER selection
            # is dropped here, before it ever reaches
            # `resumed_records` -- the exact same effect as a
            # candidate with no record at all, so it falls straight
            # through to `pending_jobs` below and genuinely
            # re-executes against the CURRENT judging suite, never
            # replaying the old verdict.
            if (
                candidate_id(job) in request.rejudge_ids
                or record.get("outcome_bucket") in request.rejudge_outcomes
            ):
                rejudged_total += 1
                rejudged_candidate_ids.add(candidate_id(job))
                continue
            if (
                request.resource_sha256 is not None
                and record.get("outcome_bucket") in RESOURCE_SENSITIVE_BUCKETS
                and record.get("resource_sha256") != request.resource_sha256
            ):
                # (B529) Resource settings left the judge identity,
                # so an outcome that can depend on them is re-executed
                # when they changed; killed/survived are reused.
                resource_rejudged_total += 1
                rejudged_candidate_ids.add(candidate_id(job))
                continue
            records.append(record)
    return _OwnStoreResult(
        records=tuple(records),
        rejected_total=rejected_total,
        rejudged_total=rejudged_total,
        resource_rejudged_total=resource_rejudged_total,
        promoted_total=promoted_total,
        locus_unavailable_total=locus_unavailable_total,
        rejudged_candidate_ids=frozenset(rejudged_candidate_ids),
    )


def _flex_from_sources(
    sources: Sequence[StoreSource], *, carry_over_reason: str | None
) -> FlexOptions:
    """Project source records into the legacy exact-id/carry-forward helpers."""
    for source in sources:
        if source.policy not in COMBINE_POLICIES:
            raise ValueError(f"unknown combine policy {source.policy!r}")
        if not isinstance(source.path, Path):
            raise ValueError("combine source paths must be Path objects")
    manifests = tuple(
        source.path for source in sources if source.policy == "owner-manifest"
    )
    if len(manifests) > 1:
        raise ValueError("combine accepts at most one owner manifest")
    return FlexOptions(
        merge_sources=tuple(
            source.path for source in sources if source.policy == "same-judge"
        ),
        shard_stores=tuple(
            source.path for source in sources if source.policy == "strict-shard"
        ),
        merge_manifest=manifests[0] if manifests else None,
        carry_over_sources=tuple(
            source.path
            for source in sources
            if source.policy == "import-unverified"
        ),
        carry_over_reason=carry_over_reason,
        carry_forward_sources=tuple(
            source.path for source in sources if source.policy == "carry-forward"
        ),
    )


def combine(
    sources: Sequence[StoreSource], request: CombineRequest
) -> CombineResult:
    """Apply the fixed same-judge → shard → owner-manifest → import →
    carry-forward pipeline.
    """
    if request.judge is None:
        if request.resume or sources:
            raise ValueError("mutation store combine requires a judge identity")
        return CombineResult(
            records=(),
            rejected_total=0,
            rejudged_total=0,
            resource_rejudged_total=0,
            promoted_total=0,
            imported_total=0,
            locus_unavailable_total=0,
            rejudged_candidate_ids=frozenset(),
            hints=(),
        )
    if not request.resume and sources:
        raise ValueError("mutation store combine requires resume=True")

    exact = _flex_from_sources(
        sources, carry_over_reason=request.carry_over_reason
    )
    state_root = request.state_root
    judge = request.judge

    if exact.shard_stores:
        assert state_root is not None

        def _strict_record(job: MutantJob, raw: bytes) -> Any:
            return _load_validated_state_record(
                state_root,
                job,
                judge=judge,
                raw_override=raw,
                context=request.context,
            )

        shard_counts = _validate_shard_stores(
            exact.shard_stores,
            jobs=request.jobs,
            judge=judge,
            campaign_deadline_sha256=request.campaign_deadline_sha256,
            load_record=_strict_record,
        )
        if request.write_progress is not None:
            request.write_progress(
                {
                    "event": "shard_merge",
                    "candidate_total": request.total,
                    "shard_count": len(exact.shard_stores),
                    "records_per_store": shard_counts,
                }
            )

    if exact.merge_manifest is not None:
        assert state_root is not None
        try:
            manifest = load_merge_manifest(exact.merge_manifest)
        except LaneConfigError as exc:
            raise MergeManifestError(str(exc)) from exc
        if request.rejudge_ids:
            # The manifest is the only combine stage that writes before own
            # resume. Decide this later refusal now, so a refused run leaves
            # every target record byte-identical.
            _reject_unknown_rejudge_ids(
                request.rejudge_ids,
                {candidate_id(job) for job in request.jobs},
            )
        manifest_result = validate_merge_manifest(manifest, request)
        if request.write_progress is not None:
            request.write_progress(
                {
                    "event": "merge_manifest",
                    "candidate_total": request.total,
                    "owners": manifest_result.owners,
                    "non_owner_duplicates": manifest_result.non_owner_duplicates,
                    "unplanned_records": manifest_result.unplanned_records,
                    "target_replaced": manifest_result.target_replaced,
                }
            )

    own = _resume_own_store(request)
    resumed_records = list(own.records)
    imported_total = 0
    locus_unavailable_total = own.locus_unavailable_total
    hints: list[Hint] = []

    if exact.reads_carry_over_stores:
        assert state_root is not None
        resumed_ids = {
            record["candidate_id"] for record in resumed_records
        }
        carry = _carry_over_records(
            exact,
            jobs=[
                job
                for job in request.jobs
                if candidate_id(job) not in resumed_ids
                and candidate_id(job) not in request.rejudge_ids
            ],
            judge=judge,
            state_root=state_root,
            state_root_fd=request.state_root_fd,
            state_root_guard=request.state_root_guard,
            campaign_deadline_sha256=request.campaign_deadline_sha256,
            context=request.context,
            rejudge_outcomes=request.rejudge_outcomes,
            resource_sha256=request.resource_sha256,
            clock=request.clock,
        )
        resumed_records.extend(carry.records)
        if request.write_progress is not None:
            request.write_progress(
                {
                    "event": "carry_over",
                    "candidate_total": request.total,
                    "imported_total": carry.imported_total,
                    "copied_total": carry.copied_total,
                    "refused_total": len(carry.refused),
                    "refused_sample": [
                        {"candidate_id": identity, "reason": reason}
                        for identity, reason in carry.refused[:10]
                    ],
                    "from_judge_counts": dict(
                        sorted(carry.from_judge_counts.items())
                    ),
                    "sources": [
                        str(path) for path in exact.carry_over_sources
                    ],
                    "merge_sources": [
                        str(path) for path in exact.merge_sources
                    ],
                    "shard_stores": [str(path) for path in exact.shard_stores],
                    "reason": exact.carry_over_reason,
                }
            )
        imported_total = carry.imported_total
        locus_unavailable_total += carry.locus_unavailable

    if exact.carry_forward_sources:
        assert state_root is not None
        resumed_ids_before_carry = frozenset(
            record["candidate_id"] for record in resumed_records
        )
        carry_forward = _carry_forward_records(
            exact,
            jobs=[
                job
                for job in request.jobs
                if candidate_id(job) not in request.rejudge_ids
            ],
            skip_candidate_ids=resumed_ids_before_carry,
            judge=judge,
            state_root=state_root,
            state_root_fd=request.state_root_fd,
            state_root_guard=request.state_root_guard,
            campaign_deadline_sha256=request.campaign_deadline_sha256,
            context=request.context,
            rejudge_outcomes=request.rejudge_outcomes,
            clock=request.clock,
            deadline_check=request.deadline_check,
        )
        resumed_records.extend(carry_forward.records)
        imported_total += carry_forward.survivors_carried
        hints.extend(carry_forward.hints)
        if request.write_progress is not None:
            request.write_progress(
                {
                    "event": "carry_forward",
                    "candidate_total": request.total,
                    "considered_total": carry_forward.considered_total,
                    "exact_id": carry_forward.exact_id,
                    "locus": carry_forward.locus,
                    "window": carry_forward.window,
                    "survivors_carried": carry_forward.survivors_carried,
                    "kills_requeued": carry_forward.kills_requeued,
                    "ambiguous_loci_ignored": (
                        carry_forward.ambiguous_loci_ignored
                    ),
                    "unmatched": carry_forward.unmatched,
                    "from_judges": carry_forward.from_judges,
                    "same_judge_resume": carry_forward.same_judge_resume,
                    "invalid_records": carry_forward.invalid_records,
                    "sources": [
                        str(path) for path in exact.carry_forward_sources
                    ],
                    "reason": CARRY_FORWARD_REASON,
                }
            )

    return CombineResult(
        records=tuple(resumed_records),
        rejected_total=own.rejected_total,
        rejudged_total=own.rejudged_total,
        resource_rejudged_total=own.resource_rejudged_total,
        promoted_total=own.promoted_total,
        imported_total=imported_total,
        locus_unavailable_total=locus_unavailable_total,
        rejudged_candidate_ids=own.rejudged_candidate_ids,
        hints=tuple(hints),
    )


def _has_clean_failure_report(
    *,
    outcome: Outcome,
    returncode: int | None,
    witness: MutationWitnessReceipt | None,
    process_tree: ProcessTreeEvidence | None,
) -> bool:
    """Whether a normal pytest failed-call receipt independently proves FAIL."""
    if (
        outcome is not Outcome.FAIL
        or returncode != 1
        or process_tree is None
        or process_tree.leader_status != "exited"
        or process_tree.outliving_descendants != 0
        or not process_tree.clean
    ):
        return False
    if isinstance(witness, MutationWitnessReceipt):
        report = witness.to_dict()
    else:
        return False
    return (
        set(report)
        == {
            "node_id",
            "when",
            "outcome",
            "session_exit_status",
            "process_exit_status",
        }
        and isinstance(report.get("node_id"), str)
        and bool(report.get("node_id"))
        and report.get("when") == "call"
        and report.get("outcome") == "failed"
        and type(report.get("session_exit_status")) is int
        and report.get("session_exit_status") == 1
        and type(report.get("process_exit_status")) is int
        and report.get("process_exit_status") == returncode
    )


def _non_exited_judgeable_terminal(
    process_tree: ProcessTreeEvidence | None,
    terminal: _termination.Termination,
) -> bool:
    return (
        process_tree is not None
        and process_tree.leader_status != "exited"
        and terminal is _termination.Termination.PASSED
    )


def _bucket_for_run(
    run: _MutantRun,
    *,
    equivalence_artifact: str | None,
    baseline_equivalence: bytes | None,
    kill_signal_artifact: str | None,
) -> str:
    """Classify one complete R2 candidate run using the shared terminal row."""
    process_tree = run.process_tree_evidence
    observation = _contention.observation_input(
        _contention.ContentionObservation(
            "clear" if run.resource_limit_evidence is not None else "unavailable",
            limits=run.resource_limit_evidence,
        )
    )
    terminal = _termination.classify(
        outcome=run.result.outcome,
        reason_code=run.result.reason_code,
        returncode=run.result.returncode,
        decided_by_report=_has_clean_failure_report(
            outcome=run.result.outcome,
            returncode=run.result.returncode,
            witness=run.execution.witness,
            process_tree=process_tree,
        ),
        process_tree=process_tree,
        limits=run.resource_limit_evidence,
        observation=observation,
        observation_required=True,
    )
    if _non_exited_judgeable_terminal(process_tree, terminal):
        return "crashed"
    bucket = _termination.r2_bucket(terminal)
    if terminal in _termination.CREDIT_JUDGEABLE:
        if run.forced_bucket is not None:
            bucket = run.forced_bucket
        elif equivalence_artifact is None:
            bucket = (
                "survived"
                if terminal is _termination.Termination.PASSED
                else "killed"
            )
        else:
            assert baseline_equivalence is not None
            bucket = _equivalence_bucket(
                terminal,
                equivalence_bytes=run.equivalence_bytes,
                baseline_equivalence=baseline_equivalence,
            )
    if (
        bucket == "killed"
        and kill_signal_artifact is not None
        and run.kill_signal is None
    ):
        return "crashed"
    return bucket


def _equivalence_bucket(
    terminal: _termination.Termination,
    *,
    equivalence_bytes: bytes | None,
    baseline_equivalence: bytes,
) -> str:
    """(P34/A-279, carve §3.6) the SAME four-outcome split widened into a
    function of ``(outcome, artifact-present, artifact-bytes)`` -- reachable
    ONLY when the lane declares ``judge.mutation.equivalence_artifact``. The
    caller (:func:`run_mutation`) decides before applying this extension
    whether the lane declares an equivalence artifact.

    ``PASS`` and ``FAIL`` both split the identical way: an ABSENT artifact
    means the mutated schema was never measured at all -- the lane declared
    one and its own command did not write it, so nothing was observed and
    this is ``crashed``, never a kill and never a survival (§9 M11's own
    reachable case: invalid DDL that still exits non-zero looks like a kill
    under the old exit-status-only mapping). Byte-EQUAL to the baseline's
    own artifact means the mutant provably changed nothing -- ``equivalent``,
    evidence about the MUTATION rather than about the tests. Anything else
    is the ordinary ``PASS``->``survived`` / ``FAIL``->``killed`` mapping.
    Non-judgeable terminations are already mapped by the shared table.
    """
    if terminal not in _termination.JUDGEABLE:
        return _termination.r2_bucket(terminal)
    if terminal is _termination.Termination.PASSED:
        if equivalence_bytes is None:
            return "crashed"
        return "equivalent" if equivalence_bytes == baseline_equivalence else "survived"
    if equivalence_bytes is None:
        return "crashed"
    return "equivalent" if equivalence_bytes == baseline_equivalence else "killed"
