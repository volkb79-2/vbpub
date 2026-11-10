<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/r1002-selftest-stall-REPORT.md -->
<!-- relation: self-test stall evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# r1002 selftest lane stall - diagnosis report (2026-11-08)

Host: r1002 only (identity guard passed before every host command: hostname -f / hostnamectl --static / /etc/vbpub/server-id = 799611; API check OK).
Job: `sat-selftest-20261108T190525-r1002`, lane `run-gate-project:selftest`, commit 11b4a7c34a84, container
`run-gate-rg-sat-selftest-20261108T190525-r1002-11b4a7c34a84-selftest-165486-1794164736` (d6f77345bdb1).
Evidence: `<controller-program-dir>/logs/r1002-selftest-stall-20261108/` (inner1.txt, inner2.txt, shim-args.txt, cgroup-stats.txt, live-lane-log-tail.txt, status-before-cancel.txt, cancel-output.txt).

## Verdict
Genuinely stuck (deadlocked), not slow. Cancelled at 19:20:17Z after ~15 min with no progress. Root cause is a test-harness bug in the run-gate selftest, not a host or resource problem.

## Evidence
- Timeline: container started 19:05:36Z; pytest progress dots stopped at 56% at 19:07:58Z (lane.log mtime); at 19:19-19:20 the container had run ~13.5 min, i.e. ~12 min silent. Earlier PASS runs of this lane take 252-256 s. The queue's own 60-min cancel would not have fired for another 45 min; run-gate's own 20 min lane budget would have killed it at ~19:26 (that is what happened in the earlier INFRA:BUDGET_EXCEEDED runs).
- Host healthy and idle: load 0.34, CPU/IO/memory PSI all ~0 (full avg = 0), disk 79% used, swap barely used.
- Container cgroup (dev-gates.slice/docker-<id>.scope): memory.current 556 MiB of 2 GiB max, memory.events all zero (high/max/oom/oom_kill = 0), memory.pressure 0, cpu nr_throttled 0, io.pressure ~0. The 2 GiB limit is NOT throttling. Low CPU / low IO is simply because everything is blocked.
- The 7 processes: docker-init, bash -c wrapper, pytest (main thread in poll_schedule_timeout, waiting on pipes), and two `/bin/sh .../shim/docker` processes each in a `while [ -f .hang ]; sleep 0.2` loop (the `sleep 0.2` children are the only activity).
- The test on screen: `TestStallEndToEnd.test_a_silent_lane_is_stopped_with_evidence_and_error_status` (run-gate-project/tests/test_run_gate.py:20522). It sets `<state>/.hang` so the fake `docker logs -f` blocks, expects run-gate to detect the stall (1 s), capture evidence with `docker logs <name>`, `docker rm -f`, and return 2.
- Fake docker call log of the test shows exactly 5 calls: run, run (lanes inventory), run -d, `logs -f <name>`, `logs <name>` - and NO `rm`. So run-gate did detect the stall and reached `save_container_logs()` (run_gate.py:8164, `subprocess.run([docker,"logs",name], capture_output=True)`, no timeout), which is blocked.
- Why the non-streaming `logs <name>` blocks: the shim in `fake_docker_stateful` (test_run_gate.py ~17440) decides "streaming form" with `case "$*" in *-f*)`. The container name is `repo-run-gate-proj-mutation-<8 hex hash>-...`; here the hash is `f4f89a3d`, so `-f4f89a3d` matches `*-f*` and the evidence-capture call is treated as `logs -f` and hangs while `.hang` exists. Verified: `case "logs repo-...-f4f89a3d-..." in *-f*` matches, an `a4f...` hash does not. shim-args.txt shows both pids (`logs -f <name>` and plain `logs <name>`) in the hang loop with ppid = pytest.
- The name hash depends on the per-run random tmp dir (`selftest.0XT6AM`), so the trigger is random: probability 1/16 per selftest run that the hash starts with `f`.

## Reproducibility
- host-sat-results.tsv: 20 selftest runs including this one; 3 hung at the same place: r1002 20261108T114538 (INFRA:BUDGET_EXCEEDED, 1238 s, log stops at 56%), v1001 20261108T182109 (INFRA:BUDGET_EXCEEDED, 1258 s), and this one. 3/20 = 15% observed vs 6.25% expected (small sample; consistent). The controller-side note in HANDOFF-STATE-20261107.md (REN-A: "selftest hung on test_a_silent_lane...") is the same test.
- Distinct from the other known class: the "unreaped zombie children" failure of this test (fails in ~9 s on pristine main via `_direct_child_pids() == []`, HANDOFF 16:10Z). That one fails fast; this one deadlocks. Different mechanism, same test.
- Not related: `sat-focused-...-r1002` INFRA:UNKNOWN at 18:55Z (cgroup-profiler:focused, 69 s) is another lane.

## Action taken
Evidence saved first, then cancelled ONLY this job via the queue's own `remote-gate-r1002.py cancel sat-selftest-20261108T190525-r1002` (SIGTERM to the job). Status became `finished exit=143`; the lane container is gone (run-gate removed it on the way out); no other container or job was touched. The queue's polling loop will collect and record it as INFRA (expected `INFRA:ERROR` or similar); this result must be classed as harness flake, not a product verdict, and the lane re-run (next queue slot) should PASS with 15/16 probability.

## Recommended fixes (backlog text)
1. run-gate selftest harness (primary): "RG-xxx: `fake_docker_stateful` shim treats any `docker logs ... <name>` as streaming when the container name contains `-f` (`case "$*" in *-f*)`). With a name hash starting with `f` (1/16 of runs) `save_container_logs` blocks and `test_a_silent_lane_is_stopped_with_evidence_and_error_status` (and any other `.hang` test) deadlocks until the 20 min lane budget, yielding INFRA:BUDGET_EXCEEDED ~6% of selftest runs. Fix: match the `-f` flag on an argument boundary (loop over `"$@"` and compare `[ "$a" = "-f" ] || [ "$a" = "--follow" ]`), and add a test that a container named `...-f0000000-...` captures evidence without hanging."
2. run-gate product hardening (secondary): `save_container_logs` runs `docker logs <name>` with no timeout. Add a bounded timeout (e.g. 60 s) so evidence capture can never prevent the `docker rm -f` of a stalled lane ("evidence is best-effort" per its docstring); a wedged docker daemon would otherwise reproduce this for real.
3. Test-level guard: give the end-to-end stall tests a per-test timeout (e.g. 120 s) so a hang fails that one test with a traceback instead of burning the 20 min lane budget.
4. Queue rule (host-sat-queue.sh): optional, no change needed. The selftest lane has a 20 min run-gate budget and a typical 255 s duration, so a per-lane `LANE_TIMEOUT_S` of ~900 s for selftest would cancel hangs sooner than 3600 s; otherwise treat INFRA:BUDGET_EXCEEDED on selftest as "re-run once" before escalating.
5. The 2 GiB container limit: no change; memory.events were all zero.
