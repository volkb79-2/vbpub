"""Read-only Docker credential configuration contracts for CIU registry checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import registry  # noqa: E402


def test_registry_check_uses_default_docker_config_and_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A syntactically valid but malformed Docker config cannot authenticate a deploy."""
    home = tmp_path / "operator-home"
    config_path = home / ".docker" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(["not", "a", "Docker", "config"]), encoding="utf-8")
    monkeypatch.setattr(registry.Path, "home", lambda: home)

    assert registry.check_registry_auth("registry.example.test") is False


def test_registry_check_accepts_explicit_canonical_docker_hub_credential(tmp_path: Path) -> None:
    """The exact canonical Docker Hub key remains a valid credential lookup target."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"auths": {"https://index.docker.io/v1/": {"identitytoken": "hub-token"}}}),
        encoding="utf-8",
    )

    assert registry.check_registry_auth("https://index.docker.io/v1/", config_path) is True
