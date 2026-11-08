# mounttrace: layered mounts, mount namespaces and propagation on Linux

A guide to the question *"which filesystem does this path really reach, from whose point of view,
and what changes when someone mounts or unmounts later?"*, plus the reference for
[`mounttrace.py`](mounttrace.py), a read-only analyser that answers it from the kernel's own tables.

Scope: Linux (written against Debian 13, kernel 7.x, util-linux 2.41, btrfs-progs 6.x). The kernel
behaviour quoted here is the same on any kernel ≥ 4.11 (≥ 4.18 for the btrfs subvolume ioctl).
Statements marked **[T]** are reproduced by a test in [`tests/test_mounttrace.py`](tests/test_mounttrace.py)
against real mounts: in throwaway user and mount namespaces, or, for btrfs and Docker, as root in a
disposable QEMU guest (§9.6).

---

## 0. Quick start

```bash
# Where do writes to this path go, and what is stacked or hidden along the way?
python3 mounttrace.py /srv/data/projects --submounts

# The same path from a container's point of view, compared with the host's
pid=$(docker inspect -f '{{.State.Pid}}' mycontainer)
sudo python3 mounttrace.py /data/projects --pid "$pid" --host-path /srv/data/projects

# Every mountpoint under a tree: host vs container (same path on both sides)
sudo python3 mounttrace.py /var/lib/pterodactyl --pid "$pid" --audit-prefix /var/lib/pterodactyl/volumes

# Same, but the container sees the tree under another path
sudo python3 mounttrace.py /home/container --pid "$pid" \
    --audit-prefix /var/lib/pterodactyl/volumes/$uuid=/home/container

# Before mounting something on the host: where will it show up?
sudo python3 mounttrace.py / --pid "$pid" --predict /var/lib/pterodactyl/volumes/$uuid/WS/Saved/Logs

# Keep watching (prints a new report whenever an inspected mount table changes)
sudo python3 mounttrace.py /var/lib/pterodactyl/volumes --pid "$pid" \
    --audit-prefix /var/lib/pterodactyl/volumes --watch 1
```

Exit status: `0` report printed with no ALERT, `3` at least one ALERT, `1` error, `2` usage error.
You can therefore use it as a check in scripts: `mounttrace.py … --audit-prefix … || alert`.

---

## 1. Why this is a hard question

On a host with ext4, btrfs subvolumes, ZFS datasets, tmpfs, bind mounts and containers, one path can
be served by several layers, and one directory can be reached through several paths. Three separate
questions get mixed up:

| Question | Meaning | Native tool gets you… |
|---|---|---|
| **Effective mount** | Which filesystem (and which directory in it) actually receives a write to `/a/b/c` right now? | `findmnt -T /a/b/c` |
| **Mount ancestry** | Which mount boundaries are crossed resolving `/a/b/c`, and how are those mount objects linked? | partly: `findmnt --id`, by hand |
| **Hidden mounts** | Which mounts still exist but cannot be reached because something was mounted over them or over an ancestor directory? | partly: `findmnt --shadowed` |

Containers and services add a fourth and fifth:

| Question | Meaning |
|---|---|
| **Whose view?** | Each process can have its own mount namespace. The host, a service with `PrivateTmp=`, and a container can resolve the same path to different filesystems. |
| **What happens later?** | Will a mount or umount made on the host *after* the container started show up inside it? That is **mount propagation**, and it is decided per mount, not per container. |

`findmnt` exposes almost all the raw data, but nothing packages "explain what this process gets for
this pathname, and what will change" into one command. `mounttrace.py` does that, and only reads
`/proc`.

The authoritative source for all of this is **`/proc/<pid>/mountinfo`**, not `/etc/fstab` (intent),
`mount`/`/proc/mounts` (fewer fields), `df` (one line per path, after deduplication) or `lsblk`
(block devices only, which says nothing about tmpfs, binds or namespaces).

---

## 2. The mental model

### 2.1 Mounts are objects; a mountpoint is "(parent mount, directory)"

