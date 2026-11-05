from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, Input, Select, Static, TextArea, Tree

from helpers import wait_until
from tnt.actions import Kind, apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.menu import MENU
from tnt.session import open_session
from tnt.testing import FakeServer, _RpcError
from tnt.tui.app import TntApp
from tnt.tui.core_views import PoolsView
from tnt.tui.workflows import WorkflowView


def _apply(session, action, confirm=None):
    plan = make_plan(action, session.rpc)
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirm)


def test_scrub_schedule_crud_and_run_now_uses_v27_job():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            workflows = session.workflows
            assert workflows.scrub_tasks() == []
            create = workflows.scrub_task_create_action({
                "pool": 1,
                "threshold": 14,
                "description": "monthly maintenance",
                "schedule": {"minute": "00", "hour": "03", "dom": "1", "month": "*", "dow": "*"},
                "enabled": True,
            })
            _apply(session, create)
            task = workflows.scrub_tasks()[0]
            assert task["pool_name"] == "tank" and task["threshold"] == 14

            update = workflows.scrub_task_update_action(task["id"], {"threshold": 7, "enabled": False})
            _apply(session, update)
            assert workflows.scrub_task(task["id"])["enabled"] is False
            assert workflows.scrub_task(task["id"])["threshold"] == 7

            run_now = workflows.scrub_task_run_action(task["id"])
            assert run_now.kind is Kind.MUTATE
            call = run_now.calls[0]
            assert call.method == "zpool.scrub.run" and call.job is True
            assert call.params == ({"pool_name": "tank", "scan_type": "SCRUB", "action": "START", "threshold": 0},)
            assert "pool.scrub.scrub" not in server.called()
            _apply(session, run_now)
            assert server.scrubs_started == ["tank"]
            assert "zpool.scrub.run" in server.called()

            server.pools.append({"id": 3, "name": "backup", "healthy": True, "warning": False,
                                 "status": "ONLINE", "status_code": "ONLINE", "scan": None,
                                 "expand": None, "size": 1000, "allocated": 100, "free": 900,
                                 "topology": {"data": []}})
            move = workflows.scrub_task_update_action(task["id"], {"pool": 3})
            _apply(session, move)
            assert workflows.scrub_task(task["id"])["pool_name"] == "backup"

            delete = workflows.scrub_task_delete_action(task["id"])
            assert delete.kind is Kind.DESTRUCTIVE
            with pytest.raises(Refused, match="confirmation"):
                _apply(session, delete)
            _apply(session, delete, delete.confirm_target)
            assert workflows.scrub_tasks() == []
        finally:
            session.close()


def test_scrub_schedule_validation_and_boot_pool_guards():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            make_task = session.workflows.scrub_task_create_action
            with pytest.raises(Refused, match="pool"):
                make_task({})
            with pytest.raises(Refused, match="pool"):
                make_task({"pool": True})
            with pytest.raises(Refused, match="pool"):
                make_task({"pool": 999})
            with pytest.raises(Refused, match="field"):
                make_task({"pool": 1, "unknown": "value"})
            with pytest.raises(Refused, match="schedule"):
                make_task({"pool": 1, "schedule": {"minute": 0}})
            with pytest.raises(Refused, match="threshold"):
                make_task({"pool": 1, "threshold": True})
            with pytest.raises(Refused, match="description"):
                make_task({"pool": 1, "description": False})
            with pytest.raises(Refused, match="enabled"):
                make_task({"pool": 1, "enabled": 1})
            with pytest.raises(Refused, match="at least one"):
                session.workflows.scrub_task_update_action(1, {})

            server.scrub_tasks[8] = {"id": 8, "pool": 1, "pool_name": "tank", "enabled": True,
                                     "threshold": True, "description": "", "schedule": {}}
            with pytest.raises(Refused, match="malformed"):
                session.workflows.scrub_tasks()
            del server.scrub_tasks[8]

            server.scrub_tasks[7] = {"id": 7, "pool": True, "pool_name": "tank", "enabled": True,
                                     "threshold": 35, "description": "", "schedule": {}}
            with pytest.raises(Refused, match="positive integer"):
                session.workflows.scrub_task(7)
            del server.scrub_tasks[7]

            server.scrub_tasks[6] = {"id": 6, "pool": 1, "pool_name": "stale-name", "enabled": True,
                                     "threshold": 35, "description": "", "schedule": {}}
            with pytest.raises(Refused, match="pool name"):
                session.workflows.scrub_task(6)
            del server.scrub_tasks[6]

            server.scrub_tasks[5] = {"id": 5, "pool": 1, "pool_name": "tank", "enabled": True,
                                     "threshold": 35, "description": "", "schedule": {}}
            server.pools[0]["status"] = "OFFLINE"
            with pytest.raises(Refused, match="ONLINE or DEGRADED"):
                session.workflows.scrub_task_run_action(5)
            server.pools[0]["status"] = "ONLINE"
            server.pools[0]["scan"] = {"function": "UNKNOWN", "state": "SCANNING"}
            with pytest.raises(Refused, match="scan state is unknown"):
                session.workflows.scrub_task_run_action(5)
            server.pools[0]["scan"] = {"function": "SCRUB", "state": "SCANNING"}
            with pytest.raises(Refused, match="active scrub or resilver"):
                session.workflows.scrub_task_run_action(5)
            server.pools[0]["scan"] = None

            names = iter(("tank", "renamed"))
            resolve_pool = session.workflows._scrub_pool_by_id

            def changing_pool_name(pool_id, *, fields=("id", "name")):
                pool = resolve_pool(pool_id, fields=fields)
                pool["name"] = next(names)
                return pool

            session.workflows._scrub_pool_by_id = changing_pool_name
            with pytest.raises(Refused, match="pool name"):
                session.workflows.scrub_task_run_action(5)

            server.pools.append({"id": 2, "name": "boot-pool", "healthy": True, "warning": False,
                                 "status": "ONLINE", "status_code": "ONLINE", "scan": None, "expand": None,
                                 "topology": {"data": []}})
            with pytest.raises(Refused, match="boot-pool"):
                make_task({"pool": 2})

            server.scrub_tasks[9] = {"id": 9, "pool": 2, "pool_name": "boot-pool", "enabled": True,
                                     "threshold": 35, "description": "", "schedule": {}}
            for action in (
                lambda: session.workflows.scrub_task_update_action(9, {"enabled": False}),
                lambda: session.workflows.scrub_task_run_action(9),
                lambda: session.workflows.scrub_task_delete_action(9),
            ):
                with pytest.raises(Refused, match="boot-pool"):
                    action()
        finally:
            session.close()


