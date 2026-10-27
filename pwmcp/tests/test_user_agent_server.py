"""PWMCP-09 server side without a container: wrapper, contract, admin health, ua-override."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

PWMCP_ROOT = Path(__file__).resolve().parents[1]
CONTAINER = PWMCP_ROOT / "containers" / "pwmcp"
WRAPPER = CONTAINER / "pwmcp-ua-args.sh"
GATEWAY = CONTAINER / "run-server-gateway" / "index.js"
UA_OVERRIDE = CONTAINER / "ua-override" / "index.js"
NODE = shutil.which("node")
SH = shutil.which("sh")

EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"


def _wrap(prefix: str, env: dict[str, str]) -> list[str]:
    """Run the wrapper around `printf '%s\\n'` so each argv element prints on its own line."""

    full_env = {k: v for k, v in os.environ.items() if k != "PWMCP_USER_AGENT_RESOLVED"}
    full_env.update(env)
    result = subprocess.run(
        [SH or "sh", str(WRAPPER), prefix, "--", "printf", "%s\n", "base-arg"],
        env=full_env, capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


@pytest.mark.skipif(SH is None, reason="sh is needed")
def test_wrapper_is_a_no_op_without_an_override() -> None:
    assert _wrap("--user-agent=", {}) == ["base-arg"]
    assert _wrap("--user-agent=", {"PWMCP_USER_AGENT_RESOLVED": ""}) == ["base-arg"]


@pytest.mark.skipif(SH is None, reason="sh is needed")
def test_wrapper_appends_the_ua_as_one_argv_element() -> None:
    assert _wrap("--user-agent=", {"PWMCP_USER_AGENT_RESOLVED": EDGE}) == [
        "base-arg", f"--user-agent={EDGE}",
    ]
    assert _wrap("--chrome-arg=--user-agent=", {"PWMCP_USER_AGENT_RESOLVED": "a b (c)"}) == [
        "base-arg", "--chrome-arg=--user-agent=a b (c)",
    ]


@pytest.mark.skipif(SH is None, reason="sh is needed")
def test_wrapper_rejects_a_malformed_invocation() -> None:
    result = subprocess.run([SH or "sh", str(WRAPPER), "--user-agent=", "x"], capture_output=True, text=True)
    assert result.returncode == 2


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _gateway_contract(extra_env: dict[str, str]) -> dict[str, object]:
    public = _unused_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PWMCP_")}
    env.update({
        "PWMCP_RUN_SERVER_PORT": str(public),
        "PWMCP_RUN_SERVER_UPSTREAM_PORT": str(_unused_port()),
        "PWMCP_RUN_SERVER_ADMIN_PORT": str(_unused_port()),
        "PWMCP_CONTRACT_PATH": str(PWMCP_ROOT / "pwmcp.contract.json"),
        **extra_env,
    })
    process = subprocess.Popen([NODE or "node", str(GATEWAY)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


@pytest.mark.skipif(NODE is None, reason="Node.js is needed")
def test_gateway_contract_publishes_the_default_when_unset() -> None:
    assert _gateway_contract({})["user_agent"] == {"value": None, "source": "default"}


@pytest.mark.skipif(NODE is None, reason="Node.js is needed")
def test_gateway_contract_publishes_the_active_override_and_its_source() -> None:
    contract = _gateway_contract({
        "PWMCP_USER_AGENT_RESOLVED": EDGE,
        "PWMCP_USER_AGENT_SOURCE": "preset:edge-windows",
    })
    assert contract["user_agent"] == {"value": EDGE, "source": "preset:edge-windows"}
    assert contract["schema_version"] == 1  # the static contract is otherwise unchanged


def test_admin_health_reports_the_override() -> None:
    source = (CONTAINER / "admin-server" / "index.js").read_text(encoding="utf-8")
    assert "userAgent: process.env.PWMCP_USER_AGENT_RESOLVED || null" in source
    assert "userAgentSource: process.env.PWMCP_USER_AGENT_SOURCE || 'default'" in source


@pytest.mark.skipif(NODE is None, reason="Node.js is needed")
def test_ua_override_without_an_override_exits_cleanly_and_says_so() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PWMCP_")}
    result = subprocess.run([NODE or "node", str(UA_OVERRIDE)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "no user-agent override configured" in result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is needed")
def test_ua_override_builds_cdp_params_with_matching_metadata() -> None:
    script = (
        f"const {{buildParams}} = require({json.dumps(str(UA_OVERRIDE))});"
        "process.stdout.write(JSON.stringify([buildParams('ua', ''), buildParams('ua', '{\"brands\":[]}')]));"
    )
    out = subprocess.run([NODE or "node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [{"userAgent": "ua"}, {"userAgent": "ua", "userAgentMetadata": {"brands": []}}]


def test_entrypoint_is_off_by_default_and_validates_through_the_helper() -> None:
    entrypoint = (CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")
    assert "''|default)" in entrypoint  # unset/default never invokes python
    assert 'python3 "$PWMCP_UA_HELPER" ua "$PWMCP_USER_AGENT"' in entrypoint
    assert "pwmcp: user agent OVERRIDE active" in entrypoint  # logged once at start
    assert "export PWMCP_USER_AGENT_SOURCE PWMCP_USER_AGENT_RESOLVED PWMCP_USER_AGENT_METADATA" in entrypoint
