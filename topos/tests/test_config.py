from __future__ import annotations

from pathlib import Path

import pytest

from topos.config import ThresholdBand, ToposConfig, load


@pytest.mark.parametrize(
    ("band", "value", "expected"),
    [
        (ThresholdBand(10, 20), None, 0.0),
        (ThresholdBand(10, 20), -1, 0.0),
        (ThresholdBand(0, -2), 3, 1.0),
        (ThresholdBand(20, 10), 10, 0.5),
        (ThresholdBand(10, 10), 5, 0.5),
        (ThresholdBand(10, 10), 10, 1.0),
        (ThresholdBand(10, 20), 5, 0.25),
        (ThresholdBand(10, 20), 10, 0.5),
        (ThresholdBand(10, 20), 15, 0.75),
        (ThresholdBand(10, 20), 25, 1.0),
    ],
)
def test_threshold_band_normalizes_public_score(band, value, expected) -> None:
    assert band.normalize(value) == pytest.approx(expected)


def test_threshold_band_uses_valid_tier_then_default() -> None:
    config = ToposConfig(
        thresholds={
            "default": {"cpu": {"warn": 4, "crit": 8}},
            "gold": {"cpu": {"warn": 2, "crit": 6}},
            "broken": {"cpu": "not-a-band"},
            "not-a-table": ["ignored"],
        }
    )
    assert config.threshold_band("cpu", tier="gold", warn=10, crit=20) == ThresholdBand(2.0, 6.0)
    assert config.threshold_band("cpu", tier="broken", warn=10, crit=20) == ThresholdBand(4.0, 8.0)
    assert config.threshold_band("cpu", tier="not-a-table", warn=10, crit=20) == ThresholdBand(4.0, 8.0)
    assert config.threshold_band("missing", tier="gold", warn=10, crit=20) == ThresholdBand(10, 20)


def test_load_normalizes_ports_weights_and_threshold_precedence(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[thresholds.default.cpu]
warn = 4
crit = 8
[thresholds.gold.cpu]
warn = 2
crit = 6
[thresholds.pressure_score.weights]
psi_mem_full_avg10 = "12.5"
new_metric = 3
bad_metric = "nope"

[tiers]
protected_services = ["ssh"]

[net.classes]
web = [443, " 80 ", "8002-8000", "bad", "0", "65536", "9-x"]
ignored = "not-a-list"
""",
        encoding="utf-8",
    )
    config = load(path)

    assert config.threshold_band("cpu", tier="gold", warn=10, crit=20) == ThresholdBand(2.0, 6.0)
    assert config.net.classes == {"web": (80, 443, 8000, 8001, 8002), "ignored": ()}
    assert config.diagnostics.score_weights["psi_mem_full_avg10"] == 12.5
    assert config.diagnostics.score_weights["new_metric"] == 3.0
    assert config.diagnostics.score_weights["bad_metric"] == 0.0


def test_load_missing_file_keeps_public_defaults(tmp_path: Path) -> None:
    config = load(tmp_path / "missing.toml")
    assert config == ToposConfig()
