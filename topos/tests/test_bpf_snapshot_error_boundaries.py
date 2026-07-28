"""Deterministic failure-contract tests for the BPF snapshot bridge."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from topos.daemon import bpf_snapshot
from topos.daemon.bpf_snapshot import BpfSnapshotBridge, BpfSnapshotError


def _bridge(tmp_path: Path, **kwargs: object) -> BpfSnapshotBridge:
    root = tmp_path / "bpf-root"
    root.mkdir()
    (root / "map").touch()
    return BpfSnapshotBridge(root, command_runner=lambda _argv: "[]", **kwargs)


def test_missing_map_pin_is_a_typed_confinement_error(tmp_path: Path) -> None:
    """A map name inside the root still must refer to an existing pinned map."""
    bridge = _bridge(tmp_path)

    with pytest.raises(BpfSnapshotError, match="does not exist"):
        bridge._validate_map_path("not-pinned")


def test_snapshot_replace_failure_cleans_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed atomic replace is typed and leaves no bridge-owned temp file."""
    bridge = _bridge(tmp_path)

    def fail_replace(_source: Path, _dest: Path) -> None:
        raise OSError("read-only state directory")

    monkeypatch.setattr(bpf_snapshot.os, "replace", fail_replace)

    with pytest.raises(BpfSnapshotError, match="failed to write snapshot"):
        bridge.write_snapshot({"schema_version": 1}, tmp_path)

    assert not list(tmp_path.glob(".snapshot.json.*.tmp"))
    assert not (tmp_path / "snapshot.json").exists()


def test_bpftool_oserror_is_wrapped_with_operation_context(tmp_path: Path) -> None:
    """Runner filesystem errors never leak as an untyped daemon failure."""
    bridge = _bridge(tmp_path)

    def unavailable(_argv: list[str]) -> str:
        raise OSError("operation not permitted")

    bridge._command_runner = unavailable

    with pytest.raises(BpfSnapshotError, match="bpftool execution failed"):
        bridge._run_bpftool(tmp_path / "bpf-root" / "map")


def test_parser_rejects_list_valued_map_data() -> None:
    """Per-CPU list values cannot be mistaken for one logical counter row."""
    with pytest.raises(BpfSnapshotError, match="per-CPU"):
        BpfSnapshotBridge._parse_bpftool_output(
            '[{"key":{"cgroup_id":7,"direction":"ingress"},'
            '"value":[{"bytes":1,"packets":1}]}]'
        )


def test_subprocess_runner_bounds_stderr_and_preserves_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production runner exposes a typed, bounded diagnostic on failure."""
    def failed(argv: list[str], **_kwargs: object) -> bytes:
        raise subprocess.CalledProcessError(23, argv, stderr=b"x" * 700)

    monkeypatch.setattr(subprocess, "check_output", failed)

    with pytest.raises(BpfSnapshotError) as raised:
        bpf_snapshot._subprocess_runner(["bpftool", "--json"])

    message = str(raised.value)
    assert message.startswith("bpftool exited 23: ")
    assert len(message.removeprefix("bpftool exited 23: ")) == 512
