"""Tests for CIU grouping rendering helpers.

These tests assert on the rendered artifact (Text/string output) rather
than internal flags, per Oracle 4 (tier is visible).
"""

from __future__ import annotations

from rich.text import Text

from topos.grouping import CiuGroup, group_entities
from topos.model import CiuMeta, DockerMeta, Entity, EntityFrame, Frame, MetricValue
from topos.ui.table import _group_header_row, _phase_display, render_data_table_container_grouped


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

        from topos.config import ToposConfig
        config = ToposConfig()

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

        from topos.config import ToposConfig
        config = ToposConfig()

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

        from topos.config import ToposConfig
        config = ToposConfig()

        _, _, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )
        header_plain = rows[0][0].plain
        assert "(label)" in header_plain, f"expected (label) in header, got {header_plain!r}"

    def test_inferred_appears_in_group_header(self) -> None:
        """Group header for inferred-sourced entities shows '(inferred)'."""
        ef = _make_entity_frame("scope-a", "a", stack="s", phase_raw="phase_1", phase=1, source="inferred")
        frame = _make_frame([ef])

        from topos.config import ToposConfig
        config = ToposConfig()

        _, _, row_keys, rows = render_data_table_container_grouped(
            frame, config, width=120, profile="triage", sort_by="name", filter_text=""
        )
        header_plain = rows[0][0].plain
        assert "(inferred)" in header_plain, f"expected (inferred) in header, got {header_plain!r}"


class TestOracle4TierVisibleInRenderedRows:
    """Oracle 4, verbatim: "A ``label``-sourced and an ``inferred``-sourced
    entity **in the same stack** are rendered distinguishably; assert on the
    rendered artifact, not on an internal flag."

    The original implementation promoted a mixed group's source to ``label``
    and marked the tier only on the group header, so the two entities rendered
    identically -- under a header that claimed ``(label)``. That hides the
    mis-attribution the inference heuristic can make, which is the whole reason
    the tiers are kept distinct.
    """

    def _render(self):
        from topos.config import ToposConfig

        lab = _make_entity_frame(
            "c-lab", "lab-01", stack="app/web", phase_raw="phase_1", phase=1, source="label"
        )
        inf = _make_entity_frame(
            "c-inf", "inf-01", stack="app/web", phase_raw="phase_1", phase=1, source="inferred"
        )
        frame = _make_frame([lab, inf])
        _, _, row_keys, rows = render_data_table_container_grouped(
            frame, ToposConfig(), width=120, profile="triage", sort_by="name", filter_text=""
        )
        return dict(zip(row_keys, rows))

    def test_the_two_entities_render_differently(self) -> None:
        by_key = self._render()
        label_row = by_key["c-lab"][0].plain
        inferred_row = by_key["c-inf"][0].plain
        assert label_row != inferred_row
        assert "(inferred)" in inferred_row
        assert "(inferred)" not in label_row

    def test_a_mixed_group_header_does_not_claim_label(self) -> None:
        by_key = self._render()
        header = by_key["__group__app/web__phase_1"][0].plain
        assert "(mixed)" in header
        assert "(label)" not in header


class TestGroupedViewHonoursSort:
    """``sort_by``/``sort_reverse`` were accepted and silently ignored, so the
    sort hotkey and header-click sorting were no-ops in this view while the
    status bar still reported a sort mode."""

    def _row_order(self, sort_by: str, reverse: bool | None = None) -> list[str]:
        from topos.config import ToposConfig

        frames = [
            _make_entity_frame("c-a", "alpha", stack="s", phase_raw="phase_1", phase=1),
            _make_entity_frame("c-b", "bravo", stack="s", phase_raw="phase_1", phase=1),
            _make_entity_frame("c-c", "charlie", stack="s", phase_raw="phase_1", phase=1),
        ]
        # Distinct ram values so a ram sort is observably different from a name sort.
        for ef, ram in zip(frames, (10.0, 30.0, 20.0)):
            ef.metrics["ram"] = MetricValue(ram, "exact", raw=int(ram))
        frame = _make_frame(frames)
        _, _, row_keys, _ = render_data_table_container_grouped(
            frame,
            ToposConfig(),
            width=120,
            profile="triage",
            sort_by=sort_by,
            sort_reverse=reverse,
            filter_text="",
        )
        return [k for k in row_keys if not k.startswith("__")]

    def test_sort_by_name_orders_entities_within_the_group(self) -> None:
        assert self._row_order("name") == ["c-a", "c-b", "c-c"]

    def test_sort_by_ram_reorders_entities_within_the_group(self) -> None:
        # ram desc by default: bravo(30) > charlie(20) > alpha(10)
        assert self._row_order("ram") == ["c-b", "c-c", "c-a"]

    def test_sort_reverse_is_honoured(self) -> None:
        assert self._row_order("ram", reverse=False) == ["c-a", "c-c", "c-b"]
