# Assay RG-55 merged sidecar review — 2026-09-14

Verdict: **ACCEPT**

Reviewed the merged assay sidecar at exact tip
`b032c2689f95ddb476f43d139fa02efb1b3e383c` on branch
`review/rg55-merged-sidecar-luna-xhigh`, relative to `assay-v6.2.0`.
This was a fresh review in an isolated worktree. No product code, unrelated
package, merge, or release was changed. The only new file is this report.

The requested `assay/docs/README.md` path does not exist at this tip. The
shipped user-facing README is `assay/README.md`; the documentation test's
three-document set confirms that path, and it was reviewed together with
`assay/docs/DESIGN-GUIDE.md` and `assay/docs/CONSUMERS.md`.

## Evidence

Read before review:

- `nyxloom/reference/AUTHORING.md`;
- `assay/nyxloom-trove/reports/assay-B092-B098-REVIEW-R1-2026-09-13.md`;
- `assay/nyxloom-trove/reports/assay-B097-REVIEW-round2-fixverify.md`;
- `assay/CHANGES.md`, `assay/README.md`, `assay/docs/DESIGN-GUIDE.md`, and
  `assay/docs/CONSUMERS.md`;
- current history and `git diff assay-v6.2.0..b032c268 -- assay`.

The reviewed range is 30 assay files, with 2,099 insertions and 324
deletions. `git diff --check assay-v6.2.0..b032c268 -- assay` produced no
output and exited 0.

### PSI gating

The initial host reading was above the execution limit:

```text
some avg10=9.05
full avg10=7.16
```

No tests or probes were launched at that reading. Every subsequent execution
was guarded by an `awk` check requiring both `/proc/pressure/memory` `some`
and `full` `avg10 <= 5`. The allowed reading captured before the final
evidence pass was:

```text
some 0.00
full 0.00
```

No xdist or mutation campaign was launched, and the authoritative clean-tip
tester-unified gate at `0303a24d` was not rerun.

### Focused serial tests

One serial `pytest` invocation, guarded by the PSI check, ran:

```text
python -m pytest -q \
  assay/tests/test_config_mutation.py \
  assay/tests/test_config_vocabularies.py \
  assay/tests/test_cli_lanes.py \
  assay/tests/test_mutation_isolation.py \
  assay/tests/test_mutation_judge_identity.py \
  assay/tests/test_mutation_judge_identity_properties.py \
  assay/tests/test_liveness.py \
  assay/tests/test_liveness_proc_helpers.py \
  assay/tests/test_liveness_runner_monitor.py \
  assay/tests/test_docs_examples_and_vocabulary.py \
  assay/tests/test_runner_run_lane_r2.py \
  assay/tests/test_mutation_hung_bucket.py \
  assay/tests/test_mutation_progress_budget_plan.py
```

Result: `439 passed in 34.02s`, exit 0.

### Fresh combined-axis probes

A PSI-guarded `PYTHONPATH=assay/src python` probe used temporary NDJSON and
temporary repositories, without changing the worktree:

- B092 combined a filtered report entry with an omitted unsafe-symlink path,
  changed an excluded entry and omission, changed an included entry, checked
  declaration-order stability, and checked the distinct legacy/explicit-empty
  domains. All expected digest equalities and inequalities passed.
- B097 combined duplicate owner nodeids, two worker pids, same event-file
  interleaving with out-of-order timestamps, worker-only finish, a malformed
  test pid, and a malformed finish. Owner-only counting, chronological
  per-pid gaps, candidate-finish isolation, and the documented mixed legacy
  fallback all passed.
- B096/B098 derived the six shipped canonical buckets
  (`killed`, `survived`, `crashed`, `budget_exceeded`, `equivalent`, `hung`),
  checked the `error` alias is CLI-only, and found every bucket plus
  `identity_exclude` on the three-document surface.
- An actual one-test pytest subprocess through the materialized liveness
  plugin returned 0, emitted positive producer pids, and emitted
  `session_finish`.

The exact probe completed with:

```text
B092 combined digest: PASS ...
B097 combined event stream: PASS ...
B096/B098 vocabulary/docs: PASS ...
R-36h live plugin subprocess: PASS ...
ALL FRESH PROBES PASS
```

A separate PSI-guarded local-link probe checked 35 local Markdown anchors
across the three user-facing documents and found zero dangling anchors.
The shipped `test_docs_examples_and_vocabulary.py` also passed all 42 tests,
including current-schema-v2 checks for live TOML examples through the shipped
loader, closed vocabulary coverage, and README-to-DESIGN-GUIDE anchors.

## Contract assessment

- B092 preserves the legacy digest when the key is omitted, gives an explicit
  empty declaration a tagged identity domain, filters only matching frozen
  Git-manifest entries (including omitted paths), and leaves final judge
  inputs such as argv outside that tree-content filter.
- B096 derives help text from `MUTATION_BUCKETS`, retains the `error` →
  `crashed` convenience alias, and keeps the runtime parser closed and
  canonical.
- B097 stamps producer pid identity, uses the first-session owner for test
  counts, computes gaps per process, ignores foreign finishes when identity is
  usable, and preserves merged legacy behavior for malformed or mixed records.
- B098 documents the complete native mutation bucket surface while retaining
  `killed / (killed + survived)` arithmetic. Liveness/profiling observation
  changes did not alter the normal subprocess verdict in the live probe or
  focused runner tests.

Findings: P0 none; P1 none; P2 none. **ACCEPT.**
