# REBASE — P0 (W8, B111) and P2 (W6, B113) onto Wave A

Delta list for `b110/P0-measurement-hygiene.md` and `b110/P2-loop-guards.md`.
- **Baseline.** Both briefs were anchored at `db85f747`; this note re-checks them at `5bbd916e` (landing plus the B107 merge `7d7b0fb9`).
- **What changed since then:** `liveness.py`, `mutation.py`, `runner.py`, the new `liveness_resources.py`, `assay.toml`, the docs and the B107 tests. All other anchors below were re-read and are unchanged.
- **Main is ahead by one change:** `35adca38` deletes both real-child liveness tests in `test_cli_run.py`.

## R0. Do on landing before stage 2
1. **The B105 exclusion fixture is stale, and that blocks both P0 and P2.**
   - B107 inserted `liveness.py:92`, which moved the pragma from line 93 to 94 and its body to 95.
   - `tests/fixtures/b105-coverage-exclusions.json` (landing only) still says `[93, 94]`, and line 93 is now blank.
   - The fast suite cannot see this (`test_b105_source_coverage_controls.py:136-145` checks the fixture only against itself). The preflight's `_validate_b105_exclusion_inventory` and P2's O5b will both fail.
   - Neither package can fix it: P0 forbids editing the fixture, and P2 may edit only its git.py entry.
   - **Fix:** a controller commit setting the entry to `[94, 95]`, as `ef9bb3bb` did for targets.
2. **Re-merge main into landing now.** This removes the plan §4 known-red test for every package. It also avoids a modify/delete conflict with P2's Work 3.
3. **Test paths move.** W3 (stage 0) moves the judge tests into `tests/<component>/`, and W4 moves the tooling tests into `gate/tests/`.
   - Resolve every `tests/…:N` anchor by file name, then by function.
   - New test files go into the component that W3 assigns.
   - All implementers are **Sonnet**.

## P2 (W6, B113)
**Header:**
- The branch is now `wave-a-w6-p2-guards`, cut from `assay-b110-landing`.
- The old merge order "P2 → P1 → P3c" is void: P1 is superseded and P3c belongs to v14.
- P2 merges in stage 2 **before W4**, because W4 re-inventories `test_b105_source_coverage_controls.py`, the conftest B105 hook and the exclusions fixture.
- W5 does not overlap P2. The CASES table finds `sql_lex` sites by substring, so it tolerates line shifts.

**Anchors:**
- **Unchanged:**
  - go 203/326/334/356; javascript 176/258; sql_lex 64/197/280; go_modfile 77/393;
  - isolation 52/1406; git 261/335/357/376/1342; cli 488; python 881;
  - `test_cli_run` 127/463/529/586/645.
- **Moved:**

| Anchor | Was | Now |
|---|---|---|
| `liveness.py` `__init__` (now also takes `resource_reader`) | 1299-1316 | 1340-1358 |
| `liveness.py` `start_new_session` | 1375 | 1418 |
| `liveness.py` timeout check | 1530-1537 | 1692-1699 |
| `runner.py` | 4420 | 4427 |
| DESIGN-GUIDE §B105 | 1934 | 1972 |

**Obsolete: drop Work 3, O6, and the `liveness.py:1530` rows** in the decision and traceability tables.
- Main deleted both watchdog targets.
- The remaining real-child test (`test_liveness_runner_monitor.py:1069`: `sleep 300`, fake clock, fake 600 s timeout) cannot spin under one mutant. With the timeout check (`:1692`) disabled, the hung path still ends it; with hung detection disabled, the fake timeout does.
- Record `:1692` in the P2 report as "test-level hazard removed with its host test".
- Drop `test_cli_run.py` from the scope. Work 1–2 and O1–O5b are unchanged.

**New notes:**
- **Q-P2-1 (carver): where `test_scanner_progress_guards.py` goes.** Its CASES span four adapters and core (`isolation`).
  - Recommendation: split the file by W3 component, so each component's mutants are killed by its own tests (B130).
  - Its `from conftest import PROJECT_ROOT` follows W3's conftest.
