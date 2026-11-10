# assay Rust R-0 spikes

Measured 2026-11-10 on the `topos-sampler-poc-20261105` sampler. Runs used exact
named Docker containers with `--memory 2g --cpus 2`; offline runs also used
`--network none`.

## Image and versions

Base `rust@sha256:e97df055c54a05160e7c67ba3b2963de6ddb95903911baf8922dbcd1b394f698`:
rustc 1.99.1 / LLVM 23.1.1, cargo 1.99.1, clippy 0.1.99, rustfmt
1.10.0-stable, cargo-nextest 0.9.151, cargo-llvm-cov 0.9.1, cargo-mutants
27.1.0, cargo-deny 0.20.2, Python 3.13.5.

The Dockerfile is
`/tmp/claude-1003/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/rust-r0/Dockerfile`.
It installs tools with network, then runs the only crate fetch:
`cargo fetch --locked`. The runtime has 23 cached `.crate` files (78 MB under
`CARGO_HOME`), no extracted `registry/src`, and the subject plus assay parser.

## S1' — image cache and offline negatives

**PASS, with a material surprise and required hardening.**

```sh
docker run -d --name assay-rust-r0-s1-positive --network none \
  --memory 2g --cpus 2 assay-rust-r0:local \
  bash -c 'cd /work/topos/sampler && cargo test --offline --locked'
docker inspect -f '{{.HostConfig.NetworkMode}}' assay-rust-r0-s1-positive
docker wait assay-rust-r0-s1-positive
```

Docker reported `none`; exit was 0; 81 sampler tests and one probe test passed.

After deleting the cached `serde_json-1.0.154.crate`, Cargo exited 101:

```text
error: failed to download `serde_json v1.0.154`
Caused by:
  attempting to make an HTTP request, but --offline was specified
```

Changing the lock checksum for that package made `--locked` exit 101:

```text
error: checksum for `serde_json v1.0.154` changed between lock files
```

**Surprise: Cargo does not verify a cached archive against Cargo.lock.** Appending
one byte to the cached archive passed. Repacking it after injecting
`compile_error!("tampered cached crate")` also passed Cargo's cache checks; rustc
failed only after the injected source was compiled.

The corrected design uses the build-generated `rust-crate-cache.sha256` and
`verify-crate-cache` before every Cargo lane. Both controls were exercised:

```text
ERROR: cached crate is missing: serde_json-1.0.154.crate
ERROR: cached crate checksum mismatch: serde_json-1.0.154.crate expected e7e9...bd6 got c685...f6a
```

Both exit 1. The precheck is wired into the Dockerfile for R-1; the measured
image was hardened with `Dockerfile.harden` after the cold build.

## S2 — stable cargo-llvm-cov and assay's lcov parser

**PASS for line coverage; SURPRISE for branch capability.**

```sh
cargo llvm-cov --all-targets --offline --locked --lcov \
  --output-path /tmp/sampler-lcov.info
cargo llvm-cov report --summary-only
```

Raw report: 9,347 regions (1,457 missed, 84.41%); 468 functions (94 missed,
79.91%); 4,535 lines (734 missed, 83.81%); 13 absolute `SF:` records.

Probe facts:

| case | observed |
|---|---|
| unused fn | lines 17–19 are `DA:...,0`, function count 0 |
| derive | generated lines 1–4 have no `DA:` (`#[automatically_derived]` skipped) |
| macro body | definition lines 6–14 have no `DA:`; call line 25 is `DA:25,1` |
| wrapped expression | lines 22, 23, 24 are separately `DA:...,1` |
| inline test | lines 35–41 measured; attribute lines 32–34 have no executable record |
| line/region numbers | lcov has line numbers; JSON report has region counts |

Running `assay/src/assay`'s parser from `/opt/assay/src` accepted all 13 files;
all keys were absolute, all `excluded` values were `None`, and the probe's
executed/missing line sets matched the lcov records.

**Surprise:** stable mode emits no `BRDA`, but cargo-llvm-cov writes `BRF:0`
and `BRH:0`. Assay's artifact-level lcov rule turns those markers into empty
branch data rather than unavailable branch data, so `require_branch` could
pass vacuously. R-1 must normalize those marker-only records for Rust or teach
the parser that marker-only output means unavailable.

## S5 — size, contents, Python, build time

**PASS contents; SURPRISE size.**

```sh
docker buildx build --load --resource memory=2g --resource cpu-quota=200000 \
  --build-arg RUST_R0_BUILD_ID=rust-r0-20261110 \
  -t assay-rust-r0:local -f Dockerfile context
docker image inspect assay-rust-r0:local --format '{{.Id}} {{.Size}}'
```

Cold build: 708.988 s (11m49s), image
`sha256:5dc450dcde8e1fe5d23c3d3487d593ad5c2d8064ccbeef1c12744feaebcd44a6`,
1,609,402,586 bytes (1.61 GB). The 1.1–1.4 GB estimate was low: base 922 MB,
Rust components 478 MB, Debian/Python 146 MB, tools/cache 59.4 MB. The
hardened overlay built in 1.501 s from that image.

## S6 — run-gate environment and network-none

**PASS environment selection; FAIL current per-lane network enforcement.**

`run-gate.root.toml` uses explicit environment modes. `[lanes.<name>] environment`
selects one; project entries shadow central entries. `SPEC.md` `R-07` and `R-15`
have no network field and never pass `--network none`.

Intended R-1 entry:

```toml
[environments.tester-unified-rust]
mode = "ephemeral"
image = "tester-unified-rust:local"
image_env = "RUN_GATE_TESTER_UNIFIED_RUST_IMAGE"
cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"
forward_env = ["CGROUP_PARENT_DEV_BACKGROUND"]

[environments.tester-unified-rust.resources]
cpus = "2"
```

This selects the image and host-owned slice, but cannot request network-none.
R-1 must add a supported network policy and thread it into the Docker argv. The
S1' run independently proves the image itself works with
`HostConfig.NetworkMode == "none"`.

## Subject deviations

The sampler's exact `=` pins and its network-fetching Dockerfile conflict with
the 2026-11-08 decisions. They are test-subject deviations only, not adopted by
hostobs or `tester-unified-rust`.
