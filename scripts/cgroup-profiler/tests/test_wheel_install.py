"""Build the real wheel, install it into a clean venv, run the console scripts.

This is the lane the layout facts cannot replace: it proves the wheel is
self-contained (the vendored DAMON module, the package data and the entry
points are inside it), that `cgpctl` and `cgpd` run from an installed copy with
no checkout on the path, and that no build-host path leaked into the artifact.

The build runs from a COPY of pyproject.toml, README.md, NOTICE and src/ in a
temp directory (pip builds in-tree and must not write into the checkout, which
the gate mounts read-only). The version is pinned with setuptools-scm's pretend
variable because the copy has no git metadata; this also keeps the lane
independent of tags. The venv is created with `--system-site-packages`-free
defaults and the wheel is installed with `--no-deps --no-index`: package 1's
code imports only the standard library, and the metadata test below asserts the
declared closure instead of resolving it over the network.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRETEND = "2.0.0.dev0"

_BUILD_TOOLS = ("setuptools", "wheel", "setuptools_scm")
_MISSING = [m for m in _BUILD_TOOLS if importlib.util.find_spec(m) is None]

pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"wheel build tooling absent in this environment: {_MISSING}",
)


def _run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kwargs)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel-build")
    tree = work / "tree"
    tree.mkdir()
    for name in ("pyproject.toml", "README.md", "NOTICE"):
        shutil.copy2(PROJECT_ROOT / name, tree / name)
    shutil.copytree(
        PROJECT_ROOT / "src", tree / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    out = work / "dist"
    env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CGPROFILE=PRETEND)
    proc = _run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--disable-pip-version-check", "-w", str(out), str(tree)],
        env=env, cwd=str(work),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    built = sorted(out.glob("cgprofile-*.whl"))
    assert [p.name for p in built] == [f"cgprofile-{PRETEND}-py3-none-any.whl"]
    return built[0]


@pytest.fixture(scope="module")
def cli_extended_wheel(tmp_path_factory) -> Path:
    """cli-extended built from THIS checkout (the library directory below the repository root), offline.

    cgpctl imports cli_extended for real, and the gate image may carry an older release than the pin
    (0.5.0 against >=0.5.1) with no index to resolve from. The clean venv therefore gets the checkout's own
    cli-extended as a wheel, built the same way as the cgprofile wheel above (copied tree, pretend version).
    """
    source = PROJECT_ROOT.parents[1] / "libraries" / "cli-extended"
    assert (source / "pyproject.toml").is_file(), f"cli-extended source missing at {source}"
    work = tmp_path_factory.mktemp("cli-extended-build")
    tree = work / "tree"
    tree.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(source / name, tree / name)
    shutil.copytree(source / "src", tree / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    out = work / "dist"
    env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED="0.5.1")
    proc = _run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--disable-pip-version-check", "-w", str(out), str(tree)],
        env=env, cwd=str(work),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    built = sorted(out.glob("cli_extended-*.whl"))
    assert len(built) == 1, built
    return built[0]


@pytest.fixture(scope="module")
def venv(wheel, cli_extended_wheel, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("clean-venv") / "venv"
    proc = _run([sys.executable, "-m", "venv", str(root)])
    assert proc.returncode == 0, proc.stderr
    # No PYTHONPATH: the gate puts libraries/cli-extended/src on it, and pip would then see cli-extended as
    # "already satisfied" and install nothing into the venv.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    for artefact in (cli_extended_wheel, wheel):
        pip = _run(
            [str(root / "bin" / "python"), "-m", "pip", "install", "--no-index", "--no-deps",
             "--disable-pip-version-check", str(artefact)],
            env=env,
        )
        assert pip.returncode == 0, pip.stdout + pip.stderr
    return root


def test_the_wheel_ships_the_package_the_data_and_the_vendored_module(wheel):
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    for required in (
        "cgprofile/__init__.py",
        "cgprofile/serve.py",
        "cgprofile/cli/cgpctl.py",
        "cgprofile/cli/cgpd.py",
        "cgprofile/cli/legacy.py",
        "cgprofile/_damon/__init__.py",
        "cgprofile/_damon/damon_analysis.py",
        "cgprofile/data/cgpd.service.in",
        "cgprofile/data/cgpd.toml.default",
        "cgprofile/data/cgprofile.slice.in",
        f"cgprofile-{PRETEND}.dist-info/METADATA",
        f"cgprofile-{PRETEND}.dist-info/entry_points.txt",
    ):
        assert required in names, required
    assert not any(n.startswith(("lib/", "tests/")) for n in names)
    assert not any(n.endswith(".pyc") or "__pycache__" in n for n in names)
    assert any(n.endswith("/licenses/NOTICE") or n.endswith("/NOTICE") for n in names)


def test_the_wheel_metadata_declares_the_final_closure(wheel):
    with zipfile.ZipFile(wheel) as z:
        meta = z.read(f"cgprofile-{PRETEND}.dist-info/METADATA").decode()
        entry_points = z.read(f"cgprofile-{PRETEND}.dist-info/entry_points.txt").decode()
    lines = meta.splitlines()
    assert "Name: cgprofile" in lines
    assert f"Version: {PRETEND}" in lines
    unconditional = [l for l in lines if l.startswith("Requires-Dist:") and "extra ==" not in l]
    assert unconditional == ["Requires-Dist: cli-extended>=0.5.1"]
    assert "cgpctl = cgprofile.cli.cgpctl:main" in entry_points
    assert "cgpd = cgprofile.cli.cgpd:main" in entry_points


def test_no_checkout_or_build_host_path_leaks_into_the_wheel(wheel):
    # The two roots a build can bake in: the project checkout and the temp
    # build tree. Source prose may mention workspace paths in docstrings; that
    # is text, not a leaked build input.
    forbidden = [str(PROJECT_ROOT), str(wheel.parent.parent)]
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            try:
                text = z.read(info.filename).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for needle in forbidden:
                assert needle not in text, f"{needle!r} in {info.filename}"


@pytest.mark.parametrize("script", ["cgpctl", "cgpd"])
def test_the_installed_console_script_reports_the_wheel_version(venv, script, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CGPROFILE_VERSION")}
    proc = _run([str(venv / "bin" / script), "--version"], env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{script} {PRETEND}\n"


def test_the_installed_package_imports_from_site_packages_only(venv, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CGPROFILE_VERSION")}
    code = (
        "import cgprofile, cgprofile._damon.damon_analysis as d, cgprofile.damon as m;"
        "print(cgprofile.__file__);print(d.__file__);print(m.available())"
    )
    proc = _run([str(venv / "bin" / "python"), "-I", "-c", code], env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    first, second, third = proc.stdout.splitlines()
    assert "site-packages" in first and "site-packages" in second
    assert str(PROJECT_ROOT) not in proc.stdout
    assert third in ("True", "False")
