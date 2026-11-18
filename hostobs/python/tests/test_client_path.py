#!/usr/bin/env python3
"""`Connection.open`, `reconnect`, `require_profiling` and `hostobs-mark` over real `AF_UNIX` paths.

The one thread of the suite lives here, in `PathServer`, because `open()` blocks in the handshake
until the server answers; it serves scripted answers and never decides anything.

    python3 -I -m unittest discover -s python/tests -p 'test_client_path.py' -v
"""

from __future__ import annotations

import io
import json
import os
import socket
import struct
import sys
import tempfile
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_probe import hello_result, json_bytes, raw_frame  # noqa: E402
from hostobs_client import mark, profiling  # noqa: E402
from hostobs_client.conn import Connection, ProtocolError  # noqa: E402
from hostobs_client.envelope import EnvelopeError, build_request, check_end, check_evt, check_rep  # noqa: E402
from hostobs_client.stream import Session  # noqa: E402
from hostobs_client.wire import ConnectionLost, ServerError  # noqa: E402


def read_exactly(conn: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            break
        data += chunk
    return data


class PathServer:
    """Accepts connections one after another; ``script(index, conn)`` answers each (binary hello)."""

    def __init__(self, path: str, script) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        self.script = script
        self.count = 0
        self.served = threading.Semaphore(0)  # released once per connection, after its fd is closed
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                try:
                    self.script(self.count, conn)
                except OSError:
                    pass
            self.count += 1
            self.served.release()

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # wakes the accept() of the serving thread
        except OSError:
            pass
        self.sock.close()
        self.thread.join(5)


def binary_hello(epoch_by_index):
    def script(index: int, conn: socket.socket) -> None:
        read_exactly(conn, 8)
        conn.sendall(b"HOB\x01" + struct.pack("<HH", 1, 1))
        header = read_exactly(conn, 12)
        read_exactly(conn, struct.unpack_from("<I", header)[0] - 12)
        conn.sendall(raw_frame(2, 1, json_bytes({"ok": True, "result": hello_result(epoch=epoch_by_index(index))})))
        while conn.recv(4096):
            pass

    return script


def socket_dir() -> str:
    """A directory whose socket paths fit ``sun_path`` (107 bytes) whatever ``TMPDIR`` is."""
    return tempfile.mkdtemp(prefix="hob", dir="/tmp")


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = socket_dir()
        self.addCleanup(shutil.rmtree, directory, True)
        self.path = os.path.join(directory, "observe.sock")


class OpenAndReconnect(TempDirCase):
    def test_open_reconnect_session_semantics_and_no_fd_leak(self) -> None:
        epochs = {0: 1, 1: 1, 2: 2}
        server = PathServer(self.path, binary_hello(epochs.__getitem__))
        self.addCleanup(server.close)
        before = len(os.listdir("/proc/self/fd"))
        first = Connection.open(self.path, "observe", client="t", timeout_s=5)
        self.assertEqual((first.session, first.epoch), (Session.FIRST, 1))
        second = first.reconnect()
        self.assertTrue(first.closed)
        self.assertEqual(second.session, Session.SAME)
        third = second.reconnect()
        self.assertEqual((third.session, third.epoch), (Session.CHANGED, 2))
        third.close()
        for _ in range(3):  # the server thread has closed its end of each connection
            self.assertTrue(server.served.acquire(timeout=10))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_socket_paths_fit_sun_path_under_a_long_tmpdir(self) -> None:
        long_tmp = "/tmp/" + "x" * 145
        with mock.patch.dict(os.environ, {"TMPDIR": long_tmp}), mock.patch.object(tempfile, "tempdir", None):
            directory = socket_dir()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "observe.sock")
        self.assertLess(len(path.encode()), 100)
        server = PathServer(path, binary_hello(lambda _i: 1))
        self.addCleanup(server.close)
        Connection.open(path, "observe", client="t", timeout_s=5).close()
        self.assertTrue(server.served.acquire(timeout=10))

    def test_unreachable_paths_close_their_socket(self) -> None:
        before = len(os.listdir("/proc/self/fd"))
        with self.assertRaises(FileNotFoundError):
            Connection.open(self.path, "observe", client="t", timeout_s=1)
        Path(self.path).write_text("")
        with self.assertRaises(OSError):
            Connection.open(self.path, "observe", client="t", timeout_s=1)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_a_socket_connection_cannot_reconnect(self) -> None:
        a, b = socket.socketpair()
        self.addCleanup(b.close)
        b.sendall(b"HOB\x01" + struct.pack("<HH", 1, 1) + raw_frame(2, 1, json_bytes({"ok": True, "result": hello_result()})))
        conn = Connection(a, "observe", client="t", timeout_s=5)
        with self.assertRaises(ValueError):
            conn.reconnect()
        conn.close()


