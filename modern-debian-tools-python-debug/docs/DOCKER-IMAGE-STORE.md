# Docker Engine image-store decision

This guide records the difference between this host's legacy Docker `overlay2`
graphdriver and Docker Engine's containerd image store, and gives a reversible
maintenance-window migration procedure. It is a plan only: writing this file
does not change Docker configuration, stop a container, migrate data, or mutate
the host.

## Executive recommendation

Adopt the containerd image store eventually, but do not switch this busy host
in place during normal operation. First prove a cold MDT/devcontainer pull and
rebuild on a disposable Docker Engine 29 instance using the default containerd
store. If that passes, migrate this host in a scheduled maintenance window with
all important images reproducible or exported, all persistent data backed up,
and a tested configuration rollback.

The migration is worthwhile primarily for a more complete OCI image model, not
as a cure for `overlay2` directory count or as a guaranteed performance
improvement. It enables local image indexes/multi-platform images, attached
provenance and SBOM attestations, and pluggable snapshotters. It also changes
the storage layout and may use materially more disk.

## Two uses of OverlayFS, not “OverlayFS versus containerd”

The names describe different architectural layers:

- The legacy Docker image store uses Docker's `overlay2` *graphdriver* for
  image layers and container writable layers. Docker now calls `overlay2` a
  legacy storage driver superseded by the containerd `overlayfs` snapshotter in
  its [OverlayFS driver documentation](https://docs.docker.com/engine/storage/drivers/overlayfs-driver/).
- The containerd image store uses containerd's content store and metadata, and
  delegates mounted/unpacked filesystem snapshots to a *snapshotter*. Docker
  Engine uses containerd's `overlayfs` snapshotter by default, as documented in
  [containerd image store with Docker Engine](https://docs.docker.com/engine/storage/containerd/).

Consequently, switching does **not** normally remove OverlayFS from the I/O
path. Both defaults use the Linux OverlayFS union filesystem for runnable
snapshots. The change is from Docker's graphdriver/image metadata model to
containerd's content-store, image-index and snapshotter model.

Docker Engine 29 uses the containerd image store by default only for fresh
installations. An installation upgraded from an earlier Engine retains its
classic graphdriver until explicitly migrated. This compatibility behavior is
documented both in the [Engine 29 release notes](https://docs.docker.com/engine/release-notes/29/)
and the [storage-driver selection guide](https://docs.docker.com/engine/storage/drivers/select-storage-driver/).

## Capability differences

| Capability | Legacy `overlay2` graphdriver store | containerd image store |
| --- | --- | --- |
| Single-platform images and normal containers | Yes | Yes |
| Store a complete multi-platform image/index locally | No; an external `docker-container` Buildx builder can build and push it without loading it | Yes |
| Store BuildKit provenance and SBOM attestations with the local image | No complete image-index model | Yes |
| Default filesystem implementation | Linux OverlayFS through `overlay2` | Linux OverlayFS through the `overlayfs` snapshotter |
| Alternative snapshotters | No | Architecture supports snapshotters such as stargz for lazy pulling and nydus/dragonfly for other distribution models |
| Wasm image/workload support | No native store support | Supported by the image-store architecture |

Docker lists multi-platform images, attestations, Wasm and advanced/lazy
snapshotters as the main reasons for the containerd store in its
[Engine image-store guide](https://docs.docker.com/engine/storage/containerd/).
The [multi-platform build guide](https://docs.docker.com/build/building/multi-platform/)
also explains why the classic store cannot load a complete manifest list even
though a separate Buildx builder can publish one directly to a registry.

For MDT this means the current governed `docker-container` Buildx builder is
not made obsolete. It remains useful for isolated cache and resource control.
The containerd store would make the daemon itself a better OCI consumer and
local inspection target; it does not automatically flatten images, improve the
Dockerfile, or make a repacker correct.

“Supports lazy snapshotters” is also not the same as “all pulls become lazy.”
The default remains the `overlayfs` snapshotter. A remote/lazy snapshotter has
additional installation, daemon configuration, image-format and operational
requirements and should be evaluated as a separate project.

## Current host evidence and disk implications

The point-in-time inventory captured for this decision is:

- Docker Engine 29 on an installation upgraded from an older Engine;
- active storage driver: legacy `overlay2`;
- 41 Docker containers;
- 188 Docker images;
- `docker system df` image usage: approximately 347.9 GB;
- BuildKit/build-cache usage: approximately 273.1 GB.

These are operational evidence, not immutable constants. Refresh them before a
migration with at least:

```bash
docker version
docker info
docker info --format '{{json .DriverStatus}}'
docker system df -v
docker ps -a --size
docker buildx ls
docker buildx du --builder mdt-governed-v1
findmnt -T /var/lib/docker
df -hT /var/lib/docker
```

Do not blindly add the two reported byte totals: image, BuildKit cache and
shared-layer accounting can describe different stores or shared/reclaimable
content. Use `docker system df -v`, each builder's `docker buildx du`, and
filesystem-level measurements together to establish actual occupied space and
the safe reclaimable subset.

The containerd image store normally retains each pulled layer in compressed
form in its content store **and** in unpacked form for snapshots. Docker warns
that this uses more disk than the classic store, which retained the unpacked
representation. Docker also warns that containerd storage can have a separate
data path from a customized Docker data root; verify and configure the actual
paths and backing filesystem before pulling hundreds of gigabytes. See
[Docker's disk-space guidance](https://docs.docker.com/engine/storage/containerd/#disk-space-usage).

During a reversible migration the old `overlay2` image/container store remains
on disk while the new store downloads and unpacks its own images. On this host,
that overlap is the central constraint. Plan free space for the working set in
both stores, temporary downloads/unpacks, logs, and BuildKit cache. Do not prune
the legacy store merely to make the first migration fit; doing so destroys the
fast rollback path. Review old images and BuildKit cache independently before
the window, and prune only explicitly approved/reproducible content.

## Why this is not a live toggle

Enabling `features.containerd-snapshotter` requires restarting `dockerd`.
Docker does not convert the active legacy store in place as part of the normal
toggle. Images and containers belonging to the inactive backend stay on disk
but become hidden; switching back makes the legacy objects visible again. The
official [enablement instructions](https://docs.docker.com/engine/storage/containerd/#enable-containerd-image-store-on-docker-engine)
state this explicitly.

With 41 container records and 188 images, toggling while stacks are expected to
remain available could produce both downtime and an apparently empty daemon.
Recreating workloads in the new store and then switching back can also leave
two hidden generations of container metadata. Never run both generations by
alternating the setting: stop the currently visible workloads before each
switch and verify ports, names, networks and persistent-data ownership before
starting the other generation.

Docker has an *experimental* `containerd-migration` feature, but Docker
recommends starting fresh and cautions that automatic migration may not work in
all cases. Its threshold mechanism requires no containers and an image count at
or below the configured threshold. This host is intentionally not a candidate;
do not raise the threshold to 188 to force an experimental conversion. See
[experimental automatic migration](https://docs.docker.com/engine/storage/containerd/#experimental-automatic-migration).

Also check `userns-remap` before planning: Docker documents that the containerd
image store is not available with user-namespace remapping enabled.

## Maintenance-window migration checklist

### 1. Prove the target architecture elsewhere

1. Create a disposable host/VM with the same Engine 29 minor release, kernel,
   filesystem class and architecture, installed fresh so the containerd image
   store is the default.
2. Verify `docker info -f '{{ .DriverStatus }}'` contains
   `driver-type io.containerd.snapshotter.v1` and confirm that the active
   snapshotter is `overlayfs`.
3. Pull the immutable MDT/VSC image digest, create a representative
   devcontainer, run lifecycle hooks, and measure cold pull through first
   successful `docker exec`/VS Code connection.
4. Exercise Buildx push, local multi-platform/index inspection, SBOM/provenance
   inspection, container restart, host reboot and prune behavior.
5. Record disk growth as compressed content and unpacked snapshots are created.

### 2. Inventory and establish recovery artifacts

1. Freeze deployment changes for the window and record the exact Engine,
   containerd, Buildx, kernel and configuration versions.
2. Save `/etc/docker/daemon.json`, Docker systemd drop-ins, registry mirrors,
   proxy settings, insecure-registry settings, data-root settings and builder
   definitions.
3. Export `docker inspect` evidence for every container, image, network and
   volume, while treating Compose files/CIU configuration as the authoritative
   deployment source.
4. Map every container to its Compose/CIU project and verify that it can be
   recreated. Resolve all one-off/manual containers before proceeding.
5. Back up databases with application-aware dumps and back up named-volume or
   bind-mount data with its owning service stopped or quiesced. `docker save`
   does not back up volumes.
6. Push all locally unique images to an approved registry by immutable digest,
   or `docker save` them to verified storage with checksums. Confirm credentials
   and registry availability from the host.
7. Record current container/image lists, mounts, networks, volume names, health,
   port bindings and stack smoke-test results for comparison.
8. Verify enough free space for the new compressed content plus unpacked
   snapshots while retaining the old store and recovery artifacts.

### 3. Stop cleanly and enable the new store

1. Announce downtime and stop application traffic/jobs.
2. Stop stacks through their owning Compose/CIU workflow in dependency-safe
   order. Do not use `down -v`; persistent volumes are recovery assets.
3. Confirm no containers are running and take the final consistent data backup.
4. Stop Docker and verify the daemon and container processes have exited.
5. Back up the Docker configuration again and validate the proposed JSON
   offline.
6. Add the feature without removing unrelated configuration:

   ```json
   {
     "features": {
       "containerd-snapshotter": true
     }
   }
   ```

7. Start Docker and immediately inspect the journal for migration, snapshotter,
   mount, permission or data-path errors.
8. Verify:

   ```bash
   docker info -f '{{ .DriverStatus }}'
   docker info
   docker system df -v
   ```

   The driver status must contain `driver-type io.containerd.snapshotter.v1`.
   Seeing few/no legacy containers and images is expected because the old store
   is hidden, not deleted.

### 4. Restore, deploy and accept

1. Re-authenticate to registries and pull critical images by immutable digest.
2. Recreate networks and deploy stacks from Compose/CIU definitions; do not
   manually recreate containers from memory.
3. Restore persistent data only when required. Named volumes/bind mounts may
   still exist independently of the image-store metadata, but verify each path,
   owner and service contract rather than assuming it.
4. Recreate or reselect governed Buildx builders and verify their resource
   limits and cache ownership. Do not assume the daemon image-store switch
   migrated a `docker-container` builder's separate cache.
5. Run stack health, application smoke, restart and reboot tests. Include an MDT
   devcontainer cold pull/rebuild and first-connect timing.
6. Verify multi-platform/index and attestation behavior with a known image. The
   structure of Docker BuildKit attestations is documented in
   [image attestation storage](https://docs.docker.com/build/metadata/attestations/attestation-storage/).
7. Record new disk usage and compare image pull, unpack, create, start and
   steady-state I/O separately.
8. Keep the legacy store untouched through an agreed rollback period. Only
   after formal acceptance should its retirement and disk reclamation be
   planned as a separate, backed-up operation.

## Rollback checklist

Rollback is a controlled backend switch, not an attempt to merge both stores:

1. Stop all workloads created under the containerd image store and quiesce
   persistent data.
2. Capture logs, inspection output and any new data/backups needed from the new
   deployment.
3. Stop Docker.
4. Restore the saved `daemon.json`, or set/remove
   `features.containerd-snapshotter` so the legacy backend is selected, without
   disturbing unrelated daemon settings.
5. Start Docker and verify `Storage Driver: overlay2` and the expected legacy
   container/image inventory.
6. Before starting anything, check for duplicate service ownership, ports,
   networks, volume paths and database state. Restore application data to the
   chosen point if writes occurred after migration.
7. Start legacy stacks through their normal Compose/CIU owners and run the
   recorded health/smoke tests.
8. Preserve the failed containerd store and logs until the incident is
   understood; do not prune it during rollback.

## Decision gate

Proceed on this host only when all of the following are true:

- disposable-host testing proves MDT/devcontainer and dstdns workflows;
- every active container has a declarative owner and recovery path;
- stateful data restore has been tested;
- immutable images are available from a registry or verified export;
- storage headroom covers both stores during the rollback window;
- Docker/containerd data roots and their backing filesystem are explicitly
  known;
- the maintenance window permits full stack stop, daemon restart, pull/unpack
  and application verification;
- named builders and resource-governance checks are included in acceptance;
- rollback has an owner, threshold and time budget.

Until those gates pass, retain the current legacy `overlay2` store. Continue to
publish proper OCI indexes and attestations through the governed external
Buildx builder and registry; local daemon storage is not required to gain those
registry-side benefits.
