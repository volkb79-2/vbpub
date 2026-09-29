"""The watch state machine, the policy parser and the bounded stream reader
— RG55-INTERFACE-CONTRACT.md §8.2/§8.4, RG-55 P6 C7 (CP-8).

Everything here runs on a FAKE clock and hand-built readings, which is the
only way every §8.4 transition gets exercised at all: the real ones take
five minutes of idle bound each. `test_serve_watch.py` covers the daemon's
watch wiring and safe refusal when a shared-scope lane has no kill boundary;
`test_serve_placement.py` checks the cgroup-kill write oracles. Live kernel
enforcement is a separate acceptance probe, not modeled by this fake-clock
state-machine suite.
"""

from __future__ import annotations

import json
import os

import pytest

from lib import liveness


# ── §8.4: policy parsing (`bad-policy` is the caller's, here it is an error) ──

class TestParsePolicy:
    def test_no_options_is_the_default_policy(self):
        policy = liveness.parse_policy({})
        assert policy.progress_stream is None
        assert policy.idle_bound == "auto"
        assert policy.ceiling == "auto"
        # The default is REPORT: a session nobody asked to have killed is
        # never killed (§8.4).
        assert policy.on_stall == "report"

    def test_explicit_nulls_read_exactly_like_absent_options(self):
        # The reference translator sends explicit nulls for options the
        # caller did not give (§8.1 rule 2 / `_ctl_request`), so this is the
        # shape EVERY real `start` request has.
        assert liveness.parse_policy(
            {"progress_stream": None, "idle_bound": None, "ceiling": None, "on_stall": None}
        ) == liveness.parse_policy({})

    def test_full_policy_is_parsed(self):
        policy = liveness.parse_policy({
            "progress_stream": "/run/lane/progress.ndjson", "idle_bound": 120,
            "ceiling": "900.5", "on_stall": "kill",
        })
        assert policy.progress_stream == "/run/lane/progress.ndjson"
        assert policy.idle_bound == 120.0
        # A numeric STRING is accepted: `--idle-bound`/`--ceiling` are string
        # options on the CLI (they have to be — `auto` is legal), and the
        # client forwards what it was given rather than deciding.
        assert policy.ceiling == 900.5
        assert policy.on_stall == "kill"

    @pytest.mark.parametrize("args, fragment", [
        ({"progress_stream": ""}, "non-empty"),
        ({"progress_stream": "   "}, "non-empty"),
        ({"progress_stream": 17}, "non-empty"),
        ({"progress_stream": "relative/progress.ndjson"}, "absolute"),
        ({"idle_bound": "soon"}, "--idle-bound"),
        ({"idle_bound": 0}, "greater than 0"),
        ({"idle_bound": -5}, "greater than 0"),
        ({"idle_bound": True}, "--idle-bound"),
        ({"idle_bound": [300]}, "--idle-bound"),
        ({"ceiling": "eventually"}, "--ceiling"),
        ({"ceiling": 0}, "greater than 0"),
        ({"on_stall": "maim"}, "--on-stall"),
    ])
    def test_unparsable_options_are_refused(self, args, fragment):
        with pytest.raises(liveness.PolicyError) as exc:
            liveness.parse_policy(args)
        assert fragment in str(exc.value)

    def test_idle_bound_auto_is_the_floor_until_a_hint_arrives(self):
        policy = liveness.parse_policy({})
        assert policy.idle_bound_seconds(None) == 300.0
        # D-22: max(300, 3 x hint) — the floor wins for a fast cadence...
        assert policy.idle_bound_seconds(45.0) == 300.0
        # ...and the hint wins for a slow one.
        assert policy.idle_bound_seconds(200.0) == 600.0

    def test_an_authored_idle_bound_ignores_the_hint(self):
        policy = liveness.parse_policy({"idle_bound": 30})
        assert policy.idle_bound_seconds(None) == 30.0
        assert policy.idle_bound_seconds(9000.0) == 30.0

    def test_a_policy_built_with_no_ceiling_at_all_has_none(self):
        # `parse_policy` never produces this (an absent `--ceiling` is
        # `auto`), but the dataclass allows it and C8/P5 may construct one.
        assert liveness.Policy(ceiling=None).ceiling_seconds(300.0) is None

    def test_ceiling_auto_needs_a_declared_expected_duration(self):
        policy = liveness.parse_policy({})
        assert policy.ceiling_seconds(None) is None  # no guess, no ceiling
        assert policy.ceiling_seconds(300.0) == 900.0
        assert liveness.parse_policy({"ceiling": 60}).ceiling_seconds(None) == 60.0


