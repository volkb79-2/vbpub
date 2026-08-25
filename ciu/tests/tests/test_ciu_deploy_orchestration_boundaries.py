"""Public multi-action orchestration contracts for :mod:`ciu.deploy`.

These tests stop at the orchestrator's boundary: Docker, rendering, and graph
probing are substituted with deterministic action boundaries, while
``deploy.main`` still parses the user's real argv and owns ordering, preflight,
render reuse, continuation, and exit semantics.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _profile() -> Profile:
    return Profile(
        name="core",
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "ciu",
                "environment_tag": "test",
                "phases": {},
            }
        },
    )


def _patch_public_run_basics(monkeypatch, tmp_path, profile: Profile, selection: list[dict]) -> None:
    """Isolate ``main`` from environment/config I/O, not from orchestration."""
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _cfg, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)


def test_public_cli_reuses_one_render_for_ordered_check_and_graph(monkeypatch, tmp_path):
    """A user's ``--check --graph`` validates then displays one coherent graph.

    The negative is material: if the dispatch path re-rendered independently,
    stateful/template-backed configurations could validate one graph and print
    another.  The trace proves argv order and one shared rendered model.
    """
    profile = _profile()
    selection = [{"path": "applications/api", "service": {}}]
    _patch_public_run_basics(monkeypatch, tmp_path, profile, selection)
    rendered = {"applications/api": {"api": {"provides": ["pg:app"]}}}
    trace: list[tuple[str, object]] = []

    def fake_render(root, active_profile, active_selection, ciu_context=None):
        assert root == tmp_path
        assert active_profile is profile
        assert active_selection is selection
        trace.append(("render", None))
        return rendered

    # ciu-P18: `json_output` joined action_check's keyword-only surface when
    # `ciu check --json` landed (S13.4a). This double must track the real
    # signature or the orchestration assertion below fails on a TypeError
    # rather than on the ordering it exists to pin.
    def fake_check(root, active_profile, active_selection, received, *, live, json_output=False):
        trace.append(("check", received))
        assert live is False
        assert json_output is False
        return 0

    def fake_graph(root, active_profile, active_selection, received, *, fmt):
        trace.append(("graph", received))
        assert fmt == "json"
        return 0

    monkeypatch.setattr(deploy, "render_selected_stacks", fake_render)
    monkeypatch.setattr(deploy, "action_check", fake_check)
    monkeypatch.setattr(deploy, "action_graph", fake_graph)

    assert deploy.main(["--check", "--graph", "--format", "json"]) == 0
    assert trace == [("render", None), ("check", rendered), ("graph", rendered)]


def test_public_cli_stops_after_failed_action_without_ignore_errors(monkeypatch, tmp_path):
    """A failed validation action must not publish a subsequent graph (S7.3)."""
    profile = _profile()
    selection = [{"path": "applications/api", "service": {}}]
    _patch_public_run_basics(monkeypatch, tmp_path, profile, selection)
    trace: list[str] = []
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {"applications/api": {}})
    monkeypatch.setattr(deploy, "action_check", lambda *_args, **_kw: trace.append("check") or 2)
    monkeypatch.setattr(
        deploy,
        "action_graph",
        lambda *_args, **_kw: trace.append("graph") or 0,
    )

    assert deploy.main(["--check", "--graph"]) == 2
    assert trace == ["check"]


def test_public_cli_ignore_errors_continues_but_preserves_nonzero_verdict(monkeypatch, tmp_path):
    """Break-glass continuation still cannot launder a failed check (S7.3)."""
    profile = _profile()
    selection = [{"path": "applications/api", "service": {}}]
    _patch_public_run_basics(monkeypatch, tmp_path, profile, selection)
    trace: list[str] = []
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {"applications/api": {}})
    monkeypatch.setattr(deploy, "action_check", lambda *_args, **_kw: trace.append("check") or 1)
    monkeypatch.setattr(deploy, "action_graph", lambda *_args, **_kw: trace.append("graph") or 0)

    assert deploy.main(["--check", "--graph", "--ignore-errors"]) == 1
    assert trace == ["check", "graph"]


def test_public_cli_runs_every_deploy_preflight_before_starting_stack(monkeypatch, tmp_path):
    """A normal ``--deploy`` checks all host safety contracts before engine use.

    A regression that moves any preflight behind ``action_deploy`` would change
    this trace and could start a stack with bad provisioning, registry access,
    or an uninstalled governance slice.
    """
    profile = _profile()
    selection = [{"path": "applications/api", "service": {}}]
    _patch_public_run_basics(monkeypatch, tmp_path, profile, selection)
    rendered = {"applications/api": {"api": {}}}
    trace: list[str] = []

    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: trace.append("render") or rendered)
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: trace.append("vault"))
    monkeypatch.setattr(deploy, "provisioning_preflight", lambda *_args, **_kw: trace.append("provisioning"))
    monkeypatch.setattr(deploy, "registry_preflight", lambda *_args: trace.append("registry"))
    monkeypatch.setattr(deploy, "governance_slice_preflight", lambda *_args, **_kw: trace.append("governance"))
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda **_kw: trace.append("network"))

    def fake_deploy(root, active_profile, active_selection, **kwargs):
        assert kwargs["rendered"] is rendered
        assert kwargs["health_after_phase"] is False
        trace.append("deploy")
        return 0

    monkeypatch.setattr(deploy, "action_deploy", fake_deploy)

    assert deploy.main(["--deploy"]) == 0
    assert trace == ["render", "vault", "provisioning", "registry", "governance", "network", "deploy"]
