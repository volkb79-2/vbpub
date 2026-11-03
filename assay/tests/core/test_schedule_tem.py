"""B207 -- two-ended-matching candidate scheduling (unit level).

Deterministic and clock-free: every duration is a recorded-style number handed
to the scheduler, so a run replays identically. The synthetic suites copy the
SHAPE of the recorded per-candidate profiles the design simulation used
(``docs/design/resource-profiles-sim``): an IOBench-like suite (flat ~372 MiB
normals, a low class, three 878 MiB highs) and a cli-extended-like suite
(82 MiB normals at 9 s, 51 highs at 256 MiB and 33 s).
"""

from __future__ import annotations

import heapq
import json

import pytest

from assay import _schedule
from assay._schedule import (
    HIGH,
    LOW,
    NORMAL,
    HistoryEntry,
    Prediction,
    TemScheduler,
    classify,
    derive_thresholds,
    parse_history,
    plan_dispatch_order,
    render_history,
    resolve_predictions,
)

MIB = 2**20


def _pairs(count: int, path: str = "a.py") -> list[tuple[str, str]]:
    return [(f"{index:064x}", path) for index in range(count)]


def _profile(wall: float, rss_mib: float, source: str = "profile") -> Prediction:
    return Prediction(wall, int(rss_mib * MIB), source)


def _iobench_like() -> tuple[list[tuple[str, str]], list[float], list[int]]:
    """1,220 candidates: 1,101 normal near 372 MiB, 116 low near 120 MiB,
    3 high at 878 MiB, spread over files, in a fixed pseudo-random order."""
    rss: list[float] = []
    wall: list[float] = []
    for index in range(1220):
        slot = (index * 7919) % 1220  # fixed permutation (7919 is prime)
        if slot < 3:
            rss.append(878.0)
            wall.append(110.0)
        elif slot < 119:
            rss.append(100.0 + (slot % 7) * 10)
            wall.append(30.0 + slot % 11)
        else:
            rss.append(360.0 + (slot % 15))
            wall.append(90.0 + slot % 19)
    pairs = [(f"{i:064x}", f"f{i // 40}.py") for i in range(1220)]
    return pairs, wall, [int(r * MIB) for r in rss]


def _cli_extended_like() -> tuple[list[tuple[str, str]], list[float], list[int]]:
    rss: list[float] = []
    wall: list[float] = []
    for index in range(1456):
        if index % 364 < 13:  # 52 of 1456, clustered in four runs like a file
            rss.append(256.0)
            wall.append(33.0)
        else:
            rss.append(76.0 + index % 13)
            wall.append(8.0 + (index % 5) * 0.5)
    pairs = [(f"{i:064x}", f"m{i // 30}.py") for i in range(1456)]
    return pairs, wall, [int(r * MIB) for r in rss]


def _replay_with_running_sets(
    scheduler: TemScheduler, durations: list[float], actual_rss: list[int]
) -> list[tuple[int, list[int]]]:
    """Drive *scheduler* with a fake clock; return, at every start, the started
    index and the indices running right after it."""
    now = 0.0
    heap: list[tuple[float, int]] = []
    log: list[tuple[int, list[int]]] = []
    while scheduler.remaining or heap:
        while len(heap) < scheduler.workers:
            index = scheduler.next_candidate(now)
            if index is None:
                break
            heapq.heappush(heap, (now + durations[index], index))
            log.append((index, scheduler.running_indices))
        assert heap, "scheduler stalled"
        now, finished = heapq.heappop(heap)
        scheduler.complete(
            finished, now, wall_seconds=durations[finished], rss_peak_bytes=actual_rss[finished]
        )
    return log


def _peak_actual_running(
    order: list[int], durations: list[float], rss: list[int], workers: int
) -> float:
    """Peak of the summed ACTUAL RSS when *order* is dispatched greedily."""
    now = 0.0
    heap: list[tuple[float, int]] = []
    level = 0
    peak = 0
    queue = list(order)
    while queue or heap:
        while queue and len(heap) < workers:
            index = queue.pop(0)
            heapq.heappush(heap, (now + durations[index], index))
            level += rss[index]
            peak = max(peak, level)
        now, finished = heapq.heappop(heap)
        level -= rss[finished]
    return peak


# -- classes ----------------------------------------------------------------


