from __future__ import annotations

import asyncio
import json

import pytest

from tnt.actions import ApplyResult, CallExecution
from tnt.errors import PlanExecutionError, Refused
from textual.widgets import Button, DataTable, Input, Static, Tree

from tnt.cli import build_cli
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp
from tnt.tui.core_views import DashboardView, JobsView, ServicesView, StorageHealthView


def _select(app, label):
    tree = app.query_one("#nav", Tree)
    target = None

    def find(node):
        nonlocal target
        for child in node.children:
            if child.data and child.data.label == label:
                target = child
                return
            find(child)

    find(tree.root)
    assert target is not None
    tree.select_node(target)


async def _settle(pilot, predicate):
    for _ in range(200):
        await pilot.pause(0.05)
        if predicate():
            return
    app = pilot.app
    view = app.query_one("#content").children[0] if app.query_one("#content").children else None
    status = app.query_one("#core-status", Static).render().plain if view is not None else "no view"
    raise AssertionError(f"core read view did not settle: current={app.current!r}, status={status!r}, "
                         f"offset={getattr(view, 'page_offset', None)!r}, loading={getattr(view, 'loading', None)!r}")


def test_core_view_rows_render_available_and_fallback_values():
    dashboard = DashboardView(None)._rows({
        "host": {"name": "nas", "hostname": "nas.local", "version": None},
        "cpu": {"status": "available", "aggregations": {"mean": 12.5}},
        "memory": {"status": "unavailable", "error": "unsupported"},
    })
    assert ("Version", "Unknown") in dashboard
    assert ("CPU history (min / mean / max)", '{"mean": 12.5}') in dashboard
    assert ("MEMORY history", "Unavailable: unsupported") in dashboard

    jobs = JobsView(None)._rows({
        "rows": [
            {"id": 1, "method": "app.start", "state": "RUNNING", "progress": None},
            {"id": 2, "method": "app.stop", "state": "SUCCESS",
             "progress": {"percent": None, "description": ""}},
            {"id": 3, "method": "app.update", "state": "RUNNING",
             "progress": {"percent": 42, "description": "pulling"}},
        ],
        "local_jobs": [
            {"job_id": 4, "method": "app.start", "state": "RUNNING",
             "progress": {"percent": 0, "description": ""}},
            {"job_id": 5, "method": "app.stop", "state": "SUCCESS", "progress": None},
        ],
    })
    assert [row[3] for row in jobs] == ["Unknown", "Unknown", "42% pulling", "0%", "Unknown"]
    assert jobs[3][1] == "app.start · local"

    services = ServicesView(None)
    assert services._rows({"rows": [], "possibly_more": False}) == []
    assert services._rows({"rows": [], "possibly_more": True})[-1][0] == "Result limit reached"

    health = StorageHealthView(None)
    rows = health._rows({
        "pools": [
            {"name": "tank", "status_code": "ONLINE", "status": "DEGRADED", "warning": None},
            {"name": "backup", "status_code": None, "status": "OFFLINE"},
            {"name": "archive", "status_code": None, "status": None, "healthy": False},
        ],
        "disks": [{"name": "sda", "pool": "tank", "size": 100}],
        "pools_possibly_more": False,
        "disks_possibly_more": False,
    })
    assert [row[2] for row in rows] == ["ONLINE", "OFFLINE", "False", "tank"]
    assert health._rows({"pools": [], "disks": [], "pools_possibly_more": False,
                         "disks_possibly_more": True})[-1][2] == "Possibly truncated"


def test_overview_cli_commands_return_json_for_all_five_views(config, opener, capsys):
    cli = build_cli(opener=opener)
    commands = [
        ["dashboard"], ["alerts", "list"], ["jobs", "list", "--limit", "20", "--offset", "0"],
        ["services"], ["storage", "health"],
    ]
    results = []
    for command in commands:
        code = cli.run(argv=[*command, "--config", str(config), "--json"])
        output = capsys.readouterr().out
        assert code == 0
        results.append(json.loads(output))
    assert results[0]["host"]["hostname"] == "test"
    assert results[1] == []
    assert results[2]["scope"] == "host-wide" and results[2]["limit"] == 20
    assert results[2]["local_jobs"] == []
    assert results[3]["rows"][0]["service"] == "cifs"
    assert results[4]["pools"][0]["healthy"] is True


def test_dashboard_accepts_fractional_uptime_seconds_from_v27_fake_server():
    with FakeServer() as server:
        server.system_info["uptime_seconds"] = 86400.5
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.read_views.dashboard()
            assert result["uptime_seconds"] == 86400.5
        finally:
            session.close()