class TestWatchInterval:
    def test_default_and_clamps(self):
        assert liveness.clamp_watch_interval(None) == 30.0
        assert liveness.clamp_watch_interval(45) == 45.0
        assert liveness.clamp_watch_interval(1) == 5.0      # §8.2's floor
        assert liveness.clamp_watch_interval(10_000) == 300.0  # and its ceiling

    def test_a_non_number_is_a_value_error(self):
        with pytest.raises(ValueError):
            liveness.clamp_watch_interval("often")
        with pytest.raises(ValueError):
            liveness.clamp_watch_interval(True)


# ── §8.4: the bounded progress-stream read ──────────────────────────────

def _write_stream(path, *objects, trailing_newline=True):
    text = "".join(json.dumps(obj) + "\n" for obj in objects)
    if not trailing_newline:
        text = text.rstrip("\n")
    path.write_text(text)
    return path


class TestReadProgressStream:
    def test_fifo_without_writer_is_absent_and_cannot_block_the_watcher(self, tmp_path):
        path = tmp_path / "progress.ndjson"
        os.mkfifo(path)
        sample = liveness.read_progress_stream(str(path))
        assert sample.present is False
        assert sample.last_event is None

    def test_character_device_is_absent(self, tmp_path):
        path = tmp_path / "progress.ndjson"
        path.symlink_to("/dev/null")
        assert liveness.read_progress_stream(str(path)).present is False

    def test_absent_file_is_not_an_error(self, tmp_path):
        sample = liveness.read_progress_stream(str(tmp_path / "nope.ndjson"))
        assert sample.present is False
        assert sample.identity is None and sample.last_event is None

    def test_a_directory_reads_as_absent(self, tmp_path):
        # A lane that made the path a directory (or the daemon losing
        # permission mid-run) must not take the sampler thread down.
        assert liveness.read_progress_stream(str(tmp_path)).present is False

    def test_last_complete_line_and_cadence_hint(self, tmp_path):
        path = _write_stream(
            tmp_path / "p.ndjson",
            {"event": "plan", "expect_next_event_within_s": 45},
            {"event": "candidate", "id": 1},
        )
        sample = liveness.read_progress_stream(str(path))
        assert sample.present is True
        assert sample.last_event == "candidate"
        assert sample.cadence_hint_seconds == 45.0

    def test_a_half_written_last_line_is_never_parsed(self, tmp_path):
        path = _write_stream(
            tmp_path / "p.ndjson", {"event": "plan"}, {"event": "candidate"},
            trailing_newline=False,
        )
        # The producer is mid-write: the newline-terminated `plan` is the
        # last COMPLETE line, and reading the fragment would have lost it.
        assert liveness.read_progress_stream(str(path)).last_event == "plan"

    def test_identity_changes_when_a_line_is_appended(self, tmp_path):
        path = _write_stream(tmp_path / "p.ndjson", {"event": "a"})
        first = liveness.read_progress_stream(str(path))
        second = liveness.read_progress_stream(str(path), previous=first)
        assert second.identity == first.identity  # nothing was written
        _write_stream(tmp_path / "p.ndjson", {"event": "a"}, {"event": "b"})
        third = liveness.read_progress_stream(str(path), previous=second)
        assert third.identity != second.identity
        assert third.last_event == "b"

    def test_a_rewritten_same_length_last_line_still_changes_identity(self, tmp_path):
        # Size alone would miss this; the digest half of the identity is why
        # it does not.
        path = _write_stream(tmp_path / "p.ndjson", {"event": "aa"})
        first = liveness.read_progress_stream(str(path))
        _write_stream(tmp_path / "p.ndjson", {"event": "bb"})
        assert liveness.read_progress_stream(str(path)).identity != first.identity

    def test_the_read_is_bounded_and_still_finds_the_header_hint(self, tmp_path):
        path = tmp_path / "big.ndjson"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "plan", "expect_next_event_within_s": 90}) + "\n")
            payload = "x" * 512
            for i in range(300):  # ~150 KiB, well past the 64 KiB tail
                fh.write(json.dumps({"event": "candidate", "i": i, "pad": payload}) + "\n")
        assert os.path.getsize(path) > liveness.STREAM_TAIL_BYTES
        sample = liveness.read_progress_stream(str(path))
        assert sample.last_event == "candidate"
        # The `plan` header scrolled far out of the 64 KiB tail; the first
        # read looks at the head exactly once to find the hint.
        assert sample.cadence_hint_seconds == 90.0
        # And the hint is carried forward rather than re-read every tick.
        later = liveness.read_progress_stream(str(path), previous=sample)
        assert later.cadence_hint_seconds == 90.0

    def test_the_head_scan_skips_blanks_and_garbage_to_find_the_hint(self, tmp_path):
        path = tmp_path / "big.ndjson"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n")                      # a blank first line
            fh.write("not json at all\n")       # a log line that leaked in
            fh.write(json.dumps([1, 2, 3]) + "\n")   # JSON, but not an object
            fh.write(json.dumps({"event": "header"}) + "\n")  # object, no hint
            fh.write(json.dumps({"event": "plan", "expect_next_event_within_s": 77}) + "\n")
            payload = "y" * 512
            for i in range(200):
                fh.write(json.dumps({"event": "candidate", "i": i, "pad": payload}) + "\n")
        assert liveness.read_progress_stream(str(path)).cadence_hint_seconds == 77.0

    def test_a_big_stream_with_no_hint_anywhere_reads_back_without_one(self, tmp_path):
        # The head scan runs to the end and finds nothing: a producer that
        # publishes no cadence hint at all leaves `--idle-bound auto` on its
        # 300 s floor rather than inventing a number.
        path = tmp_path / "big.ndjson"
        payload = "z" * 512
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(300):
                fh.write(json.dumps({"event": "candidate", "i": i, "pad": payload}) + "\n")
        sample = liveness.read_progress_stream(str(path))
        assert sample.present is True and sample.cadence_hint_seconds is None
        assert liveness.Policy().idle_bound_seconds(sample.cadence_hint_seconds) == 300.0

    def test_a_non_positive_cadence_hint_is_ignored(self, tmp_path):
        path = _write_stream(
            tmp_path / "p.ndjson",
            {"event": "plan", "expect_next_event_within_s": 0},
            {"event": "phase", "expect_next_event_within_s": "soon"},
        )
        assert liveness.read_progress_stream(str(path)).cadence_hint_seconds is None

    def test_garbage_lines_are_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "p.ndjson"
        path.write_text('{"event": "real"}\nnot json at all\n[1, 2, 3]\n')
        sample = liveness.read_progress_stream(str(path))
        # The last line that parses AS AN OBJECT wins; a JSON array is not
        # an event.
        assert sample.last_event == "real"

    def test_a_file_with_no_complete_object_does_not_report_progress(self, tmp_path):
        path = tmp_path / "p.ndjson"
        path.write_text("")
        first = liveness.read_progress_stream(str(path))
        assert first.present is True and first.last_event is None
        path.write_text("partial")
        second = liveness.read_progress_stream(str(path), previous=first)
        assert second.identity is None and second.last_event is None
        path.write_text('{"event":"candidate"}\n')
        assert liveness.read_progress_stream(str(path), previous=second).identity is not None

    def test_appending_an_unfinished_line_keeps_the_complete_event_identity(self, tmp_path):
        path = _write_stream(tmp_path / "p.ndjson", {"event": "candidate"})
        first = liveness.read_progress_stream(str(path))
        with path.open("a") as fh:
            fh.write('{"event":"candidate","partial":')
        second = liveness.read_progress_stream(str(path), previous=first)
        assert second.identity == first.identity
        assert second.last_event == "candidate"
        with path.open("a") as fh:
            fh.write('true}\n')
        third = liveness.read_progress_stream(str(path), previous=second)
        assert third.identity != first.identity

    def test_a_complete_line_exactly_at_the_tail_limit_is_kept(self, tmp_path):
        prefix = b'{"event":"plan","pad":"'
        suffix = b'"}\n'
        line = prefix + b"x" * (liveness.STREAM_TAIL_BYTES - len(prefix) - len(suffix)) + suffix
        assert len(line) == liveness.STREAM_TAIL_BYTES
        path = tmp_path / "exact-tail.ndjson"
        path.write_bytes(line)
        sample = liveness.read_progress_stream(str(path))
        assert sample.last_event == "plan"
        assert sample.identity is not None

    def test_a_cut_json_line_at_the_tail_boundary_is_not_parsed(self, tmp_path):
        fake = json.dumps({"event": "fake"}, separators=(",", ":")).encode() + b"\n"
        prefix = b"x" * 17
        path = tmp_path / "cut-line.ndjson"
        path.write_bytes(prefix + fake + b"z" * (liveness.STREAM_TAIL_BYTES - len(fake)))
        assert os.path.getsize(path) > liveness.STREAM_TAIL_BYTES
        sample = liveness.read_progress_stream(str(path))
        assert sample.last_event is None

    def test_prior_reads_do_not_rescan_the_head_for_a_new_hint(self, tmp_path):
        def fixed_line(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return raw + b" " * (256 - len(raw)) + b"\n"

        path = tmp_path / "carried-head.ndjson"
        tail = b"x" * 70_000
        path.write_bytes(fixed_line({"event": "header"}) + tail)
        first = liveness.read_progress_stream(str(path))
        assert first.cadence_hint_seconds is None
        path.write_bytes(fixed_line({"event": "plan", "expect_next_event_within_s": 90}) + tail)
        second = liveness.read_progress_stream(str(path), previous=first)
        assert second.cadence_hint_seconds is None

    def test_a_head_hint_does_not_overwrite_a_hint_found_in_the_tail(self, tmp_path):
        def fixed_line(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return raw + b" " * (256 - len(raw)) + b"\n"

        tail_hint = json.dumps(
            {"event": "candidate", "expect_next_event_within_s": 90},
            separators=(",", ":"),
        ).encode() + b"\n"
        path = tmp_path / "tail-wins.ndjson"
        path.write_bytes(fixed_line({"event": "plan", "expect_next_event_within_s": 12})
                        + b"x" * 70_000 + b"\n" + tail_hint)
        sample = liveness.read_progress_stream(str(path))
        assert sample.cadence_hint_seconds == 90.0


class TestReadingHelpers:
    def test_resolve_stream_path_goes_through_proc_root(self):
        assert liveness.resolve_stream_path("/proc", 4242, "/run/lane/p.ndjson") == (
            "/proc/4242/root/run/lane/p.ndjson"
        )

    def test_subtree_cpu_seconds_sums_the_subtree(self, tmp_path):
        for pid, ticks in ((11, 100), (12, 50)):
            d = tmp_path / str(pid)
            d.mkdir()
            # proc(5) fields are counted from the LAST ")": index 0 there is
            # `state`, so utime/stime (fields 14/15) are 10 and 11 after it.
            fields = ["0"] * 20
            fields[10], fields[11] = str(ticks), str(ticks)
            (d / "stat").write_text(f"{pid} (a lane) S " + " ".join(fields) + "\n")
        total = liveness.subtree_cpu_seconds([11, 12, 99], str(tmp_path))
        # 99 does not exist — a pid that exited between the walk and the read
        # contributes nothing instead of raising.
        assert total == pytest.approx(2 * (100 + 50) / os.sysconf("SC_CLK_TCK"))
        assert liveness.subtree_cpu_seconds([], str(tmp_path)) is None
        assert liveness.subtree_cpu_seconds([99], str(tmp_path)) is None

    def test_cgroup_io_bytes_sums_every_device(self, tmp_path):
        (tmp_path / "io.stat").write_text(
            "8:0 rbytes=1000 wbytes=2000 rios=1 wios=1\n"
            "8:16 rbytes=30 wbytes=nonsense rios=0 wios=0\n"
        )
        assert liveness.cgroup_io_bytes(str(tmp_path)) == 3030
        assert liveness.cgroup_io_bytes(str(tmp_path / "missing")) is None


# ── §8.4: the state machine, every transition ───────────────────────────

class _Fake:
    """A fake sampler: one `LivenessSample` per call, on a fake clock."""

    def __init__(self, tracker, *, step=10.0):
        self.tracker = tracker
        self.step = step
        self.mono = 0.0
        self.cpu = 0.0
        self.io = 0
        self.stream = None
        self.host_psi = 0.0
        self.slice_psi = 0.0
        self.leaf_psi = None
        self.leaf_high = False
        self.alive = True
        self.changes = []

    def tick(self, n=1, **kw):
        for _ in range(n):
            for key, value in kw.items():
                setattr(self, key, value)
            self.mono += self.step
            change = self.tracker.observe(liveness.LivenessSample(
                mono=self.mono, at=f"2026-09-12T10:{int(self.mono) // 60:02d}:"
                                   f"{int(self.mono) % 60:02d}Z",
                elapsed_seconds=self.mono, cpu_seconds_total=self.cpu,
                io_bytes_total=self.io, stream=self.stream,
                host_psi_full_avg10=self.host_psi, slice_psi_full_avg10=self.slice_psi,
                leaf_psi_full_avg10=self.leaf_psi, leaf_memory_high_applied=self.leaf_high,
                subtree_alive=self.alive,
            ))
            if change is not None:
                self.changes.append(change)
        return self.tracker.state


def _tracker(**policy_args):
    return liveness.LivenessTracker(
        liveness.parse_policy(policy_args), started_at="2026-09-12T10:00:00Z",
        expected_duration_seconds=policy_args.pop("_expected", None),
    )


def _stream(identity, event=None, hint=None):
    return liveness.StreamSample(
        path="/run/lane/p.ndjson", present=True, identity=identity, last_event=event,
        cadence_hint_seconds=hint,
    )


class TestStateMachine:
    def test_partial_stream_bytes_cannot_keep_a_silent_lane_alive(self, tmp_path):
        path = _write_stream(tmp_path / "p.ndjson", {"event": "candidate"})
        tracker = _tracker(idle_bound=20, progress_stream=str(path))
        fake = _Fake(tracker)
        first = liveness.read_progress_stream(str(path))
        fake.tick(stream=first)
        with path.open("a") as fh:
            fh.write('{"event":"candidate","partial":')
        partial = liveness.read_progress_stream(str(path), previous=first)
        assert fake.tick(2, stream=partial) == "stalled"
        assert tracker.verdict == "reported"

    def test_a_busy_session_stays_ok(self):
        t = _tracker(idle_bound=100)
        fake = _Fake(t)
        for _ in range(30):
            fake.cpu += 5.0  # 5 s of CPU every 10 s tick: unambiguously alive
            fake.tick()
        assert t.state == "ok" and t.verdict == "none" and t.reason is None
        assert fake.changes == []
        assert t.readings == 30

    def test_silence_becomes_stalled_exactly_at_the_bound(self):
        t = _tracker(idle_bound=100)
        fake = _Fake(t)
        assert fake.tick(9) == "ok"      # 90 s idle
        assert t.liveness_block()["idle_for_seconds"] == 80.0  # first tick has no dt
        assert fake.tick(2) == "stalled"  # crosses 100 s
        assert fake.changes == ["stalled"]
        assert t.verdict == "reported"   # default policy never kills
        assert "no activity for" in t.reason and "idle bound 100.0s" in t.reason

    def test_io_growth_alone_keeps_a_session_alive(self):
        t = _tracker(idle_bound=50)
        fake = _Fake(t)
        for _ in range(20):
            fake.io += 4096
            fake.tick()
        assert t.state == "ok"
        assert t.liveness_block()["io_bytes"] == 20 * 4096

    def test_cpu_growth_under_the_threshold_is_not_activity(self):
        # 0.2 s of CPU per 10 s tick is 0.6 s over the trailing 30 s — under
        # §8.4's 1 s bar, so it is NOT activity and the clock keeps running.
        t = _tracker(idle_bound=100)
        fake = _Fake(t)
        for _ in range(15):
            fake.cpu += 0.2
            fake.tick()
        assert t.state == "stalled"

    def test_a_stream_line_alone_keeps_a_session_alive(self):
        t = _tracker(idle_bound=50, progress_stream="/run/lane/p.ndjson")
        fake = _Fake(t)
        for i in range(20):
            fake.tick(stream=_stream(f"{i}:aa", event="candidate"))
        assert t.state == "ok"
        block = t.liveness_block()
        assert block["stream"]["last_event"] == "candidate"
        assert block["stream"]["path"] == "/run/lane/p.ndjson"
        assert block["last_activity_at"] == block["stream"]["last_event_at"]

    def test_busy_but_silent_is_runaway_not_stalled(self):
        # D-17 layer 2's busy loop: CPU keeps the ACTIVITY clock reset, so
        # this is never `stalled`; the CADENCE clock (stream lines only)
        # crosses the bound and the verdict is `runaway`.
        t = _tracker(idle_bound=50, progress_stream="/run/lane/p.ndjson")
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="candidate"))
        for _ in range(10):
            fake.cpu += 5.0
            fake.tick()
        assert t.state == "runaway"
        assert t.verdict == "reported"
        assert "no progress-stream event for" in t.reason
        assert t.liveness_block()["idle_for_seconds"] == 0.0  # genuinely alive

    def test_a_silent_stream_with_io_but_no_cpu_is_neither_stalled_nor_runaway(self):
        # I/O keeps the ACTIVITY clock reset (so: not stalled) while the
        # cadence clock passes the bound with no CPU growth (so: not
        # runaway either — "busy but silent" is specifically about CPU).
        # A lane writing output but publishing no events is ok, and saying
        # otherwise would kill a legitimately slow writer.
        t = _tracker(idle_bound=50, progress_stream="/run/lane/p.ndjson")
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="candidate"))
        for _ in range(10):
            fake.io += 4096
            fake.tick()
        assert t.state == "ok"

    def test_runaway_is_unreachable_without_a_progress_stream(self):
        # No stream = no cadence signal = nothing to be silent on. A busy
        # session with no stream is `ok`, deliberately (module docstring).
        t = _tracker(idle_bound=50)
        fake = _Fake(t)
        for _ in range(20):
            fake.cpu += 5.0
            fake.tick()
        assert t.state == "ok"

    def test_a_terminal_stream_event_with_a_live_subtree_is_hung(self):
        t = _tracker(idle_bound=1000, progress_stream="/run/lane/p.ndjson")
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="verdict"))
        assert t.state == "ok"          # inside the 30 s grace
        fake.tick(2)                    # +20 s
        assert t.state == "ok"
        fake.tick()                     # 30 s after the terminal event
        assert t.state == "hung"
        assert "'verdict'" in t.reason and "still alive" in t.reason

    def test_a_terminal_stream_event_whose_subtree_exits_is_not_hung(self):
        t = _tracker(idle_bound=1000, progress_stream="/run/lane/p.ndjson")
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="end"))
        fake.tick(5, alive=False)
        assert t.state == "ok"

    def test_over_ceiling_beats_every_other_state(self):
        t = liveness.LivenessTracker(
            liveness.parse_policy({"ceiling": 60, "on_stall": "kill"}),
            started_at="2026-09-12T10:00:00Z",
        )
        fake = _Fake(t)
        assert fake.tick(6) == "ok"       # elapsed 60 s, not yet OVER
        assert fake.tick() == "over_ceiling"
        assert "over the ceiling of 60.0s" in t.reason
        assert t.kill_requested is True   # §8.4: over ceiling IS killable

    def test_ceiling_auto_comes_from_the_declared_expected_duration(self):
        t = liveness.LivenessTracker(
            liveness.parse_policy({}), started_at="2026-09-12T10:00:00Z",
            expected_duration_seconds=20.0,
        )
        assert t.watch_block()["policy"]["ceiling_s"] == 60.0
        fake = _Fake(t)
        assert fake.tick(7) == "over_ceiling"

    def test_throttled_needs_both_pressure_and_an_applied_memory_high(self):
        t = _tracker(idle_bound=1000)
        fake = _Fake(t)
        fake.tick(leaf_psi=44.0, leaf_high=False)
        assert t.state == "ok"           # pressure with no memory.high: not ours
        fake.tick(leaf_psi=18.0, leaf_high=True)
        assert t.state == "ok"           # under the 20.0 bar
        fake.tick(leaf_psi=44.0, leaf_high=True)
        assert t.state == "throttled"
        assert t.verdict == "reported"
        # Never killed, even under `kill` — §8.4 says so in as many words.
        t.policy = liveness.parse_policy({"on_stall": "kill"})
        assert t.kill_requested is False

    def test_the_idle_clock_pauses_under_slice_and_host_pressure(self):
        t = _tracker(idle_bound=100)
        fake = _Fake(t)
        fake.tick(5, slice_psi=40.0)     # 50 s of pressure
        block = t.liveness_block()
        assert block["idle_for_seconds"] == 0.0
        assert block["paused_for_seconds"] == 40.0
        assert block["pause_reason"] == "slice-psi"
        fake.tick(5, slice_psi=0.0, host_psi=40.0)
        assert t.liveness_block()["pause_reason"] == "host-psi"
        assert t.state == "ok"           # 100 s of wall time, none of it idle
        fake.tick(12, host_psi=0.0)
        assert t.state == "stalled"      # the clock resumes when pressure lifts

    def test_activity_resets_the_pause_accounting_too(self):
        t = _tracker(idle_bound=100)
        fake = _Fake(t)
        fake.tick(3, slice_psi=40.0)
        assert t.liveness_block()["paused_for_seconds"] == 20.0
        fake.cpu += 5.0
        fake.tick()
        assert t.liveness_block()["paused_for_seconds"] == 0.0

    def test_a_state_change_is_reported_once(self):
        t = _tracker(idle_bound=50)
        fake = _Fake(t)
        fake.tick(12)
        assert fake.changes == ["stalled"]  # not once per tick after it

    def test_recovery_from_stalled_is_a_state_change_back_to_ok(self):
        t = _tracker(idle_bound=50)
        fake = _Fake(t)
        assert fake.tick(8) == "stalled"
        fake.cpu += 5.0
        fake.tick()
        assert t.state == "ok" and t.verdict == "none" and t.reason is None
        assert fake.changes == ["stalled", "ok"]

    def test_a_shrinking_subtree_never_counts_as_negative_cpu(self):
        t = _tracker(idle_bound=1000)
        fake = _Fake(t)
        fake.tick(cpu=100.0)
        fake.tick(cpu=140.0)
        fake.tick(cpu=10.0)   # a big pid exited and took its time with it
        fake.tick(cpu=12.0)
        assert t.liveness_block()["cpu_seconds"] == 42.0  # 40 grown, then 2

    def test_cpu_at_the_exact_window_cutoff_is_retained(self):
        t = _tracker(idle_bound=1000)
        t.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:00:00Z", elapsed_seconds=0.0,
            cpu_seconds_total=0.0,
        ))
        t.observe(liveness.LivenessSample(
            mono=10.0, at="2026-09-12T10:00:10Z", elapsed_seconds=10.0,
            cpu_seconds_total=1.0,
        ))
        t.observe(liveness.LivenessSample(
            mono=liveness.CPU_WINDOW_SECONDS,
            at="2026-09-12T10:00:30Z", elapsed_seconds=liveness.CPU_WINDOW_SECONDS,
            cpu_seconds_total=1.0,
        ))
        assert t.cpu_seconds_recent == pytest.approx(1.0)

    def test_the_cpu_window_never_pops_its_sole_entry_at_the_cutoff(self, monkeypatch):
        # A zero-width window makes the sole-entry guard observable without a
        # sleep or a fabricated second sample outside the window contract.
        monkeypatch.setattr(liveness, "CPU_WINDOW_SECONDS", 0.0)
        t = _tracker(idle_bound=1000)
        t.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:00:00Z", elapsed_seconds=0.0,
            cpu_seconds_total=0.0,
        ))
        t.observe(liveness.LivenessSample(
            mono=1.0, at="2026-09-12T10:00:01Z", elapsed_seconds=1.0,
            cpu_seconds_total=0.0,
        ))
        assert t.cpu_seconds_recent == pytest.approx(0.0)

    def test_omitted_leaf_evidence_does_not_imply_memory_high(self):
        t = _tracker(idle_bound=1000)
        t.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:00:00Z", elapsed_seconds=0.0,
            leaf_psi_full_avg10=44.0,
        ))
        assert t.state == liveness.STATE_OK

    def test_omitted_subtree_alive_defaults_alive_for_terminal_hang_detection(self):
        t = _tracker(idle_bound=1000, progress_stream="/run/lane/p.ndjson")
        t.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:00:00Z", elapsed_seconds=0.0,
            stream=_stream("1:aa", event="end"),
        ))
        change = t.observe(liveness.LivenessSample(
            mono=liveness.HUNG_GRACE_SECONDS,
            at="2026-09-12T10:00:30Z", elapsed_seconds=liveness.HUNG_GRACE_SECONDS,
            stream=_stream("1:aa", event="end"),
        ))
        assert change == liveness.STATE_HUNG

    def test_exact_cpu_growth_threshold_counts_as_activity(self):
        t = _tracker(idle_bound=10)
        fake = _Fake(t)
        fake.tick(cpu=0.0)
        fake.tick(cpu=1.0)
        assert t.state == liveness.STATE_OK
        assert t.idle_for_seconds == 0.0

    @pytest.mark.parametrize("pressure", ["slice_psi", "host_psi"])
    def test_exact_psi_pause_threshold_does_not_pause(self, pressure):
        t = _tracker(idle_bound=1000)
        fake = _Fake(t)
        fake.tick(**{pressure: 5.0})
        fake.tick(**{pressure: 5.0})
        assert t.paused_for_seconds == 0.0
        assert t.idle_for_seconds == pytest.approx(10.0)
        assert t.pause_reason is None

    def test_enforced_observation_returns_none_not_a_false_state_change(self):
        t = _tracker(idle_bound=1, on_stall="kill")
        fake = _Fake(t)
        fake.tick(2)
        assert t.state == liveness.STATE_STALLED
        t.record_kill([4242])
        assert t.observe(liveness.LivenessSample(
            mono=30.0, at="2026-09-12T10:00:30Z", elapsed_seconds=30.0,
        )) is None

    def test_exact_leaf_psi_threshold_does_not_report_throttling(self):
        t = _tracker(idle_bound=1000)
        t.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:00:00Z", elapsed_seconds=0.0,
            leaf_psi_full_avg10=20.0, leaf_memory_high_applied=True,
        ))
        assert t.state == liveness.STATE_OK

    def test_exact_silent_time_bound_evaluates_runaway(self):
        t = _tracker(
            idle_bound=10, progress_stream="/run/lane/p.ndjson",
        )
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="candidate"))
        fake.tick(cpu=1.0)
        assert t.state == liveness.STATE_RUNAWAY


