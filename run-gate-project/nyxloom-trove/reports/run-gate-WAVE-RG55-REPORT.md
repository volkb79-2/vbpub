# RG-55 wave report — final closeout

Closed 2026-10-21 (operator decision, all recommendations of
`RG55-FINISH-PLAN-20261021` accepted). Live-evidence snapshot 2026-10-11;
release facts verified against git tags on `origin/main` (`9b33474c7`) on
2026-10-21. This replaces the "interim closeout" of 2026-10-11, whose
"not complete or released" statement was overtaken by the INT-01 integration
and the publications of 2026-10-17/18.

**RG-55 as scoped (P0-P8) is shipped and closed.** The residue is tracked as
named backlog rows, not as the wave. Two things are explicitly NOT delivered:
mutation (R2) evidence on the final trees, and the six cgprofile review
blockers (fixed in the next package, cgprofile 1.1.0).

## What shipped, per package

| Package | Delivered | Release / commit |
|---|---|---|
| P0 | contract, fixtures, RG-53 (`coverage_gate` branch coverage) | within run-gate 23.7.0 |
| P1 | cgprofile daemon (sessions, DAMON, socket/exec control, store) | `cgprofile-v1.0.0` (`a27fd282f`, 2026-10-17) |
| P2 | run-gate client: per-lane profile, footprint manifest, history | `run-gate-v23.7.0` (`652a118cb`, 2026-09-13) |
| P4 | RG-57..RG-61 (bare-host profiling, warnings, in-flight record, doc drift) | `run-gate-v23.8.0` (`409439c15`, 2026-09-16) |
| P5 | socket/exec carriers, `ctl watch`, placement barrier, best-effort admission wait | `run-gate-v24.0.0` (`d3e7d1101`, 2026-10-17), via `e40476b1f` and the R2-cap fix `da511e7bf` |
| P6 | cgprofile follow-ups CP-2, CP-4..CP-12 (events, report charts, limits table, watch, placement, journaled recovery) | folded into `cgprofile-v1.0.0` (no 1.1.0 was ever tagged for it; RW-569) |
| P7 | Assay B091 progress-judged candidates, `hung` outcome, `--rejudge`; follow-on B107 | `assay-v6.2.0` (`bd461dcd5`), `assay-v8.0.0` (`e4e3db005`) |
| P8 | mdt host-setup `dev-gates.slice`, `/run/cgprofile` mount, cap-watcher knob | `a71c46b08` (review ACCEPT round 3) |
| later | CP-18..CP-21, RG-89 native observer, RG-98..RG-103 | `cgprofile-v1.0.0`, `run-gate-v24.0.0`, `run-gate-v24.0.1` (`f793a3b8a`, 2026-10-18) |

P3 (this close-out): live probes and DAMON overhead below; final report and
backlog reconciliation 2026-10-21. Host-side installation of the P8
host-setup and deployment of the released daemon/wheel are NOT verified by this
report (cockpit is read-only); they are tracked as the deployment check that
gates the dstdns brief.

## Not delivered, and where it is tracked

- **R2 (mutation) was not obtained** for cgprofile (1,593 candidates), P5 or
  P6. Exact-tree attempts ended `ERROR/EXEC_FAILED` (Assay B145, an
  un-attributable shared-ancestor resource event), and the contract-1 campaign
  was stopped on purpose (RW-569). By operator decision the next releases go
  without R2 and the release notes must say so. R2 is revisited when the CP-24
  stop-protocol code lands (needs Assay B157/CP-23 for parallel attribution).
- **cgprofile review blockers.** The Sol review REJECT of 1.0.0 (six blockers:
  placement after origin change, unconfirmed DAMON teardown reporting `ok:true`,
  implicit unbounded `cgprofile.slice`, missing live acceptance of the
  contract-2 observer from a gated peer, dirty/ineligible saved receipts and
  unproved image identity) is fixed in cgprofile 1.1.0. CP-24..CP-26 (stop
  protocol, quarantine, DAMON pool provenance) follow later; the CP-24 packet is
  parked on `park/stash-2026-10-17-park-cp-24-25-26-work-from-cgprofile-rel`.
- **RG-56** atomic admission (with CP-15) is deferred; its design and seven
  implementation commits stay as branch refs `rg56-admission*`.
- **RG-108** (stop-pending sessions treated as unavailable) must release before
  a CP-24 daemon is installed.
- **RG-99** live concurrent exec-mode acceptance remains open.
- The four `[no-ciu-root]` rg55 worktrees are left to the ciu worktree fix.

## Live acceptance and measurement

The detailed live evidence is preserved in
[`run-gate-WAVE-RG55-P5-REPORT.md`](run-gate-WAVE-RG55-P5-REPORT.md). On the
candidate daemon, Docker-exec and Unix-socket carriers both completed
start/watch/status/stop against synthetic workloads. A matched same-target
probe differed by 53,248 bytes of measured peak (about 0.18%); this is parity
evidence for those runs, not a general accuracy guarantee.

Placement probes exercised both refusal and success paths. Moving the only
process out of its Docker origin correctly refused when that would remove the
origin scope; the anchored shared-scope case moved the selected process into a
profiler-owned leaf and restored it on stop. A candidate on-stall kill probe
ended with the target killed, placement restored, and the transient scope
gone. The accepted P5 review also records the live success/refusal pair for the
Run-Gate placement barrier. The earlier teardown `write-failed` observation is
not concealed: the repaired candidate's anchored and refusal probes completed
cleanly, and the P5 report records the distinction.