def test_dashboard_unavailable_graphs_and_job_rows_without_progress_descriptions():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            dashboard = DashboardView(session)
            payload = {"host": {"name": "lab", "hostname": "nas", "version": None},
                       "cpu": {"status": "unavailable", "error": "reporting disabled"},
                       "memory": {"status": "available", "aggregations": {"mean": 4}}}
            rows = dashboard._rows(payload)
            assert ("CPU history", "Unavailable: reporting disabled") in rows
            assert ("MEMORY history (min / mean / max)", '{"mean": 4}') in rows
            dashboard.on_input_changed(type("Changed", (), {"input": type("Input", (), {"id": "other"})()})())

            jobs = JobsView(session)._rows({
                "rows": [{"id": 7, "method": "app.start", "state": "RUNNING", "progress": {"percent": 25}},
                         {"id": 9, "method": "app.stop", "state": "SUCCESS", "progress": None},
                         {"id": 10, "method": "app.restart", "state": "RUNNING",
                          "progress": {"percent": 50, "description": "Restarting service"}}],
                "local_jobs": [{"job_id": "8", "method": "app.stop", "state": "WAITING",
                                "progress": {"percent": None, "description": "Waiting for lock"}}],
            })
            assert jobs[0][3] == "25%" and jobs[1][3] == "Unknown"
            assert jobs[2][3] == "50% Restarting service" and jobs[3][3] == "Unknown Waiting for lock"
        finally:
            session.close()


def test_dashboard_rejects_invalid_uptime_numbers_from_v27_fake_server():
    for value in (True, -0.5, float("inf"), float("nan")):
        with FakeServer() as server:
            server.system_info["uptime_seconds"] = value
            session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
            try:
                with pytest.raises(Refused, match="uptime_seconds"):
                    session.read_views.dashboard()
            finally:
                session.close()


def test_dashboard_accepts_array_samples_from_v27_reporting_fake_server():
    with FakeServer() as server:
        server.reporting_graphs = {
            "cpu": [[1793242030, 0, 0, 1, 0, 0]],
            "memory": [[1793242030, 123, 456]],
        }
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.read_views.dashboard()
            assert result["cpu"]["status"] == "available"
            assert result["cpu"]["data"] == [[1793242030, 0, 0, 1, 0, 0]]
            assert result["memory"]["data"] == [[1793242030, 123, 456]]
        finally:
            session.close()


def test_overview_cli_keeps_reporting_unavailable_and_alert_denials_as_errors(config, opener, server, capsys):
    cli = build_cli(opener=opener)
    server.fail["reporting.get_data"] = "REPORTING_READ required"
    code = cli.run(argv=["dashboard", "--config", str(config), "--json"])
    dashboard = json.loads(capsys.readouterr().out)
    assert code == 0
    assert dashboard["cpu"]["status"] == "unavailable"
    assert "REPORTING_READ" in dashboard["cpu"]["error"]

    server.fail["alert.list"] = "ALERT_LIST_READ required"
    code = cli.run(argv=["alerts", "list", "--config", str(config), "--json"])
    output = capsys.readouterr()
    assert code != 0 and "ALERT_LIST_READ" in output.err
    assert output.out == ""


def test_cli_emits_nonzero_partial_job_outcome_with_its_remote_id(config, opener, monkeypatch, capsys):
    def fail_after_apply(*_args, **_kwargs):
        partial = ApplyResult([], [CallExecution(1, "app.start", "failed", "73", "FAILED", 1,
                                                 "job failed")])
        raise PlanExecutionError("plan stopped; review Jobs before recovery", partial)

    monkeypatch.setattr("tnt.cli.apply_plan", fail_after_apply)
    cli = build_cli(opener=opener)
    code = cli.run(argv=["app", "start", "example-app", "--config", str(config),
                         "--allow-write", "--json"])
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert code == 1
    assert payload["completed"] is False
    assert payload["calls"][0]["job_id"] == "73"
    assert payload["calls"][0]["state"] == "FAILED"
    assert "review Jobs" in payload["error"]


