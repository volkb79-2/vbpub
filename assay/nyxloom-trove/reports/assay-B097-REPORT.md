# Assay B097 implementation report

## Result

B097 implements the B6-b liveness identity contract in `src/assay/liveness.py`.
The materialized pytest plugin now writes an authoritative positive producer
`pid` on every record and copies a non-empty `PYTEST_XDIST_WORKER` value as
descriptive metadata. The caller's record is copied before stamping, so hook
records cannot acquire timestamps or identity fields that leak into a later
hook.

The parser uses pid ownership only when the relevant records are completely
usable for that calculation. Test readers select the process that owns the
first `session_start` (the xdist controller's event timeline), preserving
repeated event records rather than deduplicating by `nodeid`. The monitor
passes its real candidate `proc.pid` to finish detection; when all finish
records are stamped, only that pid's `session_finish` qualifies. Baseline gap
calibration partitions timestamped records by pid, orders each process's
timeline by event timestamp, and takes the maximum process-local worst gap;
the leading gap is from the owner of the first `session_start`.

Missing, boolean, non-integer, or non-positive pids are legacy data. If the
records needed for a result are mixed or malformed, all valid evidence is
retained and the existing merged interpretation is used. `xdist_worker` is
never an identity fallback. B6-a's full post-finish idle grace, CPU rule,
budget behavior, schema, mutation classification, and public liveness policy
are unchanged.

## Traceability

| Contract | Implementation | Behavioral oracle | Controlled break distinguished |
| --- | --- | --- | --- |
| Producer identity and no caller mutation | `_PLUGIN_SOURCE._append` | materialized plugin subprocess records; direct JSON record checks | absent worker is omitted; stale caller identity cannot leak |
| Owner-only test count | `_selected_test_events`, `count_test_events` | controller plus two workers, four owner records | worker duplicates and equal-nodeid records do not become a nodeid set |
| Owner-only finish | `_read_events_progress`, `LivenessRunner._monitor` | stamped worker finish followed by owner finish and normal exit | first worker finish cannot arm the finish grace |
| Per-pid calibration | `baseline_event_gaps` | interleaved and timestamp-reordered process timelines | merged gaps and file-order gaps produce different results |
| Legacy/malformed compatibility | pid validation and fallback branches | no-pid, malformed, and mixed fixtures | invalid records are not dropped and worker labels are not trusted |
| Existing semantics | unchanged B6-a monitor branches and legacy readers | retained progressing-tail and single-process tests | removal of the idle conjunct or legacy fallback goes red |

## Cost and residuals

The monitor still re-reads the NDJSON side file end-to-end on each polling
tick, so progress inspection is structurally `O(N)` in the current event-file
size. B097 does not introduce a timing threshold or claim a performance
improvement; bounded history/incremental parsing remain the separately filed
B095 work. The per-pid calibration adds one in-memory grouping/sort pass over
the already parsed baseline records. Behavioral correctness is covered without
making a verdict depend on machine speed.

The xdist fixture drives the shipped parser and monitor path with a real
interleaved controller/two-worker wire shape and verifies four owner-PID test
records, rather than launching an xdist pytest during this implementation.
The first controller validation run found one test-only `NameError` in the
new append regression (`json` was not imported); the import was added without
changing product code. After a fresh PSI-gated launch, the focused serial
suite passed: `tests/test_liveness.py`,
`tests/test_liveness_proc_helpers.py`, and
`tests/test_liveness_runner_monitor.py` — **116 passed**. The docs and
cross-document vocabulary suite passed separately — **42 passed**. The real
materialized-plugin subprocess test is therefore covered by the focused
suite; no xdist subprocess is launched because the estate host rule requires
serial pytest. No gate, mutation campaign, merge, release, or
external-worktree action was performed.

A fresh adversarial Luna xhigh review and the registered gate remain
controller work before merge. The B096 combined registered gate is already
running asynchronously on the parent branch; B097 itself still needs a
quiet-tip gate after review because this report update and import fix changed
the tip.
## Review status

The fresh Luna xhigh adversarial review initially rejected only the extra EOF
blank line in the brief (P2); all behavioral probes and compliant focused
suites passed. The controller removed that blank line in `23b75167`, and the
same reviewer accepted the fix-verification in
`assay-B097-REVIEW-round2-fixverify.md`. The report commit is evidence only;
the registered gate must judge the final tree that includes it.
