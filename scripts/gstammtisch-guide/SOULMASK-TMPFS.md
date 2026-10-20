# SOULMASK-TMPFS.md — Unified read-only tmpfs ramdisk

*2026-07-29 — replaces the legacy split ramdisks (soulmask-pak-ramdisk.service,
soulmask-static-ramdisk.service). See `files/legacy/` for the old files.*

## Why this exists

Two Soulmask dedicated-server containers run on this host. They share the same
Linux depot (appid 3017300, depot 3017301 — byte-identical across instances,
verified). Without a ramdisk, each container's kernel page cache holds
**separate copies** of the same ~2.6G of install data because Linux page cache
is keyed per-inode, not per-content.

A shared tmpfs bind-mounted into every container makes the page cache genuinely
shared. And because tmpfs pages are **anonymous-backed** (shmem), the kernel
cannot silently evict them under memory pressure — they must go through zswap
(compressed RAM) before reaching disk swap. This prevents multi-second game
stalls when the host is under memory pressure from other workloads.

## Shortcomings of the legacy split-ramdisk design

The original design (2026-07-07, extended 2026-07-21) had two independent
services, two tmpfs mounts, two sets of scripts, two cgroup slices, and two
config keys per instance (`PAK_RAMDISK` + `STATIC_RAMDISK`). It suffered from
three problems:

### 1. Partial coverage — steam updates could partially corrupt state

The legacy ramdisks covered the `.pak`/`.sig`/`.utoc`/`.ucas` files (pak
ramdisk) and `Engine/`, `WS/Binaries/`, `linux64/`, `Steam/`, `steamclient.so`,
`libsteamwebrtc.so` (static ramdisk). But **`steamapps/` (download staging +
app manifests), `steamcmd/` (the updater itself), and `Manifest_*.txt` (depot
metadata) were on disk**, not on any ramdisk.

This meant a steam update could:
1. Download to `steamapps/downloading/` on disk (succeeds)
2. Delete the old `.pak` from the pak tmpfs (succeeds — tmpfs was writable)
3. Write the new `.sig` to the pak tmpfs (succeeds — small file)
4. **Fail to write the new `.pak`** — tmpfs space exhausted because the
   **running other instance still holds the deleted old `.pak` open** (Unix
   semantics: deleted files persist until last fd closes, still consuming
   space)

