# Shared game releases and tmpfs lifecycle — v5, implementation go

- Status: **implementation go** (all conditional-go findings resolved),
  2026-07-23
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
| 7 | One holder slice cannot carry two class policies; parent protection unbacked | Manager blocker | **Adopted** — per-class populate+hold services inside a generation slice; each carries its class memory policy; `game-releases.slice` gains a protection budget; zswap restated as charge-holder policy → §Generation slices |
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
   impossible "one holder slice, two policies" shape; `game-releases.slice`
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

## Kickoff plan (the "go" sequence)

Contracts-first (decision 7). No Wings patch code lands before Phase 0a
freezes.

```text
Phase 0a  Protocol contract freeze                     [no Wings patches]
          - provider protocol v1 conformance fixtures: a fake provider
            (Go test server implementing §Provider protocol v1 exactly) and
            a fake Wings driver (client exercising every rule)
          - golden request/response vectors; idempotency, duplicate,
            reorder, crash-recovery, and fault scripts
          - frozen = fixtures committed + protocol version pinned
Phase 0b  Repository and gate preparation              [parallel with 0a]
          - patchstack series.yaml + SERIES tooling (below)
          - golden vanilla-compatibility harness (companion §Golden harness)
          - CI matrix runnable in tester-unified:
            vanilla | each series prefix per commit | combined DAG | both targets
Phase 1   Independent Wings patches (parallel)
          - resources R1 (config diagnostics), R2 (node placement)
          - lifecycle L2 (readiness events)
          gate: golden harness + per-commit build/vet/test
Phase 2   Lifecycle L1 + L1b against the frozen fixtures
          - start-attempt transaction, validation, callbacks, disposal,
            boot-restore retry
          gate: conformance suite + the race/fault oracles of §Acceptance
Phase 3   Resources R3–R5, R6 core, R7 (+R8 optional); integration I1 on
          the combined branch
          gate: privileged systemd e2e + lock-order race gate
Phase 4   Lifecycle L3
          gate: dependency-semantics oracles
Phase 5   Manager v1: store+journal+worker contract → publication topology
          e2e → provider server vs the Phase-0a fixtures → SteamCMD driver →
          CLI
          gate: kill-at-every-phase, topology recovery, charging/hold oracles
Phase 6   Soulmask profile + managed egg + migration rehearsal → migration
          window (§Migration)
```

Dependency DAG (issue 4):

```text
vanilla
├─ lifecycle: L1 → L1b        (provider admission → dynamic mounts/leases)
│             L2 → L3         (readiness → dependencies; independent of L1)
└─ resources: R1;  R2 → R3 → R4 → R5 → R6core → R7;  R8 after R5

combined branch v2/<ref> = resources ⊕ lifecycle ⊕ I1
  I1 = one integration patch binding L2 events into R6's ReadySignal registry
```

Every branch builds and tests at every commit. The **manager requires
L1 + L1b only**; L2/L3 improve the Soulmask cluster behavior; resources and
L4 are not protocol dependencies of the manager.

### Patchstack tooling (normative)

`patchstack/series.yaml` replaces prose:

```yaml
series:
  cgroup:      {layout: legacy, bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}}
  lifecycle:   {bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}, depends: []}
  resources:   {bases: {pterodactyl: v1.13.1, pelican: "main@70f3344"}, depends: []}
  integration: {depends: [resources, lifecycle]}
combined_order: [resources, lifecycle, integration]   # the one legal order
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
    shared-release:
      socket: /run/wings-providers/shared-release.sock
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
      allowed_source_roots: [/run/game-releases, /var/lib/game-releases]
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

Containerized Wings (production shape) additionally mounts:
`/run/wings-providers`, `/run/game-releases:ro,rslave`,
`/var/lib/game-releases:ro,rslave` — `rslave` because generations are
mounted after the Wings container starts and Docker's default bind
propagation is `rprivate`. An operator refusing those mounts must set
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
GET  /v1/healthz         → 200 {protocol: 1, provider: "shared-release"}
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

### Upstream ladder

L1 admission (optionally without dynamic mounts for a smaller first review) →
L1b dynamic RO mounts + leases + disposal → L2 readiness → L3 dependencies.
Each PR ships non-Soulmask use cases. Never upstream: app IDs, game paths,
tmpfs copy logic, egg-named unit triggering, cgroup-named lifecycle
features.

## Layer 2 — shared-release manager

### Process and package layout (carried)

One Go binary `shared-release` (daemon + CLI subcommands `daemon, stage,
status, activate, rollback, gc, doctor, operation`); packages as rev 3
(`internal/{config,profile,store,source/steam,publish,lease,providerapi,
adminapi,journal}`). Sockets: provider socket (above) and
`/run/game-releases/admin.sock` (0600 root). The CLI refuses when the daemon
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
alongside: filesystem owners (store `game-releases:game-releases`,
0755/0644 world-readable — content carries no secrets; a future flag maps a
group for profiles that differ), systemd policy (only
`game-releases-*` units), and the child-worker credential table above.

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
`appmanifest_3017300.acf`; stage jobs in `game-releases-stage.slice`
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
game-releases-gen-<g8>.slice                  # aggregate per generation
├─ game-releases-hold-<g8>-pak.service        # populate + hold, pak policy
└─ game-releases-hold-<g8>-code.service       # populate + hold, code policy
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
  `game-releases.slice` unit file carries `MemoryMin ≥ Σ active class
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

