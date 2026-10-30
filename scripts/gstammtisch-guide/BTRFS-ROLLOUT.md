# BTRFS-ROLLOUT.md — gstammtisch: separate btrfs filesystems for Wings and for the devcontainer CoW area

*2026-10-22. Plan, not yet applied. Host: gstammtisch (kernel 7.2.6+deb13, root ext4 on LVM `gstammtisch-vg`,
one PV `vda5`, VG fully allocated to `root`). This is the host-specific runbook; the design, the btrfs
concepts and the tool contract are in the generic guide
[`mdt-cockpit/docs/BTRFS-COW.md`](../../mdt-cockpit/docs/BTRFS-COW.md), whose section numbers are cited
below as "generic §n". Evidence tags as there: **[M]** measured here, **[K]** documented behaviour, **[T]**
untested (the self-check script covers most of them; run it on r1002 first).*

## 1. What and why

| Filesystem | Mounted at | Holds | Why separate |
|---|---|---|---|
| `host-services` (btrfs, own LV) | subvolume `pterodactyl` at `/var/lib/pterodactyl` | Wings state, game volumes, backups; room for further host-service subvolumes (section 6) | Own journal/transaction: dev writeback and snapshot commits no longer ride on the game's fsync ([HOST-IO-INTERFERENCE.md](HOST-IO-INTERFERENCE.md) §7, "separate filesystem for game data"). Every subvolume added later shares that transaction (section 6). |
| `devcontainers-cow` (btrfs, own LV) | `/srv/devcontainers-cow` (whole filesystem) | ISOs, VM disks, assay snapshots, per-worktree scratch | Reflink/snapshot/compression; snapshot commits stay off the game's filesystem. Design: generic §1 to §8. |

An LV gives each filesystem its own journal but **not** a separate device queue: all LVs sit on `vda`, so the
queue bounds in [HOST-IO-INTERFERENCE.md](HOST-IO-INTERFERENCE.md) §5 stay necessary, and swap-in waits (now
the dominant game wait with `WS.log` on tmpfs, §4.6) are not affected at all (generic §2.5).

Docker (`/var/lib/docker`, overlay2) and `/workspaces` stay on ext4.

## 2. Sizing

[M] `df /`: 939G, 445G used, 446G free. `/var/lib/pterodactyl` is 7.2G. `~/mdt--mounted-folders/tmp` is 88G
(ISOs and scratch; much of it can move to the CoW area). Swap is 2 x 34.5G with 7.9G in use on 15 GB RAM.

LV sizes (all grow online later; start modest and keep VG space unallocated):

| LV | Size | Notes |
|---|---|---|
| `root` | 620G | after shrink; about 175G free (144G usable after the ext4 reserve) |
| `host-services` | 60G | 7.2G today; room for backups/archives |
| `devcontainers-cow` | 200G | scratch; qgroups are not used, so watch `btrfs filesystem usage` |
| unallocated | about 74G | headroom for growing either LV |

**Alternative without touching root:** one swap partition is plenty. `swapoff /dev/vda7`, `pvcreate` it and
`vgextend` give 34G of VG space with no root shrink and no downtime. Do that instead of (or before) the shrink
if a smaller change is wanted. Remember the September loop-swap incident: change fstab carefully and test on
r1002.

## 3. Shrink root (offline)

Prerequisite: console access (hoster VNC/serial) and a hoster-side snapshot or a verified backup. ext4 cannot
shrink while mounted, so boot a rescue ISO or an initramfs shell where `findmnt /` shows nothing, then:

```bash
vgchange -ay gstammtisch-vg
e2fsck -f /dev/mapper/gstammtisch--vg-root
# --resizefs runs resize2fs in the correct order (shrink the filesystem first, then the LV).
lvreduce --resizefs -L 620G gstammtisch-vg/root
e2fsck -f /dev/mapper/gstammtisch--vg-root
```

Never run `lvreduce` below the filesystem size without `--resizefs`: it destroys data. Reboot normally.

## 4. Create the filesystems

```bash
apt install btrfs-progs rsync            # rsync is NOT installed on this host [M]; the migration needs it
lvcreate -L 60G  -n host-services    gstammtisch-vg
lvcreate -L 200G -n devcontainers-cow gstammtisch-vg
mkfs.btrfs -L host-services     -m dup -d single /dev/gstammtisch-vg/host-services
mkfs.btrfs -L devcontainers-cow -m dup -d single /dev/gstammtisch-vg/devcontainers-cow
```

