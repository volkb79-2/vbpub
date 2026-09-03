"""Tests for rcon_probe.py's Source RCON packet framing.

This module had no test coverage at all before: the signed/unsigned
struct-packing bug (req_id packed/unpacked as unsigned `<III`/`<II`
instead of signed `<Iii`/`<ii`) went unnoticed because nothing exercised
_pack/_recv_packet's round-trip, especially the -1 auth-failure sentinel.
"""
import os
import struct
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
import rcon_probe as rp


def _packet_bytes(req_id: int, pkt_type: int, body: str) -> bytes:
    """Build raw wire bytes for a Source RCON packet (test-side encoder,
    independent of rp._pack, so these tests don't just check _pack against
    itself)."""
    payload = body.encode() + b'\x00\x00'
    return struct.pack('<Iii', 4 + 4 + len(payload), req_id, pkt_type) + payload


class FakeSocket:
    """Minimal socket stand-in: serves recv() from a pre-loaded buffer."""

    def __init__(self, data: bytes = b''):
        self._buf = data
        self.sent = []

    def recv(self, n):
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        pass

    def close(self):
        pass


class TestPack:
    def test_positive_req_id_roundtrips(self):
        packet = rp._pack(42, 2, 'hello')
        length = struct.unpack('<I', packet[:4])[0]
        req_id, pkt_type = struct.unpack('<ii', packet[4:12])
        assert length == len(packet) - 4
        assert req_id == 42
        assert pkt_type == 2
        assert packet[12:-2] == b'hello'
        assert packet[-2:] == b'\x00\x00'

    def test_negative_req_id_packs_as_signed(self):
        # The auth-failure sentinel: must round-trip as -1, not 4294967295.
        packet = rp._pack(-1, 2, '')
        req_id, _ = struct.unpack('<ii', packet[4:12])
        assert req_id == -1


class TestRecvPacket:
    def test_normal_packet(self):
        sock = FakeSocket(_packet_bytes(7, 2, 'ok'))
        req_id, pkt_type, body = rp._recv_packet(sock)
        assert req_id == 7
        assert pkt_type == 2
        assert body == 'ok'

    def test_auth_failure_sentinel_decodes_as_negative_one(self):
        # This is the exact bug this module had: unsigned unpacking decoded
        # the server's -1 auth-failure sentinel as 4294967295, so
        # `req_id == -1` in rcon_connect() could never fire.
        sock = FakeSocket(_packet_bytes(-1, 2, ''))
        req_id, _, _ = rp._recv_packet(sock)
        assert req_id == -1


class TestRecvn:
    def test_raises_on_early_close(self):
        sock = FakeSocket(b'ab')  # only 2 bytes available, 4 requested
        with pytest.raises(ConnectionError):
            rp._recvn(sock, 4)

    def test_accumulates_across_partial_reads(self):
        class SlowSocket(FakeSocket):
            def recv(self, n):
                # Always return at most 1 byte, forcing multiple recv() calls.
                chunk, self._buf = self._buf[:1], self._buf[1:]
                return chunk

        sock = SlowSocket(b'abcd')
        assert rp._recvn(sock, 4) == b'abcd'


class TestRconConnect:
    def test_auth_failure_raises_permission_error(self, monkeypatch):
        sock = FakeSocket(_packet_bytes(-1, 2, ''))
        monkeypatch.setattr(rp.socket, 'create_connection', lambda *a, **k: sock)
        with pytest.raises(PermissionError):
            rp.rcon_connect('127.0.0.1', 19000, 'wrongpass')

    def test_auth_success_returns_socket(self, monkeypatch):
        sock = FakeSocket(_packet_bytes(1, 2, ''))
        monkeypatch.setattr(rp.socket, 'create_connection', lambda *a, **k: sock)
        result = rp.rcon_connect('127.0.0.1', 19000, 'rightpass')
        assert result is sock


class TestReadProcStatusKb:
    def test_self_vmrss_is_positive(self):
        assert rp._read_proc_status_kb(os.getpid(), 'VmRSS') > 0

    def test_missing_pid_returns_zero(self):
        assert rp._read_proc_status_kb(999999999, 'VmRSS') == 0

    def test_missing_key_returns_zero(self):
        assert rp._read_proc_status_kb(os.getpid(), 'NoSuchStatusKey') == 0


class TestReadMemoryHigh:
    def test_empty_scope_returns_none(self):
        assert rp._read_memory_high('') is None

    def test_missing_path_returns_none(self):
        assert rp._read_memory_high('/nonexistent/cgroup/scope') is None

    def test_max_value_returns_none(self, tmp_path):
        (tmp_path / 'memory.high').write_text('max\n')
        assert rp._read_memory_high(str(tmp_path)) is None

    def test_numeric_value_parsed(self, tmp_path):
        (tmp_path / 'memory.high').write_text('1073741824\n')
        assert rp._read_memory_high(str(tmp_path)) == 1073741824
