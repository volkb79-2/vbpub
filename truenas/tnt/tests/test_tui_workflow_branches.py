"""Focused tests for workflow screen builders, review dialogs, and safe rendering."""
from __future__ import annotations

import asyncio
import base64
import json
import struct
import threading
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea, Tree

from tnt.actions import Action, ApplyResult, CallExecution, Kind, RpcCall, make_plan
from tnt.adapters.v27 import V27
from tnt.errors import Refused
from tnt.profile import load_profiles
from tnt.session import Session
from tnt.transport import GuardedRpc
from tnt.tui.app import TntApp
from tnt.tui.workflows import PlanReview, WorkflowView, _contains_sensitive_field, _display, _flat


class Host:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []
        self.writes = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        if method in {"container.create", "container.update", "container.start", "container.stop", "container.delete",
                      "vm.create", "vm.update", "vm.device.create", "vm.start", "vm.stop", "vm.restart",
                      "vm.poweroff", "vm.suspend", "vm.resume", "vm.delete", "pool.dataset.create",
                      "pool.dataset.update", "pool.dataset.delete", "pool.snapshot.create", "pool.snapshot.clone",
                      "pool.snapshot.hold", "pool.snapshot.release", "pool.snapshot.rollback", "pool.snapshot.delete",
                      "user.create", "user.update", "user.delete", "user.set_password"}:
            self.writes.append((method, params, job))
        value = self.results.get(method, [])
        if isinstance(value, Exception):
            raise value
        return value

    def subscribe_event(self, name, callback):
        assert name == "container.metrics"
        callback("ADDED", fields={"4": {"cpu": {"cpu_user_percentage": 1.0, "cpu_system_percentage": 2.0},
                                         "mem_usage_limit": {"mem_usage_limit_used_mib": 1,
                                                              "mem_usage_limit_available_mib": 2}}})
        return "sub"

    def unsubscribe_event(self, subscription):
        assert subscription == "sub"

    def close(self):
        pass


def make_session(config, host, *, read_only=False):
    profiles, default = load_profiles(config)
    profile = profiles[default or next(iter(profiles))]
    return Session(profile, GuardedRpc(host, read_only=read_only), V27("TrueNAS-27.0.0-RC.1"))


def build_action(view, operation):
    """Exercise the same input-to-action function used by WorkflowView._make_plan."""
    vm_state = {}
    action = view._build_action_from_input(operation, view._capture_action_input(), view.session.workflows, vm_state)
    view.pending_vm_name = vm_state.get("name")
    view.pending_vm_devices = vm_state.get("devices", [])
    view.pending_vm_create = vm_state.get("create", False)
    return action


def choose(app, label):
    tree = app.query_one("#nav", Tree)
    found = None

    def walk(node):
        nonlocal found
        for child in node.children:
            if child.data and child.data.label == label:
                found = child
                return
            walk(child)
    walk(tree.root)
    assert found is not None
    tree.select_node(found)


async def wait_loaded(pilot, app):
    for _ in range(100):
        await pilot.pause(0.05)
        if " loaded." in str(app.query_one("#workflow-note", Static).render()):
            return
    raise AssertionError("workflow view did not load")


def test_safe_display_helpers_flatten_tree_and_hide_nested_fields():
    rows = _flat([{"id": "tank", "children": [{"id": "tank/home", "children": [{"name": "deep"}]},
                                          {"name": "nameless", "children": "ignored"}]},
                  "invalid", 4, None])
    assert rows == [{"id": "tank"}, {"id": "  tank/home"}, {"name": "    deep"}, {"name": "  nameless"}]
    assert _flat({"name": "root", "children": (1, 2)}) == [{"name": "root"}]
    assert _display(None) == "" and _display("hello") == "hello"
    assert _display({"password": "secret"}) == '{"password": "***"}'
    assert _contains_sensitive_field({"items": [{"New_Password": "x"}]})
    assert not _contains_sensitive_field({"comment": "password", "count": 2})


