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

## Shared keyed trees and overlay slots

Beside disposable runs, cowtree owns the generic mechanism for **publishing,
leasing and cleaning up keyed read-only trees** in a host-configured shared
root (AD2-3; ownership split in the parallelisation addendum 2 section 2). It
still knows nothing about git, assay, lanes or Docker; it never mounts. The
roots come only from `MDT_TREE_CACHE_ROOT` and `MDT_TREE_UPPER_ROOT` set by
host policy. `shared_roots()` returns `None` when the cache root is unset (the
caller decides whether to fall back to a copy and logs its own WARNING) and
raises `RootInvalid` when it is set but unusable; an unset upper root means no
overlay slots (`overlay_ready` is false).

```python
from cowtree import Cache, shared_roots, create_worker, worker_volume, remove_token

roots = shared_roots()                       # None -> caller falls back
cache = Cache(roots.cache_root)
pub = cache.ensure(key, build, owner="lane-7")   # key = caller's content hash; lease taken atomically
dirs = create_worker(roots.upper_root, token, 0, cache, key)
vol = worker_volume(dirs, cache, key, f"rg-tslot-{token}-w0")
argv = vol.mount_argv("/work/w0")            # --mount type=volume,volume-driver=local,"volume-opt=o=lowerdir=..."
...
remove_token(roots.upper_root, token)        # after the container and volume are removed
pub.lease.release()
```

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
- **GC policy** (`cowtree cache gc --idle SECONDS`, one global GC lock, each
  key under its own exclusive lock taken without waiting): scratch and
  incomplete keys are removed; dead leases are pruned; a key with a live
  lease, idle below the threshold, or named by any `/proc/self/mountinfo` row
  (a bind of it or below, or an overlay `lowerdir=`/`upperdir=`/`workdir=`) is
  kept; anything else is renamed to scratch and removed by exact path. Foreign
  entries are never touched. `cowtree cache survey` and `cowtree cache verify`
  are read-only.
- **Overlay recipe.** `overlay_volume` / `worker_volume` build the measured M-2
  `docker volume create --driver local --opt type=overlay --opt device=overlay
  --opt o=lowerdir=...,upperdir=...,workdir=...,redirect_dir=on,xino=auto`
  and the equivalent CSV-quoted `--mount ... volume-opt=o=...` argument. Every
  path and option is validated against injection; the lower must be a published
  key's tree. `redirect_dir=on` requires a root mount (never `userxattr`);
  the upper is created with the lower root's mode and owner (they become the
  merged root's); a marker pins each upper to one key and `reuse_worker`
  refuses another; removal falls back to a helper container when the root-owned
  work dir blocks `rmtree`.
- **Repair.** `repair_entry(template, merged, rel)` restores one changed file
  or symlink (bytes, mode, mtime) or removes an added one, and raises
  `unrepairable` for directory-level changes; `reset_worker` rebuilds a bad
  upper from nothing; `upper_full` implements the 80 % tmpfs degrade check.

See the [consumer guide](CONSUMERS.md) for copyable examples and the
[specification](SPEC.md) for operation semantics. The package is embedded in
consumer wheels; there is no index-bound `cowtree` dependency.

Qualify the source with its registered tester-unified lane:

```console
run-gate cowtree
```