A *mount* is a kernel object that attaches **a directory of a filesystem** (its root, often but not
always the filesystem's top) at **a directory inside another mount** (its mountpoint). So:

- Mounting at `/srv/x` really means "on the dentry `x` as reached *through the mount that serves
  `/srv`*". If you later mount something over `/srv`, the older mount at `/srv/x` still exists, but
  you can no longer reach it by name: it is **hidden**.
- Several mounts can sit at the same pathname, each stacked on the previous one. The kernel records
  this as "mount B's parent is mount A, mountpoint = A's root". Only the top of the stack is
  reachable.
- A bind mount is just another mount object whose root is some directory of an existing filesystem.
  It is **not** a symlink and **not** a subscription to a path: it pins that directory (dentry). If
  the source directory is later renamed, the bind follows the rename. If something is later mounted
  at the source *path*, see §7.3 F/G.

### 2.2 Anatomy of a `mountinfo` line

```
 36 35 98:0 /mnt1 /mnt2 rw,noatime master:1 shared:7 - ext3 /dev/root rw,errors=continue
 (1)(2) (3)   (4)   (5)      (6)       (7)          (8) (9)    (10)        (11)
```

| # | Field | Notes |
|---|---|---|
| 1 | mount ID | Unique system-wide while the mount exists. A container's *copy* of a host mount has its own ID. IDs are reused after unmount. (Kernel ≥ 6.8 also has 64-bit never-reused IDs via `statmount`/`listmount`; `findmnt --kernel=listmount -o UNIQ-ID`.) |
| 2 | parent ID | The mount this one is attached to. For the namespace root, a mount outside the visible table. |
| 3 | major:minor | `st_dev` of the **superblock**. Same value means same filesystem instance. Each tmpfs mount is its own superblock, so it has its own dev. All binds of one tmpfs share its dev. On btrfs, mountinfo shows one dev for all subvolumes, while `stat` shows a per-subvolume `st_dev`. |
| 4 | root (FSROOT) | Which directory of that filesystem is this mount's root. `/` for a normal mount. For a bind of `/data/sub`, this is `/sub` (relative to the filesystem). On btrfs it includes the subvolume path. |
| 5 | mountpoint | Relative to **the process's root** (`chroot`/container root). Mounts outside that root are not listed at all. |
| 6 | mount options | Per-*mount* (VFS) flags: `ro`/`rw`, `nosuid`, `nodev`, `noexec`, atime. |
| 7 | optional fields | Propagation: `shared:N`, `master:N`, `propagate_from:N`, `unbindable`. None means private. |
| 8 | `-` | Separator. |
| 9 | fstype | `ext4`, `btrfs`, `zfs`, `tmpfs`, `overlay`… A bind shows the fstype of what it binds. |
| 10 | source | Device, dataset, or any string the mounter chose. Soulmask tags its per-instance tmpfs `soulmask-pi-<uuid8>` here. May be empty. |
| 11 | superblock options | Filesystem-wide options (`size=`, `uid=`, `subvol=`, `upperdir=`). Shared by every mount of that superblock. |

Spaces, tabs, newlines and backslashes in paths are escaped as `\040`, `\011`, `\012`, `\134`.

### 2.3 Read-only has two layers

`ro` in field 6 makes **this mount** read-only. Other binds of the same filesystem can still be
writable (`mount -o bind,remount,ro`). `ro` in field 11 makes **the superblock** read-only, so
every mount of it is read-only (`mount -o remount,ro /mnt/tmpfs`). mounttrace prints which layer it
is: `RO(mount)`, `RO(superblock)` or `RO(mount+superblock)`. Neither says anything about
permissions, quotas, ZFS `readonly=` properties, LSMs or a full disk. A writable mount is not a
write *permission*.

### 2.4 What identifies "the same thing" across views

| Identity | Comparable across mount namespaces? | Use |
|---|---|---|
| mount ID | IDs are global, but each namespace has its **own copies**, so the IDs differ | Following parent chains within one view |
| dev (major:minor) | Yes, superblocks are global | "Same filesystem instance?" |
| dev + FSROOT + suffix | Yes | "Same directory?", from mountinfo alone |
| `stat` dev + inode via `/proc/<pid>/root/...` | Yes (strongest; needs root) | "Literally the same object?" |
| peer group N (`shared:N`, `master:N`) | **Yes**, groups span namespaces | "Are these two mounts linked for propagation?" |

mounttrace compares with `stat` dev+inode when it can and falls back to mountinfo identity, and says
which evidence it used.

### 2.5 btrfs subvolumes: filesystem boundaries without a mount

A btrfs **subvolume** is an independent file tree inside one btrfs filesystem: its own inode
namespace (its root directory is always inode 256), its own `st_dev` as reported by `stat`, and the
unit of snapshots, quotas (qgroups) and the read-only flag. You reach one in two ways:

| Way | What mountinfo shows | Example |
|---|---|---|
| **Mounted** with `subvol=` / `subvolid=` | a mount entry with `FSROOT='/@a'` and superblock options `subvol=/@a,subvolid=256` | fstab `subvol=pterodactyl` on `/var/lib/pterodactyl` |
| **Walked into** as a nested directory of a mounted subvolume | **nothing**: no mount entry; `findmnt -T` reports the outer mount | `/a/nested` where `nested` is a subvolume inside `@a` |

Consequences, all **[T]** (real btrfs in the test VM):

- **mountinfo dev is the same for every mount of the filesystem**, but `stat` shows a different
  `st_dev` per subvolume. So compare btrfs objects with `stat` dev+inode, never with mountinfo dev
  alone. mounttrace's fallback identity includes the FSROOT path, which stays unique.
- **A read-only snapshot fails writes with `EROFS` although its mount is `rw`.** mountinfo cannot show
  this, because it is a subvolume flag. mounttrace reads it with the unprivileged
  `BTRFS_IOC_GET_SUBVOL_INFO` ioctl and prints `READ-ONLY`, plus `snapshot of <uuid>`.
- **Snapshots do not include nested subvolumes.** In the snapshot, the nested subvolume's place is an
  empty stub directory with **inode 2**, in which nothing can be created. mounttrace marks it
  `EMPTY-SUBVOLUME STUB`. This matters for backups: data in a nested subvolume is not in a snapshot of
  its parent.
- **Mount events attach to (mount, directory)**. A tmpfs mounted at `/top/@a/plain` (via a mount of
  the top level) does **not** appear at `/a/plain` (via a separate `subvol=@a` mount of the same
  directory), unless the two mounts are propagation peers, for example because one is a bind of the
  other made while shared. `--predict` reports `nothing appears` for such a second mount.
- `findmnt` shows the subvolume in SOURCE as `/dev/x[/@a]`.

mounttrace on btrfs adds `subvol=`/`subvolid=` to every btrfs mount line, prints nested subvolume
boundaries in the leaf → root list (`btrfs nested subvolume id=… (boundary without a mount)`), and
names the subvolume that receives writes in WRITE ROUTING. By hand: `btrfs subvolume show PATH`,
`btrfs subvolume list -o MOUNTPOINT`, `btrfs property get -ts PATH ro`, `stat -c '%d %i' PATH`.

---

## 3. Walking a path: root → leaf, reported leaf → root

The kernel resolves `/srv/game/cache/file.dat` **from the process's root towards the leaf**. At
each component it asks: is this dentry a mountpoint *in the mount I am currently in*? If yes, it
steps onto the topmost mount stacked there. It cannot start at the leaf, because the leaf's mount is
only known after resolving everything above it.

What you usually want to *read* is the opposite: the leaf's answer first, then why. So mounttrace
**computes root → leaf and prints leaf → root**:

```
EFFECTIVE MOUNT BOUNDARIES (leaf -> root; the kernel resolves root -> leaf):
  /srv/game/cache  ->  #55 tmpfs 'tmpfs' FSROOT='/' ...
  /srv/game        ->  #42 btrfs '/dev/nvme0n1p2[/@game]' FSROOT='/@game' ...
  /srv             ->  #31 zfs 'tank/data' ...
  /                ->  #25 ext4 '/dev/sda2' ...
```

One line is printed each time the effective mount *changes*. It is keyed on the mount ID, not the
fstype, so a bind of ext4 onto ext4 still shows up as a boundary.

### 3.1 Pathname ancestry ≠ kernel parent chain

The **KERNEL PARENT-ID CHAIN** section follows field 2 from the effective mount upwards. It usually
matches the boundaries above, but not always:

- **Stacked mounts**: a mount stacked at the same path has the *covered* mount as parent. The chain
  then passes through a mount you cannot reach by name, at the same pathname.
- **Bind mounts**: a bind of `/data/sub` mounted at `/mnt/b` has as parent the mount serving `/mnt`,
  not the one serving `/data`. FSROOT is the only trace of where it came from. The original *source
  path* cannot be recovered with certainty, because the kernel records filesystem + directory, not
  the string you typed.

So the boundaries answer "what does pathname resolution cross", while the chain answers "how are the
mount objects wired".

### 3.2 Symlinks and other roots

mounttrace resolves symlinks one component at a time through `/proc/<pid>/root`, so an absolute
symlink inside a container restarts at the *container's* `/`. Calling `realpath` on the
`/proc/<pid>/root/...` string would escape to the host's `/`. If the leaf does not exist yet, the
missing remainder is analysed lexically, which is useful for "where would a new file go".

---

## 4. Hidden mounts

There are two ways a mount becomes unreachable, and mounttrace labels them. A third label names
the root cause when the hidden mount's parent mount is itself covered:

| Label | Situation | Example |
|---|---|---|
| `overmounted at the same mountpoint; #X is on top` | Another mount was stacked at the same path | `mount -t tmpfs A /d; mount -t tmpfs B /d` hides A **[T]** |
| `unreachable: resolving P ends in #X mounted at Q` | An **ancestor** directory was overmounted; the hidden mount's own path is different from the covering one | `mount C /x/sub; mount D /x` makes C unreachable although nothing is mounted *at* `/x/sub` any more **[T]** |
| `unreachable: its parent mount #P at Q is itself hidden (…)` | The mount hangs off a mount that was covered; whatever sits at its path now is stacked on the covering mount, not on it | `mount P /a; mount M /a/b; mount Q /a; mount R /a/b`: M is hidden because P is |

`findmnt --shadowed` lists only the first kind ("over-mounted by another filesystem"). To find all
of them, use mounttrace's HIDDEN section (`--all-hidden` for the whole table) or reason over the full
tree yourself (`findmnt --all --tree -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT`).

Hidden does not mean idle. Processes that already have files or a working directory open there keep
using it (§8).

**Looking underneath an overmount** without unmounting anything: a *non-recursive* bind of the
directory that contains the hidden layer shows that layer's contents, because a plain bind does not
copy the submounts:

```bash
mkdir -p /mnt/probe
mount --bind /var/lib/pterodactyl/volumes/$uuid /mnt/probe   # not --rbind
ls /mnt/probe/WS/Saved/Logs     # the on-disk directory beneath the per-instance tmpfs
umount /mnt/probe
```

---

## 5. The native toolbox (util-linux 2.41)

All options below exist in Debian 13's `findmnt` (checked with `findmnt --help` / `--list-columns`).

```bash
# Effective mount for any path (not only mountpoints), with the columns that matter
findmnt -T /deep/path -o TARGET,SOURCE,FSTYPE,FSROOT,ID,PARENT,VFS-OPTIONS,FS-OPTIONS,PROPAGATION

# One mount by ID, including parents you are walking up to
findmnt --all --list --id 42 -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT

# Over-mounted filesystems (only the same-mountpoint kind, see section 4)
findmnt --shadowed -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT

# Complete tree, and everything below one mountpoint (-R goes downwards only)
findmnt --all --tree -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT
findmnt -R /srv/projects -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT

# Another process's view: read its mountinfo ...
findmnt -N <PID> --tree -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT,PROPAGATION
# ... or resolve a path inside its namespace AND root (needs root)
sudo nsenter -t <PID> -m -r -- findmnt -T /deep/path -o ID,PARENT,TARGET,SOURCE,FSTYPE,FSROOT

# The raw truth, with propagation tags
grep ' /var/lib/pterodactyl ' /proc/<PID>/mountinfo
```

`findmnt -N <PID>` swaps in the other process's *table*, but a `-T <path>` lookup is not guaranteed to
resolve symlinks and existence inside that process's root. Use `nsenter -m -r` when the container's
root differs, or mounttrace, which resolves through `/proc/<pid>/root` without entering anything.

---

## 6. Mount namespaces: every process has a view

A mount namespace is a private copy of the mount table, made when the namespace is created
(`unshare -m`, `clone(CLONE_NEWNS)`, Docker, systemd sandboxing). From then on the copies are
**independent objects**. Whether later changes travel between them is decided by propagation (§7).

Where you meet them:

- **Containers**: Docker, Podman, Kubernetes, and Wings game servers through Docker.
- **systemd services**: `PrivateTmp=`, `ProtectSystem=`, `ProtectHome=`, `ReadOnlyPaths=`,
  `BindPaths=`, `TemporaryFileSystem=`, `PrivateMounts=`, `RootDirectory=`. The service writes into
  *its* view, and your shell may not see what it sees.
- **Some daemons** (older `docker.service` units had `MountFlags=slave`, and some backup or VPN
  agents) run in their own namespace.

How to compare: `readlink /proc/<pid>/ns/mnt` (same `mnt:[…]` = same view). mounttrace prints that ID
in every section header and says when `--pid` shares your namespace. `/proc/<pid>/mountinfo` is that
process's table, and `/proc/<pid>/root` is a door into its root *through its mount tree*.

---

## 7. Mount propagation

### 7.1 Concepts

A **peer group** is a set of mounts (possibly in different namespaces) that forward mount and umount
events *below them* to each other. Each mount has one of these propagation types:

| Type | mountinfo | Receives events from | Sends events to |
|---|---|---|---|
| **shared** | `shared:N` | its peers | its peers and their slaves |
| **slave** | `master:N` | group N (its master) | nobody |
| **shared + slave** | `shared:K master:N` | group N, and its peers K | its peers K and their slaves |
| **private** | none | nobody | nobody |
| **unbindable** | `unbindable` | nobody | nobody; also cannot be bind-mounted |

The `r` in `rshared`, `rslave`, `rprivate` means "apply to this mount *and every mount below it*".
These are **operations** (`mount --make-rslave /x`, Docker's `bind.propagation: rslave`). What the
kernel stores is the result: tags on each mount. You cannot tell from mountinfo whether someone
typed `--make-rslave`, used Docker's option, or got there another way. You can only see that a mount
is `master:N`. `propagate_from:N` appears when the master group is not visible in that namespace and
names the closest visible group that dominates it.

What exactly propagates: when a mount (or umount) happens at directory *d* inside mount **H**, the
kernel replays it in every mount that receives events from H's group, at the same dentry *d*, **if
that mount's root contains d**. A container bind whose FSROOT is `/var/lib/pterodactyl` receives
events for `/var/lib/pterodactyl/...` from the host's `/` mount group, but not for `/etc/...`.

Defaults you will meet:

- systemd makes the host's `/` **shared** at boot (everything below inherits it), so host mounts are
  normally in peer groups.
- `unshare -m` (the command) and Docker binds default to **(r)private** copies (Docker reports `rprivate` in `.Mounts`) **[T]**.
- A new namespace created by a *less privileged* user namespace turns shared mounts it copies into
  slaves.
- Docker refuses `rslave`/`rshared` bind propagation unless the source's mount is shared (or, for
  slave, shared or slave): `… is mounted on / but it is not a shared or slave mount`. **[T]**

### 7.2 File operations never propagate, and don't need to

Creating, writing, deleting, `chmod`, `chown`, renaming files: these are operations **on the
filesystem**, not on the mount table. Every mount that exposes the same filesystem directory shows
them immediately, whatever the propagation mode (rprivate included). Propagation is only about
**mount and umount events**, that is, which filesystems are attached where.

### 7.3 Worked examples: the same action under each mode

Setup for all examples: on the host, `/var/lib/pterodactyl` lives on the shared root filesystem. A
container (think Wings) got a bind of it at the same path, with propagation as listed. "Container
sees" is what a process inside it gets by path afterwards.

**A. The host creates or edits a file**: `echo hello > /var/lib/pterodactyl/volumes/abc/test.txt`

| Mode | Container sees |
|---|---|
| rprivate / rslave / rshared | `test.txt` containing `hello`, the same inode |

No mount event happened, so there is nothing to propagate.

**B. The host mounts a new tmpfs below the bind *after* the container started**:
`mount -t tmpfs t /var/lib/pterodactyl/volumes/abc/WS/Saved/Logs` (the directory held `old.log` on
disk)

| Mode | Container sees under `Logs` | Writes from the container go to |
|---|---|---|
| rprivate | the disk directory, with `old.log` | **disk**, while the host writes into the tmpfs. The two views have diverged. **[T]** |
| rslave | the new, empty tmpfs | the tmpfs, the same as the host **[T]** |
| rshared | the new, empty tmpfs | the tmpfs, the same as the host |

**C. The host unmounts a tmpfs that existed when the container started**:
`umount …/WS/Saved/Logs`

| Mode | Container sees afterwards |
|---|---|
| rprivate | **still the tmpfs**: its private copy keeps that tmpfs alive and writable. The host can no longer reach it by any path. This is a **ghost mount**. **[T]** |
| rslave / rshared | the disk directory: the umount propagated |

A ghost is not "possibly" retained. A private copy is a full mount and lives until that namespace
dies or unmounts it.

**D. The host's umount when a container copy is busy**

| Mode | Result |
|---|---|
| rprivate | The host umount succeeds. The container's copy is unaffected (C). |
| rslave / rshared | A normal `umount` **fails with `EBUSY` ("target is busy")** if any propagated copy is busy, for example a process in the container has its cwd or an open file there. `umount -l` (lazy) detaches it anyway. **[T]** |

So rslave buys consistency at the price that container activity can block host-side unmounts. The
Soulmask teardown handles this: a busy per-instance tmpfs stays recorded and is retried.

**E. A process inside the container mounts something**: `mount -t tmpfs t …/abc/test`

| Mode | Host sees |
|---|---|
| rprivate | nothing |
| rslave | nothing: slaves receive, they never send **[T]** |
| rshared | the mount, on the host and in every other peer **[T]** |

This is why rslave and not rshared is the right choice for "the container must follow host mounts":
a compromised or buggy container cannot plant mounts on the host.

**F. The host mounts a new filesystem *on the bind's source directory itself***:
`mount /dev/x /var/lib/pterodactyl` while the container runs

| Mode | Container sees |
|---|---|
| rprivate | the old directory (the bind pins the old dentry) |
| rslave | **the new filesystem**: the event happens on the host's `/` mount at the dentry `/var/lib/pterodactyl`, which is the container mount's root, so the copy lands on top of the container's bind **[T]** |

**G. The host replaces the *mount* that the bind came from**: the source was itself a mount
(e.g. `/var/lib/pterodactyl` already btrfs); the host unmounts it and mounts something else there

| Mode | Container sees |
|---|---|
| any | **the old filesystem, still alive**. The container's bind is a slave of the *old mount's* group, not of its parent's, so neither the umount nor the new mount reaches it. A non-lazy host umount succeeds, and the container keeps the old filesystem busy. **[T]** |

F vs G is the difference between "mount over a directory" and "replace a mount". Renames matter too:
`mv /var/lib/pterodactyl /var/lib/pterodactyl.old` while a container runs moves the container's bind
along with the dentry (its FSROOT now reads `….old`). A filesystem mounted at the new
`/var/lib/pterodactyl` is then outside that bind's root and never reaches it. **Stop every consumer
before migrating a bind source** (as [BTRFS-ROLLOUT.md](../gstammtisch-guide/BTRFS-ROLLOUT.md) §5
does).

**H. The receiving container already has its own mount at that path**

If a slave already has a mount at the dentry where a propagated mount arrives, the kernel (≥ 4.11)
*tucks* the propagated mount **beneath** the existing one. The container keeps writing into its own
mount, and the propagated one stays hidden until the container's own mount is removed. **[T]**
`--predict` reports this as `TUCKED`.

### 7.4 Verifying propagation, not assuming it

A Compose file saying `propagation: rslave` is intent. Check the running state:

```bash
docker inspect <name> --format '{{range .Mounts}}{{printf "%s -> %s : %s\n" .Source .Destination .Propagation}}{{end}}'
grep ' /var/lib/pterodactyl ' /proc/1/mountinfo /proc/<PID>/mountinfo
#   host:      ... shared:123 ...
#   container: ... master:123 ...      <- slave of the host's group: linked
```

The link has to hold **per mount**: on the host, at the mount that serves the path in question (the
mount *containing* the place where the future event happens); on the other side, at a mount that
receives that group's events and places the copy at the corresponding path. That receiver is not
necessarily the mount serving the path there now: a container's own tmpfs (or a retained copy) can
sit on top of the real receiver, and the copy is then tucked beneath it. mounttrace's `propagation:`
lines and `--predict` make this match over every receiver: chains through shared+slave mounts,
`propagate_from`, FSROOT containment, hidden receivers and tucking. "Both directions" is reported
only for a direct peer of the host group; a peer of a slave receives but never sends back. They cover
the namespaces you pass (`--pid`, `--peer`), and say so.

### 7.5 Where rslave matters outside Docker

- **systemd**: `PrivateMounts=yes` (and options that imply it, such as `ProtectSystem=`,
  `PrivateTmp=`, `ReadOnlyPaths=`) gives the service a namespace that receives host mount events but
  does not send its own back, which is slave semantics.
- **Kubernetes** `mountPropagation`: `None` = rprivate, `HostToContainer` = rslave,
  `Bidirectional` = rshared (privileged containers only).
- **Automounters, removable media, nested ZFS datasets, late network mounts**: anything that mounts
  *after* a long-running consumer (backup agent, file server, indexer) started. Without a slave link
  the consumer keeps seeing the empty directory underneath.
- **Daemons with their own namespace**: if `dockerd` itself ran with `MountFlags=slave` (old unit
  files), mounts it makes for containers would never be visible on the host, and host mounts made
  later would reach it only one way. Check with `findmnt -N $(pidof dockerd) -T /var/lib/docker -o PROPAGATION`.

---

## 8. Limits: what no mount-table analysis can tell you

- **Open files outlive name resolution.** A process that opened a file (or `chdir`ed, or `mmap`ed it)
  before a mount change keeps using the old object. Check holders with:
  - open fds: `grep -l "^mnt_id:\s*<ID>$" /proc/*/fdinfo/* 2>/dev/null` (each fd's `mnt_id`)
  - memory maps: `/proc/<pid>/maps` lists `dev inode` per mapping; match against the mount's dev
  - cwd/root: `ls -l /proc/<pid>/cwd /proc/<pid>/root`
  Game servers `mmap` their pak files at start, so old paks stay in use until the process restarts.
- **Lazily unmounted mounts** (`umount -l`) disappear from every mountinfo but live on while used.
  Only the open-file checks above find them.
- **Overlay filesystems** (container roots) write file data to `upperdir`. mounttrace prints it when
  the superblock options show it, but the physical location is a host path you then trace again.
- **Writable ≠ permitted.** Ownership, mode, ACLs, LSMs, quotas, ZFS `readonly=`/`quota`, a full
  filesystem, or a read-only *superblock* behind a writable bind all still apply.
- **Not atomic.** Host and container tables are read one after another, so a change in between can
  produce a one-off difference. Re-run, or use `--watch`.
- **Only inspected namespaces.** Propagation receivers in namespaces you did not pass are invisible
  to the tool, though they exist (and can still make a host umount fail with EBUSY, §7.3 D).
- **Ancient kernels** (< 4.11) could create several mounts with the same parent and mountpoint
  ("shadow mounts"). mounttrace warns and assumes the most recently attached one.

---

## 9. mounttrace reference

### 9.1 Requirements and safety

- Python ≥ 3.9, standard library only. A single file you can copy anywhere: `install -m 0755 mounttrace.py /usr/local/sbin/mounttrace`.
- **Read-only**: it reads `/proc/*/mountinfo`, `stat`s and `readlink`s through `/proc/<pid>/root`,
  and reads the state file. It never mounts, unmounts, enters namespaces or runs other programs.
- Inspecting another process's root needs root (ptrace access). Without it, symlink resolution and
  inode identity degrade to mountinfo evidence, with a NOTE.

### 9.2 Options

| Option | Effect |
|---|---|
| `PATH` | Absolute path inside the selected process's root (`/` is fine when only audits are wanted). |
| `--pid PID` | Analyse PID's view (its mountinfo and root). Default: the invoking process. |
| `--host-path P` | Also analyse `P` in the invoking view, compare the objects, and report the propagation link between the host mount serving `P` and the PID mount serving `PATH`. |
| `--peer PID:PATH` | Analyse another process's view at its `PATH` and compare it with the selected view (and with the host when `--host-path` is given). Repeatable. |
| `--audit-prefix H[=C]` | Pair every mountpoint under `H` (invoking view) with the same relative path under `C` in the `--pid` view (`C` defaults to `H`), and classify each pair (9.4). |
| `--predict P` | Where would a new mount made at `P` in the invoking view appear, in this view and in `--pid`/`--peer` views? Repeatable. |
| `--state-file F` | Audit a `soulmask_tmpfs` state file (§10.4). Works with or without `--pid`, and also without a state file (it then scans for leftovers). |
| `--volumes-root DIR` | Instance volume root for the state audit (default `/var/lib/pterodactyl/volumes`). |
| `--submounts` | List visible mounts below `PATH`. |
| `--all-hidden` | List every hidden mount, not only those on or below `PATH`. |
| `--no-resolve` | Treat paths lexically: no symlink resolution, no existence checks. |
| `--watch S` | Re-read the inspected tables (and the state file) every `S` seconds and print a full new report on any change. Exits `1` with a hint when a watched PID disappears (for example after a container restart gives it a new PID). Mount changes only, not file changes. |
| `--version` | Print the version. |

### 9.3 Report sections

| Section | Contents |
|---|---|
| header | View label, the path, the mount namespace ID, the resolved path, NOTEs (missing components, permission fallbacks) |
| EFFECTIVE MOUNT BOUNDARIES | §3, leaf → root, with `covers #…` lines for stacked mounts at the same path |
| WRITE ROUTING | The effective mount with both option layers, the object path inside its filesystem, overlay `upperdir`, read-only notice |
| KERNEL PARENT-ID CHAIN | §3.1 |
| HIDDEN MOUNTS | §4, with the reason |
| VISIBLE SUBMOUNTS | with `--submounts` |
| COMPARE | `SAME object` / `DIFFERENT objects` with the evidence used, read-only differences, and `propagation:` (`LINKED as slave/peer …` / `NOT LINKED …` plus what a new host mount at that path would do) |
| MOUNT AUDIT | §9.4 |
| SOULMASK TMPFS AUDIT | §10.4 |
| PREDICT | §9.5 |

Every line starting with `ALERT:` counts towards exit status 3, and the total is printed at the end.

### 9.4 Audit classes (`--audit-prefix`)

| Class | Meaning | ALERT? |
|---|---|---|
| `OK` | Both views have a mount at the pair, and it serves the same object | no |
| `DIVERGENT` | Both have a mount there, but they serve different objects | yes |
| `HOST-ONLY` | The host has a mount the PID view does not see: made after the process started without a propagation link, or unmounted only in that view | yes |
| `PID-ONLY` | The PID view keeps a mount the host does not have there: a ghost copy (§7.3 C), or a mount made inside that namespace (§7.3 E) | yes |
| `HOST-ONLY (same object)` / `PID-ONLY (same object)` | An extra mount layer that still reaches the same object, for example the container's own volume bind at `/home/container`, or a bind of a directory onto itself | no |

Each non-OK pair also prints both mounts and the propagation link. A `PID-ONLY` mount *can* be
intentional; the class says "different", not "wrong".

### 9.5 How `--predict` decides

1. Resolve `P` in the invoking view. The event attaches to the mount **H** that serves `P` (when `P`
   is already a mountpoint, the top of its stack), at filesystem path `rel` = FSROOT(H) + suffix.
2. If H has no `shared:N`, nothing propagates (slave-only mounts receive but never send).
3. Otherwise collect every mount (in all inspected views) that is a peer of N or a slave of N, and
   follow shared+slave mounts transitively.
4. For each receiver R: if `rel` is not inside R's FSROOT, the event does not reach R. Otherwise it
   lands at `R.mountpoint + (rel − FSROOT(R))`, and the result is classified as `APPEARS`, `TUCKED`
   beneath an existing mount (§7.3 H), `attached but hidden` behind a deeper mount, or invisible
   because R itself is hidden.
5. A later umount at the original location goes to the same set; a busy copy makes a normal umount
   fail with EBUSY.

### 9.6 Tests

Two levels, both against real kernels:

| Command | Where | Covers |
|---|---|---|
| `python3 tests/test_mounttrace.py` | anywhere with unprivileged user namespaces (devcontainer included); no root, no change to the real mount table | overmounts and ancestor hiding, bind FSROOT with spaces, symlinks, private ghosts, slave and shared links, the mapped game-container audit, tucking, EBUSY on busy slave copies, mounting over a bind source vs replacing the source mount, the Soulmask state audit, `--watch`. Root-only tests skip. |
| `testing/vm/run-vm-tests.sh` | a QEMU/TCG guest with its own kernel | all of the above as an unprivileged user, then **as root**: real btrfs on a loop device (nested subvolumes, ioctl IDs vs `btrfs subvolume show`, mountinfo vs `stat` dev, read-only snapshot, inode-2 stub, two mounts of one directory) and real Docker (default `rprivate` vs `rslave`: ghost and missing mounts, the link check, Docker refusing `rslave` on a private source). In this lane a skipped root test is a failure. |

Loop devices and swap are not namespaced, so the root tests refuse to run anywhere but in that guest
(`MOUNTTRACE_VM=1` plus `systemd-detect-virt --vm`).

### 9.7 Project layout and the test VM

```
scripts/mounttrace/
  mounttrace.py            the tool (single file, copy anywhere)
  mounttrace.md            this guide
  tests/test_mounttrace.py the suite (both levels)
  testing/vm/setup-vm-lab.sh   once per checkout: link artifacts/vm-lab into the CoW area
  testing/vm/run-vm-tests.sh   the VM lane
  artifacts/vm-lab -> $MDT_COW_ROOT/<repo>/vm-lab/<checkout>/mounttrace   (ignored symlink)
```

- **VM state on CoW storage.** `setup-vm-lab.sh` follows mdt-cockpit `docs/BTRFS-COW.md`: §5 (the
  root comes from `MDT_COW_ROOT`, it must be btrfs, no silent fallback) and §6.2 (the checkout stays
  on its normal filesystem; its scratch is an ignored symlink into the CoW area). `<checkout>` is
  `main` or the worktree name, so worktrees never share VM state.
- **Harness.** The lane drives the shared QEMU harness `scripts/debian-install-v2/testing/vm`
  (`vmctl`, the governed runner container in `$CGROUP_PARENT_DEV_GATES`) with
  `MDT_VM_PROJECT_DIR=scripts/mounttrace`. That gives the lane its own runner and this project's
  vm-lab.
- **Saved state and rollback.** The first run builds and seals the base image `mounttrace-ready`:
  Debian 13 cloud image, cloud-init completed then disabled, plus `btrfs-progs`, `docker.io` and a
  local `mt-busybox:1` image built from `busybox-static` (no registry pull). Every round boots a fresh
  qcow2 overlay on that base and destroys it afterwards, so each round starts from the identical
  saved state. `--keep` leaves the round's VM up for inspection, and `--rebuild-base` re-seals the
  base, for example after changing the package list.

```bash
scripts/mounttrace/testing/vm/setup-vm-lab.sh        # once per checkout
scripts/mounttrace/testing/vm/run-vm-tests.sh        # first run also seals the base
scripts/mounttrace/testing/vm/run-vm-tests.sh --keep # then: MDT_VM_PROJECT_DIR=$PWD/scripts/mounttrace \
                                                     #   scripts/debian-install-v2/testing/vm/run-vm-harness.sh ssh <run>
```

---

## 10. Case study: Soulmask tmpfs layer + Pterodactyl Wings

Background: [SOULMASK-TMPFS.md](../gstammtisch-guide/SOULMASK-TMPFS.md) and
[`soulmask_tmpfs-toggle.sh`](../gstammtisch-guide/files/usr/local/sbin/soulmask_tmpfs-toggle.sh) (with
`soulmask_tmpfs-setup.sh` / `-teardown.sh`).

### 10.1 Three views, not two

| View | Process | How it reaches the volumes | Propagation from host |
|---|---|---|---|
| **Host** | `soulmask_tmpfs.service`, your shell | `/var/lib/pterodactyl/volumes/<uuid>/…` | n/a (the source of events) |
| **Wings** | Wings container | the same path, via a bind of `/var/lib/pterodactyl` | **rslave** (compose `bind.propagation: rslave`) |
| **Game server** | one container per instance, created by Wings through Docker | `/home/container/…` = bind of `volumes/<uuid>` | **rprivate** (Docker default) |

Mounts the setup makes, all host-side, below the volume tree:

| Path in the instance volume | Backing | Recorded in the state file as |
|---|---|---|
| `/mnt/soulmask_tmpfs` | the shared tmpfs, populated from the `ROLE=main` instance | `RAMDISK=` (`READONLY=` 0/1) |
| `WS/Content/Paks`, `Engine`, `WS/Binaries`, … | **bind** of `RAMDISK/<rel>`: the same inode for every instance | `TARGET=` |
| `WS/Saved/Logs` (per-instance sections) | **own tmpfs** per instance, source tag `soulmask-pi-<uuid8>` | `PI_TARGET=` + `PI_DERIVED=` |
| `WS/Saved/world.db`, `WS/Config`, Steam dirs | disk (no mount) | – |

### 10.2 Which failure is which

| Symptom | Cause | How mounttrace shows it | Fix |
|---|---|---|---|
| Wings: `chown … read-only file system` after the tmpfs was torn down (outage of 2026-07-31) | Wings' **private** copy still held the old read-only binds (§7.3 C) | Wings audit: `PID-ONLY` / ghost ALERT under `volumes` | rslave on the Wings bind (in place); restart Wings once to drop existing ghosts |
| Wings: EROFS on chown although views agree | the tmpfs **is** read-only, and Wings' pre-boot chown walk calls `chown` unconditionally | audit `OK`, but `RO(superblock)` on the targets; state audit flags `READONLY=` mismatches | accepted trade-off `SOULMASK_TMPFS_READONLY=0` (or a Wings patch that skips no-op chowns) |
| A game keeps writing logs after teardown, but nothing is visible on the host | the game container's **private** copy of its per-instance tmpfs (§7.3 C) | mapped audit of that container: `PID-ONLY` ALERT at `WS/Saved/Logs` | teardown's preflight refuses while the game runs; stop the game first |
| A new instance volume or mount is not visible in a **running** game | rprivate: host mounts after container start never arrive (§7.3 B) | `HOST-ONLY` in the mapped audit, `NOT LINKED` in COMPARE | the order "mounts first, then start the container" (unit is `Before=docker.service`, Wings is restarted after setup) |
| Teardown `umount` reports busy | with rslave, a busy **Wings** copy blocks the host umount (§7.3 D) | `--predict` lists the Wings receiver; holders via `fdinfo` (§8) | teardown keeps busy per-instance mounts recorded; retry when Wings is idle |

### 10.3 Procedure (read-only)

Look up PIDs by **exact container name** only.

```bash
# A. Wings: PID and the propagation it really got
wings_cid=$(docker compose -f /root/ptero-wings-patched-13.4/docker-compose.yml ps -q wings)
wings_pid=$(docker inspect -f '{{.State.Pid}}' "$wings_cid")
docker inspect "$wings_cid" --format '{{range .Mounts}}{{printf "%s -> %s : %s\n" .Source .Destination .Propagation}}{{end}}'

# B. Wings vs host over the whole volume tree, plus the state file
sudo mounttrace /var/lib/pterodactyl/volumes --pid "$wings_pid" \
    --audit-prefix /var/lib/pterodactyl/volumes --state-file /run/soulmask_tmpfs.state
#    expect: only OK lines, "OK shared …: SAME inode", "OK per-instance …: own tmpfs",
#    "LINKED as slave" on non-OK pairs; exit 0

# C. One game container (its volume appears as /home/container)
uuid=<full-instance-uuid>
game_pid=$(docker inspect -f '{{.State.Pid}}' "$uuid")
sudo mounttrace /home/container --pid "$game_pid" \
    --audit-prefix /var/lib/pterodactyl/volumes/$uuid=/home/container
#    expect: "PID-ONLY (same object)" for the volume bind itself, OK for every tmpfs bind;
#    a PID-ONLY ALERT here after a teardown is a ghost

# D. All three views for one directory
p=/var/lib/pterodactyl/volumes/$uuid/WS/Saved/Logs
sudo mounttrace "$p" --pid "$wings_pid" --host-path "$p" --peer "$game_pid:/home/container/WS/Saved/Logs"
#    service on:  host, Wings and game: SAME object (the per-instance tmpfs)
#    service off, game stopped: host and Wings on the disk directory

# E. Before toggling: who will receive a new mount at that path?
sudo mounttrace / --pid "$wings_pid" --peer "$game_pid:/" --predict "$p"
#    expect: Wings "APPEARS", game "nothing appears" (rprivate)

# F. During a planned toggle (in a second terminal), see each change as it happens
sudo mounttrace /var/lib/pterodactyl/volumes --pid "$wings_pid" \
    --audit-prefix /var/lib/pterodactyl/volumes --state-file /run/soulmask_tmpfs.state --watch 1
```

Restarting the tmpfs service is a maintenance window (SOULMASK-TMPFS.md "Deployment"), so inspect
the current state (A–E) first instead of toggling just to test propagation.

### 10.4 What the state audit checks

- `RAMDISK=` is a tmpfs mountpoint, its read-only state matches `READONLY=` (and `READONLY=0` while the
  RAMDISK is read-only is flagged, because the Wings chown walk will fail).
- Every `TARGET=` is a mountpoint on the host, a tmpfs bind **served by RAMDISK** (same dev, same
  inode as `RAMDISK/<rel>`), and read-only when `READONLY=1`. All instances' binds of one `rel`
  resolve to the same inode.
- Every `PI_TARGET=` is its own tmpfs carrying the tag `soulmask-pi-<uuid8>`, writable, and no two
  instances share a superblock.
- With `--pid`, each target in that view is the same object as on the host. If it is missing there,
  the propagation link is missing.
- `PI_ERRORS>0` and the `PI_ERROR=` messages.
- **Leftovers, even without a state file**: tagged per-instance tmpfs mounts on the host that are not
  recorded (teardown finds those by tag; setup refuses to keep them), other unrecorded tmpfs mounts
  under the volume root (NOTE), and in the `--pid` view any visible tmpfs under the volume root with no
  matching host mount, which is a ghost.

The state file is the service's *intent*. The tool reports the kernel's *reality* when the two
disagree, which is why it is kept separate from the toggle script: one manages policy, the other
inspects.

### 10.5 Notes on the Wings compose file

- `/var/lib/pterodactyl/` with `rslave` is what makes 10.2 row 1 go away **for mounts made after Wings
  started**. Ghosts created before the change only go away with a Wings restart.
- `/var/lib/docker/containers/` with `rslave`: Docker writing logs and `config.v2.json` there is file
  activity (§7.2), which rprivate would show just as well. rslave matters only for **mounts** below
  that tree (for example per-container `mounts/shm` tmpfs with `ipc: shareable`). Check whether there are
  any with `findmnt -R /var/lib/docker/containers`. If there are, rslave also keeps Wings from pinning
  copies of them after a container is removed. Either way the comment should say "mount events", not
  "container create/remove".
- The btrfs migration of `/var/lib/pterodactyl` ([BTRFS-ROLLOUT.md](../gstammtisch-guide/BTRFS-ROLLOUT.md)
  §5) stops the games and Wings before `mv` + `mount`, which is required: §7.3 F/G and the rename
  note there show that a running Wings would either see the new filesystem while the games keep the
  old one, or keep serving the renamed `.old` tree. Afterwards, step B must come back with only `OK`
  lines.
- After that migration `/var/lib/pterodactyl` is the btrfs subvolume `pterodactyl` (§2.5). Wings'
  bind then shows `FSROOT='/pterodactyl'` and `subvol='/pterodactyl'`, and each game container's
  `/home/container` shows `FSROOT='/pterodactyl/volumes/<uuid>'`. A snapshot of that subvolume
  contains neither the tmpfs layers (mounts are never part of a snapshot) nor any nested subvolume
  created later below it, which leaves the inode-2 stub.
