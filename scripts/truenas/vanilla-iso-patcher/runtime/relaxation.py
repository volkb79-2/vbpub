"""Apply a pinned experimental adapter to the newly installed boot environment."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from unified_patch import UnifiedPatchError, apply_patch_batch


RUNTIME_DIR = Path("/usr/local/lib/truenas-vanilla-iso")
ADAPTERS_DIR = RUNTIME_DIR / "adapters"
BOOT_POOL = "boot-pool"
DIST_PACKAGES = PurePosixPath("lib/python3/dist-packages")


class RelaxationError(RuntimeError):
    """The post-install adapter did not match or could not be applied safely."""


def _run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RelaxationError(f"command failed ({result.returncode}): {' '.join(argv)}\n{detail}")
    return result


def _adapter(adapter_id: str) -> tuple[Path, dict[str, Any]]:
    if not adapter_id or "/" in adapter_id or ".." in adapter_id:
        raise RelaxationError("invalid adapter id in ISO profile")
    directory = ADAPTERS_DIR / adapter_id
    try:
        metadata = json.loads((directory / "adapter.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RelaxationError(f"cannot read adapter metadata: {exc}") from exc
    if metadata.get("id") != adapter_id or metadata.get("schema_version") != 1:
        raise RelaxationError("adapter metadata identity or schema is invalid")
    try:
        rows = (directory / "bundle.sha256").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise RelaxationError(f"cannot read adapter bundle fingerprints: {exc}") from exc
    if not rows:
        raise RelaxationError("adapter bundle fingerprint list is empty")
    for row in rows:
        fields = row.split(maxsplit=1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise RelaxationError("adapter bundle fingerprint list is malformed")
        filename = fields[1].lstrip(" *")
        if not filename or "/" in filename or ".." in filename:
            raise RelaxationError("adapter bundle fingerprint has an unsafe filename")
        source = directory / filename
        if source.is_symlink() or not source.is_file():
            raise RelaxationError(f"adapter bundle file is missing: {filename}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != fields[0].lower():
            raise RelaxationError(f"adapter bundle fingerprint mismatch: {filename}")
    return directory, metadata


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RelaxationError(f"unsafe adapter path: {value!r}")
    return path


def _verify_target_hashes(adapter_dir: Path, usr_root: Path, metadata: dict[str, Any]) -> None:
    manifest_name = str(metadata.get("target_hash_manifest") or "")
    if not manifest_name or "/" in manifest_name or ".." in manifest_name:
        raise RelaxationError("invalid adapter target hash manifest path")
    try:
        rows = (adapter_dir / manifest_name).read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise RelaxationError(f"cannot read target hash manifest: {exc}") from exc
    if not rows:
        raise RelaxationError("target hash manifest is empty")

    seen: set[PurePosixPath] = set()
    for row in rows:
        fields = row.split(maxsplit=1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise RelaxationError("target hash manifest contains a malformed entry")
        try:
            bytes.fromhex(fields[0])
        except ValueError as exc:
            raise RelaxationError("target hash manifest contains a malformed SHA-256") from exc
        relative = _safe_relative(fields[1].lstrip(" *"))
        if relative in seen:
            raise RelaxationError(f"duplicate target hash entry: {relative}")
        seen.add(relative)
        target = usr_root.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file():
            raise RelaxationError(f"adapter target is not a regular file: {relative}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != fields[0].lower():
            raise RelaxationError(f"installed release fingerprint mismatch: {relative}")


def _write_atomically(target: Path, payload: bytes, mode: int, uid: int, gid: int) -> None:
    temporary = target.with_name(f".{target.name}.truenas-vanilla-{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _store_transaction(usr_root: Path, changed: dict[PurePosixPath, str], helpers: dict[str, str], adapter_dir: Path):
    pending: dict[Path, tuple[bytes | None, int, int, int]] = {}
    payloads: dict[Path, bytes] = {}
    for relative, content in changed.items():
        target = usr_root.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file():
            raise RelaxationError(f"patch target changed before write: {relative}")
        metadata = target.stat(follow_symlinks=False)
        pending[target] = (target.read_bytes(), metadata.st_mode & 0o7777, metadata.st_uid, metadata.st_gid)
        payloads[target] = content.encode("utf-8")

    for source_name, destination_name in helpers.items():
        source = adapter_dir / source_name
        relative = _safe_relative(destination_name)
        target = usr_root.joinpath(*relative.parts)
        if target.exists() or target.is_symlink() or not target.parent.is_dir():
            raise RelaxationError(f"adapter helper destination is occupied or missing: {relative}")
        pending[target] = (None, 0o644, 0, 0)
        payloads[target] = source.read_bytes()

    committed: list[Path] = []
    try:
        for target, payload in payloads.items():
            original, mode, uid, gid = pending[target]
            _write_atomically(target, payload, mode, uid, gid)
            committed.append(target)
    except BaseException:
        for target in reversed(committed):
            original, mode, uid, gid = pending[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomically(target, original, mode, uid, gid)
        raise
    return pending


def _rollback(backup: dict[Path, tuple[bytes | None, int, int, int]]) -> None:
    for target, (original, mode, uid, gid) in reversed(list(backup.items())):
        if original is None:
            target.unlink(missing_ok=True)
        else:
            _write_atomically(target, original, mode, uid, gid)


def _remove_generated_bytecode(usr_root: Path, changed: dict[PurePosixPath, str], helpers: dict[str, str]) -> None:
    relative_paths = set(changed)
    relative_paths.update(_safe_relative(value) for value in helpers.values())
    for relative in relative_paths:
        target = usr_root.joinpath(*relative.parts)
        cache = target.parent / "__pycache__"
        if not cache.is_dir():
            continue
        for bytecode in cache.glob(target.stem + ".*.pyc"):
            bytecode.unlink(missing_ok=True)


def _verify_release(root_dataset: str, expected_version: str) -> None:
    """Read /etc/version from its separate ZFS dataset in a private mount namespace.

    SCALE installs ``/etc`` as a child dataset of the boot environment. The
    parent root dataset therefore has an empty /etc mountpoint until that
    child dataset is mounted. Mounting it at the live installer's /etc would
    hide the installer environment, so keep that mount confined to an
    unshared namespace.
    """
    etc_dataset = f"{root_dataset}/etc"
    listed = _run(
        ["zfs", "list", "-H", "-o", "name", "-t", "filesystem", etc_dataset],
        check=False,
    )
    if listed.returncode or listed.stdout.strip() != etc_dataset:
        raise RelaxationError("installed boot environment has no separate etc dataset")

    namespace_script = (
        "set -eu; "
        "mount --make-rprivate /; "
        "trap 'zfs unmount \"$1\"' EXIT; "
        "zfs mount \"$1\"; "
        "cat /etc/version"
    )
    result = _run(
        [
            "unshare",
            "--mount",
            "--propagation",
            "private",
            "--",
            "sh",
            "-c",
            namespace_script,
            "verify-installed-release",
            etc_dataset,
        ]
    )
    actual = result.stdout.strip()
    if actual != expected_version:
        raise RelaxationError(
            f"installed release {actual!r} does not match adapter release {expected_version!r}"
        )


def _cleanup_imported_pool(
    *,
    imported: bool,
    usr_was_mounted: bool,
    usr_mount: Path | None,
    usr_dataset: str | None,
    original_readonly: str | None,
    root_was_mounted: bool,
    root_mount: Path | None,
) -> None:
    """Attempt every cleanup step, but fail if any pool state remains uncertain."""
    errors: list[str] = []

    def attempt(label: str, operation) -> None:
        try:
            operation()
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    if usr_was_mounted and usr_mount is not None and usr_mount.is_mount():
        attempt("unmount installed /usr", lambda: _run(["umount", str(usr_mount)]))
    if usr_dataset and original_readonly in {"on", "off"}:
        attempt(
            "restore installed /usr readonly property",
            lambda: _run(["zfs", "set", f"readonly={original_readonly}", usr_dataset]),
        )
    if root_was_mounted and root_mount is not None and root_mount.is_mount():
        attempt("unmount installed boot environment", lambda: _run(["umount", str(root_mount)]))
    if imported:
        attempt("export boot-pool", lambda: _run(["zpool", "export", "-f", BOOT_POOL]))
    if root_mount is not None and not root_mount.is_mount():
        attempt("remove temporary mountpoint", root_mount.rmdir)

    if errors:
        raise RelaxationError("boot-pool cleanup failed: " + "; ".join(errors))


def apply_relaxation(adapter_id: str, expected_version: str, progress) -> None:
    adapter_dir, adapter = _adapter(adapter_id)
    if adapter.get("release_version") != expected_version or not adapter.get("experimental"):
        raise RelaxationError("ISO profile and experimental adapter release pins disagree")
    helpers = adapter.get("helper_files")
    if not isinstance(helpers, dict) or not helpers:
        raise RelaxationError("adapter helper file map is empty")

    active = _run(["zpool", "list", "-H", "-o", "name", BOOT_POOL], check=False)
    if active.returncode == 0 and active.stdout.strip() == BOOT_POOL:
        raise RelaxationError("boot-pool is unexpectedly imported after installer completion")

    _run(["zpool", "import", "-N", "-f", BOOT_POOL])
    imported = True
    root_mount: Path | None = None
    usr_dataset: str | None = None
    usr_was_mounted = False
    root_was_mounted = False
    original_readonly: str | None = None
    usr_mount: Path | None = None
    backup: dict[Path, tuple[bytes | None, int, int, int]] = {}
    changed: dict[PurePosixPath, str] = {}

    try:
        bootfs = _run(["zpool", "get", "-H", "-o", "value", "bootfs", BOOT_POOL]).stdout.strip()
        expected_root_prefix = f"{BOOT_POOL}/ROOT/"
        if not bootfs.startswith(expected_root_prefix) or bootfs.count("/") != 2:
            raise RelaxationError(f"boot-pool bootfs is not a single installed environment: {bootfs!r}")
        root_dataset = bootfs
        usr_dataset = f"{root_dataset}/usr"
        listed = _run(["zfs", "list", "-H", "-o", "name", "-t", "filesystem", usr_dataset], check=False)
        if listed.returncode or listed.stdout.strip() != usr_dataset:
            raise RelaxationError("installed boot environment has no separate usr dataset")

        root_mount = Path(tempfile.mkdtemp(prefix="truenas-unattended-root-", dir="/run"))
        _run(["mount", "-t", "zfs", "-o", "ro", root_dataset, str(root_mount)])
        root_was_mounted = True
        _verify_release(root_dataset, expected_version)
        usr_mount = root_mount / "usr"
        if not usr_mount.is_dir():
            raise RelaxationError("installed root environment has no usr mountpoint")

        original_readonly = _run(["zfs", "get", "-H", "-o", "value", "readonly", usr_dataset]).stdout.strip()
        if original_readonly not in {"on", "off"}:
            raise RelaxationError(f"unexpected usr readonly property: {original_readonly!r}")
        _run(["zfs", "set", "readonly=off", usr_dataset])
        # ``usr`` has a normal ZFS mountpoint property (for example /usr), not
        # ``legacy``. zfsutil allows mounting it at the temporary target path
        # without changing that property on the installed boot environment.
        _run(["mount", "-t", "zfs", "-o", "rw,zfsutil", usr_dataset, str(usr_mount)])
        usr_was_mounted = True

        dist_root = usr_mount.joinpath(*DIST_PACKAGES.parts)
        _verify_target_hashes(adapter_dir, dist_root, adapter)
        patch_texts = []
        patch_names = adapter.get("patches")
        if not isinstance(patch_names, list) or not patch_names:
            raise RelaxationError("adapter patch list is empty")
        for name in patch_names:
            if not isinstance(name, str) or "/" in name or ".." in name:
                raise RelaxationError("invalid patch filename in adapter metadata")
            patch_texts.append((adapter_dir / name).read_text(encoding="utf-8"))
        try:
            changed = apply_patch_batch(dist_root, patch_texts)
        except (OSError, UnicodeError, UnifiedPatchError) as exc:
            raise RelaxationError(f"release adapter did not apply exactly: {exc}") from exc

        backup = _store_transaction(dist_root, changed, helpers, adapter_dir)
        compile_paths = [
            "/usr/lib/python3/dist-packages/" + path.as_posix()
            for path in changed
        ] + [
            "/usr/lib/python3/dist-packages/" + _safe_relative(value).as_posix()
            for value in helpers.values()
        ]
        _run(["chroot", str(root_mount), "/usr/bin/python3", "-m", "py_compile", *compile_paths])
        _remove_generated_bytecode(dist_root, changed, helpers)
        progress(f"Applied experimental {adapter_id} boot-pool adapter to {root_dataset}")
    except BaseException:
        if backup:
            _rollback(backup)
            if usr_mount is not None:
                _remove_generated_bytecode(usr_mount.joinpath(*DIST_PACKAGES.parts), changed, helpers)
        raise
    finally:
        active_error = sys.exception()
        try:
            _cleanup_imported_pool(
                imported=imported,
                usr_was_mounted=usr_was_mounted,
                usr_mount=usr_mount,
                usr_dataset=usr_dataset,
                original_readonly=original_readonly,
                root_was_mounted=root_was_mounted,
                root_mount=root_mount,
            )
        except RelaxationError as cleanup_error:
            if active_error is None:
                raise
            active_error.add_note(str(cleanup_error))
