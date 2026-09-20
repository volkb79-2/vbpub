from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from urllib.error import URLError

import pytest

from pwmcp_client import PwmcpContract
from pwmcp_client import cli as cli_module
from pwmcp_client import contract as contract_module
from pwmcp_client import session
from pwmcp_client.diagnostics import PwmcpArgumentParser, cli_headline


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "1.63.0-r2",
        "playwright": {"python": "1.63.0", "protocol": "1.63"},
        "endpoints": {
            "playwright_ws": "ws://pwmcp:3000/",
            "health": "http://pwmcp:3000/health",
        },
        "run_server": {
            "limits": {
                "default_lease_seconds": 10,
                "max_lease_seconds": 20,
                "max_clients": 2,
                "idle_recycle_seconds": 30,
            }
        },
    }


def _contract() -> PwmcpContract:
    return PwmcpContract.from_dict(_payload())


def test_contract_loads_over_http_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps(_payload()).encode()

    def fake_urlopen(request: object, timeout: float) -> Response:
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(contract_module.urllib.request, "urlopen", fake_urlopen)
    contract = contract_module.load_contract("https://example.test/contract", timeout=2.5)
    assert contract.release == "1.63.0-r2"
    assert calls == [("https://example.test/contract", 2.5)]


def test_contract_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "contract.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        contract_module.load_contract(path)


def test_contract_from_dict_converts_scalar_fields() -> None:
    value = _payload()
    value["schema_version"] = "1"
    value["run_server"] = {"limits": {
        "default_lease_seconds": "10",
        "max_lease_seconds": "20",
        "max_clients": "2",
        "idle_recycle_seconds": "30",
    }}
    contract = PwmcpContract.from_dict(value)
    assert contract.schema_version == 1
    assert contract.max_clients == 2


def test_cli_contract_prints_normalized_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    cli_module.main(["contract", "--contract", str(path)])
    output = json.loads(capsys.readouterr().out)
    assert output["release"] == "1.63.0-r2"
    assert output["ws_url"] == "ws://pwmcp:3000/"


def test_cli_doctor_reports_verified_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = _contract()
    monkeypatch.setattr(cli_module, "load_contract", lambda source: contract)
    monkeypatch.setattr(cli_module, "verify_installed_playwright", lambda value: "1.63.4")
    cli_module.main(["doctor", "--contract", "ignored"])
    assert "ok release=1.63.0-r2 playwright=1.63.4" in capsys.readouterr().out


def test_parser_help_and_usage_both_start_with_headline(capsys: pytest.CaptureFixture[str]) -> None:
    parser = PwmcpArgumentParser(prog="pwmcp")
    parser.add_argument("command")
    assert parser.format_help().startswith(cli_headline())
    assert parser.format_usage().startswith(cli_headline())
    with pytest.raises(SystemExit):
        parser.error("bad command")
    assert capsys.readouterr().err.splitlines()[0] == cli_headline()


def test_verify_playwright_rejects_missing_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise session.importlib.metadata.PackageNotFoundError("playwright")

    monkeypatch.setattr(session.importlib.metadata, "version", missing)
    with pytest.raises(session.VersionMismatch, match="not installed"):
        session.verify_installed_playwright(_contract())


@pytest.mark.parametrize("value", ["1", "invalid.63"])
def test_major_minor_rejects_malformed_version(value: str) -> None:
    with pytest.raises((session.VersionMismatch, ValueError)):
        session._major_minor(value)


def test_verify_playwright_rejects_protocol_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session.importlib.metadata, "version", lambda _name: "1.62.9")
    with pytest.raises(session.VersionMismatch, match="does not match"):
        session.verify_installed_playwright(_contract())


def test_verify_playwright_accepts_patch_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session.importlib.metadata, "version", lambda _name: "1.63.4")
    assert session.verify_installed_playwright(_contract()) == "1.63.4"


def test_tcp_preflight_rejects_invalid_url() -> None:
    with pytest.raises(session.PwmcpUnavailable, match="invalid"):
        session._tcp_preflight("not-a-websocket-url", 1.0)


def test_tcp_preflight_wraps_socket_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("refused")

    monkeypatch.setattr(session.socket, "create_connection", fail)
    with pytest.raises(session.PwmcpUnavailable, match="unreachable at pwmcp:3000"):
        session._tcp_preflight("ws://pwmcp:3000/", 1.0)


