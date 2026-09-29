"""The declared mutation-report format is checked before parser dispatch."""

from __future__ import annotations

import pytest

from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.mutation_parsers import load_mutation_report


def test_an_unknown_report_format_is_a_lane_configuration_error():
    with pytest.raises(LaneConfigError, match="not a mutation-report format"):
        load_mutation_report("{}", declared_format="stryker-html")


def test_a_declared_report_with_the_wrong_signature_is_a_format_mismatch():
    with pytest.raises(AssayError) as caught:
        load_mutation_report("<mutation-report />", declared_format="mutation-report-json")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.FORMAT_MISMATCH
