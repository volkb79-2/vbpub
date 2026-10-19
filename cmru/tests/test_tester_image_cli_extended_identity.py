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
# The base the Dockerfile builds FROM when no --base-image is given, and the
# zero-release upstream base the bootstrap guide selects explicitly.
BASE = re.search(r"^ARG TESTER_UNIFIED_BASE_IMAGE=(\S+)$", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
DEFAULT_BASE = BASE.group(1) if BASE else ""
UPSTREAM_BASE = "mcr.microsoft.com/devcontainers/python:3.14-trixie"
assert UPSTREAM_BASE != DEFAULT_BASE


BASE_ID = "sha256:" + "b" * 64


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
        '    *org.vbpub.tester-unified.base-image-id*) echo "$FAKE_BASE_IMAGE_ID_LABEL";;\n'
        '    *org.vbpub.tester-unified.base-image*) echo "$FAKE_BASE_IMAGE_LABEL";;\n'
        '    *"{{.Id}}"*) echo "$FAKE_BASE_IMAGE_ID";;\n'
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
        # What the (fake) built image says it was built FROM: by default the
        # Dockerfile's own default base, as a real default build records.
        "FAKE_BASE_IMAGE_LABEL": DEFAULT_BASE,
        # The identity the (fake) docker resolves the base to, and what the built image's id label says.
        "FAKE_BASE_IMAGE_ID": BASE_ID,
        "FAKE_BASE_IMAGE_ID_LABEL": BASE_ID,
    }
    return root, env, log


def _build(tmp_path, *args, label=None, base_label=None, env_extra=None):
    root, env, log = _tree(tmp_path)
    env.update(env_extra or {})
    if label is not None:
        env["FAKE_CLI_EXTENDED_LABEL"] = label
    if base_label is not None:
        env["FAKE_BASE_IMAGE_LABEL"] = base_label
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
    assert "does not carry the expected cli-extended wheel" in result.stderr


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


# --- org.vbpub.tester-unified.base-image: the base the image was built FROM ---------


def test_a_default_build_must_record_the_dockerfiles_default_base(tmp_path):
    ok, log = _build(tmp_path / "ok", label=PINNED_DIGEST, base_label=DEFAULT_BASE)
    assert ok.returncode == 0, ok.stderr
    assert "--build-arg TESTER_UNIFIED_BASE_IMAGE=" not in _buildx(log)  # the Dockerfile default applies

    other, _ = _build(tmp_path / "other", label=PINNED_DIGEST, base_label=UPSTREAM_BASE)
    assert other.returncode == 2
    assert "not built from the requested base" in other.stderr
    assert f"expected: {DEFAULT_BASE}" in other.stderr
    assert f"observed: {UPSTREAM_BASE}" in other.stderr


def test_an_explicit_base_is_passed_to_buildx_and_accepted_when_recorded(tmp_path):
    result, log = _build(
        tmp_path, "--base-image", UPSTREAM_BASE, label=PINNED_DIGEST, base_label=UPSTREAM_BASE,
    )

    assert result.returncode == 0, result.stderr
    assert f"--build-arg TESTER_UNIFIED_BASE_IMAGE={UPSTREAM_BASE}" in _buildx(log)


def test_an_explicit_base_the_build_did_not_consume_is_refused(tmp_path):
    """A requested base the Dockerfile ignored (a renamed or unconsumed ARG builds the
    default base) must fail the build step even though the wheel labels are correct."""
    result, _log = _build(
        tmp_path, "--base-image", UPSTREAM_BASE,
        "--cli-extended-resolve", "local", "--cli-extended-wheel-sha256", PUBLISHED,
        label=PUBLISHED, base_label=DEFAULT_BASE,
    )

    assert result.returncode == 2
    assert "not built from the requested base" in result.stderr
    assert f"expected: {UPSTREAM_BASE}" in result.stderr
    assert f"observed: {DEFAULT_BASE}" in result.stderr


def test_an_image_without_a_base_label_is_refused(tmp_path):
    for index, base in enumerate(([], ["--base-image", UPSTREAM_BASE])):
        result, _log = _build(tmp_path / str(index), *base, label=PINNED_DIGEST, base_label="")
        assert result.returncode == 2
        assert "not built from the requested base" in result.stderr
        assert "observed: <no label>" in result.stderr


# --- org.vbpub.tester-unified.base-image-id: WHICH content the base reference was ----------


def test_the_resolved_base_identity_is_passed_to_buildx_and_accepted_when_recorded(tmp_path):
    result, log = _build(tmp_path, label=PINNED_DIGEST, base_label=DEFAULT_BASE)
    assert result.returncode == 0, result.stderr
    assert f"--build-arg TESTER_UNIFIED_BASE_IMAGE_ID={BASE_ID}" in _buildx(log)


def test_an_image_recording_another_base_identity_is_refused(tmp_path):
    """A stale local tester-base tag: the label must carry the identity the base resolved to."""
    other = "sha256:" + "c" * 64
    for index, label in enumerate((other, "")):
        result, _log = _build(
            tmp_path / str(index), label=PINNED_DIGEST, base_label=DEFAULT_BASE,
            env_extra={"FAKE_BASE_IMAGE_ID_LABEL": label},
        )
        assert result.returncode == 2
        assert "does not record the identity of the base" in result.stderr
        assert f"expected: {BASE_ID}" in result.stderr


def test_a_base_that_cannot_be_identified_is_refused_before_the_build(tmp_path):
    result, log = _build(
        tmp_path, label=PINNED_DIGEST, base_label=DEFAULT_BASE, env_extra={"FAKE_BASE_IMAGE_ID": ""},
    )
    assert result.returncode == 2
    assert "unexpected base image identity" in result.stderr
    assert not any(line.startswith("buildx build") for line in log.read_text(encoding="utf-8").splitlines())


def test_a_digest_reference_is_its_own_identity(tmp_path):
    digest = "a" * 64
    result, log = _build(
        tmp_path, "--base-image", f"example.invalid/base@sha256:{digest}", label=PINNED_DIGEST,
        base_label=f"example.invalid/base@sha256:{digest}",
        env_extra={"FAKE_BASE_IMAGE_ID": "", "FAKE_BASE_IMAGE_ID_LABEL": f"sha256:{digest}"},
    )
    assert result.returncode == 0, result.stderr
    assert f"--build-arg TESTER_UNIFIED_BASE_IMAGE_ID=sha256:{digest}" in _buildx(log)
