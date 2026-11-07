"""Fake-root coverage for the service-side cgprofiled doctor."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgprofile import daemon_config, unitfiles
from cgprofile.cli import installer, service_doctor


def _args(root, config=None):
    return argparse.Namespace(config=config, root=str(root))


def _runtime():
    return SimpleNamespace()


def _paths(root):
    return installer.InstallPaths(str(root))


def _config_file(root):
    path = Path(_paths(root).config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(daemon_config.render_config({}), encoding="utf-8")
    return str(path)


def _group(name, gid, members=()):
    return SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=list(members))


def _groups(monkeypatch, mapping):
    def getgrnam(name):
        if name not in mapping:
            raise KeyError(name)
        entry = mapping[name]
        if isinstance(entry, tuple):
            return _group(name, entry[0], entry[1])
        return _group(name, entry)

    monkeypatch.setattr(service_doctor.grp, "getgrnam", getgrnam)


def _as(monkeypatch, euid):
    monkeypatch.setattr(service_doctor.os, "geteuid", lambda: euid)


_REAL_LSTAT = os.lstat


def _patch_lstat(monkeypatch, answer):
    """Route ``lstat(path)`` through ``answer(path)``.

    Calls that carry ``dir_fd`` (pytest's own tmp cleanup) stay real, because the
    patch is still active while the tmp directory is removed.
    """
    def lstat(path, *args, **kwargs):
        if args or kwargs:
            return _REAL_LSTAT(path, *args, **kwargs)
        return answer(path)

    monkeypatch.setattr(service_doctor.os, "lstat", lstat)


def _owned_by(monkeypatch, uid, gid=None):
    """Report the real node with another owner (the fake root belongs to the test user)."""
    def answer(path):
        info = _REAL_LSTAT(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=uid, st_gid=info.st_gid if gid is None else gid)

    _patch_lstat(monkeypatch, answer)


def _lstat_raises(monkeypatch, exc):
    def answer(_path):
        raise exc

    _patch_lstat(monkeypatch, answer)


def _broken_config(root):
    path = Path(_paths(root).config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bogus = 1\n", encoding="utf-8")


def test_doctor_registers_the_plan_check_names_in_order():
    assert [check.name for check in service_doctor.SERVICE_DOCTOR_CHECKS] == [
        "config-valid", "units-current", "groups", "socket-units", "socket-nodes",
        "socket-dirs", "state-dir", "service", "connect", "foreign-rules", "caller-access",
    ]


def test_the_default_root_is_the_live_host():
    assert service_doctor._paths(argparse.Namespace(config=None)).root == "/"


def test_config_valid_and_invalid_checks(tmp_path):
    _config_file(tmp_path)
    assert service_doctor._check_config_valid(_runtime(), _args(tmp_path)).status == "ok"
    Path(_paths(tmp_path).config).write_text("bogus = 1\n", encoding="utf-8")
    result = service_doctor._check_config_valid(_runtime(), _args(tmp_path))  # a fresh namespace: no cache
    assert result.status == "fail" and result.remedy
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(daemon_config.render_config({}), encoding="utf-8")
    assert service_doctor._check_config_valid(_runtime(), _args(tmp_path, str(explicit))).status == "ok"
    assert service_doctor._check_config_valid(_runtime(), _args(tmp_path, str(tmp_path / "missing"))).status == "fail"


def test_config_permission_refusal_skips_for_nonroot(monkeypatch, tmp_path):
    _as(monkeypatch, 1000)
    monkeypatch.setattr(service_doctor.daemon_config, "read_config_file",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(daemon_config.ConfigError("Permission denied")))
    result = service_doctor._check_config_valid(_runtime(), _args(tmp_path))
    assert result.status == "skip" and "run as root" in result.summary
    values, skipped = service_doctor._config_or_skip(_args(tmp_path))
    assert values is None and skipped.status == "skip" and "run as root" in skipped.summary
    _as(monkeypatch, 0)
    values, skipped = service_doctor._config_or_skip(_args(tmp_path))
    assert values is None and skipped.status == "skip" and "could not be loaded" in skipped.summary


def _install_units(root):
    config = _config_file(root)
    values, _digest = daemon_config.read_config_file(config, explicit=True)
    installed = service_doctor._installed_resources(_paths(root))
    for name, text in unitfiles.render(config_values=values).items():
        Path(installed[name]).parent.mkdir(parents=True, exist_ok=True)
        Path(installed[name]).write_text(text, encoding="utf-8")
    return installed


def test_units_current_accepts_all_resources_and_reports_drift(monkeypatch, tmp_path):
    installed = _install_units(tmp_path)
    assert service_doctor._check_units_current(_runtime(), _args(tmp_path)).status == "ok"

    Path(installed["cgprofiled.service"]).write_text("drift\n", encoding="utf-8")
    result = service_doctor._check_units_current(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "cgprofiled.service" in result.summary
    assert result.remedy and result.details["problems"]

    Path(installed["cgprofiled.service"]).unlink()
    result = service_doctor._check_units_current(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "cannot read" in result.summary

    _as(monkeypatch, 1000)
    original = Path.read_text

    def unreadable(path, *args, **kwargs):
        if str(path) == installed["cgprofile.slice"]:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert service_doctor._check_units_current(_runtime(), _args(tmp_path)).status == "skip"


def test_checks_that_need_the_config_skip_when_it_cannot_load(tmp_path):
    _broken_config(tmp_path)
    for check in (service_doctor._check_units_current, service_doctor._check_groups,
                  service_doctor._check_socket_nodes, service_doctor._check_socket_dirs,
                  service_doctor._check_connect, service_doctor._check_caller_access):
        result = check(_runtime(), _args(tmp_path))
        assert result.status == "skip" and "config could not be loaded" in result.summary, check.__name__


def test_groups_check_requires_distinct_existing_dedicated_groups(monkeypatch, tmp_path):
    _config_file(tmp_path)
    gids = {"cgprofile-control": 10, "cgprofile-observer": 11, "docker": 12}
    _groups(monkeypatch, gids)
    assert service_doctor._check_groups(_runtime(), _args(tmp_path)).status == "ok"
    gids.pop("docker")
    assert service_doctor._check_groups(_runtime(), _args(tmp_path)).status == "ok"
    gids["docker"] = 12

    gids.pop("cgprofile-observer")
    result = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "cgprofile-observer" in result.summary and result.remedy
    gids["cgprofile-observer"] = 10
    assert "same GID" in service_doctor._check_groups(_runtime(), _args(tmp_path)).summary
    gids["cgprofile-observer"] = 12
    assert "docker GID" in service_doctor._check_groups(_runtime(), _args(tmp_path)).summary
    gids["cgprofile-control"] = 0
    result = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "GID 0" in result.summary and result.remedy


def test_groups_check_skips_unreadable_config_for_nonroot(monkeypatch, tmp_path):
    _as(monkeypatch, 1000)
    monkeypatch.setattr(
        service_doctor.daemon_config, "read_config_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(daemon_config.ConfigError("Permission denied")),
    )
    result = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert result.status == "skip" and "run as root" in result.summary


def test_groups_check_reports_repeated_name_and_record_read_errors(monkeypatch, tmp_path):
    config = Path(_paths(tmp_path).config)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '[sockets.control]\npath = "/run/cgprofile/control/cgprofile.sock"\n'
        'group = "same"\nmode = "0660"\n\n'
        '[sockets.observer]\npath = "/run/cgprofile/observer/cgprofile.sock"\n'
        'group = "same"\nmode = "0660"\n', encoding="utf-8")
    _groups(monkeypatch, {"same": 10})
    repeated = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert repeated.status == "fail" and "groups must differ" in repeated.summary

    _config_file(tmp_path)
    _groups(monkeypatch, {"cgprofile-control": 10, "cgprofile-observer": 11})
    monkeypatch.setattr(service_doctor, "_read_install_record", lambda _path: {"control_users": 7})
    invalid = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert invalid.status == "fail" and "control_users" in invalid.summary

    def unreadable(_path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(service_doctor, "_read_install_record", unreadable)
    _as(monkeypatch, 1000)
    skipped = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert skipped.status == "skip" and "run as root" in skipped.summary
    _as(monkeypatch, 0)
    refused = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert refused.status == "fail" and "cannot read" in refused.summary


def test_groups_check_lists_actual_and_recorded_members(monkeypatch, tmp_path):
    config = _config_file(tmp_path)
    _install_record(tmp_path)
    paths = _paths(tmp_path)
    Path(paths.install_record).write_text(
        'control_users = ["alice", "primary"]\nobserver_users = ["bob"]\n', encoding="utf-8")
    groups = {
        "cgprofile-control": (10, ["alice", "other"]),
        "cgprofile-observer": (11, ["bob"]),
        "docker": 12,
    }
    _groups(monkeypatch, groups)
    accounts = {"alice": True, "bob": True, "primary": True}

    def getpwnam(name):
        if not accounts.get(name, False):
            raise KeyError(name)
        return SimpleNamespace(pw_gid=10 if name == "primary" else 99)

    monkeypatch.setattr(service_doctor.pwd, "getpwnam", getpwnam)
    result = service_doctor._check_groups(_runtime(), _args(tmp_path, config))
    assert result.status == "ok"
    assert result.details["groups"]["control"]["members"] == ["alice", "other", "primary"]
    assert result.details["groups"]["control"]["recorded_users"] == ["alice", "primary"]
    assert result.details["groups"]["observer"]["recorded_users"] == ["bob"]

    groups["cgprofile-control"] = (10, ["other"])
    result = service_doctor._check_groups(_runtime(), _args(tmp_path, config))
    assert result.status == "warn" and "alice" in result.summary
    assert result.remedy == "rerun cgprofiled install (recorded users are re-added) or --add-user LEVEL=USER"
    accounts["alice"] = False
    result = service_doctor._check_groups(_runtime(), _args(tmp_path, config))
    assert result.status == "warn" and "does not exist" in result.summary
    assert result.remedy == "rerun cgprofiled install (recorded users are re-added) or --add-user LEVEL=USER"


@pytest.mark.parametrize("level,group,gids,needle", [
    ("control", "docker", {"docker": 12}, "must not be docker"),
    ("control", "root-alias", {"root-alias": 0}, "GID 0"),
    ("observer", "docker-alias", {"docker": 12, "docker-alias": 12}, "docker GID"),
])
def test_groups_check_reports_each_forbidden_gid_even_when_config_check_fails(
        monkeypatch, tmp_path, level, group, gids, needle):
    control = group if level == "control" else "cgprofile-control"
    observer = group if level == "observer" else "cgprofile-observer"
    path = Path(_paths(tmp_path).config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[sockets.control]\npath = "/run/cgprofile/control/cgprofile.sock"\n'
        f'group = "{control}"\nmode = "0660"\n\n'
        f'[sockets.observer]\npath = "/run/cgprofile/observer/cgprofile.sock"\n'
        f'group = "{observer}"\nmode = "0660"\n', encoding="utf-8")
    gids.setdefault("cgprofile-control", 10)
    gids.setdefault("cgprofile-observer", 11)
    _groups(monkeypatch, gids)
    result = service_doctor._check_groups(_runtime(), _args(tmp_path))
    assert result.status == "fail" and needle in result.summary and result.remedy


def test_socket_units_check_reports_enabled_and_active_state(monkeypatch):
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    assert service_doctor._check_socket_units(_runtime(), _args("/tmp")).status == "ok"
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *argv: argv[-1] != "cgprofile-observer.socket")
    result = service_doctor._check_socket_units(_runtime(), _args("/tmp"))
    assert result.status == "fail" and "cgprofile-observer.socket" in result.summary and result.remedy


def test_systemctl_helper_reports_exit_status_and_failures(monkeypatch):
    monkeypatch.setattr(service_doctor.subprocess, "run",
                        lambda argv, **kw: SimpleNamespace(returncode=0 if argv[1] == "good" else 3))
    assert service_doctor._systemctl("good") is True and service_doctor._systemctl("bad") is False

    def broken(argv, **_kwargs):
        raise OSError("no systemctl")

    monkeypatch.setattr(service_doctor.subprocess, "run", broken)
    assert service_doctor._systemctl("good") is False


def test_expected_socket_problem_checks_type_owner_group_and_mode(monkeypatch, tmp_path):
    _groups(monkeypatch, {"control": 10})
    details = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=10)
    _patch_lstat(monkeypatch, lambda _path: details)
    assert service_doctor._expected_socket_problem(str(tmp_path / "s"), "control", "0660") is None
    details.st_mode = stat.S_IFREG | 0o660
    assert "not a socket" in service_doctor._expected_socket_problem("s", "control", "0660")
    details.st_mode = stat.S_IFSOCK | 0o600
    assert "expected root:control 0660" in service_doctor._expected_socket_problem("s", "control", "0660")
    details.st_mode = stat.S_IFSOCK | 0o660
    details.st_uid = 7
    assert "expected root:control 0660" in service_doctor._expected_socket_problem("s", "control", "0660")
    _groups(monkeypatch, {})
    assert "does not exist" in service_doctor._expected_socket_problem("s", "missing", "0660")
    _lstat_raises(monkeypatch, FileNotFoundError())
    assert "FileNotFoundError" in service_doctor._expected_socket_problem("absent", "control", "0660")
    _lstat_raises(monkeypatch, PermissionError("denied"))
    with pytest.raises(PermissionError):
        service_doctor._expected_socket_problem("s", "control", "0660")


def _bind_socket(path, mode):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, mode)
    listener.listen(1)
    return listener


def test_socket_nodes_check_uses_configured_mode_and_group(monkeypatch, tmp_path):
    _config_file(tmp_path)
    paths = _paths(tmp_path)
    sockets = daemon_config.socket_settings({})
    nodes = {level: paths.at(sockets[level]["path"]) for level in daemon_config.SOCKET_LEVELS}
    listeners = [_bind_socket(node, 0o660) for node in nodes.values()]
    try:
        _groups(monkeypatch, {"cgprofile-control": 7101, "cgprofile-observer": 7102})

        def answer(path):
            if str(path) == nodes["control"]:
                return SimpleNamespace(st_mode=_REAL_LSTAT(path).st_mode, st_uid=0, st_gid=7101)
            if str(path) == nodes["observer"]:
                return SimpleNamespace(st_mode=_REAL_LSTAT(path).st_mode, st_uid=0, st_gid=7102)
            return _REAL_LSTAT(path)

        _patch_lstat(monkeypatch, answer)
        assert service_doctor._check_socket_nodes(_runtime(), _args(tmp_path)).status == "ok"
        os.chmod(nodes["observer"], 0o600)
        result = service_doctor._check_socket_nodes(_runtime(), _args(tmp_path))
        assert result.status == "fail" and "0660" in result.summary and result.remedy
        assert result.details["problems"]
    finally:
        for listener in listeners:
            listener.close()


def test_socket_nodes_check_permission_handling(monkeypatch, tmp_path):
    _config_file(tmp_path)
    _lstat_raises(monkeypatch, PermissionError(13, "denied"))
    _as(monkeypatch, 1000)
    assert service_doctor._check_socket_nodes(_runtime(), _args(tmp_path)).status == "skip"
    _as(monkeypatch, 0)
    result = service_doctor._check_socket_nodes(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "denied" in result.summary


def _make_dir(path, mode):
    Path(path).mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)


def test_socket_dirs_check_requires_root_owned_nonwritable_directories(monkeypatch, tmp_path):
    _config_file(tmp_path)
    paths = _paths(tmp_path)
    for directory in (paths.runtime_root, paths.control_dir, paths.observer_dir):
        _make_dir(directory, 0o755)
    _owned_by(monkeypatch, 0, 0)
    assert service_doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "ok"
    # 0750 is stricter than the layout and still fine: the check forbids group/other WRITE
    os.chmod(paths.observer_dir, 0o750)
    assert service_doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "ok"
    os.chmod(paths.control_dir, 0o770)
    result = service_doctor._check_socket_dirs(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "not group/other writable" in result.summary and result.remedy
    os.chmod(paths.control_dir, 0o755)
    _owned_by(monkeypatch, 5, 0)
    assert service_doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "fail"
    _owned_by(monkeypatch, 0, 0)
    Path(paths.observer_dir).rmdir()
    missing = service_doctor._check_socket_dirs(_runtime(), _args(tmp_path))
    assert missing.status == "fail" and "No such file" in missing.summary
    _lstat_raises(monkeypatch, PermissionError(13, "denied"))
    _as(monkeypatch, 1000)
    assert service_doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "skip"


def test_state_dir_check_passes_fails_and_skips_permission_denial(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    missing = service_doctor._check_state_dir(_runtime(), _args(tmp_path))
    assert missing.status == "fail" and missing.remedy and "No such file" in missing.summary
    _make_dir(paths.state_dir, 0o700)
    _owned_by(monkeypatch, 0, 0)
    assert service_doctor._check_state_dir(_runtime(), _args(tmp_path)).status == "ok"
    os.chmod(paths.state_dir, 0o755)
    result = service_doctor._check_state_dir(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "0755" in result.summary
    _as(monkeypatch, 1000)
    _lstat_raises(monkeypatch, PermissionError("denied"))
    skipped = service_doctor._check_state_dir(_runtime(), _args(tmp_path))
    assert skipped.status == "skip" and "run as root" in skipped.summary


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
        (json.dumps({"ok": True, "contract": 2}) + "\n").encode(),
    ]

    def socket_factory(*_args, **_kwargs):
        client = FakeClient(responses.pop(0))
        clients.append(client)
        return client

    monkeypatch.setattr(service_doctor.socket, "socket", socket_factory)
    assert service_doctor._version_request("/control") == {"ok": True, "contract": 2}
    assert json.loads(clients[0].sent) == {"verb": "version", "args": {}, "contract": 2}
    monkeypatch.setattr(service_doctor.socket, "socket", socket_factory)
    assert service_doctor._observer_request("/observer") == {"ok": True, "contract": 2}
    sent = json.loads(clients[-1].sent)
    assert sent["verb"] == "resource-limits" and sent["contract"] == 2
    assert type(sent["args"]["thread_id"]) is int and sent["args"]["thread_id"] > 0
    bad = FakeClient(b"{}\n")
    monkeypatch.setattr(service_doctor.socket, "socket", lambda *_args, **_kwargs: bad)
    with pytest.raises(OSError, match="successful contract-2 version response"):
        service_doctor._version_request("/control")
    bad.response = b"{}\n"
    with pytest.raises(OSError, match="no contract-2 resource-limits envelope"):
        service_doctor._observer_request("/observer")
    # an endpoint that answers but refuses the probe is a failed probe; observer-refused is a live endpoint
    bad.response = (json.dumps({"ok": False, "contract": 2, "error": {"code": "bad-argument"}}) + "\n").encode()
    with pytest.raises(OSError, match="refused the contract-2 probe: bad-argument"):
        service_doctor._observer_request("/observer")
    bad.response = (json.dumps({"ok": False, "contract": 2, "error": "not-a-dict"}) + "\n").encode()
    with pytest.raises(OSError, match="no error code"):
        service_doctor._observer_request("/observer")
    bad.response = (json.dumps({"ok": False, "contract": 2, "error": {"code": "observer-refused"}}) + "\n").encode()
    assert service_doctor._observer_request("/observer")["error"]["code"] == "observer-refused"
    # a peer that closes without answering produces a decode error the callers map to a failure
    empty = FakeClient(b"")
    monkeypatch.setattr(service_doctor.socket, "socket", lambda *_args, **_kwargs: empty)
    with pytest.raises(ValueError):
        service_doctor._version_request("/control")
    with pytest.raises(ValueError):
        service_doctor._observer_request("/observer")


class RealObserverClient(FakeClient):
    """Answers with the daemon's real observer dispatcher, not a canned reply (C1)."""

    def __init__(self, server):
        super().__init__(b"")
        self.server = server

    def sendall(self, data):
        super().sendall(data)
        response = self.server._observer_dispatch(data.strip(), (os.getpid(), os.getuid(), os.getgid()))
        self.response = (json.dumps(response) + "\n").encode()


def test_the_observer_probe_passes_against_the_real_observer_contract(monkeypatch, tmp_path):
    """C1: the doctor's `connect` probe used a contract-1 `host` request the observer never serves."""
    from cgprofile import serve

    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=str(tmp_path / "ctl.sock"),
        cgroup_root=str(tmp_path / "cgroup"), proc_root=str(tmp_path / "proc"),
        gates_slice_name="dev-gates.slice",
    )
    client = RealObserverClient(server)
    monkeypatch.setattr(service_doctor.socket, "socket", lambda *_args, **_kwargs: client)
    body = service_doctor._observer_request("/observer")
    assert body["contract"] == 2 and (body["ok"] is True or body["error"]["code"] == "observer-refused")
    # the contract-1 `host` request the old probe sent is refused by the same dispatcher
    old_request = {"verb": "host", "args": {}, "contract": 1}
    refusal = server._observer_dispatch(json.dumps(old_request).encode(), (os.getpid(), os.getuid(), os.getgid()))
    assert refusal["ok"] is False and refusal["error"]["code"] == "bad-argument"


