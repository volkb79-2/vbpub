"""Bounded read API for allowlisted inspect-files content.

Provides gated, confined, bounded regular-file reads for catalog-resolved
paths.  No subprocess, no mutation, no arbitrary root-file reads.

Every read path is:
1. Resolved from catalog/entity metadata (not user-supplied absolute paths).
2. Confined to the allowlisted root via descriptor-relative traversal
   (``dir_fd`` + ``O_NOFOLLOW`` at every component) — never a lexical-only
   check.
3. Opened with ``os.open(..., os.O_RDONLY | os.O_NOFOLLOW)`` and stat-verified
   as a regular file (not a symlink, device, FIFO, socket, or directory).
4. Read with a hard byte limit, a hard line limit, and safe decoding.
   Reads are chunk-based, never line-by-line, so single giant lines are bounded.
5. Limits are **aggregate** across all files in a multi-file read (e.g. cgroup).
6. Production reads require root (EUID 0); the root check is an injectable seam
   for testing.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path
from collections.abc import Callable
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

# Conservative absolute maximums — enforced at argument validation time
# to prevent pathological values, even from code that calls the API directly.
_ABSOLUTE_MAX_BYTES = 1_048_576  # 1 MiB
_ABSOLUTE_MAX_LINES = 100_000

# Chunk size for bounded reads (must be <= _DEFAULT_MAX_BYTES).
_READ_CHUNK_SIZE = 65536  # 64 KiB

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
    """Open *resolved_path* for bounded reading via descriptor-relative
    traversal — NOT a lexical ``is_relative_to`` check alone.

    Steps (in order):

    1. Open *allow_root* with ``O_RDONLY | O_DIRECTORY | O_NOFOLLOW`` so that
       if the root itself is a symlink we fail immediately.
    2. Compute the relative path from *allow_root* to *resolved_path*.  Reject
       any relative component that starts with ``..``.
    3. Walk intermediate components one at a time, each with
       ``O_RDONLY | O_DIRECTORY | O_NOFOLLOW | dir_fd=parent_fd`` — any
       component that is a symlink (or a non-directory) is rejected.
    4. Open the final leaf with ``O_RDONLY | O_NONBLOCK | O_NOFOLLOW |
       dir_fd=parent_fd``.
    5. ``fstat`` the descriptor and verify it is a regular file
       (``stat.S_ISREG``).
    6. Return a regular ``io.BufferedReader`` wrapping the descriptor.

    This approach is race-resistant: an attacker cannot swap a symlink in
    between a lexical check and the open because every intermediate component
    is traversed via ``dir_fd`` with ``O_NOFOLLOW``, anchored at the
    already-opened *allow_root* directory descriptor.

    Raises ``ValueError`` or ``OSError`` on any violation.
    """
    # 1. Open allow_root with no-follow.
    try:
        root_fd = os.open(
            str(allow_root),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        msg = f"cannot open allow_root {allow_root}: {exc}"
        raise ValueError(msg) from exc

    try:
        # 2. Compute relative path and reject traversal.
        relative_str = os.path.relpath(str(resolved_path), str(allow_root))
        if relative_str.startswith(".."):
            msg = f"path {resolved_path} is not under {allow_root}"
            raise ValueError(msg)

        parts = Path(relative_str).parts
        current_fd = root_fd

        # 3. Walk intermediate components.
        for part in parts[:-1]:
            if part in ("", ".", ".."):
                msg = f"path {resolved_path} is not under {allow_root}"
                raise ValueError(msg)
            child_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd

        # 4. Open the final leaf.
        leaf = parts[-1] if parts else "."
        fd = os.open(
            leaf,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=current_fd,
        )

        # Track whether the fd is still owned by this function so we
        # never double-close (the except handler runs on ValueError too).
        fd_owned = True
        try:
            # 5. fstat and require regular file.
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                msg = (
                    f"not a regular file: {resolved_path}"
                    f" (mode={st.st_mode:o})"
                )
                raise ValueError(msg)

            # 6. Wrap in a buffered reader — transfer ownership.
            fd_owned = False
            return os.fdopen(fd, "rb")
        except (ValueError, OSError):
            if fd_owned:
                os.close(fd)
            raise

    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


# ---------------------------------------------------------------------------
# Limit validation
# ---------------------------------------------------------------------------


def _validate_limits(
    max_bytes: int,
    max_lines: int,
) -> None:
    """Validate that *max_bytes* and *max_lines* are positive and within
    conservative absolute maximums.

    Raises ``ValueError`` on violation.
    """
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        msg = f"max_bytes must be a positive int, got {max_bytes!r}"
        raise ValueError(msg)
    if max_bytes > _ABSOLUTE_MAX_BYTES:
        msg = (
            f"max_bytes {max_bytes} exceeds absolute maximum"
            f" {_ABSOLUTE_MAX_BYTES}"
        )
        raise ValueError(msg)
    if not isinstance(max_lines, int) or max_lines <= 0:
        msg = f"max_lines must be a positive int, got {max_lines!r}"
        raise ValueError(msg)
    if max_lines > _ABSOLUTE_MAX_LINES:
        msg = (
            f"max_lines {max_lines} exceeds absolute maximum"
            f" {_ABSOLUTE_MAX_LINES}"
        )
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Bounded read
# ---------------------------------------------------------------------------


def _bounded_read(
    buf: IO[bytes],
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_lines: int = _DEFAULT_MAX_LINES,
    aggregate_bytes: int = 0,
    aggregate_lines: int = 0,
) -> tuple[str, bool, bool, int, int]:
    """Read from *buf* with the given limits, in fixed-size chunks.

    Reads in fixed-size chunks (never line-by-line) so that a single giant
    line with no newline never materializes unboundedly in memory.

    *aggregate_bytes* and *aggregate_lines* are byte/line counts already
    consumed by previous files in the same multi-file read (e.g. cgroup).
    If provided, the limits are applied **cumulatively** — the caps are still
    *max_bytes*/*max_lines*, but the counts start from the given aggregate
    offset.

    Returns ``(decoded_text, truncated_bytes, truncated_lines,
    new_aggregate_bytes, new_aggregate_lines)``.
    Bytes are decoded with ``errors="replace"`` and unsafe C0 / C1 / DEL
    control characters are sanitized (replaced with U+FFFD) while
    preserving newline (``\\n``) and tab (``\\t``).  Terminal escape
    sequences, NUL bytes, and other control codes cannot replay in the
    returned text.
    """
    _validate_limits(max_bytes, max_lines)

    truncated_bytes_flag = False
    truncated_lines_flag = False
    total_bytes = aggregate_bytes
    total_lines = aggregate_lines
    chunks: list[bytes] = []

    # If aggregate caps are already exhausted, read nothing from this file.
    if total_bytes >= max_bytes:
        return "", True, truncated_lines_flag, total_bytes, total_lines
    if total_lines >= max_lines:
        return "", truncated_bytes_flag, True, total_bytes, total_lines

    # Read in fixed-size chunks — never line-by-line.
    while True:
        chunk = buf.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        chunk_len = len(chunk)

        # Count newlines in this chunk.
        nl_count = chunk.count(b"\n")

        # Would this chunk push us over either limit?
        if total_bytes + chunk_len > max_bytes:
            # Accept bytes up to the limit, then stop.
            remaining = max_bytes - total_bytes
            if remaining > 0:
                partial = chunk[:remaining]
                chunks.append(partial)
                total_bytes += remaining
            # If remaining == 0, total_bytes stays unchanged (exhausted).
            total_bytes = max_bytes
            truncated_bytes_flag = True
            break

        if total_lines + nl_count > max_lines:
            # Accept lines up to the limit, then stop.
            remaining_lines = max_lines - total_lines
            if remaining_lines > 0:
                pos = 0
                for _ in range(remaining_lines):
                    idx = chunk.find(b"\n", pos)
                    if idx == -1:
                        break
                    pos = idx + 1
                if pos > 0:
                    partial = chunk[:pos]
                    chunks.append(partial)
                    total_bytes += len(partial)
                # else: no newline in this chunk — already exhausting via
                # bytes, so this should be unreachable (lines can't exceed
                # max_lines without a newline when aggregate lines are < max).
                # Fall through: stop reading.
            # remaining_lines == 0: aggregate already exhausted — append
            # nothing, the caps are already hit.
            total_lines = max_lines
            truncated_lines_flag = True
            break

        chunks.append(chunk)
        total_bytes += chunk_len
        total_lines += nl_count

    raw = b"".join(chunks)
    text = raw.decode("utf-8", errors="replace")
    # Sanitize unsafe C0 control characters while preserving \n (0x0A)
    # and \t (0x09).  Replace NUL (0x00), terminal escape (0x1B), and
    # all other C0 codes (0x01-0x08, 0x0B, 0x0C, 0x0E-0x1F) with the
    # Unicode replacement character U+FFFD.
    sanitized_chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x0A or code == 0x09:
            sanitized_chars.append(ch)  # preserve newline and tab
        elif code < 0x20 or code == 0x7F:
            sanitized_chars.append("\ufffd")
        elif 0x80 <= code <= 0x9F:
            sanitized_chars.append("\ufffd")  # C1 control codes
        else:
            sanitized_chars.append(ch)
    text = "".join(sanitized_chars)
    return text, truncated_bytes_flag, truncated_lines_flag, total_bytes, total_lines


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
# Root enforcement (per TUI-SPEC 4.8)
# ---------------------------------------------------------------------------


def _injectable_is_root(
    *,
    fixture_root: Path | None = None,
    is_root: Callable[[], bool] | None = None,
) -> bool:
    """Return True if the effective user is root (EUID == 0).

    When *is_root* is provided (testing seam) it is called directly —
    tests can inject ``lambda: True`` or ``lambda: False`` without
    requiring actual root privileges or relying on *fixture_root*.

    For backward compatibility, when *fixture_root* is provided but
    *is_root* is not, the check defaults to ``os.geteuid() == 0``
    (the production path).  *fixture_root* does **not** itself imply
    root; use *is_root* to explicitly control that seam.
    """
    if is_root is not None:
        return is_root()
    return os.geteuid() == 0


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
    is_root: Callable[[], bool] | None = None,
) -> GatedReadResult:
    """Build a bounded file read for the given kind and target.

    Gated on **both** ``inspect_files=True`` and ``admin=True``.

    *max_bytes* and *max_lines* control the read bounds.  Both must be
    positive integers below conservative absolute maximums (1 MiB /
    100 000 lines).

    *fixture_root* is a testing seam — when provided it replaces the standard
    filesystem root (``/var/lib/docker`` for Docker logs,
    ``/sys/fs/cgroup`` for cgroup files).  It does **not** bypass the
    root-EUID check; use *is_root* for that.

    *is_root* is an optional callable for testing — when provided it is
    called to determine root status.  In production (``is_root is None``)
    the ``os.geteuid() == 0`` check is used, so the caller must be root.

    In production (``fixture_root is None`` and ``is_root is None``) the
    caller must be root, per TUI-SPEC §4.8 ("available only in root/admin
    or daemon-approved modes").

    Returns ``InspectFilesReadResult`` on success, ``InspectFilesReadError``
    on a resolvable failure (missing file, permission denied, invalid path),
    or ``ReadDenied`` when the gating flags are not active.
    """
    # ---- Validate limits early ----
    try:
        _validate_limits(max_bytes, max_lines)
    except ValueError as exc:
        try:
            ik = InspectFilesKind(kind)
        except ValueError:
            ik = None
        return InspectFilesReadError(kind=ik, target=target, error=str(exc))

    # ---- Gating ----
    if not inspect_files or not admin:
        try:
            ik = InspectFilesKind(kind)
        except ValueError:
            ik = None
        return ReadDenied(kind=ik, target=target)

    # ---- Root check (production only) ----
    if not _injectable_is_root(fixture_root=fixture_root, is_root=is_root):
        try:
            ik = InspectFilesKind(kind)
        except ValueError:
            ik = None
        return InspectFilesReadError(
            kind=ik,
            target=target,
            error="file inspection requires root; re-run as root or via "
            "the daemon",
        )

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

    # ---- Read each path with aggregate limits ----
    # The aggregate limits apply to the RENDERED content (headers + body),
    # so we reserve per-file framing overhead before each read.
    results: list[dict] = []
    agg_bytes = 0
    agg_lines = 0
    for path in paths_to_read:
        pstr = str(path)
        # Framing overhead for a successfully rendered file:
        #   "# /path\n"  = len(pstr) + 3
        #   trailing "\n\n" up to 2
        header_cost = len(pstr) + 5

        # If we can't fit even the header, skip this file entirely.
        if agg_bytes + header_cost > max_bytes:
            results.append({
                "path": pstr,
                "error": None,
                "content": "",
                "truncated_bytes": True,
                "truncated_lines": False,
            })
            agg_bytes = max_bytes
            continue

        try:
            buf = _confine_and_open(path, allow_root)
        except (ValueError, OSError, FileNotFoundError) as exc:
            # Error entries also consume budget for the rendered line:
            # "# /path: [ERROR] msg\n"
            error_cost = len(pstr) + len(str(exc)) + 14
            if agg_bytes + error_cost > max_bytes:
                results.append({
                    "path": pstr,
                    "error": None,
                    "content": "",
                    "truncated_bytes": True,
                    "truncated_lines": False,
                })
                agg_bytes = max_bytes
                continue
            agg_bytes += error_cost
            results.append({
                "path": pstr,
                "error": str(exc),
                "content": "",
                "truncated_bytes": False,
                "truncated_lines": False,
            })
            continue

        try:
            # Reserve framing overhead in aggregate before reading content.
            read_agg_bytes = agg_bytes + header_cost
            text, trunc_b, trunc_l, agg_bytes, agg_lines = _bounded_read(
                buf,
                max_bytes=max_bytes,
                max_lines=max_lines,
                aggregate_bytes=read_agg_bytes,
                aggregate_lines=agg_lines,
            )
            results.append({
                "path": pstr,
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

    # Cgroup: combine multiple files into a structured result.
    # Files whose content is empty because the aggregate cap was already
    # exhausted (truncated flag set) are omitted from the rendered output
    # — their headers would consume budget we no longer have.
    combined_parts: list[str] = []
    any_trunc_bytes = False
    any_trunc_lines = False
    first_error: str | None = None
    combined_paths: list[str] = []
    for r in results:
        any_trunc_bytes = any_trunc_bytes or r["truncated_bytes"]
        any_trunc_lines = any_trunc_lines or r["truncated_lines"]
        if r["error"] is not None:
            if first_error is None:
                first_error = r["error"]
            combined_parts.append(f"# {r['path']}: [ERROR] {r['error']}\n")
            combined_paths.append(r["path"])
        elif r["content"] or not (r["truncated_bytes"] or r["truncated_lines"]):
            # Non-empty content or non-truncated empty file — render.
            combined_parts.append(f"# {r['path']}\n{r['content']}")
            if not r["content"].endswith("\n"):
                combined_parts.append("\n")
            combined_parts.append("\n")
            combined_paths.append(r["path"])
        # else: empty content because aggregate cap exhausted — skip header too.

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
