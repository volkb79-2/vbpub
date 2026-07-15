# Slices/cgroups for Pterodactyl/Pelican — fresh tiered proposal and deployment strategy

Date: 2026-07-15
Status: proposal (supersedes neither companion doc; builds on both)

Companion documents (verified details live there, not restated here):

- `wings-cgroup-parent-proposal.md` — the compiled v1/v2 Wings patches, source-level
  verification of Wings v1.13.1 and the panel payload path, live Findings A/D,
  single-node deployment runbook (Appendix A).
- `wings-cgroup-parent-proposal-review.md` — external review; guardrail requirements
  (shared runtime/installer resolver, namespace/allowlist), effort table, egg
  assessment.

This document is a deliberate re-framing, written as if starting fresh: it derives
the tier ladder from first principles rather than from the v1→v3 patch history,
resurfaces two options the earlier docs dismissed too quickly, adds an alternative
automation architecture (external slice-manager) that keeps the Wings fork minimal,
and answers the strategic question directly: **should we fork Panel and/or Wings,
and how do we carry patches across upstream releases?**

---

## 0. The re-framing: two independent axes, only one needs a Wings change

Everything in this problem decomposes into two orthogonal concerns:

**Axis 1 — Placement.** Which systemd slice does a container's `docker-<id>.scope`
land under? This is set **once, at container create time**, via Docker's
`HostConfig.CgroupParent`. It cannot be changed afterwards without recreating the
container (moving PIDs between cgroups behind Docker's and systemd's backs is not
viable). Wings v1.13.1 has zero plumbing for it — **this is the only part of the
entire problem that genuinely requires a Wings code change** (or a Docker-daemon-level
workaround, see T0).

**Axis 2 — Properties.** What floors/ceilings/weights (`MemoryMin`, `MemoryLow`,
`MemoryHigh`, `CPUWeight`, `IOWeight`, zswap knobs) exist on the slices in the
ancestor chain? These are **pure host-side systemd state** — unit files,
`systemctl set-property`, or D-Bus transient-unit properties. They need **no Wings
code and no Panel code, ever**. The only design questions are who writes them
(sysadmin/IaC, an external reconciler, Wings itself, or eventually the panel) and
whether they are reload-safe (systemd-owned values are; raw cgroupfs writes are
wiped by any `daemon-reload` — Finding D, proven live).

Two hard constraints shape every tier (both verified in the companion proposal):

1. Docker's API cannot express `memory.min` — protection floors *must* live on
   systemd slices. Egg/panel data can only ever carry the *values*; the *structure*
   is host-side.
2. Floors are hierarchical: a floor under `system.slice` (`memory.min=0`) is
   arithmetically dead (Finding A, proven live). Placement is therefore not
   cosmetic — without Axis 1, Axis 2 is unreachable for per-server guarantees.

Every tier below is simply a choice of **who supplies placement × who owns
properties**, with increasing automation and increasing code footprint.

A note on scope: the original goal named "cgroup limits for Wings itself" as well
as for game containers. Wings' own limits turn out to be the *easy* half — solvable
today with zero code (T0a) — which is why the earlier proposals barely mention it.
It is handled explicitly here so it doesn't get lost.

---

## T0 — Zero code changes anywhere (deployable today)

### T0a. Limits for Wings itself — already fully solvable

Wings runs either as a docker-compose service (our node) or as a native systemd
service. Both cases are closed problems:

- **Compose deployment:** the compose spec supports `cgroup_parent:` per service.
  Add to the wings service in `/root/ptero-wings/docker-compose.yml`:

  ```yaml
  services:
    wings:
      cgroup_parent: wings-mgmt.slice
  ```

  Install a real `wings-mgmt.slice` unit with `MemoryHigh`/`MemoryMax`/`CPUWeight`
  as desired. Wings' management process is now bounded and observable (PSI)
  independently of the game containers it manages.
