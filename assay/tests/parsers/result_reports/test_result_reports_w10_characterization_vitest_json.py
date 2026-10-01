"""B129 / W10 step 1: characterization of the strict-int guard in
``result_reports.vitest_json._count``.

Pins accept and refuse, with the exact message. Run against the UNCHANGED
source; never edited to follow the refactor.
"""

from __future__ import annotations

import pytest

from assay.result_reports import vitest_json
from assay.result_reports.model import ReportUnusable


@pytest.mark.parametrize("value", [0, 1, 7])
def test_count_accepts_a_strict_int(value):
    assert vitest_json._count({"numTotalTests": value}, "numTotalTests") == value


@pytest.mark.parametrize("value", [True, False, 1.5, "1", None, [1], {}])
def test_count_refuses_a_bool_or_non_int(value):
    with pytest.raises(ReportUnusable) as caught:
        vitest_json._count({"numTotalTests": value}, "numTotalTests")
    assert str(caught.value) == (
        f"vitest-json report's 'numTotalTests' is {type(value).__name__}, "
        f"not an integer"
    )


def test_count_refuses_an_absent_field():
    with pytest.raises(ReportUnusable) as caught:
        vitest_json._count({}, "numTotalTests")
    assert str(caught.value) == "vitest-json report has no 'numTotalTests' field"
