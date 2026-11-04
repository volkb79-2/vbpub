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
    shared_root="/var/cache/mytool/trees", upper_root="/var/cache/mytool/uppers",
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
cowtree cache survey|gc|verify --root D
```

### Behaviour of the pieces

- **Publish.** `ensure(key, build)` takes an exclusive flock on `.locks/<key>`,
  builds into `<key>.tmp-<token>/tree`, writes the `COMPLETE` verification
  manifest (a sha256 over every entry's type, path, mode, size and content
  hash or symlink target), and renames the directory into place. Concurrent
  callers wait, then reuse the winner's tree; a failed build removes its scratch.
  `seal=True` additionally chmods the tree read-only (off by default: the mount
  is the read-only protection, and a chmod forces full-file copy-ups).
  `verify(key)` re-hashes the tree and raises `tree-invalid` on drift.
- **Lease and release.** `lease(key, owner)` holds a flock on its own lease
  file, which records pid, start time, boot id, pid namespace, host and owner.
  The kernel drops the flock with the holder, so liveness is immune to pid
  reuse and needs no heartbeat; `heartbeat()` only refreshes the idle clock.
  `lease(key, owner, pid=N)` records an external holder judged by pid plus start
  time, and a holder in another boot or pid namespace is conservatively kept
  alive. `release()` / the context manager removes the lease.
- **GC policy** (`cowtree cache gc --root D --idle SECONDS`, one global GC lock,
  each key under its own exclusive lock taken without waiting): scratch and
  incomplete keys are removed; dead leases are pruned; a key with a live
  lease, idle below the threshold, or named by any `/proc/self/mountinfo` row
  (a bind of it or below, or an overlay `lowerdir=`/`upperdir=`/`workdir=`) is
  kept; anything else is renamed to scratch and removed by exact path. Foreign
  entries are never touched.
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
