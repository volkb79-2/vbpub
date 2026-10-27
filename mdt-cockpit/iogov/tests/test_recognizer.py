"""Shift recognizer: property tests on synthetic regime changes (incl. a 20x collapse and recovery), hysteresis, determinism."""

from __future__ import annotations

import math
import random

import pytest

from iogov.config import Config
from iogov.recognizer import (Window, analyze_lane, classify_demand, extract, new_events, pattern_hint, recognize)

CFG = Config()
DAY = CFG.window_hours * 3600.0
LANE = "w_fresh_qd4"
BASE = 340e6  # B/s, the healthy rate used for synthetic series


def series(levels, noise=0.12, seed=0, lane=LANE):
    """windows with rate = level * 2**(gauss*noise): `levels` is a list of per-window capability."""

    rng = random.Random(seed)
    return {i: Window(i, 1, [lv * 2 ** rng.gauss(0, noise)], set()) for i, lv in enumerate(levels)}


def run(levels, noise=0.12, seed=0, cfg=CFG, lane=LANE):
    return analyze_lane(lane, series(levels, noise, seed), [], cfg, DAY)


def kinds(res):
    return [(e["kind"], e["evidence"]["window_start"]) for e in res.events]


def test_stationary_noise_never_alarms_across_many_seeds():
    for seed in range(300):
        res = run([BASE] * 60, noise=0.2, seed=seed)
        bad = [k for k in kinds(res) if k[0].startswith("shift")]
        assert not bad, (seed, bad)
        assert res.phase == "OK"


def test_single_outlier_window_is_dismissed_not_alarmed():
    for seed in range(100):
        levels = [BASE] * 30
        levels[17] = BASE / 8  # one bad day (neighbour, cache flush...)
        res = run(levels, noise=0.12, seed=seed)
        assert not [k for k in kinds(res) if k[0] == "shift_detected"], seed


def test_20x_collapse_is_recognised_within_the_confirm_delay_and_sized():
    for seed in range(50):
        levels = [BASE] * 10 + [BASE / 20] * 10
        res = run(levels, noise=0.15, seed=seed)
        det = [e for e in res.events if e["kind"] == "shift_detected"]
        assert len(det) == 1
        assert 10 <= det[0]["evidence"]["window_start"] <= 10 + CFG.confirm_windows - 1  # confirm_windows windows after the step
        assert det[0]["evidence"]["direction"] == "down"
        assert 12 < det[0]["evidence"]["factor"] < 32
        assert "capability shifted down" in det[0]["text"] and "baseline reset suggested" in det[0]["text"]
        assert res.phase == "ALARM_DOWN"  # frozen reference: still alarmed in the new regime until the operator resets


def test_recovery_after_collapse_clears_quickly_even_after_a_long_collapse():
    for seed in range(30):
        levels = [BASE] * 8 + [BASE / 20] * 40 + [BASE] * 6
        res = run(levels, noise=0.15, seed=seed)
        cleared = [e for e in res.events if e["kind"] == "shift_cleared"]
        assert len(cleared) == 1 and cleared[0]["evidence"]["reason"] == "recovered"
        assert cleared[0]["evidence"]["window_start"] <= 8 + 40 + CFG.clear_windows  # S is capped, a long collapse does not slow recovery
        assert res.phase == "OK"


def test_2x_and_4x_shifts_are_detected_with_the_documented_delay():
    # k=0.5, h=1.5, confirm=2: 2x drift needs S>=1.5 (3 windows) then one more confirming window
    res = run([BASE] * 10 + [BASE / 2] * 10, noise=0.0)
    det = [e for e in res.events if e["kind"] == "shift_detected"]
    assert det and det[0]["evidence"]["window_start"] == 10 + 3
    res = run([BASE] * 10 + [BASE / 4] * 10, noise=0.0)
    assert [e for e in res.events if e["kind"] == "shift_detected"][0]["evidence"]["window_start"] == 10 + 1


