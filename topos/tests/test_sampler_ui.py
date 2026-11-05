"""The single view fed by a sampler (fake protocol server): real keys through the pilot.

No assertion depends on elapsed time; waits are bounded polls for a condition.
"""
from __future__ import annotations

import asyncio

import pytest
from sampler_fake import T0_MS, FakeSampler, build_current
from textual.css.query import NoMatches

from topos.config import ToposConfig
from topos.sampler_adapter import convert_current
from topos.sampler_client import SamplerClient
from topos.sampler_source import SamplerSource
from topos.ui.app import ToposApp
from topos.ui.data_table import MouseTable, _plain


@pytest.fixture
def fake(tmp_path):
    server = FakeSampler(tmp_path / "s.sock").start()
    server.advance = True
    yield server
    server.stop()


def _app(fake, *, timeout_s: float = 1.0, **kw) -> ToposApp:
    source = SamplerSource(SamplerClient(fake.path, timeout_s=timeout_s), poll_s=0.25)
    return ToposApp(source, config=ToposConfig(), source_label="SAMPLER", overlay=source.overlay, **kw)


async def _until(pilot, cond, what: str, rounds: int = 400) -> None:
    for _ in range(rounds):
        await pilot.pause()
        if cond():
            await pilot.pause()
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"never happened: {what}")


def _text(app: ToposApp, widget: str) -> str:
    return str(app.query_one(widget).render())


def _cell(app: ToposApp, row: str, col: str) -> str:
    return _plain(app.query_one("#body-table", MouseTable).get_cell(row, col))


def _run(coro) -> None:
    asyncio.run(asyncio.wait_for(coro, 60))


def test_a_frame_or_failure_during_teardown_is_not_a_source_failure(fake) -> None:
    """The feed thread can deliver after the screen lost its widgets (the app is exiting)."""

    async def run() -> None:
        app = _app(fake)
        async with app.run_test(size=(120, 40)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 1, "a frame")
            frame = app.current_frame
            app.exit()  # the app is going away; its widgets are removed one at a time
            await app.query_one("#meters").remove()
            await app.query_one("#status").remove()
            app._apply_frame(frame)  # neither raises NoMatches
            app._on_source_failed("gone", "retrying")
            assert app._source_error == "gone (retrying)"

    _run(run())


def test_a_missing_widget_in_a_running_app_still_raises(fake) -> None:
    """The teardown guard must not hide a real bug such as a renamed widget id."""

    async def run() -> None:
        app = _app(fake)
        async with app.run_test(size=(120, 40)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 1, "a frame")
            app._stop_event.set()  # stop the feed so it cannot hit the missing widget on its own
            await app.query_one("#meters").remove()
            with pytest.raises(NoMatches):
                app._refresh_view()
            await app.query_one("#status").remove()
            with pytest.raises(NoMatches):
                app._refresh_status("x")

    _run(run())


@pytest.mark.parametrize("size", [(80, 24), (170, 48)])
def test_real_keys_show_real_sampler_data(fake, size) -> None:
    async def run() -> None:
        app = _app(fake)
        async with app.run_test(size=size) as pilot:
            await _until(pilot, lambda: app.frames_received >= 2, "two frames")
            for key in ("down", "down", "a", "a", "a", "v", "v", "v", "s", "tab", "tab", "space", "space"):
                await pilot.press(key)
            assert "mode=SAMPLER" in _text(app, "#status")
            table = app.query_one("#body-table", MouseTable)
            keys = set(table.row_keys)
            # process rows float under their cgroup: hot or blocked only, blocked first
            assert "system.slice/db.service#100" in keys and "system.slice/db.service#101" in keys
            assert "system.slice/db.service#102" not in keys  # 0.1% cpu, no IO: not hot
            assert _cell(app, "system.slice/db.service#100", "blk") == "D"
            # BLK counts every blocked task of the subtree: 1 own + 2 + 2
            assert _cell(app, "", "blk") == "5"
            assert _cell(app, "system.slice", "blk") == "4"
            assert _cell(app, "system.slice/db.service", "blk") == "2"
            assert _cell(app, "docker.slice", "blk") == "0"
            # unavailable stays n/a (first sample), a limit of max is not a number
            if "io_r" in table.column_keys:
                assert _cell(app, "system.slice/db.service", "io_r") == "n/a"
            assert _cell(app, "system.slice", "ram_ceil").startswith(" 954M/1.9G")

    _run(run())