def test_workflow_action_builders_cover_all_core_screen_operations(config):
    host = Host({
        "pool.dataset.details": [{"id": "tank/home", "children": [], "mounted": False, "containers": [], "vms": []}],
        "pool.dataset.get_instance": {"id": "tank/home"}, "pool.dataset.query": [],
        "pool.snapshot.get_instance": {"id": "tank/home@nightly"}, "pool.snapshot.query": [],
        "container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"}, "autostart": False}],
        "vm.query": [{"id": 21, "name": "guest", "status": {"state": "STOPPED"}}],
        "vm.get_console": "21_guest", "vm.get_display_devices": [], "vm.get_display_web_uri": {"uri": None},
        "user.query": [{"id": 8, "username": "alice", "full_name": "Alice", "builtin": False, "local": True,
                         "sshpubkey": ""}],
        "auth.me": {"pw_name": "operator"},
    })
    session = make_session(config, host)
    key_type = b"ssh-ed25519"
    key = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", len(key_type)) + key_type + bytes(32)).decode() + " test"

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            matrix = [
                ("Datasets", "datasets", [
                    ("create", {"type": "FILESYSTEM"}, "tank/new", "pool.dataset.create"),
                    ("update", {"readonly": "ON"}, "tank/home", "pool.dataset.update"),
                    ("delete", {}, "tank/home", "pool.dataset.delete"),
                ]),
                ("Snapshots", "snapshots", [
                    ("create", {"name": "nightly"}, "tank/home", "pool.snapshot.create"),
                    ("clone", {"dataset_dst": "tank/copy"}, "tank/home@nightly", "pool.snapshot.clone"),
                    ("hold", {}, "tank/home@nightly", "pool.snapshot.hold"),
                    ("release", {}, "tank/home@nightly", "pool.snapshot.release"),
                    ("rollback", {}, "tank/home@nightly", "pool.snapshot.rollback"),
                    ("delete", {}, "tank/home@nightly", "pool.snapshot.delete"),
                ]),
                ("Containers", "containers", [
                    ("create", {"image": {"name": "nginx", "version": "latest"}}, "web", "container.create"),
                    ("update", {"autostart": True}, "web", "container.update"),
                    ("start", {}, "web", "container.start"), ("stop", {}, "web", "container.stop"),
                    ("restart", {}, "web", "container.stop"), ("delete", {}, "web", "container.delete"),
                ]),
                ("Virtual Machines", "vm", [
                    ("console", {}, "guest", "vm.get_console"),
                    ("create", {"memory": 2048, "disk_zvol": "tank/guest", "disk_size": 1073741824,
                                 "iso": "/mnt/tank/install.iso", "nic_attach": "br0"}, "guest", "vm.create"),
                    ("update", {"memory": 4096}, "guest", "vm.update"),
                    ("device", {"dtype": "NIC", "nic_attach": "br0"}, "guest", "vm.device.create"),
                    ("start", {}, "guest", "vm.start"), ("stop", {}, "guest", "vm.stop"),
                    ("restart", {}, "guest", "vm.restart"), ("poweroff", {}, "guest", "vm.poweroff"),
                    ("suspend", {}, "guest", "vm.suspend"), ("resume", {}, "guest", "vm.resume"),
                    ("delete", {}, "guest", "vm.delete"),
                ]),
                ("Local Users", "users", [
                    ("create", {"full_name": "New User", "group": 10, "password_disabled": True}, "bob", "user.create"),
                    ("update", {"full_name": "Alice Example"}, "alice", "user.update"),
                    ("access", {"locked": True}, "alice", "user.update"),
                    ("password", {}, "alice", "user.set_password"),
                    ("ssh-key-add", {}, "alice", "user.update"),
                    ("ssh-key-replace", {}, "alice", "user.update"),
                    ("ssh-key-remove", {}, "alice", "user.update"),
                    ("delete", {}, "alice", "user.delete"),
                ]),
            ]
            for label, key_name, operations in matrix:
                choose(app, label)
                await wait_loaded(pilot, app)
                view = app.query_one(f"#view-{key_name}", WorkflowView)
                for operation, data, name, method in operations:
                    view.query_one("#workflow-data", TextArea).text = json.dumps(data)
                    view.query_one("#workflow-name", Input).value = name
                    view.query_one("#workflow-secret", Input).value = (
                        key if operation in {"ssh-key-add", "ssh-key-replace"} else "safe-not-a-real-secret")
                    view.query_one("#workflow-recursive", Checkbox).value = operation == "delete"
                    action = build_action(view, operation)
                    assert action.calls[0].method == method, (label, operation, action.calls)
                    if key_name == "datasets" and operation == "create":
                        assert action.calls[0].params[0]["name"] == "tank/new"
                    if key_name == "vm" and operation == "create":
                        assert view.pending_vm_create is True and len(view.pending_vm_devices) == 3
                        assert "disk_zvol" not in action.calls[0].params[0]
                    if key_name == "users" and operation == "password":
                        assert action.calls[0].params[0]["new_password"].reveal() == "safe-not-a-real-secret"
                    if key_name == "users" and operation in {"ssh-key-add", "ssh-key-replace"}:
                        assert key in action.calls[0].params[1]["sshpubkey"].reveal()
            users = app.query_one("#view-users", WorkflowView)
            users.query_one("#workflow-data", TextArea).text = '{"full_name": "New User", "group": 10, "password_disabled": true}'
            users.query_one("#workflow-name", Input).value = "new-user"
            users.query_one("#workflow-secret", Input).value = ""
            passwordless = build_action(users, "create")
            assert "password" not in passwordless.calls[0].params[0]
            assert host.calls
    asyncio.run(go())
    session.close()


