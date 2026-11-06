"""Exact behavioral coverage for the socket-activated deployment helpers."""

from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from topos.daemon.deploy import (
    DaemonPreflightReport,
    PreflightCheck,
    _group_label,
    _user_label,
    preflight_daemon_deployment,
    render_preflight_json,
)
from topos.service.config import ObserveSocketConfig, ServiceConfig, SocketsConfig


def _config(socket_path: Path, *, state_dir: Path, group: str = "ops") -> ServiceConfig:
    return ServiceConfig(
        state_dir=state_dir,
        sockets=SocketsConfig(
            observe=ObserveSocketConfig(path=socket_path, group=group, mode="0660")
        ),
    )


def test_render_install_plan_text_with_warning_only_step_is_complete() -> None:
    from topos.daemon.deploy import (
        InstallPlanStep,
        build_install_plan,
        render_install_plan_text,
    )

    warning = "This step has a warning and no command."
    plan = replace(
        build_install_plan(),
        steps=(InstallPlanStep(order=1, description="Review the warning", warning=warning),),
    )

    rendered = render_install_plan_text(plan)

    assert "Step 1: Review the warning" in rendered
    assert f"  note: {warning}" in rendered
    assert "  command:" not in rendered
    assert rendered.endswith("This is a PLAN only. No files were written and no system state was changed.")


def test_preflight_reports_unavailable_state_group_and_socket() -> None:
    socket_path = Path("/virtual/run/observe.sock")
    state_dir = Path("/virtual/run/state")
    unavailable = OSError("runtime unavailable")
    with (
        patch("topos.daemon.deploy._current_identity", return_value=("alice", 1000, 1000, ())),
        patch.object(Path, "stat", side_effect=unavailable) as path_stat,
        patch.object(Path, "lstat", side_effect=unavailable) as path_lstat,
        patch("topos.daemon.deploy.grp.getgrnam", side_effect=KeyError("ops")),
    ):
        result = preflight_daemon_deployment(
            socket_path,
            config=_config(socket_path, state_dir=state_dir),
            connect_timeout_s=0.25,
        )

    checks = {check.name: check for check in result.checks}
    assert result.state_dir == state_dir
    assert checks["state_dir"].message == "state directory /virtual/run/state is not available: runtime unavailable"
    assert checks["daemon_group"].message == "daemon group 'ops' does not exist"
    assert checks["socket"].message == "socket /virtual/run/observe.sock is not present: runtime unavailable"
    assert not result.usable
    assert path_stat.call_args_list == [call()]
    assert path_lstat.call_args_list == [call()]


def test_preflight_rejects_non_directory_state_and_non_socket_path() -> None:
    socket_path = Path("/virtual/run/observe.sock")
    state_dir = Path("/virtual/run/state")
    regular = SimpleNamespace(st_uid=101, st_gid=202, st_mode=stat.S_IFREG | 0o640)
    with (
        patch("topos.daemon.deploy._current_identity", return_value=("alice", 1000, 1000, ())),
        patch.object(Path, "stat", return_value=regular),
        patch.object(Path, "lstat", return_value=regular),
        patch("topos.daemon.deploy._user_label", side_effect=lambda uid: f"user-{uid}"),
        patch("topos.daemon.deploy._group_label", side_effect=lambda gid: f"group-{gid}"),
        patch("topos.daemon.deploy.grp.getgrnam", return_value=SimpleNamespace(gr_gid=303)),
    ):
        result = preflight_daemon_deployment(
            socket_path, config=_config(socket_path, state_dir=state_dir), group_name="ops"
        )

    checks = {check.name: check for check in result.checks}
    assert checks["state_dir"].message == "state path /virtual/run/state exists but is not a directory"
    assert checks["daemon_group"].ok is False
    assert checks["socket"].message == "/virtual/run/observe.sock exists but is not a Unix socket"
    assert result.socket_is_socket is False
    assert result.can_connect is None


def test_preflight_checks_private_state_and_configured_socket_access() -> None:
    socket_path = Path("/virtual/run/observe.sock")
    state_dir = Path("/virtual/run/state")
    private_state = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o700)
    observe_socket = SimpleNamespace(st_uid=0, st_gid=303, st_mode=stat.S_IFSOCK | 0o660)
    with (
        patch("topos.daemon.deploy._current_identity", return_value=("alice", 1000, 1000, (303,))),
        patch.object(Path, "stat", return_value=private_state),
        patch.object(Path, "lstat", return_value=observe_socket),
        patch("topos.daemon.deploy._user_label", side_effect=lambda uid: "root" if uid == 0 else f"user-{uid}"),
        patch("topos.daemon.deploy._group_label", side_effect=lambda gid: "ops" if gid == 303 else "root"),
        patch("topos.daemon.deploy.grp.getgrnam", return_value=SimpleNamespace(gr_gid=303)),
        patch("topos.daemon.deploy._can_connect", side_effect=OSError("connection refused")) as can_connect,
    ):
        result = preflight_daemon_deployment(
            socket_path,
            config=_config(socket_path, state_dir=state_dir),
            connect_timeout_s=0.25,
        )

    checks = {check.name: check for check in result.checks}
    assert checks["state_dir"].ok is True
    assert checks["daemon_group"].ok is True
    assert checks["socket"].ok is True
    assert checks["connect"].ok is False
    assert "connection refused" in checks["connect"].message
    assert result.usable is False
    can_connect.assert_called_once_with(socket_path, timeout_s=0.25)


def test_identity_labels_fall_back_to_numeric_values() -> None:
    with patch("topos.daemon.deploy.pwd.getpwuid", side_effect=KeyError(4242)) as get_user:
        assert _user_label(4242) == "4242"
    with patch("topos.daemon.deploy.grp.getgrgid", side_effect=KeyError(4343)) as get_group:
        assert _group_label(4343) == "4343"
    get_user.assert_called_once_with(4242)
    get_group.assert_called_once_with(4343)


def test_preflight_json_uses_state_dir_and_socket_shape() -> None:
    report = DaemonPreflightReport(
        socket_path=Path("/run/topos/observe/topos.sock"),
        group_name="ops",
        current_user="alice",
        current_uid=1000,
        current_gid=1001,
        supplemental_groups=(1001, 1002),
        state_dir=Path("/run/topos/state"),
        state_dir_owner="root",
        state_dir_group="root",
        state_dir_mode="0700",
        socket_present=False,
        socket_owner=None,
        socket_group=None,
        socket_mode=None,
        socket_is_socket=None,
        can_connect=None,
        checks=(PreflightCheck(name="socket", ok=False, message="missing", remedy="start socket unit"),),
        usable=False,
    )
    payload = json.loads(render_preflight_json(report))
    assert payload["state_dir"] == {
        "group": "root", "mode": "0700", "owner": "root", "path": "/run/topos/state"
    }
    assert payload["socket"]["path"] == "/run/topos/observe/topos.sock"
    assert "runtime_dir" not in payload