def test_tcp_preflight_closes_successful_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            closed.append(True)

    monkeypatch.setattr(session.socket, "create_connection", lambda *args, **kwargs: Connection())
    session._tcp_preflight("ws://pwmcp:3000/", 1.0)
    assert closed == [True]


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_error: Exception | None = None,
) -> tuple[list[dict[str, object]], list[bool], list[bool]]:
    connect_calls: list[dict[str, object]] = []
    stopped: list[bool] = []
    closed: list[bool] = []

    class Browser:
        def close(self) -> None:
            closed.append(True)

    class Chromium:
        def connect(self, endpoint: str, *, headers: dict[str, str], timeout: float) -> Browser:
            connect_calls.append({"endpoint": endpoint, "headers": headers, "timeout": timeout})
            if connect_error:
                raise connect_error
            return Browser()

    class Playwright:
        chromium = Chromium()

        def stop(self) -> None:
            stopped.append(True)

    class SyncPlaywright:
        def start(self) -> Playwright:
            return Playwright()

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SyncPlaywright()
    playwright = types.ModuleType("playwright")
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return connect_calls, stopped, closed


def test_browser_lease_connect_builds_headers_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    monkeypatch.setattr(session, "load_contract", lambda source, timeout: contract)
    monkeypatch.setattr(session, "verify_installed_playwright", lambda value: "1.63.4")
    preflight: list[tuple[str, float]] = []
    monkeypatch.setattr(session, "_tcp_preflight", lambda endpoint, timeout: preflight.append((endpoint, timeout)))
    monkeypatch.setenv("PWMCP_LEASE_SECONDS", "15")
    monkeypatch.setenv("PWMCP_SESSION_LABEL", "from-env")
    calls, stopped, closed = _install_fake_playwright(monkeypatch)

    lease = session.BrowserLease.connect(
        ws_url="ws://override:3010/",
        contract_url="ignored",
        timeout_ms=2500,
    )
    assert preflight == [("ws://override:3010/", 2.5)]
    assert calls == [{
        "endpoint": "ws://override:3010/",
        "headers": {"X-PWMCP-Lease-Seconds": "15", "X-PWMCP-Session-Label": "from-env"},
        "timeout": 2500,
    }]
    with lease as same:
        assert same is lease
    assert closed == [True]
    assert stopped == [True]


def test_browser_lease_connect_uses_explicit_lease_and_label(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = _contract()
    monkeypatch.setattr(session, "load_contract", lambda source, timeout: contract)
    monkeypatch.setattr(session, "verify_installed_playwright", lambda value: "1.63.4")
    monkeypatch.setattr(session, "_tcp_preflight", lambda endpoint, timeout: None)
    calls, _stopped, _closed = _install_fake_playwright(monkeypatch)
    session.BrowserLease.connect(
        contract_url="ignored",
        lease_seconds=17,
        label="explicit",
        timeout_ms=1000,
    )
    assert calls[0]["endpoint"] == contract.ws_url
    assert calls[0]["headers"] == {
        "X-PWMCP-Lease-Seconds": "17",
        "X-PWMCP-Session-Label": "explicit",
    }


def test_browser_lease_connect_stops_playwright_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    monkeypatch.setattr(session, "load_contract", lambda source, timeout: contract)
    monkeypatch.setattr(session, "verify_installed_playwright", lambda value: "1.63.4")
    monkeypatch.setattr(session, "_tcp_preflight", lambda endpoint, timeout: None)
    _calls, stopped, _closed = _install_fake_playwright(monkeypatch, connect_error=RuntimeError("bad ws"))
    with pytest.raises(RuntimeError, match="bad ws"):
        session.BrowserLease.connect(contract_url="ignored")
    assert stopped == [True]


def test_browser_lease_close_stops_playwright_when_browser_close_fails() -> None:
    stopped: list[bool] = []

    class Browser:
        def close(self) -> None:
            raise RuntimeError("browser close failed")

    class Playwright:
        def stop(self) -> None:
            stopped.append(True)

    lease = session.BrowserLease(Browser(), Playwright(), _contract())
    with pytest.raises(RuntimeError, match="browser close failed"):
        lease.close()
    assert stopped == [True]
