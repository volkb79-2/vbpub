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
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cli_extended.testing import invoke_module

from tests._cx_paths import library_source_uses

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

_WHEEL_NAME = "cli_extended-0.3.0-py3-none-any.whl"


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
    # The script reads the wheel builder's base from the real Dockerfile (committed with the tree).
    (repo_root / "wheel-builder").mkdir()
    (repo_root / "wheel-builder" / "Dockerfile").write_text(
        (REPO_ROOT / "wheel-builder" / "Dockerfile").read_text(encoding="utf-8"), encoding="utf-8",
    )
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2023-11-14T22:13:20+00:00",
        "GIT_COMMITTER_DATE": "2023-11-14T22:13:20+00:00",
    }
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    # The script refuses to build from bytes that differ from HEAD, so the fixture's
    # project files are COMMITTED (the tag still names this one commit).
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=True)
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
    docker_log = tmp_path / "docker.log"
    builder_marker = tmp_path / "wheel-builder-ready"
    order_log = tmp_path / "order.log"
    tester_base_marker = tmp_path / "tester-base-ready"
    docker.write_text(
        "#!/bin/sh\n"
        f'printf "docker %s\\n" "$*" >> "{order_log}"\n'
        # tester-base:local is present unless CMRU_TEST_NO_TESTER_BASE is set; building it makes it appear.
        'if [ "$1" = "image" ] && [ "$2" = "inspect" ] && [ "$3" = "tester-base:local" ]; then\n'
        f'  if [ -n "$CMRU_TEST_NO_TESTER_BASE" ] && [ ! -f "{tester_base_marker}" ]; then exit 1; fi\n'
        '  exit 0\n'
        'fi\n'
        'case "$*" in *"-t tester-base:local"*)\n'
        f'  printf "%s\\n" "$*" >> "{docker_log}"; touch "{tester_base_marker}"; exit 0;;\n'
        'esac\n'
        'if [ "$1" = "buildx" ] && [ "$2" = "build" ]; then\n'
        f'  printf "%s\\n" "$*" >> "{docker_log}"\n'
        f'  touch "{builder_marker}"\n'
        '  exit 0\n'
        'fi\n'
        # The host-systemd slice probe (`docker run ... systemctl show <slice>`): only the slices
        # named in CMRU_TEST_LOADED_SLICES are real units; any other name answers the way systemd
        # does for a typo (LoadState=loaded, no FragmentPath: an unlimited transient slice).
        'case "$*" in *systemctl*)\n'
        '  slice=""; prev=""\n'
        '  for arg in "$@"; do if [ "$prev" = show ]; then slice="$arg"; fi; prev="$arg"; done\n'
        '  case " $CMRU_TEST_LOADED_SLICES " in\n'
        '    *" $slice "*) printf "LoadState=loaded\\nFragmentPath=/etc/systemd/system/%s\\n" "$slice";;\n'
        '    *) printf "LoadState=loaded\\nFragmentPath=\\n";;\n'
        '  esac\n'
        '  exit 0;;\n'
        'esac\n'
        'if [ "$1" = "image" ]; then\n'
        f'  if [ -f "{builder_marker}" ]; then echo python-wheel-builder; else echo wrong-image; fi\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    # Records the handlers launch (the one call under test) and runs every other
    # interpreter call (floor read, digest, unpack) with the real interpreter.
    python = fake_bin / "fakepython"
    python.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        # Source mode: the cli-extended wheel-build call. The real handler needs docker (faked
        # above), so this stand-in records the call and builds the same wheel with the real
        # interpreter's pip; the handler's own contract (dist/ in --cwd) is what it reproduces.
        '  *"-m cmru.handlers wheel-build --cwd "*"/cli-extended")\n'
        '    lib="${*##*--cwd }"\n'
        f'    {{ echo "CXB_ARGS=$*"; echo "CXB_PP=$PYTHONPATH"; echo "CXB_VERSION=$SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED"\n'
        '      echo "CXB_EPOCH=$SOURCE_DATE_EPOCH"; echo "CXB_IMAGE=$CMRU_WHEEL_BUILDER_IMAGE"\n'
        f'    }} > "{record}.cxbuild"\n'
        f'    rm -rf "$lib/dist"; PYTHONNOUSERSITE=1 exec "{sys.executable}" -m pip wheel --no-deps --no-build-isolation '
        '--no-index -q -w "$lib/dist" "$lib";;\n'
        # The host-slice proof (require_installed_cgroup_parent). CMRU_TEST_PLACEMENT_FAIL
        # stands for a typo'd/uncertain slice: the real probe's refusal is exit non-zero.
        '  *"require_installed_cgroup_parent"*)\n'
        # CMRU_TEST_REAL_PLACEMENT: run the script's REAL verification (handlers.py against the
        # fake docker above) instead of this stand-in.
        f'    if [ -n "$CMRU_TEST_REAL_PLACEMENT" ]; then exec "{sys.executable}" "$@"; fi\n'
        '    shift 3\n'
        f'    echo "verify $1 $2" >> "{order_log}"\n'
        '    if [ -n "$CMRU_TEST_PLACEMENT_FAIL" ]; then exit 1; fi\n'
        "    exit 0;;\n"
        '  *"-m cmru.handlers"*)\n'
        '    { echo "ARGS=$*"; echo "PP=$PYTHONPATH"; echo "EPOCH=$SOURCE_DATE_EPOCH"\n'
        '      second="$(echo "$PYTHONPATH" | cut -d: -f2)"\n'
        '      if [ -f "$second/cli_extended/__init__.py" ]; then echo "CX=$(cat "$second/cli_extended/__init__.py")"; fi\n'
        f'    }} > "{record}"\n'
        # Optional probe: run code in a REAL bare interpreter with the exact PYTHONPATH
        # the handlers launch got (the staged unpack + dist-info is all it can see).
        '    if [ -n "$CMRU_TEST_TOY_CMRU_WHEEL" ]; then mkdir -p dist; rm -f dist/*.whl; cp "$CMRU_TEST_TOY_CMRU_WHEEL" dist/; fi\n'
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
    env.pop("CMRU_BOOTSTRAP_CLI_EXTENDED", None)
    env.pop("CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION", None)
    return {
        "repo_root": repo_root, "bootstrap": bootstrap, "record": record, "commit_env": commit_env,
        "docker_log": docker_log, "builder_marker": builder_marker, "env": env,
        "tester_base_marker": tester_base_marker,
        "order_log": order_log,
    }


def _run_bootstrap(tree) -> subprocess.CompletedProcess[str]:
    # The stub builds nothing, so the script's final wheel check fails; the
    # recorded launch is what is under test.
    return subprocess.run(
        ["bash", str(tree["bootstrap"])], env=tree["env"], capture_output=True, text=True, check=False,
    )


def _record(tree) -> dict[str, str]:
    return dict(
        # `CX=` is the staged package's __init__: a real (multi-line) one has lines without `=`.
        line.split("=", 1) for line in Path(tree["record"]).read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_fresh_checkout_bootstrap_is_the_only_source_build_launcher():
    bootstrap = BOOTSTRAP

    assert not (REPO_ROOT / "cmru.py").exists()
    assert bootstrap.is_file()
    assert os.access(bootstrap, os.X_OK)
    source = bootstrap.read_text(encoding="utf-8")
    assert "python3 -m cmru.handlers" not in source
    assert "-m cmru.handlers wheel-build" in source
    # BG-10: -s and an epoch; cli_extended is the verified RELEASED wheel. Its checkout may
    # be named ONLY inside the explicit zero-release source-mode branch: once in the source
    # binding inventory, once for the pre-build import check, and once in the build subprocess.
    uses = library_source_uses(source)
    assert len(uses) == 4, uses  # binding paths + binding roots + import check + build subprocess
    assert all(inside for _number, _line, inside in uses), uses
    assert sum("source_binding_paths+=" in line for _number, line, _inside in uses) == 1
    assert sum("source_binding_roots+=" in line for _number, line, _inside in uses) == 1
    assert sum("verify_pythonpath=" in line for _number, line, _inside in uses) == 1
    assert sum("export PYTHONPATH=" in line for _number, line, _inside in uses) == 1
    assert "libraries/worktree/src" in source
    assert '"${python_bin}" -s -m cmru.handlers' in source
    assert "CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256" in source and "fetch-cli-extended.py" in source
    assert "SOURCE_DATE_EPOCH" in source and "log -1 --format=%ct" in source
    assert "CMRU_DOCKER_CGROUP_PARENT" in source
    assert "org.vbpub.wheel-builder.kind" in source
    assert 'python-wheel-builder' in source
    dockerfile = (REPO_ROOT / "wheel-builder" / "Dockerfile").read_text(encoding="utf-8")
    assert 'LABEL org.vbpub.wheel-builder.kind="python-wheel-builder"' in dockerfile
    assert 'CMRU_WHEEL_BUILDER_IMAGE:-wheel-builder:local' not in source
    assert 'CMRU_WHEEL_BUILDER_IMAGE' in source


def test_bootstrap_rebuilds_an_existing_tag_that_is_not_the_declared_wheel_builder(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)

    _run_bootstrap(tree)

    docker_commands = Path(tree["docker_log"]).read_text(encoding="utf-8")
    # No --pull: the builder's base is the LOCAL tester-base tag, which a registry pull would refuse.
    assert "buildx build --load --cgroup-parent test.slice" in docker_commands
    assert "--pull" not in docker_commands
    assert "wheel-builder/Dockerfile" in docker_commands
    assert "-t wheel-builder:test" in docker_commands
    assert Path(tree["builder_marker"]).is_file()


def test_bootstrap_builds_tester_base_first_when_it_is_absent_and_only_then(tmp_path):
    """The wheel builder is FROM the local tester-base tag: absent, the script builds it (public inputs)
    before the builder, with the docker-driver builder and no --pull; present, it is not rebuilt."""
    for absent in (True, False):
        tree = _bootstrap_tree(tmp_path / ("absent" if absent else "present"))
        wheel, digest = _toy_wheel(tmp_path / ("w1" if absent else "w2"))
        tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)
        if absent:
            tree["env"]["CMRU_TEST_NO_TESTER_BASE"] = "1"

        _run_bootstrap(tree)

        commands = Path(tree["docker_log"]).read_text(encoding="utf-8").splitlines()
        base_builds = [i for i, line in enumerate(commands) if "-t tester-base:local" in line]
        builder_builds = [i for i, line in enumerate(commands) if "-t wheel-builder:test" in line]
        assert builder_builds, commands
        assert all("--pull" not in line for line in commands)
        if absent:
            assert base_builds and base_builds[0] < builder_builds[0], commands
            assert Path(tree["tester_base_marker"]).is_file()
        else:
            assert base_builds == [], commands


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
    assert fetched[fetched.index("--min-version") + 1] == "0.3.0"  # the floor from cmru's pyproject
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
    assert "CMRU_BOOTSTRAP_CLI_EXTENDED=source" in result.stderr  # the zero-release path is named
    assert not Path(tree["record"]).exists()


# --- W3-ZERO Z1: the explicit zero-release source mode ---

CX_LIB = REPO_ROOT / "libraries" / "cli-extended"
_needs_build_tools = pytest.mark.skipif(
    not CX_LIB.is_dir(), reason="repository-root library absent (isolated canary tree)",
)


def _recommit_tree(tree) -> None:
    """Fold the working tree into the one fixture commit and keep the exact tag on it.

    The commit date is pinned, so SOURCE_DATE_EPOCH and the derived version stay the
    fixture's documented values (1700000000 and 1.2.3).
    """
    root = tree["repo_root"]
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run(
        [*git, "commit", "-q", "--amend", "--no-edit", "--allow-empty"],
        check=True, env=tree["commit_env"],
    )
    subprocess.run(["git", "-C", str(root), "tag", "-f", "cmru-v1.2.3"], check=True, capture_output=True)


def _with_source_library(tree) -> None:
    """Give the throw-away tree the library inputs the source-mode build reads."""
    root = tree["repo_root"]
    (root / "tester-unified").mkdir(exist_ok=True)
    library = root / "libraries" / "cli-extended"
    library.mkdir(parents=True)
    shutil.copy2(CX_LIB / "pyproject.toml", library / "pyproject.toml")
    shutil.copy2(CX_LIB / "README.md", library / "README.md")
    shutil.copytree(CX_LIB / "src", library / "src", ignore=shutil.ignore_patterns("__pycache__"))
    _recommit_tree(tree)


@_needs_build_tools
def test_release_mode_with_no_release_fails_with_the_zero_release_hint_and_never_builds_from_source(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)  # the source IS available: a silent fallback would use it
    fetcher = tree["repo_root"] / "tester-unified" / "fetch-cli-extended.py"
    fetcher.write_text("raise SystemExit('cannot read the release pointer: 404')\n", encoding="utf-8")

    for env_extra in ({}, {"CMRU_BOOTSTRAP_CLI_EXTENDED": "release"}):
        tree["env"].update(env_extra)
        result = _run_bootstrap(tree)
        assert result.returncode == 2
        assert "CMRU_BOOTSTRAP_CLI_EXTENDED=source" in result.stderr
        assert "could not fetch the released cli-extended wheel" in result.stderr
        # NO silent fallback: nothing was built, nothing was launched.
        assert "source wheel sha256" not in result.stderr
        assert not Path(tree["record"]).exists()


@_needs_build_tools
def test_source_mode_builds_the_wheel_offline_logs_its_sha256_and_stages_it(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)
    tree["env"]["CMRU_BOOTSTRAP_CLI_EXTENDED"] = "source"

    result = _run_bootstrap(tree)

    lines = _record(tree)
    library = tree["repo_root"] / "libraries" / "cli-extended"
    (built,) = list((library / "dist").glob("cli_extended-*.whl"))
    digest = hashlib.sha256(built.read_bytes()).hexdigest()
    # The run log records the digest of exactly the built wheel, and its +local version.
    assert f"cli-extended source wheel sha256={digest} file=cli_extended-0.3.0+bootstrap.source-py3-none-any.whl" \
        in result.stderr, result.stderr
    assert built.name == "cli_extended-0.3.0+bootstrap.source-py3-none-any.whl"
    # It was built by cmru's own wheel-build handler, with the pretend version and the builder image.
    build = dict(line.split("=", 1) for line in Path(f"{tree['record']}.cxbuild").read_text("utf-8").splitlines())
    assert build["CXB_ARGS"].startswith("-s -m cmru.handlers wheel-build --cwd ")
    assert build["CXB_VERSION"] == "0.3.0+bootstrap.source" and build["CXB_IMAGE"] == "wheel-builder:test"
    # The library source is on the path for THAT step only ...
    assert str(library / "src") in build["CXB_PP"].split(os.pathsep)
    # ... and the cmru wheel build uses the staged wheel like any other mode.
    roots = lines["PP"].split(os.pathsep)
    assert str(library / "src") not in roots
    assert not Path(roots[1]).exists()  # staging does not outlive the script


@_needs_build_tools
def test_the_real_registry_builds_in_a_bare_interpreter_with_the_source_built_wheel(tmp_path):
    # No release anywhere: no fetcher, no wheel env. Only the checkout's library.
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)
    probe_out = tmp_path / "probe.txt"
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED="source",
        CMRU_TEST_PROBE_PY=str(_bare_interpreter()),
        CMRU_TEST_PROBE_OUT=str(probe_out),
        CMRU_TEST_PROBE_CODE=_PROBE.format(
            src=str(PROJECT_DIR / "src"), worktree=str(REPO_ROOT / "libraries" / "worktree" / "src"),
        ) + 'print("CXV=" + metadata.version("cli-extended"))\n',
    )
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU", "PYTHONPATH"):
        tree["env"].pop(name, None)

    _run_bootstrap(tree)

    raw = probe_out.read_text(encoding="utf-8") if probe_out.exists() else ""
    lines = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    assert lines.get("DIST") == "1.2.3" and lines.get("IDENTITY") == "1.2.3", raw
    assert lines.get("CXV") == "0.3.0+bootstrap.source", raw
    assert "site-packages" not in lines["CX"] and "libraries" not in lines["CX"], raw


