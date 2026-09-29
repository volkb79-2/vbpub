"""O2 — ``judge.coverage.format`` is cross-checked against the parser
registry's own keys AT CONFIG-LOAD TIME (A-068), distinct from the two
parse-time failures :mod:`assay.coverage` itself owns (``UNREADABLE_ARTIFACT``
for a malformed record, ``FORMAT_MISMATCH`` for a declared/sniffed
signature mismatch — see ``tests/test_coverage_registry.py`` and the
per-format parser test modules).

Negative: accepting any non-empty string (P01's original debt, A-068) lets a
lane declare a format nothing can parse and fail only much later, at run
time, instead of loudly at load time.
"""

from __future__ import annotations

import pytest
from conftest import R1_LANE, Project, set_key

from assay.config import load_lane_file
from assay.coverage import FORMAT_REGISTRY
from assay.errors import LaneConfigError, Outcome, ReasonCode
from assay.vocabulary import (
    COVERAGE_PRODUCERS_BY_FORMAT,
    COVERAGE_PRODUCER_REQUIRED_FORMATS,
    REFUSED_COVERAGE_PRODUCERS,
)


def test_unregistered_coverage_format_is_rejected_at_load_time(project: Project):
    path = project.write(
        set_key(R1_LANE, "coverage", '{ format = "not-a-real-format", artifact = "cov.json" }')
    )
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not-a-real-format" in str(excinfo.value)


@pytest.mark.parametrize("fmt", sorted(FORMAT_REGISTRY))
def test_every_registered_format_loads_at_config_time(fmt: str, project: Project):
    # (B045) A format whose producers DISAGREE requires `producer`, so the
    # minimal loadable table for such a format is a three-key one. This test
    # derives that from the shipped vocabulary rather than hardcoding which
    # format needs it, so opening a second required format cannot leave a
    # stale literal here.
    producer = ""
    if fmt in COVERAGE_PRODUCER_REQUIRED_FORMATS:
        allowed = [
            name
            for name in COVERAGE_PRODUCERS_BY_FORMAT[fmt]
            if name not in REFUSED_COVERAGE_PRODUCERS
        ]
        producer = f', producer = "{allowed[0]}"'
    path = project.write(
        set_key(
            R1_LANE,
            "coverage",
            f'{{ format = "{fmt}", artifact = "cov.out"{producer} }}',
        )
    )
    judge = load_lane_file(path).lane("package").judge
    assert judge is not None
    assert judge.coverage is not None
    assert judge.coverage.format == fmt
