"""KI-94: the mandatory ``[github] token_source`` key (``gh`` | ``cmru``).

``gh`` runs ``gh auth token --hostname github.com`` (subprocess mocked here, never the
real binary); ``cmru`` keeps the environment-then-secret-file resolution. The token must never
appear in a diagnostic, in the ``gh`` environment or in argv beyond the fixed command.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cmru import config
from cmru.config import load_forge_config

SECRET = "ghp_SUPERSECRETVALUE0123456789"

PROJECT = """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
{token_source}[targets]
host = "github"
registry = ["ghcr.io"]
[runtime]
kind = "none"
[project]
id = "alpha"
description = "alpha"
template_revision = 2
prefix = "alpha-v"
artifacts = ["wheel"]
scm_dist = "alpha"
[project.version]
strategy = "scm"
bump = "conventional"
paths = ["shared"]
[project.release]
git_tag = true
build_step = "build"
[steps.run-tests]
quiet = true
commands = [ { label = "test", argv = ["true"], cwd = "." } ]
[steps.build]
quiet = true
commands = [ { label = "build", argv = ["true"], cwd = "." } ]
[steps.push]
quiet = true
commands = [ { label = "push", argv = ["true"], cwd = "." } ]
"""


def _write(tmp_path: Path, token_source: str | None, *, secret: bool = False) -> Path:
    line = "" if token_source is None else f"token_source = {token_source}\n"
    path = tmp_path / "cmru.toml"
    path.write_text(PROJECT.replace("{token_source}", line), encoding="utf-8")
    if secret:
        (tmp_path / "cmru.secret.toml").write_text(
            f'schema_version = 1\n[github]\ntoken = "{SECRET}"\n', encoding="utf-8",
        )
    return path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    config.reset_gh_token_cache()  # the lookup is cached once per process (H2)
    for name in ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "GH_TOKEN", "CMRU_RELEASE_TRANSACTION_CHILD"):
        monkeypatch.delenv(name, raising=False)


def _fake_gh(monkeypatch, *, stdout=f"{SECRET}\n", returncode=0, raises=None):
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(config.subprocess, "run", fake_run)
    return calls


def test_gh_source_runs_the_fixed_command_with_a_minimal_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ambient-must-not-reach-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-env-token")
    monkeypatch.setenv("SOME_OTHER", "x")
    calls = _fake_gh(monkeypatch)
    forge = load_forge_config(_write(tmp_path, '"gh"'))
    assert calls == []  # H2: config load never runs gh
    assert forge.github.token == SECRET  # trailing newline stripped
    assert forge.github.token_source == "gh"
    assert set(forge.project_tokens) == {"alpha"}
    assert SECRET not in repr(forge.project_tokens) and SECRET not in repr(forge.github)
    assert forge.project_tokens["alpha"].resolve() == SECRET
    assert len(calls) == 1  # one cached lookup per process, however often it is read
    assert calls[0]["argv"] == ["gh", "auth", "token", "--hostname", "github.com"]
    env = calls[0]["env"]
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and "SOME_OTHER" not in env
    assert calls[0]["capture_output"] is True


def test_gh_source_needs_no_secret_file_and_ignores_one(tmp_path, monkeypatch):
    _fake_gh(monkeypatch, stdout="gh-token-value\n")
    path = _write(tmp_path, '"gh"', secret=True)
    forge = load_forge_config(path)
    assert forge.github.token == "gh-token-value"
    assert SECRET not in repr(forge.project_tokens)


def test_cmru_source_reads_the_secret_file_and_never_runs_gh(tmp_path, monkeypatch):
    calls = _fake_gh(monkeypatch)
    forge = load_forge_config(_write(tmp_path, '"cmru"', secret=True))
    assert forge.github.token == SECRET
    assert forge.github.token_source == "cmru"
    assert calls == []


def test_cmru_source_environment_token_still_wins(tmp_path, monkeypatch):
    calls = _fake_gh(monkeypatch)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "env-token")
    forge = load_forge_config(_write(tmp_path, '"cmru"', secret=True))
    assert forge.github.token == "env-token"
    assert calls == []


def test_cmru_source_without_any_token_still_loads(tmp_path, monkeypatch):
    forge = load_forge_config(_write(tmp_path, '"cmru"'))
    assert forge.github.token is None


def test_missing_token_source_is_a_config_error_naming_both_values(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        load_forge_config(_write(tmp_path, None))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "token_source" in err and "'gh'" in err and "'cmru'" in err
    assert "cmru.orchestration.toml" in err


@pytest.mark.parametrize("value", ['"GH"', '"pat"', '""', "1", "true"])
def test_invalid_token_source_is_a_config_error(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exc:
        load_forge_config(_write(tmp_path, value))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "token_source" in err and "'gh'" in err and "'cmru'" in err


def test_gh_binary_missing_is_a_config_error(tmp_path, monkeypatch, capsys):
    _fake_gh(monkeypatch, raises=FileNotFoundError("gh"))
    forge = load_forge_config(_write(tmp_path, '"gh"'))  # loads: validation only
    with pytest.raises(SystemExit) as exc:
        forge.github.token
    assert exc.value.code == 2
    assert "`gh` CLI is not installed" in capsys.readouterr().err


@pytest.mark.parametrize("stdout, returncode", [("", 1), ("", 0), ("\n", 0), (SECRET, 4)])
def test_gh_failure_or_empty_token_is_a_config_error_with_login_hint(
    tmp_path, monkeypatch, capsys, stdout, returncode,
):
    _fake_gh(monkeypatch, stdout=stdout, returncode=returncode)
    forge = load_forge_config(_write(tmp_path, '"gh"'))
    with pytest.raises(SystemExit) as exc:
        forge.github.token
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "gh auth login" in err
    assert "gh auth refresh -h github.com -s read:packages,write:packages,delete:packages" in err
    assert SECRET not in err  # even a failing gh that printed a token is never echoed


def test_gh_timeout_is_a_config_error(tmp_path, monkeypatch, capsys):
    _fake_gh(monkeypatch, raises=subprocess.TimeoutExpired(["gh"], 15))
    forge = load_forge_config(_write(tmp_path, '"gh"'))
    with pytest.raises(SystemExit) as exc:
        forge.github.token
    assert exc.value.code == 2
    assert "gh auth login" in capsys.readouterr().err


def test_token_source_in_the_secret_file_is_refused_with_a_pointer(tmp_path, capsys):
    path = _write(tmp_path, '"cmru"')
    (tmp_path / "cmru.secret.toml").write_text(
        f'[github]\ntoken = "{SECRET}"\ntoken_source = "gh"\n', encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        load_forge_config(path)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "does not belong in the secret file" in err and "cmru.orchestration.toml" in err
    assert SECRET not in err


def test_release_child_handoff_never_runs_gh(tmp_path, monkeypatch):
    """A transaction child gets the parent's resolved token over the pipe, never calls gh."""
    from cmru import credential_handoff

    calls = _fake_gh(monkeypatch)
    handoff = credential_handoff.CredentialHandoff(root="handed-token")
    monkeypatch.setattr(credential_handoff, "child_handoff", lambda: handoff)
    forge = load_forge_config(_write(tmp_path, '"gh"'))
    assert forge.github.token == "handed-token"
    assert calls == []


