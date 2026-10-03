from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import config, git_auth, transaction, version


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://github.com/acme/vbpub.git", True),
        ("https://person@github.com/ACME/vbpub", True),
        ("https://github.com/acme/vbpub.git/", True),
        ("git@github.com:acme/vbpub.git", False),
        ("https://github.com/acme/other.git", False),
        ("https://person:secret@github.com/acme/vbpub.git", False),
        ("https://github.com/acme%2fother/vbpub.git", False),
        ("https://github.com/acme%5cother/vbpub.git", False),
        ("https://github.com/acme//vbpub.git", False),
        ("https://github.com//", False),
        ("https://github.com", False),
        ("https://github.com/acme", False),
        ("https://example.invalid/acme/vbpub.git", False),
        ("http://github.com/acme/vbpub.git", False),
        ("https://github.com:444/acme/vbpub.git", False),
        ("https://github.com:not-a-port/acme/vbpub.git", False),
        ("https://github.com/acme/vbpub.git?redirect=other", False),
        ("https://github.com/acme/vbpub.git#fragment", False),
    ],
)
def test_github_https_origin_matching_is_exact(origin, expected):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="secret")

    assert "secret" not in repr(auth)
    assert git_auth.github_https_origin_matches(origin, auth) is expected


@pytest.mark.parametrize(
    ("owner", "repo"),
    [
        ("", "vbpub"), ("acme", ""),
        ("acme/other", "vbpub"), ("acme\\other", "vbpub"),
        ("acme", "vbpub/other"), ("acme", "vbpub\\other"),
    ],
)
def test_github_https_origin_matching_rejects_invalid_configured_path_parts(owner, repo):
    auth = git_auth.GitHubGitAuth(owner=owner, repo=repo, token="secret")

    assert not git_auth.github_https_origin_matches(
        "https://github.com/acme/vbpub.git", auth,
    )


def test_askpass_reads_token_at_prompt_time_without_embedding_it(tmp_path, monkeypatch):
    token = "pat-that-must-not-appear-in-the-helper"
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token=token)
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda _root, *, for_push: ["https://github.com/acme/vbpub.git"],
    )
    with git_auth._git_environment(tmp_path, auth, for_push=False) as environment:
        assert environment is not None
        helper = Path(environment["GIT_ASKPASS"])
        source = helper.read_text(encoding="utf-8")
        assert token not in source
        assert helper.stat().st_mode & 0o777 == 0o700
        compile(source, str(helper), "exec")

        child_environment = os.environ.copy()
        child_environment.update(environment)
        password = subprocess.run(
            [str(helper), "Password for 'https://cmru@github.com':"],
            capture_output=True, text=True, env=child_environment, check=True,
        )
        username = subprocess.run(
            [str(helper), "Username for 'https://github.com':"],
            capture_output=True, text=True, env=child_environment, check=True,
        )
        assert password.stdout == token
        assert username.stdout == "cmru"
        token_prompt = subprocess.run(
            [str(helper), "Token for 'https://github.com':"],
            capture_output=True, text=True, env=child_environment, check=True,
        )
        assert token_prompt.stdout == token
        foreign = subprocess.run(
            [str(helper), "Password for 'https://cmru@uploads.example.invalid':"],
            capture_output=True, text=True, env=child_environment, check=False,
        )
        assert foreign.returncode != 0
        assert foreign.stdout == ""
        no_credential = child_environment.copy()
        no_credential.pop("CMRU_GIT_AUTH_TOKEN")
        missing = subprocess.run(
            [str(helper), "Password for 'https://cmru@github.com':"],
            capture_output=True, text=True, env=no_credential, check=False,
        )
        assert missing.returncode != 0
        unrecognized = subprocess.run(
            [str(helper), "Confirm access to 'https://github.com':"],
            capture_output=True, text=True, env=child_environment, check=False,
        )
        assert unrecognized.returncode != 0
        no_url = subprocess.run(
            [str(helper), "Password:"],
            capture_output=True, text=True, env=child_environment, check=False,
        )
        assert no_url.returncode != 0
        malformed_url = subprocess.run(
            [str(helper), "Password for 'https://[bad':"],
            capture_output=True, text=True, env=child_environment, check=False,
        )
        assert malformed_url.returncode != 0
    assert not helper.exists()


