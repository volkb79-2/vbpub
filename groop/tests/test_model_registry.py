from __future__ import annotations

import pytest

from groop.model import Entity, EntityFrame, Frame, MetricValue, frame_from_jsonable, frame_to_jsonable
from groop.registry import REGISTRY


def test_registry_names_are_canonical() -> None:
    assert REGISTRY
    assert all(name == spec.name for name, spec in REGISTRY.items())
    assert all(name == name.lower() and "-" not in name for name in REGISTRY)


def test_frame_round_trip_uses_compact_metric_values() -> None:
    frame = Frame(
        1,
        10.0,
        5.0,
        {"host_load1": MetricValue(0.1, "host")},
        {
            "x.slice": EntityFrame(
                Entity("x.slice", "slice", ""),
                {
                    "ram": MetricValue(123, "exact", raw=123),
                    "effective_memory_min": MetricValue(64, "derived"),
                    "governance_origin": MetricValue(2, "derived"),
                    "governance_drift": MetricValue(0, "derived"),
                },
                governance={"summary": {"origin": "systemd_unit", "drift": False, "severity": "none"}},
            )
        },
    )
    jsonable = frame_to_jsonable(frame)
    assert jsonable["host"]["host_load1"] == [0.1, "host"]
    assert jsonable["entities"]["x.slice"]["metrics"]["ram"] == [123, "exact", 123]
    assert jsonable["entities"]["x.slice"]["governance"]["summary"]["origin"] == "systemd_unit"
    assert frame_from_jsonable(jsonable) == frame


def test_registry_enforcement_rejects_unknown_metric() -> None:
    frame = Frame(1, 10.0, 5.0, {"not_registered": MetricValue(1, "host")}, {})
    with pytest.raises(ValueError, match="absent from registry"):
        frame_to_jsonable(frame)
