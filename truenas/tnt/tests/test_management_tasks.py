from __future__ import annotations

import asyncio

import pytest
from textual.widgets import DataTable, Static, Tree

from tnt.actions import apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


def _apply(session, action, confirm=None):
    plan = make_plan(action, session.rpc)
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirm)


def test_data_protection_tasks_use_v27_methods_and_mask_cloud_credentials():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            workflows = session.workflows
            snapshot = workflows.snapshot_task_create_action({
                "dataset": "tank", "schedule": {"minute": "00", "hour": "02", "dom": "*", "month": "*", "dow": "*"},
                "lifetime_value": 14, "lifetime_unit": "DAY", "naming_schema": "daily-%Y-%m-%d_%H-%M",
            })
            _apply(session, snapshot)
            tasks = workflows.snapshot_tasks()
            assert len(tasks) == 1 and tasks[0]["dataset"] == "tank"
            run_snapshot = workflows.snapshot_task_run_action(tasks[0]["id"])
            assert run_snapshot.calls[0].job is False
            assert "pool.snapshottask.run" in make_plan(run_snapshot, session.rpc).render()
            _apply(session, run_snapshot)
            assert workflows.snapshot_task(tasks[0]["id"])["last_run"] == "2026-10-28T00:00:00Z"
            _apply(session, workflows.snapshot_task_enable_action(tasks[0]["id"], False))
            assert workflows.snapshot_tasks()[0]["enabled"] is False
            delete = workflows.snapshot_task_delete_action(tasks[0]["id"])
            _apply(session, delete, delete.confirm_target)
            assert workflows.snapshot_tasks() == []

            replication = workflows.replication_tasks()
            assert replication[0]["source_datasets"] == ["tank/home"]
            run_replication = workflows.replication_run_action("offsite")
            rendered = make_plan(run_replication, session.rpc).render()
            assert "tank/home" in rendered and "archive/home" in rendered
            _apply(session, run_replication, "offsite")
            _apply(session, workflows.replication_enable_action("offsite", False))
            assert workflows.replication_tasks()[0]["enabled"] is False

            cloud = workflows.cloudsync_tasks()
            assert cloud[0]["credentials"] == "***"
            assert cloud[0]["encryption_password"] == "***"
            assert "synthetic-cloud-secret" not in repr(cloud)
            _apply(session, workflows.cloudsync_enable_action("offsite-copy", False))
            _apply(session, workflows.cloudsync_run_action("offsite-copy"), "offsite-copy")
            assert workflows.cloudsync_tasks()[0]["enabled"] is False
            assert "cloudsync.sync" in server.called()
            assert "replication.run" in server.called()
        finally:
            session.close()


@pytest.mark.parametrize("payload, message", [
    ({"dataset": "tank", "schedule": {}, "lifetime_value": 1, "lifetime_unit": "DAY",
      "naming_schema": "daily-%Y-%m-%d_%H-%M"}, "schedule"),
    ({"dataset": "tank", "schedule": {"hour": "02"}, "lifetime_value": 0, "lifetime_unit": "DAY",
      "naming_schema": "daily-%Y-%m-%d_%H-%M"}, "retention"),
    ({"dataset": "tank", "schedule": {"hour": "02"}, "lifetime_value": 1, "lifetime_unit": "DAY",
      "naming_schema": "daily"}, "naming_schema"),
    ({"dataset": "boot-pool", "schedule": {"hour": "02"}, "lifetime_value": 1, "lifetime_unit": "DAY",
      "naming_schema": "daily-%Y-%m-%d_%H-%M"}, "boot-pool"),
])
def test_snapshot_task_create_validates_schedule_retention_name_and_boot_pool(payload, message):
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            with pytest.raises(Refused, match=message):
                session.workflows.snapshot_task_create_action(payload)
        finally:
            session.close()


def test_data_protection_cli_reads_tasks_and_masks_cloud_credentials(config, opener, capsys):
    cli = build_cli(opener=opener)
    code = cli.run(argv=["task", "replication", "list", "--config", str(config), "--json"])
    out = capsys.readouterr().out
    assert code == 0 and '"target_dataset": "archive/home"' in out
    code = cli.run(argv=["task", "cloudsync", "list", "--config", str(config), "--json"])
    out = capsys.readouterr().out
    assert code == 0 and "***" in out and "synthetic-cloud-secret" not in out


def test_data_protection_tui_views_load_snapshot_replication_and_cloudsync(config):
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(150, 48)) as pilot:
                tree = app.query_one("#nav", Tree)
                for key, expected_method, expected_value in (
                        ("snapshot-tasks", "pool.snapshottask.query", None),
                        ("replication", "replication.query", "offsite"),
                        ("cloud-sync", "cloudsync.query", "offsite-copy")):
                    target = None

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
                    for _ in range(40):
                        await pilot.pause(0.05)
                        note = str(app.query_one("#workflow-note", Static).render())
                        if "loaded." in note:
                            break
                    table = app.query_one("#workflow-table", DataTable)
                    assert any(expected_method == call for call in server.called())
                    if expected_value is not None:
                        rendered = " ".join(str(table.get_cell_at((row, col))) for row in range(table.row_count) for col in range(3))
                        assert expected_value in rendered
                        if key == "cloud-sync":
                            assert "synthetic-cloud-secret" not in rendered

        try:
            asyncio.run(go())
        finally:
            session.close()
