"""Daemon process shutdown coordination (SIGTERM, producer death).

``topos daemon serve`` runs ``server.serve_forever()`` on the main thread and
cleans up in a ``finally`` block. Two events must drive that same path:

* SIGTERM/SIGINT (``systemctl stop`` sends SIGTERM, which by default kills the
  interpreter without running ``finally``), and
* death of the frame producer (the collector raised or its iterator ended);
  without this the process would stay up serving a frozen last frame while
  systemd's ``Restart=on-failure`` never fires.

Both call :meth:`ShutdownCoordinator.request`, which asks the server to leave
``serve_forever`` from a helper thread (``BaseServer.shutdown`` must never be
called from the thread running ``serve_forever``).
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from typing import Any, Protocol


class _Shutdownable(Protocol):
    def shutdown(self) -> None: ...


class ShutdownCoordinator:
    """Idempotent shutdown request shared by signal handlers and watchdogs."""

    def __init__(self, server: _Shutdownable | None = None) -> None:
        self._server = server
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._producer_failed = False

    def bind_server(self, server: _Shutdownable) -> None:
        """Attach the server; a request made before binding is replayed now."""
        with self._lock:
            self._server = server
            pending = self._reason is not None
        if pending:
            self._spawn_shutdown(server)

    @staticmethod
    def _spawn_shutdown(server: _Shutdownable) -> None:
        threading.Thread(target=server.shutdown, name="topos-daemon-shutdown", daemon=True).start()

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def producer_failed(self) -> bool:
        """True when shutdown was caused by the frame producer terminating."""
        return self._producer_failed

    def request(self, reason: str, *, producer_failed: bool = False) -> bool:
        """Ask the server loop to stop. Only the first request wins."""
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            self._producer_failed = producer_failed
            server = self._server
        if server is not None:
            self._spawn_shutdown(server)
        return True

    def on_producer_terminal(self, kind: str) -> None:
        """``FrameBroker`` terminal callback: failed/exhausted must stop the daemon."""
        if kind in ("failed", "exhausted"):
            self.request(f"frame producer {kind}", producer_failed=True)

    def install_signal_handlers(self) -> Callable[[], None]:
        """Install SIGTERM/SIGINT handlers; return a function restoring the old ones.

        Outside the main thread ``signal.signal`` is unavailable; in that case
        nothing is installed and the restore function is a no-op.
        """
        previous: dict[int, Any] = {}

        def _handler(signum: int, _frame: object) -> None:
            self.request(signal.Signals(signum).name)

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[signum] = signal.signal(signum, _handler)
            except ValueError:
                break

        def _restore() -> None:
            for signum, old in previous.items():
                try:
                    signal.signal(signum, old)
                except (ValueError, TypeError):
                    pass

        return _restore
