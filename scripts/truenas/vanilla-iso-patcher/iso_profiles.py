"""Build-time validation and installation of the unattended ISO flavor."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any


FLAVORS = ("bounded", "unattended", "unattended-relaxed")
AUTOINSTALL_FLAG = "truenas.autoinstall=1"
RUNTIME_DIR = Path("usr/local/lib/truenas-vanilla-iso")
DROPIN_PATH = Path("etc/systemd/system/truenas-installer.service.d/10-vanilla-iso-patcher.conf")
RUNTIME_SOURCE = Path(__file__).resolve().parent / "runtime"
ADAPTERS_SOURCE = Path(__file__).resolve().parent / "adapters"
ADAPTERS_BY_VERSION = {
    "27.0.0-RC.1": "v27",
    "28.0.0-MASTER+20261014-230204": "v28-nightly-20261014",
}

GRUB_CONFIG_PATHS = ("/boot/grub/grub.cfg", "/EFI/debian/grub.cfg")
AUTOINSTALL_ENTRY = """menuentry --hotkey=u --id=truenas-unattended 'Unattended install (automatically erases target disks)' {
    load_video
    set background_color=black
    linux    /vmlinuz gfxpayload=text quiet nomodeset boot=live toram=filesystem.squashfs $gfx_console $serial_console noeject truenas.autoinstall=1
    initrd   /initrd.img
}"""


class ProfileError(RuntimeError):
    """An ISO does not satisfy the selected profile's compatibility contract."""


def _function_parameters(tree: ast.Module, function_name: str, *, asynchronous: bool) -> list[str] | None:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef if asynchronous else ast.FunctionDef)
        and node.name == function_name
    ]
    if len(matches) != 1:
        return None
    return [argument.arg for argument in matches[0].args.args]


