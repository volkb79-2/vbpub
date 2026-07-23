# Shared game releases and tmpfs lifecycle — v3 proposal, fable revision

- Status: implementation-ready revised architecture, 2026-07-23
- Input: [`shared-ramdisk-update-lifecycle-3-codex.md`](shared-ramdisk-update-lifecycle-3-codex.md)
  (the codex v3 proposal). This document supersedes it; the codex v1/v2/v3 chain
  remains the design history.
- Companion: [`shared-ramdisk-update-lifecycle-cgroups-1-codex-fable.md`](shared-ramdisk-update-lifecycle-cgroups-1-codex-fable.md)
  (the resources/cgroups series of the same Wings v2 program).
- Starting point: vanilla Pterodactyl Wings v1.13.1 (tag = commit `e771816d`)
  and pelican-dev/wings `main @ 70f3344` (v1.0.0-beta26+1). The local patches
  0001–0011 are prior art and production evidence, not dependencies.
- Evidence rule: every load-bearing claim about Wings cites the vanilla source
  (`git show v1.13.1:<path>` in `build/wings-pterodactyl`); kernel behavior
  cites the kernel's cgroup-v2/tmpfs documentation; systemd/Docker behavior
  cites their upstream docs or source. Local documents (STRATEGY.md,
  CGROUP-SEMANTICS.md, MEASUREMENTS.md) are treated as hints and host-local
  measurements, never as the definition of a mechanism. See the Evidence index
  at the end.
- State rule (unchanged, absolute): `WS/Saved/**` — especially every `world.db`
  and `WS/Saved/GameplaySettings/GameXishu.json` — is never shared, never used
  as content input, and never modified by a release transaction.

## Decision log (interview, 2026-07-23)

These four decisions were made explicitly by the operator and reshape the codex
v3 plan. They are recorded here so implementation does not re-litigate them.

1. **Upstream target: dual, Pelican for PRs.** The v2 series is designed and
   verified against Pterodactyl Wings v1.13.1 (what production runs) and
   carried dual-target like the existing patchstack; pelican-dev/wings is the
   audience for actual upstream submissions (Pterodactyl is in maintenance).
2. **Manager shape: one Go binary, daemon + CLI.** Same toolchain as Wings.
   The daemon owns the provider socket, leases, reconciliation, and journal;
   the CLI is a subcommand talking to the daemon over a local admin socket;
   stage/publish workers run as systemd transient units.
