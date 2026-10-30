"""Textual widgets that render remote strings as literal text."""
from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable as TextualDataTable
from textual.widgets import Label as TextualLabel
from textual.widgets import Static as TextualStatic


class _LiteralText(Text):
    """Mark table strings that were wrapped to avoid markup parsing."""


def _literal(value: Any) -> Any:
    """Keep strings literal when Textual would otherwise parse console markup."""
    return _LiteralText(value) if isinstance(value, str) else value


def _cell_value(value: Any) -> Any:
    return value.plain if isinstance(value, _LiteralText) else value


class Static(TextualStatic):
    """A Static widget whose string content is never interpreted as markup."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(*args, **kwargs)


class Label(TextualLabel):
    """A Label widget whose string content is never interpreted as markup."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["markup"] = False
        super().__init__(*args, **kwargs)


class DataTable(TextualDataTable):
    """A DataTable that renders string cells literally without changing cell reads."""

    def add_column(self, label: Any, *args: Any, **kwargs: Any) -> Any:
        if "default" in kwargs:
            kwargs["default"] = _literal(kwargs["default"])
        return super().add_column(_literal(label), *args, **kwargs)

    def add_row(self, *cells: Any, **kwargs: Any) -> Any:
        if "label" in kwargs:
            kwargs["label"] = _literal(kwargs["label"])
        return super().add_row(*(_literal(cell) for cell in cells), **kwargs)

    def update_cell(self, row_key: Any, column_key: Any, value: Any, **kwargs: Any) -> None:
        super().update_cell(row_key, column_key, _literal(value), **kwargs)

    def get_cell(self, row_key: Any, column_key: Any) -> Any:
        return _cell_value(super().get_cell(row_key, column_key))
