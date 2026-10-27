from __future__ import annotations

import builtins
import json
import sys
import types
from pathlib import Path

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import session
from pwmcp_client import user_agent as ua_module
from pwmcp_client.contract import PwmcpContract


def _install_cli_browser(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_error: Exception | None = None,
    navigation_fails: bool = False,
    contract: object | None = None,
    reported_user_agent: str | None = None,
    cdp_fails: bool = False,
) -> dict[str, object]:
    calls: dict[str, object] = {
        "connect": [],
        "goto": [],
        "locators": [],
        "visible_filters": [],
        "first_matches": [],
        "wait_for": [],
        "screenshots": [],
        "context_options": [],
        "context_closed": [],
        "closed": [],
        "cdp": [],
    }

    class FakePlaywrightError(Exception):
        pass

    class FakeLocator:
        def __init__(self, selector: str) -> None:
            self.selector = selector

        def filter(self, *, visible: bool) -> "FakeLocator":
            calls["visible_filters"].append({"selector": self.selector, "visible": visible})
            return self

        @property
        def first(self) -> "FakeLocator":
            calls["first_matches"].append(self.selector)
            return self

        def inner_text(self, *, timeout: int) -> str:
            calls["inner_text_timeout"] = timeout
            return "rendered page text"

        def aria_snapshot(self, *, timeout: int) -> str:
            calls["snapshot_timeout"] = timeout
            return '- heading "Sample page" [level=1]'

        def wait_for(self, *, state: str, timeout: int) -> None:
            calls["wait_for"].append({"state": state, "timeout": timeout})

    class FakePage:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls["goto"].append({"url": url, "wait_until": wait_until, "timeout": timeout})
            if navigation_fails:
                raise FakePlaywrightError("navigation failed")

        def locator(self, selector: str) -> FakeLocator:
            calls["locators"].append(selector)
            return FakeLocator(selector)

        def screenshot(self, **kwargs: object) -> bytes:
            calls["screenshots"].append(kwargs)
            return b"fake image bytes"

        def evaluate(self, expression: str) -> str | None:
            calls["evaluate"] = expression
            return reported_user_agent

    class FakeCdp:
        def send(self, method: str, params: object) -> None:
            if cdp_fails:
                raise FakePlaywrightError("cdp refused")
            calls["cdp"].append({"method": method, "params": params})

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def new_cdp_session(self, _page: object) -> FakeCdp:
            return FakeCdp()

        def close(self) -> None:
            calls["context_closed"].append(True)

    class FakeBrowser:
        def new_context(self, **options: object) -> FakeContext:
            calls["context_options"].append(options)
            return FakeContext()

    class FakeLease:
        browser = FakeBrowser()

        def __enter__(self) -> "FakeLease":
            return self

        def __exit__(self, *_args: object) -> None:
            calls["closed"].append(True)

    class BrowserLeaseStub:
        @classmethod
        def connect(cls, **kwargs: object) -> FakeLease:
            calls["connect"].append(kwargs)
            if connect_error:
                raise connect_error
            lease = FakeLease()
            if contract is not None:
                lease.contract = contract
            return lease

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError
    playwright = types.ModuleType("playwright")
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(cli_module, "BrowserLease", BrowserLeaseStub)
    calls["playwright_error"] = FakePlaywrightError
    return calls


