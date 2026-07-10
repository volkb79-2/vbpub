"""Daemon-side BPF snapshot bridge.

Reads an explicitly configured pinned BPF counter map via ``bpftool`` through
an argv-only, injectable command runner, decodes the P17/P18 logical
dimensions, builds the ``cgroup_map`` from a configured cgroup-v2 root, and
atomically writes the resulting ``snapshot.json`` consumed by
:class:`groop.providers.net_bpf.BpfProvider`.

The bridge does **not** load, attach, detach, or compile BPF programs.
It consumes already-loaded and pinned maps.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

CommandRunner = Callable[[list[str]], str]

SNAPSHOT_FILENAME = "snapshot.json"
SCHEMA_VERSION = 1
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB max bpftool output


class BpfSnapshotError(Exception):
    """Raised on a recoverable BPF snapshot failure.

    The caller should preserve the last valid snapshot when catching this.
    """


class BpfSnapshotBridge:
    """Safely translate a pinned BPF counter map into the P18 snapshot contract.

    Args:
        bpf_root: Absolute path to the groop BPF pin root
            (e.g. ``/sys/fs/bpf/groop``). All map pin paths must reside
            underneath this directory.
        command_runner: Injectable argv-only command runner; defaults to
            :func:`_subprocess_runner`.
        cgroup_id_resolver: Injectable resolver that returns ``{int cgroup_id:
            str entity_key}`` for the configured cgroup-v2 root. Defaults to
            :func:`_walk_cgroup_ids`.
        max_output_bytes: Maximum number of bytes of ``bpftool`` output to
            accept. Output exceeding this is discarded as an error.

    The bridge keeps the last valid snapshot in memory so a transient refresh
    failure can return the previous snapshot.
    """

    def __init__(
        self,
        bpf_root: Path,
        *,
        command_runner: CommandRunner | None = None,
        cgroup_id_resolver: Callable[[], dict[int, str]] | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self._bpf_root = _resolve_safe(bpf_root)
        self._command_runner = command_runner or _subprocess_runner
        self._cgroup_id_resolver = cgroup_id_resolver or _walk_cgroup_ids
        self._max_output_bytes = max_output_bytes
        self._last_valid_snapshot: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def bpf_root(self) -> Path:
        return self._bpf_root

    @property
    def last_valid_snapshot(self) -> dict[str, Any] | None:
        """The last successfully built snapshot, or ``None``."""
        return self._last_valid_snapshot

    def refresh(self, map_pin_rel_path: str) -> dict[str, Any]:
        """Read the pinned BPF map and produce an up-to-date snapshot.

        Args:
            map_pin_rel_path: Path component **relative** to *bpf_root*
                identifying the pinned map, e.g. ``"groop_cgroup_skb"``.

        Returns:
            The new snapshot dictionary (also stored as last valid).

        Raises:
            BpfSnapshotError: On any failure (invalid path, nonzero exit,
                malformed JSON, invalid rows, etc.). The last valid snapshot
                is preserved.
        """
        map_pin_path = self._validate_map_path(map_pin_rel_path)
        raw = self._run_bpftool(map_pin_path)
        entries = self._parse_bpftool_output(raw)
        cgroup_map = self._build_cgroup_map()
        snapshot = self._build_snapshot(entries, cgroup_map, map_pin_rel_path)
        self._last_valid_snapshot = snapshot
        return snapshot

    def write_snapshot(self, snapshot: dict[str, Any], dest_dir: Path) -> Path:
        """Atomically write *snapshot* as ``snapshot.json`` under *dest_dir*.

        Writes to a private temporary file in the same directory, flushes and
        fsyncs it, then atomically replaces the destination. Permissions are
        set to ``0o644`` (non-world-writable).

        Returns:
            The final path to ``snapshot.json``.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / SNAPSHOT_FILENAME

        # Write to a temp file in the same directory for atomic rename
        tmp = dest_dir / f".{SNAPSHOT_FILENAME}.{os.getpid()}.tmp"
        try:
            data = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest)
        except OSError as exc:
            # Clean up temp file if it still exists
            tmp.unlink(missing_ok=True)
            raise BpfSnapshotError(f"failed to write snapshot: {exc}") from exc

        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_map_path(self, rel_path: str) -> Path:
        """Resolve *rel_path* under *bpf_root* and enforce confinement.

        Raises:
            BpfSnapshotError: If the resolved path escapes *bpf_root* or
                contains traversal components.
        """
        if not rel_path or ".." in rel_path.split("/"):
            raise BpfSnapshotError(f"invalid map pin relative path: {rel_path!r}")

        candidate = (self._bpf_root / rel_path).resolve()
        if not str(candidate).startswith(str(self._bpf_root)):
            raise BpfSnapshotError(
                f"map pin path {candidate} escapes bpf root {self._bpf_root}"
            )
        if not candidate.exists():
            raise BpfSnapshotError(f"map pin path {candidate} does not exist")
        return candidate

    def _run_bpftool(self, map_pin_path: Path) -> str:
        """Run ``bpftool --json map dump pinned PATH`` and return stdout.

        Raises:
            BpfSnapshotError: If the command fails or output exceeds the max.
        """
        argv = ["bpftool", "--json", "map", "dump", "pinned", str(map_pin_path)]
        try:
            stdout = self._command_runner(argv)
        except FileNotFoundError:
            raise BpfSnapshotError("bpftool is not installed") from None
        except OSError as exc:
            raise BpfSnapshotError(f"bpftool execution failed: {exc}") from exc

        if len(stdout.encode("utf-8")) > self._max_output_bytes:
            raise BpfSnapshotError(
                f"bpftool output exceeds {self._max_output_bytes} byte limit"
            )
        return stdout

    @staticmethod
    def _parse_bpftool_output(raw: str) -> list[dict[str, Any]]:
        """Parse the JSON output from ``bpftool --json map dump pinned``.

        ``bpftool`` returns a JSON array of entry objects. Each entry has:
            - ``key`` (hex-encoded or raw bytes)
            - ``value`` (hex-encoded or raw bytes)

        This decoder expects a decoded representation where the map's
        key/value spec has been applied via ``bpftool`` (i.e. ``--json`` with
        the map's BTF or key/value size produces structured output).

        For percpu_array and similar per-CPU maps, bpftool returns an array
        ``values``. This bridge only handles the simple single-value case.

        Raises:
            BpfSnapshotError: On parse failure.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BpfSnapshotError(f"bpftool JSON parse error: {exc}") from exc

        if not isinstance(data, list):
            raise BpfSnapshotError(f"bpftool output is not a JSON array: {type(data).__name__}")

        entries: list[dict[str, Any]] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise BpfSnapshotError(f"bpftool entry {i} is not a dict: {type(item).__name__}")
            entry = _decode_bpftool_entry(item, i)
            entries.append(entry)
        return entries

    def _build_cgroup_map(self) -> dict[str, str]:
        """Resolve the cgroup-id-to-entity-key mapping.

        Returns:
            ``{str(cgroup_id): str(entity_key)}``

        Raises:
            BpfSnapshotError: If the resolver fails.
        """
        try:
            raw = self._cgroup_id_resolver()
        except OSError as exc:
            raise BpfSnapshotError(f"cgroup id resolution failed: {exc}") from exc

        return {str(cid): ekey for cid, ekey in raw.items()}

    def _build_snapshot(
        self,
        entries: list[dict[str, Any]],
        cgroup_map: dict[str, str],
        map_name: str,
    ) -> dict[str, Any]:
        """Assemble the full P18 snapshot dictionary."""
        return {
            "schema_version": SCHEMA_VERSION,
            "comment": (
                "BPF map snapshot produced by groop daemon BpfSnapshotBridge. "
                "Consumed by groop.providers.net_bpf.BpfProvider."
            ),
            "generated_at": time.time(),
            "source": {
                "bpf_root": str(self._bpf_root),
                "map": map_name,
                "bridge_version": "0.1.0-p42",
            },
            "maps": {
                map_name: entries,
            },
            "cgroup_map": cgroup_map,
        }


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _resolve_safe(path: Path) -> Path:
    """Resolve *path* to an absolute, symlink-free path.

    Raises:
        BpfSnapshotError: If the path does not exist or cannot be resolved.
    """
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BpfSnapshotError(f"cannot resolve BPF root {path}: {exc}") from exc


def _decode_bpftool_entry(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Decode a single bpftool JSON entry into P17/P18 logical dimensions.

    ``bpftool --json map dump`` typically returns structured key/value

    For a map with a known key/value shape, ``bpftool`` may present them as
    decoded fields. This function handles both raw hex and structured output.

    The expected decoded shape (matching P17/P18 design):
        - ``cgroup_id`` (int)
        - ``direction`` ("ingress" | "egress")
        - ``family`` ("ipv4" | "ipv6" | "other")
        - ``proto`` ("tcp" | "udp" | "icmp" | "other")
        - ``bytes`` (int, non-negative)
        - ``packets`` (int, non-negative)
    """
    entry: dict[str, Any] = {}

    # Extract from either top-level decoded fields or key/value sub-objects
    # bpftool --json with key/val spec selected returns:
    # {"key": ..., "value": ..., ...}
    # But with --json alone on a non-BTF map, it returns hex strings.
    # We expect the bpftool version/format that produces decoded output.
    key_obj = item.get("key") if isinstance(item.get("key"), dict) else item
    val_obj = item.get("value") if isinstance(item.get("value"), dict) else item

    # Try decoded fields from key
    if isinstance(key_obj, dict):
        entry["cgroup_id"] = _pop_int(key_obj, "cgroup_id", index)
        entry["direction"] = _pop_str(key_obj, "direction", index)
        entry["family"] = _pop_str(key_obj, "family", index) or "other"
        entry["proto"] = _pop_str(key_obj, "proto", index) or "other"

    # Try decoded fields from value
    if isinstance(val_obj, dict):
        entry["bytes"] = _pop_nonneg_int(val_obj, "bytes", index)
        entry["packets"] = _pop_nonneg_int(val_obj, "packets", index)

    # Fallback: if the entry has top-level decoded fields (e.g. from a
    # structured dump format), read them directly.
    if "cgroup_id" not in entry:
        entry["cgroup_id"] = _pop_int(item, "cgroup_id", index)
    if "direction" not in entry:
        entry["direction"] = _pop_str(item, "direction", index)
    if "family" not in entry:
        entry["family"] = _pop_str(item, "family", index) or "other"
    if "proto" not in entry:
        entry["proto"] = _pop_str(item, "proto", index) or "other"
    if "bytes" not in entry:
        entry["bytes"] = _pop_nonneg_int(item, "bytes", index)
    if "packets" not in entry:
        entry["packets"] = _pop_nonneg_int(item, "packets", index)

    # Validate required fields
    if entry.get("cgroup_id") is None:
        raise BpfSnapshotError(f"bpftool entry {index}: missing or invalid 'cgroup_id'")
    direction = entry.get("direction", "")
    if direction not in ("ingress", "egress"):
        raise BpfSnapshotError(
            f"bpftool entry {index}: invalid direction {direction!r}; expected ingress/egress"
        )
    family = entry.get("family", "")
    if family not in ("ipv4", "ipv6", "other"):
        raise BpfSnapshotError(
            f"bpftool entry {index}: invalid family {family!r}; expected ipv4/ipv6/other"
        )
    proto = entry.get("proto", "")
    if proto not in ("tcp", "udp", "icmp", "other"):
        raise BpfSnapshotError(
            f"bpftool entry {index}: invalid proto {proto!r}; expected tcp/udp/icmp/other"
        )

    return entry


