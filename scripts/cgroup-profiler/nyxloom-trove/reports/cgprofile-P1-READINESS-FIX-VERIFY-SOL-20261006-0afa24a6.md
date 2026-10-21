# P1 readiness repairs: scoped fix verification — 2026-10-06

**Verdict: ACCEPT for the B1/B2/B3 test-readiness fixes.** This is an unnumbered continuation of the scoped review, not a new full P1 daemon review or release clearance.

## Exact scope and evidence

- Candidate HEAD: `0afa24a6e4a206f260df848d9650d9bc849745f3`; tree: `296b2ed27646bd06277e6bac260876bdfbfffa70`. Both were read from the clean candidate worktree `.worktrees/rg55-p1-review-repairs-20261005`. The separate review worktree was detached at this exact commit for inspection; only this report was added there.
- Reviewed the cumulative test-only repair through `e520b855`, `b2a270ee`, and `0afa24a6`, with emphasis on the exact `b2a270ee..0afa24a6` diff in `tests/test_serve_watch.py` and `tests/test_serve_socket_carrier.py`. Compared it with the prior scoped REJECT report and read `lib/serve.py::_bind`, `_accept_loop`, and `_handle_connection` to verify what the socket-ready state means.
- The candidate's `.run-gate/history.json` independently records eligible, clean-tree PASS/exit 0 for `r0-r1` (164.107 s, started 03:18:34Z) and `r3` (15.216 s, started 03:22:12Z), both at the full candidate SHA. The controller reports 2,369 R0–R1 passes with 100% line/branch coverage, 7/7 R3 canaries rejected, and doctor exit 0 with no failures. This review did not run tests, gates, mutation, containers, or daemon operations.

## B1 — token-child lifetime and readiness: closed

The child writes `b'R'` and then blocks reading a parent-controlled release pipe. The parent requires exactly `b'R'`, keeps the release write end open through the daemon's first token-owner `/proc` scan and the no-signal assertions, and releases/reaps the child in `finally`. Thus parent scheduling delay cannot let a timed child expire before those checks. The test still asserts the token PID was found, forbids a numeric `os.kill`, confirms the child is live, and checks the `reported` / `kill-refused:unplaced-token-subtree` result. The earlier Popen-failure cleanup remains inside `finally`; no assertion was weakened.

## B2 — synthetic socket connection contaminating `accepted`: closed

`_wait_for_socket(server, thread)` now observes `server._sock` instead of opening a connection. `SessionServer._bind()` assigns `_sock` only after `bind()`, `listen(16)`, and `settimeout()` complete. Each caller starts its accept thread before waiting. If that thread exits before publishing `_sock`, the helper raises; ordinary scheduling delay merely extends the wait. The helper creates no peer and cannot invoke `_peer_uid`, set `TestPeerCredentials`' `accepted` event, consume the serial accept loop, or dispatch a request. The event in `test_refused_peer_reply_survives_client_write_after_accept` is therefore reached only by that test's real client. Publication occurs before the permission-reassertion calls and before the accept loop starts waiting; the socket is already listening and can queue the test client, whose response assertions still expose a server that fails to accept or answer it.

This closes both the e7a6 pathname-before-`listen()` gap and the b2a synthetic-peer false-positive path without changing the behavioral request/response assertions. The helper has no local wall-clock failure threshold; a thread that stays alive but never publishes `_sock` would wait for the outer gate watchdog. That diagnostic limit is not a false PASS or an ordinary-load-dependent verdict in this scoped review.

## B3 — second pipe allocation: closed

The second `os.pipe()` is now inside a `try` that catches `BaseException`, closes both descriptors from the first pipe, and re-raises. If the second allocation succeeds, the existing outer `finally` owns all four descriptors, including when `Popen` or a later assertion fails. The previously identified descriptor leak on second-allocation failure is gone.

## Remaining holds

The old `e7a6b759` R2 stopped in its baseline after 1,924 passes / one failure and judged no candidates. This `0afa24a6` tree has not yet been R2-judged, and the registered full gate remains pending. P3 live daemon/carrier/placement/restoration probes and measured DAMON overhead also remain open. This scoped ACCEPT does not approve merging the full P1 package, releasing it, or activating the daemon.
