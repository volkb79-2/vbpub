"""Isolation guard for the Mattermore tryout: it must not collide with the live
Mattermost (voice-screen-share/mattermost-server) or the live tls-edge.

Reads the shipped defaults and compose template only; no docker, no network.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULTS = PROJECT / "ciu.defaults.toml.j2"
COMPOSE = PROJECT / "ciu.compose.yml.j2"
GLOBAL = PROJECT / "ciu.global.defaults.toml.j2"
MM_DEFAULTS = PROJECT.parent / "mattermost-server" / "ciu.defaults.toml.j2"
MM_GLOBAL = PROJECT.parent / "mattermost-server" / "ciu.global.defaults.toml.j2"

# Public/live edge ports the tryout must never take.
LIVE_EDGE_PORTS = {80, 443, 8443, 8444, 9443}


def _cfg() -> dict:
    return tomllib.loads(DEFAULTS.read_text(encoding="utf-8"))["mattermore"]


def test_own_project_name_and_prefix() -> None:
    g = tomllib.loads(GLOBAL.read_text(encoding="utf-8"))
    assert g["deploy"]["project_name"] == "mattermore"
    assert "mattermore" in _cfg()["container_prefix"]
    if MM_GLOBAL.is_file():
        live = tomllib.loads(MM_GLOBAL.read_text(encoding="utf-8"))
        assert live["deploy"]["project_name"] != g["deploy"]["project_name"]


def test_no_host_ports_by_default() -> None:
    cfg = _cfg()
    assert cfg["tryout"]["media_host_port"] == 0
    assert cfg["expose_public"] is False
    text = COMPOSE.read_text(encoding="utf-8")
    # every `ports:` block is behind the media_host_port condition
    for m in re.finditer(r"^\s+ports:", text, re.MULTILINE):
        before = text[: m.start()]
        assert before.rfind("{% if mattermore.tryout.media_host_port %}") > before.rfind("{% endif %}"), \
            "a ports: block is not guarded by tryout.media_host_port"


def test_published_media_port_is_never_a_live_edge_port() -> None:
    port = _cfg()["tryout"]["media_host_port"]
    assert port not in LIVE_EDGE_PORTS


def test_no_tls_edge_calls_routers_or_ingress_by_default() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "mattermost-calls-tcp" not in text and "mattermost-calls-udp" not in text
    assert "traefik.tcp" not in text and "traefik.udp" not in text
    # the only router name is distinct from the live Mattermost's
    assert "nyxloom-mattermost" not in text


def test_container_and_volume_names_do_not_overlap_the_live_project() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    names = re.findall(r"container_name: (.+)", text)
    assert names and all(n.startswith("{{ mattermore.container_prefix }}") for n in names)
    if MM_DEFAULTS.is_file():
        live = MM_DEFAULTS.read_text(encoding="utf-8")
        assert "-mattermost-db" not in "".join(names)
        assert "mattermore" not in live  # live project never mentions the tryout


def test_images_are_pinned_by_digest() -> None:
    cfg = _cfg()
    for image_tag in (cfg["app"]["image_tag"], cfg["postgres"]["image_tag"]):
        assert re.search(r"@sha256:[0-9a-f]{64}$", image_tag), image_tag


def test_calls_media_port_matches_the_image_listener() -> None:
    cfg = _cfg()
    assert cfg["calls"]["enabled"] is True
    assert cfg["calls"]["group_calls_allowed"] is True
    assert cfg["calls"]["port"] == 8443
