# Changelog

All notable changes to cowtree are recorded here. Cowtree is an embedded
internal library, not a separately published release target.

## [Unreleased]

### Added
- Add the stdlib-only run API, btrfs/reflink/copy backends, input-derived
  admission, owner manifests, flock leases, conservative garbage collection,
  CLI, fake ioctl tests, and opt-in live suite.
- Add tester-unified R0/R1 qualification with 100% line and branch coverage.
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

<!-- cmru: release history -->
