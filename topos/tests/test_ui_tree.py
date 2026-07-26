from __future__ import annotations

from rich.console import Console

from topos.config import ToposConfig
from topos.model import DockerMeta, Entity, EntityFrame, Frame, MetricValue
from topos.ui.tree import render_data_table_tree, render_tree_table


def _frame() -> Frame:
    def ef(key: str, name: str, parent: str | None, ram: int) -> EntityFrame:
        return EntityFrame(
            entity=Entity(
                key=key, kind="scope", parent=parent, tier="work",
                docker=DockerMeta(cid=key, full_id=f"sha256:{key}_deadbeef", name=name, image=f"{name.lower()}:latest"),
            ),
            metrics={"ram": MetricValue(ram, "exact"), "cpu_pct": MetricValue(ram, "exact")},
            network={"source_label": "fixture"},
        )

    return Frame(
        schema_version=1,
        ts=1.0,
        interval_s=1.0,
        host={},
        entities={
            "parent": ef("parent", "Zulu", None, 10),
            "child": ef("child", "Alpha", "parent", 90),
            "sibling": ef("sibling", "Bravo", None, 50),
        },
    )


def _text(table) -> str:
    console = Console(record=True, width=100)
    console.print(table)
    return console.export_text()


def test_rich_tree_orders_filters_preserves_ancestors_and_collapses() -> None:
    frame = _frame()
    config = ToposConfig()

    by_name = render_tree_table(
        frame, config, width=80, profile="minimal", sort_by="name", filter_text="",
        selected_key=None, collapsed_keys=set(),
    )
    assert by_name.row_keys == ("sibling", "parent", "child")
    rendered = _text(by_name.table)
    assert "▾ Zulu" in rendered
    assert "    ▾ Zulu" not in rendered  # depth-0: no tree-depth indent beyond Rich column padding

    by_ram = render_tree_table(
        frame, config, width=80, profile="minimal", sort_by="ram", filter_text="",
        selected_key=None, collapsed_keys={"parent"},
    )
    assert by_ram.row_keys == ("sibling", "parent")
    rendered = _text(by_ram.table)
    assert "▸ Zulu" in rendered and "Alpha" not in rendered

    filtered = render_tree_table(
        frame, config, width=80, profile="minimal", sort_by="name", filter_text="alpha",
        selected_key=None, collapsed_keys={"parent"},
    )
    assert filtered.row_keys == ("parent", "child")
    assert "▾ Zulu" in _text(filtered.table)


def test_rich_tree_selection_profile_metadata_and_empty_row() -> None:
    config = ToposConfig(columns={"profiles": {"custom": {"list": ["name", "ram", "ignored_metric"]}}})
    result = render_tree_table(
        _frame(), config, width=100, profile="custom", sort_by="name", filter_text="",
        selected_key="sibling", collapsed_keys=set(),
    )
    rendered = _text(result.table)
    assert result.row_keys == ("sibling", "parent", "child")
    assert result.title.startswith("TREE | profile=custom ignored=ignored_metric")
    assert rendered.count("> ") == 1
    assert "NAME" in rendered and "RAM" in rendered

    empty = Frame(schema_version=1, ts=1.0, interval_s=1.0, host={}, entities={})
    empty_result = render_tree_table(
        empty, ToposConfig(), width=80, profile="minimal", sort_by="name", filter_text="",
        selected_key=None, collapsed_keys=set(),
    )
    assert empty_result.row_keys == ()
    assert "no rows" in _text(empty_result.table)


def test_data_table_tree_has_public_columns_prefix_reverse_and_empty_sentinel() -> None:
    frame = _frame()
    columns, labels, keys, rows = render_data_table_tree(
        frame, ToposConfig(), width=80, profile="minimal", sort_by="ram", sort_reverse=False,
        filter_text="", collapsed_keys=set(),
    )
    assert columns == ("name", "ram", "rf_d_per_s", "psi_mem_full_avg10", "cpu_pct")
    assert labels[0] == "NAME"
    assert keys == ("parent", "child", "sibling")
    assert rows[0][0].plain.startswith("▾ Zulu")
    assert all("> " not in cell.plain for row in rows for cell in row)

    empty = Frame(schema_version=1, ts=1.0, interval_s=1.0, host={}, entities={})
    _, _, empty_keys, empty_rows = render_data_table_tree(
        empty, ToposConfig(), width=80, profile="minimal", sort_by="name", sort_reverse=None,
        filter_text="", collapsed_keys=set(),
    )
    assert empty_keys == ("__empty__",)
    assert empty_rows[0][0].plain == "no rows"


def test_tree_renderers_keep_rows_when_a_custom_profile_has_no_supported_columns() -> None:
    config = ToposConfig(columns={"profiles": {"empty": {"list": ["not_a_metric"]}}})

    rich_result = render_tree_table(
        _frame(), config, width=80, profile="empty", sort_by="name", filter_text="",
        selected_key=None, collapsed_keys=set(),
    )
    assert rich_result.row_keys == ("sibling", "parent", "child")

    columns, labels, keys, rows = render_data_table_tree(
        _frame(), config, width=80, profile="empty", sort_by="name", filter_text="",
        collapsed_keys=set(),
    )
    assert (columns, labels, keys, rows) == ((), (), ("sibling", "parent", "child"), [[], [], []])
