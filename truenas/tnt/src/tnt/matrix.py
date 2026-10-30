"""Read the canonical ACTION-MATRIX table (a markdown table, ids A-xx / B-xx, methods in backticks)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROW_ID = re.compile(r"^[AB]-\d{2}$")
BACKTICK = re.compile(r"`([^`]+)`")
REQUIRED = ("id", "path", "action", "API methods", "verdict", "size", "v27")
BACKLOG_VERDICTS = ("API-TUI", "pending decision")


@dataclass(frozen=True)
class Row:
    id: str
    path: str
    action: str
    methods: tuple[str, ...]
    verdict: str
    size: str
    v27: str


def parse(text: str) -> list[Row]:
    """Rows of the ``## Actions`` table. The header is found by its ``| id |`` first cell."""
    rows: list[Row] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "id":
            missing = [c for c in REQUIRED if c not in cells]
            if missing:
                raise ValueError(f"matrix header lacks column(s) {missing}")
            header = cells
            continue
        if header is None or not ROW_ID.match(cells[0]) or len(cells) != len(header):
            continue
        col = dict(zip(header, cells))
        rows.append(Row(col["id"], col["path"], col["action"], tuple(m for m in BACKTICK.findall(col["API methods"]) if m != "-"),
                        col["verdict"], col["size"], col["v27"]))
    return rows


def load(path: Path) -> list[Row]:
    return parse(path.read_text("utf-8"))


def backlog_matrix_ids(backlog: str) -> dict[str, set[str]]:
    """TNT item id -> the matrix row ids named in that item's matrix COLUMN (the second cell of its table row).

    A row id that only appears in a notes column or in prose does not count as covered.
    """
    out: dict[str, set[str]] = {}
    for line in backlog.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| TNT-") and len(cells) >= 2:
            out[cells[0]] = {t for t in re.findall(r"[AB]-\d{2}", cells[1])}
    return out


def uncovered(rows: list[Row], backlog: str) -> list[str]:
    """Backlog-verdict matrix rows that no TNT item names in its matrix column."""
    named = set().union(*backlog_matrix_ids(backlog).values()) if backlog_matrix_ids(backlog) else set()
    return [r.id for r in backlog_rows(rows) if r.id not in named]


def backlog_rows(rows: list[Row]) -> list[Row]:
    """Rows whose verdict says the TUI (not the patched UI) is the product path."""
    return [r for r in rows if r.verdict in BACKLOG_VERDICTS]