@_needs_build_tools
def test_source_mode_version_is_overridable_and_must_be_a_local_version(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED="source", CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION="0.2.0")

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "+local" in result.stderr or "X.Y.Z+local" in result.stderr
    assert not Path(tree["record"]).exists()


def test_source_mode_cannot_be_combined_with_a_supplied_wheel(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED="source",
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
    )

    result = _run_bootstrap(tree)

    assert result.returncode == 2 and "cannot be combined" in result.stderr
    assert not Path(tree["record"]).exists()


def test_a_wheel_path_mode_value_is_verified_by_sha256_like_the_wheel_variable(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)

    _run_bootstrap(tree)
    assert _record(tree)["CX"] == "MARKER = 'released-wheel'"

    Path(tree["record"]).unlink()
    tree["env"]["CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256"] = "0" * 64
    result = _run_bootstrap(tree)
    assert result.returncode == 2 and "sha256 mismatch" in result.stderr
    assert not Path(tree["record"]).exists()


# --- B1/D-1: the bootstrap stages a cmru distribution identity (D2 has no source fallback) ---

def _real_cli_extended_wheel(directory: Path) -> tuple[Path, str]:
    """A wheel carrying the REAL installed ``cli_extended`` package (the released
    0.3.0 in this environment), so the probe below runs the real module."""
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


