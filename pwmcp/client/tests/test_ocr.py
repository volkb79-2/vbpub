"""PWMCP-11 phase 1: the OCR client library, contract fields and the ``pwmcp ocr`` CLI, offline.

A local urllib-compatible fake sidecar stands in for the real service; a fake browser stands
in for the remote one. Nothing here proves the real sidecar's recognition quality.
"""

from __future__ import annotations

import http.server
import io
import json
import sys
import threading
import types
from pathlib import Path

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import diagnostics, ocr
from pwmcp_client.contract import PwmcpContract

PNG = b"\x89PNG\r\n\x1a\n" + b"rest-of-a-png"
CANNED = {
    "engine": "tesseract", "engine_version": "5.3.4", "lang": "eng", "psm": 6, "width": 640, "height": 400,
    "elapsed_ms": 12, "text": "Debian GNU/Linux installer\nInstall",
    "lines": [{"text": "Debian GNU/Linux installer", "conf": 93.5, "box": [8, 8, 300, 16]},
              {"text": "Install", "conf": 90.0, "box": [8, 40, 60, 16]}],
    "words": [{"text": "Debian", "conf": 95.0, "box": [8, 8, 60, 16], "line": 0},
              {"text": "Install", "conf": 90.0, "box": [8, 40, 60, 16], "line": 1}],
}