def test_workflow_view_input_refusals_loading_and_vm_recovery_messages(config):
    host = Host({"pool.dataset.details": [], "pool.snapshot.query": [], "container.query": [], "vm.query": [],
                 "user.query": [], "auth.me": {"pw_name": "operator"}})
    session = make_session(config, host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            choose(app, "Virtual Machines")
            await wait_loaded(pilot, app)
            view = app.query_one("#view-vm", WorkflowView)
            view._load_failed("offline")
            assert "Failed to load Virtual Machines" in str(app.query_one("#workflow-note", Static).render())
            with pytest.raises(ValueError, match="JSON input is invalid"):
                view.query_one("#workflow-data", TextArea).text = "{"
                build_action(view, "start")
            view.query_one("#workflow-data", TextArea).text = "[]"
            with pytest.raises(ValueError, match="JSON object"):
                build_action(view, "start")
            view.query_one("#workflow-data", TextArea).text = "{}"
            with pytest.raises(ValueError, match="disk_zvol and disk_size"):
                build_action(view, "create")
            view.pending_vm_name = "guest"
            view.pending_vm_devices = []
            view._open_next_vm_device()
            assert "VM created and all requested device plans were applied" in str(
                app.query_one("#workflow-note", Static).render())
            view.pending_vm_name = "missing"
            view.pending_vm_devices = [{"dtype": "NIC"}]
            view._open_next_vm_device()
            for _ in range(100):
                await pilot.pause(0.05)
                if "next device plan was refused" in str(app.query_one("#workflow-note", Static).render()):
                    break
            assert "next device plan was refused" in str(app.query_one("#workflow-note", Static).render())
            assert view.pending_vm_name is None and view.pending_vm_devices == []

            choose(app, "Local Users")
            await wait_loaded(pilot, app)
            users = app.query_one("#view-users", WorkflowView)
            users.query_one("#workflow-data", TextArea).text = '{"sshpubkey": "never here"}'
            with pytest.raises(ValueError, match="hidden secret field"):
                build_action(users, "update")
            users.query_one("#workflow-data", TextArea).text = "{}"
            users.query_one("#workflow-secret", Input).value = ""
            with pytest.raises(ValueError, match="enter a password"):
                build_action(users, "password")
            users.query_one("#workflow-secret", Input).value = "not-a-key"
            with pytest.raises(Refused, match="supported OpenSSH public-key"):
                build_action(users, "ssh-key-add")
            users._loaded([{"id": 8, "username": "alice", "sshpubkey": ""}])
            table = users.query_one("#workflow-table", DataTable)
            assert table.row_count == 1
            users.rows = []
            assert users._selected() is None
            users.key = "unknown"
            users.query_one("#workflow-data", TextArea).text = "{}"
            with pytest.raises(ValueError, match="unsupported workflow action"):
                build_action(users, "unrecognized")

            choose(app, "Containers")
            await wait_loaded(pilot, app)
            containers = app.query_one("#view-containers", WorkflowView)
            containers._loaded([{"name": "broken-metrics", "metrics": {
                "cpu": {"cpu_user_percentage": True, "cpu_system_percentage": 2}, "mem_usage_limit": None}}])
            rendered = str(containers.query_one("#workflow-table", DataTable).get_cell_at((0, 2)))
            assert "unavailable" in rendered
            containers.key = "snapshots"
            with pytest.raises(ValueError, match="unsupported workflow action"):
                build_action(containers, "unrecognized")
            containers.key = "containers"
            containers._plan_applied(make_plan(session.workflows.snapshot_hold_action("tank/home@nightly"), session.rpc),
                                     {"ok": True})
            assert "Applied plan; result:" in str(containers.query_one("#workflow-note", Static).render())
    asyncio.run(go())
    session.close()


def test_workflow_action_builders_cover_management_screens(config):
    from tnt.session import open_session
    from tnt.testing import FakeServer

    with FakeServer() as server:
        server.smb_shares[1] = {"id": 1, "name": "team", "path": "/mnt/tank/share", "enabled": True}
        server.nfs_shares[2] = {"id": 2, "path": "/mnt/tank/export", "enabled": True}
        server.snapshot_tasks[7] = {"id": 7, "dataset": "tank", "enabled": True,
                                    "schedule": {"hour": "02"}, "lifetime_value": 2,
                                    "lifetime_unit": "WEEK", "naming_schema": "auto-%Y-%m-%d_%H-%M"}
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                for key, operations in (
                    ("sharing", [
                        ("create-smb", {"name": "team-new", "path": "/mnt/tank/share"}, "team-new", "sharing.smb.create"),
                        ("create-nfs", {"path": "/mnt/tank/export", "networks": ["192.0.2.0/24"]}, "", "sharing.nfs.create"),
                        ("delete-smb", {}, "team", "sharing.smb.delete"),
                        ("delete-nfs", {}, "/mnt/tank/export", "sharing.nfs.delete"),
                    ]),
                    ("snapshot-tasks", [
                        ("create", {"dataset": "tank", "schedule": {"hour": "02"}, "lifetime_value": 2,
                                    "lifetime_unit": "WEEK", "naming_schema": "auto-%Y-%m-%d_%H-%M"}, "", "pool.snapshottask.create"),
                        ("run", {}, "7", "pool.snapshottask.run"),
                        ("enable", {}, "7", "pool.snapshottask.update"),
                        ("disable", {}, "7", "pool.snapshottask.update"),
                        ("delete", {}, "7", "pool.snapshottask.delete"),
                    ]),
                    ("replication", [
                        ("run", {}, "offsite", "replication.run"),
                        ("enable", {}, "offsite", "replication.update"),
                        ("disable", {}, "offsite", "replication.update"),
                    ]),
                    ("cloud-sync", [
                        ("run", {}, "offsite-copy", "cloudsync.sync"),
                        ("enable", {}, "offsite-copy", "cloudsync.update"),
                        ("disable", {}, "offsite-copy", "cloudsync.update"),
                    ]),
                    ("apps", [
                        ("start", {}, "example-app", "app.start"),
                        ("stop", {}, "example-app", "app.stop"),
                        ("restart", {}, "example-app", "app.stop"),
                        ("delete", {}, "example-app", "app.delete"),
                    ]),
                ):
                    target = None
                    tree = app.query_one("#nav", Tree)

                    def find(node):
                        nonlocal target
                        for child in node.children:
                            if child.data and child.data.key == key:
                                target = child
                                return
                            find(child)

                    find(tree.root)
                    assert target is not None
                    tree.select_node(target)
                    await wait_loaded(pilot, app)
                    view = app.query_one(f"#view-{key}", WorkflowView)
                    for operation, data, name, method in operations:
                        view.query_one("#workflow-data", TextArea).text = json.dumps(data)
                        view.query_one("#workflow-name", Input).value = name
                        view.query_one("#workflow-recursive", Checkbox).value = False
                        action = build_action(view, operation)
                        methods = [call.method for call in action.calls]
                        assert method in methods, (key, operation, methods)
                        if key == "apps" and operation == "delete":
                            assert action.calls[0].params[1]["remove_ix_volumes"] is False
                    if key == "vm":
                        view.query_one("#workflow-data", TextArea).text = json.dumps(
                            {"memory": 2048, "disk_zvol": "tank/guest-basic", "disk_size": 1073741824})
                        view.query_one("#workflow-name", Input).value = "guest-basic"
                        action = build_action(view, "create")
                        assert [device["dtype"] for device in view.pending_vm_devices] == ["DISK"]
                        assert action.calls[0].method == "vm.create"
                    if key == "snapshot-tasks":
                        view.query_one("#workflow-name", Input).value = "not-an-id"
                        with pytest.raises(ValueError, match="task ID"):
                            build_action(view, "run")
                        view.query_one("#workflow-name", Input).value = "7"
                        with pytest.raises(ValueError, match="unsupported workflow action"):
                            build_action(view, "unsupported")
                    if key == "apps":
                        view.query_one("#workflow-name", Input).value = ""
                        view._loaded([])
                        with pytest.raises(ValueError, match="exact app ID"):
                            build_action(view, "start")
                        view.query_one("#workflow-name", Input).value = "example-app"
                        with pytest.raises(ValueError, match="unsupported workflow action"):
                            build_action(view, "unsupported")
                    if key == "sharing":
                        with pytest.raises(ValueError, match="unsupported workflow action"):
                            build_action(view, "unsupported")
                    if key == "replication":
                        with pytest.raises(ValueError, match="unsupported workflow action"):
                            build_action(view, "unsupported")
                    if key == "cloud-sync":
                        with pytest.raises(ValueError, match="unsupported workflow action"):
                            build_action(view, "unsupported")

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_plan_review_refusal_apply_cancel_and_parent_callbacks(config):
    host = Host({"container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"},
                                      "autostart": False}]})
    session = make_session(config, host, read_only=True)
    action = session.workflows.container_update_action("web", {"autostart": True})
    plan = make_plan(action, session.rpc)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            choose(app, "Containers")
            await wait_loaded(pilot, app)
            view = app.query_one("#view-containers", WorkflowView)
            app.push_screen(PlanReview(plan, session, parent_view=view))
            await pilot.pause(0.05)
            await pilot.click("#plan-apply")
            await pilot.pause(0.05)
            assert not host.writes and "Plan refused" in str(app.screen.query_one("#plan-render", Static).render())
            await pilot.click("#plan-cancel")
            await pilot.pause(0.05)

            destructive = Action("TNT-39", Kind.DESTRUCTIVE, "Delete web",
                                 (RpcCall("container.delete", (4, {}), job=True),),
                                 (RpcCall("container.query", ([["name", "=", "web"]], {}),),), "web")
            delete_plan = make_plan(destructive, session.rpc)
            review = PlanReview(delete_plan, session, parent_view=view)
            app.push_screen(review)
            await pilot.pause(0.05)
            await pilot.click("#plan-cancel")
            await pilot.pause(0.05)
            assert host.writes == []

            review_no_parent = PlanReview(plan, session)
            app.push_screen(review_no_parent)
            await pilot.pause(0.05)
            await pilot.click("#plan-cancel")
            await pilot.pause(0.05)
    asyncio.run(go())
    session.close()


