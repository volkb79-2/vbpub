"""PWMCP-09 server side without a container: wrapper, contract, admin health, ua-override."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from _testdeps import optional_module

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


class _FakeCdpHttp(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"Browser": "Chrome/156"} if self.path == "/json/version" else []).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return None


@pytest.mark.skipif(NODE is None, reason="Node.js is needed")
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, (None, "default")),
        ({"PWMCP_USER_AGENT_RESOLVED": EDGE, "PWMCP_USER_AGENT_SOURCE": "preset:edge-windows"},
         (EDGE, "preset:edge-windows")),
    ],
)
def test_admin_health_reports_the_override(env: dict[str, str], expected: tuple[str | None, str]) -> None:
    cdp = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeCdpHttp)
    threading.Thread(target=cdp.serve_forever, daemon=True).start()
    admin_port = _unused_port()
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("PWMCP_")}
    full_env.update({"PWMCP_ADMIN_PORT": str(admin_port), "PWMCP_CDP_PORT": str(cdp.server_address[1]), **env})
    process = subprocess.Popen([NODE or "node", str(CONTAINER / "admin-server" / "index.js")],
                               env=full_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{admin_port}/browser/health", timeout=1) as response:
                    health = json.load(response)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
    finally:
        process.terminate()
        process.wait(timeout=3)
        cdp.shutdown()
    assert health["cdpAlive"] is True
    assert (health["userAgent"], health["userAgentSource"]) == expected


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


def _entrypoint_snippet() -> str:
    text = (CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")
    start = text.index("PWMCP_UA_HELPER=")
    end = text.index("for name in PWMCP_RUN_SERVER_PORT")
    return text[start:end]


def _run_snippet(tmp_path: Path, user_agent: str | None, *, mode: str = "per-session",
                 real_python: bool = False) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Execute the entrypoint's real override block under `sh -e` with python3 stubbed or real."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "python-called"
    python = bin_dir / "python3"
    if real_python:
        python.symlink_to(sys.executable)
    else:
        python.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
    base_conf = tmp_path / "shared.conf"
    base_conf.write_text("[program:chromium]\n", encoding="utf-8")
    part = tmp_path / "part"
    part.write_text("[program:ua-override]\n", encoding="utf-8")
    generated = tmp_path / "generated.conf"
    snippet = (_entrypoint_snippet()
               .replace("/etc/supervisor/conf.d/pwmcp-ua-override.part", str(part))
               .replace("/tmp/pwmcp-shared-ua.conf", str(generated)))
    script = (
        f"PWMCP_BROWSER_MODE={mode}\nSUPERVISOR_CONF={base_conf}\n{snippet}\n"
        'printf "src=%s\\nua=%s\\nconf=%s\\n" "$PWMCP_USER_AGENT_SOURCE" "$PWMCP_USER_AGENT_RESOLVED" "$SUPERVISOR_CONF"\n'
    )
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin",
           "PWMCP_UA_HELPER": str(PWMCP_ROOT / "client" / "src" / "pwmcp_client" / "user_agent.py")}
    if user_agent is not None:
        env["PWMCP_USER_AGENT"] = user_agent
    result = subprocess.run([SH or "sh", "-e", "-c", script], env=env, capture_output=True, text=True)
    return result, marker, generated


@pytest.mark.skipif(SH is None, reason="sh is needed")
@pytest.mark.parametrize("value", [None, "", "default"])
def test_entrypoint_off_by_default_never_calls_python_and_keeps_the_config(tmp_path: Path, value: str | None) -> None:
    result, marker, generated = _run_snippet(tmp_path, value, mode="shared")
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert "src=default" in result.stdout and "ua=\n" in result.stdout
    assert f"conf={tmp_path / 'shared.conf'}" in result.stdout and not generated.exists()
    assert "user agent: default (no override)" in result.stderr


@pytest.mark.skipif(SH is None, reason="sh is needed")
def test_entrypoint_invalid_user_agent_is_fatal(tmp_path: Path) -> None:
    result, _marker, _generated = _run_snippet(tmp_path, "edge-window", real_python=True)
    assert result.returncode != 0
    assert "FATAL: invalid PWMCP_USER_AGENT" in result.stderr
    assert "src=" not in result.stdout  # never got as far as exporting a value


@pytest.mark.skipif(SH is None, reason="sh is needed")
def test_entrypoint_override_resolves_logs_and_adds_ua_override_only_in_shared_mode(tmp_path: Path) -> None:
    shared, _m, generated = _run_snippet(tmp_path / "a", "edge-windows", mode="shared", real_python=True)
    assert shared.returncode == 0, shared.stderr
    assert f"ua={EDGE}" in shared.stdout and "src=preset:edge-windows" in shared.stdout
    assert "user agent OVERRIDE active: source=preset:edge-windows" in shared.stderr
    assert f"conf={generated}" in shared.stdout
    assert generated.read_text(encoding="utf-8") == "[program:chromium]\n[program:ua-override]\n"
    per_session, _m2, generated2 = _run_snippet(tmp_path / "b", "edge-windows", real_python=True)
    assert per_session.returncode == 0, per_session.stderr
    assert not generated2.exists() and f"conf={tmp_path / 'b' / 'shared.conf'}" in per_session.stdout


def test_shipped_shared_config_has_no_ua_override_program_but_the_fragment_does() -> None:
    assert "[program:ua-override]" not in (CONTAINER / "supervisord.shared.conf").read_text(encoding="utf-8")
    part = (CONTAINER / "supervisord.ua-override.part").read_text(encoding="utf-8")
    assert "[program:ua-override]" in part and "/opt/pwmcp/ua-override/index.js" in part


def test_ua_override_sends_metadata_for_existing_and_newly_attached_targets() -> None:
    websockets = optional_module("websockets", "the websockets package is needed to drive the ua-override CDP client")
    if NODE is None:
        pytest.skip("Node.js is needed")
    import asyncio

    seen: list[dict[str, object]] = []
    ws_port = _unused_port()

    async def handler(ws: object) -> None:
        async for raw in ws:  # type: ignore[attr-defined]
            msg = json.loads(raw)
            seen.append(msg)
            method = msg.get("method")
            if method == "Target.getTargets":
                await ws.send(json.dumps({"id": msg["id"], "result": {"targetInfos": [  # type: ignore[attr-defined]
                    {"targetId": "T-existing", "type": "page"}, {"targetId": "T-worker", "type": "service_worker"}]}}))
            elif method == "Target.attachToTarget":
                await ws.send(json.dumps({"method": "Target.attachedToTarget", "params": {  # type: ignore[attr-defined]
                    "sessionId": "S-" + msg["params"]["targetId"],
                    "targetInfo": {"targetId": msg["params"]["targetId"], "type": "page"}}}))
                # A page opened later arrives through auto-attach.
                await ws.send(json.dumps({"method": "Target.attachedToTarget", "params": {  # type: ignore[attr-defined]
                    "sessionId": "S-new", "targetInfo": {"targetId": "T-new", "type": "page"}}}))

    class _Http(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"webSocketDebuggerUrl": f"ws://127.0.0.1:{ws_port}/devtools/browser/x"}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return None

    port = _unused_port()
    http_server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Http)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    started = threading.Event()
    stop = threading.Event()

    def run_ws() -> None:
        async def main() -> None:
            async with websockets.serve(handler, "127.0.0.1", ws_port):
                started.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)
        asyncio.run(main())

    threading.Thread(target=run_ws, daemon=True).start()
    assert started.wait(5)
    metadata = json.dumps({"brands": [{"brand": "Microsoft Edge", "version": "155"}]})
    env = {k: v for k, v in os.environ.items() if not k.startswith("PWMCP_")}
    env.update({"PWMCP_CDP_PORT": str(port), "PWMCP_USER_AGENT_RESOLVED": EDGE,
                "PWMCP_USER_AGENT_SOURCE": "preset:edge-windows", "PWMCP_USER_AGENT_METADATA": metadata})
    process = subprocess.Popen([NODE, str(UA_OVERRIDE)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def overrides() -> dict[str, dict[str, object]]:
        return {m["sessionId"]: m for m in list(seen) if m.get("method") == "Emulation.setUserAgentOverride"}

    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(overrides()) < 2:
            time.sleep(0.05)
        got = overrides()
    finally:
        process.terminate()
        process.wait(timeout=3)
        http_server.shutdown()
        stop.set()
    assert set(got) == {"S-T-existing", "S-new"}  # existing page and newly attached page; not the worker
    for message in got.values():
        assert message["params"] == {"userAgent": EDGE, "userAgentMetadata": json.loads(metadata)}
    assert any(m.get("method") == "Target.setAutoAttach" and m["params"]["waitForDebuggerOnStart"] for m in seen)
    time.sleep(0.2)
    resumed = {m["sessionId"] for m in list(seen) if m.get("method") == "Runtime.runIfWaitingForDebugger"}
    assert {"S-T-existing", "S-new"} <= resumed  # attached targets are never left paused


def test_the_dockerfile_part_destination_is_the_one_the_entrypoint_reads_and_supervisord_uses_the_chosen_conf() -> None:
    dockerfile = (CONTAINER / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")
    destination = "/etc/supervisor/conf.d/pwmcp-ua-override.part"
    assert f"supervisord.ua-override.part {destination}" in dockerfile
    assert destination in entrypoint
    assert 'exec supervisord -c "$SUPERVISOR_CONF"' in entrypoint
