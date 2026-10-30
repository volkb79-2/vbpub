# Wings patch stack design guide

## Forward-porting and image builds

The active target moved from official Wings `v1.13.3` to `v1.13.4` on
2026-10-30. The release tag is pinned to full commit
`747bc4393daf5d95fdf61d76382f5e23749cffe9`; the `.3` series stays available as
the source record for the earlier image. Separate series directories keep each
release's patch bytes and digest tied to the exact upstream tree.

All 11 original Change-Ids remain stable across the port. Patch content changes
are expected because v1.13.4 changed the configuration loader, startup probe,
SFTP handshake flow, and dependencies. Three-way conflicts were resolved while
preserving those upstream changes. Two small v1.13.4-only guard patches were
then needed: one keeps the upstream boot liveness check and attach timeout, and
one handles the nil process configuration constructed by an upstream console
test. Validation first caught the boot merge's compile errors, then the server
test caught the nil configuration read. The patch-by-patch result is recorded
in the [forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md).

The `.4` Go test container uses `golang:1.27.2-alpine`, matching the upstream
Dockerfile's toolchain tag. The image build resolved it to
`sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d`; the
runtime base resolved to
`sha256:15f8637509792e05b97d83451b0b6ccfa4abd548f997e406ac1e72c87fe1fa78`.
The `.3` rebuild used Go 1.24.11 Alpine at
`sha256:ef75fa8822a4c0fb53a390548b3dc1c39639339ec3373c58f5441117e1ff46ae`.
Those resolved base digests are build evidence, while `stack.conf` pins the
versioned Go tags used by the scripts.

`build-image.sh` adds `--load` because the configured remote BuildKit builder
otherwise leaves a successful image only in its cache; loading imports the
tag into the local Docker daemon without pushing it to a registry. In this
environment the managed builder is resource-capped in `dev-buildkitd.slice`;
operators run builds under `nice` and `ionice`. The registered Wings test lane
runs in tester-unified under `$CGROUP_PARENT_DEV_GATES`. It checks the Go build,
vet, unit tests, workflow contracts, and integration-tag compilation without
starting a nested Docker container. The separate registered coverage lane
runs Assay R1 under the same gate boundary.

The running `1.13.3-cgroup.6` image has no OCI labels, so its original series
digest cannot be recovered from the image. Its binary SHA-256 was nevertheless
reproduced exactly from the current `.3` patch series with Go 1.24.11, the
Dockerfile build flags, and `VERSION=1.13.3-cgroup.6`. The `.7` binary differs
because the version linker value changed to `1.13.3-cgroup.7`; its Go build
settings otherwise match. Full IDs and hashes are in the
[README image table](README.md#images-observed-and-built-on-2026-10-30).

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
configured ref and fail unless its resolved commit equals the pinned SHA. Build
metadata names that base alongside the patchseries version. The equivalence
proof remains tied to the historical `.3` base and reports that series digest;
it does not compare trees across the `.3` and `.4` releases.

## Series digest and image suffix

The series digest hashes the full pinned upstream commit followed by each
patch's exact bytes in filename order. Each field is prefixed by its length as
an unsigned eight-byte big-endian integer before the bytes are added to
SHA-256. Filenames, `patchseries` version, and build time are excluded because
they do not change the source series identity. The tool version and UTC build
date remain separate image labels.

This definition replaced an earlier digest that also included filenames and
tool-version metadata. The existing `.3.7` and `.4.1` image labels retain the
earlier digests `358e0116e1ceca1296daa156ea2b4e82ebf0719197d9731c6ee950523d028d66`
and `923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7`.
Their source digests recomputed under the current definition are `3f9df8045b72c0346a0a2bc9aeac7fa1af1fb033e3e395d7c568ab22fae2558b` and
`fbe1fcddd19c59127f45d3afb923c532a15d054dfdd23f7d3f6c398c48838dec`. The
ledger keeps the source digest in `series_digest` and the value present in the
already-built image label in `image_label_series_digest`; the images were not
rebuilt to rewrite labels. The active v1.13.4 digest is `fbe1fcddd19c59127f45d3afb923c532a15d054dfdd23f7d3f6c398c48838dec`.

The image suffix is derived from committed ledger history while holding an
exclusive `flock` on the stable `image-ledger.json.lock` sidecar. The script
persists a reservation before starting Docker, so another process sees that
suffix as occupied. A known digest reuses its suffix for the same upstream
version; a new digest advances that upstream version's `cgroup.N`. A
successful build atomically records its image ID and date and consumes the
reservation. A Docker build failure releases the reservation. If Docker has
already produced the tag but the record step fails, the reservation remains
active to prevent another digest from overwriting an unrecorded image; an
operator can release it with the helper's `release` command after resolving
that state. Atomic JSON replacement happens inside the same lock, so two
record operations cannot overwrite each other's ledger snapshot.

The v1.13.3 ledger history includes the pre-ledger `.4` and `.6` suffixes; the
`.6` record notes the observed image ID and binary hash while leaving its
label-less original digest unknown. The `.7` build record preserves both the
recomputed source digest and its earlier image-label digest. v1.13.4 has its
own suffix sequence, with `.1` recorded for its first image. The
`current_series` entry tracks the active v1.13.4 source identity separately
from image history.

Patch export follows a fail-preserving staging process. `export-patches.sh`
writes format-patch output into a sibling staging directory, validates all
trailers with `patchseries check`, and only then replaces the canonical
series directory. A failed generation or validation leaves the previous patch
set in place.

## Test gate

`scripts/test.sh` invokes the registered `wings` Run-Gate lane. Run-Gate
resolves `$CGROUP_PARENT_DEV_GATES`, verifies the loaded host slice, and runs
the Go toolchain and workflow tests inside tester-unified. The lane does not
launch nested Docker. Its wrapper refuses Run-Gate extra mounts and
`DOCKER_HOST`, and its worker fails if the standard host Docker socket is
present. `INTEGRATION=1` therefore fails with an explicit message. The
separate registered `coverage` lane runs Assay R1 under the same placement
boundary.

## Separate responsibilities

`patchseries` owns patch parsing, metadata validation, requirement closure,
diffstat rendering, identity-based delta, and digest construction. Git owns
patch application and review (`git am`, `git format-patch`, `git range-diff`).
Wings scripts own upstream pins and image provenance. The workflow does not
introduce a second patch manager or infer semantic risk from diff text.
