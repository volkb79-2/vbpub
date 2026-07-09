from __future__ import annotations

import importlib.resources as resources
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
DEFAULT_SERVICE_DEST = Path("/etc/systemd/system/groop.service")
DEFAULT_TMPFILES_DEST = Path("/etc/tmpfiles.d/groop.conf")
SERVICE_ASSET = "assets/systemd/groop.service"
TMPFILES_ASSET = "assets/systemd/groop.tmpfiles"


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


# ── Install Plan (P25) ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallPlanStep:
    """A single step in the daemon deployment install plan.

    The step describes what an operator should do; it is never executed by
    the install-plan command itself.
    """

    order: int
    description: str
    command: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class DaemonInstallPlan:
    """Non-mutating install plan for the packaged daemon deployment templates."""

    socket_path: Path
    group_name: str
    service_dest: Path
    tmpfiles_dest: Path
    service_asset: str
    tmpfiles_asset: str
    service_content: str
    tmpfiles_content: str
    steps: tuple[InstallPlanStep, ...]
    warnings: tuple[str, ...]


def _read_asset(asset_path: str) -> str:
    """Read a packaged asset file using importlib.resources."""
    pkg = resources.files("groop")
    return (pkg / asset_path).read_text()


def build_install_plan(
    *,
    socket_path: Path | str = DEFAULT_DAEMON_SOCKET,
    group_name: str = DEFAULT_DAEMON_GROUP,
    service_dest: Path | str = DEFAULT_SERVICE_DEST,
    tmpfiles_dest: Path | str = DEFAULT_TMPFILES_DEST,
) -> DaemonInstallPlan:
    """Build a non-mutating install plan for the packaged daemon templates.

    The plan describes ordered operator steps, referencing the packaged
    systemd service and tmpfiles templates.  No host state is inspected or
    modified.
    """
    socket_path = Path(socket_path)
    service_dest = Path(service_dest)
    tmpfiles_dest = Path(tmpfiles_dest)

    service_content = _render_service_content(
        _read_asset(SERVICE_ASSET),
        socket_path=socket_path,
        group_name=group_name,
    )
    tmpfiles_content = _render_tmpfiles_content(
        _read_asset(TMPFILES_ASSET),
        runtime_dir=socket_path.parent,
        group_name=group_name,
    )

    steps = (
        InstallPlanStep(
            order=1,
            description=f"Create the system group {group_name!r} for daemon socket access",
            command=f"groupadd --system {group_name}",
            warning=(
                f"If the group {group_name!r} already exists, skip this step."
            ),
        ),
        InstallPlanStep(
            order=2,
            description=(
                "Add each approved non-root user to the daemon group so they "
                "can attach to the socket"
            ),
            command=f"usermod -aG {group_name} <username>",
            warning=(
                "Repeat for every user who should read daemon telemetry. "
                "Users must log out and back in for the group change to take effect."
            ),
        ),
        InstallPlanStep(
            order=3,
            description=(
                f"Install the tmpfiles configuration to {tmpfiles_dest} "
                f"(ensures {socket_path.parent} is created with 0750 root:{group_name})"
            ),
            command=_install_heredoc_command(tmpfiles_content, tmpfiles_dest),
            warning="Review the rendered tmpfiles content before copying.",
        ),
        InstallPlanStep(
            order=4,
            description=(
                f"Install the systemd service unit to {service_dest}"
            ),
            command=_install_heredoc_command(service_content, service_dest),
            warning="Review the rendered service unit before enabling; verify ExecStart and socket path.",
        ),
        InstallPlanStep(
            order=5,
            description="Reload systemd so it picks up the new service unit",
            command="systemctl daemon-reload",
        ),
        InstallPlanStep(
            order=6,
            description="Enable and start the groop daemon service",
            command="systemctl enable --now groop.service",
            warning=(
                "The daemon runs as root and binds the group-readable socket. "
                "Verify with: groop daemon preflight"
            ),
        ),
        InstallPlanStep(
            order=7,
            description=(
                "Verify the deployment with the read-only preflight command "
                "from a non-root client account"
            ),
            command=f"groop daemon preflight --socket {socket_path}",
            warning=(
                "Run this from a non-root user in the daemon group to confirm "
                "the socket is reachable."
            ),
        ),
    )

    warnings = (
        "This is a PLAN, not an installer. No host state has been modified.",
        f"The daemon socket at {socket_path} will be group-readable by {group_name!r}.",
        "Template files are rendered from the packaged groop assets; verify paths before copying.",
    )

    return DaemonInstallPlan(
        socket_path=socket_path,
        group_name=group_name,
        service_dest=service_dest,
        tmpfiles_dest=tmpfiles_dest,
        service_asset=SERVICE_ASSET,
        tmpfiles_asset=TMPFILES_ASSET,
        service_content=service_content,
        tmpfiles_content=tmpfiles_content,
        steps=steps,
        warnings=warnings,
    )


def install_plan_to_jsonable(plan: DaemonInstallPlan) -> dict[str, object]:
    """Convert an install plan to a JSON-serializable dict (deterministic)."""
    return {
        "group": plan.group_name,
        "plan": "install",
        "service_asset": plan.service_asset,
        "service_content": plan.service_content,
        "service_dest": str(plan.service_dest),
        "socket_path": str(plan.socket_path),
        "steps": [
            {
                "command": step.command,
                "description": step.description,
                "order": step.order,
                "warning": step.warning,
            }
            for step in plan.steps
        ],
        "tmpfiles_asset": plan.tmpfiles_asset,
        "tmpfiles_content": plan.tmpfiles_content,
        "tmpfiles_dest": str(plan.tmpfiles_dest),
        "warnings": list(plan.warnings),
    }


def _render_service_content(template: str, *, socket_path: Path, group_name: str) -> str:
    rendered = template.replace("Group=groop", f"Group={group_name}")
    rendered = rendered.replace("--socket /run/groop/groop.sock", f"--socket {socket_path}")
    rendered = rendered.replace("the groop group", f"the {group_name} group")
    return rendered


def _render_tmpfiles_content(template: str, *, runtime_dir: Path, group_name: str) -> str:
    rendered = template.replace("the groop group", f"the {group_name} group")
    rendered = rendered.replace("d /run/groop 0750 root groop -", f"d {runtime_dir} 0750 root {group_name} -")
    return rendered


def _install_heredoc_command(content: str, dest: Path) -> str:
    body = content.rstrip("\n")
    return f"install -m 0644 -o root -g root /dev/stdin {dest} <<'EOF'\n{body}\nEOF"


def render_install_plan_text(plan: DaemonInstallPlan) -> str:
    """Render a human-readable install plan (copy/pasteable steps)."""
    lines = [
        "groop daemon install plan",
        "=" * 60,
        f"socket path : {plan.socket_path}",
        f"daemon group : {plan.group_name}",
        f"service unit : {plan.service_dest}",
        f"tmpfiles conf: {plan.tmpfiles_dest}",
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
