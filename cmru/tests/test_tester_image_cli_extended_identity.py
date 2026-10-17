"""``tester-unified/build-image.sh`` binds every image to exact cli-extended bytes.

The real script runs against a throw-away tree with fake ``docker``, ``cmru`` and
Run-Gate-identity helpers, so what is exercised is the script's own argument
policy and its post-build label check.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "tester-unified" / "build-image.sh"
DOCKERFILE = REPO_ROOT / "tester-unified" / "Dockerfile"

pytestmark = pytest.mark.skipif(
    not (BUILDER.is_file() and DOCKERFILE.is_file()),
    reason="tester-unified absent (isolated canary tree)",
)

PIN = re.search(r"^ARG CLI_EXTENDED_WHEEL_SHA256=([0-9a-f]{64})$", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
PINNED_DIGEST = PIN.group(1) if PIN else ""
PUBLISHED = "c" * 64  # newly released bytes: deliberately NOT the pinned digest
assert PUBLISHED != PINNED_DIGEST


def _tree(tmp_path: Path):
    root = tmp_path / "tree"
    (root / "tester-unified").mkdir(parents=True)
    (root / "run-gate-project" / "dist").mkdir(parents=True)
    shutil.copy2(BUILDER, root / "tester-unified" / "build-image.sh")
    shutil.copy2(DOCKERFILE, root / "tester-unified" / "Dockerfile")
    wheel = root / "run-gate-project" / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel.write_bytes(b"run-gate wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (root / "tester-unified" / "run-gate-wheel-version.py").write_text(
        "import json\n"
        f"print(json.dumps({{'version': '24.0.0', 'sha256': '{digest}', "
        "'filename': 'run_gate-24.0.0-py3-none-any.whl'}))\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    (fake_bin / "cmru").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'if [ "$1" = image ]; then\n'
        '  case "$*" in\n'
        '    *org.vbpub.run-gate.version*) echo 24.0.0;;\n'
        f'    *org.vbpub.run-gate.wheel-sha256*) echo {digest};;\n'
        '    *org.vbpub.cli-extended.wheel-sha256*) echo "$FAKE_CLI_EXTENDED_LABEL";;\n'
        '  esac\n'
        'fi\n'
        'exit 0\n',
        encoding="utf-8",
    )
    for name in ("cmru", "docker"):
        (fake_bin / name).chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
        "TMPDIR": str(tmp_path),
    }
    return root, env, log


def _build(tmp_path, *args, label=None):
    root, env, log = _tree(tmp_path)
    if label is not None:
        env["FAKE_CLI_EXTENDED_LABEL"] = label
    result = subprocess.run(
        ["bash", str(root / "tester-unified" / "build-image.sh"), *args],
        env=env, capture_output=True, text=True, check=False, cwd=root,
    )
    return result, log


def _buildx(log: Path) -> str:
    return next(line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("buildx build"))


def test_latest_without_the_published_digest_is_refused_before_any_docker_call(tmp_path):
    result, log = _build(tmp_path, "--cli-extended-resolve", "latest")

    assert result.returncode == 2
    assert "requires --cli-extended-wheel-sha256" in result.stderr
    assert not log.exists()  # no unverified bytes can be reached


@pytest.mark.parametrize("digest", ["abc", "C" * 64])
def test_latest_and_local_refuse_a_malformed_digest(tmp_path, digest):
    for mode in ("latest", "local"):
        result, log = _build(
            tmp_path / mode, "--cli-extended-resolve", mode, "--cli-extended-wheel-sha256", digest,
        )
        assert result.returncode == 2 and "64 lowercase hex" in result.stderr
        assert not log.exists()


def test_a_digest_is_refused_in_pinned_and_default_modes(tmp_path):
    for index, resolve in enumerate((["--cli-extended-resolve", "pinned"], [])):
        result, log = _build(tmp_path / str(index), *resolve, "--cli-extended-wheel-sha256", PUBLISHED)
        assert result.returncode == 2
        assert "valid only with --cli-extended-resolve local or latest" in result.stderr
        assert not log.exists()


def test_latest_passes_the_published_digest_and_accepts_a_matching_label(tmp_path):
    result, log = _build(
        tmp_path, "--cli-extended-resolve", "latest",
        "--cli-extended-wheel-sha256", PUBLISHED, label=PUBLISHED,
    )

    assert result.returncode == 0, result.stderr
    command = _buildx(log)
    assert "CLI_EXTENDED_RESOLVE=latest" in command
    assert f"CLI_EXTENDED_WHEEL_SHA256={PUBLISHED}" in command


def test_a_latest_image_still_carrying_the_old_pinned_bytes_is_refused(tmp_path):
    """Newly released bytes differ from the old pin; an image whose label names the
    old pinned digest must fail the build step, not be handed to the gate."""
    result, _log = _build(
        tmp_path, "--cli-extended-resolve", "latest",
        "--cli-extended-wheel-sha256", PUBLISHED, label=PINNED_DIGEST,
    )

    assert result.returncode == 2
    assert "does not carry the expected cli-extended wheel" in result.stderr
    assert f"expected: sha256={PUBLISHED}" in result.stderr
    assert f"observed: sha256={PINNED_DIGEST}" in result.stderr


def test_an_unlabelled_image_is_refused(tmp_path):
    result, _log = _build(
        tmp_path, "--cli-extended-resolve", "local",
        "--cli-extended-wheel-sha256", PUBLISHED, label="",
    )

    assert result.returncode == 2 and "<no label>" in result.stderr


def test_the_default_pinned_image_must_be_labelled_with_the_pinned_digest(tmp_path):
    ok, log = _build(tmp_path / "ok", label=PINNED_DIGEST)
    assert ok.returncode == 0, ok.stderr
    assert "CLI_EXTENDED_WHEEL_SHA256" not in _buildx(log)  # the Dockerfile pin applies

    stale, _ = _build(tmp_path / "stale", label=PUBLISHED)
    assert stale.returncode == 2
    assert f"expected: sha256={PINNED_DIGEST}" in stale.stderr


def test_local_mode_labels_the_supplied_digest(tmp_path):
    result, log = _build(
        tmp_path, "--cli-extended-resolve", "local",
        "--cli-extended-wheel-sha256", PUBLISHED, label=PUBLISHED,
    )

    assert result.returncode == 0, result.stderr
    assert f"CLI_EXTENDED_WHEEL_SHA256={PUBLISHED}" in _buildx(log)
