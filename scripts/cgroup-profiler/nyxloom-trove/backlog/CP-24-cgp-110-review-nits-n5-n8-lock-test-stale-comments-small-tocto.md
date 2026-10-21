---
kind: backlog-entry
schema_version: 1
id: CP-24
title: "CGP-110 review NITs N5-N8: held-lock test does not pin S9, stale comments, small TOCTOU, misleading stop message"
status: open
type: "bugfix"
severity: "low"
component: "daemon"
context_estimate: "small"
provenance: "CGP-110 review round r4 (review-CGP-110.md, section r4 NITs), 2026-10-21; branch cgp-110-20261021 at 6ea5ef2fb (not yet merged)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 3
filed_date: "2026-10-21"
---

## Source

The r4 review of CGP-110 (branch `cgp-110-20261021`, commit `6ea5ef2fb`, "fix(cgprofile): bounded finalize lock; guard
liveness kill and DAMON paths after close (review CGP-110 r3)") returned READY with four NITs, none blocking, and asked that
they be filed. **The branch is not yet merged**: the `serve.py` and test locations below are as of that commit and may move
before it lands. Review text: `review-CGP-110.md`, "NITs (backlog, none blocking)" in the program directory.

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
