"""Tests for lib/summary.py — the incremental Summary accumulator.

**Golden reproduction.** RG55-INTERFACE-CONTRACT.md ships five one-second
frames of a fake cgroup v2 + /proc tree
(``tests/fixtures/contract/frames/0..4``) and golden Summary documents
computed from them by hand (``tests/fixtures/contract/README.md`` derives
every number). Feeding those five frames through ``SummaryAccumulator`` MUST
reproduce ``summary-v1.json`` (scope ``container-shared``) and
``summary-container-v1.json`` (scope ``container``) byte-for-byte after
``json.dumps(sort_keys=True, indent=2)`` — that is the contract, not an
implementation detail, so these tests only ever go through
``add_sample``/``finalize``, never a `_`-prefixed helper.

**Hand mutants.** ``test_hand_mutants.py`` (sibling file) runs the ten
required mutation probes against this same golden path — kept separate so a
reviewer can see the golden test and the mutant list without one screen of
noise drowning the other.
"""

from __future__ import annotations

import filecmp
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from lib import metrics, summary

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contract"
FRAMES_DIR = FIXTURES / "frames"
CONTAINER_ID = "deadbeefcafebabefeedfacefeedbeadf00dbabe1234567890abcdef00112233"
SCOPE_CGROUP = f"/dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope"
THRESHOLDS = {"hot_rate_pct": 5, "warm_rate_pct": 1, "cold_age_s": 30, "idle_age_s": 120}


def _frame_paths(base: Path, n: int):
    frame = base / str(n)
    target = frame / "dev.slice" / "dev-background.slice" / f"docker-{CONTAINER_ID}.scope"
    slice_dir = frame / "dev.slice" / "dev-background.slice"
    proc = frame / "proc"
    return target, slice_dir, proc


def _damon_frame(base: Path, n: int) -> Dict[str, int]:
    return json.loads((base / str(n) / "damon.json").read_text())


