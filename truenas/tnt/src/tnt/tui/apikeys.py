"""Credentials > My API Keys: safe listing and reviewed API-key changes."""
from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from ..actions import Plan, make_plan
from ..errors import TntError
from ..keyfiles import write_api_key_file
from ..session import Session
from .workflows import PlanReview


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
        try:
            rows = self.session.workflows.api_keys()
        except Exception as exc:
            self.app.call_from_thread(self._load_failed, str(exc))
        else:
            self.app.call_from_thread(self._loaded, rows)

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
            f"{len(rows)} API key(s). Key verifier material is hidden; raw keys are shown only at creation."
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
        row = self._selected()
        try:
            workflows = self.session.workflows
            if operation == "create":
                action = workflows.api_key_create_action(name, username, expires_at)
            elif operation == "rename" and row is not None:
                action = workflows.api_key_rename_action(row["id"], name)
            elif operation == "revoke" and row is not None:
                action = workflows.api_key_revoke_action(row["id"])
            else:
                raise ValueError("choose an action and select an API key for rename or revoke")
            plan = make_plan(action, self.session.rpc)
        except Exception as exc:
            self.query_one("#apikey-note", Static).update(f"Plan refused: {exc}")
            return
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        methods = {call.method for call in plan.action.calls}
        if "api_key.create" in methods:
            entry = result[0] if isinstance(result, list) and result else None
            raw_key = entry.get("key") if isinstance(entry, dict) else None
            if not isinstance(raw_key, str) or not raw_key:
                self.query_one("#apikey-note", Static).update(
                    "The key was created, but v27 returned no one-time key value; inspect the key list and rotate manually."
                )
                result = None
                return
            result = None
            entry = None
            self.action_refresh()
            self.app.push_screen(ApiKeyReveal(raw_key))
            raw_key = None
            return
        self.action_refresh()
        message = "API key renamed." if "api_key.update" in methods else "API key revoked and removed."
        self.query_one("#apikey-note", Static).update(message)
        result = None


class ApiKeyReveal(ModalScreen[None]):
    """The sole UI that can hold/display a raw key; closing clears its widget and object reference."""
    CSS = """
    ApiKeyReveal { align: center middle; }
    ApiKeyReveal > Vertical { width: 85%; max-height: 85%; border: thick $error; background: $surface; padding: 1 2; }
    ApiKeyReveal #apikey-secret { height: auto; padding: 1; border: round $primary; }
    ApiKeyReveal #apikey-reveal-note { height: auto; color: $text-muted; }
    ApiKeyReveal Input { width: 1fr; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text.from_markup("[bold red]WARNING: This API key is shown only once. Copy or save it now.[/bold red]"))
            yield Static(Text(self.key), id="apikey-secret")
            yield Static(
                "It is not saved to tnt.secrets.toml. To use it with tnt, save a private key file and add "
                "api_key_file = \"/absolute/path\" under the selected profile in tnt.toml. Open the config with: $EDITOR tnt.toml",
                id="apikey-reveal-note",
            )
            yield Input(placeholder="Optional new absolute path; file will be created mode 0600", id="apikey-save-path")
            with Horizontal():
                yield Button("Write key file", id="apikey-save", variant="primary")
                yield Button("Close and forget", id="apikey-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apikey-save":
            path = self.query_one("#apikey-save-path", Input).value
            try:
                saved = write_api_key_file(path, self.key)
            except TntError as exc:
                self.query_one("#apikey-reveal-note", Static).update(f"File was not written: {exc}")
            else:
                self.query_one("#apikey-reveal-note", Static).update(
                    f"Saved the key to {saved} with mode 0600. Add api_key_file = {json.dumps(saved)} to the selected profile; "
                    "use $EDITOR tnt.toml. The key was not added to tnt.secrets.toml."
                )
            return
        if event.button.id == "apikey-close":
            self.action_close()

    def action_close(self) -> None:
        self._clear_key()
        self.dismiss()

    def on_unmount(self) -> None:
        self._clear_key()

    def _clear_key(self) -> None:
        self.key = ""
        try:
            self.query_one("#apikey-secret", Static).update(Text("Hidden; this value cannot be retrieved again."))
        except Exception:
            pass
