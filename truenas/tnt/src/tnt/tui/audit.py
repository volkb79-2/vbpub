"""System > Audit: paginated, filtered, read-only audit entries."""
from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Select, Static

from ..session import Session


_SERVICES = tuple((name, name) for name in ("MIDDLEWARE", "S3", "SMB", "SUDO", "SYSTEM"))
_PAGE_SIZE = 100


class AuditView(Vertical):
    DEFAULT_CSS = """
    AuditView { height: 1fr; }
    AuditView #audit-note { height: auto; color: $text-muted; padding: 0 1; }
    AuditView #audit-filters { height: auto; }
    AuditView #audit-filters Input { width: 1fr; }
    AuditView #audit-table { height: 1fr; }
    """

    def __init__(self, session: Session):
        super().__init__(id="view-audit")
        self.session = session
        self.page_offset = 0
        self.rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Audit entries have not been loaded.", id="audit-note")
        with Vertical(id="audit-filters"):
            with Horizontal():
                yield Select(_SERVICES, value="MIDDLEWARE", id="audit-service")
                yield Input(placeholder="Since: ISO-8601 with Z/offset", id="audit-since")
                yield Input(placeholder="Until: ISO-8601 with Z/offset", id="audit-until")
            with Horizontal():
                yield Input(placeholder="Username filter", id="audit-user")
                yield Input(placeholder="Event filter", id="audit-event")
                yield Button("Refresh", id="audit-refresh", variant="primary")
                yield Button("Newer", id="audit-previous", disabled=True)
                yield Button("Older", id="audit-next", disabled=True)
        table = DataTable(id="audit-table", cursor_type="row", zebra_stripes=True)
        table.add_columns("Timestamp", "User", "Event", "Service", "Success", "Event details")
        yield table

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        service = self.query_one("#audit-service", Select).value
        since = self.query_one("#audit-since", Input).value.strip() or None
        until = self.query_one("#audit-until", Input).value.strip() or None
        user = self.query_one("#audit-user", Input).value.strip() or None
        event = self.query_one("#audit-event", Input).value.strip() or None
        self.query_one("#audit-note", Static).update("Loading audit entries…")
        self._load(service, since, until, user, event, self.page_offset)

    @work(thread=True, exclusive=True)
    def _load(self, service: Any, since: str | None, until: str | None, user: str | None,
              event: str | None, offset: int) -> None:
        try:
            rows = self.session.workflows.audit_entries(service=service, since=since, until=until, user=user,
                                                       event=event, limit=_PAGE_SIZE, offset=offset)
        except Exception as exc:
            self.app.call_from_thread(self._failed, str(exc))
        else:
            self.app.call_from_thread(self._loaded, rows, offset)

    def _failed(self, message: str) -> None:
        self.query_one("#audit-note", Static).update(f"Audit query failed: {message}")

    def _loaded(self, rows: list[dict[str, Any]], offset: int) -> None:
        self.rows = rows
        table = self.query_one("#audit-table", DataTable)
        table.clear()
        for row in rows:
            details = row.get("event_data") or row.get("service_data") or {}
            table.add_row(str(row.get("timestamp", "")), str(row.get("username", "")),
                          str(row.get("event", "")), str(row.get("service", "")),
                          str(row.get("success", "")), json.dumps(details, sort_keys=True, default=str))
        end = offset + len(rows)
        self.query_one("#audit-note", Static).update(
            f"{len(rows)} audit event(s), rows {offset + 1 if rows else 0}–{end}; page size {_PAGE_SIZE}. Read-only."
        )
        self.query_one("#audit-previous", Button).disabled = offset == 0
        self.query_one("#audit-next", Button).disabled = len(rows) < _PAGE_SIZE

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "audit-previous":
            self.page_offset = max(0, self.page_offset - _PAGE_SIZE)
        elif event.button.id == "audit-next":
            self.page_offset += _PAGE_SIZE
        elif event.button.id == "audit-refresh":
            self.page_offset = 0
        self.action_refresh()
