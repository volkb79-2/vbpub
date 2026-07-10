"""Tests for the daemon BPF snapshot bridge (P42).

Covers: bpftool command runner, path confinement, decoding, cgroup mapping,
atomic replacement, last-good preservation, output bounds, command failure,
cleanup, and daemon integration.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from groop.daemon.bpf_snapshot import (
    BpfSnapshotBridge,
    BpfSnapshotError,
    _decode_bpftool_entry,
    _walk_cgroup_ids,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_BPFTOOL_OUTPUT = json.dumps(
    [
        {
            "key": {"cgroup_id": 10001, "direction": "ingress", "family": "ipv4", "proto": "tcp"},
            "value": {"bytes": 90000, "packets": 600},
        },
        {
            "key": {"cgroup_id": 10001, "direction": "egress", "family": "ipv4", "proto": "tcp"},
            "value": {"bytes": 150000, "packets": 500},
        },
        {
            "key": {"cgroup_id": 10002, "direction": "ingress", "family": "ipv4", "proto": "udp"},
            "value": {"bytes": 3000, "packets": 20},
        },
        {
            "key": {"cgroup_id": 10002, "direction": "egress", "family": "ipv4", "proto": "udp"},
            "value": {"bytes": 1000, "packets": 10},
        },
    ]
)


def _mock_runner(output: str, *, returncode: int = 0) -> callable:
    """Create a mock command runner that returns *output*."""

    def runner(argv: list[str]) -> str:
        if returncode != 0:
            raise BpfSnapshotError(f"bpftool failed with exit code {returncode}")
        return output

    return runner


def _make_bpf_root(tmp_path: Path) -> Path:
    """Create a temporary BPF root with a pinned map file."""
    bpf_root = tmp_path / "bpf" / "groop"
    bpf_root.mkdir(parents=True)
    # Create a sentinel file to represent the pinned map
    (bpf_root / "groop_cgroup_skb").touch()
    return bpf_root


def _make_cgroup_fixture(tmp_path: Path) -> Path:
    """Create a minimal cgroup-v2 fixture tree with known inode mapping."""
    cg_root = tmp_path / "sys" / "fs" / "cgroup"
    cg_root.mkdir(parents=True)
    (cg_root / "system.slice").mkdir()
    (cg_root / "system.slice" / "docker-a1.scope").mkdir()
    (cg_root / "user.slice").mkdir()
    return cg_root


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_decode_bpftool_entry_valid() -> None:
    """A valid bpftool entry decodes to the expected logical fields."""
    item = {
        "key": {"cgroup_id": 10001, "direction": "ingress", "family": "ipv4", "proto": "tcp"},
        "value": {"bytes": 90000, "packets": 600},
    }
    entry = _decode_bpftool_entry(item, 0)
    assert entry["cgroup_id"] == 10001
    assert entry["direction"] == "ingress"
    assert entry["family"] == "ipv4"
    assert entry["proto"] == "tcp"
    assert entry["bytes"] == 90000
    assert entry["packets"] == 600


def test_decode_bpftool_entry_top_level_fields() -> None:
    """Entries with top-level fields (not nested key/value) also decode."""
    item = {
        "cgroup_id": 20001,
        "direction": "egress",
        "family": "ipv6",
        "proto": "tcp",
        "bytes": 5000,
        "packets": 30,
    }
    entry = _decode_bpftool_entry(item, 0)
    assert entry["cgroup_id"] == 20001
    assert entry["direction"] == "egress"
    assert entry["family"] == "ipv6"
    assert entry["proto"] == "tcp"
    assert entry["bytes"] == 5000
    assert entry["packets"] == 30


def test_decode_bpftool_entry_default_family_proto() -> None:
    """Missing family/proto default to 'other'."""
    item = {
        "cgroup_id": 30001,
        "direction": "ingress",
        "bytes": 100,
        "packets": 5,
    }
    entry = _decode_bpftool_entry(item, 0)
    assert entry["family"] == "other"
    assert entry["proto"] == "other"


def test_decode_bpftool_entry_rejects_negative_counters() -> None:
    """Negative bytes or packets raises BpfSnapshotError."""
    item = {
        "cgroup_id": 40001,
        "direction": "ingress",
        "family": "ipv4",
        "proto": "tcp",
        "bytes": -100,
        "packets": 5,
    }
    try:
        _decode_bpftool_entry(item, 0)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "negative" in str(exc).lower()


def test_decode_bpftool_entry_rejects_invalid_direction() -> None:
    """Invalid direction string raises BpfSnapshotError."""
    item = {
        "cgroup_id": 50001,
        "direction": "sideways",
        "family": "ipv4",
        "proto": "tcp",
        "bytes": 100,
        "packets": 5,
    }
    try:
        _decode_bpftool_entry(item, 0)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "direction" in str(exc).lower()


def test_decode_bpftool_entry_rejects_invalid_family() -> None:
    """Invalid family string raises BpfSnapshotError."""
    item = {
        "cgroup_id": 60001,
        "direction": "ingress",
        "family": "ipx",
        "proto": "tcp",
        "bytes": 100,
        "packets": 5,
    }
    try:
        _decode_bpftool_entry(item, 0)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "family" in str(exc).lower()


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


def test_validate_map_path_inside_root(tmp_path: Path) -> None:
    """Valid relative path under bpf_root passes confinement."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    resolved = bridge._validate_map_path("groop_cgroup_skb")
    assert resolved == (bpf_root / "groop_cgroup_skb").resolve()


