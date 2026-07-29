"""Malformed rendered stack rejection for CIU's public graph action."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, provisioning  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_graph_rejects_invalid_rendered_stack_before_publishing_topology(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S3.5 corruption cannot be advertised as an empty dependency graph."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}}},
    )
    monkeypatch.setattr(
        provisioning,
        "render_graph",
        lambda *_args, **_kwargs: pytest.fail("invalid stack config must not publish a graph"),
    )

    assert deploy.action_graph(
        tmp_path,
        profile,
        [{"path": "applications/api"}],
        {"applications/api": {"state": {}}},
        fmt="json",
    ) == 2

    output = capsys.readouterr().out
    assert "[S3.5] Stack config has no non-reserved top-level key" in output
    assert "No stacks with requires/provides" not in output