- **Native systemd deployment:** a drop-in with `Slice=wings-mgmt.slice` plus
  resource directives, or resource directives directly on `wings.service`.

This is Tier 0 in the purest sense: it should be done regardless of every other
decision in this document, and it satisfies the "limits for wings itself" half of
the original goal outright.

### T0b. Dedicated-node daemon default — unfairly dismissed, worth reinstating

`dockerd` accepts a daemon-wide default cgroup parent (`daemon.json`:
`"cgroup-parent": "wings.slice"`, systemd driver naming). Every container the
daemon creates — server containers, installer containers, everything — lands under
that slice, with no Wings changes at all.

The earlier review rejected this (Option G, "too broad: affects unrelated Docker
workloads"). That is correct **for our mixed-use case-study host** (dev/test stacks
share the daemon) — but it is wrong as a general verdict. On a **dedicated Wings
node** — which is what every multi-node game host actually runs — "all containers
on this daemon" and "all Wings workloads" are the same set, minus the Wings
management container itself, which T0a's per-service `cgroup_parent:` override
exempts cleanly.

What T0b delivers on a dedicated node, for zero code and zero fork burden:

- the whole game tier bounded under one `wings.slice` (`MemoryMax`, `CPUWeight`
  versus host services);
- a **real, arithmetically effective protection floor for the tier**
  (`MemoryMin` on a top-level slice — no dead ancestor above it);
- tier-level PSI/accounting for free.

What it cannot deliver: per-server placement, hence per-server floors. And the
choice of parent is per-daemon, not per-server, so tiering (premium vs best-effort
slices) is out.

Verdict: **the correct baseline for dedicated nodes while unpatched upstream Wings
is in use**, and a fine permanent answer for operators who only need tier-level
guarantees. Not sufficient for our mixed host or for per-server floors.

### T0c. Host-side property reconciler (`set-property`) — the current watcher, hardened

A host daemon/timer that applies properties to slices and to Docker scopes via
`systemctl set-property` (systemd-owned ⇒ reload-safe, unlike the raw writes that
Finding D killed). This is Axis 2 automation with no code changes to either
project.

Hard limit: it cannot fix placement. On a shared node, scopes stay under
`system.slice`, so per-server *floors* remain arithmetically dead; only ceilings
(`MemoryHigh`/`Max`) and weights work. That is exactly the compensating role the
current Soulmask watcher plays. Keep it as the property-owner for T1/T2 below; stop
expecting floors from it alone.

**T0 summary:** do T0a now unconditionally. T0b is the right zero-fork baseline
for any future dedicated node. Neither gives per-server placement on a shared node
— that is what the ladder below buys.

---

## T1 — Minimal Wings patch: node-wide `docker.cgroup_parent` (~65 lines)

The compiled, vetted v1 patch from the companion proposal (§2): one config key,
validated at startup, applied to all server **and installer** containers.
Properties stay host-owned (static slice units + T0c for residuals).

- Placement: Wings, one slice per node. Properties: sysadmin/IaC.
- Capability: everything T0b gives, but works on **shared** nodes (only
  Wings-created containers move; dev/test workloads untouched) and per-node choice
  without touching the Docker daemon.
- On a single-game-server node (ours), the per-node slice *is* the per-server
  slice — full floors/ceilings for the game, today.
- Fork burden: 4 files, stable touch points (`Create()`, installer `Execute()`,
  config struct, startup validation). The cheapest possible recurring rebase.
- Deployment: Appendix A runbook in the companion proposal, including the
  mandatory pre-flight (`systemctl show <slice> -p FragmentPath -p MemoryMin …` +
  throwaway-container smoke test) that closes the false-positive-rollout footgun
  (review F3).

---

## T2 — Per-server placement: guarded reserved variable (~250 lines with tests)

The v2 shape with the review's guardrails made non-negotiable:

- Reserved, admin-only egg/server variable `WINGS_CGROUP_PARENT` resolved by a
  **shared helper used by both** the runtime create path and the installer create
  path (review F1).