def test_plan_review_success_passes_typed_confirmation_and_supports_no_parent(config, monkeypatch):
    from tnt.tui import workflows as tui_workflows

    host = Host({"container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"}}]})
    session = make_session(config, host)
    action = session.workflows.container_delete_action("web")
    plan = make_plan(action, session.rpc)
    applied = []

    def fake_apply(received_plan, rpc, *, allow_write, typed_confirmation=None, job_progress=None, cancelled=None):
        applied.append((received_plan, rpc, allow_write, typed_confirmation))
        return [True]

    monkeypatch.setattr(tui_workflows, "apply_plan", fake_apply)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            review = PlanReview(plan, session)
            app.push_screen(review)
            await pilot.pause(0.05)
            review.query_one("#plan-confirm", Input).value = "web"
            review.on_button_pressed(SimpleNamespace(button=review.query_one("#plan-apply", Button)))
            for _ in range(100):
                await pilot.pause(0.05)
                if applied:
                    break
            assert len(applied) == 1 and applied[0][2:] == (True, "web")
            assert app.screen.__class__.__name__ != "PlanReview"

            uncertain_review = PlanReview(plan, session)
            app.push_screen(uncertain_review)
            await pilot.pause(0.05)
            uncertain_review._system_apply_uncertain([])
            await pilot.pause(0.05)
            assert app.screen.__class__.__name__ != "PlanReview"
    asyncio.run(go())
    session.close()