- `test_errors.py` moves to core.
- W10 later shifts git.py's pragma lines, and P2's O5b is its check.

## P0 (W8, B111)
**Header:**
- The branch is now `wave-a-w8-p0-measure`, cut from landing after stage 2.
- The implementer is **Sonnet**, not the brief's "Opus for W2". Re-carve W2's packet (see below) before dispatch.
- **Unblocks:**
  - P1 is superseded, but the leak fix is still needed before A-468(b)'s tiering is re-decided after W4.
  - W9 (P8) consumes P0's fields, so **P0 merges before W9**.
  - P6, P4, P7, P3d and P5 are v14 or deferred.
  - G1–G5 still land (A-472).

**Anchors moved:**

`liveness.py`

| Anchor | Was | Now |
|---|---|---|
| `_pid_cpu_ticks` | 529-544 | 549-564 |
| └ the split idiom | 540-541 | 560-561 |
| `tree_cpu_seconds` | 586-625 | 606-645 |
| └ the walk | 607-622 | 627-642 |
| `__init__` | 1299-1333 | 1340-1376 |
| `__call__` | 1336-1380 | 1381-1423 |
| └ stale cleanup | 1351-1359 | 1394-1402 |
| `_monitor` | 1441-1541 | 1484-1706 |
| plugin | 160-236 | 161-237 |
| events and `_selected_test_events` | 630-720 | 650-740 |
| `baseline_test_events` | 785-800 | 805-820 |
| `_read_events_progress` | 1016-1057 | 1036-1077 |
| `_EventProgressReader` | 1060-1130 | 1080-1150 |
| post-finish hung | 1475-1476 / 1516-1519 | 1643-1644 / 1671-1677 |
| grace constant | 525 | 544 |

`mutation.py`

| Anchor | Was | Now |
|---|---|---|
| auto budget | 1877-1909 | 2126-2158 |
| `plan` event | 2223-2257 | 2472-2506 |
| forwarding | 2258-2272 | 2507-2521 |
| `_MutantRun` | 1643-1666 | 1889-1915 |
| `_run_attempt` | 2646-2760 | 2895-3009 |
| └ `started_monotonic` | 2657 | 2906 |
| └ `tests_completed` read | 2742-2749 | 2991-2998 |
| `_run_one` | 2857-2888 | 3107-3138 |
| candidate event and state record | 2929-2989 | 3179-3251 |
| loader | 1305-1330 | 1547-1572 |

Unchanged in `mutation.py`: lines 846-870 and 951-998, and the pragmas at 148, 155 and 156.

`runner.py`

| Anchor | Was | Now |
|---|---|---|
| `command_finished` | 3088-3100 | 3095-3107 |
| `LivenessRunner(...)` | 4418-4430 | 4425-4437 |

Docs

| Anchor | Was | Now |
|---|---|---|
| CONSUMERS | 2609-2614 | 2647-2652 |
| CONSUMERS | 2630 | 2668 |
| DESIGN-GUIDE, liveness | 470-500 | B107's subsection at 484-519 (put P0's paragraph there) |
| DESIGN-GUIDE §B105 | 1934 | 1972 |
| README | 955-975 | 975-995 |

**Unchanged at this commit:** cli, analysis, `tools/b105_report_check.py`, `self-qualification-gate.sh`, `test_liveness.py`, `test_b106` and the isolation anchors. W2, W3 and W4 will move them before P0 starts, so resolve them by name.

**Invalidated by B107 (W2 packet):**
- **(a) Resource reads now feed classification.** On every tick, `_monitor` calls `resource_reader` (cgroup `cpu.stat` plus PSI) **before** it classifies. `hung` requires `resource_trace_complete` and a clear interval. P0's `sampler` is therefore a third per-tick reader:
  - it runs after the hung and timeout decisions;
  - it never touches `resource_trace` or `previous_resources`;
  - O6's ordering spy must assert that it runs after both `cpu_reader` and `resource_reader`.
