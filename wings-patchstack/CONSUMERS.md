# Wings patch series consumer guide

## Apply the pinned series

Run these commands from the repository root in a fresh review checkout:

```bash
wings-patchstack/scripts/clone.sh pterodactyl
wings-patchstack/scripts/apply.sh pterodactyl
```

The active series is official Wings `v1.13.4`, pinned to full commit
`747bc4393daf5d95fdf61d76382f5e23749cffe9` in `wings-patchstack/stack.conf`.
Clone and apply fetch the tag and refuse if it resolves to another commit. The
older `v1.13.3` series is retained as the source record for its `.6` and `.7`
images.

## Validate and inspect the changes

`wings-patchstack/scripts/test.sh` delegates to the registered `wings` Run-Gate
lane in `wings-patchstack/run-gate.toml`. The lane uses tester-unified, where
Run-Gate resolves and verifies `$CGROUP_PARENT_DEV_GATES` before starting the
test container. The tester image must be built with the Go toolchain selected
by `PTERODACTYL_GO_IMAGE` in `stack.conf`; `tester-unified/build-image.sh`
reads that value and records it in the image label. Build it under a unique
tag and select its immutable image ID for the lane:

```bash
tester-unified/build-image.sh --tag tester-unified:wings-<date>
RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:wings-<date>)"
export RUN_GATE_TESTER_UNIFIED_IMAGE
wings-patchstack/scripts/test.sh pterodactyl
```

The lane runs Go build, vet, unit tests, workflow contract tests, and
integration compile checks without starting a nested container. `INTEGRATION=1`
is refused because the gate does not receive the host Docker socket. Use
`wings-patchstack/scripts/coverage.sh` or `COVERAGE=1` with `test.sh` for the
separately registered Assay R1 coverage lane. Both wrappers refuse
`RUN_GATE_EXTRA_MOUNTS` and `DOCKER_HOST`, and both workers reject the standard
Docker socket.

Check patch metadata, request a capability dependency closure, or compare the
two release series by stable Change-Id:

```bash
PYTHONPATH=libraries/patchseries/src python3 -m patchseries check \
  wings-patchstack/patches/pterodactyl-v1.13.4 \
  --vocabulary wings-patchstack/vocabulary.toml
PYTHONPATH=libraries/patchseries/src python3 -m patchseries closure \
  wings-patchstack/patches/pterodactyl-v1.13.4 wings-scope-resource-control
PYTHONPATH=libraries/patchseries/src python3 -m patchseries delta \
  wings-patchstack/patches/pterodactyl-v1.13.3 \
  wings-patchstack/patches/pterodactyl-v1.13.4
```

The forward-port report explains which patches applied cleanly and which
required adaptation:
[forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md). The
historical `scripts/prove-v1-equivalence.sh` compares the archived and
canonical v1.13.3 trees only; it does not claim equivalence across releases.

Each `Requires` target must sort before its dependent; `check` rejects an
out-of-order series and `closure` emits changes in application order. Rename-
only and mode-only format-patch changes are supported; binary patches are
refused with a precise error. When refreshing patch files, export to the
canonical series directory through the staging script:

```bash
wings-patchstack/scripts/export-patches.sh pterodactyl
```

It validates the generated series before replacing the committed directory,
so an export failure leaves the previous patch set intact.

## Rebuild and deploy later

After the tests pass, build from the pinned series. Use the managed, resource
capped BuildKit builder; the command runs at reduced host scheduling priority.
`build-image.sh` applies the patches again, requires the resulting tree to match
the reviewed checkout, derives the suffix from the ledger, stamps provenance,
and loads the image into the local daemon. It does not push an image.

```bash
BUILDX_BUILDER=mdt-managed nice -n 19 ionice -c 3 \
  wings-patchstack/scripts/build-image.sh pterodactyl
```

Use the `built:` tag printed by the script after a new build. Every successful
build gets a new suffix, including a rebuild of unchanged source, so do not
derive the tag from the source digest.

For the ledger state recorded on 2026-10-30, the active image tag is
`wings-local:1.13.4-cgroup.1`. The ledger's `series_digest` is the recomputed
ordered-patch-bytes-plus-upstream-commit identity. The already-built image
retains its earlier `org.vbpub.wings.series-digest` label, recorded separately
as `image_label_series_digest`; the two values are listed in the
[README image table](README.md#images-observed-and-built-on-2026-10-30). No
rebuild was needed to keep that history explicit. Confirm the tag, provenance
labels, and binary version before any later deployment:

```bash
docker image inspect wings-local:1.13.4-cgroup.1 \
  --format '{{.Id}} {{json .Config.Labels}}'
docker run --rm --pull never --cpus 1 \
  --cgroup-parent="$CGROUP_PARENT_DEV_GATES" \
  --entrypoint /usr/bin/wings wings-local:1.13.4-cgroup.1 version
```

Deployment is intentionally separate. During a planned maintenance window,
set `WINGS_COMPOSE_DIR` to the directory containing your Compose file, then edit
`$WINGS_COMPOSE_DIR/docker-compose.yml` so the `wings` service uses
`wings-local:1.13.4-cgroup.1`. Inspect the resolved Compose configuration, then
recreate that service from the local image:

```bash
WINGS_COMPOSE_DIR="/path/to/your/compose/project"
cd "$WINGS_COMPOSE_DIR"
docker compose config
docker compose up -d --no-deps --pull never wings
```

[`--pull never`](https://docs.docker.com/reference/cli/docker/compose/up/) makes
Compose require the already-loaded local image rather than contacting a
registry. Do not run `docker compose pull` for this registry-less tag. The
2026-10-30 work recorded both digest definitions and verified the existing
images; it did not deploy or restart the production Wings service.
