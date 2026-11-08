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
    reported_user_agent: object = None,
    cdp_fails: bool = False,
    texts: list[str] | None = None,
    fail_navigations: int = 0,
    page_events: list[tuple[str, object]] | None = None,
    main_status: int | None = None,
    navigation_error: str = "navigation failed",
    html: str = "<main><h1>Sample page</h1><p>Rendered article.</p></main>",
    selected_html: dict[str, str] | None = None,
) -> dict[str, object]:
    texts_left = list(texts or ["rendered page text"])
    calls: dict[str, object] = {
        "handlers": {},
        "settles": [],
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
            return texts_left.pop(0) if len(texts_left) > 1 else texts_left[0]

        def inner_html(self, *, timeout: int) -> str:
            calls["inner_html_timeout"] = timeout
            return (selected_html or {}).get(self.selector, html)

        def count(self) -> int:
            return 1

        def aria_snapshot(self, *, timeout: int) -> str:
            calls["snapshot_timeout"] = timeout
            return '- heading "Sample page" [level=1]'

        def wait_for(self, *, state: str, timeout: int) -> None:
            calls["wait_for"].append({"state": state, "timeout": timeout})

    class FakePage:
        def on(self, event: str, handler: object) -> None:
            calls["handlers"].setdefault(event, []).append(handler)

        def wait_for_timeout(self, milliseconds: int) -> None:
            calls["settles"].append(milliseconds)

        def goto(self, url: str, *, wait_until: str, timeout: int) -> object:
            calls["goto"].append({"url": url, "wait_until": wait_until, "timeout": timeout})
            if navigation_fails or len(calls["goto"]) <= fail_navigations:
                raise FakePlaywrightError(navigation_error)
            for event, payload in page_events or []:
                for handler in calls["handlers"].get(event, []):
                    handler(payload)
            return types.SimpleNamespace(status=main_status) if main_status is not None else None

        def locator(self, selector: str) -> FakeLocator:
            calls["locators"].append(selector)
            return FakeLocator(selector)

        def screenshot(self, **kwargs: object) -> bytes:
            calls["screenshots"].append(kwargs)
            return b"fake image bytes"

        def evaluate(self, expression: str) -> object:
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
        "inspect", "https://self-signed.example", "--ignore-https-errors", "--wait-for", "body"
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
        (["https:///no-host"], "absolute http:// or https:// URL"),
        (["https://example.test", "--timeout-ms", "0"], "--timeout-ms must be a positive integer"),
        (["https://example.test", "--format", "screenshot"], "--output is required"),
        (["https://example.test", "--format", "screenshot", "--output", "page.gif"], "must end in .png, .jpg, or .jpeg"),
    ],
)
def test_inspect_rejects_invalid_inputs_before_connecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    # A compare-swapped suffix guard can let the invalid page.gif case reach
    # the browser and write relative to cwd. Keep every such mutant side effect
    # outside the repository tree.
    monkeypatch.chdir(tmp_path)
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
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "edge-windows", "--wait-for", "body"]) == 0
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
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--user-agent", "my-bot/1.0", "--wait-for", "body",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "content": "rendered page text",
        "diagnostics": [],
        "format": "text",
        "http_error": False,
        "http_status": None,
        "user_agent": "my-bot/1.0",
        "user_agent_source": "custom",
    }


def test_non_string_observed_user_agent_falls_back_to_the_configured_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, reported_user_agent=123)
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--user-agent", "my-bot/1.0", "--wait-for", "body",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "content": "rendered page text",
        "diagnostics": [],
        "format": "text",
        "http_error": False,
        "http_status": None,
        "user_agent": "my-bot/1.0",
        "user_agent_source": "custom",
    }


def test_json_result_without_diagnostics_uses_the_stable_object_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, reported_user_agent="Mozilla/5.0 HeadlessChrome/156.0.0.0")
    assert cli_module.main(["inspect", "https://example.test", "--json", "--wait-for", "body"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "content": "rendered page text",
        "diagnostics": [],
        "format": "text",
        "http_error": False,
        "http_status": None,
    }
    assert captured.err == ""


def test_json_result_keeps_diagnostics_in_the_stable_object_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, main_status=404)
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--wait-for", "body", "--allow-http-error",
    ]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["content"] == "rendered page text"
    assert result["http_status"] == 404 and result["http_error"] is True
    assert any("main document HTTP 404" in item for item in result["diagnostics"])


