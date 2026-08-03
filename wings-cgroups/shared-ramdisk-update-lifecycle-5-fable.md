# Shared game releases and tmpfs lifecycle — v5, implementation go

- Status: **implementation go** (all conditional-go findings resolved),
  2026-07-23
- **Amendment 1, 2026-08-03**: two production behaviours of vanilla Wings,
  surfaced on the case-study node while the unified `soulmask_tmpfs` system
  was deployed, are folded in — the mount-propagation requirement on Wings'
  parent volumes mount, and the unconditional pre-boot chown walk versus
  read-only mounts. See §Field findings; every section the amendment touched
  is marked **[A1]**. Net additions: invariant 14, a complete containerized-
  Wings mount contract, a new one-patch series **F1**, and oracles 14–18.
- **Amendment 2, 2026-08-03**: the program is **reordered around vanilla-Wings
  compatibility as the MVP**. The manager is named `srdm`
  (*shared-ramdisk-depot-manager*) and gains an **exposure-driver** seam:
  `host-bind` (works on stock Wings, no patch) is v1 and the default;
  `provider` (the L1/L1b protocol of rev 5) becomes v2. Exposure carries an
  `access: ro | rw` axis, and a `harvest` command promotes an in-place-updated
  generation into a verified release. Consequence: the manager ships *before*
  every Wings patch instead of after them, and the case-study migration moves
  four phases earlier. Marked **[A2]**. Decisions 9–12; §Exposure drivers.
- Supersedes: [`shared-ramdisk-update-lifecycle-3-codex-fable.md`](shared-ramdisk-update-lifecycle-3-codex-fable.md)
  (rev 3-fable). Incorporates every finding of the combined review
  [`shared-ramdisk-update-lifecycle-4-codex-combined-final-remarks.md`](shared-ramdisk-update-lifecycle-4-codex-combined-final-remarks.md).
- Companion: [`shared-ramdisk-update-lifecycle-cgroups-2-fable.md`](shared-ramdisk-update-lifecycle-cgroups-2-fable.md)
  (resources series rev 2 — carries review issues 5, 6 and the
  capability/reset/golden-harness contracts).
- Implementation bases (the only Wings compatibility targets): Pterodactyl
  Wings `v1.13.1` = `e771816d5e072b3f2a8b9383bfcaffaa8f569dfa`; Pelican Wings
  `main @ 70f3344cc588b31e1f48e10ddcb87d116b957e69` (v1.0.0-beta26+1). The
  local 0001–0011 stack and the current host setup are evidence and
  prototypes only.
- Evidence rule: Wings claims cite vanilla source (`git show v1.13.1:<path>`
  in `build/wings-pterodactyl`); kernel claims cite kernel documentation;
  systemd/Docker claims cite upstream docs/source. Local docs are hints.
- State rule (absolute): `WS/Saved/**` — especially every `world.db` and
  `WS/Saved/GameplaySettings/GameXishu.json` — is never shared, never used as
  content input, never modified by a release transaction.

## Decision log

Interview 2026-07-23 (first round, unchanged):

1. Dual-target; Pelican is the PR audience.
2. Manager = one Go binary, daemon + CLI.
3. Wings v2 patches first, manager second against v2-patched Wings; no
   provider-less interim; no patch-0011 bridge.
4. Docs are normative; open items marked **[open]**.

Interview 2026-07-23 (second round, resolving the review):

5. **Disposal**: provider-managed servers auto-dispose their stopped
   container definition once Offline settles (review issue 2, recommended
   option); the upstream PR carries a config default.
6. **Acquisition scope**: manager v1 is **strictly manual** — `stage`,
   `activate`, `rollback` by operator command only. No shipped timer, no
   scheduler. Automatic detection/staging is a post-v1 package; the Soulmask
   "auto-detect + auto-stage" default moves there (review issue 12).
7. **Kickoff**: **contracts-first** — the provider-protocol conformance
   fixtures (fake provider + fake Wings driver) are frozen before any Wings
   patch code lands; then the safe-work sequence proceeds.
8. **L4 deferred**: maintenance lease / observable power operations are out
   of the v2 program. Rollouts use RCON save/flush + Wings stop (routes
   through Stopping; never trips crash detection). Revisit after migration
   if `SaveAndExit`-style flows matter.

Interview 2026-08-03 (Amendment 2) [A2]:

9. **Name**: `srdm` — *shared-ramdisk-depot-manager*. The long form is the
   product name, the repository directory and the prose; **`srdm` is every
   identifier**: binary, CLI, provider id, slice root, unit prefix,
   `/run/srdm`, `/var/lib/srdm`. A single token with no `-` is required, not
   cosmetic: systemd's `-` is the slice hierarchy separator, so a hyphenated
   root would nest under auto-created ancestors with `MemoryMin=0` and every
   class floor beneath it would be arithmetically dead — Finding A of
   [`STRATEGY.md`](STRATEGY.md), and the reason the host's `soulmask_tmpfs`
   uses an underscore. `srdm.slice` sits at cgroup root; `srdm-gen-<g8>.slice`
   nests correctly beneath it.
10. **Vanilla-Wings compatibility is the MVP, not a fallback.** Adoption must
    not be hostage to upstream review. `srdm` v1 requires **no Wings patch**:
    it exposes generations by binding them into the server's volume path
    (`exposure: host-bind`). The rev-5 provider protocol becomes the *second*
    exposure driver, shipped in v2 once L1/L1b land. The seam is one
    interface; everything upstream of it — store, manifest, publication,
    hold services, charging, journal, retention, CLI, doctor — is shared.
11. **Exposure has an access axis** `ro | rw`. `rw` is permitted only for a
    generation with **exactly one** consumer; such a generation is marked
    dirty-capable and forfeits its attestation until re-verified. Two
    consumers on a writable shared tmpfs is the 2026-07-29 corruption by
    construction, so it is refused, not warned about.
12. **`harvest` is in v1.** An in-place-updated generation (the game's own
    updater writing through an `rw` exposure) can be quiesced, re-hashed,
    classified and promoted into a normal immutable release. This removes the
    containerized SteamCMD driver from the MVP critical path: the game's own
    updater is a legitimate acquisition source, and `harvest` is what makes
    its output trustworthy.

## Review triage

Every point of the combined review, with disposition. "Adopted" means the
resolution is normative in this revision (or the companion, where noted).

| # | Review point | Severity | Disposition → where |
|---|---|---|---|
| 1 | L1 mounts/labels injected after `SyncWithEnvironment()` snapshot; concurrent sync erases them | Blocker | **Adopted** — per-start-attempt overlay + overlay-aware settings composition; verified against `server/update.go:21-31` → §L1 start-attempt transaction |
| 2 | Stopped container definitions pin G; documented cutover cannot pass | Blocker | **Adopted** (decision 5) — auto-dispose on settled Offline for managed servers; crash path ordered; e2e oracle → §Disposal |
| 3 | Callback/recovery semantics not yet a protocol | Blocker | **Adopted** — complete schemas for all five routes, envelope rules, idempotency + tombstones, commit-pending policy, reconcile authority table, openat2 trust statement, configurable peer credentials, `allow` field removed → §Provider protocol v1 |
| 4 | Series not independent once R6 consumes L2 | Blocker | **Adopted** — R6 core is standalone behind a `ReadySignal` interface with the egg-done default; a one-patch integration series `I1` binds L2 on the combined branch; `series.yaml` metadata + CI matrix → §Workstreams; companion §PR sequence |
| 5 | `reserved` ledger cannot be rebuilt from systemd units (offline servers undercounted) | Blocker | **Adopted** — two ledgers (reserved-desired from server configs incl. Offline; admission-active transactional); sync-rejection retains last accepted resource revision → companion §Floor budget |
| 6 | Sync-triggered reconcile deadlocks on the power lock | Blocker | **Adopted** — normative lock hierarchy (power → resource/attempt → admission/driver), pre-start passes its power context, Panel sync never takes the power lock → companion §Online reconciliation; §L1 uses the same order |
| 7 | One holder slice cannot carry two class policies; parent protection unbacked | Manager blocker | **Adopted** — per-class populate+hold services inside a generation slice; each carries its class memory policy; `srdm.slice` gains a protection budget; zswap restated as charge-holder policy → §Generation slices |
| 8 | Publication mount topology and recovery under-specified | Manager blocker | **Adopted** — exact mount sequence (op-private tmpfs → populate/verify → RO bind exposure), per-class mount options, mountinfo-based recovery → §Publication topology |
| 9 | `Before=docker.service` contradictory and deployment-specific | Manager blocker | **Adopted** — republish path needs no Docker; ordering contract stated against the Wings service; Wings-side bounded boot-restore retry as the general net → §Boot ordering |
| 10 | "One state writer" vs transient workers | Manager blocker | **Adopted** — worker contract: op-private writes, fsync'd result records, daemon-only commits, cancellation/orphan rules → §Worker contract |
| 11 | "Narrowly privileged" understates the daemon | Manager blocker | **Adopted** — daemon is a privileged (root, v1) node service, stated plainly with a capability table; parsing pushed into workers → §Privilege model |
| 12 | v1 automation contradiction | Manager blocker | **Resolved by decision 6** — strictly manual v1; defaults tables corrected → §Manager v1 scope |
| G1 | Enrollment workflow not normative | Detail | **Adopted** — six-step happy path; group creation/auto-enroll/move/remove semantics → §Enrollment |
| G2 | Provider mounts can hide Wings-managed egg configuration files; install guard too loose | Detail | **Adopted** — collision rule against `ProcessConfiguration.ConfigurationFiles`; install guard exact-matches provider *and* profile → §L1 validation, §Managed egg |
| G3 | SFTP/backup/disk claims false for migrated volumes during soak | Detail | **Adopted** — oracle and migration text corrected → §Acceptance oracles, §Migration |
| G4 | L3 semantics incomplete (reverse index, intent, queue lifecycle, observability; generation check misplaced) | Detail | **Adopted** — all six sub-points specified; generation-equality removed from the dependency engine (manager cohort resolution is the authority; `doctor` cross-checks labels) → §L3 |
| G5 | Golden harness flakiness (clocks, IDs, ordering) | Detail | **Adopted** → companion §Golden harness |
| G6 | Resource capability/reset contracts unpinned | Detail | **Adopted** → companion §Systemd driver rules (reset table, capability probes, required-rejects-not-degrades, block-device discovery, bounds) |

