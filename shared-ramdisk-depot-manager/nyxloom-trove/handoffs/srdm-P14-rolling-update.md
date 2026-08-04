---
schema_version: 1
id: srdm-P14-rolling-update
project: srdm
title: "srdm update — the ordered cluster update, with a readiness gate"
tier: sonnet5-high
input_revision: "0903f307"
depends_on: [D-025]
session: fresh
source: {kind: user, ref: nyxloom-trove/roadmap.md}
scope:
  touch:
    - "internal/power/**"
    - "internal/opctl/**"
    - "internal/assign/**"
    - "internal/profile/**"
    - "internal/config/**"
    - "internal/doctor/**"
    - "cmd/srdm/**"
    - "tools/canary.sh"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/reports/srdm-P14-LOG.md"
  forbid:
    - "internal/store"
    - "internal/publish"
    - "internal/expose"
    - "internal/harvest"
    - "internal/consumer"
oracles:
  - id: O29
    observable: "The stop order is every slave before main, and the start order is main before every slave, with no slave started before main has signalled ready — asserted from the recorded operation order."
    negative: "A slave runs against a main on a different generation, which is a version-mismatched cluster rather than an updated one."
    gate: unit
  - id: O33
    observable: "Readiness matches only on a log line produced AFTER the start it belongs to; a timeout fails the update and triggers the ordered rollback rather than falling through to assume-ready."
    negative: "A line from a previous run reports ready instantly and forever, which is the failure mode of every naive log matcher — the slaves then start against a main that is not up."
    gate: unit
  - id: O30
    observable: "The assignment is recorded AFTER every server has moved and BEFORE the old generation is torn down — the same crash window activate uses."
    negative: "Recorded first, a crash leaves an assignment naming a release nothing has mounted; recorded last, a crash after teardown names a generation that no longer exists."
    gate: unit
  - id: O31
    observable: "When server K fails to come back up on the new generation, the update rolls back: K is re-exposed to the OLD generation and restarted, then every server already moved is rolled back the same way, and the assignment is left naming the old release."
    negative: "A split cohort — some servers on the new release, some on the old — which for a game cluster is a version mismatch nobody asked for."
    gate: unit
  - id: O32
    observable: "The update is refused, before anything is stopped, when the node cannot hold two generations at once; the refusal names the shortfall in bytes."
    negative: "The cohort is taken down and the publish then fails on ENOSPC or an OOM, leaving every server offline with nothing to come back to."
    gate: unit
gates: [unit, coverage, canary]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
  - "the Panel API shape cannot be confirmed from documentation"
---

# P14 — `srdm update`, the ordered cluster update

> The id, filename and branch still say "rolling". They are stable
> identifiers and are deliberately **not** being renamed mid-flight; the
> design they name is the ordered cohort cycle in §2, not a rolling update.

## Working setup

Worktree `/workspaces/vbpub/.worktrees/srdm-p14-rolling-update`, branch
`feat/srdm-P14-rolling-update`, cut from `main`. Rebase, never merge `main`
in, or the coverage gate measures the wrong delta.

## Context to read first

Paths from the **vbpub repo root**.

1. `shared-ramdisk-depot-manager/nyxloom-trove/PLAN.md` — authoritative.
   `wings-cgroups/` is superseded history; do not read or cite it.
2. `shared-ramdisk-depot-manager/internal/opctl/opctl.go` — read the package
   doc comment and `activate()` **carefully**. The ordering rule this
   package must preserve is stated there and is the whole content of O30.
3. `shared-ramdisk-depot-manager/internal/expose/expose.go` — `Driver` is
   the interface shape to copy for `power.Driver`: one interface, one real
   implementation, a fake for tests.

## Why

This is **M2's exit condition**, not a convenience. M2 is "a content update
is performed through srdm rather than around it", and today it cannot be:
`activate` refuses while a consumer holds the generation (oracle 24, and
rightly — swapping content under a running process is not a restart away
from working). So the real procedure is *stop every server by hand in the
Panel, run srdm, start every server by hand*, and a cohort is fully offline
for the whole window.

srdm already publishes the new generation **before** touching the old one —
two generations coexist for the duration of an `activate`. That is what lets
the switch happen with the cohort down and still have something to restart
on if the publish fails, and nothing uses it yet.

## What to build

### 1. `internal/power` — the power surface

```go
type State string  // "running", "offline", "starting", "stopping", "unknown"

type Driver interface {
    Name() string
    Status(ctx context.Context, serverID string) (State, error)
    Stop(ctx context.Context, serverID string) error   // blocks until settled offline
    Start(ctx context.Context, serverID string) error  // blocks until running
}
```

One real implementation, `PanelDriver`, against the **Pterodactyl Panel
client API** (`POST /api/client/servers/<short-id>/power` with a
`{"signal":"stop"|"start"}` body; poll `GET .../resources` for state).
Config gains `panel.url`, `panel.token`, and per-server timeouts.

**Do not stop containers through Docker directly**, even though srdm already
holds that socket. Wings owns the container lifecycle: a container that dies
underneath it is a crash, and Wings will mark it so and may restart it —
racing the very swap this package is performing. Going through the Panel is
not ceremony, it is the difference between an orchestrated update and two
systems fighting.

