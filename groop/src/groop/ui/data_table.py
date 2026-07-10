from __future__ import annotations

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
        self.clear(columns=True)
        self._col_keys = column_keys
        for col_key, label in zip(column_keys, column_labels):
            self.add_column(label, key=col_key)
        self._row_keys = row_keys
        for rk, cells in zip(row_keys, rows):
            self.add_row(*cells, key=rk)

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
