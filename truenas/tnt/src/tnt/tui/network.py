"""System > Network: a read-only view of live v27 interface, route, IP, and DNS state."""
from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..session import Session
from ..workflows import _safe_value


class NetworkView(Vertical):
    DEFAULT_CSS = """
    NetworkView { height: 1fr; }
    NetworkView #network-note { height: auto; color: $text-muted; padding: 0 1; }
    NetworkView #network-output { height: 1fr; overflow-y: auto; padding: 0 1; }
    """

    def __init__(self, session: Session):
        super().__init__(id="view-network")
        self.session = session

    def compose(self) -> ComposeResult:
        yield Static("Loading network status…", id="network-note")
        yield Static("", id="network-output", markup=False)

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#network-note", Static).update("Loading network status…")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        try:
            result = self.session.workflows.network_show()
        except Exception as exc:
            self.app.call_from_thread(self._failed, str(exc))
        else:
            self.app.call_from_thread(self._loaded, result)

    def _failed(self, message: str) -> None:
        self.query_one("#network-note", Static).update(f"Failed to load network status: {message}")

    def _loaded(self, result: dict[str, Any]) -> None:
        safe = _safe_value(result)
        self.query_one("#network-output", Static).update(json.dumps(safe, indent=2, sort_keys=True, default=str))
        self.query_one("#network-note", Static).update("Network status loaded. Read-only; no changes are available.")
