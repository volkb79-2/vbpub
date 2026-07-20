# PR draft — pelican-dev/wings

> **Status: DRAFT — not submitted.** Patches: `../patchstack/patches/pelican-main/`
> (0001+0002; include 0003 tests at maintainers' preference).
> Submit with: fork pelican-dev/wings → push `cgroup/main` → open PR. See `README.md` here.
>
> Sections through "Security notes" are the body of **PR 2** (placement). The
> trailing "Relationship to the existing `io_weight`" section belongs to the
> stacked **PR 3** (0004+0005, per-server slices) and should open that PR body,
> not this one.

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

---

## Relationship to the existing `io_weight` (PR 3)

*This section belongs at the top of the stacked per-server-slices PR. Raising it
first because it is the one place this series visibly overlaps code you already
ship, and because it ends in a naming question only maintainers can settle.*

Wings already has a per-server IO weight: `environment/settings.go` declares
`IoWeight uint16` ("a value between 10 and 1000"), applied as
`resources.BlkioWeight` and guarded by `blkioWeightSupported()`. It is
panel-supplied, per-server, and lands on the **container scope**.

That knob works, and this series does not replace or fix it. Verified
empirically on a cgroup-v2 + BFQ host with the systemd cgroup driver:
`docker run --blkio-weight 700` produced, on that container's scope,

```
io.bfq.weight = 700
io.weight     = 100   # unchanged default
```

so runc writes BFQ's own file directly, on BFQ's own 1..1000 scale — no
compression, nothing broken.

After this series a node has **three** IO-weight knobs:

| key | set by | scale | applied to | via |
|---|---|---|---|---|
| `io_weight` (existing) | panel, per server | 10..1000, BFQ scale | container **scope** | runc → `io.bfq.weight` |
| `docker.per_server_slices.defaults.io_weight` (new) | node admin | 1..10000, systemd `IOWeight` scale | per-server **slice** | systemd → `io.bfq.weight`, compressed ~11x |
| `io_bfq_weight` (new) | node admin | 1..1000, BFQ scale | per-server **slice** | systemd `IOWeight`, pre-divided so the compression cancels |

They are complementary rather than redundant: cgroup-v2 weights are relative
among siblings at each level, so a scope-level weight settles containers against
each other *under one slice*, while a slice-level weight settles slices against
each other *under the node tier* — the two compose multiplicatively into the
effective share, and neither can express the other's decision.

The problem is the name, not the mechanism: two of the three are called
`io_weight` while meaning different scales at different levels of the hierarchy.
An operator who reads "IO weight 1000" in the panel and writes `io_weight: 1000`
in `config.yml` has set two different things, one of which does not mean what
the number suggests. We would rather fix this before merge than after.

Our suggestion — offered as a question, not a decision — is to rename the
slice-level systemd-scale key to something unambiguous, e.g.
`io_weight_systemd`, and keep `io_bfq_weight` as-is since it already names its
scale. **Which key names do you want?** We will rename to whatever you prefer;
none of these keys have shipped, so there is no compatibility cost to getting it
right now.

One smaller observation while in this code: `blkioWeightSupported()` probes for
the existence of `io.weight` — the iocost controller's file — to decide whether
BFQ weighting is available, which is unrelated to the path runc actually takes
(`io.bfq.weight`). On the host tested, `/sys/fs/cgroup/system.slice/io.weight`
did not exist while `/sys/fs/cgroup/io.weight` did, so the probe passed via its
fallback path. We have not found a host where it wrongly returns false, so this
is not a reported bug — just a check against a file unrelated to the mechanism
it gates, which looks incidental.
