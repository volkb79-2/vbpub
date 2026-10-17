"""The documented first-release sequence runs on a clean host, in order.

``docs/BOOTSTRAP-FROM-ZERO.md`` is executable here: its fenced command blocks
are extracted and run against a throw-away tree with a fake Docker. The oracle
is that step 1 reaches a working first ``cmru`` invocation from the artifacts
the build actually produced (no preinstalled CMRU, no stale wheel), and that a
stale tester-image pin cannot silently keep the old cli-extended bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile
from pathlib import Path

import pytest

from tests.test_installed_wheel_subprocess import (
    CX_LIB,
    PROJECT_DIR,
    REPO_ROOT,
    _bootstrap_tree,
    _with_source_library,
)

GUIDE = REPO_ROOT / "docs" / "BOOTSTRAP-FROM-ZERO.md"
IDENTITY_TOOL = PROJECT_DIR / "tools" / "cli_extended_release_identity.py"
DOCKERFILE = REPO_ROOT / "tester-unified" / "Dockerfile"

_needs_repo = pytest.mark.skipif(
    not (GUIDE.is_file() and CX_LIB.is_dir()),
    reason="repository-root guide/library absent (isolated canary tree)",
)


def _step_blocks(step: int) -> list[str]:
    """The fenced ``bash`` blocks of one numbered step of the guide."""
    text = GUIDE.read_text(encoding="utf-8")
    start = re.search(rf"^{step}\. \*\*", text, re.MULTILINE)
    following = re.search(rf"^{step + 1}\. \*\*", text, re.MULTILINE)
    assert start and following, f"guide step {step} not found"
    section = text[start.start():following.start()]
    blocks = re.findall(r"```bash\n(.*?)```", section, re.DOTALL)
    return [re.sub(r"^   ", "", block, flags=re.MULTILINE) for block in blocks]


def _toy_cmru_wheel(directory: Path, version: str, marker: str) -> Path:
    """A minimal real wheel carrying a ``cmru`` console script."""
    directory.mkdir(parents=True, exist_ok=True)
    dist_info = f"cmru-{version}.dist-info"
    wheel = directory / f"cmru-{version}-py3-none-any.whl"
    files = {
        "cmru_toy.py": (
            "def main():\n"
            "    import cli_extended  # the wheel installed beside it\n"
            f"    print({marker!r})\n"
        ),
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: cmru\nVersion: {version}\n",
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist_info}/entry_points.txt": "[console_scripts]\ncmru = cmru_toy:main\n",
    }
    record = "".join(f"{name},,\n" for name in files) + f"{dist_info}/RECORD,,\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(f"{dist_info}/RECORD", record)
    return wheel


def _read_env_file(path: Path) -> dict[str, str]:
    out = subprocess.run(
        ["bash", "-c", 'set -a; . "$1"; env -0', "_", str(path)],
        capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"},
    ).stdout
    return dict(item.split("=", 1) for item in out.split("\0") if "=" in item)


@_needs_repo
def test_step_one_reaches_a_working_first_cmru_from_the_artifacts_it_built(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)
    root: Path = tree["repo_root"]  # type: ignore[assignment]
    env: dict[str, str] = tree["env"]  # type: ignore[assignment]

    # A genuinely clean host: neither a cmru command nor a cmru distribution is
    # visible, and the only environment is a fresh venv without system packages.
    clean_path = f"{Path(env['PATH'].split(':')[0])}:/usr/bin:/bin"
    assert shutil.which("cmru", path=clean_path) is None
    venv = tmp_path / "estate-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
    estate_python = venv / "bin" / "python"
    # A STALE cmru from an earlier life of this environment must be replaced, not kept.
    stale = _toy_cmru_wheel(tmp_path / "stale", "9.9.9", "stale-cmru")
    subprocess.run(
        [str(estate_python), "-m", "pip", "install", "--no-index", "--no-deps", str(stale)],
        check=True, capture_output=True,
    )
    toy = _toy_cmru_wheel(tmp_path / "toy", "0.0.0+bootstrap.source", "bootstrapped-cmru")
    env.update(
        PATH=clean_path,
        ESTATE_PYTHON=str(estate_python),
        CMRU_TEST_TOY_CMRU_WHEEL=str(toy),
    )

    blocks = _step_blocks(1)
    assert len(blocks) == 1
    script = "set -euo pipefail\n" + blocks[0]
    # Every command in the block precedes the first cmru invocation or is it.
    assert script.index("pip install") < script.index("cmru --version")
    result = subprocess.run(
        ["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "bootstrapped-cmru"
    # The wheels installed are exactly the ones the build recorded, by path and digest.
    identity = _read_env_file(root / "cmru" / "dist" / "bootstrap-artifacts.env")
    cmru_wheel = Path(identity["BOOTSTRAP_CMRU_WHEEL"])
    cx_wheel = Path(identity["BOOTSTRAP_CLI_EXTENDED_WHEEL"])
    assert cmru_wheel.parent == root / "cmru" / "dist" and cmru_wheel.name == toy.name
    assert hashlib.sha256(cmru_wheel.read_bytes()).hexdigest() == identity["BOOTSTRAP_CMRU_WHEEL_SHA256"]
    assert hashlib.sha256(cx_wheel.read_bytes()).hexdigest() == identity["BOOTSTRAP_CLI_EXTENDED_WHEEL_SHA256"]
    installed = subprocess.run(
        [str(estate_python), "-c",
         "import importlib.metadata as m; print(m.version('cli-extended'), m.version('cmru'))"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert installed == ["0.3.0+bootstrap.source", "0.0.0+bootstrap.source"]  # stale 9.9.9 replaced


@_needs_repo
def test_step_one_install_refuses_a_wheel_that_changed_after_the_build(tmp_path):
    tree = _bootstrap_tree(tmp_path)
    _with_source_library(tree)
    root: Path = tree["repo_root"]  # type: ignore[assignment]
    env: dict[str, str] = tree["env"]  # type: ignore[assignment]
    toy = _toy_cmru_wheel(tmp_path / "toy", "0.0.0+bootstrap.source", "bootstrapped-cmru")
    venv = tmp_path / "estate-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
    env.update(
        PATH=f"{env['PATH'].split(':')[0]}:/usr/bin:/bin",
        ESTATE_PYTHON=str(venv / "bin" / "python"), CMRU_TEST_TOY_CMRU_WHEEL=str(toy),
    )
    build_only, _, rest = _step_blocks(1)[0].partition("\n. cmru/dist/bootstrap-artifacts.env")
    subprocess.run(["bash", "-c", "set -e\n" + build_only], cwd=root, env=env, check=True, capture_output=True)
    wheel = next((root / "cmru" / "dist").glob("cmru-*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n. cmru/dist/bootstrap-artifacts.env" + rest],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert "FAILED" in result.stdout or "FAILED" in result.stderr
    assert not (venv / "bin" / "cmru").exists()


# --- the released cli-extended bytes reach the tester image ---

OLD_PIN_SHA = "a" * 64
NEW_SHA = "b" * 64
OLD_URL = "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.3.0/cli_extended-0.3.0-py3-none-any.whl"


def _dockerfile(path: Path, url: str, sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FROM scratch\n"
        "ARG CLI_EXTENDED_RESOLVE=pinned\n"
        f"ARG CLI_EXTENDED_WHEEL_URL={url}\n"
        f"ARG CLI_EXTENDED_WHEEL_SHA256={sha}\n",
        encoding="utf-8",
    )


def _pointer(path: Path, sha: str, url: str) -> None:
    path.write_text(json.dumps({"version": "0.3.0", "asset": "x.whl", "sha256": sha, "url": url}), encoding="utf-8")


def _identity(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(IDENTITY_TOOL), *args], capture_output=True, text=True, check=False,
    )


def _parse(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines())


def test_newly_released_bytes_that_differ_from_the_old_pin_select_latest(tmp_path):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL, OLD_PIN_SHA)
    _pointer(tmp_path / "latest.json", NEW_SHA, OLD_URL)

    result = _identity(
        "--released-sha256", NEW_SHA, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    )

    assert result.returncode == 0, result.stderr
    values = _parse(result.stdout)
    assert values["CLI_EXTENDED_DOCKERFILE_PIN"] == "stale"
    assert values["CLI_EXTENDED_RESOLVE"] == "latest"  # never the old embedded digest
    assert values["CLI_EXTENDED_RELEASED_SHA256"] == NEW_SHA
    assert values["CLI_EXTENDED_DOCKERFILE_PIN_SHA256"] == OLD_PIN_SHA


def test_a_pin_updated_to_the_released_asset_may_use_pinned(tmp_path):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL, NEW_SHA)
    _pointer(tmp_path / "latest.json", NEW_SHA, OLD_URL)

    result = _identity(
        "--released-sha256", NEW_SHA, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    )

    values = _parse(result.stdout)
    assert (values["CLI_EXTENDED_DOCKERFILE_PIN"], values["CLI_EXTENDED_RESOLVE"]) == ("current", "pinned")


def test_same_digest_but_a_different_url_is_not_a_current_pin(tmp_path):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL.replace("v0.3.0", "v0.2.0"), NEW_SHA)
    _pointer(tmp_path / "latest.json", NEW_SHA, OLD_URL)

    values = _parse(_identity(
        "--released-sha256", NEW_SHA, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    ).stdout)

    assert values["CLI_EXTENDED_RESOLVE"] == "latest"


@pytest.mark.parametrize("released", ["", "ABC", "B" * 64])
def test_a_malformed_released_digest_refuses(tmp_path, released):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL, OLD_PIN_SHA)
    _pointer(tmp_path / "latest.json", NEW_SHA, OLD_URL)

    result = _identity(
        "--released-sha256", released, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    )

    assert result.returncode == 2 and result.stdout == ""
    assert "64 lowercase hex" in result.stderr


def test_a_pointer_that_does_not_name_the_published_digest_refuses(tmp_path):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL, OLD_PIN_SHA)
    _pointer(tmp_path / "latest.json", OLD_PIN_SHA, OLD_URL)  # still the old release

    result = _identity(
        "--released-sha256", NEW_SHA, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    )

    assert result.returncode == 2 and result.stdout == ""
    assert OLD_PIN_SHA in result.stderr and NEW_SHA in result.stderr


@pytest.mark.parametrize("content", [None, "not json", "[]", json.dumps({"version": "1"})])
def test_an_unreadable_or_malformed_pointer_is_a_refusal_not_no_release(tmp_path, content):
    _dockerfile(tmp_path / "Dockerfile", OLD_URL, OLD_PIN_SHA)
    if content is not None:
        (tmp_path / "latest.json").write_text(content, encoding="utf-8")

    result = _identity(
        "--released-sha256", NEW_SHA, "--dockerfile", str(tmp_path / "Dockerfile"),
        "--pointer-file", str(tmp_path / "latest.json"),
    )

    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.startswith("cli-extended-release-identity:")


@pytest.mark.skipif(not DOCKERFILE.is_file(), reason="tester-unified absent (isolated canary tree)")
def test_the_real_dockerfile_pin_is_readable_by_the_check(tmp_path):
    text = DOCKERFILE.read_text(encoding="utf-8")
    pinned = re.search(r"^ARG CLI_EXTENDED_WHEEL_SHA256=([0-9a-f]{64})$", text, re.MULTILINE).group(1)
    url = re.search(r"^ARG CLI_EXTENDED_WHEEL_URL=(\S+)$", text, re.MULTILINE).group(1)
    _pointer(tmp_path / "latest.json", pinned, url)

    values = _parse(_identity(
        "--released-sha256", pinned, "--dockerfile", str(DOCKERFILE),
        "--pointer-file", str(tmp_path / "latest.json"),
    ).stdout)

    assert values["CLI_EXTENDED_RESOLVE"] == "pinned"


def _step_three_tree(tmp_path: Path, pin_sha: str, pointer_sha: str) -> tuple[Path, dict[str, str], Path]:
    """A tree where ``build-image.sh`` and ``docker`` are recorders."""
    root = tmp_path / "tree"
    (root / "cmru" / "tools").mkdir(parents=True)
    shutil.copy2(IDENTITY_TOOL, root / "cmru" / "tools" / IDENTITY_TOOL.name)
    _dockerfile(root / "tester-unified" / "Dockerfile", OLD_URL, pin_sha)
    _pointer(root / "latest.json", pointer_sha, OLD_URL)
    log = tmp_path / "build.log"
    builder = root / "tester-unified" / "build-image.sh"
    builder.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n', encoding="utf-8")
    builder.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *org.vbpub.cli-extended.wheel-sha256*) echo "$FAKE_CLI_EXTENDED_LABEL";;\n'
        '  *) echo "sha256:image-$$";;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}", "HOME": str(tmp_path),
        # What the (fake) built image's cli-extended label says it installed.
        "FAKE_CLI_EXTENDED_LABEL": NEW_SHA,
    }
    return root, env, log


def _release_log(released: str) -> str:
    """What `cmru release cli-extended` leaves in the root audit log (quiet push step)."""
    return (
        "[INFO] CLI-EXTENDED_WHEEL_ASSET_URL=https://example.invalid/cli_extended-0.3.0-py3-none-any.whl\n"
        f"[INFO] CLI-EXTENDED_WHEEL_SHA256={released}\n"
        f"[INFO] CLI-EXTENDED_WHEEL_SHA256_URL=https://example.invalid/{released}.sha256\n"
    )


def _run_step_three_identity_block(
    root: Path, env: dict[str, str], released: str, log_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run step 3's identity block with the digest READ from the release log, as documented."""
    blocks = _step_blocks(3)
    block = next(item for item in blocks if "cli_extended_release_identity.py" in item)
    needle = "--dockerfile tester-unified/Dockerfile"
    assert block.count(needle) == 2  # the pre-build check and the post-build re-check
    block = block.replace(needle, needle + " --pointer-file latest.json")
    # The digest is never typed: no placeholder is left for the operator to fill in.
    assert "<" not in block and "CLI_EXTENDED_RELEASED_SHA256='" not in block
    (root / "cmru.release.log").write_text(
        _release_log(released) if log_text is None else log_text, encoding="utf-8",
    )
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + block], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )


