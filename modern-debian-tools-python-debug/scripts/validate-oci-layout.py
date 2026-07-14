#!/usr/bin/env python3
"""Fail when an OCI layout contains structurally impossible layer paths."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


def _blob_path(layout: Path, digest: str) -> Path:
    algorithm, separator, value = digest.partition(":")
    if not separator or not algorithm or not value:
        raise ValueError(f"invalid OCI digest: {digest!r}")
    return layout / "blobs" / algorithm / value


def _load_descriptor_json(layout: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    path = _blob_path(layout, str(descriptor.get("digest", "")))
    with path.open("rb") as stream:
        return json.load(stream)


def _image_manifests(layout: Path) -> Iterator[dict[str, Any]]:
    with (layout / "index.json").open("rb") as stream:
        root = json.load(stream)

    pending = list(root.get("manifests") or [])
    seen: set[str] = set()
    while pending:
        descriptor = pending.pop()
        digest = str(descriptor.get("digest", ""))
        if digest in seen:
            continue
        seen.add(digest)
        document = _load_descriptor_json(layout, descriptor)
        if "layers" in document:
            yield document
        elif "manifests" in document:
            pending.extend(document.get("manifests") or [])
        else:
            raise ValueError(f"descriptor {digest} is neither an image index nor a manifest")


def _normalized_member_path(name: str) -> PurePosixPath | None:
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if not name or name == ".":
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe tar member path: {name!r}")
    return path


def _layer_conflicts(blob: Path) -> tuple[int, list[str]]:
    non_directories: set[PurePosixPath] = set()
    parents_with_children: set[PurePosixPath] = set()
    directory_paths: set[PurePosixPath] = set()
    entries = 0

    with tarfile.open(blob, mode="r:*") as archive:
        for member in archive:
            path = _normalized_member_path(member.name)
            if path is None:
                continue
            entries += 1
            for parent in path.parents:
                if parent != PurePosixPath("."):
                    parents_with_children.add(parent)
            if member.isdir():
                directory_paths.add(path)
            else:
                non_directories.add(path)

    conflicts = sorted(non_directories & parents_with_children)
    type_changes = sorted(non_directories & directory_paths)
    messages = [f"non-directory path has descendants in the same layer: {path}" for path in conflicts]
    messages.extend(f"path is both a directory and non-directory in the same layer: {path}" for path in type_changes)
    return entries, messages


def validate_layout(layout: Path) -> tuple[int, int, list[str]]:
    if not (layout / "oci-layout").is_file() or not (layout / "index.json").is_file():
        raise ValueError(f"not an OCI image layout: {layout}")

    manifest_count = 0
    entry_count = 0
    errors: list[str] = []
    seen_layers: set[str] = set()
    for manifest in _image_manifests(layout):
        manifest_count += 1
        for descriptor in manifest.get("layers") or []:
            digest = str(descriptor.get("digest", ""))
            if digest in seen_layers:
                continue
            seen_layers.add(digest)
            entries, conflicts = _layer_conflicts(_blob_path(layout, digest))
            entry_count += entries
            errors.extend(f"{digest}: {message}" for message in conflicts)

    if not manifest_count:
        raise ValueError("OCI layout contains no image manifests")
    return manifest_count, entry_count, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("--max-errors", type=int, default=20)
    args = parser.parse_args()

    try:
        manifests, entries, errors = validate_layout(args.layout)
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"[ERROR] OCI validation could not inspect {args.layout}: {exc}", file=sys.stderr)
        return 2

    if errors:
        print(
            f"[ERROR] OCI validation rejected {args.layout}: "
            f"{len(errors)} structural conflict(s)",
            file=sys.stderr,
        )
        for error in errors[: args.max_errors]:
            print(f"[ERROR]   {error}", file=sys.stderr)
        if len(errors) > args.max_errors:
            print(f"[ERROR]   ... {len(errors) - args.max_errors} more", file=sys.stderr)
        return 1

    print(
        f"[INFO]     OCI structure valid: {manifests} manifest(s), "
        f"{entries} layer entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