Unconfigured, every method **refuses** naming what to set. Same discipline
as `expose`: a refusal always carries its fix.

### 2. `opctl.Update` — the sequence

**This is a dependency-ordered cohort cycle, not a rolling update.** A
cluster has a **main** server and **slaves** that connect to it; a slave
running against a main on a different content version is a broken cluster,
so "one at a time, at most one down" is exactly the wrong shape. The whole
cohort goes down, in order, and comes back in the reverse order behind a
readiness gate.

```
preflight   the release exists and verifies; exactly one main in the cohort;
            the power driver is configured and every server answers Status;
            the node can hold TWO generations at once
stop        every SLAVE                       ← main still up
stop        MAIN                              ← cohort now fully down
publish     the new generation                ← nothing is holding
move        unexpose every server from old, expose to new
assign      record the new release            ← after all moves, before teardown
teardown    the old generation
start       MAIN
wait        MAIN readiness (§3)               ← the gate
start       every SLAVE
```

`--from <dir>` as sugar: promote a staged directory into a release and then
update to it, so the practical case is genuinely one command.

**Why the headroom check survives even though the cohort is down.** Publish
happens before teardown deliberately — a publish that fails must leave the
old generation to restart on. So two generations still coexist briefly. The
constraint is *softer* than a rolling design would need, because the
servers' own memory is freed while they are stopped, but it is not gone.

**Server roles.** `assign.Server` gains `Role` (`main` | `slave`), exactly
one main per profile, validated. `attach` gains `--role`, defaulting to
`slave`. `assign.SchemaVersion` goes to 2; a v1 document has no roles, and
reading one should **refuse with a fix** rather than migrate — guessing
which server is main is guessing which one takes the cluster down.

**Rollback (O31).** Any failure after the first stop walks the same order in
reverse against the **old** generation: re-expose everything to old, start
main, wait ready, start slaves. Leave the assignment naming the old release.
A partially-updated cohort is a version-mismatched cluster, which is worse
than a failed update.

### 3. The readiness gate (O33)

Main is ready when a configured pattern appears in its **container log**.
srdm already holds the Docker socket — `internal/consumer/docker.go` is the
model for talking to Docker with no third-party dependency.

```json
"readiness": { "kind": "log-match", "pattern": "<regexp>", "timeout": "300s" }
```

Three properties, each needing a test:

1. **Only lines from the current start count.** Stream with a `since`
   timestamp taken when the start was issued. Matching a line from a
   previous run reports ready instantly and forever — the failure mode of
   every naive log matcher, and here it starts the slaves against a main
   that is not up.
2. **A timeout FAILS the update** and triggers the ordered rollback. Never
   fall through to assume-ready.
3. **`kind` is a closed vocabulary** with one member today, so a future
   structured signal is additive rather than breaking.

### 4. The headroom check (O32)

The update holds two generations resident at once, because publish precedes
teardown. Refuse **before anything is stopped** when the node cannot: sum
the target generation's
class sizes, add the live one's, compare against what the host actually has.
Add it to `doctor` too — an operator should learn this on a quiet afternoon,
not with a cohort half down. `internal/cgroupfs` and the existing
`checkParentSlice` are the shape to follow.

### 5. `cmd/srdm` — the verb

```
srdm update --profile <file> --release <id>
srdm update --profile <file> --from <dir> --release <id>
```

Report per server as it goes; the operator is watching a cluster move.

## Watch for

- **`activate` already holds the lock for its whole run.** `Update` is a
  longer operation doing the same class of thing — take the lock once, for
  the whole update, and call the existing `activate()` internals rather than
  re-implementing the publish/expose/assign/teardown sequence.
- **`refuseIfHeld` must be bypassed deliberately, not accidentally.** The
  whole point is that srdm is the one stopping the consumers, so the guard
  that refuses while consumers hold must be satisfied *per server at the
  moment that server is swapped* — after its stop, before its re-expose.
  Never disable it globally; that would remove the only thing standing
  between a bug here and the 2026-07-29 corruption shape.
- The Panel API shape must be **confirmed from Pterodactyl's documentation**,
  not guessed. If it cannot be confirmed, that is an `escalate_if` — build
  the interface and the fake, mark `PanelDriver` as unverified in the LOG,
  and say so rather than shipping a guess.

## Gate

`tools/gate.sh <worktree> unit`, then `coverage` against `main` on a
committed tree (exit 3 is NO MEASUREMENT, not a pass), then
`tools/canary-run.sh` reporting 0 survived. `privileged-e2e` must stay green
but this package adds no case to it — a real cohort update needs a real
Panel and real containers, neither of which the gate container has. Say so
in the LOG rather than implying coverage this does not have.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a
forbidden file, **STOP** — write `BLOCKED: <reason>` to the LOG, commit and
exit. Do not improvise. A BLOCKED exit is a success mode. Product gaps are
**decisions**: file a `D-<NNN>` and keep working.

## Decisions this package is expected to produce

- Whether `srdm` talks to the Panel or to Wings directly, and what the
  credential surface is. Both are defensible; the LOG must say which and why,
  and the journal must never record the token (`journal.WithSecrets`).
- What "settled offline" means precisely, and how long srdm waits before
  calling a stop failed. Wings' own crash detection has opinions here.
