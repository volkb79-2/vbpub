"""The image wrapper carries one resolved release version into bake."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("cgprofile_build_push", PROJECT / "build-push.py")
assert _SPEC is not None and _SPEC.loader is not None
build_push = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_push)


def test_build_passes_tag_resolved_version_to_bake(monkeypatch):
    captured = {}
    monkeypatch.setenv("CGPROFILE_VERSION", "9.9.9")
    monkeypatch.setenv("GIT_REVISION", "untrusted")
    monkeypatch.setattr(
        build_push, "resolve_build_version",
        lambda root, *, require_release_tag: "1.1.0",
    )
    monkeypatch.setattr(build_push, "git_revision", lambda: "deadbeef")
    monkeypatch.setattr(
        build_push, "run",
        lambda argv, env: captured.update(argv=argv, env=env),
    )

    build_push.do_build()

    assert captured["argv"] == [
        "docker", "buildx", "bake", *build_push._FS_ALLOW, "all",
        "--set", "cgprofile-local.args.CGPROFILE_VERSION=1.1.0",
        "--set", "cgprofile-local.args.GIT_REVISION=deadbeef", "--load",
    ]
    assert "CGPROFILE_VERSION" not in captured["env"]
    assert "GIT_REVISION" not in captured["env"]


def test_push_passes_resolved_version_to_login_and_bake(monkeypatch):
    captured = {}
    monkeypatch.setenv("CGPROFILE_VERSION", "9.9.9")
    monkeypatch.setenv("GIT_REVISION", "untrusted")
    monkeypatch.setenv("GITHUB_USERNAME", "release-user")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "test-token")
    monkeypatch.setattr(
        build_push, "resolve_build_version",
        lambda root, *, require_release_tag: "1.1.0",
    )
    monkeypatch.setattr(build_push, "git_revision", lambda: "deadbeef")
    monkeypatch.setattr(
        build_push.subprocess, "run",
        lambda argv, **kwargs: captured.update(login=argv, login_options=kwargs),
    )
    monkeypatch.setattr(
        build_push, "run",
        lambda argv, env: captured.update(bake=argv, env=env),
    )

    build_push.do_push()

    assert captured["login"] == ["docker", "login", "ghcr.io", "-u", "release-user", "--password-stdin"]
    assert captured["login_options"]["input"] == b"test-token"
    assert captured["bake"] == [
        "docker", "buildx", "bake", *build_push._FS_ALLOW,
        "cgprofile-release",
        "--set", "cgprofile-release.args.CGPROFILE_VERSION=1.1.0",
        "--set", "cgprofile-release.args.GIT_REVISION=deadbeef",
        "--set", "cgprofile-release.tags=ghcr.io/volkb79-2/cgprofile:1.1.0",
        "--push",
    ]
    assert "CGPROFILE_VERSION" not in captured["env"]
    assert "GIT_REVISION" not in captured["env"]


def test_bake_file_uses_development_defaults_without_ambient_release_variables():
    bake = (PROJECT / "docker-bake.hcl").read_text(encoding="utf-8")

    assert 'variable "CGPROFILE_VERSION"' not in bake
    assert 'variable "GIT_REVISION"' not in bake
    assert 'CGPROFILE_VERSION = "0.0.0-dev"' in bake
    assert 'GIT_REVISION       = "unknown"' in bake
    assert 'tags = ["ghcr.io/volkb79-2/cgprofile:0.0.0-dev"]' in bake
    assert "${CGPROFILE_VERSION}" not in bake


def test_push_reports_absent_release_version_without_login(monkeypatch, capsys):
    def refuse_version(root, *, require_release_tag):
        raise RuntimeError(
            "no cgprofile-v<version> tag points at HEAD; "
            "refuse publication without an exact release tag"
        )

    monkeypatch.setattr(build_push, "resolve_build_version", refuse_version)
    monkeypatch.setattr(build_push.subprocess, "run", lambda *args, **kwargs: pytest.fail("login ran"))

    with pytest.raises(SystemExit) as exc_info:
        build_push.do_push()

    assert exc_info.value.code == 1
    assert "no cgprofile-v<version> tag points at HEAD" in capsys.readouterr().err


def test_push_refuses_ambient_version_before_authentication_or_login(
    tmp_path, monkeypatch, capsys,
):
    import subprocess

    from lib import version

    (tmp_path / "cmru.toml").write_text(
        '[project]\nprefix = "cgprofile-v"\n', encoding="utf-8"
    )
    monkeypatch.setattr(build_push, "HERE", tmp_path)
    monkeypatch.setenv("CGPROFILE_VERSION", "1.0.0")

    def no_release_tag(argv, **kwargs):
        assert argv == ["git", "tag", "--points-at", "HEAD"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(version.subprocess, "run", no_release_tag)

    with pytest.raises(SystemExit) as exc_info:
        build_push.do_push()

    assert exc_info.value.code == 1
    assert "without an exact release tag" in capsys.readouterr().err
