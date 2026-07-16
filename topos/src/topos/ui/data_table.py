from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import App
from textual.binding import Binding
from textual.widgets import DataTable


class MouseTable(DataTable, inherit_bindings=False):
    """A DataTable subclass using row-mode cursor with native up/down/Enter.

    Key bindings
    ------------
    - Up/Down: navigate rows natively (fires ``RowHighlighted``).
    - Enter: activates the highlighted row (fires ``RowSelected``).
    - PageUp/PageDown: scrolling.
    - Left/Right: delegated to the app for tree collapse/expand.
    - Home/End: NOT handled here so the app can use them for replay.

    The parent app catches ``HeaderSelected``, ``RowHighlighted``, and
    ``RowSelected`` messages to implement sort, selection tracking, and
    drill-down opening.

    Mouse support degrades harmlessly when the terminal sends no mouse
    events — the DataTable simply relies on keyboard input.
    """

    # Explicitly list every DataTable binding EXCEPT left/right/home/end
    # (left/right fire tree collapse/expand; home/end fire replay navigation).
    BINDINGS = (
        Binding("enter", "select_cursor", "Select", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.show_cursor = True
        self.show_row_labels = False
        self.zebra_stripes = False
        self._col_keys: tuple[str, ...] = ()
        self._column_labels: tuple[str, ...] = ()
        self._row_keys: tuple[str, ...] = ()

    def populate(
        self,
        *,
        column_keys: tuple[str, ...],
        column_labels: tuple[str, ...],
        row_keys: tuple[str, ...],
        rows: list[list],
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
        """
        columns_changed = column_keys != self._col_keys
        labels_changed = column_labels != self._column_labels
        rows_changed = row_keys != self._row_keys

        if columns_changed:
            self.clear(columns=True)
            for col_key, label in zip(column_keys, column_labels):
                self.add_column(label, key=col_key)
            rows_changed = True
        else:
            if labels_changed:
                # DataTable has no public header-label setter. Updating its
                # native column metadata preserves hit-test keys, horizontal
                # scroll, and rendered-cell cache across indicator changes.
                for column, label in zip(self.ordered_columns, column_labels):
                    column.label = Text(label)
                self._update_count += 1
                self._require_update_dimensions = True
                self.refresh(layout=True)
            if rows_changed:
                # Preserve column widths/header layout during sort, filter,
                # and entity-set changes instead of rebuilding everything.
                self.clear()

        self._col_keys = column_keys
        self._column_labels = column_labels
        if rows_changed:
            for rk, cells in zip(row_keys, rows):
                self.add_row(*cells, key=rk)
        else:
            # The stable common case for live/replay sampling: update values
            # in place while retaining row keys, cursor, and scroll position.
            for rk, cells in zip(row_keys, rows):
                for col_key, cell in zip(column_keys, cells):
                    self.update_cell(rk, col_key, cell, update_width=True)
        self._row_keys = row_keys

    async def _on_click(self, event: events.Click) -> None:
        """Activate a data row on its first click.

        Textual's native DataTable hit metadata is used here; no terminal
        coordinate arithmetic is involved. Upstream posts ``RowSelected``
        only when the clicked cell is already the cursor coordinate. For row
        cursors that makes a newly clicked entity require a second click, so
        post the same native selection message after the first cursor move.
        """
        meta = event.style.meta
        row_index = meta.get("row")
        column_index = meta.get("column")
        data_cell = (
            isinstance(row_index, int)
            and isinstance(column_index, int)
            and row_index >= 0
            and column_index >= 0
            and not meta.get("out_of_bounds", False)
        )
        previously_selected_cell = (
            data_cell
            and self.cursor_coordinate.row == row_index
            and self.cursor_coordinate.column == column_index
        )
        # We explicitly invoke the upstream implementation once. Suppress
        # Textual's subsequent base-class default handler or a header click
        # would emit HeaderSelected twice.
        event.prevent_default()
        await super()._on_click(event)
        event.stop()
        if data_cell and not previously_selected_cell:
            # ``super`` moved the row cursor and posted RowHighlighted, but
            # intentionally did not post RowSelected on a first click.
            self._post_selected_message()

    def update_cursor_from_key(self, key: str | None) -> None:
        """Move the DataTable cursor to the row whose key matches *key*.

        No-op if *key* is ``None`` or not found.
        """
        if key is None or key not in self._row_keys:
            return
        row_index = self._row_keys.index(key)
        self.move_cursor(row=row_index)

    def row_key_at_cursor(self) -> str | None:
        """Return the stable entity key at the current cursor, or None."""
        cursor_row, _ = self.cursor_coordinate
        if cursor_row is None or cursor_row < 0 or cursor_row >= len(self._row_keys):
            return None
        rk = self._row_keys[cursor_row]
        return None if rk.startswith("__empty__") else rk

    def action_select_cursor(self) -> None:
        """Enter key handler — delegates to the parent so ``RowSelected``
        is posted and the app can open the drill-down screen."""
        super().action_select_cursor()

    def action_cursor_left(self) -> None:
        """Left arrow — delegate to the app for tree collapse.

        Without this override Textual's screen-level focus navigation
        consumes ``left`` before app bindings can fire.
        """
        app = self.app
        if isinstance(app, App) and hasattr(app, "action_collapse_tree"):
            app.action_collapse_tree()

    def action_cursor_right(self) -> None:
        """Right arrow — delegate to the app for tree expand.

        Without this override Textual's screen-level focus navigation
        consumes ``right`` before app bindings can fire.
        """
        app = self.app
        if isinstance(app, App) and hasattr(app, "action_expand_tree"):
            app.action_expand_tree()
