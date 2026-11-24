#!/usr/bin/env python3
"""Review hardening of `hostobs_client`: linear-time text checks, argument and hello validation,
exact deadline boundaries, queue and corr bounds, and the loss/refusal tables.

    python3 -I -m unittest discover -s python/tests -p 'test_client_hardening.py' -v
"""

from __future__ import annotations

import contextlib
import io
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_probe import BLOCK, Clock, NS, Rig, block_frame, hello_result, json_bytes, raw_frame  # noqa: E402
from hostobs_client import conn as conn_module, envelope, mark, profiling  # noqa: E402
from hostobs_client.conn import Connection, OperationTimeout, ProtocolError  # noqa: E402
from hostobs_client.envelope import EnvelopeError, build_request  # noqa: E402
from hostobs_client.wire import ServerError  # noqa: E402

N = 20_000
CAP_S = 1.0


class LinearTime(unittest.TestCase):
    """A hostile 16 MiB payload must not be able to pin a core in C regex work (WIRE.md 4.2, 7.3)."""

    def shapes(self) -> dict:
        n = N
        return {
            "unterminated string of escaped quotes": b'{"a":"' + b'\\"' * n + b"}",
            "run of [": b'{"a":' + b"[" * n + b"}",
            "run of objects": b'{"a":' + b'{"a":' * n + b"}",
            "unterminated key": b'{"' + b'\\"' * n + b"}",
            "100k deep": b'{"a":' + b"[" * 100_000 + b"]" * 100_000 + b"}",
            "5000-digit integer": b'{"ok":true,"result":{"n":' + b"9" * 5000 + b"}}",
            "quote storm": b'{"a":' + b'"x' * n + b"}",
        }

    def test_every_shape_is_refused_quickly(self) -> None:
        for name, payload in self.shapes().items():
            with self.subTest(shape=name):
                start = time.perf_counter()
                with self.assertRaises(EnvelopeError) as raised:
                    envelope.check_rep(payload)
                self.assertEqual(raised.exception.kind, "json-text")
                self.assertLess(time.perf_counter() - start, CAP_S)
                start = time.perf_counter()
                with self.assertRaises(EnvelopeError):
                    envelope.parse_object(payload)
                self.assertLess(time.perf_counter() - start, CAP_S)

    def test_depth_is_counted_only_on_text_that_parsed(self) -> None:
        calls = []
        real = envelope.max_depth

        def counting(raw):
            calls.append(len(raw))
            return real(raw)

        with mock.patch.object(envelope, "max_depth", counting):
            for payload in self.shapes().values():
                with self.assertRaises(EnvelopeError):
                    envelope.check_rep(payload)
            self.assertEqual(calls, [], "the string-stripping regex never sees unparsed hostile text")
            envelope.check_rep(b'{"ok":true,"result":{"a":[1]}}')
            self.assertEqual(len(calls), 1)

    def test_the_connection_refuses_such_a_reply_without_hanging(self) -> None:
        rig = Rig()
        self.addCleanup(rig.close)
        conn = rig.connect()
        rig.probe.send(raw_frame(2, 2, self.shapes()["unterminated string of escaped quotes"]))
        start = time.perf_counter()
        with self.assertRaises(EnvelopeError):
            conn.request("status", {})
        self.assertLess(time.perf_counter() - start, CAP_S)
        self.assertTrue(conn.closed)


