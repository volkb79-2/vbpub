"""Final paired behavioural coverage for CMRU CLI/config contracts."""
from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from cmru import cli, config


def test_output_options_export_only_explicit_flags(monkeypatch):
    monkeypatch.delenv("CMRU_SHOW_RUN_DETAILS", raising=False)
    monkeypatch.delenv("CMRU_LOG_APPEND", raising=False)
    cli._apply_output_options(SimpleNamespace(show_run_details=False, log_append=False))
    assert "CMRU_SHOW_RUN_DETAILS" not in os.environ
    cli._apply_output_options(SimpleNamespace(show_run_details=True, log_append=True))
    assert os.environ["CMRU_SHOW_RUN_DETAILS"] == "1"
    assert os.environ["CMRU_LOG_APPEND"] == "1"


class _Response:
    status = 200
    headers = {"X-Test": "yes"}
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return b"body"


def test_http_request_builds_auth_headers_and_preserves_success_response(monkeypatch):
    seen = {}
    def fake_open(request):
        seen["method"] = request.method
        seen["auth"] = request.headers["Authorization"]
        return _Response()
    monkeypatch.setattr(cli, "urlopen", fake_open)
    assert cli.http_request("POST", "https://api.invalid", "secret") == (200, "body", {"X-Test": "yes"})
    assert seen == {"method": "POST", "auth": "Bearer secret"}


def test_http_request_http_error_returns_body_and_headers(monkeypatch):
    error = HTTPError("https://api.invalid", 404, "gone", {"X": "y"}, io.BytesIO(b"missing"))
    monkeypatch.setattr(cli, "urlopen", lambda _request: (_ for _ in ()).throw(error))
    assert cli.http_request("GET", "https://api.invalid", "") == (404, "missing", {"X": "y"})


def test_project_step_refuses_missing_derived_root_or_declared_step(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", cwd=None, runner_steps={})
    with pytest.raises(RuntimeError, match="working directory"):
        cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    project.cwd = "demo"
    with pytest.raises(RuntimeError, match="declared step"):
        cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")


def test_project_step_constructs_stable_root_and_passes_project_environment(monkeypatch, tmp_path):
    step = object()
    project = SimpleNamespace(name="demo", cwd="demo", runner_steps={"build": step}, env={"X": "1"}, build_metadata={})
    calls = []
    monkeypatch.setattr(cli, "execute_step", lambda *args, **kwargs: calls.append((args, kwargs)))
    cli.run_project_step(project, "build", tmp_path, tmp_path / "logs")
    assert calls[0][0][0] is step
    assert calls[0][0][2] == tmp_path / "demo" / "logs" / "cmru"
    assert calls[0][1]["extra_env"] == {"X": "1"}


@pytest.mark.parametrize("raw, expected", [(None, None), ("bad", "table"), ({"bump": "bad"}, "bump")])
def test_version_spec_refuses_non_table_and_invalid_bump(raw, expected):
    if expected is None:
        assert cli._parse_version_spec(raw, "demo") is None
    else:
        with pytest.raises(ValueError, match=expected):
            cli._parse_version_spec(raw, "demo")


def test_version_spec_defaults_and_preserves_explicit_resolution_inputs():
    spec = cli._parse_version_spec({"strategy": "file:VERSION", "bump": "patch", "paths": ["shared"], "base_version": "2.0.0", "file": "BUILD"}, "demo")
    assert spec == cli.VersionSpec("file:VERSION", "patch", ("shared",), "2.0.0", "BUILD")
    assert cli._bare_prefix("demo-v") == "demo"
    assert cli._bare_prefix("demo") == "demo"


def test_release_environment_clears_stale_credentials_and_merges_explicit_empty(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "older")
    cli.apply_release_env(
        cli.GitHubConfig("owner", "repo", "new", "org"),
        cli.ReleaseEnvConfig({"CMRU_TEST": "", "OTHER": 3}, "ghcr.io"),
    )
    assert os.environ["GITHUB_PUSH_PAT"] == "new"
    assert "GITHUB_TOKEN" not in os.environ
    assert os.environ["CMRU_TEST"] == ""
    assert os.environ["REGISTRY"] == "ghcr.io"


def test_project_environment_override_changes_token_and_process_values(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "apply_release_env", lambda github, env: seen.append((github, dict(env.env))))
    project = SimpleNamespace(github_token="project-token", env={"X": "project"})
    cli.apply_project_release_env(cli.GitHubConfig("o", "r", "root", "user"), cli.ReleaseEnvConfig({"X": "root", "Y": "y"}, None), project)
    assert seen[0][0].token == "project-token"
    assert seen[0][1] == {"X": "project", "Y": "y"}


def test_publish_credential_check_distinguishes_complete_and_missing_selection():
    good = SimpleNamespace(github_token="tok")
    bad = SimpleNamespace(github_token="")
    cli.require_project_publish_credentials({"a": good}, ["a"])
    with pytest.raises(RuntimeError, match=r"project\(s\): b"):
        cli.require_project_publish_credentials({"a": good, "b": bad}, ["a", "b"])


def test_git_wrapper_reports_success_failure_and_missing_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=" result \n", stderr=""))
    assert cli._git(tmp_path, "status") == "result"
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="bad"))
    with pytest.raises(RuntimeError, match="status failed"):
        cli._git(tmp_path, "status")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError))
    with pytest.raises(RuntimeError, match="unavailable"):
        cli._git(tmp_path, "status")


