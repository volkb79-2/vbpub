# A btrfs copy-on-write area for devcontainers

Optional host feature: one btrfs filesystem, mounted on the host, of which each user's own directory is
bind-mounted into the cockpit devcontainer. It gives tools instant snapshots and rollback, reflinked copies
of large files, and transparent zstd compression where a directory opts in, without placing any repository
on a compressed or copy-on-write filesystem.

*Status: design, not applied on any production host yet. Evidence tags used below:* **[M]** *measured on the
reference host (gstammtisch, 2026-10-22),* **[R]** *verified on the scratch VM r1002 on 2026-10-22 (Debian 13,
kernel 7.2.9, btrfs-progs 7.1; results hold for those versions),* **[K]** *documented behaviour of btrfs,
systemd or the kernel, not re-verified here,* **[T]** *assumption still untested. `host-setup/scripts/
mdt-cow-selfcheck.sh` reproduces the [R] items on any scratch host (section 9).*

Host-specific rollouts (sizing, LVM, existing services) are runbooks outside this document; the reference
host's is `scripts/gstammtisch-guide/BTRFS-ROLLOUT.md`.

## 1. Decisions in one table

| Question | Decision | Why |
|---|---|---|
| btrfs or ZFS | **btrfs** | In the kernel (no module rebuild per kernel upgrade); writes are charged to the cgroup that made them, so per-cgroup IO control keeps working; no second cache (ARC) on a memory-pressured host; a snapshot is one ioctl on a directory, no device, mount or delegation needed from inside a container. ZFS's strengths (send/receive, RAID, ARC) matter little for scratch data on one virtual disk. |
| How many filesystems | **One for all devcontainers**, namespaced `<user>/<repo>/...` | One shared free-space pool; reflinks and snapshots work only inside one filesystem. |
| What a container sees | **Only its own user level** (`<mount>/<user>`), at the same path as on the host | Least privilege; the bind source fails closed (section 7). |
| Compression | Per directory or subvolume (`btrfs property`), **no `compress=` mount option** | Repos and ISOs stay uncompressed; compressible areas opt in. Needs no separate filesystem. |
| Snapshot unit | A **subvolume** | Only subvolumes can be snapshotted, made read-only or deleted atomically. Directories are the namespace. |
| Device | Its **own LV or disk**, not a file on the root filesystem | Own journal and transaction stream (section 2.5). A loop file on ext4 couples both filesystems through the ext4 journal. |
| Where repos live | Unchanged, on the host's normal filesystem | Only scratch, snapshots, images and artifacts go on the CoW area. |

Not covered: moving Docker's data root onto btrfs (overlay2 on ext4 stays; image pulls and builds are write
bursts you do not want sharing a transaction stream with latency-sensitive services).

## 2. Concepts you need (read once)

### 2.1 Four independent names

LVM, the btrfs label, subvolume names and mountpoints are four separate namespaces that only line up by
convention. Confusing them is the usual source of fstab mistakes.

| Namespace | Example | Used by |
|---|---|---|
| LVM: VG / LV | `vg0` / `devcontainers-cow` | `lvcreate -n`; device node `/dev/vg0/devcontainers-cow` (= `/dev/mapper/vg0-devcontainers--cow`) |
| btrfs filesystem **label** | `devcontainers-cow` (`mkfs.btrfs -L`) | `LABEL=...` in fstab and `mount`; one label per filesystem |
| btrfs **subvolume** name = its path below the top level | `pterodactyl`, `snapshots` | `btrfs subvolume create`, `subvol=` in fstab |
| mountpoint | `/srv/devcontainers-cow` | where the tree appears; independent of the three above |

Keeping the LV name and the label identical makes `lsblk -f` and fstab read alike.

### 2.2 How each reference in an fstab line resolves

```fstab
LABEL=host-services       /var/lib/service  btrfs  subvol=service,noatime,discard=async,nofail  0 0
LABEL=devcontainers-cow   /srv/devcontainers-cow  btrfs  noatime,discard=async,user_subvol_rm_allowed,nofail  0 0
```

- **`LABEL=x`** is a device lookup, independent of LVM and of subvolumes: `mkfs.btrfs -L x` writes the label
  into the superblock; udev (blkid) creates `/dev/disk/by-label/x`; fstab's `LABEL=x` means that symlink. The
  label must be unique across all block devices. [K]