def test_plan_review_rejects_stale_sessions_and_workers_without_the_active_lease(config, monkeypatch):
    from tnt.tui import workflows as tui_workflows
    host = Host({"container.query": [{"id": 11, "name": "web", "status": {"state": "STOPPED"}}]})
    session = make_session(config, host, read_only=False)
    action = session.workflows.container_update_action("web", {"autostart": True})
    plan = make_plan(action, session.rpc)
    other = make_session(config, Host(), read_only=False)
    cancelled = []
    worker_started = threading.Event()
    begin = tui_workflows.begin_session_work

    def observe_begin(view):
        worker_started.set()
        return begin(view)

    monkeypatch.setattr(tui_workflows, "begin_session_work", observe_begin)

    async def go():
        profiles, _ = load_profiles(config)
        app = TntApp(session, hosts=profiles)
        async with app.run_test(size=(160, 50)) as pilot:
            review = PlanReview(plan, other, parent_view=SimpleNamespace(_plan_cancelled=cancelled.append))
            app.push_screen(review)
            await pilot.pause(0.05)
            assert cancelled == [plan]
            review.invalidate_for_host_change()
            assert cancelled == [plan]

            current = PlanReview(plan, session)
            app.push_screen(current)
            await pilot.pause(0.05)
            current._show_job_progress({"progress": {"percent": 25.4}})
            assert "25%" in str(current.query_one("#plan-progress", Static).render())
            current._show_job_progress({"progress": {"percent": True}})
            assert "running" in str(current.query_one("#plan-progress", Static).render())
            current.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="irrelevant")))
            assert app.screen is current

            transition = app.session_controller.begin_switch("throwaway")
            current._apply_plan(None, False)
            for _ in range(100):
                await pilot.pause(0.05)
                if worker_started.is_set() and "No calls were sent" in str(
                        current.query_one("#plan-progress", Static).render()):
                    break
            assert worker_started.is_set()
            assert "No calls were sent" in str(current.query_one("#plan-progress", Static).render())
            assert not host.writes
            assert app.session_controller.cancel_transition(transition)
            current.generation = -1
            current.on_button_pressed(SimpleNamespace(button=current.query_one("#plan-apply", Button)))
            await pilot.pause(0.05)
            assert app.screen.__class__.__name__ != "PlanReview"
    try:
        asyncio.run(go())
    finally:
        session.close()
        other.close()


