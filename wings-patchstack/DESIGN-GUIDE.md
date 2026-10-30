# Wings patch stack design guide

## Forward-porting and image builds

The active target moved from official Wings `v1.13.3` to `v1.13.4` on
2026-10-30. The release tag is pinned to full commit
`747bc4393daf5d95fdf61d76382f5e23749cffe9`; the `.3` series stays archived as
the source record for the running image. Separate series directories keep each
release's patch bytes and digest tied to the exact upstream tree.

All 11 original Change-Ids remain stable across the port. Patch content changes
are expected because v1.13.4 changed the configuration loader, startup probe,
SFTP handshake flow, and dependencies. Three-way conflicts were resolved while
preserving those upstream changes. Two small v1.13.4-only guard patches were
then needed: one keeps the upstream boot liveness check and attach timeout, and
one handles the nil process configuration constructed by an upstream console
test. Validation first caught the boot merge's compile errors, then the server
test caught the nil configuration read. The unpatched v1.13.4 server tests
passed, and the corrected patched suite passed. The patch-by-patch result is
recorded in the [forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md).

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
otherwise left the successful image only in its cache; loading imports the
tag into the local Docker daemon, without pushing it to a registry. In this
environment the managed builder is resource-capped in `dev-buildkitd.slice`;
operators run builds under `nice` and `ionice`. Go test containers require the
host-supplied `CGROUP_PARENT_DEV_GATES` and have a three-CPU limit. The script
fails if the variable is absent and checks `LoadState=loaded` when host systemd
is visible; inside this devcontainer it trusts the value injected by
`devcontainer.json`. These limits keep Wings verification from competing
freely with production workloads.

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
or image ID. The ledger now also records the observed pre-ledger `.6` image as
the `.3` suffix floor, even though its original digest remains unknown; the next
distinct `.3` series therefore built as `.7`. Each upstream version has its own
suffix sequence, so the first `.4` build is `.1`.

## Separate responsibilities

`patchseries` owns patch parsing, metadata validation, requirement closure,
diffstat rendering, identity-based delta, and digest construction. Git owns
patch application and review (`git am`, `git format-patch`, `git range-diff`).
Wings scripts own upstream pins and image provenance. The workflow does not
introduce a second patch manager or infer semantic risk from diff text.