def test_tagless_bootstrap_accepts_an_explicit_temporary_cmru_version(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    subprocess.run(
        ["git", "-C", str(tree["repo_root"]), "tag", "-d", "cmru-v1.2.3"],
        check=True, capture_output=True,
    )
    wheel, digest = _toy_wheel(tmp_path / "release")
    probe_out = tmp_path / "probe.txt"
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel),
        CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
        SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU="0.0.0+bootstrap.source",
        CMRU_TEST_PROBE_PY=str(_bare_interpreter()),
        CMRU_TEST_PROBE_OUT=str(probe_out),
        CMRU_TEST_PROBE_CODE="import importlib.metadata as m; print(m.version('cmru'))",
    )
    tree["env"].pop("SETUPTOOLS_SCM_PRETEND_VERSION", None)

    _run_bootstrap(tree)

    assert probe_out.read_text("utf-8").strip() == "0.0.0+bootstrap.source"


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


# --- placement is proven on the host before ANY Docker/Buildx workload ---

def test_bootstrap_proves_the_host_slice_before_the_first_docker_workload(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest)

    _run_bootstrap(tree)

    order = Path(tree["order_log"]).read_text(encoding="utf-8").splitlines()
    first_buildx = next(i for i, line in enumerate(order) if line.startswith("docker buildx build"))
    verify = [i for i, line in enumerate(order) if line.startswith("verify ")]
    assert verify and verify[0] < first_buildx
    assert order[verify[0]] == f"verify test.slice {tree['repo_root'] / 'cmru'}"