@pytest.mark.parametrize("fail", [True, False])
def test_plan_review_drops_callbacks_from_cancelled_apply_workers(config, monkeypatch, fail):
    from tnt.tui import workflows as tui_workflows

    host = Host({"container.query": [{"id": 11, "name": "web", "status": {"state": "STOPPED"}}]})
    session = make_session(config, host, read_only=False)
    action = session.workflows.container_update_action("web", {"autostart": True})
    plan = make_plan(action, session.rpc)
    leases = []
    begin = tui_workflows.begin_session_work

    def capture_lease(view):
        lease = begin(view)
        leases.append(lease)
        return lease

    def cancel_during_apply(*_args, **_kwargs):
        leases[-1].cancelled.set()
        if fail:
            raise RuntimeError("host changed during apply")
        return ["completed"]

    monkeypatch.setattr(tui_workflows, "begin_session_work", capture_lease)
    monkeypatch.setattr(tui_workflows, "apply_plan", cancel_during_apply)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            choose(app, "Containers")
            await wait_loaded(pilot, app)
            for _ in range(2):
                review = PlanReview(plan, session)
                app.push_screen(review)
                await pilot.pause(0.05)
                previous_leases = len(leases)
                review._apply_plan(None, False)
                for _ in range(100):
                    await pilot.pause(0.05)
                    if len(leases) > previous_leases and leases[-1].finished.is_set():
                        break
                assert len(leases) == previous_leases + 1 and leases[-1].finished.is_set()
                assert not host.writes
                if app.screen is review:
                    review.dismiss()
                    await pilot.pause(0.05)

    try:
        asyncio.run(go())
    finally:
        session.close()


