"""Unit tests for the ctl wire client (cgprofile.ctlwire)."""

from __future__ import annotations

import json
import os
import socket
import tempfile

import pytest

import _fakectl
from cgprofile import ctlwire
from cgprofile.ctlwire import WireFailure


@pytest.fixture()
def sockdir():
    with tempfile.TemporaryDirectory(prefix="cgw") as directory:
        yield directory


@pytest.fixture()
def serve(sockdir):
    started = []

    def make(handlers):
        daemon = _fakectl.FakeDaemon(os.path.join(sockdir, "c.sock"), handlers)
        started.append(daemon)
        return daemon

    yield make
    for daemon in started:
        daemon.close()


class FakeSock:
    """A client socket double: scripted recv chunks, optional send/recv errors."""

    def __init__(self, chunks=(), send_error=None, recv_error=None):
        self.chunks = list(chunks)
        self.sent = b""
        self.send_error = send_error
        self.recv_error = recv_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sendall(self, data):
        if self.send_error:
            raise self.send_error
        self.sent += data

    def recv(self, size):
        if self.recv_error:
            raise self.recv_error
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


# ── socket selection ─────────────────────────────────────────────────────────

def test_resolve_socket_precedence():
    assert ctlwire.resolve_socket("/a", {"CGPROFILE_CONTROL_SOCKET": "/b"}) == ("/a", "flag")
    assert ctlwire.resolve_socket(None, {"CGPROFILE_CONTROL_SOCKET": "/b"}) == ("/b", "env")


def test_resolve_socket_empty_env_is_a_usage_error():
    with pytest.raises(WireFailure) as caught:
        ctlwire.resolve_socket(None, {"CGPROFILE_CONTROL_SOCKET": ""})
    assert caught.value.exit_code == 2
    assert caught.value.code == "endpoint-unset"
    assert "CGPROFILE_CONTROL_SOCKET" in caught.value.message


def test_resolve_socket_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", "/from/env")
    assert ctlwire.resolve_socket(None) == ("/from/env", "env")


def test_socket_not_mounted_remedy_uses_the_endpoint_directory_variable():
    assert "CGPROFILE_CONTROL_SOCKET_DIR" in ctlwire.CAUSE_REMEDY["socket-not-mounted"]
    assert "/run/cgprofile" not in ctlwire.CAUSE_REMEDY["socket-not-mounted"]


# ── cause tags ───────────────────────────────────────────────────────────────

def test_classify_every_cause(sockdir):
    inside = os.path.join(sockdir, "none.sock")
    assert ctlwire.classify(inside, FileNotFoundError())[0] == "socket-missing"
    assert ctlwire.classify(os.path.join(sockdir, "gone", "x.sock"), FileNotFoundError())[0] == "socket-not-mounted"
    assert ctlwire.classify("bare.sock", FileNotFoundError())[0] == "socket-missing"
    assert ctlwire.classify(inside, ConnectionRefusedError())[0] == "daemon-refusing"
    assert ctlwire.classify(inside, PermissionError())[0] == "socket-permission"
    tag, why = ctlwire.classify(inside, OSError("odd"))
    assert tag == "daemon-refusing" and "odd" in why


def test_connect_to_a_missing_socket_is_exit_3(sockdir):
    with pytest.raises(WireFailure) as caught:
        ctlwire.roundtrip(os.path.join(sockdir, "absent.sock"), "version")
    assert (caught.value.exit_code, caught.value.cause, caught.value.code) == (3, "socket-missing", "unreachable")


def test_connect_to_a_bound_but_unlistened_socket_is_refusing(sockdir):
    path = os.path.join(sockdir, "dead.sock")
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(path)
    try:
        with pytest.raises(WireFailure) as caught:
            ctlwire.roundtrip(path, "version")
    finally:
        dead.close()
    assert caught.value.cause == "daemon-refusing"


# ── one-line round trips ─────────────────────────────────────────────────────

def test_request_line_shape():
    line = ctlwire.request_line("stop", {"session": "s-1"}, 1)
    assert json.loads(line) == {"verb": "stop", "args": {"session": "s-1"}, "contract": 1}
    assert json.loads(ctlwire.request_line("version", None, 2))["args"] == {}


