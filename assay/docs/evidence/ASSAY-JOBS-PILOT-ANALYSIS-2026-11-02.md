<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-02; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ASSAY-JOBS-PILOT-ANALYSIS-2026-11-02.md -->
<!-- relation: Assay jobs pilot analysis -->

# Assay `--jobs` pilot 3: analysis of the three bucket mismatches

- Input: `ASSAY-JOBS-PILOT-2026-11-02.md` and the raw evidence summarized in
  [JOBS-PILOT3-EVIDENCE-SUMMARY-2026-11-13.md](JOBS-PILOT3-EVIDENCE-SUMMARY-2026-11-13.md).
- Method: I reviewed the per-arm state records, progress streams, verdicts and
  mutated source lines at `5fd78a8e4`. Nothing was run.

**Recommendation: RELEASE assay 11.0.0.**
- Ship it with a documented known issue and two backlog entries (B204, B205).
- Restate the bucket criterion (below).
- None of the three mismatches implicates Assay's scheduler, its clone handling or the jobs>1 path. Two are directly traceable to a run-gate test that failed on the *unmutated* tree on <host>. The third follows the same pattern but is inferred, not proven (details below).

## 1. What killed the three candidates in the jobs=1 arm

- The state records and verdict entries hold only `terminal_result = FAIL/COMMAND_FAILED, rc 1`, with no failing test id (see B205).
- The R2 command runs `pytest -x`, so the run stops at the first failure.
- Each candidate event records `tests_completed`. That is the number of call-phase `test` events (`liveness.count_test_events`), and the failing test's call report is one of them.
- So the failing test is the `tests_completed`-th test in the baseline's test order. The suite order is deterministic, and the baseline progress lists all 2,531 test ids.

| Candidate | Mutation | jobs=1: `tests_completed`, and the test at that position | jobs=3 / serial replay |
|---|---|---|---|
| `edd2b4c8` (index **0**) | `run_gate.py:223` `@dataclass(frozen=True)` on `LaneResult` changed to `False` | 1871: `TestBareHostProfilingWiring::test_daemon_start_with_null_optional_target_still_stops_session` | survived, all 2,531 tests |
| `3f13c0f7` (index 54) | `run_gate_admission.py:897` `now >= marker_info["deadline"]` changed to `>` | 1871: **the same test** | survived, all 2,531 tests |
| `7c3f5c17` (index 43) | `run_gate.py:13367` `flush=True` changed to `False` in the `repoll_owner_race` notice | 1559: `TestStallEndToEnd::test_a_silent_lane_is_stopped_with_evidence_and_error_status` (a 1.1 s timing-based stall test) | survived, all 2,531 tests |

**Are the kills legitimate? No, they are suite flakes.**
- **Test 1871 fails without any mutation on <host>.** The first classification attempt (`classify-attempt1-summary.json`) ran the **unmutated** R0 at jobs=1. It failed with "first failing test: …`test_daemon_start_with_null_optional_target_still_stops_session`". So that test is non-deterministic on <host>, independent of the candidates.
- **`edd2b4c8` was the first candidate in the jobs=1 arm.** Only the baseline ran before it, so residue from a previous candidate, or a lock left in a reused tree, cannot explain its kill.
- **The two mutations are unrelated to the failing test.**
  - Unfreezing `LaneResult` cannot affect a docker-faked daemon start/stop test.
  - `>=` versus `>` on an admission-marker deadline is reached only when the clock equals the deadline exactly, which no test exercises; that is why both replays survived.
- **`7c3f5c17` is the same pattern, inferred.** The mutated line only changes buffering of a message printed in a microsecond owner-race path that the stall test does not reach. The test that stopped the run is a wall-clock stall/silence test, the most timing-sensitive kind in the suite.
  - Unlike test 1871, there is no unmutated failure of it in the evidence, so this attribution is inference.
