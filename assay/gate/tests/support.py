"""Import point for the tooling tests (``gate/tests``; W4, B123, CD14, CD31).

``gate/`` and ``gate/tests/`` are packages, so pytest imports this tree's
``conftest.py`` as ``gate.tests.conftest`` and never rebinds
``sys.modules["conftest"]``: that name stays the judge's ``tests/conftest.py``
in every collection order. The judge conftest is loaded only on demand here,
under the unique module name ``assay_judge_conftest`` (never ``conftest``), by
the same mechanism ``analysis/tests/conftest.py`` uses (W2, CD13): the module is
loaded once and shared through ``sys.modules``.

The helpers only the tooling tests use (the parent-repository guard and the
wheel-installing ``Standalone``) live here; they left ``tests/conftest.py`` with
the tests that use them.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

#: The `assay/` project directory (`gate/tests/<this file>`).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
assert (PROJECT_ROOT / "pyproject.toml").is_file(), f"no pyproject.toml at {PROJECT_ROOT}"
#: The monorepo checkout above `assay/`.
REPO_ROOT = PROJECT_ROOT.parent

_JUDGE_NAME = "assay_judge_conftest"


def _load_judge_conftest():
    """The judge's ``tests/conftest.py`` as ``assay_judge_conftest``, loaded once."""
    loaded = sys.modules.get(_JUDGE_NAME)
    if loaded is not None:
        return loaded
    path = PROJECT_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location(_JUDGE_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_JUDGE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_JUDGE_NAME]
        raise
    return module


judge = _load_judge_conftest()

GitRepo = judge.GitRepo
why_invalid = judge.why_invalid
runner_verdict_fixture = judge.runner_verdict_fixture
verdict_fixture = judge.verdict_fixture
SCHEMA_PATH = judge.SCHEMA_PATH
#: The judge's B105 coverage-archive hook, under a name that is not a pytest hook
#: name here (a module binding `pytest_sessionfinish` would register it as a hook).
b105_coverage_sessionfinish = judge.pytest_sessionfinish


def _parent_repository_toplevel() -> Path | None:
    """:data:`REPO_ROOT`'s own git work-tree top, or ``None`` when
    :data:`REPO_ROOT` is not the top of a real work tree (B063).

    Asks git rather than looking for a ``.git`` marker, because a linked
    worktree's marker is a gitfile and a submodule's is a redirect — "is
    there a repository here" is git's question to answer, not a filesystem
    heuristic's.

    The toplevel is compared, not merely tested for existence: a module that
    needs THIS monorepo (to read `cmru/assay.toml`, to `git archive` the
    pinned commit, to walk sibling projects) is not served by assay having
    been copied into some unrelated repository's subdirectory, where every
    such read would fail confusingly instead of skipping honestly.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return Path(proc.stdout.strip()).resolve()
    except OSError:  # pragma: no cover - a path git printed but we cannot resolve
        return None


#: (B063) Apply as ``pytestmark`` in any module that shells out to
#: ``git -C REPO_ROOT``. Three modules did so unconditionally, and R-1
#: measured the cost from a ``cp -r`` copy of ``assay/`` outside the vbpub
#: checkout: **11 failed, 13 errors**, constant on every copy, all 24 in
#: those three modules — a noisy floor a real regression would have had to be
#: spotted against, which is exactly what happened during Wave D's mutation
#: testing.
#:
#: **Skip-with-a-named-reason, not resolve-from-context.** The rejected
#: alternative was `git rev-parse --show-toplevel` from ``PROJECT_ROOT`` to
#: find *some* repository to use instead. It was rejected because these
#: modules are testing a property of the CHECKOUT — that assay sits inside
#: the monorepo whose other projects and whose history they read — not a
#: property of assay. When that property is false, "there is no repository
#: here" is the true answer; reaching further up the tree to find a
#: different repository would answer a question nobody asked, and would make
#: the result depend on what happened to be above the copy.
_PARENT_TOPLEVEL = _parent_repository_toplevel()
requires_parent_repository = pytest.mark.skipif(
    _PARENT_TOPLEVEL != REPO_ROOT.resolve(),
    reason=(
        f"this module reads the monorepo checkout at {REPO_ROOT} with real git "
        f"commands, and it is not the top of a git work tree here (git reported "
        f"{_PARENT_TOPLEVEL}); assay is being run from a copy or a bind of the "
        f"project directory alone, so there is no parent repository to read "
        f"(B063)"
    ),
)


# --- assay, built and installed with nothing else present ---------------------
#
# Hoisted from tests/test_dependency_purity.py so P01b's packaging oracle
# and P01a's purity oracle share ONE build; nine more packages would otherwise
# each inherit a copy of a subtle two-environment procedure (A-070).


def _build_backend_home() -> Path:
    """Locate an importable setuptools, by DERIVATION from this interpreter.

    The scratch venv is built with ``--no-build-isolation --no-index`` so that
    nothing is fetched from a network. That needs the build backend to be
    importable from somewhere, and the honest way to find it is to ask the
    interpreters we already have rather than to hardcode a container path.
    """
    probe = "import setuptools, pathlib; print(pathlib.Path(setuptools.__file__).parent.parent)"
    candidates = [Path(sys.executable), Path(sys.base_prefix) / "bin" / "python3"]
    for exe in candidates:
        if not exe.exists():
            continue
        proc = subprocess.run([str(exe), "-c", probe], capture_output=True, text=True)
        if proc.returncode == 0:
            return Path(proc.stdout.strip())
    raise AssertionError(
        f"no interpreter among {candidates} can import setuptools, so the "
        f"offline scratch-venv install cannot be built"
    )


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


@dataclass(frozen=True)
class Standalone:
    """assay, built and installed with nothing else present."""

    venv: Path
    wheel: Path

    def run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.venv / "bin" / argv[0]), *argv[1:]],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
