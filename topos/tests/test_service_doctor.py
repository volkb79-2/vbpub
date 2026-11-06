"""Fake-root checks for the service-side toposd doctor."""

from __future__ import annotations

import argparse
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.service import doctor
from topos.service.config import parse_config
from topos.service.doctor import SERVICE_DOCTOR_CHECKS
from topos.service.install import InstallPaths, _render_config
from topos.service.units import render_units


def _args(root: Path, **values):
    return argparse.Namespace(root=str(root), **values)


def _runtime():
    return SimpleNamespace()


def _config(paths: InstallPaths, group="topos-observe") -> str:
    path = Path(paths.config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_config(group), encoding="utf-8")
    return str(path)


def _group(monkeypatch, mapping):
    def getgrnam(name):
        if name not in mapping:
            raise KeyError(name)
        return SimpleNamespace(gr_name=name, gr_gid=mapping[name])

    monkeypatch.setattr(doctor.grp, "getgrnam", getgrnam)


def test_doctor_registers_the_contract_check_names_in_order() -> None:
    assert [check.name for check in SERVICE_DOCTOR_CHECKS] == [
        "config-valid", "units-current", "groups", "socket-units", "socket-nodes",
        "socket-dirs", "state-dir", "service", "connect", "foreign-rules", "caller-access",
    ]


def test_config_valid_and_invalid(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    args = _args(tmp_path)
    _config(paths)
    assert doctor._check_config_valid(_runtime(), args).status == "ok"
    Path(paths.config).write_text("invalid = [\n", encoding="utf-8")
    result = doctor._check_config_valid(_runtime(), args)
    assert result.status == "fail" and result.remedy


def test_config_permission_skips_for_nonroot_and_other_read_errors_fail(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths)
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == Path(paths.config):
            raise PermissionError("Permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    result = doctor._check_config_valid(_runtime(), _args(tmp_path))
    assert result.status == "skip" and "run as root" in result.summary
    config, skipped = doctor._config_or_skip(_args(tmp_path))
    assert config is None and skipped.status == "skip"
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    result = doctor._check_config_valid(_runtime(), _args(tmp_path))
    assert result.status == "fail"


def test_units_current_compares_all_four_resources(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths)
    config = parse_config(Path(paths.config).read_text(encoding="utf-8"))
    destinations = {
        "topos-observe.socket": paths.at("/etc/systemd/system/topos-observe.socket"),
        "topos.service": paths.at("/etc/systemd/system/topos.service"),
        "topos.slice": paths.at("/etc/systemd/system/topos.slice"),
        "topos.sysusers": paths.sysusers,
    }
    for name, text in render_units(config).items():
        destination = Path(destinations[name])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    args = _args(tmp_path)
    assert doctor._check_units_current(_runtime(), args).status == "ok"

    Path(destinations["topos.service"]).write_text("drift\n", encoding="utf-8")
    result = doctor._check_units_current(_runtime(), _args(tmp_path))
    assert result.status == "fail" and result.remedy and result.details["problems"]

    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    original = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == Path(destinations["topos.slice"]):
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert doctor._check_units_current(_runtime(), _args(tmp_path)).status == "skip"


def test_groups_check_only_requires_the_configured_group(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths, "docker")
    _group(monkeypatch, {"docker": 991})
    assert doctor._check_groups(_runtime(), _args(tmp_path)).status == "ok"
    _group(monkeypatch, {})
    result = doctor._check_groups(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "docker" in result.remedy


def test_socket_units_check_enabled_and_active(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_run_systemctl", lambda *_argv: True)
    assert doctor._check_socket_units(_runtime(), _args(Path("/tmp"))).status == "ok"
    calls = []

    def failed(*argv):
        calls.append(argv)
        return argv[1] != "is-active"

    monkeypatch.setattr(doctor, "_run_systemctl", failed)
    result = doctor._check_socket_units(_runtime(), _args(Path("/tmp")))
    assert result.status == "fail" and "not active" in result.summary and result.remedy
    assert calls == [("is-enabled", "--quiet", "topos-observe.socket"),
                     ("is-active", "--quiet", "topos-observe.socket")]


def test_expected_socket_problem_checks_socket_group_owner_and_mode(monkeypatch) -> None:
    path = "/fake/run/topos/observe/topos.sock"
    details = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=991)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: details)
    _group(monkeypatch, {"docker": 991})
    assert doctor._expected_socket_problem(path, "docker", "0660") is None
    details.st_mode = stat.S_IFREG | 0o660
    assert "not a socket" in doctor._expected_socket_problem(path, "docker", "0660")
    details.st_mode = stat.S_IFSOCK | 0o600
    assert "expected root:docker 0660" in doctor._expected_socket_problem(path, "docker", "0660")
    details.st_mode = stat.S_IFSOCK | 0o660
    details.st_uid = 3
    assert "expected root:docker 0660" in doctor._expected_socket_problem(path, "docker", "0660")
    details.st_uid = 0
    details.st_gid = 3
    assert "expected root:docker 0660" in doctor._expected_socket_problem(path, "docker", "0660")
    _group(monkeypatch, {})
    assert "group docker does not exist" in doctor._expected_socket_problem(path, "docker", "0660")
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    assert "No such file" in doctor._expected_socket_problem(path, "docker", "0660")


def test_socket_nodes_check_uses_configured_observe_group_and_mode(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths, "docker")
    _group(monkeypatch, {"docker": 991})
    good = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=991)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: good)
    assert doctor._check_socket_nodes(_runtime(), _args(tmp_path)).status == "ok"
    good.st_mode = stat.S_IFREG | 0o660
    assert doctor._check_socket_nodes(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError("Permission denied")))
    assert doctor._check_socket_nodes(_runtime(), _args(tmp_path)).status == "skip"


