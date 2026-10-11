# RG-55 P5 — Run-Gate v1.1 integration report

**State: in progress; this report does not authorize merge, release, or
shipment.** The rev-63 pre-body placement barrier repairs P5 review-round-1
blocker B3 and has now passed four real-daemon acceptance probes (exec and
bare-host success/refusal pairs) plus 71 focused tests. The B3 repair,
documentation, probe fixture, and RW-555 evidence are committed as
`aee194b7e` on branch `rg55-p5-transport-port-20261010`. Final short-gate
evidence and the fresh Sol review are still pending. Earlier
`db6d` selftest and cgroup-profiler coverage receipts remain historical, not
exact-current-tree evidence. Fresh cgroup-profiler gates, Run-Gate
`selftest`/`assay-r1`/`assay-r3`/`doctor`, Nyxloom lint, fresh Sol review, and
exact-tree mutation/full gates remain outstanding.

## Candidate identity and release boundary

- Current resumed source checkpoint: rev 63, commit
  `aee194b7e` (`fix(run-gate): block lane body until requested placement is
  verified`); the B3 repair and its live probe evidence are described below.
  The next metadata-freeze commit will pin the Sol packet to this code commit.
- Main/origin base at this checkpoint: `efaf112945233e3a8f6d53f2fef136439d18d089`.
- Candidate: worktree `.worktrees/rg55-p5-transport-port-20261010`, branch
  `rg55-p5-transport-port-20261010`, HEAD
  `465f67d060dcbdb7a47e92e95ee082405f14ca67` at the time of the earlier
  package receipts. That checkpoint includes the prior integrated code
  `3ccb7528` plus the committed CP-19 guard, tests, and docs.
- Run-Gate revision: 63. Read-only `cmru status` from the candidate resolves
  Run-Gate `23.11.0`, Assay `9.1.0`, and
  cgroup-profiler `0.1.0` by default (no existing cgprofile tag). The wave's
  authorized cgprofile release target remains explicitly `1.0.0`, subject to
  cgprofile's own final gates; the P5 handoff's original `23.9.0` / revision
  43 is historical.
- This is an **integrated, not P5-only, diff**: the original code checkpoint
  had 67 paths across Run-Gate, Assay, and cgroup-profiler relative to main.
  It includes the RG-89
  observer work needed to make native R2 possible. A P5-only review verdict
  must not be used to claim the other included package work is released or
  complete; final review and release records must identify the complete
  integration scope and its remaining package-specific gates.
- The cgroup-profiler helper consolidation from `rg55-dry-helpers-20261004`
  (`e5d9962c25e18d443f5e3712c906fff57c0d561e`) is also included. RW-437
  records a fresh Sol/xhigh ACCEPT for provisional integration after its
  r0-r1 (2,275 tests, 100% line+branch) and r3 (7/7 canaries rejected).
  RW-438 explicitly says this is not release readiness: final-source R2 and
  the initial-DAMON-start fix's final validation remain separate
  requirements. Live DAMON evidence is now recorded below.

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

The following cgroup-profiler unit/coverage/canary paragraphs are historical
interim receipts from the dirty working tree; current committed-tree receipts
follow in a separate section below.

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

## Exact committed-tree cgroup-profiler gates

On candidate tree `465f67d060dcbdb7a47e92e95ee082405f14ca67`, cgroup-profiler
`unit` passed: 2,499 passed, 4 warnings, 126.08s, in exact container
`cgprofile-gate-712557-1791664303`. Whole-package `coverage` passed: 2,499
passed, 8,389 statements, 3,110 branches, 100% line and branch coverage,
125.35s, in `cgprofile-gate-714038-1791664472`. Both were verified under
`dev-gates.slice` at 3 CPUs and 1-GiB memory; the gate scripts removed their
own containers. The canary gate rejected all seven canaries (7 rejected,
0 survived, exit 0) in exact container `rg55-p5-canary-465f67d0-01`, detached
under `dev-gates.slice` with the same limits. A first harness attempt failed
before tests because its read-only root blocked Git's safe-directory write;
that exact container was removed and the retry used writable `/tmp`. No
product canary failed. Nyxloom lint passed, the interface-contract mirrors
were byte-identical, and `git diff --check` passed.