def _pop_int(obj: dict[str, Any], key: str, index: int) -> int | None:
    """Extract an integer field, returning ``None`` on absence or invalid type."""
    val = obj.get(key)
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        ival = int(val)
        if ival == val:
            return ival
    return None


def _pop_nonneg_int(obj: dict[str, Any], key: str, index: int) -> int | None:
    """Extract a non-negative integer field."""
    val = _pop_int(obj, key, index)
    if val is not None and val < 0:
        raise BpfSnapshotError(
            f"bpftool entry {index}: negative {key}={val}"
        )
    return val


def _pop_str(obj: dict[str, Any], key: str, index: int) -> str:
    """Extract a string field, returning empty string on absence."""
    val = obj.get(key)
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


def _walk_cgroup_ids(cgroup_root: Path | None = None) -> dict[int, str]:
    """Walk the cgroup-v2 tree and return ``{cgroup_id: entity_key}``.

    This is the **production** cgroup-id resolver. It reads
    ``/proc/self/cgroup`` and walks the cgroup tree to obtain numeric cgroup
    IDs.

    .. important::
        **Kernel identity assumption**: The cgroup ID read from
        ``cgroupfs`` (via ``stat -c %i`` on a cgroup directory) is the same
        numeric ID the kernel uses in BPF ``bpf_get_current_cgroup_id()``.
        This holds on cgroup-v2 hosts running kernel >= 4.18. On earlier
        kernels or cgroup-v1 hybrids, the IDs may differ. The caller must
        validate this assumption on the target host before relying on BPF
        cgroup ID mapping.

    Args:
        cgroup_root: Path to the cgroup-v2 root (default:
            ``/sys/fs/cgroup``).

    Returns:
        ``{cgroup_id: entity_key}`` mapping.

    Raises:
        OSError: On filesystem read failures.
    """
    if cgroup_root is None:
        cgroup_root = Path("/sys/fs/cgroup")

    result: dict[int, str] = {}
    root_stat = cgroup_root.stat()
    result[root_stat.st_ino] = ""  # root entity

    for path in cgroup_root.rglob("*"):
        if not path.is_dir():
            continue
        # Skip hidden/control directories
        if path.name.startswith("."):
            continue
        try:
            cid = path.stat().st_ino
        except OSError:
            continue
        rel = path.relative_to(cgroup_root)
        result[cid] = str(rel)

    return result


def _subprocess_runner(argv: list[str]) -> str:
    """Default command runner: delegate to ``subprocess.check_output``.

    Never invokes a shell. Returns stdout as text.
    """
    import subprocess

    return subprocess.check_output(argv, timeout=30).decode("utf-8")
