from __future__ import annotations

from rich.console import Console

from topos.config import ToposConfig
from topos.model import DockerMeta, Entity, EntityFrame, Frame, MetricValue
from topos.ui.table import (
    format_metric_value,
    render_container_table,
    resolve_columns,
    resolve_profile,
    snapshot_container_table,
)


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


def _container_frame() -> Frame:
    def entity_frame(key: str, name: str | None, ram: int) -> EntityFrame:
        return EntityFrame(
            entity=Entity(
                key=key,
                kind="scope",
                parent="system.slice",
                docker=None if name is None else DockerMeta(key, f"{key}-full", name, "example:latest"),
            ),
            metrics={"ram": MetricValue(ram, "exact"), "cpu_pct": MetricValue(5, "exact")},
        )

    return Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "system.slice/zulu.scope": entity_frame("system.slice/zulu.scope", "zulu", 2048),
            "system.slice/alpha.scope": entity_frame("system.slice/alpha.scope", "alpha", 1024),
            "system.slice/host.service": entity_frame("system.slice/host.service", None, 4096),
        },
    )


def _rendered_text(table) -> str:
    console = Console(record=True, width=140)
    console.print(table)
    return console.export_text()


def test_container_table_public_render_and_snapshot_filter_sort_and_selection() -> None:
    frame = _container_frame()
    kwargs = dict(width=140, profile="auto", sort_by="name", filter_text="", selected_key="system.slice/alpha.scope")

    rendered = render_container_table(frame, ToposConfig(), **kwargs)
    snapshot = snapshot_container_table(frame, ToposConfig(), **kwargs)
    unselected = snapshot_container_table(frame, ToposConfig(), **{**kwargs, "selected_key": None})
    table_text = _rendered_text(rendered.table)

    assert rendered.row_keys == ("system.slice/alpha.scope", "system.slice/zulu.scope")
    assert snapshot.row_keys == rendered.row_keys
    assert snapshot.cells != unselected.cells
    assert "alpha" in table_text and "zulu" in table_text and "host.service" not in table_text
    assert table_text.index("alpha") < table_text.index("zulu")
    assert all(cell in table_text for row in snapshot.cells for cell in row if cell)

    filtered = render_container_table(frame, ToposConfig(), **{**kwargs, "filter_text": "zulu", "selected_key": None})
    assert filtered.row_keys == ("system.slice/zulu.scope",)
    assert "zulu" in _rendered_text(filtered.table) and "alpha" not in _rendered_text(filtered.table)


def test_container_table_renders_no_container_rows_placeholder_after_filter() -> None:
    rendered = render_container_table(
        _container_frame(), ToposConfig(), width=140, profile="auto", sort_by="name",
        filter_text="no-such-container", selected_key=None,
    )
    snapshot = snapshot_container_table(
        _container_frame(), ToposConfig(), width=140, profile="auto", sort_by="name",
        filter_text="no-such-container", selected_key=None,
    )

    assert rendered.row_keys == () and snapshot.row_keys == () and snapshot.cells == ()
    assert "no container rows" in _rendered_text(rendered.table)