- **`subvol=service`** is looked up *inside* the filesystem, after the device is found. It is a path
  **relative to the top level** (subvolume ID 5). It resolves because
  `btrfs subvolume create <top-level mount>/service` created a subvolume with exactly that path; it must exist
  before the first fstab mount ("can't find subvol" otherwise). In that command, `<top-level mount>` is an
  ordinary host path (the directory where the top level is temporarily mounted with `-o subvolid=5`); the
  command splits the argument into parent directory and last component, and only the path *below the top
  level* is the subvolume's name. `<top>/a/b/service` would need `subvol=a/b/service`. A subvolume is renamed
  like a directory (`mv` inside the filesystem). [K]
- **No `subvol=`** (the second line) mounts the *default* subvolume: ID 5, the top level, unless someone ran
  `btrfs subvolume set-default`. Add `subvolid=5` to pin it. Never `set-default` on a filesystem whose top
  level should stay out of reach. [K]
- **IDs:** the top level is always **5** (`FS_TREE`); user subvolumes and snapshots start at **256**
  (`btrfs subvolume list <mnt>`). IDs 1 to 4 are internal trees and not mountable. `subvolid=<n>` is the
  rename-proof but opaque alternative to `subvol=<path>`. [K]
- **Subvolume name and mountpoint are independent.** `subvol=service` could be mounted at `/srv/x`. Using the
  same word is a readability convention (distros use `@` and `@home` for `/` and `/home`).

For readers who know ZFS:

| ZFS | btrfs |
|---|---|
| pool | the filesystem (device + label) |
| dataset `pool/service` | subvolume `service` (path from the top level) |
| `mountpoint=` property, mounted by `zfs-mount` | the fstab line (`subvol=` + mountpoint); nothing mounts automatically |
| `compression=`, `readonly=` | `btrfs property set` on the subvolume; `ro` as a mount option |
| dataset quota | qgroups (costly with many snapshots; not used here) |
| child datasets | a *nested* subvolume appears as a directory inside its parent |

What feels convoluted is that a subvolume is also a directory. Creating subvolumes only at the (unmounted) top
level, one per mount, avoids nesting and gives ZFS's flat list of datasets.

### 2.3 Which mount options are per mount, which per filesystem

btrfs-specific options (`discard=`, `compress=`, `commit=`, `user_subvol_rm_allowed`) belong to the
**filesystem (superblock)** and are taken from the mount that *creates* the superblock, the first mount since
the filesystem was last fully unmounted. With one fstab line per filesystem that is the fstab mount, so its
`discard=async` is the one in force (`findmnt -o OPTIONS`). Per-mount (VFS) options (`noatime`, `ro`, `nosuid`,
`nodev`, `noexec`) are really per mount. [K]

- A **later mount of the same filesystem joins the existing superblock** and cannot change filesystem-wide
  options. [R] A second mount asking for `nodiscard` while the first had `discard=async` was **accepted
  without error and silently ignored**: `findmnt` on both then shows `discard=async`. Check the options of the
  *first* mount, not of the line you just wrote. The temporary top-level mounts in this document therefore pass
  no such options and inherit the fstab mount's.
- Change one on a live filesystem with `mount -o remount,discard=async <any mountpoint of it>`, and in fstab
  too (fstab is read only when the superblock is created). [K]
- `discard=async` only helps if the layers below pass discards: check `lsblk -D` (non-zero `DISC-GRAN`). [K]

### 2.4 Every subvolume has its own device number

`rsync -x`, `du -x`, `find -xdev` and `mountpoint` treat a nested subvolume as another filesystem and skip it.
`mv` across subvolumes is copy-and-delete, hard links across them fail (`EXDEV`), while `cp --reflink` works.
Design consequence: keep renames inside one subvolume (download into a staging directory of the *same*
subvolume the file will live in), and do not nest subvolumes inside trees that tools walk with `-x`. [K]

### 2.5 What an LV isolates, and what it does not

