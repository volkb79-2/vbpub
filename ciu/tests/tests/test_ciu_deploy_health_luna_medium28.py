"""Reject scalar rendered Compose healthcheck tests before image probing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_scalar_healthcheck_test_is_authoring_error_before_image_probe(tmp_path, monkeypatch):
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api:1
    healthcheck:
      test: 42
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        health,
        "probe_image_tools",
        lambda *_args, **_kwargs: pytest.fail("scalar healthcheck must not start an image probe"),
    )

    with pytest.raises(ValueError) as exc_info:
        health.preflight_probe([compose])

    message = str(exc_info.value)
    assert "[S7.7]" in message
    assert "api" in message
    assert str(compose) in message