- **Namespace/allowlist enforcement in Wings** (review F2): panel-supplied values
  are untrusted; accept only `wings.slice`/`wings-*.slice` (or an explicit
  `docker.allowed_cgroup_parents` list / children of the configured root). Invalid
  override → fail closed to the node default, log UUID + attempted value.
- Panel: **no code changes**; panel *data* changes only (egg reimport/update with
  the admin-only variable, per-server overrides where needed). `user_viewable=false`
  hides it from tenants' UI/API but not from the process — placement metadata only,
  never secrets (review F5).
- Properties: still host-owned — per-server slice units pre-installed by
  sysadmin/IaC (systemd dash-naming auto-nests `wings-<uuid>.slice` under
  `wings.slice`), plus T0c for anything residual.

Capability: full per-server placement and therefore full per-server floors — the
complete original goal — at the cost of manual per-server unit-file management.
That cost is real but scriptable (a 20-line "create slice unit for server UUID"
helper in IaC), and it is the natural stopping point for a small fleet.

---

## T3 — Automated per-server slice management (two architectures)

When per-server slices multiply, hand-managing unit files stops scaling. Axis 2
automation has two competing architectures — this is where this proposal diverges
most from the earlier docs, which only considered 3b:

### T3a. External slice-manager daemon (recommended flavor) — Wings fork stays at T2

A small standalone host service (own repo, no fork of anything):

1. subscribes to Docker events (and lists containers at startup);
2. on container create/start, reads the container's env/labels via
   `docker inspect` — the `WINGS_CG_*` metadata that T2's transport already
   delivers into the container (`WINGS_CGROUP_PARENT` plus optional
   `WINGS_CG_MEMORY_MIN`, `WINGS_CG_MEMORY_HIGH`, `WINGS_CG_CPU_WEIGHT`, … or one
   `WINGS_CGROUP_JSON` blob);
3. creates/updates the `wings-<uuid>.slice` transient unit via systemd D-Bus
   (`StartTransientUnit`/`SetUnitProperties` — the reload-safe channel), enforcing
   the `wings-*.slice` namespace and a node-wide floor budget
   (Σ child `MemoryMin` ≤ `wings.slice` `MemoryMin`);
4. reconciles on daemon-reload, host boot, and periodically; garbage-collects
   slices whose containers are gone.

Trade-offs, honestly:

- **Pro:** the Wings patch never grows beyond T2 (~250 lines) — the entire D-Bus /
  budget / lifecycle complexity lives outside the fork, ships on our schedule,
  is testable in isolation, and works identically under Pterodactyl Wings, Pelican
  Wings, or a future merged upstream. Root-equivalent D-Bus power is confined to a
  ~500-line auditable daemon instead of being added to Wings' surface.
- **Con:** a startup race — the container can exist for a second or two before its
  slice has properties (the slice *name* exists immediately as a limit-less
  transient slice created by systemd on placement; the reconciler then sets
  properties on it). For game servers with multi-second boot times this window is
  cosmetic; if it ever matters, a `docker events --filter type=container` trigger
  closes it to sub-second.
- **Con:** one more host service to deploy/monitor — but it *replaces* the current
  watcher rather than adding to it, and it is the same class of component.

### T3b. In-Wings D-Bus slice manager (the earlier v2.5)

Wings itself parses the spec variables, creates/reconciles transient slices before
container create, removes them on server delete. Cleaner lifecycle (no race, no
extra service), but: host D-Bus socket mounted into the Wings container,
root-equivalent code surface inside Wings, reconciliation semantics entangled with
Wings' own lifecycle (live-restore, restarts — review F4), and a fork delta of
weeks, permanently rebased. **Only worth building as an upstream RFC** (ideally in
Pelican, which owns both halves) — not as private fork code.

**Verdict:** if automation is needed before upstream moves, build T3a. Promote its
design to a T3b/v3 upstream RFC in parallel — the spec format and namespace/budget
rules transfer 1:1.