@_needs_repo
def test_step_three_rebuilds_the_image_from_the_released_bytes_when_the_pin_is_stale(tmp_path):
    root, env, log = _step_three_tree(tmp_path, pin_sha=OLD_PIN_SHA, pointer_sha=NEW_SHA)

    result = _run_step_three_identity_block(root, env, NEW_SHA)

    assert result.returncode == 0, result.stderr
    (command,) = log.read_text(encoding="utf-8").splitlines()
    assert "--cli-extended-resolve latest" in command and "--no-cache" in command
    # `latest` is only ever built with the published digest, never unverified.
    assert f"--cli-extended-wheel-sha256 {NEW_SHA}" in command
    assert "--cli-extended-resolve pinned" not in command
    assert "--tag tester-unified:cli-extended-released" in command
    assert "Dockerfile pin: stale; resolve mode: latest" in result.stdout


@_needs_repo
def test_step_three_never_builds_when_the_pointer_lags_the_published_release(tmp_path):
    root, env, log = _step_three_tree(tmp_path, pin_sha=OLD_PIN_SHA, pointer_sha=OLD_PIN_SHA)

    result = _run_step_three_identity_block(root, env, NEW_SHA)

    assert result.returncode == 2
    assert not log.exists()  # build-image.sh was never reached


@_needs_repo
def test_step_three_refuses_an_image_whose_label_names_other_bytes(tmp_path):
    root, env, log = _step_three_tree(tmp_path, pin_sha=OLD_PIN_SHA, pointer_sha=NEW_SHA)
    env["FAKE_CLI_EXTENDED_LABEL"] = OLD_PIN_SHA  # the image kept the old pinned bytes

    result = _run_step_three_identity_block(root, env, NEW_SHA)

    assert result.returncode != 0
    assert "tester-unified:cli-extended-released" in log.read_text(encoding="utf-8")  # built, then refused