These receipts are exact for `465f67d0`; later report/log/packet commits
change final HEAD. Rerun cgroup-profiler unit, whole-package coverage,
canary, Nyxloom lint, Run-Gate `selftest`, `assay-r1`, `assay-r3`, and
`doctor` on the final quiet review tree. Earlier Run-Gate receipts on
`3ccb7528` remain historical only.

## Resumed gate evidence on `db6d5b73` (before this report/log refresh)

Run-Gate `selftest` completed PASS on the quiet candidate tree
`db6d5b73693ec1a42b0a4f5a254df145a2a6946a` at 2026-10-10 21:18:48Z. Its
separately read history record is run `05f3bbad164f46f31ba8566f59b0c3b3`,
exit 0, duration 148.785s; the exact container was
`run-gate-vbpub-selftest-753342-1791666979` (`dev-gates.slice`, 3 CPUs,
1-GiB memory). It reported 1,843 passed, 2 skipped, 1 warning, with
759/759 changed executable lines and 344/344 branches covered. The profile
was unavailable with `EBUSY` because host `DAMON_STAT=Y`; this did not change
the test verdict. The approved DAMON pair is separate live-measurement
evidence, not a requirement that every gate run have DAMON samples.

The cgroup-profiler registered `r0-r1` coverage lane then completed PASS on
the same `db6d` tree: 2,499 passed, 4 pytest warnings, 132.07s; all 8,389
statements and 3,110 branches were covered (100% line and branch coverage).
Its exact test container was `cgprofile-gate-757826-1791667475`, read back
under `dev-gates.slice` at 3 CPUs, then removed by the runner. The output also
contained one expected test-fixture diagnostic: it could not change a
temporary socket's owner in the unprivileged container; pytest still passed.
This is the package coverage lane, not a separate `unit` or canary receipt.

Run-Gate `assay-r1` did not start. Its admission preflight found 937 MiB used
of the 1,536-MiB gates slice with 1,063 MiB required; the registered runner
waited for capacity and was canceled after 300 seconds before creating a
container. The command exited 2 from that cancellation, with no assay verdict
and no product PASS/FAIL. It must be relaunched on the final quiet tree after
the competing gate releases capacity. No external container was stopped or
modified.

Nyxloom lint passed from `scripts/cgroup-profiler` on this candidate tree.
The Run-Gate directory has neither a local `nyxloom-trove/nyxloom.toml` nor a
host-registered project root, so `nyxloom lint` from `run-gate-project`
returns L0 (`no owning project found`) rather than linting its review packet.
I did not invoke `nyxloomctl lint`: its host registry includes `/workspaces/dstdns`,
which is explicitly outside this task's access boundary. This is a lint-scope
limitation, not a Run-Gate test verdict.

## Live carrier, placement, liveness, and DAMON evidence

The original live-probe entries below are historical checkpoint evidence;
they include the pre-CP-19 restore warning and are superseded for candidate
acceptance by the later subsection. The host singleton is now restored from
main, `cgprofile-host-daemon` reports the main revision, its socket is
listening, `DAMON_STAT=Y`, and its session registry is empty.

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
measurements recorded in the prior controller checkpoint. Those historical
logs do not supply candidate proof; current candidate-image probes and the
fresh DAMON pair are recorded in the following subsections.

### Candidate live acceptance on daemon image revision `465f67d0`

CIU-104 correctly refused `ciu up` from the candidate worktree because another
checkout owns the fixed host singleton. The candidate image was built locally
and its revision label verified; candidate `ciu up --dry-run` rendered. To
exercise candidate runtime code without bypassing ownership, the main
checkout's CIU lifecycle replaced the idle singleton with the local candidate
image and preserved its session volume. The daemon was then rebuilt from main
and redeployed via main CIU. No sidecar daemon was used.

The socket and Docker-exec carriers profiled the same synthetic target
container, one at a time. Socket session `s-20261010T204614Z-7e44` recorded 39
samples, 3,710,976-byte baseline, and 29,134,848-byte peak. Exec session
`s-20261010T204636Z-71e6` recorded 37 samples, 3,751,936-byte baseline, and
29,188,096-byte peak. The 53,248-byte peak difference is about 0.18%; both
start/watch/status/stop paths succeeded with DAMON off.

