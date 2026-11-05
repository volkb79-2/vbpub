"""Behaviour of the line-rendered ``MouseTable`` widget (no timing assertions)."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import App, ComposeResult

from topos.ui.data_table import MouseTable

KEYS = ("name", "ram", "cpu_pct")
LABELS = ("NAME", "RAM", "CPU%")


def _rows(names, *, style: str | None = None) -> list[list]:
    return [[Text(n, style=style or ""), Text("1.0GiB"), Text(str(i))] for i, n in enumerate(names)]


class Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple] = []

    def compose(self) -> ComposeResult:
        yield MouseTable(id="t")

    def on_mouse_table_header_selected(self, event) -> None:
        self.events.append(("header", event.column_key))

    def on_mouse_table_row_highlighted(self, event) -> None:
        self.events.append(("highlight", event.row_key))

    def on_mouse_table_row_selected(self, event) -> None:
        self.events.append(("select", event.row_key))

    def on_mouse_table_row_clicked(self, event) -> None:
        self.events.append(("click", event.row_key))


def _fill(table: MouseTable, names, *, labels=LABELS, style=None) -> None:
    table.populate(
        column_keys=KEYS,
        column_labels=labels,
        row_keys=tuple(names),
        rows=_rows(names, style=style),
    )


def _line(table: MouseTable, y: int) -> str:
    return table.render_line(y).text


def test_header_is_fixed_while_rows_scroll() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, [f"row{i:02d}" for i in range(30)])
            await pilot.pause()
            assert _line(table, 0).startswith("NAME")
            assert _line(table, 1).startswith("row00")
            table.scroll_to(y=10, animate=False, immediate=True)
            await pilot.pause()
            assert _line(table, 0).startswith("NAME")
            assert _line(table, 1).startswith("row10")
            # The last row is reachable.
            table.scroll_to(y=999, animate=False, immediate=True)
            await pilot.pause()
            last_visible = max(y for y in range(1, table.size.height) if _line(table, y).strip())
            assert _line(table, last_visible).startswith("row29")

    asyncio.run(run())


def test_cursor_keys_move_and_post_highlight_and_selection() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 8)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a", "b", "c"])
            table.focus()
            await pilot.pause()
            await pilot.press("down", "down", "up")
            await pilot.press("enter")
            await pilot.pause()
            assert table.row_key_at_cursor() == "b"
            assert ("select", "b") in app.events
            assert [e for e in app.events if e[0] == "highlight"] == [
                ("highlight", "b"),
                ("highlight", "c"),
                ("highlight", "b"),
            ]
            # Cursor stops at the ends instead of wrapping.
            await pilot.press("pageup", "up")
            assert table.row_key_at_cursor() == "a"
            await pilot.press("pagedown", "down")
            assert table.row_key_at_cursor() == "c"

    asyncio.run(run())


def test_cursor_follows_its_row_key_across_populate_and_scroll_is_kept() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            names = [f"row{i:02d}" for i in range(30)]
            _fill(table, names)
            await pilot.pause()
            table.update_cursor_from_key("row20")
            await pilot.pause()
            scroll = table.scroll_offset.y
            assert scroll > 0
            _fill(table, list(reversed(names)))
            await pilot.pause()
            assert table.row_key_at_cursor() == "row20"
            assert table.cursor_coordinate.row == 9
            assert table.scroll_offset.y == scroll
            # A vanished row clamps to the table instead of failing.
            _fill(table, names[:3])
            await pilot.pause()
            assert table.row_key_at_cursor() in names[:3]

    asyncio.run(run())


def test_header_click_posts_header_selected_for_the_clicked_column() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a", "b"])
            await pilot.pause()
            start, width = table.column_span("ram")
            await pilot.click(MouseTable, offset=(start + width // 2, 0))
            await pilot.pause()
            assert ("header", "ram") in app.events
            assert not [e for e in app.events if e[0] == "select"]

    asyncio.run(run())


def test_first_click_on_a_row_moves_cursor_and_reports_the_click() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a", "b", "c"])
            await pilot.pause()
            await pilot.click(MouseTable, offset=(2, 3))  # line 3 = third row
            await pilot.pause()
            assert table.row_key_at_cursor() == "c"
            assert any(e[0] == "click" and e[1] == "c" for e in app.events)

    asyncio.run(run())


def test_columns_wider_than_the_widget_scroll_horizontally() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(20, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a-very-long-row-name-indeed"], labels=("NAME", "RAM", "CPU%"))
            await pilot.pause()
            assert table.virtual_size.width > table.size.width
            assert "CPU%" not in _line(table, 0)
            table.scroll_to(x=table.max_scroll_x, animate=False, immediate=True)
            await pilot.pause()
            assert _line(table, 0).rstrip().endswith("CPU%")
            assert _line(table, 1).rstrip().endswith("0")

    asyncio.run(run())


def test_cell_styles_survive_as_segment_styles() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a", "b"], style="red")
            await pilot.pause()
            strip = table.render_line(2)  # row b, not under the cursor
            red = [seg for seg in strip if seg.text.strip() == "b"]
            assert red and red[0].style is not None and red[0].style.color.name == "red"

    asyncio.run(run())


def test_empty_placeholder_row_is_inert() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["__empty__"])
            await pilot.pause()
            assert table.row_key_at_cursor() is None
            await pilot.click(MouseTable, offset=(2, 1))
            await pilot.pause()
            assert table.row_key_at_cursor() is None

    asyncio.run(run())


def test_header_label_markup_suffix_is_not_displayed() -> None:
    async def run() -> None:
        app = Host()
        async with app.run_test(size=(40, 6)) as pilot:
            table = app.query_one(MouseTable)
            _fill(table, ["a"], labels=("NAME", "RAM[subtree]", "CPU%"))
            await pilot.pause()
            assert table.column_labels == ("NAME", "RAM", "CPU%")
            assert "subtree" not in _line(table, 0)

    asyncio.run(run())
