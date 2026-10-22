# BTRFS-COW.md — separate btrfs filesystems for Wings and for the devcontainer CoW area

*2026-10-22. Plan, not yet applied. Host: gstammtisch (kernel 7.2.6+deb13, root ext4 on LVM
`gstammtisch-vg`, one PV `vda5`, VG fully allocated to `root`). Facts below marked [M] were measured
on the host on 2026-10-22; the rest is design intent to be verified on r1002 first.*

## 1. What and why

| Filesystem | Mountpoint | Holds | Why separate |
|---|---|---|---|
| `host-services` (btrfs, own LV) | subvolume `pterodactyl` mounted at `/var/lib/pterodactyl` | Wings state, game volumes, backups; room for further host-service subvolumes (section 6a) | Own journal/transaction: dev writeback and snapshot commits no longer ride on the game's fsync (HOST-IO-INTERFERENCE.md §7, "separate filesystem for game data"). Every extra subvolume added later shares that transaction (section 6a). |
| `devcontainer-vb` (btrfs, own LV) | `/srv/vbcow` | ISOs, VM disks, assay snapshots, per-worktree scratch | Reflink/snapshot/compression. Snapshot creation forces a btrfs transaction commit; keeping it off the game's filesystem is the point. |

An LV gives each filesystem its own journal. It does **not** give a separate device queue: all LVs
still sit on `vda`, so the queue bounds in HOST-IO-INTERFERENCE.md §5 stay necessary, and swap-in
waits (now the dominant game wait with `WS.log` on tmpfs, §4.6) are not affected at all.

Docker (`/var/lib/docker`, overlay2) and `/workspaces` stay on ext4.

## 2. Sizing

[M] `df /`: 939G, 445G used, 446G free. (The "189 GB free" quoted earlier is stale.) `/var/lib/pterodactyl`
is 7.2G. `~/mdt--mounted-folders/tmp` is 88G (ISOs and scratch; much of it can move to `/srv/vbcow`).

Suggested LV sizes (all can be grown online later, so start modest and keep VG space unallocated):

| LV | Size | Notes |
|---|---|---|
| `root` | 620G | after shrink; ~175G free (about 144G usable after the ext4 reserve) |
| `host-services` | 60G | 7.2G today; room for backups/archives |
| `devcontainer-vb` | 200G | scratch; qgroups are not used, so watch `btrfs fi usage` |
| unallocated | ~74G | headroom for growing either LV |

Optional extra without touching root: swap is 2 x 34.5G with 7.9G in use [M]. `swapoff /dev/vda7`,
`pvcreate` it and `vgextend` yields 34G of VG space with no root shrink. Do that instead of (or before)
the shrink if a smaller change is wanted. Remember the September loop-swap incident: change fstab
carefully and test on r1002.

## 3. Shrink root (offline)

Prerequisite: console access (hoster VNC/serial) and a hoster-side snapshot or a verified backup.
`ext4` cannot shrink while mounted, so the root must be unmounted: boot a rescue ISO or an initramfs
shell where `findmnt /` shows nothing, then:

```bash
vgchange -ay gstammtisch-vg
e2fsck -f /dev/mapper/gstammtisch--vg-root
# --resizefs runs resize2fs in the correct order (shrink fs first, then the LV).
lvreduce --resizefs -L 620G gstammtisch-vg/root
e2fsck -f /dev/mapper/gstammtisch--vg-root
```

Never run `lvreduce` without `--resizefs` below the filesystem size: it destroys data. Reboot normally.

## 4. Create the filesystems

Four different namespaces are involved; they are independent and only line up by convention here:

| Namespace | Example | Used by |
|---|---|---|
| LVM: VG / LV name | `gstammtisch-vg` / `host-services` | `lvcreate -n`; device node `/dev/gstammtisch-vg/host-services` (= `/dev/mapper/gstammtisch--vg-host--services`) |
| btrfs filesystem **label** | `host-services` (`mkfs.btrfs -L`) | `LABEL=...` in fstab and `mount`; one label per filesystem |
| btrfs **subvolume** name = path below the top level | `pterodactyl`, `snapshots` | `btrfs subvolume create`, `subvol=` in fstab |
| mountpoint | `/var/lib/pterodactyl`, `/srv/vbcow` | where the tree appears; independent of all the names above |