3. **Sequencing: Wings v2 patches first, manager second.** Plan and build the
   full v2 Wings patch program from vanilla — everything the release manager
   needs (provider, mounts, leases, dependency/readiness, maintenance intent)
   *and* the redesigned slices/cgroups support — then build the shared-release
   manager against that v2-patched Wings. There is **no provider-less interim
   mode** and no bridge through the current patch 0011 unit trigger. (This
   replaces codex v3's "manager MVP before the provider patch" ordering.)
4. **Document depth: normative.** Drift-prone parts — protocol, config
   schemas, state layout, unit and label naming, Wings touch-points — are
   pinned normatively in this document. Genuinely open items are marked
   **[open]**.

## Executive conclusion

The three-layer decomposition stands:

```text
1. Wings v2 lifecycle capabilities (patch series "lifecycle", from vanilla)
   - external start-preparation/admission provider
   - provider-supplied read-only dynamic mounts with leases
   - generic readiness events + startup dependency coordination
   - optional maintenance intent / observable power operations

2. Independent shared-release manager (Go daemon + CLI)
   - source drivers (SteamCMD first)
   - immutable persistent releases, validation, rollback
   - tmpfs generations with per-class memory policy, multiple RO consumers
   - channels, pins, leases, reconciliation, rollout CLI

3. Game/application profile (Soulmask first)
   - app IDs, layout classification, content classes, readiness line
   - MAIN/CLIENT cohort policy, save/stop order, RCON adapter
   - tmpfs and memory/cgroup policy for this host
```

Wings does not learn SteamCMD, Soulmask paths, tmpfs copying, or release
rollback. The manager does not impersonate Wings for ordinary Panel starts.
Wings owns container lifecycle and calls a registered provider before creation;
the provider owns content preparation and returns an attested, read-only mount
set. Every start source — Panel websocket, REST, crash recovery, boot
restoration — funnels through `HandlePowerAction` → `onBeforeStart()`
([verified] `server/power.go:56,123-135,171`; boot: `cmd/root.go:237-259`;
crash: `server/crash.go:47`; REST: `router/router_server.go:53`; websocket:
`router/websocket/websocket.go:354-376`), so the provider gate covers all of
them by construction.

All Wings additions are strictly opt-in. A v2-patched Wings with no lifecycle
provider configured, or a server that selects none, follows the stock container
lifecycle byte-for-byte. Compatibility is an acceptance oracle, not a
preference.

Persistent disk remains the release **authority**; tmpfs is the Soulmask
serving backend (`tmpfs-required`). The production observation driving this is
unchanged: unrelated host disk activity harmed the game even when the game did
no I/O — clean pak page-cache eviction followed by disk refaults. tmpfs turns
those pages into swap-backed shmem that cannot be dropped as clean cache and
participates in zswap/swap policy instead
([kernel] tmpfs documentation: tmpfs pages are swap-backed; [measured]
SOULMASK.md §2c).

Downloading and validating a candidate release happens on persistent disk while
the old generation keeps serving. Cutover is a separate, explicit operation.
On this 16 GB host it is a coordinated cohort cutover with all consumers
stopped, one tmpfs generation at a time ([measured] MEMORY-ARCHITECTURE.md:
16 GB RAM, two ~6 GiB hot sets; a second ~2.5 GB generation is refused by the
capacity preflight while both instances run).

Per-server `latest`/`previous`/pinned intent is supported by the general
manager. It never overrides a group's safety policy: the Soulmask MAIN/CLIENT
pair uses `cohort` — while one member runs generation G, an ordinary restart of
the other also gets G; a new candidate becomes the cohort generation only
through an explicit or scheduled rollout.

## Changes made relative to the codex v3 input

Numbered so review can address them individually. Items 1–5 are consequences
of the 2026-07-23 interview decisions; the rest are corrections, added
precision, or new design content.

1. **Re-sequenced the whole plan: Wings v2 patch program first, manager
   second.** Codex v3 shipped a manager MVP whose milestone 4 introduced the
   Wings provider; the operator chose the reverse. The manager now targets
   v2-patched Wings from day one, `prepare-start` never has to fall back to
   host-side pre-created binds, and the "MVP before the full manager" framing
   becomes "manager v1 scope" (§Layer 2). The current patch 0011 unit-trigger
   bridge is explicitly *not* part of the path and is retired at migration.
2. **Pinned the manager implementation shape**: one Go binary
   (`shared-release`), daemon + CLI subcommands, systemd units, transient
   worker units over D-Bus — with a normative package and state layout
   (§Layer 2). Codex left the implementation shape open.
3. **Pinned the provider protocol normatively**: HTTP/1.1 over a Unix socket
   with versioned routes, exact request/response schemas, machine-readable
   error codes, idempotency and timeout rules, and the rule that
   `prepare-start` is *fast* — it grants leases against an already-published
   generation and never stages content inline (§Provider protocol). Codex
   showed illustrative JSON only and allowed a 20-minute prepare timeout,
   which would have let user starts block on downloads.
4. **Recorded the dual-target/patchstack mechanics**: a new `SERIES` dimension
   for the patchstack tooling, branch and directory naming, and the exact
   upstream bases (§Workstreams). The existing tooling models exactly one
   contiguous series per target ([verified] `patchstack/scripts/common.sh`
   `resolve_target`), so this is a real, small tooling change that codex did
   not identify.
5. **Fixed the activation variable to one well-known name**
   (`WINGS_CONTENT_PROVIDER`), not a per-provider configurable name. Two
   providers configuring the same variable name would make resolution
   ambiguous; a fixed name keeps resolution deterministic and greppable.
6. **Added the memory-charging design** for shared tmpfs generations — absent
   from codex v3 entirely. Kernel rule: "A memory area is charged to the
   cgroup which instantiated it and stays charged to the cgroup until the
   area is released"; for shared areas, charging is "in-deterministic;
   however, over time, the memory area is likely to end up in a cgroup which
   has enough memory allowance" ([kernel] cgroup-v2 "Memory Ownership").
   Consequences: game-slice `memory.min` does *not* protect shared generation
   pages; each generation gets a dedicated holder slice the populate job runs
   in; charges migrate toward re-faulting consumers over time; teardown must
   truncate tmpfs files before removing the holder slice or the memcg lingers
   as a dying cgroup (§Generation slices and charging).
7. **Added content classes.** Production runs *two* tmpfs services with
   opposite memory policy for good, measured reasons: the pak is
   zstd-incompressible (1.006×) so `soulmask-paks.slice` sets
   `MemoryZSwapMax=0` (cold pak bypasses zswap straight to disk swap), while
   the static Engine/binaries content is compressible and keeps zswap
   ([verified] `files/etc/systemd/system/soulmask-paks.slice`,
   `soulmask-static.slice`; [kernel] `memory.zswap.max` semantics). Codex v3
   flattened this into one generation with one policy. A generation now
   consists of one or more **classes**, each with its own tmpfs, holder slice,
   and memory policy; the profile assigns managed roots to classes.
8. **Specified the Wings touch-points against verified vanilla anchors** —
   the provider call site inside `onBeforeStart()` after `Sync()`
   (`server/power.go:171-173`), mounts composed in `Server.Mounts()`
   (`server/mounts.go:22`), the environment mount conversion that already
   carries `ReadOnly` end-to-end (`environment/docker/container.go:434`),
   labels via the environment configuration (`container.go:166-174`), lease
   release seams at `OnStateChange` → Offline (`server/server.go:317`),
   delete (`router/router_server.go:192` → `Destroy()`), and boot
   reconciliation (`cmd/root.go:237-264`). Codex cited files; this pins
   functions and behavior, including one correction: vanilla reaches
   `Running` only via the console done-matcher (`server/listeners.go:182`),
   never inside `Start()`.
9. **Documented the SFTP/backup/disk-quota behavior change** as a feature
   with UX consequences: backups and SFTP walk the server's host volume only
   ([verified] `server/backup.go:60` → `backup_local.go:68`;
   `sftp/handler.go:79`), so provider-mounted content disappears from both
   (backups shrink by ~2.4 GB per server, content stops counting against the
   disk quota, users no longer see `WS/Content` over SFTP). Today's in-volume
   host binds are visible to both — this is a real behavioral delta that
   needs release notes, not a footnote.
10. **Resolved the install/reinstall interplay concretely.** The install
    container receives the full server environment ([verified]
    `server/install.go:403` sets `Env: ip.Server.GetEnvironmentVariables()`),
    so the managed-egg install script gates on `WINGS_CONTENT_PROVIDER` and
    skips the SteamCMD content download for managed servers, creating only
    per-instance mutable paths. Reinstall stays safe by construction. Codex
    offered two vague options ("or the provider tolerates the ordinary
    install").
11. **Turned self-update into a loud failure instead of a policy hope.**
    With read-only provider mounts, an entrypoint `+app_update` write fails
    visibly at start; managed eggs additionally ship `AUTO_UPDATE=0`. The
    2026-07-21 `VALIDATE` egg variable stays relevant only for unmanaged
    servers.
12. **Added the 2026-07-21 stale-pak incident as a motivating case study**
    mapping each observed failure (cp-once staleness, `root:root` `Paks/`
    ownership silently defeating steamcmd validate, `DungeonEGLv50` version
    mismatch) to the design property that prevents it.
13. **Grounded every Soulmask number**: appid 3017300, single content depot
    3017301 (~1.94 GiB installed), pak ~1.79 GB shared byte-identically
    across both maps (SOULMASK.md §9), the six static roots (≈386 M), holder
    floor calibrations (150M/200M), real UUIDs and roles, the registration
    readiness line, host capacity (16 GB RAM, 8 cores, ~69 GiB swap, zswap
    zstd). Codex used placeholders.
14. **Classified `Steam` as per-instance mutable** (codex v3's own evidence:
    `Steam/config` and `Steam/logs` are live runtime writes) and added the
    other mutable entries the profile must carry (`steamapps`, `ksm-optin.so`,
    `.steam`, `.config`). The old static share bind-mounted `Steam` read-only
    from a shared copy — a live bug in the retired system, now impossible by
    classification.
15. **Defined lease lifecycle against Docker reality**: a stopped container
    still *references* its mount sources in its definition ([verified]
    containers are removed only at the next start's `OnBeforeStart`
    (`environment/docker/power.go:26`) or at delete (`Destroy`)), so
    generation GC requires both "no lease" and "no container (running or
    stopped) labeled with the generation".
16. **Added mount-ordering and collision rules** (sort by target depth,
    reject duplicate targets, nested-target ordering for class mounts like
    `WS/Content` + `WS/Content/Paks`) — Docker applies the mount list as
    given; nothing upstream guarantees a safe order for nested targets.
17. **Kept codex's RCON-local-only, cohort, single-generation, and
    trust-level decisions unchanged** — they were right — and kept its
    failure-policy table, extending it with new rows (generation holder slice
    missing, ENOSPC mid-populate, socket permission errors).
18. **Added an explicit open-questions list** (§Open questions) instead of
    leaving unknowns implicit inside prose: `WS/Config` classification audit,
    egg done-matcher change, retention count, KSM decision (M7 still open).

## Case study: the 2026-07-21 incident this design must make impossible

What happened (from the incident record, condensed):

- The static ramdisk populates once per path and then *reuses* the copy on
  every later start ([verified] `soulmask-static-ramdisk-setup.sh`: paths are
  `cp -a`'d only if not already present; same for the pak service). A tmpfs
  copy made from a stale volume stays stale until someone tears it down.
- The MAIN volume's `WS/Content/Paks` directory was owned `root:root` instead
  of `988:988`. SteamCMD (running as uid 988) could never replace the pak —
  every validate pass, including Panel Reinstall, reported overall "Success!"
  while the one write that mattered silently failed. The pak froze at the
  June 26 build.
- Result: clients on 1.0.14 saw a server registering as 1.0.13, repeated
  `Create Dungeon Failed: DungeonEGLv50`, and a day of misdirected debugging
  (including a wrong "Steam depot lag" theory).

Design properties that each map to one of those failures:

| Incident failure | Design property |
|---|---|
| cp-once tmpfs reuse serves stale content | Generations are immutable, named by build identity, and only ever *replaced*, never refreshed in place; a generation exists only as the published form of a complete, hash-verified persistent release |
| Silent partial steamcmd write (permission bug) | Release creation happens in a manager-owned transaction directory with normalized ownership; completion requires a manifest with per-file hashes and probes; a release without `COMPLETE` is never selectable |
| Content and per-instance state entangled in one volume | Release-owned roots are provider-mounted read-only; the volume holds only mutable per-instance state; a permission anomaly in either domain cannot silently affect the other |
| "Success!" with no version attestation | Every prepared start carries the generation identity as a Docker label and in the lease; `status`/`doctor` and the readiness check compare expected vs. served generation |

## Terminology

- **Release** — an immutable, validated content tree on persistent disk,
  identified by source build identity (e.g. `steam-3017300-24123343`).
- **Generation** — a release *published* for serving; on this host, one or
  more tmpfs mounts (one per class) populated from the release.
- **Class** — a subset of a release's managed roots sharing one memory
  policy (Soulmask: `pak` and `code`).
- **Lease** — one consumer container's right to a generation's mount set,
  granted at `prepare-start`, bound to the container at `commit-start`.
- **Cohort** — a group policy under which all members serve one generation.
- **Managed root** — a release-owned path mounted read-only into consumers.
- **Mutable root** — a per-instance path that stays in the server volume.

## Goals and invariants

### General product goals

Unchanged from codex v3: reuse one immutable release across any number of
read-only containers; support disk/tmpfs/future publication backends and
SteamCMD/archive/custom source drivers behind stable interfaces; per-server
channel/pin selection bounded by group policy; ordinary Panel power controls
keep working; narrow, node-authorized privileged interface; failures visible
and actionable; the Wings patches useful beyond Soulmask (mod packs, licensed
assets, snapshot-backed data sets, network-storage gates, multi-process
clusters).

### Soulmask invariants

1. MAIN and CLIENT never consume a partially installed release.
2. Their normal content backend is tmpfs (`tmpfs-required`), per class.
3. MAIN and CLIENT run one cohort generation unless an operator explicitly
   enables a tested mixed-version mode.
4. All release mounts are read-only to game containers.
5. `WS/Saved/**` is per-instance persistent storage outside the provider's
   source and managed mount roots.
6. A release becomes selectable only after it exists completely on persistent
   disk and passes validation.
7. A host reboot reconstructs tmpfs from the release store; no tmpfs content
   is ever copied back as authority.
8. A user can start, stop, or restart through the Panel at any time. Wings
   admits the start, waits per dependency policy, or returns a precise
   conflict; it never silently bypasses content policy.
9. A background update cannot monopolize disk, CPU, or memory needed by the
   running game (enforced by the manager slice hierarchy, not by hope).
10. A rollout stop is an expected Wings lifecycle action; a voluntary game
    exit during maintenance is not misclassified as a crash.
11. Generation memory charges live in holder slices with explicit policy;
    no game slice's floor is silently spent protecting shared content, and
    no shared content is silently unprotected.

## Architecture comparison

Unchanged verdicts from codex v3, retained for the record:

| Architecture | Durable update | tmpfs isolation | User restarts safe | General value | Verdict |
|---|---:|---:|---:|---:|---|
| Current writable tmpfs populated from an instance | No | Yes | No | Low | Reject (and see case study) |
| One mutable shared disk install | Non-atomic | No | Version races | Medium | Reject for Soulmask |
| Immutable disk releases served directly | Yes | No | Yes | High | Useful backend, insufficient Soulmask default |
| **Immutable disk releases + per-class tmpfs generations** | Yes | Yes | Yes | High | **Recommended** |
| Content baked into OCI images | Yes | Layer sharing | Yes | Medium | Viable elsewhere, not Steam-first |

| Control integration | Every Panel start covered | Privilege surface | Verdict |
|---|---:|---|---|
| Wings performs mounts/updates itself | Yes | Source code + mount privilege in Wings | Reject |
| Wings starts an egg-named systemd unit | Yes | Arbitrary-unit risk, weak result contract (this is patch 0011 — retired at migration) | Reject as contract |
| **Node-configured Unix-socket start provider** | Yes | Narrow validated protocol | **Recommended** |
| External manager only calls Wings/Panel API | No — direct user starts bypass it | Broad token | Orchestration only |
| Manual host scripts | No | Operator/root | Emergency fallback |

## Workstreams and ordering

Per decision 3, the program is three workstreams executed in order, with
production untouched until the migration window:

```text
Workstream A  Wings v2 patch program (from vanilla, dual-target)
              series "lifecycle" (this document, Layer 1)
              series "resources" (companion cgroups document)
              gate: unit + integration + privileged systemd e2e + golden
              stock-compatibility diff

Workstream B  shared-release manager v1 (Go daemon + CLI)
              targets v2-patched Wings only
              gate: kill-at-every-phase store tests, publish/verify e2e,
              provider conformance suite against Workstream A

Workstream C  Soulmask profile + managed egg variant + migration
              gate: disposable-server rehearsal, then the §Migration runbook
```

### Patchstack tooling extension (normative)

The existing tooling models one contiguous series per target
(`patchstack/patches/<target>-<ref>/`, branch `cgroup/<ref>`). Extend
`patchstack/scripts/common.sh` with a series dimension:

```text
resolve_target <series> <target>
  PATCH_DIR = patchstack/patches/<series>/<target>-<ref>/
  BRANCH    = <series>/<ref>
  series "cgroup" maps to the legacy layout patches/<target>-<ref>/ unchanged
```

- New series names: `lifecycle` and `resources`.
- Bases: pterodactyl `v1.13.1` (`e771816d`), pelican `main @ 70f3344`
  (v1.0.0-beta26+1) — re-export against newer pelican main with the existing
  `export-patches.sh` flow. Go toolchains per `patchstack/stack.conf`
  (golang:1.24 pterodactyl, golang:1.25 pelican).
- One production image is built with *both* v2 series applied
  (`lifecycle/<ref>` rebased onto `resources/<ref>` or vice versa at build
  time; keep the series independent in history, combined only in the build
  branch `v2/<ref>`). The legacy `cgroup/<ref>` branch keeps producing the
  production image until migration.
- Known pelican port delta from the legacy series: `DefaultMapping` became a
  pointer; expect equivalent small deltas, fixed per-target at export time.

## Layer 1 — Wings v2 lifecycle series

Four patches, each independently valuable and reviewable. "L" numbers are the
series order; upstream PRs follow the same boundaries.

### L1 — external start-preparation/admission provider

**Seam.** `Server.onBeforeStart()` ([verified] `server/power.go:171`) already
runs on every real start, immediately after `s.Sync()` refreshes Panel
configuration and before the environment recreates the container
(`environment/docker/power.go:26`: remove + `Create()`). Insert the provider
call after the existing suspension/disk checks:

```text
onBeforeStart():
  s.Sync()                                  # existing, power.go:173
  suspension + SyncWithEnvironment + disk   # existing
  s.prepareContentProvider(ctx)             # NEW  (server/content_provider.go)
  s.UpdateConfigurationFiles()              # existing
```

It must not run in the generic boot-time `CreateEnvironment()` pass for
offline servers — `onBeforeStart` already has exactly that property (boot
restoration goes through `HandlePowerAction(Start)`, [verified]
`cmd/root.go:247`).

**Resolution.** After sync, read `WINGS_CONTENT_PROVIDER` from the resolved
server environment. Precedence:

```text
node per-server override (docker.lifecycle_providers.overrides[uuid])
  > node egg allow-list check
  > validated server/egg selector variables
  > absent selector = none (stock path, no socket operation ever)
```

An unknown or unauthorized non-empty selector is an actionable start error for
that server only. Wings startup never connects to configured sockets just to
probe them.

**Node configuration (normative):**

```yaml
docker:
  lifecycle_providers:
    shared-release:
      socket: /run/wings-providers/shared-release.sock
      required_when_selected: true          # false = log-and-continue-stock
      prepare_timeout: 15s                  # prepare must be fast; see protocol
      call_timeout: 5s                      # commit/abort/release/reconcile
      allowed_eggs: []                      # empty = any egg may select
      selector_variables:                   # only these are forwarded, ever
        profile: WINGS_CONTENT_PROFILE
        group: WINGS_CONTENT_GROUP
        channel: WINGS_CONTENT_CHANNEL
        release: WINGS_CONTENT_RELEASE
      allowed_source_roots:
        - /run/game-releases
        - /var/lib/game-releases
      allowed_target_root: /home/container
      denied_targets:
        - /home/container/WS/Saved
      overrides: {}                         # uuid -> {provider|none, selectors...}
```

There is no implicit default provider. A configured block registers the ID and
socket; only a server's explicit selector (or a node override) enrols it.
Wings forwards *only* the configured selector variables — never the full
environment, which contains `RCON_PASSWORD` and other secrets ([verified] the
egg carries secrets in ordinary variables; `server/server.go:151`
`GetEnvironmentVariables()` returns everything).

**Validation rules (Wings side, all enforced before Docker sees anything):**

- provider mounts must be `read_only: true`;
- every source must resolve — via `openat2(2)` with `RESOLVE_BENEATH`
  (`golang.org/x/sys/unix.Openat2`), not lexical prefixing — under one of
  `allowed_source_roots`;
- every target must be under `allowed_target_root`, must not equal or be an
  ancestor of any `denied_targets` entry, and duplicate targets are rejected;
- accepted mounts are sorted by target path depth (shallow first) before being
  appended, so nested class mounts (`WS/Content`, then `WS/Content/Paks`)
  bind in a defined order — Docker applies the list as given;
- a provider cannot add capabilities, devices, environment, Docker socket
  access, or host namespaces — the response schema simply has no fields for
  them, and unknown fields are rejected;
- required-provider failure aborts the start before container creation with
  the provider's error text attached.

**Mount injection.** `Server.Mounts()` ([verified] `server/mounts.go:22`)
composes default volume + passwd + custom mounts; append the validated
provider mounts there. The existing conversion already carries `ReadOnly`
into Docker ([verified] `environment/docker/container.go:434`
`convertMounts`). Labels: merge `wings.content.provider`,
`wings.content.lease`, `wings.content.generation` into the environment labels
consumed at create ([verified] `container.go:166-174`).

For a containerized Wings (production shape: compose service, [verified]
SETUP.md §mounts — docker.sock, `/etc/pterodactyl`, `/var/lib/pterodactyl`,
`/run/dbus/system_bus_socket`) two host paths must be added to the Wings
container for L1 to function:

```yaml
# wings compose service additions
- /run/wings-providers:/run/wings-providers          # provider sockets
- /run/game-releases:/run/game-releases:ro,rslave    # published generations
- /var/lib/game-releases:/var/lib/game-releases:ro,rslave   # release store
```

`rslave` matters: generations are mounted by the manager *after* the Wings
container started; Docker's default bind propagation is `rprivate`, under
which later host mounts never appear inside Wings and source validation would
fail against an empty directory ([docker] bind-propagation documentation). If
an operator will not expose the roots this way, node config must set
`trust_provider_paths: true` and the docs must say plainly that containment
then rests on the provider, not on Wings.

**Lifecycle callbacks.** Wings calls, per start attempt:

- `prepare-start` — in `onBeforeStart()`; grants a lease or fails fast;
- `commit-start` — after `Environment.Start()` returns success, carrying the
  container ID;
- `abort-start` — if create/start fails after a successful prepare;
- `release` — when the server transitions to Offline
  ([verified] `server/server.go:317` `OnStateChange`; crash-restart decisions
  live in the same place, so ordering is well-defined: release fires before
  any crash-triggered new start's prepare), and again defensively on delete
  (`router/router_server.go:192` → `Environment.Destroy()`);
- `reconcile` — on Wings boot after the state restoration pass
  (`cmd/root.go:237-264`), reporting all known (server, lease, container)
  triples so the manager can drop stale leases.

A stopped-but-not-deleted container still *references* its mount sources in
its Docker definition (containers are removed only at the next start's
`OnBeforeStart` or at delete — [verified] `environment/docker/power.go:26`,
`container.go:271`). Lease release therefore does not by itself make a
generation removable; see GC rules in Layer 2.

### Provider protocol v1 (normative)

Transport: HTTP/1.1 over a Unix stream socket. Rationale: both ends are Go;
stdlib client/server, per-call timeouts, status codes, and
`curl --unix-socket` debuggability for free. The socket directory
`/run/wings-providers/` is root:root 0755; sockets are 0600 (Wings runs as
root in its container and connects as uid 0; the manager verifies
`SO_PEERCRED` uid 0). Protocol version is the URL prefix; incompatible
requests get `409 incompatible-protocol`.

```text
POST /v1/prepare-start      -> 200 PrepareResponse | 4xx/5xx Error
POST /v1/commit-start       -> 204 | Error
POST /v1/abort-start        -> 204 | Error          (idempotent by lease_id)
POST /v1/release            -> 204 | Error          (idempotent by lease_id)
POST /v1/reconcile          -> 200 ReconcileResponse
GET  /v1/healthz            -> 200 {"protocol":1,"provider":"shared-release"}
```

PrepareRequest:

```json
{
  "protocol": 1,
  "request_id": "wings-generated-uuid",
  "server_uuid": "b87c0a5b-2387-4a1c-8863-ff23e6800a1d",
  "volume_root": "/var/lib/pterodactyl/volumes/b87c0a5b-...",
  "selectors": {
    "profile": "soulmask",
    "group": "soulmask-prod",
    "channel": "stable",
    "release": "latest"
  }
}
```

PrepareResponse:

```json
{
  "allow": true,
  "lease_id": "lease-01J...",
  "generation": "steam-3017300-24123343",
  "mounts": [
    {"source": "/run/game-releases/soulmask/steam-3017300-24123343/code/Engine",
     "target": "/home/container/Engine", "read_only": true},
    {"source": "/run/game-releases/soulmask/steam-3017300-24123343/pak/WS/Content/Paks",
     "target": "/home/container/WS/Content/Paks", "read_only": true}
  ],
  "labels": {
    "wings.content.provider": "shared-release",
    "wings.content.lease": "lease-01J...",
    "wings.content.generation": "steam-3017300-24123343"
  }
}
```

Error body (any non-2xx):

```json
{"code": "generation-not-published", "retryable": false,
 "message": "group soulmask-prod has no published generation; run: shared-release activate soulmask-prod --release latest",
 "operation": "op-20260723-104512-4821"}
```

Error codes (closed set, v1): `unknown-profile`, `unknown-group`,
`unauthorized-selector`, `generation-not-published`, `generation-degraded`,
`cohort-locked` (rollout in progress; retryable), `lease-conflict`,
`capacity`, `incompatible-protocol`, `internal`.

Hard rule: **`prepare-start` performs no staging, no population, no
downloads.** It resolves the selector against already-published state, writes
a durable lease record, and answers — target under 1 s, budget 15 s. Anything
not published fails fast with the CLI command to fix it in the message. (A
later protocol version may add an opt-in `prepare_may_publish` for
non-interactive fleets; it is out of v1.)

`commit-start` binds `{lease_id, container_id}`. `release` carries
`{lease_id, reason: stopped|deleted|aborted|superseded}`. `reconcile` sends
Wings' full current view `[{server_uuid, lease_id?, container_id?,
generation?}]`; the response lists leases the manager dropped or kept, and
any generation Wings references that the manager no longer publishes
(surfaced as a server health warning, not an automatic stop).

### L2 — readiness events (generic)

A small patch making "this server is genuinely ready" a first-class, reusable
event, consumed by L3 (dependencies), by the resources series (startup→steady
phase), and by rollout verification.

- Default readiness = the egg's existing `startup.done` match — the exact
  mechanism that already flips state to Running ([verified]
  `server/listeners.go:149,182`; matcher type
  `remote.ProcessConfiguration.Startup.Done`, `remote/types.go:145`).
- Optional distinct matcher (server/egg variable `WINGS_READY_MATCH`, same
  literal-or-`regex:` syntax the legacy `WINGS_CG_STEADY_MATCH` proved out)
  for games where "Panel shows Running" precedes "safe to depend on".
  Soulmask: Running fires on `Create Dungeon Successed` (egg done matcher,
  [verified] egg line 19) while dependency-grade readiness is the
  registration heartbeat `registe server soulmask session succeed`
  ([verified] egg `WINGS_CG_STEADY_MATCH` default; the misspelling is the
  genuine log line).
- Event: `Ready(kind, startAttemptID, timestamp)`, one-shot per start
  attempt, cleared on stop/crash/new start. A timeout is *not* a Ready event
  — the legacy stack already embodies this rule (child starts fire only on
  match, never on the grace backstop, [verified]
  `server/slice_phase.go:162,172-174`) and v2 keeps it as a hard contract.

### L3 — startup dependencies

Server-side declarations, node-bounded:

```text
WINGS_START_AFTER=<uuid>            # prerequisite server on this node
WINGS_DEPENDENCY_POLICY=wait        # wait | reject | start-prerequisite
WINGS_DEPENDENCY_TIMEOUT=10m
WINGS_AUTOSTART_DEPENDENTS=1        # on the prerequisite
```

Behavior (all start sources funnel through `HandlePowerAction`, so coverage
is structural):

1. Dependent start while prerequisite Ready → proceeds immediately.
2. Prerequisite Starting → dependent queues until Ready or bounded timeout.
   Queueing happens *before* the dependent's power lock is taken
   ([verified] lock semantics `system/locker.go:34,47` — REST passes
   `waitSeconds` → `TryAcquire`; websocket uses immediate `Acquire`), so a
   queued dependent never deadlocks its own lock, and the REST 202-async
   contract ([verified] `router/router_server.go:53` returns Accepted and
   runs the action in a goroutine) is preserved.
3. Prerequisite Offline → per policy: reject with `dependency offline`, or
   start it (`start-prerequisite`), or wait.
4. Prerequisite Ready → starts queued/autostart dependents via their own
   `HandlePowerAction(Start)` (the legacy child-start proved this pattern,
   [verified] `server/slice_children.go:83-94`).
5. Cycles detected and reported, never deadlocking boot — port the bounded
   ancestor walk (`internal/cgroups/boot.go:41,91`, 64-hop bound) and the
   boot-deferral behavior (`cmd/root.go:224-285`): after reboot, a dependent
   whose prerequisite is also restarting defers to the prerequisite's Ready.
6. When the provider is active and the group policy is `cohort`, the
   dependent's prepared generation must equal the prerequisite's committed
   generation; mismatch is a precise start error (`cohort-locked` window or
   operator error).
7. Readiness clears on prerequisite stop/crash/new start; a grace timeout
   never counts (L2 contract).

### L4 — maintenance intent and observable power operations (optional)

Two small, separable improvements:

- Power operations get an operation ID and a queryable status, so a rollout
  tool does not have to poll `GET /api/servers/:server` and guess (the REST
  power endpoint is fire-and-forget 202 today, [verified]
  `router_server.go:53-…`).
- A bounded **maintenance lease**: `POST /api/servers/:server/maintenance`
  marks the next exit expected for N minutes. Vanilla crash detection
  restarts on any Starting/Running→Offline transition unless the state
  machine passed through Stopping, and exit code 0 counts as a crash by
  default ([verified] `server/crash.go:47` +
  `config.system.crash_detection.detect_clean_exit_as_crash` default `true`,
  `config/config.go:258-270`; trigger condition `server/server.go:344-346`).
  The lease suppresses exactly that path, making RCON `SaveAndExit` safe.

v1 rollouts do not require L4: the safe no-patch flow is RCON save/flush,
then a Wings-initiated stop (which routes Stopping → no crash restart).

### What goes upstream, what does not

Upstream PR ladder (Pelican first, cross-submit Pterodactyl): L1 provider
admission (without dynamic mounts if reviewers want it smaller), L1b dynamic
read-only mounts + leases, L2 readiness, L3 dependencies, L4 power
operations/maintenance. Each PR ships with non-Soulmask use cases (mod packs,
licensed per-customer assets, snapshot data sets, NFS-availability gates,
multi-process clusters).

Never upstream: app IDs, Soulmask paths, tmpfs copy logic, systemd unit
triggering from egg data (the 0011 pattern), cgroup-named lifecycle features.

## Layer 2 — shared-release manager

One Go module, one binary `shared-release`, daemon + CLI.

### Process and package layout (normative)

```text
cmd/shared-release/main.go        # subcommands: daemon, stage, status,
                                  # activate, rollback, gc, doctor, operation,
                                  # rollout (post-v1)
internal/config                   # /etc/game-releases/config.yaml
internal/profile                  # profiles.d loader + validation
internal/store                    # transactions, releases, manifests, GC
internal/source/steam             # SteamCMD driver (containerized)
internal/publish                  # tmpfs generations, holder slices (D-Bus)
internal/lease                    # durable leases, docker reconciliation
internal/providerapi              # /v1 HTTP-over-UDS server (Wings-facing)
internal/adminapi                 # CLI-facing UDS (root-only)
internal/journal                  # operation records + structured events
```

Two sockets: `/run/wings-providers/shared-release.sock` (Wings-facing,
protocol v1) and `/run/game-releases/admin.sock` (CLI-facing, 0600 root).
The CLI talks only to the daemon; if the daemon is down, every command except
`doctor --offline` refuses with the systemctl hint. State is owned by exactly
one process — no file-lock choreography.

### Filesystem layout (normative)

```text
/etc/game-releases/config.yaml
/etc/game-releases/profiles.d/soulmask.yaml
/var/lib/game-releases/
├── transactions/<txn-id>/                  # in-progress source work
├── profiles/soulmask/releases/<release-id>/
│   ├── root/                               # the content tree
│   ├── release.json                        # manifest: files, sizes, hashes,
│   │                                       # classes, source identity, probes
│   └── COMPLETE                            # written last, fsync'd
├── profiles/soulmask/channels/stable       # symlink -> ../releases/<id>
├── state/
│   ├── groups/soulmask-prod.json           # members, policy, assigned generation
│   ├── leases/<lease-id>.json
│   └── operations/<op-id>.json             # + events.jsonl per operation
/run/game-releases/
├── admin.sock
└── soulmask/<release-id>/
    ├── code/                               # tmpfs mount (class "code")
    └── pak/                                # tmpfs mount (class "pak")
```

Store ownership: a dedicated `game-releases` system user owns
`/var/lib/game-releases`; directories 0755, files 0644 — world-readable by
design, because consumers run as arbitrary container uids (988 here) and game
content carries no secrets. A future profile with secret-bearing content gets
a group-mapping knob; not v1.

Release IDs: `steam-<appid>-<buildid>` (collision with a re-released same
buildid appends `-r2`). Operation IDs: `op-<yyyymmdd>-<hhmmss>-<4 random>`.

### Manager configuration (normative)

```yaml
# /etc/game-releases/config.yaml
state_dir: /var/lib/game-releases
run_dir: /run/game-releases
provider_socket: /run/wings-providers/shared-release.sock
retention:
  min_releases: 3          # never below active+previous; leased always kept
slices:
  parent: game-releases.slice
  control: game-releases-control.slice
  stage: game-releases-stage.slice
  publish: game-releases-publish.slice
  generation_prefix: game-releases-gen-      # + short release hash + .slice
docker:
  socket: /var/run/docker.sock               # label reconciliation only
steam:
  runner_image: ghcr.io/ptero-eggs/steamcmd:debian
  run_uid: 988
  run_gid: 988
```

### Persistent release store

A transaction becomes a release only after: source success → path
classification against the profile (an unclassified new path **blocks
promotion** and names the path) → per-file SHA-256 manifest → size and free-
space checks → required probes (binary exists and is executable, pak present
and within expected size band) → ownership/mode normalization → durable
`release.json` → fsync'd `COMPLETE`. Promotion flips a channel symlink
atomically (write + `rename(2)`); nothing ever updates a release in place.

Kill-at-every-phase is a v1 test gate: `kill -9` the daemon at each phase
boundary; on restart, reconciliation must either resume or quarantine the
transaction, and `latest` must never resolve to anything incomplete.

### SteamCMD source driver

- Runs steamcmd **containerized**, matching the runtime uid:
  `docker run --rm -u 988:988 -v <txn-dir>:/home/container -w /home/container
  ghcr.io/ptero-eggs/steamcmd:debian` — the same image the egg uses, which
  proved out during the incident (a root-invoked steamcmd hit EACCES quirks;
  the correctly-scoped container worked).
- Update detection: `steamcmd +app_info_print 3017300` — branch/buildid
  straight from Steam (verified live 2026-07-21: single `public` branch,
  buildid `24123343`); recorded into the operation journal.
- Acquisition: `+force_install_dir /home/container +login anonymous
  +app_update 3017300 validate +quit`; `validate` is unconditional at release
  creation. Build identity read from
  `steamapps/appmanifest_3017300.acf` (buildid + depot manifest IDs; single
  content depot 3017301, ~1.94 GiB installed).
- The stage job runs as a systemd transient unit in
  `game-releases-stage.slice` (lowest CPU/IO weight, hard `io.max` device
  ceilings, `MemoryHigh` bound, no memory protection) — the worker launch
  uses the same D-Bus `StartTransientUnit` machinery the resources series
  uses, so accounting is correct from the first byte.
- steamcmd has no bandwidth flag; the cgroup `io.max` and optional
  `CPUQuota` on the stage slice are the real limiters.

### Generation slices and charging (new section)

Kernel facts this design is built on ([kernel] cgroup-v2 "Memory Ownership"):

> "A memory area is charged to the cgroup which instantiated it and stays
> charged to the cgroup until the area is released."

> "A memory area may be used by processes belonging to different cgroups. To
> which cgroup the area will be charged is in-deterministic; however, over
> time, the memory area is likely to end up in a cgroup which has enough
> memory allowance to avoid high reclaim pressure."

Consequences, made explicit:

1. tmpfs generation pages are charged to whichever cgroup first faults them —
   i.e. the populate job. Therefore **populate runs inside the generation's
   holder slice** (`game-releases-gen-<hash>.slice`, one per generation,
   below `game-releases.slice`), created as a systemd transient slice before
   population and alive for the generation's whole published life.
2. Game-slice `memory.min` does **not** protect shared generation pages; the
   holder slice carries the class policy instead. Per-class holder-slice
   properties come from the profile (Soulmask values below).
3. If a generation page is reclaimed (to zswap/swap) and later re-faulted by
   a game, the re-fault charges the *game's* scope — over time hot shared
   pages migrate their charge toward the consumers that actually touch them.
   This is fine and expected; the holder floor exists to keep the populate-
   time charge of the hot set resident, and per-game floors then cover their
   own re-faulted pages. Do not "fix" charge migration; document it in
   `doctor` output instead (holder slice `memory.current` shrinking over
   uptime is normal).
4. Protection is evaluated relative to the reclaim root: reclaim triggered by
   a cgroup's own `memory.high`/`max` is not fended off by its own
   `memory.min` ([kernel] mm/vmscan.c `mem_cgroup_calculate_protection` —
   the reclaim root's own protection is skipped). So a holder slice must not
   carry a `MemoryHigh` below its resident class size, or it will thrash its
   own content.
5. Teardown order is fixed: unmount consumer binds → remove tmpfs files /
   unmount the tmpfs → stop the holder slice. Removing the cgroup first
   leaves it as a dying memcg pinned by the still-charged tmpfs pages
   (observable as `nr_dying_descendants` in `cgroup.stat`); `doctor` checks
   that count.

Per-class zswap policy uses the per-cgroup knobs ([kernel] cgroup-v2:
`memory.zswap.max` — "zswap usage hard limit... refuses further stores";
`memory.zswap.writeback` — 0 "disables all swapping attempts to swapping
devices... including zswap writebacks and swapping due to zswap store
failures"; both available on this kernel line, and as systemd properties
`MemoryZSwapMax=`/`MemoryZSwapWriteback=` per systemd.resource-control(5) as
shipped on Debian 13):

| Class | Content | Policy | Rationale ([measured]) |
|---|---|---|---|
| `pak` | `WS/Content/Paks` (~1.79 GB) | `MemoryZSwapMax=0`, `MemoryZSwapWriteback=yes`, `MemoryMin=150M` | pak is zstd-incompressible (1.006×); zswap stores would waste CPU+RAM; cold pak pages go straight to disk swap; 150M floor carried over from the calibrated `soulmask-paks.slice` |
| `code` | Engine, WS/Binaries, linux64, Steam client libs (≈386 M) | zswap default (on), `MemoryMin=200M` | executable/code pages compress well; floor carried from `soulmask-static.slice` |

tmpfs mount sizing: `ceil(class manifest bytes × 1.15)` rounded to 64 M —
Soulmask: pak 2 G, code 512 M (the legacy 3 G / 1 G mounts were generous
fixed sizes; per-generation sizing replaces them). tmpfs size is a hard cap;
ENOSPC during populate quarantines the generation, never publishes it.

Capacity preflight before allocating any generation:
`MemAvailable + reclaimable-with-headroom > Σ class sizes + 1 G guard`, and
dual-generation publication is refused whenever the sum of both generations
plus both instances' floors exceeds the host budget — on this 16 GB host with
two ~6 GiB hot sets and an 8 G tier floor, dual-generation is effectively
always refused, which is the intended single-generation cohort behavior.

### Publication and verification

Populate into a hidden directory (`<release-id>.tmp/`) inside the class
tmpfs, `cp --archive` from the release store, verify **every file** against
the manifest hashes, `chmod -R a-w`, rename to the final path, then mark the
generation published in group state. Consumers only ever see fully-verified
content. Never update a visible generation in place; never copy tmpfs back
to disk.

Reboot recovery: `/run` is volatile; on boot the daemon republishes the
assigned generation of every group that has (or is configured for) active
consumers, from the release store, before Wings' boot restoration can
`prepare-start` — unit ordering `Before=docker.service` on the manager's
publish-at-boot path, same pattern the legacy ramdisk units used. A
`prepare-start` racing an unfinished republish gets `generation-degraded`
(retryable) rather than a stale mount.

### Leases and GC

Lease records are durable JSON; states `granted → committed → released`.
Reconciliation sources, in order of authority: Wings `reconcile` reports,
Docker labels (`docker ps -a` filtered on `wings.content.lease` — including
**stopped** containers, whose definitions still reference generation paths),
lease files. A generation is removable only when: not the assigned generation
of any group, no lease in `granted`/`committed`, and no container (any state)
labeled with it. Persistent releases are removable when not referenced by any
channel, generation, or lease, respecting `retention.min_releases` (default
3) — with ~2.4 GB per release on a 954 GiB volume, retention is cheap;
generations in RAM are the scarce resource.

### Operation journal

Every state-changing command/job writes `state/operations/<op-id>.json`
(actor, command, profile/group/servers, timestamps, phase transitions, source
identity, bytes/files/hashes, capacity preflight, slice identity, lease and
label changes, result, whether state changed) plus an append-only
`events.jsonl`; the daemon also emits structured logs to journald. No
credential or full environment ever appears in either. `status --json` and
`operation show <id> --json` expose the same state machine-readably;
`--dry-run` prints intended source, paths, capacity, affected leases, and
transitions without mutating anything.

### Manager v1 scope

Includes: one node; profile engine + Soulmask profile; SteamCMD driver;
transactions → validated immutable releases; single active generation per
group with per-class tmpfs + holder slices; provider protocol v1 serving any
number of consumers; explicit offline `activate`/`rollback` (previous
release); crash-safe state + reboot republish; journal; CLI
`stage|status|activate|rollback|gc|doctor|operation`.

Excludes (deliberately): dual simultaneous generations and rolling cutover;
automatic Panel/Wings stop/start orchestration (`rollout` command lands
post-v1; until then the runbook drives stops/starts, with L3 guaranteeing
MAIN-before-CLIENT ordering on the start side); RCON scheduling; network API
or multi-node; user-defined source executables; automatic save restoration.

Versioned from day one so later features never discard deployed state: the
release manifest schema, group/lease state schema, journal schema, provider
protocol, Docker label names.

### Release selectors and group policies

Unchanged from codex v3 (definitions of `latest`, `previous`,
`pinned:<id>`, `cohort`, `candidate`; policies `independent`/`cohort`/
`rolling` with the cohort rule that an ordinary restart of one member while a
sibling runs G resolves to G). One addition: `latest` is evaluated against
*complete releases on the selected channel*, never against upstream builds —
restated because the incident showed how easily "latest" drifts in meaning.

### Manager slice hierarchy

```text
-.slice
├─wings.slice                         game tier (resources series' domain)
│ ├─wings-mgmt.slice
│ └─wings-<uuid>.slice …
└─game-releases.slice                 aggregate ceiling: MemoryHigh/Max,
  │                                   CPUQuota, io.max device ceilings
  ├─game-releases-control.slice       daemon; modest floor so status/cancel/
  │                                   lease service survive stage pressure
  ├─game-releases-stage.slice         downloads/unpack/hash: lowest weights,
  │                                   hard io.max, MemoryHigh, no protection
  ├─game-releases-publish.slice       populate/verify jobs during cutover
  └─game-releases-gen-<hash>.slice    one per published generation: holds the
                                      tmpfs charges + per-class policy
```

Weights settle sibling contention only and are work-conserving; the actual
ceilings are `CPUQuota`, `MemoryHigh`/`Max`, and `io.max` on the parent
([kernel] cgroup-v2 weight/limit semantics; the companion document's
semantics sections carry the worked details). The parent aggregate is the
load-bearing part: N individually-limited workers without it multiply into an
unbounded total. This hierarchy is plain host systemd state and works
regardless of the Wings resources series.

## Layer 3 — Soulmask application profile

```yaml
# /etc/game-releases/profiles.d/soulmask.yaml
profile: soulmask
source:
  driver: steam
  app_id: 3017300              # single content depot 3017301
  extra_apps: [1007]           # Steamworks redistributable, as the egg installs
  login: anonymous
classes:
  pak:
    tmpfs_size_policy: manifest*1.15
    slice: {memory_min: 150M, zswap_max: 0, zswap_writeback: true}
  code:
    tmpfs_size_policy: manifest*1.15
    slice: {memory_min: 200M}
managed_roots:                  # release-owned, provider-mounted RO
  - {path: Engine,                class: code}
  - {path: WS/Binaries,           class: code}
  - {path: linux64,               class: code}
  - {path: steamclient.so,        class: code}
  - {path: libsteamwebrtc.so,     class: code}
  - {path: WS/Content,            class: code, except: [Paks]}
  - {path: WS/Content/Paks,       class: pak}
  - {path: WS/Config,             class: code}   # [open] pending audit, below
mutable_roots:                  # stay in the per-instance volume
  - WS/Saved                    # world.db, GameXishu.json — the state rule
  - Steam                       # Steam/config + Steam/logs are live runtime
                                # writes (proven in the old static share)
  - .steam
  - .config
  - steamapps                   # legacy install metadata; unused when managed
  - ksm-optin.so                # per-instance KSM shim (LD_PRELOAD)
probes:
  - {file: WS/Binaries/Linux/WSServer-Linux-Shipping, executable: true}
  - {file: WS/Content/Paks/WS-LinuxServer.pak, min_size: 1G}
readiness:
  match: "registe server soulmask session succeed"   # ~2-min heartbeat line
notes:
  panel_running_match: "Create Dungeon Successed"     # egg startup.done
```

The definitive managed/mutable split is generated from a clean Steam install
plus a runtime write audit (run a disposable instance against a candidate
generation with everything mounted RO except declared mutable roots; every
EROFS denial in the game log is either a missing mutable classification or a
game bug to note). `WS/Config` is release-owned *pending* that audit
**[open]** — the old static share omitted it although the depot installs it.

### Cluster policy

```yaml
group: soulmask-prod
mode: cohort
backend: tmpfs-required
members:
  b87c0a5b-2387-4a1c-8863-ff23e6800a1d:   # MAIN — DLC_Level01_Main,
    role: main                            # holds established world/account.db
  6c418fe7-9be1-4971-87ec-529f6e909f89:   # CLIENT — Level01_Main
    role: client
    start_after: b87c0a5b-2387-4a1c-8863-ff23e6800a1d
```

Materialized from egg selectors plus provider state (auto-enrolment within
node-allow-listed groups) or pinned in node overrides for hardened setups.
Policy results: cohort generation assignment; MAIN Ready before CLIENT start
(L3); CLIENT stops before MAIN in rollouts; both containers have in-game
Steam self-update disabled (`AUTO_UPDATE=0` + RO mounts make violations
loud); ordinary restarts reuse the cohort generation.

Both maps ship the same `WS-LinuxServer.pak` (one depot, no DLC depot), so
one generation serves both instances byte-identically — the fact that made
the shared pak tmpfs viable in the first place.

### Managed egg variant

Changes relative to the current
`egg-soulmask-rcon-ksm-cgroups.json`:

- add admin-only variables: `WINGS_CONTENT_PROVIDER=shared-release`,
  `WINGS_CONTENT_PROFILE=soulmask`, `WINGS_CONTENT_GROUP=soulmask-prod`,
  `WINGS_CONTENT_CHANNEL=stable`, `WINGS_CONTENT_RELEASE=latest`,
  `WINGS_START_AFTER` (CLIENT only), `WINGS_READY_MATCH` (the registration
  line);
- `AUTO_UPDATE` default `0` for managed servers;
- install script: when `WINGS_CONTENT_PROVIDER` is non-empty in the install
  environment (which receives all server variables — [verified]
  `server/install.go:403`), skip the SteamCMD download entirely and only
  create mutable directories and the RCON/KSM helper files;
- the legacy `WINGS_CG_*` variables migrate per the resources series'
  profile mapping (companion document).

### RCON adapter

Unchanged from codex v3 in substance; grounded specifics: RCON stays
local-only — no published port; the adapter pattern is
`exec-soulmask-rcon.sh`: find the UUID-named container whose process tree
shows `WSServer-Linux-Shipping`, run the RCON client with
`docker run --network container:<cid>` so the source is 127.0.0.1, read
`RCON_PORT`/`RCON_PASSWORD` from the container's injected environment. v1
keeps RCON out of the daemon: a root-only helper with a fixed allow-list
(player count, broadcast, `SaveWorld 0`, `BackupDataBase world`). The
network-facing Steam driver never gets the Docker socket for namespace
joining; only this narrow helper does. `SaveAndExit` remains forbidden unless
an L4 maintenance lease is active (crash-detection evidence in L4).

## Rollout flow (post-v1 CLI; v1 = same steps as runbook)

1. Resolve target release H (stage it first if needed — staging runs while G
   serves).
2. Capacity + mode check: Soulmask requires cohort offline cutover; H's
   tmpfs is not allocated yet.
3. Player query + broadcast countdown via the RCON helper.
4. `SaveWorld 0` + `BackupDataBase world`; verify replies; record `world.db`
   and `GameXishu.json` hashes as tripwires.
5. Stop CLIENT via Wings, await Offline; stop MAIN, await Offline. (Wings
   stop routes through Stopping → no crash restart; never RCON `SaveAndExit`
   without an L4 lease.)
6. Under the group lock: verify all leases released and no labeled
   containers remain (stopped containers pin their old definition; they are
   recreated at next start, so labels—not mounts—are the check); tear down
   G's generation (binds → files → holder slice, in that order); populate
   and verify H per class; assign cohort H.
7. Release the group lock **before** starting anything (a `prepare-start`
   waiting on the lock during MAIN's start would deadlock the rollout).
8. Start MAIN via Wings; its prepare resolves H.
9. Await MAIN Ready (registration line). L3 starts CLIENT (or the CLI does,
   observing Ready).
10. Verify: identical generation labels on both containers, RO mounts,
    readiness, RCON reachable, no `DungeonEGLv50`-class errors; soak, then
    mark H verified.
11. On failure: stop the group, reconstruct G (rollback = same machinery,
    previous release). Binary rollback after H wrote saves requires the
    save-schema policy gate — automatic only before H accepted traffic.

Steam automation levels stay: detect → stage → schedule → rollout; Soulmask
default = auto-detect + auto-stage, manual/scheduled rollout.

## Failure and selection policy

| Condition | Manager behavior | Soulmask policy |
|---|---|---|
| Steam/source unavailable | Keep current release | Continue G; alert |
| Candidate staging fails / killed | Quarantine transaction | Continue G; alert |
| New path unclassified | Refuse promotion, name the path | Continue G |
| ENOSPC mid-populate | Quarantine generation, free it | Continue G |
| Background I/O pressure high | Stage slice throttled by io.max; optional pause | Protect live game |
| H cannot fit while G published | Refuse dual publication | Wait for cohort cutover |
| Reboot republish fails, no consumers up | Keep releases; block prepare with `generation-degraded` | Remain offline; explicit disk-degraded override only |
| Holder slice creation fails | Refuse publication (charging policy would be wrong) | Alert |
| Cohort member requests H while sibling runs G | Resolve G; report H pending | Ordinary restart uses G |
| Provider socket absent/denied on selected start | Wings: required → precise start error; optional → stock path + warning | `required_when_selected: true` |
| MAIN not Ready at CLIENT start | L3 queue/reject per policy | Wait; timeout is not readiness |
| RCON save fails during rollout | Abort before stopping anything | Operator override only |
| `SaveAndExit` without maintenance lease | Wings crash-restarts (by design, verified) | Forbidden workflow |
| Rollout crash after stops | Reconcile from durable state; start only attested G or H | Journal names the resume point |

## Security model

Trust levels unchanged from codex v3 (Panel variables untrusted → node
allow-lists; Wings validates provider responses; provider node-trusted for
declared roots only; publisher narrowly privileged; downloader network-facing
and unprivileged; orchestrator scoped). Additions: socket peer-cred check
(uid 0) on the provider socket; admin socket 0600 root; store world-readable
by declared policy (no secrets in game content — a profile-level flag exists
for futures that differ); unknown fields in provider responses rejected;
selector syntax constrained (`[a-z0-9-]{1,64}`; release selector additionally
`latest|previous|pinned:<release-id>`).

Never accepted from egg/server variables: absolute paths, unit names, shell,
mount flags, credentials, another server's volume path, writability.

## Migration of the two live servers

Current interim state (post-incident): `soulmask-static-ramdisk.service` is
stopped/torn down; the volumes serve from disk; MAIN's pak was re-fetched and
verified byte-identical to a clean install. That interim state persists until
this migration — do not re-enable the legacy services meanwhile unless
latency pain forces it (if so: tear down and re-run setup to repopulate
fresh, never reuse).

1. Build and gate Workstream A; build Workstream B against it — all against
   disposable fixture servers, production untouched.
2. Save and back up both `WS/Saved` trees; record `world.db` +
   `GameXishu.json` hashes.
3. `shared-release stage soulmask` → first real release; compare its
   manifest against the deployed volumes (expected: equality with the
   repaired MAIN content); finalize the managed/mutable audit (`WS/Config`
   decision **[open]**).
4. Rehearse on a disposable server: provider mounts, SFTP/backup visibility
   delta, install-script guard, readiness, KSM shim, RCON.
5. Maintenance window: stop CLIENT then MAIN; swap the Wings image/config to
   the v2 build (both series), update the egg to the managed variant
   (`AUTO_UPDATE=0`, provider selectors); retire
   `soulmask-pak-ramdisk.service`, `soulmask-static-ramdisk.service`, their
   toggle scripts, and the `allowed_ramdisk_units` config; `activate`
   soulmask-prod; start MAIN, verify Ready + generation label; start CLIENT
   (L3); verify cluster behavior.
6. Reboot test: generations reconstruct from the store; boot restoration
   starts both in order.
7. Rehearse a real update: stage H while G serves, roll out, roll back.
8. The old in-volume content stays untouched through soak (it is simply
   masked by mounts where mounted, and `steamapps` goes inert); after soak,
   optionally archive `Engine`/`WS/Content` etc. out of the volumes to
   reclaim ~2.4 GB each — with backups shrinking accordingly.

## Acceptance oracles

### Wings v2 lifecycle series

1. No provider configured → byte-identical Docker create requests and
   lifecycle events vs vanilla (golden-diff harness shared with the
   resources series); zero socket operations.
2. Provider registered, server unselected → stock path; provider downtime
   has no effect on that server.
3. Every real start source (Panel WS, REST, crash restart, boot restore,
   L3 child start) produces exactly one `prepare-start`; offline
   `CreateEnvironment()` produces none.
4. Required-provider failure → no container, actionable error, server
   Offline (not crash-looping).
5. Validation rejects: writable mounts, out-of-root sources, symlink escapes
   (test with a symlink inside an allowed root pointing outside),
   denied/duplicate/ancestor-of-denied targets, unknown response fields.
6. Create failure → `abort-start`; success → labels + `commit-start`;
   Offline → `release`; delete → `release(deleted)`; kill -9 Wings between
   any two → boot `reconcile` converges without deleting an in-use
   generation.
7. Backup/SFTP: provider-mounted content absent from both; disk usage
   excludes it; documented in release notes.
8. RO self-update: entrypoint `+app_update` against managed roots fails
   loudly at start with `AUTO_UPDATE=1` (and managed egg ships 0).
9. L2: Ready fires once per attempt on the configured line; never on
   timeout; cleared on stop/crash/new attempt.
10. L3: CLIENT never reaches container start before MAIN Ready +
    matching-generation check; cycles detected; reboot defers CLIENT to
    MAIN's restart; REST stays 202-async; websocket lock semantics
    unchanged.
11. L4 (if built): maintenance lease suppresses exactly one expected exit;
    `SaveAndExit` without lease still crash-restarts (regression-pinning the
    vanilla behavior).

### Manager

1. Kill staging at arbitrary points → G unaffected; no incomplete release
   ever `latest`; transaction quarantined with journal record.
2. Saturate stage I/O under a live game workload → `io.max` ceilings hold;
   game refault/latency regression stays within the calibrated bound
   (repeat of the incident's docker-build scenario as a test).
3. Publish H while G serves (on a big-RAM fixture) → G's files, mounts,
   hashes, tmpfs usage unchanged.
4. Single-generation mode: publication refuses while any lease or labeled
   container (running **or stopped**) references G.
5. Every managed root write/unlink/rename fails in consumers (EROFS);
   `WS/Saved` writes isolated and persistent; hashes of `world.db`
   unchanged by any manager operation.
6. Reboot → republish from store before consumer starts; prepare during
   republish gets `generation-degraded`, not stale content.
7. Holder-slice accounting: after populate, class `memory.current` ≈ class
   size in the holder slice; game slices unchanged; teardown leaves no
   dying-memcg growth (`cgroup.stat nr_dying_descendants` stable).
8. Charge migration: after reclaim pressure + game re-faults, holder
   `memory.current` may shrink — `doctor` explains rather than alarms.
9. Two concurrent stage jobs stay within the parent aggregate ceilings;
   control slice stays responsive (status/cancel under stage saturation).
10. Journal: every state-changing operation has a durable record with
    source identity, validation, assignment, leases, slices, result; no
    credential appears anywhere in state or logs.

### Soulmask rollout

1. Rollout stops CLIENT before MAIN, starts MAIN before CLIENT; both end on
   identical generation labels.
2. An ordinary MAIN restart while CLIENT runs G receives G even with H
   staged.
3. Explicit rollback reconstructs G from the store (never from hidden
   copies); save-schema gate enforced after H served traffic.
4. Docker image-build pressure during play no longer reproduces the pak
   refault regression at calibrated settings (the original motivating test,
   now automated).
5. No RCON port reachable externally; the namespace adapter still works.

### Gate

`tester-unified` with full run-uid identity, plus the privileged
systemd-in-Docker e2e harness (extend `test/e2e-systemd/` — it already
proves effective floors, reload survival, budget behavior, orphan GC) grown
with: provider conformance suite (a fake provider driving every validation
rule), mount-propagation cases (`rprivate` vs `rslave` into a containerized
Wings), tmpfs charging assertions, and kill/reboot recovery. The devcontainer
remains the cockpit, not the gate.

## Defaults

| Decision | Soulmask v1 |
|---|---|
| Persistent releases | ≥3 retained, immutable, hash-verified |
| Publication | Single generation, two classes (pak/code), holder slices |
| Consumers | RO leases, one cohort (MAIN+CLIENT) |
| Acquisition | Auto-detect + auto-stage in stage slice; manual/scheduled rollout |
| Activation/rollback | Explicit CLI, cohort confirmed offline |
| Wings integration | `WINGS_CONTENT_PROVIDER=shared-release`, required-when-selected |
| Start ordering | L3 `WINGS_START_AFTER`, Ready = registration line |
| RCON | Root-only local helper, fixed allow-list, no published port |
| External API control | None in v1; rollout CLI post-v1 (node token transitional, documented as full-privilege) |
| Audit | Durable operation IDs + journald structured events |

## Open questions

1. **`WS/Config` classification** — release-owned pending the runtime write
   audit (§Layer 3). Owner: profile audit during Workstream C step 3.
2. **Egg done-matcher** — keep Panel-Running on `Create Dungeon Successed`
   and dependency-Ready on the registration line (current plan), or move the
   egg's done matcher to the registration line and drop the distinct
   matcher? UX trade-off (Panel shows "starting" ~17 s longer); decide at
   egg-variant time.
3. **Retention count** — default 3; raise if rollback-beyond-previous
   matters.
4. **KSM shim** — M7 decision still open (measured ~190 MB profit on the
   flattened instance vs ~300 MB threshold); orthogonal to this design
   (tmpfs page cache is shared by mounting, not by KSM), but the shim file
   stays a mutable root either way.
5. **`prepare_may_publish`** — deliberately excluded from protocol v1;
   revisit if unattended fleets need it.

## Evidence index

Wings v1.13.1 (verified via `git show v1.13.1:<path>`, tag = `e771816d`):

| Fact | Anchor |
|---|---|
| Power flow, locks, wait-vs-abort | `server/power.go:56-135`; `system/locker.go:34,47` |
| Pre-start Panel sync + checks | `server/power.go:171-200` (`onBeforeStart`) |
| Container always removed+recreated on start | `environment/docker/power.go:26` (`OnBeforeStart`) |
| Running reached only via console done matcher | `server/listeners.go:149-182`; `remote/types.go:145` |
| Mount composition + allow-list (`allowed_mounts`) | `server/mounts.go:22,66`; `config/config.go:365` |
| RO honored end-to-end; HostConfig fields; labels; no `CgroupParent` in vanilla | `environment/docker/container.go:138-260,434` |
| Install container gets full server env, shares volume at `/mnt/server` | `server/install.go:403` |
| Crash detection semantics + config defaults | `server/crash.go:47`; `config/config.go:258-270`; `server/server.go:317,344-346` |
| `Sync()` callers (pre-start, boot, REST sync, install) | `server/power.go:173`; `cmd/root.go:264`; `router/router_server.go:145,158`; `server/install.go:89` |
| Boot restoration via states.json → `HandlePowerAction` | `cmd/root.go:170-259`; `config/config.go:744` |
| REST 202-async power; websocket immediate-lock power | `router/router_server.go:53`; `router/websocket/websocket.go:354-376` |
| Delete path (Destroy + volume removal) | `router/router_server.go:192`; `environment/docker/container.go:271` |
| Single node-wide bearer token | `router/middleware/middleware.go:166-181` |
| Backups/SFTP walk host volume only | `server/backup.go:60`; `server/backup/backup_local.go:68`; `sftp/handler.go:79` |
| Environment seams incl. `InSituUpdate` | `environment/environment.go` (interface) |

Kernel (docs.kernel.org, admin-guide/cgroup-v2.html unless noted): memory
ownership/charging ("charged to the cgroup which instantiated it…";
shared-area charging indeterminate, migrates toward cgroups with allowance);
`memory.min`/`low` semantics + proportional overcommit distribution;
`memory_recursiveprot` mount option ("Recursively apply memory.min and
memory.low protection to entire subtrees…"); `memory.zswap.max` (store
refusal) and `memory.zswap.writeback` (0 disables all device swapping incl.
store-failure fallthrough); reclaim-root protection skip (mm/vmscan.c
`mem_cgroup_calculate_protection`); tmpfs = swap-backed
(filesystems/tmpfs.html). systemd: `MemoryZSwapMax=`/`MemoryZSwapWriteback=`/
`IOWeight=` per systemd.resource-control(5) (Debian 13). Docker: bind mounts
+ `rprivate` default propagation (docs.docker.com storage/bind-mounts).

Local production evidence ([measured]/[verified] against
`scripts/gstammtisch-guide/files/` and `game_stuff/soulmask/`): host 16 GB /
8 cores / ~69 GiB swap / zswap zstd (`MEMORY-ARCHITECTURE.md`); pak 1.79 GB
zstd 1.006×; `soulmask-paks.slice` (`MemoryMin=150M`, `MemoryZSwapMax=0`,
`MemoryZSwapWriteback=yes`) and `soulmask-static.slice` (`MemoryMin=200M`);
static share = 6 roots ≈386 M; egg matchers and variables; appid 3017300 /
depot 3017301; cluster UUIDs and roles (SOULMASK.md §9); legacy patch
behaviors cited from the current stack where they serve as prior art
(`server/slice_phase.go`, `server/slice_children.go`,
`internal/cgroups/boot.go`, patch 0011 hook `container.go:213-223`).