`/etc/fstab` (same LABEL style as the swap lines; how each field resolves: generic §2.2, option scope: §2.3):

```fstab
LABEL=host-services      /var/lib/pterodactyl    btrfs  subvol=pterodactyl,noatime,discard=async,nofail,x-systemd.device-timeout=30s  0 0
LABEL=devcontainers-cow  /srv/devcontainers-cow  btrfs  noatime,discard=async,user_subvol_rm_allowed,nofail,x-systemd.device-timeout=30s  0 0
```

`discard=async` reaches the virtual disk: `lsblk -D` reports 512 B granularity on `vda` and the LVs [M].
`user_subvol_rm_allowed` is only on the CoW filesystem; the Wings one does not need it.

Then the `chattr +i` guard on **both** mountpoints (generic §3.1; the Wings mountpoint is handled in section 5
because it holds data today), and the user area:

```bash
host-setup/scripts/mdt-cow-init-user.sh /srv/devcontainers-cow vb       # uid 1003
```

## 5. Migrate `/var/lib/pterodactyl`

Do this with the game stopped. The soulmask setup script refuses symlinked volume paths, so **mount** the
filesystem directly on `/var/lib/pterodactyl`; do not symlink.

```bash
# 1. stop the game servers, switch the tmpfs layer off (teardown refuses while the game runs; use the
#    toggle/teardown from SOULMASK-TMPFS.md, exact invocation not re-verified here) and stop wings.
#    Verify: `findmnt | grep pterodactyl` shows no tmpfs submounts.
mkdir -p /mnt/hs-top && mount -o subvolid=5 LABEL=host-services /mnt/hs-top   # top level, temporary
btrfs subvolume create /mnt/hs-top/pterodactyl      # the name `subvol=pterodactyl` in fstab refers to
rsync -aHAXSx --numeric-ids --info=progress2 /var/lib/pterodactyl/ /mnt/hs-top/pterodactyl/
umount /mnt/hs-top
mv /var/lib/pterodactyl /var/lib/pterodactyl.old
mkdir /var/lib/pterodactyl && chattr +i /var/lib/pterodactyl     # fstab line from section 4 is already in place
systemctl daemon-reload && mount /var/lib/pterodactyl
# 2. start wings, switch the tmpfs layer back on, check the game; delete .old after a few days.
```

`-x` keeps rsync off the per-instance tmpfs submounts, which must already be gone.

### Ordering with `soulmask_tmpfs.service`

The unit has `After=local-fs.target`, `DefaultDependencies=no`, `Before=docker.service`, and its `ExecStart`
bind-mounts tmpfs *into* `/var/lib/pterodactyl/volumes/...`. An fstab mount is ordered before
`local-fs.target` **only without `nofail`**: the `nofail` line in section 4 makes the mount merely wanted, not
ordered before `local-fs.target` [R, r1002], so `After=local-fs.target` does **not** guarantee the mount is up.
What guarantees it is `RequiresMountsFor=/var/lib/pterodactyl`, which the repo unit
(`files/etc/systemd/system/soulmask_tmpfs.service`) carries: it orders the unit after the mount and fails the
unit when the mount cannot be made [R], instead of mounting tmpfs on the ext4 directory underneath (details:
[SOULMASK-TMPFS.md](SOULMASK-TMPFS.md) "Ordering with a dedicated Wings filesystem"). Deploy the unit file with
`daemon-reload` before the migration; it is a no-op until then.

`RequiresMountsFor` also orders shutdown: the unit's `ExecStop` (unmounting the bind targets) runs before
systemd unmounts the btrfs, so the unmount does not hit "busy". Wings runs in Docker with an `rslave` bind of
`/var/lib/pterodactyl`. Docker is ordered after the soulmask unit (`Before=docker.service`), which requires the
mount, so on a normal boot the mount is up before Docker starts. If the mount fails, the unit fails but
`Before=` is only ordering: Docker and Wings still start, find the empty immutable directory, and fail on their
first write instead of forking state onto ext4. Do not put `RequiresMountsFor=` on `docker.service` itself: a
missing Wings filesystem would then stop every dev container too.

