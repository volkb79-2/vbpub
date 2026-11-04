#!/usr/bin/env python3
"""Rebuild exact-build stock middleware roots from official update images.

The updater feed supplies the exact version, filename, size, and SHA-256. Each
download is a SquashFS image containing a nested ``rootfs.squashfs``; only the
target files named by that build's ``targets.sha256`` are extracted. This tool
does not connect to or run a TrueNAS host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.request import Request, urlopen


PATCH_ROOT = Path(__file__).resolve().parents[1]
UPDATE_ROOT = "https://auto-public.sys.truenas.net"
USER_AGENT = "vbpub-truenas-stock-root-rebuilder/1"


class RebuildError(RuntimeError):
    """The feed, update image, extraction, or fingerprint check failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            value = json.load(response)
    except Exception as exc:
        raise RebuildError(f"cannot read official update metadata at {url}: {exc}") from exc
    if not isinstance(value, dict):
        raise RebuildError(f"official update metadata is not a JSON object: {url}")
    return value


def read_targets(path: Path) -> dict[str, str]:
    targets: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise RebuildError(f"invalid target fingerprint at {path}:{number}")
        relative = PurePosixPath(fields[1])
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                or relative.parts[0] not in {"middlewared", "truenas_pylibvirt"}):
            raise RebuildError(f"unsafe target path at {path}:{number}: {fields[1]!r}")
        if fields[1] in targets:
            raise RebuildError(f"duplicate target path at {path}:{number}: {fields[1]}")
        targets[fields[1]] = fields[0]
    if not targets:
        raise RebuildError(f"target manifest is empty: {path}")
    return targets


def release_artifact(version: str, train: str) -> tuple[dict[str, Any], str, str]:
    if not train.startswith("TrueNAS-") or "/" in train or ".." in train:
        raise RebuildError(f"unsafe update train in registry for {version}: {train!r}")
    feed_url = f"{UPDATE_ROOT}/{train}/releases.json"
    feed = fetch_json(feed_url)
    release = feed.get(version)
    if not isinstance(release, dict) or release.get("version") != version:
        raise RebuildError(f"official {train} feed does not list the exact registered build {version}")
    filename = release.get("filename")
    checksum = release.get("checksum")
    size = release.get("filesize")
    if (not isinstance(filename, str) or Path(filename).name != filename
            or not filename.endswith(".update")
            or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or type(size) is not int or size <= 0):
        raise RebuildError(f"official update metadata is incomplete for {version}: {release!r}")
    return release, feed_url, f"{UPDATE_ROOT}/{train}/{filename}"


def download_update(url: str, release: dict[str, Any], cache_dir: Path) -> Path:
    filename = release["filename"]
    destination = cache_dir / filename
    expected_size = release["filesize"]
    expected_hash = release["checksum"]
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise RebuildError(f"update cache path is not a regular file: {destination}")
        if destination.stat().st_size != expected_size or sha256_file(destination) != expected_hash:
            raise RebuildError(f"cached update does not match the official feed: {destination}")
        return destination

    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = cache_dir / f".{filename}.{os.getpid()}.partial"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    try:
        with urlopen(request, timeout=120) as response, partial.open("xb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise RebuildError(
                f"downloaded update disagrees with its official feed: {filename} "
                f"size={size}/{expected_size} sha256={digest.hexdigest()}/{expected_hash}"
            )
        os.replace(partial, destination)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, RebuildError):
            raise
        raise RebuildError(f"cannot download official update {url}: {exc}") from exc
    return destination