Result: `.pak` file **gone** from the shared tmpfs, `.sig` updated
(mismatched), running instance still works (mmap'd the old inode), but any
**restarting instance cannot start** because there's no `.pak` to mmap.

**This is exactly what happened on 2026-07-29.** The steam content log shows
`"Disk write failure"` at commit time — steam downloaded 1.65 GiB to staging,
tried to move it to the tmpfs-backed `WS/Content/Paks/`, and failed because
the 3G tmpfs had only 1.4G free (the deleted old `.pak` held by the running
b87c0a5b process consumed 1.67G). The new `.sig` (110K) was small enough to
write, so the tmpfs ended up with a new `.sig` and NO `.pak`.

### 2. Writable tmpfs — no fail-safe

The legacy tmpfs mounts were **read-write**. Steam inside the container could
write through the bind mounts. The designers expected this (SOULMASK.md noted
"steamcmd updates are the only thing that legitimately changes them") but
didn't account for the **partial-write failure mode**: when the write of the
large `.pak` fails but the small `.sig` succeeds, the system ends up in an
**inconsistent state** that the running instance tolerates (old inode held
open) but a restarting instance cannot survive (file missing).

### 3. No guaranteed source instance

Both legacy setup scripts enumerated instances in filesystem order and picked
the **first** one that had the required files. There was no guarantee that the
first instance was the most recently updated (ROLE=main) one. If a client
instance was enumerated first and had stale files, the ramdisk would be
populated from stale content.

### 4. Two services, two scripts, duplicated logic

The pak ramdisk and static ramdisk were deliberately kept separate (different
cgroup slices for independent memory control). But they shared ~80% of their
logic: instance enumeration, config loading, size checking, bind-mounting,
state persistence, teardown. Every fix or improvement had to be applied twice.

## New design — unified read-only tmpfs

### One service, one tmpfs, one config

| Component | Legacy | Unified |
|---|---|---|
| Service | `soulmask-pak-ramdisk.service` + `soulmask-static-ramdisk.service` | `soulmask_tmpfs.service` |
| Tmpfs | `/mnt/soulmask-paks` (3G) + `/mnt/soulmask-static` (1G) | `/mnt/soulmask_tmpfs` (5G) |
| Slice | `soulmask-paks.slice` + `soulmask-static.slice` (children of `soulmask.slice`) | `soulmask_tmpfs.slice` (parent, at cgroup root) + `soulmask_tmpfs-ZSwapMax{0,1}.slice` (children) |
| Setup script | `soulmask-pak-ramdisk-setup.sh` + `soulmask-static-ramdisk-setup.sh` | `soulmask_tmpfs-setup.sh` |
| Teardown | `soulmask-pak-ramdisk-teardown.sh` + `soulmask-static-ramdisk-teardown.sh` | `soulmask_tmpfs-teardown.sh` |
| Toggle | `soulmask-pak-ramdisk-toggle.sh` (pak only) | `soulmask_tmpfs-toggle.sh` |
| Paths config | `static-ramdisk-paths.conf` (static only) | `soulmask_tmpfs-paths.conf` (both types) |
| Instance opt-in | `PAK_RAMDISK=1` + `STATIC_RAMDISK=1` | `TMPFS=1` |
| State file | `/run/soulmask-pak-ramdisk.state` + `/run/soulmask-static-ramdisk.state` | `/run/soulmask_tmpfs.state` |

### Read-only enforcement (default; currently disabled on this host — see below)

After populating the tmpfs and setting container-user ownership, the setup
script **remounts the tmpfs read-only** at the VFS level, unless
`SOULMASK_TMPFS_READONLY=0`:

```bash
mount -o remount,ro /mnt/soulmask_tmpfs
```

Any write attempt by steamcmd or the game produces an immediate `EROFS`
(Read-only file system) error — **no partial writes, no silent corruption**.
The failure is clean, deterministic, and visible in the container log.

**Conflict with Wings, discovered 2026-07-31:** Wings' pre-boot chown walk
(`server/filesystem.Chown`) calls `Lchownat` unconditionally on every file —
there is no "already correct owner, skip" check. A `chown` syscall on a
truly read-only mount always fails `EROFS`, even when the requested
owner already matches (the kernel doesn't special-case a no-op chown). So
**any instance that is both `ROLE=main` and `TMPFS=1`** — i.e. bind-mounted
onto its *own* directory, which is required for it to share pages with
other instances — can never be (re)started via Wings while the tmpfs is
read-only. This is not a staleness/propagation bug; it reproduces even with
a perfectly fresh, correctly-propagated tmpfs.

**Current accepted tradeoff:** rather than waiting on a Wings patch (skip
`Lchownat` when uid/gid already match — a valid fix, not yet built), this
host runs the tmpfs **read-write** (`SOULMASK_TMPFS_READONLY=0`, set via
`Environment=` in `soulmask_tmpfs.service`). This reintroduces the residual
risk the read-only mode exists to prevent: the actual 2026-07-29 corruption
happened because steamcmd deletes the old file *before* the new one
finishes writing (not a lack of atomic `rename()` — steamcmd's own update
mechanics simply aren't safely atomic under space pressure), so a large
enough update could still exhaust tmpfs space mid-write. This is **mitigated,
not eliminated**, by running a much larger headroom margin than the setup
that failed (5G tmpfs vs. ~2.3G payload, vs. the original 3G tmpfs with only
1.4G free). A fully robust fix (stage each update in an isolated tmpfs
generation, verify, then atomically swap each bind target via a stacked
`mount --bind`, leaving already-running instances on their old,
already-mmap'd generation) was scoped but not built — revisit if this
becomes a live problem, or once the Wings patch lands and read-only can be
restored without breaking `ROLE=main` + `TMPFS=1` instances.

To restore read-only enforcement: remove (or set to `1`)
`SOULMASK_TMPFS_READONLY` in `soulmask_tmpfs.service`, `daemon-reload`, and
re-run `--stop`/`--start` — but only once the Wings patch is deployed,
otherwise `ROLE=main` instances that are also `TMPFS=1` will hit the EROFS
chown failure again.

### Full coverage — nothing reaches disk

The paths config (`soulmask_tmpfs-paths.conf`) now covers **everything** Steam
touches during an update:

| Content type | Paths | Slice |
|---|---|---|
| Incompressible | `WS/Content/Paks` | `soulmask_tmpfs-ZSwapMax0` |
| Compressible | `Engine`, `WS/Binaries`, `linux64`, `Steam/*` (except logs/appcache), `steamclient.so`, `libsteamwebrtc.so`, `steamapps`, `steamcmd`, `Manifest_*.txt` | `soulmask_tmpfs-ZSwapMax1` |

Deliberately **excluded** (stay on disk):
- `WS/Saved/` — world save, account database, per-instance state (except
  `WS/Saved/Logs`, a per-instance tmpfs — see "Per-instance tmpfs" below)
- `WS/Config/` — server configuration, per-instance
- `Steam/logs/` — steam runtime logs (written continuously, keep writable)
- `Steam/appcache/` — steam HTTP cache (written continuously)
- `archive-*.tar.gz` — Wings backup tarballs

### ROLE=main is the authoritative source

The setup script finds the instance with `ROLE=main` and uses **only its
volume** as the source for populating the tmpfs. It asserts that exactly one
instance has `ROLE=main` and refuses to start otherwise. This guarantees the
ramdisk always reflects the main server's content, which is the one that gets
updated during the manual update procedure.

### Two cgroup slices, one service

The two content-specific slices live under a shared parent at cgroup root:

```
soulmask_tmpfs.slice (MemoryMin=400M, at cgroup root)
├── soulmask_tmpfs-ZSwapMax0.slice (pak: MemoryMin=150M, MemoryZSwapMax=0)
└── soulmask_tmpfs-ZSwapMax1.slice (binaries: MemoryMin=250M, zswap-eligible)
```

The parent `soulmask_tmpfs.slice` carries `MemoryMin=400M` — this satisfies
both children's floors (150M + 250M). The kernel requires the parent's
MemoryMin >= each child's MemoryMin for the child's floor to be effective.
Unlike the legacy slices (which lived under `soulmask.slice`), the underscore
in `soulmask_tmpfs` keeps it at cgroup root — only `-` acts as a hierarchy
separator in systemd slice unit names.

| Slice | Content | Policy |
|---|---|---|
| `soulmask_tmpfs-ZSwapMax0` | Pak files (incompressible) | `MemoryMin=150M`, `MemoryZSwapMax=0` — bypass zswap, cold pak → disk swap |
| `soulmask_tmpfs-ZSwapMax1` | Binaries, Steam, libs (compressible) | `MemoryMin=250M` — zswap-eligible |

The single service starts in `ZSwapMax1`. The setup script moves its own
process (`echo $$ > .../cgroup.procs`) to `ZSwapMax0` before copying the
incompressible paths, then back to `ZSwapMax1` before copying the compressible
paths. The kernel charges pages to whichever cgroup the allocating process is
in, so each content type's pages end up in the correct slice.

## Per-instance tmpfs (`WS/Saved/Logs`) — SMT-01, 2026-10-20

### Why

The game thread `FAsyncWriter_WS` writes `WS/Saved/Logs/WS.log` and calls
`fdatasync`. Under dev IO load that blocks for up to ~26 s on the shared root
ext4 journal (`jbd2_log_wait_commit`, `folio_wait_writeback`,
`wait_transaction_locked`) and the tick rate drops from 30 to ~7. On a tmpfs the
log touches no ext4 at all. The operator accepts that these logs are lost on
service restart or reboot; nothing is copied back.

### What a per-instance section is

Every other section of `soulmask_tmpfs-paths.conf` is **shared**: the path is
copied from the `ROLE=main` instance into one tmpfs and bind-mounted into every
`TMPFS=1` instance. That is wrong for something the game *writes* — two servers
would write the same `WS.log`. The two new sections are different:

| Section | Mount | Pages |
|---|---|---|
| `[per-instance-compressible]` | own empty rw tmpfs per `TMPFS=1` instance, on `<volume>/<path>` | swappable, zswap-eligible |
| `[per-instance-noswap]` | same, plus the tmpfs `noswap` option | resident, uncompressed |

Nothing is copied in or back; the shared `/mnt/soulmask_tmpfs` is not involved.
Shipped: `WS/Saved/Logs` under `[per-instance-compressible]`, nothing under
`[per-instance-noswap]`.

Mount options are **derived, never literals**: `uid`/`gid`/`mode` come from
`stat` of the existing on-disk directory (988:988, 0755 today); `size` comes
from `SOULMASK_TMPFS_PER_INSTANCE_SIZE` (set in `soulmask_tmpfs.service`, 256M;
the script has no default and refuses without it); always
`nosuid,nodev,noexec`. A directory that does not exist yet is skipped with a log
line, never created (its ownership would be a guess). The mount source is
tagged `soulmask-pi-<first 8 hex of the uuid>`.

Size syntax: a positive integer with an optional `k`/`m`/`g` suffix. `0` (which
tmpfs treats as **unlimited**) and percentages (relative to RAM) are refused, as
is anything malformed.

### Containment: a symlink can never redirect the mount

`<volume>/WS/Saved/Logs` lies in a tree the game (uid 988 in the container) can
write. A planted symlink there must not make a ROOT mount land on a host
directory (`/etc/gstammtisch`, another instance's volume, ...). Setup therefore
refuses a target when the volume itself or **any component** of
`<volume>/<path>` is a symlink (even one that stays inside the volume), or when
the canonical path leaves the canonical volume, or the volume leaves
`VOLUMES_ROOT`. The check runs while planning and **again immediately before each
mount** (the tree can change in between; the remaining window is the few
microseconds between that re-check and the `mount` syscall, which cannot be
closed from shell). A refusal is an error line plus a non-zero exit; the other
instance's mount still proceeds. Paths must also be relative, without `..`,
whitespace, `%`, `|` or glob characters, and must not repeat, nest, or overlap a
shared path.

### Memory accounting — what "compressible" means here

tmpfs pages are charged to the memcg of the task that **writes** them. For
these mounts that is the game container's cgroup
(`wings.slice/docker-<id>.scope`), **not** the `soulmask_tmpfs-ZSwapMax{0,1}`
slices — the setup script's slice placement only governs the shared tmpfs it
copies into itself, and does not apply to these pages. Consequences:

- `[per-instance-compressible]` pages are swappable and so zswap-eligible under
  the game cgroup's own `memory.zswap.max` (`max` on this host today).
- `[per-instance-noswap]` pages stay resident and uncompressed. If the kernel
  rejects `noswap` the script **fails**; it never falls back to a swappable
  mount (the section's whole claim would then be false).
- The size cap counts against the game cgroup's `memory.max` (12 GiB, ~4.5 GiB
  in use) as it fills; two instances can each fill their own 256M.
- When the container exits, its memcg's tmpfs pages are reparented to
  `wings.slice` and stay resident until the tmpfs is unmounted or the host
  restarts; they are not freed by stopping the game.

### Existing on-disk logs

They are not moved or deleted. They are *hidden* under the mount and reappear
after unmount. Setup logs a `NOTICE` with the hidden size (78 MB on
`b87c0a5b…` when this was written). They still count towards the volume's disk
usage in Wings but are invisible in the file manager while the tmpfs is
mounted. To inspect or remove them, bind the volume **non-recursively** (a plain
`--bind` does not carry submounts, so it shows the underlying disk):

```bash
mkdir -p /mnt/probe && mount --bind /var/lib/pterodactyl/volumes/<uuid> /mnt/probe
ls -la /mnt/probe/WS/Saved/Logs        # the hidden on-disk logs
rm -f /mnt/probe/WS/Saved/Logs/WS-backup-*.log   # only if you want them gone
umount /mnt/probe
```

### Bounded size: one deterministic pruning mechanism

UE renames `WS.log` to `WS-backup-<ts>.log` at every server start, and the
tmpfs lives until restart or reboot, so backups would pile up (a full tmpfs
gives the game `ENOSPC`, exactly when the logs matter most). A log is about
66 MB, so the 256M cap holds only a few of them.

`soulmask_logs-prune.sh` is the **only** pruning mechanism. It runs once at the
end of setup and every 10 minutes from `soulmask-logs-prune.timer`
(`OnBootSec=5min`, `OnUnitActiveSec=10min`; enable it with
`systemctl enable --now soulmask-logs-prune.timer`):

1. It acts only on `PI_TARGET=` mounts from the state file that are, right now,
   a `tmpfs` whose source is a `soulmask-pi-` tag. If the tmpfs is not mounted
   it does nothing (it never prunes the on-disk directory underneath).
2. A **backup** is exactly: a regular file (not a symlink, not a directory) in
   the top level of the mount, named `WS-backup-<[A-Za-z0-9._-]+>.log`. Nothing
   else is ever deleted. The live `WS.log` does not match and is never touched.
3. It keeps the newest `SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS` (by mtime),
   deletes the rest, then deletes the oldest remaining backups while usage is
   above `SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT` percent of the tmpfs.
4. If it is still above the mark with no backup left, the live log alone is too
   large: it logs an error and exits 1 (raise `SOULMASK_TMPFS_PER_INSTANCE_SIZE`).

Both values come from the unit Environment (shipped: `KEEP_BACKUPS=1`,
`HIGH_WATER_PCT=75`), identical in `soulmask_tmpfs.service` and
`soulmask-logs-prune.service` (a test asserts it). The script has no defaults and
refuses (exit 2) unless `KEEP_BACKUPS` is an integer >= 0 and `HIGH_WATER_PCT` is
an integer in 1..99.

Arithmetic with ~66 MB logs and a 256M cap: steady state is the live log plus
one backup, about 132 MB (52%); right after a restart, before the next prune pass,
the old live log has become a second backup, about 140 MB (55%), still under the
75% mark (192 MB) with room for the live log to grow.

**Why not a systemd-tmpfiles age rule (the first version):** it cannot bound
size — three same-day restarts already exceed 256M, and
`systemd-tmpfiles-clean.timer` (measured on this host, 2026-10-20: static and
active, 15 min after boot then every 1 d) only prunes daily and only by age. It
also needs an `X` exemption to keep an idle live log (measured with
`systemd-tmpfiles --clean`, systemd 257: an `e` age rule deletes an idle
`WS.log` too, and `0` wipes everything), and a malformed age silently disables
the whole rule. Two mechanisms would mean two policies that can disagree, so
there is one.

### Failure policy: per-instance problems never block the shared tmpfs

The game needs the shared paks to start at all, whereas without the log tmpfs
its logs simply stay on the disk. So setup treats a per-instance problem (bad
configuration, a symlinked or foreign-mounted target, a failed mount) as an
**error line plus a non-zero exit at the end** — it finishes the shared tmpfs
first. The reverse also holds: if the shared part fails, the per-instance mounts
are still applied (so the logs do not silently stay on ext4). The unit shows
`failed` either way, which is the signal to read the journal. If a per-instance
mount fails halfway, every per-instance mount made in that run is rolled back
(mounts kept from an earlier run are left alone).

### Idempotence and the state file

- Re-running setup **keeps** a target that is already ours: fstype `tmpfs`,
  source `soulmask-pi-<uuid8>`, **recorded in the state file** with its derived
  uid/gid/mode, and carrying exactly the options this run would have mounted
  (`rw`, `nosuid,nodev,noexec`, `size=`, `uid=`, `gid=`, `mode=`, `noswap` iff the
  section says so). A mount with our tag but stale options (e.g. the unit's SIZE
  changed) or not recorded is refused with the remedy (teardown and re-run).
  Any other mount on a target — including an unrelated tmpfs — is refused;
  mounts are never stacked.
- The state file records `PI_TARGET=<path>` and `PI_DERIVED=<path> <uid> <gid>
  <mode> <uuid>` (distinct from the shared `TARGET=` lines). Writing it merges
  the earlier entries that are still mounted, so an instance flipped to
  `TMPFS=0` or a path removed from the conf never orphans a live mount.
- `DRY_RUN=1` (or `--dry-run`) prints every mount, unmount and prune action for
  both scripts without executing it.

### Teardown: a successful umount does not prove the game stopped

The game container holds its **own copy** of the mount in its mount namespace.
A host-side `umount` therefore succeeds while the game runs, and the game then
keeps writing logs into an orphaned tmpfs nobody can see. Teardown therefore
checks, for each per-instance target, whether the instance's container
(`docker ps` filtered by the exact name = uuid) is running, and **refuses that
target** (error, non-zero exit, target stays recorded) before any `umount`. If
docker is unreachable while `docker.service` is active it also refuses
("cannot determine"); only a stopped daemon counts as no container (shutdown).
A target whose instance is not recorded is refused too. Children are unmounted
before parents. A target that cannot be unmounted (busy on the host) stays
recorded and makes teardown exit non-zero.

Note `systemctl restart soulmask_tmpfs.service` / `stop` run this teardown, so
they are now guarded for per-instance mounts; the shared bind targets keep their
old behaviour (`soulmask_tmpfs-toggle.sh --stop` additionally refuses while any
Soulmask container runs).

### Tests

`tests/test_soulmask_tmpfs_per_instance.py` runs the real scripts against
PATH-shimmed `mount`/`umount`/`mountpoint`/`findmnt`/`docker`/`df`/`systemctl`/
`stat` and real temp directories and symlinks (`SOULMASK_TMPFS_STATE_FILE` and
`SOULMASK_TMPFS_RAMDISK` redirect the host paths for tests only; the production
values are the defaults). The canaries in `tools/canary-run.sh` prove it goes
red when containment, refusal, rollback-relevant, prune-safety or
container-check logic is broken.

### Deployment (controller)

1. Confirm no players are online on either instance.
2. Stop both game servers (`6c418fe7…`, `b87c0a5b…`) — teardown refuses while
   they run.
3. Install the files to the host
   (`/usr/local/sbin/soulmask_tmpfs-{setup,teardown,toggle}.sh`,
   `/usr/local/sbin/soulmask_logs-prune.sh`, `/usr/local/sbin/soulmask-instance-lib.sh`,
   `/etc/gstammtisch/soulmask_tmpfs-paths.conf`,
   `/etc/systemd/system/{soulmask_tmpfs.service,soulmask-logs-prune.service,soulmask-logs-prune.timer}`),
   then `systemctl daemon-reload` and
   `systemctl enable --now soulmask-logs-prune.timer`.
4. `systemctl restart soulmask_tmpfs.service` (teardown, setup, restart wings;
   it re-copies ~2.6 GB). Read the journal: any `ERROR` line is a per-instance
   problem and the unit will show `failed`.
5. Start the servers.
6. Verify:
   - `findmnt -t tmpfs | grep soulmask-pi-` shows one tmpfs per instance on
     `WS/Saved/Logs`;
   - `WS.log` grows inside it, and `df` on each mount stays well below the
     75% mark after the first restart cycle;
   - the D-state probe shows no ext4 or jbd2 stacks for `FAsyncWriter_WS`;
   - `systemctl list-timers soulmask-logs-prune.timer` and
     `journalctl -u soulmask-logs-prune.service` show a clean pass.

## Manual update procedure

With the tmpfs read-only, game updates **must** follow this procedure:

```
1. Stop ALL Soulmask containers (Wings panel → power → stop)
2. systemctl stop soulmask_tmpfs.service
   → unmounts all bind targets, then the tmpfs
   → containers now see underlying disk files
3. Start the ROLE=main server (b87c0a5b)
   → steamcmd auto-updates: downloads to steamapps/, writes to all paths on DISK
   → game starts, verify it works
4. Stop the main server
5. systemctl start soulmask_tmpfs.service
   → reads paths config
   → finds ROLE=main instance (b87c0a5b) — its volume has the freshly-updated files
   → moves to ZSwapMax0 slice, copies incompressible paths from main's volume into tmpfs
   → moves to ZSwapMax1 slice, copies compressible paths
   → chowns to container user
   → remounts tmpfs read-only
   → bind-mounts every path into every TMPFS=1 instance
   → writes state file
6. Start all containers (Wings panel)
   → main server, then client server
```

This is an **atomic switch**: the tmpfs is populated from the freshly-updated
disk in one shot, then made read-only. Until step 5 completes, all instances
see disk-backed files. After step 5, all instances see the read-only tmpfs.

## Migration from legacy

1. Install new files (systemd units, scripts, configs):
   - `/etc/systemd/system/soulmask_tmpfs.slice` — parent slice (at cgroup root, MemoryMin=400M)
   - `/etc/systemd/system/soulmask_tmpfs-ZSwapMax0.slice` — incompressible content
   - `/etc/systemd/system/soulmask_tmpfs-ZSwapMax1.slice` — compressible content
   - `/etc/systemd/system/soulmask_tmpfs.service` — unified service
   - `/usr/local/sbin/soulmask_tmpfs-setup.sh` — setup
   - `/usr/local/sbin/soulmask_tmpfs-teardown.sh` — teardown
   - `/usr/local/sbin/soulmask_tmpfs-toggle.sh` — toggle
   - `/etc/gstammtisch/soulmask_tmpfs-paths.conf` — paths configuration
   - Updated: `/etc/gstammtisch/instance-defaults.env`, `instances.d/*.env`
   - Updated: `/usr/local/sbin/soulmask-instance-lib.sh`, `soulmask-pak-mempress.sh`
2. Stop both legacy services:
   ```bash
   systemctl stop soulmask-pak-ramdisk.service soulmask-static-ramdisk.service
   systemctl disable soulmask-pak-ramdisk.service soulmask-static-ramdisk.service
   ```
3. Enable and start the unified service:
   ```bash
   systemctl daemon-reload
   systemctl enable --now soulmask_tmpfs.service
   ```
4. The teardown script cleans up legacy state files and mount points
   automatically on first stop. The legacy slice files can be removed after
   confirming the unified service works.

## Tmpfs sizing

- Payload (2026-07-29): ~2.6G (pak 1.7G + binaries/libs 500M + steamcmd 200M + Steam runtime 200M)
- Tmpfs: 5G (configurable via `SOULMASK_TMPFS_SIZE` env)
- Headroom: ~2.4G — unused tmpfs space does not consume RAM (zero pages), so
  a generous cap is free. The headroom exists in case a game update makes the
  payload larger.
- The 5G covers the scenario where a running instance momentarily holds a
  deleted old `.pak` open during the manual update (though with the read-only
  design, this should not happen — updates only occur when the service is
  stopped and the tmpfs is unmounted).

## Verification

```bash
# Check service status
systemctl status soulmask_tmpfs.service

# Verify tmpfs is read-only
findmnt /mnt/soulmask_tmpfs | grep -q '\<ro\>' && echo "ro OK" || echo "NOT ro!"

# List bind mounts
cat /run/soulmask_tmpfs.state

# Check a specific bind mount
findmnt /var/lib/pterodactyl/volumes/<uuid>/WS/Content/Paks

# Dry-run (no changes)
/usr/local/sbin/soulmask_tmpfs-setup.sh --dry-run

# Toggle
soulmask_tmpfs-toggle.sh status
```

## Troubleshooting

### "FATAL: no instance has ROLE=main"
Exactly one instance must have `ROLE=main` in its
`/etc/gstammtisch/instances.d/<uuid>.env`. This is the authoritative source
for tmpfs population. Note this check only considers `TMPFS=1` instances —
an instance with `ROLE=main` but `TMPFS=0` does not count, and setup fails
with this error even though a ROLE=main line clearly exists in some env
file. (Hit 2026-07-31 when `b87c0a5b` was temporarily set `TMPFS=0`.)

### "FATAL: multiple instances have ROLE=main"
Only one instance may be ROLE=main. Check all instance configs.

### "ERROR: total payload >= tmpfs size"
The combined content is larger than the tmpfs. Raise `SOULMASK_TMPFS_SIZE`
(in the service environment or via `systemctl set-environment`) and restart
the service.

### Container sees "Read-only file system" errors
Two different causes, depending on *when* and *what* is writing:

- **Steam/game writing during normal read-only operation**: by design.
  Steam/game updates are blocked. Use the manual update procedure.
- **Wings' pre-boot chown failing** (`failed to chown root server directory
  during pre-boot process: ... lchownat <path>: read-only file system`,
  logged by Wings itself, not the game): this is the ROLE=main +
  self-bind-mount conflict described in "Read-only enforcement" above, not
  a stale/leftover mount — it reproduces even with a perfectly fresh,
  correctly-torn-down-and-repopulated tmpfs. Confirm the tmpfs is actually
  read-write (`soulmask_tmpfs-toggle.sh` status line) before assuming this;
  this host currently runs `SOULMASK_TMPFS_READONLY=0` specifically to
  avoid this failure.

### Legacy mounts still present after migration
The teardown script has a legacy cleanup path. Run:
```bash
systemctl stop soulmask_tmpfs.service
systemctl start soulmask_tmpfs.service
```
This stops (cleaning up legacy mounts) and restarts (creating fresh unified
mounts).