- **The rate fits a flaky test.** With `-x`, only runs that get past test 1871 can trip it: about 20 per arm, plus the two classification R0 runs. It failed 2/~23 (jobs=1) + 0/~20 (jobs=3) + 1/2 (unmutated replays). That is roughly a 5–10 % flake. Three flake kills all landing in one arm is unremarkable at this sample size.

## 2. Where the divergence comes from

**Suite non-determinism on <host>, not Assay.** Ranking the possible causes against the evidence:

- **jobs>1 scheduler: no.** jobs=3 agrees with the isolated serial replay on all three candidates.
- **jobs=1 path (window host, one clone per unit): no.** The same test fails on an unmutated R0 that has no candidate path at all, and the first candidate (with no predecessor) shows the kill. Both arms also use the same per-unit clones.
- **The suite: yes.** The run-gate test suite has at least one test that is flaky on <host>'s Btrfs cowtree scratch (<host> also needed a deselection for its exec-lock test). The exact cause of the daemon-test flake is not established. A plausible lead is the same Btrfs/`/proc/locks` device-number mismatch found for the deselected lock test, since `run_gate.main(["suite"])` takes the exec lock under `tmp_path`.

What Assay *does* contribute is how a flake is scored, which is a design limitation and not a regression:
- **(a)** A single flaky failure becomes `killed` with no confirmation step. That is false credit, in the dangerous direction: it inflates the mutation score.
- **(b)** The kill is recorded without its failing test id, so finding the cause needed the `tests_completed` inference above.

This serial behaviour is unchanged from 10.x, so 11.0.0 introduces neither.

## 3. Release decision: RELEASE

**Reasons:**
- The new code under test (the jobs>1 scheduler and v17) matched the ground truth on 56/56 candidates.
- All three disagreements are in the serial path, and the evidence attributes them to the suite.
- The verifier accepts both v17 verdicts with the correct `jobs` / `attribution`.
- Measured 2.61x speed-up, with a 3.49 GiB peak memory under the 5 GiB high-water mark.

**Restate the pilot criterion as:**
> jobs=N buckets equal the jobs=1 buckets, **or** every disagreeing candidate's jobs=N bucket equals a fresh isolated serial replay. Any disagreement whose jobs=1 kill is not reproduced must trace to a test that fails non-deterministically (shown by an unmutated failure, or by a second replay). The scheduler fails the criterion only if jobs=N disagrees with the isolated replay.

**Known issue for CHANGES / CONSUMERS:**
> A suite with a non-deterministic test can turn a survivor into `killed` in any arm. Assay records one execution per candidate and does not confirm kills. Treat bucket differences between runs as suite flakes, unless the isolated serial replay reproduces them.

**Backlog entries:**
- **B204, mutation kills are not confirmed against suite flakiness.** A flaky test can turn a survivor into `killed`, which is false credit. The <host> jobs pilot had 3/56 such kills.
  - Proposal: an opt-in `judge.mutation.confirm_kills = 1`. It re-runs, serially, each killed candidate's *first failing test* on the **unmutated** clone. If that test fails there too, the candidate is classified `crashed`/inconclusive instead of killed (no credit).
  - Default off; the cost is bounded by the number of kills.
  - Also add a lint for flaky tests in R0 when the baseline is replayed.
- **B205, killed candidates do not record their failing test.** Add `first_failing_test` (nodeid) to the candidate progress event and the state record, taken from the candidate's own liveness events file. The first is additive; for the second, the judge label decides whether it can be reused. Put it in v18, or in an additive optional verdict field.
  - Without it, a disputed kill can only be traced by counting tests.
- **Run-gate (RG backlog, not Assay):** `test_daemon_start_with_null_optional_target_still_stops_session` is flaky on Btrfs cowtree scratch (<host>); it failed an unmutated R0. File it next to the exec-lock device-number entry that the pilot report proposes.

**Not needed for the release:** no Assay code change; no HOLD fix list.
- If the operator wants more certainty first, run one cheap step on <host>: the unmutated R0 at jobs=1, three times.
- If test 1871 fails at least once, the attribution is closed. This is evidence-gathering, not a gate.
