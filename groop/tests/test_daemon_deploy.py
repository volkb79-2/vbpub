from __future__ import annotations

import grp
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.daemon import FrameBroker, serve_unix_socket
from groop.daemon.deploy import preflight_daemon_deployment, preflight_report_to_jsonable, render_preflight_text


def _current_group_name() -> str:
    return grp.getgrgid(os.getgid()).gr_name


def _start_socket(socket_path: Path):
    server = serve_unix_socket(socket_path, FrameBroker([fixture_frame()]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    return env


def test_daemon_preflight_json_and_text_report_for_usable_socket(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "good"
    runtime_dir.mkdir()
    runtime_dir.chmod(0o750)
    socket_path = runtime_dir / "groop.sock"
    server = _start_socket(socket_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "daemon",
                "preflight",
                "--socket",
                str(socket_path),
                "--group",
                _current_group_name(),
                "--json",
            ],
            check=False,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["socket"]["present"] is True
        assert payload["socket"]["can_connect"] is True
        assert all(check["ok"] for check in payload["checks"])

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "daemon",
                "preflight",
                "--socket",
                str(socket_path),
                "--group",
                _current_group_name(),
            ],
            check=False,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode == 0, proc.stderr
        assert "usable: yes" in proc.stdout
        assert "current process can connect" in proc.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_preflight_rejects_world_writable_runtime_dir(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "bad"
    runtime_dir.mkdir()
    runtime_dir.chmod(0o777)
    socket_path = runtime_dir / "groop.sock"
    server = _start_socket(socket_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "daemon",
                "preflight",
                "--socket",
                str(socket_path),
                "--group",
                _current_group_name(),
            ],
            check=False,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode == 1
        assert "world-writable" in proc.stdout
        assert "usable: no" in proc.stdout
        assert proc.stderr == ""
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_preflight_helper_does_not_invoke_mutation_or_systemd(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "safe"
    runtime_dir.mkdir()
    runtime_dir.chmod(0o750)
    socket_path = runtime_dir / "groop.sock"
    server = _start_socket(socket_path)
    try:
        import os as os_module
        import subprocess as subprocess_module

        monkeypatch.setattr(os_module, "chown", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected chown")))
        monkeypatch.setattr(os_module, "chmod", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected chmod")))
        monkeypatch.setattr(subprocess_module, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected systemd invocation")))

        report = preflight_daemon_deployment(socket_path, group_name=_current_group_name())
        assert report.usable is True
        assert report.can_connect is True
        assert json.loads(json.dumps(preflight_report_to_jsonable(report)))["ok"] is True
        assert "usable: yes" in render_preflight_text(report)
    finally:
        server.shutdown()
        server.server_close()


def test_systemd_templates_are_packaged() -> None:
    import importlib.resources as resources

    root = resources.files("groop")
    service = (root / "assets/systemd/groop.service").read_text()
    tmpfiles = (root / "assets/systemd/groop.tmpfiles").read_text()
    assert "groop daemon serve --socket /run/groop/groop.sock" in service
    assert "d /run/groop 0750 root groop -" in tmpfiles


# ── Install plan (P25) tests ─────────────────────────────────────────────────


def test_install_plan_deterministic_defaults() -> None:
    """build_install_plan with defaults returns deterministic JSON."""
    from groop.daemon.deploy import (
        DEFAULT_DAEMON_GROUP,
        DEFAULT_DAEMON_SOCKET,
        DEFAULT_SERVICE_DEST,
        DEFAULT_TMPFILES_DEST,
        build_install_plan,
        install_plan_to_jsonable,
        render_install_plan_text,
    )

    plan1 = build_install_plan()
    plan2 = build_install_plan()

    assert plan1.socket_path == DEFAULT_DAEMON_SOCKET
    assert plan1.group_name == DEFAULT_DAEMON_GROUP
    assert plan1.service_dest == DEFAULT_SERVICE_DEST
    assert plan1.tmpfiles_dest == DEFAULT_TMPFILES_DEST

    j1 = json.dumps(install_plan_to_jsonable(plan1), sort_keys=True)
    j2 = json.dumps(install_plan_to_jsonable(plan2), sort_keys=True)
    assert j1 == j2, "install plan must be deterministic"

    text = render_install_plan_text(plan1)
    assert "Step 1" in text
    assert "Step 7" in text
    assert "PLAN only" in text
    assert "groupadd" in text
    assert "systemctl" in text
    assert "install -m 0644 -o root -g root /dev/stdin" in text
    assert str(DEFAULT_DAEMON_SOCKET) in text


def test_install_plan_custom_args() -> None:
    """Custom socket, group, and dest paths appear in the plan."""
    from groop.daemon.deploy import build_install_plan, install_plan_to_jsonable

    plan = build_install_plan(
        socket_path="/tmp/custom/groop.sock",
        group_name="custom-group",
        service_dest="/opt/systemd/system/groop.service",
        tmpfiles_dest="/opt/tmpfiles.d/groop.conf",
    )

    assert plan.socket_path == Path("/tmp/custom/groop.sock")
    assert plan.group_name == "custom-group"
    assert plan.service_dest == Path("/opt/systemd/system/groop.service")
    assert plan.tmpfiles_dest == Path("/opt/tmpfiles.d/groop.conf")

    j = install_plan_to_jsonable(plan)
    assert j["socket_path"] == "/tmp/custom/groop.sock"
    assert j["group"] == "custom-group"
    assert j["service_dest"] == "/opt/systemd/system/groop.service"
    assert j["tmpfiles_dest"] == "/opt/tmpfiles.d/groop.conf"
    assert j["plan"] == "install"
    assert "Group=custom-group" in plan.service_content
    assert "--socket /tmp/custom/groop.sock" in plan.service_content
    assert "d /tmp/custom 0750 root custom-group -" in plan.tmpfiles_content
    assert "/run/groop 0750 root groop" not in plan.tmpfiles_content
    assert all("assets/systemd" not in (step.command or "") for step in plan.steps)


def test_install_plan_contains_correct_template_content() -> None:
    """The plan embeds the actual packaged template content."""
    from groop.daemon.deploy import build_install_plan

    plan = build_install_plan()
    assert "groop daemon serve --socket /run/groop/groop.sock" in plan.service_content
    assert "d /run/groop 0750 root groop -" in plan.tmpfiles_content
    assert "groop.service" in plan.service_asset
    assert "groop.tmpfiles" in plan.tmpfiles_asset


def test_install_plan_steps_reference_every_phase() -> None:
    """All 7 installation steps are present and non-empty."""
    from groop.daemon.deploy import build_install_plan

    plan = build_install_plan()
    assert len(plan.steps) == 7

    descriptions = {s.order: s.description for s in plan.steps}
    assert 1 in descriptions
    assert "group" in descriptions[1].lower()

    expected_commands = {"groupadd", "usermod", "install", "systemctl", "preflight"}
    all_commands = {s.command for s in plan.steps if s.command}
    assert all_commands
    assert any("groupadd" in (s.command or "") for s in plan.steps)
    assert any("systemctl" in (s.command or "") for s in plan.steps)


def test_install_plan_cli_json(tmp_path: Path) -> None:
    """groop daemon install-plan --json emits deterministic JSON with exit 0."""
    import subprocess as subprocess_module

    proc = subprocess_module.run(
        [sys.executable, "-m", "groop.cli", "daemon", "install-plan", "--json"],
        check=False,
        cwd=fixture_root().parents[1],
        env=_cli_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert payload["plan"] == "install"
    assert payload["group"] == "groop"
    assert len(payload["steps"]) == 7
    assert "groupadd" in payload["steps"][0]["command"]


def test_install_plan_cli_text(tmp_path: Path) -> None:
    """groop daemon install-plan (text) prints ordered steps and warnings."""
    import subprocess as subprocess_module

    proc = subprocess_module.run(
        [sys.executable, "-m", "groop.cli", "daemon", "install-plan"],
        check=False,
        cwd=fixture_root().parents[1],
        env=_cli_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert proc.stderr == ""
    assert "groop daemon install plan" in proc.stdout
    assert "Step 1" in proc.stdout
    assert "Step 7" in proc.stdout
    assert "PLAN only" in proc.stdout or "read-only" in proc.stdout


def test_install_plan_does_not_mutate_host(monkeypatch) -> None:
    """The install-plan helper must not invoke mutation or systemd."""
    import subprocess as subprocess_module
    from groop.daemon.deploy import build_install_plan

    import os as os_module
    monkeypatch.setattr(os_module, "chown", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected chown")))
    monkeypatch.setattr(os_module, "chmod", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected chmod")))
    monkeypatch.setattr(subprocess_module, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected systemd invocation")))

    # Should not throw — build_install_plan is pure content assembly.
    plan = build_install_plan()
    assert plan is not None
    assert len(plan.steps) == 7
    # JSON round-trip should work without host interaction.
    from groop.daemon.deploy import install_plan_to_jsonable
    j = json.dumps(install_plan_to_jsonable(plan), sort_keys=True)
    payload = json.loads(j)
    assert payload["plan"] == "install"


# ── Daemon current (P30) tests ────────────────────────────────────────────


def test_daemon_current_returns_canonical_json(tmp_path: Path) -> None:
    """groop daemon current --socket TMP returns canonical frame JSON."""
    socket_path = tmp_path / "groop.sock"
    server = _start_socket(socket_path)
    from groop.model import frame_to_jsonable

    try:
        import io
        from groop.cli import _main_daemon

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = _main_daemon(["current", "--socket", str(socket_path)])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout
        assert code == 0, f"expected 0, got {code}"
        payload = json.loads(output)
        assert payload == frame_to_jsonable(fixture_frame())
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_current_pretty_json(tmp_path: Path) -> None:
    """groop daemon current --pretty-json produces indented JSON."""
    socket_path = tmp_path / "groop.sock"
    server = _start_socket(socket_path)

    try:
        import io
        from groop.cli import _main_daemon

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = _main_daemon(["current", "--socket", str(socket_path), "--pretty-json"])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout
        assert code == 0
        # Pretty JSON should contain newlines and indentation
        assert "\n" in output
        assert "  " in output
        payload = json.loads(output)
        assert "schema_version" in payload
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_current_missing_socket_returns_nonzero(tmp_path: Path) -> None:
    """groop daemon current against a missing socket returns non-zero and
    does not fall back to live collection."""
    import io
    from groop.cli import _main_daemon

    missing = tmp_path / "nonexistent.sock"
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    code = None
    try:
        code = _main_daemon(["current", "--socket", str(missing)])
    finally:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    assert code is not None
    assert code == 2, f"expected 2, got {code}"
    assert "cannot connect" in stderr_val.lower() or "no such" in stderr_val.lower()
    assert stdout_val == ""  # No live collection fallback


def test_daemon_current_parse_args(tmp_path: Path) -> None:
    """groop daemon current parses correctly with default socket."""
    from groop.cli import parse_daemon_args
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    args = parse_daemon_args(["current", "--json"])
    assert args.command == "current"
    assert args.socket == DEFAULT_DAEMON_SOCKET
    assert args.json is True
    assert args.pretty_json is False


def test_daemon_current_parse_args_custom_socket(tmp_path: Path) -> None:
    """groop daemon current --socket /custom.sock parses correctly."""
    from groop.cli import parse_daemon_args

    args = parse_daemon_args(["current", "--socket", "/tmp/custom.sock", "--pretty-json"])
    assert args.command == "current"
    assert args.socket == Path("/tmp/custom.sock")
    assert args.pretty_json is True


# ── Daemon current error guidance (P31) tests ─────────────────────────────


def test_daemon_current_missing_default_socket_guidance(tmp_path: Path) -> None:
    """daemon current against a missing default socket prints guidance with
    preflight and install-plan."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonConnectError
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    exc = DaemonConnectError(f"cannot connect to {DEFAULT_DAEMON_SOCKET}: No such file or directory")
    msg = _format_daemon_error(exc, DEFAULT_DAEMON_SOCKET)
    assert "cannot connect to " + str(DEFAULT_DAEMON_SOCKET) in msg
    assert "Try: groop daemon preflight" in msg
    assert "groop daemon install-plan" in msg


def test_daemon_current_missing_custom_socket_guidance(tmp_path: Path) -> None:
    """daemon current against a missing custom socket prints guidance with
    preflight --socket <path>."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonConnectError

    custom = Path("/tmp/missing-custom.sock")
    exc = DaemonConnectError(f"cannot connect to {custom}: Connection refused")
    msg = _format_daemon_error(exc, custom)
    assert "cannot connect to /tmp/missing-custom.sock" in msg
    assert "Try: groop daemon preflight --socket /tmp/missing-custom.sock" in msg
    assert "install-plan" not in msg


def test_daemon_current_missing_socket_guidance_via_cli(tmp_path: Path) -> None:
    """daemon current CLI against a missing socket prints guidance."""
    import io
    from groop.cli import _main_daemon

    missing = tmp_path / "no-socket-here.sock"
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    code = None
    try:
        code = _main_daemon(["current", "--socket", str(missing)])
    finally:
        stderr_val = sys.stderr.getvalue()
        stdout_val = sys.stdout.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr
    assert code == 2
    assert "cannot connect" in stderr_val.lower()
    assert "Try: groop daemon preflight --socket" in stderr_val
    assert stdout_val == ""
