"""The full real pipeline, end to end: :func:`assay.adjudication.
load_adjudicated_evidence` reading a real document from disk, and
:func:`assay.runner.assemble_verdict` -- proving the two B004/A-430 touches
(``adjudication.py`` and ``runner.py``) actually compose, mirroring
:mod:`tests.test_attestation_pipeline_integration`'s shape for the Tier-2
sibling.

**A-334 / carve F7:** the green (``verified-match``) witness is
`nyxloom-trove/carve-assets/W2/ciu-provenance-green-reference.json` -- a REAL
document ciu's own producer emitted (`ciu/tests/tests/
test_ciu_provenance_json.py`'s `test_verified_match`, over a mocked docker
seam; ciu's shipped decision logic and serializer produced every byte). It is
copied into this repository's own carve-assets, not read cross-repo at test
time. `overall` is pinned at `"mismatch"` on every host in this estate (§8.1
of the carve), so this is the ONLY green witness available; there is no live
deployed instance anywhere that can produce one. The mismatch witness
(`ciu-provenance-live-mismatch.json` / the ciu-7.10.1 counterpart) is a real
`mismatch` capture at BOTH accepted `schema_version` values (DA-R12), proving
the `{1, 2}` parser end to end against real bytes, not synthetic ones.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from conftest import PROJECT_ROOT, fixed_clock, make_lane

from assay import runner
from assay.adjudication import load_adjudicated_evidence
from assay.config import EvidenceConfig, JudgeConfig
from assay.errors import Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration

CARVE_ASSETS = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "W2"
GREEN_REFERENCE = CARVE_ASSETS / "ciu-provenance-green-reference.json"
LIVE_MISMATCH_SCHEMA_1 = CARVE_ASSETS / "ciu-provenance-live-mismatch.json"
LIVE_MISMATCH_SCHEMA_2 = CARVE_ASSETS / "ciu-provenance-live-mismatch-ciu-7.10.1.json"

#: The green reference's own `commit_under_test` (a real ciu 6.0.3 capture --
#: see the MANIFEST beside it). Extended to a syntactically legal 40-hex HEAD
#: for the tests that need one, matching carve F7(a)'s "pass `head` as
#: `1b369e23` + 32 hex" correction.
_GREEN_REFERENCE_COMMIT_PREFIX = "1b369e23"


def _remaining() -> float:
    return 60.0


def _r0_lane_declaring_provenance():
    judge = JudgeConfig(
        language=None,
        source_roots=None,
        source_root_paths=None,
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=None,
        canary=None,
        base=None,
        adjudication_dir="artifacts/adjudicated",
        evidence=(EvidenceConfig(source="adjudicated", key="image-provenance"),),
    )
    return make_lane(name="package", argv=("/bin/sh", "-c", "exit 0"), env={}, judge=judge)


def _run_lane_with_evidence(evidence, declared, *, commit: str, tmp_path: Path):
    lane = _r0_lane_declaring_provenance()
    clock = fixed_clock(
        datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 12, 0, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    return runner.assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=evidence,
        declared_evidence=declared,
    )


def test_a_real_ciu_verified_match_document_flows_through_to_an_overall_pass_verdict(
    tmp_path: Path,
):
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    (adjudication_dir / "image-provenance.json").write_bytes(GREEN_REFERENCE.read_bytes())
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)
    head = _GREEN_REFERENCE_COMMIT_PREFIX + "a" * 32

    evidence = load_adjudicated_evidence(
        tmp_path,
        head=head,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )

    verdict = _run_lane_with_evidence(evidence, declared, commit=head, tmp_path=tmp_path)

    assert verdict.outcome is Outcome.PASS
    assert verdict.evidence[0].status is Outcome.PASS
    assert verdict.evidence[0].verified_by_assay is False
    assert verdict.evidence[0].reason_code is None


def test_a_real_ciu_mismatch_document_flows_through_to_an_overall_no_measurement_verdict(
    tmp_path: Path,
):
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    (adjudication_dir / "image-provenance.json").write_bytes(
        LIVE_MISMATCH_SCHEMA_1.read_bytes()
    )
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)
    # This lane's own HEAD is unrelated to the captured document's commit --
    # `mismatch` renders PROVENANCE_UNVERIFIED regardless of what HEAD is.
    head = "f" * 40

    evidence = load_adjudicated_evidence(
        tmp_path,
        head=head,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )

    verdict = _run_lane_with_evidence(evidence, declared, commit=head, tmp_path=tmp_path)

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.PROVENANCE_UNVERIFIED


def test_the_ciu_7_10_1_schema_version_2_mismatch_document_is_accepted_the_same_way(
    tmp_path: Path,
):
    """DA-R12, proven against the actual re-captured bytes rather than a
    synthetic ``schema_version``: ciu 7.10.1 emits ``2``, and the adjudicator
    must accept it exactly as it accepts ``1``."""
    document = json.loads(LIVE_MISMATCH_SCHEMA_2.read_text())
    assert document["schema_version"] == 2, "fixture drifted; re-check the frozen asset"
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    (adjudication_dir / "image-provenance.json").write_bytes(
        LIVE_MISMATCH_SCHEMA_2.read_bytes()
    )
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)
    head = "f" * 40

    evidence = load_adjudicated_evidence(
        tmp_path,
        head=head,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )

    verdict = _run_lane_with_evidence(evidence, declared, commit=head, tmp_path=tmp_path)

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.PROVENANCE_UNVERIFIED


def test_no_document_at_all_flows_through_as_an_honest_no_measurement_never_a_stale_pass(
    tmp_path: Path,
):
    """Carve row 4 / F6's freshness discipline: an ABSENT document (the
    consumer's `rm -f` producer-failure shape) is `PROVENANCE_UNVERIFIED`,
    never silently skipped and never a stale PASS."""
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)
    head = "f" * 40

    evidence = load_adjudicated_evidence(
        tmp_path,
        head=head,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )

    verdict = _run_lane_with_evidence(evidence, declared, commit=head, tmp_path=tmp_path)

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.PROVENANCE_UNVERIFIED


def test_a_verified_match_document_for_a_different_commit_is_unverified_not_pass(
    tmp_path: Path,
):
    """Carve O4: staleness / wrong commit. The document is real and green,
    but this run's HEAD does not share its prefix."""
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    (adjudication_dir / "image-provenance.json").write_bytes(GREEN_REFERENCE.read_bytes())
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)
    head = "deadbeef" + "a" * 32
    assert not head.startswith(_GREEN_REFERENCE_COMMIT_PREFIX)

    evidence = load_adjudicated_evidence(
        tmp_path,
        head=head,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )

    verdict = _run_lane_with_evidence(evidence, declared, commit=head, tmp_path=tmp_path)

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.PROVENANCE_UNVERIFIED
