"""Focused coverage for default TCP and absent Compose input paths."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


def test_wait_tcp_default_connector_uses_host_port_and_interval(monkeypatch):
    calls: list[tuple[tuple[str, int], float | None]] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_create_connection(address, timeout=None):
        calls.append((address, timeout))
        return FakeConnection()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    result = health.wait_tcp(
        "service.local",
        8123,
        timeout_s=9.0,
        interval_s=0.25,
        clock=lambda: 0.0,
    )

    assert result is True
    assert calls == [(('service.local', 8123), 0.25)]


def test_preflight_probe_skips_missing_compose_path(tmp_path, monkeypatch):
    missing_compose = tmp_path / "rendered.compose.yml"
    probed = []

    def fake_probe(*args, **kwargs):
        probed.append((args, kwargs))
        return {}

    monkeypatch.setattr(health, "probe_image_tools", fake_probe)

    result = health.preflight_probe([missing_compose])

    assert result == []
    assert probed == []
