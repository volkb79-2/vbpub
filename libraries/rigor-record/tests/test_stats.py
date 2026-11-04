import math

import pytest

from conftest import GOLDEN, golden
from rigor_record import stats
from rigor_record.stats import baseline, common_major, distribution, nearest_rank, normalise

VECTORS = golden("percentile-vectors.json")


def _values(case):
    if "range" in case:
        lo, hi = case["range"]
        # reversed, so a missing sort is caught
        return list(range(hi, lo - 1, -1))
    return case["values"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["id"])
def test_percentile_vectors(case):
    dist = distribution(_values(case), unit="s", source="assay-clock")
    assert {k: dist[k] for k in case["expect"]} == case["expect"]
    assert dist["unit"] == "s" and dist["source"] == "assay-clock"
    assert dist["n_excluded"] == {"contaminated": 0, "no_measurement": 0}


def test_percentile_value_is_always_a_measured_sample():
    sample = [1.0, 2.0, 100.0]
    dist = distribution(sample, unit="s", source="x")
    assert dist["p50"] in sample and dist["max"] == 100.0


def test_input_order_is_irrelevant():
    a = distribution([4, 1, 3, 2, 9, 7, 8, 5, 6, 10], unit="ops", source="s")
    b = distribution(list(range(1, 11)), unit="ops", source="s")
    assert a == b


def test_exclusions_are_reported_not_counted():
    dist = distribution([1, 2, 3], unit="s", source="s", contaminated=4, no_measurement=2)
    assert dist["n"] == 3 and dist["n_excluded"] == {"contaminated": 4, "no_measurement": 2}


@pytest.mark.parametrize("percent", [0, 101, -1, 50.0, True, "50"])
def test_nearest_rank_rejects_bad_percent(percent):
    with pytest.raises(ValueError, match="percent"):
        nearest_rank([1, 2, 3], percent)


def test_nearest_rank_rejects_empty_sample():
    with pytest.raises(ValueError, match="empty"):
        nearest_rank([], 50)


def test_nearest_rank_uses_integer_ranks():
    # 0.29 * 100 would be 28.999999999999996 in floating point; integer arithmetic gives rank 29 exactly
    assert nearest_rank(list(range(1, 101)), 29) == 29
    assert nearest_rank(list(range(1, 101)), 100) == 100
    assert nearest_rank(list(range(1, 101)), 1) == 1


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "1", None])
def test_distribution_rejects_non_finite_or_non_numbers(bad):
    with pytest.raises(ValueError):
        distribution([1, bad], unit="s", source="s")


def test_distribution_rejects_unknown_unit():
    with pytest.raises(ValueError, match="unit"):
        distribution([1], unit="seconds", source="s")


@pytest.mark.parametrize("kw", [{"contaminated": -1}, {"no_measurement": 1.5}, {"contaminated": True}])
def test_distribution_rejects_bad_exclusion_counts(kw):
    with pytest.raises(ValueError, match="non-negative"):
        distribution([1], unit="s", source="s", **kw)


def test_min_n_table():
    assert stats.MIN_N == {50: 1, 90: 10, 98: 50, 99: 100}


@pytest.mark.parametrize("item", VECTORS["normalisation"])
def test_normalisation_vectors(item):
    assert normalise(item["value"], item["single_core"], item["major"]) == item["expect"]


def test_baselines_per_major():
    assert stats.BASELINES == {6: 2500, 7: 2500}
    assert baseline(6) == 2500 and baseline(7) == 2500
    assert stats.DEFAULT_MAJOR == 7


@pytest.mark.parametrize("major", [5, 8, 0, None, True, "7"])
def test_unknown_major_is_refused(major):
    with pytest.raises(ValueError, match="major"):
        baseline(major)
    with pytest.raises(ValueError, match="major"):
        normalise(10, 2500, major)


def test_majors_are_never_mixed():
    assert common_major([7, 7, 7]) == 7
    assert common_major([6]) == 6
    with pytest.raises(ValueError, match="mixed"):
        common_major([6, 7])
    with pytest.raises(ValueError, match="mixed"):
        common_major([])
    with pytest.raises(ValueError, match="major"):
        common_major([9])


@pytest.mark.parametrize("score", [0, -5, 2500.0, True, None])
def test_normalise_rejects_bad_score(score):
    with pytest.raises(ValueError, match="single_core"):
        normalise(10, score, 7)


def test_normalise_rejects_bad_value():
    with pytest.raises(ValueError):
        normalise(math.nan, 2500, 7)
    with pytest.raises(ValueError):
        normalise("3", 2500, 7)


def test_golden_dir_exists():
    assert (GOLDEN / "percentile-vectors.json").is_file()
