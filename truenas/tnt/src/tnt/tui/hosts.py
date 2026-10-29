"""Host picker and host form; all persistence goes through :mod:`tnt.host_store`."""
from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select

from ..errors import TntError
from ..host_store import HostStore
from ..profile import AUTH_MODES, TLS_MODES, Profile, Secret, SecretEntry
from .widgets import DataTable, Static


class _FocusedHostTable(DataTable):
    def on_mount(self) -> None:
        self.focus()


class HostPicker(Vertical):
    DEFAULT_CSS = """
    HostPicker { height: 1fr; padding: 1; }
    HostPicker #hosts-note { height: auto; color: $text-muted; }
    HostPicker #hosts-table { height: 1fr; }
    HostPicker #hosts-buttons { height: auto; }
    HostPicker #hosts-buttons > Horizontal { height: 3; }
    HostPicker #hosts-buttons Button { min-width: 0; margin-right: 1; }
    """

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def compose(self) -> ComposeResult:
        default_note = (f" Default host: {self.controller.default_host}." if self.controller.default_host
                        else " No default host is set.")
        has_hosts = bool(self.controller.hosts)
        note = ("Select a TrueNAS host.\n"
                "Credentials and SSH key paths stay hidden.\n"
                "Up/Down choose rows; Left/Right or Home/End inspect columns." + default_note
                if has_hosts else "No hosts configured. Add one to continue, or choose Quit.")
        yield Static(note, id="hosts-note")
        table = _FocusedHostTable(id="hosts-table", cursor_type="row", zebra_stripes=True)
        table.add_columns("Host", "URL", "Description", "Last status")
        for name, host in sorted(self.controller.hosts.items()):
            table.add_row(name, host.url, host.description, self.controller.last_status.get(name, "not tested"),
                          key=name)
        yield table
        with Vertical(id="hosts-buttons"):
            with Horizontal():
                yield Button("Switch to selected", id="hosts-switch", variant="primary", disabled=not has_hosts)
                yield Button("Test", id="hosts-test", disabled=not has_hosts)
                yield Button("Add", id="hosts-add")
            with Horizontal():
                yield Button("Edit", id="hosts-edit", disabled=not has_hosts)
                yield Button("Set default", id="hosts-default", disabled=not has_hosts)
                yield Button("Remove", id="hosts-remove", variant="error", disabled=not has_hosts)
                yield Button("Cancel" if self.controller.session else "Quit", id="hosts-cancel")

    def on_mount(self) -> None:
        if self.controller.hosts:
            self._sync_host_actions()
        else:
            self.query_one("#hosts-add", Button).focus()

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id == "hosts-table":
            self._sync_host_actions()

    def _sync_host_actions(self) -> None:
        if not self.is_mounted:
            return
        try:
            enabled = self.selected_name() is not None
            button_ids = ("hosts-switch", "hosts-test", "hosts-edit", "hosts-default", "hosts-remove")
            buttons = [self.query_one(f"#{button_id}", Button) for button_id in button_ids]
        except NoMatches:
            # Mount and row-highlight events can arrive before sibling widgets
            # enter the DOM. Compose already sets the initial disabled state.
            return
        for button in buttons:
            button.disabled = not enabled

    def refresh_rows(self) -> None:
        table = self.query_one("#hosts-table", DataTable)
        table.clear()
        controller = self.controller
        for name, host in sorted(controller.hosts.items()):
            table.add_row(name, host.url, host.description, controller.last_status.get(name, "not tested"), key=name)
        if controller.hosts:
            default_note = f" Default host: {controller.default_host}." if controller.default_host else " No default host is set."
            note = ("Select a TrueNAS host.\nCredentials and SSH key paths stay hidden.\n"
                    f"Use Up/Down to choose a row; Left/Right or Home/End inspect columns.{default_note}")
        else:
            note = "No hosts configured. Add one to continue, or choose Quit."
        self.query_one("#hosts-note", Static).update(note)
        self._sync_host_actions()
        if not controller.hosts:
            self.query_one("#hosts-add", Button).focus()

    def selected_name(self) -> str | None:
        table = self.query_one("#hosts-table", DataTable)
        if not table.row_count:
            return None
        try:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except (AttributeError, TypeError, ValueError):
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        host_name = self.selected_name()
        if event.button.id == "hosts-switch":
            if host_name:
                self.app.switch_host(host_name)
            return
        if event.button.id == "hosts-test":
            if host_name:
                self.query_one("#hosts-note", Static).update(f"Testing {host_name}; the selected session stays unchanged…")
                self._test(host_name, self.app.session_controller.generation)
            return
        if event.button.id == "hosts-add":
            self.app.push_screen(HostForm())
            return
        if event.button.id == "hosts-edit":
            if host_name:
                self.app.push_screen(HostForm(current=self.app.session_controller.hosts[host_name]))
            return
        if event.button.id == "hosts-default":
            if host_name:
                self.app.set_default_host(host_name)
            return
        if event.button.id == "hosts-remove":
            if host_name:
                self.app.push_screen(HostRemoveConfirm(host_name))
            return
        if event.button.id == "hosts-cancel":
            if self.app.session is None:
                self.app.action_quit()
            else:
                self.app.close_host_picker()

    @work(thread=True, exclusive=True)
    def _test(self, host_name: str, generation: int) -> None:
        try:
            status = self.app.session_controller.test_host(host_name)
        except Exception as exc:
            result = f"connection failed: {exc}"
        else:
            result = "connected"
        self.app.call_from_thread(self.app.host_test_finished, generation, host_name, result)


