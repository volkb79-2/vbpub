from __future__ import annotations

import grp
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from conftest import fixture_root
from groop.daemon import FrameBroker, serve_unix_socket
from groop.daemon.deploy import preflight_daemon_deployment, preflight_report_to_jsonable, render_preflight_text


def _current_group_name() -> str:
    return grp.getgrgid(os.getgid()).gr_name


def _start_socket(socket_path: Path):
    server = serve_unix_socket(socket_path, FrameBroker([]))
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
