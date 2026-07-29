"""Focused contracts for deploy registry preflight."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def test_registry_preflight_without_url_skips_credential_check(monkeypatch):
    """An unset registry URL does not require Docker credentials."""
    calls = []

    def check_registry_auth(url):
        calls.append(url)
        return True

    monkeypatch.setattr(deploy.registry_pkg, "check_registry_auth", check_registry_auth)

    deploy.registry_preflight({})

    assert calls == []


def test_registry_preflight_reports_present_credentials(monkeypatch, capsys):
    """Authenticated registry configuration emits its operator notice."""
    monkeypatch.setattr(deploy.registry_pkg, "check_registry_auth", lambda url: True)

    deploy.registry_preflight({"deploy": {"registry": {"url": "registry.example.test"}}})

    assert "registry.example.test" in capsys.readouterr().out


def test_registry_preflight_requires_docker_login(monkeypatch):
    """Missing registry credentials raise actionable setup guidance."""
    monkeypatch.setattr(deploy.registry_pkg, "check_registry_auth", lambda url: False)

    with pytest.raises(ValueError) as exc_info:
        deploy.registry_preflight(
            {"deploy": {"registry": {"url": "registry.example.test"}}}
        )

    message = str(exc_info.value)
    assert "registry.example.test" in message
    assert "docker login" in message