def test_roundtrip_and_call(serve):
    daemon = serve({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    doc = ctlwire.call(daemon.path, "version")
    assert doc["cgprofile"] == "1.2.0"
    assert daemon.requests == [{"verb": "version", "args": {}, "contract": 1}]


def test_call_maps_a_refusal(serve):
    daemon = serve({"stop": lambda a, c: {"ok": False, "error": {"code": "unknown-session", "message": "no"}}})
    with pytest.raises(WireFailure) as caught:
        ctlwire.call(daemon.path, "stop", {"session": "s-x"})
    assert (caught.value.exit_code, caught.value.code) == (2, "unknown-session")


def test_roundtrip_send_failure_is_unreachable(monkeypatch):
    monkeypatch.setattr(ctlwire, "_connect", lambda path, timeout: FakeSock(send_error=BrokenPipeError()))
    with pytest.raises(WireFailure) as caught:
        ctlwire.roundtrip("/x/y.sock", "version")
    assert caught.value.exit_code == 3


def test_roundtrip_recv_failure_is_unreachable(monkeypatch):
    monkeypatch.setattr(ctlwire, "_connect", lambda path, timeout: FakeSock(recv_error=ConnectionResetError()))
    with pytest.raises(WireFailure) as caught:
        ctlwire.roundtrip("/x/y.sock", "version")
    assert caught.value.cause == "daemon-refusing"


def test_roundtrip_reads_across_chunks(monkeypatch):
    monkeypatch.setattr(ctlwire, "_connect", lambda path, timeout: FakeSock([b'{"ok": tr', b'ue}\n']))
    assert ctlwire.roundtrip("/x", "version") == {"ok": True}


@pytest.mark.parametrize("data", [b"", b"   \n", b"not json\n", b"\xff\xfe\n", b"[1]\n", b'{"ok": 1}\n', b'{"x": 1}\n'])
def test_parse_response_rejects_unusable_answers(data):
    with pytest.raises(WireFailure) as caught:
        ctlwire.parse_response(data)
    assert (caught.value.exit_code, caught.value.code) == (3, "bad-response")


def test_parse_response_accepts_both_ok_values():
    assert ctlwire.parse_response(b'{"ok": false}\n') == {"ok": False}


# ── refusal table ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,verb,exit_code,cause", [
    ("peer-refused", "stop", 2, None),
    ("peer-refused", "config-set", 2, None),
    ("peer-refused", "status", 3, "socket-permission"),
    ("daemon-error", "status", 1, None),
    ("stop-incomplete", "stop", 1, None),
    ("export-too-large", "export", 1, None),
    ("busy", "export", 1, None),
    ("bad-argument", "start", 2, None),
    ("unknown-session", "show", 2, None),
])
def test_refusal_table(code, verb, exit_code, cause):
    failure = ctlwire.refusal({"ok": False, "error": {"code": code, "message": "m"}}, verb)
    assert (failure.exit_code, failure.cause, failure.code) == (exit_code, cause, code)
    assert failure.message == f"{code}: m"


def test_refusal_without_an_error_object_is_daemon_error():
    failure = ctlwire.refusal({"ok": False, "error": "text"})
    assert (failure.exit_code, failure.code) == (1, "daemon-error")


# ── handshake ────────────────────────────────────────────────────────────────

def test_daemon_contracts_and_version_parsing():
    assert ctlwire.daemon_contracts({"contracts": [1, 2]}) == [1, 2]
    assert ctlwire.daemon_contracts({"contracts": []}) == [1]
    assert ctlwire.daemon_contracts({"contracts": ["1"]}) == [1]
    assert ctlwire.daemon_contracts({}) == [1]
    assert ctlwire.daemon_version({"daemon": {"version": "2.0.0"}}) == "2.0.0"
    assert ctlwire.daemon_version({"daemon": {"version": 2}, "cgprofile": "1.2.0"}) == "1.2.0"
    assert ctlwire.daemon_version({"cgprofile": 3}) is None