def test_thresholds_follow_the_mode_of_the_distribution_not_fixed_sizes():
    _, _, rss = _iobench_like()
    thresholds = derive_thresholds(rss)
    assert thresholds is not None
    # design 5.3: IOBench has a ~372 MiB mode; high above ~1.5x its p95
    assert 540 * MIB < thresholds.high_bytes < 570 * MIB
    assert 170 * MIB < thresholds.low_bytes < 200 * MIB
    classes = [classify(value, thresholds) for value in rss]
    assert classes.count(HIGH) == 3
    assert classes.count(LOW) == 116
    assert classes.count(NORMAL) == 1101


def test_thresholds_scale_with_the_lane_not_with_constants():
    small = derive_thresholds([v * MIB for v in (80, 82, 84, 82, 83, 256)])
    large = derive_thresholds([v * MIB * 4 for v in (80, 82, 84, 82, 83, 256)])
    assert small is not None and large is not None
    assert large.high_bytes == pytest.approx(4 * small.high_bytes)


def test_class_factors_are_configurable():
    values = [100 * MIB] * 10
    default = derive_thresholds(values)
    tight = derive_thresholds(values, high_factor=1.1, low_factor=0.9)
    assert default is not None and tight is not None
    assert tight.high_bytes < default.high_bytes < 200 * MIB
    assert tight.low_bytes > default.low_bytes
    assert classify(120 * MIB, tight) == HIGH
    assert classify(120 * MIB, default) == NORMAL


def test_no_predictions_means_no_thresholds_and_everything_normal():
    assert derive_thresholds([]) is None
    assert classify(10**12, None) == NORMAL
    assert classify(None, derive_thresholds([MIB])) == NORMAL


# -- dispatch rules ---------------------------------------------------------


def test_without_predictions_the_order_is_canonical():
    pairs = _pairs(9)
    predictions = [Prediction(None, None, _schedule.SOURCE_NONE)] * 9
    order, _ = plan_dispatch_order(pairs, predictions, [1.0] * 9, workers=3)
    assert order == list(range(9))


def test_longest_predicted_first_among_equal_class_ties_by_canonical_index():
    pairs = _pairs(6)
    walls = [5.0, 9.0, 9.0, 1.0, 7.0, 9.0]
    predictions = [_profile(w, 100) for w in walls]
    order, _ = plan_dispatch_order(pairs, predictions, walls, workers=1)
    assert order == [1, 2, 5, 4, 0, 3]


def test_at_most_one_high_runs_and_lows_fill_while_it_runs():
    rss = [100, 100, 400, 100, 400, 100, 40, 40, 40, 100]
    walls = [10.0, 10.0, 50.0, 10.0, 50.0, 10.0, 5.0, 5.0, 5.0, 10.0]
    pairs = _pairs(len(rss))
    predictions = [_profile(w, r) for w, r in zip(walls, rss)]
    actual = [int(r * MIB) for r in rss]
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=4)
    assert [scheduler.predicted_class(i) for i in (2, 4)] == [HIGH, HIGH]
    assert [scheduler.predicted_class(i) for i in (6, 7, 8)] == [LOW, LOW, LOW]
    log = _replay_with_running_sets(scheduler, walls, actual)
    for _, running in log:
        assert sum(scheduler.predicted_class(i) == HIGH for i in running) <= 1
    # highs start early: the first dispatch is a high, the second high waits
    # for the first to finish, and lows are taken while a high runs.
    started = [index for index, _ in log]
    assert started[0] == 2
    assert started[1:4] == [6, 7, 8]
    assert started.index(4) > started.index(2)


def test_admission_never_plans_a_running_sum_above_the_budget():
    pairs, walls, rss = _cli_extended_like()
    predictions = [_profile(w, r / MIB) for w, r in zip(walls, rss)]
    budget = 600 * MIB
    scheduler = TemScheduler(pairs, predictions, budget_bytes=budget, workers=6)
    log = _replay_with_running_sets(scheduler, walls, rss)
    assert len(log) == 1456
    for _, running in log:
        total = sum(rss[i] for i in running)
        # one candidate alone may exceed B; otherwise the sum fits
        assert total <= budget or len(running) == 1
    assert max(sum(rss[i] for i in running) for _, running in log) <= budget


