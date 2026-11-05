from __future__ import annotations

import grp
import json
import os
import pwd
import socket
import stat
from dataclasses import dataclass, field
from pathlib import Path

from topos.service.config import DEFAULTS, ServiceConfig, load_config, parse_config
from topos.service.units import render_units


DEFAULT_DAEMON_SOCKET = Path("/run/topos/observe/topos.sock")
DEFAULT_DAEMON_GROUP = DEFAULTS.sockets.observe.group
DEFAULT_SERVICE_DEST = Path("/etc/systemd/system/topos.service")
DEFAULT_SOCKET_DEST = Path("/etc/systemd/system/topos-observe.socket")
DEFAULT_SLICE_DEST = Path("/etc/systemd/system/topos.slice")
DEFAULT_SYSUSERS_DEST = Path("/etc/sysusers.d/topos.conf")


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
    state_dir: Path
    state_dir_owner: str | None
    state_dir_group: str | None
    state_dir_mode: str | None
    socket_present: bool
    socket_owner: str | None
    socket_group: str | None
    socket_mode: str | None
    socket_is_socket: bool | None
    can_connect: bool | None
    checks: tuple[PreflightCheck, ...]
    usable: bool


def preflight_daemon_deployment(
    socket_path: Path | str | None = None,
    *,
    group_name: str | None = None,
    config_path: Path | str | None = None,
    config: ServiceConfig | None = None,
    connect_timeout_s: float = 1.0,
) -> DaemonPreflightReport:
    cfg = config if config is not None else load_config(config_path)
    socket_config = cfg.sockets.observe
    socket_path = Path(socket_path) if socket_path is not None else socket_config.path
    group_name = group_name if group_name is not None else socket_config.group
    expected_mode = socket_config.mode
    state_dir = cfg.state_dir
    current_user, current_uid, current_gid, supplemental_groups = _current_identity()

    checks: list[PreflightCheck] = []

    state_dir_owner = state_dir_group = state_dir_mode = None
    try:
        state_stat = state_dir.stat()
    except OSError as exc:
        checks.append(
            PreflightCheck(
                name="state_dir",
                ok=False,
                message=f"state directory {state_dir} is not available: {exc.strerror or exc}",
                remedy="start the topos service so systemd creates the configured state directory",
                details={"path": str(state_dir), "error": str(exc)},
            )
        )
    else:
        state_dir_owner = _user_label(state_stat.st_uid)
        state_dir_group = _group_label(state_stat.st_gid)
        state_dir_mode = _mode_label(state_stat.st_mode)
        if not stat.S_ISDIR(state_stat.st_mode):
            checks.append(
                PreflightCheck(
                    name="state_dir",
                    ok=False,
                    message=f"state path {state_dir} exists but is not a directory",
                    remedy="remove the conflicting path and let the topos service create its state directory",
                    details={
                        "path": str(state_dir),
                        "mode": state_dir_mode,
                        "owner": state_dir_owner,
                        "group": state_dir_group,
                    },
                )
            )
        else:
            ok = state_stat.st_uid == 0 and state_stat.st_gid == 0 and state_dir_mode == "0700"
            message = (
                f"state directory {state_dir} is 0700 root:root"
                if ok
                else f"state directory {state_dir} must be 0700 root:root (is {state_dir_mode} {state_dir_owner}:{state_dir_group})"
            )
            checks.append(
                PreflightCheck(
                    name="state_dir",
                    ok=ok,
                    message=message,
                    remedy=None if ok else f"set {state_dir} to 0700 root:root",
                    details={
                        "path": str(state_dir),
                        "mode": state_dir_mode,
                        "owner": state_dir_owner,
                        "group": state_dir_group,
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
                remedy="install the configured group with systemd-sysusers or map observe to an existing group",
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
        socket_stat = socket_path.lstat()
    except OSError as exc:
        checks.append(
            PreflightCheck(
                name="socket",
                ok=False,
                message=f"socket {socket_path} is not present: {exc.strerror or exc}",
                remedy="start topos-observe.socket and topos.service, or point preflight at the deployed socket",
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
                    remedy="remove the conflicting path and let topos-observe.socket create the socket",
                    details={
                        "path": str(socket_path),
                        "mode": socket_mode,
                        "owner": socket_owner,
                        "group": socket_group,
                    },
                )
            )
        else:
            socket_owner_ok = socket_stat.st_uid == 0
            socket_group_ok = socket_group == group_name
            socket_mode_ok = socket_mode == expected_mode
            socket_ok = socket_owner_ok and socket_group_ok and socket_mode_ok
            if socket_ok:
                message = f"socket {socket_path} is {socket_mode} root:{socket_group}"
                remedy = None
            else:
                message = (
                    f"socket {socket_path} must be {expected_mode} root:{group_name} "
                    f"(is {socket_mode} {socket_owner}:{socket_group})"
                )
                remedy = "check sockets.observe.group and mode in toposd.toml, then restart topos-observe.socket"
            checks.append(
                PreflightCheck(
                    name="socket",
                    ok=socket_ok,
                    message=message,
                    remedy=remedy,
                    details={
                        "path": str(socket_path),
                        "mode": socket_mode,
                        "owner": socket_owner,
                        "group": socket_group,
                        "expected_mode": expected_mode,
                        "expected_group": group_name,
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
        state_dir=state_dir,
        state_dir_owner=state_dir_owner,
        state_dir_group=state_dir_group,
        state_dir_mode=state_dir_mode,
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
        "state_dir": {
            "group": report.state_dir_group,
            "mode": report.state_dir_mode,
            "owner": report.state_dir_owner,
            "path": str(report.state_dir),
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
        f"topos daemon preflight: {report.socket_path}",
        f"user {report.current_user} uid={report.current_uid} gid={report.current_gid} groups={_group_list_label(report.supplemental_groups)}",
        f"state dir {report.state_dir} {report.state_dir_mode or 'unavailable'} {report.state_dir_owner or '-'}:{report.state_dir_group or '-'}",
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


# ── Install Plan ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallPlanStep:
    """A single read-only operator step in the deployment plan."""

    order: int
    description: str
    command: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class DaemonInstallPlan:
    """Non-mutating plan rendered from the daemon's effective config."""

    socket_path: Path
    group_name: str
    socket_mode: str
    state_dir: Path
    destinations: dict[str, Path]
    units: dict[str, str]
    steps: tuple[InstallPlanStep, ...]
    warnings: tuple[str, ...]


def build_install_plan(
    *,
    config_path: Path | str | None = None,
    config: ServiceConfig | None = None,
    socket_path: Path | str | None = None,
    group_name: str | None = None,
    service_dest: Path | str = DEFAULT_SERVICE_DEST,
) -> DaemonInstallPlan:
    """Build a read-only systemd installation plan from toposd.toml."""
    cfg = config if config is not None else load_config(config_path)
    if socket_path is not None or group_name is not None:
        socket = cfg.sockets.observe
        path = Path(socket_path) if socket_path is not None else socket.path
        group = group_name if group_name is not None else socket.group
        source = "\n".join(
            [
                f"state_dir = {json.dumps(str(cfg.state_dir))}",
                "",
                "[sockets.observe]",
                f"path = {json.dumps(str(path))}",
                f"group = {json.dumps(group)}",
                f"mode = {json.dumps(socket.mode)}",
            ]
        )
        cfg = parse_config(source)
    units = render_units(cfg)
    destinations = {
        "topos-observe.socket": DEFAULT_SOCKET_DEST,
        "topos.service": Path(service_dest),
        "topos.slice": DEFAULT_SLICE_DEST,
        "topos.sysusers": DEFAULT_SYSUSERS_DEST,
    }
    steps = (
        InstallPlanStep(
            1,
            f"Install the rendered sysusers file to {destinations['topos.sysusers']}",
            _install_heredoc_command(units["topos.sysusers"], destinations["topos.sysusers"]),
        ),
        InstallPlanStep(
            2,
            "Create any declared system groups with systemd-sysusers",
            "systemd-sysusers",
            "Mapped existing groups are not created.",
        ),
        InstallPlanStep(
            3,
            f"Install the observe socket unit to {destinations['topos-observe.socket']}",
            _install_heredoc_command(units["topos-observe.socket"], destinations["topos-observe.socket"]),
        ),
        InstallPlanStep(
            4,
            f"Install the daemon service unit to {destinations['topos.service']}",
            _install_heredoc_command(units["topos.service"], destinations["topos.service"]),
        ),
        InstallPlanStep(
            5,
            f"Install the resource slice to {destinations['topos.slice']}",
            _install_heredoc_command(units["topos.slice"], destinations["topos.slice"]),
        ),
        InstallPlanStep(6, "Reload systemd's unit definitions", "systemctl daemon-reload"),
        InstallPlanStep(
            7,
            "Enable and start socket activation and the continuously running daemon",
            "systemctl enable --now topos-observe.socket topos.service",
        ),
        InstallPlanStep(
            8,
            "Check the deployed socket from an authorized client account",
            "topos daemon preflight",
        ),
    )
    return DaemonInstallPlan(
        socket_path=cfg.sockets.observe.path,
        group_name=cfg.sockets.observe.group,
        socket_mode=cfg.sockets.observe.mode,
        state_dir=cfg.state_dir,
        destinations=destinations,
        units=units,
        steps=steps,
        warnings=(
            "This is a PLAN, not an installer. No host state has been modified.",
            "Install /etc/topos/toposd.toml with the same values used to render these units.",
            f"The observe socket is {cfg.sockets.observe.mode} root:{cfg.sockets.observe.group} at {cfg.sockets.observe.path}.",
        ),
    )



def install_plan_to_jsonable(plan: DaemonInstallPlan) -> dict[str, object]:
    """Convert an install plan to a JSON-serializable dict (deterministic)."""
    return {
        "group": plan.group_name,
        "mode": plan.socket_mode,
        "plan": "install",
        "state_dir": str(plan.state_dir),
        "socket_path": str(plan.socket_path),
        "destinations": {name: str(path) for name, path in plan.destinations.items()},
        "units": plan.units,
        "steps": [
            {
                "command": step.command,
                "description": step.description,
                "order": step.order,
                "warning": step.warning,
            }
            for step in plan.steps
        ],
        "warnings": list(plan.warnings),
    }


def _install_heredoc_command(content: str, dest: Path) -> str:
    body = content.rstrip("\n")
    return f"install -m 0644 -o root -g root /dev/stdin {dest} <<'EOF'\n{body}\nEOF"


def render_install_plan_text(plan: DaemonInstallPlan) -> str:
    """Render a human-readable install plan (copy/pasteable steps)."""
    lines = [
        "topos daemon install plan",
        "=" * 60,
        f"socket path : {plan.socket_path}",
        f"socket access: {plan.socket_mode} root:{plan.group_name}",
        f"state dir    : {plan.state_dir}",
        *[f"{name:20} {path}" for name, path in plan.destinations.items()],
        "",
        "--- plan steps (read-only; no host mutation) ---",
        "",
    ]
    for step in plan.steps:
        lines.append(f"Step {step.order}: {step.description}")
        if step.command:
            lines.append(f"  command: {step.command}")
        if step.warning:
            lines.append(f"  note: {step.warning}")
        lines.append("")
    lines.append("--- warnings ---")
    for w in plan.warnings:
        lines.append(f"  ! {w}")
    lines.append("")
    lines.append("This is a PLAN only. No files were written and no system state was changed.")
    return "\n".join(lines)
