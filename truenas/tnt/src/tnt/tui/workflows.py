"""Shared query screens and reviewed action dialogs for the M1 workflows."""
from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea

from ..actions import Plan, apply_plan, make_plan
from ..errors import TntError
from ..profile import Secret
from ..session import Session
from ..workflows import _safe_value, validate_ssh_public_keys


SCREEN_TITLES = {
    "datasets": "Datasets",
    "snapshots": "Snapshots",
    "containers": "Containers",
    "vm": "Virtual Machines",
    "users": "Local Users",
    "sharing": "Shares",
}

OPERATIONS = {
    "datasets": (("Create dataset", "create"), ("Update selected", "update"), ("Delete selected", "delete")),
    "snapshots": (("Create snapshot", "create"), ("Clone selected", "clone"), ("Hold selected", "hold"),
                   ("Release selected", "release"), ("Rollback selected", "rollback"), ("Delete selected", "delete")),
    "containers": (("Create container", "create"), ("Update selected", "update"), ("Start selected", "start"),
                    ("Stop selected", "stop"), ("Restart selected", "restart"), ("Delete selected", "delete")),
    "vm": (("Create VM", "create"), ("Update selected", "update"), ("Add device", "device"), ("Start selected", "start"),
           ("Stop selected", "stop"), ("Restart selected", "restart"), ("Power off selected", "poweroff"),
           ("Suspend selected", "suspend"), ("Resume selected", "resume"), ("Console/VNC info", "console"),
           ("Delete selected", "delete")),
    "users": (("Create user", "create"), ("Update selected", "update"), ("Set password", "password"),
              ("Add SSH public key", "ssh-key-add"), ("Replace SSH public keys", "ssh-key-replace"),
              ("Remove SSH public keys", "ssh-key-remove"), ("Delete selected", "delete")),
    "sharing": (("Create SMB share", "create-smb"), ("Create NFS share", "create-nfs"),
                ("Delete selected SMB share", "delete-smb"), ("Delete selected NFS share", "delete-nfs")),
}


_READERS = {
    "datasets": lambda w: w.datasets(),
    "snapshots": lambda w: w.snapshots(),
    "containers": lambda w: w.containers(),
    "vm": lambda w: w.vms(),
    "users": lambda w: w.users(),
    "sharing": lambda w: ([{"protocol": "SMB", **row} for row in w.shares_smb()] +
                           [{"protocol": "NFS", **row} for row in w.shares_nfs()]),
}


