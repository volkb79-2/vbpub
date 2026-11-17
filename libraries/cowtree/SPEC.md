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
allocation directory; btrfs also reserves bytes from btrfs runs deleted by this
process on the same filesystem in the preceding 60 seconds. The pending-delete
ledger uses the Btrfs filesystem ID from `BTRFS_IOC_FS_INFO`; ordinary
filesystems use the allocation directory's `st_dev`. Therefore roots in
different Btrfs subvolumes with distinct device IDs still share the allowance.
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

## Repo paths

`cowtree.repo_paths` (stdlib, Python >= 3.11) reads `vm-lab.toml` from a repository root. Its schema is
`schema = 1`, a required top-level `repo` (the repository's folder name below the CoW area, written `<repo-name>`
below: one directory name, no `/`, no leading dot) and `[paths.<name>]` tables with `repo` (required, plain location
relative to the root), `cow` (optional, location below `<MDT_COW_ROOT>/<repo-name>/`) and `compression` (`none` or
`zstd`; btrfs sets only an algorithm through the inode property, never a level). `{project}` is the only placeholder; the caller passes the project directory (relative
to the root). Nothing is derived from the directory name of the root, so the main checkout and every worktree of the
repository resolve to the same CoW folders, while the plain fallback stays inside the checkout or worktree that holds
the file. This table is separate from the COW-BL-01 declaration of gitignored paths for worktree
snapshots, which stays open.

| Id | Contract |
| --- | --- |
| RP1 | Validation refuses with `RepoPathsInvalid` (code `path`, status 5, message names the file): invalid TOML or a name defined twice, an unknown top-level or entry key, `schema` other than the integer 1, a top-level `repo` that is missing, not a string, empty, contains `/` or `\`, starts with a dot (so also `..`) or is `shared` (the directory cowtree keeps in every area root; two repositories of one user must also never share a `repo` key, they would share their CoW folders), a missing `paths` table, an empty name, a `repo` or `cow` that is empty, not a string, absolute, or has an empty, `.` or `..` segment, a placeholder other than `{project}`, `{project}` in only one of `repo` and `cow`, and a bad `compression`. |
| RP2 | `find_repo_root(start)` returns the nearest directory at or above `start` that holds `vm-lab.toml`; it never consults the current directory by itself; none found is `UsageError` (code `usage`, status 2). `load_repo_paths(root)` takes the root explicitly. |
| RP3 | `resolve(name, project=, environ=, ops=)` returns mode `cow` with `<area>/<repo-name>/<cow>` only when the entry has `cow`, `COWTREE_MODE` is not `off`, `MDT_COW_ROOT` is set (non-empty), absolute and an existing directory, mountinfo says its filesystem is btrfs, and that path is an existing directory. Mode `plain` with `<root>/<repo>` is the answer only when the entry has no `cow`, `COWTREE_MODE=off`, `MDT_COW_ROOT` is unset or empty, the `<repo>/<cow>` folder does not exist, or a `PermissionError` while `stat`-ing the root hides it (a permission error is the only failure that falls back). Any other set `MDT_COW_ROOT` raises `RootInvalid` (status 3): relative, not an existing directory, or on a filesystem other than btrfs (the message names the type found; a mis-mount must not be hidden by a plain answer); an invalid `COWTREE_MODE` raises too; an entry without `cow` never consults the area. `resolve` performs no write at all and never calls `detect()` (no `.cowtree`, no probe); it reads the environment, `stat` and mountinfo only. |
| RP4 | `project` must be a relative directory without `..`, `.` or empty segments, and is validated whenever given; an entry that uses `{project}` without one, and an unknown name, are `UsageError`. |
| RP5 | `provision(...)` acts only with a btrfs area and an entry with `cow`: parents as plain directories, `btrfs subvolume create`, then `btrfs property set <path> compression <value>` when the entry declares one (skipped when an existing subvolume already reports that value; unset equals `none`, see the DESIGN-GUIDE caveat). It uses `detect()`, which may create `.cowtree/` in the area and runs a capability probe, creates the parent directories and the subvolume and sets the property. An existing plain directory at the CoW location is refused (`PathRefused`) and nothing is run; a failed command or a failing parent-directory creation is `BackendFailed` (status 6). With `COWTREE_MODE=off`, an unset or empty `MDT_COW_ROOT`, or an entry without `cow` it returns the plain result and creates nothing; a set `MDT_COW_ROOT` on a filesystem other than btrfs raises `RootInvalid` (status 3) as in RP3. The plain repo location is never touched. |
| RP6 | `cowtree path NAME [--project DIR] [--repo-root DIR] [--provision] [--json]` prints the path (JSON: `name`, `project`, `path`, `mode`, `repo`); without `--repo-root` the root is searched upward from the current directory; errors use the typed status table. `libraries/cowtree/repo-path` runs it from the source tree next to the script. |

## Persistence declarations (COW-BL-04)

Relaxed fsync is the default for disposable CoW state. Page cache survives a
process or container kill, so every operation stays correct across those; only
a host crash or power loss loses unsynced data.

| Id | Write | Durability declaration |
| --- | --- | --- |
| P1 | Clone creation (btrfs snapshot, reflink, copy) | No fsync. After a host crash the clone is discarded and recreated. |
| P2 | Sealed trees | No fsync of content; the seal marker is written last by atomic rename, so a reader sees a complete seal or none. Unsealed or missing marker means rebuild. |
| P3 | `owner.json` and `owner.lock` | Atomic replace stays; the file and directory fsync are not relied on for correctness. A manifest that is empty, truncated or unparsable is read as "no manifest": `gc` reports `manifestless-or-invalid`, `dead_flock_runs` skips it, `survey` reports `unknown`, and nothing trusts it. Test: `test_truncated_owner_manifest_is_untrusted_and_never_collected`. |
| P4 | Explicit copies to a durable destination | The one durable write: fsync of the destination. |

P3 retains such a run rather than deleting it: removal of a manifestless
directory is an operator decision, never automatic, because the same state is
visible to a run that is mid-creation.

## Live qualification

`tests/live/test_live.py` is stdlib `unittest` and is gated by
`COWTREE_LIVE_ROOT`. Without a root it skips cleanly. With a suitable btrfs or
reflink area it checks snapshot/reflink isolation, allocated-space sharing and
reclamation, and cross-process flock behavior. A live run needs the test root
visible inside `tester-unified`; this package does not add the run-gate bind
translation for that host path.
