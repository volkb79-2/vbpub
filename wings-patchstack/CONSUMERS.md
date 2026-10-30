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
older `v1.13.3` series is retained for the running `.6` image's source record.

## Validate and inspect the changes

The test workflow sends source over a tar stream to the configured Go
container. It builds, vets, runs the affected package tests, and compiles the
Docker and systemd integration-tag code. The host must supply
`CGROUP_PARENT_DEV_GATES`; the script refuses to launch without it, verifies
`LoadState=loaded` where host systemd is visible, and limits the Go container to
three CPUs. Inside this devcontainer the value is trusted from
`devcontainer.json`, because host systemd is not mounted there.

```bash
: "${CGROUP_PARENT_DEV_GATES:?Use the host-supplied dev-gates cgroup parent}"
nice -n 19 ionice -c 3 wings-patchstack/scripts/test.sh pterodactyl
```

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

The v1.13.4 report explains which patches applied cleanly and which required
adaptation: [forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md).
The historical `scripts/prove-v1-equivalence.sh` compares the archived and
canonical v1.13.3 trees only; it is not a cross-release equivalence claim.

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

For this ledger state, the resulting tag is
`wings-local:1.13.4-cgroup.1`. Confirm the tag, provenance labels, and binary
version before any later deployment:

```bash
docker image inspect wings-local:1.13.4-cgroup.1 \
  --format '{{.Id}} {{json .Config.Labels}}'
docker run --rm --pull never --cpus 1 \
  --cgroup-parent="$CGROUP_PARENT_DEV_GATES" \
  --entrypoint /usr/bin/wings wings-local:1.13.4-cgroup.1 version
```

Deployment is intentionally separate. During a planned maintenance window,
edit `/root/ptero-wings-patched-13.3/docker-compose.yml` so the `wings` service
uses `wings-local:1.13.4-cgroup.1`, inspect the resolved Compose configuration,
then recreate that service from the local image:

```bash
cd /root/ptero-wings-patched-13.3
docker compose config
docker compose up -d --no-deps --pull never wings
```

[`--pull never`](https://docs.docker.com/reference/cli/docker/compose/up/) makes
Compose require the already-loaded local image rather than contacting a
registry. Do not run `docker compose pull` for this registry-less tag. This
guide records the later procedure; the 2026-10-30 work built and verified the
image but did not deploy or restart the production Wings service.
