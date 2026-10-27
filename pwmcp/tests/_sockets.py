"""Raw-socket helpers for tests: never assume one recv() returns a whole HTTP response.

A server may write the status line + headers and the body in separate packets (and a slow host, or a
coverage run, makes that visible). Read to the declared Content-Length, or to EOF.
"""

from __future__ import annotations

import re
import socket


def read_http_response(sock: socket.socket, first: bytes = b"", timeout: float = 10.0) -> bytes:
    """The complete HTTP response: headers plus ``Content-Length`` body bytes (or everything up to EOF).

    ``first`` is data already received by the caller (for example the first chunk of a polling loop).
    """

    sock.settimeout(timeout)
    data = first
    while True:
        head, separator, body = data.partition(b"\r\n\r\n")
        if separator:
            declared = re.search(rb"(?i)\r\ncontent-length:\s*(\d+)", head)
            if declared is None or len(body) >= int(declared.group(1)):
                return data
        chunk = sock.recv(65536)
        if not chunk:
            return data
        data += chunk


def read_to_eof(sock: socket.socket, timeout: float = 10.0) -> bytes:
    sock.settimeout(timeout)
    data = b""
    while chunk := sock.recv(65536):
        data += chunk
    return data