---

## T4 — Panel-native schema (the product end state, upstream-or-fork-only)

Egg/server schema for slice properties (`memory_min/low/high`, `cpu_weight`,
`io_weight`), admin UI + validation, one new block in the panel→Wings payload
(`ServerConfigurationStructureService` whitelist + a struct beside
`Build environment.Limits`), Wings applying it via the T3b machinery.

This requires Panel code changes — the only tier that does. It is the correct
first-class product design, and it is **not private-fork material**: a panel fork
is a permanent, migration-bearing, security-patch-tracking liability that a
placement feature cannot justify. Pursue T4 exclusively as an upstream feature —
realistically in Pelican, where panel and wings share one org and one review
pipeline. Bonus: T2's egg-variable transport degrades gracefully — if T4 ever
lands, the variables just stop being needed; nothing breaks.

---

## Decision matrix

| | Wings self-limits | Tier floor/ceiling | Per-server placement | Per-server floors | Slice automation | Wings code | Panel code | Fork burden |
|---|---|---|---|---|---|---|---|---|
| **T0a** compose/unit for Wings | ✅ | — | — | — | — | none | none | none |
| **T0b** daemon default (dedicated node) | via T0a | ✅ | ❌ | ❌ | — | none | none | none |
| **T0c** set-property reconciler | — | ceilings/weights only | ❌ | ❌ (dead under `system.slice`) | partial | none | none | none |
| **T1** node-wide `cgroup_parent` | via T0a | ✅ (shared nodes too) | per-node only | single-server nodes only | manual units | ~65 lines | none | minimal |
| **T2** guarded per-server variable | via T0a | ✅ | ✅ | ✅ | manual units (scriptable) | ~250 lines | none (data only) | small |
| **T3a** + external slice-manager | via T0a | ✅ | ✅ | ✅ | ✅ (outside fork) | = T2 | none (data only) | small |
| **T3b** in-Wings D-Bus manager | via T0a | ✅ | ✅ | ✅ | ✅ (inside Wings) | weeks | none (data only) | heavy |
| **T4** panel-native | via T0a | ✅ | ✅ | ✅ | ✅ | medium | migrations+UI+API | fork: prohibitive; upstream: right |

---

## Recommendation — what to do now

### Deploy (next 1–2 weeks)

1. **Now, zero risk:** T0a — put the Wings container itself under
   `wings-mgmt.slice` via compose `cgroup_parent:` + a real slice unit. Independent
   of everything else.
2. **This week:** T1 on the production node. The patch is already compiled and
   vetted; the runbook exists (companion proposal, Appendix A). Install the real
   `soulmask.slice`/`wings.slice` unit **first**, run the mandatory pre-flight and
   throwaway-container smoke test, then flip `docker.cgroup_parent`, recreate the
   game container in a planned window. On this single-server node, T1 already
   delivers the full goal: effective `memory.min` floors, reload-safe, and the
   blunt `system.slice MemoryMin` hack can be retired.
