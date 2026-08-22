"""P84 — the gate environment is declared, and the gate knows about it.

The bug this guards: ``mcp`` was a declared optional extra whose absence hid 16
tests behind a module-level ``importorskip``, while the gate only knew about
``zstandard``. Any *new* optional extra would repeat that silently. These tests
tie the three places that must agree — the declared extras, the ``[dev]`` extra
that builds the gate env, and the conftest gate — so they cannot drift apart.

P96 additions: py-compile gate argv behavioural tests, worktree import path
validation, gate-tool importability. The py-compile argv is parsed from
run-gate.toml ([lanes.py-compile]) — the SSOT since run-gate adoption
(D-110/D-111); nyxloom.toml [gates.py-compile] is only a thin pointer at it.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from conftest import _REQUIRED_TEST_EXTRAS

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
GATE_TOML = Path(__file__).resolve().parents[1] / "run-gate.toml"


def _optional_dependencies() -> dict[str, list[str]]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_dev_extra_installs_every_other_optional_extra() -> None:
    """``pip install -e 'topos[dev]'`` must build a gate env that reaches
    every optional code path — that is the whole contract of the [dev] extra."""
    optional = _optional_dependencies()
    dev_requirements = set(optional["dev"])
    for extra in optional:
        if extra == "dev":
            continue
        assert f"topos[{extra}]" in dev_requirements, (
            f"optional extra {extra!r} is not installed by 'topos[dev]', so the "
            f"documented gate environment cannot reach the code it guards"
        )


def test_gate_knows_about_every_optional_extra() -> None:
    """Every declared extra is checked by the conftest gate.

    Without this, adding an extra + tests that skip without it reproduces the
    exact defect P84 exists to remove: a green suite that never ran them.
    """
    optional = _optional_dependencies()
    declared = {extra for extra in optional if extra != "dev"}
    gated = {extra_name for _, extra_name in _REQUIRED_TEST_EXTRAS}
    assert declared == gated, (
        f"extras declared in pyproject.toml but not gated in conftest: "
        f"{sorted(declared - gated)}; gated but not declared: {sorted(gated - declared)}"
    )


def test_pytest_cov_and_xdist_are_importable() -> None:
    """P96: pytest-cov (pytest_cov) and pytest-xdist (xdist) are project-owned
    gate tools the tester-unified gate environment supplies. This test guards
    against accidental removal or a gate-image rebuild that drops them."""
    import pytest_cov  # noqa: F401
    import xdist  # noqa: F401


# --------------------------------------------------------------------------- #
# P96 — py-compile gate argv tested against real temporary git repos
# --------------------------------------------------------------------------- #

def _pycompile_argv() -> list[str]:
    """Parse the actual py-compile argv from run-gate.toml (the SSOT)."""
    with GATE_TOML.open("rb") as fh:
        cfg = tomllib.load(fh)
    raw = cfg["lanes"]["py-compile"]["argv"]
    assert isinstance(raw, list), f"py-compile argv is not a list: {type(raw)}"
    return [str(s) for s in raw]


def _run_pycompile_in_repo(repo: Path) -> subprocess.CompletedProcess:
    """Execute the actual py-compile gate argv in *repo* (serves as {worktree})."""
    argv = _pycompile_argv()
    # argv = ["bash", "-c", "cd {worktree} && set -o pipefail && git diff ..."]
    shell_cmd = argv[2] if len(argv) >= 3 and argv[1] == "-c" else argv[1]
    # Substitute {worktree} with the repo path
    resolved = shell_cmd.replace("{worktree}", str(repo))
    return subprocess.run(
        ["bash", "-c", resolved],
        capture_output=True, text=True,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, check=True)


def test_pycompile_syntax_error_exits_nonzero() -> None:
    """The py-compile gate argv (parsed from nyxloom.toml) must exit nonzero
    when a changed .py file has a syntax error."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _git(repo, "init", "--initial-branch", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "T")
        (repo / "good.py").write_text("x = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "init")
        _git(repo, "checkout", "-b", "feature")
        # Add a file with syntax error
        (repo / "bad.py").write_text("def foo(:\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "bad")

        proc = _run_pycompile_in_repo(repo)
        assert proc.returncode != 0, (
            f"py-compile should fail on syntax error, got exit {proc.returncode}: "
            f"stdout={proc.stdout}, stderr={proc.stderr}"
        )


def test_pycompile_no_changed_files_exits_zero() -> None:
    """The py-compile gate argv exits zero when no .py files have changed."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _git(repo, "init", "--initial-branch", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "T")
        (repo / "good.py").write_text("x = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "init")
        _git(repo, "checkout", "-b", "feature")
        # No .py files changed — only a .txt
        (repo / "note.txt").write_text("hello\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "feature")

        proc = _run_pycompile_in_repo(repo)
        assert proc.returncode == 0, (
            f"py-compile with no changed .py should exit 0, got {proc.returncode}: "
            f"stdout={proc.stdout}, stderr={proc.stderr}"
        )


def test_pycompile_filename_with_spaces_succeeds() -> None:
    """The NUL-delimited git diff -z | xargs -0 -r pattern handles filenames
    containing spaces without shell-splitting."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _git(repo, "init", "--initial-branch", "main")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "T")
        (repo / "good.py").write_text("x = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "init")
        _git(repo, "checkout", "-b", "feature")
        # Filename with spaces
        (repo / "my file.py").write_text("y = 2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "spaced")

        proc = _run_pycompile_in_repo(repo)
        assert proc.returncode == 0, (
            f"py-compile with spaced filename should exit 0, got {proc.returncode}: "
            f"stdout={proc.stdout}, stderr={proc.stderr}"
        )


# --------------------------------------------------------------------------- #
# P96 — imported topos must resolve under the worktree, not image-baked /src
# --------------------------------------------------------------------------- #

def test_topos_imported_from_worktree() -> None:
    """When PYTHONPATH=topos/src is set and CWD is the vbpub repo root, the
    imported topos package must resolve under the CURRENT worktree, not from
    an image-baked /src/topos path. The gate command must export PYTHONPATH
    for this to hold under tester-unified.

    This test runs in the cockpit (devcontainer) and uses the file's own
    location to derive the expected source tree. In the gate container with
    a bound worktree, the same check rejects stale baked imports.
    """
    import topos
    f = topos.__file__
    assert f is not None, "topos.__file__ must be set"

    resolved = Path(f).resolve()
    expected_parent = Path(__file__).resolve().parents[1] / "src" / "topos"

    # The resolved path must be a child of the expected source tree
    try:
        resolved.relative_to(expected_parent)
    except ValueError:
        raise AssertionError(
            f"topos was imported from {resolved}, which is not under "
            f"the expected source tree {expected_parent}. "
            f"This means PYTHONPATH=topos/src was NOT exported (or the test "
            f"is running from an image-baked /src/topos location)."
        )
