"""PWMCP-11 phase 1: the OCR client library, contract fields and the ``pwmcp ocr`` CLI, offline.

A local urllib-compatible fake sidecar stands in for the real service; a fake browser stands
in for the remote one. Nothing here proves the real sidecar's recognition quality.
"""

from __future__ import annotations

import http.client
import http.server
import io
import json
import socket
import struct
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
    "lines": [{"text": "Debian GNU/Linux installer", "conf": 93.5, "conf_mean": 93.5, "conf_min": 91.0,
               "low_conf_words": None, "box": [8, 8, 300, 16]},
              {"text": "Install", "conf": 90.0, "conf_mean": 90.0, "conf_min": 90.0, "low_conf_words": None,
               "box": [8, 40, 60, 16]}],
    "words": [{"text": "Debian", "conf": 95.0, "box": [8, 8, 60, 16], "line": 0, "low_conf": None},
              {"text": "Install", "conf": 90.0, "box": [8, 40, 60, 16], "line": 1, "low_conf": None}],
    # no min_conf was given: nothing was assessed, so these are null (not false/0)
    "low_conf_words": None, "min_conf_applied": None, "drop_low_conf": False, "dropped_words": 0,
    "unscored_words": 0,
}
CANNED_DICT = {**CANNED, "completeness_unknown": False}  # what OcrResult.to_dict() adds
# What a sidecar from before the low_conf contract answered: still understood (defaults fill in).
OLD_SHAPE = {
    "engine": "tesseract", "engine_version": "5.3.4", "lang": "eng", "psm": 6, "width": 640, "height": 400,
    "elapsed_ms": 12, "text": "Install",
    "lines": [{"text": "Install", "conf": 90.0, "box": [8, 40, 60, 16]}],
    "words": [{"text": "Install", "conf": 90.0, "box": [8, 40, 60, 16], "line": 0}],
}


