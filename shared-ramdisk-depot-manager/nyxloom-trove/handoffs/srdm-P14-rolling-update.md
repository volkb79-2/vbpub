---
schema_version: 1
id: srdm-P14-rolling-update
project: srdm
title: "srdm update — the orchestrated rolling cluster update"
tier: sonnet5-high
input_revision: "0903f307"
depends_on: [D-025]
session: fresh
source: {kind: user, ref: nyxloom-trove/roadmap.md}
scope:
  touch:
    - "internal/power/**"
    - "internal/opctl/**"
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
    observable: "A rolling update over a two-server cohort stops, swaps and starts each server ONE AT A TIME: at no point are both servers simultaneously offline, and the order is recorded in the journal."
    negative: "The whole cohort goes down at once, which is the outage a rolling update exists to avoid."
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

# P14 — `srdm update`, the orchestrated rolling cluster update

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
two generations coexist for the duration of an `activate`. That is exactly
what a rolling update needs, and nothing uses it yet.

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

```
preflight   the release exists and verifies; the assignment has servers;
            the power driver is configured and every server answers Status;
            the node can hold TWO generations at once
publish     the new generation — old still live, every server still running
per server, ONE AT A TIME:
              stop → wait settled offline
              unexpose from old → expose to new
              start → wait running
assign      record the new release            ← after all moves, before teardown
teardown    the old generation
```

`--strategy rolling|all-at-once`, default `rolling`. `all-at-once` stops the
whole cohort first and is for nodes that cannot hold two generations; it
trades downtime for headroom and must say so when chosen.

`--from <dir>` as sugar: promote a staged directory into a release and then
update to it, so the practical case is genuinely one command.

**Rollback (O31).** If server K fails to come up on the new generation:
re-expose K to the old and restart it, then walk back every server already
moved, the same way, one at a time. Leave the assignment naming the **old**
release and the old generation standing. A partially-updated cohort is a
version-mismatched cluster, which is worse than a failed update.

### 3. The headroom check (O32)

A rolling update holds two generations resident at once. Refuse **before
anything is stopped** when the node cannot: sum the target generation's
class sizes, add the live one's, compare against what the host actually has.
Add it to `doctor` too — an operator should learn this on a quiet afternoon,
not with a cohort half down. `internal/cgroupfs` and the existing
`checkParentSlice` are the shape to follow.

### 4. `cmd/srdm` — the verb

```
srdm update --profile <file> --release <id> [--strategy rolling|all-at-once]
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
but this package adds no case to it — a real rolling update needs a real
Panel, which the gate container does not have. Say so in the LOG rather than
implying coverage this does not have.

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
