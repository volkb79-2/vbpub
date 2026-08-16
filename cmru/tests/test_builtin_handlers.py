"""Tests for explicit CMRU command-library handlers and strict project config.

Projects compose handler commands in their own required step declarations; the
orchestrator never infers a step from an artifact label. Stdlib + tmp files only.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

import pytest

from cmru import cli, handlers, release


# ─── reusable wheel glue (cmru.release) ──────────────────────────────────────
def _make_wheel(path: Path, version: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"pkg-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: pkg\nVersion: {version}\n",
        )
    return path


def test_read_wheel_version(tmp_path):
    wheel = _make_wheel(tmp_path / "pkg-1.2.3-py3-none-any.whl", "1.2.3")
    assert release.read_wheel_version(wheel) == "1.2.3"


def test_find_built_wheel_single(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    w = _make_wheel(dist / "pkg-1.0.0-py3-none-any.whl", "1.0.0")
    assert release.find_built_wheel(dist, "pkg-*.whl") == w


def test_find_built_wheel_none_exits(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(SystemExit):
        release.find_built_wheel(dist, "pkg-*.whl")


def test_find_built_wheel_multiple_exits(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist / "pkg-1.0.0-py3-none-any.whl", "1.0.0")
    _make_wheel(dist / "pkg-2.0.0-py3-none-any.whl", "2.0.0")
    with pytest.raises(SystemExit):
        release.find_built_wheel(dist, "pkg-*.whl")


class _FakeGH:
    """Minimal stand-in exposing only resolve_latest (what the validator uses)."""

    def __init__(self, info):
        self._info = info
        self.calls = 0

    def resolve_latest(self, prefix):  # noqa: ARG002
        self.calls += 1
        return self._info


def test_validate_latest_release_ok():
    info = {
        "version": "1.0.0",
        "tag": "ciu-v1.0.0",
        "assets": [
            {"name": "ciu-1.0.0-py3-none-any.whl", "url": "http://x/whl"},
            {"name": "ciu-1.0.0-py3-none-any.whl.sha256", "url": "http://x/sha"},
        ],
    }
    out = release.validate_latest_release(_FakeGH(info), "ciu")
    assert out["version"] == "1.0.0"
    assert out["asset"] == "ciu-1.0.0-py3-none-any.whl"
    assert out["sha256_url"] == "http://x/sha"


def test_validate_latest_release_missing_sha256_exits():
    info = {
        "version": "1.0.0",
        "tag": "ciu-v1.0.0",
        "assets": [{"name": "ciu-1.0.0-py3-none-any.whl", "url": "http://x/whl"}],
    }
    with pytest.raises(SystemExit):
        release.validate_latest_release(_FakeGH(info), "ciu")


def test_validate_latest_release_none_exits():
    gh = _FakeGH(None)
    with pytest.raises(SystemExit):
        # retries=1, delay=0 so the test does not sleep
        release.validate_latest_release(gh, "ciu", retries=1, delay=0)
    assert gh.calls == 1


def test_check_build_prerequisites_requires_explicit_builder_image(monkeypatch):
    from cmru import exit_codes

    monkeypatch.delenv(handlers._WHEEL_BUILDER_IMAGE_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        handlers._check_build_prerequisites()
    assert exc.value.code == exit_codes.PREREQ_MISSING


def test_check_build_prerequisites_explicit_builder_image_is_accepted(monkeypatch):
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:test")
    monkeypatch.setattr(handlers.shutil, "which", lambda _name: "/usr/bin/docker")
    handlers._check_build_prerequisites()  # must not raise


def test_check_build_prerequisites_container_mode_needs_docker(monkeypatch):
    from cmru import exit_codes

    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:local")
    monkeypatch.setattr(handlers.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        handlers._check_build_prerequisites()
    assert exc.value.code == exit_codes.PREREQ_MISSING


def test_check_build_prerequisites_container_mode_docker_present(monkeypatch):
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:local")
    monkeypatch.setattr(handlers.shutil, "which", lambda name: "/usr/bin/docker")
    handlers._check_build_prerequisites()  # must not raise


def test_host_bind_source_resolves_bind_mount(monkeypatch):
    mountinfo = (
        "1996 1972 253:0 /home/vb/volkb79-2/vbpub /workspaces/vbpub rw,relatime "
        "- ext4 /dev/mapper/gstammtisch--vg-root rw,errors=remount-ro\n"
    )

    def fake_read_text(self, encoding="utf-8"):
        if str(self) == "/proc/self/mountinfo":
            return mountinfo
        raise OSError("unexpected path")

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    assert handlers._host_bind_source(Path("/workspaces/vbpub/cmru")) == \
        "/home/vb/volkb79-2/vbpub/cmru"
    assert handlers._host_bind_source(Path("/workspaces/vbpub")) == \
        "/home/vb/volkb79-2/vbpub"


def test_host_bind_source_rejects_unavailable_mountinfo(monkeypatch):
    def fake_read_text(self, encoding="utf-8"):
        raise OSError("no such file")

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    with pytest.raises(RuntimeError, match="refuses to guess"):
        handlers._host_bind_source(Path("/workspaces/vbpub/cmru"))


def test_cmd_wheel_build_refuses_the_retired_local_python_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv(handlers._WHEEL_BUILDER_IMAGE_ENV, raising=False)
    project = tmp_path / "cmru"
    project.mkdir()
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _cwd: tmp_path / ".git")

    with pytest.raises(SystemExit):
        handlers.cmd_wheel_build(argparse.Namespace(cwd=str(project)))


def test_cmd_wheel_build_container_mode(tmp_path, monkeypatch):
    project = tmp_path / "cmru"
    project.mkdir()
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:local")
    monkeypatch.setenv(handlers._DOCKER_CGROUP_PARENT_ENV, "dev-background.slice")
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _cwd: tmp_path / ".git")
    monkeypatch.setattr(handlers, "_host_bind_source", lambda p: f"/host{p}")
    monkeypatch.setattr(handlers, "_wheel_builder_git_mount_args", lambda _source, **_kw: [])
    calls = []
    monkeypatch.setattr(
        handlers.subprocess, "run",
        lambda argv, **kw: calls.append((argv, kw)),
    )
    handlers.cmd_wheel_build(argparse.Namespace(cwd=str(project)))
    assert len(calls) == 1
    argv, _kw = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--cgroup-parent") + 1] == "dev-background.slice"
    assert argv[argv.index("-v") + 1] == f"/host{project.parent}:{project.parent}"
    assert argv[argv.index("-w") + 1] == str(project.parent)
    assert "wheel-builder:local" in argv
    assert argv[-1] == str(project)


def test_cmd_wheel_build_container_mode_mounts_the_git_common_dir_too(tmp_path, monkeypatch):
    """The regression this guards: a wheel built inside the isolated release
    worktree's container, with only the worktree bind-mounted, cannot resolve
    git history at all (worktree .git is a pointer OUTSIDE that subtree). The
    builder must have that history to derive the intended package version."""
    project = tmp_path / "cmru"
    project.mkdir()
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:local")
    monkeypatch.setenv(handlers._DOCKER_CGROUP_PARENT_ENV, "dev-background.slice")
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _cwd: tmp_path / ".git")
    monkeypatch.setattr(handlers, "_host_bind_source", lambda p: f"/host{p}")
    monkeypatch.setattr(
        handlers, "_wheel_builder_git_mount_args",
        lambda _source, **_kw: ["-v", f"/host/common-git-dir:{tmp_path / '.gitcommon'}"],
    )
    calls = []
    monkeypatch.setattr(
        handlers.subprocess, "run",
        lambda argv, **kw: calls.append((argv, kw)),
    )
    handlers.cmd_wheel_build(argparse.Namespace(cwd=str(project)))
    argv, _kw = calls[0]
    assert f"-v" in argv
    assert f"/host/common-git-dir:{tmp_path / '.gitcommon'}" in argv


def test_git_common_dir_returns_none_outside_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        handlers.subprocess, "run",
        lambda *_a, **_kw: argparse.Namespace(returncode=128, stdout=""),
    )
    assert handlers._git_common_dir(tmp_path) is None


def test_git_common_dir_resolves_relative_output_against_cwd_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        handlers.subprocess, "run",
        lambda *_a, **_kw: argparse.Namespace(returncode=0, stdout=".git\n"),
    )
    assert handlers._git_common_dir(tmp_path) == (tmp_path / ".git").resolve()


def test_git_common_dir_passes_through_an_already_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        handlers.subprocess, "run",
        lambda *_a, **_kw: argparse.Namespace(returncode=0, stdout="/elsewhere/.git\n"),
    )
    assert handlers._git_common_dir(tmp_path) == Path("/elsewhere/.git")


def test_wheel_builder_git_mount_args_empty_when_common_dir_is_inside_cwd_parent(tmp_path, monkeypatch):
    """An ordinary (non-worktree) checkout: the common git dir is already covered
    by the existing cwd_parent mount — no second mount needed."""
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _p: tmp_path / ".git")
    assert handlers._wheel_builder_git_mount_args(tmp_path) == []


def test_wheel_builder_git_mount_args_mounts_when_common_dir_is_outside_cwd_parent(tmp_path, monkeypatch):
    """A release worktree: the common git dir lives in a completely different
    directory — it must be bind-mounted separately, at its own absolute path,
    so the worktree's .git pointer file resolves correctly inside the container."""
    common_dir = tmp_path / "elsewhere" / ".git"
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _p: common_dir)
    monkeypatch.setattr(handlers, "_host_bind_source", lambda p: f"/host{p}")

    worktree = tmp_path / "worktree"
    assert handlers._wheel_builder_git_mount_args(worktree) == [
        "-v", f"/host{common_dir}:{common_dir}",
    ]


