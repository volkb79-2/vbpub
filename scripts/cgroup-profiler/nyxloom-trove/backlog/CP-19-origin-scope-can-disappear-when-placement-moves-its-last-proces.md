---
kind: backlog-entry
schema_version: 1
id: CP-19
title: "Origin scope can disappear when placement moves its last process"
status: open
type: "bugfix"
severity: "high"
component: "placement lifecycle"
context_estimate: "small"
provenance: "RG-55 P3 live placement probe, 2026-10-10"
filed_by: "RG-55 controller"
spec_owner: "cgroup-profiler placement"
filed_date: "2026-10-10"
---

## Observed mechanism and reproduction

The RG-55 P3 live placement probe used a synthetic Docker scope containing
only one selected process. Start moved that PID into the profiler-owned leaf
and reported placement success. systemd then unloaded the now-empty origin
Docker scope. Stop correctly refused to guess a replacement unit and
reported `place-refused:identity-unavailable`, leaving exact restoration
unavailable. Probe: session `s-20261010T193616Z-b361`, container
`rg55-p3-place-20261010` (ID
`c03e86c8d90e1e2b3ed2b17ebf4296c44b9afaf4cb611936df7fe2e3eba2bcc5`). The
synthetic container was removed by exact name after evidence collection.

## Why cgroup-profiler owns it

The daemon moves processes through systemd and owns the placement journal.
When it moves the last process out of an origin transient scope, systemd may
unload that source unit; no exact systemd destination remains for safe
stop-time restoration.

## Proposed contract and current repair

Inventory every recorded origin unit's complete cgroup subtree before the
initial PID transfer and immediately before each later transfer from an
origin unit. Refuse with `place-refused:origin-scope-would-empty` when the
selected move would leave no process behind. Refuse unknown or inconsistent
membership as `place-refused:identity-unavailable`. Never attach a survivor
to a guessed parent cgroup. Ordinary profiling remains available when
placement is refused.

The membership check is observational, not atomic with systemd's move. An
unrelated process can exit after enumeration; if that removes the origin
unit, the daemon must preserve the journal and report the incomplete restore.
A stronger source-lifetime lease or launching work inside its final leaf is a
separate design requirement, not implied by this check.

## Oracles

- A one-process source scope is refused before systemd scope creation, with no
  PID moved and no placement journal or leaf created.
- An unprofiled process in the source unit, including a child cgroup, keeps a
  normal placement eligible and is not moved.
- A later-discovered process is refused if it would be the last process in
  its source unit; no systemd attach is attempted.
- Unknown, unreadable, inconsistent, or symlinked source membership refuses
  closed; no guessed destination is used.
- Live acceptance repeats the one-process refusal and a normal shared-scope
  placement/stop with the candidate daemon; exact stop restores survivors and
  retires only the profiler-owned scope/leaf.

## Updates

2026-10-10: initial and later pre-move checks, refusal vocabulary, docs, and
fake-cgroup regressions are in the RG-55 candidate. The registered package
unit gate passed 2,499 tests in 129.60 seconds, including the expanded
placement regressions. Whole-package coverage passed at 100% line and branch
coverage (8,389 statements, 3,110 branches), and all seven package canaries
were rejected. Final committed-tree gates, live acceptance against the
candidate daemon, and independent Sol review remain pending.

2026-10-15: Candidate-daemon live acceptance passed both the one-process
`origin-scope-would-empty` refusal and a token-scoped move/stop cycle. The
worker PID's host cgroup was identical before and after stop, and was observed
inside the expected profiler leaf during placement. Final exact-tip registered
gates and independent review remain pending. Evidence:
`reports/cgprofile-CP18-CP19-LIVE-ACCEPTANCE-20261015.md`.
