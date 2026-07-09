from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
