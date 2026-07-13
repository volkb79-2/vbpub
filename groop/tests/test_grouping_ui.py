"""Tests for CIU grouping rendering helpers.

These tests assert on the rendered artifact (Text/string output) rather
than internal flags, per Oracle 4 (tier is visible).
"""

from __future__ import annotations

from rich.text import Text

from groop.grouping import CiuGroup, group_entities
from groop.model import CiuMeta, DockerMeta, Entity, EntityFrame, Frame, MetricValue
from groop.ui.table import _group_header_row, _phase_display, render_data_table_container_grouped


def _make_entity_frame(
    key: str,
    name: str,
    *,
    stack: str | None = None,
    phase_raw: str | None = None,
    phase: int | None = None,
    source: str = "label",
    has_ciu: bool = True,
    docker: bool = True,
) -> EntityFrame:
    """Build a minimal EntityFrame with optional CIU metadata."""
    ciu_meta = None
    if has_ciu:
        ciu_meta = CiuMeta(stack=stack, phase_raw=phase_raw, phase=phase, source=source)
    docker_meta = None
    if docker:
        docker_meta = DockerMeta(
            cid=key[-12:] if len(key) >= 12 else key,
            full_id=key,
            name=name,
            image="test:latest",
        )
    entity = Entity(key=key, kind="scope", parent="system.slice", docker=docker_meta, ciu=ciu_meta)
    return EntityFrame(entity=entity, metrics={"ram": MetricValue(42.0, "exact", raw=42)})


def _make_frame(entity_frames: list[EntityFrame]) -> Frame:
    return Frame(
        schema_version=1,
        ts=1000.0,
        interval_s=5.0,
        host={"host_load1": MetricValue(0.1, "host")},
        entities={ef.entity.key: ef for ef in entity_frames},
    )


# ---------------------------------------------------------------------------
# _phase_display
# ---------------------------------------------------------------------------


class TestPhaseDisplay:
    def test_valid_phase_shows_number(self) -> None:
        assert _phase_display(2, "phase_2") == "2"

    def test_unparseable_shows_raw(self) -> None:
        assert _phase_display(None, "phase_x") == "? (phase_x)"

    def test_absent_shows_dash(self) -> None:
        assert _phase_display(None, None) == "-"


# ---------------------------------------------------------------------------
# _group_header_row — Oracle 4: tier is visible in the rendered artifact
# ---------------------------------------------------------------------------


class TestGroupHeaderRow:
    """The group header row must render the source tier distinguishably.

    Oracle 4 requires asserting on the rendered artifact, not an internal flag.
    """

    def test_label_source_marker(self) -> None:
        """A label-confirmed group header shows '(label)'."""
        group = CiuGroup(stack="app/web", phase=2, phase_raw="phase_2", source="label")
        columns = ("name", "ram")
        cells = _group_header_row(columns, group)
        # First cell is the header Text
        header_text = cells[0]
        assert isinstance(header_text, Text)
        plain = header_text.plain
        assert "(label)" in plain, f"expected '(label)' in header, got {plain!r}"

    def test_inferred_source_marker(self) -> None:
        """An inferred group header shows '(inferred)'."""
        group = CiuGroup(stack="app/web", phase=2, phase_raw="phase_2", source="inferred")
        columns = ("name", "ram")
        cells = _group_header_row(columns, group)
        header_text = cells[0]
        assert isinstance(header_text, Text)
        plain = header_text.plain
        assert "(inferred)" in plain, f"expected '(inferred)' in header, got {plain!r}"

    def test_marker_is_different(self) -> None:
        """Label and inferred headers produce distinct text."""
        label_group = CiuGroup(stack="s", phase=1, phase_raw="phase_1", source="label")
        inf_group = CiuGroup(stack="s", phase=1, phase_raw="phase_1", source="inferred")
        label_text = _group_header_row(("name",), label_group)[0].plain
        inf_text = _group_header_row(("name",), inf_group)[0].plain
        assert label_text != inf_text
        assert "(label)" in label_text
        assert "(inferred)" in inf_text

    def test_group_header_shows_stack_and_phase(self) -> None:
        """Header includes stack name and phase number."""
        group = CiuGroup(stack="infra/redis-core", phase=3, phase_raw="phase_3", source="label")
        cells = _group_header_row(("name",), group)
        plain = cells[0].plain
        assert "infra/redis-core" in plain
        assert "phase 3" in plain

    def test_header_remaining_cells_are_empty(self) -> None:
        """All columns after the first are empty text in a header row."""
        group = CiuGroup(stack="s", phase=1, phase_raw="phase_1", source="label")
        cells = _group_header_row(("name", "ram", "cpu_pct"), group)
        assert isinstance(cells[0], Text)
        assert all(isinstance(c, Text) for c in cells[1:])
        # Second and third cells should be empty
        assert all(c.plain == "" for c in cells[1:])


