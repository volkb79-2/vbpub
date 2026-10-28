import asyncio

import pytest
from textual.widgets import DataTable, Static, Tree

from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp
from tnt.tui.disks import DisksView


@pytest.fixture
def session(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    yield s
    s.close()


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
            select(app, "Datasets")
            await pilot.pause(0.2)
            text = str(app.query_one("#view-planned", Static).render())
            assert "Datasets: planned, milestone M1" in text and "A-01" in text
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert "Go to Storage > Disks" in titles and "Go to System > Network" in titles and "Go to Dashboard" in titles
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