The LV name and the label are kept identical (`host-services`, `devcontainer-vb`) so `lsblk -f` and fstab
read the same; the mountpoints keep their own names.

```bash
lvcreate -L 60G  -n host-services    gstammtisch-vg
lvcreate -L 200G -n devcontainer-vb gstammtisch-vg
mkfs.btrfs -L host-services    -m dup -d single /dev/gstammtisch-vg/host-services
mkfs.btrfs -L devcontainer-vb -m dup -d single /dev/gstammtisch-vg/devcontainer-vb
```

`apt install btrfs-progs` on the host first. Do **not** set `compress=` at mount time: compression is
then applied only where a directory opts in (section 7).

`/etc/fstab` (same LABEL style as the swap lines):

```fstab
LABEL=host-services     /var/lib/pterodactyl  btrfs  subvol=pterodactyl,noatime,discard=async,nofail,x-systemd.device-timeout=30s  0 0
LABEL=devcontainer-vb    /srv/vbcow            btrfs  noatime,discard=async,user_subvol_rm_allowed,nofail,x-systemd.device-timeout=30s  0 0
```

`nofail` keeps SSH reachable if a filesystem is missing; the guard in section 5 stops that from
turning into a silent fallback to ext4. `user_subvol_rm_allowed` lets the container user delete the
subvolumes it creates; mount options belong to the host superblock and the bind mount inherits them.

### 4a. How each reference in those fstab lines resolves

**`LABEL=host-services`** (device lookup, nothing to do with LVM or subvolumes):
1. `mkfs.btrfs -L host-services` writes the label into the filesystem's superblock.
2. At boot udev reads it (blkid) and creates `/dev/disk/by-label/host-services`, a symlink to the LV's device node.
3. fstab's `LABEL=host-services` is shorthand for that symlink. The LV name is irrelevant here and could
   change without breaking fstab. The label must be unique across all block devices on the host.

**`subvol=pterodactyl`** (lookup *inside* the filesystem, after the device is found):
- It is a path **relative to the top level (subvolume ID 5)** of that filesystem. It resolves because
  `btrfs subvolume create /mnt/hs-top/pterodactyl` (section 6) created a subvolume with exactly that path.
  It must exist before the first fstab mount, otherwise the mount fails ("can't find subvol") and `nofail`
  lets the boot continue.
- In `btrfs subvolume create /mnt/hs-top/pterodactyl`, `/mnt/hs-top` is an ordinary **host path**: the
  directory where the top level is temporarily mounted (`mount -o subvolid=5 ...`), and it exists only while
  that mount does. The command splits the argument into parent directory and last component: the parent must
  already exist on that btrfs, the last component `pterodactyl` is the name of the new subvolume. Relative to
  the top level the new subvolume's path is therefore `pterodactyl`, and that is what `subvol=` names. The
  `/mnt/hs-top/` prefix is not part of the subvolume's name. Creating `/mnt/hs-top/a/b/pterodactyl` instead
  would need `subvol=a/b/pterodactyl`. A subvolume is renamed like a directory (`mv`, within the filesystem).
- `/srv/vbcow` has no `subvol=`, so it mounts the default subvolume, which is the top level (ID 5) unless
  `btrfs subvolume set-default` was run. Add `subvolid=5` to the options to pin that.

**Subvolume name vs mountpoint.** They are independent: the subvolume name is a path inside the filesystem,
the mountpoint is the second fstab field. `subvol=pterodactyl` could be mounted at `/srv/x`. Using the same
word is only a convention to keep fstab readable (distro setups use `@` and `@home` mounted at `/` and
`/home`). Mapping to ZFS, which separates the same ideas:

| ZFS | btrfs |
|---|---|
| pool | the filesystem (device/LV + label) |
| dataset `pool/pterodactyl` | subvolume `pterodactyl` (path from the top level) |
| `mountpoint=` property, mounted by `zfs-mount` | the fstab line (`subvol=` + mountpoint); nothing mounts automatically |
| `compression=`, `readonly=` dataset properties | `btrfs property set` on the subvolume; `ro` as a mount option |
| dataset quota | qgroups (not used here) |
| inherited by child datasets | a *nested* subvolume appears as a directory inside its parent; only the top level is not mounted here |

