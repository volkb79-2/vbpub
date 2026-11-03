"""IOB-R2-01 (group B): exact-threshold oracles for the plausibility checks."""
import dataclasses

import pytest

from iobench import plausibility as P
from iobench.cache import MARGIN


def test_a_check_is_immutable_and_equal_when_only_its_effects_differ():
    check = P.Check("IOB-PLA-X", "fail", "d", ("caps",), (("caps", "absent", "r"),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        check.status = "pass"
    assert check == P.Check("IOB-PLA-X", "fail", "d", ("caps",), ())  # the effects are the means of degrading, not part of the verdict


def test_the_largest_rung_exactly_at_the_cache_margin_passes():
    assert P.pla_02(int(MARGIN * 10), 10.0, False).status == "pass"
    assert P.pla_02(int(MARGIN * 10) - 1, 10.0, False).status == "fail"


def test_littles_law_holds_at_exactly_both_edges_of_the_band():
    assert P.pla_06(1000.0, 7000.0, 10).status == "pass"  # ratio 0.7
    assert P.pla_06(1000.0, 13000.0, 10).status == "pass"  # ratio 1.3
    assert P.pla_06(1000.0, 6999.0, 10).status == "fail"
    assert P.pla_06(1000.0, 13001.0, 10).status == "fail"


def test_a_fill_that_differs_from_the_sequential_rewrite_by_exactly_the_factor_passes():
    assert P.pla_09(5.0, 1.0, False).status == "pass"
    assert P.pla_09(1.0, 5.0, False).status == "pass"
    assert P.pla_09(5.1, 1.0, False).status == "fail"


def test_fresh_writes_exactly_one_and_a_half_times_faster_than_rewrites_pass():
    assert P.pla_13(150.0, 100.0, False).status == "pass"
    assert P.pla_13(150.1, 100.0, False).status == "fail"


def test_a_steady_value_exactly_three_times_off_the_history_median_is_within_the_band():
    assert P.pla_17(3.0, 1.0).status == "pass"
    assert P.pla_17(1.0, 3.0).status == "pass"
    assert P.pla_17(3.1, 1.0).status == "info"
