"""Run creation, owner manifests, leases, and workspace operations."""
from __future__ import annotations

import datetime as _datetime
import fcntl
import json
import logging
import os
import secrets
import socket
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Self

from .backends import CopyBackend, LinuxOps
from .detect import Area, Capabilities, _log_once, detect
from .errors import (
    BackendFailed,
    CowError,
    LeaseHeld,
    PathRefused,
    RootInvalid,
    SpaceRefused,
)
from .paths import mount_fstype, validate_name, within
from .space import SpaceEstimate, admit, available_bytes, pending_delete_bytes

_FORMAT = "cowtree-run/1"
_LOCK_FLAGS = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)


def _pid_starttime(pid: int) -> int:
    text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    closing = text.rfind(")")
    if closing < 0:
        raise OSError("malformed /proc/<pid>/stat")
    fields_after_comm = text[closing + 1:].split()
    try:
        return int(fields_after_comm[19])
    except (IndexError, ValueError) as exc:
        raise OSError("malformed /proc/<pid>/stat starttime") from exc


def _owner_identity() -> dict[str, int | str]:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        pidns = os.stat("/proc/self/ns/pid").st_ino
        return {
            "uid": os.geteuid(),
            "pid": os.getpid(),
            "pid_starttime": _pid_starttime(os.getpid()),
            "boot_id": boot_id,
            "pidns": pidns,
            "hostname": socket.gethostname(),
        }
    except OSError as exc:
        raise BackendFailed("owner-identity", exc.errno, "/proc", str(exc)) from exc