class Arguments(unittest.TestCase):
    def test_open_validates_before_creating_a_socket(self) -> None:
        bad = [
            {"level": "root"}, {"client": ""}, {"client": "x" * 65}, {"timeout_s": 0}, {"timeout_s": -1.0},
            {"timeout_s": float("inf")}, {"timeout_s": float("nan")}, {"timeout_s": 1e400}, {"timeout_s": True},
            {"timeout_s": "5"}, {"timeout_s": conn_module.MAX_TIMEOUT_S * 2},
        ]
        with mock.patch.object(conn_module.socket, "socket", side_effect=AssertionError("socket created")):
            for kw in bad:
                args = {"level": "observe", "client": "c", "timeout_s": 1.0, **kw}
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    Connection.open("/nonexistent", args.pop("level"), **args)

    def test_constructor_validates_the_same_way(self) -> None:
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        for timeout in (float("inf"), float("nan"), 0, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                Connection(a, "observe", client="c", timeout_s=timeout)

    def test_mark_timeout_values(self) -> None:
        for value in ("inf", "-inf", "nan", "1e400", "0", "-1", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    mark.main(["point", "x", "--timeout", value], environ={})
            self.assertEqual(raised.exception.code, 2)
        with self.assertRaises(ValueError):
            mark.send_mark("/nonexistent", mark.build_args("point", "x"), timeout_s=float("inf"))

    def test_deeply_nested_args_are_a_value_error(self) -> None:
        deep: dict = {}
        for _ in range(5000):
            deep = {"a": deep}
        with self.assertRaises(ValueError):
            build_request("status", deep)
        rig = Rig()
        self.addCleanup(rig.close)
        conn = rig.connect()
        with self.assertRaises(ValueError):
            conn.request("status", deep)
        self.assertFalse(conn.closed)


class HelloValidation(unittest.TestCase):
    def refused(self, **over) -> ProtocolError:
        rig = Rig()
        self.addCleanup(rig.close)
        rig.probe.prefix_reply()
        rig.probe.rep_ok(1, hello_result(**over))
        with self.assertRaises(ProtocolError) as raised:
            Connection(rig.client, "observe", client="test-client", timeout_s=1, clock=rig.clock)
        self.assertEqual(raised.exception.kind, "hello-mismatch")
        self.assertTrue(rig.client.closed)
        return raised.exception

    def accepted(self, **over) -> Connection:
        rig = Rig()
        self.addCleanup(rig.close)
        conn = rig.connect(**over)
        self.addCleanup(conn.close)
        return conn

    def test_proto_fields_must_be_exact_integers(self) -> None:
        for proto in ({"major": 1.0, "minor": 1}, {"major": True, "minor": 1}, {"major": 1, "minor": True}, {"major": 1, "minor": 1.0}):
            with self.subTest(proto=proto):
                self.refused(proto=proto)

    def test_epoch_is_a_u64(self) -> None:
        for epoch in (-1, 2**64, 1.5, True, "1"):
            with self.subTest(epoch=epoch):
                self.refused(epoch=epoch)
        self.assertEqual(self.accepted(epoch=2**64 - 1).epoch, 2**64 - 1)
        self.assertEqual(self.accepted(epoch=0).epoch, 0)

    def test_limits_must_carry_the_clients_own_hello_and_a_frame_header(self) -> None:
        hello_len = 12 + len(build_request("hello", {"client": "test-client", "proto": {"major": 1, "minor": 1}}))
        limits = hello_result()["limits"]
        self.refused(limits={**limits, "max_request_bytes": hello_len - 1})
        self.refused(limits={**limits, "max_request_bytes": 0})
        self.refused(limits={**limits, "max_frame_bytes": 11})
        conn = self.accepted(limits={**limits, "max_request_bytes": hello_len, "max_frame_bytes": 12})
        self.assertEqual(conn.limits["max_request_bytes"], hello_len)

    def test_subscribe_result_fields_must_be_exact_integers(self) -> None:
        for result in ({"sub": 2.0, "interval_ms": 1000}, {"sub": 2, "interval_ms": 1000.0}, {"sub": True, "interval_ms": 1000}):
            rig = Rig()
            self.addCleanup(rig.close)
            conn = rig.connect()
            rig.probe.rep_ok(2, result)
            with self.subTest(result=result), self.assertRaises(ProtocolError):
                conn.subscribe({"entities": [{"all": True}], "metrics": ["m"]}, 1000)


class RigCase(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.probe = self.rig.probe
        self.clock = self.rig.clock
        self.addCleanup(self.rig.close)

    def connect(self, **kw):
        conn = self.rig.connect(**kw)
        self.addCleanup(conn.close)
        return conn


class DeadlineBoundaries(RigCase):
    """A deadline fires when ``now > deadline``, strictly (WIRE.md 5): equality is not late."""

    def test_a_reply_at_exactly_the_deadline_is_accepted(self) -> None:
        conn = self.connect(timeout_s=3.0)
        self.probe.rep_ok(2, {"n": 1})
        self.rig.client.on_send = lambda _data: self.clock.advance(3 * NS)  # now == deadline after the send
        self.assertEqual(conn.request("status", {}), {"n": 1})
        self.assertFalse(conn.closed)

    def test_one_nanosecond_later_it_is_too_late(self) -> None:
        conn = self.connect(timeout_s=3.0)
        self.probe.rep_ok(2, {"n": 1})
        self.rig.client.on_send = lambda _data: self.clock.advance(3 * NS + 1)
        with self.assertRaises(OperationTimeout):
            conn.request("status", {})
        self.assertTrue(conn.closed)

    def test_a_socket_timeout_at_exactly_the_deadline_is_not_yet_a_timeout(self) -> None:
        conn = self.connect(timeout_s=3.0)
        self.rig.client.exact = True
        before = len(self.rig.client.timeouts)
        with self.assertRaises(OperationTimeout):
            conn.request("status", {})
        # send 3.0 s, recv 3.0 s (clock lands on the deadline: not late), recv at the 1 ms floor (late)
        self.assertEqual(self.rig.client.timeouts[before:], [3.0, 3.0, 0.001])

    def test_timeouts_stay_positive_when_the_deadline_is_already_reached(self) -> None:
        clock = Clock()
        step = 2 * NS

        def stepping() -> int:
            value = clock.now
            clock.now += step
            return value

        rig = Rig()
        self.addCleanup(rig.close)
        rig.probe.prefix_reply()
        rig.probe.rep_ok(1, hello_result())
        with self.assertRaises(OperationTimeout):
            Connection(rig.client, "observe", client="c", timeout_s=2.0, clock=stepping)
        self.assertTrue(rig.client.timeouts)
        self.assertEqual(set(rig.client.timeouts), {0.001})  # every call was at or past its deadline


class Bounds(RigCase):
    def open_stream(self, conn):
        self.probe.rep_ok(2, {})
        return conn.blocks("series_batch", {})

    def feed(self, count: int) -> None:
        self.probe.block(2, True)
        for i in range(count - 1):
            self.probe.block(2, False, variant=i % 2)

    def test_query_queue_count_bound_is_inclusive(self) -> None:
        with mock.patch.object(conn_module, "QUERY_QUEUE", 3):
            conn = self.connect()
            stream = self.open_stream(conn)
            self.feed(3)
            self.probe.rep_ok(3, {})
            conn.request("status", {})
            self.assertEqual(len(conn._streams[2].queue), 3)
            self.assertEqual([next(stream).header.n_rows for _ in range(3)], [3, 3, 3])

    def test_query_queue_overflows_one_block_past_the_bound(self) -> None:
        with mock.patch.object(conn_module, "QUERY_QUEUE", 3):
            conn = self.connect()
            self.open_stream(conn)
            self.feed(4)
            self.probe.rep_ok(3, {})
            with self.assertRaises(ProtocolError) as raised:
                conn.request("status", {})
            self.assertEqual(raised.exception.kind, "queue-overflow")
            self.assertTrue(conn.closed)

    def test_query_queue_is_bounded_in_bytes_and_freed_by_reading(self) -> None:
        first, bare = len(block_frame(2, True)), len(block_frame(2, False))
        with mock.patch.object(conn_module, "QUERY_BYTES", first + bare):
            conn = self.connect()
            stream = self.open_stream(conn)
            self.feed(2)
            self.probe.rep_ok(3, {})
            conn.request("status", {})  # exactly the bound: accepted
            self.assertEqual(conn._query_bytes, first + bare)
            next(stream)
            self.assertEqual(conn._query_bytes, bare)
            self.probe.block(2, False)
            self.probe.rep_ok(4, {})
            conn.request("status", {})  # room again after one block was read
            self.probe.block(2, False)
            self.probe.rep_ok(5, {})
            with self.assertRaises(ProtocolError) as raised:
                conn.request("status", {})
            self.assertEqual(raised.exception.kind, "queue-overflow")
            self.assertEqual(conn._query_bytes, 0)

    def test_corr_space_ends_at_u32_max(self) -> None:
        conn = self.connect()
        conn._next_corr = 2**32 - 1
        self.probe.rep_ok(2**32 - 1, {"last": True})
        self.assertEqual(conn.request("status", {}), {"last": True})
        with self.assertRaises(ProtocolError) as raised:
            conn.request("status", {})
        self.assertEqual(raised.exception.kind, "corr-exhausted")
        self.assertTrue(conn.closed)

    def test_unsubscribe_not_found_is_swallowed_other_errors_are_not(self) -> None:
        for code, raises in (("not-found", False), ("internal", True)):
            rig = Rig()
            self.addCleanup(rig.close)
            conn = rig.connect()
            rig.probe.rep_ok(2, {"sub": 2, "interval_ms": 1000})
            sub = conn.subscribe({"entities": [{"all": True}], "metrics": ["m"]}, 1000)
            rig.probe.rep_err(3, code)
            with self.subTest(code=code):
                if raises:
                    with self.assertRaises(ServerError):
                        sub.close()
                else:
                    sub.close()
                    sub.close()

    def test_client_dropped_grows_only_while_another_call_pumps(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {"sub": 2, "interval_ms": 1000})
        sub = conn.subscribe({"entities": [{"all": True}], "metrics": ["m"]}, 1000)
        self.probe.block(2, True)
        for i in range(11):
            self.probe.block(2, False, variant=i % 2)
        next(sub)  # returns after the first block: the rest are still in the socket
        self.assertEqual(sub.client_dropped, 0)
        self.probe.rep_ok(3, {})
        conn.request("status", {})  # pumps everything that was waiting
        self.assertEqual(sub.client_dropped, 3)  # 11 more arrived behind 0 queued: 11 > 8 kept


class Tables(unittest.TestCase):
    def test_refusal_codes_and_fatal_codes_are_the_documented_sets(self) -> None:
        self.assertEqual(
            conn_module.FATAL_CODES,
            {"unknown-mode", "binary-required", "unsupported-version", "hello-required", "bad-frame",
             "frame-too-large", "deadline", "idle", "slow-client", "shutting-down"},
        )
        self.assertEqual(
            profiling.REFUSED_CODES, {"forbidden", "unsupported-feature", "unsupported-version", "unknown-verb"}
        )

    def test_each_refusing_server_code_is_refused_and_others_unreachable(self) -> None:
        def raising(error):
            def opener(*_a, **_k):
                raise error

            return opener

        env = {"HOSTOBS_OBSERVE_SOCKET": "/x"}
        for code in sorted(profiling.REFUSED_CODES):
            state = profiling.require_profiling("observe", True, environ=env, opener=raising(ServerError(code)))
            self.assertEqual(state.name, "refused", code)
        for code in ("internal", "bad-request", "shutting-down", "deadline"):
            state = profiling.require_profiling("observe", True, environ=env, opener=raising(ServerError(code)))
            self.assertEqual(state.name, "unreachable", code)


if __name__ == "__main__":
    unittest.main()
