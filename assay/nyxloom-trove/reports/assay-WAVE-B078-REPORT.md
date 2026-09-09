# assay wave B078 — checkpoint 1 acceptance REPORT (2026-09-08/09)

Gate: **`tester-unified` GREEN** on `c80d94d451b92154394ecb48b845b21e6e7478c5`.
Read from the gate's own log in a separate step: `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`run-gate: lane 'tester-unified' exit 0`, `tester-unified: PASS (exit 0)` on
that commit, and every `ASSAY_GATE_PHASE=` marker through `pyflakes-clean`.

Every claim below cites a named test. Nothing here is asserted from reading
the diff.

---

## Backlog `## B078` acceptance box, item by item

### ☑ "a lane declaring `result_report` with a verified-complete, zero-failure vitest JSON report and a non-zero wrapped-process exit code is judged `PASS`"

> **Round-1 status: this item was the REJECT.** It held on the R0-only path
> and was false for every R1+ lane — i.e. false for the cited live repro. Both
> paths are wired now; the evidence below is the post-repair set.

`tests/test_runner_result_report.py::test_verified_complete_zero_failure_report_passes_over_a_nonzero_exit`
— a real `/bin/sh` writes a real 140-test, 0-failure vitest document and then
exits 1. Asserts `outcome is PASS`, `reason_code is None`, and that
`returncode == 1` survives on the in-process `CommandResult` (the report
decides the outcome; it does not rewrite what the process did — though note
`returncode` is not, and never was, a verdict field: see the correction under
"One deviation" below).

`::test_the_r0_claim_for_the_rg45_shape_is_a_pass_claim` carries the same
scenario through `build_r0_claim`, because the `Claim` is what a consumer's
gate actually reads.

**Both shipped `run_lane` paths, one per lane shape, each with a must-fail
control** — `run_lane` dispatches on declared rigor and the two branches reach
`execute_plan` through entirely different call chains, so one end-to-end test
cannot stand in for the other:

| Lane shape | Path | Test | Control |
|---|---|---|---|
| R0-only | `run_lane`'s direct branch | `::test_run_lane_direct_r0_path_applies_the_tiebreak` | `::test_run_lane_direct_r0_path_still_fails_without_the_declaration` |
| **R0+R1** (RG-45's `ui_unit` shape) | `_run_prepared_lane`'s baseline `_execute_snapshot_unit` | `::test_run_lane_r0_r1_baseline_applies_the_tiebreak` | `::test_run_lane_r0_r1_baseline_still_fails_without_the_declaration` |

The R0+R1 test additionally asserts that **R1 was evaluated at all** — it
cannot run behind an R0 `FAIL`, so this is the difference between a green gate
and a gate reporting a coverage claim that was never computed.
`::test_run_lane_r0_r1_baseline_report_naming_failures_fails_over_a_zero_exit`
covers the opposite direction on that path, and
`::test_run_lane_r0_r1_baseline_creates_the_reports_parent_directory` covers
the snapshot's `create_missing_parents=True`.

**Verified load-bearing, not just green:** removing the one-line baseline
wiring turns exactly those three R0+R1 tests red and nothing else — run and
reverted during the repair.

R2/R3-declaring lanes take the same `_run_prepared_lane` baseline path and are
covered by construction; `tests/test_result_report_wiring_sweep.py` is what
keeps that true (see the Blocker 2 entry below).

### ☑ "a truncated/malformed/absent report falls back to A-073 unchanged"

`::test_an_unusable_report_falls_back_to_a073_on_a_nonzero_exit` and
`::test_an_unusable_report_falls_back_to_a073_on_a_zero_exit`, each
parametrized over **twelve** shapes:

| id | payload | why it must not be believed |
|---|---|---|
| `truncated-mid-write` | `{"success": true, "numTotalTests": 140, "numFail` | claims success, cut off mid-flush — the named acceptance shape |
| `wrong-shape-array` | `[]` | not an object |
| `wrong-shape-other-tool` | `{"stats": {...}}` | another tool's report |
| `no-finished-marker` | counts, no `success` | never finished |
| `success-not-a-boolean` | `"success": "yes"` | not a vitest document |
| `not-json-at-all` | prose | nothing was structured |
| `zero-tests-collected` | `numTotalTests: 0` | measured nothing |
| `count-not-an-integer` | `"140"` | not a count |
| `count-is-a-boolean` | `true` | `bool` is an `int` subclass — would read as 1 test |
| `failed-count-missing` | no `numFailedTests` | incomplete |
| `counts-contradict` | 9 failed of 3 | self-contradictory |
| `negative-failed-count` | `-1` | not a count |

**Both exit-code directions are asserted for every shape.** "Falls back to
A-073" is a claim about the whole rule; a fallback that quietly forced `FAIL`
would pass a one-directional test and be just as wrong.

Absent report: `::test_a_report_never_written_falls_back_to_a073` — the
genuine-crash signature (nothing at the declared path) still `FAIL`s, and the
test asserts the file really is absent so it cannot pass vacuously.

Unsafe object: `::test_a_symlinked_report_path_falls_back_and_is_never_followed`
— a symlink pointing at a valid all-green report outside the reservation is
refused, not followed, and the target is left untouched.

At the reader level, `tests/test_result_reports.py::test_the_reader_refuses_every_unusable_document`
(8 documents) and `::test_a_pathologically_nested_document_refuses_instead_of_crashing`
(200,000 nested arrays → `ReportUnusable`, not `RecursionError`) pin the same
refusals one layer down, each naming its own sentence.

### ☑ "a verified-complete report naming real failures is judged `FAIL` regardless of exit code"

`::test_verified_complete_report_naming_failures_fails_over_a_zero_exit` — 3
failures reported, process exits **0** → `FAIL`/`COMMAND_FAILED`.
`::test_verified_complete_report_naming_failures_fails_with_a_nonzero_exit` —
the agreeing case.
`::test_a_report_claiming_success_while_naming_failures_still_fails` —
`success: true` with a non-zero failure count is judged by the **counts**,
because `success` is computed by the same orchestrator whose internal error is
the defect; it is used only as the "run finished" marker.
`tests/test_result_reports.py::test_the_reader_extracts_the_counts_not_the_success_flag`
states the same rule at the reader boundary.

### ☑ "a lane not declaring `result_report` is byte-for-byte unaffected"

Proven three ways, deliberately — an absence of new failures is not a proof.

1. **Structurally**:
   `::test_an_undeclaring_lane_never_touches_the_reservation_machinery`
   monkeypatches `safeio.reserve_output` to raise `AssertionError`, then runs
   an undeclaring lane. None of B078's code is entered. This asserts the
   property SR-1 actually promises (the new path is not taken), not that
   today's fields happen to match.
2. **Behaviourally**:
   `::test_an_undeclaring_lane_ignores_a_perfectly_good_report_on_disk` — the
   *same* all-green report that turns the RG-45 shape into a `PASS` above
   changes nothing without the declaration, and the file is neither read nor
   removed (asserted by re-reading its content afterwards).
3. **At the config boundary**:
   `tests/test_config_result_report.py::test_a_lane_omitting_the_table_carries_none_and_declares_nothing`
   — `lane.result_report is None` and `"result_report" not in lane.as_declared()`.
   `as_declared()` is compared against `tomllib`'s own parse in
   `::test_a_declared_result_report_round_trips_through_as_declared`, so an
   invented default or a dropped key is an inequality rather than something a
   reviewer must notice.

Beyond the lane surface, the **default parameter is the mechanism**:
`execute_plan(result_report=None)` is what every other caller (R2 candidates,
R3 canary halves, the `environment_command` probe) gets without opting out.
The whole pre-existing suite — 4,300+ tests, all green in the gate — exercises
those paths unchanged.

### ☑ "fault-injection regression tests for every completeness-check failure shape (truncated write, wrong shape, zero-test report)"

The twelve-shape table above, in both exit directions (24 parametrized cases),
plus stale-report, symlinked-path, missing-parent-directory and
never-written cases, plus the eight reader-level documents and the five
core-level summaries in `tests/test_result_reports.py`.

---

## Beyond the box: things asserted because a reviewer would ask

**The stale-report hole is closed, and proven closed.**
`::test_a_stale_report_from_an_earlier_run_is_removed_and_never_read`
pre-creates a valid all-green report, runs a command that writes nothing and
exits 1, and asserts both that the verdict is `FAIL` and that the stale file is
**gone**. This is the single most dangerous shape the feature could have had:
the crash that stops a report being written is also the crash that leaves the
previous run's report at the declared path, so without reserving and arming,
a genuine crash would be waved through as a `PASS` — the exact hazard A-073
exists to prevent. The mechanism is `safeio.reserve_output`/`arm`/`consume`,
the same discipline the coverage artifact and the ingested mutation report
already get (B046's own reasoning).

**The other terminals are untouched.**
`::test_a_declared_report_does_not_rescue_an_expired_budget` — a killed
process has no exit code to tiebreak, so `BUDGET_EXCEEDED`/`LANE_TIMEOUT`
survives even with a complete report already on disk.
`::test_a_declared_report_does_not_rescue_a_refused_argv_append` — A-095's
gate returns before anything launches, and the test asserts a pre-existing
file is **still there**, proving the reservation is not even built on that
path.

**The happy path is unchanged field-for-field.**
`::test_a_green_run_with_a_declared_report_is_the_unchanged_pass_shape` — no
reason code, `returncode == 0`, and both output tails still `None`. The tails
are retained only on the terminal that is no longer plain-green.

**The config surface refuses by naming the field.**
`tests/test_config_result_report.py::test_a_malformed_declaration_refuses_naming_the_field`
— 11 shapes (not a table, either key missing, unknown key, unregistered
format, absolute path, `..` escape, empty path, non-string values, `.git`
component), each asserting both the specific message and that the refusal
names the lane.

**Checkpoints 2 and 3 are provably not built.**
`tests/test_result_reports.py::test_the_registered_vocabulary_is_exactly_checkpoint_one`
asserts `RESULT_REPORT_FORMATS == {"vitest-json"}`, and
`::test_an_unregistered_format_refuses_rather_than_guessing` shows
`pytest-json-report` refusing rather than being silently ignored.

---

## Constraints, each checked against the tree rather than remembered

| SR-6 constraint | Status |
|---|---|
| No `Outcome`/`EXIT_CODES` change | `src/assay/errors.py` untouched — not in any commit's diff |
| No change to A-073's default | The three "undeclaring lane" proofs above; `execute_plan`'s `passed` reduces to `proc.returncode == 0` when no summary exists |
| No new `ReasonCode` | `COMMAND_FAILED` only; `errors.py` untouched |
| No verdict-schema change | `VERDICT_SCHEMA_VERSION` untouched; `LANE_SCHEMA_VERSION` stays 2 (A-432 precedent for an additive field) |
| No `run-gate.py` change | not in any commit's diff |
| No dstdns-side argv change | none — dstdns's own follow-up |
| No retry heuristic | none |
| No cross-container CPU-quota primitive | none |
| Checkpoints 2/3 not built | asserted by test, above |
| `Claim.detail` decision | **not used** — `Claim._check_detail` forbids detail on a PASS, and the PASS direction is the one worth annotating; provenance rides on the retained `returncode` + tails instead. Reasoning in the LOG. |

## Round-1 review reconciliation

Review: `assay-WAVE-B078-REVIEW-round1.md` (`27b66c25`) — **REJECT, 4
blockers.** All four addressed; repair commits `388f23a2`, `f535f04e`,
`92803df3`. Reconciled against the reviewer's own frontmatter table.

| Blocker | Fix | Evidence |
|---|---|---|
| **1** — the tiebreak never reached the R0 command of any R1+ lane, so RG-45's live repro was unfixed | `_execute_snapshot_unit` gains a forwarded `result_report=None`; `_run_prepared_lane`'s baseline is the sole caller that passes it (controller ruling D-1), with `create_missing_parents=True` for the snapshot it owns | the four R0+R1 tests above, mutation-verified |
| **2** — `result_report` declarable but inert on any R1+ lane | Resolved by construction once blocker 1 landed: every lane shape's R0 claim now consults it, so no load-time rigor refusal is needed. Pinned mechanically rather than asserted | `tests/test_result_report_wiring_sweep.py` (4 tests): every `execute_plan`/`execute_command` call site must pass `result_report=` or carry a written reason in `EXCLUDED_SITES`; `REQUIRED_SITES` catches the reverse (a site that *stops* passing it — round 1's exact defect) |
| **3** — the branch's own "canary halves are unchanged by construction" was false | `canary._run_pipeline` passes `result_report=None` explicitly (controller ruling D-2), via a sentinel default on `execute_command` so "the lane's declaration" and "no report at all" stay distinct | `::test_the_legacy_canary_pipeline_never_consults_a_declared_report`, its control `::test_execute_command_honours_the_lane_declaration_by_default`, the behavioural `::test_a_declaring_lane_produces_identical_canary_behaviour`, and `test_result_report_wiring_sweep.py::test_the_legacy_canary_pipeline_is_pinned_as_excluded_by_value` (pins the *value*, so flipping it fails there rather than nowhere) |
| **4** — CONSUMERS.md documented a `returncode` verdict field that does not exist | Documentation corrected, no schema change (controller ruling D-3). A `PASS` carrying output tails is now named as the detection method (OBS 4), since a plain green run omits them | `docs/CONSUMERS.md` "How to tell, from a verdict, that a report overrode an exit code"; `runner.py`'s corresponding comment |

### Round-2 fix-verification: ACCEPT-conditional, condition met

`assay-WAVE-B078-REVIEW-round2-fixverify.md` (`ad90bdc4`) confirmed all four
blocker fixes and raised one test-only condition, which is now fixed.

`test_a_declaring_lane_produces_identical_canary_behaviour` was hollow. Its
lane ran `("/bin/sh", "-c", "exit 1")`, which writes no report at all, so both
sides fell back to A-073 and the equality held whether `_run_pipeline`
excluded the declaration or consulted it. The reviewer proved it by mutation:
with `canary.py` flipped to `result_report=lane.result_report`, the test still
passed.

It now runs `shell_writing(vitest_document(total=140, failed=0), exit_code=1)`
— the RG-45 shape — so a canary that consulted the report would return `PASS`
for the declaring lane and `FAIL` for the other, and the equality goes red. It
also pins the shared VALUE (`FAIL`/`COMMAND_FAILED`), so "both sides moved
together" cannot satisfy it either, and asserts the report file really was
written, so the test cannot silently revert to proving nothing.

**Re-verified by the same mutation:** with `canary.py` flipped to
`result_report=lane.result_report`, this test and the sweep's value-pin both
go red (2 failed); reverted, 49 passed. Behavioural enforcement of blocker 3
no longer rests on the AST pin alone.

Non-blocking observations: **OBS 2** actioned (the vitest reader joins the
pinned untrusted-JSON list, now nine). **OBS 5** actioned (DESIGN-GUIDE's
scope sentence was false in both directions and is now exhaustive: three
consulting sites, three named exclusions). **OBS 1** read and deliberately not
actioned — the unreachable `finished` check is defence-in-depth for
Checkpoints 2/3 and collapsing it would make the core format-specific.
**OBS 3** narrowed by blocker 1's fix (the parent-directory limitation is now
R0-only lanes); the reviewer's suggested `--progress` "declared report was not
usable" note is Checkpoint 2's, not this wave's.

