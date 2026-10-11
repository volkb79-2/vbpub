# RG-55 wave report — interim closeout

Evidence snapshot: 2026-10-11. RG-55 is **not complete or released**. The
implementation and live-probe evidence are substantially in place, and P5 plus
the cgroup-profiler R2-cap correction are provisionally integrated on local
`main`. Mutation evidence, final full gates, publication, installation, and
final adoption closeout remain open.

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

## Integration and test status

- P5's repaired code passed selftest, assay R1, and assay R3; a fresh Sol
  review accepted it, and it was provisionally merged to main as
  `e40476b1f6fdd3ab8df1d90cafe26ad54d122a30` (RW-560/561).
- The cgroup-profiler mutation cap was raised from 1,500 to 2,000 after source
  planning found 1,593 candidates. Fresh Sol review accepted the change;
  exact-tree R0/R1 and R3 passed. It was provisionally merged as
  `da511e7bf` (RW-562).
- The exact-tree cgprofile R2 is still running in
  `.worktrees/rg55-cgprofile-cap-20261011` at `06e7804e`, container
  `run-gate-vbpub-r2-1100789-1791691250`, under `dev-gates.slice` capped at 3
  CPUs. At the 04:05:28Z progress read it had completed 14/1,593 candidates,
  all killed. That early sample is insufficient for a defensible ETA.
- The P5 R2 attempt on `05e7b6cd` ended with `ERROR/EXEC_FAILED` after 12
  candidates, not a mutation verdict. Assay detected an unmatched resource
  event on a shared cgroup ancestor and withheld outcomes. It overlapped the
  cgprofile R2 launch; the precise counter/source is not established. Preserve
  its partial records and resume from the exact judged tree after the active
  cgprofile R2 ends, avoiding concurrent mutation campaigns in the shared
  ancestor.
- No final `gate-full` receipt, survivor disposition, or release has been
  recorded. The cap worktree must remain unchanged until its R2 ends.

## Release and deployment status

Read-only `cmru status` reports `run-gate-v23.10.0` as the latest Run-Gate
tag and `run-gate-v23.11.0` as the next minor; cgroup-profiler has no tag and
the plan remains an explicit `cgprofile-v1.0.0` release. CMRU's default
untagged version suggestion (`cgprofile-v0.1.0`) is not the wave's approved
version. Assay's latest tag is `assay-v9.0.0`; the assay release belongs to
the coordinating Assay work, not this wave. The daemon container is running
from the local image `cgprofile:local`; no RG-55 package release is recorded.

The cockpit venv currently reports `assay 8.0.1.dev76`, and its installed
`run-gate` refuses the current `run-gate.toml` because it does not recognize
`resource_observer`. Do not use that installed entrypoint for current source
gates; use the repository's source runner until the proper Run-Gate release
is available, then install the released wheel and verify it against the
current config. No wheel was installed or published in this closeout snapshot.

Local `main` is 149 commits ahead of `origin/main`. CMRU releases operate from
an `origin/main` snapshot. The operator previously authorized publication
when the gap was 83 commits; the current larger gap is outside that exact
authorization, so obtain renewed direction before pushing the now-advanced
main or attempting publication. This does not prevent local gate/review work.

## Remaining closeout

1. Finish cgprofile R2 on the quiet judged tree; triage every survivor and any
   non-killed bucket, then run the final required full gate.
2. Resume P5 R2 from its partial records after the active campaign finishes;
   if it passes, complete survivor accounting and its final full gate.
3. Refresh any gates invalidated by later tree changes, then complete the
   authorized CMRU release transactions, install the released Run-Gate wheel,
   and deploy the released cgprofile daemon from main via CIU.
4. Recheck the daemon singleton and live acceptance after deployment; preserve
   the DAMON limitation/overhead disclosure above.
5. Fill the remaining release/version fields in
   [`run-gate-WAVE-RG55-DSTDNS-ADOPTION-BRIEF.md`](run-gate-WAVE-RG55-DSTDNS-ADOPTION-BRIEF.md),
   but do not dispatch it to dstdns. Mark RG-55 fixed only after all release,
   evidence, backlog, and closeout requirements are met.
