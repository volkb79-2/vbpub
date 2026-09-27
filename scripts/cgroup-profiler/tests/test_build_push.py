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
    monkeypatch.setattr(
        build_push, "resolve_build_version",
        lambda root, *, require_release_tag, environ: "1.0.0",
    )
    monkeypatch.setattr(build_push, "git_revision", lambda: "deadbeef")
    monkeypatch.setattr(
        build_push, "run",
        lambda argv, env: captured.update(argv=argv, env=env),
    )

    build_push.do_build()

    assert captured["argv"] == ["docker", "buildx", "bake", *build_push._FS_ALLOW, "all", "--load"]
    assert captured["env"]["CGPROFILE_VERSION"] == "1.0.0"
    assert captured["env"]["GIT_REVISION"] == "deadbeef"


def test_push_passes_resolved_version_to_login_and_bake(monkeypatch):
    captured = {}
    monkeypatch.setenv("GITHUB_USERNAME", "release-user")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "test-token")
    monkeypatch.setattr(
        build_push, "resolve_build_version",
        lambda root, *, require_release_tag, environ: "1.0.0",
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
    assert captured["bake"] == ["docker", "buildx", "bake", *build_push._FS_ALLOW, "all", "--push"]
    assert captured["env"]["CGPROFILE_VERSION"] == "1.0.0"
    assert captured["env"]["GIT_REVISION"] == "deadbeef"


def test_push_reports_absent_release_version_without_login(monkeypatch, capsys):
    def refuse_version(root, *, require_release_tag, environ):
        raise RuntimeError("no cgprofile-v<version> tag points at HEAD")

    monkeypatch.setattr(build_push, "resolve_build_version", refuse_version)
    monkeypatch.setattr(build_push.subprocess, "run", lambda *args, **kwargs: pytest.fail("login ran"))

    with pytest.raises(SystemExit) as exc_info:
        build_push.do_push()

    assert exc_info.value.code == 1
    assert "no cgprofile-v<version> tag points at HEAD" in capsys.readouterr().err