The part that feels convoluted is that a subvolume is also a directory: a subvolume created inside a mounted
subvolume shows up as a subdirectory of it. Keeping the top level unmounted and creating subvolumes only there
(one per mount) avoids nesting and gives the ZFS-like "flat list of datasets".

**Who sets `discard=async` when only a subvolume is mounted?** The mount itself. btrfs-specific options
(`discard=`, `compress=`, `commit=` ...) apply to the **filesystem (superblock)**, not to a subvolume, and
they are taken from the mount that *creates* the superblock, i.e. the first mount of that filesystem since it
was last fully unmounted. With the top level unmounted and one fstab line per filesystem, that first mount is
the fstab mount, so its `discard=async` is what is active; `mount` output and `findmnt -o OPTIONS` show it.
Consequences:
- Per-mount (VFS) options such as `noatime`, `ro`, `nosuid`, `nodev`, `noexec` really are per mount and may
  differ between subvolume mounts of the same filesystem.
- A **later mount of the same filesystem joins the existing superblock** and cannot change fs-wide options
  (they are ignored or refused, depending on kernel; test on r1002). So the temporary top-level mounts in
  this guide pass no fs-wide options, and when another mount is already active they simply inherit its
  `discard=async`. During the first migration there is no earlier mount: the temporary mount runs without
  discard, is unmounted again, and the superblock is gone before the fstab mount sets it.
- To change an fs-wide option on a live filesystem: `mount -o remount,discard=async <any mountpoint of it>`;
  put the new value in fstab too, since it is read only when the superblock is created.
- Discard must also pass through the layers below: LV and `vda` report discard support on this host
  (`lsblk -D`, DISC-GRAN 512B), so `discard=async` reaches the virtual disk.

## 5. `chattr +i` — make "not mounted" fail loudly

`chattr +i` sets the immutable attribute. On a directory it means nothing can be created, removed or
renamed inside it, and the directory itself cannot be removed or renamed; only root can set or clear
it. Set it on the **underlying ext4 directory while nothing is mounted there**. When the btrfs is
mounted on top, its own root directory is shown and the flag has no effect; when the mount is missing,
every write lands on the immutable empty directory and fails with `EPERM` instead of silently filling
the root disk or forking Wings state.

```bash
mkdir -p /srv/vbcow
mountpoint -q /srv/vbcow && umount /srv/vbcow          # flag is set with the mount absent
chattr +i /srv/vbcow
lsattr -d /srv/vbcow                                   # expect: ----i---------
mount /srv/vbcow                                       # mounting on an immutable dir is allowed
touch /srv/vbcow/ok && rm /srv/vbcow/ok                # works: you are on btrfs
umount /srv/vbcow; touch /srv/vbcow/x                  # expect: Operation not permitted
mount /srv/vbcow
```

Same for `/var/lib/pterodactyl` and for **every further subvolume mountpoint** (section 6a): when the
filesystem is missing, each of them is an empty immutable directory. Section 6 moves the old data away first. To remove such a
directory later: `chattr -i` first.

## 6. Migrate `/var/lib/pterodactyl`

Do this with the game stopped. The setup script refuses to run against symlinked volume paths, so
**mount** the filesystem directly on `/var/lib/pterodactyl`; do not symlink.

```bash
# 1. stop: game servers, tmpfs layer (teardown refuses while the game runs), then wings
# tmpfs layer off: use the toggle/teardown from SOULMASK-TMPFS.md (exact invocation not re-verified here)
# verify: findmnt | grep pterodactyl shows no tmpfs submounts
# stop wings (its compose project), then:
mkdir -p /mnt/hs-top && mount -o subvolid=5 /dev/gstammtisch-vg/host-services /mnt/hs-top   # top level, temporary
btrfs subvolume create /mnt/hs-top/pterodactyl
rsync -aHAXSx --numeric-ids --info=progress2 /var/lib/pterodactyl/ /mnt/hs-top/pterodactyl/
umount /mnt/hs-top
mv /var/lib/pterodactyl /var/lib/pterodactyl.old
mkdir /var/lib/pterodactyl && chattr +i /var/lib/pterodactyl
systemctl daemon-reload && mount /var/lib/pterodactyl
# 2. start wings, switch the tmpfs layer back on; check the game, delete .old after a few days
```

`-x` keeps rsync off the per-instance tmpfs submounts, which must already be gone.

