---
kind: backlog-entry
schema_version: 1
id: CP-7
title: "daemon session manifest's limits table is always {} -- effective cgroup limits are never resolved, so every analyze.py proposal check is a permanent no-op for a daemon-collected report"
status: open
type: "feature"
severity: "low"
provenance: "RG-55 wave, cgprofile-P1-DAEMON C5 (RW-14), 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

`lib/serve.py`'s `_manifest_for` (RW-14, C5) writes `"limits": {}`
unconditionally for every daemon session — a deliberate, explicit scoping
decision documented inline in that method's own comment ("the daemon does
not resolve effective cgroup limits — that is `cmd_collect`'s own job, out
of scope for P1"). This is correct as far as it goes: `ctl report`'s real
render (RW-14) never crashes on it, since `lib.analyze.build`'s
`_effective_limits`/every proposal-check function already tolerates an
empty/absent limits table by producing zero proposals rather than raising.
But the PRACTICAL consequence is that every proposal check in
`lib/analyze.py` (`_check_oversubscription`, `_check_recursiveprot_gap`,
and whatever else reads `Analysis.limits`) is permanently a no-op for
every daemon-collected report, even when the profiled cgroup genuinely has
an oversubscription or a strict-mode protection gap a `cgprofile run`
session against the SAME cgroup would have flagged.

## Why cgroup-profiler owns it

`lib.limits.effective()` (the ancestor-chain resolver `cmd_collect` already
calls) and `lib.serve.py`'s own manifest writer are both this project's
own modules; no other project depends on whether the daemon resolves
limits.

## Proposed contract

Not designed here. `_create_session_locked` already has `self.cgroup_root`
and the target's `cgroup` path at session-start time — the natural place
to call `limits.effective(cgroup, self.cgroup_root, limits.mount_flags())`
once (mirroring `cmd_collect`'s own one-shot-at-manifest-write-time
pattern, not a per-sample re-resolution — effective limits rarely change
mid-session) and store the `_limits_snapshot`-shaped result
(`cgprofile.py`'s own `_limits_snapshot` helper, already the schema
`analyze.py` expects — see `tests/test_cgprofile.py::TestLimitsSnapshot`'s
own "the end-to-end point" test) into the manifest's `limits` table keyed
by cgroup path, same shape `cmd_collect` already produces.

## Oracles

A fix should be verified with a fake cgroup tree carrying two sibling
children whose effective `memory.max` sums past a fake host RAM figure
(`lib/limits.py`'s own ancestor-chain test fixtures are the template) and
assert the daemon-collected session's rendered proposals include the
`oversubscribed:` finding `_check_oversubscription` would produce — a
session that computes `limits: {}` (the current, correct-for-P1 behavior)
is the controlled wrong implementation to red-first against.

## SPEC ownership

`scripts/cgroup-profiler/lib/serve.py` (`_create_session_locked`,
`_manifest_for`), `scripts/cgroup-profiler/lib/limits.py` (`effective`,
`mount_flags`, already used by `cmd_collect`), `cgprofile.py`
(`_limits_snapshot`, already the shape to reuse verbatim).

## Provenance

RG-55 wave, `cgprofile-P1-DAEMON` C5 (RW-14: making the daemon write a
`lib.store.RunDir`-compatible session directory for `ctl report`) — a
deliberate scoping decision recorded inline in `lib/serve.py`'s
`_manifest_for`, formalized here as a backlog entry per C9's own
housekeeping pass rather than left as a comment nobody searches for.
Filed 2026-09-12.
