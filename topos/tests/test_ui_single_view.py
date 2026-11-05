"""The single view: column config, priority planning, noise levels, side pane, honest cells.

Layout assertions use the plan and mounted geometry, never timing or host load.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from rich.text import Text

from topos.config import ToposConfig, UiConfig, load
from topos.model import Entity, EntityFrame, Finding, Frame, MetricValue
from topos.ui import columns as cols
from topos.ui.app import ToposApp
from topos.ui.data_table import MouseTable
from topos.ui.overlay import ProcRow

SRC = Path(__file__).resolve().parents[1] / "src" / "topos"


def _ef(key: str, parent: str | None, kind: str = "slice", findings=None, governance=None, **metrics) -> EntityFrame:
    values = {name: MetricValue(v, "unlimited" if v == "unlimited" else "exact") for name, v in metrics.items()}
    values = {k: (MetricValue(None, "unlimited") if m.src == "unlimited" else m) for k, m in values.items()}
    return EntityFrame(entity=Entity(key, kind, parent), metrics=values, findings=findings or [], governance=governance)


def _frame() -> Frame:
    drift_gov = {
        "summary": {"origin": "systemd_unit", "drift": True, "drifted_limits": ["mem_high"], "unit": "a.slice"},
        "limits": {
            "mem_high": {"live_value": 2e9, "live_src": "exact", "origin": "systemd_unit", "recorded_value": 1e9,
                         "recorded_origin": "systemd_unit", "drift": True, "reason": "live differs from the unit"},
        },
        "effective_memory_min": {"reason": "no ancestor clamps it", "path": []},
    }
    ents = [
        _ef("", None, "root", cpu_pct=120.0, ram=4e9, pids_current=50.0),
        _ef("a.slice", "", cpu_pct=60.0, ram=2e9, mem_high=2e9, mem_max="unlimited", pids_current=9.0, governance=drift_gov,
            psi_mem_some_avg10=1.0, io_r_bps=3e6),
        _ef("a.slice/svc.service", "a.slice", "service", cpu_pct=2.0, ram=5e8, pids_current=3.0),
        _ef("hot.slice", "", cpu_pct=80.0, ram=1e9, pids_current=2.0, psi_io_some_avg10=12.0),
        _ef("idle.slice", "", cpu_pct=0.0, ram=1e6, pids_current=0.0, mem_high="unlimited"),
        _ef("quiet.slice", "", cpu_pct=0.0, ram=2e6, pids_current=0.0),
    ]
    host = {"host_mem_total": MetricValue(8e9, "host"), "host_mem_available": MetricValue(4e9, "host")}
    return Frame(1, 100.0, 1.0, host, {e.entity.key: e for e in ents}, {"block_devices": [], "net_devices": []})


def _app(**kwargs) -> ToposApp:
    return ToposApp(iter([_frame()]), config=kwargs.pop("config", ToposConfig()), **kwargs)


async def _ready(app: ToposApp, pilot) -> None:
    for _ in range(50):
        await pilot.pause()
        if app.frames_received:
            break
    await pilot.pause()


def _ids(app: ToposApp) -> list[str]:
    return list(app.query_one("#body-table", MouseTable).column_keys)


# ── column file and planning ──────────────────────────────────────────


def test_shipped_file_parses_in_spec_order_and_name_first() -> None:
    columns = cols.load_columns(None)
    assert [c.id for c in columns][:3] == ["name", "cpu", "thr"]
    assert columns[0].id == "name"
    assert {c.id for c in columns if c.pri == 1} == {"name", "cpu", "ram_ceil", "blk"}


@pytest.mark.parametrize("width", [60, 80, 120, 170, 260])
def test_plan_keeps_file_order_and_wider_keeps_a_superset(width: int) -> None:
    columns = cols.load_columns(None)
    order = [c.id for c in columns]
    kept = [c.id for c in cols.plan(columns, width).columns]
    assert kept == [i for i in order if i in kept]
    wider = {c.id for c in cols.plan(columns, width + 20).columns}
    assert set(kept) <= wider
    assert kept[0] == "name"


def test_plan_never_overflows_and_drops_highest_priority_numbers_first() -> None:
    columns = cols.load_columns(None)
    plan = cols.plan(columns, 100)
    assert sum(plan.widths) + len(plan.widths) - 1 <= 100
    kept_pri = [c.pri for c in plan.columns]
    dropped_pri = [c.pri for c in columns if c not in plan.columns]
    assert max(kept_pri) <= min(dropped_pri)


def test_config_file_decides_order_and_priority(tmp_path: Path) -> None:
    custom = tmp_path / "cols.toml"
    custom.write_text(
        '[[column]]\nid = "name"\npri = 1\n[[column]]\nid = "io_r"\npri = 1\n[[column]]\nid = "cpu"\npri = 9\n'
        '[[column]]\nid = "ram_ceil"\npri = 2\n'
    )
    columns = cols.load_columns(custom)
    assert [c.id for c in cols.plan(columns, 200).columns] == ["name", "io_r", "cpu", "ram_ceil"]
    narrow = cols.plan(columns, cols.NAME_MIN + 1 + 7 + 1 + 17)
    assert [c.id for c in narrow.columns] == ["name", "io_r", "ram_ceil"]  # cpu has the highest number, it goes first


@pytest.mark.parametrize(
    "text",
    ['[[column]]\nid = "cpu"\npri = 1\n', '[[column]]\nid = "name"\npri = 1\n[[column]]\nid = "nope"\npri = 1\n',
     '[[column]]\nid = "name"\npri = 1\n[[column]]\nid = "name"\npri = 2\n', '[[column]]\nid = "name"\n'],
)
def test_bad_column_files_are_refused(text: str) -> None:
    with pytest.raises(cols.ColumnConfigError):
        cols.parse_columns(text)


def test_app_uses_the_configured_columns_file_and_survives_a_bad_one(tmp_path: Path) -> None:
    good = tmp_path / "good.toml"
    good.write_text('[[column]]\nid = "name"\npri = 1\n[[column]]\nid = "io_r"\npri = 1\n[[column]]\nid = "cpu"\npri = 1\n')
    bad = tmp_path / "bad.toml"
    bad.write_text("not toml [")

    async def run() -> None:
        app = _app(config=ToposConfig(ui=UiConfig(columns_file=good)))
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            assert _ids(app) == ["name", "io_r", "cpu"]
        app = _app(config=ToposConfig(ui=UiConfig(columns_file=bad)))
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            assert app.columns_warning.startswith("columns file ignored")
            assert _ids(app)[:2] == ["name", "cpu"]

    asyncio.run(run())


def test_removed_profile_keys_warn(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[general]\ndefault_column_profile = "triage"\n[columns]\nwidth_tier = 1\n')
    lines: list[str] = []
    load(path, warn=lines.append)
    text = "\n".join(lines)
    assert "default_column_profile was removed" in text
    assert "columns.width_tier was removed" in text
    assert "unknown key general.default_column_profile" not in text


# ── mounted geometry at the four reference sizes ──────────────────────


@pytest.mark.parametrize("size", [(80, 24), (120, 30), (170, 48), (260, 60)])
def test_mounted_order_survives_and_nothing_is_clipped(size) -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=size) as pilot:
            await _ready(app, pilot)
            table = app.query_one("#body-table", MouseTable)
            order = [c.id for c in app.columns]
            ids = _ids(app)
            assert ids == [i for i in order if i in ids]
            assert {"name", "cpu", "ram_ceil", "blk"} <= set(ids) or size[0] < 80
            assert table.virtual_size.width <= table.scrollable_content_region.width  # nothing cut at the right
            assert app._plan.dropped == app._plan.total - len(ids)

    asyncio.run(run())


def test_columns_are_a_superset_when_the_terminal_grows() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await _ready(app, pilot)
            narrow = _ids(app)
            await pilot.resize_terminal(139, 30)  # still no pane
            await pilot.pause()
            await pilot.pause()
            assert set(narrow) < set(_ids(app))

    asyncio.run(run())


# ── side pane ─────────────────────────────────────────────────────────


def test_pane_is_46_wide_at_any_width_and_takes_its_width_first() -> None:
    async def run() -> None:
        for width in (170, 260):
            app = _app()
            async with app.run_test(size=(width, 40)) as pilot:
                await _ready(app, pilot)
                pane = app.query_one("#pane")
                table = app.query_one("#body-table", MouseTable)
                assert pane.display and pane.region.width == 46
                assert table.region.width + pane.region.width == width
                assert table.virtual_size.width <= table.scrollable_content_region.width

    asyncio.run(run())


def test_pane_hides_below_140_and_tab_toggles_it_both_ways() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            assert not app.query_one("#pane").display
            before = _ids(app)
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            assert app.query_one("#pane").display
            assert len(_ids(app)) <= len(before)  # the pane takes width first, columns drop
            await pilot.press("tab")
            await pilot.pause()
            assert not app.query_one("#pane").display
        app = _app()
        async with app.run_test(size=(170, 40)) as pilot:
            await _ready(app, pilot)
            assert app.query_one("#pane").display
            await pilot.press("tab")
            await pilot.pause()
            assert not app.query_one("#pane").display

    asyncio.run(run())


def test_pane_width_is_configurable() -> None:
    async def run() -> None:
        app = _app(config=ToposConfig(ui=UiConfig(pane_width=60)))
        async with app.run_test(size=(200, 40)) as pilot:
            await _ready(app, pilot)
            assert app.query_one("#pane").region.width == 60

    asyncio.run(run())


def test_pane_follows_the_cursor_and_shows_config_chain_drift_and_findings() -> None:
    async def run() -> None:
        frame = _frame()
        frame.entities["a.slice"].findings.append(Finding("r1", "warn", "memory.high drifted", "restart the unit"))
        app = ToposApp(iter([frame]), config=ToposConfig())
        async with app.run_test(size=(170, 40)) as pilot:
            await _ready(app, pilot)
            body = app.query_one("#pane-body")
            assert "host root" in str(body.render())
            app.query_one("#body-table", MouseTable).update_cursor_from_key("a.slice")
            await pilot.pause()
            text = str(body.render())
            assert "a.slice" in text and "CONFIG CHAIN" in text
            assert "mem_high" in text and "<-" in text and "DRIFT" in text
            assert "memory.high drifted" in text and "fix: restart the unit" in text

    asyncio.run(run())


# ── noise ─────────────────────────────────────────────────────────────


def test_noise_key_cycles_all_middle_hot_with_a_hidden_count() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(170, 40)) as pilot:
            await _ready(app, pilot)
            seen = {}
            for _ in range(3):
                seen[app.noise] = (len(app._real_row_keys()), app._rowset.hidden)
                await pilot.press("a")
                await pilot.pause()
            assert set(seen) == {"all", "middle", "hot"} and app.noise == "middle"
            assert seen["all"][1] == 0
            assert seen["all"][0] > seen["middle"][0] > seen["hot"][0]
            assert seen["middle"][1] > 0 and seen["hot"][1] > seen["middle"][1]
            assert "quiet.slice" not in app._visible_row_keys and "hot.slice" in app._visible_row_keys
            assert "noise:middle (" in str(app.query_one("#info").render())

    asyncio.run(run())


# ── honest cells ──────────────────────────────────────────────────────


def test_unset_na_and_zero_are_three_different_cells() -> None:
    ef = _ef("x", "", mem_high="unlimited", mem_max=0.0, pids_current=0.0, io_r_bps=0.0)
    ctx = cols.CellCtx(ef)
    assert cols.REGISTRY["mem_high"].cell(ctx)[0] == "·"  # unlimited: not set
    assert cols.REGISTRY["mem_max"].cell(ctx)[0] == "·"  # a configured 0 is not a limit either
    assert cols.REGISTRY["mem_low"].cell(ctx)[0] == "n/a"  # no value in the frame
    assert cols.REGISTRY["io_r"].cell(ctx)[0] == "0"  # a real zero
    assert cols.REGISTRY["io_w"].cell(ctx)[0] == "n/a"
    assert cols.REGISTRY["blk"].cell(ctx)[0] == "n/a"  # no process source in the real app
    assert cols.REGISTRY["blk"].cell(cols.CellCtx(ef, blk=2))[0] == "2"


def test_threshold_bands_colour_the_cells() -> None:
    low = cols.REGISTRY["cpu"].cell(cols.CellCtx(_ef("x", "", cpu_pct=5.0)))
    high = cols.REGISTRY["cpu"].cell(cols.CellCtx(_ef("x", "", cpu_pct=200.0)))
    assert low[1] != high[1] and "red" in high[1]


def test_ram_ceil_gauge_and_unset_ceiling() -> None:
    gauged = cols.REGISTRY["ram_ceil"].cell(cols.CellCtx(_ef("x", "", ram=1e9, mem_high=2e9), width=12))[0]
    assert gauged.startswith(" 954M/1.9G") and len(gauged) == 12
    free = cols.REGISTRY["ram_ceil"].cell(cols.CellCtx(_ef("x", "", ram=1e9, mem_high="unlimited"), width=12))[0]
    assert "/·" in free
    assert cols.REGISTRY["ram_ceil"].cell(cols.CellCtx(_ef("x", "")))[0] == "n/a"


def test_meters_print_na_for_absent_data_and_overlay_values_carry_their_label() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(170, 48)) as pilot:
            await _ready(app, pilot)
            meters = str(app.query_one("#meters").render())
            assert "per-CPU n/a" in meters and "run n/a" in meters and "demo" not in meters

        class Overlay:
            label = "demo"

            def per_cpu(self, frame):
                return [(0.5, 0.1, 0.0, 0.0)] * 2

            def processes(self, frame):
                return {"hot.slice": [ProcRow(7, "stress", 50.0, "D")]}

            def counts(self, frame):
                return (3, 1)

        app = _app(overlay=Overlay())
        async with app.run_test(size=(170, 48)) as pilot:
            await _ready(app, pilot)
            meters = str(app.query_one("#meters").render())
            assert "per-CPU demo(2)" in meters and "D-state 1 demo" in meters
            table = app.query_one("#body-table", MouseTable)
            assert any(k.endswith("#7") for k in table.row_keys)
            blk = table.get_cell("hot.slice", "blk")
            assert (blk[0] if isinstance(blk, tuple) else blk.plain) == "1"

    asyncio.run(run())


# ── keys ──────────────────────────────────────────────────────────────


def test_freeze_and_step_work_live() -> None:
    async def run() -> None:
        later = Frame(1, 101.0, 1.0, _frame().host, _frame().entities, {})
        app = ToposApp(iter([_frame(), later]), config=ToposConfig())
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(100):
                await pilot.pause()
                if app.frames_received == 2:
                    break
            assert app.current_frame.ts == 101.0
            await pilot.press("comma")
            await pilot.pause()
            assert app._frozen and app.current_frame.ts == 100.0
            await pilot.press("full_stop")
            await pilot.pause()
            assert app.current_frame.ts == 101.0
            await pilot.press("space")
            await pilot.pause()
            assert not app._frozen

    asyncio.run(run())


def test_header_click_sorts_and_sort_key_cycles_visible_columns() -> None:
    async def run() -> None:
        app = _app()
        async with app.run_test(size=(170, 40)) as pilot:
            await _ready(app, pilot)
            await pilot.press("s")
            await pilot.pause()
            assert app.sort_by == _ids(app)[2]  # cpu -> the next sortable column in the plan
            await pilot.press("S")
            await pilot.pause()
            assert app.sort_reverse is False

    asyncio.run(run())


# ── fabricated data is not reachable from topos ───────────────────────


def test_nothing_under_src_imports_the_demo_generator() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            if any("singleview_demo" in n or n.split(".")[0] == "tools" or "poc" in n.split(".") for n in names):
                offenders.append(f"{path.relative_to(SRC)}: {names}")
    assert offenders == []
    assert "Overlay" in (SRC / "ui" / "overlay.py").read_text() and not any(
        "DemoOverlay" in p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
    )


def test_text_helpers_are_importable() -> None:
    assert isinstance(Text("x"), Text)