3. **Next (fold into the same custom build):** T2 with the guardrails — shared
   runtime/installer resolver, `wings-*.slice` namespace enforcement, fail-closed
   logging, table-driven tests. Add the admin-only `WINGS_CGROUP_PARENT` variable
   to the Soulmask egg (default empty; per-server override carries the value —
   review's egg guidance). This future-proofs for a second server/tier without a
   second fork-and-rebase cycle: **ship one patch series containing T1+T2, even if
   only T1 is exercised at first.**
4. **Defer:** T3a until slice-unit management actually hurts (≳ a handful of
   servers). T3b/T4 are upstream-RFC material only.

### Fork strategy — the direct answer

**Do not hard-fork either project. Fork Wings only, in the "GitHub fork carrying a
rebasing patch series" sense. Never fork the Panel.**

- **Panel: no fork, ever, for this feature.** Tiers T1–T3 need zero panel code —
  egg variables are the verified admin-extensible transport. A panel fork means
  owning DB migrations, security patches, and API compatibility forever, for a
  feature that doesn't need it. If T4 becomes real, it goes upstream (Pelican),
  not into a private fork.
- **Wings: maintain a patch-stack fork.** Concretely:
  - GitHub fork of `pterodactyl/wings` (what production runs today); branch
    `cgroup/v1.13.1` off the release tag; **one clean commit per feature**
    (v1 placement; v2 resolver+guard; tests) — never squash into upstream history.
  - CI (GitHub Actions) on the fork: `go build ./... && go vet && go test ./...`,
    build the image with the repo's own Dockerfile, tag
    `wings-local:<upstream>-cgroup.<n>` (registry-shaped prefix deliberately
    avoided so a stray `compose pull` fails loudly — runbook rationale).
  - On each upstream release:
    `git rebase --onto <new-tag> <old-tag> cgroup/<new-tag>` → CI → the standing
    smoke test (validation unit tests + throwaway `--cgroup-parent` container
    verifying cgroup path **and** effective `memory.*` files) → redeploy.
    The touch points (`Create()`, installer `Execute()`, config struct, startup
    hook) are among the most stable code in Wings; expected conflict rate is low
    and Wings releases are infrequent (v1.13.1 is current after months). Budget
    ~1–2 hours per upstream release.
- **Upstream immediately, to shed the patch burden:** submit T1 (+T2 with
  guardrails, same PR or immediate follow-up) to **`pelican-dev/wings` first**
  (faster merge cadence, near-exact precedent PRs, owns panel+wings for the T4
  future) and **cross-submit to `pterodactyl/wings`** (costs one PR). Open a
  companion RFC issue sketching the T3/T4 road (`wings.slice` hierarchy, namespace
  guard, budget invariant) so reviewers see a staged design, not a one-off knob.
  If either merges, the fork collapses to "run the upstream tag."
- **Keep an eye on Pelican migration as a separate, later decision.** If Pelican
  merges the feature and Pterodactyl stays quiet, the long-term cheapest position
  is running Pelican Wings — but do not couple that migration to this deployment.

### Division of responsibility to hold the line on (all tiers)

- **Wings (patched):** placement only — resolve slice name, validate against the
  namespace, set `HostConfig.CgroupParent` at both create sites. Nothing else.
- **Egg variables:** transport for admin-only, non-secret placement/profile
  metadata. Not an authorization boundary — Wings validates.
- **Host (units/IaC, later T3a daemon):** all slice properties, budgets, the
  `wings.slice` parent invariant (`MemoryMin` ≥ Σ child floors), reconciliation.
- **Panel:** untouched code; carries data.

This boundary is what keeps the fork rebase-friendly for however long "a while"
turns out to be — and it is unchanged whether the endgame is an upstream merge, a
Pelican migration, or carrying the patches indefinitely.

---

## Considered and rejected (for the record)

- **Raw writes to scope cgroup files** — wiped by any `daemon-reload` (Finding D,
  live-proven). Dead.
- **Daemon-wide default on the shared node** — collateral over dev/test workloads;
  correct only on dedicated nodes (reinstated as T0b there).
- **Moving existing containers between slices in place** — not supported by
  Docker/systemd semantics at any tier; placement changes always mean container
  recreation. Every runbook must say so.
- **Arbitrary top-level egg JSON (`"cgroups": {...}`)** — round-trips the egg
  export file but never reaches Wings (hard-coded panel payload whitelist);
  `variables` is the only no-panel-code channel.
- **Wings config.yml UUID→slice map** — duplicates panel state on the node;
  stopgap at best, superseded by T2's variable.
- **Replatforming (Kubernetes/Agones with QoS classes)** — solves the resource
  model but abandons the entire Pterodactyl ecosystem (eggs, panel, tooling) for
  a placement feature; out of all proportion to the problem.