def test_wide_screen_meters_carry_the_sampler_sections(fake) -> None:
    async def run() -> None:
        app = _app(fake)
        async with app.run_test(size=(170, 48)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 2, "two frames")
            meters = _text(app, "#meters")
            assert "per-CPU(4)" in meters and "n/a" in meters  # CPU 2 had no first sample
            assert "run 4  D-state 3" in meters
            assert "await 4.5ms" in meters and "await ·" in meters  # busy device vs a device with no IO
            assert "vda1" not in meters  # partitions are folded into the disk
            assert "12/7.0/3.0/n/a" in meters  # zswap in/out/wb/rej: the reject counters need debugfs, unreadable is n/a, not 0
            assert "IO.COST" in meters and "SAMPLER cpu" in meters and "over 2" in meters and "thr 4" in meters
            assert "■anon" in meters
            assert "━" not in meters
            # the sparkline was filled from `series` before the first live frame
            assert any(ch in meters for ch in "▂▃▄▅▆▇█")

    _run(run())


def test_truncated_namespaced_and_restart_are_said_plainly(fake) -> None:
    async def run() -> None:
        fake.truncated = True
        fake.mode = "namespaced"
        app = _app(fake)
        async with app.run_test(size=(170, 48)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 2, "two frames")
            notice = _text(app, "#notice")
            assert "NAMESPACED cgroup view" in notice and str(fake.path) in notice
            assert "TRUNCATED" in notice
            assert app.query_one("#notice").display
            fake.epoch = "e2"
            await _until(pilot, lambda: "sampler restarted" in _text(app, "#notice"), "restart notice")

    _run(run())


def test_a_recent_hard_event_takes_the_why_line(fake) -> None:
    async def run() -> None:
        fake.events = [{"ts_ms": T0_MS + 1500, "type": "oom_kill", "entity": "cg:/system.slice/db.service",
                        "detail": {"delta": 2, "total": 5}}]
        app = _app(fake)
        async with app.run_test(size=(170, 48)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 2, "two frames")
            assert app.query_one("#why").display
            assert "OOM kill in /system.slice/db.service (+2, total 5)" in _text(app, "#why")

    _run(run())


def test_a_dead_sampler_shows_stale_and_the_keys_still_work(tmp_path) -> None:
    async def run() -> None:
        source = SamplerSource(SamplerClient(tmp_path / "gone.sock", timeout_s=0.2))
        app = ToposApp(source, config=ToposConfig(), source_label="SAMPLER", overlay=source.overlay)
        async with app.run_test(size=(120, 30)) as pilot:
            await _until(pilot, lambda: app._source_error is not None, "source error")
            assert "source failed" in _text(app, "#status") and "retrying" in _text(app, "#status")
            await pilot.press("question_mark")
            assert len(app.screen_stack) > 1  # the help screen opened
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    _run(run())


def test_a_slow_sampler_does_not_freeze_the_ui(fake) -> None:
    async def run() -> None:
        fake.delay_s = 3.0  # every answer is far later than the client's timeout
        app = _app(fake, timeout_s=0.3)
        async with app.run_test(size=(120, 30)) as pilot:
            # while the worker thread waits on the socket the event loop keeps serving keys
            for _ in range(5):
                await pilot.press("question_mark")
                assert len(app.screen_stack) == 2
                await pilot.press("escape")
                assert len(app.screen_stack) == 1
            await _until(pilot, lambda: app._source_error is not None, "timeout surfaced")
            assert "timed out" in app._source_error or "no answer" in app._source_error
            assert app.frames_received == 0

    _run(run())


def test_a_fallback_to_local_is_labelled_with_its_reason() -> None:
    frame = convert_current(build_current())

    async def run() -> None:
        note = "sampler unavailable (cannot connect to /run/topos/sampler.sock: No such file or directory); showing local collection"
        app = ToposApp(iter([frame]), config=ToposConfig(), source_label="LOCAL", source_note=note)
        async with app.run_test(size=(120, 30)) as pilot:
            await _until(pilot, lambda: app.frames_received >= 1, "a frame")
            assert "mode=LOCAL" in _text(app, "#status")
            assert "showing local collection" in _text(app, "#notice")
            # a local source has no process or D-state source: BLK is n/a, never a zero
            assert _cell(app, "system.slice", "blk") == "n/a"

    _run(run())
