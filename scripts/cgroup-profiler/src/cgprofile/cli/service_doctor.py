"""Read-only service-side ``cgpd doctor`` checks for the native install."""

from __future__ import annotations

import argparse
import grp
import json
import os
import socket
import stat
import subprocess
from typing import Any, Dict, Mapping, Optional, Tuple

from cli_extended import CliRegistry, CliRuntime, OptionSpec, register_doctor
from cli_extended.doctor import CheckResult, DoctorCheck

from .. import daemon_config, unitfiles
from .installer import InstallPaths, find_foreign_rules, version_key

DEFAULT_SYSTEMCTL = "systemctl"
CONNECT_TIMEOUT = 2.0


def _config(args: argparse.Namespace) -> Tuple[str, Dict[str, Any]]:
    cache = getattr(args, "_cgpd_service_doctor_config", None)
    if cache is not None:
        return cache
    path = args.config or daemon_config.DEFAULT_CONFIG_PATH
    values, _digest = daemon_config.read_config_file(path, explicit=bool(args.config))
    result = (path, values)
    args._cgpd_service_doctor_config = result
    return result


def _check_config_valid(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    try:
        path, values = _config(args)
    except daemon_config.ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return CheckResult("skip", "config is unreadable; run as root")
        return CheckResult("fail", str(exc), remedy="repair the config, then run cgpd config check")
    return CheckResult("ok", f"{path} is valid", details={"keys": sorted(values)})


def _config_or_skip(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], Optional[CheckResult]]:
    try:
        _path, values = _config(args)
    except daemon_config.ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return None, CheckResult("skip", "config is unreadable; run as root")
        return None, CheckResult("skip", f"config could not be loaded: {exc}")
    return values, None