### Publication mount topology (issue 8 — exact)

Per class, in order; every step recorded in the operation journal before it
executes:

```text
1. mkdir -p /run/game-releases/.op/<op-id>/<class>          (0700, root)
2. mount -t tmpfs -o size=<class-size>,mode=0755,nodev,nosuid[,noexec]
       tmpfs /run/game-releases/.op/<op-id>/<class>
       # noexec for pak (data only); code keeps exec
3. hold service starts; worker populates <op>/<class>/root/ from the
   release store, verifies EVERY file against the manifest, chmod -R a-w
4. daemon creates /run/game-releases/<profile>/<generation>/<class>,
   then: mount --bind <op>/<class>/root  <final>
         mount -o remount,ro,bind        <final>
5. daemon fsyncs the published-state record; only now may prepare-start
   return this generation
```

Rules: the visible path appears only as a read-only bind of an
already-verified tree — no renaming of mountpoints, no pre-verification
exposure; class size = `ceil(manifest × 1.15)` rounded to 64 M (Soulmask:
pak 2 G, code 512 M), a hard cap — ENOSPC quarantines the generation;
`/run/game-releases` propagation is the host default (shared into the
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

- The republish path needs **no Docker**: `shared-release-restore.service`
  (`After=local-fs.target`, part of the same binary) republishes assigned
  generations and opens the provider socket. Ordering against
  `docker.service` is neither required nor claimed.
- Where Wings is unit-managed (native service or a compose wrapper unit),
  add `After=shared-release-restore.service` to that unit — ordering against
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

Includes: profile engine + Soulmask profile; SteamCMD driver; transactions →
validated immutable releases; single active generation per group, per-class
tmpfs + hold services; provider protocol v1; explicit offline
`activate`/`rollback`; crash-safe state + boot republish; journal; CLI.
Excludes: **any automatic acquisition** (decision 6), dual generations,
rollout orchestration (runbook + L3 ordering until the post-v1 `rollout`
command), RCON scheduling, network API, multi-node, save restoration.
Versioned from day one: manifest, group/lease state, journal, protocol,
label names.

### Enrollment (G1 — normative)

Happy path:

1. Node admin installs the manager and registers the provider block in
   Wings node config (once).
2. Node config authorizes the egg and the profile/group namespace
   (`allowed_eggs`, selector validation).
3. Admin creates the group in the manager:
   `shared-release group create soulmask-prod --profile soulmask
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
   `[ "$WINGS_CONTENT_PROVIDER" = "shared-release" ] &&
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
    (`game-releases.slice MemoryMin` present and ≥ Σ floors), ENOSPC
    quarantine, single-generation refusal while any lease or labeled
    container exists, EROFS on every managed root, unchanged `world.db`
    hashes, reboot republish before consumer starts.
13. **SFTP/backup (corrected)**: fresh managed server → managed content
    absent from SFTP/backup/disk accounting; migrated server → legacy
    in-volume content remains visible/counted/backed-up until archived.

### Gate

`tester-unified` with full run-uid identity; the privileged systemd-in-
Docker e2e harness extended with: the Phase-0a protocol conformance suite,
mount-propagation cases (`rprivate` vs `rslave` into containerized Wings),
hold-service charging, topology recovery, disposal e2e, and the race/fault
matrix above. The devcontainer is the cockpit, not the gate.

## Defaults (corrected)

| Decision | Soulmask v1 |
|---|---|
| Persistent releases | ≥ 3 retained, immutable, hash-verified |
| Publication | Single generation; classes pak+code as hold services |
| Consumers | RO leases, one cohort (MAIN+CLIENT) |
| Acquisition | **Manual `stage` only** (decision 6); post-v1 package adds automation |
| Activation/rollback | Explicit CLI, cohort confirmed offline + disposed |
| Wings integration | `WINGS_CONTENT_PROVIDER=shared-release`, required-when-selected, auto-disposal on |
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
