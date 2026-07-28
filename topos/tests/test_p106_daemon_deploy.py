"""P106 exact behavioral coverage for daemon deployment helpers."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from topos.daemon.deploy import (
    DaemonInstallPlan,
    DaemonPreflightReport,
    InstallPlanStep,
    PreflightCheck,
    _group_label,
    _user_label,
    preflight_daemon_deployment,
    render_install_plan_text,
    render_preflight_json,
)


IDENTITY = ("alice", 1000, 1000, ())


def test_preflight_reports_unavailable_runtime_group_and_socket() -> None:
    socket_path = Path("/virtual/run/topos.sock")
    unavailable = OSError("runtime unavailable")
    with (
        patch(
            "topos.daemon.deploy._current_identity",
            return_value=IDENTITY,
        ),
        patch.object(Path, "stat", side_effect=unavailable) as path_stat,
        patch("topos.daemon.deploy.grp.getgrnam", side_effect=KeyError("ops")),
    ):
        result = preflight_daemon_deployment(
            socket_path,
            group_name="ops",
            connect_timeout_s=0.25,
        )

    assert result == DaemonPreflightReport(
        socket_path=socket_path,
        group_name="ops",
        current_user="alice",
        current_uid=1000,
        current_gid=1000,
        supplemental_groups=(),
        runtime_dir=Path("/virtual/run"),
        runtime_dir_owner=None,
        runtime_dir_group=None,
        runtime_dir_mode=None,
        socket_present=False,
        socket_owner=None,
        socket_group=None,
        socket_mode=None,
        socket_is_socket=None,
        can_connect=None,
        checks=(
            PreflightCheck(
                name="runtime_dir",
                ok=False,
                message=(
                    "runtime directory /virtual/run is not available: "
                    "runtime unavailable"
                ),
                remedy=(
                    "create it with the packaged tmpfiles rule before "
                    "starting the daemon"
                ),
                details={
                    "path": "/virtual/run",
                    "error": "runtime unavailable",
                },
            ),
            PreflightCheck(
                name="daemon_group",
                ok=False,
                message="daemon group 'ops' does not exist",
                remedy=(
                    "create the topos group and add approved users before "
                    "enabling the daemon"
                ),
                details={"group": "ops"},
            ),
            PreflightCheck(
                name="socket",
                ok=False,
                message=(
                    "socket /virtual/run/topos.sock is not present: "
                    "runtime unavailable"
                ),
                remedy=(
                    "start `topos daemon serve --socket "
                    "/virtual/run/topos.sock` or point preflight at the "
                    "deployed socket"
                ),
                details={
                    "path": "/virtual/run/topos.sock",
                    "error": "runtime unavailable",
                },
            ),
        ),
        usable=False,
    )
    assert path_stat.call_args_list == [call(), call()]


def test_preflight_reports_regular_runtime_and_socket_paths() -> None:
    socket_path = Path("/virtual/run/topos.sock")
    regular = SimpleNamespace(
        st_uid=101,
        st_gid=202,
        st_mode=stat.S_IFREG | 0o640,
    )
    with (
        patch(
            "topos.daemon.deploy._current_identity",
            return_value=IDENTITY,
        ),
        patch.object(Path, "stat", side_effect=[regular, regular]) as path_stat,
        patch(
            "topos.daemon.deploy._user_label",
            side_effect=lambda uid: f"user-{uid}",
        ),
        patch(
            "topos.daemon.deploy._group_label",
            side_effect=lambda gid: f"group-{gid}",
        ),
        patch(
            "topos.daemon.deploy.grp.getgrnam",
            return_value=SimpleNamespace(gr_gid=303),
        ),
    ):
        result = preflight_daemon_deployment(
            socket_path,
            group_name="ops",
        )

    assert result == DaemonPreflightReport(
        socket_path=socket_path,
        group_name="ops",
        current_user="alice",
        current_uid=1000,
        current_gid=1000,
        supplemental_groups=(),
        runtime_dir=Path("/virtual/run"),
        runtime_dir_owner="user-101",
        runtime_dir_group="group-202",
        runtime_dir_mode="0640",
        socket_present=True,
        socket_owner="user-101",
        socket_group="group-202",
        socket_mode="0640",
        socket_is_socket=False,
        can_connect=None,
        checks=(
            PreflightCheck(
                name="runtime_dir",
                ok=False,
                message=(
                    "runtime path /virtual/run exists but is not a directory"
                ),
                remedy=(
                    "remove the path and create /run/topos as a directory"
                ),
                details={
                    "path": "/virtual/run",
                    "mode": "0640",
                    "owner": "user-101",
                    "group": "group-202",
                },
            ),
            PreflightCheck(
                name="daemon_group",
                ok=False,
                message=(
                    "current user alice is not a member of daemon group ops"
                ),
                remedy="add alice to the ops group",
                details={
                    "group": "ops",
                    "gid": 303,
                    "member": False,
                    "supplemental_groups": [],
                },
            ),
            PreflightCheck(
                name="socket",
                ok=False,
                message=(
                    "/virtual/run/topos.sock exists but is not a Unix socket"
                ),
                remedy="remove the path and let the daemon create its socket",
                details={
                    "path": "/virtual/run/topos.sock",
                    "mode": "0640",
                    "owner": "user-101",
                    "group": "group-202",
                },
            ),
        ),
        usable=False,
    )
    assert path_stat.call_args_list == [call(), call()]


def test_preflight_reports_world_writable_runtime_and_connect_error() -> None:
    socket_path = Path("/virtual/run/topos.sock")
    runtime = SimpleNamespace(
        st_uid=101,
        st_gid=303,
        st_mode=stat.S_IFDIR | 0o777,
    )
    socket = SimpleNamespace(
        st_uid=101,
        st_gid=303,
        st_mode=stat.S_IFSOCK | 0o660,
    )
    connect_error = OSError("connection refused")
    with (
        patch(
            "topos.daemon.deploy._current_identity",
            return_value=("alice", 1000, 303, (303,)),
        ),
        patch.object(
            Path,
            "stat",
            side_effect=[runtime, socket],
        ) as path_stat,
        patch(
            "topos.daemon.deploy._user_label",
            side_effect=lambda uid: f"user-{uid}",
        ),
        patch(
            "topos.daemon.deploy._group_label",
            side_effect=lambda gid: f"group-{gid}",
        ),
        patch(
            "topos.daemon.deploy.grp.getgrnam",
            return_value=SimpleNamespace(gr_gid=303),
        ),
        patch(
            "topos.daemon.deploy._can_connect",
            side_effect=connect_error,
        ) as can_connect,
    ):
        result = preflight_daemon_deployment(
            socket_path,
            group_name="ops",
            connect_timeout_s=0.25,
        )

    assert result == DaemonPreflightReport(
        socket_path=socket_path,
        group_name="ops",
        current_user="alice",
        current_uid=1000,
        current_gid=303,
        supplemental_groups=(303,),
        runtime_dir=Path("/virtual/run"),
        runtime_dir_owner="user-101",
        runtime_dir_group="group-303",
        runtime_dir_mode="0777",
        socket_present=True,
        socket_owner="user-101",
        socket_group="group-303",
        socket_mode="0660",
        socket_is_socket=True,
        can_connect=False,
        checks=(
            PreflightCheck(
                name="runtime_dir",
                ok=False,
                message=(
                    "runtime directory /virtual/run is world-writable (0777)"
                ),
                remedy=(
                    "tighten /run/topos to 0750 root:topos via tmpfiles or "
                    "systemd runtime directory settings"
                ),
                details={
                    "path": "/virtual/run",
                    "mode": "0777",
                    "owner": "user-101",
                    "group": "group-303",
                    "world_writable": True,
                },
            ),
            PreflightCheck(
                name="daemon_group",
                ok=True,
                message="current user alice can use daemon group ops",
                remedy=None,
                details={
                    "group": "ops",
                    "gid": 303,
                    "member": True,
                    "supplemental_groups": [303],
                },
            ),
            PreflightCheck(
                name="socket",
                ok=True,
                message=(
                    "socket /virtual/run/topos.sock is present as 0660 "
                    "user-101:group-303"
                ),
                details={
                    "path": "/virtual/run/topos.sock",
                    "mode": "0660",
                    "owner": "user-101",
                    "group": "group-303",
                },
            ),
            PreflightCheck(
                name="connect",
                ok=False,
                message=(
                    "current process cannot connect to "
                    "/virtual/run/topos.sock: connection refused"
                ),
                remedy=(
                    "make sure the daemon is running and the current user "
                    "has socket access"
                ),
                details={
                    "path": "/virtual/run/topos.sock",
                    "error": "connection refused",
                },
            ),
        ),
        usable=False,
    )
    assert path_stat.call_args_list == [call(), call()]
    can_connect.assert_called_once_with(socket_path, timeout_s=0.25)


def test_identity_labels_fall_back_to_numeric_values() -> None:
    with patch(
        "topos.daemon.deploy.pwd.getpwuid",
        side_effect=KeyError(4242),
    ) as get_user:
        assert _user_label(4242) == "4242"
    with patch(
        "topos.daemon.deploy.grp.getgrgid",
        side_effect=KeyError(4343),
    ) as get_group:
        assert _group_label(4343) == "4343"

    get_user.assert_called_once_with(4242)
    get_group.assert_called_once_with(4343)


def test_render_preflight_json_is_canonical_and_complete() -> None:
    report = DaemonPreflightReport(
        socket_path=Path("/run/topos.sock"),
        group_name="ops",
        current_user="alice",
        current_uid=1000,
        current_gid=1001,
        supplemental_groups=(1001, 1002),
        runtime_dir=Path("/run"),
        runtime_dir_owner="root",
        runtime_dir_group="ops",
        runtime_dir_mode="0750",
        socket_present=False,
        socket_owner=None,
        socket_group=None,
        socket_mode=None,
        socket_is_socket=None,
        can_connect=None,
        checks=(
            PreflightCheck(
                name="socket",
                ok=False,
                message="missing",
                remedy="start daemon",
                details={"path": "/run/topos.sock"},
            ),
        ),
        usable=False,
    )
    expected = {
        "checks": [
            {
                "details": {"path": "/run/topos.sock"},
                "message": "missing",
                "name": "socket",
                "ok": False,
                "remedy": "start daemon",
            }
        ],
        "current": {
            "gid": 1001,
            "groups": [1001, 1002],
            "uid": 1000,
            "user": "alice",
        },
        "group": {"name": "ops"},
        "ok": False,
        "runtime_dir": {
            "group": "ops",
            "mode": "0750",
            "owner": "root",
            "path": "/run",
        },
        "socket": {
            "can_connect": None,
            "group": None,
            "is_socket": None,
            "mode": None,
            "owner": None,
            "path": "/run/topos.sock",
            "present": False,
        },
    }

    assert render_preflight_json(report) == json.dumps(
        expected,
        sort_keys=True,
    )


def test_render_install_plan_text_with_warning_only_step_is_complete() -> None:
    plan = DaemonInstallPlan(
        socket_path=Path("/run/topos.sock"),
        group_name="ops",
        service_dest=Path("/etc/topos.service"),
        slice_dest=Path("/etc/topos.slice"),
        tmpfiles_dest=Path("/etc/topos.conf"),
        service_asset="service.asset",
        slice_asset="slice.asset",
        tmpfiles_asset="tmpfiles.asset",
        service_content="service",
        slice_content="slice",
        tmpfiles_content="tmpfiles",
        steps=(
            InstallPlanStep(
                order=1,
                description="Review configuration",
                command=None,
                warning="No command is required",
            ),
        ),
        warnings=("Plan warning",),
    )

    assert render_install_plan_text(plan) == "\n".join(
        (
            "topos daemon install plan",
            "=" * 60,
            "socket path : /run/topos.sock",
            "daemon group : ops",
            "service unit : /etc/topos.service",
            "resource slice: /etc/topos.slice",
            "tmpfiles conf: /etc/topos.conf",
            "",
            "--- plan steps (read-only; no host mutation) ---",
            "",
            "Step 1: Review configuration",
            "  note: No command is required",
            "",
            "--- warnings ---",
            "  ! Plan warning",
            "",
            (
                "This is a PLAN only. No files were written and no system "
                "state was changed."
            ),
        )
    )