def _check_units_current(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    rendered = unitfiles.render(config_values=values)
    problems = unitfiles.check(unitfiles.default_installed_map(), rendered)
    if problems:
        return CheckResult("fail", problems[0], remedy="rerun cgpd install with the intended wheel pin",
                           details={"problems": problems})
    return CheckResult("ok", "service, socket, slice and sysusers resources match the installed config")


def _check_groups(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    sockets = daemon_config.socket_settings(values)
    groups = {level: sockets[level]["group"] for level in daemon_config.SOCKET_LEVELS}
    gids: Dict[str, Optional[int]] = {}
    missing = []
    for level, name in groups.items():
        try:
            gids[level] = grp.getgrnam(name).gr_gid
        except KeyError:
            gids[level] = None
            missing.append(name)
    if missing:
        return CheckResult("fail", "missing socket group(s): " + ", ".join(missing),
                           remedy=("create missing mapped groups, or use the default cgprofile level groups; "
                                   "then rerun cgpd install"))
    if gids["control"] == gids["observer"]:
        return CheckResult("fail", "control and observer groups resolve to the same GID",
                           remedy="configure separate groups and rerun cgpd install")
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        docker_gid = None
    if gids["observer"] == docker_gid:
        return CheckResult("fail", "observer group resolves to the docker GID",
                           remedy="configure cgprofile-observer or another non-docker group")
    return CheckResult("ok", "control and observer groups exist and the observer GID differs from docker",
                       details={"groups": groups, "gids": gids})


def _systemctl(*argv: str) -> bool:
    try:
        result = subprocess.run([DEFAULT_SYSTEMCTL, *argv], capture_output=True, text=True,
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _check_socket_units(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    units = ("cgprofile-control.socket", "cgprofile-observer.socket")
    problems = []
    for unit in units:
        if not _systemctl("is-enabled", "--quiet", unit):
            problems.append(f"{unit} is not enabled")
        if not _systemctl("is-active", "--quiet", unit):
            problems.append(f"{unit} is not active")
    if problems:
        return CheckResult("fail", "; ".join(problems),
                           remedy="run cgpd install --enable to enable and start both socket units")
    return CheckResult("ok", "both per-level socket units are enabled and active")


def _expected_socket_stat(path: str, group: str, mode: str) -> Optional[str]:
    try:
        info = os.lstat(path)
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
        return (f"{path}: {info.st_uid}:{info.st_gid} {actual_mode:04o}, expected "
                f"root:{group} {expected_mode:04o}")
    return None


def _check_socket_nodes(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    sockets = daemon_config.socket_settings(values)
    problems = [problem for level in daemon_config.SOCKET_LEVELS
                if (problem := _expected_socket_stat(
                    sockets[level]["path"], sockets[level]["group"], sockets[level]["mode"]))]
    if problems:
        return CheckResult("fail", problems[0],
                           remedy="restart the affected cgprofile-<level>.socket unit",
                           details={"problems": problems})
    return CheckResult("ok", "both socket nodes are root-owned with configured group and mode")


def _check_socket_dirs(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    sockets = daemon_config.socket_settings(values)
    paths = {"/run/cgprofile"}
    paths.update(os.path.dirname(sockets[level]["path"]) for level in daemon_config.SOCKET_LEVELS)
    problems = []
    for path in sorted(paths):
        try:
            info = os.lstat(path)
        except OSError as exc:
            problems.append(f"{path}: {exc.strerror or type(exc).__name__}")
            continue
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode != 0o755:
            problems.append(f"{path}: {info.st_uid}:{info.st_gid} {mode:04o}, expected root:root 0755")
    if problems:
        return CheckResult("fail", problems[0], remedy="remove the mismatched runtime tree, then restart cgpd install",
                           details={"problems": problems})
    return CheckResult("ok", "socket directories are root:root 0755")


def _check_state_dir(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    path = "/run/cgprofile/state"
    try:
        info = os.lstat(path)
    except OSError as exc:
        if os.geteuid() != 0 and isinstance(exc, PermissionError):
            return CheckResult("skip", "state directory is unreadable; run as root")
        return CheckResult("fail", f"{path}: {exc.strerror or type(exc).__name__}",
                           remedy="restart cgpd.service to create its state directory")
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode != 0o700:
        return CheckResult("fail", f"{path} is {info.st_uid}:{info.st_gid} {mode:04o}, expected root:root 0700",
                           remedy="stop cgpd, remove the mismatched state directory, then restart cgpd")
    return CheckResult("ok", f"{path} is root:root 0700")


def _version_request(path: str) -> Dict[str, Any]:
    payload = {"verb": "version", "args": {}, "contract": 2}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(CONNECT_TIMEOUT)
        client.connect(path)
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
    body = json.loads(data.decode("utf-8"))
    if (not isinstance(body, dict) or body.get("ok") is not True
            or body.get("contract") != 2):
        raise OSError("control socket returned no successful contract-2 version response")
    return body


def _check_service(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    if not _systemctl("is-active", "--quiet", "cgpd.service"):
        return CheckResult("fail", "cgpd.service is not active", remedy="run systemctl start cgpd.service")
    current = "/opt/cgprofile/current"
    try:
        target = os.readlink(current)
        version = os.path.basename(target.rstrip("/"))
        version_key(version)
    except (OSError, ValueError):
        return CheckResult("fail", "the /opt/cgprofile/current version pointer is missing or invalid",
                           remedy="run cgpd install with the intended release pin")
    try:
        values, _digest = _config(args)
        path = daemon_config.socket_settings(values)["control"]["path"]
        response = _version_request(path)
        daemon = response.get("daemon") if isinstance(response.get("daemon"), dict) else {}
        running_version = daemon.get("version")
        if running_version != version:
            return CheckResult("fail", f"cgpd reports {running_version}, current points to {version}",
                               remedy="restart cgpd.service so it runs the installed version")
        activation = response.get("socket_activation")
        if not isinstance(activation, dict):
            return CheckResult("fail", "cgpd does not report socket activation state",
                               remedy="install the 2.2.0 service units and restart cgpd.service")
        fds = activation.get("fds", {})
        mode = activation.get("mode")
        if (mode != "activated" or not isinstance(fds, dict)
                or set(fds) != set(daemon_config.SOCKET_LEVELS)
                or any(type(fds.get(level)) is not int or fds[level] not in (0, 1)
                       for level in daemon_config.SOCKET_LEVELS)):
            return CheckResult("fail", "cgpd is not running with valid systemd socket activation",
                               remedy="start cgpd.service through both cgprofile socket units")
        state = (f"mode={activation.get('mode')} control_fds={fds.get('control', 0)} "
                 f"observer_fds={fds.get('observer', 0)}")
    except (OSError, ValueError, daemon_config.ConfigError, json.JSONDecodeError) as exc:
        try:
            group = daemon_config.socket_settings(_config(args)[1])["control"]["group"]
            gid = grp.getgrnam(group).gr_gid
        except (daemon_config.ConfigError, KeyError):
            gid = None
        if os.geteuid() != 0 and gid is not None and not _caller_in_group(gid):
            return CheckResult("skip", "cannot verify the running version; caller lacks control-socket access",
                               details={"reason": str(exc)})
        return CheckResult("fail", f"cannot verify the running cgpd version: {type(exc).__name__}: {exc}",
                           remedy="check cgpd.service and its control socket")
    return CheckResult("ok", f"cgpd.service is active at version {version}; {state}",
                       details={"version": version, "socket_activation": state})


def _caller_in_group(gid: int) -> bool:
    return gid == os.getegid() or gid in os.getgroups()


def _observer_request(path: str) -> Dict[str, Any]:
    payload = {"verb": "host", "args": {}, "contract": 1}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(CONNECT_TIMEOUT)
        client.connect(path)
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
    body = json.loads(data.decode("utf-8"))
    if not isinstance(body, dict) or body.get("ok") is not True or body.get("contract") != 1:
        raise OSError("observer socket returned no successful contract-1 host response")
    return body


def _check_connect(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    sockets = daemon_config.socket_settings(values)
    problems = []
    skipped_levels = []
    results: Dict[str, Any] = {}
    for level in daemon_config.SOCKET_LEVELS:
        path = sockets[level]["path"]
        group = sockets[level]["group"]
        try:
            gid = grp.getgrnam(group).gr_gid
        except KeyError:
            problems.append(f"{level}: configured group {group} is absent")
            continue
        if os.geteuid() != 0 and not _caller_in_group(gid):
            skipped_levels.append(level)
            results[level] = "skip: caller is not in the socket group"
            continue
        try:
            if level == "control":
                response = _version_request(path)
                activation = response.get("socket_activation", {})
                results[level] = {"ok": True, "socket_activation": activation}
            else:
                _observer_request(path)
                results[level] = {"ok": True, "contract": 1}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{level}: {type(exc).__name__}: {exc}")
            results[level] = "fail"
    if problems:
        return CheckResult("fail", problems[0], remedy="restart the matching cgprofile socket unit and inspect cgpd logs",
                           details=results)
    suffix = f"; skipped expected access for {', '.join(skipped_levels)}" if skipped_levels else ""
    return CheckResult("ok", "available access levels answer their health probes" + suffix, details=results)


def _check_foreign_rules(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    paths = InstallPaths()
    found = find_foreign_rules(paths)
    if found:
        return CheckResult("fail", "foreign tmpfiles, sysusers or drop-in files claim the cgprofile service",
                           remedy="remove the listed old host-setup files manually before installing cgpd",
                           details={"paths": found}, lines=tuple(found))
    return CheckResult("ok", "no foreign tmpfiles, sysusers or service drop-ins claim cgprofile")


def _check_caller_access(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    sockets = daemon_config.socket_settings(values)
    memberships = {}
    missing = []
    for level in daemon_config.SOCKET_LEVELS:
        name = sockets[level]["group"]
        try:
            gid = grp.getgrnam(name).gr_gid
        except KeyError:
            memberships[level] = False
            continue
        memberships[level] = _caller_in_group(gid)
        if not memberships[level]:
            missing.append(name)
    if os.geteuid() == 0:
        return CheckResult("ok", "root has administrative access; level group membership is informational",
                           details={"memberships": memberships})
    if missing:
        return CheckResult("warn", "caller is not in: " + ", ".join(missing),
                           remedy="add the caller to the required level group and start a new login session",
                           details={"memberships": memberships})
    return CheckResult("ok", "caller belongs to both configured access groups",
                       details={"memberships": memberships})


SERVICE_DOCTOR_CHECKS = (
    DoctorCheck("config-valid", "configured cgpd settings parse and validate", _check_config_valid),
    DoctorCheck("units-current", "installed service resources match the rendered config", _check_units_current),
    DoctorCheck("groups", "both access groups exist and observer differs from docker", _check_groups),
    DoctorCheck("socket-units", "both per-level socket units are enabled and active", _check_socket_units),
    DoctorCheck("socket-nodes", "both listening nodes are sockets with configured owner, group and mode", _check_socket_nodes),
    DoctorCheck("socket-dirs", "socket directories are root-owned and not writable by group or others", _check_socket_dirs),
    DoctorCheck("state-dir", "daemon state directory is root-owned mode 0700", _check_state_dir),
    DoctorCheck("service", "cgpd service is active at the installed version", _check_service),
    DoctorCheck("connect", "each accessible socket answers a health or contract probe", _check_connect),
    DoctorCheck("foreign-rules", "no foreign tmpfiles or drop-ins claim the service tree", _check_foreign_rules),
    DoctorCheck("caller-access", "report the caller's access-group memberships", _check_caller_access),
)


def register_service_doctor(registry: CliRegistry) -> None:
    register_doctor(registry, SERVICE_DOCTOR_CHECKS,
                    description="check cgpd installation, systemd sockets, permissions and connectivity")

