from __future__ import annotations

from pathlib import Path

import pytest

from topos.config import (
    HistoryConfig,
    NetConfig,
    ThresholdBand,
    ToposConfig,
    load,
)


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
        # warn==0 path (line 29): when warn normalizes to 0 but crit > 0
        (ThresholdBand(0, 20), 10, 0.5),
        (ThresholdBand(0, 20), 25, 1.0),
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


def test_history_capacity_and_grace_handle_nonpositive_interval() -> None:
    h = HistoryConfig()
    assert h.capacity_for_interval(0) == 1
    assert h.capacity_for_interval(-5) == 1
    assert h.entity_grace_frames(0) == 0
    assert h.entity_grace_frames(-1) == 0


def test_net_classify_port_returns_none_for_unknown() -> None:
    assert NetConfig().classify_port(80) is None
    assert NetConfig(classes={"web": (80, 443)}).classify_port(22) is None


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


def test_load_handles_edge_case_port_and_weight_values(tmp_path: Path) -> None:
    path = tmp_path / "edge.toml"
    path.write_text(
        """
[net.classes]
# integer out of range (0, 65536) + non-int-non-str float
edge = [0, 65536, 3.14]

[thresholds.pressure_score]
weights = "not-a-dict"
""",
        encoding="utf-8",
    )
    config = load(path)
    # Out-of-range integers, float, and bool are all filtered out
    assert config.net.classes == {"edge": ()}
    # Non-dict weights falls back to defaults
    assert config.diagnostics.score_weights == {
        "psi_mem_full_avg10": 24.0,
        "psi_mem_some_avg10": 10.0,
        "psi_io_full_avg10": 16.0,
        "psi_io_some_avg10": 6.0,
        "psi_cpu_some_avg10": 4.0,
        "rf_d_per_s": 20.0,
        "rf_f_per_s": 10.0,
        "mem_events_high_per_s": 6.0,
        "mem_events_oom_kill_per_s": 4.0,
        "io_cap_saturation_pct": 0.0,
        "network_loss_pct": 0.0,
    }


# --- D-05 / D-04: one ConfigError, strict values, unknown-key warnings -------

from topos.config import ConfigError  # noqa: E402


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "text",
    [
        "[general\n",
        "[general]\ninterval = 0\n",
        "[general]\ninterval = -3\n",
        "[general]\ninterval = 0.05\n",
        "[general]\ninterval = nan\n",
        '[general]\ninterval = "abc"\n',
        '[general]\ndefault_view = "wide"\n',
        '[history.daemon]\ncompression = "gzip"\n',
        '[damon]\npaddr_enabled = "true"\n',
        "[history]\nfull_resolution_seconds = 0\n",
        "[history]\nfull_resolution_seconds = -5\n",
        "[history]\nentity_grace_seconds = -1\n",
        "[processes]\nhard_cap = 0\n",
        "[history.daemon]\nmax_age_days = -1\n",
    ],
)
def test_invalid_config_raises_config_error(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, text)
    with pytest.raises(ConfigError) as info:
        load(path, warn=lambda _line: None)
    assert info.value.path == path
    assert info.value.message


def test_interval_floor_is_accepted_exactly(tmp_path: Path) -> None:
    assert load(_write(tmp_path, "[general]\ninterval = 0.1\n")).interval == 0.1


def test_unknown_keys_warn_once_per_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "[general]\ninterval = 2\nintervall = 3\n[bogus]\nx = 1\n[history.daemon]\nenabeld = true\n")
    lines: list[str] = []
    cfg = load(path, warn=lines.append)
    assert cfg.interval == 2.0
    assert len(lines) == 3
    assert any("general.intervall" in line for line in lines)
    assert any("[bogus]" in line for line in lines)
    assert any("history.daemon.enabeld" in line for line in lines)


def test_unsupported_known_keys_warn_no_effect(tmp_path: Path) -> None:
    path = _write(tmp_path, '[hotkeys]\nprofile = "htop"\n[colors]\nwarn = "red"\n[history]\ndownsample_interval_seconds = 30\n')
    lines: list[str] = []
    load(path, warn=lines.append)
    joined = "\n".join(lines)
    assert "hotkeys is accepted but has no effect yet" in joined
    assert "colors is accepted but has no effect yet" in joined
    assert "history.downsample_interval_seconds is accepted but has no effect yet" in joined


def test_valid_config_emits_no_warnings(tmp_path: Path) -> None:
    lines: list[str] = []
    load(_write(tmp_path, '[general]\ninterval = 2\ndefault_view = "container"\n[damon]\npaddr_enabled = true\n'), warn=lines.append)
    assert lines == []


def test_missing_file_loads_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "absent.toml").interval == 5.0


def test_config_view_modes_match_ui_view_modes() -> None:
    from topos import config as config_mod
    from topos.ui.table import VIEW_MODES

    assert config_mod._VIEW_MODES == VIEW_MODES
