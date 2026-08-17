"""Exercise all optional observed-state fields in the public agent status verb."""
from __future__ import annotations

from types import SimpleNamespace

from cmru.agent import cli


def test_cmd_status_renders_error_and_timestamps_when_present(monkeypatch, capsys):
    observed = SimpleNamespace(
        health="failed",
        applied_generation=7,
        adapter_phase="deploy",
        release_digest="sha",
        error_class="RuntimeError",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:01:00Z",
    )
    monkeypatch.setattr("cmru.agent.state.read_node_id", lambda scope: "node")
    monkeypatch.setattr("cmru.agent.state.read_observed", lambda scope: observed)
    monkeypatch.setattr("cmru.agent.state.read_current_generation", lambda scope: 7)
    assert cli.cmd_status(SimpleNamespace(scope="user")) == 0
    rendered = capsys.readouterr().out
    assert "error_class:        RuntimeError" in rendered
    assert "started_at:         2026-01-01T00:00:00Z" in rendered
    assert "finished_at:        2026-01-01T00:01:00Z" in rendered
    monkeypatch.setattr("cmru.agent.state.read_observed", lambda scope: SimpleNamespace(
        health="healthy", applied_generation=7, adapter_phase="deploy", release_digest="sha",
        error_class=None, started_at=None, finished_at=None,
    ))
    assert cli.cmd_status(SimpleNamespace(scope="user")) == 0
