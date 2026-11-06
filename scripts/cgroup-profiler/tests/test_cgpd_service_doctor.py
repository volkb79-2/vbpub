"""Fake-root coverage for the service-side cgpd doctor."""

from __future__ import annotations

import argparse
import json
import socket
import stat
from types import SimpleNamespace

import pytest

from cgprofile import daemon_config, unitfiles
from cgprofile.cli import service_doctor


def _args(config=None):
    return argparse.Namespace(config=config)


def _runtime():
    return SimpleNamespace()


def _config_file(tmp_path):
    path = tmp_path / "cgpd.toml"
    path.write_text(daemon_config.render_config({}), encoding="utf-8")
    return str(path)


def _group(name, gid):
    return SimpleNamespace(gr_name=name, gr_gid=gid)


def test_doctor_registers_the_plan_check_names_in_order():
    assert [check.name for check in service_doctor.SERVICE_DOCTOR_CHECKS] == [
        "config-valid", "units-current", "groups", "socket-units", "socket-nodes",
        "socket-dirs", "state-dir", "service", "connect", "foreign-rules", "caller-access",
    ]


def test_config_valid_and_invalid_checks(tmp_path):
    valid = _config_file(tmp_path)
    assert service_doctor._check_config_valid(_runtime(), _args(valid)).status == "ok"
    assert service_doctor._check_config_valid(_runtime(), _args(str(tmp_path / "missing"))).status == "fail"


def test_config_permission_refusal_skips_for_nonroot(monkeypatch, tmp_path):
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(service_doctor.daemon_config, "read_config_file",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(daemon_config.ConfigError("Permission denied")))
    result = service_doctor._check_config_valid(_runtime(), _args(str(tmp_path / "secret")))
    assert result.status == "skip" and "run as root" in result.summary
    values, skipped = service_doctor._config_or_skip(_args(str(tmp_path / "secret")))
    assert values is None and skipped.status == "skip" and "run as root" in skipped.summary


def test_units_current_accepts_all_resources_and_reports_drift(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    values, _digest = daemon_config.read_config_file(config, explicit=True)
    installed = {}
    for name, text in unitfiles.render(config_values=values).items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        installed[name] = str(path)
    monkeypatch.setattr(unitfiles, "default_installed_map", lambda: installed)
    args = _args(config)
    assert service_doctor._check_units_current(_runtime(), args).status == "ok"
    Path(installed["cgpd.service"]).write_text("drift\n", encoding="utf-8")
    result = service_doctor._check_units_current(_runtime(), args)
    assert result.status == "fail" and "cgpd.service" in result.summary
    assert result.remedy and result.details["problems"]


def test_groups_check_requires_distinct_existing_non_docker_groups(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    gids = {"cgprofile-control": 10, "cgprofile-observer": 11, "docker": 12}
    monkeypatch.setattr(service_doctor.grp, "getgrnam", lambda name: _group(name, gids[name]))
    assert service_doctor._check_groups(_runtime(), _args(config)).status == "ok"

    gids.pop("cgprofile-observer")
    assert service_doctor._check_groups(_runtime(), _args(config)).status == "fail"
    gids["cgprofile-observer"] = 10
    assert "same GID" in service_doctor._check_groups(_runtime(), _args(config)).summary
    gids["cgprofile-observer"] = 12
    assert "docker GID" in service_doctor._check_groups(_runtime(), _args(config)).summary


def test_socket_units_check_reports_enabled_and_active_state(monkeypatch):
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    assert service_doctor._check_socket_units(_runtime(), _args()).status == "ok"
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *argv: argv[-1] != "cgprofile-observer.socket")
    result = service_doctor._check_socket_units(_runtime(), _args())
    assert result.status == "fail" and "cgprofile-observer.socket" in result.summary and result.remedy


def test_expected_socket_stat_checks_type_owner_group_and_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(service_doctor.grp, "getgrnam", lambda name: _group(name, 10))
    details = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=10)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: details)
    assert service_doctor._expected_socket_stat(str(tmp_path / "s"), "control", "0660") is None
    details.st_mode = stat.S_IFREG | 0o660
    assert "not a socket" in service_doctor._expected_socket_stat("s", "control", "0660")
    details.st_mode = stat.S_IFSOCK | 0o600
    assert "expected root:control 0660" in service_doctor._expected_socket_stat("s", "control", "0660")
    details.st_mode = stat.S_IFSOCK | 0o660
    details.st_uid = 7
    assert "expected root:control 0660" in service_doctor._expected_socket_stat("s", "control", "0660")
    monkeypatch.setattr(service_doctor.grp, "getgrnam", lambda _name: (_ for _ in ()).throw(KeyError()))
    assert "does not exist" in service_doctor._expected_socket_stat("s", "missing", "0660")
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    assert "No such file" in service_doctor._expected_socket_stat("absent", "control", "0660")


def test_socket_nodes_check_uses_configured_mode_and_group(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    sockets = daemon_config.socket_settings(daemon_config.read_config_file(config, explicit=True)[0])
    entries = {sockets[level]["path"]: SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=10 if level == "control" else 11,
    ) for level in daemon_config.SOCKET_LEVELS}
    monkeypatch.setattr(service_doctor.os, "lstat", lambda path: entries[path])
    monkeypatch.setattr(service_doctor.grp, "getgrnam",
                        lambda name: _group(name, 10 if name == "cgprofile-control" else 11))
    args = _args(config)
    assert service_doctor._check_socket_nodes(_runtime(), args).status == "ok"
    entries[sockets["observer"]["path"]].st_mode = stat.S_IFSOCK | 0o600
    assert service_doctor._check_socket_nodes(_runtime(), args).status == "fail"


