"""Remaining collection and UI boundary contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import fixture_frame, fixture_root
from topos.collect.cgroup import collect_cgroup
from topos.collect.host import _swap_devices
from topos.config import ToposConfig
from topos.model import Entity
from topos.ui.app import ToposApp


def _write(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def test_collect_cgroup_keeps_present_optional_counters_without_inventing_absent_ones(
    tmp_path: Path,
) -> None:
    """Kernel files may expose only a subset of the documented counters."""
    _write(tmp_path / "memory.events", "low 2\n")
    _write(tmp_path / "cpu.stat", "usage_usec 9\n")

    sample = collect_cgroup(tmp_path, "", Entity(key="", kind="root", parent=None))

    assert sample.raw_counters["memory.events:low"] == 2
    assert "memory.events:high" not in sample.raw_counters
    assert sample.raw_counters["cpu.stat:usage_usec"] == 9
    assert "cpu.stat:throttled_usec" not in sample.raw_counters


def test_swap_parser_skips_truncated_proc_row(tmp_path: Path) -> None:
    """A /proc/swaps row lacking usage columns is not a device measurement."""
    _write(tmp_path / "swaps", "Filename Type Size Used Priority\n/swapfile file\n")

    assert _swap_devices(tmp_path) == ([], "host")


def test_tree_actions_leave_root_and_uncollapsed_selection_stable() -> None:
    """Tree navigation cannot collapse above root or expand a visible node."""
    async def run() -> None:
        app = ToposApp(
            iter([fixture_frame()]),
            config=ToposConfig(default_view="tree", default_column_profile="auto"),
            cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
            proc_root=fixture_root() / "procfs" / "network",
        )
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(20):
                await pilot.pause()
                if app.frames_received:
                    break
            assert app.selected_key == ""

            app.action_expand_tree()
            assert "" not in app._collapsed_tree_keys

            app._collapsed_tree_keys.add("")
            app.action_collapse_tree()
            assert app.selected_key == ""
            assert "" in app._collapsed_tree_keys

    asyncio.run(run())
