# Wave REPORT — progress/resume family (2026-09-08)

Acceptance-box status per item, for all four items of the wave. Narrative,
commit hashes and the rulings are in `assay-WAVE-PROGRESS-RESUME-LOG.md`
beside this file.

**Registered gate: GREEN at `243de634`** — `tester-unified: PASS (exit 0)`,
all 12 `ASSAY_GATE_PHASE` markers, zero `ASSAY_GATE_DIAGNOSTIC` lines,
`ASSAY_REGISTERED_GATE_COMPLETE=1`. Verdict read from the log's own markers
in a separate step (LESSONS L4).

**Wave-wide invariants, held by every item:**

* `assay verify` is **unaffected** — progress and resume state are
  diagnostic, never evidence, and `verify.py` was not touched by any item;
* no verdict-schema change: `VERDICT_SCHEMA_VERSION` stays 10,
  `assay.toml`'s `schema_version` stays 2, `assay lanes --json`'s
  `inventory_schema` stays 1;
* stall detection stays entirely with the **caller** (run-gate RG-36); assay
  gained no stall threshold of its own, in any item.

---

## B067 — `budget = "unbounded"` — ACCEPTED, 3/3

| box | status | evidence |
| --- | --- | --- |
| `unbounded` with a missing unit bound refuses at load naming the unit | ✅ | `config._refuse_unbounded_without_unit_bounds`; `tests/test_config_unbounded_budget.py` (29 tests) covers R0/R1, ingested R2, native R2 and R3 |
| an unbounded R2 lane with `budget_per_candidate` runs to completion with no `LANE_TIMEOUT` path reachable (measured on a real lane) | ✅ | `test_a_real_unbounded_R2_lane_runs_every_candidate_with_no_lane_timeout` — measured `timeouts == [None, 45.0]` |
| CONSUMERS' worked mutation lane shows the recommended shape | ✅ | `docs/CONSUMERS.md`: `budget = "unbounded"` + `budget_per_candidate` + run-gate `stall_timeout` |

**One gap, filed rather than hidden: B076.** The unbounded R2 lane's own
baseline run is the one command left with no bound (the `None` in the
measurement above). Deliberate: `budget_per_candidate` is a per-*mutant*
bound and a baseline runs the whole suite, so tightening it there would
refuse healthy lanes. Filed with three options and none chosen; explicitly
out of this wave's scope.

---

## B064 — R0/R1 phase-boundary progress stream — ACCEPTED for the R0/R1 half, 3/4