def test_a_candidate_larger_than_the_budget_runs_alone_rather_than_never():
    pairs = _pairs(3)
    predictions = [_profile(10, 100), _profile(10, 900), _profile(10, 100)]
    scheduler = TemScheduler(pairs, predictions, budget_bytes=500 * MIB, workers=3)
    # nothing running: the 900 MiB high is admitted although it exceeds B
    assert scheduler.next_candidate(0.0) == 1
    # while it runs nothing else fits (900 + 100 > 500): wait for a completion
    assert scheduler.next_candidate(0.0) is None
    scheduler.complete(1, 10.0, wall_seconds=10.0, rss_peak_bytes=900 * MIB)
    assert scheduler.next_candidate(10.0) == 0
    assert scheduler.next_candidate(10.0) == 2


def test_queued_high_reserves_the_slot_and_starts_when_the_running_sum_drains():
    """H1=300 and H2=500 MiB with B=600 MiB. H2 does not fit beside the three
    normals that filled the slots while H1 ran, so no further normal may start
    (the reservation); H2 starts the moment the sum has drained to B - 500."""
    rss = [300, 500] + [100] * 8
    walls = [30.0, 20.0] + [10.0] * 8
    pairs = _pairs(len(rss))
    scheduler = TemScheduler(
        pairs, [_profile(w, r) for w, r in zip(walls, rss)], budget_bytes=600 * MIB, workers=6
    )
    assert [scheduler.predicted_class(i) for i in (0, 1)] == [HIGH, HIGH]
    assert scheduler.next_candidate(0.0) == 0  # longest high first
    # a high runs: lows then normals fill until the budget is reached
    assert [scheduler.next_candidate(0.0) for _ in range(4)] == [2, 3, 4, None]
    scheduler.complete(0, 30.0, wall_seconds=30.0, rss_peak_bytes=300 * MIB)
    # running: three normals (300 MiB). H2 needs 800 > 600: reserve, start nothing
    assert scheduler.running_predicted_bytes == 300 * MIB
    assert scheduler.next_candidate(30.0) is None
    scheduler.complete(2, 31.0, wall_seconds=10.0, rss_peak_bytes=100 * MIB)
    assert scheduler.next_candidate(31.0) is None  # 200 + 500 > 600
    scheduler.complete(3, 32.0, wall_seconds=10.0, rss_peak_bytes=100 * MIB)
    assert scheduler.next_candidate(32.0) == 1  # 100 + 500 == 600 fits
    # with the high running, normals take the remaining slots only if they fit
    assert scheduler.next_candidate(32.0) is None


def test_next_candidate_never_stalls_with_nothing_running():
    rng_rss = [10, 900, 20, 900, 15, 900]
    pairs = _pairs(6)
    predictions = [_profile(5.0, r) for r in rng_rss]
    order, _ = plan_dispatch_order(
        pairs, predictions, [5.0] * 6, workers=3, budget_bytes=100 * MIB
    )
    assert sorted(order) == list(range(6))


# -- replay of recorded-shape suites ----------------------------------------


@pytest.mark.parametrize("workers", [3, 6])
def test_tem_lpt_lowers_the_running_rss_peak_on_the_cli_extended_shape(workers):
    pairs, walls, rss = _cli_extended_like()
    predictions = [_profile(w, r / MIB) for w, r in zip(walls, rss)]
    order, scheduler = plan_dispatch_order(
        pairs, predictions, walls, workers=workers, actual_rss_bytes=rss
    )
    canonical_peak = _peak_actual_running(list(range(1456)), walls, rss, workers)
    scheduled_peak = _peak_actual_running(order, walls, rss, workers)
    assert scheduled_peak < 0.75 * canonical_peak
    thresholds = scheduler.thresholds
    assert thresholds is not None
    # analytic envelope with at most one high: H_max + (N-1) x high threshold
    envelope = max(rss) + (workers - 1) * thresholds.high_bytes
    assert scheduled_peak <= envelope


def test_tem_lpt_makespan_stays_within_a_percent_of_the_lower_bound():
    pairs, walls, rss = _cli_extended_like()
    predictions = [_profile(w, r / MIB) for w, r in zip(walls, rss)]
    workers = 6
    order, _ = plan_dispatch_order(pairs, predictions, walls, workers=workers)
    now = 0.0
    heap: list[tuple[float, int]] = []
    queue = list(order)
    while queue or heap:
        while queue and len(heap) < workers:
            index = queue.pop(0)
            heapq.heappush(heap, (now + walls[index], index))
        now, _ = heapq.heappop(heap)
    lower_bound = max(sum(walls) / workers, max(walls))
    assert now <= lower_bound * 1.01