def test_resolve_versions_from_git_sets_source_metadata_and_exact_tag_override(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli, "_git", lambda _root, *args: "1700000000" if any("format=%ct" in arg for arg in args) else "a" * 40)
    project = SimpleNamespace(prefix="demo-v", scm_dist="demo-pkg")
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="demo-v1.2.3\n")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.delenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_PKG", raising=False)
    cli.resolve_versions_from_git(tmp_path, {"demo": project})
    assert os.environ["SOURCE_DATE_EPOCH"] == "1700000000"
    assert os.environ["OCI_REVISION"] == "a" * 40
    assert os.environ["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_PKG"] == "1.2.3"
    assert "HEAD on demo-v1.2.3" in capsys.readouterr().out


def test_resolve_versions_from_git_skips_projects_without_exact_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_git", lambda _root, *args: "1" if any("format=%ct" in arg for arg in args) else "a" * 40)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    project = SimpleNamespace(prefix="demo-v", scm_dist="demo")
    monkeypatch.delenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO", raising=False)
    cli.resolve_versions_from_git(tmp_path, {"demo": project})
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO" not in os.environ


def test_source_version_and_installed_version_fallbacks_are_distinct(monkeypatch):
    monkeypatch.setattr(cli, "_source_tree_version", lambda: None)
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "9.9.9")
    assert cli._cmru_version() == "9.9.9"
    monkeypatch.setattr("importlib.metadata.version", lambda _name: (_ for _ in ()).throw(RuntimeError("none")))
    assert cli._cmru_version() == "dev"


def test_ordered_configs_omits_unorchestrated_projects_and_tag_selection_filters_latest(monkeypatch, tmp_path):
    a = SimpleNamespace()
    assert list(cli._ordered_configs({"a": a, "b": SimpleNamespace()}, ["b", "missing", "a"])) == ["b", "a"]
    monkeypatch.setattr(cli, "_git", lambda *_: "demo-v1.0.0\ndemo-latest\ndemo-v2.0.0\n")
    assert cli._tag_on_head(tmp_path, "demo-") == "demo-v2.0.0"


def test_push_tags_has_no_side_effect_for_empty_and_warns_on_failure(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=1))
    cli._push_tags(tmp_path, [])
    assert calls == []
    cli._push_tags(tmp_path, ["demo-v1"])
    assert calls and "demo-v1" in calls[0]
    assert "continuing" in capsys.readouterr().out


def test_config_cleanup_and_project_document_reject_invalid_shapes(capsys):
    with pytest.raises(SystemExit):
        config._parse_cleanup({"max_age_days": 0, "release_tag_prefixes": [], "keep_release_tags": [], "ghcr_packages": [], "ghcr_delete_packages": []})
    assert "positive" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        config._parse_cleanup({"release_tag_prefixes": [], "keep_release_tags": [], "ghcr_packages": []})
    assert "ghcr_delete_packages" in capsys.readouterr().out