def test_socket_dirs_check_requires_root_owned_nonwritable_directories(monkeypatch, tmp_path):
    good = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: good)
    assert service_doctor._check_socket_dirs(_runtime(), _args()).status == "ok"
    bad = SimpleNamespace(st_mode=stat.S_IFDIR | 0o770, st_uid=0, st_gid=0)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda path: bad if str(path).endswith("control") else good)
    assert service_doctor._check_socket_dirs(_runtime(), _args()).status == "fail"
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    assert service_doctor._check_socket_dirs(_runtime(), _args()).status == "fail"


def test_state_dir_check_passes_fails_and_skips_permission_denial(monkeypatch):
    good = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: good)
    assert service_doctor._check_state_dir(_runtime(), _args()).status == "ok"
    bad = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: bad)
    assert service_doctor._check_state_dir(_runtime(), _args()).status == "fail"
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(service_doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    result = service_doctor._check_state_dir(_runtime(), _args())
    assert result.status == "skip" and "run as root" in result.summary


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.sent = b""
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True

    def settimeout(self, _timeout):
        pass

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.sent += data

    def recv(self, _size):
        response, self.response = self.response, b""
        return response


def test_version_and_observer_requests_use_the_declared_contract(monkeypatch):
    clients = []
    responses = [
        (json.dumps({"ok": True, "contract": 2}) + "\n").encode(),
        (json.dumps({"ok": True, "contract": 1}) + "\n").encode(),
    ]

    def socket_factory(*_args, **_kwargs):
        client = FakeClient(responses.pop(0))
        clients.append(client)
        return client

    monkeypatch.setattr(service_doctor.socket, "socket", socket_factory)
    assert service_doctor._version_request("/control") == {"ok": True, "contract": 2}
    assert json.loads(clients[0].sent) == {"verb": "version", "args": {}, "contract": 2}
    monkeypatch.setattr(service_doctor.socket, "socket", socket_factory)
    assert service_doctor._observer_request("/observer") == {"ok": True}
    assert json.loads(clients[-1].sent) == {"verb": "host", "args": {}, "contract": 1}
    bad = FakeClient(b"{}\n")
    monkeypatch.setattr(service_doctor.socket, "socket", lambda *_args, **_kwargs: bad)
    with pytest.raises(OSError, match="successful contract-2 version response"):
        service_doctor._version_request("/control")
    with pytest.raises(OSError, match="contract-1 host response"):
        service_doctor._observer_request("/observer")


def test_service_check_requires_active_matching_activated_daemon(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    monkeypatch.setattr(service_doctor.os, "readlink", lambda _path: "2.2.0")
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {
        "ok": True, "contract": 2,
        "daemon": {"version": "2.2.0"},
        "socket_activation": {"mode": "activated", "fds": {"control": 1, "observer": 1}},
    })
    result = service_doctor._check_service(_runtime(), _args(config))
    assert result.status == "ok" and "control_fds=1" in result.summary

    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {
        "ok": True, "contract": 2, "daemon": {"version": "2.2.0"},
        "socket_activation": {"mode": "activated", "fds": {"control": 2, "observer": 1}},
    })
    assert service_doctor._check_service(_runtime(), _args(config)).status == "fail"
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {
        "ok": True, "contract": 2, "daemon": {"version": "2.1.0"},
    })
    assert service_doctor._check_service(_runtime(), _args(config)).status == "fail"


def test_service_check_skips_socket_access_when_nonroot_lacks_group(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    monkeypatch.setattr(service_doctor.os, "readlink", lambda _path: "2.2.0")
    monkeypatch.setattr(service_doctor, "_version_request",
                        lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr(service_doctor.grp, "getgrnam", lambda _name: _group("cgprofile-control", 42))
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    result = service_doctor._check_service(_runtime(), _args(config))
    assert result.status == "skip" and "lacks control-socket access" in result.summary


def test_connect_check_probes_accessible_levels_and_skips_expected_eacces(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    monkeypatch.setattr(service_doctor.grp, "getgrnam",
                        lambda name: _group(name, 10 if name == "cgprofile-control" else 11))
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 0)
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {
        "ok": True, "contract": 2, "socket_activation": {},
    })
    monkeypatch.setattr(service_doctor, "_observer_request", lambda _path: {"ok": True, "contract": 1})
    result = service_doctor._check_connect(_runtime(), _args(config))
    assert result.status == "ok" and result.details["control"]["ok"] is True

    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    result = service_doctor._check_connect(_runtime(), _args(config))
    assert result.status == "ok" and "skipped expected access" in result.summary

    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    monkeypatch.setattr(service_doctor, "_version_request",
                        lambda _path: (_ for _ in ()).throw(OSError("unavailable")))
    result = service_doctor._check_connect(_runtime(), _args(config))
    assert result.status == "fail" and "control" in result.summary


def test_foreign_rule_and_caller_access_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(service_doctor, "find_foreign_rules", lambda _paths: ["/etc/tmpfiles.d/mdt-cgprofile.conf"])
    result = service_doctor._check_foreign_rules(_runtime(), _args())
    assert result.status == "fail" and "manual" in result.remedy and result.lines
    monkeypatch.setattr(service_doctor, "find_foreign_rules", lambda _paths: [])
    assert service_doctor._check_foreign_rules(_runtime(), _args()).status == "ok"

    config = _config_file(tmp_path)
    monkeypatch.setattr(service_doctor.grp, "getgrnam",
                        lambda name: _group(name, 10 if name == "cgprofile-control" else 11))
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 0)
    assert service_doctor._check_caller_access(_runtime(), _args(config)).status == "ok"
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    assert service_doctor._check_caller_access(_runtime(), _args(config)).status == "warn"
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    assert service_doctor._check_caller_access(_runtime(), _args(config)).status == "ok"