def test_inspect_text_navigates_remotely_waits_and_closes_lease(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main([
        "inspect",
        "https://example.test/page",
        "--contract",
        "contract.json",
        "--wait-until",
        "domcontentloaded",
        "--wait-for",
        "#content",
        "--timeout-ms",
        "4000",
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rendered page text\n"
    assert captured.err == ""
    assert calls["connect"] == [{
        "contract_url": "contract.json",
        "label": "pwmcp-inspect",
        "timeout_ms": 4000,
    }]
    assert calls["goto"] == [{
        "url": "https://example.test/page",
        "wait_until": "domcontentloaded",
        "timeout": 4000,
    }]
    assert calls["locators"] == ["#content", "body"]
    assert calls["visible_filters"] == [{"selector": "#content", "visible": True}]
    assert calls["first_matches"] == ["#content"]
    assert calls["wait_for"] == [{"state": "visible", "timeout": 4000}]
    assert calls["context_options"] == [{"ignore_https_errors": False}]
    assert calls["cdp"] == []  # no override configured: no user-agent emulation at all
    assert calls["context_closed"] == [True]
    assert calls["closed"] == [True]


def test_inspect_can_ignore_https_errors_for_its_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main([
        "inspect", "https://self-signed.example", "--ignore-https-errors"
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rendered page text\n"
    assert captured.err == ""
    assert calls["context_options"] == [{"ignore_https_errors": True}]
    assert calls["context_closed"] == [True]


def test_inspect_snapshot_writes_a_trailing_newline_to_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    output = tmp_path / "snapshot.yml"
    assert cli_module.main([
        "inspect", "https://example.test", "--format", "snapshot", "--output", str(output)
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"wrote snapshot output to {output}" in captured.err
    assert output.read_text(encoding="utf-8") == '- heading "Sample page" [level=1]\n'


@pytest.mark.parametrize(("suffix", "image_type"), [(".jpg", "jpeg"), (".jpeg", "jpeg"), (".png", "png")])
def test_inspect_screenshot_writes_requested_image_type_and_full_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    image_type: str,
) -> None:
    calls = _install_cli_browser(monkeypatch)
    output = tmp_path / f"page{suffix}"
    assert cli_module.main([
        "inspect",
        "https://example.test",
        "--format",
        "screenshot",
        "--output",
        str(output),
        "--full-page",
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"wrote screenshot output to {output}" in captured.err
    assert output.read_bytes() == b"fake image bytes"
    assert calls["screenshots"] == [{
        "full_page": True,
        "type": image_type,
        "animations": "disabled",
        "timeout": 30_000,
    }]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["http://["], "URL is malformed"),
        (["relative/path"], "absolute http:// or https:// URL"),
        (["https://example.test", "--timeout-ms", "0"], "--timeout-ms must be a positive integer"),
        (["https://example.test", "--format", "screenshot"], "--output is required"),
        (["https://example.test", "--format", "screenshot", "--output", "page.gif"], "must end in .png, .jpg, or .jpeg"),
    ],
)
def test_inspect_rejects_invalid_inputs_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", *args]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert calls["connect"] == []


def test_inspect_reports_missing_playwright_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_import = builtins.__import__

    def import_without_playwright(name: str, *args: object, **kwargs: object) -> object:
        if name == "playwright.sync_api":
            raise ImportError("missing playwright")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_playwright)
    assert cli_module.main(["inspect", "https://example.test"]) == 1
    captured = capsys.readouterr()
    assert "Playwright Python package is not installed" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("failure", ["connect", "navigate"])
def test_inspect_reports_remote_browser_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    calls = _install_cli_browser(
        monkeypatch,
        connect_error=session.PwmcpError("service unavailable") if failure == "connect" else None,
        navigation_fails=failure == "navigate",
    )
    assert cli_module.main(["inspect", "https://example.test"]) == 1
    captured = capsys.readouterr()
    assert "pwmcp inspect failed:" in captured.err
    assert "check that PWMCP is running" in captured.err
    assert calls["closed"] == ([] if failure == "connect" else [True])
    assert calls["context_closed"] == ([] if failure == "connect" else [True])


def test_inspect_reports_output_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    output = tmp_path / "directory"
    output.mkdir()
    assert cli_module.main([
        "inspect", "https://example.test", "--output", str(output)
    ]) == 1
    assert "cannot write text output" in capsys.readouterr().err


def test_inspect_requires_a_url() -> None:
    assert cli_module.main(["inspect"]) == 2


# --- PWMCP-09: overridable user agent ---------------------------------------------


def _contract(**overrides: object) -> PwmcpContract:
    fields: dict[str, object] = {
        "schema_version": 1,
        "release": "1.0.0",
        "playwright_version": "1.64.0",
        "protocol_version": "1.64",
        "ws_url": "ws://pwmcp:3000/",
        "health_url": "http://pwmcp:3000/health",
        "default_lease_seconds": 1800,
        "max_lease_seconds": 7200,
        "max_clients": 2,
        "idle_recycle_seconds": 30,
    }
    fields.update(overrides)
    return PwmcpContract(**fields)  # type: ignore[arg-type]


def test_per_call_preset_sets_context_ua_client_hints_and_reports_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, contract=_contract())
    edge = ua_module.PRESETS["edge-windows"]
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "edge-windows"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rendered page text\n"
    assert captured.err == f"[INFO] user agent: {edge} (source: preset:edge-windows)\n"
    assert calls["context_options"] == [{"ignore_https_errors": False, "user_agent": edge}]
    (cdp,) = calls["cdp"]
    assert cdp["method"] == "Emulation.setUserAgentOverride"
    assert cdp["params"] == ua_module.cdp_override_params(edge)
    brands = {b["brand"] for b in cdp["params"]["userAgentMetadata"]["brands"]}
    assert "Microsoft Edge" in brands and "HeadlessChrome" not in brands
    assert calls["context_closed"] == [True]  # the override dies with the context


def test_json_result_carries_content_user_agent_and_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--json", "--user-agent", "my-bot/1.0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "content": "rendered page text",
        "user_agent": "my-bot/1.0",
        "user_agent_source": "custom",
    }


def test_json_result_without_an_override_keeps_the_original_bare_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, reported_user_agent="Mozilla/5.0 HeadlessChrome/156.0.0.0")
    assert cli_module.main(["inspect", "https://example.test", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == "rendered page text"  # a bare JSON string, as before
    assert captured.err == ""


def test_output_file_with_json_prints_nothing_extra_without_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    out = tmp_path / "r.txt"
    assert cli_module.main(["inspect", "https://example.test", "--json", "--output", str(out)]) == 0
    assert capsys.readouterr().out == ""


def test_output_file_with_json_reports_identity_only_under_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    out = tmp_path / "r.txt"
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--output", str(out), "--user-agent", "chrome-windows",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_agent_source"] == "preset:chrome-windows" and payload["output"] == str(out)


def test_override_identity_line_cannot_be_silenced_by_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--quiet", "--user-agent", "edge-windows"]) == 0
    assert "(source: preset:edge-windows)" in capsys.readouterr().err


def test_default_mode_identity_line_obeys_quiet_and_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--quiet"]) == 0
    assert capsys.readouterr().err == ""


def test_server_level_override_published_in_the_contract_is_applied_and_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chrome = ua_module.PRESETS["chrome-windows"]
    calls = _install_cli_browser(
        monkeypatch,
        contract=_contract(user_agent=chrome, user_agent_source="preset:chrome-windows"),
    )
    assert cli_module.main(["inspect", "https://example.test"]) == 0
    assert calls["context_options"] == [{"ignore_https_errors": False, "user_agent": chrome}]
    assert "(source: preset:chrome-windows)" in capsys.readouterr().err


def test_per_call_default_beats_the_server_level_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(
        monkeypatch, contract=_contract(user_agent="x", user_agent_source="custom")
    )
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "default"]) == 0
    assert calls["context_options"] == [{"ignore_https_errors": False}]
    assert capsys.readouterr().err == ""  # back to default: no identity line