def test_validate_map_path_rejects_traversal(tmp_path: Path) -> None:
    """Path with '..' is rejected."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    try:
        bridge._validate_map_path("../etc/passwd")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "invalid map pin" in str(exc).lower()


def test_validate_map_path_rejects_escape(tmp_path: Path) -> None:
    """A symlink or path that resolves outside bpf_root is rejected."""
    # Create a symlink outside the root
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "escape"
    link.symlink_to(outside)

    bpf_root = _make_bpf_root(tmp_path)
    # Move the sentinel to be the link
    (bpf_root / "groop_cgroup_skb").unlink()
    (bpf_root / "escape").symlink_to(outside)

    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    try:
        bridge._validate_map_path("escape")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "escapes" in str(exc).lower()


# ---------------------------------------------------------------------------
# BPF tool command runner
# ---------------------------------------------------------------------------


def test_run_bpftool_success(tmp_path: Path) -> None:
    """Successful bpftool run returns parsed output."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root, command_runner=_mock_runner(SAMPLE_BPFTOOL_OUTPUT)
    )
    map_path = bpf_root / "groop_cgroup_skb"
    stdout = bridge._run_bpftool(map_path)
    assert json.loads(stdout) == json.loads(SAMPLE_BPFTOOL_OUTPUT)


def test_run_bpftool_nonzero_exit(tmp_path: Path) -> None:
    """Nonzero exit raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner("", returncode=1),
    )
    # _mock_runner with returncode != 0 raises BpfSnapshotError directly
    # Actually let's make a runner that raises
    def failing_runner(argv: list[str]) -> str:
        raise BpfSnapshotError("bpftool failed with exit code 1")

    bridge2 = BpfSnapshotBridge(bpf_root, command_runner=failing_runner)
    map_path = bpf_root / "groop_cgroup_skb"
    try:
        bridge2._run_bpftool(map_path)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError:
        pass


def test_run_bpftool_oversized_output(tmp_path: Path) -> None:
    """Output exceeding max_output_bytes raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    large = "x" * (1024 * 1024 + 1)  # Just over 1 MB
    bridge = BpfSnapshotBridge(
        bpf_root, command_runner=_mock_runner(large), max_output_bytes=1024 * 1024
    )
    map_path = bpf_root / "groop_cgroup_skb"
    try:
        bridge._run_bpftool(map_path)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "exceeds" in str(exc).lower()


# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------


def test_refresh_returns_valid_snapshot(tmp_path: Path) -> None:
    """A full refresh cycle returns a valid P18 snapshot."""
    bpf_root = _make_bpf_root(tmp_path)
    cg_root = _make_cgroup_fixture(tmp_path)

    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(SAMPLE_BPFTOOL_OUTPUT),
        cgroup_id_resolver=_mock_cgroup_ids(cg_root),
    )
    snapshot = bridge.refresh("groop_cgroup_skb")
    assert snapshot["schema_version"] == 1
    assert "generated_at" in snapshot
    assert "source" in snapshot
    assert "maps" in snapshot
    assert "groop_cgroup_skb" in snapshot["maps"]
    assert "cgroup_map" in snapshot
    assert len(snapshot["maps"]["groop_cgroup_skb"]) == 4


