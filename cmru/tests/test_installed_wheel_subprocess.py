"""Subprocess tests for the installed-wheel world (W2-PKG4, KI-51).

cmru depends on the RELEASED cli-extended wheel: no test here (or in the gate
environment) puts ``libraries/cli-extended/src`` on a ``PYTHONPATH``. The two
module-entry tests moved out of ``test_cli_dispatch.py`` run against the
interpreter's installed ``cli_extended`` through the library's hermetic
``invoke_module`` (``home`` is required, AC-23); the bootstrap tests cover
``build-initial-standalone.sh``'s verified-wheel step with a fake interpreter.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cli_extended.testing import invoke_module

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
BOOTSTRAP = PROJECT_DIR / "build-initial-standalone.sh"


def _own_sources() -> list[Path]:
    """cmru's source and the vendored-in-wheel ``worktree`` library: the two
    roots a source checkout legitimately puts on the path. cli_extended is NOT
    one of them."""
    return [PROJECT_DIR / "src", REPO_ROOT / "libraries" / "worktree" / "src"]


def test_removed_module_console_dispatch_alias_refuses_version(tmp_path):
    proc = invoke_module("cmru.cli", ["--version"], home=tmp_path, pythonpath=_own_sources())
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == (
        "Use the installed 'cmru' command; python -m cmru.cli is not supported.\n"
    )


def test_source_module_invocation_works_from_the_cmru_project_directory(tmp_path):
    proc = invoke_module(
        "cmru.handlers", ["--help"], home=tmp_path, cwd=PROJECT_DIR, pythonpath=_own_sources(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "wheel-build" in proc.stdout


def test_module_invocations_import_cli_extended_from_the_installed_distribution(tmp_path):
    """The child's cli_extended is the installed wheel's package, not a source
    tree under the repository (CX-D1: no vendored/editable library mapping)."""
    probe = subprocess.run(
        [sys.executable, "-c", "import cli_extended; print(cli_extended.__file__)"],
        capture_output=True, text=True, check=True, cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, _own_sources()))},
    )
    location = Path(probe.stdout.strip()).resolve()
    assert REPO_ROOT / "libraries" / "cli-extended" not in location.parents, location


# --- build-initial-standalone.sh: the verified cli-extended wheel (D8-style, sha256) ---

_WHEEL_NAME = "cli_extended-0.2.0-py3-none-any.whl"


def _toy_wheel(directory: Path) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / _WHEEL_NAME
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("cli_extended/__init__.py", "MARKER = 'released-wheel'\n")
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


def _bootstrap_tree(tmp_path: Path) -> dict[str, object]:
    # A throw-away tree with its own git history: the gate fixtures copy the
    # project without .git, so the real checkout cannot be assumed here.
    repo_root = (tmp_path / "tree").resolve()
    (repo_root / "cmru").mkdir(parents=True)
    bootstrap = repo_root / "cmru" / "build-initial-standalone.sh"
    bootstrap.write_text(BOOTSTRAP.read_text(encoding="utf-8"), encoding="utf-8")
    bootstrap.chmod(0o755)
    (repo_root / "cmru" / "pyproject.toml").write_text(
        (PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8",
    )
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2023-11-14T22:13:20+00:00",
        "GIT_COMMITTER_DATE": "2023-11-14T22:13:20+00:00",
    }
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True, env=commit_env,
    )
    record = tmp_path / "record.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    # Records the handlers launch (the one call under test) and runs every other
    # interpreter call (floor read, digest, unpack) with the real interpreter.
    python = fake_bin / "fakepython"
    python.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"-m cmru.handlers"*)\n'
        '    { echo "ARGS=$*"; echo "PP=$PYTHONPATH"; echo "EPOCH=$SOURCE_DATE_EPOCH"\n'
        '      second="$(echo "$PYTHONPATH" | cut -d: -f2)"\n'
        '      if [ -f "$second/cli_extended/__init__.py" ]; then echo "CX=$(cat "$second/cli_extended/__init__.py")"; fi\n'
        f'    }} > "{record}"\n'
        "    exit 0;;\n"
        "esac\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CMRU_BOOTSTRAP_PYTHON": "fakepython",
        "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:test",
        "CMRU_BOOTSTRAP_CGROUP_PARENT": "test.slice",
        "SOURCE_DATE_EPOCH": "1",
        "TMPDIR": str(tmp_path),
    }
    env.pop("CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL", None)
    env.pop("CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256", None)
    return {"repo_root": repo_root, "bootstrap": bootstrap, "record": record, "env": env}


def _run_bootstrap(tree) -> subprocess.CompletedProcess[str]:
    # The stub builds nothing, so the script's final wheel check fails; the
    # recorded launch is what is under test.
    return subprocess.run(
        ["bash", str(tree["bootstrap"])], env=tree["env"], capture_output=True, text=True, check=False,
    )


def _record(tree) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in Path(tree["record"]).read_text(encoding="utf-8").splitlines()
    )


def test_fresh_checkout_bootstrap_is_the_only_source_build_launcher():
    bootstrap = BOOTSTRAP

    assert not (REPO_ROOT / "cmru.py").exists()
    assert bootstrap.is_file()
    assert os.access(bootstrap, os.X_OK)
    source = bootstrap.read_text(encoding="utf-8")
    assert "python3 -m cmru.handlers" not in source
    assert "-m cmru.handlers wheel-build" in source
    # BG-10: -s and an epoch; cli_extended is the verified RELEASED wheel, never a source root.
    assert "libraries/cli-extended/src" not in source
    assert "libraries/worktree/src" in source
    assert '"${python_bin}" -s -m cmru.handlers' in source
    assert "CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256" in source and "fetch-cli-extended.py" in source
    assert "SOURCE_DATE_EPOCH" in source and "log -1 --format=%ct" in source
    assert "CMRU_DOCKER_CGROUP_PARENT" in source
    assert 'CMRU_WHEEL_BUILDER_IMAGE:-wheel-builder:local' not in source
    assert 'CMRU_WHEEL_BUILDER_IMAGE' in source


def test_bootstrap_runs_python_isolated_with_the_verified_released_wheel_and_commit_epoch(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)

    _run_bootstrap(tree)

    lines = _record(tree)
    assert lines["ARGS"].startswith("-s -m cmru.handlers wheel-build")
    roots = lines["PP"].split(os.pathsep)
    repo_root = tree["repo_root"]
    assert roots[0] == str(repo_root / "cmru" / "src")
    assert roots[2] == str(repo_root / "libraries" / "worktree" / "src")
    assert str(repo_root / "libraries" / "cli-extended" / "src") not in roots
    # The middle root is the staged unpack of the verified wheel, and it held the release's package.
    assert lines["CX"] == "MARKER = 'released-wheel'"
    # The staging directory does not outlive the script.
    assert not Path(roots[1]).exists()
    assert lines["EPOCH"] == "1700000000"


def test_bootstrap_refuses_a_wheel_whose_sha256_does_not_match_before_building(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=wrong)

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "sha256 mismatch" in result.stderr
    assert not Path(tree["record"]).exists()  # cmru.handlers was never launched


@pytest.mark.parametrize("digest", ["", "abc", "A" * 64])
def test_bootstrap_requires_a_valid_digest_with_a_supplied_wheel(tmp_path, digest):
    tree = _bootstrap_tree(tmp_path)
    wheel, _ = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256" in result.stderr
    assert not Path(tree["record"]).exists()


def test_bootstrap_without_a_supplied_wheel_delegates_to_the_digest_verifying_fetcher(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, _ = _toy_wheel(tmp_path / "release")
    fetcher = tree["repo_root"] / "tester-unified" / "fetch-cli-extended.py"
    fetcher.parent.mkdir()
    args = tmp_path / "fetch-args.txt"
    fetcher.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(args)!r}).write_text(' '.join(sys.argv[1:]))\n"
        f"print({str(wheel)!r})\n",
        encoding="utf-8",
    )

    _run_bootstrap(tree)

    fetched = args.read_text(encoding="utf-8").split()
    assert fetched[fetched.index("--min-version") + 1] == "0.2.0"  # the floor from cmru's pyproject
    assert "--dest" in fetched
    assert _record(tree)["CX"] == "MARKER = 'released-wheel'"


def test_bootstrap_stops_when_the_fetcher_cannot_deliver_a_wheel(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    fetcher = tree["repo_root"] / "tester-unified" / "fetch-cli-extended.py"
    fetcher.parent.mkdir()
    fetcher.write_text("raise SystemExit('no network')\n", encoding="utf-8")

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "could not fetch the released cli-extended wheel" in result.stderr
    assert not Path(tree["record"]).exists()
