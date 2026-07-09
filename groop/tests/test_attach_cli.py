from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.daemon import FrameBroker, serve_unix_socket
from groop.model import frame_to_jsonable


def _start_broker(socket_path: Path) -> object:
    server = serve_unix_socket(socket_path, FrameBroker([fixture_frame()]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    return env


def test_attach_once_json_returns_canonical_frame(tmp_path: Path) -> None:
    socket_path = tmp_path / "groop.sock"
    server = _start_broker(socket_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "--attach",
                str(socket_path),
                "--once",
                "--json",
            ],
            check=True,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
        )
        assert json.loads(proc.stdout) == frame_to_jsonable(fixture_frame())
    finally:
        server.shutdown()
        server.server_close()


def test_attach_ui_smoke_works_against_fixture_broker(tmp_path: Path) -> None:
    socket_path = tmp_path / "groop.sock"
    server = _start_broker(socket_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "--attach",
                str(socket_path),
                "--ui-smoke",
            ],
            check=True,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
        )
        assert proc.stdout.strip() == "ui smoke ok frames=1 view=tree profile=auto"
    finally:
        server.shutdown()
        server.server_close()


def test_attach_rejects_ambiguous_combinations(tmp_path: Path) -> None:
    socket_path = tmp_path / "groop.sock"
    server = _start_broker(socket_path)
    try:
        scenarios = [
            (
                [
                    sys.executable,
                    "-m",
                    "groop.cli",
                    "--attach",
                    str(socket_path),
                    "--cgroup-root",
                    str(tmp_path / "cg"),
                    "--once",
                    "--json",
                ],
                "--attach does not accept --cgroup-root",
            ),
            (
                [
                    sys.executable,
                    "-m",
                    "groop.cli",
                    "--attach",
                    str(socket_path),
                    "--replay",
                    str(fixture_root() / "frames" / "gstammtisch-once.jsonl"),
                ],
                "choose either --attach or --replay",
            ),
            (
                [
                    sys.executable,
                    "-m",
                    "groop.cli",
                    "--attach",
                    str(socket_path),
                    "--step",
                ],
                "--attach does not accept replay pacing flags",
            ),
            (
                [
                    sys.executable,
                    "-m",
                    "groop.cli",
                    "--attach",
                    str(socket_path),
                    "--speed",
                    "2.0",
                ],
                "--attach does not accept replay pacing flags",
            ),
        ]
        for argv, expected in scenarios:
            proc = subprocess.run(
                argv,
                cwd=fixture_root().parents[1],
                env=_cli_env(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.returncode == 2
            assert expected in proc.stderr
    finally:
        server.shutdown()
        server.server_close()


# ── Default-socket attach (P30) tests ─────────────────────────────────────


def test_attach_default_socket_parse_bare_flag(tmp_path: Path) -> None:
    """--attach with no path parses as DEFAULT_DAEMON_SOCKET via argparse."""
    from groop.cli import parse_args
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    args = parse_args(["--attach", "--once", "--json"])
    assert args.attach == DEFAULT_DAEMON_SOCKET
    assert args.once is True
    assert args.json is True


def test_attach_default_socket_works_with_fixture_broker(tmp_path: Path, monkeypatch) -> None:
    """--attach with no path uses DEFAULT_DAEMON_SOCKET in main()."""
    socket_path = tmp_path / "groop.sock"
    server = _start_broker(socket_path)
    try:
        from groop import cli

        monkeypatch.setattr(cli, "DEFAULT_DAEMON_SOCKET", socket_path)
        output = StringIO()
        with redirect_stdout(output):
            code = cli.main(["--attach", "--once", "--json"])
        assert code == 0
        assert json.loads(output.getvalue()) == frame_to_jsonable(fixture_frame())
    finally:
        server.shutdown()
        server.server_close()


def test_attach_custom_socket_still_works(tmp_path: Path) -> None:
    """--attach /custom.sock --once --json still works (backward compat)."""
    socket_path = tmp_path / "groop.sock"
    server = _start_broker(socket_path)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "--attach",
                str(socket_path),
                "--once",
                "--json",
            ],
            check=True,
            cwd=fixture_root().parents[1],
            env=_cli_env(),
            text=True,
            stdout=subprocess.PIPE,
        )
        assert json.loads(proc.stdout) == frame_to_jsonable(fixture_frame())
    finally:
        server.shutdown()
        server.server_close()


def test_attach_default_socket_with_ui_smoke(tmp_path: Path) -> None:
    """--attach (bare) + --ui-smoke parses and runs against default socket."""
    from groop.cli import parse_args
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    args = parse_args(["--attach", "--ui-smoke"])
    assert args.attach == DEFAULT_DAEMON_SOCKET
    assert args.ui_smoke is True


# ── Daemon error guidance (P31) tests ─────────────────────────────────────


def test_default_socket_attach_error_guidance(tmp_path: Path) -> None:
    """Default-socket attach missing socket prints original error and
    preflight/install-plan guidance."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonConnectError
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    exc = DaemonConnectError("cannot connect to /run/groop/groop.sock: No such file or directory")
    msg = _format_daemon_error(exc, DEFAULT_DAEMON_SOCKET)
    assert "cannot connect to /run/groop/groop.sock" in msg
    assert "Try: groop daemon preflight" in msg
    assert "groop daemon install-plan" in msg


def test_custom_socket_attach_error_guidance(tmp_path: Path) -> None:
    """Custom-socket attach error suggests preflight with that socket."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonConnectError
    from pathlib import Path

    custom = Path("/tmp/my-custom.sock")
    exc = DaemonConnectError(f"cannot connect to {custom}: Connection refused")
    msg = _format_daemon_error(exc, custom)
    assert "cannot connect to /tmp/my-custom.sock" in msg
    assert "Try: groop daemon preflight --socket /tmp/my-custom.sock" in msg
    assert "install-plan" not in msg


def test_protocol_error_guidance(tmp_path: Path) -> None:
    """Protocol error guidance mentions compatible daemon and logs."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonProtocolError
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    exc = DaemonProtocolError("daemon at /run/groop/groop.sock returned malformed JSON on line 1")
    msg = _format_daemon_error(exc, DEFAULT_DAEMON_SOCKET)
    assert "compatible groop daemon" in msg
    assert "daemon logs" in msg
    assert "malformed JSON" in msg


def test_response_error_guidance(tmp_path: Path) -> None:
    """Response error guidance mentions compatible daemon and logs."""
    from groop.cli import _format_daemon_error
    from groop.daemon.client import DaemonResponseError
    from groop.daemon.deploy import DEFAULT_DAEMON_SOCKET

    exc = DaemonResponseError("daemon at /run/groop/groop.sock returned an error: denied")
    msg = _format_daemon_error(exc, DEFAULT_DAEMON_SOCKET)
    assert "compatible groop daemon" in msg
    assert "daemon logs" in msg
    assert "denied" in msg


def test_attach_missing_socket_returns_guidance_via_cli(tmp_path: Path) -> None:
    """CLI --attach against a missing socket prints original error + guidance
    and exits 2 without live-collection fallback."""
    from groop.cli import _main_inspect_files as _  # noqa: F811
    import groop.cli as cli_mod

    missing = tmp_path / "nonexistent.sock"
    output = StringIO()
    err = StringIO()
    with redirect_stdout(output), redirect_stderr(err):
        code = cli_mod.main(["--attach", str(missing), "--once", "--json"])
    assert code == 2
    assert "cannot connect" in err.getvalue().lower() or "no such" in err.getvalue().lower()
    assert "Try: groop daemon preflight --socket" in err.getvalue()
    assert output.getvalue() == ""  # No live fallback
