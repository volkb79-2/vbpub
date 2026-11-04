"""`pwmcp inspect` against bot protection: honest, structured, exit 4, and the human-in-the-loop session."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from blocked_support import load_page

from pwmcp_client import cli as cli_module
from pwmcp_client.cli import EXIT_BLOCKED

CF_HEADERS = {"server": "cloudflare", "cf-ray": "a553626f5ca61da0-FRA", "cf-mitigated": "challenge"}


class FakePlaywrightError(Exception):
    pass


def _install(
    monkeypatch: pytest.MonkeyPatch,
    page_name: str = "cloudflare-managed-challenge.html",
    *,
    status: int | None = 403,
    headers: dict[str, str] | None = None,
    goto_error: bool = False,
    wait_error: bool = False,
    events: list[tuple[str, object]] | None = None,
    probe_fails: bool = False,
    title_fails: bool = False,
    texts: list[str] | None = None,
) -> dict[str, object]:
    saved = load_page(page_name)
    calls: dict[str, object] = {"context_options": [], "goto": 0, "contexts_closed": 0, "screenshots": 0}
    state = {"handlers": {}}
    page_texts = list(texts) if texts is not None else None

    class FakeLocator:
        def filter(self, *, visible: bool) -> "FakeLocator":
            return self

        @property
        def first(self) -> "FakeLocator":
            return self

        def inner_text(self, *, timeout: int) -> str:
            if probe_fails:
                raise FakePlaywrightError("page closed")
            if page_texts:
                return page_texts.pop(0) if len(page_texts) > 1 else page_texts[0]
            return saved.text

        def aria_snapshot(self, *, timeout: int) -> str:
            return '- heading "challenge" [level=2]'

        def wait_for(self, *, state: str, timeout: int) -> None:
            if wait_error:
                raise FakePlaywrightError("Timeout 100000ms exceeded waiting for selector")

    class FakeFrame:
        def __init__(self, url: str) -> None:
            self.url = url

    class FakePage:
        url = "https://browser.geekbench.com/v7/cpu/1"
        main_frame = None  # the main frame's identity; events carry request.frame to compare against

        @property
        def frames(self) -> list[FakeFrame]:
            return [FakeFrame(url) for url in saved.frames]

        def title(self) -> str:
            if title_fails:
                raise FakePlaywrightError("no title")
            return saved.title

        def on(self, event: str, handler: object) -> None:
            state["handlers"].setdefault(event, []).append(handler)

        def goto(self, url: str, *, wait_until: str, timeout: int) -> object:
            calls["goto"] += 1
            for event, payload in events or []:
                for handler in state["handlers"].get(event, []):
                    handler(payload)
            if goto_error:
                raise FakePlaywrightError("Page.goto: Timeout 60000ms exceeded")
            if status is None:
                return None
            return types.SimpleNamespace(status=status, headers=CF_HEADERS if headers is None else headers)

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator()

        def wait_for_timeout(self, milliseconds: int) -> None:
            return None

        def screenshot(self, **_kwargs: object) -> bytes:
            calls["screenshots"] += 1
            return b"image"

        def evaluate(self, _expression: str) -> str:
            return "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/156"

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            calls["contexts_closed"] += 1

    class FakeBrowser:
        def new_context(self, **options: object) -> FakeContext:
            calls["context_options"].append(options)
            return FakeContext()

    class FakeLease:
        browser = FakeBrowser()

        def __enter__(self) -> "FakeLease":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class BrowserLeaseStub:
        @classmethod
        def connect(cls, **_kwargs: object) -> FakeLease:
            return FakeLease()

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError
    playwright = types.ModuleType("playwright")
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(cli_module, "BrowserLease", BrowserLeaseStub)
    return calls


URL = "https://browser.geekbench.com/v7/cpu/1"


def test_a_challenge_page_is_reported_blocked_with_a_distinct_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _install(monkeypatch)
    assert cli_module.main(["inspect", URL]) == EXIT_BLOCKED == 4
    captured = capsys.readouterr()
    banner = (
        "blocked by bot protection (Cloudflare managed challenge); "
        "page content below is the challenge page, not the target"
    )
    assert captured.out.startswith(banner + "\n\n")
    assert "Performing security verification" in captured.out
    assert "[ERROR] " + banner in captured.err
    assert "HTTP 403" in captured.err and "header:cf-mitigated=challenge" in captured.err
    assert "report this to the user as BLOCKED" in captured.err
    assert calls["contexts_closed"] == 1


def test_json_carries_the_structured_verdict(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _install(monkeypatch)
    assert cli_module.main(["inspect", URL, "--json"]) == EXIT_BLOCKED
    result = json.loads(capsys.readouterr().out)
    assert result["blocked"] is True
    assert result["provider"] == "Cloudflare" and result["kind"] == "managed challenge"
    assert result["status"] == 403 and result["final_url"] == URL
    assert "header:cf-mitigated=challenge" in result["signals"] and "status:403" in result["signals"]
    assert isinstance(result["waited_ms"], int) and result["waited_ms"] >= 0
    assert "in the content field" in result["message"]
    assert "Performing security verification" in result["content"]
    assert "user_agent" not in result  # no override and no storage state: nothing extra


@pytest.mark.parametrize("fmt", ["snapshot", "screenshot"])
def test_other_formats_are_checked_too_and_never_write_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path, fmt: str
) -> None:
    _install(monkeypatch)
    out = tmp_path / ("page.png" if fmt == "screenshot" else "page.yml")
    assert cli_module.main(["inspect", URL, "--format", fmt, "--output", str(out)]) == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert not out.exists()
    assert f"--output {out} was not written" in captured.err
    assert "challenge page, not the target" in captured.out


def test_a_wait_that_ends_on_the_challenge_reports_blocked_with_the_waited_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, wait_error=True)
    assert cli_module.main(["inspect", URL, "--wait-for", "table", "--json"]) == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert json.loads(captured.out)["blocked"] is True
    assert "still blocked after waiting " in captured.err and " s for 'table'" in captured.err
    assert "Timeout" not in captured.err  # not reported as a generic timeout


def test_a_navigation_timeout_on_the_challenge_is_blocked_not_generic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, goto_error=True, status=None)
    assert cli_module.main(["inspect", URL, "--wait-until", "networkidle"]) == EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "no HTTP status" in captured.err
    assert "pwmcp inspect failed" not in captured.err


def test_a_timeout_on_an_ordinary_page_stays_a_generic_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, "login-with-recaptcha.html", wait_error=True, status=200, headers={})
    assert cli_module.main(["inspect", URL, "--wait-for", "table"]) == 1
    assert "pwmcp inspect failed" in capsys.readouterr().err


def test_a_failing_probe_never_hides_the_original_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, goto_error=True, probe_fails=True, title_fails=True, status=None)
    assert cli_module.main(["inspect", URL]) == 1
    assert "Page.goto: Timeout" in capsys.readouterr().err


def test_blocked_is_not_retried(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _install(monkeypatch)
    assert cli_module.main(["inspect", URL, "--retries", "3"]) == EXIT_BLOCKED
    assert calls["goto"] == 1
    assert "retrying" not in capsys.readouterr().err


def test_a_clean_page_is_unchanged_and_not_flagged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, "login-with-recaptcha.html", status=200, headers={"server": "cloudflare"})
    assert cli_module.main(["inspect", URL]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Sign in to your account")
    assert "blocked" not in captured.out + captured.err


def test_the_latest_main_document_decides_not_the_first_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A challenge that cleared itself re-navigates: the stale cf-mitigated header must not block the real page."""

    request = types.SimpleNamespace(resource_type="document", frame=None)
    real = types.SimpleNamespace(status=200, headers={}, request=request)
    other_frame = types.SimpleNamespace(
        status=403, headers=CF_HEADERS, request=types.SimpleNamespace(resource_type="document", frame=object())
    )
    _install(monkeypatch, "login-with-recaptcha.html", events=[("response", real), ("response", other_frame)])
    assert cli_module.main(["inspect", URL]) == 0
    assert capsys.readouterr().out.startswith("Sign in")