def test_plan_review_displays_partial_apply_outcomes_before_recovery(config, monkeypatch):
    from tnt.errors import PlanExecutionError
    from tnt.tui import workflows as tui_workflows

    host = Host({"container.query": [{"id": 11, "name": "web", "status": {"state": "STOPPED"}}]})
    session = make_session(config, host, read_only=False)
    action = session.workflows.container_update_action("web", {"autostart": True})
    plan = make_plan(action, session.rpc)
    partial = ApplyResult([], [CallExecution(1, "container.update", "unknown", error="reply lost")])

    def failed_apply(*_args, **_kwargs):
        raise PlanExecutionError("one outcome is uncertain", partial)

    monkeypatch.setattr(tui_workflows, "apply_plan", failed_apply)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            review = PlanReview(plan, session)
            app.push_screen(review)
            await pilot.pause(0.05)
            review._apply_plan(None, False)
            for _ in range(100):
                await pilot.pause(0.05)
                progress = str(review.query_one("#plan-progress", Static).render())
                if "Partial or uncertain result" in progress:
                    break
            assert "Partial or uncertain result" in progress
            rendered = str(review.query_one("#plan-render", Static).render())
            assert '"status": "unknown"' in rendered
            assert "Review the Jobs view and current host state" in rendered
            assert review.query_one("#plan-apply", Button).disabled is False
            assert not host.writes

    try:
        asyncio.run(go())
    finally:
        session.close()


def test_workflow_load_failure_and_unhandled_button_are_reported(config):
    host = Host({"container.query": RuntimeError("metrics query failed")})
    session = make_session(config, host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            choose(app, "Containers")
            for _ in range(100):
                await pilot.pause(0.05)
                note = str(app.query_one("#workflow-note", Static).render())
                if "Failed to load" in note:
                    break
            assert "metrics query failed" in note
            view = app.query_one("#view-containers", WorkflowView)
            button = Button("Irrelevant", id="irrelevant")
            view.on_button_pressed(Button.Pressed(button))
            assert "Failed to load" in str(app.query_one("#workflow-note", Static).render())
    asyncio.run(go())
    session.close()


def test_workflow_button_validation_and_vm_cancel_distinguishes_stage(config):
    host = Host({"vm.query": [{"id": 21, "name": "guest", "status": {"state": "STOPPED"}}]})
    session = make_session(config, host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            choose(app, "Virtual Machines")
            await wait_loaded(pilot, app)
            view = app.query_one("#view-vm", WorkflowView)
            view.query_one("#workflow-operation", Select).clear()
            await pilot.click("#workflow-plan")
            assert "Choose an action first" in str(app.query_one("#workflow-note", Static).render())
            view.query_one("#workflow-operation", Select).value = "start"
            await pilot.pause(0.1)
            view.query_one("#workflow-data", TextArea).text = "{"
            await pilot.pause(0.1)
            await pilot.click("#workflow-plan")
            await pilot.pause(0.1)
            assert "Plan refused: JSON input is invalid" in str(app.query_one("#workflow-note", Static).render())

            vm_action = Action("TNT-40", Kind.MUTATE, "Create new", (RpcCall("vm.create", ({"name": "new"},)),),
                               (RpcCall("vm.query", ([['name', '=', 'new']], {})),))
            view.pending_vm_name = "guest"
            view.pending_vm_devices = [{"dtype": "NIC"}]
            view.pending_vm_create = True
            view._plan_cancelled(make_plan(vm_action, session.rpc))
            assert "VM creation and its device plans were cancelled" in str(app.query_one("#workflow-note", Static).render())

            view.pending_vm_name = "guest"
            view.pending_vm_devices = [{"dtype": "NIC"}]
            device_action = Action("TNT-40", Kind.MUTATE, "Add NIC", (RpcCall("vm.device.create", ({"vm": 21},)),),
                                   (RpcCall("vm.query", ([['name', '=', 'guest']], {})),))
            view._plan_cancelled(make_plan(device_action, session.rpc))
            assert "Remaining VM device plans were cancelled" in str(app.query_one("#workflow-note", Static).render())
    asyncio.run(go())
    session.close()
