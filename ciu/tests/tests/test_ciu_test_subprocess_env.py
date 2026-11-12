"""CIU Python children keep source imports when tests use a foreign cwd."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conftest as suite_conftest  # noqa: E402


def test_python_child_env_anchors_source_paths_for_a_foreign_cwd(tmp_path: Path) -> None:
    project_root = Path(suite_conftest.__file__).resolve().parents[1]
    inherited = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": os.pathsep.join(
            ("src", "../cmru/src", "../libraries/worktree/src")
        ),
    }

    env = suite_conftest.python_child_env(inherited)
    paths = env["PYTHONPATH"].split(os.pathsep)
    source_root = (project_root / "src").resolve()

    child = subprocess.run(
        [sys.executable, "-c", "import ciu; print(ciu.__file__)"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert all(Path(path).is_absolute() for path in paths)
    assert str(source_root) in paths
    assert all(
        Path(path).is_absolute()
        for path in os.environ["PYTHONPATH"].split(os.pathsep)
    )
    assert Path(child.stdout.strip()).resolve() == source_root / "ciu" / "__init__.py"
