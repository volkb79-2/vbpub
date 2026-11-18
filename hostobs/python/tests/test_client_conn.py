#!/usr/bin/env python3
"""`hostobs_client.conn` against the scripted fake probe of `fake_probe.py` (no live probe).

    python3 -I -m unittest discover -s python/tests -p 'test_client_conn.py' -v
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_probe import (  # noqa: E402
    BLOCK, DICT, ERROR_CODES, EVT, NS, REP, REQ, Rig, hello_result, json_bytes, raw_frame,
)
from hostobs_client.conn import COMPLETION_NS, Connection, OperationTimeout, ProtocolError  # noqa: E402
from hostobs_client.envelope import EnvelopeError  # noqa: E402
from hostobs_client.wire import ConnectionLost, ServerError, WireError  # noqa: E402

DOCUMENTED_FATAL = frozenset(
    ("unknown-mode", "unsupported-version", "hello-required", "bad-frame", "frame-too-large", "deadline",
     "idle", "slow-client", "shutting-down")
)
SELECTION = {"entities": [{"all": True}], "metrics": ["cpu.usage_usec"]}


class RigCase(unittest.TestCase):
    max_chunk = None

    def setUp(self) -> None:
        self.rig = Rig(self.max_chunk)
        self.probe = self.rig.probe
        self.clock = self.rig.clock
        self.addCleanup(self.rig.close)

    def connect(self, **kw):
        conn = self.rig.connect(**kw)
        self.addCleanup(conn.close)
        return conn


class Handshake(RigCase):
    def test_hello_handshake_fields_and_request(self) -> None:
        conn = self.connect(level="operator")
        self.assertEqual((conn.level, conn.minor, conn.epoch, conn.boot_id), ("operator", 1, 1, hello_result()["boot_id"]))
        self.assertEqual(conn.session, "first")
        self.assertEqual(conn.limits["max_outstanding"], 4)
        self.assertEqual(conn.features, ["peak_reset"])
        self.assertEqual(conn.verbs, ["hello", "status"])
        self.assertEqual(self.probe.requests(), [(1, "hello", {"client": "test-client", "proto": {"major": 1, "minor": 1}})])
        self.assertEqual(self.probe.prefix, b"HOB\x01\x01\x00\x01\x00")

    def test_negotiated_minor_zero(self) -> None:
        self.assertEqual(self.connect(minor=0).minor, 0)

    def test_server_major_two_is_unsupported_version_and_closes(self) -> None:
        self.probe.prefix_reply(2, 9)
        with self.assertRaises(WireError) as raised:
            from hostobs_client.conn import Connection

            Connection(self.rig.client, "observe", client="c", timeout_s=1, clock=self.clock)
        self.assertEqual(raised.exception.kind, "unsupported-version")
        self.assertTrue(self.rig.client.closed)

    def test_bad_magic_is_unknown_mode(self) -> None:
        self.probe.send(b'{"ok":fa')
        with self.assertRaises(WireError) as raised:
            Connection(self.rig.client, "observe", client="c", timeout_s=1, clock=self.clock)
        self.assertEqual(raised.exception.kind, "unknown-mode")

    def test_eof_before_prefix_is_connection_lost(self) -> None:
        self.probe.sock.close()
        from hostobs_client.conn import Connection

        with self.assertRaises(ConnectionLost):
            Connection(self.rig.client, "observe", client="c", timeout_s=1, clock=self.clock)
        self.assertTrue(self.rig.client.closed)

    def test_silent_server_times_out_on_the_fake_clock(self) -> None:
        from hostobs_client.conn import Connection

        before = self.clock.now
        with self.assertRaises(OperationTimeout):
            Connection(self.rig.client, "observe", client="c", timeout_s=2.5, clock=self.clock)
        self.assertGreater(self.clock.now - before, 2.5 * NS)
        self.assertAlmostEqual(self.rig.client.timeouts[1], 2.5, delta=0.01)
        self.assertTrue(self.rig.client.closed)

    def test_hello_error_reply_raises_server_error(self) -> None:
        from hostobs_client.conn import Connection

        self.probe.prefix_reply()
        self.probe.rep_err(1, "hello-required", "x")
        with self.assertRaises(ServerError) as raised:
            Connection(self.rig.client, "observe", client="c", timeout_s=1, clock=self.clock)
        self.assertEqual(raised.exception.code, "hello-required")
        self.assertTrue(self.rig.client.closed)

    def test_hello_shape_violations(self) -> None:
        from hostobs_client.conn import Connection

        bad = [
            hello_result(level="operator"),
            hello_result(epoch="1"),
            hello_result(proto={"major": 1, "minor": 0}),
            hello_result(limits={}),
            hello_result(features="peak_reset"),
        ]
        for result in bad:
            rig = Rig()
            rig.probe.prefix_reply()
            rig.probe.rep_ok(1, result)
            with self.subTest(result=result), self.assertRaises(ProtocolError) as raised:
                Connection(rig.client, "observe", client="c", timeout_s=1, clock=rig.clock)
            self.assertEqual(raised.exception.kind, "hello-mismatch")
            rig.close()

    def test_server_minor_above_client_is_refused(self) -> None:
        from hostobs_client.conn import Connection

        self.probe.prefix_reply(1, 2)
        with self.assertRaises(ProtocolError):
            Connection(self.rig.client, "observe", client="c", timeout_s=1, clock=self.clock)

    def test_hello_limit_lowers_the_frame_limit(self) -> None:
        conn = self.connect(limits={**hello_result()["limits"], "max_frame_bytes": 1024})
        self.probe.send(struct.pack("<IBBHI", 2000, REP, 0, 0, 2))
        with self.assertRaises(WireError) as raised:
            conn.request("status", {})
        self.assertEqual(raised.exception.kind, "oversized-frame")

    def test_argument_validation(self) -> None:
        from hostobs_client.conn import Connection

        for kw in ({"level": "root"}, {"client": ""}, {"client": "x" * 65}, {"timeout_s": 0}):
            args = {"level": "observe", "client": "c", "timeout_s": 1.0, **kw}
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                Connection(self.rig.client, args.pop("level"), **args)


class Requests(RigCase):
    def test_request_reply_and_monotonic_corr(self) -> None:
        conn = self.connect()
        self.probe.requests()
        for expect in (2, 3, 4):
            self.probe.rep_ok(expect, {"n": expect})
            self.assertEqual(conn.request("status", {}), {"n": expect})
        self.assertEqual([c for c, _v, _a in self.probe.requests()], [2, 3, 4])

    def test_every_error_code_raises_server_error(self) -> None:
        for code in ERROR_CODES:
            with self.subTest(code=code):
                rig = Rig()
                conn = rig.connect()
                rig.probe.rep_err(2, code, "msg", {"pool": "rows"})
                with self.assertRaises(ServerError) as raised:
                    conn.request("status", {})
                self.assertEqual((raised.exception.code, raised.exception.message), (code, "msg"))
                self.assertEqual(raised.exception.detail, {"pool": "rows"})
                self.assertEqual(conn.closed, code in DOCUMENTED_FATAL)  # WIRE.md 5.4, spelled out here
                rig.close()

    def test_non_fatal_error_keeps_the_connection(self) -> None:
        conn = self.connect()
        self.probe.rep_err(2, "too-many-requests")
        with self.assertRaises(ServerError):
            conn.request("status", {})
        self.probe.rep_ok(3, {})
        self.assertEqual(conn.request("status", {}), {})

    def test_corr_zero_error_reply_is_fatal(self) -> None:
        conn = self.connect()
        self.probe.rep_err(0, "bad-frame")
        with self.assertRaises(ServerError) as raised:
            conn.request("status", {})
        self.assertEqual(raised.exception.code, "bad-frame")
        self.assertTrue(conn.closed)

    def test_corr_zero_ok_reply_and_unknown_corr_are_protocol_errors(self) -> None:
        for corr in (0, 9):
            rig = Rig()
            conn = rig.connect()
            rig.probe.rep_ok(corr)
            with self.subTest(corr=corr), self.assertRaises(ProtocolError):
                conn.request("status", {})
            self.assertTrue(conn.closed)
            rig.close()

    def test_unsolicited_block_and_end_are_protocol_errors(self) -> None:
        conn = self.connect()
        self.probe.block(7, True)
        with self.assertRaises(ProtocolError) as raised:
            conn.request("status", {})
        self.assertEqual(raised.exception.kind, "unknown-corr")

    def test_local_request_validation_sends_nothing(self) -> None:
        conn = self.connect()
        self.probe.requests()
        big = {"x": "a" * 40_000}
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}
        for verb, args in (("status", big), ("sta\\tus", {}), ("", {}), ("status", {"n": float("nan")}), ("status", deep)):
            with self.subTest(verb=verb), self.assertRaises(ValueError):
                conn.request(verb, args)
        with self.assertRaises(ValueError):
            conn.request("status", [])  # type: ignore[arg-type]
        self.assertEqual(self.probe.requests(), [])
        self.assertFalse(conn.closed)

    def test_request_timeout_uses_the_fake_clock_and_closes(self) -> None:
        conn = self.connect(timeout_s=3.0)
        start = self.clock.now
        with self.assertRaises(OperationTimeout):
            conn.request("status", {})
        self.assertGreaterEqual(self.clock.now - start, 3 * NS)
        self.assertTrue(conn.closed)
        with self.assertRaises(ConnectionLost):
            conn.request("status", {})

    def test_reply_bytes_one_at_a_time(self) -> None:
        self.rig.client.max_chunk = 1
        conn = self.connect()
        self.probe.rep_ok(2, {"a": [1, 2, 3]})
        self.assertEqual(conn.request("status", {}), {"a": [1, 2, 3]})

    def test_partial_frame_is_bounded_by_the_completion_age(self) -> None:
        conn = self.connect(timeout_s=1.0)
        self.probe.send(raw_frame(REP, 2, b'{"ok":true,"result":{}}')[:15])
        with self.assertRaises(WireError) as raised:
            conn.request("status", {})
        self.assertEqual(raised.exception.kind, "deadline")
        self.assertAlmostEqual(self.rig.client.timeouts[-1], COMPLETION_NS / NS, delta=1.0)
        self.assertTrue(conn.closed)

    def test_eof_mid_frame_and_clean_eof_and_reset(self) -> None:
        conn = self.connect()
        self.probe.send(raw_frame(REP, 2, b'{"ok":true,"result":{}}')[:20])
        self.probe.sock.shutdown(socket.SHUT_WR)
        with self.assertRaises(ConnectionLost) as raised:
            conn.request("status", {})
        self.assertIsInstance(raised.exception.__cause__, WireError)
        self.assertEqual(raised.exception.__cause__.kind, "truncated")
        rig = Rig()
        conn = rig.connect()
        rig.probe.send(raw_frame(EVT, 0, json_bytes({"event": "closing", "reason": "idle"})))
        rig.probe.sock.shutdown(socket.SHUT_WR)
        with self.assertRaises(ConnectionLost) as raised:
            conn.request("status", {})
        self.assertIn("idle", str(raised.exception))
        self.assertEqual(conn.closing_reason, "idle")
        rig.close()
        rig = Rig()
        conn = rig.connect()
        rig.client.reset_after = rig.client.read
        with self.assertRaises(ConnectionLost):
            conn.request("status", {})
        rig.close()

    def test_hostile_frames(self) -> None:
        cases = {
            "unknown-frame-type": struct.pack("<IBBHI", 12, 9, 0, 0, 2),
            "unknown-frame-flags": struct.pack("<IBBHI", 12, 2, 4, 0, 2),
            "non-zero-reserved": struct.pack("<IBBHI", 12, 2, 0, 1, 2),
            "oversized-frame": struct.pack("<IBBHI", 0xFFFFFFFF, 2, 0, 0, 2),
            "truncated": struct.pack("<IBBHI", 11, 2, 0, 0, 2),
        }
        for kind, data in cases.items():
            rig = Rig()
            conn = rig.connect()
            rig.probe.send(data)
            with self.subTest(kind=kind), self.assertRaises(WireError) as raised:
                conn.request("status", {})
            self.assertEqual(raised.exception.kind, kind)
            self.assertTrue(conn.closed)
            rig.close()

    def test_wrong_direction_frames_are_protocol_errors(self) -> None:
        for kind in (REQ, DICT):
            rig = Rig()
            conn = rig.connect()
            rig.probe.send(raw_frame(kind, 2, b"{}"))
            with self.subTest(kind=kind), self.assertRaises(ProtocolError) as raised:
                conn.request("status", {})
            self.assertEqual(raised.exception.kind, "unexpected-frame")
            rig.close()

    def test_hostile_reply_text(self) -> None:
        nest = lambda depth: b'{"ok":true,"result":' + b'{"a":' * depth + b"1" + b"}" * depth + b"}"  # noqa: E731
        escape = b"\\" + b"u"  # a unicode escape is spelled at run time: the editor may fold a literal one
        escaped_extra = b'{"ok":true,"result":{},"' + escape + b'0078":1}'
        escaped_ok = b'{"' + escape + b'006fk":true,"result":{}}'
        cases = {
            "duplicate-key": [b'{"ok":true,"result":{"a":1,"a":2}}', b'{"ok":true,"ok":true,"result":{}}'],
            "json-text": [
                nest(8), nest(40), b'{"ok":true,"result":{}} ', b' {"ok":true,"result":{}}',
                b'{"ok":true,"result":{"n":NaN}}', b'{"ok":true,"result":{"n":1e999}}',
                b'{"ok":true,"result":{"n":-0}}', b"\xef\xbb\xbf" + b'{"ok":true,"result":{}}',
                b'{"ok":true,"result":{"s":"\xff"}}', b'{"ok":true,"result":{}}\n',
                b'{"ok":true,"result":{"a":[' + b"[" * 5000 + b"]" * 5000 + b"]}}",
            ],
            "unknown-key": [b'{"ok":true,"result":{},"corr":2}', escaped_extra, b'{"ok":true,"result":{},"x":1}'],
            "wrong-type": [b'{"ok":"true","result":{}}', b'{"ok":true,"result":[]}', b'{"ok":false,"error":{"code":1,"message":"","detail":{}}}'],
            "missing-key": [b'{"ok":true}', escaped_ok, b'{"ok":false,"error":{"code":"x","message":"y"}}'],
        }
        for kind, payloads in cases.items():
            for payload in payloads:
                rig = Rig()
                conn = rig.connect()
                rig.probe.send(raw_frame(REP, 2, payload))
                with self.subTest(kind=kind, payload=payload[:60]):
                    with self.assertRaises(EnvelopeError) as raised:
                        conn.request("status", {})
                    self.assertEqual(raised.exception.kind, kind)
                    self.assertTrue(conn.closed)
                rig.close()

    def test_depth_bound_is_exactly_eight(self) -> None:
        conn = self.connect()
        ok = b'{"ok":true,"result":' + b'{"a":' * 7 + b"1" + b"}" * 7 + b"}"  # depth 8
        self.probe.send(raw_frame(REP, 2, ok))
        self.assertEqual(list(conn.request("status", {})), ["a"])

    def test_closed_is_idempotent_and_context_manager(self) -> None:
        conn = self.connect()
        conn.close()
        conn.close()
        self.assertTrue(conn.closed and self.rig.client.closed)
        with self.assertRaises(ConnectionLost):
            conn.request("status", {})
        rig = Rig()
        with rig.connect() as inner:
            pass
        self.assertTrue(inner.closed)
        rig.close()

    def test_no_file_descriptor_leak(self) -> None:
        def count() -> int:
            return len(os.listdir("/proc/self/fd"))

        # The TEST keeps the probe end of every pair open until after the count, so only the
        # connection's own close() can release the client end: a leaked client socket stays counted.
        before = count()
        rigs = []
        for scenario in range(6):
            rig = Rig()
            rigs.append(rig)
            conn = rig.connect()
            if scenario == 1:
                rig.probe.send(struct.pack("<IBBHI", 12, 9, 0, 0, 2))
                with self.assertRaises(WireError):
                    conn.request("status", {})
            elif scenario == 2:
                rig.probe.rep_err(0, "idle")
                with self.assertRaises(ServerError):
                    conn.request("status", {})
            conn.close()
        for _ in range(2):
            rig = Rig()
            rigs.append(rig)
            rig.probe.sock.shutdown(socket.SHUT_WR)  # EOF without releasing the probe descriptor
            with self.assertRaises(ConnectionLost):
                Connection(rig.client, "observe", client="c", timeout_s=1, clock=rig.clock)
        try:
            self.assertEqual(count(), before + len(rigs), "only the probe ends the test holds remain open")
        finally:
            for rig in rigs:
                rig.close()
        self.assertEqual(count(), before)


class Blocks(RigCase):
    def test_block_verb_blocks_and_end_counts(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {"stream": {"tick_ms": 1000}})
        self.probe.block(2, True)
        self.probe.block(2, False, variant=1)
        self.probe.end(2, 2)
        stream = conn.blocks("current", {"selection": SELECTION})
        self.assertEqual(stream.result, {"stream": {"tick_ms": 1000}})
        got = list(stream)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].dictionary.generation, 1)
        self.assertIsNone(got[1].dictionary)
        self.assertEqual(stream.end["reason"], "complete")
        self.assertEqual(stream.received, 2)
        self.assertEqual(conn._dicts.stream_count(), 0)

    def test_page_end_with_cursor_and_continuation_is_a_new_stream(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {})
        self.probe.block(2, True)
        self.probe.end(2, 1, reason="page", nxt="cur")
        first = conn.blocks("series_batch", {})
        list(first)
        self.assertEqual(first.end["next"], "cur")
        self.probe.rep_ok(3, {})
        self.probe.block(3, True)
        self.probe.end(3, 1)
        self.assertEqual(len(list(conn.blocks("series_batch", {"cursor": "cur"}))), 1)

    def test_error_reply_for_a_block_verb_has_no_end(self) -> None:
        conn = self.connect()
        self.probe.rep_err(2, "not-found")
        with self.assertRaises(ServerError):
            conn.blocks("snapshot_at", {})
        self.assertFalse(conn.closed)

    def test_end_error_after_blocks(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {})
        self.probe.block(2, True)
        self.probe.end_err(2, 1)
        stream = conn.blocks("current", {})
        next(stream)
        with self.assertRaises(ServerError) as raised:
            next(stream)
        self.assertEqual(raised.exception.code, "internal")

    def test_end_count_mismatches(self) -> None:
        for blocks, dropped in ((1, 0), (3, 0), (2, 1)):
            rig = Rig()
            conn = rig.connect()
            rig.probe.rep_ok(2, {})
            rig.probe.block(2, True)
            rig.probe.block(2, False)
            rig.probe.end(2, blocks, dropped)
            stream = conn.blocks("current", {})
            with self.subTest(blocks=blocks, dropped=dropped), self.assertRaises(ProtocolError) as raised:
                list(stream)
            self.assertEqual(raised.exception.kind, "end-counts")
            self.assertTrue(conn.closed)
            rig.close()

    def test_end_reason_must_fit_the_stream(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {})
        self.probe.end(2, 0, reason="unsubscribed")
        with self.assertRaises(ProtocolError):
            list(conn.blocks("current", {}))

    def test_bare_first_block_and_regressed_dictionary_are_wire_errors(self) -> None:
        for second, kind in ((None, "stale-dictionary-generation"), (True, "generation-regressed")):
            rig = Rig()
            conn = rig.connect()
            rig.probe.rep_ok(2, {})
            if second is True:
                rig.probe.block(2, True)
            rig.probe.block(2, second is True and True or False)
            stream = conn.blocks("current", {})
            with self.subTest(kind=kind), self.assertRaises(WireError) as raised:
                list(stream)
            self.assertEqual(raised.exception.kind, kind)
            self.assertTrue(conn.closed)
            rig.close()

    def test_closed_block_stream_discards_remaining_blocks(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {})
        self.probe.block(2, True)
        self.probe.block(2, False)
        self.probe.end(2, 2)
        stream = conn.blocks("current", {})
        next(stream)
        stream.close()
        self.assertEqual(list(stream), [])
        self.probe.rep_ok(3, {})
        conn.request("status", {})  # pumps the rest of stream 2 without decoding it
        self.assertFalse(conn.closed)
        self.assertEqual(conn._streams, {})


class Subscriptions(RigCase):
    def subscribe(self, conn, interval_ms=1000):
        self.probe.rep_ok(2, {"sub": 2, "interval_ms": interval_ms})
        return conn.subscribe(SELECTION, interval_ms)

    def test_subscribe_iterates_blocks_with_dropped_counters_and_unsubscribes(self) -> None:
        conn = self.connect()
        sub = self.subscribe(conn)
        self.assertEqual(sub.corr, 2)
        self.probe.block(2, True)
        self.probe.block(2, False, dropped=2, variant=1)
        self.assertEqual(next(sub).header.dropped, 0)
        self.assertEqual(next(sub).header.dropped, 2)
        self.assertEqual(sub.dropped, 2)
        self.probe.rep_ok(3, {})
        self.probe.end(2, 2, dropped=1, reason="unsubscribed")
        sub.close()
        sub.close()
        self.assertEqual(sub.reason, "unsubscribed")
        self.assertEqual(sub.dropped, 3)
        self.assertEqual(self.probe.requests()[-1], (3, "unsubscribe", {"sub": 2}))
        with self.assertRaises(StopIteration):
            next(sub)
        self.assertEqual(conn._dicts.stream_count(), 0)

    def test_unread_blocks_keep_the_newest_eight(self) -> None:
        conn = self.connect()
        sub = self.subscribe(conn)
        self.probe.block(2, True)
        for i in range(9):
            self.probe.block(2, False, variant=i % 2)
        self.probe.rep_ok(3, {})
        conn.request("status", {})
        self.assertEqual(sub.client_dropped, 2)
        self.assertEqual(sub.received, 10)
        self.assertEqual(sum(1 for _ in range(8) if next(sub)), 8)

    def test_wait_for_a_block_is_bounded_by_timeout_plus_interval(self) -> None:
        conn = self.connect(timeout_s=2.0)
        sub = self.subscribe(conn, interval_ms=5000)
        start = self.clock.now
        with self.assertRaises(OperationTimeout):
            next(sub)
        self.assertGreaterEqual(self.clock.now - start, 7 * NS)

    def test_end_error_on_a_subscription(self) -> None:
        conn = self.connect()
        sub = self.subscribe(conn)
        self.probe.end_err(2, 0)
        with self.assertRaises(ServerError):
            next(sub)

    def test_client_eof_end_stops_iteration(self) -> None:
        conn = self.connect()
        sub = self.subscribe(conn)
        self.probe.end(2, 0, reason="client-eof")
        with self.assertRaises(StopIteration):
            next(sub)

    def test_subscribe_result_must_name_the_corr(self) -> None:
        conn = self.connect()
        self.probe.rep_ok(2, {"sub": 99, "interval_ms": 1000})
        with self.assertRaises(ProtocolError):
            conn.subscribe(SELECTION, 1000)
        self.assertTrue(conn.closed)

    def test_connection_loss_surfaces_on_the_subscription(self) -> None:
        conn = self.connect()
        sub = self.subscribe(conn)
        conn.close()
        with self.assertRaises(ConnectionLost):
            next(sub)
        sub.close()


if __name__ == "__main__":
    unittest.main()
