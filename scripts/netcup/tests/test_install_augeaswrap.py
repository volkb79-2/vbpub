from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner"
INSTALLER = RUNNER / "install-augeaswrap.sh"


def _installer_fixture(tmp_path: Path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / INSTALLER.name
    shutil.copy2(INSTALLER, script)
    (script_dir / "lib.sh").write_text("identity_check() { :; }\n")

    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheel_bytes = {
        "augeaswrap-0.2.0-py3-none-any.whl": b"fake augeaswrap wheel\n",
        "cli_extended-0.6.0-py3-none-any.whl": b"fake cli-extended wheel\n",
    }
    hashes = {}
    for name, data in wheel_bytes.items():
        (wheel_dir / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    venv_log = tmp_path / "venv-calls.jsonl"
    pip_log = tmp_path / "pip-calls.jsonl"
    fake_python = fake_bin / "python3"
    fake_command_source = f"#!{sys.executable}\nraise SystemExit(0)\n"
    fake_venv_python = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] != ['-m', 'pip', 'install']:
    raise SystemExit('unexpected fake pip argv: ' + repr(args))
with open(os.environ['FAKE_PIP_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(args) + '\\n')
command = Path(sys.argv[0]).parent / 'augeaswrap'
if os.environ.get('FAKE_SKIP_COMMAND') != '1':
    command.write_text({fake_command_source!r})
    command.chmod(0o755)
"""
    fake_python.write_text(
        f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] != ['-m', 'venv'] or len(args) != 3:
    raise SystemExit('unexpected fake python3 argv: ' + repr(args))
target = Path(args[2])
with open(os.environ['FAKE_VENV_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(args) + '\\n')
(target / 'bin').mkdir(parents=True, exist_ok=True)
python = target / 'bin' / 'python'
python.write_text({fake_venv_python!r})
python.chmod(0o755)
"""
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    fake_id = fake_bin / "id"
    fake_id.write_text("#!/bin/sh\nprintf '1003\\n'\n")
    fake_id.chmod(fake_id.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VENV_LOG": str(venv_log),
        "FAKE_PIP_LOG": str(pip_log),
    }
    root = tmp_path / "root"
    args = [
        "--wheel-dir",
        str(wheel_dir),
        "--augeaswrap-version",
        "0.2.0",
        "--augeaswrap-sha256",
        hashes["augeaswrap-0.2.0-py3-none-any.whl"],
        "--cli-extended-version",
        "0.6.0",
        "--cli-extended-sha256",
        hashes["cli_extended-0.6.0-py3-none-any.whl"],
        "--root",
        str(root),
    ]
    return script, args, env, root, venv_log, pip_log, hashes, wheel_dir


def _run(script: Path, args: list[str], env: dict[str, str]):
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


def _jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_installs_both_verified_wheels_offline_and_links_the_root_command(tmp_path):
    script, args, env, root, venv_log, pip_log, hashes, wheel_dir = _installer_fixture(tmp_path)

    result = _run(script, args, env)

    assert result.returncode == 0, result.stderr
    assert _jsonl(venv_log) == [["-m", "venv", str(root / "opt/augeaswrap/0.2.0/venv")]]
    pip_args = _jsonl(pip_log)[0]
    assert pip_args[:3] == ["-m", "pip", "install"]
    assert "--no-index" in pip_args and "--no-deps" in pip_args and "--force-reinstall" in pip_args
    assert pip_args[-2:] == [
        str(wheel_dir / "cli_extended-0.6.0-py3-none-any.whl"),
        str(wheel_dir / "augeaswrap-0.2.0-py3-none-any.whl"),
    ]
    assert (root / "usr/local/bin/augeaswrap").is_symlink()
    assert (root / "usr/local/bin/augeaswrap").resolve() == root / "opt/augeaswrap/0.2.0/venv/bin/augeaswrap"
    assert (root / "opt/augeaswrap/0.2.0/venv/.wheel-sha256").read_text().split() == [
        hashes["augeaswrap-0.2.0-py3-none-any.whl"],
        hashes["cli_extended-0.6.0-py3-none-any.whl"],
    ]


@pytest.mark.parametrize("option", ["--augeaswrap-sha256", "--cli-extended-sha256"])
def test_hash_mismatch_refuses_before_venv_creation(tmp_path, option):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args[args.index(option) + 1] = "0" * 64

    result = _run(script, args, env)

    assert result.returncode == 4
    assert "SHA-256 mismatch" in result.stderr
    assert not root.exists()
    assert _jsonl(venv_log) == []


def test_dry_run_verifies_inputs_without_writing_or_invoking_pip(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)

    result = _run(script, [*args, "--dry-run"], env)

    assert result.returncode == 0, result.stderr
    assert "would create venv" in result.stdout
    assert "--no-index --no-deps" in result.stdout
    assert not root.exists()
    assert _jsonl(venv_log) == []
    assert _jsonl(pip_log) == []


def test_successful_install_is_idempotent_for_the_same_wheel_hashes(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)

    first = _run(script, args, env)
    second = _run(script, args, env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(_jsonl(venv_log)) == 1
    assert len(_jsonl(pip_log)) == 1
    assert (root / "usr/local/bin/augeaswrap").resolve() == root / "opt/augeaswrap/0.2.0/venv/bin/augeaswrap"


def test_refuses_a_regular_file_at_the_root_path_without_mutating_it(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    link = root / "usr/local/bin/augeaswrap"
    link.parent.mkdir(parents=True)
    link.write_text("preserve this file\n")

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "refusing to replace non-symlink command" in result.stderr
    assert link.read_text() == "preserve this file\n"
    assert _jsonl(venv_log) == []


@pytest.mark.parametrize("option", ["--augeaswrap-version", "--cli-extended-version"])
def test_rejects_non_numeric_version_before_writing(tmp_path, option):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args[args.index(option) + 1] = "../current"

    result = _run(script, args, env)

    assert result.returncode == 2
    assert "dotted numeric versions" in result.stderr
    assert not root.exists()
    assert _jsonl(venv_log) == []


def test_rejects_non_hexadecimal_hash_before_writing(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args[args.index("--cli-extended-sha256") + 1] = "z" * 64

    result = _run(script, args, env)

    assert result.returncode == 2
    assert "64 hexadecimal digits" in result.stderr
    assert not root.exists()
    assert _jsonl(venv_log) == []


def test_refuses_to_install_into_fixed_root_paths_without_root_identity(tmp_path):
    script, args, env, _, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args = args[: args.index("--root")]

    result = _run(script, args, env)

    assert result.returncode == 6
    assert "must run as root" in result.stderr
    assert _jsonl(venv_log) == []


def test_refuses_a_symlinked_augeaswrap_install_root(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    install_root = root / "opt/augeaswrap"
    install_root.parent.mkdir(parents=True)
    install_root.symlink_to(outside, target_is_directory=True)

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "refusing symlinked install path" in result.stderr
    assert _jsonl(venv_log) == []


def test_refuses_an_incomplete_existing_venv_without_rebuilding_it(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    venv = root / "opt/augeaswrap/0.2.0/venv"
    venv.mkdir(parents=True)

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "existing venv is incomplete" in result.stderr
    assert _jsonl(venv_log) == []


def test_refuses_successful_pip_without_the_augeaswrap_entrypoint(tmp_path):
    script, args, env, root, _, pip_log, _, _ = _installer_fixture(tmp_path)
    env["FAKE_SKIP_COMMAND"] = "1"

    result = _run(script, args, env)

    assert result.returncode == 7
    assert "Augeaswrap command was not installed" in result.stderr
    assert len(_jsonl(pip_log)) == 1
    assert not (root / "usr/local/bin/augeaswrap").exists()
