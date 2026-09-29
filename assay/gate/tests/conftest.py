"""Fixtures for the tooling tests (``gate/tests``; W4, B123, CD14).

``gate/tests`` is a package, so this file is ``gate.tests.conftest`` and the
judge's ``tests/conftest.py`` keeps the bare module name ``conftest``. Fixtures
the judge already defines are re-bound by name from the judge module that
``gate.tests.support`` loads on demand; there is no star import.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT, Standalone, _build_backend_home, _clean_env, judge

git_repo = judge.git_repo
schema = judge.schema
validator = judge.validator


@pytest.fixture(scope="session")
def standalone(tmp_path_factory) -> Standalone:
    """Build assay's wheel and install it into a venv that has nothing else.

    Build and install are two subprocesses with two different environments, on
    purpose (A-070). The build needs ``setuptools`` on ``PYTHONPATH``; if that
    ``PYTHONPATH`` were also present for the *install*, pip would resolve
    requirements against whatever else happens to live in that directory and a
    declared runtime dependency could be silently considered satisfied — the
    venv would no longer contain "only assay" in the sense the claim needs.
    So the install runs with a clean environment and ``--no-index``: nothing to
    fetch from, nothing to leak in.

    The copied source tree is deliberately ``pyproject.toml`` + ``src/`` +
    ``analysis/src/`` only,
    with no MANIFEST and no VCS plugin available, so a data file reaches the
    wheel ONLY if ``[tool.setuptools.package-data]`` puts it there. That is what
    makes the packaging oracle able to fail.
    """
    tmp = tmp_path_factory.mktemp("standalone")
    source = tmp / "assay-src"
    source.mkdir()
    shutil.copy(PROJECT_ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    # (A-478) The wheel holds the analysis package too: pyproject's
    # `where = ["src", "analysis/src"]` reads it from here.
    shutil.copytree(
        PROJECT_ROOT / "analysis" / "src",
        source / "analysis" / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )

    venv = tmp / "venv"
    base = Path(sys.base_prefix) / "bin" / "python3"
    creator = str(base if base.exists() else sys.executable)
    subprocess.run([creator, "-m", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    assert python.exists(), "the fresh venv has no interpreter"

    wheels = tmp / "wheels"
    build_env = _clean_env()
    build_env["PYTHONPATH"] = str(_build_backend_home())
    built = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            str(source),
        ],
        capture_output=True,
        text=True,
        env=build_env,
    )
    assert built.returncode == 0, f"wheel build failed:\n{built.stdout}\n{built.stderr}"
    candidates = sorted(wheels.glob("assay-*.whl"))
    assert len(candidates) == 1, f"expected one assay wheel, got {candidates}"

    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", str(candidates[0])],
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert installed.returncode == 0, (
        "offline install failed — with zero runtime dependencies there is "
        f"nothing to resolve:\n{installed.stdout}\n{installed.stderr}"
    )
    return Standalone(venv=venv, wheel=candidates[0])
