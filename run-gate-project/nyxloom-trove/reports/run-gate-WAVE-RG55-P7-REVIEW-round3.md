# run-gate-WAVE-RG55-P7 — adversarial review, round 3 (final)

**Reviewed commit:** `cd1f84fe96388335d68d847d8c6a32bc974377bd`
on branch `assay-liveness`, parent `6f3aefad4ed10d05901c03089df9b65891b7cb3b`.
The review worktree was clean before and after the review. This is the last
permitted round.

## VERDICT

**ACCEPT.** Round-2 blocker B6 is repaired as RW-57 required. No blocker
remains.

## B6 behavioral verification

`assay/src/assay/liveness.py:1214-1217` adds the one binding conjunct:

```python
and idle_for >= _HUNG_SESSION_FINISH_GRACE_S
```

The monitor updates `last_progress_at` from any newly parsed event and from
stdout/stderr size growth before evaluating `hung`. The post-finish branch
therefore now requires both (a) at least 30 s since the first observed
`session_finish` and (b) at least 30 s since the latest event/output progress.
An early xdist worker finish cannot expire an active tail; a genuinely idle
post-finish process still can, without consulting CPU. The calibrated
idle/flat-CPU branch and the elapsed-budget branch are untouched.

The regression in
`assay/tests/test_liveness_runner_monitor.py:332-422` is behavior-bearing:

- The retained true-hang test uses growing CPU and a 600 s idle bound to
  isolate the post-finish branch. With no later event it expires exactly at
  virtual `t=31` (finish first observed at `t=1`); with a later event at
  `t=20`, it expires exactly at `t=50`. Thus the new idle conjunct and its
  inclusive `>=` boundary are both pinned.
- The xdist-shaped test uses one merged events file, three `session_start`
  records, an early worker `session_finish` at `t=5`, one `test` every 2 s
  from `t=6` through `t=200` (98 exact records), two later finish records at
  `t=201/202`, growing process-tree CPU, a 600 s monitor bound, and normal
  controller exit at `t=203`. It asserts normal return and no kill/reap.
- On the parent expression, the first finish arms `session_finish_at=5` and
  the finish-only disjunct raises `LivenessHungExpired` at `t=35`, despite a
  test event at `t=34`; the new regression therefore fails for the exact old
  defect rather than merely sharing its vocabulary.

Independent targeted execution, with cache/bytecode writes disabled:

```text
test_session_finish_then_still_alive_is_hung_after_a_full_idle_grace
test_xdist_session_finishes_do_not_hang_a_progressing_candidate
3 passed in 3.40s
```

No second full gate was launched.

## Compatibility and scope

- The repair range changes seven files only: `liveness.py`, its monitor test,
  README, DESIGN-GUIDE, CONSUMERS, CHANGES, and the assay backlog. It does not
  change config loading, runner dispatch, mutation classification/scoring,
  verdict serialization/verification, the schema-v11 JSON or its W7 locked
  copy. Blob ids for `config.py`, `runner.py`, `mutation.py`, `verdict.py` and
  `verdict.schema.json` are identical at `6f3aefad` and `cd1f84fe`.
- Consequently RW-36 injection, the default `liveness = "auto"` policy,
  native-only disclosure, `hung` scoring, and the documented same-number
  schema-11 compatibility break remain exactly as accepted in round 2.
- No run-gate or cgroup-profiler implementation file changes in this repair;
  their relevant blobs are identical at parent and tip. R-36h best-effort
  profiling and D-15 daemon-safety behavior are therefore preserved, with no
  profiling path capable of changing this verdict. The only verdict behavior
  changed is the intended assay correction: a progressing candidate is no
  longer falsely classified `hung`.
- `git diff --check 6f3aefad..cd1f84fe` is clean.

## Docs and deferred work

The three user documents have distinct, synchronized jobs:

- README states the visible guarantee and links the rationale.
- DESIGN-GUIDE records why later progress resets the grace and why pid-aware
  parsing remains deferred.
- CONSUMERS states the exact two hung branches, xdist behavior, the remaining
  duplicate-count/merged-calibration limitations, absence of a new WARN, and
  the existing `liveness = false` escape hatch.

CHANGES records the B6 fix under `Fixed` and retains the schema-11/`auto`
disclosures. The limitations are not disguised as fixed. Backlog rows
`B093`–`B098` each appear in frontmatter and as a body entry with a mechanical
oracle. In particular, B097 faithfully carries B6-b/B6-c forward: pid/worker
stamping, controller-finish recognition, exact xdist test counts and per-pid
gap calibration. This is the disposition RW-57 ordered.

## Gate binding

The controller-supplied live transcript for the exact tip reports
`tester-unified: PASS`, wheel installation, B006(a) qualification with
`claim[R0..R3]=PASS`, independent self-hosting `7 passed`, pyflakes clean,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, and final exit `0`.

That transcript is independently bound by
`assay/.run-gate/history.json` in the review worktree:

```json
{"commit":"cd1f84fe96388335d68d847d8c6a32bc974377bd",
 "dirty":false,"exit_code":0,"outcome":"pass",
 "history_eligible":true,"started_at":"2026-09-13T10:14:52Z",
 "duration_seconds":1177.521}
```

The gate started after the reviewed commit was created, and HEAD remains that
exact commit with a clean worktree.

## Non-blocking close-out

**N1 — the tracked P7 implementer LOG/REPORT are historical and stale.**
They end at session 10 / `03f42bb9` and the REPORT still says the registered
gate was not run because that text predates both the B6 repair and the
controller's final gate. They contain no `cd1f84fe` entry. Do not use that old
sentence as current gate evidence; close it out in the controller's records.
This is not a product or acceptance blocker: the exact-tip gate is bound above,
and this shared-main round-3 record is the authoritative final review record.

## Changed-file observations

- `assay/src/assay/liveness.py`: one semantic conjunct plus accurate contract
  text; no threshold, CPU, budget, event-reader or classification change.
- `assay/tests/test_liveness_runner_monitor.py`: the old finish-only test is
  strengthened into two exact idle-boundary cases; one xdist-shaped
  progressing-tail regression added.
- `assay/{README.md,docs/DESIGN-GUIDE.md,docs/CONSUMERS.md,CHANGES.md}`:
  synchronized B6 behavior and candid xdist limitations.
- `assay/nyxloom-trove/4-backlog.md`: B093–B098 filed exactly as RW-57 ruled.
- No other product code, schema, fixture, profiling, gate-driver or unrelated
  file changed.
