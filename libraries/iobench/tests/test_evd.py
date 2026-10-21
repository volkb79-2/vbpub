"""IOB-EVD: replay of recorded evidence through the shipped rules, and the mutation sanity of those replays."""
import json
import pathlib

import pytest

from iobench import cache, derive, ladder, series

GIB = 1 << 30
MIB = 1 << 20
FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


# -- replays (each returns nothing and asserts values that depend on a rule constant) ---------------------------------
def replay_gstammtisch():
    d = load("gstammtisch_tables.json")
    t_eff = derive.t_eff(20, 8 * GIB)
    dirty, flags = derive.r1(W_s=2.0, R_drain_bps=d["drain_bps"], inflight_bytes=d["inflight_bytes_q16_r64"], T_eff_bytes=t_eff)
    assert dirty == 16 * MIB and flags == []
    assert derive.r1b(dirty_bytes=dirty) == 4 * MIB
    assert derive.r2(qd90_write=8, current_nr_requests=256) == d["expected"]["nr_requests"]
    p99 = {kb: v[1] * 1000.0 for kb, v in ((128, d["probe_p99_ms_4_4"]["d16 q16 r128"]),)}
    sectors, sflags = derive.r3(R_drain_bps=d["drain_bps"], max_hw_kb=4096, F=0.25, P_us=250_000.0,
                                read_ratio=d["read_ratio_by_sectors_kb"], probe_p99_us={"128": p99[128]})
    assert (sectors, sflags) == (64, ["read_floor_violated"])
    qos_max, _ = derive.qos_max(best_read_rung=d["qos_example"]["best_read_over_R"] * 9800, R=9800, best_w=d["qos_example"]["write_best_bps"],
                                worst_w=d["qos_example"]["write_worst_bps"])
    assert qos_max == d["expected"]["qos_max"]


def replay_r1002():
    # the drain is sequential ~1.3 GB/s: the budget exceeds the kernel's threshold -> leave the kernel value
    dirty, _ = derive.r1(W_s=2.0, R_drain_bps=1.3e9, inflight_bytes=64 * 1280 * 1024, T_eff_bytes=derive.t_eff(20, 8 * GIB))
    assert dirty == "kernel" and derive.r1b(dirty_bytes=dirty) == "kernel"
    assert derive.r3(R_drain_bps=1.3e9, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio={}, probe_p99_us={})[0] == "kernel"


def replay_recorded_cache():
    doc = load("r1002_recorded.json")
    rungs = [{"ws_bytes": r["ws_gib"] * GIB, "steady_iops": float(r["steady_iops"])} for r in doc["rungs_le_0_85_usable"]]
    cliff, ref, classed = ladder.classify_rungs(rungs)
    assert 250 * GIB <= cliff <= 300 * GIB
    post = [(r["ws_bytes"], r["steady_iops"]) for r in classed if r["class"] == "post_cliff"]
    assert cache.e1_fit(post, ref).fit == "insufficient"
    state = cache.classify_cache(None, None, cliff_bytes=cliff, largest_rung=300 * GIB)
    assert state.state == "undetermined" and "cache_residual_possible" in state.reasons
    allr = [{"ws_bytes": r["ws_gib"] * GIB, "steady_iops": float(r["steady_iops"])} for r in doc["rungs_le_0_85_usable"][2:] + doc["rungs_beyond"]]
    assert 3.5 <= ladder.cached_tier_factor(allr) <= 4.0
    facts = doc["facts"]
    assert facts["fresh_first_touch_randwrite_iops"] == 3596 and 3.5 < facts["rewrite_randwrite_iops"] / facts["fresh_first_touch_randwrite_iops"]
    assert ladder.rearm_verdict is not None
    flat = series.reduce_series([(float(t), facts["seq_read_mib_s_flat"] * MIB / MIB) for t in range(1, 121)])
    assert flat.rate_limited is True


def replay_model():
    m = load("r1002_model.json")
    c, f, r = m["capacity_gib"], m["cached_iops"], m["backing_iops"]
    at300 = 1 / ((c / 300) / f + (1 - c / 300) / r)
    assert at300 == pytest.approx(46315, rel=0.01)          # reproduces the recorded 300 GiB point (46440 vs 46315)
    mx, _ = derive.qos_max(best_read_rung=m["best_read_rung"], R=r, best_w=m["write_best_bps"], worst_w=m["write_worst_bps"])
    assert mx == m["expected_qos_max"] == 1035


REPLAYS = {"gstammtisch": replay_gstammtisch, "r1002": replay_r1002, "recorded_cache": replay_recorded_cache, "model": replay_model}


def test_evd_01_gstammtisch_replay():
    replay_gstammtisch()


def test_evd_01_r1002_replay():
    replay_r1002()
    replay_recorded_cache()
    replay_model()


def test_evd_01_facts_are_asserted_with_provenance():
    for name in ("r1002_recorded.json", "gstammtisch_tables.json"):
        assert len(load(name)["_provenance"]["recorded"]) == 64
    for name in ("r1002_model.json", "two_tier_e1_good_but_wrong.json"):
        assert load(name)["_provenance"]["synthetic"] is True and load(name)["_provenance"]["generator"]
    secret_words = ("password", "token", "secret", "BEGIN ", "ssh-")
    for path in FIX.glob("*.json"):
        assert not any(w in path.read_text().lower() for w in (x.lower() for x in secret_words)), path.name


# one constant per rule, the replay that must notice it
MUTATIONS = [
    (derive, "T_REQ_S", 0.001, "r1002"), (derive, "T_REQ_S", 0.100, "gstammtisch"),
    (derive, "DIRTY_CEIL_SHARE", 0.001, "gstammtisch"),
    (derive, "NR_MIN", 32, "gstammtisch"), (derive, "NR_MAX", 8, "gstammtisch"),
    (derive, "SECTORS_MIN_KB", 128, "gstammtisch"), (derive, "QOS_MARGIN", 200, "model"),
    (ladder, "CLIFF_RATIO", 0.1, "recorded_cache"), (ladder, "CLIFF_RATIO", 0.45, "recorded_cache"),
    (series, "RATE_LIMITED_CV", 0.0, "recorded_cache"),
]


@pytest.mark.parametrize("module,name,value,replay", MUTATIONS, ids=[f"{m[0].__name__.split('.')[-1]}.{m[1]}={m[2]}" for m in MUTATIONS])
def test_evd_02_mutation_sanity(monkeypatch, module, name, value, replay):
    REPLAYS[replay]()                       # green on the shipped constant
    monkeypatch.setattr(module, name, value)
    with pytest.raises(Exception):          # an AssertionError from the replay (or the rule refusing): it noticed
        REPLAYS[replay]()
