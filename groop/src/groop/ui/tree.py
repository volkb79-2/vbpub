from __future__ import annotations

from rich.table import Table

from groop.config import GroopConfig
from groop.model import EntityFrame, Frame
from groop.record.ring import HistoryRing

from .table import RenderedRows, _make_table, display_name, format_metric_value, metric_sort_value, resolve_profile


def render_tree_table(
    frame: Frame,
    config: GroopConfig,
    *,
    width: int,
    profile: str,
    sort_by: str,
    filter_text: str,
    selected_key: str | None,
    collapsed_keys: set[str],
    ring: HistoryRing | None = None,
) -> RenderedRows:
    layout = resolve_profile(config, width=width, profile=profile)
    title = f"TREE | profile={layout.name}"
    if layout.ignored_columns:
        title = f"{title} ignored={','.join(layout.ignored_columns)}"
    table = _make_table(layout.columns, title=f"{title} | sort={sort_by or 'name'}")
    row_keys: list[str] = []
    rows = _ordered_rows(frame, sort_by=sort_by, filter_text=filter_text, collapsed_keys=collapsed_keys)
    for depth, entity_frame, collapsed in rows:
        row_keys.append(entity_frame.entity.key)
        cells = [format_metric_value(column_name, entity_frame, ring=ring) for column_name in layout.columns]
        if cells:
            prefix = _tree_prefix(frame, entity_frame, depth, collapsed=collapsed)
            cells[0] = prefix + cells[0]
        table.add_row(*_row_cells_from_cells(cells, selected=entity_frame.entity.key == selected_key))
    if not row_keys:
        table.add_row("no rows", *[""] * (max(0, len(layout.columns) - 1)))
    return RenderedRows(table=table, row_keys=tuple(row_keys), title=table.title or "")


def _ordered_rows(
    frame: Frame,
    *,
    sort_by: str,
    filter_text: str,
    collapsed_keys: set[str],
) -> list[tuple[int, EntityFrame, bool]]:
    children: dict[str | None, list[EntityFrame]] = {}
    for entity_frame in frame.entities.values():
        children.setdefault(entity_frame.entity.parent, []).append(entity_frame)
    needle = filter_text.lower().strip()

    def include(entity_frame: EntityFrame) -> bool:
        if not needle:
            return True
        haystacks = (display_name(entity_frame.entity).lower(), entity_frame.entity.key.lower())
        return any(needle in haystack for haystack in haystacks)

    def walk(parent: str | None, depth: int) -> list[tuple[int, EntityFrame, bool]]:
        branch = children.get(parent, [])
        ordered = _sort_branch(branch, sort_by)
        out: list[tuple[int, EntityFrame, bool]] = []
        for entity_frame in ordered:
            descendants = walk(entity_frame.entity.key, depth + 1)
            collapsed = bool(descendants) and not needle and entity_frame.entity.key in collapsed_keys
            if include(entity_frame) or descendants:
                out.append((depth, entity_frame, collapsed))
                if not collapsed:
                    out.extend(descendants)
        return out

    return walk(None, 0)


def _sort_branch(branch: list[EntityFrame], sort_by: str) -> list[EntityFrame]:
    if sort_by == "name":
        return sorted(branch, key=lambda entity_frame: display_name(entity_frame.entity).lower())
    return sorted(branch, key=lambda entity_frame: metric_sort_value(sort_by, entity_frame), reverse=True)


def _tree_prefix(frame: Frame, entity_frame: EntityFrame, depth: int, *, collapsed: bool) -> Table | str:
    has_children = any(child.entity.parent == entity_frame.entity.key for child in frame.entities.values())
    glyph = "▸ " if has_children and collapsed else "▾ " if has_children else "  "
    return type(format_metric_value("name", entity_frame))(f"{'  ' * depth}{glyph}")


def _row_cells_from_cells(cells, *, selected: bool):
    if not cells:
        return cells
    name_cell = cells[0]
    marker = ">" if selected else " "
    cells[0] = type(name_cell).assemble((f"{marker} ", "bold cyan" if selected else ""), name_cell)
    return cells
