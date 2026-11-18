# tester-unified-rust

`tester-unified-rust` is the dedicated offline gate image for Rust workspaces.
It keeps Rust toolchains and cargo tools out of `tester-unified`, so Python
projects retain their existing gate image. The image carries stable Rust,
Clippy, rustfmt, LLVM tools, cargo-nextest, cargo-llvm-cov, cargo-mutants,
cargo-deny, Python, and the crate archive cache for the reviewed hostobs lock.

Build from the repository checkout with
[`build-image.sh`](build-image.sh). The image build uses network access only
to install the toolchain tools and run `cargo fetch --locked`. Registered Rust
lanes run with Docker networking disabled and verify the lock's registry set
and cached archive checksums before every Cargo invocation. The image identity
is the registry-set digest of the lock (downloaded packages and lock version,
not workspace members), so a workspace-only lock edit needs no rebuild; see
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for which image tag runs what.

The BuildKit builder defaults to `mdt-managed` and is selectable with
`--builder NAME` or `TESTER_UNIFIED_RUST_BUILDER` for hosts that only have the
`default` docker-driver builder. The docker driver passes `--cgroup-parent`
through (measured on v1001; the docker-container driver does not honour it):
during a RUN step the build's processes must appear under the requested slice
(`systemd-cgls` or `<slice>/cgroup.procs` on the host), and a build that does
not land there is to be discarded. `--builder` changes nothing else; see
[`docs/CONSUMERS.md`](docs/CONSUMERS.md), which also lists what the CMRU
preflight needs (a git checkout among other things).

The first consumer is the hostobs M0 workspace. Its registered `lint` and
`selftest` lanes live in [`hostobs/run-gate.toml`](../hostobs/run-gate.toml).
See [`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the image and lane contract,
and [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the design choices.
