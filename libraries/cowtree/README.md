# cowtree

`cowtree` is a stdlib-only workspace library for disposable trees and files. It
selects btrfs snapshots, reflink copies, or ordinary copies behind one `Run`
API ([backend behavior](DESIGN-GUIDE.md#one-adapter-for-three-storage-paths)),
keeps owner manifests and flock leases beside each run, and reaps only dead
flock-owned runs. While a lease is active it also refuses replaced run,
`trees/`, or run-owned tree paths when their identity check detects the change;
see the [ownership rules](DESIGN-GUIDE.md#ownership-and-identity). Cloning a
copy or reflink tree sealed with `Run.seal()` restores owner write permission
so the clone can be used as a candidate. A destination collision leaves the
existing path intact. It does not know about Git, assay, lanes, VMs, or Docker.
The embedded API advertises `API_VERSION = 1`.
The CLI provides `detect`, `survey`, `gc`, and `make-shared`. A run can declare
an external owner with `external_ref`; cowtree never reaps external runs.
Default `gc` scans only the configured CoW area's runs. It does not discover
plain-mode runs; it logs an INFO line directing operators to `cowtree gc
--scan DIR`, where `DIR` contains those runs. See the [GC and ownership
rules](DESIGN-GUIDE.md#ownership-and-identity).
The retained workspace plan is [COW-PLAN-OPUS-2026-10-30.md](docs/COW-PLAN-OPUS-2026-10-30.md).

## CLI exit statuses

The CLI uses distinct statuses for typed failures. Argument syntax errors keep
argparse's status `2`. The reasoning and mapping are in the [design
guide](DESIGN-GUIDE.md#cli-exit-statuses).

| Status | Code | Meaning |
| --- | --- | --- |
| 1 | `cow` | Generic cowtree failure |
| 2 | `usage` | Invalid command syntax or arguments |
| 3 | `root-invalid` | Configured root is invalid or unusable |
| 4 | `space` | Space admission refused the estimate |
| 5 | `path` | Path or destination was refused |
| 6 | `backend` | Filesystem backend operation failed |
| 7 | `lease` | Run lease could not be acquired |
| 8 | `busy` | A shared-cache key lock or the GC lock is held elsewhere |
| 9 | `key-missing` | The shared-cache key is not published |
| 10 | `tree-invalid` | A published tree no longer matches its manifest |
| 11 | `unrepairable` | An overlay entry cannot be repaired from the template |
| 12 | `level-unavailable` | No provisioning level is usable (or the pinned one is not) |
| 13 | `key-collision` | `ensure` found the key published with a different content identity |

ZFS uses the reflink backend when block cloning is available; cowtree does not
provide a native ZFS dataset backend.

The area is enabled by `MDT_COW_ROOT` when `COWTREE_MODE=auto` (the default).
With the root unset, cowtree logs one INFO line and uses a plain temporary
directory. `COWTREE_MODE=off` forces that plain path; any other mode is
refused. A configured but invalid area is refused before a run directory is
allocated. Space admission uses the consumer's input-derived `SpaceEstimate`
and the [backend-specific formulas](DESIGN-GUIDE.md#input-derived-space-admission);
it has no fixed floor. Btrfs admission also accounts for this process's pending
deletes from the preceding 60 seconds on the same filesystem, including across
subvolumes. GC reads
`owner.json` manifests up to 64 KiB and retains larger ones as invalid; cowtree
refuses to write larger manifests. See the [ownership
rules](DESIGN-GUIDE.md#ownership-and-identity).

## Shared keyed trees and provisioning levels

Beside disposable runs, cowtree provides **standalone** provisioning of
per-worker trees: publish a keyed read-only tree once, lease it, give each
worker a private writable copy of it, and clean up. No tool is required by
another: the library reads no environment variables, knows no slice, host
policy, run-gate or mdt-cockpit, and never mounts anything itself unless you
hand it a runner. Everything is plain configuration (`Settings`); host
policies are translated by adapters (below).

### Levels (what each needs)

`decide(Settings(...))` probes the host and picks the best usable level;
`Decision.report()` returns the probe result for every level and the chosen
one (log it). `level="A"`..`"D"` pins a level: a pinned level that is not
usable raises `level-unavailable` instead of degrading silently. With
`auto` and nothing configured there is no hidden default: it raises.

| Level | Mechanism | Needs (all explicit in `Settings`) |
| --- | --- | --- |
| A | one shared read-only tree plus a per-worker overlay | `shared_root` and `upper_root` (writable directories on one filesystem each) and either a usable docker socket (`overlay_via="docker"`, `docker_socket`) or the `mount` capability (`overlay_via="mount"`, CAP_SYS_ADMIN) |
| B | per-worker copy inside a tmpfs | `tmpfs_dir` on a tmpfs, for example `/dev/shm` |
| C | per-worker reflink clone | `clone_dir` on a reflink-capable filesystem (probed with a real FICLONE) |
| D | per-worker plain copy | `copy_dir`, any writable directory |

Every level hands each worker a private writable directory whose initial
content has the same manifest digest as the published tree
(`Provisioner.verify_worker`), so verdict-relevant semantics do not depend on
the level; only memory and speed do (A shares inodes and page cache, B-D copy).
Level A is observed through the container (`slot.mount_argv(dst)`, or `slot.path`
in mount mode), so `verify_worker` is for B-D.

### Standalone third-party quickstart

No vbpub knowledge needed. `pip install` your project (cowtree is stdlib only), then:

```python
from cowtree import Provisioner, Settings, decide

settings = Settings(                      # level="auto": best usable of A, B, C, D
    shared_root="/srv/mytool-trees",       # level A: must be on a tmpfs (else set allow_non_tmpfs_shared_root=True)
    upper_root="/var/cache/mytool/uppers",
    docker_socket="/var/run/docker.sock",  # level A (omit all three to skip it)
    tmpfs_dir="/dev/shm",                  # level B
    clone_dir="/var/cache/mytool/clone",   # level C
    copy_dir="/var/cache/mytool/copy",     # level D
)
print(decide(settings).report())           # probe result and chosen level

prov = Provisioner(settings)               # prov.level is the chosen level
key = "sha256-of-your-inputs"              # you supply the content/identity hash
prov.publish(key, lambda tree_dir: populate(tree_dir))   # built once, atomically
slot = prov.worker("run-42", 0, key)       # private writable tree per worker
use(slot.path)                             # levels B-D (level A: slot.mount_argv("/work/w0") for docker run)
prov.cleanup("run-42")                     # release workers; then `prov.gc(idle_seconds=3600)` now and then
```

The same pieces are available separately: `Cache` (publish / lease / verify /
gc), `overlay_volume` / `worker_volume` / `create_worker` / `reuse_worker` /
`reset_worker` / `repair_entry` / `upper_full` (the overlay recipe), and the CLI:

```console
cowtree probe --shared-root D --upper-root U --docker-socket S --tmpfs-dir /dev/shm --copy-dir C
cowtree cache survey|gc|verify --root D [--allow-non-tmpfs]
```

### Behaviour of the pieces

- **Publish.** `ensure(key, build)` takes an exclusive flock on `.locks/<key>`,
  builds into `<key>.tmp-<token>/tree`, writes the `COMPLETE` verification
  manifest (a sha256 over every entry's type, path, mode, size and content
  hash or symlink target), and renames the directory into place. Concurrent
  callers wait, then reuse the winner's tree; a failed build removes its scratch.
  `seal=True` additionally chmods the tree read-only (off by default: the mount
  is the read-only protection, and a chmod forces full-file copy-ups; the
  chmod caveat below applies). `verify(key)` re-hashes the tree and raises
  `tree-invalid` on drift. `survey`, `verify` and `require_complete` are
  read-only: they never create `.locks` or anything else, so they work on a
  root the caller cannot write.
- **Key collisions.** Keys are chosen by the caller; `ensure` trusts them. Use
  a collision-resistant hash (at least 128 bits) over everything that decides
  the content. For a guard, pass `identity="<hash inputs or source revision>"`:
  it is recorded in `COMPLETE`, and a later `ensure` with a different identity
  (or against a key published without one) raises `key-collision` instead of
  silently reusing another tree. A consumer that starts passing `identity=`
  on a host whose keys were published without one should expect a one-time
  `key-collision` per existing key (they carry no identity to compare):
  clear those keys once (`cowtree cache gc --idle 0`, or wait for aging) and
  they are republished with an identity.
- **Leased keys are never replaced.** `gc` and `ensure` probe `<key>/.lease`
  (`LOCK_EX|LOCK_NB`) before removing anything, whatever state `COMPLETE` is
  in: a key with a live lessee and an invalid or missing `COMPLETE` is kept
  by `gc` (`leased`) and makes `ensure` raise `busy` rather than rebuild.
- **On-disk layout.** The layout is exactly that of
  `HOST-CONTRACT.md` item 3 (beside this file), so a host GC written to the
  contract (for example mdt-cockpit's `mdt_tree_cache.py`) honours cowtree's
  leases: `<key>/{COMPLETE,tree/,.lease,.last-used}`, builds in
  `<key>.tmp-<token>`, cowtree-internal locks in `.locks/`. Only `tree/` is
  sealed; the key directory stays group-writable. Every directory and file
  cowtree creates gets an explicit mode (`0770`/`0660`, the root's setgid bit
  kept) regardless of the umask, so a group-shared root works at umask 022.
  Files your `build` writes into `tree/` keep the modes you give them: make them
  group-readable for the other lessees.
- **Lease and release.** `lease(key, owner)` touches `.last-used`, then holds
  `LOCK_SH` (flock) on `<key>/.lease` until `release()` or the holder's death:
  the kernel drops the flock with the holder, so there is no stale lease, no
  pid-reuse hazard and no heartbeat (`heartbeat()` only refreshes the idle
  clock). After the lock it re-validates (the lease inode is still the one at
  `<key>/.lease` and `COMPLETE` still resolves), because a host GC may remove
  the key between `open` and `flock`; a removed key raises `key-missing`.
  `owner` is an in-memory label. The flock is shared by `fork()` without
  exec; an external process that cannot hold the descriptor has no lease
  mode (pid leases were dropped because the host contract defines only
  flock): keep the lessee alive in the process that needs the key.
- **GC policy** (`cowtree cache gc --root D --idle SECONDS`, one global GC lock,
  each key under its own exclusive lock taken without waiting): the root must
  still be a tmpfs (below); scratch and incomplete keys are removed; a key
  whose `<key>/.lease` cannot be locked `LOCK_EX|LOCK_NB` has a live lessee and
  is kept (a lease that is not a regular file keeps it too, and the lock is
  held until the key is gone); a key idle below the threshold is kept; a key
  named by **any process's** `/proc/<pid>/mountinfo` row (a bind of it or
  below, or an overlay `lowerdir=`/`upperdir=`/`workdir=`) is kept, and so is
  every key when some process's mountinfo cannot be read; anything else is
  renamed to scratch and removed by exact path. Foreign entries are never
  touched. The lease is the primary guard (a mount taken after the mountinfo
  scan is protected only by a lease taken **before** it), the mountinfo scan
  the second line. A GC run by a user who cannot read every
  `/proc/<pid>/mountinfo` (other users' processes under `hidepid`, or no
  `CAP_SYS_PTRACE`/root) keeps **all** keys: safe, but it frees nothing. Run
  the GC as root or with that capability, or use the host's timer.
- **tmpfs rule.** The shared cache is a tmpfs area. `Cache(root, require_tmpfs=...)`
  has no default: `True` refuses a root that is not on `tmpfs`/`ramfs` (at
  construction and again at every `gc`), so GC can never delete from a real
  disk; `False` is the explicit opt-out for a private cache on a normal
  filesystem (CLI: `--allow-non-tmpfs`; `Settings.allow_non_tmpfs_shared_root`
  for level A; levels C and D are private disk directories and opt out
  internally, level B requires its tmpfs).
- **tmpfs charging.** A tmpfs page is charged to the cgroup of the process that
  first writes it, not to the one that later reads it (reads never charge,
  mounting never charges). When the writing cgroup is removed (a finished lane
  scope) the charge moves to its parent. Populate the cache from a **persistent**
  slice (for example `systemd-run --slice=tree-cache.slice -- python build.py`,
  or `docker run --cgroup-parent tree-cache.slice`), never from inside a lane, or
  the first lane is charged for the shared pages and can be OOM-killed with a
  half-built key (GC then removes the scratch). Without a persistent slice the
  charge lands on whichever parent is left; cowtree cannot place a process in
  a cgroup for you.
- **chmod and copy-up.** In an overlay, any chmod issued through the merged
  view on a lower file, even to the mode it already has, copies the whole
  file up into the upper (overlayfs `metacopy` is off here), and a tree-wide
  chmod therefore turns every lower file into a private copy. `seal=True`
  strips the write bits of every entry, so a consumer that has to write or
  normalise modes through an overlay ends up chmod-ing (`chmod u+w`, `cp -p`,
  `tar` extraction, package installers) and loses the sharing it came for.
  Seal only trees that are consumed by plain copies (levels B-D) or by
  read-only mounts; never rely on `seal` to protect an overlay lower, the
  mount's read-only flag does that.
- **Overlay recipe (level A).** `worker_volume` builds the measured
  `docker volume create --driver local --opt type=overlay --opt device=overlay
  --opt o=lowerdir=...,upperdir=...,workdir=...,redirect_dir=on,xino=auto`
  and the CSV-quoted `--mount ... volume-opt=o=...` argument (and the plain
  `mount -t overlay` form). Paths and options are validated against injection;
  the lower must be a published key's tree. `redirect_dir=on` requires a root
  mount (never `userxattr`); the upper is created with the lower root's mode and
  owner (they become the merged root's); a marker pins each upper to one key and
  `reuse_worker` refuses another; removal falls back to a helper container when
  the root-owned work dir blocks `rmtree`.
- **Repair.** `repair_entry(template, merged, rel)` restores one changed file
  or symlink (bytes, mode, mtime) or removes an added one, and raises
  `unrepairable` for directory-level changes; `reset_worker` rebuilds a bad
  upper; `upper_full` implements the 80 % tmpfs degrade check.

### Adapters (host policy lives outside the library)

`cowtree.adapters.mdt_settings(env)` is the one place that names the
mdt-cockpit host contract: `MDT_TREE_CACHE_ROOT` becomes `shared_root`,
`MDT_TREE_UPPER_ROOT` becomes `upper_root`, `MDT_TREE_DOCKER_SOCKET` the socket
(default `/var/run/docker.sock`). Placing a populate helper in a cgroup slice
(`CGROUP_PARENT_DEV_CACHE`) is the caller's job. The host timer runs
`cowtree cache gc --root "$MDT_TREE_CACHE_ROOT"`. (The older `Run` API still
reads `MDT_COW_ROOT`/`COWTREE_MODE`; that is the legacy adapter surface, see
COW-BL-09.)

See the [consumer guide](CONSUMERS.md) for copyable examples and the
[specification](SPEC.md) for operation semantics. The package is embedded in
consumer wheels; there is no index-bound `cowtree` dependency.

Qualify the source with its registered tester-unified lane:

```console
run-gate cowtree
```
