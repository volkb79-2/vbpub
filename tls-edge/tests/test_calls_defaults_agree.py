"""tls-edge side: the Mattermost Calls defaults are on and internally consistent.

Operator decision 2026-11-04: Calls is ON by default and tls-edge forwards
TCP+UDP 8443 as the single media pair. These oracles read the shipped defaults
and the static edge-proxy copy and pin that they agree. The cross-project check
against the Mattermost project's own defaults lives in that project's tests
(and is repeated here, skipped when the sibling is not in the checkout).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "ciu-stack" / "ciu.defaults.toml.j2"
COMPOSE_J2 = ROOT / "ciu-stack" / "ciu.compose.yml.j2"
TRAEFIK_J2 = ROOT / "ciu-stack" / "traefik.yml.j2"
EDGE_COMPOSE = ROOT / "edge-proxy" / "docker-compose.yml"
EDGE_TRAEFIK = ROOT / "edge-proxy" / "traefik.yml"


def _tls_edge() -> dict:
    return tomllib.loads(DEFAULTS.read_text(encoding="utf-8"))["tls_edge"]


def test_calls_enabled_by_default() -> None:
    assert _tls_edge()["calls"]["enabled"] is True


def test_calls_media_pair_is_8443() -> None:
    calls = _tls_edge()["calls"]
    assert calls["public_port"] == 8443
    assert calls["udp_internal_port"] == 8443
    # TCP needs its own internal port: internal 8443/TCP is the alt HTTPS entrypoint.
    assert calls["tcp_internal_port"] != 8443


def test_alt_https_does_not_collide_with_calls_tcp() -> None:
    t = _tls_edge()
    assert t["ports"]["https_alt"] != t["calls"]["public_port"]
    assert t["ports"]["https_alt"] != t["ports"]["https"]


def test_static_edge_proxy_publishes_the_same_pair() -> None:
    calls = _tls_edge()["calls"]
    text = EDGE_COMPOSE.read_text(encoding="utf-8")
    pub = calls["public_port"]
    assert f'"{pub}:{calls["tcp_internal_port"]}/tcp"' in text
    assert f'"{pub}:{calls["udp_internal_port"]}/udp"' in text
    traefik = EDGE_TRAEFIK.read_text(encoding="utf-8")
    assert re.search(
        rf'mattermost-calls-tcp:\s*\n\s*address: ":{calls["tcp_internal_port"]}"', traefik
    )
    assert re.search(
        rf'mattermost-calls-udp:\s*\n\s*address: ":{calls["udp_internal_port"]}/udp"', traefik
    )


def test_templates_use_the_configured_ports_for_both_entrypoints() -> None:
    compose = COMPOSE_J2.read_text(encoding="utf-8")
    assert "tls_edge.calls.public_port" in compose
    assert "tls_edge.calls.tcp_internal_port" in compose
    assert "tls_edge.calls.udp_internal_port" in compose
    traefik = TRAEFIK_J2.read_text(encoding="utf-8")
    assert "mattermost-calls-tcp" in traefik and "mattermost-calls-udp" in traefik


def _mattermost_defaults() -> Path | None:
    for base in (ROOT.parent, ROOT.parent / "voice-screen-share"):
        cand = base / "mattermost-server" / "ciu.defaults.toml.j2"
        if cand.is_file():
            return cand
    return None


def test_agrees_with_mattermost_project_when_present() -> None:
    mm = _mattermost_defaults()
    if mm is None:
        pytest.skip("mattermost-server sibling not in this checkout")
    mattermost = tomllib.loads(mm.read_text(encoding="utf-8"))["mattermost"]["calls"]
    calls = _tls_edge()["calls"]
    assert mattermost["enabled"] is True
    assert calls["enabled"] is True
    assert mattermost["public_port"] == calls["public_port"]
    assert mattermost["port"] == calls["udp_internal_port"]
