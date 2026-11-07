"""Read-only ``toposd doctor`` checks for the installed host service."""

from __future__ import annotations

import argparse
import grp
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from cli_extended import CliRegistry, CliRuntime, register_doctor
from cli_extended.doctor import CheckResult, DoctorCheck

from .config import ConfigError, ServiceConfig, load_config
from .install import (
    InstallError,
    InstallPaths,
    _hello,
    manual_removal_message,
    old_installation_problems,
    version_key,
)
from .lock import LockError, parse_pin
from .units import render_units


SYSTEMCTL = "systemctl"
CONNECT_TIMEOUT = 1.0


def _paths(args: argparse.Namespace) -> InstallPaths:
    return InstallPaths(getattr(args, "root", "/"))


def _config(args: argparse.Namespace) -> ServiceConfig:
    cached = getattr(args, "_topos_service_doctor_config", None)
    if cached is not None:
        return cached
    result = load_config(_paths(args).config)
    args._topos_service_doctor_config = result
    return result


def _permission_skip(exc: OSError, label: str) -> CheckResult | None:
    if isinstance(exc, PermissionError) and os.geteuid() != 0:
        return CheckResult("skip", f"cannot inspect {label}; run as root")
    return None


def _check_config_valid(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    try:
        _config(args)
    except ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return CheckResult("skip", "config is unreadable; run as root")
        return CheckResult("fail", str(exc), remedy="repair the config, then run toposd config check")
    return CheckResult("ok", f"{_paths(args).config} is valid")


def _config_or_skip(args: argparse.Namespace) -> tuple[ServiceConfig | None, CheckResult | None]:
    try:
        return _config(args), None
    except ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return None, CheckResult("skip", "config is unreadable; run as root")
        return None, CheckResult("skip", f"config could not be loaded: {exc}")


def _check_units_current(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    paths = _paths(args)
    installed = {
        "topos-observe.socket": paths.at("/etc/systemd/system/topos-observe.socket"),
        "topos.service": paths.at("/etc/systemd/system/topos.service"),
        "topos.slice": paths.at("/etc/systemd/system/topos.slice"),
        "topos.sysusers": paths.sysusers,
    }
    problems: list[str] = []
    for name, expected in render_units(config).items():
        try:
            actual = Path(installed[name]).read_text(encoding="utf-8")
        except OSError as exc:
            skipped = _permission_skip(exc, installed[name])
            if skipped:
                return skipped
            problems.append(f"{installed[name]}: {exc.strerror or type(exc).__name__}")
        else:
            if actual != expected:
                problems.append(f"{installed[name]} is missing or drifted")
    if problems:
        return CheckResult(
            "fail", problems[0],
            remedy="rerun toposd install with the intended topos, cli-extended and daemon-lock pins",
            details={"problems": problems},
        )
    return CheckResult("ok", "service, socket, slice and sysusers resources match the installed config")


def _check_groups(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    name = config.sockets.observe.group
    try:
        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        return CheckResult("fail", f"observe group {name!r} does not exist",
                           remedy=f"create group {name!r} or map observe to an existing group "
                                  "(toposd install --observe-group GROUP), then rerun toposd install")
    return CheckResult("ok", f"observe group {name} exists", details={"group": name, "gid": gid})


def _run_systemctl(*args: str) -> bool:
    try:
        result = subprocess.run([SYSTEMCTL, *args], capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _check_socket_units(_runtime: CliRuntime, _args: argparse.Namespace) -> CheckResult:
    problems = []
    if not _run_systemctl("is-enabled", "--quiet", "topos-observe.socket"):
        problems.append("topos-observe.socket is not enabled")
    if not _run_systemctl("is-active", "--quiet", "topos-observe.socket"):
        problems.append("topos-observe.socket is not active")
    if problems:
        return CheckResult("fail", "; ".join(problems), remedy="run toposd install --enable")
    return CheckResult("ok", "topos-observe.socket is enabled and active")


def _configured_socket_path(config: ServiceConfig, paths: InstallPaths) -> str:
    return paths.at(config.sockets.observe.path.as_posix())


def _expected_socket_problem(path: str, group: str, mode: str) -> str | None:
    """Return what is wrong with the socket node; PermissionError propagates."""
    try:
        info = os.lstat(path)
    except PermissionError:
        raise
    except OSError as exc:
        return f"{path}: {exc.strerror or type(exc).__name__}"
    if not stat.S_ISSOCK(info.st_mode):
        return f"{path}: not a socket"
    try:
        expected_gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return f"{path}: group {group} does not exist"
    actual_mode = stat.S_IMODE(info.st_mode)
    expected_mode = int(mode, 8)
    if info.st_uid != 0 or info.st_gid != expected_gid or actual_mode != expected_mode:
        return f"{path}: {info.st_uid}:{info.st_gid} {actual_mode:04o}, expected root:{group} {expected_mode:04o}"
    return None


def _check_socket_nodes(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    paths = _paths(args)
    socket_cfg = config.sockets.observe
    path = _configured_socket_path(config, paths)
    try:
        problem = _expected_socket_problem(path, socket_cfg.group, socket_cfg.mode)
    except PermissionError as exc:
        if os.geteuid() != 0:
            return CheckResult("skip", "socket node is unreadable; run as root")
        problem = f"{path}: {exc.strerror or type(exc).__name__}"
    if problem:
        return CheckResult("fail", problem, remedy="restart topos-observe.socket")
    return CheckResult("ok", f"{path} is a socket owned by root with the configured group and mode")


def _check_socket_dirs(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    paths = _paths(args)
    socket_parent = os.path.dirname(_configured_socket_path(config, paths))
    directories = sorted({paths.runtime_root, socket_parent})
    problems: list[str] = []
    for path in directories:
        try:
            info = os.lstat(path)
        except OSError as exc:
            skipped = _permission_skip(exc, path)
            if skipped:
                return skipped
            problems.append(f"{path}: {exc.strerror or type(exc).__name__}")
            continue
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode & 0o022:
            problems.append(f"{path}: {info.st_uid}:{info.st_gid} {mode:04o}, expected root-owned and not group/other writable")
    if problems:
        return CheckResult("fail", problems[0], remedy="remove the mismatched runtime tree and rerun toposd install",
                           details={"problems": problems})
    return CheckResult("ok", "socket directories are root-owned and not group- or other-writable")


def _check_state_dir(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    path = _paths(args).at(config.state_dir.as_posix())
    try:
        info = os.lstat(path)
    except OSError as exc:
        skipped = _permission_skip(exc, path)
        if skipped:
            return skipped
        return CheckResult("fail", f"{path}: {exc.strerror or type(exc).__name__}",
                           remedy="restart topos.service to create its state directory")
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode != 0o700:
        return CheckResult("fail", f"{path} is {info.st_uid}:{info.st_gid} {mode:04o}, expected root:root 0700",
                           remedy="stop topos.service, remove the mismatched state directory, then restart it")
    return CheckResult("ok", f"{path} is root:root 0700")


def _current_version(paths: InstallPaths) -> str | None:
    if not os.path.lexists(paths.current):
        return None
    if not os.path.islink(paths.current):
        return None
    target = os.readlink(paths.current)
    if not target or os.path.basename(target) != target:
        return None
    version = target
    try:
        version_key(version)
    except InstallError:
        return None
    version_dir = os.path.join(paths.opt, version)
    if not os.path.isdir(version_dir) or not os.path.isfile(os.path.join(version_dir, "MANIFEST")):
        return None
    return version


def _installed_version(paths: InstallPaths) -> str | None:
    current = _current_version(paths)
    if current is None:
        return None
    record = _read_install_record(paths.install_record)
    wheel = record.get("wheel")
    if not isinstance(wheel, str):
        return None
    try:
        version, _digest = parse_pin(wheel, "install record wheel")
    except LockError:
        return None
    manifest = os.path.join(paths.opt, current, "MANIFEST")
    if version != current or not os.path.isfile(manifest):
        return None
    return version


def _read_install_record(path: str) -> dict[str, Any]:
    import tomllib

    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except PermissionError:
        raise
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _check_service(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    paths = _paths(args)
    if not _run_systemctl("is-active", "--quiet", "topos.service"):
        return CheckResult("fail", "topos.service is not active", remedy="run systemctl start topos.service")
    try:
        installed = _installed_version(paths)
    except PermissionError:
        if os.geteuid() != 0:
            return CheckResult("skip", "installed version is unreadable; run as root")
        return CheckResult("fail", "cannot inspect the installed version",
                           remedy="check /opt/topos and /etc/topos/install.toml permissions")
    except OSError as exc:
        return CheckResult("fail", f"cannot inspect the installed version: {exc}",
                           remedy="check /opt/topos and /etc/topos/install.toml")
    if installed is None:
        return CheckResult("fail", "the /opt/topos/current version pointer is missing or invalid",
                           remedy="run toposd install with the intended wheel and lock pins")
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    try:
        gid = grp.getgrnam(config.sockets.observe.group).gr_gid
    except KeyError:
        gid = None
    if os.geteuid() != 0 and (gid is None or not _caller_in_group(gid)):
        return CheckResult("skip", "cannot verify the running version without observe-socket access; run as root")
    try:
        hello = _hello(_configured_socket_path(config, paths))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return CheckResult("fail", f"cannot verify the running topos version: {type(exc).__name__}: {exc}",
                           remedy="check topos.service and the observe socket")
    identity = hello.get("identity")
    running = identity.get("version") if isinstance(identity, dict) else None
    if running != installed:
        return CheckResult("fail", f"topos reports {running}, current points to {installed}",
                           remedy="restart topos.service so it runs the installed version")
    activation = hello.get("socket_activation")
    if (
        not isinstance(activation, dict)
        or activation.get("mode") != "activated"
        or type(activation.get("listeners")) is not int
        or activation.get("listeners") != 1
    ):
        return CheckResult(
            "fail",
            "topos.service was not started by topos-observe.socket with exactly one listener",
            remedy="topos.service was not started by topos-observe.socket; run systemctl restart topos-observe.socket",
        )
    return CheckResult("ok", f"topos.service is active at version {installed}", details={"version": installed})


def _caller_in_group(gid: int) -> bool:
    return gid == os.getegid() or gid in os.getgroups()


def _check_connect(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    group = config.sockets.observe.group
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return CheckResult("fail", f"configured observe group {group!r} does not exist",
                           remedy="create the configured group, then rerun toposd install")
    if os.geteuid() != 0 and not _caller_in_group(gid):
        return CheckResult("skip", "caller is not in the observe group; EACCES is expected")
    try:
        hello = _hello(_configured_socket_path(config, _paths(args)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return CheckResult("fail", f"observe socket hello failed: {type(exc).__name__}: {exc}",
                           remedy="restart topos-observe.socket and inspect journalctl -u topos.service")
    identity = hello.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("version"), str):
        return CheckResult("fail", "observe socket returned a hello without a daemon version",
                           remedy="install a current topos wheel and restart topos.service")
    return CheckResult("ok", f"observe socket answered hello from topos {identity['version']}",
                       details={"version": identity["version"]})


def _check_foreign_rules(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    paths = _paths(args)
    found = old_installation_problems(paths)
    # Unreadable entries appear both as "cannot inspect DIR" and as
    # "foreign rule or drop-in: PATH (cannot inspect: ...)".
    if os.geteuid() != 0 and any("cannot inspect" in item for item in found):
        return CheckResult("skip", "foreign service files are unreadable; run as root")
    if found:
        return CheckResult("fail", "old or foreign files conflict with the topos host service",
                           remedy=manual_removal_message(found), details={"paths": found}, lines=tuple(found))
    return CheckResult("ok", "no old tmpfiles, drop-ins, runtime paths or installer manifests were found")


def _check_caller_access(_runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    config, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert config is not None
    group = config.sockets.observe.group
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return CheckResult("warn", f"observe group {group!r} does not exist",
                           remedy="create the configured group or map observe to an existing group")
    member = _caller_in_group(gid)
    details = {"group": group, "member": member}
    if os.geteuid() == 0:
        return CheckResult("ok", "root has administrative access; observe-group membership is informational",
                           details=details)
    if not member:
        return CheckResult("warn", f"caller is not a member of {group}",
                           remedy=f"add the caller to {group} and start a new login session", details=details)
    return CheckResult("ok", f"caller belongs to {group}", details=details)


SERVICE_DOCTOR_CHECKS = (
    DoctorCheck("config-valid", "service config parses and validates", _check_config_valid),
    DoctorCheck("units-current", "installed units match their rendered config", _check_units_current),
    DoctorCheck("groups", "the configured observe group exists", _check_groups),
    DoctorCheck("socket-units", "the observe socket unit is enabled and active", _check_socket_units),
    DoctorCheck("socket-nodes", "the observe socket has the configured owner, group and mode", _check_socket_nodes),
    DoctorCheck("socket-dirs", "socket directories are root-owned and not writable by group or others", _check_socket_dirs),
    DoctorCheck("state-dir", "service state directory is root-owned mode 0700", _check_state_dir),
    DoctorCheck("service", "topos.service is active at the installed version", _check_service),
    DoctorCheck("connect", "the observe socket answers a hello probe", _check_connect),
    DoctorCheck("foreign-rules", "no old or foreign state conflicts with the service", _check_foreign_rules),
    DoctorCheck("caller-access", "report the caller's observe-group membership", _check_caller_access),
)


def register_service_doctor(registry: CliRegistry) -> None:
    """Register doctor using cli-extended's common ``--check`` and ``--json``."""
    register_doctor(
        registry,
        SERVICE_DOCTOR_CHECKS,
        description="check topos installation, systemd socket, permissions and connectivity",
    )


__all__ = ["SERVICE_DOCTOR_CHECKS", "register_service_doctor"]
