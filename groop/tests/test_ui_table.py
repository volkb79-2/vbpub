from __future__ import annotations

from groop.config import GroopConfig
from groop.model import Entity, EntityFrame, MetricValue
from groop.ui.table import format_metric_value, resolve_columns


def test_format_metric_value_shows_unlimited_limits_as_max() -> None:
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"mem_max": MetricValue(None, "unlimited")},
    )

    assert format_metric_value("mem_max", entity_frame).plain == "max"


def test_damon_profile_uses_registry_backed_columns() -> None:
    assert resolve_columns(GroopConfig(), width=140, profile="damon") == (
        "name",
        "damon_mode",
        "damon_hot_pct",
        "damon_warm_pct",
        "damon_cold_pct",
        "damon_idle_pct",
        "damon_sample_age_s",
    )
