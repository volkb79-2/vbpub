# Changelog

All notable changes to cowtree are recorded here. Cowtree is an embedded
internal library, not a separately published release target.

## [Unreleased]

### Fixed
- COW-BL-04: declare per-write persistence expectations under relaxed fsync
  (SPEC P1-P4) and test that a truncated `owner.json` is untrusted and retained.

### Added
- Add the shared keyed-tree mechanism (AD2-3): `cowtree.cache` (`Cache.ensure`
  atomic publish with a verification manifest and an optional `identity=` guard
  (`key-collision`, exit 13), flock leases on `<key>/.lease` in the layout of
  `HOST-CONTRACT.md` item 3 (a host GC honours them; interop tested against the
  shipped `mdt_tree_cache.py`), `release`, holder-checked `gc` that honours
  leases and every process's mountinfo (binds and overlay `lowerdir=`),
  mandatory `require_tmpfs=` (explicit opt-out; CLI `--allow-non-tmpfs`),
  explicit umask-independent `0770`/`0660` modes, read-only `survey`/`verify`,
  plain-path configuration), `cowtree.provision` (standalone `Settings`,
  capability `Probes`, `decide` picking and reporting degradation levels A
  overlay, B tmpfs copy, C reflink, D copy, and a `Provisioner` giving every
  level the same publish/lease/worker/release/cleanup interface), the
  `cowtree.adapters` module as the only place that names `MDT_*` host policy, and
  `cowtree.overlay` (dockerd local overlay volume and `--mount` builders,
  per-worker upper/work creation, reuse guard, cleanup with a helper-container
  fallback, `reset_worker`, `repair_entry`, `upper_full`). CLI:
  `cowtree probe` and `cowtree cache survey|gc|verify --root`; new exit statuses 8-12.
- Add the stdlib-only run API, btrfs/reflink/copy backends, input-derived
  admission, owner manifests, flock leases, conservative garbage collection,
  CLI, fake ioctl tests, and opt-in live suite.
- Add full-suite tester-unified R0/R1 qualification with 100% line and branch
  coverage, without fail-fast truncation.
- Require explicit external owner references and verify boot/PID identity before
  GC removes a lease-free run; remove btrfs subvolume trees through the backend
  and make snapshots of sealed templates writable for candidates. Active runs
  retain path identities in memory and refuse replaced run, `trees/`, and
  run-owned tree paths. Nested `clone_file` destinations adopt their top-level
  tree for cleanup.
- Make sealed copy/reflink tree clones owner-writable, refuse recursive ingest,
  preserve colliding destinations, key delayed-delete accounting to the
  admission area, and retain cross-host runs whose boot IDs differ. Reject
  non-ASCII compression levels before creating a subvolume. Capture backend
  creation identities before `Run` adopts outputs and clean partial trees by
  recorded child identity, preserving unowned children. Isolate cleanup paths
  with non-replacing `renameat2` and verify the moved identity before deletion.
- Classify non-regular owner locks as invalid before opening them, and report
  failed or short writes while initializing the private metadata version as a
  typed invalid-root error. Expand fake-ops coverage for backend cleanup,
  identity races, shared-tree leases, and metadata initialization failures.
- Preserve typed btrfs deletion inspection failures and use the module cleanup
  helper when btrfs ingest or walk-clone population fails. Close the lock fd if
  dead-run inventory cannot inspect its file type.

<!-- cmru: release history -->
