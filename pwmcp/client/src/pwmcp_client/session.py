from __future__ import annotations

import importlib.metadata
import os
import socket
import urllib.parse
from dataclasses import dataclass

from .contract import PwmcpContract, load_contract


class PwmcpError(RuntimeError):
    pass


class PwmcpUnavailable(PwmcpError):
    pass


class VersionMismatch(PwmcpError):
    pass


def _major_minor(version: str) -> tuple[int, int]:
    pieces = version.split(".")
    if len(pieces) < 2:
        raise VersionMismatch(f"invalid Playwright version: {version}")
    return int(pieces[0]), int(pieces[1])


def verify_installed_playwright(contract: PwmcpContract) -> str:
    try:
        installed = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError as exc:
        raise VersionMismatch("the Playwright Python package is not installed") from exc
    if _major_minor(installed) != _major_minor(contract.playwright_version):
        raise VersionMismatch(
            f"Playwright client {installed} does not match pwmcp protocol {contract.protocol_version}"
        )
    return installed


def _tcp_preflight(ws_url: str, timeout: float) -> None:
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        raise PwmcpUnavailable(f"invalid pwmcp WebSocket URL: {ws_url}")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise PwmcpUnavailable(f"pwmcp is unreachable at {host}:{port}") from exc


@dataclass
class BrowserLease:
    browser: object
    playwright: object
    contract: PwmcpContract

    @classmethod
    def connect(
        cls,
        *,
        ws_url: str | None = None,
        contract_url: str | None = None,
        lease_seconds: int | None = None,
        label: str | None = None,
        timeout_ms: float = 30_000,
    ) -> "BrowserLease":
        contract_url = contract_url or os.getenv("PWMCP_CONTRACT_URL", "http://pwmcp:3000/contract")
        contract = load_contract(contract_url, timeout=timeout_ms / 1000)
        verify_installed_playwright(contract)
        endpoint = ws_url or os.getenv("DSTDNS_PWMCP_WS") or contract.ws_url
        _tcp_preflight(endpoint, timeout_ms / 1000)
        requested = lease_seconds or int(os.getenv("PWMCP_LEASE_SECONDS", contract.default_lease_seconds))
        headers = {
            "X-PWMCP-Lease-Seconds": str(requested),
            "X-PWMCP-Session-Label": label or os.getenv("PWMCP_SESSION_LABEL", "python-client"),
        }
        from playwright.sync_api import sync_playwright  # imported only in the test runtime

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect(endpoint, headers=headers, timeout=timeout_ms)
        except Exception:
            playwright.stop()
            raise
        return cls(browser=browser, playwright=playwright, contract=contract)

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self.playwright.stop()

    def __enter__(self) -> "BrowserLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
