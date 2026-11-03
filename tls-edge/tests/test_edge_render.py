"""Render/lint tests for the edge templates: every TLS mode, the port set, per-host certs."""
from __future__ import annotations

import json

import pytest
import yaml

from conftest import (
    ROOT, compose_config, defaults_config, merge, needs_docker, needs_jinja, render_to,
)

pytestmark = needs_jinja

MODES = {
    "acme-tls": {},
    "acme-http": {},
    "acme-dns": {"acme": {"dns": {"provider": "cloudflare"}}},
    "static": {"static": {"domains": ["edge.example.test"]}},
    "dev": {"static": {"domains": ["edge.example.test"]}},
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_every_mode_renders_valid_yaml(tmp_path, mode):
    cfg = merge(defaults_config(), {"tls": {"mode": mode}, **MODES[mode]})
    out = render_to(tmp_path, cfg)
    for name in ("traefik.yml", "docker-compose.yml", "conf.d/certs.yml",
                 "conf.d/options.yml", "conf.d/middlewares.yml"):
        assert yaml.safe_load((out / name).read_text()) is not None or name.startswith("conf.d")


@pytest.mark.parametrize("mode", sorted(MODES))
def test_no_alternate_entrypoint_in_any_mode(tmp_path, mode):
    cfg = merge(defaults_config(), {"tls": {"mode": mode}, **MODES[mode]})
    out = render_to(tmp_path, cfg)
    static = yaml.safe_load((out / "traefik.yml").read_text())
    assert set(static["entryPoints"]) == {
        "ping", "web", "websecure", "mattermost-calls-tcp", "mattermost-calls-udp"
    }


def test_published_ports_are_80_443_8443_pair(tmp_path):
    out = render_to(tmp_path, defaults_config())
    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    assert sorted(compose["services"]["traefik"]["ports"]) == [
        "443:443", "80:80", "8443:8443/tcp", "8443:8443/udp",
    ]
    assert "ports" not in compose["services"]["dockerproxy"]


def test_calls_off_publishes_only_80_and_443(tmp_path):
    cfg = merge(defaults_config(), {"calls": {"enabled": False}})
    out = render_to(tmp_path, cfg)
    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    assert sorted(compose["services"]["traefik"]["ports"]) == ["443:443", "80:80"]
    static = yaml.safe_load((out / "traefik.yml").read_text())
    assert "mattermost-calls-tcp" not in static["entryPoints"]


def test_http_off_drops_port_80(tmp_path):
    cfg = merge(defaults_config(), {"ports": {"expose_http": False}})
    out = render_to(tmp_path, cfg)
    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    assert "80:80" not in compose["services"]["traefik"]["ports"]
    assert "web" not in yaml.safe_load((out / "traefik.yml").read_text())["entryPoints"]


def test_acme_tls_issues_one_certificate_per_host(tmp_path):
    """Per-host certificates: the entrypoint names the resolver, no fixed domain list."""
    out = render_to(tmp_path, defaults_config())
    static = yaml.safe_load((out / "traefik.yml").read_text())
    tls = static["entryPoints"]["websecure"]["http"]["tls"]
    assert tls == {"certResolver": "letsencrypt"}          # no `domains`: per router Host()
    assert "tlsChallenge" in static["certificatesResolvers"]["letsencrypt"]["acme"]


def test_acme_dns_per_host_unless_wildcard_requested(tmp_path):
    base = {"tls": {"mode": "acme-dns"}}
    out = render_to(tmp_path / "a", merge(defaults_config(), base))
    tls = yaml.safe_load((out / "traefik.yml").read_text())["entryPoints"]["websecure"]["http"]["tls"]
    assert "domains" not in tls
    wild = merge(defaults_config(), {**base, "acme": {"dns": {"wildcard_main": "example.test"}}})
    out = render_to(tmp_path / "b", wild)
    tls = yaml.safe_load((out / "traefik.yml").read_text())["entryPoints"]["websecure"]["http"]["tls"]
    assert tls["domains"][0]["sans"] == ["*.example.test"]


def test_acme_http_needs_port_80():
    from conftest import RENDER
    cfg = merge(defaults_config(), {"tls": {"mode": "acme-http"}, "ports": {"expose_http": False}})
    with pytest.raises(SystemExit):
        RENDER.validate(cfg)


def test_adopt_existing_network_and_volume(tmp_path):
    cfg = merge(defaults_config(), {"ingress_external": True,
                                    "acme": {"volume_name": "edge-proxy_acme-data"}})
    out = render_to(tmp_path, cfg)
    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    assert compose["networks"]["ingress"] == {"name": "ingress_public", "external": True}
    assert compose["volumes"]["acme-data"] == {"name": "edge-proxy_acme-data", "external": True}


def test_no_tracked_file_hardcodes_a_real_domain():
    """Domains are inputs: tracked files use example.test-style placeholders only."""
    import subprocess

    banned = ("gstammtisch", "dchive", "richtfest")
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    hits = []
    for rel in files:
        if rel in ("get.py", "KNOWN_ISSUES_TODO_BACKLOG.md", "CHANGES.md") or rel.startswith("tests/"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        hits += [f"{rel}: {w}" for w in banned if w in text]
    assert not hits, hits


@needs_docker
def test_rendered_compose_passes_docker_compose_config(tmp_path):
    out = render_to(tmp_path, defaults_config())
    result = compose_config(out / "docker-compose.yml")
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    traefik = model["services"]["traefik"]
    assert traefik["cap_drop"] == ["ALL"] and traefik["read_only"] is True