@pytest.mark.parametrize("bad", ["bad\r\nagent", "café", "x" * 513, " padded "])
def test_invalid_per_call_user_agent_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", bad]) == 1
    assert "--user-agent is invalid" in capsys.readouterr().err
    assert calls["connect"] == []


def test_client_hint_failure_is_reported_not_silent_and_keeps_the_ua_string(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, cdp_fails=True)
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "firefox-windows"]) == 0
    captured = capsys.readouterr()
    assert "client-hint metadata not applied" in captured.err
    assert calls["context_options"][0]["user_agent"] == ua_module.PRESETS["firefox-windows"]
    assert captured.out == "rendered page text\n"


def test_custom_ua_without_a_browser_token_warns_that_hints_are_not_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "my-bot/1.0"]) == 0
    assert "client-hint metadata not set" in capsys.readouterr().err
    assert calls["cdp"] == []


def test_contract_parses_the_published_user_agent_and_defaults_when_absent() -> None:
    base = {
        "schema_version": 1, "release": "1", "playwright": {"python": "1.64.0", "protocol": "1.64"},
        "endpoints": {"playwright_ws": "ws://x/", "health": "http://x/health"},
        "run_server": {"limits": {
            "default_lease_seconds": 1, "max_lease_seconds": 2, "max_clients": 1,
            "idle_recycle_seconds": 1}},
    }
    absent = PwmcpContract.from_dict(base)
    assert (absent.user_agent, absent.user_agent_source) == (None, "default")
    published = PwmcpContract.from_dict({**base, "user_agent": {"value": None, "source": "default"}})
    assert published.user_agent is None
    active = PwmcpContract.from_dict(
        {**base, "user_agent": {"value": "ua", "source": "preset:edge-windows"}}
    )
    assert (active.user_agent, active.user_agent_source) == ("ua", "preset:edge-windows")