def test_run_remote_git_uses_secret_only_for_matching_origin(monkeypatch, tmp_path):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="secret")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "publisher-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "stale-private-secret")
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda _root, *, for_push: ["https://github.com/acme/vbpub.git"],
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)
    result = git_auth.run_remote_git(
        tmp_path, "push", "origin", "HEAD:refs/heads/main", auth=auth,
        capture_output=True, text=True,
    )

    assert result.returncode == 0
    argv, kwargs = calls[0]
    assert argv[:4] == ["git", "-c", "credential.helper=", "-c"]
    hooks_path = Path(argv[4].removeprefix("core.hooksPath="))
    assert argv[4].startswith("core.hooksPath=")
    assert hooks_path.is_dir()
    assert not (hooks_path / "pre-push").exists()
    assert argv[5:] == ["push", "origin", "HEAD:refs/heads/main"]
    assert kwargs["env"]["CMRU_GIT_AUTH_TOKEN"] == "secret"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITHUB_PUSH_PAT" not in kwargs["env"]
    assert "GITHUB_TOKEN" not in kwargs["env"]
    assert os.environ.get("CMRU_GIT_AUTH_TOKEN") == "stale-private-secret"


def test_run_local_git_strips_publisher_tokens_from_hook_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_PUSH_PAT", "publisher-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "api-secret")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "transport-secret")
    monkeypatch.setenv("CMRU_UNRELATED_SETTING", "preserved")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)

    result = git_auth.run_local_git(tmp_path, "commit", "-m", "prepare", check=True)

    assert result.returncode == 0
    argv, kwargs = calls[0]
    assert argv == ["git", "commit", "-m", "prepare"]
    assert kwargs["cwd"] == tmp_path
    assert "PATH" in kwargs["env"]
    assert kwargs["env"]["CMRU_UNRELATED_SETTING"] == "preserved"
    assert not {"GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN"} & kwargs["env"].keys()


def test_run_local_git_scrubs_caller_supplied_environment_without_mutating_it(
    monkeypatch, tmp_path,
):
    supplied = {
        "GITHUB_PUSH_PAT": "publisher-secret",
        "GITHUB_TOKEN": "api-secret",
        "CMRU_GIT_AUTH_TOKEN": "transport-secret",
        "KEEP": "value",
    }
    calls = []
    monkeypatch.setattr(
        git_auth.subprocess, "run",
        lambda argv, **kwargs: calls.append((argv, kwargs))
        or subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    git_auth.run_local_git(tmp_path, "status", env=supplied)

    assert calls[0][1]["env"] == {"KEEP": "value"}
    assert supplied["GITHUB_PUSH_PAT"] == "publisher-secret"


def test_without_publisher_tokens_temporarily_clears_and_restores_only_present_keys(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_PUSH_PAT", "publisher-secret")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "transport-secret")
    monkeypatch.setenv("KEEP", "value")

    with git_auth.without_publisher_tokens():
        assert not {"GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN"} & os.environ.keys()
        assert os.environ["KEEP"] == "value"

    assert os.environ["GITHUB_PUSH_PAT"] == "publisher-secret"
    assert "GITHUB_TOKEN" not in os.environ
    assert os.environ["CMRU_GIT_AUTH_TOKEN"] == "transport-secret"


def test_local_commit_hook_runs_without_publisher_tokens(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "config", "user.name", "cmru test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "cmru@example.invalid"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    observed = repo / "hook-env.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '%s|%s|%s' \"${{GITHUB_PUSH_PAT-unset}}\" "
        "\"${GITHUB_TOKEN-unset}\" \"${CMRU_GIT_AUTH_TOKEN-unset}\" > hook-env.txt\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hook.parent)], cwd=repo, check=True,
    )
    monkeypatch.setenv("GITHUB_PUSH_PAT", "publisher-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "api-secret")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "transport-secret")

    git_auth.run_local_git(repo, "commit", "-m", "initial", check=True)

    assert observed.read_text(encoding="utf-8") == "unset|unset|unset"


def test_release_tag_reference_hook_runs_without_publisher_tokens(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "config", "user.name", "cmru test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "cmru@example.invalid"], cwd=repo, check=True)
    hook_dir = repo / ".git" / "hooks"
    hook_dir.mkdir(exist_ok=True)
    observed = tmp_path / "reference-hook-env.txt"
    hook = hook_dir / "reference-transaction"
    hook.write_text(
        "#!/bin/sh\n"
        "printf '%s|%s|%s' \"${GITHUB_PUSH_PAT-unset}\" "
        "\"${GITHUB_TOKEN-unset}\" \"${CMRU_GIT_AUTH_TOKEN-unset}\" "
        "> \"$CMRU_TEST_HOOK_OUT\"\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "publisher-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "api-secret")
    monkeypatch.setenv("CMRU_GIT_AUTH_TOKEN", "transport-secret")
    monkeypatch.setenv("CMRU_TEST_HOOK_OUT", str(observed))

    assert version._apply_strategy_scm(repo, "demo-v", "1.0.0") == "demo-v1.0.0"

    assert observed.read_text(encoding="utf-8") == "unset|unset|unset"


def test_run_remote_git_fetch_uses_fetch_origin_auth_scope(monkeypatch, tmp_path):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="secret")
    origins = []
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda _root, *, for_push: origins.append(for_push) or ["https://github.com/acme/vbpub.git"],
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)

    result = git_auth.run_remote_git(tmp_path, "fetch", "--prune", "origin", auth=auth)

    assert result.returncode == 0
    assert origins == [False]
    assert calls[0][0][1:3] == ["-c", "credential.helper="]
    assert any(value.startswith("core.hooksPath=") for value in calls[0][0])


def test_pushurl_mismatch_never_receives_repository_token(monkeypatch, tmp_path):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="secret")
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda _root, *, for_push: [
            "https://github.com/acme/vbpub.git",
            "https://example.invalid/mirror/vbpub.git",
        ],
    )
    with git_auth._git_environment(tmp_path, auth, for_push=True) as environment:
        assert environment is None


