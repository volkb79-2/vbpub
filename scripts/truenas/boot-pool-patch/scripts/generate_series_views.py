#!/usr/bin/env python3
"""Generate the TrueNAS patch-series outline and legacy hunk bridge."""

from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


PATCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PATCH_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "libraries" / "patchseries" / "src"))
from patchseries import SeriesError, check, load_vocabulary, parse, render


class ViewError(RuntimeError):
    """Invalid patch metadata or a stale generated view."""


def patch_hunks(path: Path) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    file_path: str | None = None
    active: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("diff --git "):
            active = None
            file_path = None
        elif line.startswith("--- a/"):
            active = None
            file_path = line[6:].split("\t", 1)[0]
        elif line.startswith("+++ b/"):
            active = None
            file_path = line[6:].split("\t", 1)[0]
        elif line.startswith("@@"):
            if file_path is None:
                raise ViewError(f"hunk has no target path in {path}")
            active = {"path": file_path, "header": line, "body": []}
            hunks.append(active)
        elif active is not None and line.startswith((" ", "+", "-", "\\")):
            active["body"].append(line)
    return hunks


def hunk_digest(hunk: dict[str, Any]) -> str:
    payload = "\n".join((hunk["path"], hunk["header"], *hunk["body"])).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def changed_lines(hunk: dict[str, Any]) -> Counter[tuple[str, str]]:
    return Counter((line[0], line[1:]) for line in hunk["body"] if line.startswith(("+", "-")))


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def build_views() -> tuple[str, dict[str, Any]]:
    registry = json.loads((PATCH_ROOT / "supported-builds.json").read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise ViewError("unsupported supported-build registry schema")
    builds = registry.get("supported_builds")
    if not isinstance(builds, dict) or not builds:
        raise ViewError("supported-build registry is empty")
    vocabulary = load_vocabulary(PATCH_ROOT / "vocabulary.toml")
    parsed: dict[str, list[Any]] = {}
    bridge_rows: list[dict[str, Any]] = []
    adapter_meta: list[tuple[str, dict[str, Any]]] = []
    for version, row in sorted(builds.items()):
        adapter_root = PATCH_ROOT / row["directory"]
        series_root = adapter_root / row["series_dir"]
        changes = parse(series_root)
        check(changes, vocabulary)
        if [change.path.name for change in changes] != row["patches"]:
            raise ViewError(f"registry patch order differs from series for {version}")
        parsed[version] = changes
        adapter_meta.append((version, row))

        new_by_file: dict[str, list[tuple[str, Counter[tuple[str, str]]]]] = {}
        for change in changes:
            for hunk in patch_hunks(change.path):
                new_by_file.setdefault(hunk["path"], []).append(
                    (change.change_id, changed_lines(hunk))
                )

        legacy_root = adapter_root / "legacy"
        for patch_name in row["legacy_patches"]:
            legacy_patch = legacy_root / patch_name
            if not legacy_patch.is_file() or legacy_patch.is_symlink():
                raise ViewError(f"missing legacy bridge patch: {legacy_patch}")
            for ordinal, hunk in enumerate(patch_hunks(legacy_patch), 1):
                old_changes = changed_lines(hunk)
                candidates: dict[str, int] = {}
                for change_id, new_changes in new_by_file.get(hunk["path"], []):
                    overlap = sum((old_changes & new_changes).values())
                    if overlap:
                        candidates[change_id] = candidates.get(change_id, 0) + overlap
                if not candidates:
                    raise ViewError(
                        f"legacy hunk has no series change with matching changed lines: "
                        f"{version} {patch_name} {hunk['path']}#{ordinal}"
                    )
                bridge_rows.append({
                    "build": version,
                    "adapter_id": row["adapter_id"],
                    "legacy_patch": patch_name,
                    "hunk": ordinal,
                    "file": hunk["path"],
                    "hunk_sha256": hunk_digest(hunk),
                    "series_change_ids": [change.change_id for change in changes
                                          if change.change_id in candidates],
                    "series_change_line_overlap": [
                        {"change_id": change.change_id,
                         "changed_lines": candidates[change.change_id]}
                        for change in changes if change.change_id in candidates
                    ],
                    "legacy_changed_lines": sum(old_changes.values()),
                })

    outline_parts = [
        "# TrueNAS boot-pool patch series",
        "",
        "<!-- Generated by scripts/generate_series_views.py; edit patch trailers, not this file. -->",
        "",
        "The exact-build registry remains `supported-builds.json`. Each registered build points to an ordered `series/` directory; each patch is one logical change with `Change-Id`, `Capability`, `Role`, `Risk`, and `Requires` trailers. The inventory below is parsed and checked with the shared `libraries/patchseries` library.",
        "",
        render([(version, parsed[version]) for version, _ in adapter_meta]).strip(),
        "",
        "## Change outline",
        "",
    ]
    for version, row in adapter_meta:
        outline_parts.extend((f"### `{version}`", "", "| Patch | Change-Id | Capability | Role | Risk | Requires | Files | Hunks | Diffstat |", "|---|---|---|---|---|---|---|---:|---:|"))
        for change in parsed[version]:
            hunk_count = len(patch_hunks(change.path))
            files = ", ".join(f"`{escape_cell(name)}`" for name in change.files)
            requires = ", ".join(f"`{escape_cell(value)}`" for value in change.requires or ()) or "none"
            diffstat = f"+{change.insertions}/-{change.deletions}"
            outline_parts.append(
                f"| `{change.path.name}` | `{change.change_id}` | `{change.capability}` | "
                f"`{change.role}` | `{change.risk}` | {requires} | {files} | {hunk_count} | {diffstat} |"
            )
        outline_parts.extend(("",))
    outline_parts.extend((
        "## Legacy hunk bridge",
        "",
        "`manifest/hunk-map.json` records hashes for retained legacy hunks and links each to series changes by matching changed lines in the same target file. It is migration metadata for already-gated builds; installers read only the series listed in `supported-builds.json`.",
        "",
    ))
    outline = "\n".join(outline_parts).rstrip() + "\n"
    bridge = {
        "schema_version": 1,
        "purpose": "legacy hunk to patchseries Change-Id bridge for frozen gated builds",
        "runtime_input": False,
        "hunks": bridge_rows,
    }
    return outline, bridge


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args()
    try:
        outline, bridge = build_views()
    except (OSError, json.JSONDecodeError, SeriesError, ViewError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    outputs = {
        PATCH_ROOT / "PATCH-SERIES.md": outline,
        PATCH_ROOT / "manifest" / "hunk-map.json": json.dumps(bridge, indent=2, sort_keys=True) + "\n",
    }
    stale = False
    for path, content in outputs.items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                print(f"STALE: {path.relative_to(PATCH_ROOT)}")
                stale = True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"WROTE: {path.relative_to(PATCH_ROOT)}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
