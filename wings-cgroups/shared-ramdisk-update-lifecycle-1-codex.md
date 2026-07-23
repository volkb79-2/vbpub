# Shared Soulmask content: safe update and ramdisk lifecycle

- Status: design analysis, 2026-07-22
- Scope: the two Soulmask servers on this node, the current host ramdisk
  scripts, and the Pterodactyl Wings patch stack in `wings-cgroups/`
- Production data rule: nothing in `WS/Saved/` may be used as an update source,
  update target, shared path, or ramdisk population source. In particular,
  `world.db` and `WS/Saved/GameplaySettings/GameXishu.json` must never be moved,
  overmounted, copied back, or modified by the content manager.

## Executive recommendation

Do not let either game container update a writable shared tmpfs, and do not
copy a live tmpfs back into an instance volume. Make persistent, versioned,
validated release directories the authority. Consumers see one immutable
release, read-only. A host-side content manager may publish that same release
either directly from disk or as a derived tmpfs snapshot; tmpfs is then a
cache/presentation backend, never the only durable copy.

The recommended control plane is initially a short-lived systemd oneshot
invoked synchronously by Wings on the **actual start path**. It runs in the
host mount namespace, takes a cluster lock, prepares or verifies one release,
reconciles all mounts, writes a structured result, and exits. It must have no
`RemainAfterExit=yes` and no mount-tearing `ExecStop`. If preparation cannot
prove that the requested server will see a complete known-good generation,
Wings refuses that start. A failed Steam availability check may fall back to
the previously verified release; a missing or wrong mount may not fall back to
a private, possibly stale instance copy.

The main game server remains the cluster start authority, but no longer the
update writer:

- `b87c0a5b-2387-4a1c-8863-ff23e6800a1d` is MAIN and triggers preparation.
- `6c418fe7-9be1-4971-87ec-529f6e909f89` is CLIENT and stays stopped until
  MAIN reaches its real steady/registered event.
- Both game containers use `AUTO_UPDATE=0`; the host updater is the only
  SteamCMD writer.
- Every shared game-content mount is read-only in the consumers.
- `WS/Saved/` remains an ordinary per-instance, persistent, writable tree.

Implement this in two deliberately separate stages:

1. Publish a versioned release directly from persistent ext4. This immediately
   fixes durability, update atomicity, version skew, and duplicate page cache
   (all consumers use the same inodes). Prewarm and protect the measured hot
   file cache in a dedicated cgroup.
2. Add the tmpfs publication backend only after the disk-backed lifecycle and
   rollback tests pass. It copies from the same immutable release and retains
   disk-backed fallback to the **same generation**. This isolates the ramdisk
   performance policy from update correctness.

Do not deploy patch 0011 as the update solution in its present form. It is
useful scaffolding for calling systemd, but its trigger point, no-op semantics,
timeout, and fail-open policy contradict this lifecycle.

## What was inspected

This analysis is based on the repository and a read-only inspection of the
running node on 2026-07-22:

- `soulmask-pak-ramdisk-setup.sh` and its teardown/toggle/unit/slice
- `soulmask-static-ramdisk-setup.sh` and its teardown/unit/slice/path list
- the per-instance environment files and current Soulmask egg
- Wings v1.13.1 plus the locally applied patch-stack checkout at
  `wings-cgroups/build/wings-pterodactyl`
- patches 0010 (child start) and 0011 (systemd unit trigger)
- the running MAIN container's selected environment, install layout, Steam
  app manifest, Unreal manifest, and mount types; game-state contents were not
  read

Observed current state is not assumed to be permanent: MAIN was running from
its ext4 volume with `AUTO_UPDATE=1`, `VALIDATE=1`, and no active pak/static
tmpfs bind. The two ramdisk services were inactive. The committed host and egg
changes describe a different intended next state, so the design below covers
both rather than treating the live snapshot as configuration truth.

Relevant external contracts:

