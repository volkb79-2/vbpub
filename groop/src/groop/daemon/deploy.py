from __future__ import annotations

import grp
import json
import os
import pwd
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DAEMON_SOCKET = Path("/run/groop/groop.sock")
DEFAULT_DAEMON_GROUP = "groop"


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    message: str
    remedy: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DaemonPreflightReport:
    socket_path: Path
    group_name: str
    current_user: str
    current_uid: int
    current_gid: int
    supplemental_groups: tuple[int, ...]
    runtime_dir: Path
    runtime_dir_owner: str | None
    runtime_dir_group: str | None
    runtime_dir_mode: str | None
    socket_present: bool
    socket_owner: str | None
    socket_group: str | None
    socket_mode: str | None
    socket_is_socket: bool | None
    can_connect: bool | None
    checks: tuple[PreflightCheck, ...]
    usable: bool


def preflight_daemon_deployment(
    socket_path: Path | str = DEFAULT_DAEMON_SOCKET,
    *,
    group_name: str = DEFAULT_DAEMON_GROUP,
    connect_timeout_s: float = 1.0,
) -> DaemonPreflightReport:
    socket_path = Path(socket_path)
    runtime_dir = socket_path.parent
    current_user, current_uid, current_gid, supplemental_groups = _current_identity()

    checks: list[PreflightCheck] = []

    runtime_dir_owner = runtime_dir_group = runtime_dir_mode = None
    try:
        runtime_stat = runtime_dir.stat()
    except OSError as exc:
        checks.append(
            PreflightCheck(
                name="runtime_dir",
                ok=False,
                message=f"runtime directory {runtime_dir} is not available: {exc.strerror or exc}",
                remedy="create it with the packaged tmpfiles rule before starting the daemon",
                details={"path": str(runtime_dir), "error": str(exc)},
            )
        )
    else:
        runtime_dir_owner = _user_label(runtime_stat.st_uid)
        runtime_dir_group = _group_label(runtime_stat.st_gid)
        runtime_dir_mode = _mode_label(runtime_stat.st_mode)
        if not stat.S_ISDIR(runtime_stat.st_mode):
            checks.append(
                PreflightCheck(
                    name="runtime_dir",
                    ok=False,
                    message=f"runtime path {runtime_dir} exists but is not a directory",
                    remedy="remove the path and create /run/groop as a directory",
                    details={
                        "path": str(runtime_dir),
                        "mode": runtime_dir_mode,
                        "owner": runtime_dir_owner,
                        "group": runtime_dir_group,
                    },
                )
            )
        else:
            unsafe = bool(runtime_stat.st_mode & stat.S_IWOTH)
            ok = not unsafe
            message = f"runtime directory {runtime_dir} is {runtime_dir_mode} {runtime_dir_owner}:{runtime_dir_group}"
            remedy = None
            if unsafe:
                message = f"runtime directory {runtime_dir} is world-writable ({runtime_dir_mode})"
                remedy = "tighten /run/groop to 0750 root:groop via tmpfiles or systemd runtime directory settings"
            checks.append(
                PreflightCheck(
                    name="runtime_dir",
                    ok=ok,
                    message=message,
                    remedy=remedy,
                    details={
                        "path": str(runtime_dir),
                        "mode": runtime_dir_mode,
                        "owner": runtime_dir_owner,
                        "group": runtime_dir_group,
                        "world_writable": unsafe,
                    },
                )
            )

    try:
        expected_group = grp.getgrnam(group_name)
    except KeyError:
        checks.append(
            PreflightCheck(
                name="daemon_group",
                ok=False,
                message=f"daemon group {group_name!r} does not exist",
                remedy="create the groop group and add approved users before enabling the daemon",
                details={"group": group_name},
            )
        )
    else:
        in_group = current_uid == 0 or expected_group.gr_gid in supplemental_groups or expected_group.gr_gid == current_gid
        if in_group:
            message = (
                f"current user {current_user} can use daemon group {group_name}"
                if current_uid != 0
                else f"current user {current_user} is root; daemon group {group_name} is available"
            )
        else:
            message = f"current user {current_user} is not a member of daemon group {group_name}"
        checks.append(
            PreflightCheck(
                name="daemon_group",
                ok=in_group,
                message=message,
                remedy=None if in_group else f"add {current_user} to the {group_name} group",
                details={
                    "group": group_name,
                    "gid": expected_group.gr_gid,
                    "member": in_group,
                    "supplemental_groups": list(supplemental_groups),
                },
            )
        )

    socket_present = False
    socket_owner = socket_group = socket_mode = None
    socket_is_socket: bool | None = None
    can_connect: bool | None = None
    try:
        socket_stat = socket_path.stat()
    except OSError as exc:
        checks.append(
            PreflightCheck(
                name="socket",
                ok=False,
                message=f"socket {socket_path} is not present: {exc.strerror or exc}",
                remedy=f"start `groop daemon serve --socket {socket_path}` or point preflight at the deployed socket",
                details={"path": str(socket_path), "error": str(exc)},
            )
        )
    else:
        socket_present = True
        socket_owner = _user_label(socket_stat.st_uid)
        socket_group = _group_label(socket_stat.st_gid)
        socket_mode = _mode_label(socket_stat.st_mode)
        socket_is_socket = stat.S_ISSOCK(socket_stat.st_mode)
        if not socket_is_socket:
            checks.append(
                PreflightCheck(
                    name="socket",
                    ok=False,
                    message=f"{socket_path} exists but is not a Unix socket",
                    remedy="remove the path and let the daemon create its socket",
                    details={
                        "path": str(socket_path),
                        "mode": socket_mode,
                        "owner": socket_owner,
                        "group": socket_group,
                    },
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="socket",
                    ok=True,
                    message=f"socket {socket_path} is present as {socket_mode} {socket_owner}:{socket_group}",
                    details={
                        "path": str(socket_path),
                        "mode": socket_mode,
                        "owner": socket_owner,
                        "group": socket_group,
                    },
                )
            )
            try:
                can_connect = _can_connect(socket_path, timeout_s=connect_timeout_s)
            except OSError as exc:
                can_connect = False
                checks.append(
                    PreflightCheck(
                        name="connect",
                        ok=False,
                        message=f"current process cannot connect to {socket_path}: {exc.strerror or exc}",
                        remedy="make sure the daemon is running and the current user has socket access",
                        details={"path": str(socket_path), "error": str(exc)},
                    )
                )
            else:
                checks.append(
                    PreflightCheck(
                        name="connect",
                        ok=can_connect,
                        message=f"current process can connect to {socket_path}",
                        details={"path": str(socket_path), "can_connect": can_connect},
                    )
                )

    usable = all(check.ok for check in checks)
    return DaemonPreflightReport(
        socket_path=socket_path,
        group_name=group_name,
        current_user=current_user,
        current_uid=current_uid,
        current_gid=current_gid,
        supplemental_groups=supplemental_groups,
        runtime_dir=runtime_dir,
        runtime_dir_owner=runtime_dir_owner,
        runtime_dir_group=runtime_dir_group,
        runtime_dir_mode=runtime_dir_mode,
        socket_present=socket_present,
        socket_owner=socket_owner,
        socket_group=socket_group,
        socket_mode=socket_mode,
        socket_is_socket=socket_is_socket,
        can_connect=can_connect,
        checks=tuple(checks),
        usable=usable,
    )