@_needs_repo
def test_step_three_pinned_mode_passes_no_digest_flag_when_the_pin_is_current(tmp_path):
    root, env, log = _step_three_tree(tmp_path, pin_sha=NEW_SHA, pointer_sha=NEW_SHA)

    result = _run_step_three_identity_block(root, env, NEW_SHA)

    assert result.returncode == 0, result.stderr
    (command,) = log.read_text(encoding="utf-8").splitlines()
    assert "--cli-extended-resolve pinned" in command
    assert "--cli-extended-wheel-sha256" not in command


@_needs_repo
def test_the_guide_names_the_log_interface_and_the_exact_cgroup_inputs():
    text = GUIDE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    # step 3 reads the published identity from the release log, not the terminal
    assert "cmru.release.log" in text and "grep -E 'CLI-EXTENDED_WHEEL_(SHA256|ASSET_URL)=' cmru.release.log" in text
    assert "`quiet = true`" in text
    # wheel-build does not read the bootstrap-only variable
    assert "does **not** read `CMRU_BOOTSTRAP_CGROUP_PARENT`" in flat
    assert "`CMRU_DOCKER_CGROUP_PARENT` or `$CGROUP_PARENT_DEV_BACKGROUND`" in flat
    # each release command pins its own image
    cgprofile = next(
        block for block in _step_blocks(4) if "cmru release cgroup-profiler" in block
    )
    assert "RUN_GATE_TESTER_UNIFIED_IMAGE=" in cgprofile.split("cmru release cgroup-profiler")[0]
    assert "RUN_GATE_TESTER_CLI_EXTENDED_SHA256=" in cgprofile


