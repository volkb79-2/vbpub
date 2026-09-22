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
        SimpleNamespace(owner="owner", repo="repo"),
        SimpleNamespace(),
    )
    config_path = tmp_path / "cmru.orchestration.toml"
    events: list[str] = []
    monkeypatch.setattr(cli, "_resolve_config", lambda _: config_path)
    # This test is about preparation ordering, not invocation-context discovery.
    # The mocked config loader supplies the project set, so keep scope selection
    # within that same test boundary rather than asking the filesystem to load the
    # deliberately nonexistent temporary config.
    monkeypatch.setattr(cli, "_select_projects", lambda *_args: ["pwmcp"])
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    def prepare(*_args):
        project.project_root.mkdir(parents=True, exist_ok=True)
        (project.project_root / "cmru.vars").write_text(
            "PWMCP_VERSION=1.61.2-r3\n", encoding="utf-8"
        )
        events.append("prepare")

    monkeypatch.setattr(cli, "run_project_step", prepare)
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *_: events.append("commit"))
    monkeypatch.setattr(
        version,
        "detect_changed_projects",
        lambda *_args, **_kwargs: (
            events.append(
                "plan:" + (project.project_root / "cmru.vars").read_text(encoding="utf-8").strip()
            )
            or [("pwmcp", project, None, "patch")]
        ),
    )
    monkeypatch.setattr(version, "release_cmd", lambda *_args, **_kwargs: events.append("preview"))

    cli.main([
        "release", "--_transaction-child", "--dry-run", "--config", str(config_path)
    ])

    assert events == ["prepare", "commit", "plan:PWMCP_VERSION=1.61.2-r3", "preview"]
    assert "preparing external version inputs for dry-run" in capsys.readouterr().out


def test_external_version_without_prepare_is_skipped(monkeypatch, tmp_path):
    project = SimpleNamespace(
        version=SimpleNamespace(strategy="external:VERSION"),
        steps={},
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("a project without prepare must not be prepared")

    monkeypatch.setattr(cli, "apply_project_release_env", unexpected)
    monkeypatch.setattr(cli, "run_project_step", unexpected)
    monkeypatch.setattr(cli, "_commit_prepared_generated", unexpected)

    cli._prepare_dry_run_external_versions(
        tmp_path,
        {"external": project},
        ["external"],
        github_config=SimpleNamespace(),
        env_config=SimpleNamespace(),
    )
