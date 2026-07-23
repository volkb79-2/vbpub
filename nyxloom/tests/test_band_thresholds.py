"""Tests for band_thresholds.py — 100% diff-coverage required."""

from __future__ import annotations

import pytest

from nyxloom.band_thresholds import (
    AxisThresholds,
    DEFAULT_CUTOFFS,
    assign_band,
    assign_bands,
)


class TestAssignBand:
    """Tests for assign_band(score, cutoffs) -> int."""

    def test_none_score_returns_zero(self) -> None:
        """None score means unknown/unrated -> band 0."""
        assert assign_band(None, [0.5, 1.0]) == 0
        assert assign_band(None, []) == 0
        assert assign_band(None, [1.0, 2.0, 3.0]) == 0

    def test_empty_cutoffs_returns_band_one(self) -> None:
        """Empty cutoffs -> single band (band 1) for any rated score."""
        assert assign_band(0.0, []) == 1
        assert assign_band(-10.0, []) == 1
        assert assign_band(100.0, []) == 1
        assert assign_band(0.5, []) == 1

    def test_score_below_all_cutoffs_returns_band_one(self) -> None:
        """Score less than first cutoff -> band 1."""
        assert assign_band(0.2, [0.5, 1.0]) == 1
        assert assign_band(-5.0, [0.0, 1.0]) == 1
        assert assign_band(-0.1, [0.0, 1.0]) == 1  # strictly below first cutoff

    def test_score_exactly_on_cutoff_returns_higher_band(self) -> None:
        """Score exactly equal to a cutoff -> higher band (inclusive upper)."""
        assert assign_band(0.5, [0.5, 1.0]) == 2
        assert assign_band(1.0, [0.5, 1.0]) == 3
        assert assign_band(0.0, [0.0, 0.5]) == 2
        assert assign_band(-1.0, [-1.0, 0.0]) == 2

    def test_score_above_all_cutoffs_returns_top_band(self) -> None:
        """Score >= last cutoff -> top band (len(cutoffs) + 1)."""
        assert assign_band(1.5, [0.5, 1.0]) == 3
        assert assign_band(100.0, [0.5, 1.0]) == 3
        assert assign_band(0.5, [0.5]) == 2
        assert assign_band(0.75, [0.25, 0.5, 0.75]) == 4

    def test_score_in_middle_returns_correct_band(self) -> None:
        """Score between cutoffs -> correct middle band."""
        assert assign_band(0.75, [0.5, 1.0]) == 2
        assert assign_band(0.3, [0.25, 0.5, 0.75]) == 2
        assert assign_band(0.6, [0.25, 0.5, 0.75]) == 3

    def test_unsorted_cutoffs_sorted_defensively(self) -> None:
        """Unsorted cutoffs are sorted before use."""
        assert assign_band(0.75, [1.0, 0.5]) == 2
        assert assign_band(0.2, [1.0, 0.5]) == 1
        assert assign_band(1.5, [1.0, 0.5]) == 3
        assert assign_band(0.5, [1.0, 0.5, 0.25]) == 3  # sorted: [0.25, 0.5, 1.0], 0.5 -> band 3

    def test_negative_and_zero_scores(self) -> None:
        """Negative and zero scores handled normally by comparison."""
        assert assign_band(-1.0, [-0.5, 0.0]) == 1
        assert assign_band(-0.5, [-0.5, 0.0]) == 2
        assert assign_band(0.0, [-0.5, 0.0]) == 3
        assert assign_band(0.0, [0.0, 1.0]) == 2
        assert assign_band(0.5, [0.0, 1.0]) == 2

    def test_single_cutoff_two_bands(self) -> None:
        """One cutoff produces two bands (1 and 2)."""
        assert assign_band(0.4, [0.5]) == 1
        assert assign_band(0.5, [0.5]) == 2
        assert assign_band(0.6, [0.5]) == 2

    def test_many_cutoffs_many_bands(self) -> None:
        """N cutoffs produce N+1 bands."""
        cutoffs = [0.2, 0.4, 0.6, 0.8]
        assert assign_band(0.1, cutoffs) == 1
        assert assign_band(0.2, cutoffs) == 2
        assert assign_band(0.3, cutoffs) == 2
        assert assign_band(0.4, cutoffs) == 3
        assert assign_band(0.5, cutoffs) == 3
        assert assign_band(0.6, cutoffs) == 4
        assert assign_band(0.7, cutoffs) == 4
        assert assign_band(0.8, cutoffs) == 5
        assert assign_band(0.9, cutoffs) == 5


