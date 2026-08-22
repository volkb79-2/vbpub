"""Dry-run deployment orchestration contracts.

These tests retain the public ``ciu-deploy`` parser and action dispatcher while
replacing the host/I/O seams.  They prove that dry-run is not a safety bypass:
static config and vault ordering validation remain before stack execution, but
live registry/network effects are not attempted when nothing will start.
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


def _patch_run_basics(monkeypatch, tmp_path, profile: Profile, selection: list[dict]) -> None:
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _cfg, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)


def test_public_dry_run_keeps_static_preflights_but_skips_live_host_effects(monkeypatch, tmp_path):
    """``--deploy --dry-run`` validates config before engine dispatch, without I/O.

    A regression that either skips vault/provisioning/governance validation or
    invokes registry/network setup would respectively make dry-run a false-green
    validation route or perform live host changes despite its promise.
    """
    profile = _profile()
    selection = [{"path": "applications/api", "service": {}}]
    _patch_run_basics(monkeypatch, tmp_path, profile, selection)
    rendered = {"applications/api": {"api": {}}}
    trace: list[tuple[str, object]] = []

    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: trace.append(("render", None)) or rendered)
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: trace.append(("vault", None)))

    def provisioning(*_args, **kwargs):
        trace.append(("provisioning", kwargs))

    def governance(*_args, **kwargs):
        trace.append(("governance", kwargs))

    monkeypatch.setattr(deploy, "provisioning_preflight", provisioning)
    monkeypatch.setattr(deploy, "governance_slice_preflight", governance)
    monkeypatch.setattr(
        deploy,
        "registry_preflight",
        lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run must not authenticate registry")),
    )
    monkeypatch.setattr(
        deploy,
        "ensure_workspace_network",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not create/connect network")),
    )

    def action(root, active_profile, active_selection, **kwargs):
        assert root == tmp_path
        assert active_profile is profile
        assert active_selection is selection
        assert kwargs["rendered"] is rendered
        assert kwargs["dry_run"] is True
        trace.append(("deploy", None))
        return 0

    monkeypatch.setattr(deploy, "action_deploy", action)

    assert deploy.main(["--deploy", "--dry-run", "--no-preflight"]) == 0
    assert trace == [
        ("render", None),
        ("vault", None),
        ("provisioning", {"no_preflight": True, "probe": False}),
        ("governance", {"no_preflight": True}),
        ("deploy", None),
    ]
