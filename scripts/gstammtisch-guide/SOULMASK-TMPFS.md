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

### Read-only enforcement

After populating the tmpfs and setting container-user ownership, the setup
script **remounts the tmpfs read-only** at the VFS level:

```bash
mount -o remount,ro /mnt/soulmask_tmpfs
```

Any write attempt by steamcmd or the game produces an immediate `EROFS`
(Read-only file system) error — **no partial writes, no silent corruption**.
The failure is clean, deterministic, and visible in the container log.

### Full coverage — nothing reaches disk

The paths config (`soulmask_tmpfs-paths.conf`) now covers **everything** Steam
touches during an update:

| Content type | Paths | Slice |
|---|---|---|
| Incompressible | `WS/Content/Paks` | `soulmask_tmpfs-ZSwapMax0` |
| Compressible | `Engine`, `WS/Binaries`, `linux64`, `Steam/*` (except logs/appcache), `steamclient.so`, `libsteamwebrtc.so`, `steamapps`, `steamcmd`, `Manifest_*.txt` | `soulmask_tmpfs-ZSwapMax1` |

Deliberately **excluded** (stay on disk):
- `WS/Saved/` — world save, account database, per-instance state
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
for tmpfs population.

### "FATAL: multiple instances have ROLE=main"
Only one instance may be ROLE=main. Check all instance configs.

### "ERROR: total payload >= tmpfs size"
The combined content is larger than the tmpfs. Raise `SOULMASK_TMPFS_SIZE`
(in the service environment or via `systemctl set-environment`) and restart
the service.

### Container sees "Read-only file system" errors
This is **by design**. Steam/game updates are blocked. Use the manual update
procedure.

### Legacy mounts still present after migration
The teardown script has a legacy cleanup path. Run:
```bash
systemctl stop soulmask_tmpfs.service
systemctl start soulmask_tmpfs.service
```
This stops (cleaning up legacy mounts) and restarts (creating fresh unified
mounts).