@_needs_repo
@pytest.mark.parametrize(
    "log_text",
    [
        "",
        "[INFO] nothing about cli-extended here\n",
        f"[INFO] CLI-EXTENDED_WHEEL_SHA256={NEW_SHA}\n[INFO] CLI-EXTENDED_WHEEL_SHA256={OLD_PIN_SHA}\n",
        "[INFO] CLI-EXTENDED_WHEEL_SHA256=" + "B" * 64 + "\n",
    ],
    ids=["empty-log", "no-identity-line", "two-different-digests", "not-lowercase-hex"],
)
def test_step_three_refuses_a_release_log_that_does_not_name_exactly_one_digest(tmp_path, log_text):
    root, env, log = _step_three_tree(tmp_path, pin_sha=OLD_PIN_SHA, pointer_sha=NEW_SHA)

    result = _run_step_three_identity_block(root, env, NEW_SHA, log_text=log_text)

    assert result.returncode != 0
    assert not log.exists()  # the image build was never reached with a guessed digest


# --- the guide itself is a contract: fences, ordering, versions and the first cmru command ---

def _fenced(text: str) -> list[tuple[str, str]]:
    """Every fenced block as ``(info string, dedented body)``, in document order."""
    return [
        (info, textwrap.dedent(body))
        for info, body in re.findall(r"^[ ]*```(\S*)\n(.*?)^[ ]*```[ ]*$", text, re.DOTALL | re.MULTILINE)
    ]


