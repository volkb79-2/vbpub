# topos-P90-bounded-process-sampler — REVIEW (independent frontier merge gate)

**Reviewer:** independent frontier reviewer (Opus 4.8), 2026-07-15 — did NOT
write this code.
**Branch reviewed:** `feat/topos-P90-bounded-process-sampler` @ `996a3db`
(diff `main...feat/topos-P90-bounded-process-sampler`, 12 files, +1794/-2).
**Verdict:** **APPROVED.** No blocking defects; no reviewer fix needed. Not
merged (merge is the pipeline's job).

## What was verified (not trusted from the REPORT)

Gates re-run by me in the branch worktree venv (`textual` present):

```
# topos-suite (frontmatter gate, zero-skip full suite)
$ python -m pytest topos/tests -q
1642 passed in 192.48s      # 0 failed, 0 skipped, exit 0

# focused P90 file
$ python -m pytest topos/tests/test_p90_process_sampler.py -q
24 passed in 3.63s

# py-compile gate
$ python -m compileall topos/src/topos/procs topos/src/topos/config.py \
    topos/src/topos/model.py topos/src/topos/registry.py \
    topos/src/topos/query/semantics.py            # exit 0

# git diff --check (declared in Gates section)
$ git diff --check main...feat/...              # no whitespace errors
```

The zero-skip requirement is met (0 skipped across 1642 tests).

## Oracle-by-oracle adversarial assessment

All eight oracles are backed by tests that assert **observable outcomes**, not
implementation shape. None are hollow.

- **O1 (capped union / no eligible top candidate dropped / ≤64):**
  `test_o1_bounded_union_...` proves cold candidates and outside-top-N overlap
  keys are excluded while pinned + top-N CPU/IO are retained;
  `test_o1_hard_cap_never_exceeded...` proves the *highest*-CPU keys are the
  survivors under cap pressure (`set(retained) == set(keys[:hard_cap])`), so the
  negative ("omits an eligible top candidate") is genuinely covered. **Holds.**
- **O2 (I/O burst stays recently-hot 60s):** `test_o2_recently_hot_grace_then_expiry`
  drives 3 ticks (t=0 burst, t=30 within grace → `recently_hot` true / `io_hot`
  false, t=61 dropped). Grace is measured from last-hot timestamp, not refreshed
  by mere retention — correct. **Holds.**
- **O3 (PID reuse never joins history):** verified at two levels. Pure:
  `ProcessKey(pid,start_ticks,boot_id)` with different `start_ticks` are
  unequal and produce different `entity_key()`. End-to-end
  (`..._end_to_end`): a reused PID with a NEW `starttime` whose counters
  already exceed the old process's last `utime` yields `proc_cpu_pct is None`
  (cold under the new key) instead of a spurious continuation delta. I
  independently traced the sampler: `_compute_rates` keys `_prev` by the full
  `ProcessKey`, so the new incarnation finds no prior baseline. The old key
  survives one extra tick as an explicit **typed vanished row** (present=0,
  carrying the *old* comm), never merged into the new key's series. **Holds.**
- **O4 (deterministic caps/tie order):** `test_o4_...` runs identical input 6×
  with deliberate rate ties; `_rank` and `_sort_key` break ties by
  `(pid, start_ticks)`, fully deterministic. **Holds.**
- **O5 (pinned survives pressure):** `test_o5_...` pins a key colder than every
  hot candidate under `hard_cap=3`; it survives and only non-pinned keys are
  evicted. In code, pinned keys are placed first and only `non_pinned` is
  truncated. **Holds.**
- **O6 (typed states, not zeros):** `..._vanished_...` asserts `proc_present==0`
  (src `exact`) with `proc_cpu_pct/rss/io` = `None`/`unavail_kernel`;
  `..._permission_denied_...` monkeypatches `read_io`→`unavail_perm` and asserts
  the I/O metrics carry `src == "unavail_perm"`, never a 0. procfs readers
  return `(None, unavail_perm|unavail_kernel)` on `OSError`, distinguishing
  `PermissionError`. **Holds.**
- **O7 (owner provenance, no duplicated cgroup totals):** `..._provenance_...`
  and `..._end_to_end_no_duplicated_cgroup_metrics` assert docker/ciu/slice/unit
  join is correct AND that no cgroup accounting metric (`ram`, `cpu_pct`,
  `io_r_bps`) appears on a process row. `join_owner` reuses the same tick's
  already-enriched `Entity` table for lookup only — it reads no sysfs and sums
  nothing. Process metrics are a disjoint `proc_*` namespace on `kind="process"`
  entities in a **separate Frame stream**. **Holds.**
- **O8 (large-PID benchmark + mutation tests):** 2500-PID `sample()` stays under
  the cap and the 10s budget. The two mutation tests are genuinely
  load-bearing: they `monkeypatch.setattr(candidates_mod, "_rank"/"_apply_hard_cap", ...)`
  on the module globals that `select_candidates` resolves at call time, so the
  mutants (lowest-rate ranking; never-truncate cap) actually flow through and
  the assertions diverge from the correct run. Not decorative. **Holds.**

## Contract cross-checks beyond the oracles

- **Contract 6 (P81 sensitivity before frontend exposure):** the sampler emits a
  raw `process` block on the EntityFrame. I confirmed this is safe: P81's
  `_visit_entity_frame` (`daemon/redaction.py`) passes through only
  `entity`/`metrics`/`findings` and **fails every other field closed to the
  `sensitive` marker**. So the `cmdline` (secret-bearing) block is redacted for
  any below-`sensitive` principal — no leak. `redact_process_row` /
  `classify_process_field` add a finer-grained seam (comm=operational,
  cmdline=sensitive) for a future typed visitor; currently exercised only by
  `test_contract6_...`. Forward-looking, not dead-in-a-harmful-way.
- **Contract 3 / D-019 strict validation:** `ProcessConfig.__post_init__` raises
  `ProcessConfigError` on negatives and on `hard_cap < pinned_cap`; defaults are
  `20/20/16/60.0/64`, matching ROADMAP line 49 ("20+20/16/60-second/hard-64").
  Coverage telemetry (eligible/candidate/sampled/omitted/reasons/warm-up) is
  emitted in `host_meta["process_coverage"]`.
- **Contract 5 (feeds P88, no second engine):** `ProcessFrameSource` implements
  the `FrameSource` contract exactly like `DaemonHistoryFrameSource`
  (`provenance` + `evicted` + ascending-seq `iter_source_frames`);
  `test_contract5_...` runs a real `run_query(summary, proc_cpu_pct)` and gets
  `semantic=="rate"`, `sample_count==2` (3 frames − 1 cold start). `proc_cpu_pct`
  / `proc_cpu_host_pct` were correctly added to `_EXPLICIT_RATES` so the rate
  semantic resolves.
- **Scope:** all touched files are under `topos/**` (frontmatter `scope.touch`).
  `EntityKind` gains `"process"`; the model round-trip test and the full suite
  confirm no exhaustiveness breakage elsewhere.

## Non-blocking observations (no fix applied; below the fix bar)

1. `sampler.py` ~line 402–404: `cgroup_key, cgroup_src = read_cgroup_path(...)`
   leaves `cgroup_src` unused and is followed by a no-op
   `if cgroup_key is None: cgroup_key = None`. Purely cosmetic dead code — no
   behavioral effect, so I left the diff untouched rather than churn it.
2. Below-`sensitive` principals lose the *entire* `process` block (incl. `comm`,
   `ppid`, owner) to fail-closed redaction, i.e. `comm`'s intended `operational`
   classification is not yet honored end-to-end. This is over-redaction (safe),
   and the granular seam already exists for the future frontend visitor — not a
   contract violation.

Neither rises to a defect requiring a reviewer commit.

## Verdict

**APPROVED.** Every declared oracle is verified against a real, non-hollow test;
the mandatory gates (`topos-suite` zero-skip full suite, `py-compile`) were
re-run by me and pass; `git diff --check` is clean; scope is respected; and the
PID-reuse, typed-state, and no-duplicated-cgroup-accounting invariants hold under
independent tracing. No merge performed.
