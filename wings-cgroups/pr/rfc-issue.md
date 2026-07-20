# RFC issue draft — companion to the cgroup-parent PR

> **Status: DRAFT — not submitted.** Open as an issue alongside the PR so
> reviewers see the small knob as stage one of a coherent road, not a one-off.

**Title:** `RFC: staged path for cgroup v2 resource guarantees (slices, per-server QoS, panel-native schema)`

## Problem

Multi-tenant nodes oversubscribe memory; under pressure the kernel reclaims
from whichever server is cheapest, not the tenant causing it. Real guarantees
are cgroup-v2 `memory.min`/`low` floors — hierarchical values Docker's API
cannot express and `system.slice` zeroes out by default.

## Staged path (each stage opt-in, none blocks the previous)

1. **Placement** (the placement PR, commits 0001–0003): `docker.cgroup_parent` +
   guarded `WINGS_CGROUP_PARENT` per-server override. Wings gains no systemd
   dependency; operators own slice units.
2. **Host-side automation** (no Wings changes): an external reconciler can
   own slice properties/budgets today (reference implementation:
   wings-slice-manager — docker-events → systemd D-Bus transient slices,
   `wings-*.slice` namespace guard, floor-budget enforcement, orphan GC).
3. **Wings-managed slices** (this RFC's core question, commits 0004–0005):
   Wings creates/reconciles per-server transient slices via systemd D-Bus
   (`StartTransientUnit` / `SetUnitProperties` — the daemon-reload-safe
   channel), spec delivered via
   admin-only egg variables (`WINGS_CG_MEMORY_MIN`, …). Requires host D-Bus
   access in the Wings container; hard rules proposed:
   - Wings may only ever touch `wings.slice`/`wings-*.slice` units;
   - node-side floor budget: refuse/clamp when Σ floors exceeds it;
   - rootless deployments degrade to stage 1 behavior.
4. **Panel-native schema** (end state): per-egg/per-server slice-property
   block with real validation + admin UI, delivered through the server
   configuration payload; same Wings machinery as stage 3.

## Design invariants (all stages)

- Floors live on **slices** (systemd-owned, reload-safe) — never raw writes to
  scope cgroup files (wiped by any daemon-reload; verified live).
- Parent budget: `wings.slice` `MemoryMin` ≥ Σ child floors, else children
  compete for the shortfall.
- Placement is create-time only; changes require container recreation.
- Egg variables are transport, not an authorization boundary: Wings validates.
- Slice-level weights **compose with**, rather than replace, the per-server
  `io_weight` Wings already applies to the container scope: cgroup-v2 weights
  are relative among siblings at each level, so the scope weight settles
  containers under one slice and the slice weight settles slices under the node
  tier. Both are needed; neither expresses the other.

## Asks

1. Is stage 3 (D-Bus in Wings) acceptable in principle, or should property
   management stay host-side (stage 2) indefinitely?
2. Naming: is the `wings-*.slice` namespace acceptable as a hard guard?
3. Config key naming for the slice-level IO weights. Stage 3 introduces two,
   and one of them collides by name with the existing per-server `io_weight`
   while meaning a different scale (systemd `IOWeight`, 1..10000) at a different
   level (the slice, not the scope). Suggestion, not a decision: rename the
   systemd-scale key to `io_weight_systemd` and keep `io_bfq_weight` — but the
   maintainers should pick, and nothing has shipped, so renaming is free.
4. Relatedly: `blkioWeightSupported()` gates BFQ weighting on the existence of
   `io.weight`, the iocost controller's file, which is unrelated to the
   `io.bfq.weight` path runc actually writes. On the host we tested the probe
   passed via its fallback, so this is an observation rather than a reported
   bug — but is the file choice deliberate?
5. For stage 4: appetite for a `resources.cgroup` block in the egg schema?