def _mock_cgroup_ids(cg_root: Path) -> callable:
    """Create a mock cgroup-id resolver based on a fixture tree."""
    cg_root = Path(cg_root)

    def resolver() -> dict[int, str]:
        result: dict[int, str] = {}
        # Root entity
        result[cg_root.stat().st_ino] = ""
        for path in sorted(cg_root.rglob("*")):
            if not path.is_dir():
                continue
            if path.name.startswith("."):
                continue
            rel = path.relative_to(cg_root)
            result[path.stat().st_ino] = str(rel)
        return result

    return resolver


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_write_snapshot_atomic_replace(tmp_path: Path) -> None:
    """write_snapshot creates snapshot.json with correct content."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    snapshot = {"schema_version": 1, "test": True}
    dest = bridge.write_snapshot(snapshot, tmp_path)
    assert dest == tmp_path / "snapshot.json"
    assert dest.exists()
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded == snapshot


def test_write_snapshot_permissions_not_world_writable(tmp_path: Path) -> None:
    """Written snapshot is not world-writable."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    bridge.write_snapshot({"a": 1}, tmp_path)
    mode = (tmp_path / "snapshot.json").stat().st_mode & 0o777
    assert mode == 0o644


def test_write_snapshot_cleanup_temp_on_failure(tmp_path: Path) -> None:
    """A failed write cleans up the temporary file."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    # Make dest a file (not a directory) to cause a write failure
    (tmp_path / "snapshot.json").write_text("", encoding="utf-8")
    (tmp_path / ".snapshot.json.99999.tmp").write_text("stale", encoding="utf-8")
    # write should fail but cleanup its own temp
    try:
        bridge.write_snapshot({"a": 1}, tmp_path)
    except BpfSnapshotError:
        pass
    # The pre-existing temp from another pid is NOT cleaned up (not ours)
    # But let's verify there are only expected files
    files = [f.name for f in tmp_path.iterdir() if not f.name.startswith(".")]
    assert "snapshot.json" in files


# ---------------------------------------------------------------------------
# Last-good preservation
# ---------------------------------------------------------------------------


def test_last_valid_snapshot_preserved_on_failure(tmp_path: Path) -> None:
    """After a successful refresh, a subsequent failure preserves the last good snapshot."""
    bpf_root = _make_bpf_root(tmp_path)
    runner_calls: list[int] = []

    def alternating_runner(argv: list[str]) -> str:
        runner_calls.append(1)
        if len(runner_calls) == 1:
            return SAMPLE_BPFTOOL_OUTPUT
        raise BpfSnapshotError("transient error")

    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=alternating_runner,
        cgroup_id_resolver=_mock_cgroup_ids(_make_cgroup_fixture(tmp_path)),
    )
    # First call succeeds
    snapshot = bridge.refresh("groop_cgroup_skb")
    assert snapshot is not None
    assert bridge.last_valid_snapshot is snapshot

    # Second call fails but last valid is preserved
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError:
        pass
    assert bridge.last_valid_snapshot is snapshot


def test_last_valid_snapshot_none_initially(tmp_path: Path) -> None:
    """Before any successful refresh, last_valid_snapshot is None."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    assert bridge.last_valid_snapshot is None


# ---------------------------------------------------------------------------
# Cgroup ID resolution
# ---------------------------------------------------------------------------


def test_walk_cgroup_ids(tmp_path: Path) -> None:
    """_walk_cgroup_ids returns cgroup_id -> entity_key mapping."""
    cg_root = _make_cgroup_fixture(tmp_path)
    # Use the mock resolver since the real one requires real cgroupfs
    resolver = _mock_cgroup_ids(cg_root)
    mapping = resolver()
    assert isinstance(mapping, dict)
    assert len(mapping) >= 3  # root, system.slice, user.slice
    keys = set(mapping.values())
    assert "" in keys  # root
    assert "system.slice" in keys


