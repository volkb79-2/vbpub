"""Read-only service-side ``cgprofiled doctor`` checks for the native install.

The check names, the skip rules (a level the caller is not in, a path a non-root
caller cannot read) and the remedies are the same as the ``toposd doctor`` ones.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import socket
import stat
import subprocess
import threading
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cli_extended import CliRegistry, CliRuntime, register_doctor
from cli_extended.doctor import CheckResult, DoctorCheck

from .. import daemon_config, unitfiles
from .installer import (
    InstallError,
    InstallPaths,
    manual_removal_message,
    old_installation_problems,
    parse_pin,
    _record_words,
    version_key,
)

DEFAULT_SYSTEMCTL = "systemctl"
CONNECT_TIMEOUT = 2.0


def _paths(args: argparse.Namespace) -> InstallPaths:
    # ``root`` is a test seam (the fake-root tests set it); there is no such option.
    return InstallPaths(getattr(args, "root", "/"))


def _config(args: argparse.Namespace) -> Tuple[str, Dict[str, Any]]:
    cache = getattr(args, "_cgprofiled_service_doctor_config", None)
    if cache is not None:
        return cache
    path = args.config or _paths(args).config
    values, _digest = daemon_config.read_config_file(path, explicit=bool(args.config))
    result = (path, values)
    args._cgprofiled_service_doctor_config = result
    return result


def _permission_skip(exc: OSError, label: str) -> Optional[CheckResult]:
    if isinstance(exc, PermissionError) and os.geteuid() != 0:
        return CheckResult("skip", f"cannot inspect {label}; run as root")
    return None


def _check_config_valid(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    try:
        path, values = _config(args)
    except daemon_config.ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return CheckResult("skip", "config is unreadable; run as root")
        return CheckResult("fail", str(exc), remedy="repair the config, then run cgprofiled config check")
    return CheckResult("ok", f"{path} is valid", details={"keys": sorted(values)})


def _check_endpoint_record(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    try:
        from augeaswrap import endpoints
    except ImportError as exc:
        return CheckResult("fail", f"augeaswrap.endpoints is unavailable: {exc}",
                           remedy="install the pinned cgprofile runtime dependencies")
    expected: Dict[str, str] = {}
    sockets = daemon_config.socket_settings(values)
    for level, prefix in (("control", "CGPROFILE_CONTROL"), ("observer", "CGPROFILE_OBSERVER")):
        socket_value = sockets[level]
        try:
            gid = grp.getgrnam(socket_value["group"]).gr_gid
        except KeyError:
            return CheckResult("fail", f"configured {level} group {socket_value['group']!r} has no GID",
                               remedy="repair the socket groups, then rerun cgprofiled install")
        expected[f"{prefix}_SOCKET"] = socket_value["path"]
        expected[f"{prefix}_SOCKET_DIR"] = os.path.dirname(socket_value["path"])
        expected[f"{prefix}_GID"] = str(gid)
    problems = endpoints.verify("cgprofile", expected, root=_paths(args).root)
    if problems:
        return CheckResult(
            "fail", str(problems[0]),
            remedy="rerun cgprofiled install to republish the endpoint record",
            details={"problems": [str(problem) for problem in problems]},
        )
    return CheckResult("ok", "cgprofile endpoint fragment matches the configured sockets and host group IDs",
                       details=expected)


def _config_or_skip(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], Optional[CheckResult]]:
    try:
        _path, values = _config(args)
    except daemon_config.ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return None, CheckResult("skip", "config is unreadable; run as root")
        return None, CheckResult("skip", f"config could not be loaded: {exc}")
    return values, None


def _installed_resources(paths: InstallPaths) -> Dict[str, str]:
    return {
        "cgprofile.slice": os.path.join(paths.unit_dir, "cgprofile.slice"),
        "cgprofiled.service": os.path.join(paths.unit_dir, "cgprofiled.service"),
        "cgprofile-control.socket": os.path.join(paths.unit_dir, "cgprofile-control.socket"),
        "cgprofile-observer.socket": os.path.join(paths.unit_dir, "cgprofile-observer.socket"),
        "cgprofile.sysusers": os.path.join(paths.sysusers_dir, "cgprofile.conf"),
    }


def _check_units_current(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    installed = _installed_resources(_paths(args))
    problems: List[str] = []
    for name, expected in unitfiles.render(config_values=values).items():
        try:
            actual = Path(installed[name]).read_text(encoding="utf-8")
        except OSError as exc:
            permission = _permission_skip(exc, installed[name])
            if permission:
                return permission
            problems.append(f"{name}: cannot read {installed[name]}: {exc.strerror or type(exc).__name__}")
            continue
        difference = unitfiles.first_difference(expected, actual)
        if difference is not None:
            problems.append(f"{name}: {installed[name]} differs from the rendered unit, {difference}")
    if problems:
        return CheckResult("fail", problems[0], remedy="rerun cgprofiled install with the intended wheel pin",
                           details={"problems": problems})
    return CheckResult("ok", "service, socket, slice and sysusers resources match the installed config")


def _check_groups(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    path = args.config or _paths(args).config
    try:
        values, _digest = daemon_config.read_config_file(path, explicit=bool(args.config), validate_groups=False)
    except daemon_config.ConfigError as exc:
        if os.geteuid() != 0 and "permission denied" in str(exc).lower():
            return CheckResult("skip", "config is unreadable; run as root")
        return CheckResult("skip", f"config could not be loaded: {exc}")
    groups = {
        level: daemon_config.validate(
            f"sockets.{level}", values.get(f"sockets.{level}", daemon_config.KEYS[f"sockets.{level}"].default)
        )["group"]
        for level in daemon_config.SOCKET_LEVELS
    }
    group_entries: Dict[str, Any] = {}
    gids: Dict[str, int] = {}
    missing = []
    for level, name in groups.items():
        if name == "docker":
            return CheckResult("fail", f"{level} group must not be docker",
                               remedy="configure a dedicated level group and rerun cgprofiled install")
        try:
            entry = grp.getgrnam(name)
        except KeyError:
            missing.append(name)
            continue
        group_entries[level] = entry
        gids[level] = entry.gr_gid
    if missing:
        return CheckResult("fail", "missing socket group(s): " + ", ".join(missing),
                           remedy=("create missing mapped groups, or use the default cgprofile level groups; "
                                   "then rerun cgprofiled install"))
    for level, gid in gids.items():
        if gid == 0:
            return CheckResult("fail", f"{level} group resolves to GID 0",
                               remedy="configure a non-root dedicated level group and rerun cgprofiled install")
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        docker_gid = None
    if docker_gid is not None:
        for level, gid in gids.items():
            if gid == docker_gid:
                return CheckResult("fail", f"{level} group resolves to the docker GID",
                                   remedy="configure a dedicated level group with a different GID")
    if groups["control"] == groups["observer"]:
        return CheckResult("fail", "control and observer groups must differ",
                           remedy="configure separate groups and rerun cgprofiled install")
    if gids["control"] == gids["observer"]:
        return CheckResult("fail", "control and observer groups resolve to the same GID",
                           remedy="configure separate groups and rerun cgprofiled install")
    paths = _paths(args)
    try:
        record = _read_install_record(paths.install_record)
    except PermissionError as exc:
        skipped = _permission_skip(exc, paths.install_record)
        if skipped:
            return skipped
        return CheckResult("fail", f"cannot read {paths.install_record}: {exc.strerror or exc}",
                           remedy="make the install record readable, then rerun cgprofiled doctor")
    details: Dict[str, Any] = {}
    missing_members: List[Tuple[str, str]] = []
    missing_accounts: List[Tuple[str, str]] = []
    for level, name in groups.items():
        key = f"{level}_users"
        try:
            recorded = _record_words(record.get(key), key)
        except InstallError as exc:
            return CheckResult("fail", str(exc), remedy="repair the cgprofiled install record and rerun cgprofiled install")
        entry = group_entries[level]
        members = set(entry.gr_mem)
        for user in recorded:
            if user in members:
                continue
            try:
                account = pwd.getpwnam(user)
            except KeyError:
                missing_accounts.append((level, user))
            else:
                if account.pw_gid == gids[level]:
                    members.add(user)
                else:
                    missing_members.append((level, user))
        details[level] = {
            "name": name, "gid": gids[level], "members": sorted(members),
            "recorded_users": recorded,
        }
    if missing_members or missing_accounts:
        summary = "; ".join(f"{level} group is missing recorded user {user}" for level, user in missing_members)
        summary += ("; " if summary and missing_accounts else "")
        summary += "; ".join(f"recorded user {user} for {level} does not exist" for level, user in missing_accounts)
        return CheckResult(
            "warn", summary,
            remedy="rerun cgprofiled install (recorded users are re-added) or --add-user LEVEL=USER",
            details={"groups": details, "gids": gids},
        )
    return CheckResult("ok", "control and observer groups are dedicated and their recorded members are present",
                       details={"groups": details, "gids": gids})


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
                           remedy="run cgprofiled install --enable to enable and start both socket units")
    return CheckResult("ok", "both per-level socket units are enabled and active")


def _expected_socket_problem(path: str, group: str, mode: str) -> Optional[str]:
    """What is wrong with the socket node, or None; PermissionError propagates."""
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
        return (f"{path}: {info.st_uid}:{info.st_gid} {actual_mode:04o}, expected "
                f"root:{group} {expected_mode:04o}")
    return None


def _check_socket_nodes(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    paths = _paths(args)
    sockets = daemon_config.socket_settings(values)
    problems = []
    for level in daemon_config.SOCKET_LEVELS:
        node = paths.at(sockets[level]["path"])
        try:
            problem = _expected_socket_problem(node, sockets[level]["group"], sockets[level]["mode"])
        except PermissionError as exc:
            if os.geteuid() != 0:
                return CheckResult("skip", "socket node is unreadable; run as root")
            problem = f"{node}: {exc.strerror or type(exc).__name__}"
        if problem:
            problems.append(problem)
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
    paths = _paths(args)
    sockets = daemon_config.socket_settings(values)
    directories = {paths.runtime_root}
    directories.update(os.path.dirname(paths.at(sockets[level]["path"])) for level in daemon_config.SOCKET_LEVELS)
    problems = []
    for path in sorted(directories):
        try:
            info = os.lstat(path)
        except OSError as exc:
            permission = _permission_skip(exc, path)
            if permission:
                return permission
            problems.append(f"{path}: {exc.strerror or type(exc).__name__}")
            continue
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode & 0o022:
            problems.append(
                f"{path}: {info.st_uid}:{info.st_gid} {mode:04o}, expected root-owned and not group/other writable")
    if problems:
        return CheckResult("fail", problems[0],
                           remedy="remove the mismatched runtime tree and rerun cgprofiled install",
                           details={"problems": problems})
    return CheckResult("ok", "socket directories are root-owned and not group- or other-writable")


def _check_state_dir(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    path = _paths(args).state_dir
    try:
        info = os.lstat(path)
    except OSError as exc:
        permission = _permission_skip(exc, path)
        if permission:
            return permission
        return CheckResult("fail", f"{path}: {exc.strerror or type(exc).__name__}",
                           remedy="restart cgprofiled.service to create its state directory")
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or mode != 0o700:
        return CheckResult("fail", f"{path} is {info.st_uid}:{info.st_gid} {mode:04o}, expected root:root 0700",
                           remedy="stop cgprofiled.service, remove the mismatched state directory, then restart it")
    return CheckResult("ok", f"{path} is root:root 0700")


def _read_install_record(path: str) -> Mapping[str, Any]:
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except PermissionError:
        raise
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _installed_version(paths: InstallPaths) -> Optional[str]:
    """The version ``current`` selects, when the record and the MANIFEST agree with it."""
    if not os.path.islink(paths.current):
        return None
    target = os.readlink(paths.current)
    if not target or os.path.basename(target) != target:
        return None
    try:
        version_key(target)
    except InstallError:
        return None
    if not os.path.isfile(os.path.join(paths.opt, target, "MANIFEST")):
        return None
    wheel = _read_install_record(paths.install_record).get("wheel")
    if not isinstance(wheel, str):
        return None
    try:
        version, _digest = parse_pin(wheel, "install record wheel")
    except InstallError:
        return None
    return target if version == target else None


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
    paths = _paths(args)
    if not _systemctl("is-active", "--quiet", "cgprofiled.service"):
        return CheckResult("fail", "cgprofiled.service is not active", remedy="run systemctl start cgprofiled.service")
    try:
        version = _installed_version(paths)
    except PermissionError:
        if os.geteuid() != 0:
            return CheckResult("skip", "installed version is unreadable; run as root")
        return CheckResult("fail", "cannot inspect the installed version",
                           remedy="check /opt/cgprofile and /etc/cgprofile/install.toml permissions")
    if version is None:
        return CheckResult("fail", "the /opt/cgprofile/current version pointer is missing or invalid",
                           remedy="run cgprofiled install with the intended release pin")
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    control = daemon_config.socket_settings(values)["control"]
    try:
        gid: Optional[int] = grp.getgrnam(control["group"]).gr_gid
    except KeyError:
        gid = None
    if os.geteuid() != 0 and (gid is None or not _caller_in_group(gid)):
        return CheckResult("skip", "cannot verify the running version; caller lacks control-socket access")
    try:
        response = _version_request(paths.at(control["path"]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return CheckResult("fail", f"cannot verify the running cgprofiled version: {type(exc).__name__}: {exc}",
                           remedy="check cgprofiled.service and its control socket")
    daemon = response.get("daemon") if isinstance(response.get("daemon"), dict) else {}
    running_version = daemon.get("version")
    if running_version != version:
        return CheckResult("fail", f"cgprofiled reports {running_version}, current points to {version}",
                           remedy="restart cgprofiled.service so it runs the installed version")
    activation = response.get("socket_activation")
    if not isinstance(activation, dict):
        return CheckResult("fail", "cgprofiled does not report socket activation state",
                           remedy="install the 2.2.0 service units and restart cgprofiled.service")
    fds = activation.get("fds", {})
    if (activation.get("mode") != "activated" or not isinstance(fds, dict)
            or set(fds) != set(daemon_config.SOCKET_LEVELS)
            or any(type(fds.get(level)) is not int or fds[level] not in (0, 1)
                   for level in daemon_config.SOCKET_LEVELS)):
        return CheckResult("fail", "cgprofiled is not running with valid systemd socket activation",
                           remedy="start cgprofiled.service through both cgprofile socket units")
    state = (f"mode={activation.get('mode')} control_fds={fds.get('control', 0)} "
             f"observer_fds={fds.get('observer', 0)}")
    return CheckResult("ok", f"cgprofiled.service is active at version {version}; {state}",
                       details={"version": version, "socket_activation": state})


def _caller_in_group(gid: int) -> bool:
    return gid == os.getegid() or gid in os.getgroups()


OBSERVER_PROBE_CONTRACT = 2


def _observer_request(path: str) -> Dict[str, Any]:
    """Probe the observer endpoint with a request it serves (C1).

    The observer accepts contracts 2 and 3 only, and over contract 2 only ``resource-limits``. The probe
    asks for this process's own thread: an answer proves the endpoint is up and authenticates the peer,
    whether it is ``ok`` or the ``observer-refused`` a caller outside the gates slice gets. Anything else
    (a ``bad-argument`` for the wrong contract, no envelope at all) is a failed probe.
    """
    payload = {"verb": "resource-limits", "args": {"thread_id": threading.get_native_id()},
               "contract": OBSERVER_PROBE_CONTRACT}
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
    if not isinstance(body, dict) or body.get("contract") != OBSERVER_PROBE_CONTRACT:
        raise OSError("observer socket returned no contract-2 resource-limits envelope")
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if body.get("ok") is not True and code != "observer-refused":
        raise OSError(f"observer socket refused the contract-2 probe: {code or 'no error code'}")
    return body


def _check_connect(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    values, skipped = _config_or_skip(args)
    if skipped:
        return skipped
    assert values is not None
    paths = _paths(args)
    sockets = daemon_config.socket_settings(values)
    problems = []
    skipped_levels = []
    results: Dict[str, Any] = {}
    for level in daemon_config.SOCKET_LEVELS:
        path = paths.at(sockets[level]["path"])
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
                results[level] = {"ok": True, "socket_activation": response.get("socket_activation", {})}
            else:
                _observer_request(path)
                results[level] = {"ok": True, "contract": OBSERVER_PROBE_CONTRACT}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{level}: {type(exc).__name__}: {exc}")
            results[level] = "fail"
    if problems:
        return CheckResult("fail", problems[0],
                           remedy="restart the matching cgprofile socket unit and inspect cgprofiled logs",
                           details=results)
    suffix = f"; skipped expected access for {', '.join(skipped_levels)}" if skipped_levels else ""
    return CheckResult("ok", "available access levels answer their health probes" + suffix, details=results)


def _check_foreign_rules(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    paths = _paths(args)
    try:
        found = old_installation_problems(paths, args.config or paths.config)
    except InstallError as exc:
        return CheckResult("fail", str(exc), remedy="repair or remove the unreadable config, then rerun")
    # Unreadable entries appear both as "cannot inspect DIR" and as
    # "foreign rule or drop-in: PATH (cannot inspect: ...)".
    if os.geteuid() != 0 and any("cannot inspect" in item for item in found):
        return CheckResult("skip", "foreign service files are unreadable; run as root")
    if found:
        return CheckResult("fail", "old or foreign files conflict with the cgprofile host service",
                           remedy=manual_removal_message(found), details={"paths": found}, lines=tuple(found))
    return CheckResult("ok", "no old tmpfiles, drop-ins, runtime paths or installer manifests were found")


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
    DoctorCheck("config-valid", "configured cgprofiled settings parse and validate", _check_config_valid),
    DoctorCheck("endpoint-record", "published endpoint fragment matches the installed socket settings",
                _check_endpoint_record),
    DoctorCheck("units-current", "installed service resources match the rendered config", _check_units_current),
    DoctorCheck("groups", "both dedicated groups exist with safe, distinct GIDs and listed members", _check_groups),
    DoctorCheck("socket-units", "both per-level socket units are enabled and active", _check_socket_units),
    DoctorCheck("socket-nodes", "both listening nodes are sockets with configured owner, group and mode", _check_socket_nodes),
    DoctorCheck("socket-dirs", "socket directories are root-owned and not writable by group or others", _check_socket_dirs),
    DoctorCheck("state-dir", "daemon state directory is root-owned mode 0700", _check_state_dir),
    DoctorCheck("service", "cgprofiled service is active at the installed version", _check_service),
    DoctorCheck("connect", "each accessible socket answers a health or contract probe", _check_connect),
    DoctorCheck("foreign-rules", "no old or foreign state conflicts with the service", _check_foreign_rules),
    DoctorCheck("caller-access", "report the caller's access-group memberships", _check_caller_access),
)


def register_service_doctor(registry: CliRegistry) -> None:
    register_doctor(registry, SERVICE_DOCTOR_CHECKS,
                    description="check cgprofiled installation, systemd sockets, permissions and connectivity")
