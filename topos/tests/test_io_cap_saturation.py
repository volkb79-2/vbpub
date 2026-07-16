from __future__ import annotations

"""Tests for I/O cap saturation metric (P28)."""
from pathlib import Path

from topos.collect.cgroup import read_io_max_caps
from topos.config import ToposConfig
from topos.model import Entity, EntityFrame, MetricValue
from topos.ui.table import format_metric_value, header_label, resolve_columns


# ── io.max caps parser ────────────────────────────────────────────────────────


def test_read_io_max_caps_parses_finite(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    path.write_text("8:16 rbps=1048576 wbps=524288 riops=100 wiops=50\n")
    caps, src = read_io_max_caps(path)
    assert src == "exact"
    assert caps["rbps"] == 1_048_576
    assert caps["wbps"] == 524_288
    assert caps["riops"] == 100
    assert caps["wiops"] == 50


def test_read_io_max_caps_handles_max(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    path.write_text("8:16 rbps=max wbps=1048576 riops=max wiops=max\n")
    caps, src = read_io_max_caps(path)
    assert src == "exact"
    assert caps["rbps"] is None
    assert caps["wbps"] == 1_048_576
    assert caps["riops"] is None
    assert caps["wiops"] is None


def test_read_io_max_caps_sums_multiple_devices(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    path.write_text("8:16 rbps=1000 wbps=max\n8:17 rbps=2000 wbps=5000\n")
    caps, src = read_io_max_caps(path)
    assert caps["rbps"] == 3000  # 1000 + 2000
    assert caps["wbps"] == 5000  # None (from max) + 5000
    assert caps["riops"] is None
    assert caps["wiops"] is None


def test_read_io_max_caps_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    caps, src = read_io_max_caps(path)
    # On a nonexistent path, OSError is raised → unavail_kernel
    assert src in ("unavail_kernel", "unavail_perm")
    assert caps == {}


def test_read_io_max_caps_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    path.write_text("")
    caps, src = read_io_max_caps(path)
    assert src == "exact"
    assert all(v is None for v in caps.values())


def test_read_io_max_caps_ignores_malformed_tokens(tmp_path: Path) -> None:
    path = tmp_path / "io.max"
    path.write_text("8:16 rbps=bad wbps=2000 riops= wiops=max\n")
    caps, src = read_io_max_caps(path)
    assert src == "exact"
    assert caps["rbps"] is None
    assert caps["wbps"] == 2000
    assert caps["riops"] is None
    assert caps["wiops"] is None


# ── Saturation derivation (direct unit test) ─────────────────────────────────


def test_io_cap_saturation_uses_highest_ratio() -> None:
    """Verify io_cap_saturation_pct is derived from the highest rate/cap ratio."""
    from topos.collect.collector import Collector

    config = ToposConfig()
    c = Collector(config=config, cgroup_root=Path("/dev/null"))

    # Simulate a sample with io rates and caps
    sample = _make_sample({
        "io.stat:rbytes": 100_000,
        "io.stat:wbytes": 200_000,
        "io.stat:rios": 50,
        "io.stat:wios": 25,
        "io.max:rbps": 200_000,   # ratio = 0.5
        "io.max:wbps": 200_000,   # ratio = 1.0 → highest
        "io.max:riops": 100,       # ratio = 0.5
        "io.max:wiops": 50,        # ratio = 0.5
    })

    # First call records prev counters
    key = "demo.scope"
    c._derived_rates(key, sample, interval_s=1.0)

    # Second call produces deltas
    sample2 = _make_sample({
        "io.stat:rbytes": 200_000,   # delta 100k
        "io.stat:wbytes": 400_000,   # delta 200k
        "io.stat:rios": 100,          # delta 50
        "io.stat:wios": 50,           # delta 25
        "io.max:rbps": 200_000,
        "io.max:wbps": 200_000,       # 200k/200k = 100%
        "io.max:riops": 100,
        "io.max:wiops": 50,
    })
    rates = c._derived_rates(key, sample2, interval_s=1.0)
    sat = rates.get("io_cap_saturation_pct")
    assert sat is not None
    assert sat.v is not None
    # The highest ratio is wbps: 200k / 200k = 1.0 → 100%
    assert sat.v == 100.0
    assert sat.src == "derived"


def test_io_cap_saturation_clamps_lower_bound() -> None:
    """Negative rates (possible on reset) are clamped to 0."""
    from topos.collect.collector import Collector

    config = ToposConfig()
    c = Collector(config=config, cgroup_root=Path("/dev/null"))

    sample = _make_sample({
        "io.stat:rbytes": 1_000_000,
        "io.max:rbps": 100_000,
    })
    c._derived_rates("e.k", sample, interval_s=1.0)

    # Second sample with lower counter (simulating reset)
    sample2 = _make_sample({
        "io.stat:rbytes": 500_000,  # delta would be negative after reset
        "io.max:rbps": 100_000,
    })
    rates = c._derived_rates("e.k", sample2, interval_s=1.0)
    sat = rates.get("io_cap_saturation_pct")
    # Rates with reset produce None v, so saturation should be unavailable
    assert sat is not None
    assert sat.v is None
    assert sat.src == "derived"


def test_io_cap_saturation_allows_overshoot() -> None:
    """Values above 100% are preserved (not clamped)."""
    from topos.collect.collector import Collector

    config = ToposConfig()
    c = Collector(config=config, cgroup_root=Path("/dev/null"))

    sample = _make_sample({
        "io.stat:wbytes": 0,
        "io.max:wbps": 100_000,
    })
    c._derived_rates("e.k", sample, interval_s=1.0)

    sample2 = _make_sample({
        "io.stat:wbytes": 300_000,  # delta 300k
        "io.max:wbps": 100_000,      # 300k/100k = 3.0 → 300%
    })
    rates = c._derived_rates("e.k", sample2, interval_s=1.0)
    sat = rates.get("io_cap_saturation_pct")
    assert sat is not None and sat.v is not None
    assert sat.v == 300.0


def test_io_cap_saturation_no_caps_is_none() -> None:
    """When io.max has no finite caps, saturation should be unavailable."""
    from topos.collect.collector import Collector

    config = ToposConfig()
    c = Collector(config=config, cgroup_root=Path("/dev/null"))

    sample = _make_sample({
        "io.stat:rbytes": 100_000,
        "io.stat:wbytes": 200_000,
        "io.max:_available": 1,  # io.max was readable; all caps are "max"
    })
    rates = c._derived_rates("e.k", sample, interval_s=1.0)
    sat = rates.get("io_cap_saturation_pct")
    assert sat is not None
    assert sat.v is None
    assert sat.src == "unlimited"


# ── Table display ────────────────────────────────────────────────────────────


def test_io_cap_saturation_header_label() -> None:
    """Table header for io_cap_saturation_pct is IO_CAP%."""
    label = header_label("io_cap_saturation_pct")
    assert "IO_CAP%" in label


def test_io_cap_saturation_format_value() -> None:
    """io_cap_saturation_pct formats as a percentage."""
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"io_cap_saturation_pct": MetricValue(85.0, "derived")},
    )
    result = format_metric_value("io_cap_saturation_pct", entity_frame)
    assert "85.0%" in result.plain


def test_io_cap_saturation_in_auto_profile_at_120_width() -> None:
    """io_cap_saturation_pct appears in auto profile at width >= 120."""
    columns = resolve_columns(ToposConfig(), width=120, profile="auto")
    assert "io_cap_saturation_pct" in columns


def test_io_cap_saturation_in_wide_profile() -> None:
    """io_cap_saturation_pct appears in wide profile."""
    columns = resolve_columns(ToposConfig(), width=200, profile="wide")
    assert "io_cap_saturation_pct" in columns


# ── Diagnostics integration ──────────────────────────────────────────────────


def test_io_cap_saturation_in_diagnostics_when_high() -> None:
    """Pressure breakdown includes a contribution when saturation exceeds band."""
    from topos.diag.score import score_entity

    entity_frame = EntityFrame(
        entity=Entity(key="io.scope", kind="scope", parent=""),
        metrics={"io_cap_saturation_pct": MetricValue(95.0, "derived")},
    )
    config = ToposConfig()
    breakdown = score_entity(entity_frame, config)
    contribs = [c for c in breakdown.contributions if c["key"] == "io_cap_saturation_pct"]
    assert len(contribs) == 1
    assert float(contribs[0]["value"]) == 95.0
    # At 95% with default band (75,95), normalized should be 1.0
    assert float(contribs[0]["normalized"]) >= 0.99


def test_io_cap_saturation_diagnostics_low() -> None:
    """Low saturation produces small contribution below warn threshold."""
    from topos.diag.score import score_entity

    entity_frame = EntityFrame(
        entity=Entity(key="io.scope", kind="scope", parent=""),
        metrics={"io_cap_saturation_pct": MetricValue(10.0, "derived")},
    )
    config = ToposConfig()
    breakdown = score_entity(entity_frame, config)
    contribs = [c for c in breakdown.contributions if c["key"] == "io_cap_saturation_pct"]
    assert len(contribs) == 1
    # At 10% with default band (75 warn), normalize returns (10/75)*0.5
    assert 0.0 < float(contribs[0]["normalized"]) < 0.1


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_sample(raw_counters: dict[str, int]):
    from topos.collect.cgroup import CgroupSample

    return CgroupSample(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        path=Path("/dev/null"),
        metrics={},
        raw_counters=raw_counters,
    )
