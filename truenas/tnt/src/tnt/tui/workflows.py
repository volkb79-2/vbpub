"""Shared query screens and reviewed action dialogs for the M1 workflows."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea

from ..actions import Action, Plan, apply_plan, make_plan
from ..errors import PlanExecutionError, TntError
from ..profile import Secret
from ..session import Session
from ..workflows import _safe_value, validate_ssh_public_keys
from .session_work import (begin as begin_session_work, error_message, post as post_session_callback,
                           run as run_session_work, run_cancellable as run_cancellable_session_work)


SCREEN_TITLES = {
    "datasets": "Datasets",
    "snapshots": "Snapshots",
    "containers": "Containers",
    "vm": "Virtual Machines",
    "users": "Local Users",
    "sharing": "Shares",
    "snapshot-tasks": "Periodic Snapshot Tasks",
    "replication": "Replication Tasks",
    "cloud-sync": "Cloud Sync Tasks",
    "apps": "Apps",
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
    "users": (("Create user", "create"), ("Update selected basics", "update"), ("Update account access", "access"),
              ("Set password", "password"),
              ("Add SSH public key", "ssh-key-add"), ("Replace SSH public keys", "ssh-key-replace"),
              ("Remove SSH public keys", "ssh-key-remove"), ("Delete selected", "delete")),
    "sharing": (("Create SMB share", "create-smb"), ("Create NFS share", "create-nfs"),
                ("Delete selected SMB share", "delete-smb"), ("Delete selected NFS share", "delete-nfs")),
    "snapshot-tasks": (("Create task", "create"), ("Run selected now", "run"),
                        ("Enable selected", "enable"), ("Disable selected", "disable"), ("Delete selected", "delete")),
    "replication": (("Run selected now", "run"), ("Enable selected", "enable"), ("Disable selected", "disable")),
    "cloud-sync": (("Run selected now", "run"), ("Enable selected", "enable"), ("Disable selected", "disable")),
    "apps": (("Start selected", "start"), ("Stop selected", "stop"), ("Restart selected", "restart"),
             ("Uninstall selected", "delete")),
}


_READERS = {
    "datasets": lambda w: w.datasets(),
    "snapshots": lambda w: w.snapshots(),
    "containers": lambda w: w.containers(),
    "vm": lambda w: w.vms(),
    "users": lambda w: w.users(),
    "sharing": lambda w: ([{"protocol": "SMB", **row} for row in w.shares_smb()] +
                           [{"protocol": "NFS", **row} for row in w.shares_nfs()]),
    "snapshot-tasks": lambda w: w.snapshot_tasks(),
    "replication": lambda w: w.replication_tasks(),
    "cloud-sync": lambda w: w.cloudsync_tasks(),
    "apps": lambda w: w.apps(),
}


@dataclass(frozen=True)
class _WorkflowActionInput:
    data_text: str
    selected: dict[str, Any]
    name_input: str
    secret_text: str
    recursive: bool
    builtin: bool
    confirm_self: bool


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
                yield Checkbox("Delete data volumes" if self.key == "apps" else "Recursive", id="workflow-recursive")
                yield Checkbox("Built-in user", id="workflow-builtin")
                yield Checkbox("Confirm current user", id="workflow-confirm-self")
            yield Button("Plan action", id="workflow-plan", variant="primary")
        # Keep the JSON editor usable without Textual's optional tree-sitter grammar extras.
        yield TextArea("{}", id="workflow-data")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#workflow-note", Static).update(f"Loading {SCREEN_TITLES[self.key]}…")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        run_session_work(self, lambda session: _flat(_READERS[self.key](session.workflows)),
                         "_loaded", "_load_failed")

    def _load_failed(self, message: str) -> None:
        self.query_one("#workflow-note", Static).update(f"Failed to load {SCREEN_TITLES[self.key]}: {message}")

    def _loaded(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        table = self.query_one("#workflow-table", DataTable)
        table.clear()
        for index, row in enumerate(self.rows):
            name = row.get("username", row.get("name", row.get("description", row.get("dataset", row.get("id", "")))))
            state = row.get("type", row.get("state", ""))
            status = row.get("status")
            if isinstance(status, dict):
                state = status.get("state", state)
            summary = {key: row.get(key) for key in ("used", "available", "quota", "refquota", "recordsize", "comments",
                                                       "compression", "readonly", "mounted", "mountpoint", "vcpus", "memory",
                                                       "cpuset", "autostart", "builtin", "enabled", "source_datasets",
                                                       "target_dataset", "direction", "transfer_mode", "schedule",
                                                       "lifetime_value", "lifetime_unit", "upgrade_available",
                                                       "latest_version", "latest_app_version", "version") if key in row}
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
        form = self._capture_action_input()
        self.query_one("#workflow-note", Static).update("Checking current host state and preparing the plan…")
        self.query_one("#workflow-plan", Button).disabled = True
        self._make_plan(operation, form)

    @work(thread=True, exclusive=True)
    def _make_plan(self, operation: str, form: _WorkflowActionInput) -> None:
        def build_and_plan(session, lease):
            vm_state: dict[str, Any] = {}
            action = self._build_action_from_input(operation, form, session.workflows, vm_state)
            plan = make_plan(action, session.rpc, cancelled=lease.cancelled.is_set)
            return plan, vm_state

        run_cancellable_session_work(
            self, build_and_plan, "_plan_prepared", "_plan_failed")

    def _capture_action_input(self) -> _WorkflowActionInput:
        return _WorkflowActionInput(
            self.query_one("#workflow-data", TextArea).text,
            dict(self._selected() or {}),
            self.query_one("#workflow-name", Input).value.strip(),
            self.query_one("#workflow-secret", Input).value,
            self.query_one("#workflow-recursive", Checkbox).value,
            self.query_one("#workflow-builtin", Checkbox).value,
            self.query_one("#workflow-confirm-self", Checkbox).value,
        )

    def _plan_prepared(self, result: tuple[Plan, dict[str, Any]]) -> None:
        plan, vm_state = result
        self.pending_vm_name = vm_state.get("name")
        self.pending_vm_devices = vm_state.get("devices", [])
        self.pending_vm_create = vm_state.get("create", False)
        self.query_one("#workflow-plan", Button).disabled = False
        self._plan_ready(plan)

    def _plan_ready(self, plan: Plan) -> None:
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_failed(self, message: str) -> None:
        self.pending_vm_name = None
        self.pending_vm_devices = []
        self.pending_vm_create = False
        self.query_one("#workflow-plan", Button).disabled = False
        self.query_one("#workflow-note", Static).update(f"Plan refused: {message}")

    def _build_action(self, operation: str):
        vm_state: dict[str, Any] = {}
        action = self._build_action_from_input(operation, self._capture_action_input(), self.session.workflows,
                                               vm_state)
        self.pending_vm_name = vm_state.get("name")
        self.pending_vm_devices = vm_state.get("devices", [])
        self.pending_vm_create = vm_state.get("create", False)
        return action

    def _build_action_from_input(self, operation: str, form: _WorkflowActionInput, workflows: Any,
                                 vm_state: dict[str, Any]) -> Action:
        try:
            data = json.loads(form.data_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON input is invalid at line {exc.lineno}, column {exc.colno}") from None
        if not isinstance(data, dict):
            raise ValueError("action data must be a JSON object")
        if self.key == "users" and _contains_sensitive_field(data):
            raise ValueError("put passwords or SSH keys in the hidden secret field, not in JSON")
        selected = form.selected
        name_input = form.name_input
        name = name_input or selected.get("username", selected.get("name", selected.get("id")))
        secret_text = form.secret_text
        recursive = form.recursive
        builtin = form.builtin
        confirm_self = form.confirm_self

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
                vm_state.update({"name": str(name), "devices": devices, "create": True})
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
                return workflows.user_update_action(str(name), data, builtin=builtin, confirm_self=confirm_self)
            if operation == "access":
                return workflows.user_access_action(str(name), data, confirm_self=confirm_self)
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
        if self.key == "snapshot-tasks":
            if operation == "create":
                return workflows.snapshot_task_create_action(data)
            try:
                task_id = int(name)
            except (TypeError, ValueError):
                raise ValueError("select a periodic snapshot task ID") from None
            if operation == "run":
                return workflows.snapshot_task_run_action(task_id)
            if operation in {"enable", "disable"}:
                return workflows.snapshot_task_enable_action(task_id, operation == "enable")
            if operation == "delete":
                return workflows.snapshot_task_delete_action(task_id)
        if self.key == "replication":
            target = name_input or selected.get("name")
            if operation == "run":
                return workflows.replication_run_action(str(target or ""))
            if operation in {"enable", "disable"}:
                return workflows.replication_enable_action(str(target or ""), operation == "enable")
        if self.key == "cloud-sync":
            target = name_input or selected.get("description")
            if operation == "run":
                return workflows.cloudsync_run_action(str(target or ""))
            if operation in {"enable", "disable"}:
                return workflows.cloudsync_enable_action(str(target or ""), operation == "enable")
        if self.key == "apps":
            target = name_input or selected.get("id")
            if not isinstance(target, str) or not target:
                raise ValueError("select an installed app or enter its exact app ID")
            if operation in {"start", "stop", "restart"}:
                return workflows.app_lifecycle_action(target, operation)
            if operation == "delete":
                return workflows.app_delete_action(target, delete_data_volumes=recursive)
        raise ValueError("unsupported workflow action")

    def _open_next_vm_device(self) -> None:
        if not self.pending_vm_devices or self.pending_vm_name is None:
            self.query_one("#workflow-note", Static).update("VM created and all requested device plans were applied.")
            self.pending_vm_name = None
            return
        attributes = self.pending_vm_devices.pop(0)
        self.query_one("#workflow-note", Static).update("Checking the created VM and preparing its next device plan…")
        self._make_vm_device_plan(self.pending_vm_name, attributes)

    @work(thread=True, exclusive=True)
    def _make_vm_device_plan(self, vm_name: str, attributes: dict[str, Any]) -> None:
        def build_and_plan(session, lease):
            action = session.workflows.vm_device_create_action(vm_name, attributes)
            return make_plan(action, session.rpc, cancelled=lease.cancelled.is_set)

        run_cancellable_session_work(
            self, build_and_plan, "_plan_ready", "_vm_device_plan_failed")

    def _vm_device_plan_failed(self, message: str) -> None:
        self.pending_vm_devices = []
        self.pending_vm_name = None
        self.pending_vm_create = False
        self.query_one("#workflow-note", Static).update(
            f"VM was created, but its next device plan was refused: {message}")

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
        records = [call.as_dict() for call in getattr(result, "calls", ())]
        detail = {"result": list(result) if isinstance(result, list) else result, "calls": records}
        self.query_one("#workflow-note", Static).update(f"Applied plan; result: {_display(detail)}")

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
    PlanReview #plan-progress { height: auto; color: $text-muted; }
    """

    def __init__(self, plan: Plan, session: Session, parent_view: Any | None = None):
        super().__init__()
        self.plan = plan
        self.session = session
        self.parent_view = parent_view
        self.generation: int | None = None
        self._invalidated = False

    def on_mount(self) -> None:
        self.generation, active = self.app.session_controller.capture()
        if active is not self.session:
            self.invalidate_for_host_change()

    def invalidate_for_host_change(self) -> None:
        if self._invalidated:
            return
        self._invalidated = True
        if self.parent_view is not None:
            self.parent_view._plan_cancelled(self.plan)
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Vertical():
            mode = "WRITE ENABLED" if not self.session.rpc.read_only else "read-only"
            generation = self.app.session_controller.generation if getattr(self, "is_mounted", False) else "pending"
            yield Static(f"Host: {self.session.profile.name}  mode: {mode}  generation: {generation}\n\n"
                         + self.plan.render(), id="plan-render")
            yield Static("", id="plan-progress")
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
        if self._invalidated or self.generation is None or not self.app.session_controller.is_current(
                self.generation, self.session):
            self.invalidate_for_host_change()
            return
        confirmation = None
        if self.plan.action.confirm_target:
            confirmation = self.query_one("#plan-confirm", Input).value
        methods = {call.method for call in self.plan.action.calls}
        self.query_one("#plan-apply", Button).disabled = True
        self.query_one("#plan-progress", Static).update("Applying reviewed plan…")
        self._apply_plan(confirmation, bool(methods & {"system.reboot", "system.shutdown", "update.run"}))

    @work(thread=True, exclusive=True)
    def _apply_plan(self, confirmation: str | None, system_action: bool) -> None:
        lease = begin_session_work(self)
        if lease is None or lease.session is None:
            return
        methods = {call.method for call in self.plan.action.calls}
        calls_before = len(self.session.rpc.calls)
        try:
            result = apply_plan(
                self.plan, lease.session.rpc, allow_write=True, typed_confirmation=confirmation,
                job_progress=lambda job: post_session_callback(self, lease, "_show_job_progress", job),
                cancelled=lease.cancelled.is_set)
        except Exception as exc:
            # Power actions can close the websocket after TrueNAS accepts the request. The request result is
            # then unknown: never submit it again, but dismiss the plan and let the power view check the host.
            power_methods = methods & {"system.reboot", "system.shutdown"}
            power_was_called = any(method in power_methods for method, _params in self.session.rpc.calls[calls_before:])
            if not lease.cancelled.is_set():
                if system_action and power_was_called:
                    calls = [call.as_dict() for call in getattr(getattr(exc, "partial", None), "calls", ())]
                    post_session_callback(self, lease, "_system_apply_uncertain", calls)
                elif isinstance(exc, PlanExecutionError):
                    post_session_callback(self, lease, "_apply_partial_failed", error_message(lease.session, exc),
                                          [call.as_dict() for call in exc.partial.calls])
                else:
                    post_session_callback(self, lease, "_apply_failed", error_message(lease.session, exc))
        else:
            if not lease.cancelled.is_set():
                post_session_callback(self, lease, "_apply_succeeded", result)
        finally:
            self.app.finish_session_work(lease)

    def _show_job_progress(self, job: Any) -> None:
        progress = job.get("progress") if isinstance(job, dict) else None
        percent = progress.get("percent") if isinstance(progress, dict) else None
        if isinstance(percent, (int, float)) and not isinstance(percent, bool):
            self.query_one("#plan-progress", Static).update(f"Update job progress: {percent:.0f}%")
        else:
            self.query_one("#plan-progress", Static).update("Update job is running…")

    def _apply_failed(self, message: str) -> None:
        self.query_one("#plan-apply", Button).disabled = False
        self.query_one("#plan-progress", Static).update("")
        self.query_one("#plan-render", Static).update(f"Plan refused: {message}\n\n{self.plan.render()}")

    def _apply_partial_failed(self, message: str, calls: list[dict[str, Any]]) -> None:
        self.query_one("#plan-apply", Button).disabled = False
        self.query_one("#plan-progress", Static).update("Partial or uncertain result; do not retry blindly.")
        self.query_one("#plan-render", Static).update(
            f"{message}\n\nPer-call outcomes (including job IDs):\n{json.dumps(calls, indent=2, sort_keys=True)}\n\n"
            "Review the Jobs view and current host state before planning recovery.\n\n" + self.plan.render())

    def _system_apply_uncertain(self, calls: list[dict[str, Any]]) -> None:
        self.dismiss()
        if self.parent_view is not None:
            self.parent_view._plan_apply_uncertain(self.plan, calls)

    def _apply_succeeded(self, result: Any) -> None:
        self.dismiss()
        if self.parent_view is not None:
            self.parent_view._plan_applied(self.plan, result)
