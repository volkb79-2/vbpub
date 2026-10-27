---
kind: backlog-entry
schema_version: 1
id: CP-27
title: "CGP-110 review NITs N5-N8: held-lock test does not pin S9, stale comments, small TOCTOU, misleading stop message"
status: open
type: "bugfix"
severity: "low"
component: "daemon"
context_estimate: "small"
provenance: "CGP-110 review round r4 (controller report 2026-10-21, not in repo), 2026-10-21; branch cgp-110-20261021 at 6ea5ef2fb (merged into the 1.1.0 wave branch 2026-10-27)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 3
filed_date: "2026-10-21"
---

## Source

The r4 review of CGP-110 (branch `cgp-110-20261021`, commit `6ea5ef2fb`, "fix(cgprofile): bounded finalize lock; guard
liveness kill and DAMON paths after close (review CGP-110 r3)") returned READY with four NITs, none blocking, and asked that
they be filed. The branch was merged into the 1.1.0 wave branch on 2026-10-27; the `serve.py` and test locations below are as of that commit and have moved. Review text: controller report 2026-10-21 (not in repo), r4 review of CGP-110, section "NITs (backlog, none blocking)"; the four NITs are restated in full below.

## The four NITs (grouped; each could be split)

- **N5 (test strength).** `test_forced_finalize_with_a_worker_holding_the_session_lock_forever_returns` would also pass on the
  previous commit, because the old probe was already bounded. The real S9 defect (the lock freed after the probe, then retaken
  before the unbounded block) is only fixed structurally. Add a lock proxy or counter that asserts exactly one acquire per
  finalize, so the test goes red if the old shape returns.
- **N6 (stale comments).** The "cannot return False" comments at `serve.py` ~1499 and ~1938 (own-thread callers) are stale:
  the bounded `sess.lock` acquire can now fail if another thread holds the lock for 10 s. The return value is ignored there;
  log the failure or document that it is ignored.
- **N7 (small TOCTOU, accept or close).** A small window remains between the pre-kill `closed` check and `placement.kill()`.
  The failure mode is a failed `cgroup.kill` write on a removed leaf, and `record_kill` after close touches only the in-memory
  watch. The reviewer's disposition was "accept"; record it here so the decision is explicit and revisit only if the failure
  shows up in the daemon log.
- **N8 (misleading message).** When the lock is held by a short-lived verb on a non-wedged session, `stop` reports "worker did
  not exit" although the worker has exited. Make the message distinguish "the session lock is busy" from "the worker did not
  exit".

## Oracles

- N5: with a lock proxy, a forced finalize acquires `sess.lock` exactly once; reintroducing the free-then-retake shape fails.
- N6: a held lock for the full timeout in an own-thread caller produces a log line (or the comment states the ignored return).
- N7: either a test pins the accepted behaviour (a failed kill write on a removed leaf is swallowed and logged), or the entry is
  closed as "accepted" with the reviewer's reasoning.
- N8: `stop` against a session whose lock is held briefly by another verb does not print "worker did not exit".

## Additional MINOR findings from the 2026-10-27 wave review (none blocking)

- **N9.** `_finalize_session_locked` sets `closed = True` and does not restore state if `add_sample`, `finalize` or
  `placement.release()` raises: the session is then neither sampled nor finalized and holds its slot until restart recovery.
  Wrap the section in try/finally or define the failure disposition.
- **N10.** `_disable_damon_for_session` clears the DAMON session under the lock but closes it after dropping the lock. A forced
  finalization landing in that gap reads `damon_cleanup` as None, so the Summary omits it, and the late close sets
  `sess.damon_cleanup` after the manifest was written. The B3 "never invisible" claim has a small hole on the forced path.
- **N11.** In the forced path `thread.join` (10 s) and the `sess.lock` acquire (10 s) both run under the daemon-wide lock: up to 20 s
  per call with other verbs blocked (bounded, not hung). Consider joining outside the lock (as `stop` now does) for shutdown too.
- **N12.** `cgprofile.py` calls `daemon_slice_problem` twice (cosmetic double read); a stray blank line at `serve.py` near line 138.

### 2026-10-27 (pre-release review, branch fable-cgprofile-20261027)

- N12: the stray blank line in `lib/serve.py` is removed. The double `daemon_slice_problem` read in `cmd_serve`
  (`daemon_slice_bounded` then `daemon_slice_problem` for the message) is KEPT on purpose: `daemon_slice_bounded`
  is the monkeypatch seam four existing tests use to skip the guard; collapsing it to one call means re-pointing
  those tests. Cosmetic, accepted. N5-N11 are unchanged and still open.
