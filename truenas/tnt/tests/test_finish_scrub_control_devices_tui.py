"""TNT-57 scrub pause/cancel and TNT-60 TUI device operations with the second destructive confirmation."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Input, Static, TextArea, Tree

from tnt.actions import Kind, apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp
from tnt.tui.workflows import OPERATIONS, PlanReview

GIB = 1024 ** 3
RUNNING = {"function": "SCRUB", "state": "SCANNING"}


def _schedule(server, ident=1):
    server.scrub_tasks[ident] = {"id": ident, "pool": 1, "pool_name": "tank", "enabled": True,
                                 "threshold": 35, "description": "", "schedule": {}}


def _open(server):
    return open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)


@pytest.mark.parametrize("control,expected", [("pause", "PAUSE"), ("cancel", "CANCEL")])
def test_scrub_control_sends_the_exact_v27_payload_for_a_running_scrub(control, expected):
    with FakeServer() as server:
        _schedule(server)
        server.pools[0]["scan"] = dict(RUNNING)
        session = _open(server)
        try:
            action = session.workflows.scrub_task_control_action(1, control)
            assert action.kind is Kind.MUTATE and action.id == "TNT-57"
            call = action.calls[0]
            assert call.method == "zpool.scrub.run" and call.job is True
            assert call.params == ({"pool_name": "tank", "scan_type": "SCRUB", "action": expected, "threshold": 0},)
            apply_plan(make_plan(action, session.rpc), session.rpc, allow_write=True)
            assert server.scrubs_started == ["tank"]
        finally:
            session.close()


def test_scrub_control_refuses_idle_unknown_and_mismatched_state():
    with FakeServer() as server:
        _schedule(server)
        session = _open(server)
        try:
            with pytest.raises(Refused, match="must be 'pause' or 'cancel'"):
                session.workflows.scrub_task_control_action(1, "resume")
            for scan in (None, {"function": "SCRUB", "state": "FINISHED"}, {"function": "RESILVER", "state": "SCANNING"}, "x"):
                server.pools[0]["scan"] = scan
                with pytest.raises(Refused, match="no running scrub to cancel"):
                    session.workflows.scrub_task_control_action(1, "cancel")
            server.pools[0]["scan"] = dict(RUNNING)
            server.scrub_tasks[1]["pool_name"] = "stale"
            with pytest.raises(Refused, match="pool name"):
                session.workflows.scrub_task_control_action(1, "pause")
        finally:
            session.close()


def test_scrub_pause_and_cancel_cli_verbs(config, opener, server, capsys):
    _schedule(server)
    server.pools[0]["scan"] = dict(RUNNING)
    cli = build_cli(opener=opener)
    for verb in ("pause", "cancel"):
        assert cli.run(argv=["task", "scrub", verb, "1", "--config", str(config), "--allow-write"]) == 0
    assert server.scrubs_started == ["tank", "tank"]
    server.pools[0]["scan"] = None
    assert cli.run(argv=["task", "scrub", "cancel", "1", "--config", str(config), "--allow-write"]) != 0


def test_tui_operation_lists_expose_scrub_control_and_device_verbs():
    assert ("Pause running scrub", "pause") in OPERATIONS["scrub-tasks"]
    assert ("Cancel running scrub", "cancel") in OPERATIONS["scrub-tasks"]
    for item in (("List devices", "devices"), ("Update device", "device-update"), ("Delete device", "device-delete")):
        assert item in OPERATIONS["vm"]


def _vm_with_devices(server):
    vm = server._dispatch("vm.create", [{"name": "guest", "memory": 2048, "autostart": False}])
    vm["status"] = {"state": "STOPPED", "pid": None}
    disk = server._dispatch("vm.device.create", [{"vm": vm["id"], "order": 1, "attributes": {
        "dtype": "DISK", "create_zvol": True, "zvol_name": "tank/guest0", "zvol_volsize": GIB, "type": "VIRTIO"}}])
    iso = server._dispatch("vm.device.create", [{"vm": vm["id"], "order": 2, "attributes": {
        "dtype": "CDROM", "path": "/mnt/tank/iso/a.iso"}}])
    return disk, iso


def _open_view(app, key_title):
    tree = app.query_one("#nav", Tree)
    target = None

    def find(node):
        nonlocal target
        for child in node.children:
            if child.data and child.data.key == key_title:
                target = child
                return
            find(child)
    find(tree.root)
    assert target is not None
    tree.select_node(target)


def test_tui_device_actions_build_validate_and_apply_with_second_confirmation():
    with FakeServer() as server:
        disk, iso = _vm_with_devices(server)
        session = _open(server)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                _open_view(app, "vm")
                view = None
                for _ in range(200):
                    await pilot.pause(0.05)
                    found = app.query("#view-vm")
                    if found and "loaded." in str(found.first().query_one("#workflow-note", Static).render()):
                        view = found.first()
                        break
                else:
                    raise AssertionError("VM view did not load")
                view.query_one("#workflow-name", Input).value = "guest"

                def build(operation, text):
                    view.query_one("#workflow-data", TextArea).text = text
                    return view._build_action_from_input(operation, view._capture_action_input(), session.workflows, {})

                listing = build("devices", "{}")
                assert listing.kind is Kind.READ and listing.calls[0].method == "vm.device.query"

                update = build("device-update", f'{{"device_id": {iso["id"]}, "order": 7}}')
                assert update.calls[0].method == "vm.device.update"
                with pytest.raises(ValueError, match="device ID"):
                    build("device-update", '{"order": 7}')
                with pytest.raises(ValueError, match="device ID"):
                    build("device-delete", '{"device_id": true}')
                with pytest.raises(ValueError, match="unsupported device update"):
                    build("device-update", f'{{"device_id": {iso["id"]}, "bogus": 1}}')
                with pytest.raises(ValueError, match="unsupported device delete"):
                    build("device-delete", f'{{"device_id": {iso["id"]}, "bogus": 1}}')

                keep = build("device-delete", f'{{"device_id": {iso["id"]}}}')
                assert keep.confirm_second is None and keep.calls[0].params[1] == {
                    "force": False, "raw_file": False, "zvol": False}
                destroy = build("device-delete", f'{{"device_id": {disk["id"]}, "delete_zvol": true, "force": true}}')
                assert destroy.confirm_second == "tank/guest0"
                assert destroy.calls[0].params[1] == {"force": True, "raw_file": False, "zvol": True}

                # The plan review must offer, and require, the second typed confirmation.
                dialog = PlanReview(make_plan(destroy, session.rpc), session, parent_view=view)
                app.push_screen(dialog)
                await pilot.pause(0.1)
                target = dialog.query_one("#plan-confirm-second-target", Static).render().plain
                assert "tank/guest0" in target
                dialog.query_one("#plan-confirm", Input).value = f"guest#{disk['id']}"
                dialog.query_one("#plan-confirm-second", Input).value = "tank/wrong"
                dialog.on_button_pressed(SimpleNamespace(button=dialog.query_one("#plan-apply", Button)))
                for _ in range(200):
                    await pilot.pause(0.05)
                    if "Plan refused" in str(dialog.query_one("#plan-render", Static).render()):
                        break
                else:
                    raise AssertionError("the wrong second confirmation was not refused")
                assert disk["id"] in server.vm_devices and "tank/guest0" in server.datasets
                dialog.query_one("#plan-confirm-second", Input).value = "tank/guest0"
                dialog.on_button_pressed(SimpleNamespace(button=dialog.query_one("#plan-apply", Button)))
                for _ in range(100):
                    await pilot.pause(0.05)
                    if disk["id"] not in server.vm_devices:
                        break
                assert disk["id"] not in server.vm_devices and "tank/guest0" not in server.datasets

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_plan_review_without_a_second_confirmation_has_no_second_input():
    with FakeServer() as server:
        _disk, iso = _vm_with_devices(server)
        session = _open(server)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                plan = make_plan(session.workflows.vm_device_delete_action("guest", iso["id"]), session.rpc)
                dialog = PlanReview(plan, session)
                app.push_screen(dialog)
                await pilot.pause(0.1)
                assert not dialog.query("#plan-confirm-second")
                await pilot.click("#plan-cancel")

        try:
            asyncio.run(go())
        finally:
            session.close()
