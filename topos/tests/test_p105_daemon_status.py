"""P105 behavioral coverage for daemon client status."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from topos.daemon.client import DaemonClientError
from topos.daemon.status import (
    DaemonStatusReport,
    ProtocolStatus,
    SocketNodeStatus,
    _check_protocol,
    _check_socket_node,
    build_daemon_status,
)


def test_protocol_status_to_jsonable_includes_all_optional_fields() -> None:
    status = ProtocolStatus(ok=True, message="current", schema_version=3, frame_ts=100.25, entity_count=5)
    assert status.to_jsonable() == {
        "ok": True,
        "message": "current",
        "schema_version": 3,
        "frame_ts": 100.25,
        "entity_count": 5,
    }


def test_protocol_status_to_jsonable_omits_optional_fields() -> None:
    assert ProtocolStatus(ok=False, message="unavailable").to_jsonable() == {
        "ok": False,
        "message": "unavailable",
    }


def test_socket_node_status_serializes() -> None:
    assert SocketNodeStatus(True, "mounted socket is current").to_jsonable() == {
        "ok": True,
        "message": "mounted socket is current",
    }


def test_status_report_json_and_text_combine_socket_and_protocol() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        socket_node=SocketNodeStatus(True, "node ok"),
        protocol=ProtocolStatus(ok=True, message="hello ok"),
    )
    assert report.ok is True
    assert report.to_jsonable() == {
        "ok": True,
        "socket": "/run/example.sock",
        "group": "operators",
        "socket_node": {"ok": True, "message": "node ok"},
        "protocol": {"ok": True, "message": "hello ok"},
    }
    assert "--- Socket node ---" in report.to_text()
    assert "Overall: OK" in report.to_text()


def test_status_report_is_degraded_when_either_check_fails() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        socket_node=SocketNodeStatus(False, "not a socket"),
        protocol=ProtocolStatus(ok=True, message="hello ok", schema_version=1, frame_ts=3.0, entity_count=2),
    )
    assert report.ok is False
    assert "schema version: 1" in report.to_text()
    assert "frame timestamp: 3.0" in report.to_text()
    assert "entities: 2" in report.to_text()
    assert "Overall: DEGRADED" in report.to_text()


def test_check_protocol_returns_exact_base_client_error() -> None:
    socket_path = Path("/run/example.sock")
    with patch("topos.daemon.status.DaemonClient", side_effect=DaemonClientError("authorization failed")) as client:
        result = _check_protocol(socket_path)
    assert result == ProtocolStatus(ok=False, message="Daemon error: authorization failed")
    client.assert_called_once_with(socket_path)


def test_build_status_combines_node_and_protocol_checks() -> None:
    socket_path = Path("/run/example.sock")
    node = SocketNodeStatus(False, "node metadata differs")
    protocol = ProtocolStatus(ok=True, message="hello ok")
    with (
        patch("topos.daemon.status._check_socket_node", return_value=node) as check_node,
        patch("topos.daemon.status._check_protocol", return_value=protocol) as check_protocol,
    ):
        result = build_daemon_status(socket_path, group_name="operators")
    assert result == DaemonStatusReport(socket_path, "operators", node, protocol)
    check_node.assert_called_once_with(socket_path, "operators")
    check_protocol.assert_called_once_with(socket_path)


def _node(tmp_path: Path, monkeypatch, *, uid=0, gid=991, mode=0o660, kind=stat.S_IFSOCK):
    """A socket path whose lstat reports the given owner, group and mode."""
    import topos.daemon.status as status_module

    path = tmp_path / "topos.sock"
    info = SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=kind | mode)
    original = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self, *a, **k: info if self == path else original(self, *a, **k))
    groups = {"docker": 991, "other": 992}
    monkeypatch.setattr(status_module.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=groups[name]))
    names = {gid_: name for name, gid_ in groups.items()}
    monkeypatch.setattr(status_module.grp, "getgrgid", lambda value: SimpleNamespace(gr_name=names[value]))
    return path


def test_socket_node_accepts_any_group_unless_one_is_required(tmp_path: Path, monkeypatch) -> None:
    path = _node(tmp_path, monkeypatch)
    node = _check_socket_node(path, None)
    assert node.ok and "group docker" in node.message
    assert _check_socket_node(path, "docker").ok
    mismatch = _check_socket_node(path, "other")
    assert not mismatch.ok and "group docker, expected other" in mismatch.message
    missing = _check_socket_node(path, "absent")
    assert not missing.ok and "required group 'absent' does not exist here" in missing.message


def test_socket_node_reports_owner_mode_type_and_unresolvable_gid(tmp_path: Path, monkeypatch) -> None:
    wrong_mode = _check_socket_node(_node(tmp_path, monkeypatch, mode=0o666), None)
    assert not wrong_mode.ok and "mode 0666, expected owner root mode 0660" in wrong_mode.message
    wrong_owner = _check_socket_node(_node(tmp_path, monkeypatch, uid=1000), None)
    assert not wrong_owner.ok and "owner 1000" in wrong_owner.message
    not_socket = _check_socket_node(_node(tmp_path, monkeypatch, kind=stat.S_IFREG), None)
    assert not not_socket.ok and "not a Unix socket" in not_socket.message
    # A gid the container cannot name is shown as the number.
    unnamed = _check_socket_node(_node(tmp_path, monkeypatch, gid=4242), None)
    assert unnamed.ok and "group 4242" in unnamed.message
    assert not _check_socket_node(tmp_path / "absent.sock", None).ok


def test_status_text_shows_the_required_group_or_any() -> None:
    node = SocketNodeStatus(True, "ok")
    protocol = ProtocolStatus(True, "ok")
    assert "Required group: any" in DaemonStatusReport(Path("/s"), None, node, protocol).to_text()
    assert "Required group: docker" in DaemonStatusReport(Path("/s"), "docker", node, protocol).to_text()