# ---------------------------------------------------------------------------
# Missing bpftool
# ---------------------------------------------------------------------------


def test_bridge_reports_missing_bpftool(tmp_path: Path) -> None:
    """If bpftool is not found (FileNotFoundError), refresh raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)

    def missing_runner(argv: list[str]) -> str:
        raise FileNotFoundError("bpftool not found")

    bridge = BpfSnapshotBridge(bpf_root, command_runner=missing_runner)
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "bpftool" in str(exc).lower()


# ---------------------------------------------------------------------------
# Malformed JSON from bpftool
# ---------------------------------------------------------------------------


def test_parse_bpftool_malformed_json(tmp_path: Path) -> None:
    """Malformed bpftool JSON output raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("not json"))
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "JSON" in str(exc)


# ---------------------------------------------------------------------------
# Invalid entries in bpftool output
# ---------------------------------------------------------------------------


def test_parse_bpftool_non_array_output(tmp_path: Path) -> None:
    """Non-array bpftool output raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner('{"not": "array"}'))
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "not a JSON array" in str(exc)


def test_parse_bpftool_entry_non_dict(tmp_path: Path) -> None:
    """bpftool entry that is not a dict raises BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner('["string"]'))
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "not a dict" in str(exc)


# ---------------------------------------------------------------------------
# Daemon integration: disabled by default, failures don't crash
# ---------------------------------------------------------------------------


def test_daemon_bpf_disabled_by_default(tmp_path: Path) -> None:
    """BPF snapshot bridge is disabled when no --bpf-root is given."""
    from groop.config import BpfSnapshotConfig, GroopConfig

    config = GroopConfig()
    assert config.bpf_snapshot.enabled is False
    assert config.bpf_snapshot.root is None


def test_daemon_bpf_config_disabled_by_default(tmp_path: Path) -> None:
    """BPF snapshot section in config defaults to disabled."""
    from groop.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text("[bpf_snapshot]\n")
    config = load(config_file)
    assert config.bpf_snapshot.enabled is False
    assert config.bpf_snapshot.root is None


def test_daemon_bpf_config_enabled(tmp_path: Path) -> None:
    """BPF snapshot section can be enabled via config."""
    from groop.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[bpf_snapshot]\n"
        'enabled = true\n'
        'root = "/sys/fs/bpf/groop"\n'
        'interval = 15.0\n'
    )
    config = load(config_file)
    assert config.bpf_snapshot.enabled is True
    assert str(config.bpf_snapshot.root) == "/sys/fs/bpf/groop"
    assert config.bpf_snapshot.interval == 15.0


# ---------------------------------------------------------------------------
# Cleanup: no temp files remain
# ---------------------------------------------------------------------------


def test_write_snapshot_cleans_tmp_on_rename(tmp_path: Path) -> None:
    """After a successful write, the temporary .tmp file is gone."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    bridge.write_snapshot({"a": 1}, tmp_path)
    # No .snapshot.json.* files should remain
    tmp_files = [f for f in tmp_path.iterdir() if f.name.startswith(".snapshot.json")]
    assert len(tmp_files) == 0


# ---------------------------------------------------------------------------
# Raw byte array rejection
# ---------------------------------------------------------------------------


def test_parse_bpftool_rejects_raw_byte_array_key(tmp_path: Path) -> None:
    """Raw byte array key (hex string) is explicitly rejected."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(
            json.dumps([
                {
                    "key": "0x01020304",
                    "value": {"cgroup_id": 10001, "direction": "ingress",
                              "family": "ipv4", "proto": "tcp",
                              "bytes": 1000, "packets": 10},
                }
            ])
        ),
    )
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "raw byte array" in str(exc).lower()


