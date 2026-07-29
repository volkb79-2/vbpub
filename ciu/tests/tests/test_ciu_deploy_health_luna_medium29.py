"""Health preflight coverage for shell-string and no-healthcheck paths."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_string_healthcheck_negation_probes_curl_for_service_image(tmp_path, monkeypatch):
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api:1
    healthcheck:
      test: '! /usr/bin/curl -fsS http://localhost/health'
""",
        encoding="utf-8",
    )
    probed: list[tuple[str, list[str]]] = []

    def fake_probe(image: str, tools: list[str]) -> dict[str, bool]:
        probed.append((image, tools))
        return {tool: True for tool in tools}

    monkeypatch.setattr(health, "probe_image_tools", fake_probe)
    warnings: list[str] = []
    info: list[str] = []

    result = health.preflight_probe([compose], warn_fn=warnings.append, info_fn=info.append)

    assert result == warnings == []
    assert probed == [("example/api:1", ["curl"])]
    assert any("api" in message and "curl" in message for message in info)


def test_service_without_healthcheck_is_benign_and_informational(tmp_path, monkeypatch):
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api:1
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        health,
        "probe_image_tools",
        lambda *_args, **_kwargs: pytest.fail("no healthcheck must not start an image probe"),
    )
    warnings: list[str] = []
    info: list[str] = []

    result = health.preflight_probe([compose], warn_fn=warnings.append, info_fn=info.append)

    assert result == warnings == []
    assert info == [f"  {compose}: no CMD/CMD-SHELL healthchecks found"]
