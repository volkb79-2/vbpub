"""Hermetic tests for runner/install-fleetd.sh: fake wheels, fake venv, temp root only."""
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
INSTALLER = RUNNER / "install-fleetd.sh"
FLEETD_WHEEL = "fleetd-0.3.0-py3-none-any.whl"
CLI_EXTENDED_WHEEL = "cli_extended-0.5.0-py3-none-any.whl"


def _installer_fixture(tmp_path: Path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / INSTALLER.name
    shutil.copy2(INSTALLER, script)
    (script_dir / "lib.sh").write_text("identity_check() { :; }\n")

    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    wheel_bytes = {
        FLEETD_WHEEL: b"fake fleetd wheel\n",
        CLI_EXTENDED_WHEEL: b"fake cli-extended wheel\n",
    }
    hashes = {}
    for name, data in wheel_bytes.items():
        (wheel_dir / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    root = tmp_path / "root"
    venv_log = tmp_path / "venv-calls.jsonl"
    pip_log = tmp_path / "pip-calls.jsonl"
    fake_command_source = f"#!{sys.executable}\nraise SystemExit(0)\n"
    fake_venv_python = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] != ['-m', 'pip', 'install']:
    raise SystemExit('unexpected fake pip argv: ' + repr(args))
with open(os.environ['FAKE_PIP_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps([sys.argv[0]] + args) + '\\n')
command = Path(sys.argv[0]).parent / 'fleet'
if os.environ.get('FAKE_SKIP_COMMAND') != '1':
    command.write_text({fake_command_source!r})
    command.chmod(0o755)
"""
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] != ['-m', 'venv'] or len(args) != 3:
    raise SystemExit('unexpected fake python3 argv: ' + repr(args))
target = Path(args[2])
try:
    target.resolve().relative_to(Path(os.environ['FAKE_ROOT_PREFIX']).resolve())
except ValueError:
    raise SystemExit('fake python3 refused a path outside its test root')
with open(os.environ['FAKE_VENV_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(args) + '\\n')
(target / 'bin').mkdir(parents=True, exist_ok=True)
if os.environ.get('FAKE_SKIP_VENV_PYTHON') != '1':
    python = target / 'bin' / 'python'
    python.write_text({fake_venv_python!r})
    python.chmod(0o755)
"""
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    fake_id = fake_bin / "id"
    fake_id.write_text("#!/bin/sh\nprintf '1003\\n'\n")
    fake_id.chmod(fake_id.stat().st_mode | stat.S_IXUSR)
    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text(
        f"""#!{sys.executable}
import os, sys
from pathlib import Path
root = Path(os.environ['FAKE_ROOT_PREFIX']).resolve()
for item in sys.argv[1:]:
    if item.startswith('-'):
        continue
    try:
        Path(os.path.abspath(item)).relative_to(root)
    except ValueError:
        raise SystemExit('fake mkdir refused a path outside its test root')
os.execv('/bin/mkdir', ['mkdir', *sys.argv[1:]])
"""
    )
    fake_mkdir.chmod(fake_mkdir.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VENV_LOG": str(venv_log),
        "FAKE_PIP_LOG": str(pip_log),
        "FAKE_ROOT_PREFIX": str(tmp_path),
    }
    args = [
        "--wheel-dir",
        str(wheel_dir),
        "--fleetd-version",
        "0.3.0",
        "--fleetd-sha256",
        hashes[FLEETD_WHEEL],
        "--cli-extended-version",
        "0.5.0",
        "--cli-extended-sha256",
        hashes[CLI_EXTENDED_WHEEL],
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


def test_installs_both_verified_wheels_offline_into_a_versioned_venv_and_links_fleet(tmp_path):
    script, args, env, root, venv_log, pip_log, hashes, wheel_dir = _installer_fixture(tmp_path)

    result = _run(script, args, env)

    assert result.returncode == 0, result.stderr
    assert _jsonl(venv_log) == [["-m", "venv", str(root / "opt/fleetd/0.3.0/venv")]]
    pip_args = _jsonl(pip_log)[0]
    assert pip_args[0] == str(root / "opt/fleetd/0.3.0/venv/bin/python")
    assert pip_args[1:4] == ["-m", "pip", "install"]
    assert "--no-index" in pip_args and "--no-deps" in pip_args and "--force-reinstall" in pip_args
    assert pip_args[-2:] == [
        str(wheel_dir / CLI_EXTENDED_WHEEL),
        str(wheel_dir / FLEETD_WHEEL),
    ]
    link = root / "usr/local/bin/fleet"
    assert link.is_symlink()
    assert link.resolve() == root / "opt/fleetd/0.3.0/venv/bin/fleet"
    assert (root / "opt/fleetd/0.3.0/venv/.wheel-sha256").read_text().split() == [
        hashes[FLEETD_WHEEL],
        hashes[CLI_EXTENDED_WHEEL],
    ]
    assert "installed fleetd 0.3.0" in result.stdout


def test_never_installs_into_the_system_python(tmp_path):
    """Debian 13 refuses a system-python pip install, so pip must be the venv's own."""
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)

    result = _run(script, args, env)

    assert result.returncode == 0, result.stderr
    pip_calls = _jsonl(pip_log)
    assert len(pip_calls) == 1
    # The recorded interpreter is the venv's bin/python, never a bare python3.
    assert pip_calls[0][0] == str(root / "opt/fleetd/0.3.0/venv/bin/python")
    assert pip_calls[0][1:4] == ["-m", "pip", "install"]
    assert "/bin/python3" not in pip_calls[0][0]


def test_check_mode_reports_absence_without_writing_anything(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)

    result = _run(script, [*args, "--check"], env)

    assert result.returncode == 1
    assert "NOT installed" in result.stdout
    assert not root.exists()
    assert _jsonl(venv_log) == []
    assert _jsonl(pip_log) == []


def test_check_mode_reports_a_current_install_and_writes_nothing(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    first = _run(script, args, env)
    assert first.returncode == 0, first.stderr

    checked = _run(script, [*args, "--check"], env)

    assert checked.returncode == 0, checked.stderr
    assert "is installed at" in checked.stdout
    assert len(_jsonl(venv_log)) == 1
    assert len(_jsonl(pip_log)) == 1


def test_check_mode_reports_a_stale_install_as_not_current(tmp_path):
    script, args, env, root, venv_log, pip_log, hashes, wheel_dir = _installer_fixture(tmp_path)
    first = _run(script, args, env)
    assert first.returncode == 0, first.stderr

    fleetd_wheel = wheel_dir / FLEETD_WHEEL
    fleetd_wheel.write_bytes(b"replacement fleetd wheel content\n")
    hashes[FLEETD_WHEEL] = hashlib.sha256(fleetd_wheel.read_bytes()).hexdigest()
    args[args.index("--fleetd-sha256") + 1] = hashes[FLEETD_WHEEL]

    checked = _run(script, [*args, "--check"], env)

    assert checked.returncode == 1
    assert "NOT installed" in checked.stdout
    assert not _jsonl(pip_log)[1:]


def test_an_unreadable_install_record_is_indeterminate_not_absent(tmp_path):
    """Absence and emptiness are different states: an unreadable record must not report "not installed"."""
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    venv = root / "opt/fleetd/0.3.0/venv"
    (venv / "bin").mkdir(parents=True)
    for name in ("python", "fleet"):
        executable = venv / "bin" / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    (venv / ".wheel-sha256").mkdir()

    checked = _run(script, [*args, "--check"], env)
    applied = _run(script, args, env)

    for result in (checked, applied):
        assert result.returncode == 8, result.stderr
        assert "not a regular file" in result.stderr
        assert "NOT installed" not in result.stdout
    assert _jsonl(pip_log) == []
    assert _jsonl(venv_log) == []


def test_check_mode_does_not_require_root_identity(tmp_path):
    script, args, env, _, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args = args[: args.index("--root")]

    result = _run(script, [*args, "--check"], env)

    assert result.returncode == 1
    assert "must run as root" not in result.stderr
    assert _jsonl(venv_log) == []


def test_missing_required_argument_refuses_with_usage_before_root_setup(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    option_index = args.index("--cli-extended-sha256")
    incomplete_args = args[:option_index] + args[option_index + 2 :]

    result = _run(script, incomplete_args, env)

    assert result.returncode == 2
    assert result.stderr.startswith("usage:")
    assert not root.exists()
    assert _jsonl(venv_log) == []


@pytest.mark.parametrize("option", ["--fleetd-sha256", "--cli-extended-sha256"])
def test_hash_mismatch_refuses_before_venv_creation(tmp_path, option):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    args[args.index(option) + 1] = "0" * 64

    result = _run(script, args, env)

    assert result.returncode == 4
    assert "SHA-256 mismatch" in result.stderr
    assert not root.exists()
    assert _jsonl(venv_log) == []
    assert _jsonl(pip_log) == []


@pytest.mark.parametrize("missing", ["directory", "fleetd-wheel", "cli-extended-wheel"])
def test_missing_wheel_inputs_refuse_before_root_setup(tmp_path, missing):
    script, args, env, root, venv_log, _, _, wheel_dir = _installer_fixture(tmp_path)
    if missing == "directory":
        args[args.index("--wheel-dir") + 1] = str(tmp_path / "absent-wheels")
    elif missing == "fleetd-wheel":
        (wheel_dir / FLEETD_WHEEL).unlink()
    else:
        (wheel_dir / CLI_EXTENDED_WHEEL).unlink()

    result = _run(script, args, env)

    assert result.returncode == 3
    assert not root.exists()
    assert _jsonl(venv_log) == []


def test_successful_install_is_idempotent_for_the_same_wheel_hashes(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)

    first = _run(script, args, env)
    link = root / "usr/local/bin/fleet"
    link.unlink()
    second = _run(script, args, env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(_jsonl(venv_log)) == 1
    assert len(_jsonl(pip_log)) == 1
    assert link.is_symlink()
    assert link.resolve() == root / "opt/fleetd/0.3.0/venv/bin/fleet"


def test_successful_install_repairs_a_symlink_to_a_wrong_target(tmp_path):
    script, args, env, root, _, pip_log, _, _ = _installer_fixture(tmp_path)
    first = _run(script, args, env)
    assert first.returncode == 0, first.stderr

    link = root / "usr/local/bin/fleet"
    wrong_target = root / "wrong-fleet-target"
    wrong_target.write_text("wrong executable\n")
    link.unlink()
    link.symlink_to(wrong_target)

    second = _run(script, args, env)

    assert second.returncode == 0, second.stderr
    assert len(_jsonl(pip_log)) == 1
    assert link.is_symlink()
    assert link.resolve() == root / "opt/fleetd/0.3.0/venv/bin/fleet"


@pytest.mark.parametrize(
    ("wheel_name", "option"),
    [(FLEETD_WHEEL, "--fleetd-sha256"), (CLI_EXTENDED_WHEEL, "--cli-extended-sha256")],
)
def test_changed_wheel_hash_reinstalls_the_same_version(tmp_path, wheel_name, option):
    script, args, env, root, _, pip_log, hashes, wheel_dir = _installer_fixture(tmp_path)
    first = _run(script, args, env)
    assert first.returncode == 0, first.stderr

    wheel = wheel_dir / wheel_name
    wheel.write_bytes(b"replacement wheel content\n")
    hashes[wheel_name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    args[args.index(option) + 1] = hashes[wheel_name]
    second = _run(script, args, env)

    assert second.returncode == 0, second.stderr
    assert len(_jsonl(pip_log)) == 2
    assert (root / "opt/fleetd/0.3.0/venv/.wheel-sha256").read_text().split() == [
        hashes[FLEETD_WHEEL],
        hashes[CLI_EXTENDED_WHEEL],
    ]


def test_refuses_a_regular_file_at_the_command_path_without_mutating_it(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    link = root / "usr/local/bin/fleet"
    link.parent.mkdir(parents=True)
    link.write_text("preserve this file\n")

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "refusing to replace non-symlink command" in result.stderr
    assert link.read_text() == "preserve this file\n"
    assert _jsonl(venv_log) == []


@pytest.mark.parametrize("option", ["--fleetd-version", "--cli-extended-version"])
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


def test_rejects_a_relative_test_root_before_writing(tmp_path):
    script, args, env, _, venv_log, _, _, _ = _installer_fixture(tmp_path)
    args[args.index("--root") + 1] = "relative-root"

    result = _run(script, args, env)

    assert result.returncode == 2
    assert "--root must be an absolute path" in result.stderr
    assert _jsonl(venv_log) == []


def test_rejects_an_empty_root_instead_of_meaning_the_real_root(tmp_path):
    """`--root "$UNSET"` must never silently become `/`."""
    script, args, env, _, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    args[args.index("--root") + 1] = ""

    result = _run(script, args, env)

    assert result.returncode == 2
    assert "--root must not be empty" in result.stderr
    assert _jsonl(venv_log) == [] and _jsonl(pip_log) == []


@pytest.mark.parametrize("symlink_at", ["base", "version", "venv"])
def test_refuses_a_symlinked_install_path(tmp_path, symlink_at):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_venv = None
    install_root = root / "opt/fleetd"
    if symlink_at == "base":
        install_root.parent.mkdir(parents=True)
        install_root.symlink_to(outside, target_is_directory=True)
    elif symlink_at == "version":
        install_root.mkdir(parents=True)
        (install_root / "0.3.0").symlink_to(outside, target_is_directory=True)
    else:
        (install_root / "0.3.0").mkdir(parents=True)
        outside_venv = outside / "valid-venv"
        fake_python = Path(env["PATH"].split(os.pathsep)[0]) / "python3"
        prepared = subprocess.run(
            [str(fake_python), "-m", "venv", str(outside_venv)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert prepared.returncode == 0, prepared.stderr
        venv_log.write_text("")
        (install_root / "0.3.0/venv").symlink_to(outside_venv, target_is_directory=True)

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "refusing symlinked install path" in result.stderr
    assert _jsonl(venv_log) == []
    assert _jsonl(pip_log) == []
    if outside_venv is not None:
        assert not (outside_venv / ".wheel-sha256").exists()
        assert not (outside_venv / "bin/fleet").exists()


def test_refuses_an_incomplete_existing_venv_without_rebuilding_it(tmp_path):
    script, args, env, root, venv_log, _, _, _ = _installer_fixture(tmp_path)
    venv = root / "opt/fleetd/0.3.0/venv"
    venv.mkdir(parents=True)

    result = _run(script, args, env)

    assert result.returncode == 5
    assert "existing venv is incomplete" in result.stderr
    assert _jsonl(venv_log) == []


def test_refuses_a_created_venv_without_python_before_pip(tmp_path):
    script, args, env, root, venv_log, pip_log, _, _ = _installer_fixture(tmp_path)
    env["FAKE_SKIP_VENV_PYTHON"] = "1"

    result = _run(script, args, env)

    assert result.returncode == 7
    assert "venv Python is missing" in result.stderr
    assert len(_jsonl(venv_log)) == 1
    assert _jsonl(pip_log) == []
    assert not (root / "usr/local/bin/fleet").exists()


def test_refuses_successful_pip_without_the_fleet_entrypoint(tmp_path):
    script, args, env, root, _, pip_log, _, _ = _installer_fixture(tmp_path)
    env["FAKE_SKIP_COMMAND"] = "1"

    result = _run(script, args, env)

    assert result.returncode == 7
    assert "fleetd command was not installed" in result.stderr
    assert len(_jsonl(pip_log)) == 1
    assert not (root / "usr/local/bin/fleet").exists()


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_the_installer_is_shellcheck_clean():
    # -P pins the sourced lib.sh's directory, so the result does not depend on
    # the working directory this test happens to run from.
    subprocess.run(["shellcheck", "-x", "-P", str(INSTALLER.parent), str(INSTALLER)], check=True)
