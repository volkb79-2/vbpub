# RG-55 P5 — Run-Gate v1.1 integration report

**State: in progress. This report is not a merge, release, or shipment
authorization.** Candidate worktree
`.worktrees/rg55-p5-transport-port-20261010` is based on
`3ccb752800d5a0c8de7d756b86882e3f1edc64fe` and currently has uncommitted P3
origin-scope protection, regressions, and documentation. The candidate has
passed an interim package unit gate on that working tree; final exact-tree
gates, live acceptance against the updated daemon, fresh Sol review, and
asynchronous exact-tree R2/full-gate work remain outstanding.

## Candidate identity and release boundary

- Main/origin base at this checkpoint: `efaf112945233e3a8f6d53f2fef136439d18d089`.
- Candidate base: worktree `.worktrees/rg55-p5-transport-port-20261010`,
  branch `rg55-p5-transport-port-20261010`, base commit
  `3ccb752800d5a0c8de7d756b86882e3f1edc64fe`; the current P3 delta is
  intentionally uncommitted while its tests and docs are being completed.
- Run-Gate revision: 61. CMRU reports the next Run-Gate version as
  `23.11.0`; the P5 handoff's original `23.9.0` / revision 43 is historical.
- CMRU reports Assay's next version as `9.1.0`; cgroup-profiler has no
  release tag, and its first release remains explicitly `1.0.0`.
- This is an **integrated, not P5-only, diff**: the code checkpoint has 67
  paths across Run-Gate, Assay, and cgroup-profiler relative to current main. It includes the RG-89
  observer work needed to make native R2 possible. A P5-only review verdict
  must not be used to claim the other included package work is released or
  complete; final review and release records must identify the complete
  integration scope and its remaining package-specific gates.
- The cgroup-profiler helper consolidation from `rg55-dry-helpers-20261004`
  (`e5d9962c25e18d443f5e3712c906fff57c0d561e`) is also included. RW-437
  records a fresh Sol/xhigh ACCEPT for provisional integration after its
  r0-r1 (2,275 tests, 100% line+branch) and r3 (7/7 canaries rejected).
  RW-438 explicitly says that this is not release readiness: the final-source
  R2, the initial-DAMON-start fix's final validation, and live DAMON evidence
  remain separate requirements.

## Earlier Run-Gate short-gate evidence on base commit 3ccb

The Run-Gate rows below are historical receipts for `3ccb7528`, before the
current P3 edits; they are not final-tree release evidence.

| Lane | Result | Evidence |
|---|---|---|
| `selftest` | PASS | Run `4a9cbeddf7ee3f29a25621d406762906`; 1,843 passed, 2 skipped; 759/759 changed executable lines and 344/344 branches. The Docker create argv included `--cpus=3`; a separate `docker update` receipt was not preserved for this run. Log: `/tmp/rg55-p5-selftest-3ccb7528.log`. |
| `assay-r1` | PASS | Run `49322b531420fe9f89d4a9d2f2bcf767`; source-backed Assay `9.0.1.dev104+g3ccb75280`; 995/995 changed lines and 288/288 branches; exact container `run-gate-vbpub-assay-r1-587973-1791657151` read back `NanoCpus=3000000000`. Log and separate cap receipt: `/tmp/rg55-p5-assay-r1-3ccb7528.log` and its cap file. |
| `assay-r3` | PASS | Run `85ecbeb775766130e62d4f498e3a4956`; both canaries rejected, zero survived; exact container `run-gate-vbpub-assay-r3-602580-1791658125` read back `NanoCpus=3000000000`. Log and cap receipt: `/tmp/rg55-p5-assay-r3-3ccb7528.log` and its cap file. |
| `doctor` | PASS | 23 checks: 21 OK, 1 expected linked-worktree warning, 0 failures, 0 skips, 1 informational item (12 stale locks reported but not removed). The profiler answered; `DAMON available` is only an interface/capability indication, not proof a session can start or produce samples. Log: `/tmp/rg55-p5-doctor-3ccb7528.log`. |

