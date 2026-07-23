# Shared game releases and tmpfs lifecycle — v2 proposal

- Status: revised architecture analysis, 2026-07-22
- Starting point: vanilla Pterodactyl Wings v1.13.1; existing local patches are
  prior art, not dependencies
- General target: upstreamable Wings lifecycle capabilities plus an independent
  shared-release manager usable by different games and content sources
- Concrete application: the Soulmask MAIN/CLIENT cluster on this node
- State rule: `WS/Saved/**`, especially every `world.db` and
  `WS/Saved/GameplaySettings/GameXishu.json`, is never shared or used as content
  input and is never modified by a release transaction

## Executive conclusion

The three-layer decomposition in the remarks is correct:

```text
1. Vanilla Wings + small general lifecycle extensions
   - external start preparation/admission
   - read-only dynamic mount leases
   - dependency/readiness coordination
   - optional scoped maintenance control

2. Independent shared-release manager
   - source drivers such as SteamCMD
   - immutable persistent releases and rollback
   - tmpfs generations and multiple read-only consumers
   - channels, pins, leases, reconciliation, and rollout CLI

3. Game/application profile
   - Soulmask app IDs, layout classification, readiness, RCON adapter
   - MAIN/CLIENT cohort policy and save/stop order
   - tmpfs and memory/cgroup policy for this host
```

Wings should not learn SteamCMD, Soulmask paths, tmpfs copying, or release
rollback. The release manager should not impersonate Wings for every ordinary
Panel start. Wings owns container lifecycle and calls a registered provider
before creation; the provider owns content preparation and returns an attested,
read-only mount set. This makes every Panel/API/crash-recovery start pass through
the same gate while leaving users in control of normal power actions.

Persistent disk remains the release **authority**, but for Soulmask it should
not normally be the serving backend. The production observation is decisive:
unrelated host disk activity caused severe responsiveness loss even when the
game itself showed little I/O, consistent with hot pak pages being reclaimed
from ordinary page cache and later faulted from disk. Soulmask's policy should
therefore be `tmpfs-required` by default. Disk holds complete, durable,
validated releases; tmpfs provides the isolated live generation whose pages
cannot be discarded as clean file cache and can participate in swap/zswap
policy.

Downloading and validating a candidate release can happen on persistent disk
while the old release continues serving running containers from tmpfs. The
cutover is a separate operation. It may be rolling if enough RAM exists for two
tmpfs generations or if disk serving is temporarily allowed; on this
RAM-constrained Soulmask host it should normally be a coordinated cohort
cutover with all consumers stopped, one tmpfs generation at a time.

Per-server `latest`, `previous`, or pinned release intent is useful and should
be supported by the general manager. It does not override a group's safety
policy. For an independent server, restarting can immediately select `latest`.
For the Soulmask cluster, MAIN and CLIENT should use a `cohort` policy: while
one member is running generation G, an ordinary restart of another member also
gets G. A new candidate becomes the cohort generation only through an explicit
or scheduled cluster rollout. This is the unavoidable difference between user
choice and silently running an interdependent cluster on mixed builds.

## Changes from the first draft

The first draft established the durable-release model but underemphasized or
misframed several product requirements. This version changes it as follows.

### 1. tmpfs is a Soulmask production requirement, not merely a later option

V1 proposed proving a disk-backed serving mode first and described tmpfs as an
optional second stage. That was too weak. A disk release store is still
required for durability, but the normal Soulmask serving policy is now
`tmpfs-required`. A same-generation disk mount is an explicit degraded or
recovery mode, not an automatic benign fallback.

The reason is operational rather than theoretical: Docker image operations on
the same host harmed the game even when the server produced little I/O. The
working explanation—ordinary pak page-cache eviction followed by slow disk
refaults—is exactly the failure tmpfs was introduced to prevent. tmpfs keeps
the file data in swap-backed shmem instead of clean, freely discardable page
cache. Compressible Engine/binary pages can benefit from zswap; the measured
incompressible pak may bypass zswap and page to disk swap when cold, but its hot
set can still be isolated and protected in the dedicated cgroup.

### 2. the design starts from vanilla Wings

V1 was written too closely around patches 0010 and 0011. V2 treats vanilla
Wings as the base and defines a fresh v2 patch series. Existing code is useful
evidence—for example, the proven steady matcher and child-start work—but no
new design is constrained by its names or implementation choices.

In particular, the new core concepts should not use `WINGS_CG_*` names. They
are lifecycle/content capabilities, not cgroup features.

### 3. the product is generalized into three independently maintainable layers

V1 described a Soulmask content manager first and generalized it only
implicitly. V2 explicitly separates upstream Wings capabilities, a reusable
third-party release provider, and a Soulmask profile. SteamCMD is one source
driver; tmpfs is one publication backend; Soulmask is one policy bundle.

### 4. adding a server no longer requires editing a per-UUID host file

V1 relied heavily on root-owned cluster membership. V2 supports both hardened
host configuration and user-friendly egg/server variables. A node policy
allow-lists profiles and constrains what variables may select. Safe relative
metadata may come from an egg; arbitrary host paths, commands, credentials,
mount options, and unit names may not.

An administrator can add a new server by choosing a compatible egg, setting a
profile/group/channel/dependency, and starting it. Wings and the provider
enrol and mount it automatically. A hardened node can still pin or override
those values centrally.

### 5. release acquisition is separated from release cutover

V1's update flow required all consumers offline too early. V2 allows source
query, download, unpack, hashing, validation, and persistent release creation
while games run. Only operations that replace a tmpfs generation or enforce an
atomic cohort switch require affected consumers to stop.

Background staging is I/O and CPU work and can itself hurt this host, so it
runs in a low-priority cgroup/slice with I/O limits, optional rate limiting,
and pause/scheduling policy. “Can run live” does not mean “may compete without
limits.”

