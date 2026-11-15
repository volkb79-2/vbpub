#!/usr/bin/env python3
"""Check per-package LCOV line and llvm-cov region floors from one summary."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path


class FloorError(ValueError):
    """The coverage summary or committed per-package floors are incomplete."""


def _workspace_packages(root: Path) -> set[str]:
    try:
        workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]
    except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise FloorError(f"cannot read workspace package list: {exc}") from exc
    packages: set[str] = set()
    for pattern in workspace.get("members", []):
        matches = sorted(root.glob(pattern))
        if not matches:
            raise FloorError(f"workspace member pattern matched no packages: {pattern}")
        for package_dir in matches:
            manifest_path = package_dir / "Cargo.toml"
            try:
                manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
                name = manifest["package"]["name"]
            except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
                raise FloorError(f"cannot read workspace member {manifest_path}: {exc}") from exc
            if not isinstance(name, str):
                raise FloorError(f"workspace member has no package name: {manifest_path}")
            packages.add(package_dir.relative_to(root).as_posix())
    return packages


def _fraction(value: object, label: str, path: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise FloorError(f"coverage summary has no {label} counts for {path}")
    covered = value.get("covered")
    count = value.get("count")
    if not isinstance(covered, int) or not isinstance(count, int) or count <= 0 or covered < 0 or covered > count:
        raise FloorError(f"coverage summary has invalid {label} counts for {path}: {value!r}")
    return covered, count


def check(summary_path: Path, floors_path: Path, root: Path) -> list[str]:
    try:
        floors_doc = tomllib.loads(floors_path.read_text(encoding="utf-8"))
        floors = floors_doc["crates"]
    except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise FloorError(f"cannot read coverage floors {floors_path}: {exc}") from exc
    if not isinstance(floors, dict) or not floors:
        raise FloorError("coverage-floors.toml must declare at least one crate floor")

    package_dirs = _workspace_packages(root)
    configured = set(floors)
    if configured != package_dirs:
        missing = sorted(package_dirs - configured)
        extra = sorted(configured - package_dirs)
        details = []
        if missing:
            details.append(f"packages without floors: {', '.join(missing)}")
        if extra:
            details.append(f"floors without workspace packages: {', '.join(extra)}")
        raise FloorError("; ".join(details))

    try:
        report = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FloorError(f"cannot read llvm-cov summary {summary_path}: {exc}") from exc
    try:
        files = [file for datum in report["data"] for file in datum["files"]]
    except (KeyError, TypeError) as exc:
        raise FloorError("llvm-cov summary has no data[].files[] records") from exc

    totals = {package_dir: {"lines": [0, 0], "regions": [0, 0], "files": 0} for package_dir in configured}
    root = root.resolve()
    for file in files:
        filename = file.get("filename")
        if not isinstance(filename, str):
            raise FloorError(f"llvm-cov file record has no filename: {file!r}")
        path = Path(filename)
        try:
            relative = path.resolve().relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        except ValueError as exc:
            raise FloorError(f"llvm-cov reports a source outside the workspace: {filename}") from exc
        matches = [package_dir for package_dir in configured if relative.startswith(package_dir + "/")]
        if not matches:
            raise FloorError(f"llvm-cov reports Rust source without a committed crate floor: {filename}")
        if len(matches) != 1:
            raise FloorError(f"llvm-cov source maps to overlapping crate floors: {filename}")
        package_dir = matches[0]
        summary = file.get("summary")
        if not isinstance(summary, dict):
            raise FloorError(f"llvm-cov file record has no summary: {filename}")
        for metric in ("lines", "regions"):
            covered, count = _fraction(summary.get(metric), metric, filename)
            totals[package_dir][metric][0] += covered
            totals[package_dir][metric][1] += count
        totals[package_dir]["files"] += 1

    output: list[str] = []
    for package_dir in sorted(configured):
        package_floor = floors[package_dir]
        if not isinstance(package_floor, dict):
            raise FloorError(f"coverage floor for {package_dir} must be a table")
        for metric in ("line", "region"):
            floor = package_floor.get(metric)
            if not isinstance(floor, (int, float)) or not 0 <= floor <= 100:
                raise FloorError(f"coverage floor for {package_dir}.{metric} must be between 0 and 100")
        record = totals[package_dir]
        if record["files"] == 0:
            raise FloorError(f"llvm-cov reported no source files for crate {package_dir}")
        rendered = []
        for metric, label in (("lines", "line"), ("regions", "region")):
            covered, count = record[metric]
            floor = package_floor[label]
            if count == 0:
                raise FloorError(f"llvm-cov reported no {metric} for crate {package_dir}")
            rendered.append(f"{label} {covered * 100 / count:.2f}% ({covered}/{count}) >= {floor:.2f}%")
            if covered * 100 < floor * count:
                raise FloorError(
                    f"crate {package_dir} {label} coverage is {covered * 100 / count:.2f}% "
                    f"({covered}/{count}); floor is {floor:.2f}%"
                )
        output.append(f"{package_dir}: " + "; ".join(rendered))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("floors", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check(args.summary, args.floors, args.workspace)
    except FloorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\n".join(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