def test_bootstrap_with_an_unverified_slice_starts_no_buildx_and_no_handler(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
        CMRU_TEST_PLACEMENT_FAIL="1",
    )

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "cgroup parent verification of test.slice did not succeed" in result.stderr
    assert "CMRU_TESTER_CGROUP_PROBE_IMAGE" in result.stderr  # the config inputs are named, not just the slice
    assert not Path(tree["docker_log"]).exists()  # no Buildx
    assert not Path(tree["builder_marker"]).exists()
    assert not Path(tree["record"]).exists()  # cmru.handlers wheel-build never launched
    order = Path(tree["order_log"]).read_text(encoding="utf-8").splitlines()
    assert all(not line.startswith("docker buildx") for line in order)


# --- the script's REAL slice verification (handlers.py against a fake docker host) ---
#
# The tests above stand in for the verification; these run it. The fake docker answers the
# host-systemd probe from CMRU_TEST_LOADED_SLICES, so a name outside that list behaves as a
# typo does on a real host: LoadState=loaded with no FragmentPath (an unlimited transient slice).

_GATES_SLICE = "gates.test.slice"
_BUILD_SLICE = "test.slice"  # CMRU_BOOTSTRAP_CGROUP_PARENT in _bootstrap_tree
_PROBE_IMAGE = "debian@sha256:" + "d" * 64


