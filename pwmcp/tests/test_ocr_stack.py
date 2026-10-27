"""PWMCP-11 phase 1: ciu/compose wiring and the gateway /contract advertisement, offline.

Renders the real ciu.compose.yml.j2 with the real ciu.defaults.toml.j2 values (jinja2 and
PyYAML required; skipped with an explicit reason otherwise). What this does NOT prove is that
ciu itself accepts the result: that needs a real `ciu up` (see KNOWN_ISSUES PWMCP-11).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import time
import tomllib
import urllib.request
from pathlib import Path

import pytest
from _testdeps import optional_module

PWMCP_ROOT = Path(__file__).resolve().parents[1]
GATEWAY = PWMCP_ROOT / "containers" / "pwmcp" / "run-server-gateway" / "index.js"
NODE = shutil.which("node")

jinja2 = optional_module("jinja2", "jinja2 is needed to render the compose template")
yaml = optional_module("yaml", "PyYAML is needed to parse the rendered compose file")


def _defaults() -> dict:
    # The defaults file has no Jinja constructs of its own: it is plain TOML.
    return tomllib.loads((PWMCP_ROOT / "ciu.defaults.toml.j2").read_text(encoding="utf-8"))


GOLDEN = PWMCP_ROOT / "tests" / "golden" / "ciu.compose.yml.j2.85f5fa992"  # git show 85f5fa992:pwmcp/ciu.compose.yml.j2


def _render(mutate=None, *, strip_ocr: bool = False, template_path: Path | None = None, **env_options: bool) -> str:
    context = copy.deepcopy(_defaults())
    if strip_ocr:
        del context["pwmcp"]["ocr"]  # a consumer whose defaults predate the feature
    if mutate is not None:
        mutate(context["pwmcp"])
    deploy = {"project_name": "proj", "environment_tag": "t1", "network_name": "proj-net"}
    template = (template_path or PWMCP_ROOT / "ciu.compose.yml.j2").read_text(encoding="utf-8")
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True, **env_options)  # as ciu does
    return env.from_string(template).render(pwmcp=context["pwmcp"], deploy=deploy)


def _enable(pwmcp: dict) -> None:
    pwmcp["ocr"]["enabled"] = True


def test_defaults_ship_ocr_disabled_with_the_documented_keys() -> None:
    ocr = _defaults()["pwmcp"]["ocr"]
    assert ocr["enabled"] is False
    assert (ocr["name"], ocr["port"], ocr["engines"]) == ("pwmcp-ocr", 8941, "tesseract")
    assert (ocr["max_bytes"], ocr["max_dim"], ocr["timeout_s"]) == (8 * 1024 * 1024, 4096, 20)
    assert (ocr["mem_limit"], ocr["cpus"], ocr["pids_limit"]) == ("512m", "1.0", 128)
    assert ocr["image"]["name"] == "pwmcp-ocr"
    # the port must not collide with any other port the stack declares
    pwmcp = _defaults()["pwmcp"]
    others = [pwmcp["unified"][k] for k in ("ws_port", "mcp_port", "devtools_port", "lighthouse_port", "admin_port")]
    others += [pwmcp["run_server"]["admin_port"], pwmcp["run_server"]["upstream_port"]]
    assert ocr["port"] not in others


@pytest.mark.parametrize("env_options", [{}, {"trim_blocks": True}, {"trim_blocks": True, "lstrip_blocks": True}],
                         ids=["ciu-env", "trim_blocks", "trim+lstrip"])
def test_default_render_is_byte_identical_to_the_pre_ocr_template(env_options: dict) -> None:
    """The DEFAULT render (ocr disabled or absent) equals the render of the template at 85f5fa992.

    This is deliberately strict: it proves the OCR feature changed NOTHING for existing deployments.
    It also means ANY later edit to ciu.compose.yml.j2 (OCR-related or not) fails it, on purpose, so
    that a change to what every deployment renders is a conscious one. To make such an edit:
      1. make the edit and review `git diff` of the default render by hand;
      2. regenerate the golden from the PREVIOUS reviewed template, i.e. run
         `git show HEAD:pwmcp/ciu.compose.yml.j2 > pwmcp/tests/golden/ciu.compose.yml.j2.<short-hash>`
         (the template as committed BEFORE your edit, OCR blocks stripped if present) and point GOLDEN at it;
      3. say in the commit message that the default render changed and why.
    """

    old = _render(template_path=GOLDEN, strip_ocr=True, **env_options)
    assert _render(**env_options).encode() == old.encode(), (
        "the default render of ciu.compose.yml.j2 changed. If that is intended, follow the 'regenerate the "
        "golden deliberately' procedure in this test's docstring; if not, the template edit leaks into "
        "deployments that never enabled OCR."
    )
    assert _render(strip_ocr=True, **env_options).encode() == old.encode()
    assert _render(lambda p: p["ocr"].update(enabled=False), **env_options).encode() == old.encode()
    # also with external mode on, where the template has the most conditional blocks
    def external(pwmcp: dict) -> None:
        pwmcp["external"].update(enabled=True, unified_host="pw.example.test", guard_enabled=True,
                                 guard_htpasswd="$x", guard_user="u")
        pwmcp["unified"]["expose"] = True

    assert _render(external, **env_options).encode() == _render(external, template_path=GOLDEN, strip_ocr=True,
                                                                **env_options).encode()


def test_golden_copy_is_the_old_template_without_any_ocr_text() -> None:
    assert "ocr" not in GOLDEN.read_text(encoding="utf-8").lower()


def test_default_render_is_unchanged_and_has_only_the_pwmcp_service() -> None:
    rendered = _render()
    compose = yaml.safe_load(rendered)
    assert list(compose["services"]) == ["pwmcp"]
    assert "PWMCP_OCR" not in rendered and "pwmcp-ocr" not in rendered and "tesseract" not in rendered
    # byte-identical to a consumer whose defaults know nothing about OCR
    assert rendered == _render(strip_ocr=True)


def test_disabled_explicitly_renders_nothing_new() -> None:
    assert _render(lambda p: p["ocr"].update(enabled=False)) == _render(strip_ocr=True)


def test_enabled_renders_the_sidecar_service_on_the_stack_network_only() -> None:
    compose = yaml.safe_load(_render(_enable))
    assert sorted(compose["services"]) == ["pwmcp", "pwmcp-ocr"]
    ocr = compose["services"]["pwmcp-ocr"]
    assert ocr["image"] == "ghcr.io/volkb79-2/pwmcp-ocr:1.64.0-r3"
    assert ocr["container_name"] == "proj-t1-pwmcp-ocr"
    assert ocr["networks"] == {"default": {"aliases": ["pwmcp-ocr"]}}
    # never published, never routed
    assert "ports" not in ocr and "labels" not in ocr and "expose" not in ocr
    # hardening and limits
    assert ocr["user"] == "1000:1000" and ocr["cap_drop"] == ["ALL"]
    assert ocr["security_opt"] == ["no-new-privileges:true"]
    assert ocr["read_only"] is True and ocr["tmpfs"] == ["/tmp:size=64m,mode=1777"]
    assert (ocr["mem_limit"], ocr["memswap_limit"], ocr["cpus"], ocr["pids_limit"]) == ("512m", "512m", 1.0, 128)
    assert ocr["restart"] == "unless-stopped"
    assert ocr["environment"] == {"OCR_PORT": "8941", "OCR_MAX_BYTES": "8388608", "OCR_MAX_DIM": "4096",
                                  "OCR_TIMEOUT_S": "20",
                                  "OCR_MAX_SCALED_PIXELS": "24000000", "OCR_BODY_DEADLINE_S": "10"}
    assert "cgroup_parent" not in ocr  # the ciu governance overlay owns it (S15.3)
    assert "/health" in ocr["healthcheck"]["test"][-1] and "OCR_PORT" in ocr["healthcheck"]["test"][-1]
    assert ocr["init"] is True  # PID 1 must reap and forward SIGTERM
    assert compose["networks"]["default"]["name"] == "proj-net"


def test_enabled_exports_the_url_to_the_pwmcp_container_only() -> None:
    compose = yaml.safe_load(_render(_enable))
    env = compose["services"]["pwmcp"]["environment"]
    assert env["PWMCP_OCR_URL"] == "http://pwmcp-ocr:8941" and env["PWMCP_OCR_ENGINES"] == "tesseract"
    assert "PWMCP_OCR_URL" not in compose["services"]["pwmcp-ocr"]["environment"]


def test_hostile_values_stay_valid_yaml_in_the_env_and_the_service_names() -> None:
    def tweak(pwmcp: dict) -> None:
        pwmcp["ocr"].update(enabled=True, engines='tesseract, "x": {y}\n- z $HOME', name="o: cr #1")

    compose = yaml.safe_load(_render(tweak))
    env = compose["services"]["pwmcp"]["environment"]
    assert env["PWMCP_OCR_ENGINES"].replace("$$", "$") == 'tesseract, "x": {y}\n- z $HOME'
    assert env["PWMCP_OCR_URL"] == "http://o: cr #1:8941"
    ocr = compose["services"]["o: cr #1"]
    assert ocr["container_name"] == "proj-t1-o: cr #1" and ocr["networks"]["default"]["aliases"] == ["o: cr #1"]


def test_custom_name_port_and_limits_flow_through() -> None:
    def tweak(pwmcp: dict) -> None:
        pwmcp["ocr"].update(enabled=True, name="ocr2", port=9001, mem_limit="256m", timeout_s=7)

    compose = yaml.safe_load(_render(tweak))
    assert compose["services"]["ocr2"]["networks"]["default"]["aliases"] == ["ocr2"]
    assert compose["services"]["ocr2"]["mem_limit"] == "256m"
    assert compose["services"]["ocr2"]["environment"]["OCR_TIMEOUT_S"] == "7"
    assert compose["services"]["pwmcp"]["environment"]["PWMCP_OCR_URL"] == "http://ocr2:9001"


def test_external_mode_never_routes_or_attaches_the_sidecar() -> None:
    def tweak(pwmcp: dict) -> None:
        pwmcp["ocr"]["enabled"] = True
        pwmcp["external"].update(enabled=True, unified_host="pw.example.test", guard_enabled=False)
        pwmcp["unified"]["expose"] = True

    ocr = yaml.safe_load(_render(tweak))["services"]["pwmcp-ocr"]
    assert "ports" not in ocr and "labels" not in ocr and list(ocr["networks"]) == ["default"]


# ------------------------------------------------------------------ gateway /contract


def _gateway_contract(extra_env: dict[str, str]) -> dict:
    def free() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    public = free()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PWMCP_")}
    env.update({
        "PWMCP_RUN_SERVER_PORT": str(public),
        "PWMCP_RUN_SERVER_UPSTREAM_PORT": str(free()),
        "PWMCP_RUN_SERVER_ADMIN_PORT": str(free()),
        "PWMCP_CONTRACT_PATH": str(PWMCP_ROOT / "pwmcp.contract.json"),
        **extra_env,
    })
    process = subprocess.Popen([NODE or "node", str(GATEWAY)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{public}/contract", timeout=1) as response:
                    return json.load(response)
            except OSError:
                time.sleep(0.05)
        raise AssertionError("gateway did not serve /contract within five seconds")
    finally:
        process.terminate()
        process.wait(timeout=3)


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the gateway")
def test_gateway_contract_advertises_no_ocr_by_default() -> None:
    assert _gateway_contract({})["ocr"] == {"url": "", "engines": []}


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the gateway")
def test_gateway_contract_advertises_the_sidecar_when_configured() -> None:
    contract = _gateway_contract({"PWMCP_OCR_URL": "http://pwmcp-ocr:8941", "PWMCP_OCR_ENGINES": "tesseract, glyphgrid,"})
    assert contract["ocr"] == {"url": "http://pwmcp-ocr:8941", "engines": ["tesseract", "glyphgrid"]}
    assert contract["schema_version"] == 1 and "user_agent" in contract  # the rest is untouched
