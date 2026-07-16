# T2 — per-server placement via `WINGS_CGROUP_PARENT` (patched Wings)

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
   ./mk-server-slice.sh b87c0a5b --min 6G --low 12G --high 8G --cpu 800
   systemctl show wings-b87c0a5b.slice -p FragmentPath -p MemoryMin   # pre-flight
   ```

3. Set the server's `WINGS_CGROUP_PARENT=wings-b87c0a5b.slice` override in the
   admin panel.
4. Recreate the server container (panel stop → `docker rm <uuid>` → start).
5. Keep the parent-budget invariant: `wings.slice` `MemoryMin` ≥ Σ child
   floors — `mk-server-slice.sh --check-budget` reports the current sum.