class TestEnforcement:
    def test_report_never_kills(self):
        t = _tracker(idle_bound=50, on_stall="report")
        assert _Fake(t).tick(8) == "stalled"
        assert t.kill_requested is False
        assert t.verdict == "reported"

    def test_kill_is_requested_once_and_recorded(self):
        t = _tracker(idle_bound=50, on_stall="kill")
        assert _Fake(t).tick(8) == "stalled"
        assert t.kill_requested is True
        t.record_kill([4242, 4243])
        assert t.verdict == "killed"
        assert "cgroup kill enforced for 2 tracked pid(s)" in t.reason
        assert t.kill_requested is False  # enforced: never twice

    def test_a_killed_verdict_is_not_rewritten_by_later_ticks(self):
        t = _tracker(idle_bound=50, on_stall="kill")
        fake = _Fake(t)
        fake.tick(8)
        t.record_kill([4242])
        fake.cpu += 50.0           # the subtree's last gasps
        fake.tick(3)
        assert t.state == "stalled" and t.verdict == "killed"

    def test_a_refused_kill_is_reported_never_claimed(self):
        t = _tracker(idle_bound=50, on_stall="kill")
        _Fake(t).tick(8)
        t.record_kill_refused("no-token-in-shared-scope")
        assert t.verdict == "reported"   # NOT "killed" — nothing died
        assert "kill-refused:no-token-in-shared-scope" in t.reason
        assert t.kill_requested is False

    def test_runaway_is_never_killed_until_it_is_over_ceiling(self):
        t = liveness.LivenessTracker(
            liveness.parse_policy({
                "idle_bound": 50, "on_stall": "kill",
                "progress_stream": "/run/lane/p.ndjson", "ceiling": 200,
            }),
            started_at="2026-09-12T10:00:00Z",
        )
        fake = _Fake(t)
        fake.tick(stream=_stream("1:aa", event="candidate"))
        for _ in range(10):
            fake.cpu += 5.0
            fake.tick()
        assert t.state == "runaway" and t.kill_requested is False  # §8.4
        for _ in range(11):
            fake.cpu += 5.0
            fake.tick()
        assert t.state == "over_ceiling" and t.kill_requested is True


