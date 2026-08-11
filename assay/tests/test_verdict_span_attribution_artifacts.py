"""O3 — full, schema-valid artifacts for attribution's three terminal
shapes: attributed PASS, attributed FAIL/UNCOVERED_LINES, and the wholly
new FAIL/UNCLASSIFIED_LINES (A-100).

``assay.runner`` was outside this package's original ``scope.touch`` (only
``adapters/base.py``, ``adapters/python.py``, ``evaluate.py``, ``verdict.py``,
``schemas/**`` and ``tests/**``), so these verdicts are assembled here exactly
the way ``runner.evaluate_r1`` assembles an R1 :class:`~assay.verdict.Claim`
from a :class:`~assay.evaluate.CoverageEvaluation`. A controller repair (see
the merge commit) wired the two new fields through ``runner.evaluate_r1``'s
own ``Coverage(...)`` call site after review found it silently dropped them;
``tests/test_runner_evaluate_r1.py`` proves that end to end.

The negative this defends (O3's own text): *a producer that omits
unclassified locations or rolls them up as PASS differs from its expected
artifact.* Each hand-written fixture under ``tests/fixtures/verdicts/`` was
authored independently of ``assay.verdict``/``assay.evaluate`` -- a renamed
key, a dropped field, or a laundered outcome shows up as an inequality here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import span_verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import CoverageEvaluation, evaluate_coverage
from assay.verdict import (
    Claim,
    Coverage,
    Judgment,
    JudgmentR1,
    JudgmentResolved,
    Verdict,
    rollup,
)

REPO_TOP = Path("/repo")
PATH = "pkg/mod.py"
VERSION = "0.1.0"

#: The effective R1 policy behind every real Coverage claim this module
#: builds (P16) -- identical across all three fixtures, since `_evaluate`
#: always calls `evaluate_coverage` with the same `fail_under`/
#: `allow_excluded`/adapter/source root.
R1_JUDGMENT = Judgment(
    resolved=JudgmentResolved(
        language="python",
        source_roots=("pkg",),
        base="0000000000000000000000000000000000000a",
    ),
    r1=JudgmentR1(
        coverage_format="coverage-py-json",
        coverage_artifact="cov.json",
        fail_under=100.0,
        allow_excluded=False,
    ),
)

SOURCE = (
    "def build_config():\n"  # 1
    "    return {\n"  # 2
    '        "a": 1,\n'  # 3
    '        "b": 2,\n'  # 4
    "    }\n"  # 5
)
SOURCE_EXCLUDED_ANCHOR = (
    "def build_config():\n"  # 1
    "    return {  # pragma: no cover\n"  # 2
    '        "a": 1,\n'  # 3
    '        "b": 2,\n'  # 4
    "    }\n"  # 5
)


def _evaluate(source: str, *, added_lines: set[int], executed, missing, excluded=frozenset()):
    adapter = PythonAdapter()
    added = AddedLines(by_file=MappingProxyType({PATH: frozenset(added_lines)}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                PATH: FileCoverage(
                    executed=frozenset(executed),
                    missing=frozenset(missing),
                    excluded=frozenset(excluded),
                )
            }
        )
    )

    def read_source_text(path: str) -> str:
        return source

    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )


def _r1_claim(result: CoverageEvaluation) -> Claim:
    """Mirrors ``assay.runner.evaluate_r1``'s own ``Claim``/``Coverage``
    construction exactly, PLUS wiring the two P07 fields through -- the one
    line ``runner.py`` itself is still missing (see this module's final
    test)."""
    return Claim(
        rigor="R1",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
        coverage=Coverage(
            covered=result.covered,
            changed_executable=result.changed_executable,
            pct=result.pct,
            considered=result.considered,
            exclusion_capability=result.exclusion_capability,
            missing_lines=result.missing_lines,
            files_missing_coverage=result.files_missing_coverage,
            unclassified_lines=result.unclassified_lines,
            files_with_unclassified_lines=result.files_with_unclassified_lines,
            excluded_lines=result.excluded_lines,
            files_with_excluded_lines=result.files_with_excluded_lines,
        ),
    )


def _verdict(commit: str, started: str, ended: str, r1_claim: Claim) -> Verdict:
    r0_claim = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)
    claims = (r0_claim, r1_claim)
    outcome = rollup(claim.status for claim in claims)
    reason_code = None
    if outcome is not Outcome.PASS:
        reason_code = next(claim.reason_code for claim in claims if claim.status is outcome)
    return Verdict(
        lane="package",
        commit=commit,
        outcome=outcome,
        reason_code=reason_code,
        started=started,
        ended=ended,
        assay_version=VERSION,
        declared_rigor=("R0", "R1"),
        declared_evidence=(),
        argv_declared=("/bin/sh", "-c", "exit 0"),
        argv_appended=(),
        argv_effective=("/bin/sh", "-c", "exit 0"),
        env_declared={},
        env_effective={},
        scope="S1",
        enforcement="gate",
        judgment=R1_JUDGMENT,
        claims=claims,
    )


def test_an_attributed_pass_matches_the_independent_artifact(
    validator: Draft202012Validator,
):
    result = _evaluate(SOURCE, added_lines={4}, executed={2}, missing=set())
    verdict = _verdict(
        "c" * 40,
        "2026-08-07T13:00:00+00:00",
        "2026-08-07T13:00:01+00:00",
        _r1_claim(result),
    )

    document = verdict.to_dict()
    assert document == span_verdict_fixture("r1_pass_span_attributed")
    assert why_invalid(validator, document) == []


def test_an_attributed_fail_uncovered_matches_the_independent_artifact(
    validator: Draft202012Validator,
):
    result = _evaluate(SOURCE, added_lines={2, 4}, executed=set(), missing={2})
    verdict = _verdict(
        "d" * 40,
        "2026-08-07T13:05:00+00:00",
        "2026-08-07T13:05:01+00:00",
        _r1_claim(result),
    )

    document = verdict.to_dict()
    assert document == span_verdict_fixture("r1_fail_uncovered_lines_span_attributed")
    assert why_invalid(validator, document) == []


def test_a_fail_unclassified_lines_matches_the_independent_artifact(
    validator: Draft202012Validator,
):
    result = _evaluate(
        SOURCE_EXCLUDED_ANCHOR, added_lines={4}, executed=set(), missing=set(), excluded={2}
    )
    verdict = _verdict(
        "e" * 40,
        "2026-08-07T13:10:00+00:00",
        "2026-08-07T13:10:01+00:00",
        _r1_claim(result),
    )

    document = verdict.to_dict()
    assert document == span_verdict_fixture("r1_fail_unclassified_lines")
    assert why_invalid(validator, document) == []


# --- vacuity guards: a producer that omits/rolls up unclassified must differ -


def test_omitting_unclassified_locations_differs_from_the_expected_artifact():
    """O3's negative, first half: 'a producer that omits unclassified
    locations ... differs from its expected artifact.'"""
    result = _evaluate(
        SOURCE_EXCLUDED_ANCHOR, added_lines={4}, executed=set(), missing=set(), excluded={2}
    )
    claim = Claim(
        rigor="R1",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
        coverage=Coverage(
            covered=result.covered,
            changed_executable=result.changed_executable,
            pct=result.pct,
            considered=result.considered,
            exclusion_capability=result.exclusion_capability,
            missing_lines=result.missing_lines,
            files_missing_coverage=result.files_missing_coverage,
            # unclassified_lines/files_with_unclassified_lines OMITTED --
            # the exact producer defect the negative names.
        ),
    )
    verdict = _verdict(
        "e" * 40, "2026-08-07T13:10:00+00:00", "2026-08-07T13:10:01+00:00", claim
    )

    assert verdict.to_dict() != span_verdict_fixture("r1_fail_unclassified_lines")


