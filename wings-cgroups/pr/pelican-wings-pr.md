# PR draft — pelican-dev/wings

> **Status: DRAFT — not submitted.** Patches: `../patchstack/patches/pelican-main/`
> (0001+0002; include 0003 tests at maintainers' preference).
> Submit with: fork pelican-dev/wings → push `cgroup/main` → open PR. See `README.md` here.

**Title:** `Add cgroup parent support: node-wide docker.cgroup_parent + optional per-server override`

---

## What

Two small, opt-in, default-off additions:

1. **`docker.cgroup_parent`** (config.yml): every server and installer container
   is created with Docker `HostConfig.CgroupParent` set to a named systemd
   slice. Validated at startup (bare unit name, `.slice` suffix, no
   path/whitespace). Empty (default) = today's behavior, byte for byte.
2. **`WINGS_CGROUP_PARENT`** (reserved, admin-only egg/server variable):
   per-server override of the node-wide value, resolved by one shared helper
   for **both** the runtime and installer create paths. Wings does not trust
   panel data for placement: overrides must match the new
   `docker.allowed_cgroup_parents` allowlist or, when unset, the built-in
   `wings.slice`/`wings-*.slice` namespace — anything else **fails closed** to
   the node default with a warning that logs the attempted value.

## Why

On cgroup v2, memory *guarantees* (`memory.min`/`memory.low`) are hierarchical:
a floor is capped by every ancestor. With the systemd cgroup driver, Wings
containers land in `system.slice`, whose `memory.min` is 0 — so the
`MemoryReservation` Wings already sends is arithmetically zeroed, and operators
cannot give a paying tenant a real floor at all (Docker's API has no
`memory.min` field; slice-level policy is the only channel). One `CgroupParent`
knob makes the ancestor chain configurable:

- whole game tier under one resource-controlled slice (floors/ceilings/weights
  that host services don't share);
- per-server slices (`wings-<uuid>.slice` auto-nests under `wings.slice`) for
  per-server QoS tiers;
- per-slice PSI/accounting for free.

Wings deliberately gains **no systemd dependency**: it only places containers;
slice units and their resource properties remain host/operator concerns.

## Scope & non-goals

- No behavior change unless configured. No new dependencies.
- Wings never creates/modifies slices (a possible follow-up RFC covers
  Wings-managed transient slices; see companion issue).
- Existing containers are not moved — placement applies on next container
  recreation (documented in the config comment).
- If the named slice has no unit file, systemd creates a limit-less transient
  slice: placement works, guarantees absent. Called out in the config docs;
  deploy docs recommend `systemctl show <slice> -p FragmentPath` pre-flight.

## Validation

- Table-driven unit tests: value validation + override resolution (allowlist,
  namespace, fail-closed, last-occurrence-wins).
- Build-tagged (`dockerintegration`) tests driving the full
  `Environment.Create()` against a real daemon: default empty, node-wide
  applied, override applied, disallowed override falls back + warns. All green
  against a systemd/cgroup-v2 daemon; create-only, so they also run on
  cgroupfs CI runners.
- Live placement verified on a systemd/cgroup-v2 host:
  `system.slice/docker-*.scope` → `wings.slice/wings-*.slice/docker-*.scope`,
  with effective `memory.min` confirmed in cgroupfs.

## Security notes (multi-tenant)

- The variable must be admin-only in the panel (`user_viewable=false`,
  `user_editable=false`); Wings still validates every value because egg
  variables are data, not an authorization boundary.
- Like all server env vars it is visible to the server process — it carries a
  slice name only; docs say non-secret metadata only.
- A compromised panel payload cannot place a server outside the operator's
  allowlist/namespace (`system.slice` etc. are rejected).
