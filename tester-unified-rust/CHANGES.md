# Changelog

## [Unreleased]

### Changed

- The image lock identity is the registry-set digest (`lock-version N` plus one sorted line per package with a `source`; workspace members excluded) instead of the sha256 of the whole `hostobs/Cargo.lock`, so a path dependency between workspace crates needs no image rebuild. One function in `hostobs/tools/verify-crate-cache.py` defines it, exposed as `--print-registry-digest --lock PATH`; the Dockerfile and `build-image.sh` call it. Renamed: build arg `HOSTOBS_LOCK_REGISTRY_SHA256`, label `org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256`, baked file `/usr/local/share/hostobs-Cargo.lock.registry-sha256`, wrapper flag `--baked-registry-sha256`; `build-image.sh` prints `hostobs_lock_registry_sha256=`. The `tester-unified-rust` environment in `run-gate.root.toml` names the image built from this source, `tester-unified-rust:rs-20261118`; `tester-unified-rust:local` keeps the whole-lock identity until it is re-pointed.

### Fixed

- The `selftest` guard tests follow `coverage-floors.toml` (per-crate floors) and the current write-guard message, and the lane-set test knows `assay-r1`; they were red at the merge base.
- `build-image.sh` passes the `cmru tester-gate` bounds through `CMRU_TESTER_*` variables instead of shell-expanded option values, so the estate argv check in cmru parses the invocation (KI-173).

### Added

- **Developer layer image source** `Dockerfile.dev` (`FROM` the content-named tag of the id of the `--base` image, default `tester-unified-rust:local`, adds gdb, strace, procps, less, ca-certificates, rust-src and rust-analyzer on the already installed toolchain; the build fails if `rustc -vV` changes) and `build-dev-image.sh` (`--tag`, `--base`, `--builder`, `--cgroup-parent`, `--record FILE` for the 14-day vetting). Source only: no image was built; the gate image is unchanged. Use and limits: `docs/DEV.md`; wrapper: `hostobs/tools/dev`; tests: `tests/test_dev_environment.py`; the `selftest` lane also `bash -n`s the new scripts.
- `build-image.sh --builder NAME` (environment `TESTER_UNIFIED_RUST_BUILDER`, option first, default `mdt-managed`) selects the buildx builder for the preflight inspect and the build, for hosts that only have the `default` docker-driver builder (v1001); invalid names are refused with exit 2 before any docker call, `--cgroup-parent` stays required, and `builder=<name>` is printed before the build. Nothing else changes; the docker driver passes `--cgroup-parent` through (measured on v1001): check each build's cgroup placement and discard a build that is not under the slice. The builder-name check runs under `LC_ALL=C` so it is locale-proof.
- Added the dedicated Rust gate image with a lock-bound offline crate cache, cache precheck, and registered selftest lane.