def test_iobench_shape_replay_puts_a_high_at_most_alone_and_is_complete():
    pairs, walls, rss = _iobench_like()
    predictions = [_profile(w, r / MIB) for w, r in zip(walls, rss)]
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=6)
    log = _replay_with_running_sets(scheduler, walls, rss)
    assert sorted(i for i, _ in log) == list(range(1220))
    assert all(
        sum(scheduler.predicted_class(i) == HIGH for i in running) <= 1 for _, running in log
    )
    canonical = _peak_actual_running(list(range(1220)), walls, rss, 6)
    scheduled = _peak_actual_running([i for i, _ in log], walls, rss, 6)
    assert scheduled <= canonical


def _reference_tem_lpt(walls, rss, workers, budget):
    """An independent port of ``sched-sim.py``'s ``tem-lpt`` policy (oracle
    predictions), used to pin the real scheduler's dispatch order to it."""
    thresholds = derive_thresholds(rss)
    hi, lo = thresholds.high_bytes, thresholds.low_bytes
    cls = ["H" if r > hi else ("L" if r < lo else "N") for r in rss]
    queues = {
        k: [i for i in sorted(range(len(rss)), key=lambda i: (-walls[i], i)) if cls[i] == k]
        for k in "HNL"
    }
    running: list[tuple[float, int]] = []
    now = 0.0
    order: list[int] = []
    while any(queues.values()) or running:
        while len(running) < workers and any(queues.values()):
            current = sum(rss[i] for _, i in running)
            high_running = any(cls[i] == "H" for _, i in running)
            picked = None
            for k in ("LN" if high_running else "HNL"):
                if not queues[k]:
                    continue
                head = queues[k][0]
                if budget and running and current + rss[head] > budget:
                    if k == "H":
                        break
                    continue
                picked = queues[k].pop(0)
                break
            if picked is None:
                break
            order.append(picked)
            heapq.heappush(running, (now + walls[picked], picked))
        if not running:
            break
        now, _ = heapq.heappop(running)
    return order


@pytest.mark.parametrize("workers,budget_mib", [(3, None), (6, None), (6, 600)])
def test_dispatch_order_matches_the_reference_simulator_exactly(workers, budget_mib):
    pairs, walls, rss = _cli_extended_like()
    budget = None if budget_mib is None else budget_mib * MIB
    predictions = [_profile(w, r / MIB) for w, r in zip(walls, rss)]
    order, _ = plan_dispatch_order(
        pairs, predictions, walls, workers=workers, budget_bytes=budget
    )
    assert order == _reference_tem_lpt(walls, rss, workers, budget)


# -- predictions ------------------------------------------------------------


def test_prediction_sources_resolve_in_the_documented_order():
    history = {
        "p0": HistoryEntry("a.py", 10.0, 100 * MIB),
        "p1": HistoryEntry("a.py", 20.0, 120 * MIB),
        "other": HistoryEntry("b.py", 40.0, 400 * MIB),
    }
    candidates = [("p0", "a.py"), ("new-a", "a.py"), ("new-c", "c.py")]
    got = resolve_predictions(candidates, history, operator_wall_seconds=7.0, operator_rss_bytes=7 * MIB)
    assert got[0] == Prediction(10.0, 100 * MIB, _schedule.SOURCE_PROFILE)
    assert got[1].source == _schedule.SOURCE_HISTORY_FILE
    assert (got[1].wall_seconds, got[1].rss_peak_bytes) == (15.0, 110 * MIB)
    # unknown file: the lane median of the history
    assert got[2].source == _schedule.SOURCE_HISTORY_LANE
    assert got[2].wall_seconds == 20.0
    # no history at all: the operator medians, then none
    empty = resolve_predictions(candidates, {}, operator_wall_seconds=7.0, operator_rss_bytes=7 * MIB)
    assert {p.source for p in empty} == {_schedule.SOURCE_OPERATOR}
    assert resolve_predictions(candidates, {})[0] == Prediction(None, None, _schedule.SOURCE_NONE)


def test_a_profile_without_rss_falls_through_for_rss_only():
    history = {"x": HistoryEntry("a.py", 12.0, None), "y": HistoryEntry("a.py", 3.0, 50 * MIB)}
    got = resolve_predictions([("x", "a.py")], history)[0]
    assert got.wall_seconds == 12.0
    assert got.rss_peak_bytes == 50 * MIB
    assert got.source == _schedule.SOURCE_PROFILE