def _real_placement_tree(tmp_path: Path, loaded: list[str], gates: str = _GATES_SLICE) -> dict[str, object]:
    tree = _bootstrap_tree(tmp_path)
    root: Path = tree["repo_root"]  # type: ignore[assignment]
    (root / "libraries").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PROJECT_DIR / "src", root / "cmru" / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        REPO_ROOT / "libraries" / "worktree" / "src",
        root / "libraries" / "worktree" / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    _recommit_tree(tree)
    wheel, digest = _real_cli_extended_wheel(tmp_path / "release")
    tree["env"].update(  # type: ignore[attr-defined]
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
        CMRU_TEST_REAL_PLACEMENT="1", CMRU_TEST_LOADED_SLICES=" ".join(loaded),
        CMRU_TESTER_CGROUP_PROBE_IMAGE=_PROBE_IMAGE, CMRU_TESTER_CGROUP_PARENT=gates,
        PYTHONDONTWRITEBYTECODE="1",
    )
    for name in ("SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU", "PYTHONPATH"):
        tree["env"].pop(name, None)  # type: ignore[attr-defined]
    return tree


def _slices_asked(tree) -> list[str]:
    """The slice names the host probe was asked about, in order."""
    order = Path(tree["order_log"]).read_text(encoding="utf-8").splitlines() if Path(tree["order_log"]).exists() else []
    return [match.group(1) for line in order if (match := re.search(r"systemctl show (\S+)", line))]