def _bash_blocks() -> list[str]:
    return [body for info, body in _fenced(GUIDE.read_text(encoding="utf-8")) if info == "bash"]


@_needs_repo
def test_every_fence_of_the_guide_is_closed_and_every_shell_block_parses():
    text = GUIDE.read_text(encoding="utf-8")
    fence_lines = [line for line in text.splitlines() if line.strip().startswith("```")]
    assert len(fence_lines) % 2 == 0, "an unclosed or stray code fence"
    assert len(fence_lines) == 2 * len(_fenced(text))
    blocks = _bash_blocks()
    assert len(blocks) >= 8  # a fence regex that silently finds nothing must not pass
    for block in blocks:
        parsed = subprocess.run(["bash", "-n"], input=block, capture_output=True, text=True, check=False)
        assert parsed.returncode == 0, f"{parsed.stderr}\n{block}"


@_needs_repo
def test_no_shell_block_has_a_doubled_dangling_or_placeholder_line():
    for block in _bash_blocks():
        lines = block.splitlines()
        for number, line in enumerate(lines):
            # A doubled backslash ends the command instead of continuing it (the next line then
            # runs as a separate, failing command).
            assert not re.search(r"\\\\\s*$", line), line
            if line.rstrip().endswith("\\"):
                assert number + 1 < len(lines) and lines[number + 1].strip(), f"dangling continuation: {line}"
        # An operator-fill-in marker such as <value from the log> is not pasteable.
        assert not re.search(r"<[A-Za-z][^<>\n]* [^<>\n]*>", block), block


@_needs_repo
def test_the_controller_is_installed_and_on_path_before_the_first_cmru_command():
    commands: list[str] = []
    for block in _bash_blocks():
        commands.extend(block.splitlines())
    first_cmru = next(i for i, line in enumerate(commands) if re.search(r"(?<![\w./-])cmru\s", line))
    install = next(i for i, line in enumerate(commands) if "pip install --no-index" in line)
    # The first build script prints install commands only; the guide runs them itself, and
    # puts the environment that now owns `cmru` first on PATH AFTER installing and before use.
    path_after_install = [
        i for i, line in enumerate(commands) if line.startswith("export PATH=") and install < i < first_cmru
    ]
    assert install < first_cmru and path_after_install
    assert commands[first_cmru].strip() == "cmru --version"
    assert "bootstrap-artifacts.env" in "\n".join(commands[:install])


