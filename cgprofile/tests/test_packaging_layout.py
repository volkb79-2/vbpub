"""cgprofile 2.0 package 1: the wheel/package layout (CP-46).

These tests pin the facts the wheel depends on: one importable `cgprofile`
package under `src/` (no top-level `lib`), the two console scripts and their
interim behaviour, the version resolution, the package data, and the
pyproject/cmru facts a release needs.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path
import tomllib

import pytest

from cgprofile import version
from cgprofile.cli import cgprofilectl, cgprofiled, legacy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "src" / "cgprofile"


def _pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ── layout ──────────────────────────────────────────────────────────────────

def test_there_is_no_top_level_lib_package():
    assert not (PROJECT_ROOT / "lib").exists()
    assert not (PROJECT_ROOT / "cgprofile.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("lib.serve")


def test_the_package_lives_under_src_and_is_the_one_imported():
    assert Path(importlib.import_module("cgprofile").__file__).resolve().parent == PACKAGE_DIR
    assert (PACKAGE_DIR / "cli" / "legacy.py").is_file()
    assert (PACKAGE_DIR / "_damon" / "__init__.py").is_file()


@pytest.mark.parametrize("name", ["cgprofiled.service.in", "cgprofiled.toml.default", "cgprofile.slice.in"])
def test_package_data_files_exist_and_are_not_empty(name):
    assert (PACKAGE_DIR / "data" / name).stat().st_size > 0


# ── version resolution ──────────────────────────────────────────────────────

def test_installed_version_reports_the_distribution_metadata(monkeypatch):
    seen = []

    def fake(name):
        seen.append(name)
        return "2.0.0"

    monkeypatch.setattr(importlib.metadata, "version", fake)
    assert version.installed_version() == "2.0.0"
    assert seen == ["cgprofile"]


def test_installed_version_falls_back_to_the_source_tree_marker(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert version.installed_version() == version.SOURCE_TREE_VERSION == "0.0.0-dev"


# ── console script stubs ────────────────────────────────────────────────────

@pytest.fixture
def fixed_version(monkeypatch):
    monkeypatch.setattr(cgprofilectl, "installed_version", lambda: "9.9.9")
    monkeypatch.setattr(cgprofiled, "installed_version", lambda: "9.9.9")


def test_cgprofilectl_version(capsys, fixed_version):
    assert cgprofilectl.main(["--version"]) == 0
    assert capsys.readouterr().out == "cgprofilectl 9.9.9\n"


def test_cgprofiled_version(capsys, fixed_version):
    assert cgprofiled.main(["--version"]) == 0
    assert capsys.readouterr().out == "cgprofiled 9.9.9\n"


def test_the_stubs_read_sys_argv_when_called_without_arguments(monkeypatch, capsys, fixed_version):
    monkeypatch.setattr("sys.argv", ["cgprofilectl", "--version"])
    assert cgprofilectl.main() == 0
    monkeypatch.setattr("sys.argv", ["cgprofiled", "--version"])
    assert cgprofiled.main() == 0
    assert capsys.readouterr().out == "cgprofilectl 9.9.9\ncgprofiled 9.9.9\n"


# ── pyproject and cmru facts ────────────────────────────────────────────────

def test_pyproject_wheel_facts():
    data = _pyproject()
    project = data["project"]
    assert project["name"] == "cgprofile"
    assert project["dynamic"] == ["version"]
    assert "version" not in project
    assert project["dependencies"] == ["cli-extended>=0.5.3", "augeaswrap>=0.1.0"]
    assert project["scripts"] == {
        "cgprofile": "cgprofile.cli.cgprofile:main",
        "cgprofilectl": "cgprofile.cli.cgprofilectl:main",
        "cgprofiled": "cgprofile.cli.cgprofiled:main",
    }
    assert data["tool"]["setuptools"]["packages"]["find"] == {"where": ["src"]}
    assert data["tool"]["setuptools"]["package-data"] == {
        "cgprofile": ["data/*", "skills/*/SKILL.md"]}
    scm = data["tool"]["setuptools_scm"]
    assert scm["root"] == ".."
    assert "cgprofile-v" in scm["tag_regex"]
    assert "fallback_version" not in scm


def test_cmru_declares_the_wheel_only():
    config = tomllib.loads((PROJECT_ROOT / "cmru.toml").read_text(encoding="utf-8"))
    project = config["project"]
    assert project["id"] == "cgprofile"
    assert project["artifacts"] == ["wheel"]
    assert project["scm_dist"] == "cgprofile"
    assert config["project"]["release"]["artifact_dirs"] == ["dist"]
    build = [c["argv"] for c in config["steps"]["build"]["commands"]]
    push = [c["argv"] for c in config["steps"]["push"]["commands"]]
    assert ["cmru", "handler", "wheel-build", "--cwd", "."] in build
    assert ["cmru", "handler", "wheel-publish", "--prefix", "cgprofile", "--cwd", "."] in push
    # The image is retired from the release (CP-54): no image build/push and no
    # second release-body handler (oci-image-release would overwrite the wheel's).
    assert len(build) == 1 and len(push) == 1
    for argv in build + push:
        assert "build-push.py" not in argv and "oci-image-release" not in argv


def test_the_legacy_entry_runs_from_the_checkout_root():
    # SELF is the path the collector child (direct mode) is exec'd by.
    assert Path(legacy.SELF).resolve() == PACKAGE_DIR / "cli" / "legacy.py"


def test_the_one_x_shell_shim_setup_script_and_logtail_are_gone():
    assert not (PROJECT_ROOT / "cgprofile").exists()
    assert not (PROJECT_ROOT / "setup.sh").exists()
    assert not (PACKAGE_DIR / "logtail.py").exists()


# ── one definition of the default endpoint paths (3.0.0) ────────────────────


def test_serve_and_damon_import_the_single_default_from_daemon_config():
    from cgprofile import daemon_config, damon, serve

    assert serve.DEFAULT_SOCKET_PATH is daemon_config.DEFAULT_CONTROL_SOCKET
    assert serve.DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH is daemon_config.DEFAULT_OBSERVER_SOCKET
    assert damon.DAMON_REGISTRY_LOCK_PATH is daemon_config.DEFAULT_DAMON_LOCK_PATH


def test_no_run_cgprofile_path_literal_exists_outside_daemon_config_and_the_old_layout_refusal():
    """A re-literalised default would pass every behavioural test, so the source is searched."""
    offenders = []
    for source in sorted(PACKAGE_DIR.rglob("*.py")):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if "/run/cgprofile/" not in line or source.name == "daemon_config.py":
                continue
            old_layout = source.name == "installer.py" and ("ctl.sock" in line or "resource-observer.sock" in line)
            if not old_layout:
                offenders.append(f"{source.relative_to(PROJECT_ROOT)}:{number}: {line.strip()}")
    assert offenders == []
