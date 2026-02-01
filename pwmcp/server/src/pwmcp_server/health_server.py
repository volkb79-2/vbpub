#!/usr/bin/env python3
"""
Minimal health HTTP server for external monitoring.
"""

import logging
import os
import socket
import urllib.error
import urllib.request
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pwmcp_shared.constants import (
    DEFAULT_HEALTH_PORT,
    DEFAULT_MCP_PORT,
    DEFAULT_WS_PORT,
    ENV_HEALTH_PORT,
    ENV_MCP_PORT,
    ENV_WS_PORT,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

HEALTH_PORT = int(os.getenv(ENV_HEALTH_PORT, str(DEFAULT_HEALTH_PORT)))
HEALTH_SOCKET_TIMEOUT = float(os.getenv('HEALTH_SOCKET_TIMEOUT', '2'))
WS_PORT = int(os.getenv(ENV_WS_PORT, str(DEFAULT_WS_PORT)))
MCP_PORT = int(os.getenv(ENV_MCP_PORT, str(DEFAULT_MCP_PORT)))
MCP_ENABLED = os.getenv('MCP_ENABLED', 'true').lower() in {'1', 'true', 'yes'}
SELFTEST_URL = os.getenv('SELFTEST_URL', 'https://www.google.de')
SELFTEST_TIMEOUT = float(os.getenv('SELFTEST_TIMEOUT', '5'))

app = FastAPI()


def _check_socket(port: int) -> dict:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=HEALTH_SOCKET_TIMEOUT):
            return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - defensive for any socket error
        return {"status": "failed", "error": str(exc)}


def _build_socket_checks() -> dict:
    checks = {
        "ws": _check_socket(WS_PORT),
    }

    if MCP_ENABLED:
        checks["mcp"] = _check_socket(MCP_PORT)
    else:
        checks["mcp"] = {"status": "skipped", "reason": "MCP disabled"}

    return checks


@app.get("/health")
async def health() -> JSONResponse:
    checks = _build_socket_checks()
    failures = [name for name, result in checks.items() if result.get("status") == "failed"]
    payload = {
        "status": "healthy" if not failures else "unhealthy",
        "service": "pwmcp-server",
        "checks": checks,
    }
    status_code = 200 if not failures else 503
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/ready")
async def ready() -> JSONResponse:
    checks = _build_socket_checks()
    failures = [name for name, result in checks.items() if result.get("status") == "failed"]
    payload = {
        "status": "ready" if not failures else "not-ready",
        "service": "pwmcp-server",
        "checks": checks,
    }
    status_code = 200 if not failures else 503
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/selftest")
async def selftest() -> JSONResponse:
    try:
        with urllib.request.urlopen(SELFTEST_URL, timeout=SELFTEST_TIMEOUT) as response:
            status_code = response.status
    except (urllib.error.URLError, TimeoutError) as exc:
        payload = {
            "status": "failed",
            "service": "pwmcp-server",
            "target": SELFTEST_URL,
            "error": str(exc),
        }
        return JSONResponse(content=payload, status_code=503)

    if status_code >= 400:
        payload = {
            "status": "failed",
            "service": "pwmcp-server",
            "target": SELFTEST_URL,
            "http_status": status_code,
        }
        return JSONResponse(content=payload, status_code=503)

    payload = {
        "status": "ok",
        "service": "pwmcp-server",
        "target": SELFTEST_URL,
        "http_status": status_code,
    }
    return JSONResponse(content=payload, status_code=200)


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting health server on 0.0.0.0:%s", HEALTH_PORT)
    uvicorn.run(app, host="0.0.0.0", port=HEALTH_PORT, log_level="info")
