"""P86 — drive the real ``ciu-grouped`` view through Textual pilot keypresses.

P83 added ``ciu-grouped`` as a third view mode reached via
``action_toggle_view`` (``F5``/``t``), but every P83 test called
``group_entities()`` or ``render_data_table_container_grouped()`` directly.
The grouping logic is well covered; the app-level *wiring* was never
exercised — no test pressed ``F5``, reached ``ciu-grouped``, and asserted the
app actually rendered it.

These tests build a real ``GroopApp`` over frames containing ciu-managed
entities, press keys through ``pilot``, and assert on the MOUNTED
``DataTable`` (via ``MouseTable.ordered_rows``/``get_cell``/``get_row``) —
never on a renderer's return value, which is what ``tests/test_grouping_ui.py``
already covers. See ``handoff/P86-ciu-grouped-view-end-to-end.md`` for the
six numbered acceptance oracles this file implements.

Do not touch the grouping logic, ``CiuMeta``, the collector, or the detection
heuristics (P83/P76 own those). Do not touch ``_wait_for_frame``'s
fixed-iteration loop (backlog B-002) — it is reproduced here verbatim from
``tests/test_ui_app.py``, the same way ``tests/test_grouping_ui.py``
duplicates its own local fixture helpers rather than importing across test
modules.
"""

from __future__ import annotations

import asyncio

from rich.text import Text

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig
from groop.model import CiuMeta, DockerMeta, Entity, EntityFrame, Frame, MetricValue
from groop.ui.app import GroopApp
from groop.ui.drill import DrillDownScreen


# ---------------------------------------------------------------------------
# Fixture builders — a minimal ciu-managed frame, parallel to
# tests/test_grouping_ui.py's ``_make_entity_frame``/``_make_frame``, but
# built for driving the real app rather than calling the renderer directly.
# ---------------------------------------------------------------------------


def _ciu_entity_frame(
    key: str,
    name: str,
    *,
    stack: str | None = None,
    phase_raw: str | None = None,
    phase: int | None = None,
    source: str = "label",
    has_ciu: bool = True,
    ram: float = 42.0,
    pressure: float | None = None,
) -> EntityFrame:
    """Build a minimal container ``EntityFrame`` with optional CIU metadata."""
    ciu_meta = CiuMeta(stack=stack, phase_raw=phase_raw, phase=phase, source=source) if has_ciu else None
    docker_meta = DockerMeta(cid=key[-12:] if len(key) >= 12 else key, full_id=key, name=name, image="test:latest")
    entity = Entity(key=key, kind="scope", parent=None, docker=docker_meta, ciu=ciu_meta)
    metrics: dict[str, MetricValue] = {"ram": MetricValue(ram, "exact", raw=int(ram))}
    if pressure is not None:
        metrics["pressure"] = MetricValue(pressure, "exact", raw=int(pressure))
    return EntityFrame(entity=entity, metrics=metrics)


def _frame(entity_frames: list[EntityFrame]) -> Frame:
    return Frame(
        schema_version=1,
        ts=1000.0,
        interval_s=5.0,
        host={"host_load1": MetricValue(0.1, "host")},
        entities={ef.entity.key: ef for ef in entity_frames},
    )


def _make_ciu_app(entity_frames: list[EntityFrame]) -> GroopApp:
    """A ``GroopApp`` over a single synthetic frame, ``triage`` profile so the
    column set (and therefore which column holds the ``name``/marker cell) is
    independent of terminal width."""
    return GroopApp(
        iter([_frame(entity_frames)]),
        config=GroopConfig(default_view="tree", default_column_profile="triage"),
    )


def _wait_for_frame(app: GroopApp):
    # Reproduced verbatim from tests/test_ui_app.py (backlog B-002; out of
    # scope for this package to change).
    async def wait(pilot) -> None:
        for _ in range(10):
            await pilot.pause()
            if app.frames_received:
                break

    return wait


def _cell_text(mt, row_key: str, column_key: str = "name") -> str:
    """Read a cell's plain text from the MOUNTED DataTable, not the renderer."""
    cell = mt.get_cell(row_key, column_key)
    return cell.plain if isinstance(cell, Text) else str(cell)


