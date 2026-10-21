# P1 readiness fixes: scoped verification — 2026-10-06

**Verdict: REJECT for the two-fix set.** The token-child repair closes the prior B1 finding. The socket-readiness probe introduces a new false-positive path in a peer-credential test. This is an unnumbered test-only fix verification, not another full daemon review or P1 merge/release clearance.

## Reviewed identity and evidence

- Candidate: `b2a270ee976965e6bb7b5861c9e295aa2b00e6dc`, tree `a53441717f2304d0d20f2e2c64d302d809fc7d59`, checked out in `.worktrees/rg55-p1-readiness-review-20261006`. The review worktree had no tracked changes before this report.
- Scope: cumulative test-only delta `e7a6b759..b2a270ee` through `e520b855` and `b2a270ee`, limited to `tests/test_serve_socket_carrier.py::_wait_for_socket` and `tests/test_serve_watch.py::TestRealSubtreeEnforcement::test_unreachable_pid_is_reported_and_the_lane_remains_live`.
- Context: canonical `nyxloom/reference/AUTHORING.md`, P1 round-6 review, prior scoped socket-readiness review, previous B1 rejection, target tests, and the daemon's `_accept_loop`/`_handle_connection` and token-owner resolution.
- Caller-supplied exact-tree short gates: R0–R1 PASS (2,369 passed; 100% line and branch, 7,836 statements / 2,886 arcs; 169.2 s), R3 PASS (7/7 canaries rejected; 13.3 s), and doctor exit 0 (9 OK, two expected warnings, two info). These do not override a false-positive behavioral oracle. No tests, gates, containers, daemon operations, source edits, merges, or releases were performed in this review.

## Prior B1 — closed for the token-child test

`test_serve_watch.py` now passes the child the ready write end and release read end. The child writes `b'R'` and blocks on `os.read(release_read, 1)`; the parent requires exactly `b'R'` and retains the release write end through the initial `SubtreeResolver.refresh()` and all no-signal assertions. Parent scheduling delay after readiness therefore cannot make the child finish a timed sleep before the scan. EOF fails the exact-byte assertion instead of masquerading as readiness.

Once both pipes exist and execution enters the `try`, the `finally` path covers `Popen` failure (both pipes close), early child exit (its `poll()` reaps it and all descriptors close), server/start/assertion failure (the parked sampler is released, an existing session is stopped, and the child is released or killed and reaped), and normal success. A broken pipe during release is tolerated; if the child does not exit after release, it is killed and reaped. The test still asserts that the token PID was found, forbids a numeric `os.kill`, checks the child remains live, and requires the `reported` / `kill-refused:unplaced-token-subtree` verdict. This closes the specific B1 lifetime, EOF, and `Popen`-descriptor defects. The unchanged bounded thread joins and a stalled interpreter before its ready byte are outside this fix verification.

## New blocker B2 — the socket probe contaminates an acceptance event

`_wait_for_socket` now opens and closes a real AF_UNIX connection. A successful `connect()` proves the socket reached `listen()`, so it removes the bind-pathname/`ECONNREFUSED` gap seen in the e7a6 R2 baseline. Closing the probe causes `_handle_connection` to see EOF and return without dispatching a request; the serial accept loop is not left waiting indefinitely for a request line.

However, `TestPeerCredentials.test_refused_peer_reply_survives_client_write_after_accept` replaces `server._peer_uid` with `denied_uid`, whose call sets the test's `accepted` event (`test_serve_socket_carrier.py:1010-1025`). This patch is installed **before** `_wait_for_socket`. When the server accepts the readiness probe, `_handle_connection` calls `_peer_uid` before reading the probe's EOF, so `denied_uid` sets `accepted` for the **probe**. The later real-client `accepted.wait(timeout=2.0)` can then return immediately even when the server has not accepted that client. The test can pass while its intended ordering, client write after its own accept, is false. Because `_wait_for_socket` returns after the kernel-level connect rather than after server-side handling, simply clearing `accepted` when it returns would remain racy: the probe may be accepted afterward.

Repair the test so the event distinguishes the real client from the probe, or make readiness completion explicitly wait for server-side handling before re-arming the event. Preserve the behavioral assertion that the denied real client receives a JSON refusal after a write that follows its own accept. A gate PASS cannot certify this ordering while the probe can set the event. The helper retains a pre-existing five-second startup deadline, which can also report a healthy but heavily delayed listener as a failure; that load-sensitivity is a separate limitation, not the B2 false-positive mechanism.

## New blocker B3 — second pipe allocation can leak the first

`test_serve_watch.py:290-295` opens the readiness pipe, then opens the release pipe, and only then enters the cleanup `try`. If the second `os.pipe()` raises (for example, descriptor exhaustion), neither descriptor from the first pipe reaches `finally` and both leak in the test worker. The old single-pipe fixture did not have this two-allocation gap. Move both allocations under cleanup ownership, or close the first pipe if the second allocation fails. This does not reopen B1's scheduling fix, but the claim that all setup errors clean up their descriptors is not yet true.

## Outstanding holds

The e7a6 R2 run failed in its baseline after 1,924 passed / one failed (`TestWireShape.test_malformed_json_wire_values_get_bad_argument_and_server_survives`, `ConnectionRefusedError`) and judged no candidates. The b2a tree has no R2 verdict yet. Its registered full gate and P3 live daemon/carrier/placement/restoration probes, including measured DAMON overhead, remain open. This report does not authorize P1 release or daemon activation.
