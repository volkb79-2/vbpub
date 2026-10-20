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

## Why the legacy split-ramdisk design was replaced

The 2026-07-07 design (two services, two tmpfs mounts, `PAK_RAMDISK` + `STATIC_RAMDISK`, files in
`files/legacy/`) had three defects; each is a reason for a decision below:

1. **Partial coverage, writable tmpfs, no fail-safe.** `steamapps/`, `steamcmd/` and `Manifest_*.txt`
   stayed on disk while the paks were on a *writable* tmpfs. On 2026-07-29 a steam update downloaded
   1.65 GiB to disk staging, deleted the old `.pak` from the tmpfs, wrote the small new `.sig`, then
   failed writing the new `.pak` ("Disk write failure"): the other running instance still held the
   deleted old `.pak` open and its 1.67G consumed the 3G tmpfs. Result: new `.sig`, no `.pak`; the running
   instance survived (old inode mmap'd) but a restarting one could not start. Hence: full coverage and
   a read-only design (below). Note: the host now runs it read-write by an accepted trade-off
   (`SOULMASK_TMPFS_READONLY=0` since 2026-07-31, Wings' pre-boot chown; see "Current accepted tradeoff").
2. **No guaranteed source instance** (first instance in filesystem order, possibly stale). Hence `ROLE=main`.
3. **Duplicated logic** in two scripts. Hence one service, one script, one config.

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

Why the stall happens at all (the shared journal, the slow device, what bounds it) is in
[HOST-IO-INTERFERENCE.md](HOST-IO-INTERFERENCE.md); this tmpfs removes only the *log's* share of it.

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
from `SOULMASK_TMPFS_PER_INSTANCE_SIZE` (set in `/etc/gstammtisch/soulmask_tmpfs.env`, 512M;
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
  in use) as it fills; two instances can each fill their own 512M.
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
66 MB, so even the 512M cap holds only a handful of them.

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

The policy has **one source**, the tracked file
`/etc/gstammtisch/soulmask_tmpfs.env` (`files/etc/gstammtisch/soulmask_tmpfs.env`),
read through a mandatory `EnvironmentFile=` by BOTH `soulmask_tmpfs.service` and
`soulmask-logs-prune.service` (no `-` prefix: a missing file fails the unit
loudly; a test asserts both units reference it and carry no copy). Shipped:
`SIZE=512M`, `KEEP_BACKUPS=3`, `HIGH_WATER_PCT=75`. The scripts have no defaults
and refuse (exit 2 for the prune script) unless `KEEP_BACKUPS` is an integer >= 0
and `HIGH_WATER_PCT` an integer in 1..99. After editing the file run
`systemctl restart soulmask_tmpfs.service` (a ~10-minute IO-saturating maintenance window, see "Deployment"; the size is applied at mount time; a
kept mount whose `size=` no longer matches is refused until torn down).

Sizing arithmetic (a log is ~66 MB; the game cgroup has `memory.max` 12 GiB with
~4.5 GiB in use): steady state is the live log plus 3 backups, about 264 MB (52%
of 512M); right after a restart, before the next prune pass, the old live log has
become a 4th backup: about 330 MB (64%), still under the 75% mark (384 MB), and
logs up to ~90 MB still fit. Why 3 backups: each restart renames the live log to
a new backup, and the prune keeps the newest by mtime, so with `KEEP_BACKUPS=1` a
couple of quick restart attempts after a crash would evict the crashed session's
log, the evidence. With 3, the crash log survives up to 3 restarts per 10-minute
prune window; more than that in one window can still evict it, so copy the
evidence out first. Worst case the two tmpfs hold 2 x 512M against the instances'
cgroups (<= 0.5 GiB each, 4.5 + 0.5 of 12 GiB), swappable under
`[per-instance-compressible]`.

**Why not a systemd-tmpfiles age rule (the first version):** it cannot bound
size — a few same-day restarts already exceed the cap, and
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
**error recorded, then exit status 75 (`EX_TEMPFAIL`) at the end** — it finishes
the shared tmpfs first. The reverse also holds: if the shared part fails (exit
1), the per-instance mounts are still applied (so the logs do not silently stay
on ext4). If a per-instance mount fails halfway, every per-instance mount made in
that run is rolled back (mounts kept from an earlier run are left alone).

**Why exit 75 + `SuccessExitStatus=75` and not a second unit.** A plain non-zero
exit would mark the oneshot `failed`: `ExecStartPost` (the wings restart) would be
skipped although the shared binds are up, and `systemctl stop` / toggle-off on a
failed unit does not run `ExecStop`, leaving the mounts in place. With
`SuccessExitStatus=75` the unit stays `active`, wings is restarted and `stop`
tears down normally. A separate `soulmask_tmpfs-per-instance.service` would give
the failure its own unit but needs a second setup/teardown pair, a second state
file and an ordering contract with the shared unit and the wings restart; the
per-instance mounts are made inside the same `ExecStart` precisely so that they
exist before `ExecStartPost` restarts wings and (`Before=docker.service`) before
wings and the game containers start, which is what rslave propagation / the
containers' recursive bind need. The failure is **not swallowed**:

- every error is an `ERROR` line in the journal at priority `err`
  (`journalctl -u soulmask_tmpfs -p err`);
- the count and messages are written to the state file (`PI_ERRORS=n`,
  `PI_ERROR=<message>`);
- `soulmask-logs-prune.service` then **fails on every pass** (10 min) until a
  clean setup run rewrites `PI_ERRORS=0` — `systemctl is-failed
  soulmask-logs-prune.service` and the `mdt` unit checks show it;
- `soulmask_tmpfs-toggle.sh` (status) prints a warning with the messages.

### Idempotence and the state file

- Re-running setup **keeps** a target that is already ours: fstype `tmpfs`,
  source `soulmask-pi-<uuid8>`, **recorded in the state file** with its derived
  uid/gid/mode, and carrying exactly the options this run would have mounted
  (`rw`, `nosuid,nodev,noexec`, `size=`, `uid=`, `gid=`, `mode=`, `noswap` iff the
  section says so). A mount with our tag but stale options (e.g. the unit's SIZE
  changed) or not recorded is refused with the remedy: with the game stopped, run
  `soulmask_tmpfs-teardown.sh` (it also finds tagged mounts that are not in the
  state file) or `umount` it, then re-run.
  Any other mount on a target — including an unrelated tmpfs — is refused;
  mounts are never stacked.
- The state file records `PI_TARGET=<path>` and `PI_DERIVED=<path> <uid> <gid>
  <mode> <uuid>` (distinct from the shared `TARGET=` lines), plus `PI_ERRORS=` /
  `PI_ERROR=`. It is rewritten **after every successful per-instance mount**, so
  a setup killed mid-way leaves no unrecorded mount, and writing it merges the
  earlier entries that are still mounted, so an instance flipped to `TMPFS=0` or
  a path removed from the conf never orphans a live mount.
- `DRY_RUN=1` (or `--dry-run`) prints every mount, unmount and prune action for
  both scripts without executing it.

### Teardown: a successful umount does not prove the game stopped

The game container holds its **own copy** of the mount in its mount namespace.
A host-side `umount` therefore succeeds while the game runs, and the game then
keeps writing logs into an orphaned tmpfs nobody can see. The same holds for the
shared binds. Teardown therefore runs a **preflight before unmounting anything**:
for every instance that still has a mounted per-instance tmpfs or shared bind
target it asks `docker ps` (filtered by the exact name = uuid) whether that
container runs. If one does, the **whole teardown is refused** — exit 1, nothing
unmounted (shared binds and ramdisk included), state file unchanged. If docker
is unreachable while `docker.service` is active it also refuses ("cannot
determine"); only a stopped daemon counts as no container (shutdown). (Residual
NIT: with dockerd stopped under `live-restore` while containers keep running, the
check reads "not running".)

Per-instance tmpfs mounts are found from the state file **and** by their
`soulmask-pi-<uuid8>` mount source (`findmnt`), so a mount left by a setup that
was killed before writing its state is still unwound; its instance is taken from
its path below `VOLUMES_ROOT` and must agree with the tag. A tagged tmpfs that
is neither recorded nor attributable that way (outside `VOLUMES_ROOT`, or a tag
that disagrees with its path) is a **stray**: it is not ours to unmount, so
teardown logs `WARNING: SKIPPING stray tmpfs <path> ...` with the manual command
and carries on with everything else (it does not block the teardown). Unmount a
leftover stray by hand, once its users are stopped: `umount '<path>'`. A
*recorded* target whose instance cannot be determined is still refused (we made
it, so a running game must be ruled out). Children are unmounted before parents.
A target that cannot be unmounted (busy on the host) stays recorded and makes
teardown exit non-zero.

**Recovery after a refused stop.** When teardown refuses because a game is
running, `ExecStop` fails and systemd leaves `soulmask_tmpfs.service` in state
`failed`; a second `systemctl stop` does not run `ExecStop` again (and
`ExecStopPost` has already restarted wings, harmlessly). Once the game is
stopped: load the policy (`set -a; . /etc/gstammtisch/soulmask_tmpfs.env; set +a`) and run
`/usr/local/sbin/soulmask_tmpfs-teardown.sh` directly, then
`systemctl reset-failed soulmask_tmpfs.service`, then `systemctl start
soulmask_tmpfs.service` (or leave it stopped). `systemctl restart` on the failed
unit also works but only starts it again after skipping the stop phase, so the
mounts would be reconciled by setup's kept/refuse logic rather than torn down.

`systemctl restart soulmask_tmpfs.service` / `stop` run this teardown, so both
are guarded; `soulmask_tmpfs-toggle.sh --stop` additionally refuses while any
Soulmask container runs.

### Operator responsibilities and known gaps

Decided 2026-10-20: the operator chose not to engineer these further. They are the operator's to watch,
and are tracked in `KNOWN_ISSUES_TODO_BACKLOG.md` (GG-3).

- **Size and prune policy must fit the real log volume.** `SOULMASK_TMPFS_PER_INSTANCE_SIZE`,
  `..._KEEP_BACKUPS` and `..._HIGH_WATER_PCT` in `/etc/gstammtisch/soulmask_tmpfs.env` rest on a ~66 MB
  log. If the game logs more (verbose logging, a crash loop, a new game version), re-check with `df` on
  the live mounts and re-tune; the prune script reports a live log that alone exceeds the mark and exits
  1, but nothing watches for that on its own (see GG-2 R3-01). Any size mismatch is the operator's call.
- **Other logs are not covered.** Only `WS/Saved/Logs` is on tmpfs. These still write to the root ext4
  and still sit behind the shared journal: Steam logs (wherever steamcmd writes them inside the volume), any
  other file under `WS/Saved` (including the SQLite `world.db`/`account.db`, the largest remaining
  exposure; see [the eatmydata option](../../game_stuff/soulmask/README.md#eatmydata-optional-fsync-removal)),
  and crash dumps (location not verified here). Moving more paths is a separate decision.
- **Logs are lost at restart/reboot** by design (nothing is copied back). A crash log is evidence: copy it
  out before restarting more than `KEEP_BACKUPS` times.

### Tests

`tests/test_soulmask_tmpfs_per_instance.py` runs the real scripts against
PATH-shimmed `mount`/`umount`/`mountpoint`/`findmnt`/`docker`/`df`/`systemctl`/
`stat` and real temp directories and symlinks (`SOULMASK_TMPFS_STATE_FILE` and
`SOULMASK_TMPFS_RAMDISK` redirect the host paths for tests only; the production
values are the defaults). The canaries in `tools/canary-run.sh` prove it goes
red when containment, refusal, rollback-relevant, prune-safety or
container-check logic is broken.

### Deployment (controller)

> **Maintenance window, expect it (measured 2026-10-20, gstammtisch, live SMT-01 deploy).**
> `systemctl restart|start soulmask_tmpfs.service` re-copies ~2.6 GB from disk, then restarts Wings, and
> Wings restores its servers (steamcmd validate reads the installs). On this ~10 MB/s disk that kept `vda` at
> 100 % utilisation for about 10 minutes (reads 6–10 MB/s plus writes 8 MB/s, awaits 0.2–1.5 s, from the
> operator's `iostat`). During that time **every new container start on the host hung**, including other
> servers Wings tried to boot and unrelated `docker run`. Do it in a quiet window and do not start other
> container work until `vda` calms (`iostat -x 5`).

> **Run the scripts directly only with the policy loaded.** The unit gets `/etc/gstammtisch/soulmask_tmpfs.env`
> through `EnvironmentFile=`; a script run by hand does not. The scripts have no defaults, so without it
> they report the three policy values as unset and exit 75 (measured 2026-10-20; the refusal is correct).
> Load it first: `set -a; . /etc/gstammtisch/soulmask_tmpfs.env; set +a; /usr/local/sbin/soulmask_tmpfs-setup.sh --dry-run`.

1. Confirm no players are online on either instance.
2. Stop both game servers (`6c418fe7…`, `b87c0a5b…`) — teardown refuses while
   they run.
3. Install the files to the host, **in this order**: first
   `/etc/gstammtisch/soulmask_tmpfs.env` (the single policy source; both units
   require it, so without it even the shared tmpfs fails to start), then the
   scripts (`/usr/local/sbin/soulmask_tmpfs-{setup,teardown,toggle}.sh`,
   `/usr/local/sbin/soulmask_logs-prune.sh`,
   `/usr/local/sbin/soulmask-instance-lib.sh`) and
   `/etc/gstammtisch/soulmask_tmpfs-paths.conf`, then the units
   (`/etc/systemd/system/{soulmask_tmpfs.service,soulmask-logs-prune.service,soulmask-logs-prune.timer}`),
   and only then `systemctl daemon-reload` and
   `systemctl enable --now soulmask-logs-prune.timer`.
4. `systemctl restart soulmask_tmpfs.service` (teardown, setup, restart wings;
   it re-copies ~2.6 GB; **a ~10-minute IO-saturating maintenance window, see the warning at the top of this section**). The unit must be `active`; read the journal
   (`journalctl -u soulmask_tmpfs -p err`): any `ERROR` line is a per-instance
   problem (setup exited 75, which the unit accepts — see "Failure policy").
5. **Second setup run** — load the env first, then run it (real run or `--dry-run`):
   `set -a; . /etc/gstammtisch/soulmask_tmpfs.env; set +a; /usr/local/sbin/soulmask_tmpfs-setup.sh --dry-run`.
   Run directly WITHOUT the env it reports all three policy values as unset and exits 75 (it refuses, as
   designed, no script default); measured 2026-10-20 on gstammtisch, with the env: "keeping" for both
   instances, exit 0. It must log, for BOTH instances, "per-instance tmpfs already
   mounted at ... with the expected options — keeping" and exit 0. This is the
   only live check of the `findmnt` options parsing (size normalisation, mode).
   If it logs "refusing to keep", compare the logged options with the policy file.
6. Start the servers.
7. Verify:
   - `findmnt -t tmpfs | grep soulmask-pi-` shows one tmpfs per instance on
     `WS/Saved/Logs`;
   - `WS.log` grows inside it;
   - first prune-timer pass: `systemctl list-timers soulmask-logs-prune.timer`,
     then `systemctl start soulmask-logs-prune.service` and
     `journalctl -u soulmask-logs-prune.service -n 20` show `ok (…% used, …
     backup(s) kept …)` for both mounts and no ERROR; and `df -h` on each mount
     stays well below the 75% mark after the first game restart cycle;
   - `systemctl is-failed soulmask-logs-prune.service` says `active`/not failed
     (a failure there also means setup recorded per-instance errors);
   - the D-state probe shows no ext4 or jbd2 stacks for `FAsyncWriter_WS`.

## Manual update procedure

> **Maintenance window, expect it (measured 2026-10-20, gstammtisch, live SMT-01 deploy).**
> `systemctl restart|start soulmask_tmpfs.service` re-copies ~2.6 GB from disk, then restarts Wings, and
> Wings restores its servers (steamcmd validate reads the installs). On this ~10 MB/s disk that kept `vda` at
> 100 % utilisation for about 10 minutes (reads 6–10 MB/s plus writes 8 MB/s, awaits 0.2–1.5 s, from the
> operator's `iostat`). During that time **every new container start on the host hung**, including other
> servers Wings tried to boot and unrelated `docker run`. Do it in a quiet window and do not start other
> container work until `vda` calms (`iostat -x 5`).
> Steps 2 and 5 below are exactly that: step 5 is the heavy one.

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

# Dry-run (no changes); the policy env must be loaded first, or it exits 75
set -a; . /etc/gstammtisch/soulmask_tmpfs.env; set +a; /usr/local/sbin/soulmask_tmpfs-setup.sh --dry-run

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