def test_user_agent_and_storage_fields_ride_along_in_a_blocked_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _install(monkeypatch)
    state = _state_file(tmp_path)
    assert cli_module.main(
        ["inspect", URL, "--json", "--user-agent", "chrome-windows", "--storage-state", str(state)]
    ) == EXIT_BLOCKED
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["user_agent_source"] == "preset:chrome-windows"
    assert result["storage_state"] == {"cookies": 1, "origins": 0}
    assert "expired or be bound to another IP address or user agent" in captured.err
    assert "report this to the user" not in captured.err


# --- --storage-state: the human-in-the-loop session ----------------------------------

SECRET = "cf-clearance-secret-value"


def _state_file(tmp_path: Path, mode: int = 0o600) -> Path:
    path = tmp_path / "state.json"
    cookies = [
        {"name": "cf_clearance", "value": SECRET, "domain": ".geekbench.com", "path": "/"},
        {"name": "x", "value": SECRET, "domain": "other.example", "path": "/"},
    ]
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")
    path.chmod(mode)
    return path


def test_storage_state_is_scoped_applied_and_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls = _install(monkeypatch, "login-with-recaptcha.html", status=200, headers={})
    assert cli_module.main(["inspect", URL, "--storage-state", str(_state_file(tmp_path))]) == 0
    captured = capsys.readouterr()
    (options,) = calls["context_options"]
    assert [c["name"] for c in options["storage_state"]["cookies"]] == ["cf_clearance"]  # other.example is not sent
    assert "storage state: 1 cookies and 0 origins applied" in captured.err
    assert SECRET not in captured.out + captured.err


def test_storage_state_with_json_and_output_reports_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _install(monkeypatch, "login-with-recaptcha.html", status=200, headers={})
    state = _state_file(tmp_path)
    assert cli_module.main(["inspect", URL, "--storage-state", str(state), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["storage_state"] == {"cookies": 1, "origins": 0}
    out = tmp_path / "page.txt"
    assert cli_module.main(["inspect", URL, "--storage-state", str(state), "--json", "--output", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["storage_state"] == {"cookies": 1, "origins": 0}
    assert out.is_file()


def test_an_unsafe_storage_state_is_refused_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls = _install(monkeypatch)
    assert cli_module.main(["inspect", URL, "--storage-state", str(_state_file(tmp_path, 0o644))]) == 1
    err = capsys.readouterr().err
    assert "--storage-state is unusable" in err and "chmod 600" in err and SECRET not in err
    assert calls["goto"] == 0 and calls["context_options"] == []