def test_core_read_views_load_with_search_copy_status_and_sanitized_error(config):
    with FakeServer() as server:
        server.reporting_graphs = {"cpu": [{"time": 1, "value": 12.5}],
                                   "memory": [{"time": 1, "value": 71.0}]}
        server.alerts = [{"id": 1, "uuid": "alert-1", "source": "middlewared", "klass": "DiskAlert",
                          "args": {}, "node": "nas", "key": "disk-1", "datetime": "2026-10-28T10:00:00Z",
                          "last_occurrence": "2026-10-28T10:00:00Z", "dismissed": False, "mail": {},
                          "text": "Example disk alert", "level": "WARNING", "formatted": None,
                          "one_shot": False}]
        server.active_jobs = [{"id": ident, "method": "app.start", "state": "RUNNING",
                               "progress": {"percent": 25, "description": "Starting"} if ident % 2 else None,
                               "logs_excerpt": "app start", "error": None}
                              for ident in range(1, 201)]
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        session.job_registry.record_remote(job_id=1, method="app.start", state="RUNNING", generation=1)
        for ident in range(1000, 1201):
            session.job_registry.record_remote(job_id=ident, method="app.stop", state="SUCCESS", generation=1)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 48)) as pilot:
                server.fail["system.info"] = f"credential {server.secret().reveal()}"
                _select(app, "Dashboard")
                await _settle(pilot, lambda: "Unavailable:" in str(app.query_one("#core-error", Static).render()))
                dashboard = app.query_one("#view-dashboard")
                assert "No successful sample" in str(app.query_one("#core-status", Static).render())
                assert server.secret().reveal() not in str(app.query_one("#core-error", Static).render())
                server.fail.pop("system.info")
                app.action_refresh()
                app.action_refresh()  # a second request is ignored while the first is in flight
                await _settle(pilot, lambda: app.query_one("#core-table", DataTable).row_count > 0)
                assert "refreshed" in str(app.query_one("#core-status", Static).render())
                assert {"system.info", "reporting.get_data"} <= set(server.called())
                dashboard = app.query_one("#view-dashboard")
                dashboard._failed("temporary read failure after a successful sample")
                assert "last successful sample" in str(app.query_one("#core-status", Static).render())
                await pilot.click("#core-copy")
                search = app.query_one("#core-search", Input)
                search.value = "no visible row has this phrase"
                await pilot.pause(0.05)
                assert app.query_one("#core-table", DataTable).row_count == 0
                search.value = ""
                await pilot.pause(0.05)
                app.query_one("#core-search", Input).parent.parent.action_search()
                await pilot.pause(0.05)
                assert app.query_one("#core-search", Input).has_focus
                await pilot.click("#core-help-button")
                assert "visible" in app.query_one("#core-help", Static).classes
                await pilot.click("#core-help-button")
                await pilot.click("#core-refresh")
                await _settle(pilot, lambda: not app.query_one("#core-error", Static).render().plain)

                _select(app, "Alerts")
                await _settle(pilot, lambda: app.current == "alerts" and "alert.list" in server.called()
                              and "refreshed" in str(app.query_one("#core-status", Static).render()))
                assert app.query_one("#core-table", DataTable).row_count == 1

                _select(app, "Jobs")
                await _settle(pilot, lambda: "page 1, 200 remote row(s), 200 local" in
                              str(app.query_one("#core-status", Static).render()))
                assert "recent 200 only" in str(app.query_one("#core-status", Static).render())
                assert app.query_one("#core-table", DataTable).row_count == 400
                assert not app.query_one("#jobs-next", Button).disabled
                jobs_view = app.query_one("#view-jobs")
                jobs_view.on_button_pressed(Button.Pressed(app.query_one("#jobs-next", Button)))
                await _settle(pilot, lambda: "page 2, 0 remote row(s), 0 local" in
                              str(app.query_one("#core-status", Static).render()))
                assert app.query_one("#jobs-prev", Button).disabled is False
                jobs_view.on_button_pressed(Button.Pressed(app.query_one("#jobs-next", Button)))
                assert jobs_view.page_offset == 200  # no next page is advertised by the host
                jobs_view.on_button_pressed(Button.Pressed(app.query_one("#jobs-prev", Button)))
                await _settle(pilot, lambda: "page 1, 200 remote row(s), 200 local" in
                              str(app.query_one("#core-status", Static).render()))
                jobs_view._loaded({"rows": [], "local_jobs": [], "possibly_more": False,
                                   "role_status": "limited", "role_error": "missing JOB_READ",
                                   "local_jobs_possibly_more": False, "local_jobs_limit": 200,
                                   "scope": "host-wide"})
                assert "missing JOB_READ" in str(app.query_one("#core-status", Static).render())

                _select(app, "Services")
                await _settle(pilot, lambda: app.query_one("#core-table", DataTable).row_count == 1)
                search = app.query_one("#core-search", Input)
                search.value = "cifs"
                await pilot.pause(0.05)
                assert app.query_one("#core-table", DataTable).row_count == 1
                await pilot.click("#core-copy")

                _select(app, "Health")
                await _settle(pilot, lambda: app.query_one("#core-table", DataTable).row_count == 5)
                assert not {"service.start", "service.stop", "service.restart"} & set(server.called())
                assert ServicesView(session)._rows({"rows": [], "possibly_more": True})[-1][0] == "Result limit reached"
                assert StorageHealthView(session)._rows({"pools": [], "disks": [], "pools_possibly_more": True,
                                                          "disks_possibly_more": False})[-1][2] == "Possibly truncated"

                server.fail["pool.query"] = f"credential {server.secret().reveal()}"
                app.action_refresh()
                await _settle(pilot, lambda: "Unavailable:" in str(app.query_one("#core-error", Static).render()))
                error_text = str(app.query_one("#core-error", Static).render())
                assert server.secret().reveal() not in error_text and "<redacted>" in error_text

        try:
            asyncio.run(go())
        finally:
            session.close()
