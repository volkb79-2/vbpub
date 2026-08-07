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

Three layers, kept deliberately separate so each is independently testable:

* :func:`parse_attestation` / :func:`load_attestation_file` — pure format
  loading. No git involved; a malformed attestation FILE (bad JSON, missing
  or mistyped fields) raises :class:`~assay.errors.AssayError`
  (``ERROR``/``UNREADABLE_ARTIFACT``) here, independent of any commit's
  resolvability.
* :func:`evaluate_attestation` — the git-dependent core (A-110): proves
  equal-or-ancestor for the attested commit specifically, proves every
  declared reviewed path actually existed there, then diffs those paths
  against the commit under test. Never raises for a judged outcome — every
  git-level failure this function's own checks can produce (an unrelated
  history, a malformed/unresolvable ref, a descendant commit, a reviewed path
  that never existed at the attested commit) is caught and returned as an
  ``ERROR``/``UNREADABLE_ARTIFACT`` :class:`~assay.verdict.Evidence` value,
  never left to propagate as an uncaught exception.
* :func:`load_attested_evidence` — the orchestration entry point (A-111):
  accepts the list of declared ``(source, key)`` pairs to check as a DIRECT
  parameter (not sourced from ``assay.toml`` — `config.py` is outside this
  package's ``scope.touch`` and has no reserved field for this), validates
  duplicates within that list itself, and resolves each declared identity
  against a directory of ``<key>.json`` attestation files.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import git
from .errors import AssayError, Outcome, ReasonCode
from .verdict import Evidence, EvidenceDeclaration

__all__ = [
    "AttestationRecord",
    "evaluate_attestation",
    "load_attestation_file",
    "load_attested_evidence",
    "parse_attestation",
]


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


# --- format loading: pure, no git involved -----------------------------------


def parse_attestation(text: str, *, source_name: str) -> AttestationRecord:
    """Parse *text* (an attestation file's raw content) into an
    :class:`AttestationRecord`.

    *source_name* names *text*'s origin (typically a file path) for the error
    message only — this function never touches the filesystem itself, so
    :func:`load_attestation_file` and a test feeding in a literal string share
    this one parser.

    Raises :class:`~assay.errors.AssayError` (``ERROR``/``UNREADABLE_ARTIFACT``)
    on anything that is not a well-formed attestation: invalid JSON, a
    non-object top level, a missing required key, a ``reviewed_paths`` that
    is not a JSON array of strings, or a value :class:`AttestationRecord`
    itself refuses (empty producer, duplicate reviewed path, ...). This is
    ``UNREADABLE_ARTIFACT`` for the same reason a malformed coverage artifact
    is (P03): the file exists, but what it says cannot be trusted, which is a
    different fact from "no attestation was ever produced" (``MISSING_ATTESTATION``,
    :func:`evaluate_attestation`'s own concern for a file that is simply
    absent).
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssayError(
            f"{source_name}: not valid JSON ({exc})",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    if not isinstance(payload, dict):
        raise AssayError(
            f"{source_name}: attestation must be a JSON object, got "
            f"{type(payload).__name__}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    required = ("producer", "attested_commit", "reviewed_paths")
    missing = [key for key in required if key not in payload]
    if missing:
        raise AssayError(
            f"{source_name}: missing required key(s) {missing}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    reviewed_paths = payload["reviewed_paths"]
    if not isinstance(reviewed_paths, list) or not all(
        isinstance(item, str) for item in reviewed_paths
    ):
        raise AssayError(
            f"{source_name}: reviewed_paths must be a JSON array of strings, "
            f"got {reviewed_paths!r}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    try:
        return AttestationRecord(
            producer=payload["producer"],
            attested_commit=payload["attested_commit"],
            reviewed_paths=tuple(reviewed_paths),
        )
    except ValueError as exc:
        raise AssayError(
            f"{source_name}: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc


def load_attestation_file(path: Path) -> AttestationRecord:
    """Read and parse the attestation file at *path*.

    Raises :class:`~assay.errors.AssayError` (``ERROR``/``UNREADABLE_ARTIFACT``)
    if *path* cannot be read at all (permissions, or a race where it vanished
    between the caller's existence check and this read), or if
    :func:`parse_attestation` rejects its content. Whether *path* exists in
    the first place is the CALLER's concern (:func:`load_attested_evidence`
    treats non-existence as ``MISSING_ATTESTATION``, a judged outcome rather
    than a structural failure) — this function is only ever called once
    existence is already known.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssayError(
            f"{path}: could not be read ({exc})",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    return parse_attestation(text, source_name=str(path))


# --- the git-dependent core: ancestry, then path staleness (A-110) -----------


def _check_ancestor_or_equal(repo: Path, attested_commit: str, head: str) -> None:
    """Raise ``ERROR``/``UNREADABLE_ARTIFACT`` unless *attested_commit* is
    *head* itself or an ancestor of it.

    A-110's exact trap: ``git.run`` (P02) raises ``AssayError``/``GIT_FAILED``
    on ANY non-zero exit — verified directly against a real git binary:
    ``merge-base`` on unrelated orphan histories exits 1, and on a
    malformed/unresolvable ref exits 128. Both are caught here and remapped
    to ``UNREADABLE_ARTIFACT`` — this is the one call site in assay that
    deliberately does NOT let ``GIT_FAILED`` propagate, because *attested_commit*
    is externally supplied, untrusted input (an attestation file's own claim),
    unlike every other ref this codebase resolves, which comes from assay's
    own git plumbing (P02's trap note, superseded here for this call site
    only).

    A descendant attested commit (``merge-base`` SUCCEEDS but the result is
    not *attested_commit* — the true common ancestor is *head* itself,
    verified empirically) reaches the identical ``UNREADABLE_ARTIFACT`` via
    ordinary comparison below, no exception involved: an attestation cannot
    have reviewed a commit that comes after the one under test.
    """
    try:
        merge_base = git.run(repo, "merge-base", attested_commit, head).strip()
    except AssayError as exc:
        raise AssayError(
            f"attested commit {attested_commit!r} could not be checked "
            f"against {head!r}: {exc.message}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    if merge_base != attested_commit:
        raise AssayError(
            f"attested commit {attested_commit!r} is not {head!r} or an "
            f"ancestor of it (merge-base resolved to {merge_base!r}) — an "
            f"attestation must name the commit under test or an earlier one",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )


def _check_reviewed_paths_exist(
    repo: Path, attested_commit: str, reviewed_paths: tuple[str, ...]
) -> None:
    """Raise ``ERROR``/``UNREADABLE_ARTIFACT`` if any declared reviewed path
    did not exist at *attested_commit* — an attestation cannot have reviewed
    a file that was never in the tree it claims to review, so this is a
    broken record, not a stale one (which requires the path to have existed
    and then changed)."""
    for path in reviewed_paths:
        try:
            git.run(repo, "cat-file", "-e", f"{attested_commit}:{path}")
        except AssayError as exc:
            raise AssayError(
                f"reviewed path {path!r} does not exist at attested commit "
                f"{attested_commit!r}: {exc.message}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            ) from exc


def _changed_paths(repo: Path, attested_commit: str, head: str) -> frozenset[str]:
    """Repo-top-relative paths that differ between *attested_commit* and
    *head* — the same spelling :func:`assay.git.dirty_paths` already
    documents for ``git status``, restated for ``git diff --name-only``
    between two commits (equal commits diff to nothing, so an attestation
    naming ``head`` itself trivially has no changed paths)."""
    output = git.run(repo, "diff", "--name-only", attested_commit, head)
    return frozenset(line for line in output.splitlines() if line)


def evaluate_attestation(
    repo: Path, *, key: str, head: str, record: AttestationRecord | None
) -> Evidence:
    """The per-declaration judgement (O1/O2/O3): equal-or-ancestor, then
    path-scoped staleness, for one already-loaded attestation.

    *record* is ``None`` for a declared identity with no attestation
    produced at all — renders ``NO_MEASUREMENT``/``MISSING_ATTESTATION``
    with no payload (A-075).

    Otherwise: :func:`_check_ancestor_or_equal` and
    :func:`_check_reviewed_paths_exist` run first: whichever of A-110's four
    named failure shapes (unrelated, malformed, descendant attested commit,
    or a missing reviewed path) is present is CAUGHT here and rendered as
    ``ERROR``/``UNREADABLE_ARTIFACT`` — this function never lets that
    exception escape, so no attestation path can produce a raw
    ``GIT_FAILED``. Only then are the declared reviewed paths diffed against
    *head*: any that changed renders ``NO_MEASUREMENT``/``STALE_ATTESTATION``
    (A-075) with the full payload preserved (a consumer can see WHAT is
    stale, not just THAT it is); none changed renders ``PASS`` — "current" is
    the only ``PASS``-shaped outcome this module ever produces, and it is
    never :func:`~assay.verdict.rollup`'s job to invent one, only to see it.

    Never raises for a judged outcome: :func:`_check_ancestor_or_equal` and
    :func:`_check_reviewed_paths_exist` only ever raise ``UNREADABLE_ARTIFACT``
    by construction (both wrap every ``git.run`` failure and remap it before
    it can escape as anything else), and :func:`_changed_paths` runs only
    after both have already proven *attested_commit* resolves cleanly against
    *head* — so the single broad catch below is exhaustive, not a guess.
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
        _check_ancestor_or_equal(repo, record.attested_commit, head)
        _check_reviewed_paths_exist(repo, record.attested_commit, record.reviewed_paths)
        changed = _changed_paths(repo, record.attested_commit, head)
    except AssayError:
        return Evidence(
            source="attested",
            key=key,
            status=Outcome.ERROR,
            verified_by_assay=False,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )

    if any(path in changed for path in record.reviewed_paths):
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


# --- orchestration: the caller-supplied declared list (A-111) ---------------


def _check_no_duplicate_declarations(declared: Sequence[EvidenceDeclaration]) -> None:
    identities = [item.identity for item in declared]
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise AssayError(
            f"duplicate declared evidence identit{'y' if len(duplicates) == 1 else 'ies'}: "
            f"{duplicates}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )


def _resolve_one(
    repo: Path, *, head: str, key: str, attestations_dir: Path
) -> Evidence:
    path = attestations_dir / f"{key}.json"
    record: AttestationRecord | None
    if not path.is_file():
        record = None
    else:
        try:
            record = load_attestation_file(path)
        except AssayError:
            # load_attestation_file/parse_attestation only ever raise
            # UNREADABLE_ARTIFACT by construction -- every one of their raise
            # sites names it explicitly, so this catch is exhaustive.
            return Evidence(
                source="attested",
                key=key,
                status=Outcome.ERROR,
                verified_by_assay=False,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )
    return evaluate_attestation(repo, key=key, head=head, record=record)


def load_attested_evidence(
    repo: Path,
    *,
    head: str,
    declared: Sequence[EvidenceDeclaration],
    attestations_dir: Path,
) -> tuple[Evidence, ...]:
    """The entry point a caller (a future lane-file wiring, or this
    package's own tests) uses directly (A-111): *declared* is the list of
    ``(source, key)`` identities to check, supplied by the CALLER — never
    read from ``assay.toml``, which has no reserved field for this and stays
    untouched (``config.py`` is outside this package's ``scope.touch``). A
    real declaration mechanism is a later, separate wiring decision (the
    handoff's own words); this function's contract does not change when one
    arrives — only what feeds *declared* does.

    A duplicate ``(source, key)`` identity within *declared* itself is
    refused before any attestation is read: ``ERROR``/``BAD_LANE_CONFIG``.
    Every identity's ``source`` must be ``"attested"`` — this loader is
    Tier-3 only (A-085: the adjudicated sibling stays reserved, with no
    registry); a non-attested identity is refused the same way, since
    building any adjudicated behavior here would be exactly the "adjudicator
    registry" this package's ``escalate_if`` forbids.

    *attestations_dir* holds one JSON file per attested key, named
    ``<key>.json`` — :func:`load_attestation_file`'s own format. A missing
    file is ``NO_MEASUREMENT``/``MISSING_ATTESTATION``, judged, not an error;
    a present-but-malformed file, or a git-level failure specific to that
    key's own attested commit, is caught internally and rendered as a single
    ``ERROR``/``UNREADABLE_ARTIFACT`` evidence entry for THAT key alone —
    other keys in the same call are unaffected, so one broken attestation
    does not hide the state of the rest.

    Returns exactly one :class:`~assay.verdict.Evidence` per entry in
    *declared*, in the same order — the identity coverage
    :class:`~assay.verdict.Verdict` itself independently re-verifies.
    """
    _check_no_duplicate_declarations(declared)
    results = []
    for item in declared:
        if item.source != "attested":
            raise AssayError(
                f"evidence declaration {item.identity} is not source="
                f"'attested'; this loader handles only attested evidence — "
                f"adjudicated evidence has no loader (A-085)",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        results.append(
            _resolve_one(repo, head=head, key=item.key, attestations_dir=attestations_dir)
        )
    return tuple(results)
