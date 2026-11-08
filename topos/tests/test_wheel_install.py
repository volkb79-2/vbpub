"""The service installer bootstraps from the two source-built package zips."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path


TOPOS_ROOT = Path(__file__).resolve().parents[1]
CLI_EXTENDED_ROOT = TOPOS_ROOT.parent / "libraries" / "cli-extended"
AUGEASWRAP_ROOT = TOPOS_ROOT.parent / "augeaswrap"


def _package_zip(source: Path, package: str, destination: Path) -> Path:
    package_root = source / package
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, path.relative_to(source).as_posix())
    return destination


BOOTSTRAP = r'''
import grp, runpy, sys, types
topos_zip, cli_extended_zip, augeaswrap_zip, *cli_args = sys.argv[1:]
grp.getgrnam = lambda name: types.SimpleNamespace(gr_name=name, gr_gid=994 if name == "docker" else 991, gr_mem=[])
stdlib = [p for p in sys.path if "site-packages" not in p and "dist-packages" not in p]
sys.path[:] = [topos_zip, cli_extended_zip, augeaswrap_zip, *stdlib]
sys.argv = ["toposd", *cli_args]
code = 1
try:
    runpy.run_module("topos.service.cli", run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
assert not any(name == "topos.daemon" or name.startswith("topos.daemon.") for name in sys.modules)
assert not any(name == "topos.ui" or name.startswith("topos.ui.") for name in sys.modules)
assert not any(name == "topos.collect" or name.startswith("topos.collect.") for name in sys.modules)
assert "textual" not in sys.modules
raise SystemExit(code)
'''


def _run(
    tmp_path: Path, top_zip: Path, cli_zip: Path, augeaswrap_zip: Path, args: list[str]
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    return subprocess.run(
        [sys.executable, "-I", "-c", BOOTSTRAP, str(top_zip), str(cli_zip), str(augeaswrap_zip), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_install_help_and_dry_run_work_before_any_venv_exists(tmp_path: Path) -> None:
    top_zip = _package_zip(TOPOS_ROOT / "src", "topos", tmp_path / "topos.zip")
    cli_zip = _package_zip(CLI_EXTENDED_ROOT / "src", "cli_extended", tmp_path / "cli-extended.zip")
    augeaswrap_zip = _package_zip(AUGEASWRAP_ROOT / "src", "augeaswrap", tmp_path / "augeaswrap.zip")

    help_result = _run(tmp_path, top_zip, cli_zip, augeaswrap_zip, ["install", "--help"])
    assert help_result.returncode == 0, help_result.stderr
    assert "--daemon-lock" in help_result.stdout
    assert "--dry-run" in help_result.stdout

    injected_root = tmp_path / "root"
    injected_root.mkdir()
    digest = "1" * 64
    result = _run(
        tmp_path,
        top_zip,
        cli_zip,
        augeaswrap_zip,
        [
            "install",
            "--dry-run",
            "--root",
            str(injected_root),
            "--wheel",
            f"0.7.0@sha256:{digest}",
            "--cli-extended-wheel",
            f"0.5.3@sha256:{digest}",
            "--daemon-lock",
            f"0.7.0@sha256:{digest}",
            "--wheel-source",
            str(tmp_path),
            "--observe-group",
            "topos-observe",
            "--no-enable",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert "dry run: topos 0.7.0 would be installed" in result.stdout
    assert list(injected_root.iterdir()) == []
