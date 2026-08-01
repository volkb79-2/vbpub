"""Focused branch contracts for healthcheck parsing and image probes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_shell_parser_ignores_empty_and_invalid_segments_before_later_tool():
    assert health._parse_cmd_shell_tools("|| not@a-tool || /usr/bin/curl") == ["curl"]


def test_empty_healthcheck_tools_are_omitted_while_later_services_are_scanned(tmp_path):
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  empty:
    image: example/empty:1
    healthcheck:
      test: [CMD-SHELL, "exit 0"]
  api:
    image: example/api:1
    healthcheck:
      test: [CMD, /usr/bin/curl]
""",
        encoding="utf-8",
    )

    assert health.extract_healthcheck_tools(compose) == {
        "api": ("example/api:1", ["curl"]),
    }


def test_nonzero_image_metadata_still_uses_direct_tool_probes(monkeypatch):
    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "inspect failed")
        tool = argv[-1].split()[-1]
        return subprocess.CompletedProcess(argv, 0 if tool == "curl" else 127, "", "")

    monkeypatch.setattr(health._subprocess, "run", fake_run)

    assert health.probe_image_tools("example/api:1", ["curl", "wget"]) == {
        "curl": True,
        "wget": False,
    }


def test_preflight_reuses_cached_availability_for_second_service(tmp_path, monkeypatch):
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  first:
    image: example/api:1
    healthcheck:
      test: [CMD, curl]
  second:
    image: example/api:1
    healthcheck:
      test: [CMD, curl]
""",
        encoding="utf-8",
    )
    calls = 0

    def fake_probe(image: str, tools: list[str]) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls > 1:
            pytest.fail("cached image/tool availability was probed again")
        return {tools[0]: False}

    monkeypatch.setattr(health, "probe_image_tools", fake_probe)
    warnings: list[str] = []

    result = health.preflight_probe(
        [compose], warn_fn=warnings.append, info_fn=lambda _message: None
    )

    assert result == warnings
    assert len(result) == 2
    assert all(service in result[0] + result[1] for service in ("first", "second"))
    assert all("curl" in warning and "none found" in warning for warning in result)