def _flat(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    def add(value: Any, depth: int = 0) -> None:
        if isinstance(value, dict):
            row = dict(value)
            children = row.pop("children", [])
            name_key = "id" if "id" in row else "name"
            if depth and isinstance(row.get(name_key), str):
                row[name_key] = "  " * depth + row[name_key]
            result.append(row)
            if isinstance(children, list):
                for child in children:
                    add(child, depth + 1)
        elif isinstance(value, list):
            for item in value:
                add(item, depth)
    add(rows)
    return result


def _display(value: Any) -> str:
    safe = _safe_value(value)
    if isinstance(safe, (dict, list)):
        return json.dumps(safe, sort_keys=True, default=str)
    return "" if safe is None else str(safe)


def _contains_sensitive_field(value: Any) -> bool:
    sensitive = {"password", "old_password", "new_password", "sshpubkey", "private_key", "unixhash", "smbhash", "api_key"}
    if isinstance(value, dict):
        return any((isinstance(key, str) and key.casefold() in sensitive) or _contains_sensitive_field(child)
                   for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_field(child) for child in value)
    return False


class WorkflowView(Vertical):
    DEFAULT_CSS = """
    WorkflowView { height: 1fr; }
    WorkflowView #workflow-note { height: auto; color: $text-muted; padding: 0 1; }
    WorkflowView #workflow-actions { height: auto; padding: 0 1; }
    WorkflowView #workflow-form { height: 15; }
    WorkflowView TextArea { height: 8; }
    """

    def __init__(self, session: Session, key: str):
        super().__init__(id=f"view-{key}")
        self.session = session
        self.key = key
        self.rows: list[dict[str, Any]] = []
        self.pending_vm_name: str | None = None
        self.pending_vm_devices: list[dict[str, Any]] = []
        self.pending_vm_create = False

    def compose(self) -> ComposeResult:
        yield Static(f"Loading {SCREEN_TITLES[self.key]}…", id="workflow-note")
        table = DataTable(id="workflow-table", cursor_type="row", zebra_stripes=True)
        table.add_columns("Name", "State / Type", "Summary")
        yield table
        actions = ", ".join(label for label, _ in OPERATIONS[self.key])
        yield Static(f"Actions: {actions}. Every write opens a plan review before apply.", id="workflow-actions")
        with Vertical(id="workflow-form"):
            yield Select(OPERATIONS[self.key], prompt="Choose an action", id="workflow-operation")
            yield Input(placeholder="Name or target (uses selected row when blank)", id="workflow-name")
            yield Input(placeholder="Password or SSH key (hidden)", password=True, id="workflow-secret")
            with Horizontal():
                yield Checkbox("Recursive", id="workflow-recursive")
                yield Checkbox("Built-in user", id="workflow-builtin")
                yield Checkbox("Confirm current user", id="workflow-confirm-self")
            yield Button("Plan action", id="workflow-plan", variant="primary")
        yield TextArea("{}", language="json", id="workflow-data")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#workflow-note", Static).update(f"Loading {SCREEN_TITLES[self.key]}…")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        try:
            rows = _flat(_READERS[self.key](self.session.workflows))
        except Exception as exc:
            self.app.call_from_thread(self._load_failed, str(exc))
        else:
            self.app.call_from_thread(self._loaded, rows)

    def _load_failed(self, message: str) -> None:
        self.query_one("#workflow-note", Static).update(f"Failed to load {SCREEN_TITLES[self.key]}: {message}")

    def _loaded(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        table = self.query_one("#workflow-table", DataTable)
        table.clear()
        for index, row in enumerate(self.rows):
            name = row.get("username", row.get("name", row.get("id", "")))
            state = row.get("type", "")
            status = row.get("status")
            if isinstance(status, dict):
                state = status.get("state", state)
            summary = {key: row.get(key) for key in ("used", "available", "quota", "refquota", "recordsize", "comments",
                                                       "compression", "readonly", "mounted", "mountpoint", "vcpus", "memory",
                                                       "cpuset", "autostart", "builtin") if key in row}
            if self.key == "containers":
                summary["image"] = "not exposed by v27"
                summary["CPU count"] = "not exposed by v27"
                summary["memory limit"] = "not exposed by v27"
                metrics = row.get("metrics")
                if isinstance(metrics, dict):
                    cpu = metrics.get("cpu") if isinstance(metrics.get("cpu"), dict) else {}
                    user_cpu, system_cpu = cpu.get("cpu_user_percentage"), cpu.get("cpu_system_percentage")
                    summary["CPU usage %"] = (user_cpu + system_cpu if isinstance(user_cpu, (int, float))
                                                and not isinstance(user_cpu, bool)
                                                and isinstance(system_cpu, (int, float)) and not isinstance(system_cpu, bool)
                                                else "unavailable")
                    memory = metrics.get("mem_usage_limit") if isinstance(metrics.get("mem_usage_limit"), dict) else {}
                    summary["Memory used MiB"] = memory.get("mem_usage_limit_used_mib", "unavailable")
                    summary["Memory available MiB"] = memory.get("mem_usage_limit_available_mib", "unavailable")
                else:
                    summary["CPU/memory metrics"] = row.get("metrics_status", "unavailable")
            if self.key == "users":
                summary["ssh key configured"] = bool(row.get("sshpubkey"))
            table.add_row(str(name), str(state), _display(summary), key=str(index))
        self.query_one("#workflow-note", Static).update(f"{len(self.rows)} {SCREEN_TITLES[self.key].lower()} loaded.")

    def _selected(self) -> dict[str, Any] | None:
        table = self.query_one("#workflow-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            index = int(str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value))
            return self.rows[index]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "workflow-plan":
            return
        operation = self.query_one("#workflow-operation", Select).value
        if not isinstance(operation, str):
            self.query_one("#workflow-note", Static).update("Choose an action first.")
            return
        try:
            action = self._build_action(operation)
            plan = make_plan(action, self.session.rpc)
        except Exception as exc:
            self.query_one("#workflow-note", Static).update(f"Plan refused: {exc}")
            return
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _build_action(self, operation: str):
        self.pending_vm_name = None
        self.pending_vm_devices = []
        self.pending_vm_create = False
        try:
            data = json.loads(self.query_one("#workflow-data", TextArea).text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON input is invalid at line {exc.lineno}, column {exc.colno}") from None
        if not isinstance(data, dict):
            raise ValueError("action data must be a JSON object")
        if self.key == "users" and _contains_sensitive_field(data):
            raise ValueError("put passwords or SSH keys in the hidden secret field, not in JSON")
        selected = self._selected() or {}
        name_input = self.query_one("#workflow-name", Input).value.strip()
        name = name_input or selected.get("username", selected.get("name", selected.get("id")))
        secret_text = self.query_one("#workflow-secret", Input).value
        recursive = self.query_one("#workflow-recursive", Checkbox).value
        builtin = self.query_one("#workflow-builtin", Checkbox).value
        confirm_self = self.query_one("#workflow-confirm-self", Checkbox).value
        workflows = self.session.workflows

        if self.key == "datasets":
            if operation == "create":
                data.setdefault("name", str(name or ""))
                return workflows.dataset_create_action(data)
            if operation == "update":
                return workflows.dataset_update_action(str(name), data)
            return workflows.dataset_delete_action(str(name), recursive=recursive)
        if self.key == "snapshots":
            if operation == "create":
                data.setdefault("dataset", str(name or ""))
                return workflows.snapshot_create_action(data)
            if operation == "clone":
                return workflows.snapshot_clone_action(str(name), str(data.get("dataset_dst", "")))
            if operation in {"hold", "release", "rollback", "delete"}:
                return getattr(workflows, f"snapshot_{operation}_action")(str(name), **({"recursive": recursive} if operation == "delete" else {}))
        if self.key == "containers":
            if operation == "create":
                data.setdefault("name", str(name or ""))
                return workflows.container_create_action(data)
            if operation == "update":
                return workflows.container_update_action(str(name), data)
            if operation in {"start", "stop", "restart"}:
                return workflows.container_lifecycle_action(str(name), operation)
            return workflows.container_delete_action(str(name))
        if self.key == "vm":
            if operation == "console":
                return workflows.vm_console_action(str(name))
            if operation == "create":
                data.setdefault("name", str(name or ""))
                disk_zvol = data.pop("disk_zvol", None)
                disk_size = data.pop("disk_size", None)
                if disk_zvol is None or disk_size is None:
                    raise ValueError("VM creation needs disk_zvol and disk_size in the action data")
                devices = [{"dtype": "DISK", "create_zvol": True, "zvol_name": disk_zvol,
                            "zvol_volsize": disk_size}]
                iso_path = data.pop("iso", None)
                if iso_path is not None:
                    devices.append({"dtype": "CDROM", "path": iso_path})
                nic_attach = data.pop("nic_attach", None)
                if nic_attach is not None:
                    devices.append({"dtype": "NIC", "nic_attach": nic_attach})
                for attributes in devices:
                    workflows.validate_vm_device_attributes(attributes)
                self.pending_vm_name = str(name)
                self.pending_vm_devices = devices
                self.pending_vm_create = True
                return workflows.vm_create_action(data)
            if operation == "update":
                return workflows.vm_update_action(str(name), data)
            if operation == "device":
                return workflows.vm_device_create_action(str(name), data)
            if operation in {"start", "stop", "restart", "poweroff", "suspend", "resume"}:
                return workflows.vm_lifecycle_action(str(name), operation)
            return workflows.vm_delete_action(str(name))
        if self.key == "users":
            if operation == "create":
                data.setdefault("username", str(name or ""))
                if secret_text:
                    data["password"] = Secret(secret_text)
                return workflows.user_create_action(data)
            if operation == "update":
                return workflows.user_update_action(str(name), data, builtin=builtin)
            if operation == "password":
                if not secret_text:
                    raise ValueError("enter a password in the hidden secret field")
                return workflows.user_password_action(str(name), Secret(secret_text), confirm_self=confirm_self, builtin=builtin)
            if operation in {"ssh-key-add", "ssh-key-replace"}:
                keys = validate_ssh_public_keys(secret_text)
                return workflows.user_ssh_key_action(str(name), keys, replace=operation == "ssh-key-replace",
                                                     confirm_self=confirm_self, builtin=builtin)
            if operation == "ssh-key-remove":
                return workflows.user_ssh_key_action(str(name), [], remove=True,
                                                     confirm_self=confirm_self, builtin=builtin)
            return workflows.user_delete_action(str(name))
        if self.key == "sharing":
            if operation == "create-smb":
                return workflows.share_smb_create_action(data)
            if operation == "create-nfs":
                return workflows.share_nfs_create_action(data)
            if operation == "delete-smb":
                target = name_input or selected.get("name")
                return workflows.share_smb_delete_action(str(target or ""))
            if operation == "delete-nfs":
                target = name_input or selected.get("path")
                return workflows.share_nfs_delete_action(str(target or ""))
        raise ValueError("unsupported workflow action")

    def _open_next_vm_device(self) -> None:
        if not self.pending_vm_devices or self.pending_vm_name is None:
            self.query_one("#workflow-note", Static).update("VM created and all requested device plans were applied.")
            self.pending_vm_name = None
            return
        attributes = self.pending_vm_devices.pop(0)
        try:
            action = self.session.workflows.vm_device_create_action(self.pending_vm_name, attributes)
            plan = make_plan(action, self.session.rpc)
        except Exception as exc:
            self.pending_vm_devices = []
            self.pending_vm_name = None
            self.query_one("#workflow-note", Static).update(f"VM was created, but its next device plan was refused: {exc}")
            return
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        methods = {call.method for call in plan.action.calls}
        if "vm.create" in methods and self.pending_vm_create:
            self.pending_vm_create = False
            self._open_next_vm_device()
            return
        if "vm.device.create" in methods and self.pending_vm_name is not None:
            self._open_next_vm_device()
            return
        self.action_refresh()
        self.query_one("#workflow-note", Static).update(f"Applied plan; result: {_display(result)}")

    def _plan_cancelled(self, plan: Plan) -> None:
        if self.pending_vm_name is not None:
            vm_create_cancelled = any(call.method == "vm.create" for call in plan.action.calls)
            self.pending_vm_devices = []
            self.pending_vm_name = None
            self.pending_vm_create = False
            message = ("VM creation and its device plans were cancelled." if vm_create_cancelled else
                       "Remaining VM device plans were cancelled; review the created VM before adding devices.")
            self.query_one("#workflow-note", Static).update(message)


class PlanReview(ModalScreen[None]):
    CSS = """
    PlanReview { align: center middle; }
    PlanReview > Vertical { width: 85%; max-height: 85%; border: thick $primary; background: $surface; padding: 1 2; }
    PlanReview #plan-render { height: auto; max-height: 1fr; overflow-y: auto; }
    PlanReview #plan-confirm { height: 3; }
    """

    def __init__(self, plan: Plan, session: Session, parent_view: WorkflowView | None = None):
        super().__init__()
        self.plan = plan
        self.session = session
        self.parent_view = parent_view

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.plan.render(), id="plan-render")
            if self.plan.action.confirm_target:
                yield Input(placeholder=f"Type {self.plan.action.confirm_target} exactly", id="plan-confirm")
            with Horizontal():
                yield Button("Apply plan", id="plan-apply", variant="primary")
                yield Button("Cancel", id="plan-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan-cancel":
            self.dismiss()
            if self.parent_view is not None:
                self.parent_view._plan_cancelled(self.plan)
            return
        if event.button.id != "plan-apply":
            return
        confirmation = None
        if self.plan.action.confirm_target:
            confirmation = self.query_one("#plan-confirm", Input).value
        try:
            result = apply_plan(self.plan, self.session.rpc, allow_write=True, typed_confirmation=confirmation)
        except TntError as exc:
            self.query_one("#plan-render", Static).update(f"Plan refused: {exc}\n\n{self.plan.render()}")
            return
        self.dismiss()
        if self.parent_view is not None:
            self.parent_view._plan_applied(self.plan, result)
