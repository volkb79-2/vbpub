"""Dry-run must expose versions emitted by an external prepare step."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cmru import cli, version


def test_dry_run_prepares_external_version_before_plan(monkeypatch, tmp_path, capsys):
    project = SimpleNamespace(
        name="pwmcp",
        cwd="pwmcp",
        paths=["pwmcp"],
        prefix="pwmcp-v",
        version=SimpleNamespace(strategy="external:PWMCP_VERSION"),
        steps={"prepare": object()},
        runner_steps={"prepare": object()},
        env={},
        project_root=tmp_path / "pwmcp",
        github_token="token",
    )
    loaded = (
        tmp_path,
        {"pwmcp": project},
        ["pwmcp"],
        ["pwmcp"],
        [],
        "project-first",
        {},
        cli.CleanupConfig([], [], [], []),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    config_path = tmp_path / "cmru.orchestration.toml"
    events: list[str] = []
    monkeypatch.setattr(cli, "_resolve_config", lambda _: config_path)
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "run_project_step", lambda *_: events.append("prepare"))
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *_: events.append("commit"))
    monkeypatch.setattr(
        version,
        "detect_changed_projects",
        lambda *_args, **_kwargs: (events.append("plan") or [("pwmcp", project, None, "patch")]),
    )
    monkeypatch.setattr(version, "release_cmd", lambda *_args, **_kwargs: events.append("preview"))

    cli.main([
        "release", "--_transaction-child", "--dry-run", "--config", str(config_path)
    ])

    assert events == ["prepare", "commit", "plan", "preview"]
    assert "preparing external version inputs for dry-run" in capsys.readouterr().out
