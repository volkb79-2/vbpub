#!/usr/bin/env python3
"""Experimental xorriso growing-mode ISO build with a live-boot SquashFS overlay.

This is deliberately separate from the normal byte-preserving generator. It
keeps the upstream filesystem.squashfs extent intact, adds only changed files
as a second SquashFS layer, and writes the updated ISO tree in xorriso's
growing mode for regular-file images. It exists to measure and boot-test this
layout before it becomes a supported generator mode.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any

from iso_profiles import (
    DROPIN_PATH,
    GRUB_CONFIG_PATHS,
    RUNTIME_DIR,
    ProfileError,
    install_profile,
    patch_grub_config,
)
from patch_iso import (
    FILESYSTEM_PATH,
    INSTALLER_PATH,
    PatcherError,
    _check_boot_entries,
    _extract_iso_file,
    _iso_file_extent,
    _sha256,
    _squashfs_info,
    checked,
    patch_installer_source,
    validate_boot_pool_size,
)

OVERLAY_NAME = "filesystem-vanilla-iso-patcher.squashfs"
MODULE_CONTENT = f"filesystem.squashfs\n{OVERLAY_NAME}\n"


def _copy_overlay_file(source_root: Path, overlay_root: Path, relative: Path) -> None:
    source = source_root / relative
    target = overlay_root / relative
    if source.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2, symlinks=True)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    else:
        raise PatcherError(f"overlay source is missing or unsupported: {source}")


def _create_overlay_iso(
    input_iso: Path,
    staged_iso: Path,
    temp: Path,
    *,
    size_gib: int,
    flavor: str,
    post_install_action: str,
) -> dict[str, Any]:
    source_squashfs = temp / "filesystem.source.squashfs"
    live_root = temp / "live-root"
    overlay_root = temp / "overlay-root"
    overlay_squashfs = temp / OVERLAY_NAME
    module_file = temp / "filesystem.module"

    _extract_iso_file(input_iso, FILESYSTEM_PATH, source_squashfs)
    compression, block_size = _squashfs_info(source_squashfs)
    checked(["unsquashfs", "-no-progress", "-d", str(live_root), str(source_squashfs)])

    installer = live_root / INSTALLER_PATH
    original_source = installer.read_text(encoding="utf-8")
    patched_source = patch_installer_source(original_source, size_gib)
    installer.write_text(patched_source, encoding="utf-8")
    try:
        release_version = (live_root / "etc/version").read_text(encoding="utf-8").strip()
        profile_info = install_profile(
            live_root,
            flavor,
            size_gib,
            release_version,
            post_install_action=post_install_action,
        )
    except (OSError, ProfileError) as exc:
        raise PatcherError(f"cannot prepare {flavor} overlay: {exc}") from exc

    overlay_root.mkdir()
    _copy_overlay_file(live_root, overlay_root, INSTALLER_PATH)
    if flavor != "bounded":
        _copy_overlay_file(live_root, overlay_root, RUNTIME_DIR)
        _copy_overlay_file(live_root, overlay_root, DROPIN_PATH)

    checked(
        [
            "mksquashfs",
            str(overlay_root),
            str(overlay_squashfs),
            "-noappend",
            "-comp",
            compression,
            "-b",
            str(block_size),
            "-no-progress",
        ]
    )
    module_file.write_text(MODULE_CONTENT, encoding="ascii")

    grub_files: list[tuple[str, Path]] = []
    for index, grub_path in enumerate(GRUB_CONFIG_PATHS):
        extracted = temp / f"grub-source-{index}.cfg"
        _extract_iso_file(input_iso, grub_path, extracted)
        extent = _iso_file_extent(input_iso, grub_path)
        if extracted.stat().st_size != extent.file_bytes:
            raise PatcherError(f"ISO file record length changed for {grub_path}")
        try:
            patched = patch_grub_config(
                extracted.read_bytes(),
                extent.file_bytes,
                use_toram=False,
            )
        except ProfileError as exc:
            raise PatcherError(f"cannot prepare {grub_path} for the overlay: {exc}") from exc
        host_path = temp / f"grub-overlay-{index}.cfg"
        host_path.write_bytes(patched)
        grub_files.append((host_path, grub_path))

    shutil.copyfile(input_iso, staged_iso)
    maps = [
        (overlay_squashfs, f"/live/{OVERLAY_NAME}"),
        (module_file, "/live/filesystem.module"),
        *grub_files,
    ]
    argv = ["xorriso", "-dev", str(staged_iso), "-boot_image", "any", "keep", "-overwrite", "on"]
    for host_path, iso_path in maps:
        argv.extend(["-map", str(host_path), iso_path])
    argv.append("-commit")
    checked(argv)

    _check_boot_entries(staged_iso)
    source_extent = _iso_file_extent(input_iso, FILESYSTEM_PATH)
    output_extent = _iso_file_extent(staged_iso, FILESYSTEM_PATH)
    if source_extent != output_extent:
        raise PatcherError("growing session moved or changed the original filesystem.squashfs extent")
    verified_base = temp / "filesystem.base.verified.squashfs"
    _extract_iso_file(staged_iso, FILESYSTEM_PATH, verified_base)
    if _sha256(verified_base) != _sha256(source_squashfs):
        raise PatcherError("growing session changed the original filesystem.squashfs bytes")

    verified_module = temp / "filesystem.verified.module"
    _extract_iso_file(staged_iso, "/live/filesystem.module", verified_module)
    if verified_module.read_text(encoding="ascii") != MODULE_CONTENT:
        raise PatcherError("growing session did not preserve the intended filesystem.module order")
    verified_overlay = temp / "filesystem.overlay.verified.squashfs"
    _extract_iso_file(staged_iso, f"/live/{OVERLAY_NAME}", verified_overlay)
    verified_installer = checked(
        ["unsquashfs", "-cat", str(verified_overlay), INSTALLER_PATH.as_posix()]
    ).stdout
    if verified_installer != patched_source:
        raise PatcherError("overlay SquashFS does not contain the bounded installer source")
    verified_runtime = checked(
        ["unsquashfs", "-cat", str(verified_overlay), f"{RUNTIME_DIR.as_posix()}/profile.json"]
    ).stdout if flavor != "bounded" else ""
    if flavor != "bounded":
        profile = json.loads(verified_runtime)
        if (
            profile.get("flavor") != flavor
            or profile.get("boot_pool_size_gib") != size_gib
            or profile.get("post_install_action") != post_install_action
        ):
            raise PatcherError("overlay profile does not match the requested unattended flavor")

    for _host_path, grub_path in grub_files:
        verified_grub = temp / f"verified-{Path(grub_path).name}-{hashlib.sha256(grub_path.encode()).hexdigest()[:8]}"
        _extract_iso_file(staged_iso, grub_path, verified_grub)
        text = verified_grub.read_text(encoding="ascii")
        if "toram=filesystem.squashfs" in text:
            raise PatcherError(f"multisession GRUB config still forces one toram image: {grub_path}")
        if flavor != "bounded" and "truenas.autoinstall=1" not in text:
            raise PatcherError(f"multisession GRUB config lacks the unattended entry: {grub_path}")

    if staged_iso.stat().st_size <= input_iso.stat().st_size:
        raise PatcherError("xorriso growing mode did not append any bytes to the ISO file")
    overlay_extent = _iso_file_extent(staged_iso, f"/live/{OVERLAY_NAME}")
    if overlay_extent.byte_offset < input_iso.stat().st_size:
        raise PatcherError("the new overlay SquashFS was not appended beyond the vanilla image")
    toc_result = checked(["xorriso", "-indev", str(staged_iso), "-toc"])
    toc = toc_result.stdout + toc_result.stderr
    summary_lines = [line.strip() for line in toc.splitlines() if line.strip().startswith("Media summary:")]
    if not summary_lines:
        raise PatcherError(f"xorriso did not report a media summary after growing the ISO:\n{toc[-2000:]}")
    session_count_match = re.search(r"Media summary:\s*(\d+)\s+sessions?\b", summary_lines[-1])
    session_count = int(session_count_match.group(1)) if session_count_match else None

    return {
        "release_version": release_version,
        "flavor": flavor,
        "boot_pool_partition_gib": size_gib,
        "relaxation_adapter": profile_info["adapter_id"],
        "base_squashfs_bytes": source_extent.file_bytes,
        "overlay_squashfs_bytes": verified_overlay.stat().st_size,
        "source_squashfs_sha256": _sha256(source_squashfs),
        "overlay_squashfs_sha256": _sha256(verified_overlay),
        "xorriso_reported_sessions": session_count,
        "xorriso_media_summary": summary_lines[-1],
        "image_layout": "xorriso growing-mode file image; appended extents, latest tree visible",
    }


def create_poc(
    input_iso: Path,
    expected_sha256: str,
    output_iso: Path,
    delta_output: Path,
    size_gib: int,
    flavor: str,
    post_install_action: str = "reboot",
    xdelta: str | None = None,
) -> dict[str, Any]:
    validate_boot_pool_size(size_gib)
    if flavor not in {"bounded", "unattended", "unattended-relaxed"}:
        raise PatcherError("unknown PoC flavor")
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in expected_sha256):
        raise PatcherError("expected input SHA-256 must contain exactly 64 hexadecimal digits")
    input_iso = input_iso.expanduser().resolve(strict=True)
    output_iso = output_iso.expanduser().resolve(strict=False)
    delta_output = delta_output.expanduser().resolve(strict=False)
    if (
        not input_iso.is_file()
        or input_iso == output_iso
        or input_iso == delta_output
        or output_iso == delta_output
    ):
        raise PatcherError("input ISO and both outputs must be distinct regular-file paths")
    if output_iso.exists() or delta_output.exists():
        raise PatcherError("refusing to overwrite an existing PoC output")
    if not output_iso.parent.is_dir() or not delta_output.parent.is_dir():
        raise PatcherError("output parent directories must already exist")
    if os.geteuid() != 0:
        raise PatcherError("run as root so SquashFS ownership and extended attributes are preserved")

    expected_sha256 = expected_sha256.lower()
    source_sha256 = _sha256(input_iso)
    if not hmac.compare_digest(source_sha256, expected_sha256):
        raise PatcherError(f"input ISO SHA-256 mismatch: expected {expected_sha256}, got {source_sha256}")
    for tool in ("xorriso", "unsquashfs", "mksquashfs"):
        if shutil.which(tool) is None:
            raise PatcherError(f"required tool {tool!r} is not installed or not on PATH")

    published_iso = False
    published_delta = False
    published_manifest = False
    manifest_path = Path(str(delta_output) + ".json")
    if manifest_path.exists():
        raise PatcherError(f"refusing to overwrite an existing PoC manifest: {manifest_path}")
    try:
        with tempfile.TemporaryDirectory(prefix=".multisession-poc-", dir=output_iso.parent) as temp_name:
            temp = Path(temp_name)
            staged_iso = temp / "overlay.iso"
            metadata = _create_overlay_iso(
                input_iso,
                staged_iso,
                temp,
                size_gib=size_gib,
                flavor=flavor,
                post_install_action=post_install_action,
            )
            output_iso.hardlink_to(staged_iso)
            published_iso = True

        from delta import create_and_verify_delta

        delta_result = create_and_verify_delta(
            xdelta or "xdelta3",
            input_iso,
            output_iso,
            delta_output,
            expected_source_sha256=source_sha256,
        )
        published_delta = True
        result = {
            **metadata,
            "input_iso": str(input_iso),
            "input_bytes": input_iso.stat().st_size,
            "input_sha256": source_sha256,
            "output_iso": str(output_iso),
            "output_bytes": output_iso.stat().st_size,
            "output_sha256": _sha256(output_iso),
            "appended_bytes": output_iso.stat().st_size - input_iso.stat().st_size,
            "delta_output": str(delta_output),
            **delta_result,
        }
        descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        published_manifest = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return result
    except BaseException:
        if published_iso:
            output_iso.unlink(missing_ok=True)
        if published_delta:
            delta_output.unlink(missing_ok=True)
        if published_manifest:
            manifest_path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-iso", type=Path, required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--output-iso", type=Path, required=True)
    parser.add_argument("--delta-output", type=Path, required=True)
    parser.add_argument("--boot-pool-size-gib", type=int, required=True)
    parser.add_argument(
        "--flavor",
        choices=("bounded", "unattended", "unattended-relaxed"),
        required=True,
    )
    parser.add_argument("--post-install-action", choices=("reboot", "poweroff"), default="reboot")
    parser.add_argument("--xdelta")
    args = parser.parse_args(argv)
    try:
        result = create_poc(
            args.input_iso,
            args.expected_input_sha256,
            args.output_iso,
            args.delta_output,
            args.boot_pool_size_gib,
            args.flavor,
            args.post_install_action,
            args.xdelta,
        )
    except (OSError, PatcherError, ProfileError) as exc:
        print(f"multisession overlay PoC failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
