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

`tests/test_runner_result_report.py::test_verified_complete_zero_failure_report_passes_over_a_nonzero_exit`
— a real `/bin/sh` writes a real 140-test, 0-failure vitest document and then
exits 1. Asserts `outcome is PASS`, `reason_code is None`, and that
`returncode == 1` survives on the artifact (the report decides the outcome; it
does not rewrite what the process did).

`::test_the_r0_claim_for_the_rg45_shape_is_a_pass_claim` carries the same
scenario through `build_r0_claim`, because the `Claim` is what a consumer's
gate actually reads.

`::test_run_lane_direct_r0_path_applies_the_tiebreak` proves it on the
**shipped** path — `run_lane`'s direct R0-only branch, which is where RG-45's
own reproduction (dstdns `ui_unit`, an R0-only `kind = "assay"` lane) runs.
`::test_run_lane_direct_r0_path_still_fails_without_the_declaration` is its
must-fail control: identical lane, identical command, identical report on
disk, no declaration → `FAIL`. Without the control the first test could pass
for a reason unrelated to the report.

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

## One deviation, flagged not absorbed

The branch lives in `execute_plan`, not `execute_command` as the design
document says. `runner.py:1140-1142` is `execute_command`'s *docstring*; the
A-073 rule is implemented in `execute_plan`, and the **shipped R0-only path
(`run_lane`'s direct branch — the one RG-45 reproduces on) never calls
`execute_command`**. Wiring only the named site would have shipped a feature
that passes every unit test and does nothing for the case it was built for.
Full reasoning, and the opt-in mechanism that keeps every other `execute_plan`
caller unchanged by construction, in the LOG.

## One known limitation, stated up front

Assay does not create the report's parent directory (`create_missing_parents`
is contractually reserved for ephemeral snapshot callers; R0 runs in the live
tree). A path in a not-yet-existing directory falls back to A-073 — safe, but
silent. Documented in CONSUMERS.md with the working shape (a project-root
path), and asserted as a stated fact by
`::test_a_report_in_a_directory_that_does_not_exist_yet_falls_back`.