def test_the_loaded_config_is_cached_on_the_arguments(tmp_path):
    _config_file(tmp_path)
    args = _args(tmp_path)
    first = service_doctor._config(args)
    Path(_paths(tmp_path).config).write_text("bogus = 1\n", encoding="utf-8")
    assert service_doctor._config(args) is first


def test_an_unreadable_install_record_propagates_the_permission_error(monkeypatch, tmp_path):
    _install_record(tmp_path)

    def denied(_stream):
        raise PermissionError("denied")

    monkeypatch.setattr(service_doctor.tomllib, "load", denied)
    with pytest.raises(PermissionError):
        service_doctor._read_install_record(_paths(tmp_path).install_record)


def _install_record(root, version="2.2.0", *, with_tree=True, link=True):
    paths = _paths(root)
    if with_tree:
        manifest = Path(paths.opt) / version / "MANIFEST"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(f"installer=cgprofiled install {version}\n", encoding="utf-8")
    if link:
        os.symlink(version, paths.current)
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text(f'wheel = "{version}@sha256:{"a" * 64}"\n', encoding="utf-8")


def test_installed_version_requires_link_manifest_and_matching_record(tmp_path):
    paths = _paths(tmp_path)
    assert service_doctor._installed_version(paths) is None
    _install_record(tmp_path)
    assert service_doctor._installed_version(paths) == "2.2.0"
    Path(paths.install_record).write_text("wheel = 3\n", encoding="utf-8")
    assert service_doctor._installed_version(paths) is None
    Path(paths.install_record).write_text('wheel = "not-a-pin"\n', encoding="utf-8")
    assert service_doctor._installed_version(paths) is None
    Path(paths.install_record).write_text('wheel = "2.1.0@sha256:' + "a" * 64 + '"\n', encoding="utf-8")
    assert service_doctor._installed_version(paths) is None
    Path(paths.install_record).write_text("= invalid\n", encoding="utf-8")
    assert service_doctor._read_install_record(paths.install_record) == {}
    Path(paths.install_record).unlink()
    assert service_doctor._read_install_record(paths.install_record) == {}
    (Path(paths.opt) / "2.2.0" / "MANIFEST").unlink()
    assert service_doctor._installed_version(paths) is None


