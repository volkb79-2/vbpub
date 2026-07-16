"""Tests for the daemon status command (P32).

Covers:
- status JSON success against a fixture daemon socket
- status text success against a fixture daemon socket
- missing default/custom socket failure includes actionable guidance and exits 1
- protocol error failure includes compatible-daemon/log guidance and exits 1
- --pretty-json emits parseable indented JSON
- helper purity / no mutation expectations
"""

from __future__ import annotations

import io
import json
import os
import socketserver
import threading
from pathlib import Path

from conftest import fixture_frame
from topos.daemon import FrameBroker, serve_unix_socket


def _current_group_name() -> str:
    import grp
    return grp.getgrgid(os.getgid()).gr_name


def _start_socket(socket_path: Path):
    server = serve_unix_socket(socket_path, FrameBroker([fixture_frame()]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _serve_lines(socket_path: Path, lines: list[bytes]) -> socketserver.UnixStreamServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.rfile.readline(1024 * 1024)
            for line in lines:
                self.wfile.write(line if line.endswith(b"\n") else line + b"\n")

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestStatusHelper:
    """Unit tests for build_daemon_status and DaemonStatusReport."""

    def test_status_json_success(self, tmp_path: Path) -> None:
        """Status JSON against a fixture daemon socket succeeds."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.daemon.status import build_daemon_status

            report = build_daemon_status(socket_path, group_name=_current_group_name())
            assert report.ok is True
            assert report.protocol.ok is True
            assert report.protocol.message == "Current frame retrieved successfully."

            j = report.to_jsonable()
            assert j["ok"] is True
            assert j["socket"] == str(socket_path)
            assert j["protocol"]["ok"] is True
            assert j["protocol"]["schema_version"] is not None
            assert j["protocol"]["entity_count"] is not None
        finally:
            server.shutdown()
            server.server_close()

    def test_status_text_success(self, tmp_path: Path) -> None:
        """Status text output contains expected sections."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.daemon.status import build_daemon_status

            report = build_daemon_status(socket_path, group_name=_current_group_name())
            text = report.to_text()
            assert "topos daemon status" in text
            assert str(socket_path) in text
            assert "--- Protocol ---" in text
            assert "OK" in text or "DEGRADED" in text
        finally:
            server.shutdown()
            server.server_close()

    def test_status_pretty_json(self, tmp_path: Path) -> None:
        """--pretty-json produces parseable indented JSON."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.daemon.status import build_daemon_status

            report = build_daemon_status(socket_path, group_name=_current_group_name())
            pretty_raw = json.dumps(report.to_jsonable(), indent=2, sort_keys=True)
            assert "\n" in pretty_raw
            assert "  " in pretty_raw
            parsed = json.loads(pretty_raw)
            assert parsed["ok"] is True
            assert parsed["protocol"]["ok"] is True
        finally:
            server.shutdown()
            server.server_close()

    def test_status_missing_default_socket(self, tmp_path: Path, monkeypatch) -> None:
        """Missing default socket returns ok=False with guidance."""
        import topos.daemon.status as status_module
        from topos.daemon.status import build_daemon_status

        missing_default = tmp_path / "default.sock"
        monkeypatch.setattr(status_module, "DEFAULT_DAEMON_SOCKET", missing_default)
        report = build_daemon_status(missing_default)
        assert report.ok is False
        assert report.protocol.ok is False
        assert "Cannot connect" in report.protocol.message
        assert "preflight" in report.protocol.message
        assert "install-plan" in report.protocol.message

    def test_status_missing_custom_socket(self, tmp_path: Path) -> None:
        """Missing custom socket returns ok=False with custom-socket guidance."""
        from topos.daemon.status import build_daemon_status

        missing = tmp_path / "custom-missing.sock"
        report = build_daemon_status(missing)
        assert report.ok is False
        assert report.protocol.ok is False
        assert "Cannot connect" in report.protocol.message
        # Custom socket guidance references --socket
        assert "--socket" in report.protocol.message

    def test_status_protocol_error_message(self, tmp_path: Path) -> None:
        """Protocol error mentions compatible daemon and logs."""
        from topos.daemon.status import build_daemon_status

        socket_path = tmp_path / "bad-protocol.sock"
        server = _serve_lines(socket_path, [b"not-json"])
        try:
            report = build_daemon_status(socket_path, group_name=_current_group_name())
            assert report.ok is False
            assert report.protocol.ok is False
            assert "Protocol error" in report.protocol.message
            assert "compatible topos daemon" in report.protocol.message
            assert "daemon logs" in report.protocol.message
        finally:
            server.shutdown()
            server.server_close()

    def test_status_helper_does_not_mutate_host(self, tmp_path: Path, monkeypatch) -> None:
        """Status helper does not call mutation helpers or subprocesses."""
        import os as os_module
        import subprocess as subprocess_module

        from topos.daemon.status import build_daemon_status

        missing = tmp_path / "missing.sock"
        monkeypatch.setattr(os_module, "chown", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected chown")))
        monkeypatch.setattr(os_module, "chmod", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected chmod")))
        monkeypatch.setattr(subprocess_module, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected subprocess")))

        report = build_daemon_status(missing, group_name=_current_group_name())
        assert report.ok is False


class TestStatusCli:
    """Integration tests for the CLI status command."""

    def test_status_cli_json_success(self, tmp_path: Path) -> None:
        """topos daemon status --json against a fixture socket."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.cli import _main_daemon

            old_stdout = io.StringIO()
            import sys
            sys.stdout = old_stdout
            try:
                code = _main_daemon(["status", "--socket", str(socket_path), "--group", _current_group_name(), "--json"])
            finally:
                output = old_stdout.getvalue()
                sys.stdout = sys.__stdout__
            assert code == 0, f"expected 0, got {code}"
            payload = json.loads(output)
            assert payload["ok"] is True
            assert payload["socket"] == str(socket_path)
            assert payload["protocol"]["ok"] is True
        finally:
            server.shutdown()
            server.server_close()

    def test_status_cli_text_success(self, tmp_path: Path) -> None:
        """topos daemon status (text) against a fixture socket."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.cli import _main_daemon

            old_stdout = io.StringIO()
            import sys
            sys.stdout = old_stdout
            try:
                code = _main_daemon(["status", "--socket", str(socket_path), "--group", _current_group_name()])
            finally:
                output = old_stdout.getvalue()
                sys.stdout = sys.__stdout__
            assert code == 0, f"expected 0, got {code}"
            assert "topos daemon status" in output
        finally:
            server.shutdown()
            server.server_close()

    def test_status_cli_missing_socket_exits_1(self, tmp_path: Path) -> None:
        """Missing socket exits 1; guidance is in the stdout report text."""
        from topos.cli import _main_daemon

        missing = tmp_path / "nonexistent.sock"
        old_stdout = io.StringIO()
        import sys
        sys.stdout = old_stdout
        try:
            code = _main_daemon(["status", "--socket", str(missing)])
        finally:
            stdout_val = old_stdout.getvalue()
            sys.stdout = sys.__stdout__
        assert code == 1, f"expected 1, got {code}"
        # Guidance is in the status report text printed to stdout
        assert "Cannot connect" in stdout_val
        assert "--socket" in stdout_val

    def test_status_cli_pretty_json(self, tmp_path: Path) -> None:
        """topos daemon status --pretty-json produces indented JSON."""
        socket_path = tmp_path / "topos.sock"
        server = _start_socket(socket_path)
        try:
            from topos.cli import _main_daemon

            old_stdout = io.StringIO()
            import sys
            sys.stdout = old_stdout
            try:
                code = _main_daemon(["status", "--socket", str(socket_path), "--group", _current_group_name(), "--pretty-json"])
            finally:
                output = old_stdout.getvalue()
                sys.stdout = sys.__stdout__
            assert code == 0
            assert "\n" in output
            assert "  " in output
            payload = json.loads(output)
            assert payload["ok"] is True
        finally:
            server.shutdown()
            server.server_close()
