# P1 socket-readiness fix verification — 2026-10-06

**Verdict: ACCEPT for the scoped test-readiness fix.** The caller selected the
Sol route; no reviewer self-attestation is used as route evidence. This is a
fix-verification record for one test-only commit, not a new full daemon review,
release approval, or daemon deployment authorization.

- Exact candidate: `1653143f752dfc612f6154514ee0ed85b3f35c7a`.
- Tree: `adeb558cf589dec98838422b2a6b708ce38b6ffd`.
- Delta: only `tests/test_serve.py`, replacing a direct accept-loop thread and
  pathname-existence polling with `_start_ready_socket_server()`.
- The prior failure was a real race: `_bind()` publishes the socket pathname
  before `listen()` completes, so `os.path.exists()` did not prove that a
  client could connect. The helper signals readiness only after `_bind()`
  returns. The test retains its behavioral checks: an unterminated `start`
  request is not dispatched, no session is created, and a later valid version
  request still succeeds. No production code or teardown path changed.
- The reviewer found no new blocker. The test still has its pre-existing
  bounded connect and join timeouts; this review establishes removal of the
  identified connect race, not immunity to every possible scheduler delay.

## Evidence and limits

The controller recorded R0/R1 PASS on this exact commit: 2,369 tests,
100% line and branch coverage, run `0f6746b7f6127a1bc16a593bd0143513`.
R3 functionally passed (7/7 canaries rejected, run
`6ea9635d8cd0a1de9c5a28cdfd2119fa`), but its run-gate launch sample reported
memory-full PSI `avg10=5.11%`, above the 5% launch threshold, despite the
earlier 3.80% preflight sample. Treat that as a launch-policy miss, not fully
admissible R3 evidence; rerun R3 on the final committed tree when the fresh
launch reading is at or below 5%. The reviewer did not independently verify
the gate or doctor receipts and ran no tests, gates, containers, or probes.

Controller `run-gate.py doctor` on this exact commit reported 9 OK,
2 expected warnings (linked-worktree host-lane visibility and the intentionally
down profiler daemon), 0 failures, and 2 info.

The diagnostic P1 R2 on the older exact tree `c24b0d2b882df96ee6549cf357a713943398a564`
finished with 114/114 accounted: 109 killed, five survived, and zero
equivalent, budget-exceeded, crashed, or hung. Those five IDs match the
contract-equivalent dispositions already reviewed in the P1 survivor report;
the raw verdict is still `FAIL/MUTANTS_SURVIVED`. This diagnostic run does not
qualify as the final combined P1/P6 R2.

Exact final combined-tree R2 disposition, the registered full gate, and P3's
real daemon/carrier/placement/restoration probes plus measured DAMON overhead
remain release holds.