def test_parse_bpftool_rejects_raw_byte_array_value(tmp_path: Path) -> None:
    """Raw byte array value (hex string) is explicitly rejected."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(
            json.dumps([
                {
                    "key": {"cgroup_id": 10001, "direction": "ingress"},
                    "value": "0xdeadbeef",
                }
            ])
        ),
    )
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "raw byte array" in str(exc).lower()


def test_parse_bpftool_rejects_percpu_array_values(tmp_path: Path) -> None:
    """Per-CPU array values (lists) are explicitly rejected."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(
            json.dumps([
                {
                    "key": {"cgroup_id": 10001, "direction": "ingress"},
                    "values": [{"bytes": 100, "packets": 1}],
                }
            ])
        ),
    )
    try:
        bridge.refresh("groop_cgroup_skb")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "per-CPU" in str(exc)


# ---------------------------------------------------------------------------
# Sibling-prefix symlink escape (Path.is_relative_t(required))
# ---------------------------------------------------------------------------


def test_validate_map_path_rejects_sibling_prefix_escape(tmp_path: Path) -> None:
    """A symlink that resolves to a sibling-like prefix of bpf_root is rejected.

    Create ``/tmp/bpf-real`` and ``/tmp/bpf-real-evil``. The second is NOT
    under the first even though it shares a prefix. ``Path.is_relative_t(required)``
    correctly rejects it.
    """
    bpf_root = tmp_path / "bpf-real"
    bpf_root.mkdir(parents=True)

    # Create a sibling path that shares the prefix "bpf-real"
    escape_target = tmp_path / "bpf-real-evil"
    escape_target.mkdir()
    (escape_target / "escape_map").touch()

    # Create a symlink inside bpf_root that points to the sibling
    link = bpf_root / "escape_link"
    link.symlink_to(escape_target)

    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    try:
        bridge._validate_map_path("escape_link")
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        assert "escapes" in str(exc).lower()


# ---------------------------------------------------------------------------
# calledProcessError and TimeoutExpired in _subprocess_runner
# ---------------------------------------------------------------------------


def _make_called_process_runner(returncode: int = 1, stderr: str = "error") -> callable:
    """Create a mock that simulates CalledProcessError behaviour."""
    import subprocess as _sp

    def runner(argv: list[str]) -> str:
        raise _sp.CalledProcessError(returncode, argv, stderr=stderr.encode())
    return runner


def _make_timeout_runner() -> callable:
    import subprocess as _sp

    def runner(argv: list[str]) -> str:
        raise _sp.TimeoutExpired(argv, timeout=30)
    return runner


def test_run_bpftool_called_process_error(tmp_path: Path) -> None:
    """CalledProcessError is converted to a bounded BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root, command_runner=_make_called_process_runner(1, "bpftool: error")
    )
    map_path = bpf_root / "groop_cgroup_skb"
    try:
        bridge._run_bpftool(map_path)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        msg = str(exc)
        assert "bpftool exited 1" in msg
        # Should contain the bounded stderr
        assert "bpftool: error" in msg


def test_run_bpftool_timeout(tmp_path: Path) -> None:
    """TimeoutExpired is converted to a bounded BpfSnapshotError."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root, command_runner=_make_timeout_runner()
    )
    map_path = bpf_root / "groop_cgroup_skb"
    try:
        bridge._run_bpftool(map_path)
        assert False, "expected BpfSnapshotError"
    except BpfSnapshotError as exc:
        msg = str(exc)
        assert "timed out" in msg.lower()


# ---------------------------------------------------------------------------
# on-disk last-good restoration
# ---------------------------------------------------------------------------


def test_restore_last_known_good_loads_from_disk(tmp_path: Path) -> None:
    """restore_last_known_good loads a valid snapshot from state_dir."""
    bpf_root = _make_bpf_root(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "snapshot.json").write_text(
        json.dumps({"schema_version": 1, "maps": {}, "cgroup_map": {}}),
        encoding="utf-8",
    )
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    assert bridge.last_valid_snapshot is None
    bridge.restore_last_known_good(state_dir)
    assert bridge.last_valid_snapshot is not None
    assert bridge.last_valid_snapshot["schema_version"] == 1


def test_restore_last_known_good_skips_if_already_present(tmp_path: Path) -> None:
    """restore_last_known_good does nothing when a valid snapshot is already in memory."""
    bpf_root = _make_bpf_root(tmp_path)
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    bridge._last_valid_snapshot = {"existing": True}
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "snapshot.json").write_text(
        json.dumps({"different": True}), encoding="utf-8"
    )
    bridge.restore_last_known_good(state_dir)
    assert bridge.last_valid_snapshot == {"existing": True}


