# T2 — per-server placement via `WINGS_CGROUP_PARENT` (patched Wings)

> **Superseded for normal operation by patch 0004** (`docker.per_server_slices`,
> shipped and deployed): Wings derives the per-server slice automatically and
> manages the transient unit + properties itself — no egg variable, no
> `mk-server-slice.sh`, no UUID lookups. **Treat this folder as proof-of-concept
> and fallback tooling**, not as the way to run a 0004 node. What remains live
> here is (a) the explicit-override mechanism (a set `WINGS_CGROUP_PARENT` beats
> the derived slice; setting it to the node default opts a server out) and (b)
> the manual path for builds without 0004. Per-server floors with 0004 travel as
> admin-only `WINGS_CG_MEMORY_MIN/_LOW/_HIGH/_MAX`, `WINGS_CG_CPU_WEIGHT`,
> `WINGS_CG_IO_WEIGHT`, `WINGS_CG_IO_BFQ_WEIGHT` variables (see
> `../STRATEGY.md` §T3b).

Code: `../patchstack/patches/*/0002-*.patch`. This folder holds the panel-data
side (egg variable) and per-server slice tooling. **No panel code changes** —
but panel *data* changes: the egg gains an admin-only variable; existing
servers needing non-default placement get per-server overrides.

## How it works

- Admin-only egg/server variable `WINGS_CGROUP_PARENT` reaches Wings through
  the normal environment payload; a shared resolver applies it to **both** the
  runtime and installer containers.
- Wings does not trust the value (panel data is not a security boundary):
  accepted only if it matches `docker.allowed_cgroup_parents` (when set) or
  the built-in `wings.slice`/`wings-*.slice` namespace, or equals the node
  default. Anything else fails closed to the node default with a logged
  warning.
- `user_viewable=false`/`user_editable=false` hide it from tenants' panel
  UI/API, **not** from the server process — placement metadata only, never
  secrets.

## Setup

1. Add the variable to the egg (see `egg-variable.snippet.json`) via egg
   import/update in the admin panel, or create it directly on the egg. Leave
   `default_value` empty — set real values as **per-server** overrides
   (a UUID-specific default inside a shareable egg would be wrong).
2. Create the per-server slice (dash-naming auto-nests under `wings.slice`):

   ```bash
   ./mk-server-slice.sh deadbeef --min 6G --low 12G --high 8G --cpu 800
   systemctl show wings-deadbeef.slice -p FragmentPath -p MemoryMin   # pre-flight
   ```

   The short 8-hex name is a leftover of this PoC — it was only ever a
   hand-typed abbreviation. **Patch 0004 derives `wings-<32 hex>.slice` from
   the server's full dashless UUID**, so a slice created by hand under the
   short convention will not be the one Wings ensures. Match 0004's naming if
   the node might ever gain 0004.

3. Set the server's `WINGS_CGROUP_PARENT=wings-deadbeef.slice` override in the
   admin panel.
4. Recreate the server container (panel stop → `docker rm <uuid>` → start).
5. Keep the parent-budget invariant: `wings.slice` `MemoryMin` ≥ Σ child
   floors — `mk-server-slice.sh --check-budget` reports the current sum.
