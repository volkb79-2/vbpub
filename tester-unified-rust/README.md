# tester-unified-rust

`tester-unified-rust` is the dedicated offline gate image for Rust workspaces.
It keeps Rust toolchains and cargo tools out of `tester-unified`, so Python
projects retain their existing gate image. The image carries stable Rust,
Clippy, rustfmt, LLVM tools, cargo-nextest, cargo-llvm-cov, cargo-mutants,
cargo-deny, Python, and the crate archive cache for the reviewed hostobs lock.

Build from the repository checkout with
[`build-image.sh`](build-image.sh). The image build uses network access only
to install the toolchain tools and run `cargo fetch --locked`. Registered Rust
lanes run with Docker networking disabled and verify the lock and cached
archive checksums before every Cargo invocation.

The first consumer is the hostobs M0 workspace. Its registered `lint` and
`selftest` lanes live in [`hostobs/run-gate.toml`](../hostobs/run-gate.toml).
See [`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the image and lane contract,
and [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the design choices.
