#!/usr/bin/env python3
"""Build and exercise CMRU's installed-wheel ``get-py`` command."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
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


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cmru-installed-wheel-") as raw_tmp:
        tmp_path = Path(raw_tmp)
        wheelhouse = tmp_path / "wheelhouse"
        wheelhouse.mkdir()
        _run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheelhouse), str(project_root)],
            cwd=tmp_path,
        )
        wheels = list(wheelhouse.glob("cmru-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one CMRU wheel, found {wheels}")

        env_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / "bin" / "python"
        _run([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], cwd=tmp_path)

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
