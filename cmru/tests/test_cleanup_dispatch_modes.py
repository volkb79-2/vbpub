"""Dispatch contracts for each ``cmru cleanup`` mode."""
from __future__ import annotations

from types import SimpleNamespace

from cmru import cli as cmru_cli


def _cmru_config(tmp_path, project):
    cfg = tmp_path / "cmru.toml"
    cfg.write_text("[project]\n")
    return cfg, (
        tmp_path, {project.name: project}, [project.name], [project.name], [],
        "project-first", {}, SimpleNamespace(),
        cmru_cli.GitHubConfig("owner", "repo", "token", "user"),
        cmru_cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_dispatch_validates_unmanaged_namespace_before_delete(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    assert cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-unmanaged-release-tag", "other-v1", "demo", "--dry-run"]) == 2


def test_cleanup_dispatches_exact_local_build_deletion_and_requires_scope(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    calls = []
    monkeypatch.setattr(cmru_cli.transaction, "retained_build_output_identity", lambda *_args: object())
    monkeypatch.setattr(cmru_cli.transaction, "delete_retained_build_output", lambda *args, **kwargs: calls.append((args, kwargs)) or [tmp_path / "artifact"])
    cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-build-output", "20240101T000000Z_" + "a" * 40, "demo", "--dry-run"])
    assert calls and calls[0][1]["dry_run"] is True
    assert cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-build-output", "bad"]) == 2


def test_cleanup_age_mode_forwards_cutoff_policy(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    calls = []
    applied = []
    applied_plans = []
    original_apply = cmru_cli.CleanupPlan.apply

    def record_apply(plan):
        applied_plans.append(plan)
        original_apply(plan)

    monkeypatch.setattr(cmru_cli.CleanupPlan, "apply", record_apply)

    def capture_remove_assets(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["plan"].add("captured asset", lambda: applied.append("applied"))

    monkeypatch.setattr(cmru_cli, "remove_assets", capture_remove_assets)
    assert cmru_cli.main(["cleanup", "--config", str(cfg), "--remove-assets", "2d", "--yes"]) == 0
    assert len(calls) == 1
    assert calls[0][0][0] == "2d" and calls[0][0][1] is True
    assert isinstance(calls[0][1]["plan"], cmru_cli.CleanupPlan)
    assert len(applied_plans) == 1 and applied_plans[0] is calls[0][1]["plan"]
    assert applied == ["applied"]