The `selftest` history record is PASS/exit 0. R1's structured Assay verdict is
PASS. R3's history record is PASS/exit 0. Each verdict was read separately
from its gate log. R-45 admission delayed the first two gates by about ten
minutes; they proceeded after the bounded wait. Memory PSI full avg10 stayed
below the launch threshold. Profiling did not alter any gate verdict.

After the P3 edits, the cgroup-profiler package's declared `unit` gate passed
on the current working tree: 2,499 passed, 4 warnings in 129.60s. It ran in
`cgprofile-gate-697833-1791663112` under the verified `dev-gates.slice`, with
the 3-CPU cap and a 1-GiB container memory limit read back. The container was
removed by the gate script after its exit. This is interim dirty-tree evidence;
the final committed tree still requires unit, whole-package coverage, and
canary gates.

The whole-package `coverage` gate also passed on the current working tree:
2,499 passed in 125.64s; 8,389 statements and 3,110 branches, with 100% line
and branch coverage. Its exact container was
`cgprofile-gate-700570-1791663449`, with the same verified slice, CPU cap,
and memory limit. The `canary` gate passed all seven canaries (7 rejected,
0 survived) in `rg55-p3-origin-canary-20261010-r2`, exit 0, using a detached
`tester-unified:local` container under `dev-gates.slice` with a 3-CPU cap and
1-GiB memory limit. A first harness attempt exited before tests because its
read-only root blocked Git's global safe-directory config; the exact failed
container was removed and the successful retry stored that config in writable
`/tmp`. No product canary failed. These remain working-tree—not frozen
commit—receipts and must be repeated on the final committed tree.

These receipts cease to be exact-HEAD evidence after any candidate commit.
After the final report/packet/log edits, rerun the required short gates and
doctor on the final quiet tree before review.

## Live carrier, placement, liveness, and DAMON evidence

The currently running host daemon is `cgprofile-host-daemon`, image
`cgprofile:local`, reports `cgprofile 0.0.0-dev`, and currently has zero live
sessions. Its socket carrier is mounted in the cockpit. The live evidence
below was collected against that deployed image before the candidate's new
origin-scope guard. Candidate-daemon acceptance must use CIU to replace this
host singleton only while it has zero sessions, then restore the daemon from
main after the probes; do not create a second daemon or bypass the CIU
lifecycle.

**Carrier parity:** the same synthetic workload completed through the socket
and Docker-exec carriers, one measurement at a time with the daemon otherwise
idle. Socket session `s-20261010T193535Z-f65d` recorded 7 samples; exec
session `s-20261010T193543Z-1173` recorded 8. Both measured the same peak
cgroup charge, 116,047,872 bytes. Start/watch/status/stop succeeded on both
carriers. This is end-to-end parity evidence, not merely a `version` probe.

**Placement and charge accounting:** a one-process Docker scope reproduced
CP-19. Session `s-20261010T193616Z-b361`, target container
`rg55-p3-place-20261010` (ID
`c03e86c8d90e1e2b3ed2b17ebf4296c44b9afaf4cb611936df7fe2e3eba2bcc5`), moved
its only PID 484261 into
`/dev.slice/dev-gates.slice/rg-profile-rg55p3place20261010.scope/rg-rg55p3place20261010`.
The scope disappeared when its origin became empty; stop refused with
`place-refused:identity-unavailable` rather than guessing a restore unit. This
is the live trigger for CP-19 and the candidate's pre-move refusal.

The normal shared-scope path succeeded with a separate synthetic parent/child
container: the parent remained in the Docker origin scope while the selected
child moved into a delegated leaf; stop restored it and left no session or
leaf. A second delayed-allocation probe allocated 64 MiB after placement and
observed leaf `memory.current` 67,358,720 bytes and `memory.peak` 67,633,152
bytes, demonstrating charge-based accounting. A pre-allocation placement
probe measured zero leaf charge, consistent with the documented fact that
moving a process does not migrate existing page charges.

