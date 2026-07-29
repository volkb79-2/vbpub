"""Behavioural contracts for deploy preflight skip paths."""

from __future__ import annotations

from pathlib import Path

from ciu import config_model, deploy, provisioning
from ciu.deploy_pkg.profiles import Profile
from ciu.provisioning import ProbeResult


def test_vault_preflight_ignores_shipped_service(monkeypatch):
    """A shipped service must not enter the rendered-config vault checks."""

    def forbidden(*args, **kwargs):
        raise AssertionError("shipped service crossed a vault-preflight boundary")

    monkeypatch.setattr(config_model, "deep_merge", forbidden)
    monkeypatch.setattr(config_model, "validate_stack_shape", forbidden)
    monkeypatch.setattr(deploy.secret_directives, "find_misplaced", forbidden)
    monkeypatch.setattr(deploy.secret_directives, "discover", forbidden)
    monkeypatch.setattr(deploy, "resolve_vault_token", forbidden)
    monkeypatch.setattr(deploy, "vault_addr_from_config", forbidden)

    deploy.vault_preflight(
        Path("/tmp"),
        Profile(),
        [{"path": "vendor/shipped", "service": {"shipped": True}}],
        {},
    )


def test_provisioning_preflight_ignores_missing_rendered_entry(monkeypatch):
    """An unrendered selected entry returns before provisioning inspection."""

    def forbidden(*args, **kwargs):
        raise AssertionError("missing rendered entry crossed a provisioning boundary")

    monkeypatch.setattr(config_model, "validate_stack_shape", forbidden)
    monkeypatch.setattr(config_model, "validate_stack_provisioning", forbidden)
    monkeypatch.setattr(provisioning, "lint_graph", forbidden)
    monkeypatch.setattr(provisioning, "probe_ref", forbidden)

    deploy.provisioning_preflight(
        Path("/tmp"),
        Profile(),
        [{"path": "apps/missing", "service": {"enabled": True}}],
        {},
    )


def test_provisioning_preflight_probes_only_rendered_requires(monkeypatch):
    """Only a rendered stack's declared requires reach the live probe."""

    monkeypatch.setattr(provisioning, "lint_graph", lambda stacks: [])
    probed: list[str] = []

    def probe(ref, config, repo_root):
        probed.append(ref)
        return ProbeResult(ref=ref, satisfied=True, reason="ready")

    monkeypatch.setattr(provisioning, "probe_ref", probe)

    deploy.provisioning_preflight(
        Path("/tmp"),
        Profile(),
        [
            {"path": "apps/idle", "service": {"enabled": True}},
            {"path": "apps/needs-db", "service": {"enabled": True}},
        ],
        {
            "apps/idle": {"idle": {}},
            "apps/needs-db": {
                "needs-db": {"requires": ["pg:db/mydb"], "provides": []}
            },
        },
    )

    assert probed == ["pg:db/mydb"]