def preflight_report_to_jsonable(report: DaemonPreflightReport) -> dict[str, object]:
    return {
        "current": {
            "gid": report.current_gid,
            "groups": list(report.supplemental_groups),
            "uid": report.current_uid,
            "user": report.current_user,
        },
        "group": {"name": report.group_name},
        "ok": report.usable,
        "runtime_dir": {
            "group": report.runtime_dir_group,
            "mode": report.runtime_dir_mode,
            "owner": report.runtime_dir_owner,
            "path": str(report.runtime_dir),
        },
        "socket": {
            "can_connect": report.can_connect,
            "group": report.socket_group,
            "is_socket": report.socket_is_socket,
            "mode": report.socket_mode,
            "owner": report.socket_owner,
            "path": str(report.socket_path),
            "present": report.socket_present,
        },
        "checks": [
            {
                "details": check.details,
                "message": check.message,
                "name": check.name,
                "ok": check.ok,
                "remedy": check.remedy,
            }
            for check in report.checks
        ],
    }


def render_preflight_text(report: DaemonPreflightReport) -> str:
    lines = [
        f"groop daemon preflight: {report.socket_path}",
        f"user {report.current_user} uid={report.current_uid} gid={report.current_gid} groups={_group_list_label(report.supplemental_groups)}",
        f"runtime dir {report.runtime_dir} {report.runtime_dir_mode or 'unavailable'} {report.runtime_dir_owner or '-'}:{report.runtime_dir_group or '-'}",
    ]
    for check in report.checks:
        prefix = "OK" if check.ok else "FAIL"
        lines.append(f"{prefix} {check.message}")
        if check.remedy:
            lines.append(f"    fix: {check.remedy}")
    lines.append("usable: yes" if report.usable else "usable: no")
    return "\n".join(lines)


def _current_identity() -> tuple[str, int, int, tuple[int, ...]]:
    uid = os.geteuid()
    user = pwd.getpwuid(uid).pw_name
    gids = tuple(sorted(set(os.getgroups()) | {os.getgid(), os.getegid()}))
    return user, uid, os.getegid(), gids


def _can_connect(socket_path: Path, *, timeout_s: float) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect(str(socket_path))
    return True


def _mode_label(mode: int) -> str:
    return f"{stat.S_IMODE(mode):04o}"


def _user_label(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_label(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _group_list_label(gids: tuple[int, ...]) -> str:
    return ", ".join(_group_label(gid) for gid in gids) or "-"


def render_preflight_json(report: DaemonPreflightReport) -> str:
    return json.dumps(preflight_report_to_jsonable(report), sort_keys=True)
