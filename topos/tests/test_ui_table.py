from __future__ import annotations

from rich.console import Console

from topos.config import ToposConfig
from topos.model import DockerMeta, Entity, EntityFrame, Frame, MetricValue
from topos.record.ring import HistoryRing
from topos.ui.table import (
    available_profiles,
    format_metric_value,
    metric_sort_value,
    render_container_table,
    resolve_columns,
    resolve_profile,
    snapshot_container_table,
)


def test_profiles_discover_and_normalize_configured_profiles() -> None:
    config = ToposConfig(
        default_column_profile="forensics",
        columns={"profiles": {"forensics": {"list": ["name", "ram"]}}},
    )

    assert "forensics" in available_profiles(config)
    assert resolve_profile(config, width=80, profile="forensics").columns == ("name", "ram")
    assert resolve_profile(config, width=80, profile="missing").name == "forensics"

    invalid_default = ToposConfig(
        default_column_profile="missing",
        columns={"profiles": {"forensics": {"list": ["name", "ram"]}}},
    )
    assert resolve_profile(invalid_default, width=80, profile="missing").name == "auto"


def test_malformed_custom_profile_falls_back_to_builtin_layout() -> None:
    config = ToposConfig(columns={"profiles": {"broken": {"list": "name,ram"}}})

    layout = resolve_profile(config, width=80, profile="broken")

    assert layout.name == "broken"
    assert layout.columns == resolve_profile(ToposConfig(), width=80, profile="triage").columns


def test_format_metric_value_covers_governance_damon_and_registry_units() -> None:
    entity_frame = EntityFrame(
        entity=Entity(key="demo.scope", kind="scope", parent=""),
        metrics={
            "ram": MetricValue(2048, "exact"),
            "io_r_bps": MetricValue(2048, "exact"),
            "psi_mem_full_avg10": MetricValue(12.34, "exact"),
            "ratio": MetricValue(1.25, "exact"),
            "damon_sample_age_s": MetricValue(2.5, "exact"),
            "cpu_quota_us": MetricValue(50000, "exact"),
            "damon_mode": MetricValue(2, "exact"),
            "unknown": MetricValue(None, "unavail_kernel"),
        },
        governance={"summary": {"origin": "raw_write", "severity": "drift"}},
    )

    assert format_metric_value("governance_origin", entity_frame).plain == "raw_write"
    assert format_metric_value("governance_drift", entity_frame).plain == "drift"
    no_drift = EntityFrame(
        entity=entity_frame.entity,
        metrics={},
        governance={"summary": {"origin": "docker_default", "drift": False, "severity": "none"}},
    )
    assert format_metric_value("governance_drift", no_drift).plain == "none"
    assert format_metric_value("damon_mode", entity_frame).plain == "paddr"
    assert format_metric_value("ram", entity_frame).plain == "2.0KiB"
    assert format_metric_value("io_r_bps", entity_frame).plain == "2.0KiB/s"
    assert format_metric_value("psi_mem_full_avg10", entity_frame).plain == "12.3%"
    assert format_metric_value("ratio", entity_frame).plain == "1.2x"
    assert format_metric_value("damon_sample_age_s", entity_frame).plain == "2.5s"
    assert format_metric_value("cpu_quota_us", entity_frame).plain == "50000"
    assert format_metric_value("unknown", entity_frame).plain == "-"
    assert format_metric_value("missing_metric", entity_frame).plain == "-"


def test_metric_sort_value_is_deterministic_for_public_sort_keys() -> None:
    def frame(name: str, tier: str | None, source: str | None, cpu: MetricValue | None, value: MetricValue) -> EntityFrame:
        return EntityFrame(
            entity=Entity(key=f"{name}.scope", kind="scope", parent="", tier=tier),
            metrics={"ordinary": value, **({"cpu_pct": cpu} if cpu is not None else {})},
            network={"source_label": source} if source is not None else None,
        )

    present = frame("Alpha", "gold", "host", MetricValue(4, "exact"), MetricValue("z", "exact"))
    missing = frame("Beta", None, None, None, MetricValue(None, "unavail_kernel"))

    assert metric_sort_value("name", present) == (0, "alpha.scope")
    assert metric_sort_value("tier", present) == (0, "gold")
    assert metric_sort_value("net_source", present) == (0, "host")
    assert metric_sort_value("ordinary", present) == (0, "z")
    assert metric_sort_value("ordinary", missing) == (1, 0.0)
    assert metric_sort_value("cpu_trend", present) == (0, 4.0)
    assert metric_sort_value("cpu_trend", missing) == (1, 0.0)


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


def test_snapshot_custom_profile_with_only_unsupported_columns_keeps_entity_key() -> None:
    config = ToposConfig(
        columns={"profiles": {"unsupported": {"list": ["bogus_metric", "also_bogus"]}}}
    )

    snapshot = snapshot_container_table(
        _container_frame(), config, width=140, profile="unsupported", sort_by="name",
        filter_text="alpha", selected_key=None,
    )

    assert snapshot.columns == ()
    assert snapshot.row_keys == ("system.slice/alpha.scope",)
    assert snapshot.cells == ((),)


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


def test_public_table_edges_render_literal_unknowns_codes_and_ignored_title() -> None:
    entity_frame = EntityFrame(
        entity=Entity(key="system.slice/alpha.scope", kind="scope", parent="system.slice", docker=DockerMeta("k", "full", "alpha", "image")),
        metrics={
            "present_unknown": MetricValue("literal-value", "exact"),
            "governance_origin": MetricValue(99, "exact"),
            "governance_drift": MetricValue(None, "unavail_kernel"),
            "damon_mode": MetricValue(99, "exact"),
        },
    )
    frame = Frame(schema_version=1, ts=100.0, interval_s=5.0, host={}, entities={entity_frame.entity.key: entity_frame})
    config = ToposConfig(columns={"profiles": {"edge": {"list": ["name", "present_unknown", "bogus_metric"]}}})

    assert format_metric_value("present_unknown", entity_frame).plain == "literal-value"
    assert format_metric_value("governance_origin", entity_frame).plain == "99"
    assert format_metric_value("governance_drift", entity_frame).plain == "-"
    assert format_metric_value("damon_mode", entity_frame).plain == "99"
    rendered = render_container_table(
        frame, config, width=140, profile="edge", sort_by="name", filter_text="", selected_key=None,
    )
    assert "ignored=present_unknown,bogus_metric" in rendered.title
    text = _rendered_text(rendered.table)
    assert "ignored=present_unknown,bogus_metric" in text


def test_public_cpu_trend_is_dim_dash_for_existing_all_missing_history() -> None:
    entity_frame = EntityFrame(entity=Entity(key="demo.scope", kind="scope", parent=""), metrics={"cpu_pct": MetricValue(None, "unavail_kernel")})
    frame = Frame(schema_version=1, ts=100.0, interval_s=5.0, host={}, entities={entity_frame.entity.key: entity_frame})
    ring = HistoryRing(capacity=4, tracked_metrics=("cpu_pct",))
    ring.append_frame(frame)

    trend = format_metric_value("cpu_trend", entity_frame, ring=ring)

    assert trend.plain == "-"
    assert trend.style == "dim"


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
