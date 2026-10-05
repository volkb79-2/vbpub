"""debian-install-v2.py resolves cli-extended from exactly one place (CX-D3).

One wheel beside the script is imported from the wheel (zipimport, no pip);
two wheels are refused; no wheel means an installed library, and without one the
entrypoint refuses with a pointer to bootstrap-remote.py. The repository source
is never a fallback. Every child process gets HOME under tmp_path, no network is
involved, and the library wheel is built here from the library under test.
"""

from __future__ import annotations

import shutil
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import cli_extended
from cli_extended import invoke_script

PROJECT = Path(__file__).resolve().parents[2]
REAL_ENTRYPOINT = PROJECT / "debian-install-v2.py"
LIBRARY_PACKAGE = Path(cli_extended.__file__).resolve().parent
WHEEL_NAME = "cli_extended-0.0.0-py3-none-any.whl"
NOT_INSTALLED = (
    "[ERROR] debian-install-v2: cli-extended is not installed; run via "
    "bootstrap-remote.py or install the cli-extended wheel\n"
)

PROBE = """\
import runpy, sys
entry = sys.argv[1]
sys.argv = [entry, *sys.argv[2:]]
try:
    runpy.run_path(entry, run_name="__main__")
except SystemExit as exc:
    code = exc.code
import cli_extended
print("CLI_EXTENDED_FILE=" + cli_extended.__file__)
print("EXIT=" + repr(code))
"""


def build_wheel(directory: Path, name: str = WHEEL_NAME) -> Path:
    """Zip the library under test into a minimal pure-Python wheel (no pip)."""
    wheel = directory / name
    dist_info = name.removesuffix("-py3-none-any.whl") + ".dist-info"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(LIBRARY_PACKAGE.rglob("*")):
            relative = path.relative_to(LIBRARY_PACKAGE)
            if path.is_file() and "__pycache__" not in relative.parts:
                archive.write(path, f"cli_extended/{relative.as_posix()}")
        archive.writestr(f"{dist_info}/METADATA", "Metadata-Version: 2.1\nName: cli-extended\nVersion: 0.0.0\n")
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    return wheel


@pytest.fixture()
def install_dir(tmp_path):
    """A bootstrap-shaped install dir: entrypoint copy + the installer package."""
    directory = (tmp_path / "install").resolve()
    directory.mkdir()
    shutil.copy2(REAL_ENTRYPOINT, directory / "debian-install-v2.py")
    (directory / "debian_install_v2").symlink_to(PROJECT / "debian_install_v2")
    (tmp_path / "probe.py").write_text(PROBE, encoding="utf-8")
    return directory


@pytest.fixture()
def no_site_python(tmp_path):
    """An interpreter wrapper that cannot see any installed cli_extended."""
    wrapper = tmp_path / "python-no-site"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return str(wrapper)


def run_isolated(script, argv, tmp_path, python):
    """Run a script with an explicit interpreter and no inherited library path."""
    return invoke_script(
        script,
        argv,
        home=tmp_path / "home",
        cwd=tmp_path,
        python=python,
        env={"PYTHONPATH": None, "PYTHONHOME": None},
    )


def probe(install_dir, tmp_path, python, argv=("--version",)):
    return run_isolated(tmp_path / "probe.py", [str(install_dir / "debian-install-v2.py"), *argv], tmp_path, python)


def test_one_wheel_beside_the_entrypoint_is_imported_from_the_wheel(install_dir, tmp_path, no_site_python):
    wheel = build_wheel(install_dir)
    result = probe(install_dir, tmp_path, no_site_python)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "debian-install-v2 2.0.0\n"
        f"CLI_EXTENDED_FILE={wheel}/cli_extended/__init__.py\n"
        "EXIT=0\n"
    )
    assert result.stderr == ""
    assert not (tmp_path / "home" / ".ssh").exists()


