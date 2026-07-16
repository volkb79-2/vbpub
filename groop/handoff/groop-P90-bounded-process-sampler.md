---
schema_version: 1
id: groop-P90-bounded-process-sampler
project: groop
title: "Bounded CPU-hot and I/O-hot process projection"
tier: sonnet5-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "CPU-hot-only, I/O-hot-only and overlapping candidate fixtures form the correct capped union of top-20 CPU, top-20 I/O, selected/pinned and recently-hot-for-60s processes"
    negative: "the union omits an eligible top-CPU or top-I/O candidate, or exceeds the configured 64 hard cap"
    gate: groop-suite
  - id: O2
    observable: "a process with an I/O burst remains in the recently-hot set for the configured 60 seconds after the burst ends"
    negative: "a process is dropped from recently-hot history before the 60-second window elapses"
    gate: groop-suite
  - id: O3
    observable: "PID reuse is detected via /proc/PID/stat start time and never joins the new process's history with the old one's"
    negative: "a reused PID's history is joined with the prior process occupying that PID"
    gate: groop-suite
  - id: O4
    observable: "caps and tie-breaking order are deterministic across repeated runs of the same fixture"
    negative: "the same fixture input produces a different capped set or tie order on a repeated run"
    gate: groop-suite
  - id: O5
    observable: "selected or pinned processes survive eviction pressure from the CPU/I/O/recently-hot candidate pool"
    negative: "a selected or pinned process is evicted under pressure like an unselected candidate"
    gate: groop-suite
  - id: O6
    observable: "procfs disappearance, hidepid and permission-denied states surface as explicit typed states, not zero values"
    negative: "a permission-denied or vanished process is reported as a zero CPU/I/O value instead of a typed state"
    gate: groop-suite
  - id: O7
    observable: "cgroup, systemd unit/slice, Docker container and CIU stack/phase joins carry correct provenance without duplicating cgroup accounting totals"
    negative: "an owner join duplicates cgroup accounting totals or attributes the wrong owner"
    gate: groop-suite
  - id: O8
    observable: "a large-PID benchmark and mutation tests on the I/O candidate selection path and hard cap pass"
    negative: "a mutation of the I/O candidate selection or hard-cap enforcement survives undetected by the test suite"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["PID reuse cannot be excluded with /proc/PID/stat start time", "the candidate budget is exceeded to satisfy a view"]
advances: []
---

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

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p90-bounded-process-sampler`
  at `.worktrees/groop-p90-bounded-process-sampler` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p90-bounded-process-sampler`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.
