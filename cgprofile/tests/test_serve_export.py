"""`export`, the second streaming verb of contract 2 (PROTOCOL 10.3)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from cgprofile import serve
from tests.test_serve_contract2 import (
    ALICE, BOB, CONTAINER_ID, NOBODY, ROOT, _golden, _req, _start, server,  # noqa: F401
)

SID = "s-20260912T101500Z-0001"
SUMMARY = b'{"schema": "summary-v1.1", "session": "s-20260912T101500Z-0001"}\n'
SAMPLES = bytes(range(256)) * 3            # arbitrary binary, stands in for the gzip
EVENTS = b'{"event": "x"}\n'


def _session(server, name=SID, *, owner: Optional[int] = 1000, status="finished",
             files: Optional[Dict[str, bytes]] = None) -> Path:
    folder = Path(server.sessions_dir) / name
    folder.mkdir(parents=True)
    manifest: Dict[str, Any] = {"status": status, "ended_at": "2026-09-12T10:15:00Z"}
    if owner is not None:
        manifest["owner_uid"] = owner
    (folder / "manifest.json").write_text(json.dumps(manifest))
    contents = {"summary.json": SUMMARY, "samples.jsonl.gz": SAMPLES, "events.jsonl": EVENTS}
    for part, data in (contents if files is None else files).items():
        (folder / part).write_bytes(data)
    return folder


class _Conn:
    """A connection double: records every JSON line, optionally breaking."""

    def __init__(self, *, fail_on: Optional[int] = None, on_send=None) -> None:
        self.lines: List[Dict[str, Any]] = []
        self.closed = False
        self.timeout: Optional[float] = None
        self._fail_on = fail_on
        self._on_send = on_send

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, payload: bytes) -> None:
        if self._fail_on is not None and len(self.lines) >= self._fail_on:
            raise BrokenPipeError("peer went away")
        self.lines.append(json.loads(payload.decode("utf-8")))
        if self._on_send is not None:
            self._on_send(self.lines[-1])

    def close(self) -> None:
        self.closed = True


def _export(server, args, peer=ROOT, **conn_kwargs) -> _Conn:
    """Run one export synchronously through the real connection method."""
    assert server._export_slots.acquire()
    conn = _Conn(**conn_kwargs)
    server._export_connection(conn, args, server._make_ctx(_req("export"), peer))
    return conn


def _reassemble(lines) -> Dict[str, bytes]:
    out: Dict[str, bytes] = {}
    for line in lines[1:-1]:
        out[line["part"]] = out.get(line["part"], b"") + base64.b64decode(line["data"])
    return out


class TestExportStream:
    def test_header_data_and_end(self, server):
        _session(server)
        conn = _export(server, {"session": SID})
        header, end = conn.lines[0], conn.lines[-1]
        assert header == {
            "ok": True, "contract": 2, "session": SID, "owner_uid": 1000,
            "total_bytes": len(SUMMARY) + len(SAMPLES) + len(EVENTS),
            "parts": [
                {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in (("summary.json", SUMMARY), ("samples.jsonl.gz", SAMPLES),
                                   ("events.jsonl", EVENTS))
            ],
        }
        assert end == {"event": "end", "status": "complete"}
        assert _reassemble(conn.lines) == {
            "summary.json": SUMMARY, "samples.jsonl.gz": SAMPLES, "events.jsonl": EVENTS,
        }
        assert conn.closed and conn.timeout == serve.EXPORT_STALL_SECONDS
        assert server._exporting == {} and server._export_slots.acquire()

    def test_large_parts_are_chunked_with_seq_per_part(self, server, monkeypatch):
        monkeypatch.setattr(serve, "EXPORT_CHUNK_BYTES", 100)
        _session(server)
        conn = _export(server, {"session": SID, "parts": ["samples.jsonl.gz"]})
        data = [line for line in conn.lines if "part" in line]
        assert [line["seq"] for line in data] == [0, 1, 2, 3, 4, 5, 6, 7]
        assert all(len(base64.b64decode(line["data"])) <= 100 for line in data)
        assert _reassemble(conn.lines) == {"samples.jsonl.gz": SAMPLES}

    def test_parts_subset_is_in_header_order_and_deduplicated(self, server):
        _session(server)
        conn = _export(server, {"session": SID, "parts": ["events.jsonl", "summary.json", "events.jsonl"]})
        assert [p["name"] for p in conn.lines[0]["parts"]] == ["summary.json", "events.jsonl"]

    def test_empty_parts_list_and_missing_parts(self, server):
        _session(server, files={"summary.json": SUMMARY})
        assert _export(server, {"session": SID, "parts": []}).lines == [
            {"ok": True, "contract": 2, "session": SID, "parts": [], "total_bytes": 0,
             "owner_uid": 1000},
            {"event": "end", "status": "complete"},
        ]
        conn = _export(server, {"session": SID, "parts": None})
        assert [p["name"] for p in conn.lines[0]["parts"]] == ["summary.json"]

    def test_the_session_is_marked_as_exporting_while_it_streams(self, server):
        _session(server)
        seen: List[Dict[str, int]] = []
        _export(server, {"session": SID}, on_send=lambda line: seen.append(dict(server._exporting)))
        assert seen and all(state == {SID: 1} for state in seen)
        assert server._exporting == {}

    def test_gc_skips_a_session_being_exported(self, server):
        _session(server)
        results: List[Dict[str, Any]] = []

        def during(line):
            if not results:
                results.append(server._dispatch(_req("gc", keep=1), peer=ROOT))

        _export(server, {"session": SID}, on_send=during)
        assert results[0]["removed"] == [] and results[0]["kept"] == 1

    def test_two_exports_of_one_session_count_up_and_down(self, server):
        server._exporting[SID] = 1
        server._export_release(SID)
        assert server._exporting == {}
        server._exporting[SID] = 2
        server._export_release(SID)
        assert server._exporting == {SID: 1}

    def test_owner_and_root_may_export(self, server):
        _session(server)
        for peer in (ALICE, ROOT):
            assert _export(server, {"session": SID}, peer=peer).lines[-1]["status"] == "complete"

    def test_a_store_without_an_owner_has_a_null_owner_in_the_header(self, server):
        _session(server, owner=None)
        assert _export(server, {"session": SID}).lines[0]["owner_uid"] is None


class TestExportRefusals:
    def _refusal(self, server, args, peer=ROOT):
        conn = _export(server, args, peer=peer)
        assert len(conn.lines) == 1 and conn.closed
        assert server._exporting == {}
        return conn.lines[0]

    @pytest.mark.parametrize("args, code, fragment", [
        ({"session": SID, "bogus": 1}, "bad-argument", "unexpected export argument"),
        ({"session": 5}, "unknown-session", "no session 5"),
        ({"session": "../x"}, "unknown-session", "no session"),
        ({"session": SID, "parts": "summary.json"}, "bad-argument", "'parts' must be null or a list"),
        ({"session": SID, "parts": ["nope"]}, "bad-argument", "'parts' must be null or a list"),
        ({"session": SID, "parts": [1]}, "bad-argument", "'parts' must be null or a list"),
        ({"session": "s-20260912T101500Z-0099"}, "unknown-session", "is on record"),
    ])
    def test_argument_errors(self, server, args, code, fragment):
        _session(server)
        doc = self._refusal(server, args)
        assert doc["ok"] is False and doc["contract"] == 2
        assert doc["error"]["code"] == code and fragment in doc["error"]["message"]

    def test_a_running_session_is_session_live(self, server):
        sid = server._dispatch(_start(), peer=ALICE)["session"]
        doc = self._refusal(server, {"session": sid}, peer=ALICE)
        assert doc["error"]["code"] == "session-live"

    def test_a_manifest_still_marked_live_is_session_live(self, server):
        _session(server, status="live")
        assert self._refusal(server, {"session": SID})["error"]["code"] == "session-live"

    @pytest.mark.parametrize("peer", [BOB, NOBODY])
    def test_other_uids_are_refused(self, server, peer):
        _session(server)
        doc = self._refusal(server, {"session": SID}, peer=peer)
        assert doc["error"]["code"] == "peer-refused"
        assert "owner uid 1000" in doc["error"]["message"]

    def test_unowned_store_is_root_only(self, server):
        _session(server, owner=None)
        doc = self._refusal(server, {"session": SID}, peer=ALICE)
        assert "no owner recorded" in doc["error"]["message"]

    def test_too_large_is_refused_before_any_data(self, server):
        _session(server)
        server.export_max_bytes = 100
        doc = self._refusal(server, {"session": SID})
        assert doc["error"]["code"] == "export-too-large"
        assert "export_max_bytes (100)" in doc["error"]["message"]
        assert str(len(SUMMARY) + len(SAMPLES) + len(EVENTS)) in doc["error"]["message"]

    def test_a_symlinked_part_is_export_failed(self, server):
        folder = _session(server)
        (folder / "events.jsonl").unlink()
        (folder / "events.jsonl").symlink_to(folder / "summary.json")
        doc = self._refusal(server, {"session": SID})
        assert doc["error"]["code"] == "export-failed"

    def test_a_directory_in_place_of_a_part_is_export_failed(self, server):
        folder = _session(server)
        (folder / "events.jsonl").unlink()
        (folder / "events.jsonl").mkdir()
        doc = self._refusal(server, {"session": SID})
        assert doc["error"]["code"] == "export-failed"
        assert "not a regular file" in doc["error"]["message"] or "Invalid argument" in doc["error"]["message"]


class _FlakyFile:
    """Reads succeed for the first `good` calls, then raise OSError."""

    def __init__(self, real, good: int) -> None:
        self._real, self._good = real, good

    def fileno(self):
        return self._real.fileno()

    def read(self, size):
        if self._good <= 0:
            raise OSError(5, "Input/output error")
        self._good -= 1
        return self._real.read(size)

    def seek(self, pos):
        return self._real.seek(pos)

    def close(self):
        self._real.close()


class TestExportFailures:
    def test_a_read_error_while_hashing_is_a_plain_export_failed_error(self, server, monkeypatch):
        _session(server, files={"summary.json": SUMMARY})
        real = server._export_open
        monkeypatch.setattr(server, "_export_open", lambda path: _FlakyFile(real(path), 0))
        conn = _export(server, {"session": SID})
        assert len(conn.lines) == 1 and conn.lines[0]["error"]["code"] == "export-failed"
        assert "Input/output error" in conn.lines[0]["error"]["message"]

    def test_a_read_error_mid_stream_is_an_aborted_end(self, server, monkeypatch):
        _session(server, files={"summary.json": SUMMARY})
        real = server._export_open
        # hashing needs two reads (data, EOF); the stream's first read then fails
        monkeypatch.setattr(server, "_export_open", lambda path: _FlakyFile(real(path), 2))
        conn = _export(server, {"session": SID})
        assert [line.get("event") for line in conn.lines] == [None, "end"]
        assert conn.lines[-1]["status"] == "aborted"
        assert conn.lines[-1]["error"]["code"] == "export-failed"
        assert "cannot read summary.json" in conn.lines[-1]["error"]["message"]

    def test_shutdown_aborts_the_stream(self, server):
        _session(server)
        server.request_shutdown()
        conn = _export(server, {"session": SID})
        assert conn.lines[-1]["status"] == "aborted"
        assert "shutting down" in conn.lines[-1]["error"]["message"]

    def test_a_peer_that_goes_away_is_dropped_quietly_and_cleaned_up(self, server):
        _session(server)
        conn = _export(server, {"session": SID}, fail_on=2)
        assert len(conn.lines) == 2 and conn.closed
        assert server._exporting == {} and server._export_slots.acquire()

    def test_an_unexpected_bug_is_logged_not_raised(self, server, monkeypatch, capsys):
        def boom(args, ctx):
            raise RuntimeError("kaput")

        monkeypatch.setattr(server, "_export_admit", boom)
        conn = _export(server, {"session": SID})
        assert conn.closed and conn.lines == []
        assert "unhandled error streaming export: RuntimeError: kaput" in capsys.readouterr().err


class TestExportHandOff:
    """Through `_handle_connection` on a real socket pair: the verb reaches
    its own thread, its own slot class and contract-2 gate."""

    def _roundtrip(self, server, request) -> List[Dict[str, Any]]:
        client, daemon = socket.socketpair()
        client.settimeout(5.0)
        try:
            client.sendall((json.dumps(request) + "\n").encode())
            server._handle_connection(daemon)
            data = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            client.close()
        return [json.loads(line) for line in data.decode().splitlines()]

    def test_export_over_a_socket(self, server):
        _session(server, owner=os.getuid())
        lines = self._roundtrip(server, _req("export", session=SID))
        assert lines[0]["ok"] is True and lines[-1] == {"event": "end", "status": "complete"}
        assert _reassemble(lines)["samples.jsonl.gz"] == SAMPLES

    def test_export_at_contract_1_is_refused_not_streamed(self, server):
        _session(server, owner=os.getuid())
        (line,) = self._roundtrip(server, _req("export", 1, session=SID))
        assert line["error"]["code"] == "bad-argument" and line["contract"] == 1
        assert "exists only at contract 2" in line["error"]["message"]

    def test_export_slots_are_separate_from_watch_slots(self, server):
        _session(server, owner=os.getuid())
        while server._export_slots.acquire():
            pass
        (line,) = self._roundtrip(server, _req("export", session=SID))
        assert line["error"]["code"] == "busy" and line["contract"] == 2
        assert "too many export streams" in line["error"]["message"]
        assert server._watch_slots.acquire()  # watch has its own pool

    def test_a_thread_that_cannot_start_releases_the_slot(self, server, monkeypatch):
        class _Dead:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        monkeypatch.setattr(serve.threading, "Thread", _Dead)
        (line,) = self._roundtrip(server, _req("export", session=SID))
        assert line["error"]["code"] == "busy" and "export thread" in line["error"]["message"]
        assert server._export_slots.acquire()

    def test_the_thread_is_named_for_the_session(self, server, monkeypatch):
        seen = {}

        class _Spy:
            def __init__(self, *args, **kwargs):
                seen.update(kwargs)

            def start(self):
                return None

        monkeypatch.setattr(serve.threading, "Thread", _Spy)
        client, daemon = socket.socketpair()
        try:
            client.sendall((json.dumps(_req("export", session=SID)) + "\n").encode())
            server._handle_connection(daemon)
        finally:
            client.close()
        assert seen["name"] == f"cgprofile-export-{SID}" and seen["daemon"] is True


class TestExportGolden:
    def test_the_export_stream_is_frozen(self, server, tmp_path):
        _session(server, name="s-20260912T101500Z-9f01")
        request = _req("export", session="s-20260912T101500Z-9f01")
        conn = _export(server, request["args"])
        stream = [json.dumps(line) for line in conn.lines]
        path = Path(__file__).resolve().parent / "fixtures" / "rg55" / "socket" / "export-response.ndjson"
        text = "\n".join(stream) + "\n"
        if os.environ.get("CGPROFILE_REGEN_SOCKET_GOLDENS") == "1":  # pragma: no cover
            path.write_text(text)
        assert path.read_text() == text
        _golden("export-request.json", request, tmp_path)