def _load_golden(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _canon(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, indent=2)


def _new_accumulator(scope: str, *, damon_enabled: bool = True, token=None) -> summary.SummaryAccumulator:
    return summary.SummaryAccumulator(
        session="s-20260912T101500Z-9f01",
        daemon_name="cgprofile-host-daemon",
        daemon_version="1.0.0",
        scope=scope,
        started_at="2026-09-12T10:15:00Z",
        interval_seconds=1.0,
        container_id=CONTAINER_ID,
        cgroup=SCOPE_CGROUP,
        token=token,
        slice_name="dev-background.slice",
        damon_enabled=damon_enabled,
        damon_kdamond=1,
        damon_thresholds=THRESHOLDS,
    )


def _feed(scope: str, *, frames_dir: Path = FRAMES_DIR, damon_enabled: bool = True,
          n_frames: int = 5, acc: summary.SummaryAccumulator = None) -> Dict[str, Any]:
    acc = acc or _new_accumulator(scope, damon_enabled=damon_enabled)
    for n in range(n_frames):
        target, slice_dir, proc = _frame_paths(frames_dir, n)
        pids = summary.read_cgroup_pids(str(target))
        acc.add_sample(
            cgroup=summary.sample_target_cgroup(str(target)),
            slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
            host=metrics.sample_host(proc_root=str(proc)),
            damon=_damon_frame(frames_dir, n) if damon_enabled else None,
            pids=pids,
            mono=float(n),
        )
    return acc.finalize(ended_at="2026-09-12T10:15:04Z")


class TestGoldenReproduction:
    def test_container_shared_scope_matches_summary_v1(self):
        result = _feed("container-shared")
        assert _canon(result) == _canon(_load_golden("summary-v1.json"))

    def test_container_scope_matches_summary_container_v1(self):
        result = _feed("container")
        assert _canon(result) == _canon(_load_golden("summary-container-v1.json"))

    def test_pids_seen_is_the_union_of_cgroup_procs_across_frames(self):
        # frames/README.md: cgroup.procs grows 424242-44, gains -45 at frame1,
        # -46 at frame2, loses -46 again by frame3 -> 5 distinct pids ever
        # seen, matching both summary-v1.json's target.targets_seen and
        # damon.targets_seen (no --token in this fixture, so both report
        # "how many pids were ever found in the cgroup").
        result = _feed("container-shared")
        assert result["target"]["targets_seen"] == 5
        assert result["damon"]["targets_seen"] == 5


class TestFixtureIdentity:
    def test_contract_fixtures_are_byte_identical_to_the_frozen_copy(self):
        """RG55-INTERFACE-CONTRACT.md §6: both packages vendor the same
        fixtures byte-for-byte, so drift is caught in BOTH projects. The
        frozen copy lives at the repo root's run-gate-project/ — resolved
        relative to this worktree (not a hardcoded /workspaces/vbpub path)
        so the same test passes unmodified inside the gate container, which
        mounts the whole worktree (a full git worktree checkout, not just
        this project directory) read-only at /work.
        """
        worktree_root = Path(__file__).resolve().parents[3]
        frozen = worktree_root / "run-gate-project" / "nyxloom-trove" / "fixtures" / "rg55"
        assert frozen.is_dir(), f"expected the frozen fixtures at {frozen}"
        diffs = _dircmp_diffs(filecmp.dircmp(str(frozen), str(FIXTURES)))
        assert diffs == [], f"tests/fixtures/contract has drifted from {frozen}: {diffs}"


def _dircmp_diffs(cmp: filecmp.dircmp, prefix: str = "") -> List[str]:
    diffs = list(cmp.left_only) + list(cmp.right_only) + list(cmp.diff_files) + list(cmp.funny_files)
    out = [f"{prefix}{name}" for name in diffs]
    for name, sub in cmp.subdirs.items():
        out += _dircmp_diffs(sub, prefix=f"{prefix}{name}/")
    return out


# ── decision-ask coverage: "last read" skips back over a missing tick ──────

class TestLastReadSkipsUnreadableTrailingSample:
    """Decision ask (recorded in the REPORT): contract §7 says memory.peak_bytes
    (scope container) and pids.peak are "the last read" — this implementation
    takes that as the last *successful* read, not literally sample index n
    regardless of nullity. Pin the behavior with a case the golden fixtures
    never exercise: the final frame's memory.peak/pids.peak vanish (container
    exited a tick early) but the run must not lose the real high-water mark
    read a moment before.
    """

    def test_container_scope_peak_uses_last_non_null_reading(self):
        acc = _new_accumulator("container", damon_enabled=False)
        for n in range(5):
            target, slice_dir, proc = _frame_paths(FRAMES_DIR, n)
            cgroup = summary.sample_target_cgroup(str(target))
            if n == 4:
                cgroup["mem"]["peak"] = None
                cgroup["pids"]["peak"] = None
            acc.add_sample(
                cgroup=cgroup,
                slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                host=metrics.sample_host(proc_root=str(proc)),
            )
        result = acc.finalize(ended_at="2026-09-12T10:15:04Z")
        # frame 3's memory.peak (796917760) and pids.peak (5) are the last
        # readable ones once frame 4 goes missing.
        assert result["memory"]["peak_bytes"] == 796917760
        assert result["pids"]["peak"] == 5

    def test_all_null_last_present_is_none(self):
        acc = _new_accumulator("container", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        cgroup = summary.sample_target_cgroup(str(target))
        cgroup["mem"]["peak"] = None
        cgroup["pids"]["peak"] = None
        acc.add_sample(
            cgroup=cgroup,
            slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
            host=metrics.sample_host(proc_root=str(proc)),
        )
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["memory"]["peak_bytes"] is None
        assert result["pids"]["peak"] is None


# ── branch coverage: absent data, no slice, no damon, unavailable damon ────

class TestAbsentInputsStayNull:
    @pytest.mark.parametrize("text", [None, "", "not-a-timestamp"])
    def test_parse_iso_returns_none_for_missing_or_malformed_text(self, text):
        # `_parse_iso` is the null-preserving boundary used by duration
        # calculation.  Its private return contract is still load-bearing:
        # `[]` is falsy but is not an Optional[datetime], and would make a
        # direct caller report a fabricated value type.
        assert summary._parse_iso(text) is None

    def test_single_unreadable_sample_produces_an_all_null_summary(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={}, host={}, slice_cgroup=None)
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["memory"] == {
            "peak_bytes": None, "source": "sampled-max", "baseline_bytes": None,
            "peak_over_baseline_bytes": None, "p90_bytes": None, "median_bytes": None,
            "swap_peak_bytes": None, "anon_peak_bytes": None, "file_peak_bytes": None,
        }
        assert result["cpu"] == {
            "seconds": None, "cores_avg": None, "cores_max": None,
            "throttled_seconds": None, "nr_throttled": None,
        }
        assert result["events"] == {"oom_kill": None, "limit_drift": None, "memory_high_breach": None}
        assert result["host"]["slice"] is None
        assert result["host"]["start"]["loadavg1"] is None
        assert result["damon"] is None
        assert result["duration_seconds"] == 0.0

    def test_host_stall_stays_null_when_only_one_end_is_readable(self):
        # `_delta` refuses whenever EITHER end is unreadable (`a is None or
        # b is None`) -- a host PSI total present at the first tick and gone
        # by the last (or the reverse) must still read null, never attempt
        # `None - 1000.0`/`1000.0 - None` (a `boolop-swap` to `and` would
        # only refuse when BOTH ends are missing, letting this exact
        # one-sided case crash `finalize()` with a TypeError instead of the
        # "unreadable is never a number" contract this helper exists for).
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={}, host={"psi": {"memory": {"some_total": 1000.0, "full_total": 500.0}}})
        acc.add_sample(cgroup={}, host={"psi": {"memory": {}}})
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["host"]["memory_some_stall_seconds"] is None
        assert result["host"]["memory_full_stall_seconds"] is None

    def test_loadavg1_stays_null_when_loadavg_is_present_but_not_a_list(self):
        # `loadavg[0] if isinstance(loadavg, list) and loadavg else None` --
        # a `boolop-swap` to `or` would let any other TRUTHY, non-list value
        # through to `loadavg[0]` (e.g. indexing a malformed string), rather
        # than refusing it the way `isinstance(...) and ...` does. Real
        # `loadavg` is always a list-or-None (`_loadavg`'s own contract);
        # this proves the guard itself, not just today's producer.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={}, host={"loadavg": "not-a-list"})
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["host"]["start"]["loadavg1"] is None

    def test_damon_enabled_defaults_to_false_when_omitted(self):
        # `_new_accumulator` (every other test's factory) always passes
        # `damon_enabled` explicitly, so it can never exercise the
        # constructor's own default -- construct one directly, omitting it,
        # the way a caller relying on the default actually would. A
        # `bool-const-flip` to `True` would make every omitting caller's
        # summary carry a `damon` block (status "on") instead of `None`.
        acc = summary.SummaryAccumulator(
            session="s-20260912T101500Z-9f01",
            daemon_name="cgprofile-host-daemon",
            daemon_version="1.0.0",
            scope="container-shared",
            started_at="2026-09-12T10:15:00Z",
            interval_seconds=1.0,
            container_id=CONTAINER_ID,
            cgroup=SCOPE_CGROUP,
            token=None,
        )
        acc.add_sample(cgroup={}, host={})
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["damon"] is None

    def test_duration_stays_null_when_only_one_timestamp_parses(self):
        # `(end_dt - start_dt).total_seconds() if start_dt and end_dt else
        # None` -- a `boolop-swap` to `or` would let a single successfully-
        # parsed end (or start) through to the subtraction while the OTHER
        # side is still `None`, crashing instead of the contract's own
        # "an unparseable timestamp means an unknown duration" null.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.started_at = "not-a-timestamp"
        acc.add_sample(cgroup={}, host={})
        result = acc.finalize(ended_at="2026-09-12T10:15:04Z")
        assert result["duration_seconds"] is None

    def test_zero_interval_never_divides_by_zero_for_cores_max(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.interval_seconds = 0.0
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        for n in (0, 1):
            t, s, p = _frame_paths(FRAMES_DIR, n)
            acc.add_sample(cgroup=summary.sample_target_cgroup(str(t)),
                            slice_cgroup=summary.sample_slice_cgroup(str(s)),
                            host=metrics.sample_host(proc_root=str(p)))
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["cpu"]["cores_max"] is None

    def test_cores_max_uses_each_positive_sample_timestamp_delta(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        usages = (0, 500_000, 2_500_000)
        monos = (0.0, 0.5, 2.0)
        for usage, mono in zip(usages, monos):
            acc.add_sample(cgroup={"cpu": {"usage_usec": usage}}, host={}, mono=mono)
        result = acc.finalize(ended_at="2026-09-12T10:15:02Z")
        # The two rates are 1.0 and 1.333... cores. A fixed configured
        # interval would incorrectly report 2.0 cores for the second pair.
        assert result["cpu"]["cores_max"] == 1.333

    @pytest.mark.parametrize("monos", [(0.0, 0.0), (0.0, -1.0)])
    def test_cores_max_is_null_for_a_non_positive_timestamp_delta(self, monos):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        for usage, mono in zip((0, 1_000_000), monos):
            acc.add_sample(cgroup={"cpu": {"usage_usec": usage}}, host={}, mono=mono)
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["cpu"]["cores_max"] is None

    def test_cores_max_is_null_when_a_sample_timestamp_is_unreadable(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={"cpu": {"usage_usec": 0}}, host={}, mono=0.0)
        acc.add_sample(cgroup={"cpu": {"usage_usec": 1_000_000}}, host={}, mono=None)
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["cpu"]["cores_max"] is None

    def test_cores_max_is_null_when_usage_delta_is_unreadable(self):
        # Both timestamps are valid, but the usage delta is not.  The first
        # `or` in the guard must remain independent; Or->And would attempt to
        # divide None after this exact missing-usage transition.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={}, host={}, mono=0.0)
        acc.add_sample(cgroup={"cpu": {"usage_usec": 1_000_000}}, host={}, mono=1.0)
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["cpu"]["cores_max"] is None

    def test_unparsable_timestamps_leave_duration_and_cores_avg_null(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.started_at = "not-a-timestamp"
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        acc.add_sample(cgroup=summary.sample_target_cgroup(str(target)),
                        slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                        host=metrics.sample_host(proc_root=str(proc)))
        result = acc.finalize(ended_at="also-not-a-timestamp")
        assert result["duration_seconds"] is None
        assert result["cpu"]["cores_avg"] is None

    def test_empty_started_at_leaves_duration_null(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.started_at = ""
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        acc.add_sample(cgroup=summary.sample_target_cgroup(str(target)),
                        slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                        host=metrics.sample_host(proc_root=str(proc)))
        result = acc.finalize(ended_at="")
        assert result["duration_seconds"] is None

    def test_limit_drift_ignores_pairs_with_a_missing_side(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        base_cgroup = summary.sample_target_cgroup(str(target))
        host = metrics.sample_host(proc_root=str(proc))
        first = dict(base_cgroup, mem_max=1000, mem_high=500)
        missing = dict(base_cgroup, mem_max=None, mem_high=500)
        changed = dict(base_cgroup, mem_max=1000, mem_high=600)
        for cgroup in (first, missing, changed):
            acc.add_sample(cgroup=cgroup, slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)), host=host)
        result = acc.finalize(ended_at="2026-09-12T10:15:02Z")
        # (first->missing) skipped for a missing side, (missing->changed)
        # skipped too -- only a pair with BOTH sides present ever counts, and
        # this fixture deliberately has none, so drift is 0, not None (mem_max/
        # mem_high WERE readable at least once, so this is a real "unchanged
        # across the pairs we could compare", never an absence).
        assert result["events"]["limit_drift"] == 0

    def test_limit_drift_ignores_pair_with_only_memory_high_missing(self):
        # All four readings in a consecutive pair must be present before the
        # pair can count as drift.  In particular, the final guard term
        # (cur.memory.high) is independent: changing its preceding `or` to
        # `and` would compare these tuples and falsely report one change.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        base_cgroup = summary.sample_target_cgroup(str(target))
        host = metrics.sample_host(proc_root=str(proc))
        first = dict(base_cgroup, mem_max=1000, mem_high=500)
        missing_high = dict(base_cgroup, mem_max=1000, mem_high=None)
        for cgroup in (first, missing_high):
            acc.add_sample(
                cgroup=cgroup,
                slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                host=host,
            )
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["events"]["limit_drift"] == 0

    def test_limit_drift_is_null_when_never_readable_at_all(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        cgroup = dict(summary.sample_target_cgroup(str(target)), mem_max=None, mem_high=None)
        host = metrics.sample_host(proc_root=str(proc))
        acc.add_sample(cgroup=cgroup, slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)), host=host)
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["events"]["limit_drift"] is None

    def test_damon_unavailable_reports_reason_and_keeps_running(self):
        acc = _new_accumulator("container-shared", damon_enabled=True)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        acc.add_sample(cgroup=summary.sample_target_cgroup(str(target)),
                        slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                        host=metrics.sample_host(proc_root=str(proc)))
        acc.mark_damon_unavailable("sysfs read-only")
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["damon"]["status"] == "unavailable"
        assert result["damon"]["reason"] == "sysfs read-only"
        assert result["damon"]["kdamond"] is None
        assert result["damon"]["samples"] == 0
        assert result["damon"]["hot_bytes"] is None

    def test_damon_disabled_ignores_any_damon_samples_passed_in(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        acc.add_sample(cgroup=summary.sample_target_cgroup(str(target)),
                        slice_cgroup=summary.sample_slice_cgroup(str(slice_dir)),
                        host=metrics.sample_host(proc_root=str(proc)),
                        damon=_damon_frame(FRAMES_DIR, 0))
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["damon"] is None

    def test_peak_over_baseline_is_always_null_in_container_scope(self):
        # RW-21 / contract §3's nullability note: in scope "container" the
        # profiled cgroup IS the lane, so "peak over what it started at" is
        # not meaningful -- peak_over_baseline_bytes is unconditionally
        # None there, even when both peak and baseline ARE present (this
        # fixture deliberately sets peak BELOW baseline -- the exact
        # scenario that, pre-RW-21, used to floor at 0 -- to prove the
        # null-override happens instead of, not in addition to, that
        # floor computation).
        acc = _new_accumulator("container", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        host = metrics.sample_host(proc_root=str(proc))
        slice_sample = summary.sample_slice_cgroup(str(slice_dir))
        high_baseline = summary.sample_target_cgroup(str(target))
        high_baseline["mem"]["current"] = 900 * 1024 * 1024
        high_baseline["mem"]["peak"] = 500 * 1024 * 1024  # below baseline
        acc.add_sample(cgroup=high_baseline, slice_cgroup=slice_sample, host=host)
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["memory"]["baseline_bytes"] == 900 * 1024 * 1024
        assert result["memory"]["peak_bytes"] == 500 * 1024 * 1024
        assert result["memory"]["peak_over_baseline_bytes"] is None

    def test_peak_over_baseline_still_floors_at_zero_in_container_shared_scope(self):
        # container-shared scope is UNAFFECTED by RW-21 -- still computes
        # peak_over_baseline_bytes the original way (max(0, peak -
        # baseline)). Two samples so peak (a max() over sampled
        # memory.current) can independently differ from baseline (the
        # FIRST sample's current) without the max() trivially including it.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        target, slice_dir, proc = _frame_paths(FRAMES_DIR, 0)
        host = metrics.sample_host(proc_root=str(proc))
        slice_sample = summary.sample_slice_cgroup(str(slice_dir))
        high_baseline = summary.sample_target_cgroup(str(target))
        high_baseline["mem"]["current"] = 900 * 1024 * 1024
        lower = summary.sample_target_cgroup(str(target))
        lower["mem"]["current"] = 500 * 1024 * 1024  # below baseline
        acc.add_sample(cgroup=high_baseline, slice_cgroup=slice_sample, host=host)
        acc.add_sample(cgroup=lower, slice_cgroup=slice_sample, host=host)
        result = acc.finalize(ended_at="2026-09-12T10:15:00Z")
        assert result["memory"]["baseline_bytes"] == 900 * 1024 * 1024
        assert result["memory"]["peak_bytes"] == 900 * 1024 * 1024  # max(900, 500)
        assert result["memory"]["peak_over_baseline_bytes"] == 0

    def test_peak_over_baseline_stays_null_when_only_one_of_peak_baseline_is_readable(self):
        # `None if peak is None or baseline is None else max(0, peak -
        # baseline)` -- a `boolop-swap` to `and` would only null out when
        # BOTH are unreadable, letting this one-sided case (baseline
        # unreadable at the first tick, a real peak read later) through to
        # `max(0, <int> - None)`, a crash instead of the documented null.
        acc = _new_accumulator("container-shared", damon_enabled=False)
        acc.add_sample(cgroup={}, host={})  # first sample: mem.current unreadable -> baseline None
        acc.add_sample(cgroup={"mem": {"current": 500 * 1024 * 1024}}, host={})
        result = acc.finalize(ended_at="2026-09-12T10:15:01Z")
        assert result["memory"]["baseline_bytes"] is None
        assert result["memory"]["peak_bytes"] == 500 * 1024 * 1024
        assert result["memory"]["peak_over_baseline_bytes"] is None

    def test_unknown_scope_is_rejected_at_construction(self):
        with pytest.raises(ValueError):
            _new_accumulator("host-wide")

    def test_finalize_without_any_sample_is_a_caller_error(self):
        acc = _new_accumulator("container-shared", damon_enabled=False)
        with pytest.raises(ValueError):
            acc.finalize(ended_at="2026-09-12T10:15:00Z")


class TestSamplingHelpers:
    def test_sample_target_cgroup_of_a_vanished_cgroup_is_empty(self, tmp_path):
        assert summary.sample_target_cgroup(str(tmp_path / "gone")) == {}

    def test_read_cgroup_pids_of_a_vanished_cgroup_is_empty(self, tmp_path):
        assert summary.read_cgroup_pids(str(tmp_path / "gone")) == []

    def test_read_cgroup_pids_skips_blank_and_unparsable_lines(self, tmp_path):
        (tmp_path / "cgroup.procs").write_text("111\n\nnot-a-pid\n222\n")
        assert summary.read_cgroup_pids(str(tmp_path)) == [111, 222]

    def test_read_cgroup_pids_discards_private_namespace_zero_placeholders(self, tmp_path):
        (tmp_path / "cgroup.procs").write_text("0\n111\n0\n")
        assert summary.read_cgroup_pids(str(tmp_path)) == [111]