def test_the_wheel_beside_the_entrypoint_wins_over_a_library_on_the_path(install_dir, tmp_path):
    wheel = build_wheel(install_dir)
    # Default interpreter, with the library under test on PYTHONPATH as well.
    result = invoke_script(
        tmp_path / "probe.py",
        [str(install_dir / "debian-install-v2.py"), "--version"],
        home=tmp_path / "home",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert f"CLI_EXTENDED_FILE={wheel}/cli_extended/__init__.py\n" in result.stdout
    assert str(LIBRARY_PACKAGE) not in result.stdout


def test_two_wheels_are_refused_with_exit_2_naming_both(install_dir, tmp_path, no_site_python):
    build_wheel(install_dir, "cli_extended-0.0.0-py3-none-any.whl")
    build_wheel(install_dir, "cli_extended-0.0.1-py3-none-any.whl")
    result = run_isolated(install_dir / "debian-install-v2.py", ["--version"], tmp_path, no_site_python)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "[ERROR] debian-install-v2: more than one cli-extended wheel beside the entrypoint; "
        "keep exactly one: cli_extended-0.0.0-py3-none-any.whl, cli_extended-0.0.1-py3-none-any.whl\n"
    )


def test_no_wheel_and_no_installed_library_exits_2_with_the_exact_message(
    install_dir, tmp_path, no_site_python
):
    result = run_isolated(install_dir / "debian-install-v2.py", ["--version"], tmp_path, no_site_python)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == NOT_INSTALLED


def test_the_repository_source_is_never_a_fallback(tmp_path, no_site_python):
    # The real entrypoint sits in a vbpub checkout that has the library source
    # as a sibling project; the old source-tree fallback would have found it.
    assert (PROJECT.parents[1] / "libraries" / "cli-extended" / "src" / "cli_extended").is_dir()
    result = run_isolated(REAL_ENTRYPOINT, ["--version"], tmp_path, no_site_python)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == NOT_INSTALLED


def test_no_wheel_uses_an_installed_library(install_dir, tmp_path):
    # The default invoker puts the library under test where an installed one
    # would be; no wheel sits beside the script.
    result = invoke_script(
        install_dir / "debian-install-v2.py",
        ["--version"],
        home=tmp_path / "home",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "debian-install-v2 2.0.0\n"
    assert result.stderr == ""


def test_a_stray_non_matching_wheel_is_ignored(install_dir, tmp_path, no_site_python):
    (install_dir / "other_tool-1.0-py3-none-any.whl").write_bytes(b"not for us")
    result = run_isolated(install_dir / "debian-install-v2.py", ["--version"], tmp_path, no_site_python)
    assert result.returncode == 2
    assert result.stderr == NOT_INSTALLED


def test_one_real_wheel_plus_a_stray_other_wheel_runs_normally(install_dir, tmp_path, no_site_python):
    build_wheel(install_dir)
    (install_dir / "other_tool-1.0-py3-none-any.whl").write_bytes(b"not for us")
    result = run_isolated(install_dir / "debian-install-v2.py", ["--version"], tmp_path, no_site_python)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "debian-install-v2 2.0.0\n"
    assert result.stderr == ""


def test_wheels_created_in_reverse_name_order_are_listed_sorted(install_dir, tmp_path, no_site_python):
    names = [
        "cli_extended-0.0.2-py3-none-any.whl",
        "cli_extended-0.0.1-py3-none-any.whl",
        "cli_extended-0.0.0-py3-none-any.whl",
    ]
    for name in names:
        build_wheel(install_dir, name)
    result = run_isolated(install_dir / "debian-install-v2.py", ["--version"], tmp_path, no_site_python)
    assert result.returncode == 2
    assert result.stderr.endswith("keep exactly one: " + ", ".join(sorted(names)) + "\n")


def test_the_wheel_is_first_on_sys_path_ahead_of_a_decoy_beside_the_script(
    install_dir, tmp_path, no_site_python
):
    wheel = build_wheel(install_dir)
    decoy = install_dir / "cli_extended"
    decoy.mkdir()
    (decoy / "__init__.py").write_text("raise ImportError('decoy beat the wheel')\n", encoding="utf-8")
    result = probe(install_dir, tmp_path, no_site_python)
    assert result.returncode == 0, result.stderr
    assert f"CLI_EXTENDED_FILE={wheel}/cli_extended/__init__.py\n" in result.stdout
