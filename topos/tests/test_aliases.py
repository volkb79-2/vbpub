from __future__ import annotations

"""Tests for the centralized alias layer (P27)."""

from topos.config import ToposConfig
from topos.model import Entity, EntityFrame, MetricValue
from topos.ui.aliases import BACKEND_AWARE_LABELS, is_alias, known_aliases, resolve_column
from topos.ui.table import (
    _column_supported,
    format_metric_value,
    header_label,
    metric_sort_value,
    resolve_columns,
    resolve_profile,
)


# ── Alias resolution ─────────────────────────────────────────────────────────


def test_resolve_column_known_aliases() -> None:
    assert resolve_column("swap_dev") == "swap_disk"
    assert resolve_column("rf_dev_per_s") == "rf_d_per_s"
    assert resolve_column("rf_dev") == "rf_d_per_s"
    assert resolve_column("rf_d") == "rf_d_per_s"


def test_resolve_column_passthrough_canonical() -> None:
    assert resolve_column("swap_disk") == "swap_disk"
    assert resolve_column("rf_d_per_s") == "rf_d_per_s"
    assert resolve_column("rf_z_per_s") == "rf_z_per_s"


def test_resolve_column_passthrough_unknown() -> None:
    assert resolve_column("bogus_metric") == "bogus_metric"
    assert resolve_column("ram") == "ram"


def test_is_alias() -> None:
    assert is_alias("swap_dev") is True
    assert is_alias("rf_dev_per_s") is True
    assert is_alias("rf_dev") is True
    assert is_alias("rf_d") is True
    assert is_alias("swap_disk") is False
    assert is_alias("rf_d_per_s") is False
    assert is_alias("ram") is False


def test_known_aliases() -> None:
    assert "swap_dev" in known_aliases("swap_disk")
    assert "rf_dev_per_s" in known_aliases("rf_d_per_s")
    assert "rf_dev" in known_aliases("rf_d_per_s")
    assert "rf_d" in known_aliases("rf_d_per_s")
    assert known_aliases("nonexistent") == ()


def test_backend_aware_labels() -> None:
    assert BACKEND_AWARE_LABELS["swap_disk"] == "SWAP_DEV"
    assert BACKEND_AWARE_LABELS["rf_d_per_s"] == "RF_DEV/S"


# ── Column support (canonical + aliases) ─────────────────────────────────────


def test_canonical_columns_are_supported() -> None:
    assert _column_supported("swap_disk") is True
    assert _column_supported("rf_d_per_s") is True


def test_alias_columns_are_supported() -> None:
    assert _column_supported("swap_dev") is True
    assert _column_supported("rf_dev_per_s") is True
    assert _column_supported("rf_dev") is True
    assert _column_supported("rf_d") is True


def test_unknown_column_not_supported() -> None:
    assert _column_supported("bogus_metric") is False


# ── Display labels ───────────────────────────────────────────────────────────


def test_header_label_backend_aware_swap() -> None:
    """swap_disk header shows SWAP_DEV (backend-aware)."""
    label = header_label("swap_disk")
    assert "SWAP_DEV" in label


def test_header_label_backend_aware_refault() -> None:
    """rf_d_per_s header shows RF_DEV/S (backend-aware)."""
    label = header_label("rf_d_per_s")
    assert "RF_DEV" in label


def test_header_label_aliases_show_same_label() -> None:
    """Alias names produce the same display label as canonical."""
    assert header_label("swap_dev") == header_label("swap_disk")
    assert header_label("rf_dev_per_s") == header_label("rf_d_per_s")
    assert header_label("rf_dev") == header_label("rf_d_per_s")
    assert header_label("rf_d") == header_label("rf_d_per_s")


def test_header_label_legacy_swap_disk_still_works() -> None:
    """Legacy canonical swap_disk still produces a valid non-empty header."""
    label = header_label("swap_disk")
    assert len(label) > 0
    assert "SWAP" in label


# ── Format / sort via alias ──────────────────────────────────────────────────


def test_format_metric_via_alias() -> None:
    """format_metric_value with an alias name reads the canonical metric."""
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"swap_disk": MetricValue(42_000_000, "derived")},
    )
    result = format_metric_value("swap_dev", entity_frame)
    assert "40.1" in result.plain  # 42MB → "40.1MiB"


def test_sort_value_via_alias() -> None:
    """metric_sort_value with an alias reads the canonical metric."""
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"rf_d_per_s": MetricValue(15.0, "derived")},
    )
    result = metric_sort_value("rf_dev_per_s", entity_frame)
    assert result == (0, 15.0)


# ── Custom profile with aliases ──────────────────────────────────────────────


def test_custom_profile_with_aliases_resolve() -> None:
    """A configured profile using alias names resolves to canonical columns."""
    config = ToposConfig(columns={"profiles": {"alice": {"list": ["name", "ram", "swap_dev", "rf_dev_per_s"]}}})
    layout = resolve_profile(config, width=140, profile="alice")

    assert "name" in layout.columns
    assert "ram" in layout.columns
    assert "swap_disk" in layout.columns
    assert "rf_d_per_s" in layout.columns
    assert "swap_dev" not in layout.columns
    assert "rf_dev_per_s" not in layout.columns
    # No ignored columns for alias names
    assert "swap_dev" not in layout.ignored_columns
    assert layout.ignored_columns == ()


def test_custom_profile_with_mixed_aliases_and_canonical_deduplicates() -> None:
    """Using both alias and canonical in same profile deduplicates to canonical."""
    config = ToposConfig(columns={"profiles": {"bob": {"list": ["name", "swap_dev", "swap_disk"]}}})
    layout = resolve_profile(config, width=140, profile="bob")
    assert layout.columns == ("name", "swap_disk")
    assert layout.ignored_columns == ()


# ── Diagnostic wording (score.py) ────────────────────────────────────────────


def test_score_rf_d_label_is_backend_aware() -> None:
    """The diagnostic score input for rf_d_per_s uses 'Device anon refaults'."""
    from topos.diag.score import _INPUTS  # noqa: PLC2701

    found = [i for i in _INPUTS if i.key == "rf_d_per_s"]
    assert len(found) == 1
    assert found[0].label == "Device anon refaults"
    assert "disk" not in found[0].label.lower()


def test_score_rf_d_detail_is_backend_aware() -> None:
    """The diagnostic detail for rf_d_per_s mentions zram/mixed hosts."""
    from topos.diag.score import _INPUTS  # noqa: PLC2701

    found = [i for i in _INPUTS if i.key == "rf_d_per_s"]
    assert len(found) == 1
    detail = found[0].detail.lower()
    # The word "disk" may appear in the backend-classification listing,
    # but the detail must acknowledge zram/mixed possibility.
    assert "zram" in detail or "mixed" in detail


# ── Finding wording (rules.py) ───────────────────────────────────────────────


def test_protected_disk_refault_message_is_backend_aware() -> None:
    """The protected disk refault finding no longer claims physical disk."""
    from topos.diag.rules import evaluate_rules

    entity_frame = EntityFrame(
        entity=Entity(key="srv.scope", kind="scope", parent="", is_protected=True),
        metrics={"rf_d_per_s": MetricValue(25.0, "derived")},
    )
    config = ToposConfig()
    findings = evaluate_rules(entity_frame, config)
    refault_findings = [f for f in findings if "refault" in f.message.lower()]
    assert len(refault_findings) >= 1
    for finding in refault_findings:
        assert "from disk" not in finding.message.lower()
        assert "touching real storage" not in finding.message.lower()
        assert "swap device" in finding.message.lower() or "refault" in finding.message.lower()