class HostForm(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False, priority=True)]
    CSS = """
    HostForm { align: center middle; }
    HostForm > Vertical { width: 85%; max-height: 95%; border: thick $primary; background: $surface; padding: 1 2; }
    HostForm .host-form-row { height: auto; }
    HostForm Input { width: 1fr; }
    HostForm #host-form-warning { height: auto; color: $error; }
    HostForm #host-form-note { height: auto; color: $text-muted; }
    """

    def __init__(self, host_name: str | None = None, current: Profile | None = None):
        super().__init__()
        self.host_name = host_name
        self.current = current

    def compose(self) -> ComposeResult:
        current = self.current
        with Vertical():
            yield Static("Add host" if current is None else f"Edit host: {current.name}")
            with Horizontal(classes="host-form-row"):
                yield Input(value=current.name if current else "", placeholder="Host name", id="host-name")
                yield Input(value=current.url if current else "", placeholder="wss://host.example.invalid/api/current", id="host-url")
            with Horizontal(classes="host-form-row"):
                yield Input(value=current.username if current else "", placeholder="TrueNAS username", id="host-username")
                yield Select(tuple((value, value) for value in AUTH_MODES), value=current.auth if current else "api_key",
                             id="host-auth", allow_blank=False)
                yield Select(tuple((value, value) for value in TLS_MODES), value=current.tls if current else "verify",
                             id="host-tls", allow_blank=False)
            yield Static("", id="host-form-warning")
            with Horizontal(classes="host-form-row"):
                yield Input(value=current.description if current else "", placeholder="Description", id="host-description")
                yield Input(value=", ".join(current.tags) if current else "", placeholder="Tags (comma separated)", id="host-tags")
            with Horizontal(classes="host-form-row"):
                yield Input(value=current.ssh_user or "" if current else "",
                            placeholder="SSH user (optional; letters, numbers, . _ -)", id="host-ssh-user")
                yield Input(value=current.ssh_known_hosts or "" if current else "", placeholder="SSH known-hosts file",
                            id="host-known-hosts", password=True)
            yield Input(placeholder="SSH key path(s), comma separated; hidden, blank keeps current", id="host-ssh-keys", password=True)
            yield Input(placeholder="API key file (absolute mode-0600 path; leave blank to preserve)",
                        id="host-api-key-file", password=True)
            yield Input(placeholder="API key or password (input hidden; blank keeps current)", id="host-secret", password=True)
            yield Checkbox("I understand TLS verification will be disabled for this host", id="host-insecure-ack")
            yield Static("The credential is saved only to tnt.secrets.toml with mode 0600.", id="host-form-note")
            with Horizontal():
                yield Button("Save host", id="host-save", variant="primary")
                yield Button("Cancel", id="host-cancel")

    def on_mount(self) -> None:
        self._update_tls_warning()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "host-tls":
            self._update_tls_warning()

    def _update_tls_warning(self) -> None:
        try:
            mode = self.query_one("#host-tls", Select).value
            warning = ("WARNING: TLS certificate verification is OFF. Use only for an isolated throwaway host."
                       if mode == "insecure" else "TLS certificate verification is enabled.")
            self.query_one("#host-form-warning", Static).update(warning)
        except Exception:
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "host-cancel":
            self.action_cancel()
            return
        if event.button.id != "host-save":
            return
        try:
            self._save()
        except Exception as exc:
            self.query_one("#host-form-note", Static).update(f"Host was not saved: {exc}")

    def action_cancel(self) -> None:
        self.dismiss()

    def _save(self) -> None:
        from ..host_store import HostStore

        name_value = self.query_one("#host-name", Input).value.strip()
        url = self.query_one("#host-url", Input).value.strip()
        username = self.query_one("#host-username", Input).value.strip()
        auth = str(self.query_one("#host-auth", Select).value)
        tls = str(self.query_one("#host-tls", Select).value)
        if tls == "insecure" and not self.query_one("#host-insecure-ack", Checkbox).value:
            raise ValueError("check the insecure TLS warning before saving")
        ssh_key_text = self.query_one("#host-ssh-keys", Input).value.strip()
        tags_text = self.query_one("#host-tags", Input).value.strip()
        current = self.current
        host = Profile(
            name=name_value, url=url, username=username, tls=tls, auth=auth,
            ssh_user=self.query_one("#host-ssh-user", Input).value.strip() or None,
            ssh_key_files=(tuple(item.strip() for item in ssh_key_text.split(",") if item.strip())
                           if ssh_key_text else (current.ssh_key_files if current else ())),
            ssh_known_hosts=self.query_one("#host-known-hosts", Input).value.strip() or None,
            description=self.query_one("#host-description", Input).value.strip(),
            tags=tuple(item.strip() for item in tags_text.split(",") if item.strip()))
        credential_text = self.query_one("#host-secret", Input).value
        key_path = self.query_one("#host-api-key-file", Input).value.strip()
        credential: SecretEntry | None
        if key_path:
            if auth != "api_key":
                raise ValueError("an API key file is available only with api_key auth")
            credential = SecretEntry(api_key_file=key_path)
        elif credential_text:
            credential = (SecretEntry(api_key=Secret(credential_text)) if auth == "api_key"
                          else SecretEntry(password=Secret(credential_text)))
        elif current is None or (current and current.auth != auth):
            raise ValueError("enter the new credential in the hidden credential field")
        else:
            credential = None
        paths = self.app.host_paths
        store = HostStore(paths)
        if current is None:
            if credential is None:
                raise ValueError("enter a credential before adding the host")
            store.add(host, credential)
            old_name = None
        elif name_value != current.name:
            store.rename(current.name, name_value, profile=Profile(
                name=current.name, url=host.url, username=host.username, tls=host.tls, auth=host.auth,
                ssh_user=host.ssh_user, ssh_key_files=host.ssh_key_files,
                ssh_known_hosts=host.ssh_known_hosts, description=host.description, tags=host.tags),
                credential=credential)
            old_name = current.name
        else:
            store.edit(current.name, host, credential=credential)
            old_name = current.name
        self.query_one("#host-secret", Input).value = ""
        self.dismiss()
        self.app.host_configuration_changed(old_name, name_value)


class HostRemoveConfirm(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False, priority=True)]
    CSS = """
    HostRemoveConfirm { align: center middle; }
    HostRemoveConfirm > Vertical { width: 70%; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    HostRemoveConfirm Input { width: 1fr; }
    """

    def __init__(self, host_name: str):
        super().__init__()
        self.host_name = host_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Remove this host and delete its stored credential?\n"
                         f"Type this exact host name to confirm:\n{self.host_name}")
            yield Input(placeholder="Type host name exactly", id="host-remove-confirm")
            with Horizontal():
                yield Button("Remove host", id="host-remove-apply", variant="error")
                yield Button("Cancel", id="host-remove-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "host-remove-cancel":
            self.action_cancel()
        elif event.button.id == "host-remove-apply":
            if self.query_one("#host-remove-confirm", Input).value != self.host_name:
                self.notify("The host name did not match.", title="Host was not removed")
                return
            try:
                HostStore(self.app.host_paths).remove(self.host_name)
                self.dismiss()
                self.app.host_configuration_changed(self.host_name, None, removed=True)
            except TntError as exc:
                self.notify(str(exc), title="Host was not removed")
                return

    def action_cancel(self) -> None:
        self.dismiss()
