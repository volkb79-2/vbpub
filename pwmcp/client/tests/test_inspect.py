from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import session


def _install_cli_browser(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_error: Exception | None = None,
    navigation_fails: bool = False,
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

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            calls["context_closed"].append(True)

    class FakeBrowser:
        def new_context(self, *, ignore_https_errors: bool) -> FakeContext:
            calls["context_options"].append({"ignore_https_errors": ignore_https_errors})
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
            return FakeLease()

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
