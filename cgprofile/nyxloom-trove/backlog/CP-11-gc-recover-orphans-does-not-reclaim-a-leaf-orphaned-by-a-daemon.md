---
kind: backlog-entry
schema_version: 1
id: CP-11
title: "startup recovery restores journaled placement after daemon restart"
status: fixed
type: "bugfix"
severity: "low"
provenance: "RG-55 wave, cgprofile-P6-FOLLOWUPS session 6 C8 REPORT + RW-44, filed session 7, 2026-09-12"
filed_date: "2026-09-12"
closed_date: "2026-09-30"
closed_reason: "D-31 persists and validates placement ownership for startup recovery; focused restart and fail-closed tests pass, with registered gates/live probes still required for package release"
---

## Original defect

CP-9 (C8, `a654bd5d`) placed a lane's pids under a leaf cgroup
(`<gates slice>/rg-<token>`) and, on `stop`, moves survivors back and
`rmdir`s the leaf. If the DAEMON PROCESS ITSELF dies (crash, host reboot,
`ciu down`) while a session is placed and live, the leaf is never released
through that path — the daemon simply stops running.

`_recover_orphans` (the daemon's own startup recovery for sessions whose
`run` directory says `live` but whose process is gone) finalizes the
session's manifest so a consumer sees a terminated session rather than a
permanently "live" one. It has no `LanePlacement` object to call `release`
on (that object lived in the dead process's memory, not on disk), so it
never migrates survivors back or `rmdir`s the leaf. The leaf — and whatever
pids are still in it — is left behind under the gates slice with no code
path that will ever clean it up.

The earlier P6 round-3 safety repair refused a later placement request when
that token's leaf already existed, but did not recover the old leaf or its
processes. Refusal prevented a new session from silently taking ownership of
the orphan; it did not complete restart recovery.

## Why this matters

A gates slice accumulating orphaned `rg-<token>` leaves after every daemon
restart during development (exactly the pattern this wave's own sessions
hit repeatedly — worktree rebuilds, `ciu down`/`up` cycles) slowly starves
the slice's own capacity accounting and leaves stray pids attributed to a
session an operator can no longer query through `ctl`.

## Resolution — RG-55 P6 D-31

P6 now persists the exact delegated scope, leaf, PID start identities, and
per-PID original systemd unit/cgroup before moving work. At daemon startup,
`_recover_orphans` reads this journal even when the crash happened before the
live manifest was written, or when the session manifest had already reached
`finished`/`aborted` while cleanup remained incomplete. Recovery validates the
recorded unit/path with systemd and checks each surviving PID's start identity
and current cgroup before restoring it to its recorded origin. It never
guesses from a token-shaped directory, adopts an unrelated scope, or kills a
survivor. Once restoration and emptiness are verified, it removes only the
owned leaf and retires the exact owned scope; if identity, path, or movement
cannot be proved, it preserves the scope, leaf, and journal and publishes a
failure requiring operator attention. A completed journal on a finished
session is not replayed, so a later session reusing a token cannot cause old
state to act on new placement.

Regression coverage includes restart recovery with and without a manifest,
finished manifests with incomplete versus completed journals, successful
restoration, malformed/identity-mismatched records, and fail-closed retention.
The registered exact-tree gates and live start/stop recovery probes are part
of P6 acceptance; this row's status records the implemented repair, not a
claim that the unreleased P6 package has passed those remaining acceptance
steps.

## Provenance

RG-55 wave, cgprofile-P6-FOLLOWUPS session 6 (C8/CP-9 REPORT, "Deferred out
of C8"), flagged for filing by RW-44; filed by session 7, 2026-09-12;
implemented by P6 D-31 recovery journal, 2026-09-30.
