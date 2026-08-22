"""Remaining deploy dispatcher/helper contracts."""

from __future__ import annotations

import argparse
from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def test_run_graph_lazily_renders_then_forwards_requested_format(monkeypatch, tmp_path: Path):
    """A graph-only invocation renders once and preserves the selected output format."""

    args = argparse.Namespace(
        define_root=None, update_cert_permission=False, profile=None, phases=None,
        dry_run=False, no_preflight=False, ignore_errors=False, strict=False,
        live=False, graph_format="json",
    )
    profile = Profile(config={"deploy": {}})
    selection = [{"path": "apps/api"}]
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *_args: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda *_args: selection)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: ["graph"])
    rendered = {"apps/api": {"api": {}}}
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: rendered)
    received: list[tuple[object, str]] = []
    monkeypatch.setattr(
        deploy,
        "action_graph",
        lambda *_args, **kwargs: received.append((_args[3], kwargs["fmt"])) or 0,
    )

    assert deploy._run(args, ["--graph", "--format", "json"]) == 0
    assert received == [(rendered, "json")]


def test_other_actions_requested_detects_any_explicit_action_and_not_none():
    """Defaulting logic distinguishes no action from every declared action flag."""

    fields = {
        name: False
        for name in ("deploy", "stop", "clean", "healthcheck", "preflight", "check", "graph", "render_toml", "list_phases", "list_profiles")
    }
    assert deploy._other_actions_requested(argparse.Namespace(**fields)) is False
    fields["graph"] = True
    assert deploy._other_actions_requested(argparse.Namespace(**fields)) is True


def test_collect_bake_targets_from_legacy_phases_filters_disabled_invalid_and_nonbuild_paths():
    """Legacy phase conversion preserves only enabled application/tool targets, deduped."""

    phases = [
        {"services": [
            {"path": "applications/api", "enabled": True},
            {"path": "tools/admin"},
            {"path": "applications/api"},
            {"path": "applications/disabled", "enabled": False},
            {"path": "infrastructure/db"},
            {"enabled": True},
        ]},
        {},
    ]

    assert deploy.collect_bake_targets_from_phases(phases) == ["admin", "api"]
