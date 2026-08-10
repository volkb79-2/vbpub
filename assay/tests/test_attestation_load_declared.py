"""O3/A-210 — ``load_attested_evidence``: the caller-supplied declared list,
duplicate rejection, and the closed ``attestation_dir``/``<key>.json`` file
convention that ties :func:`assay.attestation.evaluate_attestation` to a
real, materialised attestations directory end to end.

O3 (verbatim, this module's slice): *duplicate (source,key) in the
caller-supplied declaration list renders ERROR/BAD_LANE_CONFIG (A-210).*
Negative: *duplicate collapse ... makes one reject fixture load.*

P26 replaces the old caller-composed ``attestations_dir: Path`` parameter
with a project-relative ``attestation_dir: str`` resolved through the
descriptor-safe seam, and adds the required ``remaining`` lane-deadline
callable (A-212).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.attestation import load_attested_evidence
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration


def _remaining() -> float:
    return 60.0


def _write_attestation(directory: Path, key: str, *, attested_commit: str, reviewed_paths):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.json").write_text(
        json.dumps(
            {
                "producer": "adversarial-review-bot-v3",
                "attested_commit": attested_commit,
                "reviewed_paths": list(reviewed_paths),
            }
        ),
        encoding="utf-8",
    )


# --- the dedup guard (A-210) --------------------------------------------------


def test_a_duplicate_declared_identity_is_rejected_before_any_attestation_is_read(
    git_repo, tmp_path: Path
):
    head = git_repo.head()
    declared = (
        EvidenceDeclaration(source="attested", key="review"),
        EvidenceDeclaration(source="attested", key="review"),
    )

    with pytest.raises(AssayError) as excinfo:
        load_attested_evidence(
            git_repo.path,
            head=head,
            declared=declared,
            project_root=tmp_path,
            attestation_dir="attestations",
            remaining=_remaining,
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "review" in str(excinfo.value)


def test_distinct_keys_are_not_treated_as_duplicates(git_repo, tmp_path: Path):
    head = git_repo.head()
    declared = (
        EvidenceDeclaration(source="attested", key="review-a"),
        EvidenceDeclaration(source="attested", key="review-b"),
    )

    # Neither attestation file exists -- both resolve to MISSING_ATTESTATION,
    # proving the dedup guard did not (wrongly) reject two distinct keys.
    results = load_attested_evidence(
        git_repo.path,
        head=head,
        declared=declared,
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=_remaining,
    )

    assert [item.key for item in results] == ["review-a", "review-b"]
    assert all(item.reason_code is ReasonCode.MISSING_ATTESTATION for item in results)


def test_a_non_attested_declared_source_is_rejected(git_repo, tmp_path: Path):
    head = git_repo.head()
    declared = (EvidenceDeclaration(source="adjudicated", key="sast"),)

    with pytest.raises(AssayError) as excinfo:
        load_attested_evidence(
            git_repo.path,
            head=head,
            declared=declared,
            project_root=tmp_path,
            attestation_dir="attestations",
            remaining=_remaining,
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG


# --- end-to-end through the file convention ----------------------------------


def test_load_attested_evidence_resolves_a_current_attestation_from_a_real_file(
    git_repo, tmp_path: Path
):
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    head = attested_commit
    attestations_dir = tmp_path / "attestations"
    _write_attestation(
        attestations_dir,
        "review",
        attested_commit=attested_commit,
        reviewed_paths=("reviewed.py",),
    )

    results = load_attested_evidence(
        git_repo.path,
        head=head,
        declared=(EvidenceDeclaration(source="attested", key="review"),),
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=_remaining,
    )

    assert len(results) == 1
    assert results[0].status is Outcome.PASS
    assert results[0].key == "review"
    assert results[0].attested_commit == attested_commit


def test_load_attested_evidence_isolates_one_malformed_file_from_the_rest(
    git_repo, tmp_path: Path
):
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    head = attested_commit
    attestations_dir = tmp_path / "attestations"
    attestations_dir.mkdir()
    (attestations_dir / "broken.json").write_text("{ not json", encoding="utf-8")
    _write_attestation(
        attestations_dir,
        "good",
        attested_commit=attested_commit,
        reviewed_paths=("reviewed.py",),
    )

    results = load_attested_evidence(
        git_repo.path,
        head=head,
        declared=(
            EvidenceDeclaration(source="attested", key="broken"),
            EvidenceDeclaration(source="attested", key="good"),
        ),
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=_remaining,
    )

    by_key = {item.key: item for item in results}
    assert by_key["broken"].status is Outcome.ERROR
    assert by_key["broken"].reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert by_key["good"].status is Outcome.PASS


def test_load_attested_evidence_returns_results_in_declared_order(git_repo, tmp_path: Path):
    head = git_repo.head()
    declared = (
        EvidenceDeclaration(source="attested", key="z-review"),
        EvidenceDeclaration(source="attested", key="a-review"),
    )

    results = load_attested_evidence(
        git_repo.path,
        head=head,
        declared=declared,
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=_remaining,
    )

    assert [item.key for item in results] == ["z-review", "a-review"]


def test_the_lane_deadline_expiring_before_any_record_is_read_is_never_remapped(
    git_repo, tmp_path: Path
):
    """A-210's atomic-batch rule, pinned at the loader's own boundary: a
    ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` from *remaining* propagates
    unmodified, never becomes ``BAD_LANE_CONFIG`` or ``UNREADABLE_ARTIFACT``."""
    head = git_repo.head()
    sentinel = AssayError(
        "lane deadline expired",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def expired() -> float:
        raise sentinel

    with pytest.raises(AssayError) as excinfo:
        load_attested_evidence(
            git_repo.path,
            head=head,
            declared=(EvidenceDeclaration(source="attested", key="review"),),
            project_root=tmp_path,
            attestation_dir="attestations",
            remaining=expired,
        )
    assert excinfo.value is sentinel


# --- A-210: config.py is never touched or imported ---------------------------


def test_attestation_module_never_imports_config():
    import assay.attestation as attestation_module

    source = Path(attestation_module.__file__).read_text(encoding="utf-8")
    assert "from .config" not in source
    assert "from assay.config" not in source
    assert "import config" not in source