class TestAxisThresholds:
    """Tests for AxisThresholds dataclass and band_for method."""

    def test_known_axis_with_score(self) -> None:
        """Known axis with valid score returns correct band."""
        thresholds = AxisThresholds({"intelligence": [0.5, 1.0]})
        assert thresholds.band_for("intelligence", 0.75) == 2

    def test_known_axis_with_none_score_returns_zero(self) -> None:
        """Known axis with None score -> band 0 (unknown/unrated)."""
        thresholds = AxisThresholds({"intelligence": [0.5, 1.0]})
        assert thresholds.band_for("intelligence", None) == 0

    def test_unknown_axis_returns_zero(self) -> None:
        """Axis not in mapping -> band 0 (unknown/unrated)."""
        thresholds = AxisThresholds({"intelligence": [0.5, 1.0]})
        assert thresholds.band_for("coding", 0.8) == 0
        assert thresholds.band_for("agentic", None) == 0
        assert thresholds.band_for("nonexistent", 1.0) == 0

    def test_empty_cutoffs_for_axis_returns_band_one(self) -> None:
        """Axis with empty cutoffs list -> band 1 for any rated score."""
        thresholds = AxisThresholds({"intelligence": []})
        assert thresholds.band_for("intelligence", 0.0) == 1
        assert thresholds.band_for("intelligence", 100.0) == 1
        assert thresholds.band_for("intelligence", -10.0) == 1

    def test_frozen_dataclass(self) -> None:
        """AxisThresholds is frozen (immutable) - field reassignment raises."""
        thresholds = AxisThresholds({"intelligence": [0.5]})
        with pytest.raises(AttributeError):
            thresholds.cutoffs = {}  # type: ignore[misc]
        # Note: frozen prevents field reassignment, not mutation of the dict itself

    def test_multiple_axes_independent(self) -> None:
        """Each axis uses its own cutoffs independently."""
        thresholds = AxisThresholds({
            "intelligence": [0.33, 0.66],
            "coding": [0.5],
            "agentic": [0.25, 0.5, 0.75],
        })
        assert thresholds.band_for("intelligence", 0.5) == 2
        assert thresholds.band_for("coding", 0.5) == 2
        assert thresholds.band_for("agentic", 0.5) == 3


class TestAssignBands:
    """Tests for assign_bands(scores, thresholds) -> dict[str, int]."""

    def test_all_axes_known(self) -> None:
        """All axes in scores are present in thresholds."""
        thresholds = AxisThresholds({
            "intelligence": [0.5, 1.0],
            "coding": [0.5],
        })
        scores = {"intelligence": 0.75, "coding": 0.6}
        result = assign_bands(scores, thresholds)
        assert result == {"intelligence": 2, "coding": 2}

    def test_axis_in_scores_absent_from_thresholds_returns_zero(self) -> None:
        """Axis present in scores but absent from thresholds -> band 0."""
        thresholds = AxisThresholds({"intelligence": [0.5]})
        scores = {"intelligence": 0.6, "coding": 0.8}
        result = assign_bands(scores, thresholds)
        assert result == {"intelligence": 2, "coding": 0}

    def test_none_score_returns_zero(self) -> None:
        """None score for known axis -> band 0."""
        thresholds = AxisThresholds({"intelligence": [0.5]})
        scores = {"intelligence": None}
        result = assign_bands(scores, thresholds)
        assert result == {"intelligence": 0}

    def test_empty_scores_returns_empty_dict(self) -> None:
        """Empty scores dict -> empty result."""
        thresholds = AxisThresholds({"intelligence": [0.5]})
        result = assign_bands({}, thresholds)
        assert result == {}

    def test_mixed_known_unknown_none(self) -> None:
        """Mix of known axes, unknown axes, and None scores."""
        thresholds = AxisThresholds({
            "intelligence": [0.33, 0.66],
            "coding": [0.5],
        })
        scores = {
            "intelligence": 0.5,
            "coding": None,
            "agentic": 0.8,
            "reasoning": 0.9,
        }
        result = assign_bands(scores, thresholds)
        assert result == {
            "intelligence": 2,
            "coding": 0,
            "agentic": 0,
            "reasoning": 0,
        }

    def test_does_not_mutate_inputs(self) -> None:
        """Function does not mutate scores or thresholds."""
        thresholds = AxisThresholds({"intelligence": [0.5]})
        scores = {"intelligence": 0.6}
        original_scores = dict(scores)
        original_cutoffs = dict(thresholds.cutoffs)

        assign_bands(scores, thresholds)

        assert scores == original_scores
        assert thresholds.cutoffs == original_cutoffs


class TestDefaultCutoffs:
    """Tests for DEFAULT_CUTOFFS constant."""

    def test_exists_and_is_dict(self) -> None:
        """DEFAULT_CUTOFFS is a dict with expected axes."""
        assert isinstance(DEFAULT_CUTOFFS, dict)
        assert "intelligence" in DEFAULT_CUTOFFS
        assert "coding" in DEFAULT_CUTOFFS
        assert "agentic" in DEFAULT_CUTOFFS

    def test_each_axis_has_two_cutoffs(self) -> None:
        """Each axis has exactly two cutoffs (three bands)."""
        for axis, cutoffs in DEFAULT_CUTOFFS.items():
            assert isinstance(cutoffs, list)
            assert len(cutoffs) == 2
            assert cutoffs[0] < cutoffs[1]  # ascending
            assert all(isinstance(c, float) for c in cutoffs)

    def test_default_cutoffs_usable_with_axisthresholds(self) -> None:
        """DEFAULT_CUTOFFS can construct a working AxisThresholds."""
        thresholds = AxisThresholds(DEFAULT_CUTOFFS)
        for axis in DEFAULT_CUTOFFS:
            assert thresholds.band_for(axis, 0.0) == 1
            assert thresholds.band_for(axis, 0.5) == 2
            assert thresholds.band_for(axis, 1.0) == 3

    def test_default_cutoffs_placeholder_values(self) -> None:
        """DEFAULT_CUTOFFS uses placeholder values (0.33, 0.66) as documented."""
        for axis, cutoffs in DEFAULT_CUTOFFS.items():
            assert cutoffs == [0.33, 0.66], f"axis {axis} has unexpected cutoffs: {cutoffs}"