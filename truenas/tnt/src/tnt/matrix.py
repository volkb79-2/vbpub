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


def backlog_rows(rows: list[Row]) -> list[Row]:
    """Rows whose verdict says the TUI (not the patched UI) is the product path."""
    return [r for r in rows if r.verdict in BACKLOG_VERDICTS]