def test_rolling_up_unclassified_as_pass_differs_from_the_expected_artifact():
    """O3's negative, second half: 'or rolls them up as PASS differs from
    its expected artifact.' A hypothetical buggy producer computes the
    SAME real coverage payload (genuinely non-empty ``unclassified_lines``)
    but renders the claim/verdict ``PASS`` anyway -- a real, validly
    CONSTRUCTED ``Verdict`` (neither ``Claim`` nor ``Verdict`` can detect
    this contradiction on their own; only comparison against an independent
    artifact can)."""
    result = _evaluate(
        SOURCE_EXCLUDED_ANCHOR, added_lines={4}, executed=set(), missing=set(), excluded={2}
    )
    assert result.unclassified_lines  # the fixture genuinely has one

    buggy_r1_claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.PASS,  # the bug: rolled up as PASS despite the payload
        verified_by_assay=True,
        coverage=Coverage(
            covered=result.covered,
            changed_executable=result.changed_executable,
            pct=result.pct,
            considered=result.considered,
            exclusion_capability=result.exclusion_capability,
            missing_lines=result.missing_lines,
            files_missing_coverage=result.files_missing_coverage,
            unclassified_lines=result.unclassified_lines,
            files_with_unclassified_lines=result.files_with_unclassified_lines,
        ),
    )
    r0_claim = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)
    buggy_verdict = Verdict(
        lane="package",
        commit="e" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T13:10:00+00:00",
        ended="2026-08-07T13:10:01+00:00",
        assay_version=VERSION,
        declared_rigor=("R0", "R1"),
        declared_evidence=(),
        argv_declared=("/bin/sh", "-c", "exit 0"),
        argv_appended=(),
        argv_effective=("/bin/sh", "-c", "exit 0"),
        env_declared={},
        env_effective={},
        scope="S1",
        enforcement="gate",
        judgment=R1_JUDGMENT,
        claims=(r0_claim, buggy_r1_claim),
    )

    assert buggy_verdict.to_dict() != span_verdict_fixture("r1_fail_unclassified_lines")
