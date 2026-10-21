# P1 startup-readiness test fix verification — 2026-10-06

**Verdict: REJECT for the scoped test change.** This is an unnumbered verification of one test-only commit, not another full daemon review or P1 merge/release clearance.

## Scope and evidence

- Reviewed workspace: `.worktrees/rg55-p1-readiness-review-20261006`, clean at `e7a6b7594e1a5598d4dd9973204aa4821e9a4b72` (tree `367a10cdcecb651bd35b0e5c2114d516b5c07b71`) before this report was written.
- Reviewed delta: the `e7a6b759` change to `scripts/cgroup-profiler/tests/test_serve_watch.py::TestRealSubtreeEnforcement::test_unreachable_pid_is_reported_and_the_lane_remains_live`, compared with `1653143f`. No production source or test was changed in this review.
- Read the canonical `AUTHORING.md`, P1 round-6 review, preceding socket-readiness fix verification, target test and `lib/subtree.py`'s token-owner resolution. The controller supplied 10 consecutive targeted passes and exact-tree R0–R1/R3 PASS. Those are useful observations but do not prove the test is independent of scheduling delay.
- No tests, gates, containers, daemon operations, merges or releases were run for this review.

## Blocking finding B1 — readiness does not hold the process live

The child writes one byte and immediately enters `time.sleep(30)` (`test_serve_watch.py:295`). The parent reads the byte, then builds the fake cgroup/proc inputs and starts the server; the server's first `SubtreeResolver.refresh()` reads the live PID's `/proc/<pid>/environ` for the token (`lib/subtree.py:223-237`). The byte proves the child reached its code, but it does not keep the child alive until that scan. If the parent is descheduled for more than the child's remaining sleep after the read, the child can exit first. The resulting empty token-owner set makes `lane.pid in sess.subtree_resolver.current_pids` fail even when the daemon behaves correctly. This is the same load-dependent oracle gap the change is meant to remove.

There is a second false-readiness path: `os.read(ready_read, 1)` is not checked against `b'R'`. If the child exits before writing, EOF (`b''`) is accepted as readiness and the test proceeds to the scan. The later PID assertion may fail, but it gives no direct, deterministic explanation of the child startup failure. If `Popen` itself raises, the read descriptor is also left open because its closing `finally` starts only after the call returns.

The live-PID/no-signal safety assertions themselves remain present: the test checks initial token resolution, forbids numeric `os.kill`, verifies the child is still running, and checks the `reported` refusal. The defect is fixture lifetime and readiness, not weakened assertions. A satisfactory repair would have the child signal an exact readiness byte and then wait on a parent-controlled pipe/event until after the scan and safety assertions; release and reap it in all cleanup paths. The readiness read must reject EOF. A failsafe for a hung child may bound the test, but elapsed time must not decide whether an otherwise healthy run passes.

## Separate R2 baseline evidence and disposition

The controller reports that R2 on exact `e7a6b759` failed its baseline after 1,924 passed / one failed in 122.99 seconds in `TestWireShape.test_malformed_json_wire_values_get_bad_argument_and_server_survives`. Its `_wait_for_socket` checked pathname existence before the socket was listening, and `_raw_socket_request` received `ConnectionRefusedError`. That is a separate new socket-readiness failure in `tests/test_serve.py`, outside this review's target test. This review makes no repair or gate claim about it. P1 R2 and the registered full gate remain uncleared.

The controller should repair B1 in its own worktree, re-run focused and required exact-tree gates, and obtain a scoped fix verification before treating this test change as accepted. Any repair to the separate socket helper needs its own review and exact-tree evidence.