def test_installed_version_refuses_odd_current_links(tmp_path):
    paths = _paths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    os.symlink("/opt/cgprofile/2.2.0", paths.current)
    assert service_doctor._installed_version(paths) is None
    os.unlink(paths.current)
    os.symlink("latest", paths.current)
    assert service_doctor._installed_version(paths) is None


def _activated(version="2.2.0", control=1, observer=1, mode="activated"):
    return {"ok": True, "contract": 2, "daemon": {"version": version},
            "socket_activation": {"mode": mode, "fds": {"control": control, "observer": observer}}}


def test_service_check_requires_active_matching_activated_daemon(monkeypatch, tmp_path):
    _config_file(tmp_path)
    _install_record(tmp_path)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    _as(monkeypatch, 0)
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: _activated())
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "ok" and "control_fds=1" in result.summary

    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: _activated(control=2))
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: _activated(mode="self-bind"))
    assert "valid systemd socket activation" in service_doctor._check_service(_runtime(), _args(tmp_path)).summary
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: _activated(version="2.1.0"))
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "2.1.0" in result.summary and result.remedy
    monkeypatch.setattr(service_doctor, "_version_request",
                        lambda _path: {"ok": True, "contract": 2, "daemon": {"version": "2.2.0"}})
    assert "does not report socket activation" in service_doctor._check_service(_runtime(), _args(tmp_path)).summary
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {"ok": True, "daemon": "x"})
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(service_doctor, "_version_request",
                        lambda _path: (_ for _ in ()).throw(OSError("unavailable")))
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "cannot verify the running cgprofiled version" in result.summary