def _mounted_row_keys(mt) -> tuple[str, ...]:
    """Row keys in the MOUNTED DataTable's actual display order (``ordered_rows``),
    independent of ``app._visible_row_keys`` (the renderer's return value)."""
    return tuple(row.key.value for row in mt.ordered_rows)


# ---------------------------------------------------------------------------
# Oracle 1 — the cycle reaches the view, and something is actually rendered.
# ---------------------------------------------------------------------------


def test_pilot_oracle1_f5_cycle_reaches_ciu_grouped_with_rendered_header() -> None:
    """F5 x3 from ``tree`` returns to ``tree``; the intermediate states are
    ``container`` then ``ciu-grouped``, and ``ciu-grouped`` renders a group
    header row in the MOUNTED table — not just the ``view_mode`` string."""

    async def run() -> None:
        lab = _ciu_entity_frame("c-lab", "lab-01", stack="app/web", phase_raw="phase_2", phase=2, source="label")
        app = _make_ciu_app([lab])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            assert app.view_mode == "tree"

            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "container"

            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "ciu-grouped"
            mt = app.query_one("#body-table")
            mounted_keys = _mounted_row_keys(mt)
            group_headers = [k for k in mounted_keys if k.startswith("__group__")]
            assert group_headers, f"expected a rendered group header row, got {mounted_keys!r}"
            header_text = _cell_text(mt, group_headers[0])
            assert header_text.strip(), "group header row must have rendered, non-blank text"

            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "tree"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Oracle 2 — the group header shows stack, phase, and tier, read from the
# mounted table.
# ---------------------------------------------------------------------------


def test_pilot_oracle2_group_header_shows_stack_phase_and_tier() -> None:
    async def run() -> None:
        lab = _ciu_entity_frame(
            "c-lab", "lab-01", stack="infra/redis-core", phase_raw="phase_3", phase=3, source="label"
        )
        app = _make_ciu_app([lab])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "ciu-grouped"
            mt = app.query_one("#body-table")
            group_key = "__group__infra/redis-core__phase_3"
            assert group_key in _mounted_row_keys(mt)
            header_text = _cell_text(mt, group_key)
            assert "infra/redis-core" in header_text, header_text
            assert "phase 3" in header_text, header_text
            assert "(label)" in header_text, header_text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Oracle 3 — label vs inferred tier survive into the app, distinguishably, in
# the mounted table (P83's Oracle 4, re-asserted one layer up).
# ---------------------------------------------------------------------------


def test_pilot_oracle3_label_and_inferred_entities_are_distinguishable() -> None:
    async def run() -> None:
        lab = _ciu_entity_frame("c-lab", "lab-01", stack="app/web", phase_raw="phase_1", phase=1, source="label")
        inf = _ciu_entity_frame("c-inf", "inf-01", stack="app/web", phase_raw="phase_1", phase=1, source="inferred")
        app = _make_ciu_app([lab, inf])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            mt = app.query_one("#body-table")

            label_text = _cell_text(mt, "c-lab")
            inferred_text = _cell_text(mt, "c-inf")
            assert label_text != inferred_text
            assert "(inferred)" in inferred_text, inferred_text
            assert "(inferred)" not in label_text, label_text

            # The exact defect the P83 review found: a mixed group's header
            # must not claim a uniform "(label)" tier.
            header_text = _cell_text(mt, "__group__app/web__phase_1")
            assert "(mixed)" in header_text, header_text
            assert "(label)" not in header_text, header_text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Oracle 4 — Enter on a synthetic row is inert: cursor is provably on the row
# first, then no DrillDownScreen is pushed and no exception is raised.
# ---------------------------------------------------------------------------


def test_pilot_oracle4_enter_on_group_header_is_inert() -> None:
    async def run() -> None:
        lab = _ciu_entity_frame("c-lab", "lab-01", stack="app/web", phase_raw="phase_1", phase=1, source="label")
        app = _make_ciu_app([lab])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            mt = app.query_one("#body-table")
            group_key = "__group__app/web__phase_1"
            assert group_key in _mounted_row_keys(mt)

            mt.update_cursor_from_key(group_key)
            await pilot.pause()
            # Prove the cursor is ON the synthetic row before pressing Enter.
            assert mt.cursor_coordinate.row == mt.get_row_index(group_key)
            assert app.selected_key == group_key

            await pilot.press("enter")
            await pilot.pause()
            assert len(app.screen_stack) == 1, "Enter on a group header must not push a screen"
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