def test_live_file_median_replaces_a_coarse_prediction_after_five_completions():
    pairs = [(f"{i:064x}", "a.py") for i in range(12)]
    predictions = [Prediction(None, None, _schedule.SOURCE_NONE)] * 12
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=1)
    for step in range(5):
        index = scheduler.next_candidate(float(step))
        assert index == step  # canonical while nothing is known
        scheduler.complete(index, step + 1.0, wall_seconds=30.0, rss_peak_bytes=200 * MIB)
    nxt = scheduler.prediction(5)
    assert nxt.source == _schedule.SOURCE_LIVE_FILE
    assert (nxt.wall_seconds, nxt.rss_peak_bytes) == (30.0, 200 * MIB)
    summary = scheduler.summary(errors={})
    assert summary["live_median_files"] == 1


def test_a_profile_is_never_replaced_by_a_live_median():
    pairs = [(f"{i:064x}", "a.py") for i in range(8)]
    predictions = [_profile(50.0, 500.0)] + [Prediction(None, None, _schedule.SOURCE_NONE)] * 7
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=1)
    for step in range(5):
        index = scheduler.next_candidate(float(step))
        scheduler.complete(index, step + 1.0, wall_seconds=5.0, rss_peak_bytes=50 * MIB)
    # candidate 0 ran first and finished; the rest took the live median
    assert scheduler.prediction(0).source == _schedule.SOURCE_PROFILE


# -- history file -----------------------------------------------------------


def test_history_round_trips_and_ignores_corruption():
    entries = {"a" * 64: HistoryEntry("x.py", 1.5, 123), "b" * 64: HistoryEntry("y.py", None, 9)}
    parsed, reason = parse_history(render_history(entries))
    assert reason is None
    assert parsed == entries
    assert parse_history(None) == ({}, None)
    for bad in (b"{", b"[]", json.dumps({"schema": "other/1", "candidates": {}}).encode()):
        parsed, reason = parse_history(bad)
        assert parsed == {}
        assert reason
    junk = json.dumps(
        {
            "schema": _schedule.HISTORY_SCHEMA,
            "candidates": {
                "ok": {"path": "p", "wall_seconds": 1, "profile": {"rss_peak_bytes": 2}},
                "negative": {"path": "p", "wall_seconds": -1, "profile": {"rss_peak_bytes": -5}},
                "noprofile": {"path": "p", "wall_seconds": 1},
                "nopath": {"wall_seconds": 1, "profile": {"rss_peak_bytes": 2}},
                "bool": {"path": "p", "wall_seconds": True, "profile": {"rss_peak_bytes": False}},
                "text": "x",
            },
        }
    )
    parsed, _ = parse_history(junk)
    assert set(parsed) == {"ok"}


def test_history_is_written_as_profile_digests_and_never_with_the_old_numeric_key():
    entries = {
        "a" * 64: HistoryEntry("x.py", 1.5, 123, "MEASURED", None),
        "b" * 64: HistoryEntry("y.py", 2.5, None, "NO_MEASUREMENT", "OBSERVER_UNAVAILABLE"),
    }
    document = json.loads(render_history(entries))
    assert document["schema"] == "assay-schedule-history/2"
    for item in document["candidates"].values():
        assert "rss_peak_bytes" not in item  # only inside `profile`
    assert document["candidates"]["b" * 64]["profile"] == {
        "status": "NO_MEASUREMENT", "reason": "OBSERVER_UNAVAILABLE", "rss_peak_bytes": None,
    }
    parsed, reason = parse_history(render_history(entries))
    assert reason is None and parsed == entries


def test_history_written_by_assay_11_is_still_read():
    legacy = json.dumps(
        {
            "schema": "assay-schedule-history/1",
            "candidates": {
                "a" * 64: {"path": "x.py", "wall_seconds": 4.0, "rss_peak_bytes": 700},
                "b" * 64: {"path": "y.py", "wall_seconds": 1.0, "rss_peak_bytes": None},
            },
        }
    )
    parsed, reason = parse_history(legacy)
    assert reason is None
    assert parsed == {
        "a" * 64: HistoryEntry("x.py", 4.0, 700),
        "b" * 64: HistoryEntry("y.py", 1.0, None),
    }


def test_history_reads_a_full_resource_profile_object():
    profile = {
        "schema": "resource-profile/1", "status": "MEASURED", "reason": None,
        "wall_seconds": 9.5, "metrics": {"rss_peak_bytes": 4096, "cpu_seconds": 1.0},
    }
    document = json.dumps(
        {"schema": _schedule.HISTORY_SCHEMA,
         "candidates": {"a" * 64: {"path": "x.py", "wall_seconds": None, "profile": profile}}}
    )
    parsed, reason = parse_history(document)
    assert reason is None
    assert parsed == {"a" * 64: HistoryEntry("x.py", 9.5, 4096, "MEASURED", None)}


