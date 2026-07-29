"""Behavioural contracts for deploy health summaries and TOML rendering."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def test_health_summary_pending_emits_operator_guidance(capsys):
    """Pending containers identify the first target and the recovery command."""

    deploy._print_health_summary({"pending": ["app-prod-api", "app-prod-worker"]})

    output = capsys.readouterr().out
    assert "pending: app-prod-api, app-prod-worker" in output
    assert "docker logs app-prod-api" in output
    assert "ciu health --preflight" in output


def test_health_summary_without_pending_omits_pending_guidance(capsys):
    """Healthy-only summaries do not suggest a pending-container recovery."""

    deploy._print_health_summary({"healthy": ["app-prod-api"]})

    output = capsys.readouterr().out
    assert "healthy: app-prod-api" in output
    assert "docker logs" not in output
    assert "ciu health --preflight" not in output


def test_render_toml_empty_selection_reports_no_work(monkeypatch, capsys, tmp_path: Path):
    """An empty rendering is a successful no-op, not a false success count."""

    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *args: {})

    assert deploy.action_render_toml(tmp_path, Profile(), []) == 0

    output = capsys.readouterr().out
    assert "No stacks selected to render" in output
    assert "Rendered 0 stack config(s)" not in output


def test_render_toml_reports_each_rendered_stack_deterministically(monkeypatch, capsys, tmp_path: Path):
    """Successful rendering names all selected stack outputs in sorted order."""

    monkeypatch.setattr(
        deploy,
        "render_selected_stacks",
        lambda *args: {"zeta/api": {"api": {}}, "alpha/db": {"db": {}}},
    )

    assert deploy.action_render_toml(tmp_path, Profile(), []) == 0

    output = capsys.readouterr().out
    assert output.index("rendered: alpha/db/") < output.index("rendered: zeta/api/")
    assert "Rendered 2 stack config(s)" in output
