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
The estate host rule requires serial pytest, and the controller reported
memory PSI above the launch threshold (`full avg10=6.84` on the fresh check,
after an earlier `14.75%` report). Therefore all pytest validation, including
the real materialized-plugin subprocess test, is explicitly deferred until a
fresh `cat /proc/pressure/memory` shows `full avg10 <= 5`. No gate, mutation
campaign, merge, release, or external-worktree action was performed.

A fresh adversarial Luna xhigh review and the registered gate remain
controller work before merge. The exact validation commands and their results
must be appended here once PSI permits their launch; no unrun check is claimed
as green.
