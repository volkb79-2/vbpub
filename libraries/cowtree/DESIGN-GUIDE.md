# cowtree design guide

## One adapter for three storage paths

Consumers use `open_run()` and inspect `Run.caps.cheap_clone`. The default
`COWTREE_MODE=auto` selects a configured CoW root; an unset `MDT_COW_ROOT`
selects the existing plain-copy behavior. `COWTREE_MODE=off` explicitly
selects the plain path, and other mode values raise `RootInvalid`. A set but
unusable root raises `RootInvalid` before cowtree allocates a run. The library owns
backend detection, run directories, manifests, leases, safe tree operations,
space admission, and cleanup. Consumers keep their domain-specific rules.

The backend vocabulary is `btrfs`, `reflink`, and `copy`. Btrfs uses raw Linux
ioctls for subvolume create, snapshot, read-only seal, and delete. Snapshots
from sealed templates are made writable for their candidate. The reflink
backend uses `FICLONE` for regular files and does not provide tree snapshots.
ZFS uses the reflink backend when OpenZFS block cloning is available; there is
no native ZFS dataset backend. On a plain run the `copy` backend preserves
files, symlinks, modes, and timestamps.

Sealing on copy and reflink backends clears write bits, so a later byte or
FICLONE walk would otherwise produce an unusable candidate. `clone_tree()`
restores owner write permission throughout a source tree explicitly sealed
through cowtree; an unsealed tree with read-only modes remains unchanged. A btrfs subvolume snapshot
instead clears the snapshot's read-only flag and retains the original file
modes. This keeps the clone-and-discard workflow writable across backends.

Clone operations inside a CoW run fail loudly when the required clone cannot
be made. `cp --reflink=auto` was rejected for those operations: its silent byte
copy fallback can turn a cheap-clone estimate into a full-copy workload after
admission. An input ingested from another filesystem is different: it is the
single initial write, so ingest may copy bytes when reflink returns `EXDEV`.
On ext4 without FICLONE support, leaving `MDT_COW_ROOT` unset selects the plain
copy backend; configuring that ext4 path as the CoW area is an error.

## Input-derived space admission

The consumer derives `SpaceEstimate(ingest_bytes, per_clone_bytes,
max_live_clones)` from its actual input. For btrfs and reflink, cowtree checks
`ingest_bytes + max_live_clones * per_clone_bytes`. For the copy backend it
checks `(1 + max_live_clones) * max(ingest_bytes, per_clone_bytes)`. Btrfs
admission also includes this process's bytes deleted in the preceding 60
seconds because block reclamation can lag. The pending-delete ledger is
process-local and keyed by the Btrfs filesystem ID returned by
`BTRFS_IOC_FS_INFO`. `statfs.f_fsid` is not sufficient for Btrfs because the
kernel mixes the subvolume root ID into that value. Ordinary filesystems use
the directory's `st_dev`. This groups Btrfs subvolumes that share a filesystem
and free-space pool, even though their `st_dev` values differ. Deletions
recorded by any run or GC scan on that filesystem contribute to every Btrfs
admission there. This bound can conservatively reserve bytes across distinct
roots on the same filesystem.
`Run.reserve(extra)` checks the current reservation plus an additional estimate
before the consumer grows concurrency. `SpaceRefused` reports the needed and
available bytes and the estimate values. Cowtree does not invent a fixed
capacity floor or move a run to another filesystem. Admission checks available
bytes at the actual run-allocation directory, `.cowtree/runs`.

## Ownership and identity

