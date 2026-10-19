# MDT image delivery and time-to-connect benchmarks

This is the canonical performance record for MDT image compression, layer
topology, cold delivery and first container access. Dated release summaries
were consolidated here so policy is based on time-to-connect, not compressed
bytes alone.

The normalized machine-readable record is in
[`benchmarks/results/`](../benchmarks/results/). The exploratory run's raw
per-cell JSON/TSV files were not retained, so sampled peaks are not independently
recomputable from the repository. Future runs use the checked-in harness and
must retain its direct per-run JSON and TSV beside the normalized comparison.
Re-run one image/store pair
with:

```bash
scripts/benchmark-time-to-connectable.sh \
  current-zstd3 IMAGE_REFERENCE containerd result.json
```

The harness starts a disposable Docker 29 DinD daemon with an empty data
volume. It therefore makes the pull and unpack cold without deleting the host
daemon's cache or containers. It does not require skopeo.

## 2026-07-15 controlled cold test

Host: Docker Engine 29.6.1, containerd 2.2.5, kernel 7.0.12, ext4, 8 logical
CPUs and 16 GiB RAM. Each disposable daemon received 6 CPUs, 8 GiB RAM and 12
GiB combined RAM+swap. Each cell is one cold run, so the result is directional
rather than a confidence interval.

The gzip and native-zstd images have the same config digest and 55-layer
topology. Only transport compression differs. The original 2 GB repack is a
one-week-older image and is therefore useful for topology direction, not a
strict same-content byte comparison.

| Store | Image | Compressed | Layers | Cold pull + unpack | Create/start/exec/probes |
| --- | --- | ---: | ---: | ---: | ---: |
| containerd | gzip | 2.994 GB | 55 | 77.01 s | 2.27 s |
| containerd | native zstd level 3 | 2.816 GB | 55 | **57.87 s** | 2.25 s |
| containerd | 2 GB repack, zstd 9 | 2.160 GB | 3 | 103.03 s | 2.09 s |
| overlay2 | gzip | 2.994 GB | 55 | 85.82 s | 3.05 s |
| overlay2 | native zstd level 3 | 2.816 GB | 55 | **77.66 s** | 2.13 s |
| overlay2 | 2 GB repack, zstd 9 | 2.160 GB | 3 | 123.88 s | 1.89 s |

Native zstd is the current release choice. It was 25% faster than gzip on the
containerd store and 9.5% faster on overlay2 while preserving the image's
parallel-download and cache topology. The 2 GB repack downloaded fewer bytes
but was 34–44% slower than gzip because its 1.76 GB layer serializes download,
decompression, verification and extraction. Saving roughly one second after a
warm/local pull cannot recover 26–38 seconds in cold delivery.

The native zstd benchmark image took 35.8 seconds to export and 37.6 seconds to
push from a warm, same-content BuildKit result. Its compressed size was 5.9%
below gzip. Peak sampled compression CPU was higher because useful work could
run in parallel; the configured cgroup remains the actual resource boundary.

## What “Dev Containers: Rebuild Container” does

The benchmark above stops at Docker-connectable. A full VS Code rebuild also
contains these observable phases:

1. Parse `devcontainer.json` and run the host-side `initializeCommand`.
2. Resolve the mutable image tag and pull missing blobs.
3. Fetch feature metadata and build a derived image, currently including
   `docker-outside-of-docker`.
4. Create networks, mounts and the container cgroup, then start the container.
5. Establish the first exec session.
6. Run `onCreate`, `updateContent`, `postCreate` and related lifecycle hooks;
   MDT runs `finalize_container_environment.py` here.
7. Find, install or update the matching VS Code server and extensions.
8. Start the remote extension host and complete the UI handshake.

On this host, image pull plus Docker startup is about 60–90 seconds for the
recommended artifact. A reported ten-minute rebuild is therefore not explained
by container creation. Capture **Dev Containers: Show Container Log** on the
next real rebuild to timestamp feature building, lifecycle hooks, VS Code
server installation and extensions. Those phases depend on the local editor
build and persisted mounts and cannot be faithfully simulated by `docker run`.

