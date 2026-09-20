"""Neutral, fail-closed Git worktree and workspace identity management.

This module is deliberately small.  It owns facts that are true for every
consumer of an isolated checkout: Git-family discovery, canonical paths,
allocation locking, path-derived identity, records, leases, and the order in
which a checkout is cleaned up.  Product adapters may attach labels and opaque
metadata, but this module never interprets them.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


CURRENT_RECORD_VERSION = 1
WORKSPACE_RECORD_DIR = ".workspace-instances"
_LOCK_NAME = "workspace-instance.lock"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_LEASE_MODES = frozenset({"held", "perpetual"})
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"
_ID_SPACE = 36**6


class WorkspaceError(RuntimeError):
    """A workspace operation refused or could not complete."""

    def __init__(self, message: str, *, category: str = "workspace-error") -> None:
        super().__init__(message)
        self.category = category


class WorkspaceCollisionError(WorkspaceError):
    """A short path identity is already claimed by a different path."""

    def __init__(self, workspace_id: str, first: Path, second: Path) -> None:
        super().__init__(
            f"workspace identity collision for {workspace_id!r}: "
            f"{first} and {second}; refusing allocation",
            category="collision",
        )
        self.workspace_id = workspace_id
        self.first_path = first
        self.second_path = second


WorkspaceState = str


@dataclass(frozen=True)
class ResourceNamespace:
    """Opaque namespace facts an adapter may use for owned resources."""

    workspace_id: str
    labels: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"workspace_id": self.workspace_id, "labels": dict(self.labels)}


@dataclass(frozen=True)
class Lease:
    """An explicit bounded or perpetual ownership claim."""

    holder: str
    acquired_at_utc: str
    renewed_at_utc: str
    expires_at_utc: str | None
    mode: str

    def __post_init__(self) -> None:
        if not self.holder:
            raise WorkspaceError("lease holder must not be empty", category="invalid-record")
        if self.mode not in _LEASE_MODES:
            raise WorkspaceError(
                f"unknown lease mode {self.mode!r}; expected held or perpetual",
                category="invalid-record",
            )
        if self.mode == "held" and self.expires_at_utc is None:
            raise WorkspaceError(
                "held leases require expires_at_utc", category="invalid-record"
            )
        if self.mode == "perpetual" and self.expires_at_utc is not None:
            raise WorkspaceError(
                "perpetual leases must not have expires_at_utc",
                category="invalid-record",
            )
        for label, value in (
            ("acquired_at_utc", self.acquired_at_utc),
            ("renewed_at_utc", self.renewed_at_utc),
            ("expires_at_utc", self.expires_at_utc),
        ):
            if value is not None:
                _parse_timestamp(value, label=label)

    def as_dict(self) -> dict[str, Any]:
        return {
            "holder": self.holder,
            "acquired_at_utc": self.acquired_at_utc,
            "renewed_at_utc": self.renewed_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "mode": self.mode,
        }

    def to_dict(self) -> dict[str, Any]:
        """Compatibility spelling for adapter serializers."""
        return self.as_dict()


@dataclass(frozen=True)
class WorkspaceContext:
    """Typed facts for one selected or allocated checkout."""

    source_git_root: Path
    worktree_path: Path
    git_common_dir: Path
    physical_worktree_path: Path
    workspace_id: str
    branch: str
    base_commit: str
    record_path: Path
    namespace: ResourceNamespace

    @property
    def logical_worktree_path(self) -> Path:
        """Alias retained for callers that make the namespace explicit."""

        return self.worktree_path


@dataclass(frozen=True)
class InvocationContext:
    """Independent scope facts for a CLI invocation.

    ``cmru_root`` and ``ciu_root`` are adapter-owned selections.  The shared
    resolver only fills the Git/worktree fields and never guesses either
    product's configuration root.
    """

    invocation_dir: Path
    source_git_root: Path | None = None
    git_common_dir: Path | None = None
    worktree_path: Path | None = None
    cmru_root: Path | None = None
    ciu_root: Path | None = None
    physical_source_git_root: Path | None = None
    physical_worktree_path: Path | None = None


@dataclass(frozen=True)
class WorkspaceRecord:
    """Durable ownership record for a generic workspace."""

    workspace_id: str
    source_git_root: Path
    worktree_path: Path
    physical_worktree_path: Path
    git_common_dir: Path
    branch: str
    base_commit: str
    purpose: str
    state: WorkspaceState
    created_at_utc: str
    labels: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    lease: Lease | None = None
    record_version: int = CURRENT_RECORD_VERSION

    @property
    def record_path(self) -> Path:
        return _record_path(self.git_common_dir, self.workspace_id)

    @property
    def namespace(self) -> ResourceNamespace:
        return ResourceNamespace(self.workspace_id, dict(self.labels))

    def context(self) -> WorkspaceContext:
        return WorkspaceContext(
            source_git_root=self.source_git_root,
            worktree_path=self.worktree_path,
            git_common_dir=self.git_common_dir,
            physical_worktree_path=self.physical_worktree_path,
            workspace_id=self.workspace_id,
            branch=self.branch,
            base_commit=self.base_commit,
            record_path=self.record_path,
            namespace=self.namespace,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_version": self.record_version,
            "workspace_id": self.workspace_id,
            "source_git_root": str(self.source_git_root),
            "worktree_path": str(self.worktree_path),
            "physical_worktree_path": str(self.physical_worktree_path),
            "git_common_dir": str(self.git_common_dir),
            "branch": self.branch,
            "base_commit": self.base_commit,
            "purpose": self.purpose,
            "state": self.state,
            "created_at_utc": self.created_at_utc,
            "labels": dict(self.labels),
            "metadata": dict(self.metadata),
            "lease": self.lease.as_dict() if self.lease else None,
        }


def canonical_path(path: Path | str) -> Path:
    """Return an absolute lexical path with symlinks resolved when possible."""

    return Path(path).expanduser().resolve(strict=False)


def physical_path(
    logical_path: Path | str,
    *,
    logical_root: Path | str,
    physical_root: Path | str,
) -> Path:
    """Translate a logical path without probing the physical namespace.

    The result is a host/daemon path.  The caller must not run local
    ``exists()``/``is_file()`` checks on it; those belong to the owner of that
    namespace.  Paths outside the logical root are explicit external paths and
    pass through unchanged.
    """

    logical = canonical_path(logical_path)
    source = canonical_path(logical_root)
    target = canonical_path(physical_root)
    try:
        relative = logical.relative_to(source)
    except ValueError:
        return logical
    return target / relative


def _base36(value: int) -> str:
    if value < 0:
        raise ValueError("base36 value must be non-negative")
    if value == 0:
        return "0"
    chars: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        chars.append(_BASE36[remainder])
    return "".join(reversed(chars))


def workspace_id_for_path(path: Path | str) -> str:
    """Return the six-character lower-case base-36 path identity."""

    canonical = canonical_path(path)
    digest = hashlib.sha256(str(canonical).encode("utf-8")).digest()
    return _base36(int.from_bytes(digest, "big") % _ID_SPACE).zfill(6)


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise WorkspaceError(
            f"git {' '.join(args)} could not start in {cwd}: {exc}",
            category="git-error",
        ) from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkspaceError(
            f"git {' '.join(args)} failed ({result.returncode}): {detail}",
            category="git-error",
        )
    return result.stdout.strip()


def discover_git_context(path: Path | str) -> tuple[Path, Path, str, str]:
    """Return ``(top, common_dir, branch, head)`` for *path*."""

    cwd = canonical_path(path)
    if not cwd.is_dir():
        cwd = cwd.parent
    top = canonical_path(_git(cwd, "rev-parse", "--show-toplevel"))
    common_raw = Path(_git(top, "rev-parse", "--git-common-dir"))
    common = canonical_path(common_raw if common_raw.is_absolute() else top / common_raw)
    branch = _git(top, "branch", "--show-current")
    head = _git(top, "rev-parse", "HEAD")
    if not branch:
        branch = "HEAD"
    return top, common, branch, head


def resolve_invocation(
    invocation_dir: Path | str, *, root_folder: Path | str | None = None
) -> InvocationContext:
    """Resolve Git facts from cwd or an explicit containing root."""

    invocation = canonical_path(invocation_dir)
    selected = canonical_path(root_folder) if root_folder is not None else invocation
    top, common, _branch, _head = discover_git_context(selected)
    return InvocationContext(
        invocation_dir=invocation,
        source_git_root=top,
        git_common_dir=common,
        worktree_path=top,
        physical_source_git_root=top,
        physical_worktree_path=top,
    )


def _record_path(git_common_dir: Path, workspace_id: str) -> Path:
    return canonical_path(git_common_dir) / WORKSPACE_RECORD_DIR / f"{workspace_id}.json"


def workspace_lock(git_common_dir: Path | str) -> Iterator[None]:
    """Context manager body for the one Git-family allocation lock."""

    return _workspace_lock(canonical_path(git_common_dir))


@contextmanager
def _workspace_lock(git_common_dir: Path) -> Iterator[None]:
    lock_path = git_common_dir / _LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise WorkspaceError(
                f"cannot acquire workspace allocation lock {lock_path}: {exc}",
                category="lock-error",
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WorkspaceError(f"lease {label} is not a timestamp", category="invalid-record")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise WorkspaceError(
            f"lease {label} is not a timestamp: {value!r}",
            category="invalid-record",
        ) from exc
    if parsed.tzinfo is None:
        raise WorkspaceError(
            f"lease {label} has no UTC offset", category="invalid-record"
        )
    return parsed


def _lease(raw: Any) -> Lease | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkspaceError("lease must be an object or null", category="invalid-record")
    expected = {"holder", "acquired_at_utc", "renewed_at_utc", "expires_at_utc", "mode"}
    if set(raw) != expected:
        raise WorkspaceError(
            f"lease keys must be {sorted(expected)}", category="invalid-record"
        )
    return Lease(
        holder=raw["holder"],
        acquired_at_utc=raw["acquired_at_utc"],
        renewed_at_utc=raw["renewed_at_utc"],
        expires_at_utc=raw["expires_at_utc"],
        mode=raw["mode"],
    )


def _record(raw: Any, path: Path) -> WorkspaceRecord:
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{path} must contain one JSON object", category="invalid-record")
    required = {
        "record_version", "workspace_id", "source_git_root", "worktree_path",
        "physical_worktree_path", "git_common_dir", "branch", "base_commit",
        "purpose", "state", "created_at_utc", "labels", "metadata", "lease",
    }
    if set(raw) != required:
        raise WorkspaceError(
            f"{path} has unexpected workspace-record keys; expected {sorted(required)}",
            category="invalid-record",
        )
    if raw["record_version"] != CURRENT_RECORD_VERSION:
        raise WorkspaceError(
            f"unsupported workspace record version {raw['record_version']!r} in {path}",
            category="invalid-record",
        )
    if not isinstance(raw["workspace_id"], str) or not re.fullmatch(r"[0-9a-z]{6}", raw["workspace_id"]):
        raise WorkspaceError(f"invalid workspace_id in {path}", category="invalid-record")
    for key in ("labels", "metadata"):
        if not isinstance(raw[key], dict):
            raise WorkspaceError(f"{key} in {path} must be an object", category="invalid-record")
    for key in ("source_git_root", "worktree_path", "physical_worktree_path", "git_common_dir"):
        if not isinstance(raw[key], str) or not raw[key]:
            raise WorkspaceError(f"{key} in {path} must be a path", category="invalid-record")
    for key in ("branch", "base_commit", "purpose", "state", "created_at_utc"):
        if not isinstance(raw[key], str) or not raw[key]:
            raise WorkspaceError(
                f"{key} in {path} must be a non-empty string",
                category="invalid-record",
            )
    _parse_timestamp(raw["created_at_utc"], label="created_at_utc")
    return WorkspaceRecord(
        workspace_id=raw["workspace_id"],
        source_git_root=canonical_path(raw["source_git_root"]),
        worktree_path=canonical_path(raw["worktree_path"]),
        physical_worktree_path=canonical_path(raw["physical_worktree_path"]),
        git_common_dir=canonical_path(raw["git_common_dir"]),
        branch=raw["branch"],
        base_commit=raw["base_commit"],
        purpose=raw["purpose"],
        state=raw["state"],
        created_at_utc=raw["created_at_utc"],
        labels=dict(raw["labels"]),
        metadata=dict(raw["metadata"]),
        lease=_lease(raw["lease"]),
        record_version=raw["record_version"],
    )


def read_record(path: Path | str) -> WorkspaceRecord:
    record_path = canonical_path(path)
    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"workspace record does not exist: {record_path}", category="stale-record") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"workspace record is unreadable: {record_path}: {exc}", category="invalid-record") from exc
    record = _record(raw, record_path)
    if record.record_path != record_path:
        raise WorkspaceError(
            f"workspace record {record_path} does not match its Git-family identity",
            category="root-mismatch",
        )
    return record


def write_record(record: WorkspaceRecord) -> Path:
    path = record.record_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(record.as_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise WorkspaceError(f"could not write workspace record {path}: {exc}", category="record-write") from exc
    return path


def _records(git_common_dir: Path) -> list[WorkspaceRecord]:
    directory = git_common_dir / WORKSPACE_RECORD_DIR
    if not directory.exists():
        return []
    result: list[WorkspaceRecord] = []
    for path in sorted(directory.glob("*.json")):
        result.append(read_record(path))
    return result


def list_workspaces(git_common_dir: Path | str) -> list[WorkspaceRecord]:
    """Read every family record; malformed state refuses the whole inventory."""

    return _records(canonical_path(git_common_dir))


def _validate_branch(branch: str) -> None:
    if not branch or branch.startswith("/") or ".." in branch.split("/"):
        raise WorkspaceError(f"invalid branch name {branch!r}", category="invalid-input")
    if not _NAME_RE.fullmatch(branch):
        raise WorkspaceError(f"invalid branch name {branch!r}", category="invalid-input")


def _check_collision(records: Sequence[WorkspaceRecord], workspace_id: str, path: Path) -> None:
    for record in records:
        recorded_path = canonical_path(record.physical_worktree_path)
        if recorded_path == path:
            raise WorkspaceError(
                f"physical workspace path is already recorded: {path}",
                category="occupied",
            )
        if record.workspace_id == workspace_id:
            raise WorkspaceCollisionError(workspace_id, recorded_path, path)


def _record_for_path(records: Sequence[WorkspaceRecord], path: Path) -> WorkspaceRecord | None:
    for record in records:
        if canonical_path(record.worktree_path) == path:
            return record
    return None


def _coerce_record(record_or_path: WorkspaceRecord | WorkspaceContext | Path | str) -> WorkspaceRecord:
    """Resolve the durable record for any public workspace handle."""

    if isinstance(record_or_path, WorkspaceRecord):
        return record_or_path
    if isinstance(record_or_path, WorkspaceContext):
        return read_record(record_or_path.record_path)
    return read_record(record_or_path)


def _new_record(
    *,
    source_git_root: Path,
    worktree_path: Path,
    physical_worktree_path: Path,
    git_common_dir: Path,
    branch: str,
    base_commit: str,
    purpose: str,
    labels: Mapping[str, str],
    metadata: Mapping[str, Any],
    identity_path: Path | str | None = None,
    state: WorkspaceState = "ready",
    created_at: datetime | None = None,
) -> WorkspaceRecord:
    identity_source = canonical_path(identity_path or physical_worktree_path)
    record_metadata = dict(metadata)
    if identity_path is not None:
        record_metadata["workspace.identity_path"] = str(identity_source)
    return WorkspaceRecord(
        workspace_id=workspace_id_for_path(identity_source),
        source_git_root=canonical_path(source_git_root),
        worktree_path=canonical_path(worktree_path),
        physical_worktree_path=canonical_path(physical_worktree_path),
        git_common_dir=canonical_path(git_common_dir),
        branch=branch,
        base_commit=base_commit,
        purpose=purpose,
        state=state,
        created_at_utc=_stamp(created_at or _utc_now()),
        labels=dict(labels),
        metadata=record_metadata,
    )


def create_workspace(
    source_git_root: Path | str,
    target: Path | str,
    *,
    branch: str,
    base: str = "HEAD",
    purpose: str = "workspace",
    physical_target: Path | str | None = None,
    identity_path: Path | str | None = None,
    labels: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkspaceContext:
    """Allocate one linked checkout and its record under the family lock."""

    source = canonical_path(source_git_root)
    target_path = canonical_path(target)
    physical = canonical_path(physical_target or target_path)
    _validate_branch(branch)
    if not purpose or not _NAME_RE.fullmatch(purpose):
        raise WorkspaceError(f"invalid workspace purpose {purpose!r}", category="invalid-input")
    top, common, _current_branch, _head = discover_git_context(source)
    if top != source:
        source = top
    identity_source = canonical_path(identity_path or physical)
    identity = workspace_id_for_path(identity_source)
    with _workspace_lock(common):
        records = _records(common)
        _check_collision(records, identity, physical)
        if _record_for_path(records, target_path) is not None:
            raise WorkspaceError(f"workspace path is already recorded: {target_path}", category="occupied")
        if target_path.exists():
            raise WorkspaceError(f"workspace path already exists: {target_path}", category="occupied")
        branch_probe = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=source,
            text=True,
            capture_output=True,
            check=False,
        )
        if branch_probe.returncode == 0:
            raise WorkspaceError(f"workspace branch already exists: {branch}", category="occupied")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "worktree", "add", "--no-checkout", "-b", branch, str(target_path), base],
            cwd=source, text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise WorkspaceError(
                f"git worktree add failed ({result.returncode}): {(result.stderr or result.stdout).strip()}",
                category="git-error",
            )
        try:
            base_commit = _git(target_path, "rev-parse", "HEAD")
            record = _new_record(
                source_git_root=source,
                worktree_path=target_path,
                physical_worktree_path=physical,
                git_common_dir=common,
                branch=branch,
                base_commit=base_commit,
                purpose=purpose,
                labels=labels or {},
                metadata=metadata or {},
                identity_path=identity_path,
            )
            if record.workspace_id != identity:
                raise WorkspaceError("workspace identity changed during allocation", category="collision")
            write_record(record)
        except Exception:
            subprocess.run(["git", "worktree", "remove", "--force", str(target_path)], cwd=source, check=False)
            subprocess.run(["git", "branch", "-D", branch], cwd=source, check=False)
            raise
    return record.context()


def adopt_workspace(
    source_git_root: Path | str,
    target: Path | str,
    *,
    purpose: str = "workspace",
    physical_target: Path | str | None = None,
    identity_path: Path | str | None = None,
    labels: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkspaceContext:
    """Record an already allocated linked checkout without creating it."""

    source = canonical_path(source_git_root)
    target_path = canonical_path(target)
    physical = canonical_path(physical_target or target_path)
    top, common, branch, head = discover_git_context(target_path)
    if top != target_path:
        raise WorkspaceError(f"adopt target is not the worktree top level: {target_path}", category="root-mismatch")
    if (target_path / ".git").is_dir():
        raise WorkspaceError(
            f"adopt target is the primary checkout, not a linked worktree: {target_path}",
            category="root-mismatch",
        )
    source_top, source_common, _source_branch, _source_head = discover_git_context(source)
    if source_common != common:
        raise WorkspaceError("adopt target belongs to a different Git worktree family", category="root-mismatch")
    with _workspace_lock(common):
        records = _records(common)
        identity_source = canonical_path(identity_path or physical)
        identity = workspace_id_for_path(identity_source)
        _check_collision(records, identity, physical)
        if _record_for_path(records, target_path) is not None:
            raise WorkspaceError(f"workspace path is already recorded: {target_path}", category="occupied")
        record = _new_record(
            source_git_root=source_top,
            worktree_path=target_path,
            physical_worktree_path=physical,
            git_common_dir=common,
            branch=branch,
            base_commit=head,
            purpose=purpose,
            labels=labels or {},
            metadata=metadata or {},
            identity_path=identity_path,
            state="adopted",
        )
        write_record(record)
    return record.context()


def ensure_workspace(
    record_or_path: WorkspaceRecord | WorkspaceContext | Path | str,
    *,
    labels: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkspaceContext:
    """Resume a recorded checkout and fail closed on path/branch drift."""

    record = _coerce_record(record_or_path)
    with _workspace_lock(record.git_common_dir):
        current = read_record(record.record_path)
        if current.worktree_path != canonical_path(record.worktree_path):
            raise WorkspaceError("workspace record path changed", category="stale-record")
        if not current.worktree_path.is_dir():
            raise WorkspaceError(
                f"workspace checkout is missing: {current.worktree_path}", category="stale-record"
            )
        top, common, branch, head = discover_git_context(current.worktree_path)
        if common != current.git_common_dir or branch != current.branch:
            raise WorkspaceError(
                f"workspace checkout no longer matches record {current.record_path}",
                category="stale-record",
            )
        identity_raw = current.metadata.get("workspace.identity_path")
        identity_source = (
            canonical_path(identity_raw)
            if isinstance(identity_raw, str) and identity_raw
            else current.physical_worktree_path
        )
        if workspace_id_for_path(identity_source) != current.workspace_id:
            raise WorkspaceError("workspace identity does not match recorded identity path", category="collision")
        if labels is not None or metadata is not None:
            current = replace(
                current,
                labels=dict(labels if labels is not None else current.labels),
                metadata=dict(metadata if metadata is not None else current.metadata),
            )
            write_record(current)
        return current.context()


def inspect_workspace(record_or_path: WorkspaceRecord | WorkspaceContext | Path | str) -> dict[str, Any]:
    """Return record plus fresh Git facts without repairing state."""

    record = _coerce_record(record_or_path)
    result: dict[str, Any] = {"record": record.as_dict()}
    if not record.worktree_path.is_dir():
        result["git"] = {"state": "missing"}
        return result
    top, common, branch, head = discover_git_context(record.worktree_path)
    result["git"] = {
        "top_level": str(top),
        "git_common_dir": str(common),
        "branch": branch,
        "head": head,
        "matches_record": common == record.git_common_dir and branch == record.branch,
    }
    return result


def _lease_expired(lease: Lease | None, now: datetime) -> bool:
    if lease is None or lease.mode == "perpetual":
        return False
    return _parse_timestamp(lease.expires_at_utc or "", label="expires_at_utc") <= now


def remove_unrecorded_workspace(
    source_git_root: Path | str,
    target: Path | str,
    *,
    expected_branch: str | None = None,
    purpose: str = "legacy",
    force: bool = False,
) -> None:
    """Remove a legacy linked checkout that predates a shared record.

    This is only a compatibility bridge for product records created before
    the neutral library existed. It adopts the already-registered checkout
    into the same record/lifecycle implementation and immediately removes it,
    so adapters never carry a second Git-removal algorithm.
    """

    target_path = canonical_path(target)
    _top, _common, branch, _head = discover_git_context(target_path)
    if expected_branch is not None and branch != expected_branch:
        raise WorkspaceError(
            f"legacy workspace branch changed: expected {expected_branch!r}, found {branch!r}",
            category="stale-record",
        )
    context = adopt_workspace(source_git_root, target_path, purpose=purpose)
    remove_workspace(context, force=force)


def remove_workspace(
    record_or_path: WorkspaceRecord | WorkspaceContext | Path | str,
    *,
    cleanup: Callable[[WorkspaceContext], None] | None = None,
    force: bool = False,
    delete_branch: bool = True,
) -> None:
    """Clean adapter resources, then remove the checkout and record.

    The callback runs before Git removal.  A callback failure leaves the
    checkout and record intact so an adapter can recover rather than losing its
    only cleanup context.
    """

    record = _coerce_record(record_or_path)
    with _workspace_lock(record.git_common_dir):
        current = read_record(record.record_path)
        if current.lease and not force and not _lease_expired(current.lease, _utc_now()):
            raise WorkspaceError(
                f"workspace {current.workspace_id} has an active lease held by {current.lease.holder!r}",
                category="cleanup-refusal",
            )
        if cleanup is not None:
            cleanup(current.context())
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(current.worktree_path)],
            cwd=current.source_git_root, text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise WorkspaceError(
                f"git worktree remove failed ({result.returncode}): {(result.stderr or result.stdout).strip()}",
                category="cleanup-refusal",
            )
        if delete_branch and current.branch != "HEAD":
            branch_result = subprocess.run(
                ["git", "branch", "-D", current.branch],
                cwd=current.source_git_root, text=True, capture_output=True, check=False,
            )
            if branch_result.returncode:
                raise WorkspaceError(
                    f"git branch -D {current.branch} failed "
                    f"({branch_result.returncode}): "
                    f"{(branch_result.stderr or branch_result.stdout).strip()}",
                    category="cleanup-refusal",
                )
        current.record_path.unlink(missing_ok=True)


def acquire_lease(
    record_or_path: WorkspaceRecord | WorkspaceContext | Path | str,
    *,
    holder: str,
    ttl: timedelta | None = None,
    perpetual: bool = False,
    now: datetime | None = None,
) -> WorkspaceRecord:
    """Write an explicit lease to a record."""

    if (ttl is None) == (not perpetual):
        raise WorkspaceError("choose exactly one of ttl or perpetual", category="invalid-input")
    if ttl is not None and ttl.total_seconds() <= 0:
        raise WorkspaceError("lease ttl must be positive", category="invalid-input")
    record = _coerce_record(record_or_path)
    instant = now or _utc_now()
    lease = Lease(
        holder=holder,
        acquired_at_utc=_stamp(instant),
        renewed_at_utc=_stamp(instant),
        expires_at_utc=None if perpetual else _stamp(instant + (ttl or timedelta(0))),
        mode="perpetual" if perpetual else "held",
    )
    with _workspace_lock(record.git_common_dir):
        current = read_record(record.record_path)
        if (
            current.lease is not None
            and not _lease_expired(current.lease, instant)
            and current.lease.holder != holder
        ):
            raise WorkspaceError(
                f"workspace {current.workspace_id} already has an active lease held by "
                f"{current.lease.holder!r}",
                category="lease-held",
            )
        updated = replace(current, lease=lease)
        write_record(updated)
        return updated


def release_lease(record_or_path: WorkspaceRecord | WorkspaceContext | Path | str) -> WorkspaceRecord:
    record = _coerce_record(record_or_path)
    with _workspace_lock(record.git_common_dir):
        current = read_record(record.record_path)
        updated = replace(current, lease=None)
        write_record(updated)
        return updated
