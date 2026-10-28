"""Application shell, host/session controller integration, and navigation."""
from __future__ import annotations

from typing import Iterable

from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported, SystemCommand
from textual.containers import Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Select, Static, Tree

from .. import profile as prof
from ..errors import TntError
from ..host_store import HostStore, load_host_store
from ..menu import MENU, MenuItem, walk
from ..profile import ConfigPaths, Profile, SecretEntry
from ..session import Session, SessionOpener, open_session
from ..session_controller import SessionController, SessionTransition, WorkerLease
from .disks import DisksView
from .audit import AuditView
from .apikeys import ApiKeysView
from .hosts import HostPicker
from .network import NetworkView
from .workflows import WorkflowView
from .system import SystemGeneralView, SystemPowerView, SystemUpdateView
from .core_views import VIEWS as CORE_READ_VIEWS

VIEWS = {
    **CORE_READ_VIEWS,
    "storage/disks": DisksView,
    "datasets": lambda session: WorkflowView(session, "datasets"),
    "snapshots": lambda session: WorkflowView(session, "snapshots"),
    "snapshot-tasks": lambda session: WorkflowView(session, "snapshot-tasks"),
    "replication": lambda session: WorkflowView(session, "replication"),
    "cloud-sync": lambda session: WorkflowView(session, "cloud-sync"),
    "containers": lambda session: WorkflowView(session, "containers"),
    "vm": lambda session: WorkflowView(session, "vm"),
    "users": lambda session: WorkflowView(session, "users"),
    "sharing": lambda session: WorkflowView(session, "sharing"),
    "apps": lambda session: WorkflowView(session, "apps"),
    "network": NetworkView,
    "audit": AuditView,
    "api-keys": ApiKeysView,
    "update": SystemUpdateView,
    "general": SystemGeneralView,
}


def placeholder(item: MenuItem) -> Static:
    where = f"milestone {item.milestone}"
    rows = f" Action-matrix rows: {', '.join(item.matrix)}." if item.matrix else ""
    cond = " The web UI shows this entry only when the host supports it." if item.conditional else ""
    return Static(f"{item.label}: planned, {where}.{rows}{cond}\nSee docs/DESIGN.md and BACKLOG.md.", id="view-planned")


