"""CIU graph rendering tests — `ciu graph` / provisioning.render_graph (4.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.provisioning import render_graph  # noqa: E402

STACKS = {
    "infra/db-core": {
        "provides": ["pg:db/dstdns", "pg:role/controller"],
        "requires": [],
    },
    "applications/controller": {
        "provides": [],
        "requires": ["pg:db/dstdns", "pg:role/controller", "vault:secret/redis/password"],
    },
}


def test_render_graph_mermaid_edges_and_unprovided():
    out = render_graph(STACKS, "mermaid")
    assert out.startswith("flowchart LR")
    assert "infra/db-core" in out and "applications/controller" in out
    assert "pg:db/dstdns" in out                 # provided edge label
    assert "UNPROVIDED" in out                   # unmatched require → sentinel
    assert "-.->|" in out                        # dashed edge to sentinel


def test_render_graph_dot():
    out = render_graph(STACKS, "dot")
    assert out.startswith("digraph ciu_provisioning")
    assert 'rankdir=LR' in out
    assert '"applications/controller" -> "infra/db-core"' in out


def test_render_graph_json_roundtrip():
    data = json.loads(render_graph(STACKS, "json"))
    assert "stacks" in data and "edges" in data
    triples = {(e["from"], e["ref"], e["provided"]) for e in data["edges"]}
    assert ("applications/controller", "pg:db/dstdns", True) in triples
    assert ("applications/controller", "vault:secret/redis/password", False) in triples


def test_render_graph_empty():
    assert render_graph({}, "mermaid").strip() == "flowchart LR"


def test_action_graph_prints_and_returns_zero(capsys):
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {"path": "infra/db-core", "service": {"path": "infra/db-core", "enabled": True}},
        {"path": "applications/controller", "service": {"path": "applications/controller", "enabled": True}},
    ]
    rendered = {
        "infra/db-core": {"db_core": {"provides": ["pg:db/dstdns"], "requires": []}},
        "applications/controller": {"controller": {"requires": ["pg:db/dstdns"], "provides": []}},
    }
    rc = deploy.action_graph(Path("/tmp"), profile, selection, rendered, fmt="mermaid")
    assert rc == 0
    assert "flowchart LR" in capsys.readouterr().out


def test_action_graph_empty_selection_returns_zero(capsys):
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "infra/x", "service": {"path": "infra/x", "enabled": True}}]
    rendered = {"infra/x": {"x": {"image": "nginx"}}}  # no requires/provides
    rc = deploy.action_graph(Path("/tmp"), profile, selection, rendered)
    assert rc == 0