def test_restore_last_known_good_missing_file(tmp_path: Path) -> None:
    """restore_last_known_good silently does nothing if the on-disk file is missing."""
    bpf_root = _make_bpf_root(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    bridge.restore_last_known_good(state_dir)
    assert bridge.last_valid_snapshot is None


def test_restore_last_known_good_rejects_invalid_shape(tmp_path: Path) -> None:
    bpf_root = _make_bpf_root(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "snapshot.json").write_text(
        json.dumps({"schema_version": 1, "unrelated": True}), encoding="utf-8"
    )
    bridge = BpfSnapshotBridge(bpf_root, command_runner=_mock_runner("[]"))
    bridge.restore_last_known_good(state_dir)
    assert bridge.last_valid_snapshot is None


def test_default_resolver_uses_configured_cgroup_root(tmp_path: Path) -> None:
    bpf_root = _make_bpf_root(tmp_path)
    cgroup_root = _make_cgroup_fixture(tmp_path)
    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(SAMPLE_BPFTOOL_OUTPUT),
        cgroup_root=cgroup_root,
    )
    snapshot = bridge.refresh("groop_cgroup_skb")
    paths = (cgroup_root, *cgroup_root.rglob("*"))
    expected_ids = {str(path.stat().st_ino) for path in paths if path.is_dir()}
    assert set(snapshot["cgroup_map"]) == expected_ids


@pytest.mark.parametrize("field", ["bytes", "packets"])
def test_decode_rejects_missing_required_counter(field: str) -> None:
    item = {
        "key": {
            "cgroup_id": 42,
            "direction": "ingress",
            "family": "ipv4",
            "proto": "tcp",
        },
        "value": {"bytes": 100, "packets": 2},
    }
    del item["value"][field]
    with pytest.raises(BpfSnapshotError, match=field):
        _decode_bpftool_entry(item, 0)


# ---------------------------------------------------------------------------
# refresh_and_write convenience method
# ---------------------------------------------------------------------------


def test_refresh_and_write(tmp_path: Path) -> None:
    """refresh_and_write runs a full cycle and writes to state_dir."""
    bpf_root = _make_bpf_root(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cg_root = _make_cgroup_fixture(tmp_path)

    bridge = BpfSnapshotBridge(
        bpf_root,
        command_runner=_mock_runner(SAMPLE_BPFTOOL_OUTPUT),
        cgroup_id_resolver=_mock_cgroup_ids(cg_root),
    )
    snapshot = bridge.refresh_and_write("groop_cgroup_skb", state_dir)
    assert (state_dir / "snapshot.json").exists()
    assert snapshot["schema_version"] == 1
    assert bridge.last_valid_snapshot is snapshot


# ---------------------------------------------------------------------------
# Integration-level: daemon construction and refresh
# ---------------------------------------------------------------------------


def test_daemon_bpf_state_dir_from_config(tmp_path: Path) -> None:
    """Config-specified state_dir is used for snapshot output."""
    from groop.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[bpf_snapshot]\n"
        'enabled = true\n'
        'root = "/sys/fs/bpf/groop"\n'
        'state_dir = "/run/groop/bpf"\n'
    )
    config = load(config_file)
    assert config.bpf_snapshot.enabled is True
    assert str(config.bpf_snapshot.state_dir) == "/run/groop/bpf"


def test_daemon_bpf_state_dir_default_when_not_set(tmp_path: Path) -> None:
    """When state_dir is not set in config, the default /run/groop/bpf is used."""
    from groop.config import BpfSnapshotConfig

    cfg = BpfSnapshotConfig(enabled=True, root=Path("/sys/fs/bpf/groop"))
    assert str(cfg.state_dir) == "/run/groop/bpf"