def test_output_file_with_json_reports_the_stable_metadata_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    out = tmp_path / "r.txt"
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--output", str(out), "--wait-for", "body",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "content": None,
        "diagnostics": [],
        "format": "text",
        "http_error": False,
        "http_status": None,
        "output": str(out),
    }


def test_output_file_with_json_reports_identity_only_under_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    out = tmp_path / "r.txt"
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--output", str(out), "--user-agent", "chrome-windows",
        "--wait-for", "body",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_agent_source"] == "preset:chrome-windows" and payload["output"] == str(out)
    assert payload["content"] is None and payload["diagnostics"] == []


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
    assert cli_module.main(["inspect", "https://example.test", "--quiet", "--wait-for", "body"]) == 0
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
    assert cli_module.main(["inspect", "https://example.test", "--user-agent", "default", "--wait-for", "body"]) == 0
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


# --- PWMCP-24..26: --retries with a stable-DOM check, page diagnostics, small-result warning ---


def _resp(status: int, resource_type: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(status=status, request=types.SimpleNamespace(resource_type=resource_type))


BIG = "x" * 2000


def test_retries_recovers_from_a_failed_navigation_with_a_fresh_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, texts=[BIG], fail_navigations=1)
    assert cli_module.main(["inspect", "https://example.test", "--retries", "2"]) == 0
    captured = capsys.readouterr()
    assert captured.out == BIG + "\n"
    assert "attempt 1/3 failed: navigation failed; retrying" in captured.err
    assert len(calls["goto"]) == 2 and calls["context_closed"] == [True, True]
    assert len(calls["context_options"]) == 2 and calls["closed"] == [True]  # one lease, a fresh context each


def test_retries_exhausted_reports_the_last_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, fail_navigations=5)
    assert cli_module.main(["inspect", "https://example.test", "--retries", "1"]) == 1
    captured = capsys.readouterr()
    assert "attempt 1/2 failed" in captured.err and "pwmcp inspect failed: navigation failed" in captured.err
    assert len(calls["goto"]) == 2


def test_default_generic_navigation_failure_is_not_retried_or_settled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_cli_browser(monkeypatch, fail_navigations=1)
    assert cli_module.main(["inspect", "https://example.test"]) == 1
    assert len(calls["goto"]) == 1 and calls["settles"] == []


@pytest.mark.parametrize(
    "message",
    [
        "Page.goto: Page crashed",
        "Execution context was destroyed",
    ],
)
def test_first_page_crash_is_retried_once_without_an_explicit_retries_option(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
) -> None:
    # Fake Playwright server: its first navigation fails with the crash signatures
    # reported by operators, and the fresh context's second navigation works.
    calls = _install_cli_browser(monkeypatch, fail_navigations=1, navigation_error=message)
    assert cli_module.main(["inspect", "https://example.test/page", "--format", "snapshot"]) == 0
    captured = capsys.readouterr()
    assert captured.out == '- heading "Sample page" [level=1]\n'
    assert len(calls["goto"]) == 2
    assert "retrying automatically once" in captured.err
    assert message in captured.err


@pytest.mark.parametrize("message", ["Target closed", "Browser has been closed"])
@pytest.mark.parametrize("retry_args", [[], ["--retries", "2"]])
def test_browser_level_closure_does_not_retry_on_a_dead_playwright_lease(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
    retry_args: list[str],
) -> None:
    calls = _install_cli_browser(monkeypatch, fail_navigations=1, navigation_error=message)
    assert cli_module.main([
        "inspect", "https://example.test/page", "--format", "snapshot", *retry_args
    ]) == 1
    captured = capsys.readouterr()
    assert len(calls["goto"]) == 1
    assert len(calls["context_options"]) == 1
    assert calls["closed"] == [True]
    assert "retrying" not in captured.err
    assert "check that PWMCP is still running" in captured.err