### Ordering with `soulmask_tmpfs.service`

It has `After=local-fs.target`, `DefaultDependencies=no`, `Before=docker.service`, and its ExecStart
bind-mounts tmpfs *into* `/var/lib/pterodactyl/volumes/...`. An fstab mount is ordered before
`local-fs.target` (also with `nofail`), so the unit already starts after the btrfs mount and the
start order works unchanged. The repo unit
(`files/etc/systemd/system/soulmask_tmpfs.service`) now also carries
`RequiresMountsFor=/var/lib/pterodactyl`, so a missing mount fails the unit instead of mounting
tmpfs on the ext4 directory underneath (details: SOULMASK-TMPFS.md "Ordering with a dedicated Wings
filesystem"). Deploy the unit file with `daemon-reload` before the migration; it is a no-op until then.

`RequiresMountsFor` also orders shutdown correctly: the unit's `ExecStop` (unmounting the bind
targets) runs before systemd unmounts the btrfs, so the unmount does not hit "busy". Wings itself runs
in Docker with an `rslave` bind of `/var/lib/pterodactyl`, so it follows the mount; it starts after the
mount because Docker starts later than `local-fs.target`.

## 6a. One filesystem, several host-service subvolumes (`host-services` fs)

The top level of the `host-services` filesystem is **not mounted** in normal operation. It holds subvolumes,
and each service gets its own via `subvol=` in fstab (`pterodactyl` above; later e.g. `otherservice`
at its own path). A subvolume, not a plain directory, is the unit that can be mounted separately,
snapshotted, made read-only and given its own compression property.

How the names resolve (details in 4a): every btrfs filesystem has a **top-level subvolume with the fixed ID 5**
(`FS_TREE`); user-created subvolumes and snapshots get IDs from 256 upward (`btrfs subvolume list <mnt>`
shows them). `-o subvolid=5` says "mount the top level", which is where you create subvolumes and see them
as directories. `subvol=pterodactyl` is a **path relative to the top level**, so it only resolves because
`btrfs subvolume create /mnt/hs-top/pterodactyl` created a subvolume of exactly that name there;
`subvol=` overrides any default subvolume, `subvolid=<n>` is the rename-proof (but opaque) alternative.
A bare `mount` without either option mounts the *default* subvolume, which is ID 5 unless someone ran
`btrfs subvolume set-default`; `-o subvolid=5` is used in the recipes so they cannot land in the wrong place.
`findmnt` shows the mounted subvolume as `/dev/mapper/...[/pterodactyl]`.

What this changes compared with mounting the whole filesystem:

- **Isolation is weaker than it looks.** The separate LV isolates the game from `root` and `devcontainer-vb`. A second
  service in the same btrfs shares the game's transactions, commits and free space. A snapshot of
  `otherservice` forces a commit that every subvolume waits on, and a writer there fills the pool the game
  needs (ENOSPC for both). Fine for a low-write service snapshotted hourly or daily; keep write-heavy or
  snapshot-storm workloads on `devcontainer-vb` (`/srv/vbcow`). If a neighbour turns out to be noisy, give it its own LV.
- **Mount options are filesystem-wide, except `subvol=` and VFS ones such as `noatime`.** `discard=async`
  and any `compress=` belong to the superblock the first mount creates. Write the same option set on every
  fstab line of this filesystem (a conflicting later line is ignored or refused, depending on kernel).
  Per-service compression is therefore a `btrfs property set <mountpoint> compression zstd` on that
  subvolume's mount, not a mount option.
- **Snapshots must live on the same filesystem but not inside the service tree.** A snapshot nested under
  `/var/lib/pterodactyl` would be visible to the Wings bind mount. Put them in a top-level subvolume
  `snapshots`, mounted read-only where needed (`subvol=snapshots,ro` at e.g. `/srv/host-services-snapshots`) or
  reached through a temporary `subvolid=5` mount. Snapshot read-only: `btrfs subvolume snapshot -r`.
- **Do not `set-default` a subvolume.** Always mount with an explicit `subvol=`; a bare mount then shows the
  whole top level, which is the thing to keep out of reach.