The CP-19 one-process probe (`s-20261010T204712Z-6ecd`) returned
`place-refused:origin-scope-would-empty`, `leaf: null`, and `pids_moved: 0`;
host systemd had no transient scope for the token. The anchored placement
probe (`s-20261010T204739Z-8f82`) moved one PID to its delegated leaf, retained
the Docker-origin anchor, then restored the PID to its original Docker scope.
Its summary had `placement.error=null`, the transient systemd scope was
`LoadState=not-found`, and the daemon returned to zero sessions/leaves.

The successful on-stall retry (`s-20261010T204842Z-fe02`) used
`--idle-bound 3 --on-stall kill --place`: one PID moved, the watcher returned
`stalled`/`killed`, applied `cgroup.kill`, and ended with reason `killed`.
The target exited, `placement.error=null`, its transient scope disappeared,
and the daemon was idle afterward. The first harness attempt had a Python
syntax error before the probe; its exact container was removed. The successful
retry is the evidence; the old `write-failed` warning is not being hidden or
relabelled—the repaired candidate now refuses the last-member move and the
anchored case restores cleanly.

### Fresh DAMON pair on `dev-gates.slice`

The approved pair used identical 96-MiB allocation / 8-MiB hot-set workloads,
`tester-unified:local`, 256-MiB memory limits, and 3-CPU caps. Each exact
container was verified under `dev-gates.slice`; the daemon had no concurrent
sessions. Off session `s-20261010T205743Z-446c` lasted 100s with 99 samples,
105,103,360-byte target memory peak, and no DAMON series. On session
`s-20261010T205934Z-123e` lasted 102s with 97 samples; DAMON status was `on`
for one target, hot-set median/p90 8,433,664 bytes and peak 8,974,336 bytes.
The host had `DAMON_STAT=Y` for the off run; it was temporarily set to `N`
for the on run, then restored and verified `Y`.

Aggregate `cgprofile.slice` CPU increased 2.045920 CPU-seconds over the 100s
off interval and 2.138164 over the 102s on interval. Duration-normalized
rates were 0.020459 and 0.020962 cores, a difference of 0.000503 cores
(about 0.0503% of one CPU). Three daemon `docker stats` samples were: off
2.02%, 1.91%, 13.70% CPU and 25.12, 25.23, 25.04 MiB; on 0.41%, 2.40%,
0.42% CPU and 31.61, 33.62, 35.22 MiB. Host loadavg1 was 2.28→1.74 off and
2.02→1.76 on; host memory PSI full avg10 was 0.97→0.03 off and 0.11→0.00
on. All PSI values stayed below the launch threshold. The earlier 19:46
pair estimated about 0.41% of one CPU, so these remain noisy sequential
aggregate observations—not a calibrated or universal overhead guarantee.
Neither host load nor profile data changed a functional verdict.

### Rev-63 placement-barrier acceptance on the current daemon

The four-lane fixture at
`run-gate-project/tests/fixtures/rg55-p5-placement-live/run-gate.toml`
exercised the candidate Run-Gate client against the CIU-managed
`cgprofile-host-daemon` over its Docker-exec carrier. The daemon was already
up and idle; it was not restarted or replaced. Host `dev-gates.slice` was
loaded at a 5-CPU quota and 3-GiB memory limit. The disposable runner was
`rg55-p5-placement-probe-runner`, ID
`e923da32bc2eae8f514808c724445a6b4f05c0225ccf0a3900271d1049993556`,
verified under `dev-gates.slice` at 3 CPUs and 512 MiB. It was removed by its
exact name after the probes. Host memory PSI `full avg10` stayed at 0.00% in
the launch and completion checks.

