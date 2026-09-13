# Assay B097 implementation brief

## Assignment

Implement backlog B097 (P7 B6-b) in the isolated worktree
`/workspaces/vbpub/.worktrees/assay-b097`, based on the accepted B092+B098
implementation and the B096 implementation branch. Use Luna xhigh. Do not
modify the running P6 judged tree, the B092 gate worktree, run-gate,
cgroup-profiler, dstdns, or operator-owned dirty files. Do not merge, release,
or launch a mutation campaign.

## Context to read first

Read these exact sources before editing:

1. `assay/nyxloom-trove/4-backlog.md`, entry B097, plus the B091 entry's
   liveness contract and B096's adjacent vocabulary change.
2. `assay/src/assay/liveness.py`: `_PLUGIN_SOURCE`, `_append`,
   `_iter_events`, `_iter_test_events`, `baseline_event_gaps`,
   `baseline_test_events`, `count_test_events`, and `LivenessRunner._monitor`.
3. `assay/tests/test_liveness.py`: existing plugin-materialization,
   event-parser, calibration, and runner tests; preserve the B6-a
   session-finish progressing-tail regression.
4. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-REVIEW-round2.md`
   and `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-REVIEW-HANDOFF.md`,
   especially blocker B6 and RW-57's precise follow-up boundary.
5. `assay/README.md`, `assay/docs/DESIGN-GUIDE.md`, and
   `assay/docs/CONSUMERS.md` liveness sections; the public documentation must
   describe the new identity and legacy-record behavior.

## Implementation packet (normative)

### Owned interfaces and wire shape

The materialized pytest plugin is the producer and
`assay/src/assay/liveness.py` is the sole consumer. Every record written by
`_append` must retain its current fields and additionally carry:

```json
{"pid": 1234, "xdist_worker": "gw0"}
```

`pid` is the producer process's integer `os.getpid()`. `xdist_worker` is the
string value of `PYTEST_XDIST_WORKER` when it is present and non-empty; omit
that field when it is absent. Stamping is best-effort and exception-safe in
the same way as `_append`: a stamping/read problem must not alter the wrapped
test result. Do not invent a worker identity for records that do not have one.

Old records with no `pid` remain valid and retain the current single merged
timeline interpretation. New per-process behavior applies only when the
records needed for that calculation carry a valid integer `pid`.

### Required flow

1. `_append` copies or augments the record, adds the existing timestamp, then
   stamps the producer identity without mutating caller-owned data in a way
   that can leak across hooks. Keep append/JSON/file exception safety.
2. The parser reads valid NDJSON records as today. For `test` counts and
   `baseline_event_gaps`, partition stamped records by `pid` only when the
   relevant records have usable pid stamps. Ignore unrelated xdist worker
   `session_finish` records when deciding the candidate's own finish.
3. `count_test_events` counts the candidate's own test records exactly once;
   a multi-process events file with four tests must report four, not one
   controller record plus duplicated worker records. Preserve single-process
   and legacy no-pid behavior.
4. Baseline gap measurement computes each process's chronological gaps from
   its own timestamps, then takes the maximum per-process worst gap. Do not
   build a merged cross-worker timeline that can shrink a slow worker's gap.
   The leading gap must likewise be computed from the process/session identity
   that owns the first session-start record. Preserve the existing behavior
   when no usable pid identity is present.
5. Candidate session-finish detection must only recognize the candidate
   process's own stamped `session_finish`; a worker finish cannot arm the
   trailing grace while the controller/other process is still progressing.
   Keep the B6-a idle-conjunct: the grace expires only after the candidate's
   process tree has been idle for the whole grace. Do not add a CPU shortcut.

### Identity and malformed data rules

Treat a `pid` as usable only when it is an integer but not a boolean and is
positive. Treat a missing, boolean, non-integer, or non-positive pid as legacy
for the relevant record. `xdist_worker` is descriptive only; do not use it as
the process identity or invent a fallback pid from it. A mixed file should
use the new partition only if the records needed for the particular result
are stamped; otherwise use the documented legacy interpretation rather than
silently dropping evidence.

The consumer must not call `os.kill`, `/proc`, or any process lookup to resolve
an identity in the parser. The producer's stamped pid is the authoritative
identity; process-tree accounting remains the existing `LivenessRunner`
responsibility.

### Decision table

| Input | Result | Side effect |
|---|---|---|
| All relevant records have valid pid | per-pid count/gaps/finish | none |
| Single-process records have valid pid | same numerical behavior as before | none |
| Relevant records omit pid (legacy file) | merged legacy interpretation | none |
| Mixed stamped/unstamped relevant records | retain valid evidence under the documented legacy-compatible rule; never invent identity or drop the file | none |
| Worker `session_finish`, owner still emits events | no finish-grace arm from worker | candidate continues |
| Owner `session_finish`, owner/process tree idle for less than 30 s | no hung result | candidate continues |
| Owner `session_finish`, owner/process tree idle for full 30 s | existing hung result | kill only under existing runner policy |
| Malformed pid or worker field | tolerate as legacy/descriptive data | no refusal, no verdict change |

### Prepared proof and traceability

Add behavioral tests in `assay/tests/test_liveness.py` (or a narrowly named
liveness test module) for:

- real materialized plugin records from a subprocess, including positive pid
  and worker identity when set, and omission when unset;
- a synthetic interleaved xdist-shaped events file with controller plus two
  workers and four tests: exactly four completed tests and only the owner
  session finish counts;
- two workers whose timestamps are interleaved such that merged gaps are
  tighter than one worker's own slow gap: calibration uses the worker's own
  worst gap;
- legacy no-pid records, malformed pids, and mixed records;
- the existing B6-a progressing-tail regression and single-process behavior.

The tests must distinguish a wrong implementation that merely deduplicates by
nodeid, uses `xdist_worker` as identity, takes merged gaps, or accepts the
first session finish. Add a measured/structural test or report note for the
large-events-file cost; no timing threshold may replace behavioral coverage.
Update the B097 backlog row, `CHANGES.md`, README, DESIGN-GUIDE, and
CONSUMERS in the same change. State the compatibility behavior for old files.

## Scope / forbid

Only assay liveness code, its focused tests, B097's brief/report, backlog,
CHANGES, and the three adopter-facing docs are in scope. Do not change the
schema number, mutation scoring, B092 identity semantics, B096 CLI behavior,
run-gate, or any public liveness policy beyond the pid-aware parsing required
here. Do not add a new warning or a new refusal unless the existing contract
requires it.

## Verification and checkpoint

Use serial, PSI-gated commands with `nice -n 19 ionice -c 3`. Run focused
liveness tests, relevant CLI/runner tests, docs examples, and
`git diff --check`. Before any registered gate, commit the implementation and
report on a quiet tip. A fresh adversarial Luna xhigh reviewer is required
before merge; no reviewer may be a fork of this implementer. If context nears
the dispatch checkpoint, stop at a coherent boundary, write a successor brief
and retention prompt, commit, and return both hashes.

## BLOCKED rule

Stop and report BLOCKED with the exact failing command and evidence if the
required xdist identity or owner-finish semantics cannot be implemented while
preserving legacy files and B6-a. Do not broaden scope or make a product
decision; escalate only after the same concrete blocker remains after three
focused repair attempts.