# ---------------------------------------------------------------------------
# render_data_table_container_grouped — Oracle 3: ungrouped entities
# ---------------------------------------------------------------------------


class TestRenderGroupedNoCIU:
    """When no CIU entities exist, the grouped view shows all entities
    under the 'other containers' header — no entity lost."""

    def test_no_ciu_all_ungrouped(self) -> None:
        """A frame with zero CIU-managed containers shows all as ungrouped."""
        ef1 = _make_entity_frame("scope-a", "container-a", has_ciu=False)
        ef2 = _make_entity_frame("scope-b", "container-b", has_ciu=False)
        frame = _make_frame([ef1, ef2])

        from groop.config import GroopConfig
        config = GroopConfig()

        col_keys, col_labels, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )

        # Should have a group header + 2 entity rows
        assert len(row_keys) == 3, f"expected 3 row_keys (1 header + 2 entities), got {len(row_keys)}"
        assert row_keys[0] == "__ungrouped__"
        assert row_keys[1] == "scope-a"
        assert row_keys[2] == "scope-b"
        # Header text
        assert "other containers (no CIU)" in rows[0][0].plain


class TestRenderGroupedMixed:
    """Mixed CIU and non-CIU entities in the grouped view."""

    def test_ciu_groups_and_ungrouped(self) -> None:
        """CIU entities appear in their groups, non-CIU entities appear separately."""
        ef1 = _make_entity_frame("scope-a", "a", stack="app/web", phase_raw="phase_1", phase=1, source="label")
        ef2 = _make_entity_frame("scope-b", "b", has_ciu=False)
        frame = _make_frame([ef1, ef2])

        from groop.config import GroopConfig
        config = GroopConfig()

        col_keys, col_labels, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )

        assert len(row_keys) == 4, f"expected 4 row_keys, got {len(row_keys)}"
        # Group header + CIU entity, then ungrouped header + ungrouped entity
        assert row_keys[0].startswith("__group__")
        assert row_keys[1] == "scope-a"
        assert row_keys[2] == "__ungrouped__"
        assert row_keys[3] == "scope-b"

    def test_label_appears_in_group_header(self) -> None:
        """Group header for label-sourced entities shows '(label)'."""
        ef = _make_entity_frame("scope-a", "a", stack="s", phase_raw="phase_1", phase=1, source="label")
        frame = _make_frame([ef])

        from groop.config import GroopConfig
        config = GroopConfig()

        _, _, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )
        header_plain = rows[0][0].plain
        assert "(label)" in header_plain, f"expected (label) in header, got {header_plain!r}"

    def test_inferred_appears_in_group_header(self) -> None:
        """Group header for inferred-sourced entities shows '(inferred)'."""
        ef = _make_entity_frame("scope-a", "a", stack="s", phase_raw="phase_1", phase=1, source="inferred")
        frame = _make_frame([ef])

        from groop.config import GroopConfig
        config = GroopConfig()

        _, _, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )
        header_plain = rows[0][0].plain
        assert "(inferred)" in header_plain, f"expected (inferred) in header, got {header_plain!r}"
