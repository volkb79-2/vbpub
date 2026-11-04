"""Offline guard for the Fluxer tryout (voice-screen-share/fluxer-server).

No docker daemon, no network: it reads the shipped templates, renders them
with jinja2 (when importable) and loads the hooks via importlib. Covers the
isolation contract (own names, only the LiveKit pair published, nothing of the
live edge), digest pinning, port-plan agreement with the area README, the
resource caps, "no credentials / no hard-coded domain in tracked files", and
the secret-generation hook's idempotence and file modes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]
AREA = PROJECT.parent
DEFAULTS = PROJECT / "ciu.defaults.toml.j2"
COMPOSE = PROJECT / "ciu.compose.yml.j2"
GLOBAL = PROJECT / "ciu.global.defaults.toml.j2"
README = AREA / "README.md"

# Public/live edge ports this tryout must never publish.
LIVE_EDGE_PORTS = {80, 443, 8443, 8444, 9443}
MAX_MEM_BYTES = {"postgres": 1 << 30}
DEFAULT_MAX_MEM = 768 << 20  # api/worker are the largest after postgres


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "hooks" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _render(path: Path, **context):
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(undefined=jinja2.Undefined, keep_trailing_newline=True)
    return env.from_string(path.read_text(encoding="utf-8")).render(**context)


def _cfg(**overrides) -> dict:
    text = _render(DEFAULTS, env={"PUBLIC_FQDN": "example.test"})
    cfg = tomllib.loads(text)["fluxer"]
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


def _mem_bytes(value: str) -> int:
    m = re.fullmatch(r"(\d+)([mg])", value)
    assert m, value
    return int(m.group(1)) * (1 << 20 if m.group(2) == "m" else 1 << 30)


def _tracked_files() -> list[Path]:
    """Project files git would track: `git ls-files` when available, else a walk."""
    try:
        out = subprocess.run(
            ["git", "-C", str(PROJECT), "ls-files", "--cached", "--others", "--exclude-standard", "."],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.splitlines()
        return [PROJECT / line for line in out if (PROJECT / line).is_file()]
    except (OSError, subprocess.SubprocessError):
        return [p for p in PROJECT.rglob("*") if p.is_file() and ".ciu" not in p.parts]


# ── isolation ────────────────────────────────────────────────────────────────


def test_own_project_name_and_prefix() -> None:
    g = tomllib.loads(GLOBAL.read_text(encoding="utf-8"))
    assert g["deploy"]["project_name"] == "fluxer"
    assert g["ciu"]["standalone_root"] is True
    assert "fluxer" in _cfg()["container_prefix"]
    for sibling in ("mattermost-server", "mattermore"):
        other = AREA / sibling / "ciu.global.defaults.toml.j2"
        if other.is_file():
            live = tomllib.loads(other.read_text(encoding="utf-8"))
            assert live["deploy"]["project_name"] != g["deploy"]["project_name"]


def test_governance_uses_the_dev_background_slice() -> None:
    g = tomllib.loads(GLOBAL.read_text(encoding="utf-8"))
    assert g["governance"]["enabled"] is True
    assert g["governance"]["cgroup_parent"] == "dev-background.slice"


def test_container_names_use_the_prefix() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    names = re.findall(r"container_name: (.+)", text)
    assert len(names) == 21
    assert all(n.startswith("{{ fluxer.container_prefix }}-") for n in names)


def test_only_the_livekit_pair_is_published() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    port_blocks = re.findall(r"^\s+ports:\n((?:\s+- .+\n)+)", text, re.MULTILINE)
    assert len(port_blocks) == 1, "exactly one ports: block (LiveKit media)"
    entries = re.findall(r'- "(.+)"', port_blocks[0])
    assert entries == [
        "{{ fluxer.livekit.tcp_port }}:{{ fluxer.livekit.tcp_port }}",
        "{{ fluxer.livekit.udp_port }}:{{ fluxer.livekit.udp_port }}/udp",
    ]
    cfg = _cfg()
    for port in (cfg["livekit"]["tcp_port"], cfg["livekit"]["udp_port"]):
        assert port not in LIVE_EDGE_PORTS


def test_no_host_network_privileged_or_docker_socket() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    for needle in ("network_mode", "privileged", "pid: host", "docker.sock", "cgroupns"):
        assert needle not in text


def test_no_live_edge_routers_and_own_router_name() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "nyxloom-mattermost" not in text and "mattermore-tryout" not in text
    assert "traefik.tcp" not in text and "traefik.udp" not in text
    assert text.count("traefik.http.routers.fluxer-vss.") == 2


def test_not_routed_through_the_edge_when_disabled() -> None:
    cfg = _cfg(expose_public=False)
    assert cfg["expose_public"] is False
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_render(COMPOSE, fluxer=cfg))
    assert "ingress" not in doc["networks"]
    assert all("labels" not in svc and "ingress" not in svc.get("networks", []) for svc in doc["services"].values())


def test_routed_through_the_edge_when_enabled() -> None:
    yaml = pytest.importorskip("yaml")
    cfg = _cfg(expose_public=True)
    doc = yaml.safe_load(_render(COMPOSE, fluxer=cfg))
    edge = doc["services"]["edge"]
    assert "ingress" in edge["networks"]
    assert doc["networks"]["ingress"] == {"external": True, "name": "ingress_public"}
    assert edge["labels"]["traefik.http.routers.fluxer-vss.rule"] == "Host(`fluxer.example.test`)"
    assert edge["labels"]["traefik.http.services.fluxer-vss.loadbalancer.server.port"] == "8080"
    # nothing else joins the shared ingress network
    for name, svc in doc["services"].items():
        if name != "edge":
            assert "ingress" not in svc.get("networks", [])


def test_edge_is_plain_http_behind_proxy_mode() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_render(COMPOSE, fluxer=_cfg()))
    env = doc["services"]["edge"]["environment"]
    assert env["FLUXER_EDGE_SITE_ADDRESS"] == ":8080"
    assert env["FLUXER_EDGE_TLS_DIRECTIVE"] == ""
    assert "ports" not in doc["services"]["edge"]
    caddyfile = (PROJECT / "Caddyfile").read_text(encoding="utf-8")
    assert "{$FLUXER_EDGE_SITE_ADDRESS}" in caddyfile and "/_health" in caddyfile


# ── pins and caps ────────────────────────────────────────────────────────────


def test_every_image_is_pinned_by_digest() -> None:
    images = _cfg()["images"]
    assert len(images) == 19
    for name, ref in images.items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), (name, ref)
        assert re.search(r":[\w.-]+@sha256:", ref), f"{name}: tag missing next to the digest"
    text = COMPOSE.read_text(encoding="utf-8")
    assert re.findall(r"image: (.+)", text) and all(
        m.startswith("{{ fluxer.images.") for m in re.findall(r"image: (.+)", text)
    )


def test_bundle_pin_and_freshness_exception_are_recorded() -> None:
    cfg = _cfg()
    assert cfg["release"]["bundle"] == "fluxer-release@2026.1102.205049"
    assert cfg["release"]["freshness_exception"] is True
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    assert "14-day" in readme and "2026-11-04" in readme and cfg["release"]["bundle"] in readme


def test_every_service_has_memory_and_cpu_caps_below_the_bundle_defaults() -> None:
    limits = _cfg()["limits"]
    compose = COMPOSE.read_text(encoding="utf-8")
    services = re.findall(r"^  ([a-z-]+):\n", compose.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0], re.MULTILINE)
    assert len(services) == 21
    for svc in services:
        key = svc.replace("-", "_")
        assert key in limits, svc
        assert f"{{{{ fluxer.limits.{key}.mem }}}}" in compose, svc
        cap = MAX_MEM_BYTES.get(key, DEFAULT_MAX_MEM)
        assert _mem_bytes(limits[key]["mem"]) <= cap, (svc, limits[key])
        assert float(limits[key]["cpus"]) <= 1.0
    assert sum(_mem_bytes(v["mem"]) for v in limits.values()) < 7 << 30


def test_compose_renders_and_is_valid_yaml() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_render(COMPOSE, fluxer=_cfg()))
    assert len(doc["services"]) == 21
    assert doc["networks"]["fluxer"]["driver"] == "bridge"
    for name, svc in doc["services"].items():
        assert svc.get("mem_limit"), name
        assert svc.get("cpus"), name
    # secrets only via env_file, never as a literal in the rendered compose
    rendered = json.dumps(doc)
    assert "FLUXER_SETUP_TOKEN" not in rendered
    assert "FLUXER_POSTGRES_PASSWORD" not in rendered


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI absent")
def test_docker_compose_config_validates(tmp_path: Path) -> None:
    (tmp_path / "ciu.compose.yml").write_text(_render(COMPOSE, fluxer=_cfg()), encoding="utf-8")
    proc = subprocess.run(
        ["docker", "compose", "-f", str(tmp_path / "ciu.compose.yml"), "config", "-q"],
        capture_output=True, text=True, timeout=60, env={**os.environ, "DOCKER_CONFIG": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr[-400:]


# ── port plan agreement ──────────────────────────────────────────────────────


def test_ports_agree_with_the_area_readme_port_plan() -> None:
    cfg = _cfg()
    tcp, udp = cfg["livekit"]["tcp_port"], cfg["livekit"]["udp_port"]
    text = README.read_text(encoding="utf-8")
    section = text.split("## Port plan", 1)[1].split("\n## ", 1)[0]
    table = [r for r in section.splitlines() if r.startswith("|")]
    claimed = [r for r in table if re.search(rf"\b{tcp}\b", r)]
    assert len(claimed) == 1, "the TCP port is claimed exactly once"
    assert "Fluxer" in claimed[0] and re.search(rf"\b{udp}\b", claimed[0]), claimed
    assert len([r for r in table if re.search(rf"\b{udp}\b", r)]) == 1
    # the Matrix/LiveKit row keeps 7881/7882
    matrix = [r for r in table if "Matrix" in r and "LiveKit" in r]
    assert matrix and "7881" in matrix[0] and str(tcp) not in matrix[0]
    assert (tcp, udp) == (7891, 7892)


def test_livekit_config_uses_the_configured_pair() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(_render(COMPOSE, fluxer=_cfg()))
    lk = yaml.safe_load(doc["services"]["livekit"]["environment"]["LIVEKIT_CONFIG"])
    assert lk["rtc"]["tcp_port"] == 7891 and lk["rtc"]["udp_port"] == 7892
    assert doc["services"]["livekit"]["ports"] == ["7891:7891", "7892:7892/udp"]


# ── no credentials, no hard-coded domain in tracked files ───────────────────


def test_no_ciu_runtime_state_is_tracked() -> None:
    names = {p.relative_to(PROJECT).as_posix() for p in _tracked_files()}
    assert not any(n.startswith(".ciu/") or n in {"ciu.toml", "ciu.env", "ciu.compose.yml"} for n in names)
    assert not any(n.endswith(("accounts.json", "fluxer-secrets.json")) or n.split("/")[-1].startswith("env.") for n in names)


def test_no_credentials_in_tracked_files() -> None:
    token = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9_-])")
    offenders = []
    for path in _tracked_files():
        if path.suffix in {".pyc"} or "__pycache__" in path.parts or path.name == "test_fluxer_project.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            for m in token.finditer(line):
                value = m.group(0)
                if re.search(r"sha256:[0-9a-f]{64}$", line[: m.end()]) or re.fullmatch(r"[0-9a-f]{64}", value):
                    continue  # image digests
                if re.fullmatch(r"[A-Za-z]+([-_./][A-Za-z]+)+|[a-z_.]+", value) or "/" in value or "." in value:
                    continue  # prose, paths, dotted names
                if re.fullmatch(r"[A-Z][A-Z0-9_]+", value):
                    continue  # ENV_NAMES
                if value.startswith("-") or re.search(r"=.", value):
                    continue  # command-line flags and key=value settings
                if not (re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value)):
                    continue  # rulers, plain words: a generated secret mixes letters and digits
                offenders.append(f"{path.name}:{line_no}")
    assert not offenders, offenders


def test_no_hard_coded_domain_in_tracked_files() -> None:
    cfg_text = DEFAULTS.read_text(encoding="utf-8")
    assert 'public_host = "fluxer.{{ env.PUBLIC_FQDN }}"' in cfg_text
    for path in _tracked_files():
        if "__pycache__" in path.parts or path.name == "test_fluxer_project.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("gstammtisch", "dchive", "152.53."):
            assert needle not in text, f"{path.name} mentions {needle}"


# ── hooks ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def secrets_hook():
    return _load_hook("pre_compose_secrets")


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(stack_dir=str(tmp_path), wait_healthy=None)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl absent")
def test_secrets_hook_generates_once_with_private_modes(secrets_hook, tmp_path: Path) -> None:
    cfg = {"fluxer": _cfg()}
    secrets_hook.run(cfg, _ctx(tmp_path))
    d = tmp_path / ".ciu" / "secrets"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    store_path = d / "fluxer-secrets.json"
    first = json.loads(store_path.read_text())
    expected_files = {"fluxer-secrets.json", "accounts.json", "env.fluxer", "env.api", "env.postgres",
                      "env.meilisearch", "env.livekit", "env.s3init"}
    assert {p.name for p in d.iterdir()} == expected_files
    for p in d.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    # formats
    assert len(first["vapid_public"]) == 87 and len(first["vapid_private"]) == 43
    import base64
    assert len(base64.b64decode(first["upload_relay_secret_b64"])) == 32
    assert all(len(first[k]) == 64 for k in secrets_hook._HEX_KEYS)
    # idempotent: a second run changes nothing
    secrets_hook.run(cfg, _ctx(tmp_path))
    assert json.loads(store_path.read_text()) == first
    accounts = json.loads((d / "accounts.json").read_text())
    assert set(accounts) == {"fx_admin", "fx_alice", "fx_bob", "fx_carol"}
    assert len({*accounts.values()}) == 4


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl absent")
def test_secrets_hook_keeps_existing_values_and_fills_gaps(secrets_hook, tmp_path: Path) -> None:
    cfg = {"fluxer": _cfg()}
    secrets_hook.run(cfg, _ctx(tmp_path))
    d = tmp_path / ".ciu" / "secrets"
    store = json.loads((d / "fluxer-secrets.json").read_text())
    del store["meili_master_key"]
    (d / "fluxer-secrets.json").write_text(json.dumps(store))
    kept = store["postgres_password"]
    secrets_hook.run(cfg, _ctx(tmp_path))
    after = json.loads((d / "fluxer-secrets.json").read_text())
    assert after["postgres_password"] == kept and after["meili_master_key"]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl absent")
def test_env_files_map_secrets_to_the_variable_names_the_compose_expects(secrets_hook, tmp_path: Path) -> None:
    secrets_hook.run({"fluxer": _cfg()}, _ctx(tmp_path))
    d = tmp_path / ".ciu" / "secrets"

    def keys(name: str) -> set[str]:
        return {line.split("=", 1)[0] for line in (d / name).read_text().splitlines()}

    assert keys("env.postgres") == {"POSTGRES_PASSWORD"}
    assert keys("env.meilisearch") == {"MEILI_MASTER_KEY"}
    assert keys("env.livekit") == {"LIVEKIT_KEYS"}
    assert keys("env.s3init") == {"FLUXER_S3_ACCESS_KEY", "FLUXER_S3_SECRET_KEY"}
    assert keys("env.api") == {"FLUXER_SETUP_TOKEN"}
    assert {"FLUXER_POSTGRES_PASSWORD", "FLUXER_VAPID_PUBLIC_KEY", "FLUXER_VAPID_PRIVATE_KEY",
            "FLUXER_MEDIA_PROXY_UPLOAD_RELAY_SECRET_BASE64", "FLUXER_LIVEKIT_API_SECRET"} <= keys("env.fluxer")
    # the same secret is the same value everywhere it is shared
    pg = (d / "env.postgres").read_text().split("=", 1)[1]
    assert f"FLUXER_POSTGRES_PASSWORD={pg}" in (d / "env.fluxer").read_text()


def test_hook_output_never_contains_a_secret_value(secrets_hook, tmp_path: Path, capsys) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl absent")
    secrets_hook.run({"fluxer": _cfg()}, _ctx(tmp_path))
    out = capsys.readouterr().out
    store = json.loads((tmp_path / ".ciu" / "secrets" / "fluxer-secrets.json").read_text())
    accounts = json.loads((tmp_path / ".ciu" / "secrets" / "accounts.json").read_text())
    for value in [*store.values(), *accounts.values()]:
        assert value not in out


def test_preflight_rejects_bad_accounts_and_ports(secrets_hook) -> None:
    good = {"fluxer": _cfg()}
    assert secrets_hook.validate_config(good, None) == []
    bad = {"fluxer": _cfg()}
    bad["fluxer"]["accounts"] = {"admin": "a b", "users": ["a b"]}
    bad["fluxer"]["livekit"] = {"tcp_port": 80, "udp_port": "x"}
    findings = secrets_hook.validate_config(bad, None)
    assert any("unique" in f for f in findings) and any("tcp_port" in f for f in findings)
    assert any("udp_port" in f for f in findings)


def test_accounts_payload_requires_every_credential() -> None:
    hook = _load_hook("post_compose_accounts")
    root = _cfg()
    accounts = {"fx_admin": "x", "fx_alice": "y", "fx_bob": "z", "fx_carol": "w"}
    payload = hook.build_payload(root, accounts, {"setup_token": "t"})
    assert payload["admin"]["username"] == "fx_admin" and len(payload["users"]) == 3
    assert payload["registration_mode_after"] == "closed"
    with pytest.raises(hook.AccountsError):
        hook.build_payload(root, {"fx_admin": "x"}, {"setup_token": "t"})


def test_accounts_script_prints_codes_only() -> None:
    js = (PROJECT / "hooks" / "fluxer_accounts.mjs").read_text(encoding="utf-8")
    final = [ln for ln in js.splitlines() if "console.log" in ln]
    assert final == ["console.log(JSON.stringify(result));"]
    assert "console.error" not in js and "process.stderr" not in js
