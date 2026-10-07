#!/usr/bin/env python3
"""Build and exercise CMRU's installed-wheel ``get-py`` command."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


_CONFIG = '''schema_version = 1

[github]
owner = "octocat"
repo = "demo"
owner_type = "user"

[targets]
host = "github"
registry = []

[runtime]
kind = "none"

[project]
id = "demo"
description = "installed wheel acceptance fixture"
prefix = "demo-v"
artifacts = ["tarball"]
template_revision = 1

[project.version]
strategy = "file:VERSION"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"

[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]

[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]

[project.installer]
install_dir_system = "/opt/demo"
install_dir_user = "demo"
'''


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {argv!r}\n{result.stdout}{result.stderr}"
        )


#: The dependency cmru's wheel must declare (floor per the estate version policy).
CLI_EXTENDED_REQUIREMENT = "cli-extended>=0.3.0"


def check_wheel_contents(wheel: Path) -> None:
    """The distribution oracle: the cmru wheel declares cli-extended as a
    dependency and carries NO copy of it (CX-D1), but does carry its own agent
    skill as package data. Raises ``RuntimeError`` on any violation."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"{wheel.name}: expected one METADATA file, found {metadata_names}")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    vendored = [n for n in names if n.split("/", 1)[0] == "cli_extended"]
    if vendored:
        raise RuntimeError(f"{wheel.name} vendors cli_extended files: {vendored[:3]}")
    requires = [line.split(":", 1)[1].strip() for line in metadata.splitlines()
                if line.startswith("Requires-Dist:")]
    if CLI_EXTENDED_REQUIREMENT not in requires:
        raise RuntimeError(f"{wheel.name} lacks Requires-Dist: {CLI_EXTENDED_REQUIREMENT} ({requires})")
    if "cmru/skills/cmru-cli/SKILL.md" not in names:
        raise RuntimeError(f"{wheel.name} does not ship the cmru-cli skill as package data")


def _build_wheel(source: Path, wheelhouse: Path, *, cwd: Path) -> None:
    command = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheelhouse)]
    if os.environ.get("CMRU_SMOKE_NO_BUILD_ISOLATION"):
        # Offline hosts: build with the interpreter's own setuptools/setuptools_scm.
        command.append("--no-build-isolation")
    # An in-tree `pip wheel` leaves `<name>.egg-info/` and `build/` in the source directory.
    # A leftover `cmru.egg-info` is then found by importlib.metadata for any later process
    # started with that directory first on sys.path, so the NEXT lane of the same gate
    # (coverage) would report this build's dev version instead of the image's installed one.
    # Remove only what this build created.
    preexisting = {p for p in (*source.glob("*.egg-info"), source / "build") if p.exists()}
    try:
        _run([*command, str(source)], cwd=cwd)
    finally:
        for created in (*source.glob("*.egg-info"), source / "build"):
            if created.exists() and created not in preexisting:
                shutil.rmtree(created, ignore_errors=True)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cmru-installed-wheel-") as raw_tmp:
        tmp_path = Path(raw_tmp)
        wheelhouse = tmp_path / "wheelhouse"
        wheelhouse.mkdir()
        # cli-extended is a real wheel dependency: build this checkout's wheel into
        # the wheelhouse, the stand-in for the released asset the installers fetch.
        _build_wheel(project_root.parent / "libraries" / "cli-extended", wheelhouse, cwd=tmp_path)
        _build_wheel(project_root, wheelhouse, cwd=tmp_path)
        wheels = list(wheelhouse.glob("cmru-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one CMRU wheel, found {wheels}")
        check_wheel_contents(wheels[0])

        env_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / "bin" / "python"
        # Offline install that RESOLVES the declared dependency from the wheelhouse:
        # never an index (the bare cli-extended name is unclaimed on PyPI, CX-D2).
        _run(
            [str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), str(wheels[0])],
            cwd=tmp_path,
        )
        _run(
            [str(python), "-c",
             "import cli_extended, cmru, sys;"
             "site = [p for p in sys.path if p.endswith('site-packages')][0];"
             "assert cli_extended.__file__.startswith(site), cli_extended.__file__"],
            cwd=tmp_path,
        )

        config = tmp_path / "cmru.toml"
        config.write_text(_CONFIG, encoding="utf-8")
        output = tmp_path / "demo-get.py"
        run_env = os.environ.copy()
        run_env.pop("PYTHONPATH", None)
        run_env["PYTHONNOUSERSITE"] = "1"
        _run(
            [str(env_dir / "bin" / "cmru"), "get-py", "demo", "--config", str(config), "--output", str(output)],
            cwd=tmp_path,
            env=run_env,
        )
        rendered = output.read_text(encoding="utf-8")
        if '"""demo installer' not in rendered or "[[PROJECT_NAME]]" in rendered:
            raise RuntimeError("installed cmru get-py did not render its packaged template")
        compile(rendered, str(output), "exec")

    print("installed wheel get-py acceptance: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