- **Every subvolume has its own `st_dev`.** `rsync -x`, `du -x`, `find -xdev` and `mountpoint` treat a nested
  subvolume as another filesystem and skip it; `mv` across subvolumes is copy-and-delete and hard links fail
  (`EXDEV`), while `cp --reflink` works. Do not create subvolumes *inside* the Wings tree unless Wings and the
  soulmask scripts never move or link across them; keep one subvolume per service.
- **Failure behaviour per service:** each service needs its own `RequiresMountsFor=` on its mountpoint and its
  own `chattr +i` on the unmounted directory. If the device is missing, all of them fail closed together.

New subvolume for another service, later:

```bash
# the fs is already mounted via fstab: this mount joins that superblock and inherits its fs-wide options
mount -o subvolid=5 /dev/gstammtisch-vg/host-services /mnt/hs-top
btrfs subvolume create /mnt/hs-top/otherservice
umount /mnt/hs-top
mkdir -p /srv/otherservice && chattr +i /srv/otherservice
# fstab: LABEL=host-services /srv/otherservice btrfs subvol=otherservice,<same option set as above> 0 0
mount /srv/otherservice && btrfs property set /srv/otherservice compression zstd
```

## 7. `/srv/vbcow` layout and per-directory properties

```bash
cd /srv/vbcow && chown vb:vb .           # uid 1003 = host vb; devcontainer's vscode is remapped to it
btrfs subvolume create assay-base        # example: only subvolumes can be snapshotted, plain dirs cannot
btrfs property set truenas/vm-disks compression zstd    # opt-in compression; level: see section 10
btrfs property set truenas/upstream-iso compression none  # ISOs are already compressed (zstd -3: 97% of size [M])
```

Keep renames inside one subvolume (for example download an ISO into a staging directory of the *same*
subvolume it will live in), because `mv` across subvolumes copies. Reflinks across subvolumes are fine.

Layout (each directory that is snapshotted or deleted as a unit is a subvolume):

```
/srv/vbcow/
  truenas/upstream-iso/      write-once, files named by sha256, read-only afterwards; compression none
  truenas/vm-disks/base/     golden raw images, read-only
  truenas/vm-disks/run/      per-test disks: cp --reflink=always base.raw run-X.raw
  assay/<repo>/base/         subvolume: self-contained clone kept warm (section 7a)
  assay/<repo>/snap-*/       per-judge / per-mutation-worker snapshots, deleted after the run
  worktrees/<instance-id>/   per-worktree compressed scratch (ciu: linked in as an ignored .store symlink)
```

## 7a. assay / ciu: warm base + snapshots

Goal: no new checkout per run. Measured on this host (2026-10-22, vbpub, cold-ish cache): a self-contained
`git clone --no-hardlinks` takes **26.7 s wall for 0.44 s CPU** (273 MB, IO-bound on this disk), while a
`git checkout --detach HEAD~50 && git clean -ffdx` in an existing clone takes **1.4 s**. A warm base turns the
first into the second, and the base can also carry the installed environment, which is likely the larger
cost (not measured: `uv` is not installed on the host; measure on the prototype).

Recipe:

```bash
# once per repo (and after big upstream changes: git -C base fetch):
btrfs subvolume create assay/<repo>/base
git clone --no-hardlinks --no-checkout <source repo> assay/<repo>/base    # self-contained, NOT --shared
# per run / per mutation worker:
btrfs subvolume snapshot assay/<repo>/base assay/<repo>/snap-<id>
git -C assay/<repo>/snap-<id> checkout --detach <sha> && git -C ... clean -ffdx
# after the run: rollback is deleting it
btrfs subvolume delete assay/<repo>/snap-<id>
```

Why the base is a **self-contained clone, not `git clone --shared`**:
- `--shared` records the source repo's object store in `.git/objects/info/alternates` as an absolute path.
  Every consumer (host, devcontainer, sibling containers) must resolve that path identically, the source
  must never be moved or deleted, and a `git gc`/prune there can remove objects the snapshots still need,
  which is why a pinned ref (`refs/assay/base/<id>`) would be required. A self-contained clone has none
  of these couplings; the snapshot carries its own objects, shared extent-wise with the base for free.
- The source repo usually lives on ext4; with alternates every snapshot keeps reading its objects from there,
  which defeats the point of a separate filesystem.
- Cost: one full clone per repo (273 MB for vbpub), refreshed with `git fetch` in the base. Only data that
  diverges from the base uses new space.
