"""A minimal blocking QMP client over a unix socket (QMP serves ONE client at a time)."""
from __future__ import annotations

import json
import socket
from typing import Any

from .errors import QmpError


class QmpClient:
    def __init__(self, path: str, timeout: float = 10.0) -> None:
        # "unix:PATH,server=on,..." is QEMU option syntax; a plain path is taken verbatim (it may contain commas)
        # (",," is QEMU's escape for a literal comma in that syntax)
        if path.startswith("unix:"):
            path = path.removeprefix("unix:").replace(",,", "\0").split(",", 1)[0].replace("\0", ",")
        self.path = path
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._file: Any = None
        self.greeting: dict[str, Any] = {}

    def __enter__(self) -> "QmpClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(self.timeout)  # bounds connect and every later read/write
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise QmpError(f"cannot connect to QMP socket {self.path}: {exc}") from exc
        self._sock = sock
        self._file = sock.makefile("rw", encoding="utf-8")
        try:
            self.greeting = self._read_message()
            if "QMP" not in self.greeting:
                raise QmpError(f"{self.path} did not send a QMP greeting: {self.greeting!r}")
            self.execute("qmp_capabilities")
        except BaseException:
            self.close()  # never leave a half-open connection holding QMP's single client slot
            raise

    def close(self) -> None:
        for obj in (self._file, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._file = self._sock = None

    def _read_message(self) -> dict[str, Any]:
        try:
            line = self._file.readline()
        except (OSError, TimeoutError, ValueError) as exc:  # ValueError: UnicodeDecodeError from the text wrapper
            raise QmpError(f"QMP read failed ({type(exc).__name__}: {exc})") from exc
        if not line:
            raise QmpError("QMP socket closed by QEMU")
        try:
            return json.loads(line)
        except ValueError as exc:
            raise QmpError(f"QMP sent a non-JSON line: {line[:80]!r}") from exc

    def execute(self, command: str, **arguments: Any) -> Any:
        """Run one command, skipping asynchronous events; raise QmpError on an error reply."""
        msg: dict[str, Any] = {"execute": command}
        if arguments:
            msg["arguments"] = arguments
        try:
            self._file.write(json.dumps(msg) + "\n")
            self._file.flush()
        except (OSError, TimeoutError) as exc:
            raise QmpError(f"QMP write failed ({type(exc).__name__}: {exc})") from exc
        while True:
            reply = self._read_message()
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                err = reply["error"]
                raise QmpError(f"QMP {command}: {err.get('class', 'Error')}: {err.get('desc', '')}")
            # anything else is an event: ignore

    def fresh(self) -> "QmpClient":
        """A new, unconnected client for the same socket (a timed-out connection is poisoned: replies may arrive late)."""
        c = QmpClient(self.path, self.timeout)
        return c

    def hmp(self, command_line: str) -> str:
        return self.execute("human-monitor-command", **{"command-line": command_line})
