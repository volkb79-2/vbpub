import asyncio
import json
from datetime import datetime, timezone

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static, Tree

from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


def _entry(timestamp, message_timestamp, event):
    return {"audit_id": f"audit-{message_timestamp}", "message_timestamp": message_timestamp,
            "timestamp": timestamp, "address": "192.0.2.44", "username": "operator", "session": None,
            "service": "MIDDLEWARE", "service_data": {"note": "password=synthetic-audit-secret"},
            "event": event, "event_data": {"access_token": "synthetic-audit-token", "status": "ok"},
            "success": True}


def _seed(server):
    server.audit_entries = [
        _entry("2026-10-28T10:00:00Z", 1793181600, "USER_LOGIN"),
        _entry("2026-10-28T09:00:00Z", 1793178000, "USER_LOGOUT"),
    ]


def test_audit_query_filters_pages_and_scrubs_secret_material():
    with FakeServer() as server:
        _seed(server)
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            rows = session.workflows.audit_entries(service="MIDDLEWARE", since="2026-10-27T00:00:00Z",
                                                   until="2026-10-29T00:00:00Z", user="operator",
                                                   event="USER_LOGIN", limit=1, offset=0)
            assert len(rows) == 1 and rows[0]["event"] == "USER_LOGIN"
            assert "synthetic-audit-secret" not in json.dumps(rows)
            assert "synthetic-audit-token" not in json.dumps(rows)
            request = next(request for request in server.requests if request["method"] == "audit.query")
            data = request["params"][0]
            assert data["services"] == ["MIDDLEWARE"]
            assert data["query-options"]["limit"] == 1 and data["query-options"]["offset"] == 0
            assert {tuple(filter_) for filter_ in data["query-filters"]} == {
                ("message_timestamp", ">=", 1793059200), ("message_timestamp", "<", 1793232000),
                ("username", "=", "operator"), ("event", "=", "USER_LOGIN"),
            }
            page = session.workflows.audit_entries(limit=1, offset=1)
            assert len(page) == 1 and page[0]["event"] == "USER_LOGOUT"
            before = len([r for r in server.requests if r["method"] == "audit.query"])
            with pytest.raises(Refused):
                session.workflows.audit_entries(service="OTHER")
            with pytest.raises(Refused):
                session.workflows.audit_entries(since="2026-10-28T10:00:00", until="2026-10-28T09:00:00Z")
            assert len([r for r in server.requests if r["method"] == "audit.query"]) == before
        finally:
            session.close()


def test_audit_cli_is_json_filtered_and_scrubs_secret_material(config, opener, capsys, server):
    _seed(server)
    cli = build_cli(opener=opener)
    code = cli.run(argv=["audit", "--config", str(config), "--service", "MIDDLEWARE",
                         "--since", "2026-10-27T00:00:00Z", "--until", "2026-10-29T00:00:00Z",
                         "--user", "operator", "--event", "USER_LOGIN", "--limit", "1", "--offset", "0", "--json"])
    output = capsys.readouterr().out
    result = json.loads(output)
    assert code == 0 and result[0]["event"] == "USER_LOGIN"
    assert "synthetic-audit-secret" not in output and "synthetic-audit-token" not in output


def test_audit_cli_json_normalizes_native_datetime_values(config, opener, capsys):
    native_timestamp = datetime(2026, 10, 29, 8, 17, 42, tzinfo=timezone.utc)

    def opener_with_native_datetime(profile, **kwargs):
        session = opener(profile, **kwargs)
        rpc_call = session.rpc.call

        def call(method, *params, **call_kwargs):
            result = rpc_call(method, *params, **call_kwargs)
            if method == "audit.query" and result:
                result[0]["timestamp"] = native_timestamp
            return result

        session.rpc.call = call
        return session

    cli = build_cli(opener=opener_with_native_datetime)
    code = cli.run(argv=["audit", "--config", str(config), "--json"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert result[0]["timestamp"] == "2026-10-29T08:17:42+00:00"


def test_audit_tui_filters_and_displays_only_scrubbed_events():
    with FakeServer() as server:
        _seed(server)
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                tree = app.query_one("#nav", Tree)
                target = None

                def find(node):
                    nonlocal target
                    for child in node.children:
                        if child.data and child.data.key == "audit":
                            target = child
                            return
                        find(child)

                find(tree.root)
                assert target is not None
                tree.select_node(target)
                for _ in range(60):
                    await pilot.pause(0.05)
                    note = str(app.query_one("#audit-note", Static).render())
                    if "audit event(s)" in note:
                        break
                table = app.query_one("#audit-table", DataTable)
                assert table.row_count == 2
                details = str(table.get_cell_at((0, 5)))
                assert "synthetic-audit-secret" not in details and "synthetic-audit-token" not in details
                app.query_one("#audit-event", Input).value = "USER_LOGIN"
                await pilot.click("#audit-refresh")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if table.row_count == 1:
                        break
                assert table.row_count == 1
                assert "audit.query" in server.called()

                from tnt.tui.audit import AuditView

                view = app.query_one("#view-audit", AuditView)
                view._loaded([{"timestamp": "t", "username": "u", "event": "e", "service": "MIDDLEWARE",
                               "success": True, "event_data": {}} for _ in range(100)], 100)
                refreshed_offsets = []
                view.action_refresh = lambda: refreshed_offsets.append(view.page_offset)
                view.on_button_pressed(Button.Pressed(view.query_one("#audit-previous", Button)))
                assert view.page_offset == 0 and refreshed_offsets[-1] == 0
                view.on_button_pressed(Button.Pressed(view.query_one("#audit-next", Button)))
                assert view.page_offset == 100 and refreshed_offsets[-1] == 100
                view.on_button_pressed(Button.Pressed(view.query_one("#audit-refresh", Button)))
                assert view.page_offset == 0 and refreshed_offsets[-1] == 0

        try:
            asyncio.run(go())
        finally:
            session.close()