def test_wheel_builder_git_mount_args_uses_project_git_for_a_standalone_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    common_dir = project / ".git"
    monkeypatch.setattr(handlers, "_git_common_dir", lambda source: common_dir if source == project else None)

    # The builder mounts the parent but resolves Git from the project itself.
    assert handlers._wheel_builder_git_mount_args(project, mount_root=tmp_path) == []


def test_wheel_builder_git_mount_args_rejects_unresolvable_git_common_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _p: None)
    with pytest.raises(RuntimeError, match="requires a Git worktree"):
        handlers._wheel_builder_git_mount_args(tmp_path)


def test_cmd_wheel_build_rejects_non_git_source(tmp_path, monkeypatch):
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:test")
    monkeypatch.setattr(handlers.shutil, "which", lambda _name: "/usr/bin/docker")
    project = tmp_path / "cmru"
    project.mkdir()
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _cwd: None)

    with pytest.raises(RuntimeError, match="requires a Git worktree"):
        handlers.cmd_wheel_build(argparse.Namespace(cwd=str(project)))


@pytest.mark.parametrize("command", [handlers.cmd_oci_image_build, handlers.cmd_oci_image_push])
def test_direct_oci_repack_handler_fails_before_side_effects(command, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(handlers.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(handlers, "_docker_login", lambda: calls.append("login"))
    args = type("Args", (), {
        "cwd": str(tmp_path),
        "bake_file": "docker-bake.hcl",
        "target": "img",
        "repack": True,
        "repack_target_size": "2GB",
        "repack_compression": 9,
    })()

    with pytest.raises(SystemExit) as exc:
        command(args)

    assert exc.value.code == 2
    assert calls == []


@pytest.mark.parametrize(
    ("command", "terminal_flag"),
    [
        (handlers.cmd_oci_image_build, "--load"),
        (handlers.cmd_oci_image_push, "--push"),
    ],
)
def test_direct_oci_non_repack_handler_keeps_standard_bake_flow(
    command, terminal_flag, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(handlers, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(handlers, "_docker_login", lambda: calls.append("login"))
    monkeypatch.setattr(
        handlers.subprocess,
        "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    args = type("Args", (), {
        "cwd": str(tmp_path),
        "bake_file": "docker-bake.hcl",
        "target": "img",
        "repack": False,
    })()

    command(args)

    assert calls[0] == "login"
    argv, kwargs = calls[1]
    assert argv == [
        "docker", "buildx", "bake", "-f", "docker-bake.hcl", "img", terminal_flag,
    ]
    assert kwargs == {"cwd": str(tmp_path), "check": True}


# ─── strict project-local config ─────────────────────────────────────────────
_BASE = """
schema_version = 1

[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = ["ghcr.io"]

[project]
id = "p"
description = "test product"
template_revision = 2
"""

_RUN_TESTS = """
[project.release]
git_tag = true
build_step = "build"

[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
"""

_OCI_RUN_TESTS = _RUN_TESTS.replace("git_tag = true", "git_tag = false")


def test_project_without_steps_is_rejected(tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
prefix = "p-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"

[project.release]
git_tag = true
build_step = "build"
""")
    # Removing the required project-owned step contract is a configuration error;
    # CMRU does not infer a build profile from an artifact type.
    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg)
    assert exc.value.code == 2


def test_explicit_oci_steps_load(tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
prefix = "p-v"
artifacts = ["oci-image"]
[project.version]
strategy = "none"
bump = "conventional"

[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
""" + _OCI_RUN_TESTS)
    _, projects, *_ = cli.load_config(cfg)
    assert set(projects["p"].steps) == {"run-tests", "build", "push"}
    assert projects["p"].artifacts == ("oci-image",)


def test_project_oci_table_is_rejected_as_inert_configuration(tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
prefix = "p-v"
artifacts = ["oci-image"]
[project.version]
strategy = "none"
[project.oci]
bake_file = "docker-bake.hcl"
target = "p"
repack = true
repack_target_size = "2GB"
repack_compression = 9

[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
""" + _OCI_RUN_TESTS)

    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg)
    assert exc.value.code == 2


# ─── find_artifact (generic discovery) ───────────────────────────────────────
def test_find_artifact_single(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    art = dist / "myproject-v1.2.3.tar.xz"
    art.write_bytes(b"data")
    assert release.find_artifact(dist, "myproject-v*.tar.xz") == art


def test_find_artifact_none_exits(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(SystemExit):
        release.find_artifact(dist, "myproject-v*.tar.xz")


def test_find_artifact_multiple_exits(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "myproject-v1.0.0.tar.xz").write_bytes(b"a")
    (dist / "myproject-v2.0.0.tar.xz").write_bytes(b"b")
    with pytest.raises(SystemExit):
        release.find_artifact(dist, "myproject-v*.tar.xz")


# find_built_wheel is now an alias — ensure it still works via find_artifact
def test_find_built_wheel_alias(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    w = _make_wheel(dist / "pkg-1.0.0-py3-none-any.whl", "1.0.0")
    assert release.find_built_wheel(dist, "pkg-*.whl") == w


# ─── explicit tarball-step validation ────────────────────────────────────────
_BUILD_STEP = """
[steps.build]
quiet = true
commands = [{ label = "build tarball", argv = ["bash", "scripts/build-artifact.sh"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push tarball", argv = ["true"], cwd = "." }]
"""


def test_project_without_a_required_release_phase_is_rejected(tmp_path):
    """Artifact kind never supplies a missing build phase implicitly."""
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
prefix = "p-v"
artifacts = ["tarball"]
[project.version]
strategy = "file:VERSION"
bump = "conventional"
""" + _RUN_TESTS)
    with pytest.raises(SystemExit) as exc:
        cli.load_config(cfg)
    assert exc.value.code == 2


def test_tarball_project_with_build_step_loads(tmp_path):
    """A tarball project WITH a [steps.build] loads successfully."""
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
prefix = "p-v"
artifacts = ["tarball"]
[project.version]
strategy = "file:VERSION"
bump = "conventional"
""" + _BUILD_STEP + _RUN_TESTS)
    _, projects, *_ = cli.load_config(cfg)
    assert projects["p"].artifacts == ("tarball",)
    assert "build" in projects["p"].steps
