"""Boundary contracts for decoded BPF snapshot input and recovery."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from topos.daemon import bpf_snapshot
from topos.daemon.bpf_snapshot import (
    BpfSnapshotBridge,
    BpfSnapshotError,
    _walk_cgroup_ids,
)


def _bridge(tmp_path: Path, *, resolver: object | None = None) -> BpfSnapshotBridge:
    root = tmp_path / "pins"
    root.mkdir()
    (root / "counter-map").touch()
    kwargs = {} if resolver is None else {"cgroup_id_resolver": resolver}
    return BpfSnapshotBridge(root, command_runner=lambda _argv: "[]", **kwargs)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cgroup_id": 17,
        "direction": "ingress",
        "family": "ipv4",
        "proto": "tcp",
        "bytes": 12,
        "packets": 3,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cgroup_id", True, "cgroup_id"),
        ("cgroup_id", -1, "negative cgroup_id"),
        ("direction", 7, "invalid direction"),
        ("proto", "gre", "invalid proto"),
        ("bytes", True, "invalid 'bytes'"),
        ("packets", -1, "negative packets"),
    ],
)
def test_decoded_rows_reject_invalid_types_and_ranges(
    field: str, value: object, message: str
) -> None:
    """Decoded BTF fields must be typed and within the counter contract."""
    with pytest.raises(BpfSnapshotError, match=message):
        BpfSnapshotBridge._parse_bpftool_output(json.dumps([_row(**{field: value})]))


def test_decoded_row_accepts_integral_float_but_rejects_fractional_counter() -> None:
    """JSON numbers may be integral floats, never silently truncated fractions."""
    accepted = BpfSnapshotBridge._parse_bpftool_output(
        json.dumps([_row(cgroup_id=17.0, bytes=12.0, packets=3.0)])
    )
    assert accepted[0]["cgroup_id"] == 17
    assert accepted[0]["bytes"] == 12

    with pytest.raises(BpfSnapshotError, match="invalid 'bytes'"):
        BpfSnapshotBridge._parse_bpftool_output(json.dumps([_row(bytes=12.5)]))


def test_nested_key_value_fallback_rejects_missing_top_level_dimension() -> None:
    """Partial BTF objects cannot make a missing logical dimension look valid."""
    row = {
        "key": {"cgroup_id": 17},
        "value": {"bytes": 12, "packets": 3},
        "family": "ipv4",
        "proto": "tcp",
    }
    with pytest.raises(BpfSnapshotError, match="invalid direction"):
        BpfSnapshotBridge._parse_bpftool_output(json.dumps([row]))


def test_partial_btf_key_falls_back_to_top_level_cgroup_id() -> None:
    """A partially decoded key may safely use its documented top-level field."""
    row = {
        "key": {"direction": "ingress"},
        "value": {"bytes": 12, "packets": 3},
        "cgroup_id": 17,
        "family": "ipv4",
        "proto": "tcp",
    }

    decoded = BpfSnapshotBridge._parse_bpftool_output(json.dumps([row]))

    assert decoded == [_row()]


def test_fractional_json_number_is_not_silently_truncated() -> None:
    """A non-integral JSON number is not a valid BPF integer dimension."""
    assert bpf_snapshot._pop_int({"counter": 1.5}, "counter", 0) is None


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        [],
        {"schema_version": 2, "maps": {}, "cgroup_map": {}},
        {"schema_version": 1, "maps": [], "cgroup_map": {}},
        {"schema_version": 1, "maps": {}, "cgroup_map": []},
    ],
)
def test_restore_rejects_invalid_json_shapes_without_replacing_memory(
    tmp_path: Path, snapshot: object
) -> None:
    """Only a complete P18-shaped disk snapshot may become stale fallback data."""
    bridge = _bridge(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    bridge.restore_last_known_good(state)

    assert bridge.last_valid_snapshot is None


def test_restore_rejects_malformed_json_without_raising(tmp_path: Path) -> None:
    """A torn fallback file is ignored rather than becoming daemon failure data."""
    bridge = _bridge(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "snapshot.json").write_text("{not-json", encoding="utf-8")

    bridge.restore_last_known_good(state)

    assert bridge.last_valid_snapshot is None


@pytest.mark.parametrize(
    "resolver_result",
    [
        {17: "service.scope", 18: ""},
        {17: ""},
    ],
)
def test_cgroup_map_preserves_valid_resolver_entries(
    tmp_path: Path, resolver_result: dict[int, str]
) -> None:
    """The snapshot boundary serializes every valid resolver id as a string key."""
    bridge = _bridge(tmp_path, resolver=lambda: resolver_result)
    assert bridge._build_cgroup_map() == {
        str(cgroup_id): entity for cgroup_id, entity in resolver_result.items()
    }


def test_default_subprocess_runner_reports_timeout_as_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production argv runner converts a timeout into BpfSnapshotError."""
    def timeout(_argv: list[str], **_kwargs: object) -> bytes:
        raise subprocess.TimeoutExpired("bpftool", timeout=30)

    monkeypatch.setattr(subprocess, "check_output", timeout)

    with pytest.raises(BpfSnapshotError, match="timed out after 30s"):
        bpf_snapshot._subprocess_runner(["bpftool", "--json"])


