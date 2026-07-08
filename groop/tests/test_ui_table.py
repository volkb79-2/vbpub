from __future__ import annotations

from groop.model import Entity, EntityFrame, MetricValue
from groop.ui.table import format_metric_value


def test_format_metric_value_shows_unlimited_limits_as_max() -> None:
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={"mem_max": MetricValue(None, "unlimited")},
    )

    assert format_metric_value("mem_max", entity_frame).plain == "max"
