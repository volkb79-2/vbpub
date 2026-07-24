"""Accumulate-never-delete, version-isolated benchmark store (D-B2/D-B3).

Consumes ``BenchmarkRecord`` from :mod:`nyxloom.benchmark_sources` and persists
them as ``StoredRow`` entries with store-managed provenance metadata.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark_sources import BenchmarkRecord


@dataclass(frozen=True)
class StoredRow:
    """One version-isolated benchmark store entry with provenance metadata."""

    benchmark: str | None
    version: str | None
    model_id: str
    effort: str | None
    scores: dict[str, float]
    price_input: float | None
    price_output: float | None
    context_length: int | None
    source: str
    first_seen: str
    last_seen: str
    present_upstream: bool
    raw: dict


def _key(benchmark: str | None, version: str | None, model_id: str,
         effort: str | None) -> tuple:
    return (benchmark, version, model_id, effort)


def _sort_key(row: StoredRow) -> tuple:
    return (row.benchmark or "", row.version or "", row.model_id, row.effort or "")


def merge_dataset(existing: list[StoredRow], scraped: list[BenchmarkRecord],
                  *, now: str) -> list[StoredRow]:
    by_key = {_key(r.benchmark, r.version, r.model_id, r.effort): r for r in existing}
    seen_keys: set[tuple] = set()

    for rec in scraped:
        k = _key(rec.benchmark, rec.version, rec.model_id, rec.effort)
        seen_keys.add(k)
        existing_row = by_key.get(k)
        by_key[k] = StoredRow(
            benchmark=rec.benchmark,
            version=rec.version,
            model_id=rec.model_id,
            effort=rec.effort,
            scores=dict(rec.scores),
            price_input=rec.price_input,
            price_output=rec.price_output,
            context_length=rec.context_length,
            source=rec.source,
            first_seen=existing_row.first_seen if existing_row is not None else now,
            last_seen=now,
            present_upstream=True,
            raw=dict(rec.raw),
        )

    for k, row in list(by_key.items()):
        if k not in seen_keys:
            by_key[k] = StoredRow(
                benchmark=row.benchmark,
                version=row.version,
                model_id=row.model_id,
                effort=row.effort,
                scores=dict(row.scores),
                price_input=row.price_input,
                price_output=row.price_output,
                context_length=row.context_length,
                source=row.source,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                present_upstream=False,
                raw=dict(row.raw),
            )

    return sorted(by_key.values(), key=_sort_key)


def _toml_str(value: str) -> str:
    return json.dumps(value)


def _render_row(row: StoredRow) -> str:
    lines = ["[[rows]]\n"]
    if row.benchmark is not None:
        lines.append(f"benchmark = {_toml_str(row.benchmark)}\n")
    if row.version is not None:
        lines.append(f"version = {_toml_str(row.version)}\n")
    lines.append(f"model_id = {_toml_str(row.model_id)}\n")
    if row.effort is not None:
        lines.append(f"effort = {_toml_str(row.effort)}\n")
    scores_items = ", ".join(f"{k} = {row.scores[k]}" for k in sorted(row.scores))
    lines.append(f"scores = {{{scores_items}}}\n")
    if row.price_input is not None:
        lines.append(f"price_input = {row.price_input}\n")
    if row.price_output is not None:
        lines.append(f"price_output = {row.price_output}\n")
    if row.context_length is not None:
        lines.append(f"context_length = {row.context_length}\n")
    lines.append(f"source = {_toml_str(row.source)}\n")
    lines.append(f"first_seen = {_toml_str(row.first_seen)}\n")
    lines.append(f"last_seen = {_toml_str(row.last_seen)}\n")
    lines.append(f"present_upstream = {str(row.present_upstream).lower()}\n")
    lines.append(
        f"raw_json = {_toml_str(json.dumps(row.raw, sort_keys=True))}\n")
    lines.append("\n")
    return "".join(lines)


def write_store(path: Path, rows: list[StoredRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(rows, key=_sort_key)
    text = "".join(_render_row(r) for r in sorted_rows)
    path.write_text(text, encoding="utf-8")


def load_store(path: Path) -> list[StoredRow]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    rows = data.get("rows", [])
    result = [
        StoredRow(
            benchmark=row.get("benchmark"),
            version=row.get("version"),
            model_id=row["model_id"],
            effort=row.get("effort"),
            scores=dict(row.get("scores", {})),
            price_input=row.get("price_input"),
            price_output=row.get("price_output"),
            context_length=row.get("context_length"),
            source=row["source"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            present_upstream=row["present_upstream"],
            raw=json.loads(row["raw_json"]),
        )
        for row in rows
    ]
    return sorted(result, key=_sort_key)
