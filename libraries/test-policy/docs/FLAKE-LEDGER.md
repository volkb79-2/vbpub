# Flake ledger

Rule (AGENTS.md "Tests never depend on host load or speed"): a result that differs under load, by host or
"sometimes" is a defect of the test or the tool. Each row names the test, the failure signature, the root cause
(or `unknown: needs repeat-until-fail`) and the fix status. Rows are seeded from history (AWP-10, memo section
6.4); new rows come from `flakehunt` runs and from `run-gate flakes` once it exists. A row is closed only by 20
green `flakehunt` repeats under the profile that exposed it.

Status words: FIXED (root cause removed, commit named), OPEN-HYPOTHESIS (cause not proven), CANDIDATE (found by
static reading, no failure observed), NOT-A-FLAKE (deterministic).

| ID | Test / location | Signature | Root cause | Status |
|---|---|---|---|---|
| FL-01 | assay `gate/tests/test_self_qualification_container.py::test_outer_runner_launches_bounded_cgroup_visible_container_and_reads_job_status[b110-screen-...]`, v1001, run `a1a-prep2` (sha e0c4d31a2, 2026-11-04 17:53Z) | `self-qualification-container: B105 container is missing required environment fact: ASSAY_B105_GATE_EXPECTED_TREE`, `assert 1 == 0` at `:961` | SIGPIPE under `set -o pipefail` in `assay/tools/self-qualification-container.sh`: `printf ... \| grep -q` exits 141 when `grep -q` leaves on the first match before the multi-chunk `printf` finishes, so a PRESENT fact reads as missing. Timing dependent (pipe buffer and scheduling), hence "only on v1001". The `ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query` line quoted in the first report is only the section header printed by `assay/tools/tester-unified-gate.sh:408`; it is not the cause. | FIXED a3505563a (here-string instead of a printf pipe). a1a-prep2 ran 9 minutes BEFORE the fix commit (18:02Z) |
| FL-02 | same file, `test_failed_first_publication_is_quarantined_and_retry_can_proceed[fail-1]`, v1001, run `assay-mat-tu5` (sha 659e8305f, 14:03Z) | `B110 container is missing its admitted .assay identity: ASSAY_B110_ASSAY_STATE_DEVICE`, `:1787` | Same defect as FL-01 (second env fact guarded by the same pipeline); the commit message of a3505563a cites this run. It looked like a different mode only because the missing fact differs per test. | FIXED a3505563a |
| FL-03 | same file, `test_runner_cancellation_stops_and_removes_its_owned_container`, v1001, run `assay-wp1-gate2` (sha 38d8413e4, 19:50Z; after the FL-01 fix) | `subprocess.TimeoutExpired: ... self-qualification-container.sh ... timed out after 60 seconds` at `proc.communicate(timeout=60)` `:4165`; the launcher had printed `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` and was killed (`returncode: -9`) | unknown: needs repeat-until-fail. Hypotheses: (a) a fixed 60 s budget after SIGTERM while the host ran heavy campaigns (the test waits for the stop+rm of a stubbed docker through the launcher's trap); (b) the launcher's SIGTERM trap does not terminate a blocked `docker wait` child promptly (signal delivered to bash while a foreground child runs defers the trap). `flakehunt --recipe slow-cpu` and `cpu-contended` on this single test is the first step. The 60 s is also below the 120 s failsafe floor of AGENTS.md. | OPEN-HYPOTHESIS (TP-02) |
| FL-04 | run-gate `run-gate-project/tests/test_run_gate.py::TestReattachProfilingWiring::test_reattach_adopts_recorded_session_stop_called_with_it` (RG-160, RG-162); also doctor-pressure and `basic`-instead-of-`daemon` sibling tests | `profiling: cgprofile ctl stop produced an unparsable response` (runs 2 and 3 failed, run 4 passed) | `_FakeCgprofileDaemon.close()` closed the listener while its accept thread was blocked in `accept()`; the freed fd NUMBER was reused by the next test's listener, and the stale thread answered that test's connections from a deleted plan directory (0-byte reply). The memo's first hypothesis (non-atomic plan file) was wrong. Leaked thread plus fd reuse. | FIXED 8bbc11d4d (accept loop polls with a 50 ms timeout, `close()` sets a stop event and joins). Reproduction under `slow-cpu` before the fix: see the report |
| FL-05 | run-gate `test_lock_wait_does_not_consume_lane_budget` (the rigor chain "1 s timing test") | false `BUDGET_EXCEEDED`: 0.75 s command under a 1 s budget with a 0.4 s lock hold | wall-clock assertion with 0.25 s headroom | FIXED 8bbc11d4d (2 s budget, 0.5 s command; the oracle is unchanged: lock wait alone exceeds the budget) |
| FL-06 | run-gate `TestStall*` (RG-154) | environment-sensitive outside the gate | timing thresholds | CANDIDATE: reproduce with `slow-cpu`, then inject the clock |
| FL-07 | nyxloom NL-12: `test_behavioral.py:932`, `cfg_daemon` thread outlives teardown | timing assertion plus a daemon thread surviving the test | leaked thread plus a timing assert | CANDIDATE: the plugin's leak check reports the thread; owner fixes |
| FL-08 | ciu CIU-101 flaky warning test; `TestPermissionRemovalRetry` | xdist-order dependent | order or shared state | CANDIDATE: `flakehunt -- -p xdist -n 4` and per-test isolation |
| FL-09 | cgroup-profiler CP-53 accept loop | same family as FL-04 | accept thread outliving the fixture | FIXED (CP-53); the template for the FL-04 fix |
| FL-10 | assay `tests/core/test_isolation.py` on sha fb2cf5e27 (run `assay-mat-tu3`): `test_primed_template_status_trusts_stat_data_and_still_reports_changes` (`'M other.txt' == ' M other.txt'`), `test_tiered_limits_*` (`ReasonCode` has no `LIMIT_EXCEEDED`; `nothing to commit`) | deterministic assertion and attribute errors | probably an unfinished branch state (a leading space lost from `git status --porcelain`, a missing enum member), not host dependence | NOT-A-FLAKE (unverified; re-check on current main with `flakehunt`) |
| FL-11 | dstdns fixed sleeps found by grep (`time.sleep`/`asyncio.sleep` with a constant >= 0.3 s): `tests/contract/test_workflow_contracts.py:763,821`, `tests/integration/test_echo_end_to_end.py:76,271`, `tests/integration/test_websocket_real.py:156,269,291`, `tests/integration/test_week2_features.py:412`, `tests/integration/test_demo_task_execution.py:41`, `tests/integration/test_full_workflow_simple.py:44`, `tests/integration/test_webapp_controller_proxy.py:68`, `tests/e2e/test_config_plane_live.py:156`, `tests/integration/test_ddcli_live.py:190`, `tests/unit/test_dns_operations_parsing.py:553`, `tests/observability/test_tracing.py:145,558` (a 5 s "allow OAP to ingest"), `tests/infra/test_skywalking_postgres_combination.py:254` (15 s), `tests/load/test_p171_seeded_distribution.py:526` | no failure observed | sleep as synchronisation; each should become an event or a polled condition with a failsafe of at least 120 s | CANDIDATE (grep only, 14 `time.sleep` in dstdns tests in total): run the plugin over dstdns to get the exact list |

## Root-cause classes seen so far

| Class | Rows |
|---|---|
| SIGPIPE under `pipefail` | FL-01, FL-02 |
| Thread outliving teardown plus fd-number reuse | FL-04, FL-07, FL-09 |
| Wall-clock assertion or tight budget | FL-05, FL-06 |
| Fixed 60 s wait | FL-03 |
| Order or shared state | FL-08 |
| Sleep as synchronisation | FL-11 |

The plugin rules map to the classes: `fixed-sleep`, `short-timeout`, `wall-time-assert`, `resource-leak`. SIGPIPE
under `pipefail` needs a shell lint (backlog TP-03).
