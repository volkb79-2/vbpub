from __future__ import annotations

import json
import urllib.error

import pytest

from conftest import FakeHTTPResponse, make_http_error

import telegram_client as tc
from telegram_client import TelegramClient


@pytest.fixture()
def client(monkeypatch):
    # _get_system_id() shells out to `hostname -I` - harmless/read-only, let
    # it run for real, but pin bot_token/chat_id so __init__ doesn't need env.
    return TelegramClient(bot_token="123:abc", chat_id="456")


# --- _encode_multipart / _request: the new internal HTTP layer -----------

def test_encode_multipart_contains_field_and_file_parts():
    body, content_type = tc._encode_multipart(
        {"chat_id": "456"}, {"document": ("report.txt", b"hello world")}
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=")[1]
    text = body.decode("utf-8", errors="replace")
    assert f"--{boundary}" in text
    assert 'name="chat_id"' in text
    assert 'name="document"; filename="report.txt"' in text
    assert "hello world" in text
    assert text.rstrip().endswith(f"--{boundary}--")


def test_request_get_success(monkeypatch):
    monkeypatch.setattr(
        tc.urllib.request, "urlopen",
        lambda req, timeout=10: FakeHTTPResponse(json.dumps({"ok": True}).encode()),
    )
    status, payload, raw = tc._request("GET", "https://api.telegram.org/botX/getMe")
    assert status == 200
    assert payload == {"ok": True}


def test_request_http_error_returns_status_not_raise(monkeypatch):
    monkeypatch.setattr(
        tc.urllib.request, "urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(make_http_error(400, b'{"ok": false, "description": "bad"}')),
    )
    status, payload, raw = tc._request("POST", "https://api.telegram.org/botX/sendMessage", data={"a": "b"})
    assert status == 400
    assert payload == {"ok": False, "description": "bad"}


def test_request_connection_error_raises_connection_error(monkeypatch):
    monkeypatch.setattr(
        tc.urllib.request, "urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.URLError(OSError("refused"))),
    )
    with pytest.raises(ConnectionError):
        tc._request("GET", "https://api.telegram.org/botX/getMe")


def test_request_timeout_raises_timeout_error(monkeypatch):
    monkeypatch.setattr(
        tc.urllib.request, "urlopen",
        lambda req, timeout=10: (_ for _ in ()).throw(urllib.error.URLError(TimeoutError("timed out"))),
    )
    with pytest.raises(TimeoutError):
        tc._request("GET", "https://api.telegram.org/botX/getMe")


# --- send_message ----------------------------------------------------------

def test_send_message_happy_path(client, monkeypatch):
    monkeypatch.setattr(tc, "_request", lambda method, url, **kw: (200, {"ok": True}, ""))
    assert client.send_message("hello") is True


def test_send_message_prefixes_source_by_default(client, monkeypatch):
    captured = {}

    def fake_request(method, url, **kw):
        captured.update(kw)
        return (200, {"ok": True}, "")

    monkeypatch.setattr(tc, "_request", fake_request)
    client.send_message("hello")
    assert client.system_id in captured["data"]["text"]
    assert "hello" in captured["data"]["text"]


def test_send_message_retries_on_connection_error_then_succeeds(client, monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("refused")
        return (200, {"ok": True}, "")

    monkeypatch.setattr(tc, "_request", fake_request)
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert client.send_message("hello", max_retries=3) is True
    assert calls["n"] == 2


def test_send_message_gives_up_after_max_retries(client, monkeypatch):
    monkeypatch.setattr(tc, "_request", lambda method, url, **kw: (_ for _ in ()).throw(ConnectionError("refused")))
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert client.send_message("hello", max_retries=2) is False


def test_send_message_does_not_retry_http_error(client, monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        return (400, {"ok": False, "description": "bad request"}, '{"ok": false}')

    monkeypatch.setattr(tc, "_request", fake_request)
    assert client.send_message("hello", max_retries=3) is False
    assert calls["n"] == 1  # matches original: HTTP error is not retried


# --- send_document ----------------------------------------------------------

def test_send_document_missing_file_returns_false(client):
    assert client.send_document("/nonexistent/path.txt") is False


def test_send_document_happy_path(client, monkeypatch, tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("hello report")
    captured = {}

    def fake_request(method, url, **kw):
        captured.update(kw)
        return (200, {"ok": True}, "")

    monkeypatch.setattr(tc, "_request", fake_request)
    assert client.send_document(str(path), caption="a report") is True
    assert "document" in captured["files"]
    assert captured["files"]["document"][1] == b"hello report"


# --- create_forum_topic -----------------------------------------------------

def test_create_forum_topic_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        tc, "_request",
        lambda method, url, **kw: (200, {"ok": True, "result": {"message_thread_id": 42}}, ""),
    )
    assert client.create_forum_topic("Install: v1001") == 42


def test_create_forum_topic_returns_none_when_not_ok(client, monkeypatch):
    monkeypatch.setattr(tc, "_request", lambda method, url, **kw: (200, {"ok": False}, ""))
    assert client.create_forum_topic("Install: v1001") is None


# --- test_connection ---------------------------------------------------------

def test_connection_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        tc, "_request",
        lambda method, url, **kw: (200, {"ok": True, "result": {"username": "vbpub_bot", "first_name": "vbpub"}}, ""),
    )
    assert client.test_connection() is True


def test_connection_network_failure(client, monkeypatch):
    monkeypatch.setattr(tc, "_request", lambda method, url, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    assert client.test_connection() is False


# --- send_media_group -------------------------------------------------------

def test_send_media_group_happy_path(client, monkeypatch, tmp_path):
    files = []
    for i in range(2):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(b"fake-png-bytes")
        files.append(str(p))

    monkeypatch.setattr(tc, "_request", lambda method, url, **kw: (200, {"ok": True}, ""))
    assert client.send_media_group(files, caption="album") is True


def test_send_media_group_no_files_exist_returns_false(client):
    assert client.send_media_group(["/no/such/a.png", "/no/such/b.png"]) is False


def test_send_media_group_retries_after_rate_limit(client, monkeypatch, tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"fake")
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return (429, {"ok": False, "parameters": {"retry_after": 1}}, "")
        return (200, {"ok": True}, "")

    monkeypatch.setattr(tc, "_request", fake_request)
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert client.send_media_group([str(p)], max_retries=2) is True
    assert calls["n"] == 2
