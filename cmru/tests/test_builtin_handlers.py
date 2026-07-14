"""Tests for S-REL batteries-included profile handlers (P8).

Covers the reusable wheel glue in cmru.release, the built-in step synthesis in
cmru.cli, and the load_config relaxation that lets a profile-only project omit steps.
Stdlib + tmp files only — no network, no git, no subprocess.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from cmru import cli, release


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


# ─── built-in step synthesis (cmru.cli) ──────────────────────────────────────
def test_bare_prefix():
    assert cli._bare_prefix("ciu-v") == "ciu"
    assert cli._bare_prefix("tls-edge-v") == "tls-edge"
    assert cli._bare_prefix(None) == ""


def _wheel_project(tmp_path):
    return cli.ProjectConfig(
        name="demo", env={}, steps={}, prefix="demo-v", cwd="demo",
        artifacts=("wheel",),
    )


def test_builtin_step_command_wheel(tmp_path):
    proj = _wheel_project(tmp_path)
    build = cli._builtin_step_command(proj, "build", tmp_path)
    push = cli._builtin_step_command(proj, "push", tmp_path)
    validate = cli._builtin_step_command(proj, "validate", tmp_path)

    assert build.argv[1:] == [str(cli._HANDLERS_PY), "wheel-build",
                              "--cwd", str((tmp_path / "demo").resolve())]
    assert "wheel-publish" in push.argv
    assert "--prefix" in push.argv and "demo" in push.argv
    assert "--notes-env" in push.argv and "DEMO_RELEASE_NOTES" in push.argv
    assert validate.argv[1:] == [str(cli._HANDLERS_PY), "wheel-validate", "--prefix", "demo"]


def test_builtin_step_command_unknown_step_is_none(tmp_path):
    proj = _wheel_project(tmp_path)
    assert cli._builtin_step_command(proj, "run-tests", tmp_path) is None


def test_builtin_step_command_oci_uses_oci_defaults(tmp_path):
    proj = cli.ProjectConfig(name="img", env={}, steps={}, prefix="img-v",
                             cwd="img", artifacts=("oci-image",), mint_tag=False)
    build = cli._builtin_step_command(proj, "build", tmp_path)
    push = cli._builtin_step_command(proj, "push", tmp_path)

    assert build.argv[1:] == [
        str(cli._HANDLERS_PY), "oci-image-build",
        "--cwd", str((tmp_path / "img").resolve()),
        "--bake-file", "docker-bake.hcl", "--target", "img",
    ]
    assert push.argv[1:] == [
        str(cli._HANDLERS_PY), "oci-image-push",
        "--cwd", str((tmp_path / "img").resolve()),
        "--bake-file", "docker-bake.hcl", "--target", "img",
    ]


# ─── load_config relaxation ──────────────────────────────────────────────────
_BASE = """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = ["ghcr.io"]
[orchestration]
project_order = ["p"]
default_steps = ["build", "push"]
execution_mode = "project-first"
[cleanup]
keep_release_tags = ["p-latest"]
"""


def test_wheel_project_without_steps_loads(tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
[project.p]
prefix = "p-v"
artifacts = ["wheel"]
cwd = "p"
[project.p.version]
strategy = "scm"
""")
    _, projects, *_ = cli.load_config(cfg)
    assert projects["p"].steps == {}            # no inline steps
    assert projects["p"].artifacts == ("wheel",)
    # the built-in supplies the build step
    assert cli._builtin_step_command(projects["p"], "build", tmp_path) is not None


def test_oci_project_without_steps_loads(tmp_path):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
[project.p]
prefix = "p-v"
artifacts = ["oci-image"]
cwd = "p"
[project.p.version]
strategy = "none"
""")
    _, projects, *_ = cli.load_config(cfg)
    assert projects["p"].steps == {}
    assert projects["p"].artifacts == ("oci-image",)
    assert cli._builtin_step_command(projects["p"], "build", tmp_path) is not None
    assert cli._builtin_step_command(projects["p"], "push", tmp_path) is not None


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


# ─── tarball built-in step synthesis ─────────────────────────────────────────
def _tarball_project(tmp_path):
    """A synthetic tarball project (prefix=myapp-v, cwd=myapp, artifacts=(tarball,))."""
    return cli.ProjectConfig(
        name="myapp", env={}, steps={}, prefix="myapp-v", cwd="myapp",
        artifacts=("tarball",),
    )


def test_builtin_step_command_tarball_push(tmp_path):
    proj = _tarball_project(tmp_path)
    push = cli._builtin_step_command(proj, "push", tmp_path)
    assert push is not None
    assert "tarball-publish" in push.argv
    assert "--prefix" in push.argv
    assert "myapp" in push.argv
    assert "--glob" in push.argv
    # glob contains bare prefix and v*.tar.xz pattern
    glob_idx = push.argv.index("--glob") + 1
    assert "myapp-v*.tar.xz" == push.argv[glob_idx]
    assert "--version-file" in push.argv
    assert "--notes-env" in push.argv
    assert "MYAPP_RELEASE_NOTES" in push.argv


def test_builtin_step_command_tarball_validate(tmp_path):
    proj = _tarball_project(tmp_path)
    validate = cli._builtin_step_command(proj, "validate", tmp_path)
    assert validate is not None
    assert "tarball-validate" in validate.argv
    assert "--prefix" in validate.argv
    assert "myapp" in validate.argv


def test_builtin_step_command_tarball_build_is_none(tmp_path):
    proj = _tarball_project(tmp_path)
    # tarball has no built-in build
    assert cli._builtin_step_command(proj, "build", tmp_path) is None


# ─── load_config: tarball project validation ─────────────────────────────────
_BUILD_STEP = """
[[project.p.steps.build.commands]]
label = "build tarball"
argv = ["bash", "scripts/build-artifact.sh"]
cwd = "p"
"""


def test_tarball_project_without_build_step_rejected(tmp_path):
    """A tarball project with no [steps.build] is rejected at config load time."""
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
[project.p]
prefix = "p-v"
artifacts = ["tarball"]
cwd = "p"
[project.p.version]
strategy = "file:VERSION"
""")
    with pytest.raises(ValueError, match="define \\[steps"):
        cli.load_config(cfg)


def test_tarball_project_with_build_step_loads(tmp_path):
    """A tarball project WITH a [steps.build] loads successfully."""
    # Create the project cwd so resolve_cwd doesn't fail
    (tmp_path / "p").mkdir()
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(_BASE + """
[project.p]
prefix = "p-v"
artifacts = ["tarball"]
cwd = "p"
[project.p.version]
strategy = "file:VERSION"
""" + _BUILD_STEP)
    _, projects, *_ = cli.load_config(cfg)
    assert projects["p"].artifacts == ("tarball",)
    assert "build" in projects["p"].steps
    # push and validate are built-in
    assert cli._builtin_step_command(projects["p"], "push", tmp_path) is not None
    assert cli._builtin_step_command(projects["p"], "validate", tmp_path) is not None
    assert cli._builtin_step_command(projects["p"], "build", tmp_path) is None