def test_gh_token_flows_into_the_reserved_credential_env_only(tmp_path, monkeypatch):
    from cmru import cli

    _fake_gh(monkeypatch, stdout="gh-flow-token\n")
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)
    config_path = _write(tmp_path, '"gh"')
    _root, configs, *_rest = cli.load_config(config_path, validate_dependencies=False)
    github_config = _rest[-2]
    assert github_config.token == "gh-flow-token"
    assert github_config.token_source == "gh"
    assert configs["alpha"].github_token == "gh-flow-token"
    assert "gh-flow-token" not in repr(configs["alpha"].env)


def test_doctor_reports_gh_source_without_the_value(tmp_path, monkeypatch):
    from cmru import doctor

    _fake_gh(monkeypatch, stdout="doctor-secret-token\n")
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, '"gh"')
    result = doctor.check_credentials()
    assert result.status == "ok"
    assert "token_source = gh" in result.summary
    assert "doctor-secret-token" not in repr(result)


def test_scaffold_emits_token_source_and_loads(tmp_path):
    from cmru import scaffold

    text = scaffold.render_project_toml(
        project_id="demo", description="d", owner="o", repo="r", owner_type="user",
        generated_by="test",
    )
    assert 'token_source = "gh"' in text
    orchestration = scaffold.render_orchestration_toml(
        [{"id": "demo", "config": "demo/cmru.toml"}], owner="o", repo="r", generated_by="test",
    )
    assert 'token_source = "gh"' in orchestration