def test_a_loaded_gates_slice_and_a_loaded_build_slice_are_proven_before_buildx(tmp_path):
    tree = _real_placement_tree(tmp_path, loaded=[_GATES_SLICE, _BUILD_SLICE])

    result = _run_bootstrap(tree)

    assert "cgroup parent verification" not in result.stderr, result.stderr
    # The gates tier is proven first, then the exact slice Docker will be given, and only then Buildx.
    assert _slices_asked(tree) == [_GATES_SLICE, _BUILD_SLICE]
    order = Path(tree["order_log"]).read_text(encoding="utf-8").splitlines()
    first_buildx = next(i for i, line in enumerate(order) if line.startswith("docker buildx build"))
    assert max(i for i, line in enumerate(order) if "systemctl show" in line) < first_buildx
    assert f"--cgroup-parent {_BUILD_SLICE}" in Path(tree["docker_log"]).read_text(encoding="utf-8")
    assert Path(tree["record"]).is_file()  # the wheel-build handler was launched


@pytest.mark.parametrize(
    ("loaded", "gates", "asked", "named"),
    [
        # A typo'd BUILD slice: systemd would auto-create it unlimited, so it must refuse.
        ([_GATES_SLICE], _GATES_SLICE, [_GATES_SLICE, _BUILD_SLICE], "TRANSIENT"),
        # A typo'd GATES slice: it cannot be trusted to host the probe of the build slice.
        ([_BUILD_SLICE], _GATES_SLICE, [_GATES_SLICE], "CMRU_TESTER_CGROUP_PARENT"),
        # A gates slice that was never declared: nothing is probed at all, nothing is invented.
        ([_GATES_SLICE, _BUILD_SLICE], "", [], "CMRU_TESTER_CGROUP_PARENT"),
    ],
    ids=["typoed-build-slice", "unloaded-gates-slice", "missing-gates-slice"],
)
def test_an_unproven_slice_refuses_before_buildx_and_before_the_handler(
    tmp_path, loaded, gates, asked, named,
):
    tree = _real_placement_tree(tmp_path, loaded=loaded, gates=gates)

    result = _run_bootstrap(tree)

    assert result.returncode == 2, result.stderr
    assert "cgroup parent verification of test.slice did not succeed" in result.stderr
    assert named in result.stderr, result.stderr
    assert _slices_asked(tree) == asked
    assert not Path(tree["docker_log"]).exists()  # no Buildx
    assert not Path(tree["builder_marker"]).exists()
    assert not Path(tree["record"]).exists()  # cmru.handlers wheel-build never launched


