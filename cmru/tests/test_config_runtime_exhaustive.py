"""Exhaustive runtime boundary witnesses for CMRU's build/release support."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import bundle, changelog, cli, config, handlers, runner


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x=1\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def test_config_scalar_env_and_secret_documents_refuse_shape_errors(tmp_path):
    with pytest.raises(SystemExit):
        config._scalar_env(["bad"], "env")
    with pytest.raises(SystemExit):
        config._require_table({"x": []}, "x", "root")
    secret = tmp_path / "cmru.secret.toml"
    secret.write_text("token = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(secret)


def test_config_version_artifacts_installer_and_variants_reject_invalid_values():
    with pytest.raises(SystemExit):
        config._parse_version({"strategy": "unknown"}, "demo")
    with pytest.raises(SystemExit):
        config._parse_artifacts("demo", {"dirs": "dist"})
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {"wheels": "bad"})
    with pytest.raises(SystemExit):
        config._parse_variants("demo", {"variants": [{"name": "a"}, {"name": "a"}]})


def test_config_project_and_orchestration_missing_source_facts_fail_loudly(tmp_path):
    path = tmp_path / "cmru.toml"
    path.write_text("schema_version=1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    with pytest.raises(SystemExit):
        config.load_forge_config(path, require_orchestration=True)


def test_runner_git_metadata_and_build_date_are_source_derived_or_refused(tmp_path, monkeypatch):
    root = repo(tmp_path)
    runner.apply_reproducible_env(root)
    assert os.environ["OCI_REVISION"] == git(root, "rev-parse", "HEAD")
    runner.compute_build_date({"build_metadata": {"date_env": "CREATED", "date_format": "%Y-%m-%dT%H:%M:%SZ"}}, root)
    assert os.environ["CREATED"].endswith("Z")
    monkeypatch.delenv("CREATED", raising=False)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    with patch.object(runner, "_git_out", return_value=None):
        with pytest.raises(RuntimeError, match="commit"):
            runner.compute_build_date({"build_metadata": {"created_env": "CREATED"}}, root)


def test_runner_quiet_aggregate_log_is_distinct_and_append_is_observable(tmp_path, monkeypatch):
    aggregate = tmp_path / "aggregate.log"
    monkeypatch.setenv("CMRU_RUN_LOG", str(aggregate))
    monkeypatch.delenv("CMRU_SHOW_RUN_DETAILS", raising=False)
    monkeypatch.delenv("CMRU_LOG_APPEND", raising=False)
    step = runner.StepConfig("s", [{"label": "ok", "argv": [sys.executable, "-c", "print('OK')"], "cwd": "."}],
                             None, [], None, [], [], None, {}, None, [], True)
    runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert "label='ok'" in aggregate.read_text()
    monkeypatch.setenv("CMRU_LOG_APPEND", "1")
    runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert "\n---\n" in (tmp_path / "logs" / "s.log").read_text()


def test_runner_invalid_command_shape_and_clean_dir_are_behavioral(tmp_path):
    clean = tmp_path / "cache"
    clean.mkdir()
    step = runner.StepConfig("s", [{"label": "bad", "argv": ["true"]}], None, [], None, ["cache"], [], None, {}, None, False)
    with pytest.raises(ValueError, match="cwd"):
        runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert not clean.exists()


def test_handler_tarball_version_sources_and_validate_options(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    artifact = tmp_path / "dist" / "demo-v1.tar.xz"
    artifact.write_bytes(b"artifact")
    (tmp_path / "VERSION").write_text("1.0.0\n")
    args = SimpleNamespace(cwd=str(tmp_path), prefix="demo", version_file="VERSION", version_env=None,
                           glob="*.tar.xz", notes_env=None)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "t")
    monkeypatch.setenv("GITHUB_USERNAME", "o")
    monkeypatch.setenv("GITHUB_REPO", "r")
    with patch.object(handlers, "GitHubReleases") as gh:
        gh.return_value.publish.return_value = {}
        gh.return_value.asset_download_url.return_value = "https://example.invalid/artifact"
        handlers.cmd_tarball_publish(args)
        assert gh.return_value.publish.called
    with patch.object(handlers, "validate_latest_release", return_value={"version": "1", "asset": "x", "url": "u", "sha256_url": None}):
        handlers.cmd_tarball_validate(SimpleNamespace(prefix="demo", artifact_suffix=".tar.xz"))


def test_handler_docker_login_receives_token_only_on_stdin(monkeypatch):
    monkeypatch.setenv("REGISTRY", "registry.example")
    monkeypatch.setenv("GITHUB_USERNAME", "user")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
    with patch.object(handlers.subprocess, "run") as run:
        handlers._docker_login()
    assert run.call_args.kwargs["input"] == "secret\n"
    assert "secret" not in run.call_args.args[0]


def test_bundle_run_bundle_cleans_stale_dist_and_requires_copy_sources(tmp_path, monkeypatch):
    root = tmp_path / "p"
    root.mkdir()
    (root / "dist").mkdir()
    (root / "dist" / "old.whl").write_bytes(b"old")
    config_path = root / "bundle.toml"
    config_path.write_text("project_root='.'\n[archive]\nname_template='x-{version}.tar.xz'\nversion_env='V'\nformat='xztar'\n[copy]\nfiles=['missing']\ndirs=[]\n", encoding="utf-8")
    monkeypatch.setenv("V", "1")
    with pytest.raises(FileNotFoundError):
        bundle.run_bundle(config_path)
    assert not (root / "dist" / "old.whl").exists()


def test_changelog_generated_outputs_status_errors_and_render_dates(tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", commit_generated=("generated",))
    with patch.object(changelog.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="bad", stdout="")):
        with pytest.raises(RuntimeError, match="git status"):
            changelog._generated_outputs_changed(tmp_path, project)
    section = changelog._render_section("source-a", {"Changed": ["entry"]}, source_end="a" * 40, backfilled_tag="demo-v1")
    assert "backfilled-after-release" in section and "entry" in section


def test_cli_cleanup_helpers_refuse_unmanaged_and_wrong_age(monkeypatch):
    with pytest.raises(ValueError):
        cli.parse_duration("0x")
    with pytest.raises(ValueError):
        cli.parse_duration("-1h")
    assert cli.parse_duration("1h 30m").total_seconds() == 5400


def test_cli_http_and_json_boundaries_preserve_error_and_empty_body(monkeypatch):
    class Response:
        status = 200
        headers = {}
        def read(self): return b""
        def __enter__(self): return self
        def __exit__(self, *_): return False
    monkeypatch.setattr(cli, "urlopen", lambda *_args, **_kwargs: Response())
    assert cli.load_json("https://example.invalid", "") == ([], {})