class Sidecar:
    """A scriptable fake sidecar: records every request, replies with a queued/default answer."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.reply: tuple[int, bytes] = (200, json.dumps(CANNED).encode())
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                from urllib.parse import parse_qs, urlsplit

                parts = urlsplit(self.path)
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"path": parts.path, "query": parse_qs(parts.query, keep_blank_values=True),
                                       "body": body, "content_type": self.headers["Content-Type"]})
                status, payload = outer.reply
                self.send_response(status)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return None

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sidecar(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PWMCP_OCR_URL", raising=False)
    instance = Sidecar()
    yield instance
    instance.close()


# ------------------------------------------------------------------ contract


BASE_CONTRACT = {
    "schema_version": 1, "release": "1.64.0-r3", "playwright": {"python": "1.64.0", "protocol": "1.64"},
    "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
    "run_server": {"limits": {"default_lease_seconds": 1, "max_lease_seconds": 2, "max_clients": 2,
                              "idle_recycle_seconds": 3}},
}


def test_old_server_contract_without_ocr_keeps_working() -> None:
    contract = PwmcpContract.from_dict(BASE_CONTRACT)
    assert contract.ocr_url == "" and contract.ocr_engines == ()


@pytest.mark.parametrize("ocr_value", [None, {}, {"url": None, "engines": None}, {"url": "", "engines": []}])
def test_empty_or_disabled_ocr_block_is_the_default(ocr_value: object) -> None:
    contract = PwmcpContract.from_dict({**BASE_CONTRACT, "ocr": ocr_value})
    assert (contract.ocr_url, contract.ocr_engines) == ("", ())


def test_contract_advertises_the_sidecar_and_serialises_to_json() -> None:
    contract = PwmcpContract.from_dict({**BASE_CONTRACT, "ocr": {"url": "http://pwmcp-ocr:8941",
                                                                  "engines": ["tesseract"]}})
    assert contract.ocr_url == "http://pwmcp-ocr:8941" and contract.ocr_engines == ("tesseract",)
    assert json.loads(json.dumps(contract.__dict__))["ocr_engines"] == ["tesseract"]


# ------------------------------------------------------------------ library


def test_recognize_parses_the_result(sidecar: Sidecar) -> None:
    result = ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert isinstance(result, ocr.OcrResult)
    assert (result.engine, result.engine_version, result.lang, result.psm) == ("tesseract", "5.3.4", "eng", 6)
    assert (result.width, result.height, result.elapsed_ms) == (640, 400, 12)
    assert result.text.splitlines()[0] == "Debian GNU/Linux installer"
    assert result.lines[0] == ocr.OcrLine("Debian GNU/Linux installer", 93.5, (8, 8, 300, 16))
    assert result.words[1] == ocr.OcrWord("Install", 90.0, (8, 40, 60, 16), 1)


def test_recognize_sends_defaults_and_the_image_untouched(sidecar: Sidecar) -> None:
    ocr.recognize(PNG, sidecar_url=sidecar.url + "/")
    (request,) = sidecar.requests
    assert request["path"] == "/v1/ocr" and request["body"] == PNG and request["content_type"] == "image/png"
    assert request["query"] == {"engine": ["tesseract"], "lang": ["eng"], "psm": ["6"], "scale": ["1"],
                                "min_conf": ["0"]}


def test_recognize_sends_every_option(sidecar: Sidecar) -> None:
    ocr.recognize(b"\xff\xd8jpeg", sidecar_url=sidecar.url, engine="glyphgrid", lang="eng+deu", psm=7, scale=3,
                  whitelist=";rm -rf & 09", min_conf=61.5)
    (request,) = sidecar.requests
    assert request["content_type"] == "image/jpeg"
    assert request["query"] == {"engine": ["glyphgrid"], "lang": ["eng+deu"], "psm": ["7"], "scale": ["3"],
                                "min_conf": ["61.5"], "whitelist": [";rm -rf & 09"]}
    ocr.recognize(b"raw", sidecar_url=sidecar.url)
    assert sidecar.requests[1]["content_type"] == "application/octet-stream"


def test_recognize_uses_the_environment_url(sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PWMCP_OCR_URL", sidecar.url)
    assert ocr.recognize(PNG).engine == "tesseract"


def test_recognize_ignores_proxy_environment_for_the_internal_sidecar(sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert ocr.recognize(PNG, sidecar_url=sidecar.url).engine == "tesseract"


def test_not_configured_names_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PWMCP_OCR_URL", raising=False)
    with pytest.raises(ocr.OcrNotConfigured) as caught:
        ocr.recognize(PNG)
    assert "ocr.enabled" in str(caught.value) and caught.value.code == "not_configured"
    assert isinstance(caught.value, ocr.OcrError)


def test_resolve_order_is_explicit_then_environment_then_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = PwmcpContract.from_dict({**BASE_CONTRACT, "ocr": {"url": "http://from-contract:1/", "engines": []}})
    monkeypatch.delenv("PWMCP_OCR_URL", raising=False)
    assert ocr.resolve_sidecar_url(None, contract=contract) == "http://from-contract:1"
    monkeypatch.setenv("PWMCP_OCR_URL", "http://from-env:2")
    assert ocr.resolve_sidecar_url(None, contract=contract) == "http://from-env:2"
    assert ocr.resolve_sidecar_url("https://explicit:3/", contract=contract) == "https://explicit:3"
    monkeypatch.delenv("PWMCP_OCR_URL")
    with pytest.raises(ocr.OcrNotConfigured):
        ocr.resolve_sidecar_url(None, contract=PwmcpContract.from_dict(BASE_CONTRACT))


@pytest.mark.parametrize("bad", ["ftp://x", "pwmcp-ocr:8941", "http://", "file:///etc/passwd"])
def test_non_http_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ocr.OcrError) as caught:
        ocr.resolve_sidecar_url(bad)
    assert caught.value.code == "bad_url"


def test_empty_image_is_refused_locally(sidecar: Sidecar) -> None:
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(b"", sidecar_url=sidecar.url)
    assert caught.value.code == "empty_image" and sidecar.requests == []


def test_sidecar_error_json_becomes_an_ocr_error(sidecar: Sidecar) -> None:
    sidecar.reply = (429, json.dumps({"error": "another recognition is in progress", "code": "busy"}).encode())
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert (caught.value.code, caught.value.status) == ("busy", 429)
    assert "another recognition is in progress" in str(caught.value)


@pytest.mark.parametrize("payload", [b"<html>nope</html>", b"[1, 2]", b"", b'{"error": 5}'])
def test_sidecar_error_without_usable_json_still_reports_the_status(sidecar: Sidecar, payload: bytes) -> None:
    sidecar.reply = (502, payload)
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert caught.value.status == 502 and "502" in str(caught.value)


def test_unreachable_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = Sidecar()
    url = dead.url
    dead.close()
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url=url, timeout=2)
    assert caught.value.code == "unreachable"


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"engine": "t"}', b'{"engine": "t", "lang": "eng"}'])
def test_malformed_success_responses_are_bad_response(sidecar: Sidecar, payload: bytes) -> None:
    sidecar.reply = (200, payload)
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert caught.value.code == "bad_response"


def test_result_round_trips_to_dict_and_tsv() -> None:
    result = ocr.OcrResult.from_dict(CANNED)
    assert result.to_dict() == CANNED
    assert json.loads(json.dumps(result.to_dict())) == CANNED
    assert result.to_tsv().splitlines() == [
        "line\tx\ty\tw\th\tconf\ttext", "0\t8\t8\t60\t16\t95\tDebian", "1\t8\t40\t60\t16\t90\tInstall"]
    # a missing engine_version is allowed (an unavailable engine reports none)
    assert ocr.OcrResult.from_dict({**CANNED, "engine_version": None}).engine_version is None


def test_tsv_neutralises_whitespace_inside_words() -> None:
    odd = {**CANNED, "words": [{"text": "a\tb\nc", "conf": 80.5, "box": [1, 2, 3, 4], "line": 0}]}
    row = ocr.OcrResult.from_dict(odd).to_tsv().splitlines()[1]
    assert row == "0\t1\t2\t3\t4\t80.5\ta b c"


# ------------------------------------------------------------------ CLI (--image)


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidecar: Sidecar) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "screen.png").write_bytes(PNG)
    (tmp_path / "contract.json").write_text(json.dumps(BASE_CONTRACT), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "verify_installed_playwright", lambda contract: "1.64.0")
    return tmp_path


def run(*argv: str) -> int:
    return cli_module.main(list(argv))


def test_cli_image_prints_only_the_text(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url) == 0
    captured = capsys.readouterr()
    assert captured.out == "Debian GNU/Linux installer\nInstall\n" and captured.err == ""
    assert sidecar.requests[0]["body"] == PNG


def test_cli_stdin(work: Path, sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(PNG)))
    assert run("ocr", "--image", "-", "--sidecar-url", sidecar.url) == 0
    assert "Debian" in capsys.readouterr().out and sidecar.requests[0]["body"] == PNG


def test_cli_empty_stdin_and_empty_or_missing_file(work: Path, sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b"")))
    assert run("ocr", "--image", "-", "--sidecar-url", sidecar.url) == 1
    assert "from standard input is empty" in capsys.readouterr().err
    (work / "empty.png").write_bytes(b"")
    assert run("ocr", "--image", "empty.png", "--sidecar-url", sidecar.url) == 1
    assert "is empty" in capsys.readouterr().err
    assert run("ocr", "--image", "absent.png", "--sidecar-url", sidecar.url) == 1
    assert "cannot read image" in capsys.readouterr().err
    assert sidecar.requests == []


def test_cli_global_json_prints_the_full_result(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--json") == 0
    assert json.loads(capsys.readouterr().out) == CANNED


def test_cli_format_json_works_without_the_global_flag(work: Path, sidecar: Sidecar,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["words"][0]["text"] == "Debian"


def test_cli_tsv(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--format", "tsv") == 0
    assert capsys.readouterr().out.splitlines()[1] == "0\t8\t8\t60\t16\t95\tDebian"


def test_cli_json_and_tsv_contradict(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--json", "--format", "tsv") == 2
    assert "contradict" in capsys.readouterr().err and sidecar.requests == []


def test_cli_output_file_in_each_format(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    for fmt, check in (("text", lambda t: t == "Debian GNU/Linux installer\nInstall\n"),
                       ("json", lambda t: json.loads(t) == CANNED),
                       ("tsv", lambda t: t.startswith("line\tx"))):
        assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--format", fmt,
                   "--output", f"out.{fmt}") == 0
        assert check((work / f"out.{fmt}").read_text(encoding="utf-8"))
        captured = capsys.readouterr()
        assert captured.out == "" and f"wrote {fmt} output to" in captured.err
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--quiet", "--output", "q.txt") == 0
    assert capsys.readouterr().err == ""  # --quiet silences the info line


def test_cli_output_write_failure(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--output", "missing-dir/o.txt") == 1
    assert "cannot write OCR output" in capsys.readouterr().err


def test_cli_forwards_recognition_options(work: Path, sidecar: Sidecar) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--engine", "tesseract", "--lang", "eng+deu",
               "--psm", "11", "--scale", "2", "--whitelist", "0-9", "--min-conf", "70", "--timeout-ms", "9000") == 0
    assert sidecar.requests[0]["query"] == {"engine": ["tesseract"], "lang": ["eng+deu"], "psm": ["11"], "scale": ["2"],
                                            "whitelist": ["0-9"], "min_conf": ["70"]}


def test_cli_rejects_a_psm_the_sidecar_does_not_offer(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--psm", "5") == 2
    assert sidecar.requests == []


def test_cli_sidecar_refusal_is_a_clean_failure(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    sidecar.reply = (501, json.dumps({"error": "engine not implemented", "code": "engine_unavailable"}).encode())
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--engine", "glyphgrid") == 1
    err = capsys.readouterr().err
    assert "engine_unavailable" in err and "engine not implemented" in err and "Traceback" not in err


def test_cli_url_comes_from_the_environment(work: Path, sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PWMCP_OCR_URL", sidecar.url)
    assert run("ocr", "--image", "screen.png", "--contract", str(work / "does-not-exist.json")) == 0


def test_cli_url_comes_from_the_contract(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    (work / "ocr-contract.json").write_text(
        json.dumps({**BASE_CONTRACT, "ocr": {"url": sidecar.url, "engines": ["tesseract"]}}), encoding="utf-8")
    assert run("ocr", "--image", "screen.png", "--contract", "ocr-contract.json") == 0
    assert "Debian" in capsys.readouterr().out


@pytest.mark.parametrize("contract_file", ["contract.json", "absent.json"])
def test_cli_without_any_sidecar_url_exits_2_and_names_ocr_enabled(
    work: Path, contract_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # old/disabled server (contract has no ocr block) and an unreachable contract both land here
    assert run("ocr", "--image", "screen.png", "--contract", contract_file) == 2
    err = capsys.readouterr().err
    assert "ocr.enabled" in err and "Traceback" not in err


def test_cli_usage_errors(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--sidecar-url", sidecar.url]
    cases = [
        (["ocr", *base], "exactly one of an image"),
        (["ocr", "https://example.test", "--image", "screen.png", *base, "--selector", "canvas"], "exactly one of an image"),
        (["ocr", "https://example.test", *base], "--selector is required"),
        (["ocr", "--image", "screen.png", "--selector", "canvas", *base], "apply only to a page URL"),
        (["ocr", "--image", "screen.png", "--region", "0,0,1,1", *base], "apply only to a page URL"),
        (["ocr", "--image", "screen.png", "--wait-ms", "5", *base], "apply only to a page URL"),
        (["ocr", "--image", "screen.png", "--timeout-ms", "0", *base], "--timeout-ms must be positive"),
        (["ocr", "--image", "screen.png", "--timeout-ms", "-5", *base], "--timeout-ms must be positive"),
        (["ocr", "https://example.test", "--selector", "c", "--wait-ms", "-1", *base], "must not be negative"),
    ]
    for argv, message in cases:
        assert run(*argv) == 2, argv
        assert message in capsys.readouterr().err, argv
    assert sidecar.requests == []


# ------------------------------------------------------------------ CLI (page element capture)


class Browser:
    def __init__(self) -> None:
        self.calls: dict[str, list] = {"goto": [], "screens": [], "waits": [], "ctx": [], "visible": [], "cdp": []}
        self.box: dict[str, float] | None = {"x": 10.0, "y": 20.0, "width": 640.0, "height": 400.0}
        self.fail_goto = False
        self.screenshot_bytes = b"element-png"
        self.contract = PwmcpContract.from_dict(BASE_CONTRACT)


def install(monkeypatch: pytest.MonkeyPatch, browser: Browser) -> None:
    calls = browser.calls

    class PlaywrightError(Exception):
        pass

    class Locator:
        def filter(self, *, visible: bool) -> "Locator":
            calls["visible"].append(visible)
            return self

        @property
        def first(self) -> "Locator":
            return self

        def wait_for(self, *, state: str, timeout: int) -> None:
            calls["visible"].append(state)

        def screenshot(self, **kwargs: object) -> bytes:
            calls["screens"].append(("element", kwargs))
            return browser.screenshot_bytes

        def scroll_into_view_if_needed(self, **_kwargs: object) -> None:
            calls["screens"].append(("scroll", {}))

        def bounding_box(self, **_kwargs: object) -> dict[str, float] | None:
            return browser.box

    class Page:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls["goto"].append((url, wait_until, timeout))
            if browser.fail_goto:
                raise PlaywrightError("navigation failed")

        def locator(self, selector: str) -> Locator:
            calls["selector"] = selector
            return Locator()

        def wait_for_timeout(self, ms: int) -> None:
            calls["waits"].append(ms)

        def screenshot(self, **kwargs: object) -> bytes:
            calls["screens"].append(("page", kwargs))
            return browser.screenshot_bytes

    class Cdp:
        def send(self, method: str, params: object) -> None:
            calls["cdp"].append(method)

    class Context:
        def new_page(self) -> Page:
            return Page()

        def new_cdp_session(self, _page: object) -> Cdp:
            return Cdp()

        def close(self) -> None:
            calls["closed"] = True

    class Chromium:
        def new_context(self, **options: object) -> Context:
            calls["ctx"].append(options)
            return Context()

    class Lease:
        def __init__(self) -> None:
            self.browser = Chromium()
            self.contract = browser.contract

        def __enter__(self) -> "Lease":
            return self

        def __exit__(self, *_a: object) -> None:
            calls["lease_closed"] = True

    class LeaseFactory:
        @classmethod
        def connect(cls, **kwargs: object) -> Lease:
            calls["connect"] = kwargs
            return Lease()

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = PlaywrightError
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(cli_module, "BrowserLease", LeaseFactory)


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Browser:
    instance = Browser()
    install(monkeypatch, instance)
    return instance


URL = "https://console.example.test/vnc"


def test_element_capture_sends_the_element_png(work: Path, sidecar: Sidecar, browser: Browser,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url, "--timeout-ms", "7000") == 0
    assert "Debian" in capsys.readouterr().out
    assert sidecar.requests[0]["body"] == b"element-png"
    calls = browser.calls
    assert calls["goto"] == [(URL, "load", 7000)] and calls["selector"] == "canvas"
    assert calls["ctx"] == [{"ignore_https_errors": False}]  # fresh context, no UA override by default
    assert calls["screens"] == [("element", {"type": "png", "timeout": 7000})]
    assert calls["closed"] and calls["lease_closed"] and calls["waits"] == []
    assert calls["connect"]["label"] == "pwmcp-ocr"


def test_element_capture_wait_and_https_option(work: Path, sidecar: Sidecar, browser: Browser) -> None:
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url, "--wait-ms", "1500",
               "--ignore-https-errors") == 0
    assert browser.calls["waits"] == [1500] and browser.calls["ctx"] == [{"ignore_https_errors": True}]


def test_region_clip_is_relative_to_the_element(work: Path, sidecar: Sidecar, browser: Browser) -> None:
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url, "--region", " 5, 6,100,50 ") == 0
    kind = [c[0] for c in browser.calls["screens"]]
    assert kind == ["scroll", "page"]
    assert browser.calls["screens"][1][1] == {"type": "png", "timeout": 30000,
                                              "clip": {"x": 15.0, "y": 26.0, "width": 100, "height": 50}}


@pytest.mark.parametrize("region", ["1,2,3", "a,b,c,d", "0,0,0,5", "0,0,5,0", "-1,0,5,5", "1,2,3,4,5"])
def test_malformed_region_is_a_usage_error(work: Path, sidecar: Sidecar, browser: Browser, region: str,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url, "--region", region) == 2
    assert "--region must be x,y,w,h" in capsys.readouterr().err and browser.calls["goto"] == []


@pytest.mark.parametrize("box", [None, {"x": 0.0, "y": 0.0, "width": 50.0, "height": 400.0},
                                 {"x": 0.0, "y": 0.0, "width": 640.0, "height": 40.0}])
def test_region_outside_the_element_is_refused(work: Path, sidecar: Sidecar, browser: Browser,
                                               box: dict | None, capsys: pytest.CaptureFixture[str]) -> None:
    browser.box = box
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url, "--region", "0,0,100,100") == 2
    assert "does not fit inside the element" in capsys.readouterr().err
    assert sidecar.requests == [] and browser.calls["closed"]  # the context is still closed


@pytest.mark.parametrize("url", ["ftp://x/y", "console.example.test", "http://", "http://[::1"])
def test_bad_page_urls_are_usage_errors(work: Path, sidecar: Sidecar, browser: Browser, url: str,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", url, "--selector", "canvas", "--sidecar-url", sidecar.url) == 2
    assert "URL" in capsys.readouterr().err and browser.calls["goto"] == []


def test_navigation_failure_is_a_clean_capture_error(work: Path, sidecar: Sidecar, browser: Browser,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    browser.fail_goto = True
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url) == 1
    err = capsys.readouterr().err
    assert "could not capture the element" in err and "navigation failed" in err and sidecar.requests == []
    assert browser.calls["closed"]


def test_server_level_user_agent_is_applied_to_the_capture_context(work: Path, sidecar: Sidecar, browser: Browser) -> None:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    browser.contract = PwmcpContract.from_dict({**BASE_CONTRACT, "user_agent": {"value": ua, "source": "preset:chrome-windows"}})
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url) == 0
    assert browser.calls["ctx"][0]["user_agent"] == ua and browser.calls["cdp"] == ["Emulation.setUserAgentOverride"]


def test_missing_playwright_package_is_reported(work: Path, sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert run("ocr", URL, "--selector", "canvas", "--sidecar-url", sidecar.url) == 1
    assert "Playwright Python package is not installed" in capsys.readouterr().err


def test_sidecar_is_resolved_before_any_browser_is_opened(work: Path, browser: Browser, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", URL, "--selector", "canvas", "--contract", "contract.json") == 2
    assert "ocr.enabled" in capsys.readouterr().err and browser.calls["goto"] == []


def test_ocr_verb_is_listed_in_the_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--help") == 0
    out = capsys.readouterr().out
    assert "--sidecar-url" in out and "--image" in out and "pwmcp ocr --image console.png" in out
