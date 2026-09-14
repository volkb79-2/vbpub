# Assay B097 adversarial review — round 1

## Verdict

**REJECT.** There are no P0 or P1 findings. There is one ranked P2 finding:
the required `git diff --check` gate is red because the new B097 brief has an
extra blank line at EOF. I did not repair it, per the review instruction.

Acceptance requires no ranked defect; therefore this is not an ACCEPT.

### P2-1 — required whitespace gate fails on the B097 brief

`assay/nyxloom-trove/reports/assay-B097-BRIEF-1.md:155` adds a blank line at
EOF. The exact check below reports:

```text
assay/nyxloom-trove/reports/assay-B097-BRIEF-1.md:155: new blank line at EOF.
DIFF_CHECK_EXIT=2
```

Prescription for the controller/implementer: remove the extra EOF blank line
and rerun `git diff --check`. No product code was changed by this review.

## Review target and scope

- Exact reviewed tip: `b83b99416b341422ecb4714494688f122302e892`.
- Reviewed range: `ee41553d..b83b99416b341422ecb4714494688f122302e892`.
- Worktree: `/workspaces/vbpub/.worktrees/assay-b097` only.
- The complete handoff was read first. I then read the named B091/B097
  backlog contracts, `src/assay/liveness.py`, the three named liveness test
  files, both named RG-55 P7 review files, the B097 brief/report, and the
  README/DESIGN-GUIDE/CONSUMERS liveness documentation. The full B097 diff
  was re-walked.
- No product code, schema, config, mutation scoring, release, merge, gate
  container, or mutation campaign was changed/launched. The only requested
  write is this review report.

## Commands and results

All test/probe launches were serial and used `nice -n 19 ionice -c 3`.
The guarded launches read `/proc/pressure/memory` immediately beforehand and
refused to launch when `full avg10 > 5`. One guard did refuse a probe at
`full avg10=7.61`; no test ran in that attempt.

### Handoff, diff, and scope

```text
sed -n '1,$p' assay/nyxloom-trove/reports/assay-B097-REVIEW-HANDOFF.md
=> complete handoff read successfully

git status --short && git diff --stat ee41553d..b83b99416b341422ecb4714494688f122302e892
=> clean before review; 12 files changed, 837 insertions, 74 deletions

git rev-parse HEAD
=> b83b99416b341422ecb4714494688f122302e892

git diff --name-only ee41553d..b83b99416b341422ecb4714494688f122302e892
=> CHANGES.md, README.md, docs/CONSUMERS.md, docs/DESIGN-GUIDE.md,
   nyxloom-trove/4-backlog.md, B097 BRIEF/REPORT/REVIEW-HANDOFF,
   src/assay/liveness.py, and the three named liveness test files only

git diff --name-only ... -- assay/src/assay/config.py assay/src/assay/mutation.py assay/src/assay/verdict.py assay/src/assay/schemas
=> empty; forbidden product/schema scope was unchanged
```

The exact `git diff --check ee41553d..b83b994...` result was the P2-1
failure quoted above; there were no other whitespace diagnostics.

### Focused liveness tests

First launch (before the later explicit PSI pause) printed
`full avg10=20.69` and completed `116 passed in 3.33s`, exit 0. Because that
launch began above the host-load threshold, I did not rely on it as the final
compliant evidence. The final guarded rerun was:

```text
psi=$(< /proc/pressure/memory); ...;
nice -n 19 ionice -c 3 python -m pytest -q \
  tests/test_liveness.py tests/test_liveness_proc_helpers.py \
  tests/test_liveness_runner_monitor.py
PRELAUNCH_PSI ... full avg10=0.97 ...
116 passed in 3.04s
FULL_FOCUSED_TEST_EXIT=0
```

### Required materialized-plugin subprocess probe

Command: a serial `PYTHONPATH=src nice -n 19 ionice -c 3 python - <<'PY'`
harness that materialized `assay_liveness_plugin.py`, then launched two
children with `subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p',
assay_liveness_plugin, test_file])`; each child inherited the outer nice and
ionice settings. The harness read memory PSI before each child launch.

The first harness attempt failed before launching a child because the source
layout was not on the outer interpreter path:

```text
ModuleNotFoundError: No module named 'assay'
PLUGIN_PROBE_EXIT=1
```

The corrected command added `PYTHONPATH=src` and passed:

```text
PLUGIN_SOURCE_CHECK True
gw0: child exit 0; records:
  session_start, phase, test, phase, session_finish
  all pid=43569 (positive int), all xdist_worker=gw0
absent: child exit 0; records:
  session_start, phase, test, phase, session_finish
  all pid=43571 (positive int), xdist_worker omitted
PLUGIN_PROBE_EXIT=0
```

