# assay Rust R-0 spikes

Measured 2026-11-10 on the Rust sampler subject. Cargo, rustc, and cargo-llvm-cov ran only in detached containers with `--network none --memory 2g --cpus 2`, placed under `$CGROUP_PARENT_DEV_GATES`. The exact commands and raw outputs are preserved under [rust-r0/evidence](rust-r0/evidence/).

The adopted tier topology is T1 [`lint`, `assay-r1`], T2 [`lint`,
`assay-r3`, `conformance`] without `assay-r1`, and a separate T3 lane for the
uncovered-line probe. T2 `assay-r3` uses one test-break/import-break
receipt-writing canary mechanism; `gate-full` is the T2 sequence.

## R-1 durable handoff

The production Rust image belongs at `tester-unified-rust/` in R-1. The committed `assay/docs/rust-r0/Dockerfile` and cache artifacts are examples of the R-0 experiment, not a build target; repository Dockerfile and dockerignore guard tests do not apply to them.

The example files are:

- Dockerfile: `assay/docs/rust-r0/Dockerfile`
- Precheck: `assay/docs/rust-r0/verify-crate-cache.sh`
- Measured 23-archive manifest: `assay/docs/rust-r0/rust-crate-cache.sha256`

The R-1 image installs the script at `/usr/local/bin/verify-crate-cache` and generates its own lock-specific manifest at `/usr/local/share/rust-crate-cache.sha256`. Invoke the precheck before every Cargo lane command:

```sh
/usr/local/bin/verify-crate-cache /usr/local/share/rust-crate-cache.sha256
cargo test --offline --locked
```

The measured R-0 image's earlier precheck had the manifest path fixed in the script and was invoked as `/usr/local/bin/verify-crate-cache`; the committed example takes the manifest path explicitly.

## S1′ — image cache and offline negatives

**PASS with required cache hardening.** The raw launch commands, detached wait/log/remove commands, and complete outputs for each probe are in [s1-raw-probes.md](rust-r0/evidence/s1-raw-probes.md).

| Probe | Result |
|---|---|
| Positive `cargo test --offline --locked` with Docker network mode `none` | Exit 0; the probe test and 81 sampler tests passed. |
| Remove locked `serde_json-1.0.154.crate`, then run Cargo offline | Exit 101; Cargo names the missing crate and refuses its network request. |
| Append a byte to the cached archive, then run Cargo offline | Exit 0. Cargo does not validate a cached archive against Cargo.lock. |
| Repack the archive with `compile_error!("tampered cached crate")`, then run Cargo offline | Exit 101 at compilation; Cargo accepted and extracted the altered archive. |
| Change the Cargo.lock checksum, then run with `--locked` | Exit 101; Cargo reports the changed checksum. |
| Run the manifest precheck against the intact cache | Exit 0, no output. |
| Remove an archive or alter its bytes, then run the manifest precheck | Exit 1; it names the missing archive or prints expected and actual SHA-256 values. |

At image build time, the manifest is generated after the lock-directed fetch:

```sh
find /usr/local/cargo/registry/cache -type f -name '*.crate' -print0 \
  | sort -z | xargs -0 sha256sum > /usr/local/share/rust-crate-cache.sha256
```

The checked-in manifest is the captured R-0 output. R-1 regenerates it from the production image's own Cargo.lock closure. Every Rust Cargo lane must run the manifest precheck before Cargo; Cargo's `--locked` check is still required alongside the precheck.

## S2 — stable cargo-llvm-cov and assay's lcov parser

The exact detached command, cargo stdout/stderr, summary report, parser invocation/output, SHA-256, accepted `SF:` lines, and branch-marker counts are in [s2-command-output.md](rust-r0/evidence/s2-command-output.md). The full generated tracefile is [s2-sampler-lcov.info](rust-r0/evidence/s2-sampler-lcov.info); the parser JSON is [s2-parser.json](rust-r0/evidence/s2-parser.json), and the summary table is [s2-summary.txt](rust-r0/evidence/s2-summary.txt).

The parser accepted 13 files with absolute keys and `excluded = None` for all files. The probe's executed lines were 21–26 and 35–41; its missing lines were 17–19 and 28–30. The summary was 9,347 regions (1,457 missed; 84.41%), 468 functions (94 missed; 79.91%), and 4,535 lines (734 missed; 83.81%). The tracefile contains 13 `SF:` records and 13 each of `BRF:0` and `BRH:0`; there are no `BRDA:` records.

