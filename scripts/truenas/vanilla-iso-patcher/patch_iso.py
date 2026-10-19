#!/usr/bin/env python3
"""Build a TrueNAS installer ISO with a bounded boot-pool partition."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tokenize

from iso_profiles import (
    FLAVORS,
    GRUB_CONFIG_PATHS,
    ProfileError,
    install_profile,
    patch_grub_config,
)

FILESYSTEM_PATH = "/live/filesystem.squashfs"
INSTALLER_PATH = Path("usr/lib/python3/dist-packages/truenas_installer/install.py")
MIN_BOOT_POOL_GIB = 16
MAX_BOOT_POOL_GIB = 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ISO_EXTENT_RE = re.compile(
    r"^File data lba:\s*\d+\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']+)'\s*$",
    re.MULTILINE,
)
ISO_SECTOR_BYTES = 2048


class PatcherError(RuntimeError):
    """An input ISO or one of its installer components failed validation."""


@dataclass(frozen=True)
class IsoExtent:
    """One ISO9660 data extent, including its allocated and recorded lengths."""

    byte_offset: int
    allocated_bytes: int
    file_bytes: int


def parse_single_iso_extent(report: str, iso_path: str) -> IsoExtent:
    """Parse exactly one xorriso ``report_lba`` record for an ISO path."""
    records = [
        (int(lba), int(blocks), int(file_bytes))
        for lba, blocks, file_bytes, reported_path in ISO_EXTENT_RE.findall(report)
        if reported_path == iso_path
    ]
    if len(records) != 1:
        raise PatcherError(
            f"expected one contiguous ISO extent for {iso_path}; found {len(records)}"
        )
    lba, blocks, file_bytes = records[0]
    if blocks <= 0 or file_bytes <= 0 or file_bytes > blocks * ISO_SECTOR_BYTES:
        raise PatcherError(f"xorriso reported an invalid ISO extent for {iso_path}")
    return IsoExtent(
        byte_offset=lba * ISO_SECTOR_BYTES,
        allocated_bytes=blocks * ISO_SECTOR_BYTES,
        file_bytes=file_bytes,
    )


def checked(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise PatcherError(f"could not run {argv[0]!r}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 6000:
            detail = detail[-6000:]
        raise PatcherError(
            f"command failed with exit status {result.returncode}: {argv[0]}\n{detail}"
        )
    return result


def validate_boot_pool_size(size_gib: int) -> None:
    if not MIN_BOOT_POOL_GIB <= size_gib <= MAX_BOOT_POOL_GIB:
        raise PatcherError(
            f"boot-pool size must be an integer from {MIN_BOOT_POOL_GIB} to "
            f"{MAX_BOOT_POOL_GIB} GiB"
        )


def _partition3_argument(source: str) -> tokenize.TokenInfo:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PatcherError(f"TrueNAS installer source is not valid Python: {exc}") from exc

    matching_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "run":
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        arguments = [item.value for item in node.args[0].elts if isinstance(item, ast.Constant)]
        if "sgdisk" in arguments and "-n3:0:0" in arguments and "-t3:BF01" in arguments:
            matching_calls.append(node)

    if len(matching_calls) != 1:
        raise PatcherError(
            "expected one installer sgdisk call that creates partition 3 as the "
            f"TrueNAS ZFS partition; found {len(matching_calls)}"
        )

    target = matching_calls[0]
    candidates: list[tokenize.TokenInfo] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(token.string)
            except (SyntaxError, ValueError):
                continue
            if value == "-n3:0:0":
                candidates.append(token)
    except tokenize.TokenError as exc:
        raise PatcherError(f"could not tokenize TrueNAS installer source: {exc}") from exc

    if len(candidates) != 1:
        raise PatcherError(
            "expected one literal '-n3:0:0' in the installer source; "
            f"found {len(candidates)}"
        )
    token = candidates[0]
    if not target.lineno <= token.start[0] <= (target.end_lineno or target.lineno):
        raise PatcherError("the partition-3 size literal is outside the validated sgdisk call")
    return token


def patch_installer_source(source: str, size_gib: int) -> str:
    """Replace the single whole-disk partition-3 extent in known installer code."""
    validate_boot_pool_size(size_gib)
    token = _partition3_argument(source)
    lines = source.splitlines(keepends=True)
    row = token.start[0] - 1
    lines[row] = (
        lines[row][: token.start[1]]
        + f'"-n3:0:+{size_gib}G"'
        + lines[row][token.end[1] :]
    )
    patched = "".join(lines)
    try:
        compile(patched, "truenas_installer/install.py", "exec")
    except SyntaxError as exc:
        raise PatcherError(f"patched installer source is not valid Python: {exc}") from exc
    return patched


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _squashfs_info(path: Path) -> tuple[str, int]:
    output = checked(["unsquashfs", "-s", str(path)]).stdout
    compression = re.search(r"^Compression (\w+)$", output, re.MULTILINE)
    block_size = re.search(r"^Block size (\d+)$", output, re.MULTILINE)
    if compression is None or block_size is None:
        raise PatcherError("could not read compression and block size from live filesystem.squashfs")
    return compression.group(1), int(block_size.group(1))


def _extract_iso_file(iso: Path, iso_path: str, destination: Path) -> None:
    checked(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(iso),
            "-extract",
            iso_path,
            str(destination),
        ]
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise PatcherError(f"ISO does not contain a readable {iso_path}")


def _check_boot_entries(iso: Path) -> str:
    report = checked(["xorriso", "-indev", str(iso), "-report_el_torito", "plain"])
    output = report.stdout + report.stderr
    for platform in ("BIOS", "UEFI"):
        if not re.search(rf"El Torito boot img\s*:\s*\d+\s+{platform}\b", output):
            raise PatcherError(f"output ISO is missing its {platform} El Torito boot entry")
    return output


def _squashfs_extent(iso: Path) -> IsoExtent:
    return _iso_file_extent(iso, FILESYSTEM_PATH)


def _iso_file_extent(iso: Path, iso_path: str) -> IsoExtent:
    report = checked(["xorriso", "-indev", str(iso), "-find", iso_path, "-exec", "report_lba", "--"])
    return parse_single_iso_extent(report.stdout + report.stderr, iso_path)


def _replace_extent(iso: Path, extent: IsoExtent, replacement: Path) -> None:
    """Replace one embedded ISO file without moving any other ISO bytes."""
    replacement_bytes = replacement.stat().st_size
    if replacement_bytes > extent.file_bytes:
        raise PatcherError(
            "rebuilt filesystem.squashfs is larger than its existing ISO file record "
            f"({replacement_bytes} > {extent.file_bytes} bytes)"
        )
    if extent.byte_offset + extent.allocated_bytes > iso.stat().st_size:
        raise PatcherError("filesystem.squashfs extent extends beyond the ISO file")

    with iso.open("r+b") as output, replacement.open("rb") as source:
        output.seek(extent.byte_offset)
        remaining = replacement_bytes
        while remaining:
            chunk = source.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                raise PatcherError("rebuilt filesystem.squashfs was truncated while copying")
            output.write(chunk)
            remaining -= len(chunk)
        padding = extent.file_bytes - replacement_bytes
        zeroes = bytes(1024 * 1024)
        while padding:
            chunk = zeroes[: min(len(zeroes), padding)]
            output.write(chunk)
            padding -= len(chunk)
        output.flush()
        os.fsync(output.fileno())


def patch_iso(
    input_iso: Path,
    output_iso: Path,
    size_gib: int,
    expected_sha256: str,
    flavor: str = "bounded",
    post_install_action: str = "reboot",
) -> dict[str, str | int]:
    validate_boot_pool_size(size_gib)
    if flavor not in FLAVORS:
        raise PatcherError(f"ISO flavor must be one of: {', '.join(FLAVORS)}")
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise PatcherError("expected input SHA-256 must contain exactly 64 hexadecimal digits")
    expected_sha256 = expected_sha256.lower()
    input_iso = input_iso.expanduser().resolve(strict=True)
    output_iso = output_iso.expanduser().resolve(strict=False)
    if not input_iso.is_file() or input_iso.stat().st_size == 0:
        raise PatcherError(f"input ISO is not a non-empty regular file: {input_iso}")
    if input_iso == output_iso:
        raise PatcherError("input and output ISO paths must differ")
    if output_iso.exists():
        raise PatcherError(f"output already exists; choose a new path: {output_iso}")
    if not output_iso.parent.is_dir():
        raise PatcherError(f"output directory does not exist: {output_iso.parent}")
    if os.geteuid() != 0:
        raise PatcherError(
            "run as root (for example, with sudo) so SquashFS ownership, device nodes, "
            "and extended attributes are preserved"
        )

    # Authenticate the exact caller-selected base before starting any extraction
    # or rebuild work. The expected value must come from the official sidecar or
    # another source the operator trusts.
    source_sha256 = _sha256(input_iso)
    if not hmac.compare_digest(source_sha256, expected_sha256):
        raise PatcherError(
            f"input ISO SHA-256 mismatch: expected {expected_sha256}, got {source_sha256}"
        )

    for tool in ("xorriso", "unsquashfs", "mksquashfs"):
        if shutil.which(tool) is None:
            raise PatcherError(f"required tool {tool!r} is not installed or not on PATH")

    with tempfile.TemporaryDirectory(prefix=".vanilla-iso-patcher-", dir=output_iso.parent) as temp_name:
        temp = Path(temp_name)
        source_squashfs = temp / "filesystem.squashfs"
        extracted_root = temp / "live-root"
        rebuilt_squashfs = temp / "filesystem.patched.squashfs"
        staged_iso = temp / "patched.iso"
        verified_squashfs = temp / "filesystem.verified.squashfs"

        # Keep a byte-identical source copy as the output scaffold. Rebuilding
        # the ISO filesystem tree with xorriso moves the replaced SquashFS to a
        # new extent and makes binary deltas nearly the size of the whole ISO.
        shutil.copyfile(input_iso, staged_iso)
        if not hmac.compare_digest(_sha256(staged_iso), source_sha256):
            raise PatcherError("input ISO changed while the private build copy was being made")
        extent = _squashfs_extent(staged_iso)
        if extent.byte_offset + extent.allocated_bytes > staged_iso.stat().st_size:
            raise PatcherError("filesystem.squashfs extent extends beyond the ISO file")

        _extract_iso_file(staged_iso, FILESYSTEM_PATH, source_squashfs)
        compression, block_size = _squashfs_info(source_squashfs)
        checked(
            [
                "unsquashfs",
                "-no-progress",
                "-d",
                str(extracted_root),
                str(source_squashfs),
            ]
        )

        installer_path = extracted_root / INSTALLER_PATH
        if not installer_path.is_file():
            raise PatcherError(f"ISO live filesystem has no installer source at {INSTALLER_PATH}")
        original_source = installer_path.read_text(encoding="utf-8")
        patched_source = patch_installer_source(original_source, size_gib)
        installer_path.write_text(patched_source, encoding="utf-8")

        try:
            release_version = (extracted_root / "etc/version").read_text(encoding="utf-8").strip()
            profile_info = install_profile(
                extracted_root,
                flavor,
                size_gib,
                release_version,
                post_install_action=post_install_action,
            )
        except (OSError, ProfileError) as exc:
            raise PatcherError(f"cannot prepare {flavor} installer flavor: {exc}") from exc

        checked(
            [
                "mksquashfs",
                str(extracted_root),
                str(rebuilt_squashfs),
                "-noappend",
                "-comp",
                compression,
                "-b",
                str(block_size),
                "-no-progress",
            ]
        )
        if rebuilt_squashfs.stat().st_size > extent.file_bytes and compression == "xz":
            # Some unattended bundles do not fit the vanilla ISO's exact
            # SquashFS file record with mksquashfs defaults. XZ's x86 BCJ
            # option tests the unfiltered and x86-filtered streams and keeps
            # the smaller result without changing the decompressor family.
            bcj_squashfs = temp / "filesystem.patched-bcj.squashfs"
            checked(
                [
                    "mksquashfs",
                    str(extracted_root),
                    str(bcj_squashfs),
                    "-noappend",
                    "-comp",
                    compression,
                    "-b",
                    str(block_size),
                    "-Xbcj",
                    "x86",
                    "-no-progress",
                ]
            )
            if bcj_squashfs.stat().st_size < rebuilt_squashfs.stat().st_size:
                rebuilt_squashfs = bcj_squashfs
        rebuilt_source = checked(
            [
                "unsquashfs",
                "-cat",
                str(rebuilt_squashfs),
                INSTALLER_PATH.as_posix(),
            ]
        ).stdout
        if rebuilt_source != patched_source:
            raise PatcherError("rebuilt SquashFS does not contain the expected patched installer")

        _replace_extent(staged_iso, extent, rebuilt_squashfs)
        if flavor != "bounded":
            for grub_path in GRUB_CONFIG_PATHS:
                grub_extent = _iso_file_extent(staged_iso, grub_path)
                extracted_grub = temp / f"grub-{hashlib.sha256(grub_path.encode()).hexdigest()[:12]}.cfg"
                _extract_iso_file(staged_iso, grub_path, extracted_grub)
                if extracted_grub.stat().st_size != grub_extent.file_bytes:
                    raise PatcherError(f"ISO file record length changed for {grub_path}")
                try:
                    patched_grub = patch_grub_config(
                        extracted_grub.read_bytes(),
                        grub_extent.file_bytes,
                    )
                except ProfileError as exc:
                    raise PatcherError(f"cannot patch {grub_path}: {exc}") from exc
                staged_grub = temp / f"patched-{extracted_grub.name}"
                staged_grub.write_bytes(patched_grub)
                _replace_extent(staged_iso, grub_extent, staged_grub)
        _check_boot_entries(staged_iso)
        _extract_iso_file(staged_iso, FILESYSTEM_PATH, verified_squashfs)
        verified_source = checked(
            [
                "unsquashfs",
                "-cat",
                str(verified_squashfs),
                INSTALLER_PATH.as_posix(),
            ]
        ).stdout
        if verified_source != patched_source:
            raise PatcherError("output ISO does not contain the expected patched installer")

        if flavor != "bounded":
            try:
                profile_text = checked(
                    [
                        "unsquashfs",
                        "-cat",
                        str(verified_squashfs),
                        "usr/local/lib/truenas-vanilla-iso/profile.json",
                    ]
                ).stdout
                profile = json.loads(profile_text)
            except (PatcherError, json.JSONDecodeError) as exc:
                raise PatcherError(f"output ISO has an invalid unattended profile: {exc}") from exc
            if (
                profile.get("flavor") != flavor
                or profile.get("boot_pool_size_gib") != size_gib
                or profile.get("post_install_action") != post_install_action
            ):
                raise PatcherError("output ISO unattended profile does not match the requested flavor")
            for name in ("dispatch.py", "autoinstall.py", "safety.py", "relaxation.py", "unified_patch.py"):
                runtime_path = f"usr/local/lib/truenas-vanilla-iso/{name}"
                packaged = checked(["unsquashfs", "-cat", str(verified_squashfs), runtime_path]).stdout
                if packaged != (Path(__file__).resolve().parent / "runtime" / name).read_text(encoding="utf-8"):
                    raise PatcherError(f"output ISO runtime component differs from source: {runtime_path}")
            dropin_path = "etc/systemd/system/truenas-installer.service.d/10-vanilla-iso-patcher.conf"
            if not checked(["unsquashfs", "-cat", str(verified_squashfs), dropin_path]).stdout.startswith(
                "[Service]\nExecStart=\n"
            ):
                raise PatcherError("output ISO is missing the unattended service dispatcher override")
            for grub_path in GRUB_CONFIG_PATHS:
                verified_grub = temp / f"verified-{hashlib.sha256(grub_path.encode()).hexdigest()[:12]}.cfg"
                _extract_iso_file(staged_iso, grub_path, verified_grub)
                if b"truenas.autoinstall=1" not in verified_grub.read_bytes():
                    raise PatcherError(f"output ISO is missing the unattended boot flag in {grub_path}")
            if flavor == "unattended-relaxed" and profile.get("relaxation_adapter") != profile_info["adapter_id"]:
                raise PatcherError("output ISO does not contain the requested pinned relaxation adapter")

        try:
            os.link(staged_iso, output_iso)
        except FileExistsError as exc:
            raise PatcherError(f"output appeared during patching; refusing to overwrite: {output_iso}") from exc

    return {
        "input_iso": str(input_iso),
        "output_iso": str(output_iso),
        "boot_pool_partition_gib": size_gib,
        "flavor": flavor,
        "post_install_action": post_install_action,
        "release_version": release_version,
        "relaxation_adapter": str(profile_info["adapter_id"] or ""),
        "source_sha256": source_sha256,
        "output_bytes": output_iso.stat().st_size,
        "output_sha256": _sha256(output_iso),
    }