- Do not snapshot a git **worktree** as base: its `.git` file points at one admin directory under the main
  repo, and two copies of it would corrupt it. A clone has a real `.git` directory.

**Do not copy a `.git` directory wholesale on this host.** vbpub's `.git` is **60 GB**, of which
`.git/mdt-buildkit-cache` 46G, `mdt-buildkit-cache-zstd-true-3` 8G and `mdt-artifact-cache` 5.7G are build
caches stored inside `.git`; the object store is 157 MB (plus 75 worktrees' admin files). Use `git clone`,
never `cp -a .git`. (Build caches inside `.git` are a separate finding for the owning tool's backlog.) The
first copy from ext4 to btrfs cannot be a reflink anyway; reflinks/snapshots start from the base.

Prefer `btrfs subvolume snapshot` to `cp -a --reflink=always` for base to snapshot (atomic, no per-file
work); use `cp --reflink` where the base is not a subvolume or for single files such as VM disks.

**Warm environment.** Put the installed venv / `node_modules` / caches in the base, not only the checkout.
Snapshots live at a different path, and venv shebangs and some caches hold absolute paths, so use
`python -m ...` and relocatable environments, or test a snapshot explicitly before relying on it.

Settings: an assay option such as `[isolation] snapshot_backend = "btrfs"`, root from `${VB_COW_ROOT:?}`,
verified to be btrfs (`stat -f -c %T`), failing loudly instead of copying. Pin nothing in the source repo.
Mutation testing gets the largest win: N workers get N snapshots almost instantly instead of N clones.

## 8. Devcontainer (`vbpub/.devcontainer/devcontainer.json`)

Exactly one `mounts` entry and one env var. **Add them only after the host mount works.** `mounts`
entries are `--mount`, which refuses to start the container if the source is missing, so adding the
line early breaks every rebuild.

```jsonc
// in "mounts":
"source=/srv/vbcow,target=/srv/vbcow,type=bind,bind-propagation=rslave",
// in "containerEnv":
"VB_COW_ROOT": "/srv/vbcow"
```

- Same path on both sides, so sibling containers started through docker-outside-of-docker can bind
  `/srv/vbcow/...` using the identical host path.
- `rslave` makes a host-side remount or unmount visible in the container, the same fix applied to the
  Wings bind on 2026-07-31. Without it the container keeps a stale view.
- Nothing else is needed: `--cap-add SYS_ADMIN` and `seccomp=unconfined` are already in `runArgs`
  (so the btrfs ioctls pass); the template does not set `updateRemoteUserUID`, whose default (true)
  remaps `vscode` to the host uid. Verify with `id` after the rebuild.
- `btrfs-progs` can be installed in the running container later. As `vscode` without root these work:
  `subvolume create/snapshot/delete` (delete needs `user_subvol_rm_allowed`), `property set`,
  `cp --reflink`, `chattr`. Mkfs, balance, scrub, qgroups and (probably) `subvolume list` need root.
- Tools must check `stat -f -c %T "$VB_COW_ROOT"` is `btrfs` and refuse otherwise.

## 9. Verification checklist (r1002 first)

1. `lsattr -d` shows `i` on both mountpoints while unmounted; writes give `EPERM`.
2. `systemctl list-dependencies --after soulmask_tmpfs.service | grep pterodactyl` shows the mount.
3. Reboot test with the `host-services` LV deactivated: boot completes, `soulmask_tmpfs.service` fails,
   Wings/the game do not start on ext4.
4. As `vscode` in the rebuilt container: snapshot, `cp --reflink` across subvolumes, delete.
5. `iostat -x` / PSI during a snapshot loop on `/srv/vbcow` while the game runs: game `io.pressure` stays at
   the section-4 baselines of HOST-IO-INTERFERENCE.md.

## 10. Open items

- Per-directory compression **level**: `compress=zstd:N` (1-15, default 3) is a mount option and
  reliably filesystem-wide; whether `property set` carries a level depends on kernel/progs version.
  Test on r1002; `btrfs fi defragment -czstd` recompresses existing files.
- `chattr +C` (no-CoW) on Soulmask's SQLite directory: only if CoW fragmentation shows up in
  measurements; it disables checksums and compression, and snapshots re-enable CoW.
- Docker data-root on btrfs: deliberately not part of this change.