def test_git_environment_without_origin_does_not_create_helper(monkeypatch, tmp_path):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="secret")
    monkeypatch.setattr(git_auth, "_origin_urls", lambda *_args, **_kwargs: [])

    with git_auth._git_environment(tmp_path, auth, for_push=False) as environment:
        assert environment is None


@pytest.mark.parametrize(
    "auth",
    [None, git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="")],
)
def test_git_environment_without_resolved_token_does_not_create_helper(monkeypatch, tmp_path, auth):
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda *_args, **_kwargs: pytest.fail("no credential should inspect the origin"),
    )

    with git_auth._git_environment(tmp_path, auth, for_push=False) as environment:
        assert environment is None


@pytest.mark.parametrize("for_push", [False, True])
def test_origin_urls_selects_fetch_or_push_remote_and_filters_empty_lines(
    monkeypatch, tmp_path, for_push,
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="https://github.com/acme/vbpub.git\n\n", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)

    assert git_auth._origin_urls(tmp_path, for_push=for_push) == [
        "https://github.com/acme/vbpub.git",
    ]
    expected = ["git", "remote", "get-url"]
    if for_push:
        expected.append("--push")
    expected.extend(("--all", "origin"))
    assert calls[0][0] == expected


def test_origin_urls_returns_empty_when_git_cannot_resolve_origin(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git_auth.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 2, stdout="", stderr="missing"),
    )

    assert git_auth._origin_urls(tmp_path, for_push=False) == []


def test_run_remote_git_does_not_forward_tokens_to_a_different_remote(monkeypatch, tmp_path):
    auth = git_auth.GitHubGitAuth(owner="acme", repo="vbpub", token="root-secret")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "project-publisher-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "root-secret")
    monkeypatch.setattr(
        git_auth, "_origin_urls",
        lambda _root, *, for_push: ["https://example.invalid/acme/vbpub.git"],
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)
    git_auth.run_remote_git(tmp_path, "push", "origin", "HEAD:main", auth=auth)

    argv, kwargs = calls[0]
    assert argv == ["git", "push", "origin", "HEAD:main"]
    assert "GITHUB_PUSH_PAT" not in kwargs["env"]
    assert "GITHUB_TOKEN" not in kwargs["env"]
    assert "CMRU_GIT_AUTH_TOKEN" not in kwargs["env"]


def test_run_remote_git_uses_explicit_environment_even_without_git_arguments(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_auth.subprocess, "run", fake_run)

    result = git_auth.run_remote_git(tmp_path, auth=None, env={"SENTINEL": "provided"})

    assert result.returncode == 0
    assert calls[0][0] == ["git"]
    assert calls[0][1]["env"] == {"SENTINEL": "provided"}