**Acceptance-box status.** The backlog's Checkpoint 1 tick is reverted to
`[ ]`: it was ticked in round 1 on work that did not do what the box claims,
and it belongs to fix-verification rather than to the implementer. The
fault-injection tick the reviewer judged earned is left standing.

## One deviation, flagged not absorbed

The branch lives in `execute_plan`, not `execute_command` as the design
document says. `runner.py:1140-1142` is `execute_command`'s *docstring*; the
A-073 rule is implemented in `execute_plan`, and `execute_command` is not
called by `runner.py` at all — it is public API. Wiring only the named site
would have left every `run_lane` path untouched. The reviewer verified this
independently and confirmed it; the design document's SR-6 / "Migration
surface" pointer should be corrected on main.

**Corrected in the round-1 repair:** the first version of this section went on
to claim the direct R0-only branch is "the one RG-45 reproduces on". It is
not. `ui_unit` declares `rigor = ["R0", "R1"]`, so it runs through
`_run_prepared_lane`'s baseline unit instead. Three sites carried that false
claim and all three are fixed. Full reasoning in the LOG's repair section.

**Where the tiebreak is consulted, exhaustively** — three sites, one per lane
shape: `execute_command` (the R0 step and public API), `run_lane`'s direct
branch (R0-only), `_run_prepared_lane`'s baseline unit (R1/R2/R3). Everything
else keeps `execute_plan`'s `result_report=None` default: mutation candidates,
R3's canary halves (both the snapshot engine's and `assay.canary`'s legacy
pipeline, which passes `None` explicitly), and the `environment_command`
probe. `tests/test_result_report_wiring_sweep.py` pins that list mechanically.

## One known limitation, stated up front

On an **R0-only** lane, assay does not create the report's parent directory:
`create_missing_parents` is contractually reserved for callers that own an
ephemeral assay-managed snapshot (B006(b)), and an R0-only lane runs in the
consumer's live tree. A path in a not-yet-existing directory falls back to
A-073 there — safe, but silent. Documented in CONSUMERS.md with the working
shape (a project-root path), and asserted as a stated fact by
`::test_a_report_in_a_directory_that_does_not_exist_yet_falls_back`.

Every lane declaring R1, R2 or R3 runs inside a snapshot assay owns, so the
baseline path passes `create_missing_parents=True` and has no such limitation
— proven by
`::test_run_lane_r0_r1_baseline_creates_the_reports_parent_directory`, whose
command deliberately never `mkdir`s.
