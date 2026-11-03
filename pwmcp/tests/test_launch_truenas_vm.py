"""Shell-level checks of console-capture/launch-truenas-vm.sh (QEMU replaced by a stub)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "tools" / "console-capture" / "launch-truenas-vm.sh"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    qemu = bindir / "qemu-system-x86_64"
    qemu.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB_ARGS"\n')
    qemu.chmod(0o755)
    code = tmp_path / "CODE.fd"
    code.write_bytes(b"x")
    vars_ = tmp_path / "VARS.fd"
    vars_.write_bytes(b"v")
    return {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "OVMF_CODE": str(code),
        "OVMF_VARS": str(vars_),
        "STUB_ARGS": str(tmp_path / "args.txt"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
    }


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    (Path(env["XDG_RUNTIME_DIR"])).mkdir(exist_ok=True)
    return subprocess.run(["sh", str(LAUNCHER), *args], env=env, capture_output=True, text=True, check=False)


def test_usage_exit_2(env):
    assert run(env).returncode == 2


def test_missing_disk_refused_never_created(env, tmp_path):
    w = tmp_path / "w"
    r = run(env, str(w), "none")
    assert r.returncode == 1 and "no installed disk" in r.stderr and "holds it" in r.stderr
    assert not (w / "OVMF_VARS.fd").exists() and not (w / "disk.qcow2").exists()


def test_directory_as_iso_refused(env, tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (w / "disk.qcow2").write_bytes(b"d")
    r = run(env, str(w), str(tmp_path))
    assert r.returncode == 1 and "ISO not a readable file" in r.stderr and "downloads dir:" in r.stderr


def test_alive_pidfile_refused(env, tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (w / "disk.qcow2").write_bytes(b"d")
    (w / "qemu.pid").write_text(str(os.getpid()))
    r = run(env, str(w), "none")
    assert r.returncode == 1 and "already running" in r.stderr


def test_long_workdir_moves_socket(env, tmp_path):
    w = tmp_path / ("d" * 90) / "w"
    w.mkdir(parents=True)
    (w / "disk.qcow2").write_bytes(b"d")
    r = run(env, str(w), "none")
    assert r.returncode == 0, r.stderr
    sock = [a for a in Path(env["STUB_ARGS"]).read_text().split("\n") if a.startswith("unix:")][0][5:].split(",")[0]
    assert len(sock) <= 107 and sock.startswith(env["XDG_RUNTIME_DIR"])


def test_no_env_override_or_git_resolution():
    text = LAUNCHER.read_text()
    assert "MDT_DOWNLOADS_DIR" not in text and "git-common-dir" not in text