## Field findings (production, 2026-07-31) [A1]

Two behaviours surfaced on the case-study node while the unified
`soulmask_tmpfs` system was deployed
([`../scripts/gstammtisch-guide/SOULMASK-TMPFS.md`](../scripts/gstammtisch-guide/SOULMASK-TMPFS.md)).
Both are properties of vanilla Wings, both bear on this program, and both are
resolved here rather than left as deployment lore.

| # | Finding | Evidence | Disposition → where |
|---|---|---|---|
| F-a | A containerized Wings whose `/var/lib/pterodactyl` bind uses Docker's default `rprivate` never observes host-side mounts or unmounts under the volumes root. Wings kept **ghost** binds of a torn-down tmpfs alive in its own namespace, and the next start failed on a mount that no longer existed on the host. | production outage 2026-07-31 (`b87c0a5b`, `6c418fe7`); fix live in the node's Wings compose — `/var/lib/pterodactyl` and `/var/lib/docker/containers` with `propagation: rslave`, [verified] via `docker inspect` on the running container | **Adopted** — the containerized-Wings mount contract is stated completely (volumes root included), with its shared-peer-group precondition and the deliberate *absence* of propagation on game containers → §Containerized Wings mount contract, §Generation slices, §Migration; oracles 14–15 |
| F-b | `Filesystem.Chown` walks the entire server root and calls `Lchownat` on **every** entry with no "already correct owner" skip. A chown syscall on a read-only mount returns `EROFS` even when the requested owner already matches, so any read-only mount under a server's host volume path makes the pre-boot walk fail and the server unstartable. | [verified] `server/filesystem/filesystem.go:253-294` (walk; unconditional `Lchownat` at :268 and :287); `server/power.go:207-214` (pre-boot call site) gated by `system.check_permissions_on_boot`, [verified] default `true` at `config/config.go:238` | **Adopted** — the design's structural immunity becomes invariant 14 with a regression oracle; the residual (today's host, and the migration window) is fixed by a new one-patch series **F1** → §Goals and invariants, §F1, §Migration; oracles 16–18 |

This design was **already immune to F-b by construction**: provider mounts are
Docker mounts in the *game container's* namespace and never appear under
`/var/lib/pterodactyl/volumes/<uuid>`, which is the only tree Wings walks. That
immunity was implicit. It is now invariant 14 with an oracle behind it, because
it is the single property that retires the whole class of bug the legacy
in-volume ramdisk lives with — and an implicit property is one a later refactor
is free to lose.

F-a is the opposite case: the design does *not* get it for free. It already
prescribed `rslave` for the release store, but said nothing about the volumes
root, which is where the outage actually happened and where the legacy binds
still live during migration.

## Changes from the previous revision (3-fable → 5)

1. **L1 rewritten around a per-start-attempt transaction** (issue 1). The
   3-fable seam — prepare after `SyncWithEnvironment()`, mounts appended in
   `Server.Mounts()` — was broken: vanilla snapshots `Mounts: s.Mounts()` and
   `Labels` into the environment settings in `SyncWithEnvironment()`
   ([verified] `server/update.go:21-31`), so the create would have used the
   pre-provider snapshot, and any concurrent Panel sync would have erased a
   prepared overlay. Now: an attempt object owns provider state; a single
   overlay-aware composition function produces the environment settings
   everywhere; `Server.Mounts()` stays vanilla-pure.
2. **Prepare moved to the end of preflight** and the deferred abort widened
   to every post-prepare failure, not only Docker create/start.
3. **Auto-disposal of stopped managed containers** (issue 2, decision 5):
   new lifecycle rule + updated rollout, GC, and oracles. Without it the
   documented single-generation cutover deadlocks on stopped definitions.
4. **Protocol v1 completed** (issue 3): schemas for commit/abort/release/
   reconcile; envelope limits and unknown-field rejection; request-ID
   idempotency with retention; duplicate/reorder rules; the
   commit-pending-after-successful-start policy; reconcile authority table;
   `allow` removed from the prepare response; provider returns typed
   identity, Wings constructs labels; openat2 trust statement; configurable
   peer credentials.
5. **Kickoff plan rebuilt contracts-first** (decision 7) with the review's
   safe-work sequence, an explicit cross-series DAG, `series.yaml` patchstack
   metadata, and the CI matrix. Manager prerequisites stated: L1+L1b only.
6. **Generation memory design corrected** (issue 7): one populate+hold
   service per class (Type=oneshot, `RemainAfterExit=yes`) carries the class
   policy and keeps the charges alive after the worker exits — replacing the
   impossible "one holder slice, two policies" shape; `srdm.slice`
   gains an admin-owned `MemoryMin` backing the class floors; per-class zswap
   is documented as charge-holder policy, not a per-page guarantee.
7. **Publication topology made exact** (issue 8) and **boot ordering
   corrected** (issue 9): republish needs no Docker; the ordering contract is
   against the Wings service, with a bounded Wings-side boot-restore retry
   (`generation-degraded`/socket-absent are retryable during restoration).
8. **Worker contract and privilege model added** (issues 10, 11).
9. **Manager v1 descoped to strictly manual acquisition** (decision 6):
   Soulmask defaults table corrected; automatic detection/staging is a
   post-v1 package with its own spec.
10. **L4 removed from program scope** (decision 8); the L-series is now
    L1, L1b, L2, L3.
11. **Enrollment, config-file collision, install-guard exact match, and the
    migrated-volume SFTP/backup caveat** added (G1–G3); **L3 semantics
    completed** and the cohort-generation check moved out of the dependency
    engine (G4).
12. Carried unchanged in substance: case study, terminology (extended),
    invariants, architecture verdicts, release store, SteamCMD driver,
    selectors/group policies, journal, RCON adapter, Soulmask profile,
    failure table (extended), security model (extended), defaults
    (corrected), evidence index (extended with `server/update.go`).

## Kickoff plan (the "go" sequence) — reordered by decision 10 [A2]

Rev 5 put the manager at Phase 5, behind every Wings patch. Decision 10
inverts that: `srdm` in `host-bind` mode needs **no Wings patch at all**, so
it ships first and the case-study node migrates off `soulmask_tmpfs` four
phases earlier. Contracts-first (decision 7) is **not abandoned** — it is
rescoped: the protocol freeze still precedes any L1 code, it simply no longer
gates the whole program, because the whole program no longer waits on the
protocol.

```text
Phase 1   srdm core — THE MVP, no Wings patch required
          - store: transaction → classification → per-file SHA-256 manifest
            → probes → ownership normalization → fsync'd COMPLETE →
            atomic channel flip
          - publication topology: op tmpfs → per-class hold services →
            verify → RO bind (§Publication topology, unchanged)
          - exposure driver: host-bind (ro|rw) + consumer registry +
            teardown safety (§Exposure drivers)
          - harvest: adopt an in-place-updated generation as a release
          - journal, retention, doctor, CLI
          gate: kill-at-every-phase; topology recovery from
                mountinfo+state+units; hold-service charging incl. the
                still-held-bind case; propagation precondition refusal;
                rw-single-consumer refusal; harvest round-trip
Phase 2   F1 (series `filesystem`)                [parallel with Phase 1]
          - the chown-skip patch (§F1). Promoted by decision 10 from
            "nice, ships first" to **the MVP dependency for host-bind ro**
          - the program's first upstream PR
          - needs: patchstack series.yaml + tooling, CI matrix,
            golden vanilla-compat harness (companion §Golden harness) —
            i.e. rev 5's Phase 0b, pulled here because this is where the
            first patch actually appears
          gate: golden harness + per-commit build/vet/test + oracles 16-18
Phase 3   Soulmask migration onto srdm host-bind  — retires soulmask_tmpfs
          - profile + managed egg + rehearsal → maintenance window
          - production runs srdm on stock (F1-only) Wings
          gate: §Migration; this is the real acceptance test
Phase 4   Provider protocol v1 contract freeze    [was Phase 0a]
          - fake provider + fake Wings driver + golden vectors + fault
            scripts; frozen = fixtures committed + protocol version pinned
Phase 5   Lifecycle L1 + L1b, and srdm's provider exposure driver
          gate: conformance suite + the race/fault oracles of §Acceptance
Phase 6   Lifecycle L2, L3; resources R1–R8; integration I1
          gate: per-series gates as before
Phase 7   Cutover: flip Soulmask from exposure: host-bind to
          exposure: provider. A config flip and a container recreate —
          not a migration; the store, generations and journal are untouched.
```

What this buys, stated plainly: the risky, novel, most-likely-to-be-wrong
half of the program (a transactional content store, tmpfs publication,
cgroup charging, teardown correctness) gets proven in production **before**
a single line of Wings-patch review risk is taken on. And if upstream never
merges anything, Phases 1–3 still deliver a strictly better system than the
node runs today.

Dependency DAG (issue 4):

```text
vanilla
├─ filesystem: F1             (chown skip; no dependencies, no dependents) [A1]
├─ lifecycle: L1 → L1b        (provider admission → dynamic mounts/leases)
│             L2 → L3         (readiness → dependencies; independent of L1)
└─ resources: R1;  R2 → R3 → R4 → R5 → R6core → R7;  R8 after R5

combined branch v2/<ref> = filesystem ⊕ resources ⊕ lifecycle ⊕ I1
  I1 = one integration patch binding L2 events into R6's ReadySignal registry
```

Every branch builds and tests at every commit.

**Manager dependencies, corrected by decision 10** [A2]. Rev 5 said "the
manager requires L1 + L1b only". That is now false in the direction that
matters:

| srdm exposure | Wings patches required |
|---|---|
| `host-bind`, `access: rw` | **none** — stock Wings |
| `host-bind`, `access: ro` | **F1** (or the node sets `system.check_permissions_on_boot: false`) |
| `provider` | L1 + L1b |

L2/L3 improve Soulmask cluster behavior; the resources series and L4 are not
dependencies of the manager in any mode. F1 is a dependency of nothing *in
the Wings DAG* — no other patch imports it — but it is squarely on `srdm`'s
MVP path, which is why it is Phase 2 and the first upstream PR.

### Patchstack tooling (normative)

`patchstack/series.yaml` replaces prose:

```yaml
series:
  cgroup:      {layout: legacy, bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}}
  filesystem:  {bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}, depends: []}
  lifecycle:   {bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}, depends: []}
  resources:   {bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}, depends: []}
  integration: {depends: [resources, lifecycle]}
combined_order: [filesystem, resources, lifecycle, integration]  # the one legal order
```

`resolve_target <series> <target>` maps to
`patchstack/patches/<series>/<target>-<ref>/` and branch `<series>/<ref>`;
`cgroup` keeps the legacy layout. `SERIES` alone is not sufficient (review
issue 4): apply/export/CI read `series.yaml` for bases, dependencies, and the
combined order. Go toolchains per `stack.conf` (golang:1.24 pterodactyl,
1.25 pelican); expect pelican-side type deltas of the `DefaultMapping`-
pointer kind at export.

## Case study: the 2026-07-21 incident (carried)

The static ramdisk copies content once and reuses it forever; MAIN's
`WS/Content/Paks` was `root:root`, so steamcmd (uid 988) could never replace
the pak while every validate pass reported "Success!"; clients on 1.0.14 met
a server frozen on 1.0.13 (`Create Dungeon Failed: DungeonEGLv50`).

| Incident failure | Design property |
|---|---|
| cp-once tmpfs reuse serves stale content | Immutable generations, only ever replaced, published solely from complete hash-verified releases |
| Silent partial steamcmd write | Manager-owned transactions, normalized ownership, per-file manifest + probes, `COMPLETE` written last |
| Content and instance state entangled | Release roots provider-mounted RO; the volume holds only mutable state |
| No version attestation | Generation identity in Wings-constructed labels + lease; readiness/doctor compare expected vs served |

## Terminology

Release; generation; class; lease; cohort; managed root; mutable root (as in
rev 3) — plus: **start attempt** (one Wings start with its provider overlay,
keyed by attempt ID); **overlay** (the attempt's validated mounts + reserved
labels composed into environment settings); **disposal** (removal of a
stopped managed container's Docker definition); **tombstone** (durable
record of a released lease); **hold service** (per-class transient unit that
populates a class tmpfs and then stays active to own its memory charges).

## Goals and invariants

General product goals unchanged (rev 3). Soulmask invariants unchanged
(1–11 of rev 3), plus:

12. Provider state never leaks into Panel-derived configuration: the overlay
    is attempt-scoped, cleared on failure/stop/supersession; the only
    durable committed record outside the manager is the Wings-constructed
    Docker label set.
13. A stopped provider-managed server does not pin a generation once its
    stop has settled (disposal rule).
14. **Managed content never appears under a server's host volume path —
    under `exposure: provider`.** [A1, rescoped A2] With the provider
    driver, release roots reach a server only as Docker mounts in the game
    container's namespace; `/var/lib/pterodactyl/volumes/<uuid>` holds
    mutable state and nothing else. That is what makes Wings' own filesystem
    operations structurally unable to touch read-only managed content — the
    pre-boot chown walk above all (F-b), but equally disk accounting,
    backups, SFTP and archive extraction, each of which walks that same host
    tree.

    **`exposure: host-bind` waives this invariant deliberately** (decision
    10), and the bill is itemized in §Exposure drivers rather than
    discovered. Invariant 14 is therefore not a property of `srdm`; it is
    the *reason provider mode is worth building* and the yardstick host-bind
    is measured against. Oracle 16 asserts it holds in provider mode; oracle
    19 asserts host-bind's waiver stays inside its declared bounds.

## Layer 1 — Wings v2 lifecycle series

### L1 — external start-preparation provider

#### The start-attempt transaction (issue 1)

Vanilla ordering, verified: `HandlePowerAction` holds the power lock
(`server/power.go:56`); `onBeforeStart()` (`power.go:171`) runs `Sync()`,
suspension check, **`SyncWithEnvironment()` — which snapshots
`Mounts: s.Mounts()` and `Labels` into `environment.Settings`
(`server/update.go:21-31`)** — then the disk check and
`UpdateConfigurationFiles()`; `Environment.Start()` later removes and
recreates the container from those settings (`environment/docker/power.go:26`).

Normative design:

- **Attempt object.** `server.startAttempt`: attempt ID (UUID), created-at,
  provider ID, protocol request ID, validated lease
  `{lease_id, generation, mounts}`, reserved label values, policy revision,
  state `preparing | prepared | starting | commit-pending | committed |
  failed`. At most one active attempt per server; a new admitted start
  supersedes and clears a stale one.
- **Overlay-aware composition.** One function produces environment settings
  everywhere: `composeSettings(cfg, attempt) = vanillaSettings(cfg) ⊕
  attempt.mounts ⊕ attempt.labels`. `SyncWithEnvironment()` is refactored to
  call it. Consequences, each an oracle: a Panel sync racing between prepare
  and create **preserves** the overlay; `Server.Mounts()` remains
  vanilla-pure; Panel-derived configuration maps are never mutated.
- **Ordering.** Prepare runs **after** all ordinary fallible preflight so no
  lease is created for a start that was going to fail anyway:

  ```text
  onBeforeStart():
    Sync()                          # vanilla, power.go:173
    suspension check                # vanilla
    SyncWithEnvironment()           # vanilla (composes without overlay yet)
    disk check                      # vanilla
    UpdateConfigurationFiles()      # vanilla
    Chown (if configured)           # vanilla
    prepareContentProvider()        # NEW: resolve selectors → prepare → validate
    SyncWithEnvironment()           # re-compose; overlay now included
  ```

  **On the `Chown` step** [A1]: it walks the whole server root and calls
  `Lchownat` unconditionally on every entry, with no already-correct-owner
  skip ([verified] `server/filesystem/filesystem.go:253-294`), and a chown
  on a read-only mount is `EROFS` even when the owner already matches
  (F-b). It is harmless in this design because of **invariant 14** — it
  runs over the host volume, which never carries managed content — and not
  because of where prepare sits: prepare only *computes* mounts, Docker
  applies them at create, so no ordering of these two steps could expose
  managed content to the walk. Prepare's position at the end of preflight
  is about lease economy. The safety is the invariant, and oracle 16 is
  what keeps it honest.

- **Deferred abort.** The start case in `HandlePowerAction` wraps everything
  after a successful prepare: any failure — a later preflight error,
  environment create, Docker start — triggers `abort-start` and clears the
  overlay. Only a successful `Environment.Start()` proceeds to
  `commit-start`.
- **Clearing.** Overlay cleared on: pre-create failure, abort, settled
  Offline (after `release`), supersession. Nothing attempt-scoped is
  persisted; the durable committed record is the Docker label set (reconcile
  authority below), which survives Wings restarts by construction.
- **Labels.** The provider returns typed identity fields only. Wings
  constructs the reserved labels itself:
  `wings.content.provider`, `wings.content.lease`,
  `wings.content.generation`. A provider cannot set arbitrary labels.
- **Collision validation against the complete effective mount set**: the
  default `/home/container` volume mount, passwd/machine-id mounts, custom
  (egg/node) mounts, and provider mounts ([verified] composition in
  `server/mounts.go:22,66`). Provider targets must be strict descendants of
  `/home/container` (never equal), must not equal or contain any
  `denied_targets` entry, must not duplicate any effective target, and —
  **G2** — must not equal or contain any path Wings manages as an egg
  configuration file (`ProcessConfiguration.ConfigurationFiles`), unless
  node config grants `config_file_ownership: provider` for that server.
  Accepted mounts are sorted shallow-first before Docker sees them.

#### Resolution and node configuration

Fixed activation variable `WINGS_CONTENT_PROVIDER`; precedence node
per-server override > node egg allow-list > validated server selectors >
absent = none (stock path, no socket operation). Unknown/unauthorized
non-empty selector = actionable start error for that server only.

```yaml
docker:
  lifecycle_providers:
    srdm:
      socket: /run/wings-providers/srdm.sock
      required_when_selected: true
      prepare_timeout: 15s
      call_timeout: 5s
      boot_retry_grace: 180s          # boot restoration only (issue 9)
      boot_retry_interval: 10s
      allowed_eggs: []
      selector_variables:
        profile: WINGS_CONTENT_PROFILE
        group: WINGS_CONTENT_GROUP
        channel: WINGS_CONTENT_CHANNEL
        release: WINGS_CONTENT_RELEASE
      allowed_source_roots: [/run/srdm, /var/lib/srdm]
      allowed_target_root: /home/container
      denied_targets: [/home/container/WS/Saved]
      allowed_peer_uids: [0]          # provider-side check is primary; see protocol
      dispose_stopped_containers: true   # decision 5; PR default discussed in PR text
      overrides: {}                   # uuid -> {provider|none, selectors...}
```

Only the configured selector variables are forwarded — never the full
environment ([verified] it contains `RCON_PASSWORD` et al.,
`server/server.go:151`).

Source validation uses `openat2(2)` + `RESOLVE_BENEATH` under
`allowed_source_roots`. **Trust statement (issue 3):** this validates a
resolved object at one moment; Docker later consumes a pathname. Provider
source roots and their parents are node-admin-owned and immutable to server
users; Wings validates containment and mount policy but does not defend
against a malicious *privileged* provider that swaps paths afterward — the
provider is node-trusted for its declared roots, and that is the boundary.

#### Containerized Wings mount contract (F-a) [A1]

Docker's default bind propagation is `rprivate`: the container's view of a
bound tree is a snapshot taken at container create and is never updated
again. Every tree a containerized Wings must *observe changing* therefore
needs `rslave` — and that list is longer than the release store, which is
what rev 5 got wrong.

| Mount | Mode | Why |
|---|---|---|
| `/var/lib/pterodactyl` | `rslave` | **The volumes root.** Anything mounted or unmounted under a server volume on the host — a legacy ramdisk during migration, an operator's rescue bind, any future per-server mount — is otherwise invisible to Wings, and a *torn-down* host mount lives on inside Wings as a ghost. That ghost is what made the pre-boot walk fail on 2026-07-31 (F-a). |
| `/var/lib/docker/containers` | `rslave` | Wings reads other containers' log and config files live; it must see host-side create/remove without needing a restart. |
| `/run/srdm` | `ro,rslave` | Generations are published after Wings starts; this is the tree Wings resolves provider sources against. |
| `/var/lib/srdm` | `ro,rslave` | Persistent release store — same reason. |
| `/run/wings-providers` | `rw` | Provider socket directory (dir `root:root` 0755, socket 0600). |

**Precondition**: `rslave` requires the host-side peer group to be `shared`.
On a systemd host `/` is shared by default ([verified] on the case-study
node: `/` `shared:1`, `/run` `shared:5`), so this holds unless an operator
deliberately made a subtree private. `doctor` checks it and names the
`mount --make-rshared` fix, rather than letting the failure resurface later
as a mystery.

**Deliberately absent: the game containers' own mounts.** Docker resolves
their sources in the *host* namespace at create, and `rprivate` is the
correct mode for them — a running consumer must not lose its content
because the host tore a mount down. That is not an accident of the default
we happen to tolerate; it is the property that makes immutable generations
safe, and it has a consequence recorded under §Generation slices: a
container still holding the bind keeps the tmpfs pages alive after the host
unmounts, so **disposal is a hard precondition for teardown, not a
courtesy**. Nobody should "fix" propagation here.

An operator refusing the release-store mounts must set
`trust_provider_paths: true`, which disables Wings-side source resolution
and says so in the docs.

#### Disposal of stopped managed containers (issue 2, decision 5)

Vanilla keeps a stopped container's definition until the next start's
`OnBeforeStart()` removes it ([verified] `environment/docker/power.go:26`;
stop performs no removal). A stopped definition still references its mount
sources and carries generation labels — it pins G, and the offline cutover
would deadlock.

Normative rule for provider-managed servers
(`dispose_stopped_containers: true`):

```text
state → Offline (server/server.go OnStateChange, :317)
  1. exit state captured (ExitState), console/log handling done
  2. crash decision made (server/crash.go:47)
  3a. crash restart scheduled → release lease (reason=superseded-by-restart);
      definition left for the imminent OnBeforeStart removal
  3b. no restart pending → dispose: remove the container definition
      (ContainerRemove, not Destroy — server state untouched)
      → release lease (reason=stopped)
```

Disposal is idempotent and retried on transient Docker errors; a failed
disposal leaves the lease held and surfaces in server health (the generation
stays pinned — honest, visible). Unmanaged servers are untouched. The
upstream PR presents the flag with a discussion of both defaults; our
deployment runs `true`. Direct `docker start` of a disposed container is
impossible and was already unsupported for Wings-managed containers.

E2E oracle: stop two consumers → both disposed, leases released, labels
gone → G teardown succeeds → activate H → both start against H. Crash
oracle: kill the game process → exit state observed, crash restart
recreates the container safely with a fresh lease.

#### Boot-restore retry (issue 9, Wings side)

During boot restoration only (`cmd/root.go:237-259` path), `prepare-start`
failures with retryable codes (`generation-degraded`, socket
absent/connection refused) are retried every `boot_retry_interval` for up to
`boot_retry_grace`, then fail normally. Ordinary user starts never retry —
they fail fast with the actionable message.

### Provider protocol v1 (normative, complete — issue 3)

Transport: HTTP/1.1 over a Unix stream socket
(`/run/wings-providers/<id>.sock`, dir root:root 0755, socket 0600).
Authentication: filesystem permissions first; the provider additionally
verifies `SO_PEERCRED` against its configured `allowed_peer_uids`
(default `[0]`, suitable for the rootful deployment; rootless/user-ns Wings
layouts are **unsupported in protocol v1** — a node that needs them
configures the allowed UID explicitly and owns the consequence).

Envelope rules:

- `Content-Type: application/json`; requests ≤ 64 KiB, responses ≤ 256 KiB;
  oversize → 400 / connection close.
- Unknown JSON fields are rejected **in both directions**: the provider
  rejects unknown request fields (400 `protocol-violation`); Wings rejects
  unknown response fields (treated as provider failure).
- Server-side deadlines: prepare 10 s, others 5 s (Wings-side timeouts per
  node config). Status mapping: 200/204 success; 400 `protocol-violation`;
  403 `unauthorized-selector` / peer rejection; 404 `unknown-profile` /
  `unknown-group` / `unknown-lease`; 409 `cohort-locked` /
  `lease-conflict` / `request-conflict` / `incompatible-protocol`;
  503 `generation-degraded` (retryable); 500 `internal` (body carries
  `retryable`).
- Error body: `{"code": "...", "retryable": bool, "message": "...",
  "operation": "op-..."}` — `message` always names the operator action
  (e.g. the exact CLI command).

Routes and schemas:

```text
POST /v1/prepare-start
  req  {protocol: 1, request_id, server_uuid, volume_root,
        selectors: {profile, group, channel, release}}
  resp {lease_id, generation,
        mounts: [{source, target, read_only: true}]}
       # no "allow" field (success IS the grant; denial is a typed non-2xx)
       # no label map (Wings constructs labels from lease_id + generation)

POST /v1/commit-start    req {lease_id, container_id}          → 204
POST /v1/abort-start     req {lease_id, reason}                → 204
POST /v1/release         req {lease_id,
                              reason: stopped|deleted|aborted|
                                      superseded-by-restart}   → 204
POST /v1/reconcile
  req  {protocol: 1, node_boot_id,
        servers: [{server_uuid, lease_id?, container_id?, generation?}]}
  resp {dropped: [lease_id...], kept: [lease_id...],
        unknown_generations: [generation...]}   # surfaced as health warnings
GET  /v1/healthz         → 200 {protocol: 1, provider: "srdm"}
```

Idempotency and ordering:

- **prepare**: keyed by `request_id`. Same ID + byte-identical body →
  identical cached response (the same lease), retained ≥ 24 h durably. Same
  ID + different body → 409 `request-conflict`. Wings uses a fresh
  `request_id` per attempt and reuses it only for transport-level retries of
  that attempt.
- **commit**: idempotent for identical `{lease_id, container_id}` (204).
  Unknown lease → 404; released/tombstoned lease → 409 `lease-conflict`;
  same lease, different container → 409.
- **abort**: idempotent (204 even when already aborted/tombstoned). Abort of
  a *committed* lease → 409 (must use `release`).
- **release**: idempotent (204 when already released or tombstoned).
  Unknown lease → 404 (Wings logs and proceeds).
- Lease tombstones are retained ≥ 7 days; duplicate/reordered late callbacks
  resolve against tombstones per the rules above.

**Commit failure after a successful Docker start** (normative): the game
keeps running. Wings marks the attempt `commit-pending`, retries commit with
backoff (30 s doubling, cap 5 min, for up to 10 min), then flags degraded
health. The granted lease already pins the generation, so nothing is
unsafe; manager reconciliation auto-commits a `granted` lease whose
container ID appears live with matching labels. Never stop a healthy server
over a bookkeeping failure.

**Reconcile authority rules** (when Wings state, Docker labels, and manager
lease files disagree):

| Wings reports | Docker label present | Manager lease | Resolution |
|---|---|---|---|
| yes | yes | granted/committed | keep; auto-commit if granted + container live |
| no | no | granted/committed, older than 10 min grace | release as stale, tombstone |
| no | yes (any container state) | none/tombstone | alarm "foreign labeled container"; generation stays pinned until an operator disposes it; `doctor` lists these |
| yes | no | none | Wings health warning (server believes it is managed; nothing backs it); next start re-prepares |

The manager never stops or removes containers; Docker-side actions belong to
Wings (disposal) or the operator.

Crash-recovery oracles span every boundary: kill Wings or the manager
between prepare↔create, create↔start, start↔commit, stop↔release — each must
converge via reconcile without deleting an in-use generation or leaking a
lease.

### L2 — readiness events (carried, with reset semantics)

As rev 3: default readiness = the egg `startup.done` match that already
drives Running ([verified] `server/listeners.go:149-182`); optional distinct
`WINGS_READY_MATCH` (literal or `regex:`); event
`Ready(kind, attemptID, timestamp)`, one-shot per attempt. Pinned reset
semantics (Phase-1 test surface): armed on `ProcessStartingState`; cleared
on stop, crash, and supersession; a matcher changed mid-run stores for the
next attempt; a timeout is never a Ready event. No provider or Soulmask
vocabulary anywhere in L2.

### L3 — startup dependencies (G4 resolved)

Declarations: `WINGS_START_AFTER=<uuid>` (dependent side; **exactly one
prerequisite in v1** — multi-prerequisite is a later extension),
`WINGS_DEPENDENCY_POLICY=wait|reject|start-prerequisite`,
`WINGS_DEPENDENCY_TIMEOUT=10m`, `WINGS_AUTOSTART_DEPENDENTS=1`
(prerequisite side).

Semantics:

- **Reverse index**: the server manager maintains prerequisite → dependents,
  rebuilt on every sync; a prerequisite reaching Ready consults it.
- **Queueing**: a dependent whose prerequisite is Starting queues *before*
  taking its own power lock; the queue is in-memory only. On Wings restart
  the queue is empty by construction and boot restoration's ordering logic
  (port of the proven deferral, `cmd/root.go:224-285` +
  `internal/cgroups/boot.go:41,91`, 64-hop cycle bound, fails open on
  cycles) covers the reboot case. Queue entries are cancelled — with an
  observable reason — on timeout, dependent deletion/suspension, prereq
  deletion, or explicit stop of the dependent.
- **Observability**: server resource/status output exposes
  `dependency_state: waiting|timeout|rejected` plus the blocking UUID while
  queued.
- **Removal/suspension/transfer**: deleting or suspending either side
  cancels queue entries and drops index edges; cross-node dependencies are
  invalid selectors (this is a same-node feature; the error says so).
- **Autostart honors user intent**: when a prerequisite reaches Ready,
  `WINGS_AUTOSTART_DEPENDENTS` starts only dependents that are Offline
  **and** whose last persisted state intent was running (the same
  `states.json` record boot restoration trusts, [verified]
  `cmd/root.go:170-259`) — restarting MAIN never resurrects a CLIENT the
  operator deliberately stopped. A node may opt into unconditional autostart
  explicitly (`autostart: always`).
- **No release vocabulary** in the engine: the cohort-generation equality
  check is *removed from L3*. The manager's cohort resolution is the
  authority (an ordinary restart resolves the cohort generation by
  construction); `doctor` cross-checks generation labels across a group as
  a diagnostic.

### L4 — deferred (decision 8)

Out of the v2 program. The safe rollout flow needs no patch: RCON save/
flush, then Wings stop — which transitions through Stopping so crash
detection never fires ([verified] `server/crash.go:47`,
`server/server.go:344-346`; `detect_clean_exit_as_crash` defaults true,
`config/config.go:258-270` — which is also why raw `SaveAndExit` without
Wings remains a forbidden workflow). Revisit after migration.

## F1 — pre-boot chown skips correctly-owned entries (series `filesystem`) [A1]

A one-patch series, `depends: []`, touching `server/filesystem` only. It is
not part of the lifecycle series and never appears in an L-series PR.

**Behavior.** Inside the `WalkDirat` callback of `Filesystem.Chown`, read the
entry's ownership and call `Lchownat` only when it differs from the configured
uid/gid; apply the same check to the walk-root `Lchownat` at
`filesystem.go:268`.

**Promoted by decision 10** [A2]. Rev 5's Amendment 1 argued F1 in on side
benefits, because provider exposure is immune to F-b via invariant 14. With
`host-bind` as the MVP that argument is obsolete: **host-bind `ro` runs the
pre-boot walk straight over a read-only mount, so F1 is a direct MVP
dependency**, with `system.check_permissions_on_boot: false` as the only
zero-patch alternative. It is Phase 2 and the program's first upstream PR.

**A correction to the operational record** [A2]: `SOULMASK-TMPFS.md` reports
this failure as specific to an instance that is both `ROLE=main` and
`TMPFS=1` — "bind-mounted onto its *own* directory". The code does not
support that scoping. `Chown("/")` walks the whole server root through every
mount boundary and calls `Lchownat` on every entry
([verified] `server/filesystem/filesystem.go:277-292`), so **every** consumer
of a read-only bind fails the pre-boot walk, not just the population source.
The `ROLE=main` framing looks like an artifact of which instance was tested.
This makes F1 more load-bearing, not less, and it should be confirmed on the
first host-bind rehearsal before anyone plans around the narrower reading.

The original three arguments still stand on their own:

1. **It retires a live production risk, now.** The case-study node runs its
   shared tmpfs read-write (`SOULMASK_TMPFS_READONLY=0`) for exactly one
   reason: a `ROLE=main` + `TMPFS=1` instance is bind-mounted onto its own
   directory and cannot survive the read-only walk. Read-write reintroduces
   the partial-write corruption that read-only mode exists to prevent — the
   2026-07-29 incident, where steamcmd deleted the old `.pak` before the new
   one finished writing and left the tmpfs with a `.sig` and no `.pak`.
   `SOULMASK-TMPFS.md` already names this patch as the precondition for
   restoring read-only. F1 can ship in week one; Phase 6 is the far end of
   this program, and the risk is carried the whole way otherwise.
2. **It is upstreamable on its own merit, without the read-only story.** The
   current walk dirties every inode of a multi-gigabyte game install on every
   single start. `dirent.Info()` is an `Lstatat` ([verified]
   `internal/ufs/walk_unix.go:292`), so on an unchanged tree the patch trades
   one `lchownat` — inode dirty, journal write — for one `fstatat`, which is
   read-only. The common path gets *cheaper*; the read-only-mount fix falls
   out for free. Read-only content inside a server volume is a general
   pattern (immutable shared assets, RO network mounts), not a Soulmask
   peculiarity, so the PR needs none of our vocabulary.
3. **It is the cheapest possible first PR** — one file, no config key, no new
   surface — and worth spending before the far larger L1 review lands on the
   same maintainers.

**Honest limits.** F1 helps only when the read-only content already carries
the correct ownership; a genuinely wrong-owner entry on a read-only mount
still fails, and should. It does not make read-only content writable for
SFTP, backups or archive extraction — those fail on write, as they must.

**Zero-patch alternative, available today**: `system.check_permissions_on_boot:
false` ([verified] `config/config.go:238`, default `true`) disables the
pre-boot walk node-wide. It is coarse — it also gives up Wings' ownership
self-repair after manual or SFTP edits — and it is recorded here as the
stopgap if F1 slips, not as the answer.

## Upstream ladder (all Wings series)

**F1 first** (smallest, independent, no protocol surface), then: L1 admission
(optionally without dynamic mounts for a smaller first review) → L1b dynamic
RO mounts + leases + disposal → L2 readiness → L3 dependencies. Each PR ships
non-Soulmask use cases. Never upstream: app IDs, game paths, tmpfs copy logic,
egg-named unit triggering, cgroup-named lifecycle features.

## Layer 2 — `srdm`, the shared-ramdisk-depot-manager

### Exposure drivers (decision 10) [A2]

Everything `srdm` does up to the moment a generation becomes visible to a
server is identical in both modes. Only the last step forks:

```text
store → transaction → verified immutable release
      → publication (op tmpfs → hold services → verify → RO bind)
      → EXPOSURE DRIVER                    ← the only fork
           ├─ host-bind  (stock Wings)   bind into the volume path
           └─ provider   (L1 + L1b)      Docker mounts + leases, per start
```

Shared by both: the release store, manifests and verification, publication
topology, per-class hold services and charging, retention, journal, doctor,
CLI. `srdm` is one product with two exposure drivers, never two products.

#### `host-bind` — the v1 default, no Wings patch

The generation's per-class RO bind is mounted onto the corresponding path
under `/var/lib/pterodactyl/volumes/<uuid>/…`. This is what the legacy
`soulmask_tmpfs` does; `srdm` does it with verified immutable generations,
atomic activate/rollback, a journal, retention, and a doctor behind it.

**Hard preconditions — refuse to operate, do not warn:**

1. The Wings container carries `propagation: rslave` on `/var/lib/pterodactyl`
   (F-a), and the host peer group is `shared`. Without it, every mount and
   unmount `srdm` performs is either invisible to Wings or leaves a ghost.
   `doctor` checks both and names the fix.
2. For `access: ro`, one of: **F1** in the running Wings build, **or**
   `system.check_permissions_on_boot: false` in the node config. Otherwise
   the pre-boot chown walk fails `EROFS` (F-b) and the server cannot start.
   `srdm` detects the condition up front rather than letting an operator meet
   it as an unexplained start failure at an inconvenient hour.
3. Activate, rollback and teardown require the affected consumers **stopped**.
   There is no disposal callback in this mode, so `srdm` resolves consumers
   itself — running containers by volume path, plus `/proc/*/mountinfo` — and
   refuses while any hold remains. A container still holding the bind keeps
   the tmpfs pages charged after a host unmount (§Generation slices), so this
   is a correctness gate, not politeness.

**What host-bind gives up, itemized** (this is the price of decision 10, and
it is paid knowingly):

| Property | provider | host-bind |
|---|---|---|
| Invariant 14 (content out of the volume tree) | held | **waived** |
| SFTP / backups / disk accounting | content invisible | content visible, counted, backed up (≈ 2.4 GB per Soulmask server) |
| Generation attestation | Wings-constructed Docker labels | manager-side consumer registry only |
| Admission / lease / reconcile authority | protocol-enforced | manager bookkeeping; an operator can start a server `srdm` does not know about |
| Auto-disposal on stop | yes (decision 5) | operator-ordered; `srdm` refuses unsafe teardown |
| Per-start generation resolution | yes | no — applies to servers at rest |

That last row costs less than it looks: v1 acquisition is **strictly manual**
already (decision 6), so activation was always an operator action against a
stopped cohort.

#### `access: ro | rw` (decision 11)

`ro` is the default and the safe mode: writes fail `EROFS` immediately rather
than corrupting a generation halfway.

`rw` is permitted **only when the generation has exactly one consumer**. With
two, a write is the 2026-07-29 incident by construction: the peer holds the
deleted old `.pak` open, the tmpfs exhausts mid-write, and the generation is
left with a new `.sig` and no `.pak` — survivable for the process that
already mmap'd the old inode, fatal for the next start. `srdm` refuses a
second consumer on an `rw` generation; it does not warn.

A generation exposed `rw` is marked **dirty-capable**. Its manifest stops
being authoritative the moment content diverges; `doctor` re-hashes on demand
and reports drift. While dirty it may not be promoted, shared to a second
consumer, or used as a population source for another generation.

**Writes are ephemeral.** They live in the tmpfs and evaporate on republish,
teardown or reboot. This must be loud in the operator docs: "steamcmd
reported Success" and "the update survives a reboot" are different claims,
and conflating them is how a node quietly serves stale content — the
2026-07-21 incident in a new costume.

What `rw` buys is adoption: `AUTO_UPDATE=1` and the game's own updater keep
working unchanged. No egg surgery, no operator retraining, and on the next
container recreate `srdm` can hand over a freshly published generation.

#### `harvest` (decision 12)

`srdm harvest <generation>` turns the result of an in-place update into a
first-class release:

```text
1. refuse if any consumer is running (writes must be quiesced)
2. re-walk the tmpfs tree, recompute the per-file SHA-256 manifest
3. classify every path; an unclassified new path blocks promotion
   (the same rule the SteamCMD path obeys)
4. run the profile's probes; record build identity where discoverable
5. write the transaction into the persistent store, fsync COMPLETE last
6. journal the operation with provenance: harvested-from-<generation>,
   not staged-from-<source>
```

This is today's manual procedure — ROLE=main updates on disk, then
repopulate — automated, verified and rollback-able. Its programme
consequence is large: **the containerized SteamCMD driver leaves the MVP
critical path.** The game's own updater is a legitimate acquisition source;
`harvest` is what makes its output trustworthy. The SteamCMD driver remains
in the plan (§SteamCMD driver) as the unattended path, but it is no longer
what stands between the node and a better system.

### Process and package layout (carried)

One Go binary `srdm` (daemon + CLI subcommands `daemon, stage,
status, activate, rollback, gc, doctor, operation`); packages as rev 3
(`internal/{config,profile,store,source/steam,publish,lease,providerapi,
adminapi,journal}`). Sockets: provider socket (above) and
`/run/srdm/admin.sock` (0600 root). The CLI refuses when the daemon
is down except `doctor --offline`.

### Privilege model (issue 11 — honest)

The daemon is a **privileged node service**. v1 runs it as root; that is a
deliberate, stated choice, not an oversight:

| Component | Identity | Capabilities / access |
|---|---|---|
| daemon (control, publish, provider + admin APIs, journal) | root | mount/umount, systemd D-Bus (transient units), Docker socket (worker launch + label reconciliation only), state dir ownership |
| stage worker (SteamCMD) | uid 988 inside the runner container | network egress, its transaction dir only |
| populate/hold worker | root, inside its own transient unit | read release store, write its op tmpfs |
| CLI | root via admin.sock | — |

The network-facing downloader is unprivileged and containerized; the daemon
minimizes parsing of foreign data — manifest hashing, archive/ACF parsing
run in workers; the daemon validates typed result records only. Documented
alongside: filesystem owners (store `srdm:srdm`,
0755/0644 world-readable — content carries no secrets; a future flag maps a
group for profiles that differ), systemd policy (only
`srdm-*` units), and the child-worker credential table above.

### Worker contract (issue 10 — single-writer preserved)

Stage and publish run as systemd transient units, so multiple processes
exist; authority does not:

- a worker writes **only** inside its operation-private directory
  (transaction dir, or op tmpfs) — never group/channel/lease/journal state;
- a worker ends by writing one fsync'd result record
  (`result.json` in its op dir): outcome, artifact paths, sizes, hashes,
  timings;
- the daemon validates the record and performs every authoritative commit
  (release promotion, published-state flip, lease changes, journal);
- cancellation = daemon stops the unit, marks the operation
  cancelled, quarantines the op dir;
- worker timeout = per-operation deadline enforced by the daemon (unit
  properties `RuntimeMaxSec` as backstop);
- daemon death while a worker runs: on restart the daemon adopts operations
  whose units are still active (re-attaches by unit name), quarantines
  operations whose units are gone without a result record;
- a result record for an operation the daemon no longer knows is
  quarantined, never auto-committed.

### Persistent release store, journal, SteamCMD driver (carried)

Unchanged from rev 3: transaction → classification (unclassified new path
blocks promotion) → per-file SHA-256 manifest → probes → ownership
normalization → fsync'd `COMPLETE` → atomic channel symlink flip;
kill-at-every-phase gate. Journal: durable per-operation records + events
JSONL + journald; no credentials anywhere. SteamCMD driver: containerized
as uid 988 with the egg's own runner image; `app_info_print` for build
identity; `validate` unconditional at release creation; identity from
`appmanifest_3017300.acf`; stage jobs in `srdm-stage.slice`
(io.max/CPUQuota/MemoryHigh are the real limiters).

**Manual-only in v1** (decision 6): `stage` is operator-invoked; it is
idempotent (build-identity check first; no-op with a journal record when
current). No timer, no scheduler, no polling component. Automatic
detection/staging is a post-v1 package that will bring its own
configuration, state, journaling, failure policy, and acceptance tests.

### Generation slices and charging (issue 7 — corrected)

Kernel grounding unchanged (cgroup-v2 "Memory Ownership": areas are charged
to the instantiating cgroup and stay charged; shared-area charging is
indeterminate and migrates toward cgroups with allowance; reclaim-root's own
protection is skipped). One cgroup cannot hold two memory policies, so the
rev-3 "one holder slice per generation" is replaced:

```text
srdm-gen-<g8>.slice                  # aggregate per generation
├─ srdm-hold-<g8>-pak.service        # populate + hold, pak policy
└─ srdm-hold-<g8>-code.service       # populate + hold, code policy
```

`<g8>` = first 8 hex of SHA-256(release-id), for unit-name sanity; the full
identity lives in the unit `Description` and the journal.

- Each **hold service** is a transient `Type=oneshot`,
  `RemainAfterExit=yes` unit whose `ExecStart` is the populate+verify worker
  for its class. The pages it faults are charged to the unit's own cgroup;
  after `ExecStart` exits the unit remains active, its cgroup persists, and
  the charges stay governed by the unit's properties — populate and hold are
  the *same* unit precisely so the charge and the policy can never separate.
- Class memory policy is set on the hold service:
  pak → `MemoryMin=150M, MemoryZSwapMax=0, MemoryZSwapWriteback=yes`;
  code → `MemoryMin=200M` (zswap default). [measured] calibrations carried
  from the retired `soulmask-paks.slice` / `soulmask-static.slice`.
- **Privileged e2e oracle, not an assumption** (review): after the worker
  exits, the unit is active, `memory.current` of its cgroup ≈ class size,
  and the properties read back; if any systemd version fails to keep an
  active-but-empty service's cgroup alive, the fallback is an explicit
  minimal hold process — the oracle decides, the spec allows both.
- **Parent protection is backed** (review): the admin-owned
  `srdm.slice` unit file carries `MemoryMin ≥ Σ active class
  floors` (Soulmask: ≥ 350M; ship 512M), reconciled against the host budget
  next to `wings.slice` (16 GB host: 8G tier + 512M releases — fits).
  Without this, the class floors are arithmetically dead exactly as a child
  floor under an unprotected parent always is.
- **zswap policy is charge-holder policy, not a per-page guarantee**
  (review): a generation page reclaimed and later re-faulted by a game is
  re-charged to that game's cgroup, whose zswap policy then applies. This
  migration is expected; the hold floor keeps the populate-time hot set
  resident, per-game floors cover re-faulted pages, and `doctor` explains a
  shrinking hold `memory.current` instead of alarming.
- Teardown order (uncharging by unmount): remove the RO bind exposure →
  unmount the op tmpfs (frees and uncharges every page) → stop the hold
  service → stop the generation slice. `doctor` watches
  `cgroup.stat nr_dying_descendants` for leaks.
- **"Frees and uncharges" holds only when nothing else holds a reference**
  [A1]. A game container that still has the bind in its own `rprivate`
  namespace keeps the superblock — and every page — alive across the host
  unmount; the memory is not returned and the hold cgroup does not drain.
  Disposal (decision 5) and the GC rule "no labeled container in any state"
  are therefore *load-bearing preconditions* for teardown rather than
  bookkeeping tidiness, and teardown refuses rather than proceeding into a
  silent leak when they are unmet. Oracle 15 measures the difference in
  both directions.

### Publication mount topology (issue 8 — exact)

Per class, in order; every step recorded in the operation journal before it
executes:

```text
1. mkdir -p /run/srdm/.op/<op-id>/<class>          (0700, root)
2. mount -t tmpfs -o size=<class-size>,mode=0755,nodev,nosuid[,noexec]
       tmpfs /run/srdm/.op/<op-id>/<class>
       # noexec for pak (data only); code keeps exec
3. hold service starts; worker populates <op>/<class>/root/ from the
   release store, verifies EVERY file against the manifest, chmod -R a-w
4. daemon creates /run/srdm/<profile>/<generation>/<class>,
   then: mount --bind <op>/<class>/root  <final>
         mount -o remount,ro,bind        <final>
5. daemon fsyncs the published-state record; only now may prepare-start
   return this generation
```

Rules: the visible path appears only as a read-only bind of an
already-verified tree — no renaming of mountpoints, no pre-verification
exposure; class size = `ceil(manifest × 1.15)` rounded to 64 M (Soulmask:
pak 2 G, code 512 M), a hard cap — ENOSPC quarantines the generation;
`/run/srdm` propagation is the host default (shared into the
`rslave` Wings-container view; Docker resolves game-container sources in the
host namespace at create).

**Recovery** inspects three sources and trusts their intersection:
`/proc/self/mountinfo` (which op/final mounts exist), durable operation
state, and unit state (`systemctl show` of hold units). A `COMPLETE` or
published-state file alone proves nothing about mount topology (review) —
a published record without its mounts triggers republish; mounts without a
record are torn down as orphans.

### Boot ordering (issue 9 — corrected)

The hard requirement is a contract, not a unit name:

```text
release store available → assigned generations republished →
provider socket ready → Wings restores consumers
```

- The republish path needs **no Docker**: `srdm-restore.service`
  (`After=local-fs.target`, part of the same binary) republishes assigned
  generations and opens the provider socket. Ordering against
  `docker.service` is neither required nor claimed.
- Where Wings is unit-managed (native service or a compose wrapper unit),
  add `After=srdm-restore.service` to that unit — ordering against
  the **actual Wings service**, not Docker generically.
- Where Wings is a restart-policy container (this host today), the ordering
  net is Wings-side: the L1 boot-restore retry
  (`boot_retry_grace`/`interval`) absorbs a late manager without unit
  coupling.
- Late Docker socket: the daemon starts degraded — republish and provider
  service work; stage/label-reconciliation retry until Docker appears, each
  retry journaled.

### Leases and GC (carried, simplified by disposal)

Lease states `granted → committed → released(+tombstone)`. With disposal
(decision 5), a cleanly stopped managed server leaves neither lease nor
labeled definition, so generation GC's rule — removable when not assigned,
no live lease, no labeled container in any state — passes naturally after a
cohort stop. Foreign labeled containers (reconcile table) pin their
generation until an operator disposes them. Persistent-release retention
unchanged (≥ 3, leased always kept).

### Manager v1 scope (updated)

Rescoped by decision 10 [A2] — v1 is the vanilla-Wings product:

**v1 includes**: profile engine + Soulmask profile; transactions → validated
immutable releases; single active generation per group; per-class tmpfs +
hold services + charging; **`exposure: host-bind` with the `ro|rw` axis**;
**`harvest`**; consumer registry + teardown safety; explicit offline
`activate`/`rollback`; crash-safe state + boot republish; journal; CLI;
`doctor` including the host-bind preconditions.

**v2 adds**: `exposure: provider` — provider protocol v1, leases,
Wings-constructed labels, reconcile authority, per-start resolution — once
L1/L1b land (Phase 5). Everything v1 built is reused unchanged; the cutover
is a config flip (Phase 7).

**Moved off the critical path but still planned**: the containerized SteamCMD
driver (decision 12 — `harvest` covers acquisition for the MVP).

**Excluded from both**: any automatic acquisition (decision 6), dual
generations, rollout orchestration (runbook + L3 ordering until the post-v1
`rollout` command), RCON scheduling, network API, multi-node, save
restoration.

**Versioned from day one**: manifest, group state, consumer registry, journal,
protocol, label names — and the **exposure driver name and its options**, so a
v1 host-bind deployment's state is readable by the v2 binary that will flip it
to provider.

### Enrollment (G1 — normative)

Happy path:

1. Node admin installs the manager and registers the provider block in
   Wings node config (once).
2. Node config authorizes the egg and the profile/group namespace
   (`allowed_eggs`, selector validation).
3. Admin creates the group in the manager:
   `srdm group create soulmask-prod --profile soulmask
   --mode cohort --auto-enroll allow-listed` (modes:
   `off | allow-listed | any-authorized`).
4. Panel admin sets the admin-only server variables
   (provider/profile/group/channel/release).
5. Membership: with `auto-enroll: allow-listed`, the UUID must be on the
   group's allow-list (`group allow <uuid>`); `any-authorized` accepts any
   server the node already authorized for the egg+profile; `off` requires
   explicit `group add-member`. Enrolment is idempotent.
6. First Panel start either succeeds or returns the precise error
   (`unknown-group`, membership refusal, `generation-not-published`) with
   the fixing CLI command in the message.

Move/remove: `group remove-member` / re-pointing the server's group variable
takes effect at the next start; a running lease is never disturbed by
membership edits. Group deletion is refused while members hold leases.
Authorization is these rules — a syntactically valid group name grants
nothing by itself.

## Layer 3 — Soulmask application profile (carried, three updates)

Profile YAML, cluster policy, probes, readiness lines, RCON adapter, and
classification exactly as rev 3 (managed roots incl. `WS/Content/Paks` →
pak class; `Steam`, `WS/Saved`, `steamapps`, `ksm-optin.so`, `.steam`,
`.config` mutable; `WS/Config` **[open]** pending the runtime write audit).
Updates:

1. **Install guard exact-matches** (G2):
   `[ "$WINGS_CONTENT_PROVIDER" = "srdm" ] &&
   [ "$WINGS_CONTENT_PROFILE" = "soulmask" ]` → skip content download,
   create mutable dirs + helpers only. A typo or an unrelated future
   provider installs normally instead of silently skipping.
2. **Egg configuration files vs managed roots**: before migration, the
   collision rule (§L1 validation) is checked against the egg's
   `ProcessConfiguration.ConfigurationFiles`; the `WS/Config` audit decides
   ownership if any overlap exists.
3. Cluster wiring, real UUIDs, `AUTO_UPDATE=0`, and the RO-mounts-make-
   self-update-loud property carried unchanged.

## Rollout flow (updated for disposal; no L4)

1. Stage H (operator command; G keeps serving).
2. Capacity + mode check (cohort offline cutover; H tmpfs not yet
   allocated).
3. Player query + broadcast (RCON helper).
4. `SaveWorld 0` + `BackupDataBase world`; verify; record `world.db` +
   `GameXishu.json` tripwire hashes.
5. Wings-stop CLIENT, await Offline **and disposal**; same for MAIN.
   (Disposal is automatic — decision 5; the runbook verifies
   leases-released + no labeled containers via `status`.)
6. Under the group lock: tear down G (bind exposure → op tmpfs unmount →
   hold services → gen slice), publish H per class, assign cohort H.
7. Release the lock before starting anything.
8. Start MAIN via Wings (prepare resolves H); await Ready (registration
   line).
9. Start CLIENT (L3 ordering, or the runbook observing Ready).
10. Verify identical generation labels, RO mounts, readiness, RCON; soak;
    mark H verified.
11. Failure → stop group, reconstruct G (rollback = previous release);
    binary rollback after H wrote saves stays behind the save-schema gate.

## Failure and selection policy (carried + disposal rows)

All rev-3 rows stand. Added/changed:

| Condition | Manager behavior | Soulmask policy |
|---|---|---|
| Disposal fails (Docker error) | Lease stays held; generation stays pinned; server health degraded | Retry; rollout blocks visibly at step 5 |
| Foreign labeled container found in reconcile | Alarm + pin; `doctor` lists; operator disposes | Never auto-removed |
| Commit fails after successful start | Keep running; commit-pending + retry; reconcile auto-commits | Never stop a healthy server |
| Boot restore races republish | `generation-degraded` (503, retryable); Wings boot retry absorbs | No stale mounts, ever |
| Host mount torn down while Wings' volumes bind is `rprivate` (F-a) | n/a — Wings-side | Ghost mount inside Wings; the next pre-boot walk fails on a path the host no longer has. Prevented by the mount contract, checked by `doctor`, oracle 14 [A1] |
| Teardown attempted while a consumer still holds the bind (F-a) | Refuse teardown; report the holding container | Pages stay charged and the generation stays pinned — visibly, never silently. Disposal is the fix [A1] |

## Security model (carried + protocol hardening)

Rev-3 trust levels stand. Additions: peer-credential policy configurable
(`allowed_peer_uids`, default `[0]`; rootless unsupported in v1 — stated,
not implied); unknown fields rejected both directions; body-size caps;
prepare-ID cache and tombstones bound replay windows; the openat2 trust
statement (§L1) names the residual provider trust honestly; daemon privilege
stated plainly (§Privilege model). Never accepted from egg/server
variables: paths, unit names, shell, mount flags, credentials, another
server's volume, writability.

## Migration of the two live servers (carried, one correction)

**Rescoped by decision 10** [A2]: this migration now happens at **Phase 3**,
onto `exposure: host-bind`, against stock Wings carrying F1 and nothing else.
It is no longer the last act of the program but its main acceptance test —
the point at which `soulmask_tmpfs` is retired and production runs `srdm`.
The later flip to `exposure: provider` (Phase 7) is a config change plus a
container recreate, and reuses this migration's store, generations and
journal untouched; it is not a second migration.

Because host-bind occupies the same volume paths the legacy ramdisk does, the
cutover is a **replacement in place**, which is simpler than rev 5's model:
the egg does not change shape, `WS/Saved` is untouched as always, and rollback
is "stop `srdm`, restart `soulmask_tmpfs.service`" for as long as both are
installed. Keep that reversal available through the whole soak.

Steps as rev 3 (build+gate A, back up saves, first release + manifest
comparison, disposable rehearsal, maintenance-window cutover with egg/image
swap and legacy-ramdisk retirement, reboot test, update rehearsal), with the
G3 correction made explicit:

- **During soak, the migrated volumes still contain the legacy in-volume
  content.** SFTP, backups, and disk accounting continue to show and count
  it until step 8 archives it out of the volumes (~2.4 GB each). Only a
  *fresh* managed server is born without it. The rev-3 claim "backups
  shrink at migration" was wrong for migrated volumes; it is true after the
  post-soak archive step, and immediately for new servers.

Two ordering requirements added by the field findings:

- **The propagation change comes first** [A1]. The node's Wings compose must
  carry `propagation: rslave` on `/var/lib/pterodactyl` (and
  `/var/lib/docker/containers`), **and Wings must have been recreated with
  it**, before the legacy ramdisk binds are torn down. Tearing them down
  under `rprivate` leaves ghost mounts inside Wings and reproduces the
  2026-07-31 failure in the middle of the maintenance window — the worst
  possible moment, with saves already flushed and servers already stopped.
  On the case-study node this step is already done ([verified] live via
  `docker inspect`); on any other node it is a prerequisite, not a step.
- **F1 restores the read-only tmpfs, and does it before the migration, not
  as part of it** [A1]. The legacy ramdisk runs read-write today only
  because of F-b. Shipping F1 in Phase 1 lets `SOULMASK_TMPFS_READONLY=1`
  come back while the legacy system is still in service, which shrinks the
  corruption risk carried across the *whole* program instead of only at its
  end. Treating it as part of the migration would leave the window open for
  the entire duration.

## Acceptance oracles

### L1/L1b (attempt transaction + protocol + disposal)

1. Golden harness: no provider configured → byte-identical create payloads
   and event streams vs vanilla; zero socket operations.
2. Every real start source produces exactly one prepare; offline
   `CreateEnvironment()` produces none.
3. **Race**: Panel sync (`postServerSync`) lands between prepare and Docker
   create → the created container still carries the overlay mounts+labels;
   the sync's config changes are otherwise applied.
4. **Faults**: failure injected at every post-prepare step (config files,
   chown, create, start) → abort-start fires, overlay cleared, no
   container; restart after a failed attempt gets a fresh lease; an
   unselected start after a previously selected failed attempt is stock.
5. Collisions: provider mount vs default volume mount, passwd/machine-id,
   custom mounts, denied targets, ancestor-of-denied, duplicate targets,
   **and Wings-managed egg configuration files** — all rejected.
6. Protocol conformance (Phase-0a fixtures): idempotent prepare replay,
   request-conflict, duplicate/reordered commit/abort/release against live
   and tombstoned leases, oversize bodies, unknown fields both directions,
   deadline expiry, every error-code path.
7. Commit-pending: kill the manager between start and commit → server keeps
   running; commit retries; reconcile auto-commits; health degrades only
   after the retry budget.
8. **Disposal e2e**: stop two consumers → definitions disposed, leases
   released, G teardown passes, H activates, both restart on H. Crash path:
   exit state observed, crash restart recreates safely; disposal failure
   pins G visibly.
9. Boot: kill Wings/manager at every protocol boundary → reconcile
   converges; boot-restore retry absorbs a late manager; ordinary starts
   never retry.

### L2/L3

10. Ready: once per attempt, on the configured line, never on timeout;
    cleared on stop/crash/supersession; mid-run matcher change defers.
11. Dependencies: queue/timeout/reject observable with blocking UUID;
    cancellation on delete/suspend/stop; reboot deferral; cycle fails open;
    autostart starts only intent-running dependents; REST stays 202-async;
    no release vocabulary in the engine.

### Manager

12. Kill-at-every-phase (store), topology recovery from
    mountinfo+state+units (publish), worker-contract violations (a worker
    writing outside its op dir is detected in review/test), orphan adoption
    and quarantine, hold-service charging (active-but-empty unit owns
    ≈ class size; properties read back; teardown leaves
    `nr_dying_descendants` stable), parent-protection backing
    (`srdm.slice MemoryMin` present and ≥ Σ floors), ENOSPC
    quarantine, single-generation refusal while any lease or labeled
    container exists, EROFS on every managed root, unchanged `world.db`
    hashes, reboot republish before consumer starts.
13. **SFTP/backup (corrected)**: fresh managed server → managed content
    absent from SFTP/backup/disk accounting; migrated server → legacy
    in-volume content remains visible/counted/backed-up until archived.

### Mount propagation and filesystem (F-a, F-b, F1) [A1]

Oracles 14–15 run against the real containerized Wings shape, not unit
tests — propagation is a property of the deployment, and a mock cannot fail
the way `rprivate` fails.

14. **Propagation, both directions**: with `/var/lib/pterodactyl` bound
    `rprivate`, a host-side mount under a server volume is invisible inside
    Wings **and** a host-side unmount leaves a ghost that Wings still
    traverses; with `rslave`, both events are observed. The `rprivate` half
    is the regression test — it must reproduce the 2026-07-31 symptom, so
    that a future deployment losing the flag fails here rather than in
    production. `doctor` reports the effective propagation of every mount in
    the contract table plus the host peer-group state.
15. **Teardown actually frees**: publish a generation → start a consumer →
    dispose it → tear down → the hold unit's `memory.current` drops to ~0
    and `nr_dying_descendants` stays stable. Repeat *without* disposal: the
    pages remain charged after the host unmount, and the oracle asserts the
    manager **refused** the teardown and named the holding container,
    rather than proceeding into a silent leak.
16. **Invariant 14 regression**: with a generation published and a consumer
    running, `/var/lib/pterodactyl/volumes/<uuid>` contains mutable state
    and **no mount at all** — asserted from `/proc/self/mountinfo`, not by
    inspection — and the pre-boot chown walk completes clean on a restart.
    No code path in Wings or the manager creates a mount under
    `/var/lib/pterodactyl/volumes/**`.
17. **F1 correctness**: a tree containing a read-only, correctly-owned
    subtree chowns clean; a read-only subtree with a *wrong* owner still
    fails, with the actionable EROFS naming the path; a writable
    wrong-owner tree is still repaired exactly as vanilla repairs it. Run
    against both pinned trees.
18. **F1 cost**: syscall counts (`strace -c` or equivalent) over an
    unchanged multi-GB tree, before and after. The claim "cheaper on the
    common path" ships in the PR text only if this measures it — otherwise
    the PR argues correctness alone.

### Exposure drivers — host-bind, ro|rw, harvest (decisions 10–12) [A2]

These are the **MVP gate**. Phase 1 does not ship without them.

19. **Waiver stays in bounds**: under host-bind, managed content appears at
    exactly the declared class paths under the volume root and nowhere else;
    `WS/Saved/**` is never bound, never shadowed, and `world.db` +
    `GameXishu.json` hashes are unchanged across publish, activate, rollback
    and teardown. The invariant-14 waiver is bounded, not general.
20. **Preconditions refuse, not warn**: with `/var/lib/pterodactyl` bound
    `rprivate`, `srdm` refuses to expose and names the compose fix. With
    `access: ro` requested on a Wings build lacking F1 and with
    `check_permissions_on_boot: true`, `srdm` refuses and names both
    remedies. Neither failure is allowed to surface as a server start error.
21. **`rw` is single-consumer**: binding a second consumer to an `rw`
    generation is refused. A generation written through is marked
    dirty-capable; `doctor` re-hashes and reports drift; promotion, sharing
    and use-as-source are all refused while dirty.
22. **Ephemerality is observable**: write through an `rw` exposure, republish,
    and assert the write is gone and the journal says so. This oracle exists
    to make sure the documentation's loudest warning is actually true.
23. **`harvest` round-trip**: update in place through `rw` → `harvest` →
    the resulting release's manifest matches a from-scratch stage of the same
    build identity, byte for byte, and carries `harvested-from` provenance.
    An unclassified new path blocks promotion exactly as on the staged path.
    Harvest on a running consumer is refused.
24. **Teardown safety without disposal**: with a consumer still running,
    `activate`, `rollback` and teardown are all refused with the holding
    container named; after a clean stop they proceed and the hold unit's
    `memory.current` drops to ~0 (this is oracle 15 without the protocol's
    help — the mode that has to get it right by inspection).

### Gate

`tester-unified` with full run-uid identity; the privileged systemd-in-
Docker e2e harness extended with: the Phase-0a protocol conformance suite,
mount-propagation cases (`rprivate` vs `rslave` into containerized Wings —
**for the volumes root as well as the release store**, oracle 14 [A1]),
the chown-walk-over-read-only cases (oracles 16–17 [A1]), hold-service
charging including the still-held-bind case (oracle 15 [A1]), topology
recovery, disposal e2e, and the race/fault matrix above. The devcontainer is
the cockpit, not the gate.

## Defaults (corrected)

| Decision | Soulmask v1 |
|---|---|
| Persistent releases | ≥ 3 retained, immutable, hash-verified |
| Publication | Single generation; classes pak+code as hold services |
| **Exposure (v1)** | **`host-bind`, `access: ro`** — needs F1, no other patch [A2] |
| **Exposure (v2)** | `provider` after Phase 5; cutover is a config flip [A2] |
| Consumers | RO, one cohort (MAIN+CLIENT); leases only in provider mode |
| Acquisition | **Manual `stage` or `harvest`** (decisions 6, 12); SteamCMD driver off the MVP path; post-v1 package adds automation |
| Activation/rollback | Explicit CLI; cohort confirmed offline (host-bind: `srdm` verifies no container holds the bind; provider: + disposed) |
| Wings integration (v1) | none — stock Wings + F1; `check_permissions_on_boot` left `true` |
| Wings integration (v2) | `WINGS_CONTENT_PROVIDER=srdm`, required-when-selected, auto-disposal on |
| Start ordering | L3 `WINGS_START_AFTER`; Ready = registration line |
| RCON | Root-only local helper, fixed allow-list, no published port |
| External API control | None in v1 |
| Audit | Durable operation IDs + journald structured events |

## Open questions

1. `WS/Config` classification — audit during Phase 6 **[open]**.
2. Egg done-matcher vs distinct ready-matcher — decide at egg-variant time
   **[open]**.
3. Retention count (default 3) **[open]**.
4. KSM shim (M7 still open; orthogonal) **[open]**.
5. `prepare_may_publish` — excluded from protocol v1; revisit for
   unattended fleets **[open]**.
6. L4 revisit trigger: first need for `SaveAndExit`-style maintenance or
   synchronous rollout status **[open]**.

## Evidence index

All rev-3 anchors stand (power flow `server/power.go:56-200`; recreate
`environment/docker/power.go:26`; Running via matcher
`server/listeners.go:149-182`; mounts `server/mounts.go:22,66` +
`config/config.go:365`; install env `server/install.go:403`; crash
`server/crash.go:47` + `config/config.go:258-270` +
`server/server.go:317,344-346`; Sync callers `server/power.go:173`,
`router/router_server.go:145,158`, `server/install.go:89`,
`cmd/root.go:264`; boot restore `cmd/root.go:170-259`; REST/websocket power
`router/router_server.go:53`, `router/websocket/websocket.go:354-376`;
delete `router/router_server.go:192` + `environment/docker/container.go:271`;
backups/SFTP `server/backup.go:60`, `server/backup/backup_local.go:68`,
`sftp/handler.go:79`; locker `system/locker.go:34,47`). **New this
revision**: `server/update.go:21-31` — `SyncWithEnvironment()` snapshots
`Mounts: s.Mounts()` and `Labels` into `environment.Settings` (the issue-1
proof). Kernel/systemd/Docker citations and production measurements as
rev 3.

**New in Amendment 1** [A1]:

- `server/filesystem/filesystem.go:253-294` — `Filesystem.Chown` walks the
  server root and calls `Lchownat` unconditionally at `:268` (walk root) and
  `:287` (every entry); no already-correct-owner skip exists. The F-b proof.
- `server/power.go:207-214` — the pre-boot walk call site, guarded by
  `config.Get().System.CheckPermissionsOnBoot`.
- `config/config.go:238` — `CheckPermissionsOnBoot` defaults to `true`; the
  zero-patch stopgap and the reason the walk is on by default.
- `server/server.go:331` (`EnsureDataDirectoryExists`) and
  `sftp/handler.go:155,276` — the other `Chown` callers, neither of which
  runs on the ordinary start path.
- `internal/ufs/walk_unix.go:292` — `dirent.Info()` is an `Lstatat`; the
  syscall-cost basis for F1's "cheaper on the common path" claim.
- Production, [verified] live 2026-08-03: the case-study node's Wings
  container carries `Propagation: rslave` on `/var/lib/pterodactyl` and
  `/var/lib/docker/containers` (`docker inspect`), and the host `/` is
  `shared:1` with `/run` `shared:5` (`/proc/1/mountinfo`) — the peer-group
  precondition for `rslave`.
- [`../scripts/gstammtisch-guide/SOULMASK-TMPFS.md`](../scripts/gstammtisch-guide/SOULMASK-TMPFS.md)
  §"Read-only enforcement" — the 2026-07-29 partial-write incident and the
  accepted `SOULMASK_TMPFS_READONLY=0` tradeoff F1 retires. A local doc, so
  a hint by the evidence rule; the Wings claims it makes are re-verified
  against vanilla above.