def test_gradual_ramp_is_caught_before_it_gets_to_20x():
    levels = [BASE] * 8 + [BASE * 2 ** (-0.25 * i) for i in range(1, 30)]
    res = run(levels, noise=0.05)
    det = [e for e in res.events if e["kind"] == "shift_detected"]
    assert det and det[0]["evidence"]["factor"] < 8


def test_improvement_is_reported_as_up_not_down():
    res = run([BASE / 20] * 8 + [BASE] * 8, noise=0.1, seed=3)
    det = [e for e in res.events if e["kind"] == "shift_detected"]
    assert det and det[0]["evidence"]["direction"] == "up"


def test_degrade_then_overshoot_flips_direction_and_closes_the_first_alarm():
    res = run([BASE] * 8 + [BASE / 20] * 6 + [BASE * 4] * 6, noise=0.05)
    ks = [e["kind"] for e in res.events]
    assert "shift_cleared" in ks and ks.count("shift_detected") == 2


def test_oscillation_is_bounded_by_hysteresis():
    levels = [BASE] * 8 + [BASE / 20 if (i // 2) % 2 == 0 else BASE for i in range(40)]
    res = run(levels, noise=0.1)
    assert len([e for e in res.events if e["kind"] == "shift_detected"]) <= 12  # at most one per cycle, never per window


def test_reference_is_frozen_while_alarmed_and_reset_only_by_operator_note():
    levels = [BASE] * 8 + [BASE / 20] * 12
    res = analyze_lane(LANE, series(levels, 0.05), [], CFG, DAY)
    assert res.reference == pytest.approx(BASE, rel=0.1)  # not dragged down by the degraded windows
    # operator baseline reset after the collapse: the new regime becomes the reference and the alarm closes
    res2 = analyze_lane(LANE, series(levels, 0.05), [(8 + 10) * DAY + 1], CFG, DAY)
    assert any(e["kind"] == "baseline_reset" for e in res2.events)
    assert res2.reference == pytest.approx(BASE / 20, rel=0.2)
    assert res2.phase == "OK"


def test_learning_period_and_insufficient_windows():
    res = run([BASE] * 3)
    assert res.phase.startswith("LEARNING") and not res.events
    wins = series([BASE] * 10)
    wins[5].n = 0
    res = analyze_lane("r_cold@vda", {k: Window(k, 5, [3000.0], set()) for k in range(10)}, [], CFG, DAY)
    assert res.insufficient_windows == 10  # r_cold needs min_samples=30 per window


def test_latency_lane_is_converted_to_capability():
    wins = {i: Window(i, 200, [300.0] * 5, set()) for i in range(8)}
    wins.update({i: Window(i, 200, [6000.0] * 5, set()) for i in range(8, 14)})  # 20x slower
    res = analyze_lane("w_over_4k", wins, [], CFG, DAY)
    det = [e for e in res.events if e["kind"] == "shift_detected"]
    assert det and det[0]["evidence"]["direction"] == "down" and 15 < det[0]["evidence"]["factor"] < 25


def test_confounded_windows_say_so():
    wins = {i: Window(i, 1, [BASE], {"aaa"}) for i in range(8)}
    wins.update({i: Window(i, 1, [BASE / 20], {"bbb"}) for i in range(8, 12)})
    res = analyze_lane(LANE, wins, [], CFG, DAY)
    det = [e for e in res.events if e["kind"] == "shift_detected"][0]
    assert "Settings changed" in det["text"] and det["evidence"]["confounded"] is True


def test_recognizer_is_deterministic_and_pure():
    levels = [BASE] * 8 + [BASE / 20] * 8
    a, b = run(levels, 0.1, 5), run(levels, 0.1, 5)
    assert kinds(a) == kinds(b) and [vars(r) for r in a.rows] == [vars(r) for r in b.rows]


def test_events_are_recommendations_only():
    res = run([BASE] * 8 + [BASE / 20] * 8, 0.1)
    allowed = {"lane", "kind", "text", "evidence"}
    for e in res.events:
        assert set(e) == allowed
        assert e["evidence"]["category"] == "capability"
        assert not any(w in e["text"].lower() for w in ("applied", "set to", "changed the model"))


def rec_bench(t, rate, epoch="e1", valid=True):
    lane = {"valid": valid, "rate_durable_bps": rate, "aborted": None}
    return {"v": 1, "type": "bench", "t": t, "ts": "x", "status": "ok", "region": {}, "settings_epoch": epoch,
            "lanes": {"w_fresh_qd4": lane}}


def test_dedup_of_events_already_in_the_journal():
    recs = [rec_bench(i * DAY + 100, BASE if i < 8 else BASE / 20) for i in range(14)]
    res = recognize(recs, CFG)
    evs = new_events(res, set())
    assert evs and not new_events(res, {e["evidence"]["key"] for e in evs})


def test_aborted_invalid_lanes_do_not_enter_the_series():
    recs = [rec_bench(i * DAY, BASE, valid=(i % 2 == 0)) for i in range(10)]
    lanes, _ = extract(recs, CFG)
    assert len(lanes[LANE]) == 5


def test_probe_latency_only_counts_quiet_records():
    def probe(t, quiet):
        return {"v": 1, "type": "probe", "t": t, "lane": "r_cold", "device": "vda", "quiet": quiet, "n": 40, "zero": 0,
                "lat_us": [500] * 10, "hist": [], "direct": True}
    lanes, _ = extract([probe(10, True), probe(20, False)], CFG)
    assert lanes["r_cold@vda"][0].n == 40


# --- demand-limited classification (low throughput is not a capacity drop) ----------------------------------


def test_low_passive_rate_with_healthy_probes_is_demand_limited():
    recs = [rec_bench(i * DAY, BASE) for i in range(10)]
    recs += [{"v": 1, "type": "passive", "t": 9 * DAY + 5, "devices": {"vda": {"w_bps": 2e6}}, "pressure": None}]
    res = recognize(recs, CFG)
    labels = [d["label"] for d in res["__side__"]["demand"]]
    assert labels == ["demand_limited"]
    assert not [e for e in new_events(res, set()) if e["kind"] == "shift_detected"]


def test_low_passive_rate_without_probe_evidence_is_labelled_evidence_free():
    recs = [{"v": 1, "type": "passive", "t": 3 * DAY, "devices": {"vda": {"w_bps": 1e6}}, "pressure": None}]
    res = recognize(recs, CFG)
    assert res["__side__"]["demand"][0]["label"] == "no_probe_evidence"
    assert not new_events(res, set())  # passive data alone never produces a shift recommendation


def test_degraded_probes_plus_low_passive_is_consistent_not_evidence():
    recs = [rec_bench(i * DAY, BASE if i < 8 else BASE / 20) for i in range(14)]
    recs += [{"v": 1, "type": "passive", "t": 12 * DAY + 5, "devices": {"vda": {"w_bps": 1e6}}, "pressure": None}]
    assert recognize(recs, CFG)["__side__"]["demand"][0]["label"] == "consistent_not_evidence"


def test_busy_passive_window_is_load_present():
    recs = [rec_bench(i * DAY, BASE) for i in range(10)]
    recs += [{"v": 1, "type": "passive", "t": 9 * DAY + 5, "devices": {"vda": {"w_bps": 200e6}}, "pressure": None}]
    assert recognize(recs, CFG)["__side__"]["demand"][0]["label"] == "load_present"


def test_pattern_hint_is_a_hypothesis_about_bandwidth_vs_latency():
    recs = [rec_bench(i * DAY, BASE if i < 8 else BASE / 20) for i in range(14)]
    hint = pattern_hint({k: v for k, v in recognize(recs, CFG).items() if k != "__side__"})
    assert hint and "hypothesis" in hint