def test_handshake_and_need_contract(serve):
    v2 = serve({"version": lambda a, c: dict(_fakectl.V2_VERSION)})
    doc, contracts = ctlwire.handshake(v2.path)
    assert contracts == [1, 2] and doc["daemon"]["version"] == "2.0.0"
    assert ctlwire.need_contract(v2.path, "sessions", 2)["ok"] is True


def test_need_contract_names_both_sides_for_a_1x_daemon(serve):
    v1 = serve({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    with pytest.raises(WireFailure) as caught:
        ctlwire.need_contract(v1.path, "sessions", 2)
    failure = caught.value
    assert (failure.exit_code, failure.cause) == (3, "contract-unsupported")
    assert "1.2.0" in failure.message and "[1]" in failure.message and "[1, 2]" in failure.message


def test_need_contract_with_an_unknown_daemon_version(serve):
    daemon = serve({"version": lambda a, c: {"ok": True}})
    with pytest.raises(WireFailure) as caught:
        ctlwire.need_contract(daemon.path, "sessions", 2)
    assert "(unknown version)" in caught.value.message


# ── line splitting ───────────────────────────────────────────────────────────

def test_lines_splits_skips_blanks_and_keeps_a_partial_tail():
    sock = FakeSock([b"a\n\nb\nc", b"d\n  \ntail"])
    assert list(ctlwire.lines(sock)) == [b"a", b"b", b"cd", b"tail"]


def test_lines_drops_a_whitespace_only_tail():
    assert list(ctlwire.lines(FakeSock([b"a\n   "]))) == [b"a"]


def test_lines_wraps_a_recv_error():
    with pytest.raises(WireFailure) as caught:
        list(ctlwire.lines(FakeSock(recv_error=OSError("reset"))))
    assert caught.value.exit_code == 3


def test_open_stream_connect_and_send_failures(sockdir, monkeypatch):
    with pytest.raises(WireFailure):
        ctlwire.open_stream(os.path.join(sockdir, "no.sock"), "watch", {}, 1)
    monkeypatch.setattr(ctlwire, "_connect", lambda path, timeout: FakeSock(send_error=BrokenPipeError()))
    with pytest.raises(WireFailure) as caught:
        ctlwire.open_stream("/x/y.sock", "watch", {}, 1)
    assert caught.value.exit_code == 3


class ClosingSock(FakeSock):
    closed = False

    def close(self):
        ClosingSock.closed = True


def test_open_stream_closes_the_socket_when_send_fails(monkeypatch):
    monkeypatch.setattr(ctlwire, "_connect", lambda path, timeout: ClosingSock(send_error=BrokenPipeError()))
    with pytest.raises(WireFailure):
        ctlwire.open_stream("/x/y.sock", "watch", {}, 1)
    assert ClosingSock.closed


# ── watch ────────────────────────────────────────────────────────────────────

def _watch(daemon, interval=None):
    return list(ctlwire.watch_lines(daemon.path, "s-1", interval))


def test_watch_yields_lines_until_the_end(serve):
    daemon = serve({"watch": lambda a, c: [{"ok": True}, {"event": "reading"}, {"event": "end", "status": "stopped"}]})
    docs = _watch(daemon, 10.0)
    assert [d.get("event") for d in docs] == [None, "reading", "end"]
    assert daemon.requests[0]["args"] == {"session": "s-1", "watch_interval": 10.0}


def test_watch_without_interval_sends_only_the_session(serve):
    daemon = serve({"watch": lambda a, c: [{"event": "end"}]})
    _watch(daemon)
    assert daemon.requests[0]["args"] == {"session": "s-1"}


def test_watch_refusal_line(serve):
    daemon = serve({"watch": lambda a, c: {"ok": False, "error": {"code": "unknown-session", "message": "x"}}})
    with pytest.raises(WireFailure) as caught:
        _watch(daemon)
    assert caught.value.exit_code == 2


@pytest.mark.parametrize("reply", [[b"{broken\n", {"event": "end"}], [b"[1]\n", {"event": "end"}]])
def test_watch_unusable_lines(serve, reply):
    daemon = serve({"watch": lambda a, c: reply})
    with pytest.raises(WireFailure) as caught:
        _watch(daemon)
    assert caught.value.code == "bad-response"


@pytest.mark.parametrize("reply", [[{"event": "reading"}], [{"event": "end"}, {"event": "end"}]])
def test_watch_requires_exactly_one_end(serve, reply):
    daemon = serve({"watch": lambda a, c: reply})
    with pytest.raises(WireFailure) as caught:
        _watch(daemon)
    assert caught.value.code == "stream-truncated"


# ── export ───────────────────────────────────────────────────────────────────

PARTS = {"summary.json": b'{"verdict": "ok"}', "host.jsonl": b"line1\nline2\n"}


def _export(daemon):
    return ctlwire.export_parts(daemon.path, "s-1")


def test_export_round_trip_verifies_every_part(serve):
    daemon = serve({"export": lambda a, c: _fakectl.export_reply(PARTS)})
    assert _export(daemon) == PARTS
    assert daemon.requests[0]["contract"] == 2


def test_export_refusal_header(serve):
    daemon = serve({"export": lambda a, c: {"ok": False, "error": {"code": "session-live", "message": "live"}}})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert caught.value.code == "session-live"


@pytest.mark.parametrize("first,why", [
    ({"no": "ok"}, "export header missing"),
    ({"ok": True}, "no parts list"),
])
def test_export_bad_headers(serve, first, why):
    daemon = serve({"export": lambda a, c: [first]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert why in caught.value.message


@pytest.mark.parametrize("bad", [b"{nope\n", b"[1]\n"])
def test_export_malformed_lines(serve, bad):
    header = _fakectl.export_reply(PARTS)[0]
    daemon = serve({"export": lambda a, c: [header, bad]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert caught.value.code == "bad-response"


def test_export_malformed_first_line(serve):
    daemon = serve({"export": lambda a, c: [b"{nope\n"]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert caught.value.code == "bad-response"


def test_export_aborted_by_the_daemon(serve):
    header = _fakectl.export_reply(PARTS)[0]
    end = {"event": "end", "status": "aborted", "error": {"message": "disk gone"}}
    daemon = serve({"export": lambda a, c: [header, end]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert (caught.value.code, caught.value.exit_code) == ("export-failed", 1)
    assert "disk gone" in caught.value.message


def test_export_aborted_without_an_error_object(serve):
    header = _fakectl.export_reply(PARTS)[0]
    daemon = serve({"export": lambda a, c: [header, {"event": "end", "status": "aborted"}]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert "aborted the export" in caught.value.message


@pytest.mark.parametrize("line", [{"part": "other", "data": "AA=="}, {"part": "summary.json", "data": 5}])
def test_export_unannounced_part_and_bad_data(serve, line):
    header = _fakectl.export_reply(PARTS)[0]
    daemon = serve({"export": lambda a, c: [header, line]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert "did not announce" in caught.value.message


def test_export_bad_base64(serve):
    header = _fakectl.export_reply(PARTS)[0]
    daemon = serve({"export": lambda a, c: [header, {"part": "summary.json", "data": "!!!"}]})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert "bad base64" in caught.value.message


@pytest.mark.parametrize("lines_", [
    lambda: [_fakectl.export_reply(PARTS)[0]],
    lambda: [],
])
def test_export_truncated_stream(serve, lines_):
    daemon = serve({"export": lambda a, c: lines_()})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert caught.value.code == "stream-truncated"


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("bytes", 3)])
def test_export_checksum_and_size_mismatch(serve, field, value):
    stream = _fakectl.export_reply(PARTS)
    stream[0]["parts"][0][field] = value
    daemon = serve({"export": lambda a, c: stream})
    with pytest.raises(WireFailure) as caught:
        _export(daemon)
    assert "failed verification" in caught.value.message


def test_roundtrip_with_a_daemon_that_closes_silently(serve):
    daemon = serve({"version": lambda a, c: []})
    with pytest.raises(WireFailure) as caught:
        ctlwire.roundtrip(daemon.path, "version")
    assert caught.value.code == "bad-response"
