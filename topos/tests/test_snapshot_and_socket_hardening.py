"""Snapshot window/redaction/mode and daemon socket takeover (B-19 items)."""

from __future__ import annotations

import json
import socket
import stat
from pathlib import Path

import pytest

from conftest import fixture_frame, fixture_root
from topos.config import SnapshotConfig, ToposConfig
from topos.daemon.broker import SocketInUseError, unlink_stale_socket
from topos.model import Frame
from topos.record.reader import RecordReader
from topos.record.ring import HistoryRing
from topos.snapshot.bundle import _extract_archive, create


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _bundle(tmp_path: Path, config: ToposConfig, **kwargs) -> Path:
    return create(
        "soulmask.slice/soulmask-paks.slice",
        HistoryRing.from_config(config),
        _frame_at(103.0),
        config,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        **kwargs,
    )


def _extract(bundle: Path, dst: Path) -> Path:
    out = dst / "bundle"
    out.mkdir()
    _extract_archive(bundle, out)
    return out


def test_single_frame_snapshot_contains_no_previous_frames(tmp_path: Path) -> None:
    config = ToposConfig(snapshots=SnapshotConfig(dir=tmp_path / "out", frames=1))
    bundle = _bundle(
        tmp_path, config, previous_frames=[_frame_at(100.0), _frame_at(101.0), _frame_at(102.0)]
    )
    root = _extract(bundle, tmp_path)
    assert [f.ts for f in RecordReader(root / "frames.jsonl")] == [103.0]


def test_redacted_snapshot_drops_command_lines_and_is_owner_only(tmp_path: Path) -> None:
    config = ToposConfig(snapshots=SnapshotConfig(dir=tmp_path / "out", redact=True))
    docker = {
        "Id": "abc",
        "Name": "demo",
        "Config": {
            "Env": ["TOKEN=x"],
            "Labels": {"k": "v"},
            "Cmd": ["serve", "--password=hunter2"],
            "Entrypoint": ["/bin/app", "--api-key=abc123"],
            "Image": "demo:1",
        },
    }
    bundle = _bundle(tmp_path, config, docker_inspect=docker)

    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    root = _extract(bundle, tmp_path)
    text = (root / "entity" / "docker-inspect.json").read_text()
    assert "hunter2" not in text and "abc123" not in text
    assert json.loads(text)["Config"] == {"Image": "demo:1"}


def test_unredacted_snapshot_keeps_the_command_line(tmp_path: Path) -> None:
    config = ToposConfig(snapshots=SnapshotConfig(dir=tmp_path / "out", redact=False))
    docker = {"Id": "abc", "Config": {"Cmd": ["serve"]}}
    root = _extract(_bundle(tmp_path, config, docker_inspect=docker), tmp_path)
    assert json.loads((root / "entity" / "docker-inspect.json").read_text())["Config"] == {"Cmd": ["serve"]}


# --- stale-socket steal ----------------------------------------------------


def test_a_live_daemon_socket_is_not_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "live.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    try:
        with pytest.raises(SocketInUseError, match="already serving"):
            unlink_stale_socket(path)
        assert path.exists()
    finally:
        listener.close()


def test_a_leftover_socket_file_with_no_listener_is_removed(tmp_path: Path) -> None:
    path = tmp_path / "stale.sock"
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(path))
    dead.close()  # file stays behind, nobody listens
    assert path.exists()

    unlink_stale_socket(path)

    assert not path.exists()
    unlink_stale_socket(path)  # missing path is fine


def test_serve_refuses_to_start_over_a_live_daemon(tmp_path: Path) -> None:
    from topos.daemon import DaemonApi, FrameBroker, serve_versioned_unix_socket

    path = tmp_path / "d.sock"
    first_broker = FrameBroker([_frame_at(1.0)])
    first = serve_versioned_unix_socket(path, first_broker, DaemonApi(first_broker))
    try:
        second_broker = FrameBroker([_frame_at(1.0)])
        with pytest.raises(SocketInUseError):
            serve_versioned_unix_socket(path, second_broker, DaemonApi(second_broker))
        assert path.exists()
    finally:
        first.server_close()
