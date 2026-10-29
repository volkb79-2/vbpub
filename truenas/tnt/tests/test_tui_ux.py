"""Pilot-based text review harness for the real tnt screens at representative terminal sizes.

Rendered artifacts are plain UTF-8 text under pytest's temporary directory, outside the repository.
"""
from __future__ import annotations

import asyncio

import pytest
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static, Tree

from tnt.actions import Action, Kind, Plan, RpcCall
from tnt.menu import MenuItem, walk
from tnt.profile import Profile
from tnt.session import open_session
from tnt.tui.app import KeysHelp, TntApp, VIEWS
from tnt.tui.workflows import PlanReview


SIZES = ((80, 24), (100, 30), (120, 40), (160, 50), (200, 60), (60, 40))
MENU_ITEMS = {item.key: (path, item) for path, item in walk()}
MAJOR_KEYS = tuple(VIEWS) + ("reboot", "shutdown")


@pytest.fixture
def ux_session(server):
    session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    try:
        yield session
    finally:
        session.close()


def _screen_text(app) -> str:
    """Read the actual composed terminal cells as text, without writing an image."""
    strips = app.screen._compositor.render_strips()
    return "\n".join(strip.text.rstrip() for strip in strips)


async def _wait_for_screen(pilot, app, *, limit: int = 120) -> str:
    for _ in range(limit):
        await pilot.pause(0.025)
        rendered = _screen_text(app)
        if not any(marker in rendered for marker in (
            "Loading ", "refreshing in background", "Checking update status", "Testing ",
        )):
            return rendered
    return _screen_text(app)


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
def test_real_major_screens_export_text_at_supported_sizes(size, ux_session, tmp_path):
    async def go():
        cols, rows = size
        scratch = tmp_path / f"{cols}x{rows}"
        scratch.mkdir()
        app = TntApp(ux_session)
        long_host = "qa-archive-host-eu-west-production"
        app.session_controller.hosts[long_host] = Profile(
            name=long_host,
            url="wss://archive.example.invalid/api/current",
            username="tnt",
            description="Long description for a replicated storage system",
        )
        app.session_controller.last_status[long_host] = "connected"
        async with app.run_test(size=size) as pilot:
            assert app.query_one("#nav", Tree).has_focus
            for key in MAJOR_KEYS:
                path, item = MENU_ITEMS[key]
                app.show(item)
                await pilot.pause(0.025)
                rendered = await _wait_for_screen(pilot, app)
                (scratch / f"{key.replace('/', '-')}.txt").write_text(rendered + "\n", encoding="utf-8")

                assert "Location: TrueNAS" in rendered
                assert path[-1].split()[-1] in rendered
                assert "Jump" in rendered
                assert "read-only" in rendered
                assert len(rendered.splitlines()) <= rows
                if size == (200, 60):
                    assert "conditional: Containers" in rendered
                    assert "conditional: Virtual Machines" in rendered

            app.action_select_host()
            await pilot.pause(0.05)
            hosts_table = app.query_one("#hosts-table", DataTable)
            assert hosts_table.has_focus
            hosts_before_scroll = _screen_text(app)
            (scratch / "hosts-first-columns.txt").write_text(hosts_before_scroll + "\n", encoding="utf-8")
            last_status_visible_before_scroll = "Last status" in hosts_before_scroll
            if size == (200, 60):
                assert last_status_visible_before_scroll
            assert "Select a TrueNAS host" in hosts_before_scroll
            assert "qa-archive-host" in hosts_before_scroll
            for action in ("Switch to selected", "Test", "Add", "Edit", "Set default", "Remove", "Cancel"):
                assert action in hosts_before_scroll
            hosts_table.focus()
            await pilot.press("end")
            await pilot.pause(0.05)
            hosts_after_scroll = _screen_text(app)
            (scratch / "hosts-last-column.txt").write_text(hosts_after_scroll + "\n", encoding="utf-8")
            assert "Last status" in hosts_after_scroll
            if not last_status_visible_before_scroll:
                assert hosts_after_scroll != hosts_before_scroll

            # A narrow terminal keeps the key legend and long breadcrumb legible.
            if size == (80, 24):
                rendered = _screen_text(app)
                assert "Ctrl+P Jump" in rendered and "? Help" in rendered and "Q Quit" in rendered
                assert "Location: TrueNAS" in rendered

    asyncio.run(go())


