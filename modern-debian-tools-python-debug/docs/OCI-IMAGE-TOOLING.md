# OCI image manifests, registry tools and repack design

This document separates four concepts that are easy to conflate: MDT's release
inventory, the OCI image manifest, registry clients, and filesystem repacking.
It also records which parts belong in MDT and which should become reusable CMRU
capabilities.

## Human inventory versus OCI manifest and digest

MDT's `manifest.md` is documentation for humans and agents. It records resolved
tool/runtime versions, version-selection policy, release notes and useful paths.
It is copied into the filesystem of the image and published in the repository.
Docker does not use this Markdown file to pull or run the image.

An OCI image manifest is protocol data. It contains content descriptors for one
image configuration and its ordered filesystem layers. Each descriptor records
a media type, byte size and content digest. An OCI image index (also called a
manifest list) points to one or more manifests, commonly one per platform. A
registry tag is a mutable name that resolves to an index or manifest; a digest
is the immutable content-addressed identity of those exact bytes.

```bash
crane manifest docker.io/library/nginx:1.27 | jq .
crane digest docker.io/library/nginx:1.27
docker buildx imagetools inspect --raw docker.io/library/nginx:1.27 | jq .
```

The raw response can be an index rather than the platform-specific image
manifest, so scripts must inspect `mediaType`/`manifests` before assuming a
top-level `layers` array. A platform selection produces a different manifest
digest from the parent index digest.

### Adoption policy

Every MDT and dstdns image stored in a conforming registry already has an OCI or
Docker-distribution manifest; there is nothing extra to opt into. What should be
adopted is explicit digest evidence:

- record the published index digest and per-platform manifest digest after the
  final mutation/repack step;
- verify the tag resolves to the expected digest before reporting a release;
- attach SBOM, provenance and signatures to the final digest, never to a
  transient pre-repack image;
- retain the Markdown inventory because OCI descriptors are not a useful
  package/tool inventory for humans;
- use standard OCI labels for source, revision, version, documentation and
  licenses in MDT and dstdns images.

dstdns app images do not need copies of MDT's large Markdown inventory unless a
consumer actually needs an in-image bill of materials. Their minimum useful
contract is OCI labels, immutable-digest release evidence, an SBOM/referrer and
application version/health metadata. The base image's inventory remains
available inside descendants unless an app intentionally removes it.

## Registry and artifact tool choices

The tools overlap, but they are not interchangeable:

| Tool | Primary MDT use | Keep/add policy |
| --- | --- | --- |
| Docker Buildx/BuildKit | Build, cache, OCI-layout export/import and canonical registry publication | Required release engine |
| Skopeo | Daemonless copy/sync across registry, daemon and OCI transports; useful for mirroring and manual diagnostics | Keep as an operator tool; not a canonical MDT release dependency |
| crane | Small, script-friendly manifest, digest, tag and copy operations | Included as the everyday registry inspection/scripting client |
| regctl | Deep manifest/blob/referrer inspection and controlled image mutation | Included as the advanced OCI debugging client |
| ORAS | Push/pull/attach/discover non-image OCI artifacts and referrers | Defer until CMRU publishes SBOMs, attestations or bundles as first-class OCI artifacts |
| cosign | Sign and verify final image/artifact digests | Supply-chain capability, complementary to the clients above |

Crane and regctl enter through MDT's existing staged-artifact mechanism, not
ad-hoc `curl` commands in a Dockerfile. For a GitHub-release binary,
"latest" means: resolve the latest allowed upstream release at staging time,
select the architecture-specific asset, verify its upstream digest, and record
the concrete version, source and digest. Crane publishes `checksums.txt` as a
release asset. Regctl currently does not publish a checksum-file asset, so the
stager validates the `sha256:` digest GitHub computes for the immutable release
asset and writes that value to a local sidecar for the offline Dockerfile gate.
The Dockerfile does not fetch a floating URL during an image layer.

Skopeo remains useful for sync and transport bridging even though the current
OCI-layout release path does not need it. ORAS should arrive with a maintained
artifact/referrer workflow, rather than as an unused overlapping binary.

## Layers, overlay storage and flattening

A directory count under `/var/lib/docker/overlay2` is not an image-layer count.
It includes layer snapshots and writable container layers for many images, plus
runtime bookkeeping; BuildKit also has a separate content/cache store. Shared
layers may serve many images. On newer Docker installations the containerd
image store may replace the legacy directory layout entirely.

More lower layers can add mount/startup and pathname-lookup metadata work, and
copy-on-write writes can pay copy-up latency. Those effects are workload and
kernel/storage dependent. Normal steady-state reads also benefit from OverlayFS
page-cache sharing. The number `2800` alone does not establish a performance
problem. The local audit found 98 unique image IDs, 2,481 layer references,
1,305 unique diff IDs and a maximum chain of 57—not a 2,800-layer mount.

Measure the relevant stores instead:

```bash
docker system df -v
docker buildx du --builder mdt-governed-v1
docker history --no-trunc IMAGE
docker image inspect IMAGE --format '{{len .RootFS.Layers}}'
```

At the time of this review, Docker reported about 276.8 GB of reclaimable image
data and the named MDT builder reported about 32.2 GB reclaimable cache.
Flattening a newly published image does not garbage-collect either store. An
explicit reviewed retention/prune policy addresses accumulation; repacking
addresses distribution size and topology.

Flattening also has costs:

- it removes cross-release and cross-image layer reuse;
- small changes can require pulling a large replacement layer;
- layer-level build history and cache granularity become coarser;
- it changes image/config digests and invalidates existing signatures;
- a one-layer image can have worse parallel download behavior.

Therefore MDT uses repack only as a measured optional release lane. The `2GB`
target's three layers are a result of the current image-size benchmark, not a
universal runtime optimum. Benchmark cold pull/unpack, warm startup,
representative read/write I/O, network delta after a small rebuild, and
registry/cache storage before changing it.

## Parallelism and cgroup controls

Three independent controls are involved:

- `REPACK_JOBS` is the number of image targets repacked concurrently.
- `REPACK_CONCURRENCY` is passed to `docker-repack` as its Rayon worker-pool
  size. Each zstd encoder in the current implementation is single-threaded, so
  the configured value `2` explains roughly two cores of observed repack CPU.
- the BuildKit container's CPU quota is a hard aggregate ceiling. With quota
  `400000` and period `100000`, it can consume at most four logical CPUs.

CPU shares/weight are relative priority only when workloads contend; they do
not choose a thread count or cap otherwise-idle execution. `nice` and idle-class
`ionice` have the same best-effort character. More compression workers also
increase memory and I/O pressure.

If measurements show that repack is CPU-bound and the host has six genuinely
idle cores, `REPACK_CONCURRENCY=6` can let the local repack process use them
because it runs in the caller's cgroup. Increasing the BuildKit worker above
four cores separately requires raising/removing its CPU quota. Do not increase
`REPACK_JOBS` casually: parallel full-image working sets multiply memory and
disk pressure. Compare elapsed time, peak memory, CPU PSI and I/O PSI at 2, 4
and 6 workers before changing the default.

## What belongs in CMRU

The reusable mechanism should move toward CMRU, while image-specific policy
stays with the project.

CMRU should own:

- governed named-builder creation and verified resource limits;
- direct Buildx `push` and OCI-layout output modes;
- unique per-project/per-target scratch paths and cleanup;
- generic repack invocation with target size, compression concurrency and job
  controls;
- OCI descriptor/digest validation and final-tag verification;
- optional SBOM/provenance/sign/referrer publication against the final digest;
- structured progress so `tee` receives timely line-buffered output.

The project should own its Bake target graph/tags/Dockerfile, human inventory
generation and extraction, project-specific smoke/import tests, and the
benchmark that justifies its layer target and resource defaults.

CMRU already has an `oci-image` profile, but its built-in repack handler is not
production-equivalent to MDT's guarded release implementation. The audit found
a wrong `--compression` option (upstream uses `--compression-level`), global
`/tmp/oci-{src,dst}` paths, no concurrency/validation/governed-builder support,
and a non-repack path that builds twice across build/push phases. Its tests
currently prove command assembly, not an end-to-end OCI publication. MDT's
custom steps bypass that handler; new projects must not copy it or enable it as
if it were equivalent.

Before other projects adopt the generic handler, reconcile OCI tar versus
directory-layout handling, allocate unique scratch, expose all resource knobs,
validate unpack semantics, and publish the final layout through a known-good
registry path. MDT's scripts are the current reference behavior.

## Reimplementing docker-repack

The upstream Rust project is modest in code size, but a correct repacker is not
merely a tar concatenator. It must preserve OCI descriptors/config history and
diff IDs, apply whiteouts and opaque directories correctly, retain modes,
ownership, xattrs, hardlinks and symlinks, handle file/directory type changes,
support indexes/platforms, produce deterministic compressed blobs, and define
what happens to referrers and signatures.

The existing validation gate has already found a file/descendant collision.
The current upstream implementation also contains nondeterministic shuffling
and nested-index behavior that need a deliberate contract. This is evidence for
stronger conformance tests, not evidence that a quick rewrite is safer.

Recommended sequence:

1. keep the canonical non-repacked lane independent;
2. turn every found defect into a fixture and upstream report/fix;
3. extract wrapper, validation and publication lifecycle into CMRU;
4. if upstream maintenance is insufficient, fork/patch the small MIT-licensed
   Rust implementation before considering a language rewrite;
5. require golden tests for whiteouts, opaque directories, type changes,
   links/xattrs, multi-platform indexes and deterministic repeat output;
6. compare the candidate with upstream on size, pull/unpack correctness and
   performance before changing the release default.

Owning the orchestration and validation now yields most of the integration
benefit. Owning the filesystem transformation should wait until its conformance
corpus makes that ownership cheaper and safer than maintaining the dependency.

## References

- [OCI image manifest specification](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
- [OCI image layout specification](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
- [Docker OverlayFS storage driver](https://docs.docker.com/engine/storage/drivers/overlayfs-driver/)
- [Docker container CPU constraints](https://docs.docker.com/engine/containers/resource_constraints/)
- [crane documentation](https://github.com/google/go-containerregistry/blob/main/cmd/crane/README.md)
- [regctl documentation](https://regclient.org/cli/regctl/)
- [ORAS documentation](https://oras.land/docs/)
- [docker-repack](https://github.com/orf/docker-repack)
- [Skopeo, crane, regctl and ORAS comparison](https://alexandre-vazquez.com/skopeo-crane-regctl-container-image-tools/) (useful secondary overview; verify behavior in upstream docs)
- [Awesome Docker](https://github.com/veggiemonk/awesome-docker/blob/master/README.md) (discovery list, not a normative source)