class Sidecar:
    """A scriptable fake sidecar: records every request, replies with a queued/default answer."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.reply: tuple[int, bytes] = (200, json.dumps(CANNED).encode())
        self.reply_headers: dict[str, str] = {}
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
                for key, value in outer.reply_headers.items():
                    self.send_header(key, value)
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
    assert result.lines[0] == ocr.OcrLine("Debian GNU/Linux installer", 93.5, (8, 8, 300, 16), conf_mean=93.5, conf_min=91.0, low_conf_words=None)
    assert result.words[1] == ocr.OcrWord("Install", 90.0, (8, 40, 60, 16), 1, None)
    assert (result.low_conf_words, result.min_conf_applied, result.completeness_unknown) == (None, None, False)


def test_recognize_sends_defaults_and_the_image_untouched(sidecar: Sidecar) -> None:
    ocr.recognize(PNG, sidecar_url=sidecar.url + "/")
    (request,) = sidecar.requests
    assert request["path"] == "/v1/ocr" and request["body"] == PNG and request["content_type"] == "image/png"
    # no min_conf unless one was asked for: the sidecar must not be told to assess against an invented threshold
    assert request["query"] == {"engine": ["tesseract"], "lang": ["eng"], "psm": ["6"], "scale": ["1"]}


def test_recognize_sends_every_option(sidecar: Sidecar) -> None:
    ocr.recognize(b"\xff\xd8jpeg", sidecar_url=sidecar.url, engine="glyphgrid", lang="eng+deu", psm=7, scale=3,
                  whitelist=";rm -rf & 09", min_conf=61.5, drop_low_conf=True)
    (request,) = sidecar.requests
    assert request["content_type"] == "image/jpeg"
    assert request["query"] == {"engine": ["glyphgrid"], "lang": ["eng+deu"], "psm": ["7"], "scale": ["3"],
                                "min_conf": ["61.5"], "whitelist": [";rm -rf & 09"],
                                "drop_low_conf": ["1"]}
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
    assert result.to_dict() == CANNED_DICT
    assert json.loads(json.dumps(result.to_dict())) == CANNED_DICT
    assert result.to_tsv().splitlines() == [
        "line\tx\ty\tw\th\tconf\tlow_conf\ttext", "0\t8\t8\t60\t16\t95\t\tDebian", "1\t8\t40\t60\t16\t90\t\tInstall"]  # empty low_conf: not assessed
    # a missing engine_version is allowed (an unavailable engine reports none)
    assert ocr.OcrResult.from_dict({**CANNED, "engine_version": None}).engine_version is None


def test_an_older_sidecar_without_the_low_conf_fields_is_still_understood() -> None:
    result = ocr.OcrResult.from_dict(OLD_SHAPE)
    assert result.lines[0].conf_mean == result.lines[0].conf == result.lines[0].conf_min == 90.0
    assert result.lines[0].low_conf_words is None and result.words[0].low_conf is None
    assert (result.low_conf_words, result.min_conf_applied, result.drop_low_conf, result.dropped_words) == (None, None, False, 0)
    # ... but it is marked: such a sidecar may have silently DROPPED words below a min_conf
    assert result.completeness_unknown is True


def test_low_confidence_words_are_flagged_in_the_result_and_the_tsv() -> None:
    doubtful = {**CANNED, "text": "Continue? [y/N]", "low_conf_words": 1, "min_conf_applied": 50.0,
                "lines": [{"text": "Continue? [y/N]", "conf": 53.0, "conf_mean": 53.0, "conf_min": 10.0,
                           "low_conf_words": 1, "box": [0, 0, 130, 16]}],
                "words": [{"text": "Continue?", "conf": 96.0, "box": [0, 0, 60, 16], "line": 0, "low_conf": False},
                          {"text": "[y/N]", "conf": 10.0, "box": [70, 0, 60, 16], "line": 0, "low_conf": True}]}
    result = ocr.OcrResult.from_dict(doubtful)
    assert result.text == "Continue? [y/N]" and result.low_conf_words == 1
    assert [w.low_conf for w in result.words] == [False, True]
    assert result.lines[0].conf_min == 10.0 and result.lines[0].low_conf_words == 1
    assert result.to_tsv().splitlines()[2].split("\t")[-2:] == ["1", "[y/N]"]


def test_tsv_neutralises_whitespace_inside_words() -> None:
    odd = {**CANNED, "words": [{"text": "a\tb\nc", "conf": 80.5, "box": [1, 2, 3, 4], "line": 0}]}
    row = ocr.OcrResult.from_dict(odd).to_tsv().splitlines()[1]
    assert row == "0\t1\t2\t3\t4\t80.5\t\ta b c"


# ------------------------------------------------------------------ review follow-up


@pytest.mark.parametrize("url", ["http://user:s3cret@host:1", "http://:s3cret@host:1/x", "https://user@host", "http://a@b@host"])
def test_credentials_in_the_sidecar_url_are_refused_without_echoing_them(url: str) -> None:
    with pytest.raises(ocr.OcrError) as caught:
        ocr.resolve_sidecar_url(url)
    assert caught.value.code == "bad_url" and "credentials" in str(caught.value)
    assert "s3cret" not in str(caught.value) and "user:" not in str(caught.value)
    with pytest.raises(ocr.OcrError):
        ocr.recognize(PNG, sidecar_url=url)


def test_cli_refuses_credentials_in_the_url_with_exit_2(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", "http://bob:s3cret@127.0.0.1:9") == 2
    err = capsys.readouterr().err
    assert "credentials" in err and "s3cret" not in err and "bob" not in err


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("ftp://host:9/p?token=abc#frag", "ftp://host:9/p"),
        ("ftp://[::1]:9/p", "ftp://[::1]:9/p"),
        ("ftp://host", "ftp://host"),
        ("http://", "http:<no host>"),
        ("http://[::1", "<malformed url>"),
    ],
)
def test_urls_are_redacted_in_messages(url: str, shown: str) -> None:
    assert ocr._redact(url) == shown
    with pytest.raises(ocr.OcrError) as caught:
        ocr.resolve_sidecar_url(url)
    assert "token" not in str(caught.value) and "frag" not in str(caught.value)


def test_a_redirect_is_an_error_and_the_image_is_never_sent_elsewhere(sidecar: Sidecar) -> None:
    elsewhere = Sidecar()
    try:
        sidecar.reply = (307, b"")
        sidecar.reply_headers = {"Location": elsewhere.url + "/v1/ocr"}
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=sidecar.url)
        assert (caught.value.code, caught.value.status) == ("redirect", 307)
        assert "never followed" in str(caught.value)
        assert elsewhere.requests == [] and len(sidecar.requests) == 1
        sidecar.reply = (302, b"")
        with pytest.raises(ocr.OcrError) as again:
            ocr.recognize(PNG, sidecar_url=sidecar.url)
        assert again.value.code == "redirect" and elsewhere.requests == []
    finally:
        elsewhere.close()


def test_redirect_through_the_cli_is_a_clean_failure(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    sidecar.reply = (301, b"")
    sidecar.reply_headers = {"Location": "http://evil.invalid/"}
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url) == 1
    assert "redirect" in capsys.readouterr().err


class RawServer:
    """A TCP server whose one connection is handled by ``behave(conn)``: for failure modes HTTP servers avoid."""

    def __init__(self, behave) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.done = threading.Event()

        def run() -> None:
            try:
                conn, _ = self.listener.accept()
                with conn:
                    behave(conn)
            except OSError:
                pass
            finally:
                self.done.set()

        threading.Thread(target=run, daemon=True).start()

    def close(self) -> None:
        self.listener.close()


def _read_headers(conn: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


def _read_request(conn: socket.socket) -> None:
    """Take the whole request (headers + body) so closing later does not RST away our reply."""

    data = _read_headers(conn)
    length = int(data.lower().split(b"content-length: ")[1].split(b"\r\n")[0])
    have = len(data.split(b"\r\n\r\n", 1)[1])
    while have < length:
        have += len(conn.recv(65536))


def _reset(conn: socket.socket) -> None:
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close() sends RST


def test_a_hang_up_during_the_upload_reads_as_busy_or_rejected_not_unreachable() -> None:
    def behave(conn: socket.socket) -> None:
        _read_headers(conn)
        _reset(conn)  # slam the door while the client is still sending its body

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(b"\x89PNG\r\n\x1a\n" + b"x" * 30_000_000, sidecar_url=server.url, timeout=20)
        assert caught.value.code == "busy_or_rejected"
        assert "busy or rejected" in str(caught.value) and "retry" in str(caught.value)
        assert "unreachable" not in str(caught.value)
    finally:
        server.close()


@pytest.mark.parametrize("how", ["close", "reset"])
def test_a_hang_up_after_the_full_upload_is_a_sidecar_error_not_busy(how: str) -> None:
    def behave(conn: socket.socket) -> None:
        data = _read_headers(conn)
        length = int(data.lower().split(b"content-length: ")[1].split(b"\r\n")[0])
        have = len(data.split(b"\r\n\r\n", 1)[1])
        while have < length:  # take the WHOLE body, then drop the connection without answering
            have += len(conn.recv(65536))
        if how == "reset":
            _reset(conn)

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=10)
        assert caught.value.code == "sidecar_error", caught.value
        assert "busy or rejected" not in str(caught.value) and "check its logs" in str(caught.value)
    finally:
        server.close()


def test_a_truncated_answer_is_a_sidecar_error() -> None:
    def behave(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 500\r\n\r\n{\"engine\": ")

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=10)
        assert caught.value.code == "sidecar_error"
    finally:
        server.close()


def test_an_error_reply_whose_body_cannot_be_read_still_reports_the_status() -> None:
    def behave(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.0 500 Internal Server Error\r\nContent-Length: 500\r\n\r\n{\"er")

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=10)
        assert caught.value.code == "sidecar_error"  # the truncated body is the more specific fact
    finally:
        server.close()


def test_a_sidecar_that_never_answers_times_out_with_its_own_code() -> None:
    release = threading.Event()

    def behave(conn: socket.socket) -> None:
        _read_headers(conn)
        release.wait(10)

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=0.3)
        assert caught.value.code == "timeout" and "did not answer in time" in str(caught.value)
    finally:
        release.set()
        server.close()


def test_a_malformed_status_line_is_a_sidecar_error() -> None:
    def behave(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"NOT HTTP AT ALL\r\n\r\n")

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=10)
        assert caught.value.code == "sidecar_error"
    finally:
        server.close()


def test_an_oversized_answer_is_refused(monkeypatch: pytest.MonkeyPatch, sidecar: Sidecar) -> None:
    monkeypatch.setattr(ocr, "MAX_RESPONSE_BYTES", 10)
    sidecar.reply = (200, json.dumps(CANNED).encode())
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert caught.value.code == "bad_response" and "oversized" in str(caught.value)


def test_a_close_delimited_answer_is_read_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    def behave(conn: socket.socket) -> None:
        _read_request(conn)
        conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n" + json.dumps(CANNED).encode())  # no Content-Length

    server = RawServer(behave)
    try:
        assert ocr.recognize(PNG, sidecar_url=server.url, timeout=10).engine == "tesseract"
    finally:
        server.close()
    monkeypatch.setattr(ocr, "MAX_RESPONSE_BYTES", 10)
    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(PNG, sidecar_url=server.url, timeout=10)
        assert caught.value.code == "bad_response" and "oversized" in str(caught.value)
    finally:
        server.close()


def test_a_path_prefix_in_the_sidecar_url_is_kept(sidecar: Sidecar) -> None:
    ocr.recognize(PNG, sidecar_url=sidecar.url + "/ocr/")
    assert sidecar.requests[0]["path"] == "/ocr/v1/ocr"


def test_dns_failure_and_refusal_stay_unreachable() -> None:
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url="http://no-such-host.invalid:9", timeout=5)
    assert caught.value.code == "unreachable" and "connect phase" in str(caught.value)


def test_a_slow_upload_that_times_out_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def behave(conn: socket.socket) -> None:
        release.wait(10)  # never reads: the client's send buffer fills

    server = RawServer(behave)
    try:
        with pytest.raises(ocr.OcrError) as caught:
            ocr.recognize(b"\x89PNG\r\n\x1a\n" + b"x" * 60_000_000, sidecar_url=server.url, timeout=0.5)
        assert caught.value.code == "timeout" and "upload phase" in str(caught.value)
    finally:
        release.set()
        server.close()


def test_https_urls_use_the_tls_connection_class(monkeypatch: pytest.MonkeyPatch) -> None:
    used = []

    class Fake:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            self.host, self.port = host, port

        def connect(self) -> None:
            used.append((self.host, self.port))
            raise ConnectionRefusedError(111, "refused")

        def close(self) -> None:
            return None

    monkeypatch.setattr(http.client, "HTTPSConnection", Fake)
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(PNG, sidecar_url="https://sidecar.example:8443")
    assert used == [("sidecar.example", 8443)] and caught.value.code == "unreachable"


# ------------------------------------------------------------------ review round 4


@pytest.mark.parametrize(
    "url",
    ["http://host:99999", "http://host:abc", "http://host:-1", "http://host/a b", "http://host/a\tb",
     "http://host/a\x00b", "http://host/base?x=1", "http://host/base#frag", "http://host/base?", "http://host:70000/x"],
)
def test_malformed_sidecar_urls_are_an_ocr_error_not_a_stray_exception(url: str) -> None:
    with pytest.raises(ocr.OcrError) as caught:
        ocr.resolve_sidecar_url(url)
    assert caught.value.code == "bad_url"
    with pytest.raises(ocr.OcrError):
        ocr.recognize(PNG, sidecar_url=url)


def test_a_bad_sidecar_url_through_the_cli_exits_2(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", "http://host:99999/x?token=secret") == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "secret" not in err and "token" not in err


def test_a_valid_sidecar_url_with_a_port_and_path_still_works(sidecar: Sidecar) -> None:
    assert ocr.resolve_sidecar_url("http://host:8941/base/") == "http://host:8941/base"
    assert ocr.resolve_sidecar_url("http://[::1]:8941") == "http://[::1]:8941"


def test_a_sidecar_that_predates_the_contract_is_flagged_incomplete_and_the_cli_warns(
    work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]
) -> None:
    sidecar.reply = (200, json.dumps(OLD_SHAPE).encode())
    assert ocr.recognize(PNG, sidecar_url=sidecar.url, min_conf=50).completeness_unknown is True
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--min-conf", "50") == 0
    assert "predates the low_conf contract" in capsys.readouterr().err
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url) == 0  # no threshold asked: nothing to warn about
    assert capsys.readouterr().err == ""


def test_an_unscored_word_round_trips_as_null() -> None:
    value = {**CANNED, "unscored_words": 1, "min_conf_applied": 50.0, "low_conf_words": 1,
             "words": [{"text": "TRUE", "conf": None, "box": [0, 0, 5, 5], "line": 0, "low_conf": True}],
             "lines": [{"text": "TRUE", "conf": None, "conf_mean": None, "conf_min": None, "low_conf_words": 1,
                        "box": [0, 0, 5, 5]}]}
    result = ocr.OcrResult.from_dict(value)
    assert result.words[0].conf is None and result.lines[0].conf_mean is None and result.unscored_words == 1
    assert result.to_tsv().splitlines()[1] == "0\t0\t0\t5\t5\t\t1\tTRUE"  # empty conf, flagged 1


def test_cli_min_conf_is_only_sent_when_given(work: Path, sidecar: Sidecar) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url) == 0
    assert "min_conf" not in sidecar.requests[0]["query"]
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--min-conf", "0") == 0
    assert sidecar.requests[1]["query"]["min_conf"] == ["0"]  # an explicit 0 IS a threshold


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
    assert json.loads(capsys.readouterr().out) == CANNED_DICT


def test_cli_format_json_works_without_the_global_flag(work: Path, sidecar: Sidecar,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["words"][0]["text"] == "Debian"


def test_cli_tsv(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--format", "tsv") == 0
    assert capsys.readouterr().out.splitlines()[1] == "0\t8\t8\t60\t16\t95\t\tDebian"


def test_cli_json_and_tsv_contradict(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--json", "--format", "tsv") == 2
    assert "contradict" in capsys.readouterr().err and sidecar.requests == []


def test_cli_output_file_in_each_format(work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]) -> None:
    for fmt, check in (("text", lambda t: t == "Debian GNU/Linux installer\nInstall\n"),
                       ("json", lambda t: json.loads(t) == CANNED_DICT),
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


def test_cli_a_server_that_does_not_advertise_ocr_exits_2_and_names_ocr_enabled(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("ocr", "--image", "screen.png", "--contract", "contract.json") == 2
    err = capsys.readouterr().err
    assert "ocr.enabled" in err and "Traceback" not in err and "cannot reach" not in err


def test_cli_an_unreachable_server_is_reported_as_such_not_as_ocr_not_enabled(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("ocr", "--image", "screen.png", "--contract", "absent.json") == 1
    err = capsys.readouterr().err
    assert "cannot reach the pwmcp server" in err and "absent.json" in err  # the underlying problem is shown
    assert "--sidecar-url" in err and "ocr.enabled" not in err.split("Hint")[0]


def test_cli_low_confidence_words_are_included_flagged_and_warned_about(
    work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]
) -> None:
    sidecar.reply = (200, json.dumps({
        **CANNED, "text": "Continue? [y/N]", "low_conf_words": 1, "min_conf_applied": 50.0,
        "lines": [{"text": "Continue? [y/N]", "conf": 53.0, "conf_mean": 53.0, "conf_min": 10.0,
                   "low_conf_words": 1, "box": [0, 0, 130, 16]}],
        "words": [{"text": "Continue?", "conf": 96.0, "box": [0, 0, 60, 16], "line": 0, "low_conf": False},
                  {"text": "[y/N]", "conf": 10.0, "box": [70, 0, 60, 16], "line": 0, "low_conf": True}],
    }).encode())
    args = ("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--min-conf", "50")
    assert run(*args) == 0
    captured = capsys.readouterr()
    assert captured.out == "Continue? [y/N]\n"  # complete: the doubtful token is still there
    assert "1 word(s) are below --min-conf 50" in captured.err
    assert run(*args, "--format", "tsv") == 0
    assert capsys.readouterr().out.splitlines()[2].split("\t")[-2:] == ["1", "[y/N]"]
    assert run(*args, "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["low_conf_words"] == 1 and out["words"][1]["low_conf"] is True


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


# ------------------------------------------------------------------ review round 5


def test_flags_without_a_threshold_are_refused_not_guessed() -> None:
    inconsistent = {**CANNED, "low_conf_words": 2, "min_conf_applied": None}
    with pytest.raises(ocr.OcrError) as caught:
        ocr.OcrResult.from_dict(inconsistent)
    assert caught.value.code == "bad_response" and "min_conf_applied is null" in str(caught.value)
    words = {**CANNED, "words": [{"text": "x", "conf": 5.0, "box": [0, 0, 1, 1], "line": 0, "low_conf": True}]}
    with pytest.raises(ocr.OcrError):
        ocr.OcrResult.from_dict(words)


def test_the_cli_reports_an_inconsistent_sidecar_cleanly_not_with_a_traceback(
    work: Path, sidecar: Sidecar, capsys: pytest.CaptureFixture[str]
) -> None:
    sidecar.reply = (200, json.dumps({**CANNED, "low_conf_words": 3, "min_conf_applied": None}).encode())
    assert run("ocr", "--image", "screen.png", "--sidecar-url", sidecar.url, "--min-conf", "50") == 1
    err = capsys.readouterr().err
    assert "unexpected response" in err and "Traceback" not in err and "TypeError" not in err


PREVIOUS_ROUND = {  # default min_conf 0, answers "assessed at 0, nothing below it" whatever the caller asked
    **CANNED, "min_conf_applied": 0.0, "low_conf_words": 0,
    "lines": [{**line, "low_conf_words": 0} for line in CANNED["lines"]],
    "words": [{**word, "low_conf": False} for word in CANNED["words"]],
}


def test_a_previous_round_sidecar_answer_is_not_taken_as_an_assessment_when_none_was_asked(sidecar: Sidecar) -> None:
    sidecar.reply = (200, json.dumps(PREVIOUS_ROUND).encode())
    unasked = ocr.recognize(PNG, sidecar_url=sidecar.url)
    assert unasked.completeness_unknown is True and unasked.low_conf_words == 0
    asked = ocr.recognize(PNG, sidecar_url=sidecar.url, min_conf=0)  # an explicit 0 really is an assessment
    assert asked.completeness_unknown is False
    nonzero = {**PREVIOUS_ROUND, "low_conf_words": 1}
    sidecar.reply = (200, json.dumps(nonzero).encode())
    assert ocr.recognize(PNG, sidecar_url=sidecar.url).completeness_unknown is False  # it flagged something: not that shape
    sidecar.reply = (200, json.dumps(CANNED).encode())
    assert ocr.recognize(PNG, sidecar_url=sidecar.url).completeness_unknown is False  # the current contract: nulls