- **(b) O6 and O7 use B107's `_runner` helper** (`test_liveness_runner_monitor.py:119-150`). Its default `resource_reader` returns a clear snapshot. The production default reads host PSI and would make the hung row host-dependent (§3b A).
- **(c) B107 already records per-candidate evidence.**
  - `LivenessHungExpired` and `TimeoutExpired` already carry `resource_evidence`: live-tree `candidate_cpu_s` plus cgroup deltas.
  - It is persisted as `liveness_resource_evidence` (`_MutantRun:1913`; event and record at 3205/3244).
  - The loader refuses a `hung` record that has no valid evidence (`_valid_hung_resource_evidence` 1273, 1645).
  - P0's sidecar is additive: RSS, reaped-descendant CPU, phase and startup data, for every candidate.
  - `_run_one` must pass B107's evidence through unchanged.
- **(d) The leak rationale is weaker, and the fix still stands.** After RW-57, `finish_hang` also needs no CPU growth and a complete trace, so the leak now wrongly marks only a *quiet* test running past 30 s as hung.
- **(e) The exclusion-line trap moves.** After R0.1 the pragma is at liveness `94` and its body at `95`, so the rule is "no line above `:94`".

**Invalidated by Wave A:**
- **D-W8-1 (carver decision): home of the W1 reader.** This blocks P0's W1 only.
  - P0 puts `MeasuredBaseline` in `analysis.py` and reuses its framing. W2 moves that module to `analysis/src/assay_analysis/evidence.py`. Under A-478, W2's T6 allows `src/assay` a single `assay_analysis` import, inside `cli._run_analyze`, so `assay plan` cannot use the module.
  - **(i)** A judge-side reader next to `_cmd_plan`, with its own framing. This adds B105 candidates.
  - **(ii)** An `assay analyze` subcommand that reads `assay plan` JSON plus progress and imports `mutation.auto_budget_per_candidate_seconds` one-way. `assay plan` keeps `estimate_provenance: "fallback"` and the CONSUMERS fix.
  - **Recommended: (ii).** It follows A-478 and sits next to W9/P8. P0's W2–W6 are unaffected.
- **Checker (W5).** Before P0, W2 adds its scope check and W4 adds the S1 binding (a same-commit `tester-unified` pass).
  - P0's `--plan-json` and refusals 1–9 come on top of those.
  - Refusal 8 stays first, and W4's S1 refusal and exit mapping stay.
  - The combined order is recorded in the P0 report.
  - A-474 and A-476 S1 are independent, so both stay.
  - W4 also rewrites `run_and_verify_lane`; P0's plan step and the O12 ordering pin rebase onto it.
- **Test homes.**
  - `gate/tests/` (W4): `test_b105_report_check.py`, the `test_self_lane.py` pins, and the new `test_b105_report_check_real_plan.py`.
  - W3 components: `test_mutation_resource_evidence.py`, `test_liveness_outer_stream.py`, `test_isolation_guards.py`, and `test_plan_baseline_from.py` (under option i only).
  - O10's nested target follows `test_liveness.py` to its new path.
- **G1–G5 do not conflict with W7.** The `_spec` pattern uses the `IsolationConfig` defaults, and `snapshot_history` already defaults to `"shallow"` (`config.py:1063`), the mode W7 gives the lanes. G1 walks only `objects/**`. Optionally parametrize G1 over `full`.
- **`test_python_qualification.py`:** neither package touches it. Only P1's `--ignore` did, and W1 retires the file.

## Parallelism and merge order
- **Source overlap:** none. P2 touches errors, git, the four adapters and `isolation.py:52/1406`; P0 touches cli, liveness, mutation, runner, the checker and the gate script.
- **Doc overlap:** only CHANGES and DESIGN-GUIDE, which merge as docs.
- **Staging:** P0 still needs W2 (D-W8-1) and W4 (the checker and test homes), so keep P2 in stage 2 and P0 in stage 3.
- **Merge order:**
  1. R0.1 and R0.2 on landing.
  2. Stage 2: **W6 → W4**. W5 can merge at any point.
  3. Stage 3: W7, then **W8 → W9**.
  4. W10.