class TestBlocks:
    def test_the_liveness_block_is_the_contract_shape(self):
        t = _tracker(progress_stream="/run/lane/p.ndjson")
        _Fake(t).tick(stream=_stream("1:aa", event="candidate", hint=45.0))
        block = t.liveness_block()
        assert set(block) == {
            "last_activity_at", "idle_for_seconds", "cpu_seconds", "cpu_seconds_recent",
            "io_bytes", "stream", "paused_for_seconds", "pause_reason",
        }
        assert set(block["stream"]) == {
            "path", "last_event_at", "last_event", "cadence_hint_seconds",
        }
        assert block["stream"]["cadence_hint_seconds"] == 45.0

    def test_the_watch_block_is_the_contract_shape(self):
        t = _tracker(idle_bound=50, ceiling=900, on_stall="kill",
                     progress_stream="/run/lane/p.ndjson")
        _Fake(t).tick(2)
        block = t.watch_block()
        assert set(block) == {"state", "verdict", "reason", "readings", "policy"}
        assert block["policy"] == {
            "idle_bound_s": 50.0, "ceiling_s": 900.0, "on_stall": "kill",
            "progress_stream": "/run/lane/p.ndjson",
        }
        assert block["readings"] == 2

    def test_an_auto_idle_bound_follows_the_stream_hint_into_the_block(self):
        t = _tracker(progress_stream="/run/lane/p.ndjson")
        assert t.watch_block()["policy"]["idle_bound_s"] == 300.0
        _Fake(t).tick(stream=_stream("1:aa", event="phase", hint=400.0))
        assert t.watch_block()["policy"]["idle_bound_s"] == 1200.0
