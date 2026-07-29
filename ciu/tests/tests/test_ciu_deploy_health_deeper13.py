"""Fail-closed healthcheck image-metadata probe contract."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_preflight_warns_when_corrupt_image_metadata_cannot_prove_tool_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid image metadata never converts a missing health tool into green.

    A rendered CMD healthcheck names ``curl``.  If Docker's image-inspect
    response is corrupt and the controlled shell probe cannot find that tool,
    the public preflight result must warn for the service rather than trusting
    the unusable metadata.  All Docker boundaries are replaced by this test's
    deterministic subprocess seam.
    """
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api:1
    healthcheck:
      test: [CMD, curl]
""",
        encoding="utf-8",
    )

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, "{not-json", "")
        return subprocess.CompletedProcess(argv, 127, "", "curl not found")

    monkeypatch.setattr(health._subprocess, "run", fake_run)
    warnings: list[str] = []

    result = health.preflight_probe([compose], warn_fn=warnings.append, info_fn=lambda _msg: None)

    assert result == warnings
    assert len(result) == 1
    assert "api" in result[0]
    assert "curl" in result[0]
    assert "example/api:1" in result[0]
