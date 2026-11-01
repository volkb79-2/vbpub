# WP8 continuation — 2026-11-01

## Delivered

- Assay emits verdict schema v17. Native R2 records its effective
  `judgment.r2.jobs` count and paired `attribution`; the analyzer checks both,
  compares the count to the lane ceiling, exposes them in campaign reports,
  and refuses v16 verdicts.
- The verifier accepts v17 only. The v17 schema digest and the one-diagnostic
  v16 refusal are covered by the verdict conformance tests. R3 records
  `canary_attempt.cause`, which verification uses to re-derive the import-break
  attempt.
- Process-tree evidence uses protocol `assay-process-tree/2`. Verdict evidence
  omits host-varying `cleanup_seconds`, records the leader signal and sorted
  descendant identities, and retains B171's underlying completed suite result
  and report summary on leaked descendants. Such candidates remain `crashed`.
- Resumed records from the previous judge identity are re-executed. The v16 to
  v17 adoption guidance explains this and the unchanged Run-Gate, CMRU and
  dstdns integration contracts.
- README, DESIGN-GUIDE, CONSUMERS, schema documents and `CHANGES.md` were
  updated. Existing shard and campaign bindings remain as previously pinned;
  no RS1/RS2 fields were introduced. B180 and B202 are closed in the backlog.
  B171's implementation is complete, with registered `tester-unified`
  acceptance pending the controller. No separate B203+ finding was identified.

## Verification

- Focused v17 producer/verifier selection: **270 passed**; frozen digest and
  single-diagnostic v16 refusal included.
- B171 candidate projection test, including retained FAIL/return code and
  descendant identity while staying crashed: **1 passed**.
- AP-1 shared termination alignment oracle across producer, resume
  revalidation, verifier and verdict model: **19 passed**.
- Docs examples, cross-document anchors and public vocabulary test:
  **63 passed**.
- Focused producer, process-tree, result-report, verifier, resume, SQL
  qualification, untrusted-JSON and result-report wiring batch:
  **1,204 passed**. Its first tracked-tree inventory check raced the staged
  test-file rename; the rename was staged and the inventory check then passed.
- Run-Gate top-level-field compatibility regression: **1 passed**.
- Analysis suite with the two live R2 probes excluded: **573 passed,
  2 deselected**, with **100% line and branch coverage** for
  `assay_analysis`.
- The unfiltered analysis suite was attempted: **572 passed, 2 failed**. Both
  failures require the tester container's judge provenance and visible cgroup
  hierarchy (`CGROUP_OBSERVATION_UNAVAILABLE`).
- Pyflakes on changed Assay source, test and gate Python files passed.
  `git diff --check` passed.

No Run-Gate lane or Docker command was run; those container gates are assigned
to the controller. The full unfiltered analysis result and live R2 behavior
therefore remain unverified in this cockpit.

## Reference availability

The requested `COW-PLAN-OPUS-2026-10-30.md`,
`reports/ASSAY-OPUS-REVIEW-AND-PLAN-2026-10-31.md`, and
`codex/assay-ap-common.md` were not present in this checkout. The available
PR1b continuation independently records their absence. This implementation
used the WP8 contract in the task, the available PR1b continuation and the
B171/B180/B202 backlog entries. No waiver-window or RS1/RS2 changes were made.
