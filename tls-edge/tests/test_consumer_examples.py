"""The consumer examples: `docker compose config` offline + a label lint against the edge."""
from __future__ import annotations

import json
import re
import subprocess
import tomllib

import pytest
import yaml

from conftest import ROOT, defaults_config, merge, needs_docker, needs_jinja, render_to

EXAMPLES = sorted(p.parent for p in (ROOT / "consumer-examples").glob("*/docker-compose.yml"))
ENV = {"TLS_EDGE_DOMAIN": "example.test",
       "GUARD_HASH": "user:$2y$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"}
EXPECTED = {"pattern-a-public-only", "pattern-d-guarded", "pattern-e-media-udp"}
EDGE_OVERRIDES = {
    # The compose example documents that it needs tls-edge's optional Calls L4 entrypoints.
    "pattern-e-media-udp": {"calls": {"enabled": True}},
}


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
    cfg = merge(defaults_config(), EDGE_OVERRIDES.get(example.name, {}))
    out = render_to(tmp_path, cfg)
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


@needs_jinja
def test_repository_jitsi_deployment_uses_the_default_edge_contract(tmp_path):
    """The real Jitsi project must remain routable through tls-edge's websecure entrypoint."""
    project = ROOT.parent / "voice-screen-share" / "jitsi-server"
    defaults_path = project / "ciu.defaults.toml.j2"
    compose_path = project / "ciu.compose.yml.j2"
    if not defaults_path.is_file() or not compose_path.is_file():
        pytest.skip("the vbpub voice-screen-share/jitsi-server sibling is not present")

    import jinja2

    jinja = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    defaults_text = jinja.from_string(defaults_path.read_text(encoding="utf-8")).render(
        env={"PUBLIC_FQDN": "example.test", "PUBLIC_IP": "203.0.113.7"},
    )
    cfg = tomllib.loads(defaults_text)["jitsi"]
    # CIU injects these resolved host paths before rendering the real project template.
    for service in ("prosody", "jicofo", "jvb"):
        cfg[service]["hostdir"] = {"config": f"/tmp/vol-{service}-config"}
        if service == "prosody":
            cfg[service]["hostdir"]["data"] = "/tmp/vol-prosody-data"
    cfg["container_prefix"] = "jitsi-test"
    compose_text = jinja.from_string(compose_path.read_text(encoding="utf-8")).render(jitsi=cfg)
    compose = yaml.safe_load(compose_text)

    web = compose["services"]["jitsi-web"]
    labels = web["labels"]
    router = cfg["router_name"]
    assert labels["traefik.enable"] == "true"
    assert labels["traefik.docker.network"] == "ingress_public"
    assert labels[f"traefik.http.routers.{router}.rule"] == "Host(`jitsi.example.test`)"
    assert labels[f"traefik.http.routers.{router}.entrypoints"] == "websecure"
    assert labels[f"traefik.http.services.{router}.loadbalancer.server.port"] == str(cfg["web"]["port"])
    assert "ports" not in web
    assert "ingress" in web["networks"]
    assert all("ingress" not in compose["services"][name]["networks"]
               for name in ("prosody", "jicofo", "jvb"))
    assert compose["networks"]["ingress"]["name"] == "ingress_public"
    assert cfg["jvb"]["port"] == 10000
    assert compose["services"]["jvb"]["ports"] == ["10000:10000/udp"]
    assert not any(key.startswith(("traefik.tcp.", "traefik.udp.")) for key in labels)

    edge_dir = render_to(tmp_path / "edge", defaults_config())
    edge_entrypoints = yaml.safe_load(
        (edge_dir / "traefik.yml").read_text(encoding="utf-8")
    )["entryPoints"]
    assert labels[f"traefik.http.routers.{router}.entrypoints"] in edge_entrypoints


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