def test_socket_directories_must_be_root_owned_and_not_group_other_writable(tmp_path: Path, monkeypatch) -> None:
    _config(InstallPaths(tmp_path))
    good = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: good)
    assert doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "ok"
    bad = SimpleNamespace(st_mode=stat.S_IFDIR | 0o770, st_uid=0, st_gid=0)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: bad)
    assert doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    assert doctor._check_socket_dirs(_runtime(), _args(tmp_path)).status == "skip"


def test_state_directory_checks_mode_owner_and_permission_skip(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths)
    info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: info)
    assert doctor._check_state_dir(_runtime(), _args(tmp_path)).status == "ok"
    info.st_gid = 4
    assert doctor._check_state_dir(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "lstat", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    result = doctor._check_state_dir(_runtime(), _args(tmp_path))
    assert result.status == "skip" and "run as root" in result.summary


def test_service_check_requires_active_installed_version_and_access(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths)
    Path(paths.opt, "0.7.3").mkdir(parents=True)
    Path(paths.opt, "0.7.3", "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.current).symlink_to("0.7.3")
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text('wheel = "0.7.3@sha256:' + "a" * 64 + '"\n', encoding="utf-8")
    _group(monkeypatch, {"topos-observe": 991})
    monkeypatch.setattr(doctor, "_run_systemctl", lambda *_args: True)
    monkeypatch.setattr(doctor, "_hello", lambda _path: {"identity": {"version": "0.7.3"}})
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    assert doctor._check_service(_runtime(), _args(tmp_path)).status == "ok"
    Path(paths.install_record).write_text('wheel = "0.7.2@sha256:' + "a" * 64 + '"\n', encoding="utf-8")
    result = doctor._check_service(_runtime(), _args(tmp_path))
    assert result.status == "fail" and result.remedy
    Path(paths.install_record).write_text('wheel = "0.7.3@sha256:' + "a" * 64 + '"\n', encoding="utf-8")
    monkeypatch.setattr(doctor, "_hello", lambda _path: {"identity": {"version": "0.7.2"}})
    assert doctor._check_service(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(doctor, "_run_systemctl", lambda *_args: False)
    assert doctor._check_service(_runtime(), _args(tmp_path)).status == "fail"


def test_service_and_connect_checks_skip_without_socket_access_or_fail_hello(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths)
    Path(paths.opt, "0.7.3").mkdir(parents=True)
    Path(paths.opt, "0.7.3", "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.current).symlink_to("0.7.3")
    _group(monkeypatch, {"topos-observe": 991})
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "getegid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "getgroups", lambda: [])
    monkeypatch.setattr(doctor, "_run_systemctl", lambda *_args: True)
    assert doctor._check_service(_runtime(), _args(tmp_path)).status == "skip"
    assert doctor._check_connect(_runtime(), _args(tmp_path)).status == "skip"

    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor, "_hello", lambda _path: (_ for _ in ()).throw(OSError("no listener")))
    result = doctor._check_connect(_runtime(), _args(tmp_path))
    assert result.status == "fail" and result.remedy
    monkeypatch.setattr(doctor, "_hello", lambda _path: {"identity": {}})
    assert doctor._check_connect(_runtime(), _args(tmp_path)).status == "fail"
    monkeypatch.setattr(doctor, "_hello", lambda _path: {"identity": {"version": "0.7.3"}})
    assert doctor._check_connect(_runtime(), _args(tmp_path)).status == "ok"


def test_foreign_rules_reuse_p11_and_skip_unreadable_nonroot(tmp_path: Path, monkeypatch) -> None:
    result = doctor._check_foreign_rules(_runtime(), _args(tmp_path))
    assert result.status == "ok"
    paths = InstallPaths(tmp_path)
    Path(paths.pending).parent.mkdir(parents=True)
    Path(paths.pending).write_text("interrupted\n", encoding="utf-8")
    result = doctor._check_foreign_rules(_runtime(), _args(tmp_path))
    assert result.status == "fail" and "Manual removal" in result.remedy
    monkeypatch.setattr(doctor, "old_installation_problems", lambda _paths: ["cannot inspect /etc/tmpfiles.d: denied"])
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    assert doctor._check_foreign_rules(_runtime(), _args(tmp_path)).status == "skip"


def test_caller_access_reports_root_nonmember_member_and_missing_group(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _config(paths, "docker")
    _group(monkeypatch, {"docker": 991})
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    assert doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "ok"
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "getegid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "getgroups", lambda: [])
    assert doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "warn"
    monkeypatch.setattr(doctor.os, "getgroups", lambda: [991])
    assert doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "ok"
    _group(monkeypatch, {})
    assert doctor._check_caller_access(_runtime(), _args(tmp_path)).status == "warn"


def test_systemctl_runner_handles_nonzero_and_spawn_failure(monkeypatch) -> None:
    monkeypatch.setattr(doctor.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    assert doctor._run_systemctl("is-active", "topos.service") is False
    monkeypatch.setattr(doctor.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")))
    assert doctor._run_systemctl("is-active", "topos.service") is False
