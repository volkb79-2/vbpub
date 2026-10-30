#!/usr/bin/env python3
"""Prove that each registered patch series produces the legacy output bytes.

The input roots are clean stock source trees containing the files listed by
each adapter's ``targets.sha256``. The proof applies the retained legacy patch
files in their recorded installer order and the ``patchseries`` files in
natural series order, then compares SHA-256 for every touched file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


PATCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PATCH_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "libraries" / "patchseries" / "src"))
from patchseries import SeriesError, check, load_vocabulary, parse


class ProofError(RuntimeError):
    """A proof precondition or equivalence assertion failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_targets(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise ProofError(f"invalid target hash at {path}:{number}")
        name = fields[1]
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                or relative.parts[0] not in {"middlewared", "truenas_pylibvirt"}):
            raise ProofError(f"unsafe target path at {path}:{number}: {name!r}")
        if name in result:
            raise ProofError(f"duplicate target path at {path}:{number}: {name!r}")
        result[name] = fields[0]
    if not result:
        raise ProofError(f"target manifest is empty: {path}")
    return result


def changed_paths(patch: Path) -> set[str]:
    """Read the destination path set from a unified or Git-format patch."""
    paths: set[str] = set()
    for line in patch.read_text(encoding="utf-8").splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        value = line[4:].split("\t", 1)[0]
        if value == "/dev/null":
            continue
        if value.startswith(("a/", "b/")):
            value = value[2:]
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ProofError(f"unsafe patch target in {patch}: {value!r}")
        paths.add(value)
    if not paths:
        raise ProofError(f"patch has no file paths: {patch}")
    return paths


def copy_preimages(stock_root: Path, target_names: set[str], destination: Path) -> None:
    for name in sorted(target_names):
        relative = PurePosixPath(name)
        source = stock_root.joinpath(*relative.parts)
        current = stock_root
        if any((current := current / part).is_symlink() for part in relative.parts):
            raise ProofError(f"stock target traverses a symlink: {name}")
        if not source.is_file():
            raise ProofError(f"stock source target is missing: {name}")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def apply_patch_set(root: Path, patches: list[Path], label: str) -> None:
    for patch_file in patches:
        completed = subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch_file)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ProofError(
                f"{label} patch failed: {patch_file.name}\n{completed.stdout.strip()}"
            )


