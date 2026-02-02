#!/usr/bin/env python3
"""Compare two MCR devcontainer tag snapshots and report changes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def collect_changes(prev: dict[str, Any], curr: dict[str, Any]) -> tuple[list[str], list[str]]:
    prev_matrix = prev.get("matrix", {})
    curr_matrix = curr.get("matrix", {})
    added: list[str] = []
    removed: list[str] = []

    for debian, python_map in curr_matrix.items():
        for py_version, exists in python_map.items():
            tag = f"1-{py_version}-{debian}"
            prev_exists = prev_matrix.get(debian, {}).get(py_version)
            if prev_exists is None:
                if exists:
                    added.append(tag)
                continue
            if exists and not prev_exists:
                added.append(tag)
            if not exists and prev_exists:
                removed.append(tag)

    return sorted(added), sorted(removed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare MCR devcontainer tag snapshots")
    parser.add_argument("--previous", required=True, help="Path to previous snapshot JSON")
    parser.add_argument("--current", required=True, help="Path to current snapshot JSON")
    parser.add_argument("--report-out", help="Write report to file path")
    parser.add_argument("--table-file", help="Optional table file to append to report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prev_path = Path(args.previous)
    curr_path = Path(args.current)

    if not prev_path.exists():
        print("[INFO] No previous snapshot found; skipping change detection.")
        return 2
    if not curr_path.exists():
        raise RuntimeError("Current snapshot missing")

    prev = load_json(str(prev_path))
    curr = load_json(str(curr_path))

    added, removed = collect_changes(prev, curr)
    lines: list[str] = []
    lines.append("MCR devcontainer tag update report")
    lines.append("")

    if not added and not removed:
        lines.append("No changes detected.")
    else:
        if added:
            lines.append("Newly available tags:")
            lines.extend([f"- {tag}" for tag in added])
            lines.append("")
        if removed:
            lines.append("Tags no longer available:")
            lines.extend([f"- {tag}" for tag in removed])
            lines.append("")

    if args.table_file and Path(args.table_file).exists():
        lines.append("Current availability matrix:")
        lines.append("")
        lines.append(Path(args.table_file).read_text(encoding="utf-8").rstrip())

    report = "\n".join(lines).rstrip() + "\n"
    if args.report_out:
        Path(args.report_out).write_text(report, encoding="utf-8")
    print(report)

    return 1 if (added or removed) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
