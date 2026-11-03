"""A fake cgprofile observer socket for the B206 tests.

It serves the golden contract-3 fixtures (``tests/fixtures/observer/``), or any
canned reply, one request line per connection, exactly like the daemon.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any, Callable

FIXTURES = Path(__file__).parents[1] / "fixtures" / "observer"


def golden(name: str) -> dict[str, Any]:
    """One golden fixture: ``{"request": ..., "response": ...}``."""
    return json.loads((FIXTURES / f"observer-window-v3-{name}.json").read_text(encoding="utf-8"))


class FakeObserver:
    """Serves requests on a Unix socket until closed.

    *handler* gets the parsed request and returns the reply object, a raw
    ``bytes`` reply line, or ``None`` to stay silent (the client times out).
    Every parsed request is appended to :attr:`requests`.
    """

    def __init__(self, path: Path, handler: Callable[[dict[str, Any]], Any]) -> None:
        self.path = path
        self.requests: list[dict[str, Any]] = []
        self._handler = handler
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(16)
        self._stop = threading.Event()
        self._held: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(connection,), daemon=True).start()

    def _one(self, connection: socket.socket) -> None:
        try:
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            request = json.loads(buffer.split(b"\n", 1)[0])
            self.requests.append(request)
            reply = self._handler(request)
            if reply is None:
                self._held.append(connection)  # never answer; client times out
                return
            payload = (
                reply
                if isinstance(reply, bytes)
                else json.dumps(reply, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            connection.sendall(payload)
        except OSError:
            pass
        finally:
            if connection not in self._held:
                connection.close()

    def close(self) -> None:
        self._stop.set()
        self._server.close()
        for held in self._held:
            held.close()

    def __enter__(self) -> "FakeObserver":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def golden_handler(close_name: str = "close-cgroup") -> Callable[[dict[str, Any]], Any]:
    """Answer ``window-open`` and ``window-close`` with the golden replies."""
    opened = golden("open")["response"]
    closed = golden(close_name)["response"]

    def handle(request: dict[str, Any]) -> Any:
        if request["verb"] == "window-open":
            return {**opened, "mode": request["args"]["mode"]}
        if request["verb"] == "window-close":
            return closed
        return {"ok": False, "contract": 3, "error": {"code": "bad-argument", "message": "x"}}

    return handle


def error_reply(code: str, *, contract: int = 3) -> dict[str, Any]:
    return {"ok": False, "contract": contract, "error": {"code": code, "message": f"{code} for test"}}