# --- source binding: the wheel is built from exactly the committed bytes ---------------------


def _with_packaged_sources(tree) -> None:
    """Committed package sources: what the wheel builder would actually package."""
    root = tree["repo_root"]
    for relative in ("cmru/src/cmru", "libraries/worktree/src/worktree"):
        (root / relative).mkdir(parents=True)
        (root / relative / "__init__.py").write_text("VALUE = 'committed'\n", encoding="utf-8")
    _recommit_tree(tree)


def _released_cli_extended(tree, tmp_path) -> None:
    wheel, digest = _toy_wheel(tmp_path / "release")
    tree["env"].update(
        CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL=str(wheel), CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256=digest,
    )


def _assert_refused_before_any_docker(tree, result, *named):
    assert result.returncode == 2, result.stderr
    assert "not exactly the committed HEAD" in result.stderr
    for fragment in named:
        assert fragment in result.stderr
    assert "nothing was built and no Docker command ran" in result.stderr
    # The fake docker and the fake host probe both append to the order log: it was never
    # written, so neither the placement probe nor Buildx ran; and the handler never launched.
    assert not Path(tree["order_log"]).exists()
    assert not Path(tree["docker_log"]).exists()
    assert not Path(tree["builder_marker"]).exists()
    assert not Path(tree["record"]).exists()