def test_the_verdict_schedule_summary_is_overall_ratios_sources_and_confusion():
    pairs = _pairs(3)
    predictions = [_profile(10, 100), _profile(10, 100), _profile(10, 100)]
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=2)
    errors = {}
    for step in range(3):
        index = scheduler.next_candidate(float(step))
        scheduler.complete(index, step + 1.0, wall_seconds=20.0, rss_peak_bytes=150 * MIB)
        errors[index] = scheduler.error(index, wall_seconds=20.0, rss_peak_bytes=150 * MIB)
    summary = scheduler.verdict_summary(errors=errors)
    assert summary["policy"] == "tem-lpt" and summary["jobs"] == 2
    assert summary["memory_budget_bytes"] is None
    assert set(summary["thresholds"]) == {"high_bytes", "low_bytes", "high_factor", "low_factor"}
    assert summary["error"]["rss_ratio"] == {"p50": 1.5, "p95": 1.5}
    assert summary["error"]["wall_ratio"] == {"p50": 2.0, "p95": 2.0}
    assert sum(sum(row.values()) for row in summary["error"]["class_confusion"].values()) == 3
    assert sum(summary["prediction_sources"].values()) == 3
    canonical = _schedule.canonical_verdict_summary(jobs=1, memory_budget_bytes=None)
    assert canonical["policy"] == "canonical" and canonical["error"] is None


# -- predicted versus actual ------------------------------------------------


def test_error_records_ratios_and_the_reason_when_nothing_was_measured():
    scheduler = TemScheduler(_pairs(2), [_profile(10, 100), _profile(10, 100)], budget_bytes=None, workers=1)
    error = scheduler.error(0, wall_seconds=20.0, rss_peak_bytes=150 * MIB)
    assert error["rss_ratio"] == 1.5 and error["wall_ratio"] == 2.0
    assert error["class_actual"] == NORMAL and "reason" not in error
    unmeasured = scheduler.error(1, wall_seconds=None, rss_peak_bytes=None)
    assert unmeasured["rss_ratio"] is None and unmeasured["wall_ratio"] is None
    assert "no peak RSS" in unmeasured["reason"]
    reasoned = scheduler.error(
        1, wall_seconds=3.0, rss_peak_bytes=None, profile_reason="OBSERVER_UNAVAILABLE"
    )
    assert "(OBSERVER_UNAVAILABLE)" in reasoned["reason"]
    assert reasoned["profile_reason"] == "OBSERVER_UNAVAILABLE"


def test_summary_counts_sources_classes_confusion_and_overshoots():
    rss = [100, 100, 100, 400]
    pairs = _pairs(4)
    predictions = [_profile(10, r) for r in rss]
    scheduler = TemScheduler(pairs, predictions, budget_bytes=450 * MIB, workers=2)
    errors = {}
    now = 0.0
    assert scheduler.next_candidate(now) == 3
    # actual peak of the high is far above its prediction: an overshoot
    scheduler.complete(3, 10.0, wall_seconds=10.0, rss_peak_bytes=600 * MIB)
    errors[3] = scheduler.error(3, wall_seconds=10.0, rss_peak_bytes=600 * MIB)
    summary = scheduler.summary(errors=errors)
    assert summary["class_counts"] == {HIGH: 1, NORMAL: 3, LOW: 0}
    assert summary["source_counts"]["profile"] == 4
    assert summary["class_confusion"][HIGH][HIGH] == 1
    assert summary["error"]["profile"]["rss_ratio"] == {"count": 1, "p50": 1.5, "p95": 1.5}
    assert summary["admission_overshoots"] == 1
    assert summary["memory_budget_bytes"] == 450 * MIB
    assert summary["policy"] == "tem-lpt"


def test_scheduler_rejects_nonsense_inputs():
    with pytest.raises(ValueError):
        TemScheduler(_pairs(2), [_profile(1, 1)], budget_bytes=None, workers=1)
    with pytest.raises(ValueError):
        TemScheduler(_pairs(1), [_profile(1, 1)], budget_bytes=0, workers=1)
    with pytest.raises(ValueError):
        TemScheduler(_pairs(1), [_profile(1, 1)], budget_bytes=None, workers=0)
