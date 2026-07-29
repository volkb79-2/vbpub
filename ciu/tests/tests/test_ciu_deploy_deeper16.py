"""Malformed rendered stack validation for the public ``ciu check`` action."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, provisioning  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_check_rejects_invalid_rendered_stack_before_live_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S3.5 corruption is config-red, never a green empty graph or live probe."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}}},
    )
    monkeypatch.setattr(
        provisioning,
        "probe_ref",
        lambda *_args, **_kwargs: pytest.fail("invalid stack config must not trigger a live probe"),
    )

    assert deploy.action_check(
        tmp_path,
        profile,
        [{"path": "applications/api"}],
        {"applications/api": {"state": {}}},
        live=True,
    ) == 2

    output = capsys.readouterr().out
    assert "[S3.5] Stack config has no non-reserved top-level key" in output
    assert "check passed" not in output
