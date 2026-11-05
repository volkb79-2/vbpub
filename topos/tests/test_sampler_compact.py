"""The single view asks the sampler for exactly its fields in compact form and renders what comes back.

Compact (docs/SAMPLER-PROTOCOL.md, ``current`` shaping): no ``raw``, a gauge is a bare number, a counter
``[delta, rate]``, ``unavailable`` states stay objects, ``not_applicable`` is left out. An absent metric is
``n/a`` (or ``·`` for an unset limit), never a number.
"""
from __future__ import annotations

import pytest
from sampler_fake import FakeSampler, build_current, shape_current, un

from topos.sampler_adapter import VIEW_FIELDS, convert_current
from topos.sampler_client import SamplerClient
from topos.sampler_source import SamplerSource
from topos.ui.columns import CellCtx, cn
from topos.ui.columns import REGISTRY as COLUMNS


def _compact(**over):
    return shape_current(build_current(**over), list(VIEW_FIELDS), True)


@pytest.fixture
def fake(tmp_path):
    server = FakeSampler(tmp_path / "s.sock").start()
    yield server
    server.stop()


def test_the_source_requests_view_fields_in_compact_form(fake) -> None:
    source = SamplerSource(SamplerClient(fake.path, timeout_s=1.0), poll_s=0.01)
    try:
        frame = next(iter(source._stream()))
    finally:
        source.stop_event.set()
    sent = [r for r in fake.requests if r["req"] == "current"][0]
    assert sent["compact"] is True and sent["fields"] == list(VIEW_FIELDS)
    assert "memstack.*" in sent["fields"] and "zswap.reject_*" in sent["fields"]
    assert frame.host["host_mem_total"].v == 16e9


def test_a_compact_reply_is_smaller_and_renders_the_same_values() -> None:
    full, compact = build_current(), _compact()
    assert len(repr(compact["entities"])) < len(repr(full["entities"]))
    a, b = convert_current(full), convert_current(compact)
    for key, ef in a.entities.items():
        for name, mv in ef.metrics.items():
            if name in ("pids_max",) or mv.v is None:
                continue
            assert b.entities[key].metrics[name].v == pytest.approx(mv.v), (key, name)
    assert b.host["host_swap_total"].v == a.host["host_swap_total"].v


def test_not_applicable_left_out_of_compact_renders_as_n_a_or_unset() -> None:
    frame = convert_current(_compact())
    db = frame.entities["system.slice/db.service"]
    assert "io_cost_usage_per_s" not in db.metrics and COLUMNS["iocost"].cell(CellCtx(db))[0] == "n/a"
    assert cn(frame.entities[""], "pids_max") == "na"
    assert COLUMNS["swap_max"].cell(CellCtx(db))[0] == "·"  # limit -1 is a bare number in compact: still unset
    x = frame.host_meta["sampler"]
    assert x["ksm"] == {"state": "off"} and x["iocost"] is None or x["iocost"]["state"] in ("off", "na")
    assert frame.host["host_io_cost_enabled"].v == 0
    devs = {d["name"]: d for d in frame.host_meta["block_devices"]}
    assert devs["vdb"]["await_ms"] == "idle" and devs["vda"]["await_ms"] == 4.5


def test_new_fields_give_real_values() -> None:
    frame = convert_current(_compact())
    root, db = frame.entities[""], frame.entities["system.slice/db.service"]
    assert root.metrics["ram"].v == 6e9  # root mem.current (MemTotal - MemAvailable)
    assert db.metrics["pids_max"].v == 4096
    assert db.metrics["swap_disk"].v == 5e8 - 3e8 - 1e7  # swap_current - zswapped - swapcached
    assert root.metrics["swap_disk"].v == 2e9 - 1.5e9 - 1e8  # root: host Zswapped and SwapCached
    z = frame.host_meta["sampler"]["zswap"]
    assert (z["in"], z["out"], z["wb"]) == (12.0, 7.0, 3.0)


def test_counter_reset_is_no_number_for_that_tick() -> None:
    over = {"cpu.usage_usec": {"s": "unavailable", "why": "counter_reset", "raw": 5}}
    cur = build_current()
    cur["entities"]["cg:/system.slice"].update(over)
    cur["entities"]["host"]["vm.zswpout"] = {"s": "unavailable", "why": "counter_reset", "raw": 9}
    for reply in (cur, shape_current(cur, list(VIEW_FIELDS), True)):
        frame = convert_current(reply)
        mv = frame.entities["system.slice"].metrics["cpu_pct"]
        assert mv.v is None  # never a spike, never 0
        assert COLUMNS["cpu"].cell(CellCtx(frame.entities["system.slice"]))[0] == "n/a"
        assert frame.host_meta["sampler"]["zswap"]["out"] is None


@pytest.mark.parametrize("state", ["read_timeout", "brand_new_state"])
def test_read_timeout_and_unknown_states_are_unavailable(state) -> None:
    cur = build_current()
    cur["entities"]["cg:/system.slice"]["mem.current"] = {"s": state, "why": "whatever"}
    cur["entities"]["cg:/system.slice"]["cpu.usage_usec"] = un("read_timeout")
    cur["entities"]["host"]["vm.zswpin"] = {"s": state}
    cur["procs"][0]["cmdline_state"] = {"s": "unavailable", "why": "read_timeout"}
    del cur["procs"][0]["cmdline"]
    frame = convert_current(shape_current(cur, list(VIEW_FIELDS), True))
    ef = frame.entities["system.slice"]
    assert ef.metrics["ram"].v is None and ef.metrics["cpu_pct"].v is None
    assert frame.host_meta["sampler"]["zswap"]["in"] is None
    assert frame.host_meta["sampler"]["procs"]["system.slice/db.service"][0]["pid"] == 100


def test_full_form_replies_still_work() -> None:
    """An older sampler ignores fields/compact and answers in full form."""
    frame = convert_current(build_current())
    assert frame.entities["system.slice"].metrics["ram"].v == 1e9