def test_secret_document_accepts_and_validates_schema_version_one(tmp_path):
    secret = tmp_path / "cmru.secret.toml"
    secret.write_text(
        'schema_version = 1\n[github]\ntoken = "token"\n', encoding="utf-8",
    )

    assert config._read_secret_document(secret) == {
        "schema_version": 1, "github": {"token": "token"},
    }

    for version in ("2", "true", '"1"'):
        secret.write_text(
            f"schema_version = {version}\n[github]\ntoken = \"token\"\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            config._read_secret_document(secret)


def test_run_child_self_release_imports_candidate_cmru_source(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate"
    for relative in (
        "cmru/src/cmru/cli.py",
        "libraries/worktree/src",
        "libraries/cli-extended/src",
    ):
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix:
            path.write_text("# candidate source\n", encoding="utf-8")

    observed = {}
    monkeypatch.setenv("PYTHONPATH", "/inherited/python/path")
    monkeypatch.setenv("CMRU_BIN", "/opt/cmru/bin/cmru")

    def fake_run(argv, *, cwd, env):
        observed.update(argv=argv, cwd=cwd, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    workspace = SimpleNamespace(
        path=candidate,
        branch="cmru-release-test",
        base="a" * 40,
        workspace_id="test-id",
        repo_root=tmp_path,
    )

    assert transaction.run_child(workspace, ["cmru"], project_names=["cmru"]) == 0
    assert observed["cwd"] == candidate
    assert observed["argv"][0] == "/opt/cmru/bin/cmru"
    assert observed["env"]["CMRU_TRANSACTION_PROJECTS"] == "cmru"
    assert observed["env"]["PYTHONPATH"].split(os.pathsep)[:3] == [
        str(candidate / "cmru" / "src"),
        str(candidate / "libraries" / "worktree" / "src"),
        str(candidate / "libraries" / "cli-extended" / "src"),
    ]
    assert observed["env"]["PYTHONPATH"].endswith("/inherited/python/path")


@pytest.mark.parametrize("project_names", [None, [], ["other"], ["cmru"]])
def test_run_child_uses_only_the_candidate_roots_that_exist(
    monkeypatch, tmp_path, project_names,
):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    if project_names == ["cmru"]:
        cli_file = candidate / "cmru" / "src" / "cmru" / "cli.py"
        cli_file.parent.mkdir(parents=True)
        cli_file.write_text("# source", encoding="utf-8")
    observed = {}

    def fake_run(argv, *, cwd, env):
        observed.update(argv=argv, cwd=cwd, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("CMRU_BIN", raising=False)
    monkeypatch.setattr(transaction.shutil, "which", lambda _name: None)
    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    workspace = SimpleNamespace(
        path=candidate, branch="cmru-release-test", base="a" * 40,
        workspace_id=None, repo_root=tmp_path,
    )

    assert transaction.run_child(workspace, ["cmru"], project_names=project_names) == 0

    assert observed["argv"][0] == "cmru"
    if project_names == ["cmru"]:
        assert observed["env"]["PYTHONPATH"] == str(candidate / "cmru" / "src")
    else:
        assert observed["env"].get("PYTHONPATH") is None
    assert "CMRU_WORKSPACE_ID" not in observed["env"]
    if project_names is None:
        assert "CMRU_TRANSACTION_PROJECTS" not in observed["env"]
    else:
        assert observed["env"]["CMRU_TRANSACTION_PROJECTS"] == ",".join(project_names)


def test_run_child_uses_path_cli_when_no_explicit_cmru_bin(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.delenv("CMRU_BIN", raising=False)
    monkeypatch.setattr(transaction.shutil, "which", lambda _name: "/found/cmru")
    monkeypatch.setattr(
        transaction.subprocess, "run",
        lambda argv, *, cwd, env: observed.update(argv=argv, cwd=cwd, env=env)
        or SimpleNamespace(returncode=0),
    )
    workspace = SimpleNamespace(
        path=tmp_path, branch="cmru-release-test", base="a" * 40,
        workspace_id=None, repo_root=tmp_path,
    )

    assert transaction.run_child(workspace, ["cmru"]) == 0
    assert observed["argv"][0] == "/found/cmru"