This verifies the generated source, producer stamping, optional worker
metadata, valid JSON, and real materialized-plugin subprocess behavior.

### Interleaved controller/two-worker, owner finish, reordered gaps, and
legacy/malformed matrix

Command: a serial `PYTHONPATH=src nice -n 19 ionice -c 3 python - <<'PY'`
harness writing temporary NDJSON fixtures and calling the shipped parser.
The normal fixture contained controller pid 100, worker pids 200/300, four
controller-owner test records including duplicate nodeids, four worker
duplicates, and all three session finishes.

```text
FULL_COUNT 4 ['same', 'same', 'b', 'c']
FULL_FINISH_OWNER (14, True)
WORKER_FINISH_ONLY (2, False)
REORDERED_GAPS (6.8999999999999995, 4.0)
CASE legacy COUNT 2 GAPS None
CASE malformed COUNT 4 GAPS None
CASE mixed COUNT 3 GAPS (1.0, 1.0)
CASE label_only COUNT 2 GAPS (1.0, 1.0)
CASE bool_owner COUNT 2 GAPS (1.0, 1.0)
IDENTITY_MATRIX_EXIT=0
```

The additional combined-axis attack, not present as one implementer test,
combined same descriptive worker labels on different pids, duplicate nodeids,
inter-process file interleaving, deliberately timestamp-reordered records
within one pid, and worker-before-owner finishes in one file. Result:

```text
COMBINED_AXIS COUNT 1 NODES ['same'] GAPS (9.0, 1.0) FINISH (7, True)
```

The result is the expected owner-pid selection and worst process-local gap;
the combined attack did not find a behavioral defect.

### B6-a progressing tail and process-tree behavior

```text
psi=$(< /proc/pressure/memory); ... guard full avg10 <= 5 ...;
nice -n 19 ionice -c 3 python -m pytest -q \
  tests/test_liveness_proc_helpers.py tests/test_liveness_runner_monitor.py
PRELAUNCH_PSI ... full avg10=1.94 ...
68 passed in 0.78s
B6_PROCESS_TREE_TEST_EXIT=0
```

This covered the B6-a progressing-tail control, owner/worker monitor branch,
full idle-grace conjunct, CPU-growth and `/proc`-failure behavior, real
process-group launch/kill paths, and real descendant process-tree traversal.

### Mixed finish identity boundary

Command: guarded serial `PYTHONPATH=src nice -n 19 ionice -c 3 python - <<'PY'`
fixture probe for a valid worker finish plus missing, boolean, non-positive,
or string pid finish records:

```text
worker-only (2, False)
missing-pid (3, True)
bool-pid (3, True)
nonpositive-pid (3, True)
string-pid (3, True)
owner (3, True)
MIXED_FINISH_BOUNDARY_EXIT=0
```

This is intentional legacy compatibility, stated in the brief and all three
adopter-facing documents: only a completely usable finish-identity set gets
owner-only filtering; mixed/malformed finish records retain the old any-finish
interpretation. It is recorded as a residual, not ranked as a defect.

### Docs/config/schema/anchor checks

```text
psi=$(< /proc/pressure/memory); ... guard full avg10 <= 5 ...;
nice -n 19 ionice -c 3 python -m pytest -q \
  tests/test_docs_examples_and_vocabulary.py tests/test_self_lane.py \
  tests/test_verdict_schema_is_packaged.py \
  tests/test_lane_schema_v2_locked_successors.py
PRELAUNCH_PSI ... full avg10=3.58 ...
73 passed in 10.74s
DOC_CONFIG_SCHEMA_TEST_EXIT=0
```

This verifies current-schema TOML examples, closed vocabularies, README
anchors, config loading, the lane schema, and packaged verdict-schema
consistency. No schema file is in the B097 diff.

## Findings and residuals

### Ranked findings

- P0: none.
- P1: none.
- P2: P2-1 above — required `git diff --check` failure from the extra EOF
  blank line in the new brief.

### Unranked residuals verified/documented

- Mixed or malformed finish identities intentionally retain legacy any-finish
  behavior. Thus the strict owner-only finish guarantee applies when the
  relevant finish identity set is fully usable; malformed mixed files are not
  silently upgraded to a new identity interpretation.
- B095's disclosed O(N) side-file reread and unbounded CPU history remain
  unchanged and were not reopened.
- The post-`session_finish`-to-process-exit interval remains the preexisting
  fixed-grace residual documented in the RG-55 review context; B097 does not
  claim to measure it.
- No registered gate was launched, no B096 gate was awaited or reopened, and
  no mutation campaign was launched. The report makes no green claim for
  either.

## Final review disposition

Behavioral B097 probes and compliant focused suites are green, but the
required diff hygiene gate is red. The final verdict is **REJECT** pending the
P2-1 whitespace correction; no repair was made in this review.
