from __future__ import annotations

from topos.record.ring import HistoryRing
from topos.ui.sparkline import render_sparkline, sparkline_from_history
from topos.model import Entity, EntityFrame, MetricValue
from topos.config import ToposConfig
from rich.text import Text


# ---------------------------------------------------------------------------
# render_sparkline - ASCII sparkline helper
# ---------------------------------------------------------------------------

def test_sparkline_empty() -> None:
    assert render_sparkline([], width=4) == "...."


def test_sparkline_zero_width() -> None:
    assert render_sparkline([1.0, 2.0], width=0) == ""


def test_sparkline_all_none() -> None:
    assert render_sparkline([None, None, None], width=6) == "......"


def test_sparkline_flat() -> None:
    """Flat series uses the middle character for all samples."""
    result = render_sparkline([5.0, 5.0, 5.0, 5.0], width=4)
    # Middle char of _CHARS (len=8) is "=" at index 4
    assert result == "===="


def test_sparkline_rising() -> None:
    """Rising series maps low to high characters."""
    result = render_sparkline([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], width=8)
    # 0% maps to "_" (index 0), 100% maps to "#" (index 7)
    assert result[0] == "_"
    assert result[-1] == "#"


def test_sparkline_falling() -> None:
    """Falling series maps high to low characters."""
    result = render_sparkline([100.0, 80.0, 60.0, 40.0, 20.0, 0.0], width=6)
    assert result[0] in ("%", "#")  # near 100%
    assert result[-1] in ("_", ",")  # near 0%


def test_sparkline_missing_values() -> None:
    """Missing values rendered as '.'."""
    result = render_sparkline([1.0, None, 3.0, None, 5.0], width=5)
    assert result[1] == "."
    assert result[3] == "."


def test_sparkline_downsample() -> None:
    """Long series is down-sampled to fit width."""
    long_series = list(range(100))  # 100 values, width=8
    result = render_sparkline(long_series, width=8)
    assert len(result) == 8
    # First char should be low, last high
    assert result[0] in ("_", ",")
    assert result[-1] in ("%", "#")


def test_sparkline_short_series() -> None:
    """Series shorter than width is not down-sampled."""
    result = render_sparkline([0.5, 1.5], width=8)
    assert len(result) == 8  # padded with missing chars
    assert result[0] != "."
    assert result[1] != "."


def test_sparkline_single_value() -> None:
    result = render_sparkline([42.0], width=6)
    assert len(result) == 6


# ---------------------------------------------------------------------------
# sparkline_from_history - convenience wrapper
# ---------------------------------------------------------------------------

def test_sparkline_from_history_empty() -> None:
    assert sparkline_from_history([]) == ""


def test_sparkline_from_history_all_none() -> None:
    assert sparkline_from_history([None, None]) == ""


def test_sparkline_from_history_returns_bracketed() -> None:
    result = sparkline_from_history([1.0, 2.0, 3.0], width=6)
    assert result.startswith(" [")
    assert result.endswith("]")


# ---------------------------------------------------------------------------
# _format_cpu_trend - table cell rendering with ring
# ---------------------------------------------------------------------------

def _make_ring_with_cpu(history: list[float | None]) -> HistoryRing:
    """Create a HistoryRing populated with one entity's cpu_pct data."""
    ring = HistoryRing(capacity=32)
    # Simulate frame appends to build history
    from topos.model import Frame
    for value in history:
        frame = Frame(
            schema_version=1,
            ts=0.0,
            interval_s=5.0,
            host={},
            entities={
                "test.slice": EntityFrame(
                    entity=Entity("test.slice", "slice", ""),
                    metrics={"cpu_pct": MetricValue(value, "exact") if value is not None else MetricValue(None, "unavail_kernel")},
                )
            },
        )
        ring.append_frame(frame)
    return ring