**On-stall kill:** the token-tagged synthetic child was killed after its
20-second idle bound; the summary recorded `stalled`, verdict `killed`,
21 samples, and the exact profiler-owned `cgroup.kill` path. The watch ended
after finalization and the target process was gone. However, the same summary
also reported `placement.error=place-refused:write-failed` while trying to
restore a process during teardown; after the exact synthetic container was
stopped, the daemon reported zero sessions and no leaves. This proves kill
finalization, but **does not prove warning-free placement cleanup**. Repeat
this probe on the candidate daemon and investigate if the warning recurs; do
not erase or relabel it as a clean stop.

**Matched DAMON overhead pair:** both runs used the same synthetic 96-MiB
workload with an 8-MiB hot set, a 256-MiB container limit, and a 3-CPU cap.
The daemon had no other sessions. DAMON-on required the approved temporary
`DAMON_STAT=N` host window; the host setting was restored to `Y` afterwards.
The off run (`s-20261010T194630Z-66ed`) lasted 95.594s and increased aggregate
`cgprofile.slice` CPU usage by 1.628854 CPU-seconds. The on run
(`s-20261010T194830Z-8b71`) lasted 95.377s, produced 91 samples for one
target, and reported hot-set median 8,425,472 bytes; aggregate slice CPU rose
by 2.017820 CPU-seconds. The observed difference is 0.388966 CPU-seconds
over the paired ~95-second interval (about 0.41% of one CPU). This is one
sequential pair, not a universal overhead guarantee; the slice counter is
aggregate daemon-slice usage, not a per-session CPU attribution.

For the off run, three daemon `docker stats` samples were CPU 0.21%, 3.79%,
0.30% and memory 215.1, 215.4, 52.97 MiB; host load was about 1.84 to 1.34.
For the on run, CPU was 2.82%, 4.57%, 5.07% and memory 55.77, 57.68,
60.23 MiB; host load was about 1.23 to 1.07. These noisy instantaneous
samples reflect the host's shared scheduling and page-cache state and should
not be interpreted as a calibrated percentage overhead. During the off run
host memory PSI full avg10 briefly reached 4.68% and later 0.10%; on-run end
was 0.00%. Host contention remained an observed condition, never a verdict
input.

The host's `DAMON_STAT` setting is now verified `Y` through `host-escape`.
The cockpit itself cannot read that host sysfs parameter. `host-escape` also
reported that the cgroup2 mount lacked `nsdelegate`,
`memory_recursiveprot`, and `memory_hugetlb_accounting`, then restored those
options via its supported host self-heal. Raw `/tmp/rg55-p3-*` probe logs are
not present in this resumed environment; this report preserves the exact
measurements recorded in the prior controller checkpoint. Candidate-daemon
live probes and a durable evidence bundle are still required for final P3
closeout.

## Still required

1. Commit the P3 implementation/docs and run final cgroup-profiler unit,
   whole-package coverage, and canary gates plus Run-Gate selftest, assay-r1,
   assay-r3, and doctor on the final committed tree.
2. With the daemon idle, use CIU's supported host-singleton workflow to
   exercise the candidate daemon: socket/exec parity, pre-move refusal of a
   one-process origin unit, anchored shared-scope placement/restore, and
   on-stall kill. Establish whether the earlier restore warning was a benign
   zombie/reap race or a product cleanup defect, and restore the main-managed
   daemon state after the probe.
3. Refresh the report and Sol packet with those receipts and the exact input
   revision. Obtain a fresh Sol xhigh ACCEPT; any review repair requires
   same-reviewer verification and rerunning affected gates. Then provisionally
   merge the authorized integration.
4. Run `assay-r2` and `gate-full` asynchronously against the exact judged
   tree in attached CIU worktrees; any code repair must be backported and
   judged again before release. Classify every survivor and resolve or
   explicitly backlog each remaining safety/quality finding, then perform the
   package releases/installations authorized by the release plan.

No package has been released or installed from this candidate, and no
provisional merge has been made.