- A tmpfs has no persistent backing; unmounting it loses its contents. Swap is
  an eviction mechanism, not persistence: [Linux tmpfs documentation](https://docs.kernel.org/filesystems/tmpfs.html).
- A bind mount obscures the data below it, is writable by default, and Docker
  bind propagation defaults to `rprivate`: [Docker bind-mount documentation](https://docs.docker.com/engine/storage/bind-mounts/).
- cgroup v2 accounts both page cache and anonymous memory and applies
  `memory.min`/`memory.low` protection to charged pages: [Linux cgroup v2 memory documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#memory).
- Stock Wings removes and recreates the container on every real start in
  [`Environment.OnBeforeStart`](https://github.com/pterodactyl/wings/blob/v1.13.1/environment/docker/power.go),
  while generic environment creation also happens during Wings initialization.

## Required invariants

These are gates, not preferences.

1. **Persistent authority:** every runnable generation exists completely on
   persistent disk before it can be selected.
2. **One generation per cluster start:** MAIN and CLIENT may not start on
   different content generations.
3. **No live mutation:** a running server never observes SteamCMD modifying its
   game files in place.
4. **Read-only consumers:** no game container, panel file action, or accidental
   shell can mutate shared release content.
5. **State isolation:** all of `WS/Saved/` is per-instance and outside every
   update, publication, deduplication, and rollback operation.
6. **Complete update scope:** every file installed or changed by the update is
   either in the shared immutable release or explicitly classified as updater
   metadata/runtime state. An unknown new path blocks promotion.
7. **Last-known-good fallback:** update/download failure keeps the previous
   verified generation selected. It never exposes an old private copy hidden
   below a bind mount.
8. **Start attestation:** before the game process executes, the container must
   prove which release generation it sees and that its managed paths are
   read-only.
9. **Serialized cluster lifecycle:** update, publish, rollback, mount reconcile,
   and garbage collection share one cluster lock.
10. **No surprising stop:** an ordinary start request must not silently stop a
    running sibling. If an update requires the cluster offline, the request
    blocks with an actionable reason; a separate explicit maintenance action
    may coordinate stops.
11. **Host-owned authorization:** a public Panel/egg value can request a known
    policy name at most. It cannot supply a host path, command, arbitrary unit
    name, mount options, release ID, or target UUID.
12. **Recoverable operations:** old releases and state backups are retained
    until the new generation has completed a real MAIN+CLIENT start and an
    operator-defined soak period.

## Why the current design cannot satisfy those invariants

### Current data flow

The pak and static services currently do this:

```text
first opted-in instance volume (persistent but not authoritative)
                         |
                         | cp -a, once
                         v
                  shared writable tmpfs
                    /             \
                   / bind rw       \ bind rw
                  v                 v
          MAIN volume path    CLIENT volume path
          (disk below hidden) (disk below hidden)
```

With `AUTO_UPDATE=1`, SteamCMD runs **inside** each container after Docker has
created it. It writes through the bind to tmpfs. The persistent file below the
mount is merely obscured and remains stale. A host reboot or teardown loses the
updated tmpfs, after which setup repopulates from an arbitrary old instance
copy. `validate` does not make volatile writes durable.

The problem is not limited to the pak. The running image's Unreal manifest
listed update-owned files under `WS/Config`, which the static path list does
not share. Conversely, the current static list includes `Steam`, and the live
game was actively writing `Steam/config` and `Steam/logs`. Therefore the
current manual list is both incomplete for updates and over-broad for runtime
writes.

### Concrete failure modes

| Failure | Current behavior | Required behavior |
|---|---|---|
| MAIN updates while tmpfs is mounted | New bytes exist only in tmpfs; sibling sees an in-place mutation | Update a disk staging generation; publish only after complete validation |
| Host reboots after an update | tmpfs disappears; an old private copy can repopulate it | Rebuild the selected generation from its persistent release |
| Two containers update | Concurrent writers and independent Steam manifests race on one filesystem | Consumers never run SteamCMD; one updater holds one lock |
| Update adds a new top-level path | Hand-maintained bind list silently omits it | Default to release-owned; unknown classification blocks promotion |
| Copy is interrupted | Partial path remains; the next run treats existence as success | Hidden transaction directory plus manifest/hash verification and commit marker |
| tmpfs fills | SteamCMD can fail halfway through a live update | Measure before publication; old disk release remains selected |
| One setup service fails | Pak and static trees can come from different moments | One release manifest and one cluster publication transaction |
| Unit trigger fails | Patch 0011 logs and starts against private files | Use same-generation disk fallback or refuse to start |
| Wrong/stale mount already exists | `mountpoint -q` accepts it | Verify source, generation sentinel, flags, and every target |
| Server is deleted/reinstalled | State-file/refcount assumptions can drift | Reconcile from authoritative config and observed consumers; never copy back |

Additional current-script hazards:

- Source selection is "the first opted-in instance containing a file," not an
  explicit owner and not checked against a Steam build ID. Filename sort order
  can select CLIENT before MAIN.
- Both shared mounts are writable. The word "read-only" exists in comments,
  not mount enforcement.
- The static and pak copies are separate services, state files, size checks,
  and failure domains, so they cannot provide holistic promotion.
- `cp -a` writes directly to the final tmpfs path. If it fails after creating
  that path, a later run sees the path and reuses it.
- The state file records mount targets but not a content generation, build ID,
  source manifest, hashes, or transaction state.
- Direct script invocation has no cross-process lock. systemd serializes jobs
  for one unit, but it does not serialize the two different services or manual
  script runs.
- Teardown/recreate while containers exist can leave different mount
  generations in different private Docker mount namespaces. Docker's default
  `rprivate` propagation is why a mount must be correct before container
  creation, but it also means host-side replacement is not a safe live update
  mechanism.

### Patch 0011 is not a lifecycle transaction

Patch 0011 correctly avoids giving Wings `CAP_SYS_ADMIN` and correctly applies
an exact node allow-list to panel-supplied unit names. Those are valuable
properties. Four details prevent it from solving this task:

1. The call is in `Environment.Create()`. Wings calls `CreateEnvironment()`
   for all known servers during daemon initialization, including servers that
   will stay offline. It is not limited to an operator's start transaction.
2. Both ramdisk units are `Type=oneshot` plus `RemainAfterExit=yes`.
   `StartUnit` on an already active/exited unit intentionally does not rerun
   `ExecStart`. A newly installed server is therefore still not reconciled—the
   motivating bug for the patch.
3. Fail-open is only safe for a pure optional optimization. Once hidden private
   copies can be stale or the cluster requires one build, fail-open can launch
   a wrong version and let `AUTO_UPDATE=1` diverge it further.
4. The 150-second timeout was sized for copying the present ramdisk. A real
   Steam download and full validation can legitimately take much longer.

There is also an important wording issue: `StartUnit` does not "(re)run" an
active oneshot. The patch's tests explicitly prove the opposite. Documentation
and logs should say "request start" and report whether work actually ran.

Patch 0010 remains useful. Starting CLIENT only after MAIN's registration
marker closes the read-during-update race **after** content preparation has
become durable and atomic. It does not stop an already running CLIENT, create a
cluster maintenance transaction, or prove both containers see the same build.

## Architecture alternatives

Storage architecture and control-plane architecture are separate decisions.
For example, a systemd manager can publish persistent disk files or a tmpfs
snapshot without changing the Wings contract.

### Storage alternatives

| Option | Durability/update safety | RAM behavior | Complexity | Verdict |
|---|---:|---:|---:|---|
| Independent per-instance installs | Strong if each updates successfully, but versions can skew | Worst: duplicate inodes/page cache | Low | Safe emergency fallback only |
| One mutable persistent shared install | Durable and one page cache, but live update is non-atomic | Good sharing; file pages can be reclaimed | Low-medium | Better than writable tmpfs, still unsafe for live updates |
| Versioned persistent releases, read-only consumers | Strong: stage, validate, atomic select, rollback | One shared page cache; protect/prewarm measured hot set | Medium | Recommended first production stage |
| Versioned release plus derived tmpfs snapshot | Same durable authority; safe if publication is offline/transactional | One shared shmem copy; zswap policy available | Medium-high | Recommended optional second stage |
| Writable tmpfs plus rsync/copy-back | Crash windows, partial multi-file commits, unclear deletions/renames | Same current RAM benefit | High | Reject |
| Overlayfs with tmpfs upper then commit | Whiteouts, upper/lower reconciliation, non-atomic disk commit | Can isolate writes | Very high | Reject for production updates |
| Hardlink farms between volumes | Accidental in-place write can corrupt all links; rename updates silently diverge | Shared page cache until divergence | Medium operational burden | Reject as lifecycle foundation |
| Game install baked into the Wings/game image | Immutable and shared image layers; rollback by image tag | Shared lower-layer cache | High build/publish coupling to Panel | Viable only if abandoning Steam-on-host workflow |

The following weighted score makes the tradeoff explicit rather than hiding it
in prose. Scores are 1 (poor) through 5 (strong); the total is normalized to
100. The weights reflect this node's stated priorities: correctness/durability
30%, atomicity/error detection 20%, RAM effectiveness 20%, security/blast
radius 10%, maintainability 10%, and implementation effort 10%.

| Option | Correct | Atomic/detect | RAM | Security | Maintain | Effort | Weighted |
|---|---:|---:|---:|---:|---:|---:|---:|
| Independent installs | 4 | 2 | 1 | 5 | 5 | 5 | 66 |
| Mutable shared disk tree | 2 | 1 | 4 | 4 | 4 | 4 | 56 |
| Versioned disk releases | 5 | 5 | 4 | 4 | 4 | 3 | **88** |
| Versioned disk + tmpfs | 5 | 5 | 5 | 4 | 3 | 1 | **86** |
| Writable tmpfs + copy-back | 1 | 1 | 5 | 3 | 1 | 2 | 42 |
| Overlay/tmpfs commit | 2 | 2 | 5 | 2 | 1 | 1 | 48 |
| Hardlink farm | 2 | 2 | 4 | 4 | 2 | 4 | 56 |
| Versioned game image | 5 | 5 | 4 | 4 | 2 | 1 | 80 |

The disk-release option scores slightly higher than disk+tmpfs only because it
can be delivered and proven with a smaller failure surface. The target design
supports both: tmpfs becomes an additional backend once measurement justifies
its complexity.

The persistent-release stage is not giving up the memory objective. Two
containers binding the same release files use the same inodes and therefore
one page-cache copy. A prewarm unit can charge the measured hot pages to a
dedicated slice and `memory.min`/`memory.low` can protect them. That needs a
real pressure test: page ownership and effective ancestor protection must be
observed, not inferred. If protected disk cache still produces unacceptable
refault latency, the same release manager can switch its publication backend
to tmpfs without redesigning updates.

For the ~1.7 GiB pak measured as incompressible, tmpfs with
`MemoryZSwapMax=0` ultimately swaps cold data to disk rather than compressing
it. A protected shared disk page cache may deliver the same important property
for the hot subset with less lifecycle risk. For Engine/Binaries, tmpfs may
still be worthwhile because their cold shmem pages can benefit from zswap.
That is a measurement question, not an update-integrity question.

### Control-plane alternatives

| Option | Security boundary | Operability | Verdict |
|---|---|---|---|
| Wings performs `mount(2)` itself | Wings needs host mount namespace and `CAP_SYS_ADMIN`; domain-specific teardown in public daemon | Tight lifecycle integration, largest blast radius | Reject |
| Wings starts a fixed systemd oneshot | systemd owns privilege; exact host config; journald and timeouts | Simple, no bespoke API; result needs a status file | Recommended first implementation |
| Wings calls a narrow Unix-socket broker | Root broker validates peer/request and returns structured progress/result | Best long-term UX and policy; more code/process supervision | Consider after oneshot is proven |
| Boot-only service | No new Wings capability | Misses post-boot installs and per-start verification | Insufficient alone |
| Manual host scripts | Human is the transaction coordinator | Error-prone and not self-healing | Keep only as emergency tooling |

Systemd already provides the needed privilege separation for the first
implementation. The unit must be a **runner**, not a lifecycle owner:

```ini
[Service]
Type=oneshot
ExecStart=/usr/local/libexec/soulmask-content prepare --server %i
TimeoutStartSec=20min
# no RemainAfterExit
# no ExecStop
```

With no `RemainAfterExit`, every requested prepare actually runs and exits
back to inactive. A separate explicit teardown/garbage-collection command is
safer than attaching destructive behavior to `ExecStop`.

## Recommended target design

### Authority and layout

Illustrative paths (final names are an implementation choice):

```text
/var/lib/gstammtisch/soulmask-content/
├── steamcmd/                         # updater tool, not served to games
├── transactions/<txn>/root/         # incomplete, never selectable
├── releases/<buildid>-<manifest>/
│   ├── root/                         # complete Steam install, immutable
│   ├── release.json                  # build IDs, hashes, size, classification
│   └── COMPLETE                      # written last after fsync/validation
├── current -> releases/<generation>  # atomically replaced symlink
└── previous -> releases/<generation>

/mnt/soulmask-content/                # optional tmpfs publication backend
└── <generation>/...                  # derived only; disposable

/var/lib/pterodactyl/volumes/<uuid>/
└── WS/Saved/...                      # per-instance authority, never shared
```

Do not seed a canonical release from "the first existing instance." Bootstrap
it with a fresh SteamCMD install into a new transaction directory, validate it,
and compare its build ID to the currently running build. Existing instance
volumes remain rollback evidence but are not future authorities.

The release manifest should include at least:

- transaction and generation IDs
- app ID 3017300 build ID and, if used, Steamworks app 1007 build ID
- beta branch identity without storing credentials
- file count and total apparent/allocated size
- per-file path, type, mode, size, and cryptographic hash
- the exact managed-root and runtime-root classification version
- creation/validation timestamps and tool versions
- a `COMPLETE` marker written only after data and metadata are durable

Keep at least the active and previous known-good disk releases. Never use a
hidden per-instance install as automatic rollback.

### Managed versus mutable paths

Use a **default-owned release with explicit mutable exceptions**, not a list
of interesting large files. A fresh staging install tells the manager every
path Steam owns. The policy then classifies:

- `WS/Saved/**`: per-instance mutable, forbidden to the updater/publisher
- `Steam/**`, `.steam/**`, `.config/**`: per-instance runtime state unless
  later evidence proves a subpath belongs in the release
- `steamcmd/**`: updater-private, outside the served release
- depot content such as `Engine/**`, `WS/Binaries/**`, `WS/Config/**`, and
  `WS/Content/**`: release-owned and read-only
- custom artifacts such as `ksm-optin.so`: host-managed, versioned separately
  or installed into the runtime image; never silently inherited from one
  instance

The exact first classification must be generated and reviewed from a clean
install. The important policy is mechanical: if a later update introduces a
path outside all known classes, promotion fails with the path named. This is
how "all update modifications are mapped" becomes an enforceable invariant
rather than documentation that can drift.

If mounting the whole release root with nested per-instance state mounts is
too disruptive to Wings SFTP, backup, and permission handling, publish a
small set of release-owned **root directories** instead. The generated
manifest must prove that every release-owned file is beneath one of those
mount roots. New uncovered roots block promotion. This is less elegant than a
whole-root read-only mount but substantially easier to introduce without
changing Wings' filesystem abstraction.

Every consumer bind must be read-only and verified as read-only. The publisher
can temporarily mount from either:

- `releases/<generation>/root/...` for the persistent disk backend, or
- `/mnt/soulmask-content/<generation>/...` for the tmpfs backend.

Both represent the same manifest and generation. A tmpfs failure may therefore
fall back to the canonical disk source without version skew.

### Update and start state machine

The content manager should persist an explicit state machine rather than infer
success from path existence:

```text
IDLE(current=LKG)
  -> CHECKING
  -> STAGING(txn)
  -> VALIDATING(txn)
  -> RELEASE_READY(new)
  -> PUBLISHING(new, all consumers offline)
  -> CURRENT(new)
  -> VERIFIED_RUNNING(new)

Any failure before CURRENT: delete/quarantine txn, keep LKG.
Any failure during publication: restore/rebuild LKG mounts, then either use LKG
or block start if LKG cannot be attested.
Any failed post-start health gate: coordinated stop and select previous release.
```

#### Ordinary MAIN start with no update

1. Wings syncs server configuration and removes any old MAIN container.
2. Wings invokes `soulmask-content-prepare@<MAIN-uuid>.service` as a required
   start hook.
3. The manager validates the UUID against root-owned cluster config, takes
   `flock` for that cluster, and checks observed Docker consumers.
4. It verifies the selected disk release, current publication backend, every
   host bind source/flag, free tmpfs headroom, and state tripwires.
5. It writes an atomic result for this UUID containing generation, build ID,
   backend, and `safe_to_start=true`, then exits zero.
6. Wings reads and validates that result, creates the container, labels it with
   the generation, and supplies the expected generation to a tiny entrypoint
   guard.
7. The guard checks a read-only release sentinel before execing WSServer.
8. MAIN reaches the configured registration/steady line. Patch 0010 starts
   CLIENT.
9. CLIENT's required prepare hook takes the same lock, verifies MAIN is ready
   on the same generation and its own mounts match, then permits creation.

#### MAIN start with an available update

1. The manager requires every configured cluster consumer to be offline. A
   running CLIENT produces `cluster busy: stop CLIENT before updating`, not an
   automatic stop.
2. SteamCMD runs as an unprivileged updater against a new persistent
   transaction directory, never an instance volume and never tmpfs. Use full
   validation for promotion.
3. The manager checks Steam's success status/build ID, required executables,
   non-empty pak assets, path classification, file manifest, disk space, and
   hashes. It fsyncs and atomically renames the transaction to a release.
4. In disk-backend mode, it switches selection and reconciles read-only binds.
5. In tmpfs mode, all consumers are already absent, so low-memory publication
   can discard the old RAM generation before copying the new one. This avoids
   a 2x RAM peak. If copying fails, rebuild the previous generation or use the
   previous release directly from disk.
6. The manager verifies every target and writes `CURRENT` only after all
   targets match. Sequential mount changes are acceptable because the cluster
   lock plus zero consumers provides isolation.
7. MAIN starts with `AUTO_UPDATE=0`; CLIENT starts only after MAIN is steady.
8. The release becomes `VERIFIED_RUNNING` only after both expected readiness
   gates succeed. Retention/GC starts after an additional soak period.

#### Steam/network failure

Network failure is not the same as filesystem uncertainty. If the current
known-good release and mounts attest correctly, report a degraded update check
and start that release. Record the event visibly. If no verified release can be
presented, refuse the start.

Use a configurable update-check TTL so a crash loop does not repeatedly hit
Steam. An explicit maintenance action must be able to force a check.

#### Host reboot

Persistent release metadata is the authority. A boot unit may eagerly rebuild
the selected tmpfs before Docker, but the per-start hook must still verify it.
Boot ordering alone is not a correctness gate. If eager reconstruction fails,
the first start can use the same-generation disk backend or fail safely.

#### Rollback

Rollback is selection of an older complete release followed by publication,
not restoration from an instance's hidden files. It uses the same lock,
offline check, bind verification, and start attestation as an update. The
manager never rolls `WS/Saved` backward as part of a binary rollback. Whether
Soulmask save schemas are backward compatible is a separate product decision;
if not proven, a binary rollback after the new build has written saves must
require an explicit matching save backup selection.

### State-data protection

The strongest protection is structural: the updater's filesystem access never
includes instance volume roots. The privileged publisher only receives fixed
mount targets derived from root-owned UUID configuration and refuses any path
that resolves into `WS/Saved` as a source.

Before initial migration and each explicit cluster update:

1. stop CLIENT first;
2. issue the existing safe MAIN save sequence while MAIN is still running;
3. stop MAIN cleanly;
4. create a recoverable backup/snapshot of each entire `WS/Saved/` tree;
5. record hashes plus inode/size/mtime for every `world.db` and
   `GameXishu.json` as transaction tripwires;
6. verify those tripwires again before allowing MAIN to start.

The content transaction should abort and alert if a protected file changes
during preparation. It must never attempt to "repair" that change itself.

The initial migration needs a separately reviewed rollback procedure because
Pterodactyl reinstall can affect the entire server volume. A routine content
update must not call the Panel reinstall path.

## Wings changes

### Supersede, do not merely extend, the patch 0011 contract

Keep the existing D-Bus connection and job-result handling, but introduce a
node-controlled required pre-start hook with these semantics:

1. Invoke it only from the real `OnBeforeStart` flow, after the old container
   is removed and before the new container is created. Do not invoke it from
   the boot-time `CreateEnvironment()` pass for servers that remain offline,
   installs, transfers, or generic environment reconciliation.
2. Derive the unit from node configuration and server UUID. Do not accept an
   arbitrary unit name or path from an egg variable. A template such as
   `soulmask-content-prepare@<validated-uuid>.service` is sufficient.
3. Support `required` and `best_effort` policies. Soulmask content preparation
   is required. Existing cgroup slice setup may remain best-effort because its
   failure affects resource policy, not content identity.
4. Make timeout node-configurable and large enough for a full download and
   validation. Timeout/failure of a required hook returns an error from
   `OnBeforeStart` and leaves the server offline.
5. Read a root-owned structured result and require a fresh request ID, matching
   server UUID, generation, and success state. A D-Bus job result of `done`
   only proves the process exited zero; it does not prove what was mounted.
6. Add generation/build labels to the Docker container and an expected
   generation for the entrypoint sentinel guard.
7. Publish start/update phase events to the Panel activity stream with
   generation, build, backend, fallback reason, duration, and transaction ID.

Illustrative node configuration:

```yaml
docker:
  pre_start_hooks:
    - name: soulmask-content
      server_ids:
        - b87c0a5b-2387-4a1c-8863-ff23e6800a1d
        - 6c418fe7-9be1-4971-87ec-529f6e909f89
      unit_template: soulmask-content-prepare@{server_uuid}.service
      policy: required
      timeout: 20m
      result_directory: /run/wings/pre-start-results
```

This list lives only in `/etc/pterodactyl/config.yml`. If a panel variable is
kept for portability, it should name a symbolic hook such as
`soulmask-content`, and Wings must intersect it with the node mapping above.
The panel must never select the unit or its failure policy.

Patch 0011 can remain as a generic best-effort unit trigger for unrelated
optimizations if its documentation is corrected. It should not be configured
for these content mounts once the required hook exists.

### Strengthen child readiness

Patch 0010's start-after-steady behavior should be retained, with two additions:

- When a configured CLIENT is manually started, refuse unless its MAIN is in
  the steady state and has the same content-generation label. "Running" alone
  is too early.
- Clear MAIN's steady token on stop, crash, or new start attempt. CLIENT
  deferral after Wings reboot should depend on a newly observed steady event,
  as patch 0010 already intends.

Do not use the startup-grace backstop to start CLIENT. A timeout is not
readiness.

### Egg changes

- Set `AUTO_UPDATE=0` for both managed servers and describe the host content
  manager as authoritative. Prefer making the variable admin-only or removing
  it from this managed egg variant so a user cannot re-enable in-container
  updates.
- `VALIDATE` no longer controls the game container; validation is mandatory in
  the host updater.
- Keep the MAIN `WINGS_CG_CHILD_SERVERS` relationship and the explicit
  registration `WINGS_CG_STEADY_MATCH`.
- Remove `WINGS_CG_RAMDISK_UNITS` from these servers once the node-controlled
  hook exists.
- Add the generation guard before the game command. It must fail with a clear
  console message if the sentinel is missing/mismatched or a managed mount is
  writable.

## Host content-manager design

### Trust boundary

The public-facing chain is Panel -> Wings -> fixed prepare unit -> root-owned
manager configuration. Treat Panel data as untrusted even when a variable is
marked admin-only.

Minimum safeguards:

- accept only a canonical UUID argument and look it up in root-owned config;
- derive every source and target path internally, then verify `realpath` stays
  beneath fixed roots;
- never use shell evaluation or interpolate panel values into commands;
- use an exact systemd unit template/policy configured on the node;
- root-own manager config, release metadata, and result directories;
- lock with `flock` on a root-owned file and make the transaction state
  crash-recoverable;
- run SteamCMD/download/hash work as an unprivileged content user with write
  access only to staging;
- keep the privileged publisher networkless if practical and grant only the
  capabilities it needs for bind mounts/ownership;
- use systemd hardening compatible with mount work and log every privileged
  operation with transaction and generation IDs;
- never accept a source path, target path, mount option, command, or release
  selector over the Wings-facing interface.

Wings already has the Docker socket and currently talks to the host system bus,
so compromise of Wings is already close to host-root impact. The allow-list is
still important: it prevents a compromised/misconfigured Panel payload from
turning a normal server variable into an arbitrary systemd operation. It is not
a sandbox for a fully compromised Wings process.

### Reconciliation, not fragile reference counts

Do not store an integer mount reference count and trust it across crashes.
Reconcile desired users from root-owned cluster membership plus observed
containers/mounts. A release is garbage-collectable only when:

- it is neither current nor previous/LKG;
- no configured server result/container label names it;
- no host bind source points into it;
- no Docker consumer for the cluster exists on it; and
- its soak/retention window has elapsed.

Because Docker uses private mount propagation, never garbage-collect a tmpfs
generation merely because the host bind target was detached. Require the
associated containers to be removed first.

### Observability

Every preparation should produce one machine-readable record and concise
journal/Panel events:

```json
{
  "request_id": "...",
  "server_uuid": "b87c0a5b-2387-4a1c-8863-ff23e6800a1d",
  "cluster": "soulmask-prod",
  "generation": "24123343-...",
  "build_id": "24123343",
  "backend": "disk|tmpfs",
  "update": "not-due|unchanged|promoted|failed-using-lkg",
  "mounts_verified": 4,
  "protected_state_unchanged": true,
  "safe_to_start": true
}
```

Expose a read-only status command that reports desired/current generation,
backend, mount sources/flags, consumers, last update check, last failure,
tmpfs usage/headroom, and retained releases. The operator should not need to
read shell state files manually.

## Failure policy

| Condition | Action |
|---|---|
| Steam unreachable, current release verifies | Start current LKG; warn visibly |
| New update staging or validation fails | Quarantine/delete transaction; keep LKG |
| New path has no classification | Block promotion; list paths; keep LKG |
| tmpfs unavailable or ENOSPC, disk release verifies | Bind the same generation from disk if policy permits; warn |
| Any target sees private/stale data or wrong generation | Refuse start |
| Any managed mount is writable | Refuse start |
| CLIENT running when promotion is requested | Refuse update; instruct explicit cluster stop |
| CLIENT requested while MAIN not newly steady | Refuse/defer CLIENT |
| Protected-state tripwire changes during prepare | Abort and alert; do not modify it |
| Crash during staging | Ignore incomplete transaction; current remains LKG |
| Crash during offline mount publication | On recovery reconcile all targets to recorded current/LKG before any start |
| New build starts MAIN but fails health gate | Stop cluster; select previous release; require save-schema decision if saves were written |

"Fail open" should mean "serve an already verified canonical LKG"—never
"expose whatever happens to be underneath the bind."

## Migration plan

No step below needs to mutate or relocate `WS/Saved`.

### Phase 0 — immediate safety before implementation

- Do not perform an in-container Steam update while either current writable
  tmpfs share is active.
- Do not use `systemctl restart` on the current ramdisk units while a consumer
  container exists.
- Treat patch 0011 as experimental and leave `WINGS_CG_RAMDISK_UNITS` empty for
  production content until its lifecycle contract is replaced.
- Keep MAIN and CLIENT on the same known build and preserve verified backups of
  both `WS/Saved` trees.

### Phase 1 — disk-backed versioned release

1. Implement the content manager, manifest/classification, lock, status, and
   dry-run modes without any mount changes.
2. Add fixture tests using fake Steam trees, including new paths, deletions,
   interrupted copies, bad hashes, and state-path escape attempts.
3. During a maintenance window, save and stop CLIENT then MAIN; back up and hash
   protected state.
4. Create a fresh canonical release with SteamCMD on persistent disk and verify
   it against the build currently in use.
5. Bind release-owned roots read-only from disk into both stopped instance
   volumes. Verify every source, option, generation sentinel, and state tripwire.
6. Start MAIN, wait for real registration, then CLIENT. Exercise login/transfer,
   RCON save, backup, SFTP/file manager behavior, and Wings backup behavior.
7. Reboot and repeat the same oracles before considering the migration stable.

Rollback for this phase is explicit rebinding to the retained previous release,
not simply unmounting and revealing private copies.

### Phase 2 — central updates and Wings required hook

1. Add the start-only required-hook semantics and integration tests to both
   patch stacks.
2. Set `AUTO_UPDATE=0` in both servers only when the host updater is ready.
3. Rehearse no-update, real-update, network-failure, validation-failure,
   timeout, concurrent-start, and CLIENT-already-running cases.
4. Confirm activity events and operator-facing errors identify the exact
   transaction/build and recovery action.
5. Retain two disk releases and prove rollback before enabling unattended
   update checks.

### Phase 3 — optional tmpfs backend

1. Measure disk-backed shared page cache and refault latency under the same
   pressure workload used in `MEASUREMENTS.md`.
2. Add tmpfs creation from a verified release only; never from an instance.
3. Exercise ENOSPC and interrupted-copy recovery. Use the low-memory offline
   replacement path rather than holding old and new 2–3 GiB tmpfs copies at
   once.
4. Verify disk fallback uses the same generation and that MAIN/CLIENT labels
   remain equal.
5. Calibrate cgroup charge ownership, `memory.min`/`memory.low`, zswap, and
   writeback separately for incompressible pak data and compressible binaries.

After this phase, retire the old pak/static setup and teardown services. Keep a
read-only diagnostic/migration command, not two independent lifecycle owners.

## Acceptance oracles

These assert behavior rather than implementation details.

1. **State non-interference:** while the game is stopped, before/after hashes
   of every `world.db` and `GameXishu.json` are identical across a no-update
   prepare, update staging, publication, failed publication, and binary
   rollback. Natural game writes after a server has run are tested separately.
2. **Power-loss staging:** kill the updater at random points. After restart,
   only the old complete release is selectable and both servers can start it.
3. **Power-loss publication:** kill the publisher between mount operations.
   Recovery converges every target to one recorded generation before Wings can
   start either server.
4. **No volatile authority:** modify a test tmpfs generation, reboot, and prove
   it is reconstructed from the immutable disk release rather than copied back.
5. **Unknown path:** introduce a new updater-created top-level path. Promotion
   fails and names it until policy classifies it.
6. **Read-only enforcement:** writes/renames/unlinks through both containers to
   every managed root fail with `EROFS`; writes to each instance's `WS/Saved`
   succeed and do not appear in the sibling.
7. **Generation equality:** MAIN and CLIENT entrypoint sentinels, Docker labels,
   and release hashes agree on every start, including host reboot restore.
8. **Child ordering:** CLIENT cannot start manually or at Wings boot before a
   newly observed MAIN steady event for the same generation.
9. **Safe fallback:** simulate Steam outage and tmpfs ENOSPC. The verified LKG
   starts from canonical disk; no private volume install becomes visible.
10. **Busy cluster:** request an update while CLIENT runs. Nothing is stopped or
    changed; the update is refused with the offending UUID.
11. **Concurrency:** race MAIN and CLIENT start requests. One cluster lock and
    one generation win; both either use it or remain offline.
12. **Mount identity:** a foreign/stale mount at any target is detected even
    though `mountpoint -q` succeeds.
13. **Capacity:** publication accounts for payload, filesystem overhead, and
    configured headroom before replacing the old RAM generation.
14. **Operational surfaces:** Panel console/activity, journald, and status JSON
    all name request ID, build, generation, backend, and fallback/failure.
15. **Gate:** run Wings unit/integration/systemd tests in `tester-unified`, not
    the devcontainer, plus a disposable two-container host-namespace lifecycle
    test covering the oracles above. Green unit tests without real systemd,
    Docker `rprivate` mounts, cgroup identity, and a full uid/group/HOME/XDG
    identity are not a ship signal.

## Open product decisions

The design is safe with conservative defaults, but these choices should be
made explicitly before implementation:

1. **Update trigger:** check on every MAIN start with a TTL, a scheduled
   maintenance timer, or an explicit admin action. Recommended: MAIN-start
   check with a short TTL plus an explicit force-update action.
2. **Disk fallback:** whether tmpfs failure may start the same verified release
   from disk. Recommended: yes, with a prominent degraded event; correctness is
   preserved and only latency protection changes.
3. **Busy sibling policy:** automatically stop CLIENT or refuse the update.
   Recommended: refuse ordinary starts; provide a separate explicit
   save/stop/update/start cluster action.
4. **Whole-root versus managed-root mounts:** whole-root is future-proof but
   requires deeper Wings SFTP/backup/chown changes. Recommended first delivery:
   generated/verified managed roots, then consider whole-root once those Wings
   surfaces are covered.
5. **Binary rollback after save writes:** Soulmask save-schema compatibility is
   unproven. Recommended: require explicit operator confirmation and a matching
   state backup if the new generation ran long enough to write saves.
6. **Oneshot versus broker:** recommended oneshot first. Move to a Unix-socket
   broker only if structured live progress, cancellation, or multi-game reuse
   justifies the extra API and daemon lifecycle.

## Bottom line

The architectural boundary should be:

```text
SteamCMD -> persistent staged release -> validation/atomic selection
                                      -> read-only shared disk publication
                                      -> optional read-only tmpfs publication

Wings start -> required host preparation/attestation -> MAIN
                                                    -> steady event -> CLIENT

WS/Saved -------------------------------------------------> per-instance disk
                    (never enters the content pipeline)
```

This removes manual per-instance ramdisk work without putting mount syscalls in
Wings, makes updates durable before they become visible, converts missing/wrong
mounts from silent degradation into detectable start failures, preserves a
known-good rollback path, and keeps the RAM optimization replaceable. Most
importantly, it makes `world.db` and `GameXishu.json` structurally unreachable
from the update/publish path instead of relying on operator discipline.
