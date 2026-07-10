"""Bounded read API for allowlisted inspect-files content.

Provides gated, confined, bounded regular-file reads for catalog-resolved
paths.  No subprocess, no mutation, no arbitrary root-file reads.

Every read path is:
1. Resolved from catalog/entity metadata (not user-supplied absolute paths).
2. Confined to the allowlisted root via ``Path.is_relative_to()``.
3. Opened with ``os.open(..., os.O_RDONLY | os.O_NOFOLLOW)`` and stat-verified
   as a regular file (not a symlink, device, FIFO, socket, or directory).
4. Read with a hard byte limit, a hard line limit, and safe decoding.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path
from typing import IO

from groop.inspect_files.catalog import INSPECT_CATALOG, InspectFilesKind

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Full Docker container id: exactly 64 lowercase hex characters.
_FULL_DOCKER_ID_PATTERN = __import__("re").compile(r"^[a-f0-9]{64}$")

# Default read bounds.
_DEFAULT_MAX_BYTES = 65536  # 64 KiB
_DEFAULT_MAX_LINES = 5000

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class InspectFilesReadResult:
    """Bounded successful file read result.

    ``content`` holds the decoded text up to the applied limits.
    ``truncated_bytes`` and ``truncated_lines`` indicate whether the returned
    content was cut short.
    """

    kind: InspectFilesKind
    target: str
    kind_label: str
    description: str
    path: str
    content: str
    truncated_bytes: bool = False
    truncated_lines: bool = False
    mode: str = "content"

    def to_jsonable(self) -> dict:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "kind_label": self.kind_label,
            "description": self.description,
            "path": self.path,
            "content": self.content,
            "truncated_bytes": self.truncated_bytes,
            "truncated_lines": self.truncated_lines,
            "mode": self.mode,
        }

    def to_text(self) -> str:
        header = (
            f"Read: {self.kind.value}\n"
            f"Target: {self.target}\n"
            f"Path: {self.path}\n\n"
        )
        if self.truncated_bytes:
            header += "[TRUNCATED: byte limit exceeded]\n"
        if self.truncated_lines:
            header += "[TRUNCATED: line limit exceeded]\n"
        return header + self.content


@dataclasses.dataclass(frozen=True)
class InspectFilesReadError:
    """Error result — file could not be read (does not exist, denied, etc.).

    ``content`` is never echoed on error/denied paths.
    """

    kind: InspectFilesKind | None
    target: str
    error: str
    mode: str = "error"

    def to_jsonable(self) -> dict:
        return {
            "kind": self.kind.value if self.kind else "none",
            "target": self.target,
            "error": self.error,
            "mode": self.mode,
        }

    def to_text(self) -> str:
        return f"[ERROR] {self.error}"


@dataclasses.dataclass(frozen=True)
class ReadDenied:
    """Returned when gating flags are not enabled."""

    kind: InspectFilesKind | None
    target: str
    message: str = (
        "file inspection is not enabled; re-run with --inspect-files and "
        "--admin to inspect files"
    )
    mode: str = "disabled"

    def to_jsonable(self) -> dict:
        return {
            "kind": self.kind.value if self.kind else "none",
            "target": self.target,
            "message": self.message,
            "mode": self.mode,
        }


GatedReadResult = InspectFilesReadResult | InspectFilesReadError | ReadDenied

# ---------------------------------------------------------------------------
# Path confinement and file validation
# ---------------------------------------------------------------------------


def _confine_and_open(
    resolved_path: Path,
    allow_root: Path,
) -> IO[bytes]:
    """Open *resolved_path* for bounded reading with no-follow + regular-file
    validation.

    Steps (in order):
    1. Reject if the path is not **under** *allow_root* via
       ``Path.is_relative_to()``.
    2. Open with ``os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW`` so that
       symlinks are rejected and FIFO opens do not hang.
    3. ``fstat`` the descriptor and verify it is a regular file
       (``stat.S_ISREG``).
    4. Return a regular ``io.BufferedReader`` wrapping the descriptor.

    Raises ``ValueError`` or ``OSError`` on any violation.
    """
    # 1. Confine to allow_root.
    try:
        if not resolved_path.is_relative_to(allow_root):
            msg = f"path {resolved_path} is not under {allow_root}"
            raise ValueError(msg)
    except ValueError:
        msg = f"path {resolved_path} is not under {allow_root}"
        raise ValueError(msg) from None

    # 2. Open with no-follow and nonblocking (to detect FIFOs immediately).
    fd = os.open(
        str(resolved_path),
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
    )

    try:
        # 3. fstat and require regular file.
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            msg = f"not a regular file: {resolved_path} (mode={st.st_mode:o})"
            raise ValueError(msg)

        # 4. Wrap in a buffered reader.
        return os.fdopen(fd, "rb")
    except (ValueError, OSError):
        os.close(fd)
        raise


# ---------------------------------------------------------------------------
# Bounded read
# ---------------------------------------------------------------------------


def _bounded_read(
    buf: IO[bytes],
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_lines: int = _DEFAULT_MAX_LINES,
) -> tuple[str, bool, bool]:
    """Read from *buf* with the given limits.

    Returns ``(decoded_text, truncated_bytes, truncated_lines)``.
    Bytes are decoded with ``surrogateescape`` so that arbitrary binary content
    does not raise.
    """
    truncated_bytes_flag = False
    truncated_lines_flag = False
    total_bytes = 0
    line_count = 0
    chunks: list[bytes] = []

    # Read one byte over the limit to detect truncation.
    read_size = max_bytes + 1

    for line_bytes in buf:
        line_bytes: bytes
        total_bytes += len(line_bytes)
        if total_bytes > max_bytes:
            truncated_bytes_flag = True
            break
        line_count += 1
        if line_count > max_lines:
            truncated_lines_flag = True
            break
        chunks.append(line_bytes)

    raw = b"".join(chunks)
    text = raw.decode("utf-8", errors="surrogateescape")
    return text, truncated_bytes_flag, truncated_lines_flag


# ---------------------------------------------------------------------------
# Resolve paths from catalog entries
# ---------------------------------------------------------------------------


def _resolve_docker_json_log_path(
    target: str,
    *,
    fixture_root: Path | None = None,
) -> Path:
    """Resolve the Docker JSON log file path for a full container id.

    In production (fixture_root is None) the path is
    ``/var/lib/docker/containers/<id>/<id>-json.log``.

    When *fixture_root* is provided the path is
    ``<fixture_root>/containers/<id>/<id>-json.log``.
    """
    if not _FULL_DOCKER_ID_PATTERN.match(target):
        msg = (
            f"docker container id must be exactly 64 lowercase hex chars: "
            f"{target!r}"
        )
        raise ValueError(msg)

    base = fixture_root if fixture_root is not None else Path("/var/lib/docker")
    log_file = base / "containers" / target / f"{target}-json.log"
    return log_file


def _resolve_cgroup_file_paths(
    target: str,
    *,
    fixture_root: Path | None = None,
) -> list[Path]:
    """Resolve the list of allowlisted cgroup file paths for a target.

    Uses the same catalog-defined filenames from :func:`_cgroup_files`.
    """
    from groop.inspect_files.catalog import _cgroup_files

    base = fixture_root if fixture_root is not None else Path("/sys/fs/cgroup")
    _path_previews, _ = _cgroup_files(target)
    # Re-root the preview paths under the effective base.
    relative_paths = []
    for preview in _path_previews:
        # preview is e.g. /sys/fs/cgroup/system.slice/ssh.service/memory.current
        # Strip the /sys/fs/cgroup prefix to get the relative part.
        parts = preview.relative_to("/sys/fs/cgroup")
        relative_paths.append(base / parts)
    return relative_paths


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------


def build_inspect_read(
    kind: str,
    target: str,
    *,
    inspect_files: bool = False,
    admin: bool = False,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_lines: int = _DEFAULT_MAX_LINES,
    fixture_root: Path | None = None,
) -> GatedReadResult:
    """Build a bounded file read for the given kind and target.

    Gated on **both** ``inspect_files=True`` and ``admin=True``.

    *max_bytes* and *max_lines* control the read bounds.

    *fixture_root* is a testing seam — when provided it replaces the standard
    filesystem root (``/var/lib/docker`` for Docker logs,
    ``/sys/fs/cgroup`` for cgroup files).

    Returns ``InspectFilesReadResult`` on success, ``InspectFilesReadError``
    on a resolvable failure (missing file, permission denied, invalid path),
    or ``ReadDenied`` when the gating flags are not active.
    """
    # ---- Gating ----
    if not inspect_files or not admin:
        try:
            ik = InspectFilesKind(kind)
        except ValueError:
            ik = None
        return ReadDenied(kind=ik, target=target)

    # ---- Resolve kind ----
    try:
        ik = InspectFilesKind(kind)
    except ValueError as exc:
        return InspectFilesReadError(kind=None, target=target, error=str(exc))

    entry = INSPECT_CATALOG.get(ik)
    if entry is None:
        return InspectFilesReadError(
            kind=ik, target=target,
            error=f"unknown inspection kind: {kind!r}",
        )

    # ---- Resolve path(s) ----
    try:
        if ik == InspectFilesKind.DOCKER_JSON_LOG:
            resolved_path = _resolve_docker_json_log_path(target, fixture_root=fixture_root)
            allow_root = (
                fixture_root / "containers" if fixture_root
                else Path("/var/lib/docker/containers")
            )
            paths_to_read = [resolved_path]

        elif ik == InspectFilesKind.CGROUP_FILES:
            base_root = fixture_root if fixture_root is not None else Path("/sys/fs/cgroup")
            allow_root = base_root
            paths_to_read = _resolve_cgroup_file_paths(target, fixture_root=fixture_root)

        else:
            return InspectFilesReadError(
                kind=ik, target=target,
                error=f"inspection kind {kind!r} does not support content reads",
            )
    except ValueError as exc:
        return InspectFilesReadError(kind=ik, target=target, error=str(exc))

    # ---- Read each path ----
    results: list[dict] = []
    for path in paths_to_read:
        try:
            buf = _confine_and_open(path, allow_root)
        except (ValueError, OSError, FileNotFoundError) as exc:
            results.append({
                "path": str(path),
                "error": str(exc),
                "content": "",
                "truncated_bytes": False,
                "truncated_lines": False,
            })
            continue

        try:
            text, trunc_b, trunc_l = _bounded_read(
                buf, max_bytes=max_bytes, max_lines=max_lines,
            )
            results.append({
                "path": str(path),
                "error": None,
                "content": text,
                "truncated_bytes": trunc_b,
                "truncated_lines": trunc_l,
            })
        finally:
            buf.close()

    # ---- Build response ----
    if ik == InspectFilesKind.DOCKER_JSON_LOG:
        # Single file
        r = results[0]
        if r["error"] is not None:
            return InspectFilesReadError(
                kind=ik, target=target, error=r["error"],
            )
        return InspectFilesReadResult(
            kind=ik,
            target=target,
            kind_label=entry.kind_label,
            description=entry.description,
            path=r["path"],
            content=r["content"],
            truncated_bytes=r["truncated_bytes"],
            truncated_lines=r["truncated_lines"],
        )

    # Cgroup: combine multiple files into a structured result
    combined_parts: list[str] = []
    any_trunc_bytes = False
    any_trunc_lines = False
    first_error: str | None = None
    combined_paths: list[str] = []
    for r in results:
        combined_paths.append(r["path"])
        if r["error"] is not None:
            if first_error is None:
                first_error = r["error"]
            combined_parts.append(f"# {r['path']}: [ERROR] {r['error']}\n")
        else:
            any_trunc_bytes = any_trunc_bytes or r["truncated_bytes"]
            any_trunc_lines = any_trunc_lines or r["truncated_lines"]
            combined_parts.append(f"# {r['path']}\n{r['content']}")
            if not r["content"].endswith("\n"):
                combined_parts.append("\n")
            combined_parts.append("\n")

    if first_error is not None and not any(
        r["error"] is None for r in results
    ):
        return InspectFilesReadError(
            kind=ik, target=target, error=first_error,
        )

    return InspectFilesReadResult(
        kind=ik,
        target=target,
        kind_label=entry.kind_label,
        description=entry.description,
        path="; ".join(combined_paths),
        content="".join(combined_parts).rstrip("\n"),
        truncated_bytes=any_trunc_bytes,
        truncated_lines=any_trunc_lines,
    )