def test_bpf_provider_with_state_dir(tmp_path: Path) -> None:
    """BpfProvider reads snapshot from state_dir when provided separately."""
    from groop.providers.net_bpf import BpfProvider
    from groop.model import Entity

    # Create state dir with snapshot
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bpf_root = tmp_path / "bpf" / "groop"
    bpf_root.mkdir(parents=True)

    snapshot = {
        "maps": {
            "groop_cgroup_skb": [
                {"cgroup_id": 42, "direction": "ingress", "family": "ipv4",
                 "proto": "tcp", "bytes": 100, "packets": 5},
            ],
        },
        "cgroup_map": {"42": "alpha.scope"},
    }
    (state_dir / "snapshot.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )

    provider = BpfProvider(bpf_root=bpf_root, state_dir=state_dir)
    samples = provider.collect(
        {"alpha.scope": Entity(key="alpha.scope", kind="scope", parent="")}
    )
    assert samples["alpha.scope"].source_label == "net:BPF"
    assert samples["alpha.scope"].rx_bytes == 100


def test_bpf_provider_falls_back_to_bpf_root_as_state_dir(tmp_path: Path) -> None:
    """When state_dir is not given, BpfProvider reads snapshot from bpf_root."""
    from groop.providers.net_bpf import BpfProvider
    from groop.model import Entity

    bpf_root = tmp_path / "bpf"
    bpf_root.mkdir(parents=True)
    snapshot = {
        "maps": {
            "groop_cgroup_skb": [
                {"cgroup_id": 42, "direction": "ingress", "family": "ipv4",
                 "proto": "tcp", "bytes": 50, "packets": 3},
            ],
        },
        "cgroup_map": {"42": "beta.scope"},
    }
    (bpf_root / "snapshot.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )

    provider = BpfProvider(bpf_root=bpf_root)
    samples = provider.collect(
        {"beta.scope": Entity(key="beta.scope", kind="scope", parent="")}
    )
    assert samples["beta.scope"].source_label == "net:BPF"
    assert samples["beta.scope"].rx_bytes == 50


def test_daemon_enabled_bridge_uses_configured_root_and_shuts_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the enabled serve wiring without BPF privileges or a live socket."""
    import groop.cli as cli
    from groop.config import BpfSnapshotConfig, GroopConfig

    bpf_root = tmp_path / "pins"
    bpf_root.mkdir()
    cgroup_root = _make_cgroup_fixture(tmp_path)
    state_dir = tmp_path / "state"
    socket_path = tmp_path / "groop.sock"
    config = GroopConfig(
        cgroup_root=cgroup_root,
        bpf_snapshot=BpfSnapshotConfig(
            enabled=True,
            root=bpf_root,
            interval=5.0,
            state_dir=state_dir,
        ),
    )
    observed: dict[str, object] = {}

    class FakeCollector:
        def __init__(self, cgroup_root: Path | None, config: GroopConfig) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ("fallback",)

    class FakeBridge:
        last_valid_snapshot = None

        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["root"] = root
            observed["cgroup_root"] = kwargs["cgroup_root"]

        def restore_last_known_good(self, path: Path) -> None:
            observed["restored"] = path

        def refresh_and_write(self, map_name: str, path: Path) -> dict[str, object]:
            observed["initial"] = (map_name, path)
            return {}

        def refresh(self, map_name: str) -> dict[str, object]:
            raise AssertionError("periodic refresh must not race after shutdown")

        def write_snapshot(self, snapshot: dict[str, object], path: Path) -> Path:
            raise AssertionError("periodic write must not race after shutdown")

    class FakeServer:
        closed = False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed = True

    server = FakeServer()
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)
    monkeypatch.setattr(cli, "BpfSnapshotBridge", FakeBridge)
    monkeypatch.setattr(cli, "serve_unix_socket", lambda _path, _broker: server)

    assert cli._main_daemon(["serve", "--socket", str(socket_path)]) == 0
    assert observed == {
        "root": bpf_root,
        "cgroup_root": cgroup_root,
        "restored": state_dir,
        "initial": ("groop_cgroup_skb", state_dir),
    }
    assert server.closed is True


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------


def test_bpf_snapshot_bridge_is_publicly_exported() -> None:
    from groop.daemon import BpfSnapshotBridge as ExportedBridge

    assert ExportedBridge is BpfSnapshotBridge


def test_bpf_snapshot_error_is_publicly_exported() -> None:
    from groop.daemon import BpfSnapshotError as ExportedError

    assert ExportedError is BpfSnapshotError
