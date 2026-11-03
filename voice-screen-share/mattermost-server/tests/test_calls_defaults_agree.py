"""Mattermost side: Calls is on by default and agrees with tls-edge's defaults.

Operator decision 2026-11-04: Calls ON by default; tls-edge forwards TCP+UDP
8443 (public) to the Calls listener (in-container port 8443). Reads both
projects' default files and compares the ports and the entrypoint names the
Mattermost labels reference.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1]
DEFAULTS = STACK / "ciu.defaults.toml.j2"
COMPOSE = STACK / "ciu.compose.yml.j2"


def _tls_edge_dir() -> Path | None:
    for parent in STACK.parents:
        cand = parent / "tls-edge" / "ciu-stack"
        if cand.is_dir():
            return cand
    return None


def _mm_calls() -> dict:
    return tomllib.loads(DEFAULTS.read_text(encoding="utf-8"))["mattermost"]["calls"]


def test_calls_enabled_by_default_on_media_port_8443() -> None:
    calls = _mm_calls()
    assert calls["enabled"] is True
    assert calls["port"] == 8443
    assert calls["public_port"] == 8443


def test_advertised_override_uses_the_public_port() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "MM_CALLS_ICE_HOST_PORT_OVERRIDE: \"{{ mattermost.calls.public_port }}\"" in text


def test_defaults_agree_with_tls_edge() -> None:
    edge = _tls_edge_dir()
    if edge is None:
        pytest.skip("tls-edge not in this checkout")
    tls = tomllib.loads((edge / "ciu.defaults.toml.j2").read_text(encoding="utf-8"))["tls_edge"]
    calls = _mm_calls()
    assert tls["calls"]["enabled"] is True and calls["enabled"] is True
    # Advertised public port == the port tls-edge publishes (TCP and UDP).
    assert calls["public_port"] == tls["calls"]["public_port"]
    # The Mattermost label target port is the edge's UDP internal listener number
    # (TCP gets its own internal entrypoint port; the L4 service port is ours).
    assert calls["port"] == tls["calls"]["udp_internal_port"]
    # The alternate HTTPS port was removed from the edge; Calls TCP/UDP 8443 agree.
    assert "https_alt" not in tls["ports"]
    assert tls["calls"]["tcp_internal_port"] == calls["public_port"] == 8443
    assert tls["calls"]["udp_internal_port"] == calls["port"] == 8443


def test_label_entrypoints_exist_in_tls_edge_traefik_template() -> None:
    edge = _tls_edge_dir()
    if edge is None:
        pytest.skip("tls-edge not in this checkout")
    traefik = (edge / "traefik.yml.j2").read_text(encoding="utf-8")
    labels = COMPOSE.read_text(encoding="utf-8")
    for ep in set(re.findall(r'entrypoints: "(mattermost-calls-(?:tcp|udp))"', labels)):
        assert f"{ep}:" in traefik