The latest matched DAMON pair used the same 96-MiB allocation/8-MiB hot-set
workload, 256-MiB memory limit, and 3-CPU cap, with the daemon otherwise idle.
The DAMON-off run lasted 100 seconds; the DAMON-on run lasted 102 seconds and
produced 97 samples, with an 8,433,664-byte hot-set median. Duration-normalized
aggregate `cgprofile.slice` CPU rates were 0.020459 and 0.020962 cores. The
observed difference was 0.000503 cores, or about 0.0503% of one CPU. An earlier
95-second pair measured about 0.41% of one CPU. These are noisy aggregate
observations from sequential pairs, not per-session attribution or a universal
overhead guarantee. Instantaneous `docker stats` samples varied materially;
the host's load and PSI were recorded as context, never used to change a
functional verdict.

The on-run required temporarily setting host `DAMON_STAT=N`; afterward it was
restored and verified `Y`. The current host again reports `DAMON_STAT=Y`.
That host setting prevents simultaneous DAMON_STAT and DAMON sysfs monitoring;
therefore ordinary daemon profiling remains usable while DAMON enrichment can
report unavailable. The measured on-run is evidence for the explicit temporary
sysfs-monitoring mode, not a claim that DAMON is available under the host's
default setting.

## Integration and test status (2026-10-11 snapshot)


- P5's repaired code passed selftest, assay R1, and assay R3; a fresh Sol
  review accepted it, and it was provisionally merged to main as
  `e40476b1f6fdd3ab8df1d90cafe26ad54d122a30` (RW-560/561).
- The cgroup-profiler mutation cap was raised from 1,500 to 2,000 after source
  planning found 1,593 candidates. Fresh Sol review accepted the change;
  exact-tree R0/R1 and R3 passed. It was provisionally merged as
  `da511e7bf` (RW-562).
- Earlier exact-tree cgprofile R2 resumes on `06e7804e` ended with
  `ERROR/EXEC_FAILED`, not a mutation verdict: Assay B145 withheld outcomes
  after an un-attributable shared-ancestor resource event. The latest retry
  resumed saved state at 10:34:03Z in
  `.worktrees/rg55-cgprofile-cap-20261011` at `06e7804e`, container
  `run-gate-vbpub-r2-1274146-1791714842`, with a 3-CPU cap under
  `dev-gates.slice`. The required early check found the R0 baseline pytest
  command active at 10:35:11Z; no new candidate outcome was available yet.
  Based on the prior resume's 11 candidates in about 7.3 minutes, the rough
  estimate for 1,593 candidates is about 17 hours, not a guarantee. The exact
  tree remains quiet, and this retry is serialized to avoid another
  shared-ancestor attribution failure.
- The latest P5 R2 attempt on `05e7b6cd` ended with `ERROR/EXEC_FAILED` after
  resuming 13 records and completing four new candidates, not a mutation
  verdict. Assay withheld outcomes after the same un-attributable
  shared-ancestor resource event. Preserve its partial records and resume from
  the exact judged tree only after cgprofile R2 ends.
- No final `gate-full` receipt, survivor disposition, or release has been
  recorded. The cap worktree must remain unchanged until its R2 ends.

## Release and deployment status (as of the 2026-10-11 snapshot; superseded)

Historical. The tags above are the current truth; the version
and the install statements below were true on 2026-10-11 only.


Read-only `cmru status` reports `run-gate-v23.10.0` as the latest Run-Gate
tag and `run-gate-v23.11.0` as the next minor; cgroup-profiler has no tag and
the plan remains an explicit `cgprofile-v1.0.0` release. CMRU's default
untagged version suggestion (`cgprofile-v0.1.0`) is not the wave's approved
version. Assay's latest tag is `assay-v9.0.0`; the assay release belongs to
the coordinating Assay work, not this wave. The daemon container is running
from the local image `cgprofile:local`; no RG-55 package release is recorded.

The cockpit venv reports released `assay 9.0.0`, installed from a local wheel
whose SHA-256 was checked against `cmru resolve`. Installed Run-Gate remains
23.10.0 and refuses the current `run-gate.toml` because it does not recognize
`resource_observer`. Do not use that installed entrypoint for current source
gates; use the repository's source runner until the proper Run-Gate release
is available, then install the released wheel and verify it against the
current config. No RG-55 wheel was installed or published in this closeout
snapshot.

CMRU releases operate from an `origin/main` snapshot. The operator has since
authorized pushes at any time, but local `main` also contains provisional
Assay B132-B134 commits from a separate in-flight workstream. Do not let those
unrelated commits enter an RG-55 publication snapshot without that workstream's
gate/release disposition; isolate the RG-55 release inputs or coordinate the
shared-main publication boundary first. This does not prevent local gate and
review work.

## Remaining after close-out

1. Verify deployment: released daemon running from the declared CIU workflow,
   run-gate 24.0.1 installed in the cockpit venv and the tester image, host P8
   install confirmed.
2. Then, and only then, fill the `⟨P3⟩` fields of
   [`run-gate-WAVE-RG55-DSTDNS-ADOPTION-BRIEF.md`](run-gate-WAVE-RG55-DSTDNS-ADOPTION-BRIEF.md)
   and dispatch it on the operator's say-so.
3. cgprofile 1.1.0 (six blockers), then run-gate follow-up release, per
   `RG55-FINISH-PLAN-20261021`.
