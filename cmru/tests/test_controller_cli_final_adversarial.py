"""Controller CLI contract tests for plan/error/status transitions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.controller import cli
from cmru import cli as cmru_cli


def _args(**kwargs):
    values = dict(plan="plan", landscape="land", consul_addr=None, token=None,
                  generation_base=3, dry_run=False, to_tag=None, generation=None)
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_publish_plan_load_landscape_and_engine_error_paths(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "plan.toml"; plan_path.write_text("plan")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: SimpleNamespace(landscape="from-plan"))
    seen = []
    class Engine:
        def publish(self, plan): seen.append(plan); raise RuntimeError("publish failed")
    monkeypatch.setattr(cli, "_build_engine", lambda args, landscape: (seen.append(landscape) or Engine()))
    assert cli.cmd_publish(_args(plan=str(plan_path), landscape=None)) == 1
    assert seen[0] == "from-plan" and "Publish failed" in capsys.readouterr().err
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: (_ for _ in ()).throw(ValueError("bad plan")))
    assert cli.cmd_publish(_args(plan=str(plan_path))) == 2


def test_publish_refuses_plan_without_landscape_before_engine(monkeypatch, tmp_path, capsys):
    plan = tmp_path / "plan.toml"; plan.write_text("plan")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: SimpleNamespace(landscape=""))
    called = []
    monkeypatch.setattr(cli, "_build_engine", lambda *args: called.append(True))
    assert cli.cmd_publish(_args(plan=str(plan), landscape=None)) == 2
    assert called == [] and "landscape required" in capsys.readouterr().err


def test_status_plan_success_and_engine_failure_are_observable(monkeypatch, tmp_path, capsys):
    plan = tmp_path / "plan.toml"; plan.write_text("plan")
    loaded = SimpleNamespace(landscape="land")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: loaded)
    class Engine:
        def status(self, value): return {"plan": value.landscape, "nodes": []}
    monkeypatch.setattr(cli, "_build_engine", lambda *args: Engine())
    assert cli.cmd_status(_args(plan=str(plan), landscape=None)) == 0
    assert json.loads(capsys.readouterr().out)["plan"] == "land"
    class Broken:
        def status(self, value): raise RuntimeError("status failed")
    monkeypatch.setattr(cli, "_build_engine", lambda *args: Broken())
    assert cli.cmd_status(_args(plan=str(plan))) == 1
    assert "Status failed" in capsys.readouterr().err


def test_status_catalog_success_and_backend_failure_without_plan(monkeypatch, capsys):
    backend = SimpleNamespace(_get=lambda path: (200, '[{"Node":"n1","ServiceTags":["blue"]}]', {}))
    monkeypatch.setattr(cli, "_build_backend", lambda args: backend)
    assert cli.cmd_status(_args(plan=None)) == 0
    output = capsys.readouterr().out
    assert "Registered cmru-agent nodes (1)" in output and "n1" in output
    monkeypatch.setattr(cli, "_build_backend", lambda args: SimpleNamespace(_get=lambda path: (_ for _ in ()).throw(OSError("down"))))
    assert cli.cmd_status(_args(plan=None)) == 1
    assert "Consul unavailable" in capsys.readouterr().err
    monkeypatch.setattr(cli, "_build_backend", lambda args: SimpleNamespace(_get=lambda path: (503, "", {})))
    assert cli.cmd_status(_args(plan=None)) == 0
    assert "HTTP 503" in capsys.readouterr().out


def test_rollback_forwards_tag_generation_and_reports_failure(monkeypatch, tmp_path, capsys):
    plan = tmp_path / "plan.toml"; plan.write_text("plan")
    loaded = SimpleNamespace(landscape="land")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda path: loaded)
    calls = []
    class Engine:
        def rollback(self, plan, **kwargs): calls.append((plan, kwargs))
    monkeypatch.setattr(cli, "_build_engine", lambda *args: Engine())
    assert cli.cmd_rollback(_args(plan=str(plan), to_tag="demo-v1", generation=9)) == 0
    assert calls[0][1] == {"to_tag": "demo-v1", "generation": 9}
    class Broken:
        def rollback(self, *args, **kwargs): raise RuntimeError("rollback failed")
    monkeypatch.setattr(cli, "_build_engine", lambda *args: Broken())
    assert cli.cmd_rollback(_args(plan=str(plan))) == 1
    assert "Rollback failed" in capsys.readouterr().err


def test_controller_main_dispatches_success_and_propagates_exit(monkeypatch):
    monkeypatch.setattr(cli, "cmd_hold", lambda args: 0)
    with pytest.raises(SystemExit) as result:
        cli.main(["hold", "--plan", "p"])
    assert result.value.code == 0


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
    with pytest.raises(SystemExit) as error:
        cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-unmanaged-release-tag", "other-v1", "--project", "demo", "--dry-run"])
    assert error.value.code == 2


def test_cleanup_dispatches_exact_local_build_deletion_and_requires_scope(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    calls = []
    monkeypatch.setattr(cmru_cli.transaction, "delete_retained_build_output", lambda *args, **kwargs: calls.append((args, kwargs)) or [tmp_path / "artifact"])
    cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-build-output", "20240101T000000Z_" + "a" * 40, "--project", "demo", "--dry-run"])
    assert calls and calls[0][1]["dry_run"] is True
    with pytest.raises(SystemExit) as error:
        cmru_cli.main(["cleanup", "--config", str(cfg), "--delete-build-output", "bad"])
    assert error.value.code == 2


def test_cleanup_dispatches_discard_worktree_and_rejects_project_mix(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    workspace = SimpleNamespace(path=tmp_path / "w", branch="cmru/build/x")
    calls = []
    monkeypatch.setattr(cmru_cli.transaction, "discard_build_workspace", lambda *args, **kwargs: calls.append(kwargs) or workspace)
    cmru_cli.main(["cleanup", "--config", str(cfg), "--discard-build-worktree", str(tmp_path / "w"), "--dry-run"])
    assert calls == [{"dry_run": True}]
    with pytest.raises(SystemExit) as error:
        cmru_cli.main(["cleanup", "--config", str(cfg), "--discard-build-worktree", str(tmp_path / "w"), "--project", "demo", "--dry-run"])
    assert error.value.code == 2


def test_cleanup_age_mode_forwards_cutoff_policy(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v", github_token="token")
    cfg, loaded = _cmru_config(tmp_path, project)
    monkeypatch.setattr(cmru_cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cmru_cli, "load_config", lambda path: loaded)
    calls = []
    monkeypatch.setattr(cmru_cli, "remove_assets", lambda *args: calls.append(args))
    cmru_cli.main(["cleanup", "--config", str(cfg), "--remove-assets", "2d", "--dry-run"])
    assert calls[0][0] == "2d" and calls[0][1] is True