class Profiling(TempDirCase):
    def test_unconfigured_and_unreachable_and_absent(self) -> None:
        for required, action in ((False, "run-with-notice"), (True, "refuse")):
            state = profiling.require_profiling("observer", required, environ={})
            self.assertEqual((state.name, state.action, state.refuse), ("unconfigured", action, required))
            self.assertIn("HOSTOBS_OBSERVER_SOCKET", state.notice)
            state = profiling.require_profiling("observe", required, environ={"HOSTOBS_OBSERVE_SOCKET": self.path})
            self.assertEqual((state.name, state.action), ("unreachable", action))
            self.assertIn(self.path, state.notice)
            self.assertIn("errno 2", state.notice)
            self.assertEqual(profiling.absent(required).action, action)
        self.assertEqual(profiling.absent(True).name, "absent")
        self.assertEqual(set(profiling.STATES), {"absent", "unconfigured", "unreachable", "refused", "lost"})

    def test_available_refused_level_refused_feature(self) -> None:
        server = PathServer(self.path, binary_hello(lambda _i: 1))
        self.addCleanup(server.close)
        env = {"HOSTOBS_OBSERVE_SOCKET": self.path, "HOSTOBS_OPERATOR_SOCKET": self.path}
        state = profiling.require_profiling("observe", True, environ=env, features=("peak_reset",))
        self.assertEqual((state.name, state.action, state.notice), ("available", "run", ""))
        state.connection.close()
        state = profiling.require_profiling("observe", True, environ=env, features=("damon",))
        self.assertEqual((state.name, state.action), ("refused", "refuse"))
        self.assertIn("damon", state.notice)
        state = profiling.require_profiling("operator", False, environ=env)
        self.assertEqual((state.name, state.action), ("refused", "run-with-notice"))
        self.assertIn("serves level observe", state.notice)

    def test_refused_and_unreachable_by_error_kind(self) -> None:
        def opener(error):
            def raiser(*_a, **_k):
                raise error

            return raiser

        env = {"HOSTOBS_OBSERVE_SOCKET": "/x"}
        cases = [
            (ServerError("forbidden"), "refused"),
            (ServerError("internal"), "unreachable"),
            (ConnectionLost("gone"), "unreachable"),
            (TimeoutError("slow"), "unreachable"),
            (EnvelopeError("json-text"), "unreachable"),
            (ProtocolError("hello-mismatch", {"why": "other"}), "unreachable"),
        ]
        for error, name in cases:
            state = profiling.require_profiling("observe", True, environ=env, opener=opener(error))
            self.assertEqual(state.name, name, error)
        from hostobs_client.wire import WireError

        state = profiling.require_profiling("observe", True, environ=env, opener=opener(WireError("unsupported-version")))
        self.assertEqual(state.name, "refused")

    def test_lost_states_and_loss_reasons(self) -> None:
        self.assertEqual((profiling.lost(False, "r").action, profiling.lost(False, "r").outcome), ("record-profile-error", None))
        self.assertEqual((profiling.lost(True, "r").action, profiling.lost(True, "r").outcome), ("evidence-invalid", "EVIDENCE_INVALID"))
        self.assertIn("session-lost", profiling.loss_reason(ServerError("session-lost")))
        self.assertIn("window-lost", profiling.loss_reason(ServerError("window-lost")))
        self.assertIsNone(profiling.loss_reason(ServerError("internal")))
        self.assertIsNotNone(profiling.loss_reason(Session.CHANGED))
        self.assertIsNone(profiling.loss_reason(Session.SAME))


class LineServer:
    """A one-connection JSON-mode server: reads to EOF, answers with ``reply``."""

    def __init__(self, path: str, reply: bytes) -> None:
        self.received = b""
        self.reply = reply

        def script(_index: int, conn: socket.socket) -> None:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
            conn.sendall(self.reply)

        self.server = PathServer(path, script)

    def close(self) -> None:
        self.server.close()


