"""The optional Mattermost Calls listener stays internally consistent."""
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


def test_calls_listener_is_disabled_in_generic_defaults() -> None:
    assert _tls_edge()["calls"]["enabled"] is False


def test_calls_media_pair_is_8443() -> None:
    calls = _tls_edge()["calls"]
    assert calls["public_port"] == 8443
    assert calls["udp_internal_port"] == 8443
    # There is no alternate HTTPS entrypoint any more, so the TCP media socket
    # uses the same number as UDP (a TCP and a UDP socket do not collide).
    assert calls["tcp_internal_port"] == 8443


def test_no_alternate_https_port_by_default() -> None:
    t = _tls_edge()
    assert "https_alt" not in t["ports"]
    for path in (TRAEFIK_J2, COMPOSE_J2, EDGE_COMPOSE, EDGE_TRAEFIK):
        text = path.read_text(encoding="utf-8")
        assert "websecure-alt" not in text and "https_alt" not in text, path


def test_default_public_ports_are_web_only() -> None:
    """The generic edge opens HTTP and HTTPS; optional listeners stay closed."""
    assert _tls_edge()["http3"]["enabled"] is False
    published = re.findall(r'^\s*- "(?:\{\{.*?\}\})?([^"]+)"', EDGE_COMPOSE.read_text(), re.M)
    ports = sorted(p for p in published if re.fullmatch(r"\d+:\d+(/(tcp|udp))?", p))
    assert ports == ["443:443/tcp", "80:80/tcp"]


def test_static_edge_proxy_publishes_the_same_pair() -> None:
    calls = _tls_edge()["calls"]
    text = EDGE_COMPOSE.read_text(encoding="utf-8")
    assert calls["enabled"] is False
    assert "8443:8443/tcp" not in text
    assert "8443:8443/udp" not in text
    traefik = EDGE_TRAEFIK.read_text(encoding="utf-8")
    assert "mattermost-calls-tcp:" not in traefik
    assert "mattermost-calls-udp:" not in traefik


def test_templates_use_the_configured_ports_for_both_entrypoints() -> None:
    compose = COMPOSE_J2.read_text(encoding="utf-8")
    assert "tls_edge.calls.public_port" in compose
    assert "tls_edge.calls.tcp_internal_port" in compose
    assert "tls_edge.calls.udp_internal_port" in compose
    traefik = TRAEFIK_J2.read_text(encoding="utf-8")
    assert "if tls_edge.calls.enabled" in traefik
    assert "if tls_edge.http3.enabled" in traefik
    assert "advertisedPort: {{ tls_edge.ports.https }}" in traefik
    assert "{{ bind }}{{ tls_edge.ports.https }}:443/udp" in compose


def _mattermost_defaults() -> Path | None:
    cand = ROOT.parent / "voice-screen-share" / "mattermost-server" / "ciu.defaults.toml.j2"
    return cand if cand.is_file() else None


def test_agrees_with_mattermost_project_when_present() -> None:
    mm = _mattermost_defaults()
    if mm is None:
        pytest.skip("mattermost-server sibling not in this checkout")
    mattermost = tomllib.loads(mm.read_text(encoding="utf-8"))["mattermost"]["calls"]
    calls = _tls_edge()["calls"]
    assert mattermost["public_port"] == calls["public_port"]
    assert mattermost["port"] == calls["udp_internal_port"]