def test_committed_sources_are_accepted_so_the_refusals_below_are_about_the_dirty_input(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    # Noise that is not a wheel input: bytecode caches under a source root and files
    # outside the packaged paths must not block a bootstrap.
    root = tree["repo_root"]
    (root / ".git" / "info" / "exclude").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")  # as a real repo
    (root / "cmru" / "src" / "cmru" / "__pycache__").mkdir()
    (root / "cmru" / "src" / "cmru" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\0")
    (root / "cmru" / "notes.txt").write_text("not packaged\n", encoding="utf-8")
    (root / "elsewhere.txt").write_text("not packaged\n", encoding="utf-8")

    result = _run_bootstrap(tree)

    assert "not exactly the committed HEAD" not in result.stderr
    assert Path(tree["record"]).exists()  # the wheel build was launched
    assert Path(tree["docker_log"]).exists()  # and Docker ran, after the check


@pytest.mark.parametrize("relative", ["cmru/src/cmru/__init__.py", "libraries/worktree/src/worktree/__init__.py", "cmru/pyproject.toml"])
def test_a_tracked_edit_to_a_wheel_input_is_refused_before_any_docker(tmp_path, relative):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    edited = tree["repo_root"] / relative
    # A comment is valid Python and TOML, so the file stays parseable: only the bytes differ.
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# UNCOMMITTED\n", encoding="utf-8")

    result = _run_bootstrap(tree)

    _assert_refused_before_any_docker(tree, result, f" M {relative}", "Tracked edits or untracked files")


@pytest.mark.parametrize("relative", ["cmru/src/cmru/new_module.py", "libraries/worktree/src/worktree/extra.py"])
def test_an_untracked_file_under_a_wheel_source_root_is_refused_before_any_docker(tmp_path, relative):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    (tree["repo_root"] / relative).write_text("VALUE = 'untracked'\n", encoding="utf-8")

    result = _run_bootstrap(tree)

    _assert_refused_before_any_docker(tree, result, f"?? {relative}")


def test_a_git_ignored_file_that_would_still_be_packaged_is_refused_before_any_docker(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    root = tree["repo_root"]
    (root / ".git" / "info" / "exclude").write_text("*.generated.py\n", encoding="utf-8")
    (root / "cmru" / "src" / "cmru" / "sneaky.generated.py").write_text("VALUE = 'ignored'\n", encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "sneaky" not in status  # the control: plain `git status` calls this tree clean

    result = _run_bootstrap(tree)

    _assert_refused_before_any_docker(
        tree, result, "cmru/src/cmru/sneaky.generated.py", "Git-ignored files under a wheel source root",
    )


@_needs_build_tools
@pytest.mark.parametrize("dirt", ["tracked", "untracked"])
def test_source_mode_refuses_a_dirty_cli_extended_library_before_any_docker(tmp_path, dirt):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _with_source_library(tree)
    tree["env"]["CMRU_BOOTSTRAP_CLI_EXTENDED"] = "source"
    library_src = tree["repo_root"] / "libraries" / "cli-extended" / "src"
    if dirt == "tracked":
        target = next(path for path in sorted(library_src.rglob("*.py")))
        target.write_text(target.read_text(encoding="utf-8") + "\n# UNCOMMITTED\n", encoding="utf-8")
        expected = f" M libraries/cli-extended/src/{target.relative_to(library_src).as_posix()}"
    else:
        (library_src / "cli_extended" / "stray.py").write_text("X = 1\n", encoding="utf-8")
        expected = "?? libraries/cli-extended/src/cli_extended/stray.py"

    result = _run_bootstrap(tree)

    _assert_refused_before_any_docker(tree, result, expected)
    assert not (tree["repo_root"] / "libraries" / "cli-extended" / "dist").exists()  # nothing was built


def test_a_dirty_cli_extended_library_is_not_a_wheel_input_unless_it_is_built_here(tmp_path):
    """Release mode installs the verified released wheel, so the library source is not used."""
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    library = tree["repo_root"] / "libraries" / "cli-extended" / "src" / "cli_extended"
    library.mkdir(parents=True)
    (library / "uncommitted.py").write_text("X = 1\n", encoding="utf-8")

    result = _run_bootstrap(tree)

    assert "not exactly the committed HEAD" not in result.stderr
    assert Path(tree["record"]).exists()


def test_an_unreadable_git_state_is_a_refusal_not_a_clean_tree(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    tree["env"]["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU"] = "1.2.3"  # version needs no Git
    shutil.rmtree(tree["repo_root"] / ".git")

    result = _run_bootstrap(tree)

    assert result.returncode == 2
    assert "cannot read the Git state" in result.stderr
    assert not Path(tree["order_log"]).exists() and not Path(tree["record"]).exists()


def test_the_refusal_names_at_most_twenty_paths_and_the_remedy(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_packaged_sources(tree)
    _released_cli_extended(tree, tmp_path)
    for index in range(30):
        (tree["repo_root"] / "cmru" / "src" / "cmru" / f"m{index:02d}.py").write_text("", encoding="utf-8")

    result = _run_bootstrap(tree)

    _assert_refused_before_any_docker(tree, result, "Commit, stash or remove these paths")
    listed = [line for line in result.stderr.splitlines() if line.startswith("          ??")]
    assert len(listed) == 20
