from __future__ import annotations

from topos.config import ToposConfig
from topos.model import Entity, EntityFrame, MetricValue
from topos.ui.table import format_metric_value, resolve_columns, resolve_profile


def test_format_metric_value_shows_unlimited_limits_as_max() -> None:
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"mem_max": MetricValue(None, "unlimited")},
    )

    assert format_metric_value("mem_max", entity_frame).plain == "max"


def test_damon_profile_uses_registry_backed_columns() -> None:
    assert resolve_columns(ToposConfig(), width=140, profile="damon") == (
        "name",
        "damon_mode",
        "damon_hot_pct",
        "damon_warm_pct",
        "damon_cold_pct",
        "damon_idle_pct",
        "damon_sample_age_s",
    )


def test_custom_profile_reports_unsupported_columns_gracefully() -> None:
    config = ToposConfig(columns={"profiles": {"forensics": {"list": ["name", "ram", "bogus_metric", "cpu_pct"]}}})

    layout = resolve_profile(config, width=140, profile="forensics")

    assert layout.columns == ("name", "ram", "cpu_pct")
    assert layout.ignored_columns == ("bogus_metric",)
