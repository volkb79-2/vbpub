# P90 - Bounded CPU-hot and I/O-hot process projection

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88
> **Base:** main after P88
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** PID reuse cannot be excluded with `/proc/<pid>/stat` start time, or the candidate budget is exceeded to satisfy a view. Never key history by PID alone.

## Goal

Implement D-013/D-019's `pidstat`-class process model. The monitored set is one
bounded union of CPU-hot, I/O-hot, selected/pinned and recently-hot processes,
so an operator can see who performed I/O and when—not merely which cgroup was
busy.

## Required contracts

1. Stable `ProcessKey` includes host/boot identity, PID and start time. Procfs
   disappearance, PID reuse, hidepid and permission errors are ordinary typed
   states, not zeros.
2. Keep cheap current `/proc/PID/stat` and `/proc/PID/io` baselines for visible
   PIDs, then retain the configurable union: top 20 CPU, top 20 I/O, selected or
   pinned, and recently-hot for 60 seconds, with 16 expensive enrichments and a
   hard 64 retained-process cap by default.
3. Validate all D-019 caps/timeouts in strict config. Report eligible count,
   sampled count, omitted count, reason and warm-up coverage.
4. Rows include PID/PPID, user, state, elapsed, comm/cmd, CPU%, RSS/VSZ/swap,
   read/write B/s, faults, context switches and thread count where permitted.
   Join cgroup, systemd unit/slice, Docker container and CIU stack/phase with
   provenance; never duplicate cgroup accounting totals.
5. Feed process current/raw/history through P88 projections. Drill-down may
   start/renew a detail lease later, but the cheap CPU/I/O candidate loop runs
   independently of page visibility.
6. Expensive fields have explicit unavailable/warming/stale markers. Command
   lines and identities follow P81 sensitivity classification before frontend
   exposure.

## Acceptance oracles

Deterministic procfs fixtures prove CPU-only, I/O-only and overlapping candidates
form the correct capped union; an I/O burst remains in recent history; PID reuse
does not join histories; caps and tie order are deterministic; selected items
survive pressure; permission/race states are visible; owner joins are provenance-
correct. Include a large-PID benchmark and mutation tests for the I/O candidate
path and hard cap.

## Out of scope

Sockets/FDs/wchan stack sampling, thread projection, eBPF process tracing,
mutation and arbitrary process search beyond the configured bounded sweep.

## Gates

Focused proc/query/config tests, zero-skip full suite, compile checks,
`git diff --check`, and CPU/RSS/proc-read measurements at the configured cap.
Write P90-LOG.md and P90-REPORT.md.