def _utc_now() -> tuple[str, str]:
    now = _datetime.datetime.now(_datetime.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.isoformat(timespec="seconds")


def _json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PathRefused(f"manifest values must be JSON serializable: {exc}") from exc


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while writing cowtree manifest")
        view = view[written:]


def _write_manifest(run_dir: str, document: dict[str, object], *, create: bool) -> None:
    temporary = os.path.join(run_dir, f".owner.json.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        _write_all(descriptor, _json_bytes(document))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        destination = os.path.join(run_dir, "owner.json")
        if create and os.path.lexists(destination):
            raise PathRefused("owner manifest unexpectedly already exists")
        os.replace(temporary, destination)
        directory = os.open(run_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise BackendFailed("write-manifest", exc.errno, run_dir, str(exc)) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _plain_fstype(path: str) -> str:
    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        return mount_fstype(os.path.realpath(path), text)
    except (OSError, RootInvalid):
        return "unknown"


def _estimate_dict(estimate: SpaceEstimate) -> dict[str, int]:
    return estimate.as_dict()


class Run:
    """An isolated tree namespace backed by one held flock lease."""

    def __init__(
        self,
        *,
        path: str,
        run_identity: tuple[int, int],
        trees_identity: tuple[int, int],
        run_id: str,
        tool: str,
        caps: Capabilities,
        backend,
        estimate: SpaceEstimate,
        lock_fd: int,
        manifest: dict[str, object],
        area: Area | None,
        plain_base: str | None,
        pending_device: int,
        holder: str,
        log: Callable[[str], None] | None,
    ):
        self.path = path
        self._trees_path = os.path.join(path, "trees")
        self._run_identity = run_identity
        self._trees_identity = trees_identity
        self.run_id = run_id
        self.tool = tool
        self.caps = caps
        self._backend = backend
        self._estimate = estimate
        self._lock_fd = lock_fd
        self._manifest = manifest
        self._area = area
        self._plain_base = plain_base
        self._pending_device = pending_device
        self._holder = holder
        self._log = log or logging.getLogger("cowtree").warning
        self._owned: list[str] = []
        self._owned_identities: dict[str, tuple[int, int]] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._reserved_bytes = estimate.needed(caps)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self.close()
        else:
            self._close(best_effort=True)
        return False

    def _ensure_open(self) -> None:
        if self._closed:
            raise PathRefused("cowtree run is already closed")

    def _target(self, name: str) -> str:
        validate_name(name)
        self._require_trees_directory()
        destination = os.path.join(self._trees_path, name)
        if os.path.lexists(destination):
            raise PathRefused(f"destination already exists: {name!r}")
        return destination

    def _require_trees_directory(self) -> None:
        try:
            run_info = os.lstat(self.path)
            info = os.lstat(self._trees_path)
        except OSError as exc:
            raise PathRefused(f"run trees directory is unavailable: {self._trees_path!r}") from exc
        if not stat.S_ISDIR(run_info.st_mode):
            raise PathRefused("run path must be a real directory")
        if not stat.S_ISDIR(info.st_mode):
            raise PathRefused("run trees path must be a real directory")
        if (run_info.st_dev, run_info.st_ino) != self._run_identity:
            raise PathRefused("run path identity changed while the lease was held")
        if (info.st_dev, info.st_ino) != self._trees_identity:
            raise PathRefused("run trees path identity changed while the lease was held")

    def _remember(self, path: str) -> None:
        if path not in self._owned:
            self._owned.append(path)

    def _record_identity(
        self,
        path: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if expected_identity is None:
            expected_identity = self._backend.created_identity(path)
            if expected_identity is None:
                raise PathRefused(f"backend did not record the created path identity: {path!r}")
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise PathRefused(f"run-owned path is unavailable: {path!r}") from exc
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise PathRefused(f"run-owned path must be a real directory or regular file: {path!r}")
        actual_identity = (info.st_dev, info.st_ino)
        if actual_identity != expected_identity:
            raise PathRefused(f"run-owned path identity changed during creation: {path!r}")
        self._owned_identities[path] = expected_identity
        self._backend.forget_created_identity(path)

    def _cleanup_failed_create(self, path: str) -> None:
        self._backend.cleanup_created(path)

    def _require_owned_identity(self, path: str) -> None:
        expected = self._owned_identities.get(path)
        if expected is None:
            raise PathRefused(f"run-owned path has no recorded identity: {path!r}")
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise PathRefused(f"run-owned path is unavailable: {path!r}") from exc
        if (info.st_dev, info.st_ino) != expected:
            raise PathRefused(f"run-owned path identity changed while the lease was held: {path!r}")

    def ingest_tree(self, src: str | os.PathLike[str], name: str) -> Path:
        with self._lock:
            self._ensure_open()
            source = os.path.abspath(os.fspath(src))
            if os.path.islink(source) or not os.path.isdir(source):
                raise PathRefused(f"ingest source must be a real directory: {source!r}")
            destination = self._target(name)
            if within(os.path.realpath(destination), os.path.realpath(source)):
                raise PathRefused("ingest destination must not be inside its source directory")
            self._remember(destination)
            try:
                self._backend.ingest_tree(source, destination)
                self._record_identity(destination)
            except Exception:
                self._cleanup_failed_create(destination)
                self._owned.remove(destination)
                self._owned_identities.pop(destination, None)
                raise
            return Path(destination)

    def ingest_file(self, src: str | os.PathLike[str], dst_name: str) -> Path:
        with self._lock:
            self._ensure_open()
            destination = self._target(dst_name)
            self._remember(destination)
            try:
                self._backend.ingest_file(os.fspath(src), destination)
                self._record_identity(destination)
            except Exception:
                self._cleanup_failed_create(destination)
                self._owned.remove(destination)
                self._owned_identities.pop(destination, None)
                raise
            return Path(destination)

    def make_tree(self, name: str, *, compression: str | None = None) -> Path:
        with self._lock:
            self._ensure_open()
            destination = self._target(name)
            self._remember(destination)
            try:
                self._backend.make_tree(destination, compression=compression)
                self._record_identity(destination)
            except Exception:
                self._cleanup_failed_create(destination)
                self._owned.remove(destination)
                self._owned_identities.pop(destination, None)
                raise
            return Path(destination)

    def _authorized_source(self, src: str | os.PathLike[str]) -> str:
        source = os.path.abspath(os.fspath(src))
        resolved = os.path.realpath(source)
        allowed = within(resolved, self._trees_path)
        if self._area is not None:
            allowed = allowed or within(resolved, self._area.shared_dir)
        if not allowed or resolved == os.path.realpath(self._trees_path):
            raise PathRefused(f"clone source must be inside this run or cowtree shared/: {source!r}")
        if os.path.islink(source) or not os.path.isdir(source):
            raise PathRefused(f"clone source must be a real directory: {source!r}")
        return source

    def clone_tree(self, src: str | os.PathLike[str], name: str) -> Path:
        with self._lock:
            self._ensure_open()
            source = self._authorized_source(src)
            destination = self._target(name)
            self._remember(destination)
            try:
                self._backend.clone_tree(source, destination)
                self._record_identity(destination)
            except Exception:
                self._cleanup_failed_create(destination)
                self._owned.remove(destination)
                self._owned_identities.pop(destination, None)
                raise
            return Path(destination)

    def clone_file(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        with self._lock:
            self._ensure_open()
            self._require_trees_directory()
            source = os.path.abspath(os.fspath(src))
            resolved = os.path.realpath(source)
            allowed_source = within(resolved, self._trees_path)
            if self._area is not None:
                allowed_source = allowed_source or within(resolved, self._area.shared_dir)
            if not allowed_source or os.path.islink(source):
                raise PathRefused(f"clone file source must be inside this run or cowtree shared/: {source!r}")
            destination = os.path.abspath(os.fspath(dst))
            if not within(destination, self._trees_path) or destination == self._trees_path:
                raise PathRefused(f"clone file destination must be inside this run: {destination!r}")
            if os.path.lexists(destination):
                raise PathRefused(f"clone file destination already exists: {destination!r}")
            parent = os.path.dirname(destination)
            if not os.path.isdir(parent) or not within(os.path.realpath(parent), self._trees_path):
                raise PathRefused(f"clone file destination parent must be a real directory: {parent!r}")
            relative_parent = os.path.relpath(parent, self._trees_path)
            current = self._trees_path
            if relative_parent != ".":
                for component in relative_parent.split(os.sep):
                    current = os.path.join(current, component)
                    try:
                        info = os.lstat(current)
                    except OSError as exc:
                        raise PathRefused(f"clone file destination path is unavailable: {current!r}") from exc
                    if not stat.S_ISDIR(info.st_mode):
                        raise PathRefused(f"clone file destination path has a symlink or non-directory component: {current!r}")
            relative_top = os.path.join(self._trees_path, os.path.relpath(destination, self._trees_path).split(os.sep, 1)[0])
            top_identity = None
            top_was_owned = relative_top in self._owned
            if top_was_owned:
                self._require_owned_identity(relative_top)
            elif relative_top != destination:
                top_info = os.lstat(relative_top)
                top_identity = (top_info.st_dev, top_info.st_ino)
            if relative_top == destination:
                self._remember(relative_top)
            try:
                self._backend.clone_file(source, destination)
                if relative_top == destination:
                    self._record_identity(relative_top)
                elif not top_was_owned:
                    self._record_identity(relative_top, expected_identity=top_identity)
                    self._record_created_file_identity(destination)
                    self._remember(relative_top)
                else:
                    self._record_created_file_identity(destination)
            except Exception:
                self._cleanup_failed_create(destination)
                if not top_was_owned:
                    self._owned_identities.pop(relative_top, None)
                if relative_top == destination and relative_top in self._owned:
                    self._owned.remove(relative_top)
                    self._owned_identities.pop(relative_top, None)
                raise

    def _record_created_file_identity(self, path: str) -> None:
        expected = self._backend.created_identity(path)
        if expected is None:
            raise PathRefused(f"backend did not record the created file identity: {path!r}")
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise PathRefused(f"run-owned file is unavailable: {path!r}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise PathRefused(f"run-owned file must be a regular file: {path!r}")
        if (info.st_dev, info.st_ino) != expected:
            raise PathRefused(f"run-owned file identity changed during creation: {path!r}")
        self._backend.forget_created_identity(path)

    def seal(self, tree: str | os.PathLike[str]) -> None:
        with self._lock:
            self._ensure_open()
            target = self._owned_tree(tree)
            try:
                self._backend.seal(target)
            except OSError as exc:
                raise BackendFailed("seal", exc.errno, target, str(exc)) from exc

    def delete_tree(self, tree: str | os.PathLike[str]) -> None:
        with self._lock:
            self._ensure_open()
            target = self._owned_tree(tree)
            try:
                if self.caps.backend == "btrfs":
                    from .gc import _allocated_bytes
                    allocated, _tree_device = _allocated_bytes(target)
                self._backend.delete_tree(target)
            except OSError as exc:
                raise BackendFailed("delete-tree", exc.errno, target, str(exc)) from exc
            self._owned.remove(target)
            self._owned_identities.pop(target, None)
            if self.caps.backend == "btrfs":
                from .space import record_pending_delete
                record_pending_delete(self._pending_device, allocated)

    def _owned_tree(self, value: str | os.PathLike[str]) -> str:
        self._require_trees_directory()
        target = os.path.abspath(os.fspath(value))
        if target not in self._owned or os.path.dirname(target) != self._trees_path:
            raise PathRefused(f"tree is not owned by this run: {target!r}")
        self._require_owned_identity(target)
        return target

    def reserve(self, extra: SpaceEstimate) -> None:
        with self._lock:
            self._ensure_open()
            self._require_trees_directory()
            target = self._area.runs_dir if self._area is not None else self._plain_base
            if target is None:
                raise RootInvalid("plain cowtree run has no space-admission directory")
            additional = extra.needed(self.caps)
            device = os.stat(target).st_dev
            pending = pending_delete_bytes(device) if self.caps.backend == "btrfs" else 0
            available = available_bytes(target)
            needed = self._reserved_bytes + additional + pending
            if needed > available:
                details = {
                    **self._estimate.as_dict(),
                    "additional_ingest_bytes": extra.ingest_bytes,
                    "additional_per_clone_bytes": extra.per_clone_bytes,
                    "additional_max_live_clones": extra.max_live_clones,
                }
                raise SpaceRefused(needed, available, details)
            self._reserved_bytes += additional
            self._manifest["reserved_bytes"] = self._reserved_bytes
            _write_manifest(self.path, self._manifest, create=False)

    def close(self) -> None:
        self._close(best_effort=False)

    def _close(self, *, best_effort: bool) -> None:
        with self._lock:
            if self._closed:
                return
            first_error: Exception | None = None
            try:
                self._require_trees_directory()
            except Exception as exc:  # noqa: BLE001 - refuse to delete paths after run identity changed.
                first_error = exc
            for target in list(reversed(self._owned)) if first_error is None else []:
                try:
                    self._require_owned_identity(target)
                    if self.caps.backend == "btrfs":
                        from .gc import _allocated_bytes
                        from .space import record_pending_delete
                        allocated, _tree_device = _allocated_bytes(target)
                    self._backend.delete_tree(target)
                    self._owned.remove(target)
                    self._owned_identities.pop(target, None)
                    if self.caps.backend == "btrfs":
                        record_pending_delete(self._pending_device, allocated)
                except Exception as exc:  # noqa: BLE001 - continue cleanup and release the lease.
                    if first_error is None:
                        first_error = exc
            if first_error is None:
                try:
                    os.rmdir(self._trees_path)
                except OSError as exc:
                    first_error = exc
            if first_error is None:
                for filename in ("owner.json", "owner.lock"):
                    try:
                        os.unlink(os.path.join(self.path, filename))
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        if first_error is None:
                            first_error = exc
            if first_error is None:
                try:
                    os.rmdir(self.path)
                except OSError as exc:
                    first_error = exc
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
            try:
                os.close(self._lock_fd)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
            self._closed = True
            if first_error is not None and not best_effort:
                if isinstance(first_error, CowError):
                    raise first_error
                err = first_error.errno if isinstance(first_error, OSError) else None
                raise BackendFailed("close", err, self.path, str(first_error)) from first_error
            if first_error is not None:
                self._log(f"cowtree: best-effort cleanup of {self.path!r} failed: {first_error}")


def open_run(
    tool: str,
    *,
    estimate: SpaceEstimate,
    labels: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    ops=None,
    log: Callable[[str], None] | None = None,
    plain_dir: str | os.PathLike[str] | None = None,
    plain_prefix: str | None = None,
    holder: str = "flock",
    external_ref: str | None = None,
) -> Run:
    validate_name(tool)
    if holder not in {"flock", "external"}:
        raise PathRefused("holder must be 'flock' or 'external'")
    if holder == "external" and (not isinstance(external_ref, str) or not external_ref):
        raise PathRefused("external holders require an explicit external_ref")
    if holder == "flock" and external_ref is not None:
        raise PathRefused("external_ref is only valid for an external holder")
    label_values = dict(labels or {})
    _json_bytes(label_values)
    area = detect(environ=environ, ops=ops, log=log)
    if area is None:
        operations = ops or LinuxOps()
        base = os.path.abspath(os.fspath(plain_dir)) if plain_dir is not None else tempfile.gettempdir()
        if not os.path.isdir(base) or os.path.islink(base):
            raise RootInvalid(f"plain directory must already exist and be a real directory: {base!r}")
        caps = Capabilities("copy", _plain_fstype(base), False, False, True, False, False)
        backend = CopyBackend(operations, log=log)
        admission_dir = base
    else:
        operations = area.ops
        base = area.root
        caps = area.caps
        backend = area.backend
        admission_dir = area.runs_dir
        from .gc import dead_flock_runs
        if dead_flock_runs(area.runs_dir):
            _log_once(
                f"cowtree: dead flock-owned runs exist under {area.runs_dir}; run cowtree gc",
                log,
            )
    device = os.stat(admission_dir).st_dev
    pending = pending_delete_bytes(device) if caps.backend == "btrfs" else 0
    admit(estimate, caps, admission_dir, pending_bytes=pending)
    timestamp, created_utc = _utc_now()
    run_id = f"{tool}-{timestamp}-{secrets.token_hex(8)}"
    if area is None:
        prefix = plain_prefix if plain_prefix is not None else f"cowtree-{tool}-"
        if not prefix or os.sep in prefix or "\x00" in prefix:
            raise PathRefused("plain_prefix must be a non-empty filename prefix")
        path = tempfile.mkdtemp(dir=base, prefix=f"{prefix}{timestamp}-{secrets.token_hex(8)}-")
    else:
        path = os.path.join(area.runs_dir, run_id)
        try:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
        except FileExistsError as exc:
            raise BackendFailed("allocate-run", exc.errno, path, "run id collision") from exc
        except OSError as exc:
            try:
                os.rmdir(path)
            except OSError:
                pass
            raise BackendFailed("allocate-run", exc.errno, path, str(exc)) from exc
    lock_path = os.path.join(path, "owner.lock")
    lock_fd = -1
    try:
        lock_fd = os.open(lock_path, _LOCK_FLAGS | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LeaseHeld(f"new cowtree run unexpectedly has a held lease: {run_id}") from exc
        manifest: dict[str, object] = {
            "format": _FORMAT,
            "run_id": run_id,
            "tool": tool,
            "labels": label_values,
            "backend": caps.backend,
            "api": 1,
            "created_utc": created_utc,
            "holder": holder,
            "external_ref": external_ref,
            "estimate": _estimate_dict(estimate),
            "owner": _owner_identity(),
        }
        _write_manifest(path, manifest, create=True)
        trees_path = os.path.join(path, "trees")
        os.mkdir(trees_path, 0o700)
        os.chmod(trees_path, 0o700)
        run_info = os.lstat(path)
        trees_info = os.lstat(trees_path)
    except Exception:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        try:
            import shutil
            shutil.rmtree(path)
        except OSError:
            pass
        raise
    return Run(
        path=path,
        run_identity=(run_info.st_dev, run_info.st_ino),
        trees_identity=(trees_info.st_dev, trees_info.st_ino),
        run_id=run_id,
        tool=tool,
        caps=caps,
        backend=backend,
        estimate=estimate,
        lock_fd=lock_fd,
        manifest=manifest,
        area=area,
        plain_base=base if area is None else None,
        pending_device=device,
        holder=holder,
        log=log,
    )