def test_service_check_inactive_service_and_missing_version_pointer(monkeypatch, tmp_path):
    _config_file(tmp_path)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: False)
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "version pointer is missing" in result.summary
    _install_record(tmp_path)
    _as(monkeypatch, 0)
    _broken_config(tmp_path)
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "skip"


def test_service_check_permission_handling(monkeypatch, tmp_path):
    _config_file(tmp_path)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)

    def denied(_paths):
        raise PermissionError("denied")

    monkeypatch.setattr(service_doctor, "_installed_version", denied)
    _as(monkeypatch, 1000)
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "skip"
    _as(monkeypatch, 0)
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "fail" and result.remedy


def test_service_check_skips_socket_access_when_nonroot_lacks_group(monkeypatch, tmp_path):
    _config_file(tmp_path)
    _install_record(tmp_path)
    _as(monkeypatch, 1000)
    monkeypatch.setattr(service_doctor, "_systemctl", lambda *_argv: True)
    _groups(monkeypatch, {"cgprofile-control": 42, "cgprofile-observer": 43})
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    result = service_doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "skip" and "lacks control-socket access" in result.summary
    _groups(monkeypatch, {})
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "skip"
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    _groups(monkeypatch, {"cgprofile-control": 42})
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: _activated())
    assert service_doctor._check_service(_runtime(), _args(tmp_path)).status == "ok"


