from __future__ import annotations

import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import threading
import time

import pytest


PWMCP_ROOT = Path(__file__).resolve().parents[1]
GATEWAY = PWMCP_ROOT / "containers" / "pwmcp" / "run-server-gateway" / "index.js"
NODE = shutil.which("node")


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class _Upstream:
    def __init__(self, expected_connections: int) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.expected_connections = expected_connections
        self.requests: queue.Queue[bytes] = queue.Queue()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        served = 0
        while not self.stopping.is_set() and served < self.expected_connections:
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    request.extend(chunk)
                self.requests.put(bytes(request))
                conn.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\n"
                    b"Connection: Upgrade\r\n"
                    b"Upgrade: websocket\r\n\r\n"
                )
                served += 1

    def close(self) -> None:
        self.stopping.set()
        self.listener.close()
        self.thread.join(timeout=2)


def _start_gateway(allowed_hosts: str, upstream_port: int) -> tuple[subprocess.Popen[str], int]:
    public_port = _unused_port()
    admin_port = _unused_port()
    env = os.environ.copy()
    env.update(
        {
            "PWMCP_RUN_SERVER_PORT": str(public_port),
            "PWMCP_RUN_SERVER_UPSTREAM_PORT": str(upstream_port),
            "PWMCP_RUN_SERVER_ADMIN_PORT": str(admin_port),
            "PWMCP_RUN_SERVER_ALLOWED_HOSTS": allowed_hosts,
            "PWMCP_RUN_SERVER_DEFAULT_LEASE_S": "60",
            "PWMCP_RUN_SERVER_MAX_LEASE_S": "120",
            "PWMCP_RUN_SERVER_IDLE_RECYCLE_S": "3600",
        }
    )
    process = subprocess.Popen(
        [NODE or "node", str(GATEWAY)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise AssertionError(f"gateway exited before listening: {stderr}")
        try:
            with socket.create_connection(("127.0.0.1", public_port), timeout=0.1):
                return process, public_port
        except OSError:
            time.sleep(0.02)
    process.terminate()
    raise AssertionError("gateway did not listen within five seconds")


def _upgrade(public_port: int, host: str, origin: str) -> bytes:
    request = (
        "GET /ws HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Origin: {origin}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "X-Pwmcp-Session-Label: gateway-test\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", public_port), timeout=2) as client:
        client.settimeout(2)
        client.sendall(request)
        return client.recv(4096)


def _headers(raw_request: bytes) -> dict[str, str]:
    lines = raw_request.split(b"\r\n")
    result = {}
    for line in lines[1:]:
        if not line:
            break
        name, value = line.split(b":", 1)
        result[name.decode("ascii").lower()] = value.strip().decode("ascii")
    return result


@pytest.mark.usefixtures("require_node")
def test_run_server_gateway_rejects_unknown_hosts_before_upstream() -> None:
    upstream = _Upstream(expected_connections=0)
    process, public_port = _start_gateway("pwmcp:3000", upstream.port)
    try:
        response = _upgrade(public_port, "evil.example:3000", "http://evil.example:3000")
        assert response.startswith(b"HTTP/1.1 403 Forbidden\r\n")
        with pytest.raises(queue.Empty):
            upstream.requests.get(timeout=0.25)
    finally:
        process.terminate()
        process.wait(timeout=3)
        upstream.close()


@pytest.mark.usefixtures("require_node")
def test_run_server_gateway_allows_known_hosts_and_rewrites_upstream_headers() -> None:
    allowed_hosts = "pwmcp-mcp:3000,browser.example.test"
    upstream = _Upstream(expected_connections=2)
    process, public_port = _start_gateway(allowed_hosts, upstream.port)
    try:
        for host, origin in (
            ("pwmcp-mcp:3000", "http://pwmcp-mcp:3000"),
            ("browser.example.test", "https://browser.example.test"),
        ):
            response = _upgrade(public_port, host, origin)
            assert response.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
            forwarded = upstream.requests.get(timeout=2)
            headers = _headers(forwarded)
            assert headers["host"] == f"127.0.0.1:{upstream.port}"
            assert headers["origin"] == f"http://127.0.0.1:{upstream.port}"
            assert headers["x-pwmcp-session-label"] == "gateway-test"
    finally:
        process.terminate()
        process.wait(timeout=3)
        upstream.close()


def test_compose_builds_run_server_allowlist_from_ciuderived_names() -> None:
    compose = (PWMCP_ROOT / "ciu.compose.yml.j2").read_text(encoding="utf-8")

    assert "PWMCP_RUN_SERVER_ALLOWED_HOSTS:" in compose
    assert "{{ pwmcp.unified.name }}:{{ pwmcp.unified.ws_port }}" in compose
    assert "{{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ pwmcp.unified.name }}" in compose
    assert "{% for alias in pwmcp.consumer.aliases %}" in compose
    assert "{{ pwmcp.external.unified_host }}{% endif %}" in compose
