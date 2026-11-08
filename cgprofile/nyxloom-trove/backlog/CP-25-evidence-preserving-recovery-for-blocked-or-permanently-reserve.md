---
kind: backlog-entry
schema_version: 1
id: CP-25
title: "Evidence-preserving recovery for blocked or permanently reserved sessions"
status: open
type: "bugfix"
severity: "high"
component: "recovery"
context_estimate: "large"
provenance: "CP-24 Opus high and Sol xhigh review follow-up, 2026-10-15"
filed_by: "vbpub maintenance"
spec_owner: "CGProfile session recovery"
priority: 2
filed_date: "2026-10-15"
---

## Observed mechanism

CP-24 deliberately fails closed when committed evidence is corrupt, a session
marker cannot be classified, or placement/DAMON ownership cannot be proved.
Startup may keep the daemon bound in `admission-blocked`, and an unresolved
session retains its capacity. Restart retries only identity-proven cleanup; it
cannot repair a corrupt committed tick or unknown format marker. The current
operator guidance correctly forbids deleting session state but leaves no
supported way to preserve-and-retire an irreparable record, so a daemon or
session can remain unavailable indefinitely.

CP-24 also writes the DAMON pool's `nr_kdamonds` capacity as one global sysfs
operation. A same-boot crash after pool creation can leave slots off while the
count remains nonzero. The next daemon refuses that pre-existing count and
keeps DAMON unavailable; CP-24 startup intentionally does not inspect or alter
sysfs after the old process's inode pins are lost. Per-session `reserved`
owner records prove whether the old daemon reached `kdamond_on`, but do not
authorize blind pool shrink or reuse. CP-25 does not reset this global pool;
durable pool provenance and a safe reset are owned by CP-26.

## Required contract

- Provide a supported operator recovery workflow to inspect a blocked or
  `recovery-required` record and, when safe, quarantine it outside the active
  session inventory while preserving every original byte.
- The workflow must not free capacity while a target cgroup, placement leaf,
  DAMON monitor, or other owned resource may still be active. It must verify
  ownership and cleanup itself where possible; otherwise refuse with the exact
  missing proof and keep the reservation. Do not turn an operator assertion
  into a successful measurement or terminal Summary.
- Quarantining a session never resets, shrinks, adopts, or reuses the global
  DAMON pool. A session with unresolved DAMON ownership cannot be quarantined
  as resolved. CP-26 owns durable pool provenance and the separate recovery
  verb for a proven CGProfile pool; until that work lands, preserve the pool
  and report DAMON unavailable.
- Quarantine must be atomic or recoverable across interruption, identify the
  original session and operator-supplied reason, and record a digest of the
  preserved evidence. It must not affect unrelated sessions or allow an
  admission-blocked daemon to resume normal work while any unclassified active
  record remains.
- The supported lifecycle uses CIU to quiesce and restart the daemon stack;
  do not require raw Docker lifecycle commands. If the maintenance operation
  needs a one-shot container, coordinate with CIU-141/`ciu run` rather than
  duplicating the Compose service definition.
- Keep the fail-closed behavior when evidence is insufficient. Document which
  states can be resolved automatically, which need host maintenance, and how
  the operator verifies each external ownership fact before retrying.
- Update README, DESIGN-GUIDE, CONSUMERS, PROTOCOL if wire-visible, and CHANGES
  in the same implementation package.

## Acceptance oracles

- A classifiable `recovery-required` session whose placement and DAMON cleanup
  are proved can be quarantined; the archive digest matches the preserved
  bytes, a restart no longer counts that one session, and unrelated records
  remain unchanged.
- A corrupt tick does not become a PASS, a fabricated Summary, or a survivor
  verdict during quarantine.
- If a cgroup/leaf is populated, a DAMON owner is active, a journal is
  unclassifiable, or a cleanup fact is unreadable, the workflow refuses and
  retains admission/capacity exactly as before.
- Session quarantine does not call DAMON pool-reset operations under any
  condition; a pool-recovery request is referred to CP-26.
- A pre-existing or foreign DAMON pool is never adopted, shrunk, or rewritten
  by CP-25.
- An admission-blocked daemon can expose safe diagnostics and target one
  quarantinable entry without bypassing classification of other entries.
- Interruption before, during, and after archive publication leaves either the
  original active record or a complete verifiable quarantine record; never
  both absent and never two active copies.
- CIU-managed stop/maintenance/restart, the registered CGProfile gate, and a
  live acceptance probe pass. No manual `docker run`, `docker exec`, or
  `docker stop` is part of the adopter workflow.

## Release boundary

This follow-up owns the supported recovery path. Until it ships, CP-24's
fail-closed daemon/package may be tested and released, but do not deploy it as
the host singleton if operational policy requires self-service recovery from
corrupt or unclassifiable state. The operator must preserve evidence and
escalate; a restart is not a repair.

## Landing status (2026-10-27 release wave)

Held for a later wave, deliberately not in cgprofile 1.1.0. Nothing here is implemented on main: the normative `DESIGN.md` D-33 section, the PROTOCOL/CONSUMERS/contract edits, the 18 red-first tests, the 29 `cp24-*` JSON fixtures, the 1,123-line implementation packet and the 33 design/contract review reports exist only on the park ref `park/stash-2026-10-17-park-cp-24-25-26-work-from-cgprofile-rel` (commit `f3657994a`, merge of the 2026-10-17 stash; its third parent holds the untracked files). The packet itself is marked "not ready for implementation dispatch": the fixtures were authored at control contract 1 and must be re-authored at contract 2, the public docs still describe contract 1, and the open choices need operator confirmation. The daemon change also requires the matching Run-Gate client (pending-response parsing, busy/deadline handling) to be released first, so it cannot ship ahead of run-gate 24.x. Do not re-derive it: start from the park ref.
