"""Tier-3 ATTESTED evidence: load it, and prove whether it is stale.

DESIGN-GUIDE §6/§3: assay never verifies an external review's content — it
verifies only whether the review's own claim ("I reviewed these paths as of
this commit") is still current relative to the commit under test. What makes
an attestation more than a rubber stamp, all of it mechanical:

* it names the commit it reviewed, which must be the commit under test or an
  ancestor of it — an unrelated or descendant commit cannot satisfy the
  record;
* it records its producer (model id or human identity);
* assay diffs the paths it claims to have reviewed between the attested
  commit and the commit under test — byte-identical paths stay current, a
  changed one renders ``STALE_ATTESTATION``;
* the evidence entry always carries ``verified_by_assay: False`` — enforced
  by :class:`~assay.verdict.Evidence` itself, not repeated here.

**P26 (A-210/A-211) closes two false PASSes the pre-P26 implementation had:**
a changed file beneath a reviewed *directory* used to read as current (a
plain ``diff --name-only`` membership test never asked whether the directory
itself changed), and a ``../`` evidence key used to read a seeded record
outside the declared attestation directory. Both are now the DECLARED input's
own contract: closed, bounded, descriptor-safe reads
(:func:`assay.safeio.read_bounded_input`) before any Git, and four narrow
sanitized Git helpers (:mod:`assay.git`) that ask for one exact identity —
never a display-name parser, a membership set, or a ref shorthand.

Four layers, kept deliberately separate so each is independently testable:

* :func:`parse_attestation` — pure format loading. No git, no filesystem; a
  malformed attestation body (bad JSON, duplicate member names, a value
  outside the closed grammar) raises :class:`~assay.errors.AssayError`
  (``ERROR``/``UNREADABLE_ARTIFACT``) here, independent of any commit's
  resolvability.
* :func:`load_attestation_file` — reads exactly ``<attestation_dir>/<key>.json``
  through the descriptor-safe seam and parses it. ``None`` is a genuine
  absence (no producer supplied a record); malformed/unsafe input is
  ``UNREADABLE_ARTIFACT``.
* :func:`evaluate_attestation` — the git-dependent core (A-211): verifies the
  attested commit is an exact, real commit and an ancestor-or-equal of the
  commit under test, requires every declared reviewed path to exist there,
  then requires every one to be byte-identical at the commit under test.
  Never raises for a judged outcome; every Git-level failure this function's
  own checks can produce is caught and rendered ``ERROR``/``UNREADABLE_ARTIFACT``,
  except the lane's own ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``, which is never
  remapped.
* :func:`load_attested_evidence` — the orchestration entry point (A-210):
  stages every declared identity's safe read/parse BEFORE any Git call,
  enforces the aggregate query-cost ceiling before the first Git argv, then
  resolves each valid record in declaration order.

No function in this module ever constructs a
:class:`~assay.verdict.Claim` — only :class:`~assay.verdict.Evidence`.
``Claim.source`` is closed to ``CLAIM_SOURCES = ("computed",)``
(:mod:`assay.verdict`, unmodified by this package), so an attestation path
structurally cannot become a computed claim; this module does not attempt to
and never imports ``Claim``.

Every rejection here raises :class:`~assay.errors.AssayError` directly; there
is no locally-defined exception type (A-092 — ``errors.py`` is outside this
package's ``scope.touch``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from . import git, safeio
from .errors import AssayError, Outcome, ReasonCode
from .verdict import Evidence, EvidenceDeclaration

__all__ = [
    "MAX_ATTESTATION_BYTES",
    "MAX_ATTESTATION_DIR_BYTES",
    "MAX_ATTESTATION_DIR_COMPONENTS",
    "MAX_EVIDENCE_DECLARATIONS",
    "MAX_GIT_PATH_QUERIES",
    "MAX_PRODUCER_BYTES",
    "MAX_REVIEWED_PATHS",
    "MAX_REVIEWED_PATH_BYTES",
    "AttestationRecord",
    "evaluate_attestation",
    "load_attestation_file",
    "load_attested_evidence",
    "parse_attestation",
]

#: P26/A-210 fixed constants. Every one of these is an authoritative bound;
#: none is derived from the machine or the repository.
MAX_EVIDENCE_DECLARATIONS = 64
MAX_ATTESTATION_BYTES = 1024 * 1024
MAX_ATTESTATION_DIR_BYTES = 4096
MAX_ATTESTATION_DIR_COMPONENTS = 128
MAX_PRODUCER_BYTES = 256
MAX_REVIEWED_PATHS = 1000
MAX_REVIEWED_PATH_BYTES = 4096
#: Each reviewed path costs exactly two Git argv (``ls-tree`` + ``diff``).
MAX_GIT_PATH_QUERIES = 4096

_QUERIES_PER_REVIEWED_PATH = 2

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_ATTESTATION_DIR_CONTROL: frozenset[str] = frozenset(
    chr(c) for c in range(0x20)
) | {chr(0x7F)}
_RECORD_KEYS = frozenset({"producer", "attested_commit", "reviewed_paths"})


def _unreadable(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT)


def _bad_lane_config(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG)


@dataclass(frozen=True, kw_only=True)
class AttestationRecord:
    """One parsed attestation file: what an external review claims (A-092).

    Deliberately the same three fields :class:`~assay.verdict.Evidence`'s own
    attested payload carries (``producer``, ``attested_commit``,
    ``reviewed_paths``) — this type exists only because :class:`Evidence`
    also carries ``source``/``key``/``status``/``verified_by_assay``, which
    are not properties of the REVIEW itself but of assay's judgement about
    it; keeping them separate is what lets :func:`evaluate_attestation` take
    a record and PRODUCE the judgement, rather than being handed a
    half-built :class:`Evidence` to mutate.
    """

    producer: str
    attested_commit: str
    reviewed_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.producer, str) or not self.producer:
            raise ValueError(
                f"attestation producer must be a non-empty string, got {self.producer!r}"
            )
        if not isinstance(self.attested_commit, str) or not self.attested_commit:
            raise ValueError(
                f"attestation attested_commit must be a non-empty string, got "
                f"{self.attested_commit!r}"
            )
        if not isinstance(self.reviewed_paths, tuple) or not self.reviewed_paths:
            raise ValueError(
                f"attestation reviewed_paths must be a non-empty tuple, got "
                f"{self.reviewed_paths!r}"
            )
        for path in self.reviewed_paths:
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"attestation reviewed path must be a non-empty string, got {path!r}"
                )
        if len(set(self.reviewed_paths)) != len(self.reviewed_paths):
            raise ValueError(
                f"attestation reviewed_paths contains a duplicate: {self.reviewed_paths}"
            )


# --- format loading: pure, no git/filesystem involved -------------------------


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """``object_pairs_hook`` rejecting a duplicate JSON member name (A-210):
    the default decoder silently keeps the LAST value, which makes identity
    ambiguous for a security-relevant field. Reject rather than choose.
    """
    seen: set[str] = set()
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON member {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _utf8_bytes(value: str, what: str) -> bytes:
    """Encode *value* as UTF-8 or raise -- catches a lone surrogate, which a
    nominal string-length bound would not (A-210)."""
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{what} cannot be encoded as UTF-8: {exc}") from exc


def _validate_reviewed_path(path: object, source_name: str) -> str:
    if not isinstance(path, str):
        raise _unreadable(
            f"{source_name}: a reviewed path must be a string, got {type(path).__name__}"
        )
    encoded = _utf8_bytes(path, "a reviewed path")
    if not (1 <= len(encoded) <= MAX_REVIEWED_PATH_BYTES):
        raise _unreadable(
            f"{source_name}: reviewed path must be 1..{MAX_REVIEWED_PATH_BYTES} "
            f"UTF-8 bytes, got {len(encoded)}"
        )
    if "\x00" in path or path.startswith("/"):
        raise _unreadable(f"{source_name}: reviewed path {path!r} is not project-relative")
    parts = path.split("/")
    if "" in parts or "." in parts or ".." in parts:
        raise _unreadable(
            f"{source_name}: reviewed path {path!r} is not canonical repo-top-relative "
            f"POSIX spelling"
        )
    return path


def parse_attestation(text: str, *, source_name: str) -> AttestationRecord:
    """Parse *text* (an attestation file's raw content) into an
    :class:`AttestationRecord`.

    *source_name* names *text*'s origin (typically a file path) for the error
    message only — this function never touches the filesystem itself, so
    :func:`load_attestation_file` and a test feeding in a literal string share
    this one parser.

    The only JSON object is exactly ``{"producer", "attested_commit",
    "reviewed_paths"}``. Duplicate member names and strings that cannot be
    encoded as UTF-8 (including a lone surrogate) are refused here, before any
    Git call. Raises :class:`~assay.errors.AssayError`
    (``ERROR``/``UNREADABLE_ARTIFACT``) on any violation of the closed
    grammar (A-210).
    """
    try:
        payload = json.loads(text, object_pairs_hook=_no_duplicate_pairs)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _unreadable(f"{source_name}: not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise _unreadable(
            f"{source_name}: attestation must be a JSON object, got "
            f"{type(payload).__name__}"
        )
    if set(payload) != _RECORD_KEYS:
        raise _unreadable(
            f"{source_name}: must have exactly the keys {sorted(_RECORD_KEYS)}, "
            f"got {sorted(payload)}"
        )

    producer = payload["producer"]
    if not isinstance(producer, str):
        raise _unreadable(f"{source_name}: 'producer' must be a string")
    try:
        producer_bytes = _utf8_bytes(producer, "producer")
    except ValueError as exc:
        raise _unreadable(f"{source_name}: {exc}") from exc
    if not (1 <= len(producer_bytes) <= MAX_PRODUCER_BYTES):
        raise _unreadable(
            f"{source_name}: 'producer' must be 1..{MAX_PRODUCER_BYTES} UTF-8 "
            f"bytes, got {len(producer_bytes)}"
        )

    attested_commit = payload["attested_commit"]
    if not isinstance(attested_commit, str) or not _OID_RE.fullmatch(attested_commit):
        raise _unreadable(
            f"{source_name}: 'attested_commit' must be exactly 40 lowercase hex "
            f"characters"
        )

    reviewed_paths = payload["reviewed_paths"]
    if not isinstance(reviewed_paths, list) or not (1 <= len(reviewed_paths) <= MAX_REVIEWED_PATHS):
        raise _unreadable(
            f"{source_name}: 'reviewed_paths' must be an array of "
            f"1..{MAX_REVIEWED_PATHS} strings"
        )
    validated_paths = [_validate_reviewed_path(item, source_name) for item in reviewed_paths]
    if len(set(validated_paths)) != len(validated_paths):
        raise _unreadable(f"{source_name}: 'reviewed_paths' contains a duplicate")

    try:
        return AttestationRecord(
            producer=producer,
            attested_commit=attested_commit,
            reviewed_paths=tuple(validated_paths),
        )
    except ValueError as exc:
        raise _unreadable(f"{source_name}: {exc}") from exc


def load_attestation_file(
    project_root: Path, *, attestation_dir: str, key: str
) -> AttestationRecord | None:
    """Read exactly ``<attestation_dir>/<key>.json`` once, through the
    descriptor-safe seam (P26/A-210).

    ``None`` when no producer supplied the file (any ``ENOENT`` in the walk,
    :func:`assay.safeio.read_bounded_input`'s own contract). A symlink,
    non-directory parent, non-regular final object, permission failure,
    oversized read, or invalid UTF-8/malformed JSON raises
    ``ERROR``/``UNREADABLE_ARTIFACT``.
    """
    relative_path = f"{attestation_dir}/{key}.json"
    raw = safeio.read_bounded_input(project_root, relative_path, limit=MAX_ATTESTATION_BYTES)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unreadable(f"{relative_path}: not valid UTF-8: {exc}") from exc
    return parse_attestation(text, source_name=relative_path)


# --- the git-dependent core: exact identity, ancestry, existence, currentness -


def evaluate_attestation(
    repo: Path,
    *,
    key: str,
    head: str,
    record: AttestationRecord | None,
    remaining: git.Remaining,
) -> Evidence:
    """The per-declaration judgement (A-211): exact commit identity, then
    ancestor-or-equal, then every reviewed path's existence, then every
    reviewed path's currentness, for one already-loaded attestation.

    *record* is ``None`` for a declared identity with no attestation
    produced at all — renders ``NO_MEASUREMENT``/``MISSING_ATTESTATION``
    with no payload (A-075).

    Otherwise every existence query runs before any staleness query, so a
    later missing path always outranks an earlier stale one. Never raises
    for a judged outcome: every Git-level failure this function's own checks
    can produce is caught and rendered ``ERROR``/``UNREADABLE_ARTIFACT`` —
    except the lane's own ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``, which
    propagates unmodified (A-213).
    """
    if record is None:
        return Evidence(
            source="attested",
            key=key,
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=False,
            reason_code=ReasonCode.MISSING_ATTESTATION,
        )

    try:
        git.verify_exact_commit(repo, record.attested_commit, remaining=remaining)
        if not git.is_ancestor(repo, record.attested_commit, head, remaining=remaining):
            raise AssayError(
                f"attested commit {record.attested_commit!r} is not {head!r} or "
                f"an ancestor of it",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.GIT_FAILED,
            )
        for path in record.reviewed_paths:
            kind = git.tree_entry_kind(repo, record.attested_commit, path, remaining=remaining)
            if kind is None:
                raise AssayError(
                    f"reviewed path {path!r} does not exist at attested commit "
                    f"{record.attested_commit!r}",
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.GIT_FAILED,
                )
        changed = any(
            not git.path_is_current(repo, record.attested_commit, head, path, remaining=remaining)
            for path in record.reviewed_paths
        )
    except AssayError as exc:
        if exc.reason_code is ReasonCode.LANE_TIMEOUT:
            raise
        return Evidence(
            source="attested",
            key=key,
            status=Outcome.ERROR,
            verified_by_assay=False,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )

    if changed:
        return Evidence(
            source="attested",
            key=key,
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=False,
            reason_code=ReasonCode.STALE_ATTESTATION,
            producer=record.producer,
            attested_commit=record.attested_commit,
            reviewed_paths=record.reviewed_paths,
        )
    return Evidence(
        source="attested",
        key=key,
        status=Outcome.PASS,
        verified_by_assay=False,
        producer=record.producer,
        attested_commit=record.attested_commit,
        reviewed_paths=record.reviewed_paths,
    )


# --- orchestration: closed grammar repeated at the public boundary (A-210) ---


def _validate_attestation_dir(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise _bad_lane_config(
            f"attestation_dir must be a non-empty string, got {value!r}"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _bad_lane_config(
            f"attestation_dir {value!r} cannot be encoded as UTF-8: {exc}"
        ) from exc
    if not (1 <= len(encoded) <= MAX_ATTESTATION_DIR_BYTES):
        raise _bad_lane_config(
            f"attestation_dir must be 1..{MAX_ATTESTATION_DIR_BYTES} UTF-8 bytes, "
            f"got {len(encoded)}"
        )
    if value.startswith("/"):
        raise _bad_lane_config(f"attestation_dir {value!r} must not be absolute")
    if any(ch in _ATTESTATION_DIR_CONTROL for ch in value):
        raise _bad_lane_config(f"attestation_dir {value!r} contains a control character")
    if PurePosixPath(value).as_posix() != value:
        raise _bad_lane_config(f"attestation_dir {value!r} is not canonical POSIX spelling")
    parts = value.split("/")
    if ".." in parts:
        raise _bad_lane_config(f"attestation_dir {value!r} contains a '..' component")
    if len(parts) > MAX_ATTESTATION_DIR_COMPONENTS:
        raise _bad_lane_config(
            f"attestation_dir {value!r} has more than {MAX_ATTESTATION_DIR_COMPONENTS} "
            f"components"
        )


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise _bad_lane_config(f"evidence key {key!r} does not match the closed grammar")


def _check_no_duplicate_declarations(declared: Sequence[EvidenceDeclaration]) -> None:
    identities = [item.identity for item in declared]
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise _bad_lane_config(
            f"duplicate declared evidence identit{'y' if len(duplicates) == 1 else 'ies'}: "
            f"{duplicates}"
        )


def load_attested_evidence(
    repo: Path,
    *,
    head: str,
    declared: Sequence[EvidenceDeclaration],
    project_root: Path,
    attestation_dir: str,
    remaining: git.Remaining,
) -> tuple[Evidence, ...]:
    """The entry point a caller (:mod:`assay.cli`, or this package's own
    tests) uses directly (A-210): resolve every declared identity's
    attestation, closed and bounded, before any Git call.

    Repeats the closed ``attestation_dir``/source/key/duplicate-declaration
    grammar at this public boundary and maps misuse to
    ``ERROR``/``BAD_LANE_CONFIG`` before safe I/O — this function does not
    assume every caller came through :mod:`assay.config`.

    All declarations are read/parsed first, in order. If the aggregate query
    cost (``2 * sum(len(record.reviewed_paths))`` across every structurally
    valid record) exceeds :data:`MAX_GIT_PATH_QUERIES`, every otherwise-valid
    record becomes ``ERROR``/``UNREADABLE_ARTIFACT`` and NO Git call is made
    for the batch; an identity already known missing or malformed keeps its
    own result. *remaining* is sampled at batch entry, after every bounded
    read/parse, and immediately before returning — including an all-missing
    batch — so expiry during bounded JSON work is observed as an atomic
    attestation timeout even when no valid record would otherwise launch Git.

    Returns exactly one :class:`~assay.verdict.Evidence` per entry in
    *declared*, in the same order.
    """
    remaining()

    _validate_attestation_dir(attestation_dir)
    _check_no_duplicate_declarations(declared)
    if len(declared) > MAX_EVIDENCE_DECLARATIONS:
        raise _bad_lane_config(
            f"{len(declared)} evidence declarations exceeds the "
            f"{MAX_EVIDENCE_DECLARATIONS} bound"
        )
    for item in declared:
        if item.source != "attested":
            raise _bad_lane_config(
                f"evidence declaration {item.identity} is not source='attested'; "
                f"this loader handles only attested evidence — adjudicated "
                f"evidence has no loader (A-085)"
            )
        _validate_key(item.key)

    staged: list[AttestationRecord | None | AssayError] = []
    for item in declared:
        try:
            record = load_attestation_file(
                project_root, attestation_dir=attestation_dir, key=item.key
            )
        except AssayError as exc:
            record = exc
        staged.append(record)
        remaining()

    valid_records = [item for item in staged if isinstance(item, AttestationRecord)]
    total_queries = _QUERIES_PER_REVIEWED_PATH * sum(
        len(record.reviewed_paths) for record in valid_records
    )
    if total_queries > MAX_GIT_PATH_QUERIES:
        results = tuple(
            _evidence_for_aggregate_excess(item.key, record)
            for item, record in zip(declared, staged)
        )
        remaining()
        return results

    results = []
    for item, record in zip(declared, staged):
        if isinstance(record, AssayError):
            results.append(
                Evidence(
                    source="attested",
                    key=item.key,
                    status=Outcome.ERROR,
                    verified_by_assay=False,
                    reason_code=ReasonCode.UNREADABLE_ARTIFACT,
                )
            )
        else:
            results.append(
                evaluate_attestation(repo, key=item.key, head=head, record=record, remaining=remaining)
            )
    remaining()
    return tuple(results)


def _evidence_for_aggregate_excess(
    key: str, record: AttestationRecord | None | AssayError
) -> Evidence:
    """One declared identity's result once the batch's aggregate query cost
    is known to exceed the bound (A-210): a structurally valid record
    becomes unreadable (it would have launched Git); an identity already
    known missing or malformed keeps its own result.
    """
    if record is None:
        return Evidence(
            source="attested",
            key=key,
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=False,
            reason_code=ReasonCode.MISSING_ATTESTATION,
        )
    return Evidence(
        source="attested",
        key=key,
        status=Outcome.ERROR,
        verified_by_assay=False,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