def parse_stock_roots(values: list[str], registered: set[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        version, separator, raw_path = value.partition("=")
        if not separator or not version or not raw_path:
            raise ProofError(f"--stock-root must be VERSION=PATH, got {value!r}")
        if version not in registered:
            raise ProofError(f"--stock-root names an unregistered build: {version}")
        if version in roots:
            raise ProofError(f"duplicate stock root for build: {version}")
        root = Path(raw_path).resolve(strict=True)
        if not root.is_dir():
            raise ProofError(f"stock root is not a directory for {version}: {root}")
        roots[version] = root
    missing = sorted(registered - set(roots))
    if missing:
        raise ProofError(f"missing stock roots for registered builds: {', '.join(missing)}")
    return roots


def build_proof(version: str, row: dict[str, Any], stock_root: Path, vocabulary: dict[str, frozenset[str]]) -> dict[str, Any]:
    adapter_root = PATCH_ROOT / row["directory"]
    target_manifest = adapter_root / "targets.sha256"
    manifest_sha256 = sha256_file(target_manifest)
    if manifest_sha256 != row.get("target_manifest_sha256"):
        raise ProofError(
            f"target manifest digest differs from supported-build registry for {version}: "
            f"expected={row.get('target_manifest_sha256')!r} actual={manifest_sha256}"
        )
    expected = read_targets(target_manifest)
    if len(expected) != row.get("target_count"):
        raise ProofError(
            f"target count differs from supported-build registry for {version}: "
            f"expected={row.get('target_count')!r} actual={len(expected)}"
        )
    target_issues = {
        name: {"expected": digest, "actual": None}
        for name, digest in expected.items()
    }
    for name, digest in expected.items():
        source = stock_root.joinpath(*PurePosixPath(name).parts)
        if source.is_file() and not source.is_symlink():
            actual = sha256_file(source)
            if actual == digest:
                target_issues.pop(name)
            else:
                target_issues[name]["actual"] = actual
    if target_issues:
        raise ProofError(f"stock preimage hashes do not match {version}: {target_issues!r}")

    series_root = adapter_root / row["series_dir"]
    try:
        changes = parse(series_root)
        check(changes, vocabulary)
    except (OSError, SeriesError) as exc:
        raise ProofError(f"invalid patch series for {version}: {exc}") from exc
    series_names = [change.path.name for change in changes]
    if series_names != row["patches"]:
        raise ProofError(f"registry series order differs for {version}: {series_names!r}")

    legacy_root = adapter_root / "legacy"
    legacy_patches = [legacy_root / name for name in row["legacy_patches"]]
    series_patches = [series_root / name for name in row["patches"]]
    for patch_file in [*legacy_patches, *series_patches]:
        if not patch_file.is_file() or patch_file.is_symlink():
            raise ProofError(f"missing or unsafe proof patch: {patch_file}")

    legacy_paths = set().union(*(changed_paths(path) for path in legacy_patches))
    series_paths = set().union(*(changed_paths(path) for path in series_patches))
    if legacy_paths != series_paths:
        raise ProofError(
            f"legacy/series changed-file sets differ for {version}: "
            f"legacy-only={sorted(legacy_paths-series_paths)!r} "
            f"series-only={sorted(series_paths-legacy_paths)!r}"
        )
    outside_manifest = legacy_paths - set(expected)
    if outside_manifest:
        raise ProofError(f"patch targets are absent from the stock fingerprint manifest: {sorted(outside_manifest)!r}")

    with tempfile.TemporaryDirectory(prefix="truenas-series-equivalence-") as temporary:
        root = Path(temporary)
        legacy_tree = root / "legacy"
        series_tree = root / "series"
        legacy_tree.mkdir()
        series_tree.mkdir()
        copy_preimages(stock_root, legacy_paths, legacy_tree)
        copy_preimages(stock_root, series_paths, series_tree)
        apply_patch_set(legacy_tree, legacy_patches, f"legacy {version}")
        apply_patch_set(series_tree, series_patches, f"series {version}")

        file_rows: list[dict[str, Any]] = []
        for name in sorted(legacy_paths):
            relative = PurePosixPath(name)
            stock = stock_root.joinpath(*relative.parts)
            legacy = legacy_tree.joinpath(*relative.parts)
            series = series_tree.joinpath(*relative.parts)
            stock_sha = sha256_file(stock)
            legacy_sha = sha256_file(legacy)
            series_sha = sha256_file(series)
            if legacy_sha == stock_sha or series_sha == stock_sha:
                raise ProofError(
                    f"expected both patch sets to change {name} for {version}: "
                    f"stock={stock_sha} legacy={legacy_sha} series={series_sha}"
                )
            identical = legacy.read_bytes() == series.read_bytes()
            file_rows.append({
                "file": name,
                "stock_sha256": stock_sha,
                "legacy_sha256": legacy_sha,
                "series_sha256": series_sha,
                "identical": identical,
            })
        mismatches = [item for item in file_rows if not item["identical"]]
        if mismatches:
            raise ProofError(f"relaxed file bytes differ for {version}: {mismatches!r}")

    return {
        "build": version,
        "adapter_id": row["adapter_id"],
        "target_manifest_sha256": manifest_sha256,
        "target_count": len(expected),
        "legacy_patch_count": len(legacy_patches),
        "series_patch_count": len(series_patches),
        "patched_file_count": len(file_rows),
        "all_identical": True,
        "files": file_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stock-root", action="append", default=[], metavar="VERSION=PATH",
        help="clean source tree for one exact registered build; repeat for each build",
    )
    args = parser.parse_args()
    try:
        registry = json.loads((PATCH_ROOT / "supported-builds.json").read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or registry.get("schema_version") != 1:
            raise ProofError("unsupported supported-build registry schema")
        builds = registry.get("supported_builds")
        if not isinstance(builds, dict) or not builds:
            raise ProofError("supported-build registry has no entries")
        stock_roots = parse_stock_roots(args.stock_root, set(builds))
        vocabulary = load_vocabulary(PATCH_ROOT / "vocabulary.toml")
        rows = [build_proof(version, builds[version], stock_roots[version], vocabulary)
                for version in sorted(builds)]
        result = {
            "schema_version": 1,
            "proof": "legacy and patchseries outputs are byte-identical per patched file",
            "patch_command": "patch --batch --fuzz=0 -p1",
            "build_count": len(rows),
            "all_builds_identical": all(row["all_identical"] for row in rows),
            "builds": rows,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, json.JSONDecodeError, ProofError, SeriesError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
