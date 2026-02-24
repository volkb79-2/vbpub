#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

PATTERNS = [
    re.compile(r"\{[^{}]+\}"),
    re.compile(r"<[^>\n]+>"),
    re.compile(r"\[(?:/?[bicuv]|c|-)\]"),
    re.compile(r"\[[0-9A-Fa-f]{6}\]"),
    re.compile(r"@\w+\d*"),
    re.compile(r"\\n"),
]


def extract_tokens(value: str) -> list[str]:
    out: list[str] = []
    for pattern in PATTERNS:
        out.extend(match.group(0) for match in pattern.finditer(value or ""))
    return out


def _load_changed_rows(path: Path | None) -> dict[str, set[int]]:
    if path is None:
        return {}
    changed: dict[str, set[int]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            file_name = (row.get("file") or "").strip()
            row_text = (row.get("row") or "").strip()
            if not file_name or not row_text:
                continue
            try:
                row_index = int(row_text)
            except ValueError:
                continue
            changed.setdefault(file_name, set()).add(row_index)
    return changed


def validate_file(path: Path, only_rows: set[int] | None) -> tuple[int, int]:
    issues = 0
    total = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_idx, row in enumerate(reader, start=2):
            if only_rows is not None and row_idx not in only_rows:
                continue
            english = row.get("English") or ""
            deutsch = row.get("Deutsch") or ""
            if not english.strip() or not deutsch.strip():
                continue
            total += 1
            src = set(extract_tokens(english))
            dst = set(extract_tokens(deutsch))
            missing = sorted(src - dst)
            extra = sorted(dst - src)
            if missing or extra:
                issues += 1
                print(f"[ERROR] {path.name}:{row_idx} KEY={row.get('KEY','')} missing={missing} extra={extra}")
    return total, issues


def _map_output_name_to_source(path: Path) -> str:
    name = path.name
    if name.endswith('.de.completed.csv'):
        return name.replace('.de.completed.csv', '.csv')
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate token parity between English and German columns")
    parser.add_argument("files", nargs="+", help="CSV files to validate")
    parser.add_argument(
        "--changes-csv",
        help="Optional applied_changes.csv path. If provided, only changed rows are validated.",
    )
    parser.add_argument(
        "--full-file",
        action="store_true",
        help="Validate all rows (includes pre-existing mismatches).",
    )
    args = parser.parse_args()

    changed_rows = _load_changed_rows(Path(args.changes_csv)) if args.changes_csv else {}

    grand_total = 0
    grand_issues = 0
    for file_name in args.files:
        path = Path(file_name)
        only_rows = None
        if not args.full_file and changed_rows:
            source_name = _map_output_name_to_source(path)
            only_rows = changed_rows.get(source_name, set())
        total, issues = validate_file(path, only_rows)
        grand_total += total
        grand_issues += issues
        scope = "full" if only_rows is None else f"changed_rows={len(only_rows)}"
        print(f"[INFO] {file_name}: checked={total} issues={issues} scope={scope}")

    if grand_issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