An fsync is file-scoped logically, but costs a device flush and, at times, a **filesystem-wide** transaction
commit. ext4 is no better: its fsync commits the shared journal transaction (in `data=ordered` mode that carries
other writers' data). A latency-sensitive service therefore must not share a filesystem with write-heavy or
snapshot-heavy work: a snapshot forces a btrfs commit that every subvolume of that filesystem waits on. [K]

- A separate **LV or partition** gives its own journal/transaction stream: this removes journal coupling,
  the usual cause of fsync stalls. It does **not** give a separate device queue: all LVs sit on the same
  disk and share its queue and flush. A separate **virtual disk** also separates the guest queue (the
  hoster's storage remains shared).
- Per-cgroup `io.max` or weights do not shorten a journal wait; they can even lengthen it (the throttled task
  holds the transaction open). [M on the reference host: `scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md`]
- Measured on r1002 [R], 15 s runs of a 4 KiB write+fsync loop in subvolume A, p99 latency. On a RAM disk (no
  device cost, so only the filesystem's own coupling shows): alone 0.26 ms; with a snapshot create/delete storm
  in a sibling subvolume of the same filesystem 1.03 ms (about 4x, max 8.7 ms); with 200 MB write bursts in the
  sibling 0.35 ms (no effect); load on a *different* filesystem: no effect beyond noise. The coupling exists but
  is small when the device is instant. On loop files over a fast ext4 disk the p99 stayed near 9 ms in every
  case and the 50 ms stalls were one or two per run under heavy writes, on the same filesystem and on the other
  loop-backed one alike (a loop file couples through the backing ext4). That disk does about 1.9 GB/s, so
  this does **not** reproduce a slow disk and neither proves nor refutes the separate-LV benefit there.
- Several subvolumes of one btrfs share commits and free space: a writer in one can starve the others of space
  (ENOSPC for both). Keep a latency-sensitive service's filesystem to low-write neighbours, or give the noisy
  workload its own LV.

## 3. Create the filesystem

Prerequisite: `btrfs-progs` on the host (`apt install btrfs-progs`); the container image gets it later, on
the fly. Names below are examples; use your own VG.

```bash
lvcreate -L 200G -n devcontainers-cow vg0                    # sized so the VG keeps headroom; LVs grow online
mkfs.btrfs -L devcontainers-cow -m dup -d single /dev/vg0/devcontainers-cow
```

`/etc/fstab`:

```fstab
LABEL=devcontainers-cow  /srv/devcontainers-cow  btrfs  noatime,discard=async,user_subvol_rm_allowed,nofail,x-systemd.device-timeout=30s  0 0
```

- **No `compress=`**: compression is applied only where a directory opts in (section 4).
- **`user_subvol_rm_allowed`** lets the container user delete subvolumes it created. It is a filesystem-wide
  option, so the container's bind mount inherits it. [K]
- **`nofail`** keeps the host reachable if the device is missing; the guard below stops that from turning into
  a silent fallback to the root filesystem. **It also removes the ordering:** [R] a mount without `nofail` is
  `RequiredBy=` and `Before=local-fs.target`; the same line with `nofail` is only `WantedBy=local-fs.target` and
  is **not** ordered before it. A service with just `After=local-fs.target` can therefore start before a
  `nofail` mount completes. Services that need the mount must say so with `RequiresMountsFor=<mountpoint>`
  (a dependency job that cannot mount then fails the service [R]). Note that systemd escapes `-` in unit
  names (`/mnt/a-b` is `mnt-a\x2db.mount`; use `systemd-escape -p --suffix=mount`).

### 3.1 `chattr +i` on the mountpoint: make "not mounted" fail loudly

`chattr +i` sets the immutable attribute. On a directory nothing can be created, removed or renamed inside it,
and the directory itself cannot be removed or renamed; only root can set or clear it. Set it on the
**underlying directory while nothing is mounted there**. When the filesystem is mounted on top, its own root
is shown and the flag has no effect; when the mount is missing, every write lands on the immutable empty
directory and fails with `EPERM` instead of silently filling the root disk. [R: set on an empty directory, a
mount over it works and is writable, and after the unmount the directory is `EPERM` again; only root can set
the flag]

```bash
mkdir -p /srv/devcontainers-cow
mountpoint -q /srv/devcontainers-cow && umount /srv/devcontainers-cow   # set the flag with the mount absent
chattr +i /srv/devcontainers-cow
lsattr -d /srv/devcontainers-cow                                         # expect: ----i---------
mount /srv/devcontainers-cow                                             # mounting on an immutable dir
touch /srv/devcontainers-cow/ok && rm /srv/devcontainers-cow/ok         # works: you are on btrfs
umount /srv/devcontainers-cow; touch /srv/devcontainers-cow/x            # expect: Operation not permitted
mount /srv/devcontainers-cow
```

Do this for every mountpoint of a btrfs filesystem, including each further `subvol=` mountpoint. To remove
such a directory later, `chattr -i` it first.

### 3.2 Create the user areas

The top level is root-owned; each user's directory is created once by root, the repo areas by the user or
lazily by tools:

```bash
host-setup/scripts/mdt-cow-init-user.sh /srv/devcontainers-cow alice            # root: user dir + shared/
mkdir -p /srv/devcontainers-cow/alice/myrepo                                     # user, or the tool
```

The script refuses a mountpoint that is not a btrfs mount point and uid 0, creates `<mount>/<user>` (`0750`, owned by that user) and
`<mount>/<user>/shared`, and sets nothing else. A container's `vscode` user is remapped to the host uid of the
person who opens it (devcontainer `updateRemoteUserUID` defaults to true), so host ownership by that user is
what makes the area writable. [K]

## 4. Layout and properties

Principle: *directories are the namespace; subvolumes are the units you snapshot, delete atomically or give a
property.* Start with plain directories for the namespace levels.

```
<mount>/                           top level, root:root 0755, chattr +i while unmounted
  <user>/                          per-user area, <user>:<user> 0750; the ONLY level a container mounts
    shared/                        cross-repo for this user: ISOs, golden VM images, mostly write-once
      upstream-iso/                subvolume; files named by sha256; compression none
      vm-disks/base/               subvolume; golden raw images, read-only after creation
    <repo>/                        per-repo area, plain directory
      assay/base/                  subvolume: self-contained clone + warm environment (section 6.1)
      assay/snap-*/                subvolumes (snapshots of base), deleted after each run
      worktrees/<instance-id>/     subvolume per worktree: compressed scratch, linked as .store
      vm-disks/run/                per-test disks: cp --reflink=always ../../shared/vm-disks/base/x.raw run-X.raw
```

Why this shape:

- **Addressed by target repo, not by the container you run in.** An assay run in one repo's devcontainer may
  judge another repo; it uses `$MDT_COW_ROOT/<other-repo>/assay/...`. `MDT_COW_ROOT` is therefore per user
  (`<mount>/<user>`), and the repo name is part of the path the tool builds.
- **`<user>/shared/`** holds what several of that user's repos reuse, so each ISO or golden image exists once,
  and it sits inside the one directory a container mounts, so reflinks and snapshots from it work. Sharing
  *between users* is deliberately not provided; if needed, root reflink-copies.
- **Plain directories at user and repo level.** A subvolume at `<user>/<repo>` would allow a per-repo qgroup
  limit later, but nested subvolumes inside it are then excluded from a snapshot of the parent (section 2.4).
  A plain directory converts cheaply later: `btrfs subvolume create new && cp -a --reflink=always old/. new/`.
- **"A different volume for compressible work" is not a separate filesystem.** Compression is a property on a
  directory or subvolume (mount without `compress=`), inherited by new files. A separate filesystem would
  only add isolation, which section 2.5 covers by LV.

| Path | Property | Why |
|---|---|---|
| `<user>/shared/upstream-iso` | `compression none` | ISOs are already compressed [M: a 2.3 GB TrueNAS ISO compressed to 97% with zstd -3] |
| `<user>/shared/vm-disks` | `compression zstd` | sparse/zero-heavy raw images compress well |
| `<user>/<repo>/assay/base` (snapshots carry it) | `compression zstd` | sources, venvs, caches; on a slow disk fewer bytes written wins |
| `<user>/<repo>/worktrees/<instance-id>` | `compression zstd` | compressible per-worktree scratch |

```bash
btrfs property set <user>/shared/upstream-iso compression none
# A new subvolume's root does NOT inherit its parent directory's property [R]: set it on the subvolume
# itself; snapshots of it carry the property along [R].
btrfs property set <user>/<repo>/assay/base compression zstd
```

**Level.** `compress=zstd:N` (N 1 to 15, default 3) is a mount option and filesystem-wide. A per-directory
level works on kernel 7.2.9 with btrfs-progs 7.1: `btrfs property set <dir> compression zstd:3` was accepted
and read back as `zstd:3` [R]; older versions are untested [T]. Compressible data (48 MB of one repeated byte)
took far less space under `compression zstd` than under `none` [R]. Existing files are recompressed with
`btrfs filesystem defragment -czstd`. Decompression speed
barely depends on the level; only write CPU does.

**CoW and VM images.** Copy-on-write fragments large randomly-written files such as VM disks, and `chattr +C`
(no-CoW) also disables checksums and compression (a directory that already has a compression property
**refuses** `+C` [R]), and snapshots re-enable CoW on first write. For short-lived
test VMs plain CoW is acceptable; qcow2 backing files (`qemu-img create -b base.qcow2 -F qcow2 run.qcow2`) are
an instant, filesystem-independent alternative to reflink copies.

## 5. Contract for tools (assay, ciu, ...)

- **Root:** read `MDT_COW_ROOT` (per user, set by the devcontainer, section 7). If unset, the feature is off;
  say so, do not guess a path.
- **Verify before use:** `stat -f -c %T "$MDT_COW_ROOT"` must print `btrfs`. Refuse otherwise. Never fall back
  silently to copying or to the root filesystem. If `$MDT_COW_ROOT` is missing, fail; do not create it.
- **Address by target repo:** build `$MDT_COW_ROOT/<repo>/...`; create repo directories lazily.
- **Clean up:** snapshots and run disks are deleted after the run (`btrfs subvolume delete`). Judge success by
  the **exit code**: as a non-root user `btrfs` prints `WARNING: cannot read default subvolume id: Operation not
  permitted` on every delete even though it succeeded [R]. A read-only snapshot must first be made writable
  (`btrfs property set -ts <snap> ro false`, allowed for its owner) before a user can delete it [R]. Deletion is
  **asynchronous**: `btrfs subvolume delete -c` commits afterwards, `btrfs subvolume sync <path>` waits for the
  cleanup, and the space returns later (section 8).
- **One tool setting:** e.g. assay `[isolation] snapshot_backend = "btrfs"`; the root always comes from
  `MDT_COW_ROOT`, never from a per-repo path.

## 6. Recipes

### 6.1 assay: warm base plus snapshots

Goal: no new checkout per run. Measured with a 273 MB repository [M]: a self-contained `git clone
--no-hardlinks` took **26.7 s wall for 0.44 s CPU** (IO-bound on a slow virtual disk); a `git checkout
--detach <older commit> && git clean -ffdx` in an existing clone took **1.4 s**. A warm base turns the first
into the second, and can also carry the installed environment (venv, `node_modules`), likely the larger saving
(not measured).

```bash
A="$MDT_COW_ROOT/<repo>/assay"
# once per repo, refreshed with `git -C "$A/base" fetch`:
btrfs subvolume create "$A/base" && btrfs property set "$A/base" compression zstd
git clone --no-hardlinks --no-checkout <source repo> "$A/base"       # self-contained, NOT --shared
# per run / per mutation worker:
btrfs subvolume snapshot "$A/base" "$A/snap-<id>"
git -C "$A/snap-<id>" checkout --detach <sha> && git -C "$A/snap-<id>" clean -ffdx
# afterwards: rollback is deleting it
btrfs subvolume delete "$A/snap-<id>"
```

Why the base is a **self-contained clone, not `git clone --shared`**:

- `--shared` records the source's object store in `.git/objects/info/alternates` as an absolute path. Every
  consumer (host, devcontainer, sibling containers) must resolve it identically, the source must never be
  moved or deleted, and a `git gc` or prune there can remove objects the snapshots still need, so a pinned
  ref would be required. A self-contained clone has none of these couplings; the snapshot carries its own
  objects, extent-shared with the base for free.
- The source usually lives on the host's normal filesystem; with alternates every snapshot keeps reading its
  objects from there, which defeats the point of a separate filesystem.
- Cost: one full clone per repo, plus a periodic `git fetch`. Only diverging data uses new space.
- Do not snapshot a git **worktree** as base: its `.git` file points at one admin directory under the main
  repo, and two copies would corrupt it. A clone has a real `.git` directory.

**Do not copy a `.git` directory wholesale.** On the reference host a repository's `.git` was **60 GB** because
build caches (BuildKit, artifact caches) are stored inside it, against a 157 MB object store [M]. Use `git
clone`, never `cp -a .git`. The first copy from the normal filesystem onto btrfs cannot be a reflink anyway;
reflinks and snapshots start from the base.

Use `btrfs subvolume snapshot` for base to snapshot (atomic, no per-file work); use `cp --reflink` for single
files such as VM disks. A directory that should become a subvolume converts instantly with `btrfs subvolume
create new && cp -a --reflink=always old/. new/` [R]. A snapshot of a subvolume that contains a nested
subvolume shows the nested one as an **empty directory** [R].

Container-level run on r1002 [R]: as an unprivileged uid in a plain Docker container with the user-level bind
(a synthetic 25-commit, 15 MB-`.git` repository, warm page cache): creating the warm base took 1.9 s; eight
parallel snapshot-plus-checkout workers took **0.13 s**; eight full clones took 0.28 s on the container's own
filesystem and 0.57 s on the btrfs. Space: each snapshot shared the base's extents (172 KiB exclusive of 33.5
MiB). On a fast disk with a warm cache the advantage is 2x to 4x; the 26.7 s versus 1.4 s above is the cold,
slow-disk case.

**Warm environment.** Put the installed venv, `node_modules` and caches in the base, not only the checkout, but
mind absolute paths: a snapshot lives at a different path. Verified for a venv [R]: the snapshot's own `python`
resolves `sys.prefix` to the snapshot, but console scripts keep their shebang
(`#!/…/assay/base/venv/bin/python3`), so `snap/venv/bin/pip` silently runs the **base's** interpreter and
site-packages (and breaks when the base is replaced). Run tools as `snap/venv/bin/python -m <tool>`, recreate the
venv in each snapshot (cheap), or use a relocatable venv (`uv venv --relocatable` [K]). Other caches that record
absolute paths need the same check. [T for non-venv caches]

### 6.2 Worktree scratch (ciu)

A worktree-creating tool can make `$MDT_COW_ROOT/<repo>/worktrees/<instance-id>` a subvolume, link it into the
worktree as an ignored `.store` symlink, export its path, and `btrfs subvolume delete` it together with the
worktree (one fast call instead of `rm -rf`: 0.01 s for a small one [R]). The symlink dangles afterwards [R], so
remove it too. Repository checkouts stay on the normal filesystem.

### 6.3 ISOs and VM disks

ISOs: write once into `<user>/shared/upstream-iso`, named by sha256, then read-only; no compression. VM disks:
a golden image in `shared/vm-disks/base`, per-test copies with `cp --reflink=always` into `<repo>/vm-disks/run`.

## 7. Devcontainer integration

One `mounts` entry and one `containerEnv` entry, **added only after the host mount works**. `mounts` entries
are `--mount`, which refuses to start the container when the source does not exist, so adding the line early
breaks every rebuild, and that same behaviour is the fail-closed guarantee afterwards.

```jsonc
// in "mounts":
"source=/srv/devcontainers-cow/${localEnv:USER},target=/srv/devcontainers-cow/${localEnv:USER},type=bind,bind-propagation=rslave",
// in "containerEnv":
"MDT_COW_ROOT": "/srv/devcontainers-cow/${localEnv:USER}",
```

The shipped templates carry exactly these two lines **commented out** (the host prerequisite does not exist on
every host): uncomment both after section 3. Two ways to deploy the cockpit config, both ready in
`templates/` (choose with `templates/README.md`):

| | Per-repo | Host-side folder |
|---|---|---|
| Config lives in | each repo's `.devcontainer/` | one folder on the host (own git repo recommended) |
| Template | `templates/devcontainer.json` | `templates/host-folder/devcontainer.json` |
| Containers | one per repo | one cockpit seeing the repos you choose |
| Cow lines | uncomment per repo | uncomment once |

The cow design is identical in both: nothing in `<mount>/<user>` depends on which repo (or folder) the
container was opened from, because the mount and `MDT_COW_ROOT` use only `${localEnv:USER}` and tools append the
target repo (section 4).

Details:

- **Same path on both sides**, so sibling containers started through docker-outside-of-docker can bind
  `/srv/devcontainers-cow/<user>/...` using the identical host path.
- **A host `umount` of the whole filesystem does not reach a bind of a subdirectory.** [R, shown both with
  `unshare` and with a real Docker container using `bind-propagation=rslave`] The host `umount` *succeeds* (it
  is not refused as busy), the container keeps reading and writing the same superblock, and after the host
  mounts the device again the host sees the container's writes. Consequences: the container can never silently
  fall back to the root filesystem; but the *device stays busy* while any container holds the bind, so stop
  the containers before maintenance that needs the device (`lvremove`, `btrfs check`, resizing a partition);
  and a tool's `stat -f` check cannot tell that the host has unmounted. `rslave` still matters for mounts
  *under* the bind.
- **Privileges:** none beyond Docker's defaults are needed [R]. In a container started as a non-root uid, with
  no added capabilities and the default seccomp profile, with `--cap-drop ALL`, and with the templates'
  `SYS_ADMIN` + `seccomp=unconfined`, every operation below behaved identically (a non-root process has no
  effective capabilities in any of them). The cow feature therefore does not depend on, or justify, those
  loosened settings. As a non-root user these work [R]: `subvolume create/snapshot/delete` (delete needs
  `user_subvol_rm_allowed`), read-only snapshots, `property set`, `cp --reflink`, `chattr +C`, `btrfs filesystem
  df/du`, snapshots and deletes through a bind of the user directory. These need root [R]: `subvolume list`,
  `chattr +i`; and, by design, mkfs, balance, scrub and qgroups. (`chattr +i` on mountpoints is a host task.)
- **`btrfs-progs`** is not in the image yet; install it in the running container when first needed.
- **More than one person on the host:** each gets their own `<user>` directory and sees only that. Binding the
  whole filesystem is for a deliberate admin container only.

## 8. Failure behaviour in one place

| Situation | Result |
|---|---|
| Filesystem not mounted at container start | `--mount` refuses (no `<user>/` below the empty immutable mountpoint): the rebuild fails loudly |
| Host unmounts the filesystem while a container runs | the container keeps using the same superblock [R]: no fork onto the root disk, but also no signal to the tools; the device stays busy |
| User directory missing | `--mount` refuses; tools do not create it under the top level |
| Wrong filesystem (somebody mounts ext4 there) | `stat -f -c %T` is not `btrfs`; tools refuse |
| Container started with the legacy `-v` form while the filesystem is missing | refused with `mkdir ...: operation not permitted` thanks to `chattr +i` [R]; **without** the flag Docker would create a root-owned directory on the root disk |
| Filesystem full | ENOSPC for every user of it, and unprivileged cleanup still works [R]: `rm` of files plus `sync` frees space at once; deleting a subvolume frees it only after the asynchronous cleanup (about 20 to 40 s for 900 MiB). Monitor `btrfs filesystem usage` and delete snapshots promptly |

## 9. Verification

`host-setup/scripts/mdt-cow-selfcheck.sh` builds a throwaway btrfs on a loop file under a scratch directory
and exercises the **[R]** items (30 expectations passed on r1002, 2026-10-22; install `btrfs-progs` first): ID 5 and subvolume IDs, `subvol=` and bare mounts, a conflicting second
mount, unprivileged snapshot/delete/reflink/property, property inheritance by a new subvolume and by a
snapshot, a per-directory compression level, mounting over an immutable directory, `-x` and `mv` across
subvolumes, reflink across a bind of a subdirectory, and propagation of a host unmount into a bind
namespace. It touches nothing but its scratch directory (`--preflight-only` just reports whether btrfs
and the tools are available). Run it on a scratch host first, never as the first thing on a production host.

Beyond the script, the whole container path was exercised once on r1002 [R] with throwaway scripts (not
shipped): the real mount path with `LABEL=` mounting and the `chattr +i` guard, `mdt-cow-init-user.sh` (success,
idempotent re-run, refusals), a Docker container as an unprivileged uid with the user-level `rslave` bind, the
privilege matrix, the assay, worktree and shared/ISO recipes, another uid being denied (`0750`), and the
fail-closed start with the filesystem unmounted. Not exercised: the real cockpit image and the Dev Containers
tooling (`${localEnv:USER}` substitution, `updateRemoteUserUID`).

Checks only a real deployment can make:

1. The reboot test with the device missing: boot completes, ordered services fail closed.
2. A second user: a container for user A cannot reach user B's area or the top level.
3. `iostat -x` and PSI during a snapshot loop while a latency-sensitive service runs: its `io.pressure` stays
   at its baseline.

## 10. Open items

- Per-directory compression **level** on kernels older than 7.2 (section 4) [T].
- Whether to stop containers automatically before device maintenance (section 7: the bind pins the device).
- `chattr +C` on specific database files: only if CoW fragmentation shows up in measurements.
- `btrfs-progs` and `rsync` in the image, if tools should not depend on installing them.
- Whether to ship `mdt-cow-init-user.sh` through `host-setup/install.sh`: today it is a script you run.