## 6. More host services in the `host-services` filesystem

The top level of `host-services` is **not mounted** in normal operation. It holds subvolumes and each service
gets its own through `subvol=` (`pterodactyl` above; later e.g. `otherservice` at its own path). A subvolume,
not a plain directory, is what can be mounted separately, snapshotted, made read-only and given a compression
property (concepts: generic §2.1 to §2.4).

- **Isolation is weaker than it looks.** The LV isolates the game from `root` and `devcontainers-cow`. A second
  service in the same btrfs shares the game's transactions, commits and free space: a snapshot of
  `otherservice` forces a commit every subvolume waits on, and a writer there can fill the pool the game needs.
  Fine for a low-write service snapshotted hourly or daily; keep write-heavy and snapshot-storm workloads on
  `devcontainers-cow`; give a noisy neighbour its own LV.
- **Same filesystem-wide option set on every fstab line** of this filesystem (generic §2.3). Per-service
  compression is `btrfs property set <mountpoint> compression zstd`, not a mount option.
- **Snapshots live on the same filesystem but not inside the service tree** (a snapshot nested under
  `/var/lib/pterodactyl` would be visible through Wings' bind mount). Put them in a top-level subvolume
  `snapshots`, mounted read-only where needed (`subvol=snapshots,ro` at e.g. `/srv/host-services-snapshots`) or
  reached through a temporary `subvolid=5` mount; snapshot with `btrfs subvolume snapshot -r`.
- **Do not create subvolumes inside the Wings tree** unless Wings and the soulmask scripts never move or link
  across them (generic §2.4); one subvolume per service.
- **Each service needs its own `RequiresMountsFor=` and `chattr +i`.** If the device is missing, all of them
  fail closed together.

New subvolume for another service, later:

```bash
# the filesystem is already mounted via fstab: this mount joins that superblock and inherits its options
mount -o subvolid=5 LABEL=host-services /mnt/hs-top
btrfs subvolume create /mnt/hs-top/otherservice
umount /mnt/hs-top
mkdir -p /srv/otherservice && chattr +i /srv/otherservice
# fstab: LABEL=host-services /srv/otherservice btrfs subvol=otherservice,<same option set as above> 0 0
mount /srv/otherservice && btrfs property set /srv/otherservice compression zstd
```

## 7. Devcontainer

On this host the cockpit config is currently `vbpub/.devcontainer/devcontainer.json` (untracked). After
sections 4 and 5, uncomment the two cow lines in it (they ship commented in `mdt-cockpit/templates/`; generic
§7) and rebuild. Nothing else is needed: `SYS_ADMIN` and `seccomp=unconfined` are already in its `runArgs`, and
`${localEnv:USER}` resolves to `vb`. If the config moves to a host-side folder, the same two lines go unchanged
into that folder's `devcontainer.json`.

## 8. Checks on this host (after r1002 and the self-check script)

1. `lsattr -d` shows `i` on both mountpoints while unmounted; writes give `EPERM`.
2. `systemctl list-dependencies --after soulmask_tmpfs.service | grep pterodactyl` shows the mount.
3. Reboot test with the `host-services` LV deactivated: boot completes, `soulmask_tmpfs.service` fails, Wings and
   the game do not start on ext4.
4. As `vscode` in the rebuilt container: snapshot, `cp --reflink` across subvolumes, delete.
5. `iostat -x` / PSI during a snapshot loop on `/srv/devcontainers-cow` while the game runs: game
   `io.pressure` stays at the baselines of [HOST-IO-INTERFERENCE.md](HOST-IO-INTERFERENCE.md) §4.
6. Before any maintenance that needs the CoW device (`lvextend` is online, but `lvremove`, `btrfs check`, a
   partition resize are not): stop the devcontainers. On r1002 a host `umount` succeeded while a container held a
   bind of the user directory and the container kept using the filesystem, so the device stays busy and nothing
   warns you (generic §7).

## 9. Open items

- `chattr +C` on Soulmask's SQLite directory: only if CoW fragmentation shows up in measurements.
- Docker data root on btrfs: deliberately not part of this change.
- `.git` of vbpub is 60 GB because BuildKit and artifact caches live inside it [M]; a separate finding for the
  owning tool's backlog, not addressed here.
