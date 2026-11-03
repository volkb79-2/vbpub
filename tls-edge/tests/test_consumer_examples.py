"""The consumer examples: `docker compose config` offline + a label lint against the edge."""
from __future__ import annotations

import json
import re
import subprocess

import pytest
import yaml

from conftest import ROOT, defaults_config, needs_docker, needs_jinja, render_to

EXAMPLES = sorted(p.parent for p in (ROOT / "consumer-examples").glob("*/docker-compose.yml"))
ENV = {"TLS_EDGE_DOMAIN": "example.test",
       "GUARD_HASH": "user:$2y$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"}
EXPECTED = {"pattern-a-public-only", "pattern-d-guarded", "pattern-e-media-udp"}


def _config(example) -> dict:
    import os

    result = subprocess.run(
        ["docker", "compose", "-f", str(example / "docker-compose.yml"), "config", "--format", "json"],
        capture_output=True, text=True, cwd=example, env={**os.environ, **ENV},
    )
    assert result.returncode == 0, f"{example.name}: {result.stderr}"
    return json.loads(result.stdout)


def test_example_set_is_the_documented_one():
    assert {p.name for p in EXAMPLES} == EXPECTED


@needs_docker
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_example_compose_config_is_valid_offline(example):
    model = _config(example)
    for service in model["services"].values():
        assert "ports" not in service, "a consumer must not publish host ports"
    # The routed service (the one with traefik.enable) joins the shared network.
    routed = [s for s in model["services"].values() if s.get("labels", {}).get("traefik.enable") == "true"]
    assert routed and all("ingress_public" in s["networks"] for s in routed)


@needs_docker
@needs_jinja
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_labels_target_entrypoints_the_edge_defines(example, tmp_path):
    out = render_to(tmp_path, defaults_config())
    entrypoints = set(yaml.safe_load((out / "traefik.yml").read_text())["entryPoints"])
    labels = {}
    for service in _config(example)["services"].values():
        labels.update(service.get("labels", {}))
    used = {v for k, v in labels.items() if k.endswith(".entrypoints")}
    assert used and used <= entrypoints, (used - entrypoints)
    # No alternate port and no real domain.
    blob = json.dumps(labels)
    assert "websecure-alt" not in blob and "my.domain" not in blob and "9443" not in blob


@needs_docker
def test_every_router_has_a_rule_and_a_service_and_hosts_use_the_domain_variable():
    for example in EXAMPLES:
        labels = {}
        for service in _config(example)["services"].values():
            labels.update(service.get("labels", {}))
        routers = {m.group(2) for k in labels if (m := re.match(r"traefik\.(http|tcp|udp)\.routers\.([^.]+)\.", k))}
        assert routers, example.name
        for key, value in labels.items():
            if key.endswith(".rule") and "Host(`" in value and ".routers." in key and ".http." in key:
                assert value.endswith(".example.test`)"), (example.name, value)
        for k in labels:
            m = re.match(r"traefik\.http\.routers\.([^.]+)\.rule", k)
            if m:
                assert f"traefik.http.services.{m.group(1)}.loadbalancer.server.port" in labels, m.group(1)


def test_router_names_are_unique_across_examples():
    """Router/service names are host-global: no two examples may collide."""
    seen: dict[str, str] = {}
    for example in EXAMPLES:
        text = (example / "docker-compose.yml").read_text()
        for name in set(re.findall(r"traefik\.(?:http|tcp|udp)\.routers\.([\w-]+)\.", text)):
            assert name not in seen, f"{name} in {example.name} and {seen[name]}"
            seen[name] = example.name


@needs_docker
def test_guarded_example_passes_the_hash_through_unmangled():
    labels = {}
    for service in _config(ROOT / "consumer-examples" / "pattern-d-guarded")["services"].values():
        labels.update(service["labels"])
    users = labels["traefik.http.middlewares.pattern-d-app-guard.basicauth.users"]
    # Compose escapes $ as $$ in the rendered model; one unescape gives the htpasswd line.
    assert users.replace("$$", "$") == ENV["GUARD_HASH"]
    assert labels["traefik.http.routers.pattern-d-app.middlewares"] == "pattern-d-app-guard"


@needs_docker
def test_guarded_example_env_example_is_the_documented_one():
    """The shipped .env.example, loaded as Compose loads it, yields a usable hash."""
    example = ROOT / "consumer-examples" / "pattern-d-guarded"
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(example / ".env.example"), "config", "--format", "json"],
        capture_output=True, text=True, cwd=example,
    )
    assert result.returncode == 0, result.stderr
    labels = {}
    for service in json.loads(result.stdout)["services"].values():
        labels.update(service["labels"])
    users = labels["traefik.http.middlewares.pattern-d-app-guard.basicauth.users"].replace("$$", "$")
    assert users.startswith("user:$2y$12$") and len(users) > 40
