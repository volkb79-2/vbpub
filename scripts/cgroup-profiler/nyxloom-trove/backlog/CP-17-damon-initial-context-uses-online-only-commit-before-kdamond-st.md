---
kind: backlog-entry
schema_version: 1
id: CP-17
title: "DAMON initial context uses online-only commit before kdamond startup"
status: open
type: "bugfix"
severity: "high"
component: "DAMON session lifecycle"
provenance: "RG-55 P3 live probe, 2026-10-04"
filed_by: "RG-55 controller"
spec_owner: "cgroup-profiler DAMON sysfs lifecycle"
filed_date: "2026-10-04"
---

## Observed mechanism and reproduction

The RG-55 P3 live probe found that `ctl version` reported DAMON available,
but starting a session failed with `OSError(EINVAL)` at
`kdamond_commit()`. `DamonSession.__enter__` had written the complete
context while the kdamond was off, then issued `state=commit` before its
first `state=on`. Linux DAMON sysfs treats commit as an update to an already
running kdamond and rejects the stopped state; initial sysfs inputs are
consumed by `state=on`. See the kernel's
[`damon_sysfs_commit_input()`](https://github.com/torvalds/linux/blob/master/mm/damon/sysfs.c#L2008-L2024)
and [`state=on` implementation](https://github.com/torvalds/linux/blob/master/mm/damon/sysfs.c#L2113-L2142).

This made a capability check look healthy while every real session start
could fail, and prevented P3 from measuring DAMON samples or overhead.

## Why cgroup-profiler owns it

The daemon owns DAMON kdamond lifecycle and writes its sysfs configuration.
Neither the ctl transport nor the caller should need to know the kernel's
startup ordering.

## Proposed contract

Write the complete initial context while the kdamond is off, then start it
with `state=on` without an intervening commit. Keep `state=commit` for
`recommit_targets()` after the kdamond is running. A capability report must
not be treated as evidence that the context was accepted or samples were
collected; only a successfully started session and persisted DAMON samples
prove that.

## Oracles

- The fake sysfs rejects `kdamond_commit()` while its kdamond is off with
  `EINVAL`, matching the kernel behavior observed in the live probe.
- The initial session test succeeds, records `kdamond_on` as the final
  startup operation, and asserts no `kdamond_commit` was issued.
- `recommit_targets()` continues to issue commit for an already-running
  kdamond; its focused regression must remain green.
- Live acceptance starts a real daemon session on the host and records at
  least one DAMON sample. The RG-55 report records session duration and the
  measured daemon-on versus daemon-off overhead; this remains pending.

## SPEC ownership

`lib/damon.py`, `tests/test_damon.py`, `docs/DESIGN-GUIDE.md`, and the RG-55
P3 live-probe report.

## Updates

**2026-10-04** — Fix in isolated worktree removes the pre-start commit and
keeps online recommit unchanged. Focused `tests/test_damon.py`: 83 passed.
Entry remains open until the final gates and live sample/overhead probe pass.
