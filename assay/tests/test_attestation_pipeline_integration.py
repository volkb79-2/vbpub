"""The full real pipeline, end to end: a materialised git repo, a real
attestation file on disk, :func:`assay.attestation.load_attested_evidence`,
and :func:`assay.runner.assemble_verdict` -- proving the two P10 touches
(``attestation.py`` and ``runner.py``) actually compose, not just that each
passes its own isolated unit tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from conftest import fixed_clock, make_lane

from assay import runner
from assay.attestation import load_attested_evidence
from assay.errors import Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration


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


def test_a_current_attestation_flows_through_to_an_overall_pass_verdict(
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
    declared = (EvidenceDeclaration(source="attested", key="review"),)

    evidence = load_attested_evidence(
        git_repo.path, head=head, declared=declared, attestations_dir=attestations_dir
    )

    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit=head,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=evidence,
        declared_evidence=declared,
    )

    assert verdict.outcome is Outcome.PASS
    assert verdict.evidence[0].status is Outcome.PASS
    assert verdict.evidence[0].attested_commit == attested_commit


def test_a_stale_attestation_flows_through_to_an_overall_no_measurement_verdict(
    git_repo, tmp_path: Path
):
    git_repo.write("reviewed.py", "x = 1\n")
    attested_commit = git_repo.commit_all("add reviewed.py")
    git_repo.write("reviewed.py", "x = 2\n")
    head = git_repo.commit_all("change reviewed.py after the review")
    attestations_dir = tmp_path / "attestations"
    _write_attestation(
        attestations_dir,
        "review",
        attested_commit=attested_commit,
        reviewed_paths=("reviewed.py",),
    )
    declared = (EvidenceDeclaration(source="attested", key="review"),)

    evidence = load_attested_evidence(
        git_repo.path, head=head, declared=declared, attestations_dir=attestations_dir
    )

    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit=head,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=evidence,
        declared_evidence=declared,
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.STALE_ATTESTATION


def test_a_broken_attested_commit_flows_through_to_an_overall_error_verdict(
    git_repo, tmp_path: Path
):
    head = git_repo.head()
    attestations_dir = tmp_path / "attestations"
    _write_attestation(
        attestations_dir,
        "review",
        attested_commit="not-a-real-ref-anywhere-zzz",
        reviewed_paths=("README.md",),
    )
    declared = (EvidenceDeclaration(source="attested", key="review"),)

    evidence = load_attested_evidence(
        git_repo.path, head=head, declared=declared, attestations_dir=attestations_dir
    )

    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 10, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit=head,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=evidence,
        declared_evidence=declared,
    )

    # the whole verdict is ERROR even though R0 itself genuinely passed.
    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.claims[0].status is Outcome.PASS