def validate_unattended_installer(live_root: Path) -> dict[str, str]:
    """Verify the installer API surface used by our runtime helper."""
    paths = {
        "disks.py": Path("usr/lib/python3/dist-packages/truenas_installer/disks.py"),
        "install.py": Path("usr/lib/python3/dist-packages/truenas_installer/install.py"),
        "serial.py": Path("usr/lib/python3/dist-packages/truenas_installer/serial.py"),
    }
    trees: dict[str, ast.Module] = {}
    digests: dict[str, str] = {}
    for name, relative in paths.items():
        source_path = live_root / relative
        if not source_path.is_file():
            raise ProfileError(f"unattended installer is missing required module {relative}")
        source = source_path.read_text(encoding="utf-8")
        try:
            trees[name] = ast.parse(source)
        except SyntaxError as exc:
            raise ProfileError(f"cannot parse unattended installer module {relative}: {exc}") from exc
        digests[name] = hashlib.sha256(source.encode("utf-8")).hexdigest()

    disks_tree = trees["disks.py"]
    disk_classes = [
        node for node in disks_tree.body if isinstance(node, ast.ClassDef) and node.name == "Disk"
    ]
    if len(disk_classes) != 1:
        raise ProfileError("installer Disk model changed; unattended disk safety cannot be verified")
    disk_fields = {
        target.id
        for node in disk_classes[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(target := node.target, ast.Name)
    }
    if not {"name", "size", "model", "removable"}.issubset(disk_fields):
        raise ProfileError("installer Disk model lacks fields required by unattended safety checks")
    if _function_parameters(disks_tree, "list_disks", asynchronous=True) != []:
        raise ProfileError("installer list_disks API changed; refusing unattended build")

    install_parameters = _function_parameters(trees["install.py"], "install", asynchronous=True)
    if install_parameters != [
        "destination_disks",
        "wipe_disks",
        "set_pmbr",
        "authentication",
        "post_install",
        "sql",
        "callback",
    ]:
        raise ProfileError("installer install API changed; refusing unattended build")
    if _function_parameters(trees["serial.py"], "serial_sql", asynchronous=True) != []:
        raise ProfileError("installer serial_sql API changed; refusing unattended build")
    return digests


def patch_grub_config(
    original: bytes,
    file_bytes: int,
    *,
    timeout_seconds: int = 5,
    use_toram: bool = True,
) -> bytes:
    """Keep vanilla entries and add a default unattended entry in-place.

    ``use_toram=False`` is reserved for the multisession overlay experiment:
    live-boot's single-module toram setting bypasses its filesystem.module
    stacking support, while booting directly from the medium lets it mount
    each listed SquashFS layer.
    """
    try:
        text = original.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProfileError("GRUB config is not ASCII") from exc
    if AUTOINSTALL_FLAG in text or "truenas-unattended" in text:
        raise ProfileError("input ISO already has the unattended GRUB entry")
    if not re.search(r"menuentry[^\n]*--id=video\b", text):
        raise ProfileError("GRUB config no longer has the vanilla video installer entry")
    if not re.search(r"menuentry[^\n]*--id=serial\b", text):
        raise ProfileError("GRUB config no longer has the vanilla serial installer entry")
    toram_arg = "boot=live toram=filesystem.squashfs"
    if text.count(toram_arg) != 2 or "initrd   /initrd.img" not in text:
        raise ProfileError("GRUB installer kernel or initrd layout changed")

    if not use_toram:
        text = text.replace(toram_arg, "boot=live")

    # Dropping comment-only and blank lines makes room inside the existing
    # ISO9660 file record. Every executable vanilla GRUB line is retained.
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    try:
        play_index = lines.index("insmod play")
    except ValueError as exc:
        raise ProfileError("GRUB timeout section changed; cannot set unattended default safely") from exc
    lines[play_index:play_index] = [
        'set default="truenas-unattended"',
        f"set timeout={timeout_seconds}",
    ]

    entry_index = next(
        (index for index, line in enumerate(lines) if line == "if [ ! -f /.disk/info ]; then"),
        len(lines),
    )
    unattended_entry = AUTOINSTALL_ENTRY
    if not use_toram:
        unattended_entry = unattended_entry.replace("toram=filesystem.squashfs ", "")
    lines[entry_index:entry_index] = unattended_entry.splitlines()
    patched = ("\n".join(lines) + "\n").encode("ascii")
    if len(patched) > file_bytes:
        raise ProfileError(
            f"GRUB config needs {len(patched)} bytes but its ISO file record has only {file_bytes}"
        )
    # GRUB ignores trailing spaces. Keep the ISO9660 directory record length
    # unchanged so the generator can update the bytes without relocating data.
    return patched + b" " * (file_bytes - len(patched))


def _read_bundle(adapter_id: str) -> tuple[Path, dict[str, Any]]:
    directory = ADAPTERS_SOURCE / adapter_id
    try:
        metadata = json.loads((directory / "adapter.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read vendored adapter {adapter_id}: {exc}") from exc
    if metadata.get("id") != adapter_id or metadata.get("schema_version") != 1:
        raise ProfileError(f"vendored adapter {adapter_id} has invalid metadata")
    try:
        rows = (directory / "bundle.sha256").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ProfileError(f"vendored adapter {adapter_id} has no bundle fingerprint file") from exc
    if not rows:
        raise ProfileError(f"vendored adapter {adapter_id} fingerprint file is empty")
    seen: set[str] = set()
    for row in rows:
        fields = row.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise ProfileError(f"vendored adapter {adapter_id} has a malformed bundle fingerprint")
        filename = fields[1].lstrip(" *")
        if not filename or "/" in filename or ".." in filename or filename in seen:
            raise ProfileError(f"vendored adapter {adapter_id} has an unsafe or duplicate filename")
        seen.add(filename)
        source = directory / filename
        if source.is_symlink() or not source.is_file():
            raise ProfileError(f"vendored adapter {adapter_id} fingerprint source is missing: {filename}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != fields[0]:
            raise ProfileError(f"vendored adapter {adapter_id} fingerprint mismatch: {filename}")
    required = {
        "adapter.json",
        str(metadata.get("target_hash_manifest") or ""),
        *metadata.get("patches", []),
        *metadata.get("helper_files", {}).keys(),
    }
    if not required.issubset(seen):
        raise ProfileError(f"vendored adapter {adapter_id} fingerprint list omits required files")
    return directory, metadata


def _copy_adapter(live_root: Path, adapter_id: str, release_version: str) -> dict[str, Any]:
    source_dir, metadata = _read_bundle(adapter_id)
    if metadata.get("release_version") != release_version or not metadata.get("experimental"):
        raise ProfileError("vendored adapter release pin does not match the selected ISO")
    target_dir = live_root / RUNTIME_DIR / "adapters" / adapter_id
    target_dir.mkdir(parents=True, exist_ok=False)
    filenames = [
        "adapter.json",
        "bundle.sha256",
        str(metadata.get("target_hash_manifest") or ""),
        *metadata.get("patches", []),
        *metadata.get("helper_files", {}).keys(),
    ]
    if any(not name or "/" in name or ".." in name for name in filenames):
        raise ProfileError("adapter metadata contains an invalid filename")
    if len(filenames) != len(set(filenames)):
        raise ProfileError("adapter metadata repeats a bundle filename")
    for name in filenames:
        source = source_dir / name
        if source.is_symlink() or not source.is_file():
            raise ProfileError(f"vendored adapter file is missing or unsafe: {source}")
        shutil.copyfile(source, target_dir / name)
    return metadata


def install_profile(
    live_root: Path,
    flavor: str,
    size_gib: int,
    release_version: str,
    *,
    post_install_action: str = "reboot",
) -> dict[str, Any]:
    """Install runtime files and the version-pinned profile into SquashFS."""
    if flavor not in FLAVORS:
        raise ProfileError(f"unknown ISO flavor: {flavor}")
    if post_install_action not in {"reboot", "poweroff"}:
        raise ProfileError("post-install action must be reboot or poweroff")
    if flavor == "bounded":
        if post_install_action != "reboot":
            raise ProfileError("post-install action only applies to unattended flavors")
        return {
            "flavor": flavor,
            "release_version": release_version,
            "adapter_id": None,
            "post_install_action": post_install_action,
        }

    installer_hashes = validate_unattended_installer(live_root)
    profile: dict[str, Any] = {
        "schema_version": 1,
        "flavor": flavor,
        "boot_pool_size_gib": size_gib,
        "release_version": release_version,
        "administrator_username": "truenas_admin",
        "fixed_password": "truenas",
        "grub_timeout_seconds": 5,
        "set_pmbr": False,
        "installer_module_sha256": installer_hashes,
        "post_install_action": post_install_action,
        "relaxation_adapter": None,
    }

    runtime_root = live_root / RUNTIME_DIR
    runtime_root.mkdir(parents=True, exist_ok=False)

    if flavor == "unattended-relaxed":
        adapter_id = ADAPTERS_BY_VERSION.get(release_version)
        if adapter_id is None:
            raise ProfileError(
                "unattended-relaxed is pinned to TrueNAS 27.0.0-RC.1 or "
                "28.0.0-MASTER+20261014-230204"
            )
        adapter = _copy_adapter(live_root, adapter_id, release_version)
        profile["relaxation_adapter"] = adapter_id
        profile["adapter_experimental"] = bool(adapter.get("experimental"))
    else:
        adapter_id = None

    for name in ("dispatch.py", "autoinstall.py", "safety.py", "relaxation.py", "unified_patch.py"):
        source = RUNTIME_SOURCE / name
        if source.is_symlink() or not source.is_file():
            raise ProfileError(f"unattended runtime component is missing or unsafe: {source}")
        shutil.copyfile(source, runtime_root / name)
    (runtime_root / "profile.json").write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    dropin = live_root / DROPIN_PATH
    dropin.parent.mkdir(parents=True, exist_ok=True)
    if dropin.exists():
        raise ProfileError(f"input ISO already has a vanilla ISO patcher systemd override: {dropin}")
    dropin.write_text(
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=/usr/bin/python3 /usr/local/lib/truenas-vanilla-iso/dispatch.py\n"
        "StandardOutput=journal+console\n"
        "StandardError=journal+console\n"
        "Restart=no\n",
        encoding="utf-8",
    )
    return {
        "flavor": flavor,
        "release_version": release_version,
        "adapter_id": adapter_id,
        "post_install_action": post_install_action,
    }