def test_scrub_task_cli_and_tui_are_wired_to_shared_workflows(config, opener, tmp_path):
    cli = build_cli(opener=opener)
    assert cli.run(argv=["task", "scrub", "list", "--config", str(config), "--json"]) == 0
    create_file = tmp_path / "scrub-create.json"
    create_file.write_text(json.dumps({"pool": 1, "threshold": 35, "enabled": True}))
    assert cli.run(argv=["task", "scrub", "create", "--config", str(config), "--file", str(create_file),
                         "--allow-write"]) == 0
    assert cli.run(argv=["task", "scrub", "show", "1", "--config", str(config), "--json"]) == 0
    update_file = tmp_path / "scrub-update.json"
    update_file.write_text(json.dumps({"threshold": 14}))
    assert cli.run(argv=["task", "scrub", "update", "1", "--config", str(config), "--file", str(update_file),
                         "--allow-write"]) == 0
    assert cli.run(argv=["task", "scrub", "run", "1", "--config", str(config), "--allow-write"]) == 0
    assert cli.run(argv=["task", "scrub", "delete", "1", "--config", str(config), "--allow-write",
                         "--confirm", "tank scrub schedule [1]"]) == 0

    with FakeServer() as server:
        server.scrub_tasks[1] = {"id": 1, "pool": 1, "pool_name": "tank", "enabled": True,
                                 "threshold": 35, "description": "", "schedule": {}}
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test() as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "scrub-tasks":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                await wait_until(pilot, lambda: "loaded." in str(
                    app.query_one("#view-scrub-tasks", WorkflowView).query_one("#workflow-note", Static).render()),
                    what="scrub schedule view loading")
                view = app.query_one("#view-scrub-tasks", WorkflowView)
                view.query_one("#workflow-name", Input).value = "1"
                view.query_one("#workflow-data", TextArea).text = '{"pool":1}'
                assert view._build_action_from_input("create", view._capture_action_input(),
                                                     session.workflows, {}).calls[0].method == "pool.scrub.create"
                view.query_one("#workflow-data", TextArea).text = '{"enabled":false}'
                for operation, expected in (("update", "pool.scrub.update"), ("run", "zpool.scrub.run"),
                                            ("delete", "pool.scrub.delete")):
                    action = view._build_action_from_input(operation, view._capture_action_input(),
                                                           session.workflows, {})
                    assert action.calls[0].method == expected
                view.query_one("#workflow-name", Input).value = "not-a-schedule-id"
                with pytest.raises(ValueError, match="schedule ID"):
                    view._build_action_from_input("run", view._capture_action_input(),
                                                  session.workflows, {})
                return

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_pool_inventory_rejects_bad_space_and_formats_scan_and_limit_state():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            server.pools[0]["size"] = True
            with pytest.raises(Refused, match="size"):
                session.read_views.pools()

            view = PoolsView(session)
            rows = view._rows({"rows": [{"name": "tank", "status": "ONLINE", "status_code": "ONLINE",
                                          "healthy": True, "warning": False, "allocated": 20, "size": 100,
                                          "free": 80, "scan": {"function": "SCRUB", "state": "FINISHED",
                                                                    "percentage": 100, "total_secs_left": 0,
                                                                    "secret": "hidden"}, "topology": {}}],
                               "possibly_more": True})
            assert len(rows) == 2
            assert "secret" not in rows[0][-1]
            assert rows[1][0] == "Result limit reached"
        finally:
            session.close()


def test_fake_server_scrub_handlers_report_missing_pool_errors():
    server = FakeServer()
    with pytest.raises(_RpcError, match="not found"):
        server._scrub_task_create([{"pool": 999}])
    server.scrub_tasks[1] = {"id": 1, "pool": 1, "pool_name": "tank", "threshold": 35,
                             "description": "", "schedule": {}, "enabled": True}
    with pytest.raises(_RpcError, match="not found"):
        server._scrub_task_update([1, {"pool": 999}])


def test_pool_inventory_cli_and_menu_screen(config, opener):
    cli = build_cli(opener=opener)
    assert cli.run(argv=["storage", "pools", "--config", str(config), "--json"]) == 0

    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

        async def go():
            app = TntApp(session)
            async with app.run_test() as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "storage/pools":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                await wait_until(pilot, lambda: app.query_one("#view-pools #core-table", DataTable).row_count,
                                 what="pool inventory view loading")
                table = app.query_one("#view-pools #core-table", DataTable)
                assert "tank" in " ".join(str(table.get_cell_at((0, col))) for col in range(len(table.columns)))
                return

        try:
            asyncio.run(go())
        finally:
            session.close()
