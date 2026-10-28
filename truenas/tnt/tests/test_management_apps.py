import asyncio
import json

import pytest
from textual.widgets import Checkbox, DataTable, Static, Tree

from tnt.actions import apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp
from tnt.tui.workflows import WorkflowView


def _app_row():
    return {"id": "demo", "name": "Demo Application", "state": "RUNNING", "upgrade_available": True,
            "latest_version": "2.0.0", "latest_app_version": "2.0.0", "image_updates_available": False,
            "custom_app": False, "migrated": False, "human_version": "1.0.0", "version": "1.0.0",
            "metadata": {}, "active_workloads": {"containers": 0, "used_ports": [], "used_host_ips": [],
                                "container_details": [], "volumes": [], "images": [], "networks": []},
            "notes": None, "action_required": False, "portals": {}, "config": {"password": "synthetic-app-secret"}}


def _seed(server):
    server.apps = {"demo": _app_row()}


def test_apps_read_lifecycle_restart_and_uninstall_use_v27_jobs_and_typed_target():
    with FakeServer() as server:
        _seed(server)
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            workflows = session.workflows
            rows = workflows.apps()
            assert rows[0]["id"] == "demo" and rows[0]["upgrade_available"] is True
            start = workflows.app_lifecycle_action("demo", "start")
            assert [(call.method, call.job) for call in start.calls] == [("app.start", True)]
            apply_plan(make_plan(start, session.rpc), session.rpc, allow_write=True)
            stop = workflows.app_lifecycle_action("demo", "stop")
            assert [(call.method, call.job) for call in stop.calls] == [("app.stop", True)]
            apply_plan(make_plan(stop, session.rpc), session.rpc, allow_write=True)
            restart = workflows.app_lifecycle_action("demo", "restart")
            assert [(call.method, call.job) for call in restart.calls] == [("app.stop", True), ("app.start", True)]
            apply_plan(make_plan(restart, session.rpc), session.rpc, allow_write=True)

            uninstall = workflows.app_delete_action("demo")
            assert uninstall.confirm_target == "demo"
            assert uninstall.calls[0].method == "app.delete" and uninstall.calls[0].job
            assert uninstall.calls[0].params[1] == {"remove_images": False, "remove_ix_volumes": False}
            plan = make_plan(uninstall, session.rpc)
            with pytest.raises(Refused, match="type 'demo' exactly"):
                apply_plan(plan, session.rpc, allow_write=True, typed_confirmation="yes")
            apply_plan(plan, session.rpc, allow_write=True, typed_confirmation="demo")
            assert workflows.apps() == []
            assert "app.start" in server.called() and "app.stop" in server.called() and "app.delete" in server.called()
            assert "app.redeploy" not in server.called()
        finally:
            session.close()


def test_app_cli_json_and_uninstall_delete_data_volumes_flag(config, opener, capsys, server):
    _seed(server)
    cli = build_cli(opener=opener)
    code = cli.run(argv=["app", "list", "--config", str(config), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result[0]["upgrade_available"] is True
    assert "synthetic-app-secret" not in json.dumps(result)

    code = cli.run(argv=["app", "uninstall", "demo", "--config", str(config), "--allow-write", "--dry-run", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and '"remove_ix_volumes": false' in result["plan"].lower()


def test_apps_tui_shows_upgrade_state_and_delete_volumes_defaults_no(config):
    with FakeServer() as server:
        _seed(server)
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(150, 45)) as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "apps":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                for _ in range(40):
                    await pilot.pause(0.05)
                    if "loaded" in str(app.query_one("#workflow-note", Static).render()):
                        break
                view = app.query_one("#view-apps", WorkflowView)
                table = view.query_one("#workflow-table", DataTable)
                assert table.row_count == 1 and "upgrade_available" in str(table.get_cell_at((0, 2)) or "")
                assert "synthetic-app-secret" not in str(table.get_cell_at((0, 2)))
                view.query_one("#workflow-operation").value = "delete"
                view.query_one("#workflow-name").value = "demo"
                assert view.query_one("#workflow-recursive", Checkbox).value is False
                action = view._build_action("delete")
                assert action.calls[0].params[1]["remove_ix_volumes"] is False

        try:
            asyncio.run(go())
        finally:
            session.close()
