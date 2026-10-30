# cowtree specification

## Public contract

`cowtree` is a standard-library-only package (`API_VERSION = 1`) embedded in
consumer wheels. It exports `ENV_ROOT = "MDT_COW_ROOT"`,
`ENV_MODE = "COWTREE_MODE"`, `detect()`, `open_run()`, `Run`,
`Capabilities`, `SpaceEstimate`, and typed `CowError` subclasses:
`RootInvalid` (`root-invalid`), `SpaceRefused` (`space`), `PathRefused`
(`path`), `BackendFailed` (`backend`), and `LeaseHeld` (`lease`).

`detect(*, environ=None, ops=None, log=None)` returns an `Area` or `None`.
`COWTREE_MODE` accepts `auto` (the default) or `off`; any other value raises
`RootInvalid`. `off` wins over a configured root. An absent root emits one
INFO line per process and returns `None`; an explicitly empty root is invalid. A
set root is validated before a run can be allocated: it must be absolute and normalized, resolve to itself,
exist as a real directory, belong to the non-root caller, not be world-writable,
and be mounted on a persistent filesystem other than overlay, tmpfs, ramfs,
proc, or sysfs. The filesystem type is selected from the longest matching
mountinfo mountpoint after octal escape decoding. A probe selects btrfs only
when subvolume create, snapshot, and delete work; otherwise it requires a
successful 4 KiB FICLONE. Unsupported configured roots raise `RootInvalid`.

Capabilities report backend (`btrfs`, `reflink`, or `copy`), filesystem type,
cheap clone, tree snapshot, read-only seal, per-tree compression, and
subvolume-delete support. There is no native ZFS backend: ZFS can use the
reflink backend when block cloning is available.

## Space estimates

`SpaceEstimate` contains non-negative integer `ingest_bytes`,
`per_clone_bytes`, and `max_live_clones`. Booleans are rejected. For cheap
clones, `needed = ingest_bytes + max_live_clones * per_clone_bytes`. For copy,
`needed = (1 + max_live_clones) * max(ingest_bytes, per_clone_bytes)`.
Admission compares that amount with `statvfs.f_bavail * f_frsize` at the run
allocation directory; btrfs also reserves
bytes from btrfs runs deleted by this process on the same filesystem device in
the preceding 60 seconds. This pending-delete ledger is shared by all btrfs
roots on that device in the process.
`SpaceRefused` carries the byte counts and estimate. No fixed floor, percentage,
fallback root, or auto-eviction is used. `Run.reserve(extra)` checks the
current reservation plus the additional estimate before growing.

## Run operations

`open_run(tool, *, estimate, labels=None, environ=None, ops=None, log=None,
plain_dir=None, plain_prefix=None, holder="flock", external_ref=None)` always
returns a `Run` unless it raises a typed error. Tool and tree names match
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Plain runs use `mkdtemp` below
`plain_dir` or `TMPDIR`; CoW runs use
`$MDT_COW_ROOT/.cowtree/runs/<tool>-<YYYYmmddTHHMMSSZ>-<16hex>`. Both run
directories contain `owner.json`, `owner.lock`, and `trees/`; named trees and
ingested files live under `trees/`.

Before any tree is created, cowtree creates `owner.lock`, takes a nonblocking
exclusive flock, and atomically writes and fsyncs `owner.json` with format
`cowtree-run/1`, run/tool/labels/backend/API/time, holder/external reference,
the input estimate, and owner uid/pid/starttime/boot id/pid namespace/hostname.
The serialized manifest is capped at 64 KiB; `open_run()` refuses labels that
would exceed this cap, and GC retains larger manifests as malformed.
Names are unique and not reused. `holder="external"` requires an explicit
`external_ref`; external holders are never collected by `gc`.
`open_run()` logs once when it finds dead flock-owned runs; it does not collect
them automatically.

