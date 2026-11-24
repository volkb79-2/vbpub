"""Offline guards for the jitsi-server project: render, compose validation,
no credentials/domains in tracked files, port-plan agreement, edge consumer
shape, pinned images. No network; docker is used only for `compose config`.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import jinja2
import yaml

PROJECT = Path(__file__).resolve().parents[1]
AREA = PROJECT.parent
DEFAULTS = PROJECT / "ciu.defaults.toml.j2"
COMPOSE = PROJECT / "ciu.compose.yml.j2"
GLOBAL = PROJECT / "ciu.global.defaults.toml.j2"
README_AREA = AREA / "README.md"

ENV = {"PUBLIC_FQDN": "example.test", "PUBLIC_IP": "203.0.113.7"}
LIVE_EDGE_PORTS = {80, 443, 8443, 8444, 9443}


def _render(path: Path, ctx: dict) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(path.read_text(encoding="utf-8")).render(**ctx)


def _cfg() -> dict:
    text = _render(DEFAULTS, {"env": ENV})
    return tomllib.loads(text)["jitsi"]


def _compose_text() -> str:
    cfg = _cfg()
    # hostdir values are injected by ciu at render time; stand-ins here.
    for svc in ("prosody", "jicofo", "jvb"):
        cfg[svc]["hostdir"] = {"config": f"/tmp/vol-{svc}-config"}
        if svc == "prosody":
            cfg[svc]["hostdir"]["data"] = "/tmp/vol-prosody-data"
    cfg["container_prefix"] = "jitsi-test"
    return _render(COMPOSE, {"jitsi": cfg})


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "."],
        cwd=PROJECT, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    files = [PROJECT / f for f in out if f]
    return [f for f in files if f.is_file() and "__pycache__" not in f.parts]


# ---- render -----------------------------------------------------------------

def test_defaults_and_compose_render_offline() -> None:
    text = _compose_text()
    assert "jitsi.example.test" in text
    assert "203.0.113.7" in text
    assert "Host(`jitsi.example.test`)" in text


def test_domain_is_configurable_not_hardcoded() -> None:
    other = tomllib.loads(_render(DEFAULTS, {"env": {**ENV, "PUBLIC_FQDN": "other.test"}}))["jitsi"]
    assert other["public_host"] == "jitsi.other.test"


def test_rendered_compose_validates(tmp_path: Path) -> None:
    f = tmp_path / "compose.yml"
    f.write_text(_compose_text(), encoding="utf-8")
    env = {
        **os.environ,
        "DOCKER_CONFIG": str(tmp_path),
        "JITSI_JICOFO_AUTH_PASSWORD": "x", "JITSI_JVB_AUTH_PASSWORD": "x",
        "JITSI_JICOFO_COMPONENT_SECRET": "x",
    }
    # secrets: blocks are normally added by ciu's overlay; give stand-ins
    over = tmp_path / "over.yml"
    over.write_text(
        "secrets:\n" + "".join(
            f"  {s}:\n    file: {tmp_path}/{s}\n"
            for s in ("jicofo_auth_password", "jvb_auth_password", "jicofo_component_secret")
        ), encoding="utf-8")
    for s in ("jicofo_auth_password", "jvb_auth_password", "jicofo_component_secret"):
        (tmp_path / s).write_text("x")
    proc = subprocess.run(
        ["docker", "compose", "-f", str(f), "-f", str(over), "config", "-q"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


# ---- no credentials / no domain in tracked files -----------------------------

def test_no_credentials_in_tracked_files() -> None:
    hexlike = re.compile(r"\b[0-9a-f]{32,}\b")
    pw_literal = re.compile(r"(?i)\b(password|secret|token)\w*\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{16,}[\"']?")
    for f in _tracked_files():
        if f.name == "ciu.env" or f.suffix in {".pyc"}:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        # digests are 64 hex after "sha256:"; strip them before scanning
        scrubbed = re.sub(r"sha256:[0-9a-f]{64}", "", text)
        if f.name == "CLIENT.md":  # public release-asset checksums, by design
            scrubbed = re.sub(r"\b[0-9a-f]{64}\b", "", scrubbed)
        assert not hexlike.search(scrubbed), f"hex-like secret in {f.name}"
        for m in pw_literal.finditer(scrubbed):
            assert False, f"credential literal in {f.name}: {m.group(0)[:20]}..."


def test_no_hardcoded_operator_domain_or_ip() -> None:
    # assembled from fragments so this file does not match itself
    bad = re.compile("dchive" + r"\.de|gstamm" + "tisch|152" + r"\.53\.179\.117")
    for f in _tracked_files():
        if f.name == "ciu.env" or f.suffix == ".pyc":
            continue
        assert not bad.search(f.read_text(encoding="utf-8", errors="ignore")), f.name


def test_secret_store_is_gitignored_and_secrets_are_0600() -> None:
    cfg = _cfg()
    for name, spec in cfg["secrets"].items():
        assert spec["directive"].startswith("GEN_LOCAL:jitsi/"), name
        assert spec["mode"] == "0600", name
    for rel in (".ciu/secrets/jitsi/user_admin",):
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=PROJECT, capture_output=True)
        assert r.returncode == 0, f"{rel} is not gitignored"


def test_rendered_users_and_password_declarations_are_exactly_three_hook_secrets() -> None:
    cfg = _cfg()
    assert [u["name"] for u in cfg["users"]] == ["admin", "alice", "bob"]
    assert "dummy_users" not in cfg["auth"]
    assert all("dummy" not in u for u in cfg["users"])

    expected = {
        "user_admin",
        "user_alice",
        "user_bob",
        "jicofo_auth_password",
        "jvb_auth_password",
        "jicofo_component_secret",
    }
    assert set(cfg["secrets"]) == expected
    for username in ("admin", "alice", "bob"):
        spec = cfg["secrets"][f"user_{username}"]
        assert spec["directive"] == f"GEN_LOCAL:jitsi/user_{username}"
        assert spec["mode"] == "0600"
        assert spec["consumed_by"] == "hook"
    assert cfg["hooks"]["post_compose"] == ["hooks/post_compose_users.py"]
    assert (PROJECT / "hooks" / "post_compose_users.py").is_file()


def test_auth_is_on_by_default() -> None:
    cfg = _cfg()
    assert cfg["auth"]["enabled"] is True and cfg["auth"]["type"] == "internal"
    assert cfg["auth"]["guests"] is False
    text = _compose_text()
    assert 'ENABLE_AUTH: "1"' in text and 'AUTH_TYPE: "internal"' in text


# ---- port plan ----------------------------------------------------------------

def test_port_plan_agrees_with_the_area_readme() -> None:
    cfg = _cfg()
    port = cfg["jvb"]["port"]
    assert port not in LIVE_EDGE_PORTS
    rows = [r for r in README_AREA.read_text(encoding="utf-8").splitlines()
            if re.match(rf"\|\s*{port}\s*\|\s*UDP\s*\|", r)]
    assert len(rows) == 1 and "Jitsi" in rows[0], "port plan row for the JVB port"
    compose = _compose_text()
    assert f'"{port}:{port}/udp"' in compose
    # the only host-published ports in the project: the JVB media port
    published = re.findall(r'^\s+- "(\d+):\d+(?:/\w+)?"\s*$', compose, re.MULTILINE)
    assert published == [str(port)]


def test_no_tcp_or_other_host_ports_published() -> None:
    compose = _compose_text()
    assert compose.count("ports:") - compose.count("expose:") <= 1
    assert "traefik.tcp" not in compose and "traefik.udp" not in compose


# ---- edge consumer shape ---------------------------------------------------------

def test_edge_consumer_labels_and_networks() -> None:
    text = _compose_text()
    cfg = _cfg()
    r = cfg["router_name"]
    public_host = cfg["public_host"]
    web_port = cfg["web"]["port"]
    host_rule = f"Host({chr(96)}{public_host}{chr(96)})"
    services = text.split("services:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    web = services.split("\n  jitsi-web:\n", 1)[1]
    assert f'traefik.http.routers.{r}.rule: "{host_rule}"' in web
    assert f'traefik.http.routers.{r}.entrypoints: "websecure"' in web
    assert f'traefik.http.services.{r}.loadbalancer.server.port: "{web_port}"' in web
    assert 'traefik.docker.network: "ingress_public"' in web
    assert "ingress_public" in text
    # only the web service joins the edge network
    assert "- ingress" in web
    assert services.split("\n  jitsi-web:\n", 1)[0].count("- ingress") == 0
    # service key must not be a generic alias on the shared network
    assert re.search(r"^  web:", text, re.MULTILINE) is None
    assert "no-new-privileges:true" in text and "cap_drop:" in text


def _tls_edge_default(output_dir: Path, *, http3: bool) -> tuple[dict, dict, dict]:
    edge_root = PROJECT.parents[1] / "tls-edge"
    renderer_path = edge_root / "scripts" / "render_standalone.py"
    spec = importlib.util.spec_from_file_location("jitsi_tls_edge_renderer", renderer_path)
    assert spec is not None and spec.loader is not None, f"cannot load {renderer_path}"
    renderer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = renderer
    spec.loader.exec_module(renderer)

    stack_dir = edge_root / "ciu-stack"
    edge_cfg = renderer.load_config(stack_dir, defaults_only=True)
    edge_cfg["http3"]["enabled"] = http3
    renderer.validate(edge_cfg)
    renderer.render_outputs(stack_dir, output_dir, edge_cfg, certs_only=False)
    static = yaml.safe_load((output_dir / "traefik.yml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((output_dir / "docker-compose.yml").read_text(encoding="utf-8"))
    return edge_cfg, static, compose


def test_default_tls_edge_contract_accepts_jitsi_router_and_optional_http3(tmp_path: Path) -> None:
    edge_h2, static_h2, compose_h2 = _tls_edge_default(tmp_path / "edge-h2", http3=False)
    jitsi = _cfg()
    compose = yaml.safe_load(_compose_text())
    web = compose["services"]["jitsi-web"]
    labels = web["labels"]
    router = jitsi["router_name"]

    assert jitsi["ingress_network"] == edge_h2["ingress_network"]
    assert labels["traefik.docker.network"] == edge_h2["ingress_network"]
    entrypoint_name = labels[f"traefik.http.routers.{router}.entrypoints"]
    entrypoint = static_h2["entryPoints"][entrypoint_name]
    assert entrypoint["address"] == ":443"
    assert "http3" not in entrypoint
    assert entrypoint["http"]["tls"]["certResolver"] == edge_h2["acme"]["resolver_name"]
    https_port = edge_h2["ports"]["https"]
    h2_ports = compose_h2["services"]["traefik"]["ports"]
    assert f"{https_port}:443/tcp" in h2_ports
    assert f"{https_port}:443/udp" not in h2_ports

    edge_h3, static_h3, compose_h3 = _tls_edge_default(tmp_path / "edge-h3", http3=True)
    h3_entrypoint = static_h3["entryPoints"][entrypoint_name]
    h3_port = edge_h3["ports"]["https"]
    assert h3_entrypoint["http3"]["advertisedPort"] == h3_port
    assert f"{h3_port}:443/tcp" in compose_h3["services"]["traefik"]["ports"]
    assert f"{h3_port}:443/udp" in compose_h3["services"]["traefik"]["ports"]


def test_own_names_and_governance() -> None:
    g = tomllib.loads(GLOBAL.read_text(encoding="utf-8"))
    assert g["deploy"]["project_name"] == "jitsi"
    for line in re.findall(r"container_name: (.+)", _compose_text()):
        assert line.startswith("jitsi-test-")
    text = _compose_text()
    assert text.count("mem_limit:") == 4 and text.count("cpus:") == 4


# ---- images ----------------------------------------------------------------------

def test_images_pinned_by_digest_and_at_least_14_days_old() -> None:
    cfg = _cfg()
    checked = dt.date.fromisoformat(cfg["images"]["checked_on"])
    for svc in ("web", "prosody", "jicofo", "jvb"):
        s = cfg[svc]
        assert s["image"].startswith(("jitsi/", "ghcr.io/jitsi/"))
        assert re.fullmatch(r"stable-\d+(-\d+)?@sha256:[0-9a-f]{64}", s["image_tag"]), svc
        age = (checked - dt.date.fromisoformat(s["image_date"])).days
        if not cfg["images"].get("freshness_exception"):
            assert age >= cfg["images"]["min_age_days"], svc
        else:
            assert cfg["images"]["freshness_exception_reason"], svc
    # all four from the same release
    assert len({cfg[s]["image_tag"].split("@")[0] for s in ("web", "prosody", "jicofo", "jvb")}) == 1


def test_hostdirs_are_not_group_writable() -> None:
    cfg = _cfg()
    # web has no hostdir: its /config is the rendered, read-only custom-config.js mount
    assert "hostdir" not in cfg["web"]
    for svc in ("prosody", "jicofo", "jvb"):
        mode = int(cfg[svc]["hostdir"]["config"]["mode"], 8)
        assert mode & 0o027 == 0, svc
