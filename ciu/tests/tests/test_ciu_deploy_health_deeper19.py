"""Fail-closed invalid healthcheck-mode preflight contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_unknown_list_healthcheck_mode_is_authoring_error_before_image_probe(
    tmp_path, monkeypatch
):
    """S7.7: an unsupported healthcheck mode cannot be silently healthcheck-free.

    Docker Compose accepts only ``CMD``, ``CMD-SHELL``, or ``NONE`` in list
    form.  Treating another mode as no healthcheck would falsely green-light
    an invalid deployment and skip its image probe entirely.
    """
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api:1
    healthcheck:
      test: [UNSUPPORTED, curl -fsS http://localhost/health]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        health,
        "probe_image_tools",
        lambda *_args, **_kwargs: pytest.fail("invalid healthcheck must not start an image probe"),
    )

    with pytest.raises(ValueError, match=r"\[S7\.7\].*unsupported healthcheck mode"):
        health.preflight_probe([compose])
