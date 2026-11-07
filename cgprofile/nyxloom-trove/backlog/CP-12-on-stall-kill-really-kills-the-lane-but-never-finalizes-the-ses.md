---
kind: backlog-entry
schema_version: 1
id: CP-12
title: "on-stall kill really kills the lane but never finalizes the session -- watch's own 'exactly one end' promise never resolves, the session holds its slot forever"
status: fixed
type: "bugfix"
severity: "medium"
provenance: "RG-55 wave, cgprofile-P6-FOLLOWUPS session 8, found via the REPORT's live probe plan (probe d, watch/on-stall-kill) against the real daemon, root-caused and fixed same session, 2026-09-12"
filed_by: "cgprofile-P6-FOLLOWUPS session 8"
filed_date: "2026-09-12"
closed_date: "2026-09-12"
closed_reason: "_finalize_after_kill wired into _enforce_stall_kill, 8067cc03 + live re-probe confirmed the watch stream now emits 'end' immediately after an enforced kill"
---

## Observed mechanism and reproduction

A live probe against the REAL daemon (RG-55 P6 session 8, `cgprofile:local`
built from `241122b6`, `cgprofile-p6-probe` on a scratch mount) started a
session with `--token ... --idle-bound 20 --on-stall kill`, tagged a real
`docker exec -e RUN_GATE_PROFILE_SESSION=<token>` process inside the target
container's cgroup, then `watch`ed it over the socket carrier. At t=20s the
daemon correctly judged `stalled`, correctly resolved the tagged pid via
`SubtreeResolver`, and correctly SIGKILLed it (`"SIGKILL sent to 1 pid(s)
of the token subtree"`, verdict `killed`; the pid was confirmed gone via
`docker exec <target> ps`). The `watch` stream kept right on sending
ordinary `reading` lines every `--watch-interval` for the next two minutes
(30+ observed) with no `end` line — `docs/PROTOCOL.md` §2's own invariant
("the last line is always `{"event": "end"}`") never resolved. `ctl status`
confirmed the session stayed live indefinitely (`elapsed_seconds` still
climbing, `finished` never set) — the same outcome for a real cgroup-leaf
kill (`--place`) as for the pid-loop fallback.

Root cause: `lib/serve.py`'s `_enforce_stall_kill` calls
`sess.watch.record_kill(...)` (setting `verdict = VERDICT_KILLED`) on a
successful kill, via either branch (`sess.placement.kill()`'s
`cgroup.kill` write, or the per-pid `SIGKILL` loop), but never calls
`_finalize_session_locked` — the ONLY method that sets `sess.finished =
True` / `sess.stop_event`. Before this fix, only `handle_stop` (an
explicit consumer `stop`), daemon shutdown, and the session-loop's own
crash path ever finalized a session. `_stream_watch`'s own epilogue
already special-cases `sess.watch.verdict == VERDICT_KILLED -> reason =
"killed"` for the `end` line — that branch existed but was unreachable in
production, because nothing upstream of it ever made `sess.finished` true
after an enforced kill. D-28 (`DESIGN.md` §4.15a) reads "`killed` -> the
lane's own stall-exit path" as the whole of a consumer's obligation, which
only holds if the session (and hence its one `watch` stream) actually
ends.

Impact: a killed lane keeps its session slot (`--max-sessions`) forever,
the sampler thread keeps polling a cgroup whose lane died, and any
consumer trusting the documented "exactly one end, last" `watch` invariant
(rather than special-casing an early bail on a `killed` verdict line) never
sees its stream close.

The existing test suite did not catch this because both tests that
exercise a REAL enforced kill
(`test_serve_watch.py::TestRealSubtreeEnforcement::test_a_silent_subtree_is_stalled_and_killed_under_kill`,
`test_serve_watch.py::TestWatchStream::test_the_end_reason_is_killed_after_an_enforced_kill`)
always called `stop` themselves right after the kill, regardless of
whether the daemon's own enforcement already ended the session —
`test_the_end_reason_is_killed_after_an_enforced_kill` in particular
bypasses `_enforce_stall_kill` entirely (`sess.watch.record_kill([4242])`
called directly on the tracker) and only proves the END LINE's reason text
is right once the session HAS ended some other way, never that an
enforced kill is what ends it.

## Fix

`_enforce_stall_kill` (`lib/serve.py`) now calls a new
`_finalize_after_kill(sess)` right after each successful `record_kill(...)`
(the placed-leaf `cgroup.kill` branch and the pid-loop branch) — never
after `record_kill_refused` (a refused kill leaves a lane still running,
so ending the session there would be the lie the refusal path's own
docstring already warns against). `_finalize_after_kill` acquires the
server-wide lock and calls `_finalize_session_locked(sess,
aborted_reason=None)` guarded by `if not sess.finished`, then runs
retention — the same sequence `handle_stop` already runs, just triggered
by the daemon's own enforcement instead of a consumer request.
`_enforce_stall_kill` always runs on the session's OWN sampler thread
(`_session_loop`), exactly like the pre-existing crash-path call to
`_finalize_session_locked` from that same thread, so the `sess.thread is
not threading.current_thread()` join-guard already inside it skips the
self-join; `self._lock` is an `RLock`, so no new deadlock risk.

Two pre-existing tests needed real-but-inert `summary_acc`/`rundir`
scaffolding to survive the new finalize call
(`tests/test_serve_watch.py::TestKillTargets`'s bare `_Session` factory
gained an opt-in `tmp_path` that builds a real, empty
`SummaryAccumulator`/`RunDir` for the two tests that exercise the full
`_enforce_stall_kill`, not just `_kill_targets`), and one test
(`tests/test_serve_placement.py::TestPlacedKill::test_a_placed_session_dies_by_cgroup_kill_not_by_pid`)
had its post-hoc `cgroup.kill` file read replaced with a live write-spy
(the finalize this fix adds now `rmdir`s the leaf, via
`placement.release()`, in the SAME call) plus new assertions that the
session actually finished and the leaf is actually gone — a strictly
stronger proof than the file read it replaces.

## Proof

New coverage, all green (registered r0-r1 lane, `tools/gate.sh coverage`:
1336 passed, 100% line+branch on every one of the 23 modules):
- `TestRealSubtreeEnforcement::test_a_silent_subtree_is_stalled_and_killed_under_kill`
  now asserts `finished_before_stop is True` (captured by polling
  `sess.finished` BEFORE the test's own explicit `stop` call);
  `test_the_same_lane_under_report_is_only_reported` asserts the same
  field is `False` (an unenforced `report` verdict must never
  auto-finalize).
- `TestPlacedKill::test_a_placed_session_dies_by_cgroup_kill_not_by_pid`
  asserts `sess.finished is True` and the leaf directory no longer exists
  after `_enforce_stall_kill` returns.
- `TestKillTargets::test_finalize_after_kill_is_a_no_op_once_the_session_already_finished`
  covers the idempotency guard (`if not sess.finished`) the live call
  graph never exercises twice on its own.
- Live re-probe (session 8, after the fix, `cgprofile:local` rebuilt from
  the fix commit) against a fresh real target/token: verdict `killed`,
  and the watch stream's VERY NEXT line — same timestamp — is
  `{"event": "end", "session": "...", "reason": "killed"}`. Full transcript
  in the REPORT's "Live probes (session 8)" section.

## Provenance

RG-55 wave, cgprofile-P6-FOLLOWUPS session 8, found via the REPORT's live
probe plan (probe (d), watch/on-stall-kill) against the real daemon,
root-caused and fixed same session, 2026-09-12.
