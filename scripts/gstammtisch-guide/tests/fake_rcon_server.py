"""Minimal Source RCON server for tests — real localhost socket, no docker/
nsenter/root involved. Accepts one connection at a time, checks the auth
password, then answers SERVERDATA_EXECCOMMAND packets via a caller-supplied
`responder(cmd) -> str` callable."""
from __future__ import annotations

import socket
import struct
import threading


def pack(req_id: int, pkt_type: int, body: str) -> bytes:
    payload = body.encode() + b"\x00\x00"
    header = struct.pack("<Iii", 4 + 4 + len(payload), req_id, pkt_type)
    return header + payload


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def recv_packet(sock: socket.socket):
    length = struct.unpack("<I", _recvn(sock, 4))[0]
    data = _recvn(sock, length)
    req_id, pkt_type = struct.unpack("<ii", data[:8])
    return req_id, pkt_type, data[8:-2].decode("utf-8", errors="replace")


class FakeRconServer:
    """A tiny real Source RCON server bound to 127.0.0.1:<ephemeral port>.

    `responder(cmd)` may return a string reply, or raise to force the
    connection closed (simulating a server-side drop, for reconnect tests).
    `drop_after` closes the connection after that many successful commands
    on one connection, independent of the responder.
    """

    def __init__(self, password="secret", responder=None, drop_after=None):
        self.password = password
        self.responder = responder or (lambda cmd: f"echo:{cmd}")
        self.drop_after = drop_after
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket):
        count = 0
        try:
            req_id, _, body = recv_packet(conn)
            if body != self.password:
                conn.sendall(pack(-1, 2, ""))
                return
            conn.sendall(pack(req_id, 2, ""))
            while True:
                req_id, _, cmd = recv_packet(conn)
                reply = self.responder(cmd)
                conn.sendall(pack(req_id, 0, reply))
                count += 1
                if self.drop_after is not None and count >= self.drop_after:
                    return
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)
