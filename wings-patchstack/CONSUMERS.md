# Wings patch series consumer guide

## Apply the pinned series

Run these commands from the repository root when preparing a review checkout:

```bash
wings-patchstack/scripts/clone.sh pterodactyl
wings-patchstack/scripts/apply.sh pterodactyl
wings-patchstack/scripts/test.sh pterodactyl
```

`clone.sh` and `apply.sh` refuse if `v1.13.3` no longer resolves to the SHA in
`wings-patchstack/stack.conf`. For an upstream update, record the new full
commit SHA and review the patch application before changing the pin.

`test.sh` delegates to the registered `wings` Run-Gate lane in
`wings-patchstack/run-gate.toml`. The lane uses tester-unified, where Run-Gate
resolves and verifies `$CGROUP_PARENT_DEV_GATES` before starting the test
container. The tester image must be built with the Go toolchain selected by
`PTERODACTYL_GO_IMAGE` in `stack.conf`; `tester-unified/build-image.sh` reads
that value and records it in the image label. Build it under a unique tag and
select its immutable image ID for the lane:

```bash
tester-unified/build-image.sh --tag tester-unified:wings-<date>
RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:wings-<date>)"
export RUN_GATE_TESTER_UNIFIED_IMAGE
wings-patchstack/scripts/test.sh pterodactyl
```

The lane runs Go build, vet, unit tests, workflow contract tests, and
integration compile checks without starting a nested container.
`INTEGRATION=1` is refused because the gate does not receive the host Docker
socket. Use `coverage.sh` or `COVERAGE=1 test.sh` for the separately registered
Assay R1 coverage lane. Both wrappers refuse `RUN_GATE_EXTRA_MOUNTS` and
`DOCKER_HOST`, and both workers reject the standard Docker socket.

## Inspect and select changes

Validate the trailers against the consumer vocabulary, then ask for a
dependency closure or a cross-version inventory:

```bash
PYTHONPATH=libraries/patchseries/src python3 -m patchseries check \
  wings-patchstack/patches/pterodactyl-v1.13.3 \
  --vocabulary wings-patchstack/vocabulary.toml
PYTHONPATH=libraries/patchseries/src python3 -m patchseries closure \
  wings-patchstack/patches/pterodactyl-v1.13.3 wings-scope-resource-control
PYTHONPATH=libraries/patchseries/src python3 -m patchseries render \
  --column pterodactyl-v1.13.3=wings-patchstack/patches/pterodactyl-v1.13.3 \
  --column filesystem-pterodactyl-v1.13.1=wings-patchstack/patches/filesystem/pterodactyl-v1.13.1
```

To compare two versions, pass their series directories to `patchseries delta`.
Use `git range-diff` to review the code changes paired by those stable IDs.
Each `Requires` target must sort before its dependent; `check` rejects an
out-of-order series and `closure` emits changes in application order. Rename-
only and mode-only format-patch changes are supported; binary patches are
refused with a precise error.

When refreshing patch files, export to the canonical series directory through
the staging script:

```bash
wings-patchstack/scripts/export-patches.sh pterodactyl
```

It validates the generated series before replacing the committed directory,
so an export failure leaves the previous patch set intact.

## Build an image

After the applicable source and integration gates are green, build without
choosing a suffix by hand:

```bash
wings-patchstack/scripts/build-image.sh pterodactyl
```

The script reserves its suffix under a ledger lock before building, then labels
the image with its series digest, pinned upstream commit, patchseries version,
and UTC build date. On success it records the image ID and removes the
reservation. A failed Docker build releases the reservation. If an image tag
exists but the ledger record failed, the reservation stays active to prevent
another digest from reusing that tag; resolve that state before using the
helper's `release` command. The current ledger has no recorded build for this
series. Its `current_series.series_digest` is
`3f9df8045b72c0346a0a2bc9aeac7fa1af1fb033e3e395d7c568ab22fae2558b`, while
`builds` and `reservations` are empty; this workflow change does not build an
image.