def _fake_docker_path(tmp_path: Path) -> tuple[str, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(f'#!/bin/sh\necho "docker $*" >> "{log}"\n', encoding="utf-8")
    docker.chmod(0o755)
    return f"{fake_bin}:/usr/bin:/bin", log


@_needs_repo
@pytest.mark.parametrize("missing", [None, "CGROUP_PARENT_DEV_BACKGROUND", "CGROUP_PARENT_DEV_GATES", "ESTATE_PYTHON"])
def test_the_setup_block_derives_the_checkout_and_refuses_before_pulling_when_an_input_is_missing(
    tmp_path, missing,
):
    setup = next(block for block in _bash_blocks() if "git rev-parse --show-toplevel" in block)
    assert setup.index("docker pull") > setup.index("ESTATE_PYTHON")  # nothing is pulled before the checks
    repo = tmp_path / "checkout"
    (repo / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    path, log = _fake_docker_path(tmp_path)
    env = {
        "PATH": path, "HOME": str(tmp_path), "ESTATE_PYTHON": str(python),
        "CGROUP_PARENT_DEV_BACKGROUND": "background.slice", "CGROUP_PARENT_DEV_GATES": "gates.slice",
    }
    if missing:
        env.pop(missing)

    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + setup + '\nprintf "%s\\n" "$PWD" "$PATH"\n'],
        cwd=repo / "docs", env=env, capture_output=True, text=True, check=False,
    )

    if missing:
        assert result.returncode != 0 and missing in result.stderr
        assert not log.exists()  # no Docker call with an unproven environment
    else:
        assert result.returncode == 0, result.stderr
        pwd, new_path = result.stdout.splitlines()[-2:]
        assert Path(pwd).resolve() == repo.resolve()  # the root is derived from git, from a subdirectory
        assert new_path.split(":")[0] == str(python.parent)
        assert "docker pull debian@sha256:" in log.read_text(encoding="utf-8")


@_needs_repo
def test_the_release_commands_follow_the_declared_graph_and_one_set_of_versions():
    releases: list[tuple[str, str]] = []
    for block in _bash_blocks():
        releases += re.findall(r"cmru release (\S+) --set-version (\S+)", block)
    order = [project for project, _ in releases]
    versions = dict(releases)
    assert order == ["cli-extended", "cgroup-profiler", "run-gate-project", "cmru"]
    declared = tomllib.loads((REPO_ROOT / "cmru.orchestration.toml").read_text(encoding="utf-8"))
    for index, project in enumerate(order):
        providers = declared["orchestration"]["project"][project]["depends_on"]
        # Every declared provider that the guide releases is released before its consumer ...
        assert all(order.index(provider) < index for provider in providers if provider in order), project
    # ... and CMRU's own providers (runtime wheel and gate Run-Gate) are both released first.
    assert set(declared["orchestration"]["project"]["cmru"]["depends_on"]) <= set(order[:-1])
    # The first cli-extended version must satisfy CMRU's declared floor, which the prose names.
    pyproject = (PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    floor = re.search(r'"cli-extended>=([0-9][0-9.]*)"', pyproject).group(1)
    assert tuple(map(int, versions["cli-extended"].split("."))) >= tuple(map(int, floor.split(".")))
    assert f"`cli-extended>={floor}`" in GUIDE.read_text(encoding="utf-8")
    # Run-Gate is named by ONE version in the wheel build, the retained-wheel glob and the release.
    text = GUIDE.read_text(encoding="utf-8")
    named = set(re.findall(r"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=(\S+)", text))
    named |= set(re.findall(r"run_gate-([0-9][0-9.]*)-\*\.whl", text))
    named.add(versions["run-gate-project"])
    assert len(named) == 1, named
    # ... and it is not behind what the project's own changelog says was already released.
    changes = REPO_ROOT / "run-gate-project" / "CHANGES.md"
    if changes.is_file():
        released = [
            tuple(map(int, found.split(".")))
            for found in re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", changes.read_text("utf-8"))
        ]
        (run_gate_version,) = named
        assert tuple(map(int, run_gate_version.split("."))) >= max(released)