| Lane | Session / Run-Gate run ID | Outcome and placement | Body / cleanup oracle |
|---|---|---|---|
| `exec-success` | `s-20261011T012235Z-ad35` / `8b0827acabd32b626c7bd476a8bb2b12` | PASS, exit 0; one PID moved; leaf `/dev.slice/dev-gates.slice/rg-profile-aeb85c20f1c2f466e7fe4a369097fbc1.scope/rg-aeb85c20f1c2f466e7fe4a369097fbc1`; readback `memory.high=67108864`, `memory.max=134217728` | `profile session` and verified placement were recorded before `RG55_P5_EXEC_SUCCESS_BODY`; session stopped and daemon returned to zero sessions/leaves. |
| `exec-refusal` | `s-20261011T012255Z-d3f7` / `17e1312cfe9d37bb1d90a39d68b423e4` | Run-Gate infrastructure `ERROR`, CLI exit 2; `place-refused:over-slice`, zero PIDs moved, no leaf | `RG55_P5_EXEC_REFUSAL_BODY` absent; the session was stopped/finalized, no assay verdict was written, and daemon status returned to zero sessions/leaves. |
| `bare-success` | `s-20261011T012309Z-9bbe` / `35f0ab4946bcd35ffcf68139bb978af0` | PASS, exit 0; one PID moved; leaf `/dev.slice/dev-gates.slice/rg-profile-42fd7ae080c9377b2fda9c402e83f6dd.scope/rg-42fd7ae080c9377b2fda9c402e83f6dd`; readback `memory.high=67108864`, `memory.max=134217728` | Live `ctl status` showed the leaf and one live session while the lane slept; then `RG55_P5_BARE_SUCCESS_BODY` ran. Run-Gate disclosed its shared devcontainer baseline (5,028 MiB); after the process exited, daemon status had zero sessions/leaves. |
| `bare-refusal` | `s-20261011T012326Z-e719` / `14b6f762106bf470e7cbcf0503fd09c7` | Run-Gate infrastructure `ERROR`, CLI exit 2; `place-refused:over-slice`, zero PIDs moved, no leaf | `RG55_P5_BARE_REFUSAL_BODY` absent; the session was stopped/finalized, no assay verdict was written, and daemon status returned to zero sessions/leaves. |

Both refusal lanes disclosed the bounded one-second R-45 wait and proceeded
to the daemon placement request; they did not turn the over-cap refusal into
a candidate failure or silently execute unplaced. All four lanes used
`kind = "command"`, so none produced an Assay candidate verdict. The
fixture's ignored `.run-gate/history.json` records the four structured
outcomes; the probe logs are under `/tmp/run-gate/lanes/{exec-success,
exec-refusal,bare-success,bare-refusal}/` for this host session.

The focused local iteration command
`pytest -q tests/test_run_gate.py -k 'TestPlacementBarrier or
TestExecLaneProfilingWiring or TestBareHostProfilingWiring'` passed 71 tests
with 1,646 deselected (6.05s), after `py_compile` and `git diff --check`.
This is targeted iteration evidence, not a substitute for the registered
final gates below.

## Still required

1. Freeze the Sol packet and final report, then rerun cgroup-profiler unit,
   whole-package coverage, canary, Nyxloom lint, Run-Gate `selftest`,
   `assay-r1`, `assay-r3`, and `doctor` on that quiet tree. The `db6d`
   receipts above are useful history but are not exact-current-tree evidence.
2. Obtain a fresh Sol xhigh P5 review of the complete integrated diff. Any
   review repair requires same-reviewer verification and rerunning affected
   gates. Provisional merge is authorized after review and short-gate
   evidence; it does not satisfy release readiness.
3. Run `assay-r2` and `gate-full` asynchronously against the exact judged
   tree in attached CIU worktrees. Any code repair must be backported and
   judged again before release. Classify every survivor and resolve or
   explicitly backlog each remaining safety/quality finding.
4. Treat Assay as a separate package in this integrated diff: CMRU currently
   resolves its next version as `9.1.0`, and Assay's own registered
   `tester-unified` release gate must pass on its final source tree before
   releasing that package. Run-Gate P5 gates do not certify Assay's package
   suite. cgroup-profiler's first combined P1/P6 release remains `1.0.0`; do
   not create a `1.1.0` release absent new material changes. Resolve the
   current CMRU release state before publishing/installing any package.

No package has been released or installed from this candidate, and no
provisional merge has been made.