def test_constructor_rejects_unresolvable_bpf_root(tmp_path: Path) -> None:
    """A bridge cannot be constructed around a vanished or misspelled pin root."""
    with pytest.raises(BpfSnapshotError, match="cannot resolve BPF root"):
        BpfSnapshotBridge(tmp_path / "not-a-pin-root")


def test_bridge_exposes_resolved_bpf_root_and_walker_skips_non_entities(
    tmp_path: Path,
) -> None:
    """The resolver retains directories only, excluding files and hidden controls."""
    bridge = _bridge(tmp_path)
    assert bridge.bpf_root == (tmp_path / "pins").resolve()

    root = tmp_path / "cgroup"
    child = root / "service.scope"
    child.mkdir(parents=True)
    (root / "cgroup.procs").write_text("123\n", encoding="utf-8")
    (root / ".internal").mkdir()

    mapping = _walk_cgroup_ids(root)

    assert mapping[root.stat().st_ino] == ""
    assert mapping[child.stat().st_ino] == "service.scope"
    assert ".internal" not in mapping.values()


def test_walker_default_root_and_racing_child_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default root is used and a vanished cgroup child is ignored."""
    root = tmp_path / "cgroup"
    root.mkdir()
    raced_child = root / "vanished.scope"
    raced_child.mkdir()

    real_path = bpf_snapshot.Path
    monkeypatch.setattr(bpf_snapshot, "Path", lambda _value: root)

    class _RacingChild:
        name = "vanished.scope"

        def is_dir(self) -> bool:
            return True

        def stat(self) -> object:
            raise OSError("removed while walking")

    real_rglob = real_path.rglob

    def rglob_with_race(path: Path, pattern: str) -> object:
        if path == root:
            return [_RacingChild()]
        return real_rglob(path, pattern)

    monkeypatch.setattr(real_path, "rglob", rglob_with_race)

    mapping = _walk_cgroup_ids()

    assert mapping == {root.stat().st_ino: ""}


def test_bpftool_nonzero_without_stderr_keeps_typed_exit_context(tmp_path: Path) -> None:
    """An empty stderr must not erase the command's nonzero-exit diagnosis."""
    bridge = _bridge(tmp_path)

    def failed(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(9, argv)

    bridge._command_runner = failed

    with pytest.raises(BpfSnapshotError, match=r"^bpftool exited 9$"):
        bridge._run_bpftool(tmp_path / "pins" / "counter-map")


def test_default_subprocess_runner_keeps_exit_context_without_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native bpftool failure without stderr remains a typed failure."""
    def failed(argv: list[str], **_kwargs: object) -> bytes:
        raise subprocess.CalledProcessError(9, argv)

    monkeypatch.setattr(subprocess, "check_output", failed)

    with pytest.raises(BpfSnapshotError, match=r"^bpftool exited 9$"):
        bpf_snapshot._subprocess_runner(["bpftool", "--json"])