The validated area is owned by the non-root caller and is never created by
cowtree. Cowtree reserves `.cowtree/` for `VERSION`, run manifests, leases, and
the shared-tree lock; shared data lives under `shared/`. Each run has an
exclusive unique directory containing `owner.json`, `owner.lock`, and `trees/`.
The atomic owner manifest is written before `trees/` is created. `owner.lock` is
held with `flock(LOCK_EX | LOCK_NB)` for the run's lifetime. `gc` removes only a
valid `cowtree-run/1` manifest with a `flock` holder after obtaining that lock
and proving its owner process is dead. It compares boot ID, uid, hostname, PID
namespace inode, and PID start time; when the current namespace cannot compare
the owner, GC retains the run. A hostname mismatch is always unknown, even
when boot IDs differ, because a shared scan directory may contain a live run
from another host. On btrfs, GC removes each top-level tree through
the btrfs backend so subvolumes are destroyed with the required ioctl before
removing the run directory. GC reads no more than 64 KiB from `owner.json`;
larger manifests are retained as malformed without reading the full file.
Cowtree rejects labels that would make its own owner manifest exceed
64 KiB, so it cannot create a run that its GC reader refuses to parse.
External holders and malformed or missing manifests are reported and retained.
`open_run()` reports the presence of dead
flock-owned runs once per process; it never reaps them automatically.

An active `Run` keeps `(st_dev, st_ino)` pairs for its run directory,
`trees/`, and every run-owned top-level tree or file in memory. Tree operations
and close recheck those pairs and refuse when a path is already replaced at the
check. The pairs are not written to the manifest, so no inode identity survives
the run object. Cowtree assumes processes sharing the caller's effective UID
cooperate with the private `0700` run directories and use the `Run` API; it is
not a security boundary against a same-UID process that deliberately races
path mutation. Linux path-based unlink and recursive-delete operations cannot
atomically condition deletion on an inode identity. Cleanup moves a checked
entry with `renameat2(RENAME_NOREPLACE)` and rechecks the moved identity, which
protects against ordinary path replacement and prevents restoration from
overwriting a recreated name, but does not revoke access from another process
with the same UID. Deployments that need that isolation need a separate UID or
worker boundary. A nested `clone_file` destination adopts its top-level tree
into this cleanup set; a top-level file is tracked directly.
An exclusive-create collision is a separate refusal from a partial operation;
cleanup leaves the path that won the race intact. Backends capture each
created path's `(st_dev, st_ino)` before returning it; `Run` compares that
receipt before adopting the path. Partial tree cleanup removes only entries
whose identities the backend recorded. If an unowned entry appears inside a
partial tree, cowtree preserves that entry and its containing directory; a
later `close()` can report that it could not remove the run's `trees/`
directory. This favors preserving the competing data over silently deleting
it.

Tree sources for cloning must resolve inside the current run or the area's
`shared/` directory. Ingest is the explicit boundary for a source outside the
area, and refuses a source directory that contains its destination. Run-local
names use `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Tree metadata
preservation does not imply inode identity: snapshots can keep inode numbers
with a different device, while reflink clones use new inodes. Compare
`(st_dev, st_ino)` pairs only. Sealed templates must never be executed; fixed
mtimes can otherwise expose stale bytecode. Consumers must unlink and rewrite
Git replacement files so the index sees fresh stat data, plant absolute links
inside each clone after verification, persist no cowtree inode identities,
and stage rename-sensitive files inside the destination tree.

## API clarification from the plan

The plan's `open_run(..., holder="external")` signature had no input for the
manifest's `external_ref`. Cowtree adds `external_ref=` and requires it for an
external holder instead of inventing an identifier. External runs still hold
their flock while open; `gc` never removes them, even after that flock is
released. The external owner remains responsible for their lifecycle.

## CLI exit statuses

Argparse reserves status `2` for syntax and usage errors. Cowtree maps each
`CowError.code` to a separate nonzero status so a configured but invalid root
cannot be mistaken for malformed command syntax: `cow` → `1`, `root-invalid`
→ `3`, `space` → `4`, `path` → `5`, `backend` → `6`, and `lease` → `7`.
The generic `cow` status `1` is also the fallback for an unknown subclass code.

Default `gc` scans only the configured area's run directory. Plain-mode runs
are created under the selected temporary directory and require an explicit
`gc --scan DIR`; the CLI logs that instruction instead of implying it scanned
those runs.
