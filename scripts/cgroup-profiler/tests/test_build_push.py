"""The image wrapper binds every build and publication to one source identity."""

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import subprocess

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("cgprofile_build_push", PROJECT / "build-push.py")
assert _SPEC is not None and _SPEC.loader is not None
build_push = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_push)


def test_local_build_passes_one_checked_version_and_commit_to_bake(monkeypatch):
    captured = {}
    monkeypatch.setenv("CGPROFILE_VERSION", "9.9.9")
    monkeypatch.setenv("GIT_REVISION", "untrusted")
    monkeypatch.setattr(build_push, "cmru_release_identity", lambda *, required: None)
    monkeypatch.setattr(
        build_push, "resolve_build_identity",
        lambda root, *, require_release_tag: ("0.0.0-dev", "a" * 40),
    )
    monkeypatch.setattr(
        build_push,
        "run",
        lambda argv, env, *, cwd: captured.update(argv=argv, env=env, cwd=cwd),
    )

    build_push.do_build()

    assert captured["argv"] == [
        "docker", "buildx", "bake", *build_push._FS_ALLOW, "all",
        "--set", "cgprofile-local.args.CGPROFILE_VERSION=0.0.0-dev",
        "--set", f"cgprofile-local.args.GIT_REVISION={'a' * 40}", "--load",
    ]
    assert captured["cwd"] == PROJECT
    assert "CGPROFILE_VERSION" not in captured["env"]
    assert "GIT_REVISION" not in captured["env"]


def test_cmru_build_uses_a_temporary_tree_at_the_protected_commit(monkeypatch, tmp_path):
    captured = {}
    commit = "b" * 40
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", "cgprofile-v1.2.3")
    monkeypatch.setattr(
        build_push, "cmru_release_identity",
        lambda *, required: ("1.2.3", commit, "cgprofile-v1.2.3"),
    )
    monkeypatch.setattr(build_push, "exact_build_source", lambda selected: nullcontext(tmp_path))
    monkeypatch.setattr(
        build_push,
        "run",
        lambda argv, env, *, cwd: captured.update(argv=argv, env=env, cwd=cwd),
    )

    build_push.do_build()

    assert captured["cwd"] == tmp_path
    assert f"cgprofile-local.args.GIT_REVISION={commit}" in captured["argv"]
    assert "CMRU_RELEASE_COMMIT" not in captured["env"]
    assert "CMRU_RELEASE_TAG" not in captured["env"]


def test_temporary_build_tree_contains_the_exact_full_repository_commit(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    project = repo / "scripts" / "cgroup-profiler"
    analysis = repo / "scripts" / "damon-analysis"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (analysis / "lib").mkdir(parents=True)
    (analysis / "lib" / "damon_analysis.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "build source"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    monkeypatch.setattr(build_push, "HERE", project)

    with build_push.exact_build_source(commit) as source:
        assert source == source.parents[1] / "scripts" / "cgroup-profiler"
        assert build_push.git_revision(source) == commit
        assert (source / "Dockerfile").is_file()
        assert (source.parent / "damon-analysis" / "lib" / "damon_analysis.py").is_file()
        temporary_root = source.parents[1]
        assert str(temporary_root) in subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo,
            check=True, capture_output=True, text=True,
        ).stdout

    assert str(temporary_root) not in subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout


def test_push_passes_protected_commit_to_login_and_exact_build(monkeypatch, tmp_path):
    captured = {}
    commit = "c" * 40
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", commit)
    monkeypatch.setenv("CMRU_RELEASE_TAG", "cgprofile-v1.2.3")
    monkeypatch.setenv("CGPROFILE_VERSION", "9.9.9")
    monkeypatch.setenv("GIT_REVISION", "untrusted")
    monkeypatch.setenv("GITHUB_USERNAME", "release-user")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "test-token")
    monkeypatch.setattr(
        build_push, "cmru_release_identity",
        lambda *, required: ("1.2.3", commit, "cgprofile-v1.2.3"),
    )
    monkeypatch.setattr(build_push, "exact_build_source", lambda selected: nullcontext(tmp_path))
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda argv, **kwargs: captured.update(login=argv, login_options=kwargs),
    )
    monkeypatch.setattr(
        build_push,
        "run",
        lambda argv, env, *, cwd: captured.update(bake=argv, env=env, cwd=cwd),
    )

    build_push.do_push()

    assert captured["login"] == ["docker", "login", "ghcr.io", "-u", "release-user", "--password-stdin"]
    assert captured["login_options"]["input"] == b"test-token"
    assert "GITHUB_PUSH_PAT" not in captured["login_options"]["env"]
    assert captured["cwd"] == tmp_path
    assert captured["bake"] == [
        "docker", "buildx", "bake", *build_push._FS_ALLOW,
        "cgprofile-release",
        "--set", "cgprofile-release.args.CGPROFILE_VERSION=1.2.3",
        "--set", f"cgprofile-release.args.GIT_REVISION={commit}",
        "--set", "cgprofile-release.tags=ghcr.io/volkb79-2/cgprofile:1.2.3",
        "--push",
    ]
    for name in ("CGPROFILE_VERSION", "GIT_REVISION", "CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG", "GITHUB_PUSH_PAT"):
        assert name not in captured["env"]


def test_push_refuses_absent_or_incomplete_cmru_identity_before_login(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(build_push, "HERE", tmp_path)
    monkeypatch.delenv("CMRU_RELEASE_COMMIT", raising=False)
    monkeypatch.delenv("CMRU_RELEASE_TAG", raising=False)
    monkeypatch.setenv("CGPROFILE_VERSION", "1.0.0")
    monkeypatch.setenv("GITHUB_USERNAME", "release-user")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "test-token")
    monkeypatch.setattr(
        build_push.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("registry login or build ran before identity validation"),
    )

    with pytest.raises(SystemExit) as exc_info:
        build_push.do_push()

    assert exc_info.value.code == 1
    assert "CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG are required" in capsys.readouterr().err


def test_bake_file_uses_development_defaults_without_ambient_release_variables():
    bake = (PROJECT / "docker-bake.hcl").read_text(encoding="utf-8")

    assert 'variable "CGPROFILE_VERSION"' not in bake
    assert 'variable "GIT_REVISION"' not in bake
    assert 'CGPROFILE_VERSION = "0.0.0-dev"' in bake
    assert 'GIT_REVISION       = "unknown"' in bake
    assert 'tags = ["ghcr.io/volkb79-2/cgprofile:0.0.0-dev"]' in bake
    assert "${CGPROFILE_VERSION}" not in bake


def test_release_identity_handoff_is_documented_on_all_consumer_surfaces():
    root = PROJECT
    documents = (
        root / "README.md",
        root / "docs" / "DESIGN-GUIDE.md",
        root / "docs" / "CONSUMERS.md",
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert "CMRU_RELEASE_COMMIT" in text, path
        assert "CMRU_RELEASE_TAG" in text, path
        assert "cmru release" in text, path
        assert "not an authentication token" in text, path