### 6. release intent and multiple generations are first-class

V1 had one current/previous release but no complete per-server selection
model. V2 defines channels, `latest`, `previous`, exact pins, group policies,
container leases, and the RAM consequence of keeping two tmpfs generations.

### 7. dependency readiness is guaranteed by Wings, not convention

V1 retained the local child-start patch. V2 defines a general dependency
coordinator starting from vanilla Wings. Every Wings-owned start path—Panel,
REST, crash recovery, and boot restoration—passes through it. The stock egg
`startup.done`/Running event is the default readiness signal; a general
secondary readiness matcher can be a separate patch if maintainers accept the
use case.

### 8. direct Wings API reuse is assessed explicitly

V1 did not adequately discuss an external manager calling Wings. Vanilla Wings
does permit it, but its configured bearer token is a single node-wide secret
accepted by every protected API route. It is not scoped to power actions or
servers and carries no distinct service identity. V2 permits it as a local,
site-specific bridge only and proposes safer Panel/scoped-local-control
options for a reusable product.

## Source basis

The Wings design claims below were checked against vanilla v1.13.1, not inferred
from the local cgroup branch:

- [`Environment.OnBeforeStart`](https://github.com/pterodactyl/wings/blob/v1.13.1/environment/docker/power.go)
  removes and recreates the container on every real start.
- [Wings' router](https://github.com/pterodactyl/wings/blob/v1.13.1/router/router.go)
  exposes power, command, file, reinstall, deletion, and configuration-related
  routes behind the protected route groups.
- [The authorization middleware](https://github.com/pterodactyl/wings/blob/v1.13.1/router/middleware/middleware.go)
  compares every protected request to the single configured bearer token.
- [The REST power handler](https://github.com/pterodactyl/wings/blob/v1.13.1/router/router_server.go)
  returns `202 Accepted` and performs the power action asynchronously.
- [The crash handler](https://github.com/pterodactyl/wings/blob/v1.13.1/server/crash.go)
  may restart an offline process even after exit code 0 when clean exits are
  configured as crashes, which is the default.

The storage assumptions use the kernel's [tmpfs documentation](https://docs.kernel.org/filesystems/tmpfs.html),
the [cgroup v2 memory controller documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#memory),
and Docker's [bind-mount and propagation documentation](https://docs.docker.com/engine/storage/bind-mounts/).

## Goals and invariants

### General product goals

- Reuse one immutable content release across any number of read-only
  containers.
- Support persistent disk, tmpfs, and future publication backends without
  changing source/update logic.
- Support SteamCMD, archives, image extraction, rsync, or custom source drivers.
- Let a server select a release channel or pin while a group policy can require
  cohort consistency.
- Make ordinary Panel power controls continue to work; preparation is
  automatic on the Wings start path.
- Keep the privileged interface narrow, declarative, and node-authorized.
- Make provider failure visible and actionable rather than revealing hidden
  stale files.
- Make the general Wings patches useful for asset caches, mod packs, licensed
  content, network-storage preparation, snapshots, and other games.

### Soulmask invariants

1. MAIN and CLIENT never consume a partially installed release.
2. Their normal content backend is tmpfs, not the regular volume filesystem.
3. MAIN and CLIENT run one cohort generation unless an operator explicitly
   enables a tested mixed-version mode.
4. All release mounts are read-only to game containers.
5. `WS/Saved/**` is per-instance persistent storage and outside the provider's
   source and managed mount roots.
6. A release becomes selectable only after it exists completely on persistent
   disk and passes validation.
7. A host reboot reconstructs tmpfs from that release; no tmpfs content is
   copied back as authority.
8. A user can start, stop, or restart through the Panel at any time. Wings
   either safely admits the requested start, waits for its dependencies, or
   returns a precise conflict; it never silently bypasses content policy.
9. A background update cannot monopolize disk, CPU, or memory needed by the
   running game.
10. A rollout stop is an expected Wings lifecycle action, so a voluntary game
    exit is not misclassified and automatically restarted.

## Architecture comparison after the revised requirements

| Architecture | Durable update | tmpfs isolation | User restarts safe | General/upstream value | Verdict |
|---|---:|---:|---:|---:|---|
| Current writable tmpfs populated from an instance | No | Yes | No | Low | Reject |
| One mutable shared disk install | Yes, but non-atomic | No | Version races | Medium | Reject for Soulmask |
| Immutable disk releases served directly | Yes | No | Yes | High | Useful backend, insufficient Soulmask default |
| Immutable disk releases plus tmpfs generations | Yes | Yes | Yes with leases/policy | High | Recommended |
| Game content baked into OCI images | Yes | Page-cache/image-layer sharing | Yes | Medium | Viable for image-centric operators, not Steam-first default |

| Control integration | Every Panel start covered | Privilege surface | Reusability | Verdict |
|---|---:|---|---:|---|
| Wings performs mounts/updates itself | Yes | `CAP_SYS_ADMIN` and source-specific code in Wings | Low | Reject |
| Wings starts an egg-named systemd unit | Yes | Arbitrary-unit risk; weak structured result | Medium-low | Reject as upstream contract |
| Node-configured Unix-socket start provider | Yes | Narrow protocol; Wings validates response | High | Recommended |
| External manager only calls Wings/Panel API | No; direct user starts bypass manager | Broad token unless separately scoped | Medium | Orchestration only |
| Manual host scripts | No | Operator/root | Low | Emergency fallback only |

## Layer 1 — general Wings v2 capabilities

The upstream proposal should be several small features with independent value,
not one Soulmask-shaped patch.

### V2-1: external start-preparation provider

Add a node-configured provider call to vanilla Wings' actual start path. The
hook belongs in `Environment.OnBeforeStart`, after the previous container is
removed and before Docker creates the new one. It must not run during the
generic boot-time `CreateEnvironment()` pass for an offline server.

Use a versioned JSON protocol over a Unix-domain socket rather than a shell
command or arbitrary systemd unit name. This is portable across init systems,
supports structured errors, and gives maintainers a bounded interface.

Illustrative request:

```json
{
  "protocol": 1,
  "operation": "prepare-start",
  "request_id": "uuid",
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

Illustrative response:

```json
{
  "allow": true,
  "lease_id": "lease-uuid",
  "generation": "steam-3017300-24123343-manifest12",
  "mounts": [
    {
      "source": "/mnt/game-releases/soulmask/generation/.../WS/Content",
      "target": "/home/container/WS/Content",
      "read_only": true
    }
  ],
  "labels": {
    "io.example.release.generation": "steam-3017300-24123343-manifest12"
  }
}
```

Wings remains the final authority over what Docker receives:

- the provider is registered only in node config;
- the socket is a node-mounted path, not selected by a server variable;
- the node config allow-lists source prefixes and container target prefixes;
- provider mounts must be read-only;
- destinations cannot shadow `/home/container/WS/Saved` or other node-denied
  paths;
- a provider cannot add capabilities, devices, arbitrary environment secrets,
  Docker socket access, or host namespaces;
- duplicate/colliding mounts are rejected deterministically;
- required-provider failure aborts the start before container creation;
- each accepted response gets a lease and generation Docker label.

The implementation should reuse Wings' existing mount normalization and
node-allowed-mount checks where they apply, then add provider-specific exact
target/denial and collision rules. The new path must not become a second,
weaker interpretation of Docker mount safety.

Prefix checks alone are not enough when a source path can contain symlinks.
Wings must resolve sources beneath an allow-listed root with `openat2`
`RESOLVE_BENEATH` or an equivalent safe walk before accepting them. For a
containerized Wings, the configured provider source roots need to be visible at
the same paths, normally through a read-only `rslave` bind so host-created
tmpfs generations also propagate into Wings' validation namespace. If an
operator will not expose the roots that way, the first protocol must treat the
provider as fully trusted for source resolution and say so explicitly; a
lexical prefix check must not be presented as containment.

The first dynamic-mount version should reject a mount whose destination is an
ancestor of a protected mutable target such as `/home/container/WS/Saved`.
Whole-root release mounts are a later composite-plan feature: Wings must verify
that every protected mutable subtree is re-exposed from the correct per-server
volume, in the correct mount order, before accepting the plan.

The initial upstream patch can omit dynamic mounts and implement only
allow/deny preparation if maintainers prefer a smaller review. In that form,
the provider prepares host-side nested mounts and returns attestation. Dynamic
read-only mounts are cleaner: Docker receives the exact release source in the
container specification, avoiding hidden host mount state and Docker
`rprivate` propagation surprises. They are the recommended final contract.

Wings should call provider lifecycle endpoints:

- `prepare-start` before Docker create;
- `commit-start` after successful create, naming the container/lease;
- `abort-start` if creation fails;
- `release` after container removal;
- optional `reconcile` handshake when Wings/provider starts.

Callbacks make release retention safe without trusting a fragile integer
reference count. The provider must still reconcile leases from Docker labels
after crashes.

### V2-2: provider selection from server variables, bounded by node policy

Server/egg variables are valuable UX, but they are data, not a privilege
boundary. Wings should pass only explicitly configured selector names to a
provider, never the full environment—which contains passwords and unrelated
secrets.

Illustrative node configuration:

```yaml
docker:
  lifecycle_providers:
    shared-release:
      socket: /run/wings-providers/shared-release.sock
      required: true
      timeout: 20m
      selector_variables:
        profile: WINGS_CONTENT_PROFILE
        group: WINGS_CONTENT_GROUP
        channel: WINGS_CONTENT_CHANNEL
        release: WINGS_CONTENT_RELEASE
      allowed_profiles: [soulmask, valheim, generic-steam]
      allowed_source_roots: [/mnt/game-releases, /var/lib/game-releases]
      allowed_targets: [/home/container]
      denied_targets: [/home/container/WS/Saved]
```

The server variables choose among capabilities the node has already exposed.
They do not name a socket, systemd unit, host path, command, or mount flag.

Provider selection precedence should be:

```text
node per-server override
    > node profile/egg allow-list
    > validated server/egg selectors
    > provider defaults
```

This permits two operating styles:

- **Convenience mode:** a compatible egg names an allowed profile/group; a new
  server auto-enrols on first start with no host file edit.
- **Hardened mode:** node config pins the provider/profile/group for selected
  UUIDs or egg IDs and ignores conflicting server variables.

### V2-3: startup dependencies and readiness

This is separate from content preparation. A dependent server should declare
its prerequisite, while the parent/group may declare whether dependents are
automatically started.

The smallest upstreamable behavior uses vanilla Wings' existing
`ProcessRunningState`, which is driven by the egg's `startup.done` matcher.
That event already means “Wings considers this server ready.” For Soulmask, the
egg's done matcher should be the verified server-registration line if it can be
made reliable. This avoids requiring the existing cgroup-specific steady
matcher.

If maintainers agree that “Panel Running” and “dependency ready” are genuinely
different concepts, add a second generic `readiness` matcher in a follow-up PR.
It should be an egg/server field, not a cgroup variable.

Illustrative server selectors:

```text
WINGS_START_AFTER=b87c0a5b-2387-4a1c-8863-ff23e6800a1d
WINGS_DEPENDENCY_POLICY=wait
WINGS_AUTOSTART_DEPENDENTS=1
```

The coordinator must cover every Wings start source:

- Panel WebSocket and REST power actions;
- direct Wings API actions;
- crash recovery;
- Wings/host reboot state restoration;
- provider/rollout orchestration.

Required behavior:

1. A child restart while MAIN is ready is ordinary and proceeds immediately.
2. If MAIN is starting, the child request is queued until readiness or a
   bounded timeout, not lost.
3. If MAIN is offline, node/egg policy decides whether to start MAIN or reject
   with `dependency offline`.
4. MAIN readiness starts configured queued/autostart dependents.
5. A cycle is detected and reported; it must not deadlock boot.
6. Provider generation labels are checked when the dependency/group requires
   a shared generation.
7. Readiness is cleared on MAIN stop, crash, or new start attempt.
8. A grace timeout never counts as readiness.

This is who guarantees “CLIENT starts only after MAIN is steady”: Wings does,
inside the same power-action coordinator users already exercise. The release
manager supplies generation identity but does not own ordinary dependency
starts.

### V2-4: maintenance intent and observable power operations

Vanilla Wings already handles a stop requested through `HandlePowerAction` as
expected: it transitions the server to stopping before the process exits, so
normal crash recovery does not race the stop. The direct REST power endpoint,
however, returns `202 Accepted` and performs the action asynchronously. A
rollout tool must poll state and infer completion.

Two general improvements are useful:

- return an operation ID and expose operation status/completion for power
  transitions; and
- optionally support a bounded maintenance lease that suppresses crash restart
  while a game-specific adapter performs an external graceful exit.

The maintenance lease solves the exact `SaveAndExit` problem. Without it,
Soulmask exiting because of an RCON command looks unexpected; vanilla Wings
defaults to treating even exit code 0 as a crash and restarts it. With a lease,
Wings records the exit as expected until the lease expires or is released.

This patch is optional for the first Soulmask rollout. The safer no-patch flow
is: broadcast and flush the database through RCON, then ask Wings to stop the
server using its configured stop action. Do not issue `SaveAndExit` before
Wings knows a stop is intended.

### What should be proposed upstream

Keep the PR sequence small and general:

1. external start-preparation/admission provider over a Unix socket;
2. provider-supplied read-only mounts with leases and strict node validation;
3. dependency coordination based on stock Running readiness;
4. optional readiness matcher separate from Running;
5. optional observable power operations/maintenance lease;
6. optional scoped local service credentials, only if the Panel API is not an
   adequate orchestration route.

Each PR needs use cases beyond Soulmask. Examples include a shared mod-pack
release, per-customer licensed assets materialized just before start, a
read-only snapshot-backed data set, a network filesystem availability gate,
and multi-process game clusters.

Do not upstream:

- Soulmask or Steam app IDs;
- `systemctl start <egg-supplied-name>`;
- arbitrary command hooks;
- raw mount paths from the Panel;
- tmpfs copy/update logic in Wings;
- cgroup-specific names for lifecycle features.

## Layer 2 — reusable shared-release manager

Call the product generically “shared-release manager” below; its final name is
not a design constraint.

### Components

```text
source drivers
  SteamCMD | HTTP/archive | image extraction | rsync | custom
        |
        v
persistent immutable release store
  transactions -> validate -> releases -> channels
        |
        +---------------------+
        |                     |
        v                     v
publication backends       rollout controller
  persistent bind            CLI/API/scheduler
  tmpfs generation           Wings/Panel power
  future backend             game notification/save adapters
        |
        v
Wings provider socket
  prepare -> mount lease -> commit/release/reconcile
```

The core understands releases, manifests, channels, group policies, backends,
and leases. Source and game behavior are plugins/profiles.

### Persistent release store

Every candidate is created under a transaction directory on persistent disk.
It becomes a release only after source success, path classification, size and
space checks, required-file probes, hashes, durable metadata, and a final
completion marker. Promotion changes an atomic channel pointer; it never
updates an existing release in place.

Illustrative layout:

```text
/var/lib/game-releases/
├── sources/steamcmd/
├── transactions/<txn>/
├── profiles/soulmask/releases/<generation>/
│   ├── root/
│   ├── release.json
│   └── COMPLETE
├── profiles/soulmask/channels/
│   ├── stable -> <generation>
│   └── candidate -> <generation>
└── state/groups/servers/leases/
```

The store can retain more releases cheaply relative to RAM. At minimum retain
current stable, previous stable, and any release with a live/persisted lease.

### Content rollback versus mutable-state snapshots

The manager can make a strong guarantee about **content rollback**: selecting
G after a failed H rollout reconstructs and mounts the exact immutable G
manifest. It must not imply that an application's mutable data is thereby
rolled back or remains backward-compatible.

Mutable-state snapshots belong behind a separate game/application adapter:

- a pre-rollout adapter may flush the application and snapshot or back up the
  per-instance mutable roots;
- snapshot support is capability-detected and may use filesystem, volume, or
  application-native mechanisms;
- restore is an explicit destructive operation with its own confirmation and
  retention policy, never an automatic side effect of changing content;
- a profile must declare whether saves written by H may safely be read by G.

For Soulmask, the first implementation should take verified pre-rollout
backups/tripwires but treat save-schema compatibility as unknown. Automatic
binary rollback is therefore allowed only before H has accepted live traffic
or when the profile/operator explicitly authorizes the associated save-state
plan.

### Live background staging

The following work is safe while servers consume an older immutable release:

- query the source for an available build;
- download into a new transaction directory;
- unpack/install into that directory;
- validate/hash/classify it;
- create a complete persistent release;
- mark it `candidate` or advance an acquisition channel.

None of those steps changes a running container's source inode or tmpfs
generation. A restart selects the new release only when channel/group policy
permits it.

There is still resource interference. On this host, staging should run in a
dedicated low-priority unit/slice with controls such as:

- low CPU and I/O weight;
- read/write bandwidth or IOPS ceilings on the relevant block device;
- bounded concurrent downloads/hashers;
- optional pause while Soulmask refault/FPS pressure alarms are active;
- sufficient free disk and memory preflight;
- no allocation of a second tmpfs generation during acquisition alone.

This converts update detection/download into low-impact background work while
keeping publication explicit.

### Release selectors

Define selectors precisely:

- `latest`: newest complete release on the selected channel at prepare time;
- `previous`: predecessor of that channel's latest promoted release;
- `pinned:<generation>`: exact immutable generation;
- `cohort`: generation currently assigned to the group;
- `candidate`: staged but not yet promoted, for canary testing when policy
  permits.

The word `latest` must never mean an incomplete transaction or raw upstream
build discovered but not validated.

Server variables can expose a friendly enum:

```text
WINGS_CONTENT_PROFILE=soulmask
WINGS_CONTENT_GROUP=soulmask-prod
WINGS_CONTENT_CHANNEL=stable
WINGS_CONTENT_RELEASE=latest        # latest | previous | pinned:<id>
WINGS_CONTENT_BACKEND=tmpfs         # constrained by node/profile policy
```

An administrator should normally edit `release` or `channel`, not paths or app
IDs. A profile can force `tmpfs-required` regardless of a server request.

### Independent, cohort, and rolling group policies

The manager supports three policies:

| Policy | Selection behavior | Typical use |
|---|---|---|
| `independent` | Each restart resolves its own selector | Unrelated servers sharing identical assets |
| `cohort` | All active members use one assigned generation | Soulmask MAIN/CLIENT cluster |
| `rolling` | Old and new generations may coexist during rollout | Stateless or compatibility-tested fleets |

For `cohort`, if CLIENT is running G and MAIN is restarted with `latest` now
pointing to H, MAIN receives G. H remains pending until a group rollout. This
preserves both user control (restart works) and compatibility (no silent mixed
cluster). An explicit per-server canary requires either leaving the cohort or
an operator-approved mixed-version override.

For `rolling`, per-server `latest`/`previous` works exactly as proposed, but
the publication backend must retain both generations while leases exist.

### tmpfs generation and RAM tradeoff

Persistent staging can always coexist with a running tmpfs generation. tmpfs
publication has three modes:

1. **Single-generation/offline cutover:** stop all consumers of G, discard G's
   tmpfs, populate H, verify, start consumers. Lowest RAM peak; recommended for
   Soulmask.
2. **Dual-generation rolling cutover:** keep G while populating H. Enables
   independent restarts but temporarily consumes roughly the sum of both
   generations. Only allowed after a memory-budget check.
3. **Mixed backend:** keep G in tmpfs and serve H from its persistent release,
   or the reverse. Saves RAM but reintroduces page-cache eviction latency for
   the disk-backed consumer. Soulmask should require explicit degraded-mode
   approval for this.

Thus these three desires cannot all be guaranteed simultaneously on a
hard-constrained node:

- one server may independently choose a new release at any time;
- every served release is tmpfs-backed; and
- only one release's worth of RAM is available.

The Soulmask choice should be cohort + single-generation tmpfs cutover. The
general manager supports the other policies for hosts with different budgets.

Populate a tmpfs generation only from a complete persistent release. Copy into
a hidden generation directory, verify against the release manifest, mark it
read-only, and expose it only after success. Never update the visible tmpfs
generation in place and never copy it back to disk.

### Managed and mutable layout

Profiles classify release content and per-instance state. Default to
release-owned with explicit mutable exclusions. An unknown path created by a
new source build blocks promotion until classified.

For a mount-provider design, Wings can mount release-owned roots directly and
leave the normal server volume mounted for mutable data. Whole-root read-only
publication with nested mutable mounts is the most future-proof, but it must be
tested against Wings SFTP, file manager, backups, install/reinstall, and boot
permission correction. A generated set of managed roots is a pragmatic first
implementation if its manifest proves complete coverage.

### Adding a server

Convenience-mode flow:

1. A node administrator installs/enables the shared-release provider once and
   allows the `soulmask` profile.
2. An egg defines admin-facing variables for profile, group, channel, release
   intent, and optional dependency UUID. App-specific defaults come with the
   egg/profile.
3. The Panel administrator creates the server normally and chooses those
   values. No `/etc/.../instances.d/<uuid>.env` edit is required.
4. Installation creates only per-instance mutable directories/files, or the
   provider tolerates the ordinary install and masks managed content later.
5. On first start, Wings calls the provider with the server UUID and validated
   selectors. The provider auto-enrols the UUID in the group, resolves the
   release, creates a tmpfs lease, and returns read-only mounts.
6. Wings creates and labels the container. SFTP/backup behavior follows the
   chosen mount model.

Hardened-mode additions:

- node config may restrict an egg/profile to specific groups;
- group auto-enrolment may require an admin-created join token or UUID
  allow-list;
- node overrides can force profile/channel/backend/dependency;
- a server cannot join another tenant's group merely by guessing its name.

Egg-only profile data can be supported for safe declarative fields—Steam app
ID, relative managed/mutable paths, validation probes—but only when the node
enables it. It must not permit shell fragments, absolute host paths, source
credentials, or arbitrary executables. A packaged provider profile remains
easier to review and update than a large encoded egg variable.

## Layer 3 — Soulmask application profile

### Content/source configuration

The Soulmask profile contains, subject to verification against a clean install:

- Steam dedicated-server app ID 3017300;
- optional Steamworks app 1007 handling;
- source branch/beta policy and secret references;
- release-owned roots such as `Engine`, `WS/Binaries`, `WS/Config`, and
  `WS/Content`;
- per-instance mutable roots including all `WS/Saved`, `Steam`, `.steam`, and
  `.config` runtime state;
- required binary and pak probes;
- manifest/hash policy and tmpfs capacity formula;
- entrypoint generation-sentinel check;
- KSM shim delivery policy separate from Steam content.

The exact layout must be generated from a clean Steam install. Current evidence
already shows why: `WS/Config` contains installed files omitted by the old
static share, while `Steam/config` and `Steam/logs` are live runtime writes even
though `Steam` was put in that share.

### Cluster policy

```yaml
profile: soulmask
group: soulmask-prod
mode: cohort
backend: tmpfs-required
members:
  b87c0a5b-2387-4a1c-8863-ff23e6800a1d:
    role: main
  6c418fe7-9be1-4971-87ec-529f6e909f89:
    role: client
    start_after: b87c0a5b-2387-4a1c-8863-ff23e6800a1d
```

This may be materialized from egg variables plus provider state rather than a
literal host file. The policy result is what matters:

- cohort generation assignment;
- MAIN ready before CLIENT starts;
- CLIENT stops before MAIN during rollout;
- both game containers have in-container Steam auto-update disabled;
- a normal restart while its sibling remains live reuses the cohort generation;
- new candidate promotion is a coordinated rollout.

### RCON adapter

RCON is a game-specific convenience and save/notification channel, not the
release correctness boundary. The existing `exec-soulmask-rcon.sh` proves a
workable adapter pattern: discover the UUID-named container, read RCON port and
password from its environment, and run the client in the container's network
namespace so the source is loopback.

General product choices for credentials/network access:

- a Soulmask adapter with tightly scoped access to Docker metadata/network;
- provider-managed secret references and direct RCON connectivity;
- a small helper invoked on the host with fixed operations; or
- future Wings command/console integration with scoped permission.

Giving the network-facing Steam updater unrestricted Docker socket access is
not acceptable as a general design. Split the unprivileged downloader from the
privileged publisher/orchestrator and isolate the RCON helper if Docker access
is used locally.

Useful RCON actions:

- broadcast “update staged; maintenance in N minutes”;
- query online players to choose/defer a maintenance window;
- `SaveWorld 0` plus `BackupDataBase world` before the Wings stop;
- optionally `SaveAndExit` only after a Wings maintenance lease suppresses
  crash recovery.

RCON responsiveness itself is not a health signal, as the existing script
documents. Use the real registration/readiness line, server FPS, and content
generation attestation.

## External manager calling Wings or Panel APIs

### What vanilla Wings allows

Vanilla Wings exposes:

- `POST /api/servers/:server/power` for start/stop/restart/kill;
- `GET /api/servers/:server` for current state; and
- command, file, install/reinstall, deletion, transfer, and configuration
  routes behind the same authorization middleware.

The middleware compares the bearer value to the one token from Wings config.
There is no server/action scope or caller identity. The power endpoint returns
`202` before the asynchronous action completes, so an orchestrator must poll
state and still reason about timeouts/errors from logs.

### Is reading the Wings config token acceptable?

Sometimes, locally and deliberately:

- the caller is already root-equivalent on the node;
- it is a small site-specific tool in the same trust domain as Wings;
- config/token file permissions are strict;
- the API is reached over loopback/TLS; and
- everyone accepts that compromise grants full node API control.

It should not be the reusable manager's default:

- the token authorizes far more than power—it can reach destructive file,
  reinstall, deletion, and configuration routes;
- parsing `/etc/pterodactyl/config.yml` couples the tool to Wings' secret
  representation and rotation;
- copying the token into another service expands its exposure;
- all calls look like the Panel/node credential, with no distinct least-
  privilege principal;
- an unprivileged/network-facing updater would become a node-control process;
- asynchronous `202` is a weak transaction API.

### Preferred control options

Ranked for the general solution:

1. **Panel Client API with a dedicated automation account/subuser** limited to
   the managed servers and required power/console permissions. This preserves
   the Panel as authority and avoids exposing the node token. Confirm exact
   permission/audit behavior on the deployed Panel version.
2. **Scoped local Wings service credential or Unix-socket control API** added
   upstream: allow-listed server UUIDs, actions such as read/start/stop, caller
   identity, audit metadata, TTL/rotation, and preferably operation IDs.
3. **Existing Wings node token** for the initial root-only Soulmask CLI, clearly
   documented as full node privilege and kept out of the downloader/provider
   process.
4. **Direct Docker control** only as emergency recovery. It bypasses Wings'
   locks, expected-stop state, crash detection, Panel synchronization, and
   activity semantics.

The directions of communication solve different problems:

```text
Wings -> release provider
  mandatory on every start; enforces mounts/generation despite user actions

rollout manager -> Panel/Wings control API
  optional coordinated maintenance convenience; stops/starts groups
```

An API-only manager cannot guarantee safe ordinary user restarts because a
user can start from the Panel without calling it. The Wings start provider is
therefore the correctness boundary; outbound API control is orchestration.

## Coordinated cluster rollout CLI

An explicit CLI is a good product surface:

```text
shared-release rollout soulmask-prod --to latest
```

Proposed flow:

1. Resolve `latest` to a complete persistent generation H. If none exists,
   acquire/validate it in the low-priority staging slice while G keeps running.
2. Check tmpfs size and rollout mode. For Soulmask, require cohort offline
   cutover; do not allocate H's tmpfs yet.
3. Query player count and optionally defer. Broadcast a countdown through the
   Soulmask RCON adapter.
4. Flush state with `SaveWorld 0` and `BackupDataBase world`; verify replies and
   record state tripwires/backups.
5. Ask Wings/Panel to stop CLIENT, wait for confirmed Offline, then stop MAIN
   and wait. Use Wings power stop so exit is expected. Do not call RCON
   `SaveAndExit` first unless a maintenance lease is active.
6. Under the provider group lock, remove the old tmpfs generation after all
   leases/containers are gone, populate H from its persistent release, verify
   every file/sentinel, and atomically assign cohort H.
7. Release the provider lock before asking Wings to start; otherwise a Wings
   start callback waiting on the same lock can deadlock the rollout.
8. Start MAIN through Wings/Panel. Its provider call resolves H and returns the
   read-only mounts.
9. Wait for MAIN readiness. Wings' dependency coordinator starts CLIENT; or,
   until that patch exists, the rollout CLI starts CLIENT only after observing
   MAIN Running/registration.
10. Verify CLIENT readiness, identical generation labels, read-only mounts,
    RCON reachability, and application health. Mark H verified after a soak.
11. On failure, stop the group and reconstruct previous G. A binary rollback
    after H has written saves still needs explicit save-schema policy.

This CLI does not take control away from users. Panel restarts remain valid and
pass through the provider/dependency gates. The CLI adds a safe multi-server
maintenance transaction users can choose or schedule.

### Automatic Steam update detection

The Steam source driver can poll app/branch metadata or run a controlled
SteamCMD acquisition check. Recommended automation levels:

- **detect only:** notify that a newer upstream build exists;
- **stage:** download and validate it as a candidate while G runs;
- **schedule:** choose a maintenance window and notify players;
- **roll out:** execute the coordinated CLI flow automatically if policy allows.

Default Soulmask policy should be auto-detect + auto-stage, manual/scheduled
rollout. Fully automatic rollout is reasonable only after repeated successful
failure-injection tests, reliable player notification/save confirmation, and a
defined rollback/save-schema policy.

## Failure and selection policy

| Condition | General manager behavior | Soulmask policy |
|---|---|---|
| Steam/source unavailable | Keep current complete release | Continue G; alert |
| Candidate staging fails | Quarantine transaction | Continue G; alert |
| New path unclassified | Refuse candidate promotion | Continue G; name path |
| Background I/O pressure high | Throttle/pause staging | Protect live game |
| tmpfs H cannot fit while G runs | Choose dual/disk/offline policy | Wait for cohort cutover |
| tmpfs rebuild fails with no consumers | Keep persistent releases | Block start by default; explicit disk-degraded override only |
| Independent server requests `previous` | Lease previous generation | Allowed if not cohort-conflicting |
| Cohort member requests H while sibling runs G | Resolve G or report pending H | Ordinary restart uses G |
| Provider unavailable on required start | Wings refuses create | Remain offline, actionable error |
| MAIN not ready when CLIENT starts | Wings queues/rejects by policy | Wait; never grace-success |
| RCON save fails during rollout | Abort or require operator override | Do not stop automatically |
| Manager uses `SaveAndExit` without maintenance intent | Wings may crash-restart | Forbidden workflow |
| Rollout crashes after stop | Reconcile channel/tmpfs/leases from durable state | Start only an attested G or H |

## Security model

### Trust levels

1. **Panel/server variables:** untrusted selectors, even when normally admin-
   only; safe only within node allow-lists.
2. **Wings:** container lifecycle authority; validates provider response before
   handing mounts to Docker.
3. **Release provider:** node-trusted for declared release roots, but cannot
   request arbitrary Docker privilege.
4. **Publisher/mount helper:** narrowly privileged host component with no
   source credentials/network if practical.
5. **Downloader/source driver:** network-facing and unprivileged; writes only
   transaction directories.
6. **Rollout orchestrator:** holds only scoped power/RCON credentials when
   possible; separate from downloader.

### Variable safety

Safe candidates for egg/server selection:

- registered profile ID;
- group ID within an authorized namespace;
- channel name matching a constrained syntax;
- `latest`, `previous`, or an existing immutable generation ID;
- dependency UUID already visible/authorized on the same node;
- enumerated policy choices bounded by the profile.

Never accept from an egg/server variable:

- absolute source/target paths;
- systemd unit names;
- shell commands or executable paths;
- arbitrary mount flags/capabilities/devices;
- Steam credentials or file-backed secret paths;
- another server's mutable-volume path;
- a request to make provider mounts writable.

Group membership can expose shared content across servers. Multi-tenant nodes
need owner/namespace checks or admin-issued join tokens, even when content is
read-only.

## Implementation and upstream plan

### Work from vanilla

Create a new v2 patch branch from the exact production vanilla tag, with a
parallel branch rebased onto the current upstream target. Port only generic
lessons, not the old commit dependency chain.

Suggested packages:

1. `external-start-provider`: Unix-socket prepare/admit result, required
   failure semantics, timeouts, tests;
2. `provider-readonly-mounts`: validated dynamic mount response, labels, lease
   callbacks, Docker integration tests;
3. `startup-dependencies`: graph, queue/reject/autostart, boot/crash/manual
   start coverage using stock Running readiness;
4. `readiness-event`: only if separate readiness survives maintainer review;
5. `power-operations`: operation status and optional maintenance lease;
6. `scoped-service-control`: only if a dedicated Panel automation identity is
   inadequate.

Do not bundle all six into one upstream PR. The provider and dependency
features should be independently useful and reviewable.

### Release-manager milestones

1. Core immutable release store, manifest, channels, source-driver interface,
   and fixture tests.
2. SteamCMD driver with low-impact staging slice and fake-source tests.
3. tmpfs publisher with generation verification, capacity policy, and crash
   recovery.
4. Wings provider protocol and lease reconciliation.
5. selector/group/cohort policy and auto-enrolment.
6. Soulmask profile and generation guard.
7. rollout CLI using a temporary root-only Wings-token adapter.
8. dedicated Panel/scoped-control adapter and RCON notification/save plugin.

### Migration of the two live servers

1. Keep current production behavior unchanged while building and testing the
   release store/provider against disposable fixture volumes.
2. Save and back up both `WS/Saved` trees. Record `world.db` and
   `GameXishu.json` hashes/metadata.
3. Build a clean Soulmask release from Steam, compare it to the deployed build,
   and finalize the managed/mutable classification.
4. Populate a test tmpfs generation and run a disposable container through
   provider-supplied read-only mounts.
5. Test SFTP, backups, install/reinstall behavior, KSM shim, RCON, and the
   generation guard.
6. In a maintenance window, stop CLIENT then MAIN, replace the two old ramdisk
   services with one provider-managed generation, and set both containers'
   `AUTO_UPDATE=0`.
7. Start MAIN, verify registration, start CLIENT, verify identical generation
   labels and cluster behavior.
8. Reboot and reconstruct from the persistent release.
9. Stage a real or captured update while G runs, then rehearse the coordinated
   H rollout and G rollback.

The old private install copies are not an automatic fallback. Retain them
untouched during migration, but rollback should select a validated persistent
release and reconstruct tmpfs.

## Acceptance oracles

### General Wings/provider

1. Every actual start source calls the provider exactly once; creating an
   offline environment does not.
2. Required-provider timeout/failure leaves no container and returns an
   actionable error to the initiating surface.
3. Wings rejects writable, out-of-prefix, denied-target, duplicate, and
   path-escape mounts from a test provider.
4. Create failure calls `abort-start`; successful create labels and commits the
   lease; removal releases it.
5. Provider or Wings crash reconciles leases from Docker labels without
   deleting an in-use generation.
6. A user restart still works through the Panel and receives the selected
   release automatically.
7. Dependency handling covers Panel, API, crash, and reboot starts; cycles and
   timeouts cannot deadlock Wings.
8. CLIENT never transitions to container start before MAIN's chosen readiness
   event and matching-generation check.

### Release manager/tmpfs

9. Kill acquisition at arbitrary points; G keeps serving and no incomplete
   transaction becomes `latest`.
10. Saturate staging I/O under a representative game workload; configured
    cgroup/I/O limits prevent the severe latency regression being designed
    against.
11. Stage H while G runs and prove G's files, mounts, hashes, and tmpfs usage do
    not change.
12. In single-generation mode, H publication refuses while a G lease exists.
13. In dual-generation mode, capacity is checked before allocation and both
    generations remain immutable until their final lease releases.
14. A write/unlink/rename to every managed root fails in every consumer;
    per-instance `WS/Saved` writes remain isolated and persistent.
15. Reboot loses tmpfs and reconstructs the assigned generation solely from
    its complete persistent release.
16. Unknown source paths, bad hashes, ENOSPC, foreign mounts, and stale
    sentinels are detected before start.

### Soulmask rollout

17. Content-manager operations while stopped leave every `world.db` and
    `GameXishu.json` hash unchanged.
18. The rollout stops CLIENT before MAIN and starts MAIN before CLIENT.
19. RCON save failure aborts unattended rollout without stopping the server.
20. Wings-initiated stop does not trigger crash restart; raw RCON `SaveAndExit`
    does restart under vanilla defaults unless protected by maintenance intent.
21. An ordinary MAIN restart while CLIENT runs G receives G even if H is staged.
22. Explicit cohort rollout moves both to H; an explicit rollback reconstructs
    G without using hidden private copies.
23. `tmpfs-required` blocks an unsafe automatic disk fallback. A manual
    degraded override is conspicuous and serves the same verified generation.
24. Docker build/image pressure while Soulmask runs no longer causes the pak
    refault/responsiveness regression at the calibrated tmpfs/cgroup settings.

### Gate

Run the real gate in `tester-unified`, with a full run-uid identity, plus a
dedicated privileged test container/VM for systemd, Docker, mount namespaces,
cgroup v2, tmpfs, zswap, and fault-injection. The devcontainer is the cockpit,
not the gate. Unit tests alone cannot validate `rprivate` mount behavior,
tmpfs charging, crash recovery, or real Docker lifecycle ordering.

## Product decisions and recommended defaults

| Decision | General capability | Soulmask default |
|---|---|---|
| Authority | Persistent immutable release | Required |
| Serving backend | Disk/tmpfs/profile-selectable | `tmpfs-required` |
| Acquisition | Offline or background | Background, throttled |
| Promotion | Independent/cohort/rolling | Cohort |
| Version selector | latest/previous/pinned/channel | Cohort stable; explicit candidate rollout |
| Automatic behavior | detect/stage/schedule/rollout | Auto-detect + auto-stage; scheduled/manual rollout |
| tmpfs generations | single/dual/mixed | Single; all consumers stop for cutover |
| Disk fallback | allowed/preferred/forbidden | Forbidden automatically; manual degraded mode |
| Dependency ready | stock Running or separate matcher | Registration line; stock done if reliable |
| Child start | reject/wait/auto-start | Wait + auto-start after MAIN ready |
| Busy cluster update | stage only or force maintenance | Stage only; explicit rollout stops group |
| API control | Panel/scoped Wings/node token | Panel/scoped preferred; node token transitional |
| Server enrolment | root mapping or allowed egg selectors | Egg selectors with node profile allow-list |

## Bottom line

The durable store and tmpfs solve different problems and both belong in the
Soulmask design:

```text
Steam/source
    |
    | low-priority acquisition while G runs
    v
persistent immutable release H (durability, validation, rollback)
    |
    | later, policy-controlled publication
    v
tmpfs generation H (live I/O isolation, swap/zswap behavior)
    |
    | provider returns read-only leases
    v
Wings-created MAIN/CLIENT containers
```

The clean integration is bidirectional but not circular in authority:

- Wings calls the release provider on every start, so user-initiated restarts
  cannot bypass content preparation, selection, or read-only mounts.
- Wings itself guarantees dependency readiness and normal power semantics.
- The rollout tool may call Panel/Wings to coordinate a maintenance event, but
  that is convenience and automation, not the only correctness gate.
- Soulmask-specific code supplies Steam layout, RCON, save order, cohort, and
  tmpfs policy without leaking those concepts into upstream Wings.

That architecture removes manual per-instance host work, keeps the production
tmpfs latency isolation, permits updates to be staged while servers run,
supports explicit latest/previous/pinned releases, and creates general Wings
features that can plausibly appeal upstream.
