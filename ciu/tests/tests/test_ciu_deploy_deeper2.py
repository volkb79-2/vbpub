"""Public provisioning-inspection contracts for ``ciu deploy``.

These tests use rendered stack models as the public action input.  They keep
live probes deterministic, but exercise the real graph/check behavior rather
than asserting internal call counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402
from ciu.provisioning import ProbeResult  # noqa: E402


def _profile() -> Profile:
    return Profile(name="core", phase_keys=None, config={"deploy": {"phases": {}}})


def test_graph_rejects_malformed_reference_instead_of_publishing_it(monkeypatch, tmp_path, capsys):
    """An invalid ref is a config error, never a plausible topology diagram."""
    rendered = {"applications/api": {"api": {"requires": ["pg:role/bad!"], "provides": []}}}
    monkeypatch.setattr(
        "ciu.provisioning.render_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("invalid graph must not render")),
    )

    assert deploy.action_graph(
        tmp_path, _profile(), [{"path": "applications/api"}], rendered, fmt="json"
    ) == 2
    assert "applications/api" in capsys.readouterr().out


def test_graph_emits_machine_readable_edges_for_valid_rendered_topology(tmp_path, capsys):
    """A valid graph exposes the actual consumer/provider relationship to tools."""
    rendered = {
        "infra/db": {"db": {"provides": ["pg:db/app"]}},
        "applications/api": {"api": {"requires": ["pg:db/app"]}},
    }
    selection = [{"path": "infra/db"}, {"path": "applications/api"}]

    assert deploy.action_graph(tmp_path, _profile(), selection, rendered, fmt="json") == 0
    graph = json.loads(capsys.readouterr().out)
    assert graph["stacks"] == {
        "applications/api": {"requires": ["pg:db/app"], "provides": []},
        "infra/db": {"requires": [], "provides": ["pg:db/app"]},
    }
    assert graph["edges"] == [{
        "from": "applications/api", "to": "infra/db", "ref": "pg:db/app", "provided": True,
    }]


def test_check_live_reports_every_unsatisfied_requirement_and_stays_red(monkeypatch, tmp_path, capsys):
    """``--check --live`` cannot launder a partial dependency outage into green."""
    rendered = {
        "infra/db": {"db": {"provides": ["pg:db/app", "pg:role/api"]}},
        "applications/api": {"api": {"requires": ["pg:db/app", "pg:role/api"]}},
    }
    selection = [{"path": "infra/db"}, {"path": "applications/api"}]

    def fake_probe(ref, _config, _root, **_kw):
        return ProbeResult(ref, ref == "pg:db/app", "database reachable" if ref == "pg:db/app" else "role absent")

    monkeypatch.setattr("ciu.provisioning.probe_ref", fake_probe)
    assert deploy.action_check(tmp_path, _profile(), selection, rendered, live=True) == 1
    output = capsys.readouterr().out
    assert "OK  pg:db/app" in output
    assert "FAIL pg:role/api: role absent" in output
    assert "1 unsatisfied requirement" in output