def test_tui_resize_to_too_small_and_back_keeps_the_open_screen(ux_session, tmp_path):
    async def go():
        app = TntApp(ux_session)
        async with app.run_test(size=(100, 30)) as pilot:
            app.show(MENU_ITEMS["dashboard"][1])
            await _wait_for_screen(pilot, app)

            for size in ((40, 10), (60, 10)):
                await pilot.resize_terminal(*size)
                await pilot.pause(0.1)
                rendered = _screen_text(app)
                (tmp_path / f"resize-{size[0]}x{size[1]}.txt").write_text(rendered + "\n", encoding="utf-8")
                assert "Terminal too small" in rendered

            await pilot.resize_terminal(100, 30)
            rendered = await _wait_for_screen(pilot, app)
            (tmp_path / "resize-100x30.txt").write_text(rendered + "\n", encoding="utf-8")
            assert "Terminal too small" not in rendered
            assert "Location: TrueNAS > Dashboard" in rendered

    asyncio.run(go())


def test_tui_help_palette_escape_back_and_host_picker(ux_session, tmp_path):
    async def go():
        app = TntApp(ux_session)
        async with app.run_test(size=(80, 24)) as pilot:
            app.show(MENU_ITEMS["dashboard"][1])
            await _wait_for_screen(pilot, app)
            assert app.query_one("#nav", Tree).has_focus

            app.show(MENU_ITEMS["datasets"][1])
            await _wait_for_screen(pilot, app)
            await pilot.click("#nav", offset=(4, 0))
            await pilot.pause(0.05)
            assert app.current == "dashboard"

            await pilot.press("?")
            await pilot.pause(0.05)
            assert isinstance(app.screen, KeysHelp)
            help_text = _screen_text(app)
            assert "Ctrl+P opens Jump" in help_text
            assert "cancels the open dialog" in help_text
            assert "Close help" in help_text
            (tmp_path / "help-80x24.txt").write_text(help_text + "\n", encoding="utf-8")
            await pilot.click(app.screen.query_one("#keys-help-close", Button))
            await pilot.pause(0.05)
            assert not isinstance(app.screen, KeysHelp)

            await pilot.press("?")
            await pilot.pause(0.05)
            assert isinstance(app.screen, KeysHelp)
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, KeysHelp)

            await pilot.press("ctrl+p")
            await pilot.pause(0.05)
            assert "CommandPalette" in type(app.screen).__name__
            await pilot.press("escape")

            app.action_select_host()
            await pilot.pause(0.05)
            hosts_table = app.query_one("#hosts-table", DataTable)
            hosts_table.focus()
            await pilot.press("end")
            await pilot.pause(0.05)
            host_text = _screen_text(app)
            (tmp_path / "hosts-80x24.txt").write_text(host_text + "\n", encoding="utf-8")
            assert "Location: TrueNAS > Hosts" in host_text
            assert "Select a TrueNAS host" in host_text
            assert "Credentials and SSH key paths stay hidden" in host_text
            assert "Last status" in host_text
            for action in ("Switch to selected", "Test", "Add", "Edit", "Set default", "Remove", "Cancel"):
                assert action in host_text
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert app.current == "dashboard"

            app.action_select_host()
            await pilot.pause(0.05)
            await pilot.click("#hosts-cancel")
            await pilot.pause(0.05)
            assert app.current == "dashboard"

            await pilot.press("ctrl+n")
            assert app.focused is app.query_one("#nav", Tree)
            await pilot.press("tab")
            assert app.focused is app.query_one("#core-search", Input)
            await pilot.press("escape")
            assert app.focused is app.query_one("#nav", Tree)

            app.show(MenuItem("ux-placeholder", "UX Placeholder", status="planned"))
            await pilot.pause(0.05)
            assert "Location: TrueNAS > UX Placeholder" in _screen_text(app)

            class FallbackModal(ModalScreen[None]):
                def compose(self):
                    yield Static("Fallback dismiss path")

            modal = FallbackModal()
            app.push_screen(modal)
            await pilot.pause(0.05)
            app.action_back()
            await pilot.pause(0.05)
            assert app.screen is not modal

    asyncio.run(go())