| box | status | evidence |
| --- | --- | --- |
| a ruling recorded on whether the R0/R1 phase stream is built, naming the rejected alternatives | ✅ | LOG § B064 and the backlog entry: **BUILT**; rejected *nothing* (measured zero-byte file), *per-tier bespoke events* (A-429's uniform invocation shape), *runner-aware progress* (→ B073) |
| the phase vocabulary is CLOSED and identical across tiers, and `verify.py` is untouched | ✅ | `mutation.PROGRESS_EVENTS` plus `ProgressStream.emit`'s refusal (`test_the_progress_vocabulary_is_enforced_not_merely_documented`); `verify.py` unmodified |
| R3 progress/resume reuses B007's per-attempt identity, proven by a resuming multi-target canary test | ⬜ **NOT BUILT** | out of scope by design — see below |
| the measured claims are re-checked, not inherited, at the time of building | ✅ | re-measured: `--progress` on an R0/R1 lane produced nothing, from the `r2_declared` gate in `runner._run_prepared_lane`; the set of phases assay itself owns is exactly what the entry predicted |

**Why the R3 box is deliberately open.** B064's own entry says R3
progress/resume must be keyed by **B007's** per-attempt identity, and that
inventing a second identity is the thing to avoid. The wave prompt scopes
B064 to the R0/R1 stream. The box stays unticked and the entry stays open,
rather than being closed on a half that was not built.

**Additional acceptance the item earned beyond its boxes**
(`tests/test_progress_phase_stream.py`, 16 tests):

* an R0-only lane's stream is exactly
  `run → command_started → command_finished → verdict_written`, and the
  direct path emits **no** `snapshot_materialized` and no `coverage_parsed`
  — `test_the_direct_r0_path_never_claims_a_snapshot_it_did_not_take`;
* an R1 lane emits both of those, from the same closed vocabulary;
* `verdict_written` is emitted after `write_verdict`, from `cli` — including
  on the no-artifact path, where `destination` is reported as `null` rather
  than omitted;
* the heartbeat ticks on its interval, cancels with the command, refuses a
  sub-floor value **by name**, refuses a non-numeric value, is a no-op
  without `--progress`, and stops itself (and nothing else) on a write
  failure.

---

## B065 — enriched progress events — ACCEPTED, 3/3

| box | status | evidence |
| --- | --- | --- |
| a reader with ONLY the progress file computes rate, ETA and last-event age; the numbers agree with the verdict's counts and the run's measured wall time (real run, not a fixture) | ✅ | `test_a_reader_with_only_the_progress_file_computes_rate_eta_and_age` — a real CLI run; the `end` record's buckets are compared field-by-field against that run's own R2 claim, and `elapsed_s` against the wall time measured around `main()` |
| `assay verify` is unaffected (the stream is not evidence) | ✅ | `verify.py` untouched; the verdict names no progress destination |
| CONSUMERS' progress paragraph names the fields | ✅ | `docs/CONSUMERS.md` § *The progress stream* — a full per-event field table, the two universal fields, and the two meaningful `null`s (`budget_s`, `candidate_total`) |

---

## B066 — `--state-dir PATH` — ACCEPTED, 2/2

| box | status | evidence |
| --- | --- | --- |
| two runs of one commit from two different worktrees with the same `--state-dir`: the second resumes (`event: resume`, `resumed_total > 0`); a source edit between them re-executes the touched file's candidates | ✅ | `test_two_worktrees_of_one_commit_share_one_state_dir_and_the_second_resumes` (second run: `resumed_total` equals the first run's record count, `pending_total == 0`) and `test_a_source_edit_between_the_two_runs_reexecutes_the_touched_files_candidates` (`pending_total > 0`, new identities written beside the old) — two REAL worktrees of one repository, not a fixture |
| a `--state-dir` inside the judged tree and not git-ignored refuses before any work, naming the reason | ✅ | `test_a_state_dir_inside_the_judged_tree_refuses_before_any_work` — names `DIRTY_TREE` and the path, and the directory is not created; its counterpart `test_a_gitignored_state_dir_inside_the_tree_is_accepted` proves the refusal is about *visibility to git*, not about location |

**Additional acceptance the item earned beyond its boxes**
(`tests/test_state_dir_resume.py`, 7 tests): the default location is
unchanged when no `--state-dir` is given; an existing file at the path is
refused; and the verdict names no state directory, with `assay verify` still
returning 0 — resume state is not evidence.

---

## What this wave deliberately did NOT do

| not done | why |
| --- | --- |
| **B073** — a per-language live-test-progress adapter reading the runner's own output stream | explicitly deferred and explicitly larger; the heartbeat is a pure time-based tick and no part of B073 was started |
| **stall detection inside assay** | settled ruling, not re-litigated: it stays with the caller (run-gate RG-36). assay cannot judge its own stall, and a hung unit is already caught by its unit bound |
| **B076** — bounding the unbounded R2 lane's own baseline run | filed during B067 with three options and none chosen; fixing it inside this wave was explicitly out of scope |
| **B064's R3 half** — per-attempt canary progress and per-target resume | must reuse B007's per-attempt identity; building a second one here is what B064's own entry warns against |

## Status

All four items complete, gate green on the tip that carries them. **Not
merged, not released, no reviewer dispatched** — those are the controller's
steps.
