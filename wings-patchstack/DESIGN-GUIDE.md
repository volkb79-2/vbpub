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
Build metadata then names that base alongside the patchseries version.

## Series digest and image suffix

The digest includes the ordered patch filenames and exact bytes, the upstream
commit SHA, and the `patchseries` version. It excludes build time. The Docker
image still records its UTC build date, so an operator can identify a specific
artifact without making identical source inputs look like different series.

The image suffix is derived from committed ledger history. A known digest
reuses its suffix for the same upstream version; a new digest advances that
upstream version's `cgroup.N`. Successful builds append the returned image ID
and date to the ledger. The previous `cgroup.4` artifact has no recorded digest
or image ID; the ledger marks that legacy boundary explicitly, and the next
new digest advances to `.5`.

## Separate responsibilities

`patchseries` owns patch parsing, metadata validation, requirement closure,
diffstat rendering, identity-based delta, and digest construction. Git owns
patch application and review (`git am`, `git format-patch`, `git range-diff`).
Wings scripts own upstream pins and image provenance. The workflow does not
introduce a second patch manager or infer semantic risk from diff text.
