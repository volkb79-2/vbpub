"""Focused contracts for provisioning preflight break-glass and probes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, provisioning  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402
from ciu.provisioning import ProbeResult  # noqa: E402


def _profile() -> Profile:
    return Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
    )


def test_no_preflight_announces_and_skips_graph_and_probe(monkeypatch, capsys):
    """Break-glass mode must not touch either provisioning boundary."""

    def boundary_must_not_run(*args, **kwargs):
        raise AssertionError("provisioning graph/probe activity ran")

    monkeypatch.setattr(provisioning, "lint_graph", boundary_must_not_run)
    monkeypatch.setattr(provisioning, "probe_ref", boundary_must_not_run)

    deploy.provisioning_preflight(
        Path("/tmp"),
        _profile(),
        [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}],
        {
            "apps/backend": {
                "backend": {
                    "requires": ["pg:db/mydb"],
                    "provides": [],
                }
            }
        },
        no_preflight=True,
    )

    assert "--no-preflight: skipping provisioning preflight" in capsys.readouterr().out


def test_preflight_reports_unsatisfied_typed_requirement(monkeypatch):
    """A failed live probe identifies the selected stack, ref, and reason."""

    monkeypatch.setattr(provisioning, "lint_graph", lambda stacks: [])
    monkeypatch.setattr(
        provisioning,
        "probe_ref",
        lambda ref, config, repo_root, **_kw: ProbeResult(
            ref=ref, satisfied=False, reason="database is not ready"
        ),
    )

    selection = [
        {"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}
    ]
    rendered = {
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],
                "provides": [],
            }
        }
    }

    with pytest.raises(ValueError) as exc_info:
        deploy.provisioning_preflight(Path("/tmp"), _profile(), selection, rendered)

    message = str(exc_info.value)
    assert "apps/backend" in message
    assert "pg:db/mydb" in message
    assert "database is not ready" in message