def test_pilot_oracle4_enter_on_ungrouped_header_is_inert() -> None:
    async def run() -> None:
        lab = _ciu_entity_frame("c-lab", "lab-01", stack="app/web", phase_raw="phase_1", phase=1, source="label")
        plain = _ciu_entity_frame("c-plain", "plain-01", has_ciu=False)
        app = _make_ciu_app([lab, plain])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            mt = app.query_one("#body-table")
            assert "__ungrouped__" in _mounted_row_keys(mt)

            mt.update_cursor_from_key("__ungrouped__")
            await pilot.pause()
            assert mt.cursor_coordinate.row == mt.get_row_index("__ungrouped__")
            assert app.selected_key == "__ungrouped__"

            await pilot.press("enter")
            await pilot.pause()
            assert len(app.screen_stack) == 1, "Enter on __ungrouped__ must not push a screen"
            assert not isinstance(app.screen, DrillDownScreen)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Oracle 5 — sorting works in this view, through the app: pressing the sort
# key reorders entity rows WITHIN a group in the mounted table.
# ---------------------------------------------------------------------------


def test_pilot_oracle5_sort_key_reorders_entity_rows_within_a_group() -> None:
    async def run() -> None:
        a = _ciu_entity_frame("c-a", "alpha", stack="s", phase_raw="phase_1", phase=1, ram=10.0, pressure=5.0)
        b = _ciu_entity_frame("c-b", "bravo", stack="s", phase_raw="phase_1", phase=1, ram=30.0, pressure=15.0)
        c = _ciu_entity_frame("c-c", "charlie", stack="s", phase_raw="phase_1", phase=1, ram=20.0, pressure=25.0)
        app = _make_ciu_app([a, b, c])
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "ciu-grouped"
            assert app.sort_by == "pressure"
            mt = app.query_one("#body-table")

            pressure_order = [k for k in _mounted_row_keys(mt) if not k.startswith("__")]
            assert pressure_order == ["c-c", "c-b", "c-a"], pressure_order  # pressure desc: 25, 15, 5

            await pilot.press("f6")  # cycle_sort: pressure -> ram
            await pilot.pause()
            assert app.sort_by == "ram"
            ram_order = [k for k in _mounted_row_keys(mt) if not k.startswith("__")]
            assert ram_order == ["c-b", "c-c", "c-a"], ram_order  # ram desc: 30, 20, 10
            assert ram_order != pressure_order, "sort key change must reorder rows in the mounted table"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Oracle 6 — a zero-ciu frame is unharmed: every entity appears exactly once,
# and no group header is rendered.
# ---------------------------------------------------------------------------


def test_pilot_oracle6_zero_ciu_frame_every_entity_once_no_group_header() -> None:
    async def run() -> None:
        frame = fixture_frame()
        assert all(
            ef.entity.ciu is None for ef in frame.entities.values()
        ), "fixture frame must have no ciu metadata for this oracle to be meaningful"
        app = GroopApp(
            iter([frame]),
            config=GroopConfig(default_view="tree", default_column_profile="triage"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _wait_for_frame(app)(pilot)
            await pilot.press("f5")
            await pilot.press("f5")
            await pilot.pause()
            assert app.view_mode == "ciu-grouped"
            mt = app.query_one("#body-table")
            mounted_keys = _mounted_row_keys(mt)

            group_headers = [k for k in mounted_keys if k.startswith("__group__")]
            assert not group_headers, f"zero-ciu frame must render no group header, got {group_headers!r}"

            entity_row_keys = [k for k in mounted_keys if not k.startswith("__")]
            assert sorted(entity_row_keys) == sorted(frame.entities.keys())
            assert len(entity_row_keys) == len(set(entity_row_keys)) == len(frame.entities)

    asyncio.run(run())