class Mark(TempDirCase):
    def run_main(self, argv, environ):
        err = io.StringIO()
        return mark.main(argv, environ=environ, stderr=err), err.getvalue()

    def test_mark_round_trip_with_context(self) -> None:
        server = LineServer(self.path, b'{"ok":true,"corr":1,"result":{"t_mono_ns":5,"span":null}}\n')
        self.addCleanup(server.close)
        env = {mark.SOCKET_VARIABLE: self.path, "HOSTOBS_CTX_RUN": "r1", "HOSTOBS_CTX_LANE": "", "HOSTOBS_CTX_TXN": "t9"}
        status, err = self.run_main(["end", "gate.run", "--outcome", "pass", "--attr", "a=b", "--key", "k"], env)
        self.assertEqual((status, err), (0, ""))
        line = server.received
        self.assertTrue(line.endswith(b"\n") and line.count(b"\n") == 1)
        body = json.loads(line)
        self.assertEqual((body["corr"], body["verb"]), (1, "annotate"))
        self.assertEqual(
            body["args"],
            {"kind": "end", "name": "gate.run", "key": "k", "ctx": {"run": "r1", "txn": "t9"}, "outcome": "pass", "code": None, "attrs": {"a": "b"}},
        )

    def test_unavailable_is_exit_zero_with_notice_unless_required(self) -> None:
        for env in ({}, {mark.SOCKET_VARIABLE: self.path}):
            status, err = self.run_main(["point", "x"], env)
            self.assertEqual(status, 0)
            self.assertIn("mark not written", err)
            status, err = self.run_main(["point", "x", "--require"], env)
            self.assertEqual(status, 1)

    def test_error_and_malformed_replies(self) -> None:
        replies = [
            b'{"ok":false,"corr":1,"error":{"code":"rate-limited","message":"m","detail":{"retry_after_ms":5}}}\n',
            b'{"ok":true,"result":{}}\n',
            b"not json\n",
            b'{"ok":true,"corr":7,"result":{}}\n',
            b"",
        ]
        for reply in replies:
            path = self.path + str(replies.index(reply))
            server = LineServer(path, reply)
            self.addCleanup(server.close)
            status, err = self.run_main(["point", "x", "--require"], {mark.SOCKET_VARIABLE: path})
            self.assertEqual(status, 1, reply)
            self.assertIn("mark not written", err)

    def test_usage_errors(self) -> None:
        status, err = self.run_main(["point", "Bad Name"], {})
        self.assertEqual(status, 2)
        with self.assertRaises(SystemExit) as raised:
            mark.main(["wiggle", "x"], environ={})
        self.assertEqual(raised.exception.code, 2)
        with self.assertRaises(ValueError):
            mark.build_args("point", "x", outcome="pass")


class EnvelopeUnits(unittest.TestCase):
    def test_check_rep_json_mode_and_end_evt_shapes(self) -> None:
        rep = check_rep(b'{"corr":0,"ok":false,"error":{"code":"a","message":"b","detail":{}}}', json_mode=True)
        self.assertEqual((rep.ok, rep.corr, rep.body["code"]), (False, 0, "a"))
        with self.assertRaises(EnvelopeError):
            check_rep(b'{"ok":true,"result":{}}', json_mode=True)  # corr missing
        with self.assertRaises(EnvelopeError):
            check_rep(b'{"corr":-1,"ok":true,"result":{}}', json_mode=True)
        end = check_end(b'{"ok":true,"blocks":3,"dropped":0,"next":null,"reason":"complete"}')
        self.assertEqual((end.blocks, end.next, end.reason), (3, None, "complete"))
        for bad in (
            b'{"ok":true,"blocks":1.0,"dropped":0,"next":null,"reason":"complete"}',
            b'{"ok":true,"blocks":4294967296,"dropped":0,"next":null,"reason":"complete"}',
            b'{"ok":true,"blocks":true,"dropped":0,"next":null,"reason":"complete"}',
            b'{"ok":false,"blocks":1,"dropped":0,"reason":"x"}',
        ):
            with self.assertRaises(EnvelopeError, msg=bad):
                check_end(bad)
        self.assertEqual(check_evt(b'{"event":"closing","reason":"idle"}')["reason"], "idle")
        self.assertEqual(check_evt(b'{"event":"other","a":1}')["a"], 1)
        with self.assertRaises(EnvelopeError):
            check_evt(b'{"event":"closing","reason":"idle","x":1}')

    def test_build_request_is_compact_utf8_with_exact_integers(self) -> None:
        raw = build_request("status", {"n": 2**64 - 1, "s": "é"}, corr=3)
        self.assertEqual(raw, '{"corr":3,"verb":"status","args":{"n":18446744073709551615,"s":"é"}}'.encode())


if __name__ == "__main__":
    unittest.main()
