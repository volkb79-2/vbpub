# Verdict: REJECT

**Package:** RG-55 P4 (`RG-57` through `RG-61`)  
**Review:** fresh adversarial final review, round 3  
**Reviewer:** Luna xhigh  
**Tip reviewed:** `068c1dd0f80b55553291982cb87b70f56eb5f936`  
**P2 base:** `186461de5ef5e58031c10a65c3ebf1087dfae76f`  
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`

## Blockers

### B1 — RG-60 inflight records leak when the exec client cannot be spawned

`run-gate-project/run-gate.py:7699-7711` writes the exec inflight record and
then calls `subprocess.Popen(argv)`, but the `try/finally` that clears the
record starts only on line 7711. A synchronous `Popen` failure (for example,
an absent executable, permission failure, or process-table/resource failure)
therefore escapes without entering the `finally`: the record written on line
7699 remains indefinitely. The outer `main()` history handling does not clear
it. A later invocation can then treat a failed launch as a stale/live exec
watch, which violates the handoff requirement that the record be cleared on
every completion path. This is distinct from the required killed-client case,
where the record must survive for reconciliation.

Prescription: restructure `run_exec_lane` so the cleanup boundary encloses the
`Popen` attempt, with a nullable process handle and an outermost unconditional
`clear_inflight_record` finalizer. Keep the existing kill/reap behavior when a
process exists, and do not clear a record merely because the client is killed
while an already-started child is running. Add a focused test that writes the
record, makes `subprocess.Popen` raise synchronously, asserts the exception is
reported as the lane failure, and asserts the record is gone.

### B2 — Current SPEC and consumer guidance still describe the rejected
`getrusage` algorithm

`run-gate-project/SPEC.md:1648-1655` says the bare-host fallback is
“`getrusage`-based process accounting,” and
`run-gate-project/CONSUMERS.md:57-62` repeats that description. The shipped
implementation at `run-gate-project/run-gate.py:7897-7911` uses
`os.wait4(proc.pid, 0)` and the child-specific `struct_rusage`. The later
normative SPEC text at `run-gate-project/SPEC.md:1764-1777` correctly says
“never `resource.getrusage(RUSAGE_CHILDREN)`” and explains why that old
high-water algorithm was rejected. Thus the current public/spec surface is
self-contradictory about the measurement source, precisely at the capability
P4 repaired; an adopter reading the overview can believe the known
all-children contamination bug is still the shipped behavior.

Prescription: update `SPEC.md:1651-1654` and `CONSUMERS.md:57-62` to state
`os.wait4()` on the lane's own child, `ru_maxrss * 1024`, and the resulting
rusage limitations. Sweep the remaining current prose, including the test
docstring at `run-gate-project/tests/test_run_gate.py:1291-1294`, so no live
overview or adoption text reintroduces `getrusage` as the implementation.
Retain the historical explanation of the rejected algorithm where it is
explicitly marked historical.

## Non-blocking S-items

### S1 — Self-container identity is inferred from a name lookup

`run-gate-project/run-gate.py:1827-1845` treats `/etc/hostname` as a Docker
container name and accepts the first successful `docker inspect` ID. On a
plain host whose hostname collides with a live container name, this can target
an unrelated container; in a container with a customized hostname it can
silently choose the rusage path despite daemon profiling being available.
Prescription: establish and validate a self-identity invariant (or decline
the daemon path with an explicit “identity not provable” reason), and test
both the hostname/inspect disagreement and the collision case.

### S2 — Bare-host duration is quantized to whole seconds before averaging

`run-gate-project/run-gate.py:1049-1057` emits second-resolution timestamps,
which `finish_bare_host_profiling` subtracts at
`run-gate-project/run-gate.py:2151-2154`. A short but real child can therefore
produce `duration_seconds == 0` and `cores_avg == null`. Preserve fractional
duration internally or otherwise make the contract's precision and the
derived-average behavior explicit; add a sub-second oracle.

### S3 — Exec inflight coverage starts after daemon session creation

`run-gate-project/run-gate.py:7638-7651` can establish a daemon session before
the unconditional exec record is written at `run-gate-project/run-gate.py:7699`.
A client death in that interval leaves a live session with no RG-60 record.
The current placement follows the implementer handoff's chosen ordering and
is not the B1 launch failure above, but it remains a recovery window.
Prescription: either record an explicit pre-start state that can carry the
session later, or make session establishment and record publication one
recoverable transaction.

### S4 — A wait4 failure can be hidden by the ordinary host warning

At `run-gate-project/run-gate.py:7900-7905`, the wait4 exception uses `or` and
preserves an earlier “not running in a container” warning. If wait4 itself
fails, the resulting `profile_error` does not disclose that the accounting
operation failed. Preserve both causes, or prioritize the later concrete
wait4 failure while retaining the host-path explanation.

### S5 — The P4 evidence report has contradictory status headers

`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REPORT.md:1-9`
still identifies an old tip and says R2 is incomplete, while its later
Session 9 and Session 10 at `:810-873` record final R2 and branch-gate
results. This is evidence-integrity debt for the next reviewer, not a product
runtime blocker. Update the header/status or mark the earlier material
unambiguously historical.

### S6 — Malformed successful start envelopes can leak daemon sessions

`run-gate-project/run-gate.py:1891-1908` and `:2018-2031` assume that a
successful `ctl start` response's `target` is a mapping. A response with
`"target": null` raises after the daemon session has been created. The exec
caller replaces the state in its broad start guard, losing the client/session
needed to stop that session. Contract-invalid input should still degrade
without leaking infrastructure. Prescription: validate the response shape
before committing daemon state, or retain the original state and stop a
session created before the response-processing failure; add a null/non-mapping
target oracle for both lane paths.

## Claims not verified in this round

- A fresh round-3 `selftest`, `assay-r1`, and `assay-r3` were not launched. At
  `2026-09-14T00:53:34Z`, host memory PSI was `some avg10=54.08` and
  `full avg10=36.71`, above the handoff's `<=5` admission threshold. Two
  existing gate containers were also active:
  `run-gate-vbpub-tester-unified-132487-1789346798` and
  `run-gate-vbpub-r2-4167718-1789340985`. I did not stop or interfere with
  either one.
- The daemon-up live probe was not run. The handoff forbids starting or
  stopping the host daemon, and the current environment did not provide a
  safe low-PSI window for a gate run.
- `footprint --write` was not rerun because this review is authorized to write
  only this round-3 report; that command would modify the tracked footprint
  manifest. The existing manifest and prior transcript were inspected
  read-only.
- The committed round-1 and round-2 evidence was read separately and treated
  as inherited evidence, not as fresh round-3 execution. The reported final
  R1/R2/R3 gates therefore do not erase the two source/docs findings above.

## Live evidence

- `git rev-parse HEAD` returned
  `068c1dd0f80b55553291982cb87b70f56eb5f936`.
- The specified P2 base resolves to
  `186461de5ef5e58031c10a65c3ebf1087dfae76f`.
- The worktree was clean before this report was created. No product source,
  `scripts/cgroup-profiler`, `ciu`, dstdns, or unrelated worktree was
  modified, and no commit was made.
- The full required blind-first sequence was completed before reconciling the
  historical P4 LOG/REPORT/briefs: controller RW-26/RW-27, backlog RG-57..61,
  contract §§4.1/4.3, P4 implementer handoff, SPEC R-36/R-39/R-43/R-44
  before/after, then the full diff. Round-1 and round-2 review files were
  read and preserved; this file is round 3 only.
- `git diff --check` found no whitespace error in the P4 review scope. Its
  base-to-tip output also surfaced pre-existing unrelated-tree whitespace,
  which was left untouched.
- The only new filesystem write made by this review is
  `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round3.md`.
