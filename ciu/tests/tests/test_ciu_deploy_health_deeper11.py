"""Healthcheck preflight must fail closed on unusable rendered Compose input."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_preflight_rejects_unparseable_rendered_compose_before_image_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S7.7 does not convert corrupt runtime input into a green no-op probe."""
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text("services: [unterminated\n", encoding="utf-8")
    monkeypatch.setattr(
        health,
        "probe_image_tools",
        lambda *_args, **_kwargs: pytest.fail("invalid Compose must not launch image probes"),
    )

    with pytest.raises(ValueError, match=r"\[S7\.7\].*Cannot parse rendered Compose model"):
        health.preflight_probe([compose])
