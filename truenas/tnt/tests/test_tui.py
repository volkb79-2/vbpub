import asyncio
import base64
import json
import struct

import pytest
from textual.widgets import DataTable, Input, Select, Static, TextArea, Tree

from tnt.adapters.v27 import V27
from tnt.profile import load_profiles
from tnt.session import Session
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.transport import GuardedRpc
from tnt.tui.app import TntApp
from tnt.tui.disks import DisksView
from tnt.tui.workflows import PlanReview, WorkflowView


@pytest.fixture
def session(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    yield s
    s.close()


class MemoryHost:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []
        self.vm = None
        self.vm_devices = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        if method == "vm.create":
            data = params[0]
            self.vm = {"id": 21, "name": data["name"], "memory": data["memory"],
                       "vcpus": data.get("vcpus", 1), "status": {"state": "STOPPED"},
                       "devices": [], "display_available": False}
            return self.vm
        if method == "vm.device.create":
            device = {"id": len(self.vm_devices) + 1, **params[0]}
            self.vm_devices.append(device)
            self.vm["devices"].append(device)
            return device
        if method == "vm.query" and self.vm is not None:
            filters = params[0] if params else []
            if not filters or filters[0][2] == self.vm["name"]:
                return [self.vm]
            return []
        rows = self.results.get(method, [])
        if method.endswith(".query") and params and isinstance(params[0], list) and params[0]:
            filters = params[0]
            if len(filters) == 1 and len(filters[0]) == 3 and filters[0][1] == "=":
                key, _, expected = filters[0]
                return [row for row in rows if isinstance(row, dict) and row.get(key) == expected]
        return rows

    def subscribe(self, name, callback, payload=None, sync=False):
        assert name == "container.metrics" and sync is True
        containers = self.results.get("container.query", [])
        fields = {str(row["id"]): {"cpu": {"cpu_user_percentage": 1.0, "cpu_system_percentage": 2.0},
                                    "mem_usage_limit": {"mem_usage_limit_used_mib": 12.0,
                                                         "mem_usage_limit_available_mib": 512.0}}
                  for row in containers if isinstance(row.get("status"), dict)
                  and row["status"].get("state") == "RUNNING"}
        if fields:
            callback("ADDED", fields=fields)
        return "memory-subscription"

    def unsubscribe(self, subscription_id):
        assert subscription_id == "memory-subscription"

    def close(self):
        pass


def workflow_session(config, results=None, *, host=None):
    profiles, default = load_profiles(config)
    profile = profiles[default or next(iter(profiles))]
    return Session(profile, GuardedRpc(host or MemoryHost(results), read_only=False), V27("TrueNAS-27.0.0-RC.1"))


def select(app, label):
    tree = app.query_one("#nav", Tree)

    def find(node):
        for child in node.children:
            if child.data and child.data.label == label:
                return child
            hit = find(child)
            if hit:
                return hit
    node = find(tree.root)
    tree.select_node(node)


async def settle(pilot, app, until):
    for _ in range(100):
        await pilot.pause(0.05)
        if until():
            return
    raise AssertionError("view never settled")


def test_navigation_shows_the_disks_view_fed_by_the_query_layer(session, server):
    async def go():
        app = TntApp(session)
        async with app.run_test(size=(140, 40)) as pilot:
            assert "read-only" in str(app.query_one("#banner", Static).render())
            select(app, "Disks")
            await settle(pilot, app, lambda: app.query(DataTable) and app.query_one("#disks-table", DataTable).row_count == 4)
            table = app.query_one("#disks-table", DataTable)
            assert [table.get_cell_at((i, 0)) for i in range(4)] == ["sda", "sdb", "sdc", "sdd"]
            assert "1 without partition data" in str(app.query_one("#disks-note", Static).render())
            assert app.current == "storage/disks"
            calls = len([m for m in server.called() if m == "disk.query"])
            await pilot.press("r")
            await settle(pilot, app, lambda: len([m for m in server.called() if m == "disk.query"]) > calls)
    asyncio.run(go())


def test_planned_screens_say_so_and_the_palette_lists_every_leaf(session):
    async def go():
        app = TntApp(session)
        async with app.run_test(size=(140, 40)) as pilot:
            select(app, "Replication")
            await pilot.pause(0.2)
            text = str(app.query_one("#view-planned", Static).render())
            assert "Replication: planned" in text
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert "Go to Storage > Disks" in titles and "Go to System > Network" in titles and "Go to Dashboard" in titles
            assert "Go to Containers" in titles and "Go to Virtual Machines" in titles
            assert "Go to Credentials > Local Users" in titles and "Go to Data Protection > Snapshots" in titles
            assert "Plan Delete selected in Containers" in titles
            app.action_focus_nav()
            app.action_help()
            command = next(c for c in app.get_system_commands(app.screen) if c.title == "Go to Storage > Disks")
            command.callback()
            await settle(pilot, app, lambda: app.current == "storage/disks")
            tree = app.query_one("#nav", Tree)
            tree.select_node(tree.root.children[1])  # a branch node (Storage): selecting it shows nothing new
            await pilot.pause(0.1)
            assert app.current == "storage/disks"
    asyncio.run(go())


def test_core_workflow_screens_load_shared_queries_and_show_action_lists(config):
    host = MemoryHost({
        "pool.dataset.details": [{"id": "tank", "used": {"value": "1 GiB"}}],
        "pool.snapshot.query": [],
        "container.query": [{"id": 4, "name": "web", "status": {"state": "RUNNING"},
                             "autostart": True, "cpuset": "0-1"}],
        "vm.query": [{"id": 8, "name": "guest", "status": {"state": "STOPPED"}, "memory": 2048,
                      "vcpus": 2, "autostart": True, "devices": [], "display_available": False}],
        "user.query": [{"id": 1001, "uid": 1001, "username": "alice", "full_name": "Alice",
                        "builtin": False, "local": True, "sshpubkey": "ssh-ed25519 hidden-key", "groups": []}],
    })
    session = workflow_session(config, host=host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            for label, expected, method in (("Datasets", "tank", "pool.dataset.details"),
                                            ("Snapshots", "Actions:", "pool.snapshot.query"),
                                            ("Containers", "web", "container.query"),
                                            ("Virtual Machines", "guest", "vm.query"),
                                            ("Local Users", "alice", "user.query")):
                select(app, label)
                await settle(pilot, app, lambda: "loaded" in str(app.query_one("#workflow-note", Static).render()))
                note = str(app.query_one("#workflow-note", Static).render())
                assert "loaded" in note
                if expected == "Actions:":
                    assert expected in str(app.query_one("#workflow-actions", Static).render())
                else:
                    table = app.query_one("#workflow-table", DataTable)
                    assert any(expected in str(table.get_cell_at((row, column))) for row in range(table.row_count)
                               for column in range(3)), (label, note, table.row_count, host.calls)
                    if label == "Local Users":
                        alice_row = next(row for row in range(table.row_count)
                                         if str(table.get_cell_at((row, 0))) == "alice")
                        assert "hidden-key" not in str(table.get_cell_at((alice_row, 2)))
                assert any(call[0] == method for call in host.calls)
            app.action_focus_nav()
    asyncio.run(go())
    session.close()


def test_vm_workflow_action_list_includes_console_information():
    from tnt.tui.workflows import OPERATIONS

    assert ("Console/VNC info", "console") in OPERATIONS["vm"]


def test_destructive_tui_plan_dialog_shows_calls_and_requires_exact_target(config):
    host = MemoryHost({"container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"},
                                             "autostart": True, "cpuset": None}]})
    session = workflow_session(config, host=host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            select(app, "Containers")
            await settle(pilot, app, lambda: app.query_one("#workflow-table", DataTable).row_count == 1)
            app.query_one("#workflow-operation", Select).value = "delete"
            app.query_one("#workflow-name", Input).value = "web"
            await pilot.click("#workflow-plan")
            await pilot.pause(0.1)
            assert app.screen.__class__.__name__ == "PlanReview", str(app.query_one("#workflow-note", Static).render())
            assert "container.delete" in str(app.screen.query_one("#plan-render", Static).render())
            assert app.screen.query_one("#plan-confirm", Input).placeholder.endswith("web exactly")
            await pilot.click("#plan-cancel")
    asyncio.run(go())
    session.close()


def test_user_tui_can_add_replace_and_remove_ssh_public_keys(config):
    key_type = b"ssh-ed25519"
    key = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", len(key_type)) + key_type + bytes(32)).decode() + " test"
    host = MemoryHost({"user.query": [{"id": 1001, "username": "alice", "builtin": False, "local": True,
                                       "sshpubkey": "old-key"}], "auth.me": {"pw_name": "operator"}})
    session = workflow_session(config, host=host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            select(app, "Local Users")
            await settle(pilot, app, lambda: "loaded" in str(app.query_one("#workflow-note", Static).render()))
            view = app.query_one("#view-users", WorkflowView)
            secret = app.query_one("#workflow-secret", Input)
            name = app.query_one("#workflow-name", Input)
            name.value = "alice"
            secret.value = key

            app.query_one("#workflow-operation", Select).value = "ssh-key-add"
            add = view._build_action("ssh-key-add")
            assert "old-key" in add.calls[0].params[1]["sshpubkey"].reveal()
            assert key in add.calls[0].params[1]["sshpubkey"].reveal()

            app.query_one("#workflow-operation", Select).value = "ssh-key-replace"
            replace = view._build_action("ssh-key-replace")
            assert replace.calls[0].params[1]["sshpubkey"].reveal() == key

            app.query_one("#workflow-operation", Select).value = "ssh-key-remove"
            remove = view._build_action("ssh-key-remove")
            assert remove.calls[0].params[1]["sshpubkey"].reveal() == ""
            assert "Plan Add SSH public key in Local Users" in [item.title for item in app.get_system_commands(app.screen)]
    asyncio.run(go())
    session.close()


def test_vm_tui_create_reviews_each_exact_device_call(config):
    host = MemoryHost({"vm.query": [], "pool.query": [], "boot.get_state": {}, "disk.query": []})
    session = workflow_session(config, host=host)

    async def go():
        app = TntApp(session)
        async with app.run_test(size=(160, 50)) as pilot:
            select(app, "Virtual Machines")
            await settle(pilot, app, lambda: "loaded" in str(app.query_one("#workflow-note", Static).render()))
            app.query_one("#workflow-operation", Select).value = "create"
            app.query_one("#workflow-name", Input).value = "guest"
            app.query_one("#workflow-data", TextArea).text = json.dumps({
                "memory": 2048, "disk_zvol": "tank/guest-disk", "disk_size": 10737418240,
                "iso": "/mnt/tank/install.iso", "nic_attach": "br0",
            })
            await pilot.click("#workflow-plan")
            assert isinstance(app.screen, PlanReview), str(app.query_one("#workflow-note", Static).render())
            assert "vm.create" in str(app.screen.query_one("#plan-render", Static).render())
            await pilot.click("#plan-apply")
            await settle(pilot, app, lambda: isinstance(app.screen, PlanReview) and
                         "vm.device.create" in str(app.screen.query_one("#plan-render", Static).render()))
            expected = ("zvol_name", "path", "nic_attach")
            for index, field in enumerate(expected):
                rendered = str(app.screen.query_one("#plan-render", Static).render())
                assert "vm.device.create" in rendered and field in rendered
                await pilot.click("#plan-apply")
                if index < len(expected) - 1:
                    next_field = expected[index + 1]
                    await settle(pilot, app, lambda: isinstance(app.screen, PlanReview) and
                                 next_field in str(app.screen.query_one("#plan-render", Static).render()))
            await settle(pilot, app, lambda: app.screen.__class__.__name__ != "PlanReview")
            assert [call[0] for call in host.calls].count("vm.device.create") == 3
            assert "VM created and all requested device plans were applied." in str(
                app.query_one("#workflow-note", Static).render()
            )
    asyncio.run(go())
    session.close()


def test_disks_view_reports_a_failed_load():
    server = FakeServer(fail={"disk.query": "backend exploded"})
    with server:
        s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

        async def go():
            app = TntApp(s)
            async with app.run_test(size=(140, 40)) as pilot:
                select(app, "Disks")
                await settle(pilot, app, lambda: app.query("#disks-note") and "Failed" in str(app.query_one("#disks-note", Static).render()))
                assert "backend exploded" in str(app.query_one("#disks-note", Static).render())
        asyncio.run(go())
        s.close()


def test_insecure_banner_is_marked(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    s.insecure_tls = True

    async def go():
        app = TntApp(s)
        async with app.run_test(size=(140, 40)):
            banner = app.query_one("#banner", Static)
            assert banner.has_class("insecure") and "TLS VERIFICATION OFF" in str(banner.render())
    asyncio.run(go())
    s.close()