def test_empty_error_and_job_progress_states_are_textual(server, tmp_path):
    async def go():
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            server.active_jobs = [{
                "id": 73, "method": "replication.run", "state": "RUNNING",
                "progress": {"percent": 42, "description": "Waiting for target"},
                "logs_excerpt": "Progress is being reported", "error": None,
            }]
            app = TntApp(session)
            async with app.run_test(size=(100, 30)) as pilot:
                app.show(MENU_ITEMS["alerts"][1])
                empty_text = await _wait_for_screen(pilot, app)
                assert "No rows are available." in empty_text
                assert "0 visible row(s)" in app.query_one("#core-status", Static).render().plain

                app.query_one("#core-search", Input).value = "no matching alert"
                await pilot.pause(0.05)
                filtered = _screen_text(app)
                assert "No rows match this filter." in filtered

                server.fail["alert.list"] = "synthetic alert read failure"
                app.action_refresh()
                for _ in range(120):
                    await pilot.pause(0.025)
                    failed = _screen_text(app)
                    if "Unavailable:" in failed:
                        break
                (tmp_path / "alert-error-100x30.txt").write_text(failed + "\n", encoding="utf-8")
                assert "Unavailable:" in failed
                assert "synthetic alert read failure" in failed

                server.fail.pop("alert.list")
                app.show(MENU_ITEMS["jobs"][1])
                for _ in range(120):
                    await pilot.pause(0.025)
                    status = app.query_one("#core-status", Static).render().plain
                    if "remote row(s)" in status:
                        break
                table = app.query_one("#core-table", DataTable)
                table.focus()
                await pilot.press("end")
                await pilot.pause(0.05)
                jobs = await _wait_for_screen(pilot, app)
                (tmp_path / "jobs-progress-100x30.txt").write_text(jobs + "\n", encoding="utf-8")
                assert table.get_cell_at((0, 3)) == "42% Waiting for target"
                assert "42% Waiting for target" in jobs
        finally:
            session.close()

    asyncio.run(go())


def test_long_destructive_confirmation_target_is_visible_at_80_columns(ux_session, tmp_path):
    async def go():
        target = "tank/archive-2026/archive-quarterly/final-dataset"
        action = Action(
            "UX-REVIEW", Kind.DESTRUCTIVE, "Delete dataset",
            (RpcCall("container.delete", (73, {})),),
            (RpcCall("container.query", ([], {})),), target,
        )
        plan = Plan(action, "ux-layout-fingerprint")
        app = TntApp(ux_session)
        async with app.run_test(size=(80, 24)) as pilot:
            app.show(MENU_ITEMS["dashboard"][1])
            await _wait_for_screen(pilot, app)
            app.push_screen(PlanReview(plan, ux_session))
            await pilot.pause(0.1)
            rendered = _screen_text(app)
            (tmp_path / "confirmation-80x24.txt").write_text(rendered + "\n", encoding="utf-8")
            assert "Type this exact target to confirm:" in rendered
            assert target in rendered
            assert app.screen.query_one("#plan-confirm", Input).placeholder == "Type confirmation text exactly"
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, ModalScreen)

    asyncio.run(go())
