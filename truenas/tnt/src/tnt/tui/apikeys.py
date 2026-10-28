"""Credentials > My API Keys: safe listing and reviewed API-key changes."""
from __future__ import annotations

import os
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Select, Static

from ..actions import Plan, make_plan
from ..errors import TntError
from ..host_store import HostStore, load_host_store
from ..keyfiles import write_api_key_file
from ..profile import SecretEntry
from ..session import Session
from .workflows import PlanReview
from .session_work import run as run_session_work, run_cancellable as run_cancellable_session_work


_OPERATIONS = (("Create API key", "create"), ("Rename selected", "rename"), ("Revoke selected", "revoke"))


class ApiKeysView(Vertical):
    DEFAULT_CSS = """
    ApiKeysView { height: 1fr; }
    ApiKeysView #apikey-note { height: auto; color: $text-muted; padding: 0 1; }
    ApiKeysView #apikey-form { height: auto; padding: 0 1; }
    ApiKeysView #apikey-form Input { width: 1fr; }
    ApiKeysView #apikey-table { height: 1fr; }
    """

    def __init__(self, session: Session):
        super().__init__(id="view-api-keys")
        self.session = session
        self.rows: list[dict[str, Any]] = []
        self.pending_key_path: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("API keys have not been loaded.", id="apikey-note")
        table = DataTable(id="apikey-table", cursor_type="row", zebra_stripes=True)
        table.add_columns("ID", "Name", "Username", "Created", "Expires", "Status")
        yield table
        with Vertical(id="apikey-form"):
            yield Select(_OPERATIONS, prompt="Choose an action", id="apikey-operation")
            with Horizontal():
                yield Input(placeholder="Key name / new name (1–200 characters)", id="apikey-name")
                yield Input(placeholder="Owner username (create)", id="apikey-username")
                yield Input(placeholder="Expires at ISO-8601 with offset (blank: never)", id="apikey-expires")
            yield Input(placeholder="New absolute private file for a created key (required; key is never shown)",
                        id="apikey-file")
            with Horizontal():
                yield Button("Refresh", id="apikey-refresh")
                yield Button("Plan action", id="apikey-plan", variant="primary")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#apikey-note", Static).update("Loading API keys…")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        run_session_work(self, lambda session: session.workflows.api_keys(), "_loaded", "_load_failed")

    def _load_failed(self, message: str) -> None:
        self.query_one("#apikey-note", Static).update(f"API key query failed: {message}")

    def _loaded(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        table = self.query_one("#apikey-table", DataTable)
        table.clear()
        for index, row in enumerate(rows):
            status = "revoked" if row.get("revoked") else "active"
            table.add_row(str(row.get("id", "")), str(row.get("name", "")), str(row.get("username", "")),
                          str(row.get("created_at", "")), str(row.get("expires_at") or "never"), status,
                          key=str(index))
        self.query_one("#apikey-note", Static).update(
            f"{len(rows)} API key(s). Key verifier material and one-time keys are never shown in the TUI."
        )

    def _selected(self) -> dict[str, Any] | None:
        table = self.query_one("#apikey-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            index = int(str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value))
            return self.rows[index]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apikey-refresh":
            self.action_refresh()
            return
        if event.button.id != "apikey-plan":
            return
        operation = self.query_one("#apikey-operation", Select).value
        name = self.query_one("#apikey-name", Input).value.strip()
        username = self.query_one("#apikey-username", Input).value.strip()
        expires_at = self.query_one("#apikey-expires", Input).value.strip() or None
        key_path = self.query_one("#apikey-file", Input).value.strip()
        row = self._selected()
        try:
            workflows = self.session.workflows
            if operation == "create":
                if not os.path.isabs(key_path):
                    raise ValueError("creating an API key requires an absolute output path for its private file")
                action = workflows.api_key_create_action(name, username, expires_at)
            elif operation == "rename" and row is not None:
                action = workflows.api_key_rename_action(row["id"], name)
            elif operation == "revoke" and row is not None:
                action = workflows.api_key_revoke_action(row["id"])
            else:
                raise ValueError("choose an action and select an API key for rename or revoke")
        except Exception as exc:
            self.query_one("#apikey-note", Static).update(f"Plan refused: {exc}")
            return
        self.pending_key_path = key_path if operation == "create" else None
        self.query_one("#apikey-note", Static).update("Checking current host state and preparing the plan…")
        self._make_plan(action)

    @work(thread=True, exclusive=True)
    def _make_plan(self, action) -> None:
        run_cancellable_session_work(
            self, lambda session, lease: make_plan(action, session.rpc, cancelled=lease.cancelled.is_set),
            "_plan_ready", "_plan_failed")

    def _plan_ready(self, plan: Plan) -> None:
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_failed(self, message: str) -> None:
        self.pending_key_path = None
        self.query_one("#apikey-note", Static).update(f"Plan refused: {message}")

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        methods = {call.method for call in plan.action.calls}
        if "api_key.create" in methods:
            entry = result[0] if isinstance(result, list) and result else None
            raw_key = entry.get("key") if isinstance(entry, dict) else None
            if not isinstance(raw_key, str) or not raw_key:
                self.query_one("#apikey-note", Static).update(
                    "The key was created, but the v27 response did not contain its one-time value; revoke it and create another."
                )
                result = None
                return
            path = self.pending_key_path
            self.pending_key_path = None
            try:
                if path is None:
                    raise ValueError("no private output path was captured")
                saved_to = write_api_key_file(path, raw_key)
            except Exception as exc:
                self.query_one("#apikey-note", Static).update(
                    f"The key was created but could not be saved ({type(exc).__name__}); its one-time value was discarded. "
                    "Revoke that key and create another with a new private output path."
                )
                raw_key = None
                entry = None
                result = None
                self.action_refresh()
                return
            message = f"Created API key and wrote it to {saved_to} with mode 0600. The value was never shown in the TUI."
            if self.session.profile.auth == "api_key":
                try:
                    HostStore(self.app.host_paths).edit(
                        self.session.profile.name, self.session.profile,
                        credential=SecretEntry(api_key_file=saved_to))
                    hosts, default, secrets = load_host_store(self.app.host_paths)
                    self.app.session_controller.update_hosts(hosts, default, secrets)
                    message += " The active host now uses this file for its next connection."
                except TntError as exc:
                    message += f" The key file is saved, but tnt.secrets.toml was not updated: {exc}"
            else:
                message += " This host uses password auth; switch it to api_key auth in the host editor to use this file."
            result = None
            entry = None
            raw_key = None
            self.action_refresh()
            self.query_one("#apikey-note", Static).update(message)
            return
        self.action_refresh()
        message = "API key renamed." if "api_key.update" in methods else "API key revoked and removed."
        self.query_one("#apikey-note", Static).update(message)
        result = None

    def _plan_cancelled(self, plan: Plan) -> None:
        self.pending_key_path = None
        self.query_one("#apikey-note", Static).update("API key action cancelled.")
