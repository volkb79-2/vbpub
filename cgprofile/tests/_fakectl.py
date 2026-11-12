"""A scriptable fake ctl daemon on a real AF_UNIX socket (shared by the cgprofilectl and ctlwire tests).

Each handler is ``handler(args, contract) -> reply`` where a reply is a dict (one JSON line), raw
``bytes`` (sent as is), or a list of those (several lines). A handler may also raise to drop the
connection. Every request line received is recorded in ``daemon.requests``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
from typing import Any, Callable, Dict, List

V1_VERSION = {"ok": True, "contract": 1, "cgprofile": "1.2.0"}
V2_VERSION = {
    "ok": True, "contract": 2, "contracts": [1, 2],
    "daemon": {"version": "2.0.0", "started_at": "2026-11-04T10:00:00Z", "damon": "available",
               "damon_default": "off", "sessions_live": 1, "max_sessions": 4},
    "resource_observer": {"contracts": [1, 2, 3], "path": "/tmp/cgprofile-observer.sock"},
    "config": {"overrides": 0},
}
CONFIG_DOC = {"ok": True, "contract": 2, "config": {
    "file": "/etc/cgprofile/cgprofiled.toml", "digest": "sha256:abc", "loaded_at": "2026-11-04T10:00:00Z",
    "reloads": 0, "last_reload_error": None, "overrides_file": None,
    "keys": {"allow_uids": {"value": [0, 1000], "source": "file"},
             "interval": {"value": 1.0, "source": "default"}}}}


def export_reply(parts: Dict[str, bytes], *, tamper: str = "") -> List[Any]:
    """The §10.3 export stream: header, data lines, end line."""
    header = {"ok": True, "contract": 2, "parts": [
        {"name": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in parts.items()]}
    out: List[Any] = [header]
    for name, body in parts.items():
        out.append({"part": name, "data": base64.b64encode(body).decode()})
    out.append({"event": "end", "status": "complete"})
    return out


class FakeDaemon:
    def __init__(self, path: str, handlers: Dict[str, Callable[[Dict[str, Any], int], Any]]) -> None:
        self.path = path
        self.handlers = handlers
        self.requests: List[Dict[str, Any]] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(path)
        self._server.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            try:
                request = json.loads(data)
            except ValueError:
                request = {}
            self.requests.append(request)
            handler = self.handlers.get(request.get("verb"))
            if handler is None:
                reply: Any = {"ok": False, "error": {"code": "bad-argument", "message": "unknown verb"}}
            else:
                try:
                    reply = handler(request.get("args") or {}, request.get("contract", 1))
                except Exception:  # noqa: BLE001 - a handler raising drops the connection
                    return
            for item in reply if isinstance(reply, list) else [reply]:
                raw = item if isinstance(item, bytes) else (json.dumps(item) + "\n").encode()
                try:
                    conn.sendall(raw)
                except OSError:
                    return

    def close(self) -> None:
        self._server.close()
        if os.path.exists(self.path):
            os.unlink(self.path)