`Run` is a context manager. Its methods are `ingest_tree(src, name)`,
`ingest_file(src, dst_name)`, `make_tree(name, compression=None)`,
`clone_tree(src, name)`, `clone_file(src, dst)`, `seal(tree)`,
`delete_tree(tree)`, `reserve(extra)`, and `close()`. Ingest may source files
outside the area. Clone sources must resolve inside this run or the area's
`shared/` directory. Destinations must remain inside the run, and special files
are refused. `ingest_tree()` refuses a source directory that contains its
destination. A destination that already exists or appears before exclusive
creation raises `PathRefused` and is left intact. Partial tree rollback removes
only entries whose identities the backend recorded; if an unowned entry is
found, its containing directory is preserved and `close()` may report that the
run still has data under `trees/`. The active run also checks `(st_dev, st_ino)` pairs for its run
directory, `trees/`, and run-owned top-level trees or files before operations
and close; a path already replaced at the check is refused. These checks do not
isolate cowtree from a hostile process with the same effective UID. Clone operations never silently fall back
to a byte copy.

For `clone_file` into an existing nested directory, the top-level directory
under `trees/` becomes run-owned and is removed on close. A destination at the
top level is tracked as a run-owned file.

| Operation | btrfs | reflink | copy |
|---|---|---|---|
| `make_tree` | subvolume create; optional `btrfs.compression` xattr | directory; compression ignored with INFO | directory; compression ignored with INFO |
| `ingest_tree` | subvolume plus reflink walk; cross-filesystem sources copy bytes | reflink walk; cross-filesystem sources copy bytes | symlink-preserving byte copy |
| `ingest_file` | FICLONE on the same filesystem; copy bytes across filesystems | FICLONE on the same filesystem; copy bytes across filesystems | byte copy plus metadata |
| `clone_tree` | writable subvolume-root snapshot, otherwise reflink walk | strict FICLONE walk; trees sealed through cowtree regain owner write bits | symlink-preserving byte copy; trees sealed through cowtree regain owner write bits |
| `clone_file` | strict FICLONE | strict FICLONE | byte copy plus metadata |
| `seal` | set subvolume read-only flag | clear write bits recursively | clear write bits recursively |
| `delete_tree` | clear read-only and destroy subvolume; EPERM uses empty-out/rmdir | restore directory owner-write bits and remove | remove |

`close()` removes run-owned paths, the manifest, lock, and run directory. A
normal close raises a typed error if cleanup fails. Context-manager cleanup
with another exception in flight is best effort and preserves the original
exception.

## Garbage collection and CLI

`gc(scan_dir)` examines only direct child directories. It removes a run only
when the manifest is valid, the holder is `flock`, and a nonblocking exclusive
lock succeeds and boot, uid, hostname, PID namespace, and PID start-time
identity prove the owner process is dead. A hostname mismatch remains unknown
even when boot IDs differ. Runs with a live owner or an
unverifiable namespace are reported and retained. For btrfs runs it destroys
each top-level subvolume through the btrfs backend before removing the run
directory. External, manifestless, malformed, and uninspectable runs are also
retained. `shared/` is never scanned. `make_shared()`
serializes creation under `.cowtree/shared.lock`.

The CLI is `python -m cowtree {detect,survey,gc,make-shared}`. `detect --json`
reports `off`, `btrfs`, or `reflink`; a broken configured root exits 3.
`gc --scan DIR` supports plain-mode run directories; default `gc` only scans
the configured CoW area and logs that plain runs need an explicit scan.
`owner.json` is read with a 64 KiB cap; larger manifests are retained as
malformed. `make-shared NAME --compression VALUE` accepts `zstd`, `zstd:N`
(1..15), `lzo`, and `none`.

Argparse usage errors exit `2`. Typed `CowError` statuses are `cow` → `1`,
`root-invalid` → `3`, `space` → `4`, `path` → `5`, `backend` → `6`, and
`lease` → `7`; an unknown subclass code uses `1`.

## Live qualification

`tests/live/test_live.py` is stdlib `unittest` and is gated by
`COWTREE_LIVE_ROOT`. Without a root it skips cleanly. With a suitable btrfs or
reflink area it checks snapshot/reflink isolation, allocated-space sharing and
reclamation, and cross-process flock behavior. A live run needs the test root
visible inside `tester-unified`; this package does not add the run-gate bind
translation for that host path.
