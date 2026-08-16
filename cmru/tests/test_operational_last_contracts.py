"""Exact tests for remaining small operational branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import dependencies, resolve, runner
from cmru.agent import cli as agent_cli


def test_dependency_wheel_parser_strips_comments_extras_and_blank_lines(tmp_path):
    path = tmp_path / "wheels.list"
    path.write_text("\n# comment\nDemo_pkg[extra] # note\nother==1\n", encoding="utf-8")
    assert dependencies._wheel_inputs(path) == ["Demo_pkg", "other==1"]


def test_dependency_report_records_relative_artifact_source_and_order_error(tmp_path):
    provider = SimpleNamespace(scm_dist="demo-pkg", project_root=None)
    consumer_root = tmp_path / "consumer"; (consumer_root / "pip").mkdir(parents=True)
    (consumer_root / "pip" / "wheels.list").write_text("demo_pkg\n", encoding="utf-8")
    consumer = SimpleNamespace(scm_dist="consumer", project_root=consumer_root)
    report = dependencies.build_report(
        repo_root=tmp_path, project_order=["consumer", "provider"],
        declared={"consumer": ("provider",)}, projects={"provider": provider, "consumer": consumer},
    )
    assert report.edges[-1].source == "consumer/pip/wheels.list"
    assert any("does not place" in error for error in report.errors)


def test_resolve_prefers_valid_latest_json_before_host_scan(monkeypatch):
    latest = {"version": "2", "tag": "demo-v2", "url": "u"}
    monkeypatch.setattr(resolve, "resolve_via_latest_json", lambda *args: latest)
    host = SimpleNamespace(resolve_latest=lambda prefix: {"version": "1"})
    assert resolve.resolve(host, "demo-v", gh_releases_url="https://github") == latest


def test_agent_run_and_once_refuse_identity_without_landscape(monkeypatch, capsys):
    monkeypatch.setattr(agent_cli, "_load_identity", lambda scope: ("node", {"public_key": "pub"}))
    args = SimpleNamespace(scope="user", release_root=None)
    assert agent_cli.cmd_run(args) == 2
    assert "landscape not found" in capsys.readouterr().err
    assert agent_cli.cmd_once(args) == 2
    assert "landscape not found" in capsys.readouterr().err


def test_runner_login_required_token_refuses_before_docker(monkeypatch):
    monkeypatch.setenv("REGISTRY", "ghcr.io")
    monkeypatch.setenv("USER", "owner")
    monkeypatch.delenv("TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TOKEN is required"):
        runner.maybe_login({"registry": "ghcr.io", "username_env": "USER", "token_env": "TOKEN", "required": True})


def test_runner_apply_env_command_rejects_empty_key(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="=value\n"))
    with pytest.raises(ValueError, match="empty key"):
        runner.apply_env_command(["derive"], tmp_path)
