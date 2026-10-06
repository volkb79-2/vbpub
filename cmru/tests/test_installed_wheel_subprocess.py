"""Subprocess tests for the installed-wheel world (W2-PKG4, KI-51).

cmru depends on the RELEASED cli-extended wheel: no test here (or in the gate
environment) puts the cli-extended library source on an import path. The two
module-entry tests moved out of ``test_cli_dispatch.py`` run against the
interpreter's installed ``cli_extended`` through the library's hermetic
``invoke_module`` (``home`` is required, AC-23); the bootstrap tests cover
``build-initial-standalone.sh``'s verified-wheel step with a fake interpreter.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cli_extended.testing import invoke_module

from tests._cx_paths import CX_SOURCE

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
    # D2: the version is installed metadata only. The child inherits this
    # process's environment, so whether the cmru distribution is installed is
    # decided HERE and the outcome is exact (no either-or): the gate image bakes
    # cmru (`+tester.unified`), so the lane gets exit 0 plus help; an environment
    # without the distribution gets exit 3 with one line and no traceback.
    from importlib.metadata import PackageNotFoundError, version

    try:
        version("cmru")
        installed = True
    except PackageNotFoundError:
        installed = False
    assert (proc.returncode == 0) is installed, proc.stderr
    if not installed:
        assert proc.returncode == 3
        assert proc.stderr.strip() == (
            "cmru is not installed as a distribution; install the wheel (see README)"
        )
        assert "Traceback" not in proc.stderr
    else:
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
    subprocess.run(["git", "-C", str(repo_root), "tag", "cmru-v1.2.3"], check=True)
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
        # Optional probe: run code in a REAL bare interpreter with the exact PYTHONPATH
        # the handlers launch got (the staged unpack + dist-info is all it can see).
        '    if [ -n "$CMRU_TEST_PROBE_PY" ]; then\n'
        '      "$CMRU_TEST_PROBE_PY" -s -c "$CMRU_TEST_PROBE_CODE" > "$CMRU_TEST_PROBE_OUT" 2>&1 || true\n'
        "    fi\n"
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
    assert CX_SOURCE not in source
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


# --- B1/D-1: the bootstrap stages a cmru distribution identity (D2 has no source fallback) ---

def _real_cli_extended_wheel(directory: Path) -> tuple[Path, str]:
    """A wheel carrying the REAL installed ``cli_extended`` package (the released
    0.2.0 in this environment), so the probe below runs the real module."""
    import cli_extended

    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / _WHEEL_NAME
    package = Path(cli_extended.__file__).resolve().parent
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, f"cli_extended/{path.relative_to(package).as_posix()}")
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


_PROBE = """
import sys
import importlib.metadata as metadata
sys.path += [{src!r}, {worktree!r}]
print("DIST=" + metadata.version("cmru"))
print("NAME=" + metadata.distribution("cmru").metadata["Name"])
import cli_extended
print("CX=" + cli_extended.__file__)
from cmru import cli_support
registry = cli_support.cmru_registry("cmru", "bootstrap probe")
print("IDENTITY=" + registry.identity.version)
"""


def _bare_interpreter() -> Path:
    """A real interpreter with neither cmru nor cli_extended installed."""
    candidate = Path(sys.base_prefix) / "bin" / "python3"
    if not candidate.exists():
        pytest.skip("no base interpreter to act as the bare bootstrap interpreter")
    pristine = subprocess.run(
        [str(candidate), "-s", "-c", "import cli_extended"], capture_output=True, text=True, check=False,
    )
    if pristine.returncode == 0:
        pytest.skip("the base interpreter already has cli_extended installed")
    return candidate


def _probe_bootstrap(tmp_path: Path, **extra_env: str) -> tuple[dict[str, str], str]:
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _real_cli_extended_wheel(tmp_path / "release")
    probe_out = tmp_path / "probe.txt"
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel),
        CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
        CMRU_TEST_PROBE_PY=str(_bare_interpreter()),
        CMRU_TEST_PROBE_CODE=_PROBE.format(
            src=str(PROJECT_DIR / "src"), worktree=str(REPO_ROOT / "libraries" / "worktree" / "src"),
        ),
        CMRU_TEST_PROBE_OUT=str(probe_out),
        **extra_env,
    )
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU", "PYTHONPATH"):
        if name not in extra_env:
            tree["env"].pop(name, None)
    _run_bootstrap(tree)
    raw = probe_out.read_text(encoding="utf-8") if probe_out.exists() else ""
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line), raw


def test_bootstrap_stages_a_cmru_dist_info_so_the_real_registry_builds_in_a_bare_interpreter(tmp_path):
    # Control: the bare interpreter really has no cmru distribution of its own.
    control = subprocess.run(
        [str(_bare_interpreter()), "-s", "-c",
         "import importlib.metadata as m; m.version('cmru')"],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    assert control.returncode != 0 and "PackageNotFoundError" in control.stderr

    lines, raw = _probe_bootstrap(tmp_path)

    # tag cmru-v1.2.3 on HEAD: the exact tag version, as setuptools-scm would derive it.
    assert lines.get("DIST") == "1.2.3", raw
    assert lines.get("NAME") == "cmru", raw  # PKG-4 N16: the staged dist-info names cmru
    assert lines.get("IDENTITY") == "1.2.3", raw
    assert "site-packages" not in lines["CX"], raw


def test_bootstrap_derives_the_dev_version_like_the_wheel_build_beyond_the_tag(tmp_path):
    # One empty commit past the tag: next patch, .devN+gHASH (setuptools-scm shape).
    tree = _bootstrap_tree(tmp_path)
    subprocess.run(
        ["git", "-C", str(tree["repo_root"]), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "more"],
        check=True,
    )
    wheel, digest = _real_cli_extended_wheel(tmp_path / "release")
    probe_out = tmp_path / "probe.txt"
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
        CMRU_TEST_PROBE_PY=str(_bare_interpreter()), CMRU_TEST_PROBE_OUT=str(probe_out),
        CMRU_TEST_PROBE_CODE="import importlib.metadata as m; print(m.version('cmru'))",
    )
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU", "PYTHONPATH"):
        tree["env"].pop(name, None)

    _run_bootstrap(tree)

    head = subprocess.run(
        ["git", "-C", str(tree["repo_root"]), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert re.fullmatch(rf"1\.2\.4\.dev1\+g{head[:7]}\w*", probe_out.read_text("utf-8").strip())


def test_bootstrap_honours_the_scm_pretend_version_override(tmp_path):
    lines, raw = _probe_bootstrap(tmp_path, SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU="9.8.7")
    assert lines.get("DIST") == "9.8.7" and lines.get("IDENTITY") == "9.8.7", raw


def test_bootstrap_refuses_when_no_cmru_version_can_be_derived(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    subprocess.run(["git", "-C", str(tree["repo_root"]), "tag", "-d", "cmru-v1.2.3"], check=True, capture_output=True)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU"):
        tree["env"].pop(name, None)

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "cannot derive the cmru version" in result.stderr
    assert not Path(tree["record"]).exists()