## Same-image 100 MB repack trial

The exact `20260715-2` filesystem was repacked with `--target-size 100MB`, zstd
level 9 and concurrency 2. Repacking took 306 seconds and produced 25 layers,
2,260,031,999 compressed bytes, and a 175,515,123-byte maximum layer. A sampled
process snapshot showed about 2.63 GiB RSS plus 247 MiB swap and roughly one
CPU core of sustained work; final zstd encoders are single-threaded per layer.

The artifact was **rejected before publication and cold testing**. The structural
validator found a regular non-directory path with descendants in the same
layer at:

```text
home/vscode/.local/lib/node_modules/openclaw/node_modules/
@mistralai/mistralai/esm/models/operations
```

Bypassing the gate would compare an artifact that may fail to unpack or silently
lose files. The 100 MB result therefore answers the policy question more
strongly than another timing number: smaller chunks do not remedy the
repacker's incorrect final-filesystem model. Fix the path-state algorithm and
conformance corpus first, then repeat both cold-store tests.

## Historical compression and release evidence

The 2026-06-27 same-image size sweep established that repack target size changes
topology far more than total compressed bytes:

| Policy | Compressed size | Layers |
| --- | ---: | ---: |
| native gzip | 2,776,489,762 bytes | 48 |
| native zstd | 2,308,920,317 bytes | 48 |
| repack 50 MB | 1,883 MiB | 36 |
| repack 100 MB | 1,872 MiB | 22 |
| repack 200 MB | 1,868 MiB | 12 |
| repack 500 MB | 1,857 MiB | 6 |
| repack 1 GB | 1,857 MiB | 4 |
| repack 2 GB | 1,857 MiB | 3 |
| repack 4 GB | 1,856 MiB | 2 |

The old conclusion that 2 GB was “balanced” considered only compressed bytes
and layer count. The 2026-07-15 delivery test disproves that policy for cold
developer startup.

The `20260708-2` release also established operational costs of repack:

- Three parallel zstd-9 jobs reduced four-image repack/push time from about 2
  hours 9 minutes to 20 minutes 48 seconds compared with sequential zstd-14.
- Parallel workers caused short but severe memory and I/O pressure on the
  protected workload because repack ran in the caller cgroup, outside the
  governed BuildKit leaf.
- Host `/tmp` was a 7.9 GiB tmpfs; parallel OCI layouts filled it and converted
  scratch use into memory pressure. Disk-backed `REPACK_WORK_DIR`/`TMPDIR` is
  mandatory.
- `REPACK_JOBS` and per-process `REPACK_CONCURRENCY` multiply. They are
  parallelism knobs, while CPU quota/shares are cgroup scheduling controls.

## Repack ownership decision

Upstream `docker-repack` declares MIT in `Cargo.toml`, so modification and
redistribution are permitted, but its repository lacks a root license file.
A vbpub fork must add the MIT text with upstream attribution. The latest release
is v0.5.0 from January 2025 and serious reports of checksum failure and missing
files remain unresolved. We should assume ownership rather than depend on
upstream maintenance.

Before repack can be a release gate, a fork needs:

- a final-visible-filesystem path tree covering file/directory replacement,
  whiteouts, opaque directories, links, devices and metadata;
- extracted filesystem equivalence and OCI descriptor/diff-ID validation;
- deterministic ordering/timestamps and atomic output;
- preservation of platform and relevant descriptor annotations;
- regenerated SBOM, provenance and signatures for the final digest;
- structured phase/resource telemetry;
- only then zstd-thread and target-layer-count tuning.

Native BuildKit zstd is simpler and retains the normal layer semantics. Repack
remains an experimental benchmark lane until both its conformance corpus and a
time-to-connect advantage pass.
