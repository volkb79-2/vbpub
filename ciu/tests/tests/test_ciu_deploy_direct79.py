"""`ciu deploy` dispatcher contracts without external bootstrap boundaries."""

from __future__ import annotations

import argparse
from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def _args(**overrides):
    values = {
        "define_root": None,
        "update_cert_permission": False,
        "profile": None,
        "phases": None,
        "dry_run": False,
        "no_preflight": False,
        "ignore_errors": False,
        "strict": False,
        "live": False,
        "graph_format": "mermaid",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _patch_setup(monkeypatch, profile: Profile, selection: list[dict], root: Path):
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _defined: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {"global": True})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)


def test_run_dispatches_non_deploy_actions_and_caches_render_for_check_then_graph(monkeypatch, tmp_path):
    """Explicit actions retain ordering and `--check`/`--graph` share one render."""

    profile = Profile(name="edge", config={"deploy": {}})
    selection = [{"path": "apps/api"}]
    _patch_setup(monkeypatch, profile, selection, tmp_path)
    actions = ["list_phases", "list_profiles", "stop", "clean", "healthcheck", "preflight", "check", "graph"]
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: actions)
    renders: list[tuple[Path, Profile, list[dict]]] = []
    calls: list[tuple[str, object]] = []

    def render(root, resolved_profile, resolved_selection, ciu_context=None):
        renders.append((root, resolved_profile, resolved_selection))
        return {"apps/api": {"api": {}}}

    monkeypatch.setattr(deploy, "render_selected_stacks", render)
    monkeypatch.setattr(deploy, "action_list_phases", lambda config: calls.append(("phases", config)) or 0)
    monkeypatch.setattr(deploy, "action_list_profiles", lambda config: calls.append(("profiles", config)) or 0)
    monkeypatch.setattr(deploy, "action_stop", lambda config: calls.append(("stop", config)) or 0)
    monkeypatch.setattr(deploy, "action_clean", lambda *args, **kwargs: calls.append(("clean", kwargs["ignore_errors"])) or 0)
    monkeypatch.setattr(deploy, "action_healthcheck", lambda *_args: calls.append(("health", None)) or 0)
    monkeypatch.setattr(deploy, "action_healthcheck_preflight", lambda *_args, **kwargs: calls.append(("preflight", kwargs["strict"])) or 0)
    monkeypatch.setattr(deploy, "action_check", lambda *args, **kwargs: calls.append(("check", kwargs["live"])) or 0)
    monkeypatch.setattr(deploy, "action_graph", lambda *args, **kwargs: calls.append(("graph", kwargs["fmt"])) or 0)

    assert deploy._run(_args(live=True), [f"--{action}" for action in actions]) == 0
    assert [name for name, _value in calls] == ["phases", "profiles", "stop", "clean", "health", "preflight", "check", "graph"]
    assert len(renders) == 1
    assert calls[-2:] == [("check", True), ("graph", "mermaid")]


def test_run_expands_comma_profiles_and_defaults_to_deploy_when_no_action(monkeypatch, tmp_path, capsys):
    """Repeated/comma profile input is normalized before the default deploy action."""

    profile = Profile(name="edge,db", config={"deploy": {}})
    _patch_setup(monkeypatch, profile, [], tmp_path)
    received_profiles: list[list[str] | None] = []
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, names: received_profiles.append(names) or profile)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: [])
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {})
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "registry_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "governance_slice_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "action_deploy", lambda *_args, **_kwargs: 0)

    assert deploy._run(_args(profile=["edge, db", "cache", "  "]), []) == 0
    assert received_profiles == [["edge", "db", "cache"]]
    assert "No action specified; defaulting to --deploy" in capsys.readouterr().out