**R-1 branch decision:** implement marker handling in `assay/src/assay/coverage_parsers/lcov.py`. If the artifact has no `BRDA:` records and contains only zero-valued `BRF:`/`BRH:` markers, report branch capability as unavailable (`branches = None`), not as measured empty branch coverage. Stable Rust lanes keep `require_branch = false`.

## S5 — image size, contents, and build time

The exact `docker image inspect` output is in [s5-image-inspect.txt](rust-r0/evidence/s5-image-inspect.txt), the pinned base image inspection is in [s5-base-image-inspect.txt](rust-r0/evidence/s5-base-image-inspect.txt), and the full rounded layer listing is in [s5-layer-history.txt](rust-r0/evidence/s5-layer-history.txt). Tool versions are in [s5-versions.txt](rust-r0/evidence/s5-versions.txt); byte-count measurements are in [s5-component-measurements.txt](rust-r0/evidence/s5-component-measurements.txt); build timing records are in [s5-original-build-time.txt](rust-r0/evidence/s5-original-build-time.txt), [s5-hardened-overlay-time.txt](rust-r0/evidence/s5-hardened-overlay-time.txt), and [s5-rebuild-time.txt](rust-r0/evidence/s5-rebuild-time.txt).

The reconstituted image inspected at 1,609,401,991 bytes (1.6094 GB decimal; 1.499 GiB). Its pinned base image was 921,984,474 bytes; the image total minus base was 687,417,517 bytes. This measured image size replaces the earlier estimate.

The exact cache scopes reconcile the earlier 78 MB and 59.4 MB figures:

- The 23 cached `.crate` archives total **1,889,690 bytes**.
- `/usr/local/cargo` totals **80,784,603 bytes** by `du -sb`; it includes 1,889,690 bytes of archives, a 2,262,027-byte sparse index, and 76,486,782 bytes of installed Cargo tools. The earlier “78 MB” described the whole Cargo home directory in rounded `du -sh` output, not the archive files.
- Docker history reports the `COPY /usr/local/cargo` layer as **59.4 MB**. That is a rounded image-layer figure, not the directory byte count or archive-only count.
- Other measured additions were 504,491 bytes for the sampler subject, 3,411,404 bytes for assay's parser, 595 bytes for the precheck, and 3,551 bytes for the generated manifest. Docker history's human-readable rows round each layer; the displayed row sum is about 1,609.218 MB, while image inspect reports the exact 1,609.402 MB total.

The preserved original cold build took 708.988 seconds; its hardened overlay took 1.501 seconds. Rebuilding after the local image had been pruned took 11m28.109s. This image contains rustc 1.99.1 / LLVM 23.1.1, cargo 1.99.1, clippy 0.1.99, rustfmt 1.10.0-stable, cargo-nextest 0.9.151, cargo-llvm-cov 0.9.1, cargo-mutants 27.1.0, cargo-deny 0.20.2, and Python 3.13.5.

## S6 — Run-Gate selection and network enforcement

The inspected Run-Gate config and normative SPEC excerpts are preserved in [s6-inspected-config-spec.txt](rust-r0/evidence/s6-inspected-config-spec.txt). Current `run-gate.root.toml` selects explicit environment modes and binds the shared gates slice through `cgroup_slice_env`. SPEC R-07 lists environment keys without a network key; R-15's detached Docker argv has no `--network` argument. Environment selection is explicit, but current Run-Gate cannot enforce offline container networking.

R-1 adds this **lane-level** setting to every Rust Cargo lane:

```toml
[lanes.assay-r1]
environment = "tester-unified-rust"
network = "none"
```

For ephemeral lanes, Run-Gate must translate it to `--network=none` on the detached `docker run` command. It must refuse the setting for `exec` and `host` environments because those modes do not create a disposable Docker container. The S1′ positive run independently verified that the image passes with Docker network mode `none`.

## R2 status and subject deviations

Rust R2 is **not yet adopted**. Record that once as a known issue; it is not a per-release waiver. The deferred cargo-mutants work remains an adoption decision and later work package.

The sampler subject has exact `=` pins and a network-fetching Dockerfile. Those are subject-only deviations, not decisions for hostobs or `tester-unified-rust`.