class TntApp(App):
    TITLE = "tnt"
    CSS = """
    #nav { width: 30; border-right: solid $primary; }
    #content { width: 1fr; }
    #banner { height: 1; background: $boost; padding: 0 1; }
    #banner.insecure { background: $error; color: $text; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"), ("ctrl+n", "focus_nav", "Menu"), ("r", "refresh", "Refresh"),
        ("h", "select_host", "Host"), ("w", "toggle_write_mode", "Write mode"), ("s", "ssh", "SSH"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self, session: Session | None = None, *, session_controller: SessionController | None = None,
                 hosts: dict[str, Profile] | None = None, default_host: str | None = None,
                 secret_entries: dict[str, SecretEntry] | None = None, host_paths: ConfigPaths | None = None,
                 session_opener: SessionOpener = open_session, insecure_ack: bool = False,
                 initial_write: bool = False, reconnect_delays: tuple[int, ...] = (2, 4, 8, 16, 32)):
        super().__init__()
        self.session_opener = session_opener
        self.insecure_ack = insecure_ack
        self.reconnect_delays = reconnect_delays
        self.host_paths = host_paths or prof.resolve_paths()
        if session_controller is not None:
            self.session_controller = session_controller
        else:
            initial_hosts = dict(hosts or {})
            if session is not None:
                initial_hosts.setdefault(session.profile.name, session.profile)
                if default_host is None:
                    default_host = session.profile.name
            self.session_controller = SessionController(
                initial_hosts, default_host, secret_entries or {}, session_opener,
                insecure_ack=insecure_ack, initial_session=session, initial_write=initial_write)
        self.current: str | None = None
        self._restore_key: str | None = None

    @property
    def session(self) -> Session | None:
        return self.session_controller.session

    def begin_session_work(self, session: Session) -> WorkerLease | None:
        generation, active = self.session_controller.capture()
        if active is not session:
            return None
        return self.session_controller.register_worker(generation, session)

    def finish_session_work(self, lease: WorkerLease) -> None:
        self.session_controller.finish_worker(lease)

    def deliver_session_callback(self, lease: WorkerLease, widget, callback: str, args: tuple) -> None:
        """Run a worker result only while both its session and view are still current."""
        if lease.cancelled.is_set() or not self.session_controller.is_current(lease.generation, lease.session):
            return
        if not getattr(widget, "is_attached", False):
            return
        getattr(widget, callback)(*args)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._banner_text(), id="banner", classes="insecure" if self.session and self.session.insecure_tls else "")
        with Horizontal():
            tree: Tree[MenuItem] = Tree("TrueNAS", id="nav")
            tree.show_root = False
            for item in MENU:
                self._add(tree.root, item)
            yield tree
            yield Horizontal(id="content")
        yield Footer()

    def _banner_text(self) -> str:
        session = self.session
        if session is None:
            active = self.session_controller.active_host
            if active:
                status = self.session_controller.last_status.get(active, "not connected")
                return f"{active}  {status}  generation {self.session_controller.generation}"
            return f"No host selected  generation {self.session_controller.generation}"
        write_mode = "WRITE ENABLED" if not session.rpc.read_only else "read-only"
        tls = "  TLS VERIFICATION OFF" if session.insecure_tls else ""
        return (f"{session.profile.name} {session.profile.host}  {session.backend.generation} ({session.backend.version})  "
                f"{write_mode}  generation {self.session_controller.generation}{tls}")

    def _add(self, parent, item: MenuItem) -> None:
        node = parent.add(item.label, data=item, expand=item.status == "implemented") if item.children else parent.add_leaf(item.label, data=item)
        for child in item.children:
            self._add(node, child)

    def on_mount(self) -> None:
        self.sub_title = self.session.profile.name if self.session else "Select a host"
        self.query_one("#nav", Tree).focus()
        if self.session is None:
            self.show_host_picker()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        item = event.node.data
        if item is not None and not item.children:
            self.show(item)

    def show(self, item: MenuItem) -> None:
        session = self.session
        if session is None:
            self.show_host_picker()
            return
        content = self.query_one("#content")
        content.remove_children()
        factory = VIEWS.get(item.key)
        if item.key in {"reboot", "shutdown"}:
            content.mount(SystemPowerView(session, item.key))
        else:
            content.mount(factory(session) if factory else placeholder(item))
        self.current = item.key

    def show_host_picker(self) -> None:
        content = self.query_one("#content")
        content.remove_children()
        content.mount(HostPicker(self.session_controller))
        self.current = "__hosts__"
        self._update_banner()

    def close_host_picker(self) -> None:
        if self.session is None:
            return
        item = self._menu_item(self._restore_key or self.current)
        if item is not None:
            self.show(item)
        else:
            self.query_one("#content").remove_children()
            self.current = None

    def _menu_item(self, key: str | None) -> MenuItem | None:
        if key is None or key == "__hosts__":
            return None
        return next((item for _, item in walk() if item.key == key), None)

    def _update_banner(self, message: str | None = None) -> None:
        banner = self.query_one("#banner", Static)
        banner.update(message or self._banner_text())
        session = self.session
        if session is not None and session.insecure_tls:
            banner.add_class("insecure")
        else:
            banner.remove_class("insecure")

    def action_refresh(self) -> None:
        """Refresh the open view; bound on the app so it works while the menu has focus."""
        for view in self.query_one("#content").children:
            refresh = getattr(view, "action_refresh", None)
            if refresh is not None:
                refresh()

    def action_focus_nav(self) -> None:
        self.query_one("#nav", Tree).focus()

    def action_select_host(self) -> None:
        self._restore_key = self.current if self.current != "__hosts__" else None
        self._dismiss_top_modal()
        self.show_host_picker()

    def action_toggle_write_mode(self) -> None:
        session = self.session
        if session is None:
            self.notify("Choose a host before changing its write mode.", title="No host selected")
            return
        self.switch_host(session.profile.name, write_mode=session.rpc.read_only)

    def action_help(self) -> None:
        self.notify("arrows/enter: navigate, ctrl+p: command palette, h: switch host, w: toggle write mode, "
                    "s: interactive SSH, r: refresh, q: quit", title="Keys")

    def action_ssh(self) -> None:
        """Temporarily return the terminal to OpenSSH for the selected host."""
        session = self.session
        if session is None:
            self.notify("Choose a host before opening SSH.", title="No host selected")
            return
        from ..ssh import SshHandoffError, run_interactive_ssh

        try:
            with self.suspend():
                status = run_interactive_ssh(session.profile)
        except SuspendNotSupported:
            self.notify("This terminal cannot suspend safely for an SSH session.", title="SSH hand-off unavailable")
            return
        except SshHandoffError as exc:
            self.notify(str(exc), title="SSH hand-off refused")
            return
        if status:
            self.notify(f"SSH exited with status {status}.", title="SSH session ended")

    def _dismiss_top_modal(self) -> None:
        screen = self.screen
        if isinstance(screen, ModalScreen):
            dismiss = getattr(screen, "invalidate_for_host_change", None)
            if callable(dismiss):
                dismiss()
            else:
                screen.dismiss()

    def switch_host(self, host_name: str, *, write_mode: bool | None = None) -> None:
        try:
            transition = self.session_controller.begin_switch(host_name, write_mode=write_mode)
        except TntError as exc:
            self.notify(str(exc), title="Host switch refused")
            return
        self._dismiss_top_modal()
        self._restore_key = self.current if self.current not in {None, "__hosts__"} else self._restore_key
        self.query_one("#content").remove_children()
        old_name = transition.previous_session.profile.name if transition.previous_session else "the selected host"
        self._update_banner(f"Waiting for work on {old_name} to drain; connecting to {host_name}…")
        self.sub_title = f"Switching to {host_name}"
        self._finish_host_switch(transition, self._restore_key)

    @work(thread=True, exclusive=True)
    def _finish_host_switch(self, transition: SessionTransition, restore_key: str | None) -> None:
        try:
            session = self.session_controller.finish_switch(transition)
        except Exception as exc:
            try:
                self.app.call_from_thread(self._host_switch_failed, transition.generation, transition.target_host, str(exc))
            except Exception:
                return
        else:
            try:
                self.app.call_from_thread(self._host_switch_finished, transition.generation, session, restore_key)
            except Exception:
                if session is not None:
                    session.close()

    def _host_switch_finished(self, generation: int, session: Session | None, restore_key: str | None) -> None:
        if not self.session_controller.accepts_generation(generation):
            return
        self._update_banner()
        self.sub_title = session.profile.name if session else "Select a host"
        item = self._menu_item(restore_key)
        if item is not None and session is not None:
            self.show(item)
        else:
            self.show_host_picker()

    def _host_switch_failed(self, generation: int, host_name: str | None, message: str) -> None:
        if not self.session_controller.accepts_generation(generation):
            return
        self._update_banner(f"{host_name or 'Host'} connection failed  generation {generation}")
        self.sub_title = "Select a host"
        self.show_host_picker()
        self.notify(message, title="Host connection failed")

    def disconnect_to_picker(self) -> None:
        self._dismiss_top_modal()
        try:
            transition = self.session_controller.begin_disconnect()
        except TntError as exc:
            self.notify(str(exc), title="Disconnect refused")
            return
        self.query_one("#content").remove_children()
        self._update_banner("Draining work and closing the selected host session…")
        self._finish_host_switch(transition, None)

    def set_default_host(self, host_name: str) -> None:
        try:
            HostStore(self.host_paths).set_default(host_name)
            hosts, default, entries = load_host_store(self.host_paths)
            self.session_controller.update_hosts(hosts, default, entries)
        except TntError as exc:
            self.notify(str(exc), title="Default host was not changed")
            return
        self._update_banner()
        picker = next((child for child in self.query_one("#content").children if isinstance(child, HostPicker)), None)
        if picker is not None:
            picker.refresh_rows()
        self.notify(f"Default host is {host_name}.")

    def host_test_finished(self, generation: int, host_name: str, result: str) -> None:
        if not self.session_controller.accepts_generation(generation):
            return
        picker = next((child for child in self.query_one("#content").children if isinstance(child, HostPicker)), None)
        if picker is not None:
            picker.refresh_rows()
            self.query_one("#hosts-note", Static).update(f"{host_name}: {result}")

    def host_configuration_changed(self, old_name: str | None, new_name: str | None, *, removed: bool = False) -> None:
        old_active = self.session_controller.active_host
        old_write = self.session_controller.write_mode_for(old_active) if old_active else False
        try:
            hosts, default, entries = load_host_store(self.host_paths)
            self.session_controller.update_hosts(hosts, default, entries)
        except TntError as exc:
            self.notify(str(exc), title="Host files were written but could not be reloaded")
            self.show_host_picker()
            return
        if old_active == old_name and removed:
            self.disconnect_to_picker()
            return
        if old_active == old_name and new_name is not None:
            if old_name != new_name:
                self.session_controller.write_modes[new_name] = old_write
            self.switch_host(new_name, write_mode=old_write)
            return
        self._update_banner()
        self.show_host_picker()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Select or switch TrueNAS host", "Show configured hosts and connection status", self.action_select_host)
        if self.session is not None:
            yield SystemCommand("Open interactive SSH session", "Use this host's configured SSH user and pinned keys",
                                self.action_ssh)
            mode = "read-only" if self.session.rpc.read_only else "write enabled"
            yield SystemCommand(f"Toggle host write mode (currently {mode})", "Reconnect this host with the other mode",
                                self.action_toggle_write_mode)
        for path, item in walk():
            if not item.children:
                yield SystemCommand(f"Go to {' > '.join(path)}", f"{item.status}, {item.milestone}", lambda i=item: self.show(i))
                factory = VIEWS.get(item.key)
                if factory is not None and item.key != "storage/disks" and self.session is not None:
                    from .workflows import OPERATIONS
                    for label, operation in OPERATIONS.get(item.key, ()):
                        yield SystemCommand(f"Plan {label} in {item.label}", "Opens the workflow action form",
                                            lambda i=item, op=operation: self._open_action(i, op))

    def _open_action(self, item: MenuItem, operation: str) -> None:
        self.show(item)

        def select_operation() -> None:
            try:
                self.query_one("#workflow-operation", Select).value = operation
            except Exception:
                return

        self.call_after_refresh(select_operation)
