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

For interactive development and debugging there is a separate developer layer,
`Dockerfile.dev` / [`build-dev-image.sh`](build-dev-image.sh), driven by
`hostobs/tools/dev` on the gate host; see [`docs/DEV.md`](docs/DEV.md). It reuses this
image's exact toolchain and crate cache, and a green dev run is not gate evidence.

The BuildKit builder defaults to `mdt-managed` and is selectable with
`--builder NAME` or `TESTER_UNIFIED_RUST_BUILDER` for hosts that only have the
`default` docker-driver builder. Both build scripts take ONE input, the systemd
slice name (`--cgroup-parent dev-gates.slice` or `CGROUP_PARENT_DEV_GATES`), and
derive the cgroup path for `docker buildx build --cgroup-parent` themselves
(`dev-gates.slice` becomes `dev.slice/dev-gates.slice`; every `docker run` and the
CMRU preflight keep the plain name, because the systemd cgroup driver refuses a
path there). Measured on v1001 (2026-11-18, docker-driver BuildKit): with the path
form the BuildKit RUN steps run inside the governed slice
(`/dev.slice/dev-gates.slice/buildkit/<id>`); with the plain slice name they would
land in an ungoverned root-level `/dev-gates.slice/buildkit`. The docker-container
driver is not covered by that measurement. `--builder` changes nothing else; see
[`docs/CONSUMERS.md`](docs/CONSUMERS.md), which also lists what the CMRU
preflight needs (a git checkout among other things).

The first consumer is the hostobs M0 workspace. Its registered `lint` and
`selftest` lanes live in [`hostobs/run-gate.toml`](../hostobs/run-gate.toml).
See [`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the image and lane contract,
and [`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the design choices.