def test_connect_check_probes_accessible_levels_and_skips_expected_eacces(monkeypatch, tmp_path):
    _config_file(tmp_path)
    _groups(monkeypatch, {"cgprofile-control": 10, "cgprofile-observer": 11})
    _as(monkeypatch, 0)
    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {
        "ok": True, "contract": 2, "socket_activation": {},
    })
    monkeypatch.setattr(service_doctor, "_observer_request", lambda _path: {"ok": True, "contract": 2})
    result = service_doctor._check_connect(_runtime(), _args(tmp_path))
    assert result.status == "ok" and result.details["control"]["ok"] is True
    assert result.details["observer"] == {"ok": True, "contract": 2}

    _as(monkeypatch, 1000)
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    result = service_doctor._check_connect(_runtime(), _args(tmp_path))
    assert result.status == "ok" and "skipped expected access" in result.summary

    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    monkeypatch.setattr(service_doctor, "_version_request",
                        lambda _path: (_ for _ in ()).throw(OSError("unavailable")))
    result = service_doctor._check_connect(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "control" in result.summary and result.remedy

    monkeypatch.setattr(service_doctor, "_version_request", lambda _path: {"ok": True, "contract": 2})
    _groups(monkeypatch, {"cgprofile-control": 10})
    result = service_doctor._check_connect(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "observer: configured group cgprofile-observer is absent" in result.summary


def test_foreign_rule_check_uses_the_whole_p11_scan(monkeypatch, tmp_path):
    _as(monkeypatch, 0)
    paths = _paths(tmp_path)
    assert service_doctor._check_foreign_rules(_runtime(), _args(tmp_path)).status == "ok"
    rule = Path(paths.at("/etc/tmpfiles.d/mdt-cgprofile.conf"))
    rule.parent.mkdir(parents=True)
    rule.write_text("d /run/cgprofile 0770 root docker -\n", encoding="utf-8")
    (Path(paths.opt) / "PENDING").parent.mkdir(parents=True)
    (Path(paths.opt) / "PENDING").write_text("2.2.0\n", encoding="utf-8")
    result = service_doctor._check_foreign_rules(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "manual" in result.remedy.lower() and result.lines
    assert any("mdt-cgprofile.conf" in line for line in result.lines)
    assert any("leftover install marker" in line for line in result.lines)


def test_foreign_rule_check_unreadable_entries_skip_for_nonroot_and_bad_config_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(service_doctor, "old_installation_problems",
                        lambda *_args: ["cannot inspect /etc/tmpfiles.d: denied"])
    _as(monkeypatch, 1000)
    assert service_doctor._check_foreign_rules(_runtime(), _args(tmp_path)).status == "skip"
    _as(monkeypatch, 0)
    assert service_doctor._check_foreign_rules(_runtime(), _args(tmp_path)).status == "fail"

    def broken(*_args):
        raise installer.InstallError("cannot inspect existing config")

    monkeypatch.setattr(service_doctor, "old_installation_problems", broken)
    result = service_doctor._check_foreign_rules(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "cannot inspect existing config" in result.summary


def test_caller_access_check(monkeypatch, tmp_path):
    _config_file(tmp_path)
    _groups(monkeypatch, {"cgprofile-control": 10, "cgprofile-observer": 11})
    _as(monkeypatch, 0)
    assert service_doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "ok"
    _as(monkeypatch, 1000)
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: False)
    result = service_doctor._check_caller_access(_runtime(), _args(tmp_path))
    assert result.status == "warn" and "cgprofile-control" in result.summary and result.remedy
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    assert service_doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "ok"
    _groups(monkeypatch, {})
    monkeypatch.setattr(service_doctor, "_caller_in_group", lambda _gid: True)
    assert service_doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "ok"


def test_caller_group_membership_uses_the_effective_and_supplementary_groups(monkeypatch):
    monkeypatch.setattr(service_doctor.os, "getegid", lambda: 5)
    monkeypatch.setattr(service_doctor.os, "getgroups", lambda: [7, 8])
    assert service_doctor._caller_in_group(5) and service_doctor._caller_in_group(8)
    assert not service_doctor._caller_in_group(9)


def test_register_service_doctor_adds_the_checks_to_a_registry():
    seen = {}

    def fake_register(registry, checks, description):
        seen.update(registry=registry, checks=checks, description=description)

    original = service_doctor.register_doctor
    service_doctor.register_doctor = fake_register
    try:
        service_doctor.register_service_doctor("registry")
    finally:
        service_doctor.register_doctor = original
    assert seen["checks"] is service_doctor.SERVICE_DOCTOR_CHECKS and "sockets" in seen["description"]
