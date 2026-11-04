# TrueNAS 28 rolling-nightly middleware adapter

**Supported build:** exact `28.0.0-MASTER+20261029-175210`, the sole rolling
v28 base. Older v28 nightlies are refused; update TrueNAS to the exact
registered build and verify its version before applying this adapter.

This adapter fingerprints 19 target files, uses six zero-fuzz series patches,
and includes the release-specific Path B pool wizard shim. Its target manifest
SHA-256 is
`8828c8dfd91617df0bb864c1c3e1687883db7176ea79ac459f6f2d39d7166acf` and its
deterministic install-bundle SHA-256 is
`4ff5f7e9ba90481e2d663588ffa2c98b121f7e937343abfd61081b2d9dff9935`.

The Oct 29 source capture verified all 19 fingerprints and all six current
series patches in
the former grouped patch set; the six current series files preserve the same
installed bytes. The generated [series outline](../PATCH-SERIES.md#change-outline)
records each change ID, dependency, file list, hunk count, and diffstat. The
former grouped files remain under `legacy/` for the equivalence proof and
frozen-build hunk bridge; the installer packages only `series/`. The full live
update, patch, API, and UI results are recorded separately in the generated
[acceptance index](../../docs/ACCEPTANCE-RESULTS.md);
read its individual columns before treating any behavior as accepted. Results
from the retired Oct 14 and Oct 27 adapters do not certify this build.

## Path and safety boundaries

Path A stores bounded data under `boot-pool/user-data`; Path B uses only the
explicitly reserved free-tail partition as `boot-disk-data`. Both paths share
the same physical disk. The adapter leaves boot-pool partition size, BE
creation, update, and rollback under TrueNAS control. It refuses unregistered
builds, changed source hashes, nonzero-fuzz anchors, unapproved partition
layouts, and operations that could erase the parent boot disk.

An update creates a new BE with stock `/usr`. Path A data remains on disk but
its boot-mount hook must be reapplied. Path B pool identity and data remain on
the reserved partition; the exact adapter still must be applied in the new BE.
The v28 install record is stored on the selected persistent Path A or Path B
root. It records the exact build, profile, bundle digest, source/installed
hashes, and backups; an install record alone does not certify `/usr`.

## Retargeting policy

The v28 support policy follows one rolling base: the newest nightly only. To
bump it, capture the exact update metadata and source, run `patch_drift.py
diagnose`, adapt and review changed hunks, require all target hashes and
zero-fuzz checks, then run source-state, API, and UI acceptance. Replace the
single v28 row in `supported-builds.json` only after the selected acceptance
passes. The former row becomes unsupported and the refusal directs operators
to update and recheck the exact build.

The updater feed and current TrueNAS metadata do not publish a complete
middleware Git commit. A moving GitHub `master` comparison is advisory. The
authoritative boundary is this build's exact target SHA-256 set and zero-fuzz
adapter verification against extracted or installed files; see the
[design guide](../docs/DESIGN-GUIDE.md#source-comparison) and
[update/drift evidence](../../docs/UPDATE-AND-DRIFT.md#drift-guards-and-unknown-builds-q2).