def run_unsquashfs(arguments: list[str], *, stdout=None) -> bytes | None:
    try:
        completed = subprocess.run(
            ["unsquashfs", "-no-progress", "-processors", "2", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout is None else stdout,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RebuildError(f"cannot execute unsquashfs: {exc}") from exc
    if completed.returncode != 0:
        details = completed.stderr.decode("utf-8", "replace").strip()
        raise RebuildError(f"unsquashfs failed ({completed.returncode}): {details}")
    return completed.stdout if stdout is None else None


def extract_targets(update: Path, targets: dict[str, str], version: str, work: Path) -> Path:
    outer_manifest = run_unsquashfs(["-cat", str(update), "manifest.json"])
    try:
        metadata = json.loads(outer_manifest or b"")
    except json.JSONDecodeError as exc:
        raise RebuildError(f"update image has invalid outer manifest for {version}: {exc}") from exc
    if (not isinstance(metadata, dict) or metadata.get("version") != version
            or not isinstance(metadata.get("checksums"), dict)
            or not isinstance(metadata["checksums"].get("rootfs.squashfs"), str)):
        raise RebuildError(f"update image manifest does not identify {version}: {metadata!r}")

    rootfs = work / "rootfs.squashfs"
    with rootfs.open("xb") as output:
        run_unsquashfs(["-cat", str(update), "rootfs.squashfs"], stdout=output)
    target_paths = [
        f"usr/lib/python3/dist-packages/{name}"
        for name in sorted(targets)
    ]
    extracted = work / "extracted"
    run_unsquashfs(["-match", "-dest", str(extracted), str(rootfs), *target_paths])
    return extracted


def rebuild_build(
    version: str,
    row: dict[str, Any],
    output_root: Path,
    cache_dir: Path,
) -> Path:
    adapter_root = PATCH_ROOT / row["directory"]
    target_manifest = adapter_root / "targets.sha256"
    targets = read_targets(target_manifest)
    if len(targets) != row.get("target_count"):
        raise RebuildError(f"registry target count mismatch for {version}")
    target_manifest_sha = sha256_file(target_manifest)
    if target_manifest_sha != row.get("target_manifest_sha256"):
        raise RebuildError(f"registry target manifest digest mismatch for {version}")
    destination = output_root / version
    if destination.exists() or destination.is_symlink():
        raise RebuildError(f"refusing to replace an existing stock root: {destination}")

    release, feed_url, update_url = release_artifact(version, row["train"])
    update = download_update(update_url, release, cache_dir)
    temporary_root = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=output_root))
    try:
        with tempfile.TemporaryDirectory(prefix=f"truenas-stock-{version}-") as work_name:
            extracted = extract_targets(update, targets, version, Path(work_name))
            source_prefix = extracted / "usr/lib/python3/dist-packages"
            for name, expected in targets.items():
                relative = PurePosixPath(name)
                source = source_prefix.joinpath(*relative.parts)
                current = source_prefix
                if any((current := current / part).is_symlink() for part in relative.parts):
                    raise RebuildError(f"update target traverses a symlink: {name}")
                if not source.is_file():
                    raise RebuildError(f"update is missing target source for {version}: {name}")
                actual = sha256_file(source)
                if actual != expected:
                    raise RebuildError(
                        f"update target hash differs for {version} {name}: "
                        f"expected={expected} actual={actual}"
                    )
                target = temporary_root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        info = {
            "schema_version": 1,
            "build": version,
            "train": row["train"],
            "update_feed_url": feed_url,
            "update_url": update_url,
            "update_sha256": release["checksum"],
            "update_size_bytes": release["filesize"],
            "target_manifest_sha256": target_manifest_sha,
            "target_count": len(targets),
        }
        (temporary_root / "SOURCE-INFO.json").write_text(
            json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        os.replace(temporary_root, destination)
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True,
                        help="directory that will contain one stock root per registered build")
    parser.add_argument("--cache-dir", type=Path, required=True,
                        help="cache for the multi-gigabyte official update images")
    args = parser.parse_args()
    try:
        registry = json.loads((PATCH_ROOT / "supported-builds.json").read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or registry.get("schema_version") != 1:
            raise RebuildError("unsupported supported-build registry schema")
        builds = registry.get("supported_builds")
        if not isinstance(builds, dict) or not builds:
            raise RebuildError("supported-build registry has no entries")
        args.output_root.mkdir(parents=True, exist_ok=True)
        for version, row in sorted(builds.items()):
            path = rebuild_build(version, row, args.output_root, args.cache_dir)
            print(f"STOCK_ROOT {version} {path}")
    except (OSError, json.JSONDecodeError, RebuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
