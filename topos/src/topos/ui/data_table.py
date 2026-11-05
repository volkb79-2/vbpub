from __future__ import annotations

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from collections.abc import Sequence

_GAP = 1
"""Blank cells between two columns."""

_LEFT_ALIGNED = frozenset(
    {"name", "tier", "net_source", "governance_origin", "governance_drift", "damon_mode", "cpu_trend"}
)
"""Columns whose cells are left-aligned; every other column is right-aligned."""


def _plain(cell) -> str:
    if isinstance(cell, Text):
        return cell.plain
    return cell[0] if isinstance(cell, tuple) else str(cell)


def _label_text(label: str) -> Text:
    try:
        return Text.from_markup(label)
    except Exception:  # noqa: BLE001 - malformed markup shows literally
        return Text(label)


class MouseTable(ScrollView, can_focus=True, inherit_bindings=False):
    """A line-rendered table: one ``render_line`` call per visible terminal row.

    Textual's ``DataTable`` costs hundreds of milliseconds of CPU per frame at
    topos row counts; this widget builds a styled ``Strip`` only for the lines
    actually on screen, from column widths computed once per ``populate``.

    Layout
    ------
    The header is the fixed first line; data rows scroll beneath it. When the
    columns are wider than the widget the content scrolls horizontally (the
    right side is never silently cut off).

    Key bindings
    ------------
    - Up/Down: move the cursor (posts ``RowHighlighted``).
    - PageUp/PageDown: move by a page.
    - Enter: activates the highlighted row (posts ``RowSelected``).
    - Left/Right: delegated to the app for tree collapse/expand.
    - Home/End: NOT handled here so the app can use them for replay.

    Mouse: clicking a header posts ``HeaderSelected``; clicking a data row moves
    the cursor there and posts ``RowClicked`` (Enter posts ``RowSelected``).
    """

    BINDINGS = (
        Binding("enter", "select_cursor", "Select", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
    )

    COMPONENT_CLASSES = {"mousetable--header", "mousetable--cursor"}

    DEFAULT_CSS = """
    MouseTable {
        overflow-y: scroll;
        scrollbar-size-vertical: 1;
    }
    MouseTable > .mousetable--header {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    MouseTable > .mousetable--cursor {
        background: $primary 40%;
    }
    MouseTable:focus > .mousetable--cursor {
        background: $accent;
        color: $text;
    }
    """

    class HeaderSelected(Message):
        """A column header was clicked."""

        def __init__(self, table: "MouseTable", column_key: str, column_index: int) -> None:
            super().__init__()
            self.table = table
            self.column_key = column_key
            self.column_index = column_index

        @property
        def control(self) -> "MouseTable":
            return self.table

    class RowHighlighted(Message):
        """The cursor moved to a row (``row_key`` is None for an empty table)."""

        def __init__(self, table: "MouseTable", row_key: str | None, row_index: int) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key
            self.row_index = row_index

        @property
        def control(self) -> "MouseTable":
            return self.table

    class RowSelected(Message):
        """A row was activated (Enter or click)."""

        def __init__(self, table: "MouseTable", row_key: str, row_index: int) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key
            self.row_index = row_index

        @property
        def control(self) -> "MouseTable":
            return self.table

    class RowClicked(Message):
        """A data row was clicked at *x* (content coordinates); the cursor already moved there."""

        def __init__(self, table: "MouseTable", row_key: str, row_index: int, x: int) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key
            self.row_index = row_index
            self.x = x

        @property
        def control(self) -> "MouseTable":
            return self.table

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._col_keys: tuple[str, ...] = ()
        self._column_labels: tuple[str, ...] = ()
        self._header_cells: list[Text] = []
        self._row_keys: tuple[str, ...] = ()
        self._rows: list[list] = []
        self._col_widths: list[int] = []
        self._col_starts: list[int] = []
        self._cursor_row = 0
        self._strip_cache: dict[int, Strip] = {}
        self._header_cache: Strip | None = None
        self._style_cache: dict[str, Style] = {}
        self._left_keys: frozenset[str] = _LEFT_ALIGNED
        self._fixed_widths = False

    # ── public state ──────────────────────────────────────────────────

    @property
    def row_count(self) -> int:
        return len(self._row_keys)

    @property
    def row_keys(self) -> tuple[str, ...]:
        """Row keys in display order."""
        return self._row_keys

    @property
    def column_keys(self) -> tuple[str, ...]:
        return self._col_keys

    @property
    def column_labels(self) -> tuple[str, ...]:
        return self._column_labels

    @property
    def cursor_coordinate(self) -> Coordinate:
        return Coordinate(self._cursor_row, 0)

    @cursor_coordinate.setter
    def cursor_coordinate(self, value: Coordinate) -> None:
        self._cursor_row = value.row
        self._invalidate_rows()

    def get_row_index(self, row_key: str) -> int:
        return self._row_keys.index(row_key)

    def get_cell(self, row_key: str, column_key: str):
        """The Rich ``Text`` (or value) shown at *row_key* / *column_key*."""
        return self._rows[self._row_keys.index(row_key)][self._col_keys.index(column_key)]

    def column_span(self, column_key: str) -> tuple[int, int]:
        """(start x in content coordinates, width) of a column."""
        index = self._col_keys.index(column_key)
        return self._col_starts[index], self._col_widths[index]

    def on_resize(self, event: events.Resize) -> None:
        """Tell the app the real table width changed (columns depend on it)."""
        self._invalidate_rows()
        handler = getattr(self.app, "on_table_width_changed", None)
        if handler is not None:
            handler(self.scrollable_content_region.width or event.size.width)

    # ── content ───────────────────────────────────────────────────────

    def populate(
        self,
        *,
        column_keys: tuple[str, ...],
        column_labels: tuple[str, ...],
        row_keys: tuple[str, ...],
        rows: "Sequence[list]",
        widths: "Sequence[int] | None" = None,
        left_keys: "frozenset[str] | None" = None,
    ) -> None:
        """Replace all table content.

        Parameters
        ----------
        column_keys:
            Stable canonical metric keys for each column (used for sort).
        column_labels:
            Display labels for each column header.
        row_keys:
            Stable entity key for each row (used for selection/drill).
        rows:
            Cell content for each row, parallel to *row_keys*. Each inner list
            must have the same length as *column_keys*.

        widths:
            Fixed display width per column. Without it widths follow the
            content. *rows* may then be a lazy sequence: only the lines on
            screen are ever indexed.
        left_keys:
            Column keys whose cells are left-aligned.

        The cursor follows its row key across the refresh (clamped when the
        row vanished) and the scroll offset is retained.
        """
        previous_key = self._row_keys[self._cursor_row] if 0 <= self._cursor_row < len(self._row_keys) else None
        self._col_keys = column_keys
        # Labels are Rich markup (as DataTable treated them), so a trailing
        # "[subtree]" branch suffix is not displayed.
        self._header_cells = [_label_text(label) for label in column_labels]
        self._column_labels = tuple(text.plain for text in self._header_cells)
        self._row_keys = row_keys
        self._rows = rows
        self._left_keys = _LEFT_ALIGNED if left_keys is None else left_keys
        self._fixed_widths = widths is not None
        self._fixed = list(widths) if widths is not None else []
        if previous_key is not None and previous_key in row_keys:
            self._cursor_row = row_keys.index(previous_key)
        else:
            self._cursor_row = max(0, min(self._cursor_row, len(row_keys) - 1))
        self._compute_widths()
        self._invalidate_rows()
        total = sum(self._col_widths) + _GAP * max(0, len(self._col_widths) - 1)
        self.virtual_size = Size(total, len(row_keys) + 1)

    def _compute_widths(self) -> None:
        if self._fixed_widths:
            widths = list(self._fixed)
            self._col_widths = widths
            self._col_starts = [sum(widths[:i]) + _GAP * i for i in range(len(widths))]
            return
        widths = [cell_len(label) for label in self._column_labels]
        for row in self._rows:
            for j, cell in enumerate(row):
                text = _plain(cell)
                n = len(text) if text.isascii() else cell_len(text)
                if n > widths[j]:
                    widths[j] = n
        self._col_widths = widths
        starts: list[int] = []
        x = 0
        for w in widths:
            starts.append(x)
            x += w + _GAP
        self._col_starts = starts

    def _invalidate_rows(self) -> None:
        self._strip_cache.clear()
        self._header_cache = None
        self._style_cache.clear()
        if self.is_attached:
            self.refresh()

    # ── rendering ─────────────────────────────────────────────────────

    def _component(self, name: str) -> Style:
        style = self._style_cache.get(name)
        if style is None:
            style = self.get_component_rich_style(name)
            self._style_cache[name] = style
        return style

    def _cell_segments(self, cell, key: str, width: int) -> list[Segment]:
        right = key not in self._left_keys
        if isinstance(cell, tuple):
            text = Text(cell[0], style=cell[1], no_wrap=True)
        else:
            text = cell if isinstance(cell, Text) else Text(str(cell))
        if text.cell_len > width:
            text = text.copy()
            text.truncate(width, overflow="ellipsis")
        pad = width - text.cell_len
        console = self.app.console
        segments = list(text.render(console))
        if text.style:
            # Text.render ignores the cell's own base style.
            segments = list(Segment.apply_style(segments, console.get_style(text.style, default=Style.null())))
        if pad > 0:
            blank = Segment(" " * pad)
            if right:
                segments.insert(0, blank)
            else:
                segments.append(blank)
        return segments

    def _build_strip(self, cells, *, header: bool) -> Strip:
        gap = Segment(" " * _GAP)
        segments: list[Segment] = []
        for j, cell in enumerate(cells):
            if j:
                segments.append(gap)
            segments.extend(self._cell_segments(cell, self._col_keys[j], self._col_widths[j]))
        return Strip(segments)

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        scroll_x, scroll_y = self.scroll_offset
        if y == 0:
            strip = self._header_cache
            if strip is None:
                strip = self._build_strip(self._header_cells, header=True).apply_style(
                    self._component("mousetable--header")
                )
                self._header_cache = strip
            fill = self._component("mousetable--header")
            return strip.crop_extend(scroll_x, scroll_x + width, fill)
        index = y - 1 + scroll_y
        if index >= len(self._rows):
            return Strip.blank(width, self.rich_style)
        strip = self._strip_cache.get(index)
        if strip is None:
            strip = self._build_strip(self._rows[index], header=False)
            if index == self._cursor_row:
                strip = strip.apply_style(self._component("mousetable--cursor"))
            self._strip_cache[index] = strip
        fill = self._component("mousetable--cursor") if index == self._cursor_row else self.rich_style
        return strip.crop_extend(scroll_x, scroll_x + width, fill)

    # ── cursor ────────────────────────────────────────────────────────

    def move_cursor(self, *, row: int | None = None) -> None:
        """Move the cursor to *row* (clamped); posts ``RowHighlighted`` on change."""
        if row is None or not self._row_keys:
            return
        row = max(0, min(row, len(self._row_keys) - 1))
        if row == self._cursor_row:
            return
        self._cursor_row = row
        self._invalidate_rows()
        self._ensure_cursor_visible()
        self.post_message(self.RowHighlighted(self, self._row_keys[row], row))

    def _ensure_cursor_visible(self) -> None:
        page = max(1, self.size.height - 1)
        top = self.scroll_offset.y
        if self._cursor_row < top:
            self.scroll_to(y=self._cursor_row, animate=False, immediate=True)
        elif self._cursor_row >= top + page:
            self.scroll_to(y=self._cursor_row - page + 1, animate=False, immediate=True)

    def update_cursor_from_key(self, key: str | None) -> None:
        """Move the cursor to the row whose key matches *key*.

        No-op if *key* is ``None`` or not found.
        """
        if key is None or key not in self._row_keys:
            return
        self.move_cursor(row=self._row_keys.index(key))

    def row_key_at_cursor(self) -> str | None:
        """Return the stable entity key at the current cursor, or None."""
        cursor_row = self._cursor_row
        if cursor_row < 0 or cursor_row >= len(self._row_keys):
            return None
        rk = self._row_keys[cursor_row]
        return None if rk.startswith("__empty__") else rk

    # ── actions ───────────────────────────────────────────────────────

    def action_cursor_up(self) -> None:
        self.move_cursor(row=self._cursor_row - 1)

    def action_cursor_down(self) -> None:
        self.move_cursor(row=self._cursor_row + 1)

    def action_page_up(self) -> None:
        self.move_cursor(row=self._cursor_row - max(1, self.size.height - 1))

    def action_page_down(self) -> None:
        self.move_cursor(row=self._cursor_row + max(1, self.size.height - 1))

    def action_select_cursor(self) -> None:
        """Enter key handler: posts ``RowSelected`` for the highlighted row."""
        if 0 <= self._cursor_row < len(self._row_keys):
            self.post_message(self.RowSelected(self, self._row_keys[self._cursor_row], self._cursor_row))

    def action_cursor_left(self) -> None:
        """Left arrow — delegate to the app for tree collapse.

        Without this override Textual's screen-level focus navigation
        consumes ``left`` before app bindings can fire.
        """
        app = self.app
        if isinstance(app, App) and hasattr(app, "action_collapse_tree"):
            app.action_collapse_tree()

    def action_cursor_right(self) -> None:
        """Right arrow — delegate to the app for tree expand."""
        app = self.app
        if isinstance(app, App) and hasattr(app, "action_expand_tree"):
            app.action_expand_tree()

    # ── mouse ─────────────────────────────────────────────────────────

    def _column_at(self, x: int) -> int | None:
        for index in range(len(self._col_starts) - 1, -1, -1):
            if x >= self._col_starts[index]:
                if x < self._col_starts[index] + self._col_widths[index] + _GAP:
                    return index
                return None
        return None

    async def _on_click(self, event: events.Click) -> None:
        """Header click sorts; a click on a data row moves the cursor there."""
        event.stop()
        event.prevent_default()
        scroll_x, scroll_y = self.scroll_offset
        if event.y == 0:
            column = self._column_at(event.x + scroll_x)
            if column is not None:
                self.post_message(self.HeaderSelected(self, self._col_keys[column], column))
            return
        row = event.y - 1 + scroll_y
        if not 0 <= row < len(self._row_keys):
            return
        self.focus()
        self.move_cursor(row=row)
        self.post_message(self.RowClicked(self, self._row_keys[row], row, event.x + scroll_x))
