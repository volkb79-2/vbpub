# Wings patch stack design guide

## Canonical layout and provenance

`wings-patchstack/` is the one active patch-series layout. It was chosen
because it already held the v2 `series.yaml` and F1 export, and makes shared
`patchseries` metadata available to both series. The former deployed v1
directory was consolidated into this layout. Its original patch bytes and
configuration remain under `archive/deployed-v1/` as evidence of what the
deployed workflow consumed. The old Pelican branch is archived and cannot be
selected by the active scripts.

The upstream ref and full commit SHA live together in `stack.conf`. A ref is a
human-readable name; it does not prove the commit. Clone and apply fetch the
configured ref and fail unless its resolved commit equals the pinned SHA.
Build metadata then names that base alongside the patchseries version. The
equivalence proof also prints the active series digest so the tree proof and
the identity used by image provenance are visible in one report.

## Series digest and image suffix

The digest includes the exact ordered patch bytes and upstream commit SHA.
Filenames and `patchseries` version are excluded because neither changes the
requested series identity; the package version remains a separate image label.
Build time is also excluded, while the image records its UTC build date as a
separate label.

The image suffix is derived from committed ledger history while holding an
exclusive `flock` on the stable `image-ledger.json.lock` sidecar. The script
persists a reservation before starting Docker, so the next process sees that
suffix as occupied. A known digest reuses its suffix for the same upstream
version; a new digest advances that upstream version's `cgroup.N`. A
successful build atomically records its image ID and date and consumes the
reservation. A Docker build failure releases the reservation. If Docker has
already produced the tag but the record step fails, the reservation remains
active to prevent a later digest from overwriting an unrecorded image; an
operator can release it with the helper's `release` command after resolving
that state. Atomic JSON replacement happens inside the same lock, so two
record operations cannot overwrite each other's ledger snapshot. The previous
`cgroup.4` artifact has no recorded digest or image ID; the ledger marks that
legacy boundary explicitly, and the next new digest advances to `.5`.
The ledger's `current_series` entry stores the active digest separately from
build history. The current value is
`3f9df8045b72c0346a0a2bc9aeac7fa1af1fb033e3e395d7c568ab22fae2558b`; the
build list is empty because no image exists for this series yet. The
equivalence proof recomputes that value and refuses a mismatch.

Patch export follows the same fail-preserving rule. `export-patches.sh` writes
format-patch output into a sibling staging directory, validates all trailers
with `patchseries check`, and only then renames the old series aside and swaps
the complete staged directory into place. A failed generation or validation
leaves the committed series in place.

## Test gate

`scripts/test.sh` invokes the registered `wings` Run-Gate lane. Run-Gate
resolves `$CGROUP_PARENT_DEV_GATES`, verifies the loaded host slice, and runs
the Go toolchain and workflow tests inside tester-unified. The lane has no
nested Docker launch. Its wrapper refuses Run-Gate extra mounts and `DOCKER_HOST`,
and its worker fails if the standard host Docker socket is present. `INTEGRATION=1` therefore
fails with an explicit message. Changed-line coverage has its own registered
`coverage` lane and the same container and placement boundary.

## Separate responsibilities

`patchseries` owns patch parsing, metadata validation, requirement closure,
diffstat rendering, identity-based delta, and digest construction. Git owns
patch application and review (`git am`, `git format-patch`, `git range-diff`).
Wings scripts own upstream pins and image provenance. The workflow does not
introduce a second patch manager or infer semantic risk from diff text.