def test_final_browser_crash_hint_does_not_blame_pwmcp_connectivity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_cli_browser(monkeypatch, fail_navigations=3, navigation_error="Page.goto: Page crashed")
    assert cli_module.main(["inspect", "https://example.test/page", "--format", "snapshot"]) == 1
    captured = capsys.readouterr()
    assert len(calls["goto"]) == 2  # the automatic retry is bounded to one attempt
    assert "PWMCP is reachable" not in captured.err
    assert "page or browser crashed or was closed" in captured.err
    assert "check that PWMCP is still running" in captured.err


def test_connectivity_failure_keeps_a_service_reachability_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_cli_browser(
        monkeypatch,
        connect_error=session.PwmcpUnavailable("pwmcp is unreachable at pwmcp:3001"),
    )
    assert cli_module.main(["inspect", "https://example.test/page"]) == 1
    captured = capsys.readouterr()
    assert "check that PWMCP is running and reachable from this container" in captured.err
    assert "page or browser crashed" not in captured.err
    assert len(calls["connect"]) == 1


def test_unstable_page_is_retried_and_stable_one_is_not(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, texts=["a" * 2000, "b" * 2000, "c" * 2000])
    assert cli_module.main(["inspect", "https://example.test", "--retries", "1"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "c" * 2000 + "\n"  # the last attempt is returned as it is, unchecked
    assert "attempt 1/2: the page was still changing; retrying" in captured.err
    assert len(calls["goto"]) == 2 and calls["settles"] == [500]

    calls = _install_cli_browser(monkeypatch, texts=[BIG], main_status=200)
    assert cli_module.main(["inspect", "https://example.test", "--retries", "3"]) == 0
    captured = capsys.readouterr()
    assert captured.out == BIG + "\n" and captured.err == ""
    assert len(calls["goto"]) == 1 and calls["settles"] == [500]


@pytest.mark.parametrize("bad", ["-1", "11"])
def test_retries_out_of_range_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--retries", bad]) == 1
    assert "--retries must be between 0 and 10" in capsys.readouterr().err
    assert calls["connect"] == []


def test_snapshot_format_is_checked_for_stability_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test", "--format", "snapshot", "--retries", "1"]) == 0
    assert calls["settles"] == [500]
    assert capsys.readouterr().out == '- heading "Sample page" [level=1]\n'


@pytest.mark.parametrize("fmt", ["text", "markdown", "links"])
def test_main_extraction_formats_use_the_checked_in_article_fixture(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fmt: str,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "pages" / "article.html"
    _install_cli_browser(monkeypatch, html=fixture.read_text(encoding="utf-8"))

    assert cli_module.main([
        "inspect", "https://example.test/news/release", "--format", fmt, "--main", "--wait-for", "body",
    ]) == 0
    result = capsys.readouterr()
    assert "Header chrome" not in result.out and "Footer chrome" not in result.out
    if fmt == "links":
        assert "the guide" in result.out
        assert "https://example.test/issues/7" in result.out
    else:
        assert "Release notes" in result.out
    if fmt == "markdown":
        assert "# Release notes" in result.out and "[the guide](https://example.test/guide/start?x=1&y=2)" in result.out


def test_text_selector_reads_only_the_selected_element(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(
        monkeypatch,
        html="<nav>Chrome</nav><main>Whole page</main>",
        selected_html={"#summary": "<h2>Selected summary</h2>"},
    )
    assert cli_module.main([
        "inspect", "https://example.test", "--selector", "#summary", "--wait-for", "body",
    ]) == 0
    result = capsys.readouterr()
    assert result.out == "Selected summary\n"
    assert "Chrome" not in result.out and "Whole page" not in result.out
    assert "#summary" in calls["locators"]


def test_markdown_selector_is_read_again_for_stability(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _install_cli_browser(
        monkeypatch,
        html="<article>whole page</article>",
        selected_html={"article": "<h2>Selected heading</h2><p><a href='/guide'>Guide</a></p>"},
    )
    assert cli_module.main([
        "inspect", "https://example.test", "--format", "markdown", "--selector", "article",
        "--retries", "1", "--wait-for", "body",
    ]) == 0
    captured = capsys.readouterr()
    assert "## Selected heading" in captured.out
    assert "[Guide](https://example.test/guide)" in captured.out
    assert calls["settles"] == [500]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--selector", "main", "--format", "snapshot"], "--selector requires"),
        (["--main", "--format", "snapshot"], "--main requires"),
        (["--main", "--selector", "main"], "--main and --selector cannot be used together"),
    ],
)
def test_content_extraction_options_reject_incompatible_formats(
    capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    assert cli_module.main(["inspect", "https://example.test", *extra]) == 1
    assert message in capsys.readouterr().err


def test_page_diagnostics_are_reported_on_stderr_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = [
        ("console", types.SimpleNamespace(type="error")),
        ("console", types.SimpleNamespace(type="log")),
        ("response", _resp(403, "script")),
        ("response", _resp(403, "stylesheet")),
        ("response", _resp(404, "image")),
        ("response", _resp(404, "document")),  # the main document is reported separately, not as a subresource
        ("response", _resp(200, "script")),
        ("requestfailed", object()),
    ]
    _install_cli_browser(monkeypatch, texts=[BIG], page_events=events, main_status=403)
    assert cli_module.main(["inspect", "https://example.test", "--quiet"]) == 0  # --quiet cannot hide it
    captured = capsys.readouterr()
    assert captured.out == BIG + "\n"
    assert (
        "[WARN] page diagnostics: main document HTTP 403; 1 console errors; "
        "3 failed subresources (403 x2, 404 x1); 1 requests failed outright "
        "(the page may not have rendered completely)\n"
    ) in captured.err


@pytest.mark.parametrize("fmt", ["text", "snapshot", "markdown", "links"])
def test_main_document_http_error_has_its_own_exit_code_for_textual_formats(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fmt: str,
) -> None:
    _install_cli_browser(monkeypatch, main_status=404)
    assert cli_module.main([
        "inspect", "https://example.test", "--format", fmt, "--wait-for", "body",
    ]) == cli_module.EXIT_HTTP_ERROR == 5
    captured = capsys.readouterr()
    assert "main document HTTP 404" in captured.err
    assert "--allow-http-error" in captured.err


def test_main_document_http_error_json_includes_status_fields_and_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_cli_browser(monkeypatch, main_status=404)
    assert cli_module.main([
        "inspect", "https://example.test", "--json", "--wait-for", "body",
    ]) == cli_module.EXIT_HTTP_ERROR
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["content"] == "rendered page text"
    assert result["http_status"] == 404 and result["http_error"] is True
    assert "main document HTTP 404" in captured.err


@pytest.mark.parametrize("fmt", ["markdown", "links"])
def test_main_document_http_error_json_applies_to_rendered_content_formats(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fmt: str,
) -> None:
    _install_cli_browser(monkeypatch, main_status=404)
    assert cli_module.main([
        "inspect", "https://example.test", "--format", fmt, "--json", "--wait-for", "body",
    ]) == cli_module.EXIT_HTTP_ERROR
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["format"] == fmt
    assert result["http_status"] == 404 and result["http_error"] is True
    assert "main document HTTP 404" in captured.err


def test_allow_http_error_returns_the_404_body_successfully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_cli_browser(monkeypatch, main_status=404)
    assert cli_module.main([
        "inspect", "https://example.test", "--wait-for", "body", "--allow-http-error",
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rendered page text\n"
    assert "main document HTTP 404" in captured.err


@pytest.mark.parametrize(
    ("status", "expected"),
    [(None, False), (399, False), (400, True), (599, True), (600, False)],
)
def test_http_error_range_is_exact(status: int | None, expected: bool) -> None:
    assert cli_module._is_http_error(status) is expected


def test_small_result_without_wait_for_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch)
    assert cli_module.main(["inspect", "https://example.test"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "rendered page text\n"
    assert "[WARN] result is only 18 bytes and no --wait-for was given" in captured.err
    assert "--retries N" in captured.err


def test_result_much_smaller_than_an_earlier_attempt_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, texts=["a" * 5000, "b" * 4000, "tiny"])
    assert cli_module.main(["inspect", "https://example.test", "--wait-for", "body", "--retries", "1"]) == 0
    err = capsys.readouterr().err
    assert "result is 4 bytes, much smaller than an earlier attempt (4000 bytes)" in err
    assert "only 4 bytes" not in err  # the shrink warning wins; --wait-for was given anyway


def test_console_diagnostics_count_errors_but_not_warnings() -> None:
    diagnostics = cli_module._PageDiagnostics()
    diagnostics._console(types.SimpleNamespace(type="warning"))
    assert diagnostics.console_errors == 0
    diagnostics._console(types.SimpleNamespace(type="error"))
    assert diagnostics.console_errors == 1


def test_a_subresource_status_of_400_is_reported() -> None:
    diagnostics = cli_module._PageDiagnostics()
    response = types.SimpleNamespace(
        status=400, request=types.SimpleNamespace(resource_type="script", frame=None)
    )
    diagnostics._response(response)
    assert diagnostics.statuses == {400: 1}


def test_a_main_document_status_of_400_is_reported() -> None:
    diagnostics = cli_module._PageDiagnostics()
    diagnostics.main_status = 400
    summary = diagnostics.summary()
    assert summary is not None
    assert "main document HTTP 400" in summary


def test_screenshot_is_not_retried_for_dom_stability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_cli_browser(monkeypatch)
    output = tmp_path / "page.png"
    assert cli_module.main([
        "inspect", "https://example.test", "--format", "screenshot", "--output", str(output), "--retries", "1"
    ]) == 0
    assert len(calls["goto"]) == 1
    assert len(calls["screenshots"]) == 1


def test_equal_half_size_is_not_reported_as_a_shrunken_result() -> None:
    warning = cli_module._small_result_warning(types.SimpleNamespace(wait_for="body"), "x" * 1024, [2048, 1024])
    assert warning is None


def test_exactly_one_kibibyte_is_not_a_small_shell_result() -> None:
    warning = cli_module._small_result_warning(types.SimpleNamespace(wait_for=None), "x" * 1024, [1024])
    assert warning is None


def test_the_maximum_retry_count_is_accepted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install_cli_browser(monkeypatch, texts=[BIG], main_status=200)
    assert cli_module.main([
        "inspect", "https://example.test", "--wait-for", "body", "--retries", "10"
    ]) == 0
    assert len(calls["goto"]) == 1
    assert capsys.readouterr().out == BIG + "\n"


def test_navigation_retry_warning_survives_error_log_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, fail_navigations=1)
    assert cli_module.main([
        "inspect", "https://example.test", "--wait-for", "body", "--retries", "1", "--log-level", "error"
    ]) == 0
    assert "attempt 1/2 failed: navigation failed; retrying" in capsys.readouterr().err


def test_unstable_page_retry_warning_survives_error_log_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_cli_browser(monkeypatch, texts=["a" * 2000, "b" * 2000, "b" * 2000])
    assert cli_module.main([
        "inspect", "https://example.test", "--wait-for", "body", "--retries", "1", "--log-level", "error"
    ]) == 0
    assert "the page was still changing; retrying" in capsys.readouterr().err


def test_page_diagnostic_warning_survives_error_log_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = [("response", _resp(400, "script"))]
    _install_cli_browser(monkeypatch, texts=[BIG], page_events=events, main_status=200)
    assert cli_module.main([
        "inspect", "https://example.test", "--log-level", "error"
    ]) == 0
    assert "page diagnostics: 1 failed subresources (400 x1)" in capsys.readouterr().err


def test_storage_state_transparency_survives_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scoped = cli_module.storage_module.ScopedState({"cookies": [], "origins": []}, 2, 1)
    monkeypatch.setattr(cli_module.storage_module, "load_scoped", lambda *_args: scoped)
    _install_cli_browser(monkeypatch, texts=[BIG])
    assert cli_module.main([
        "inspect", "https://example.test", "--wait-for", "body", "--storage-state", "state.json", "--quiet"
    ]) == 0
    assert "storage state: 2 cookies and 1 origins applied for this host" in capsys.readouterr().err


def test_blocked_output_refusal_warning_survives_error_log_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "target.txt"
    verdict = cli_module.BlockVerdict("Cloudflare", "managed challenge", ("header:cf-mitigated=challenge",))
    monkeypatch.setattr(cli_module, "detect_block", lambda **_kwargs: verdict)
    _install_cli_browser(monkeypatch, texts=[BIG])
    assert cli_module.main([
        "inspect", "https://example.test", "--output", str(output), "--allow-http-error", "--log-level", "error"
    ]) == cli_module.EXIT_BLOCKED
    assert f"--output {output} was not written: the result is not the target" in capsys.readouterr().err
    assert not output.exists()
