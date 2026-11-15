# Using tester-unified-rust

Declare the central Run-Gate environment `tester-unified-rust` and set
`network = "none"` on each Rust Cargo lane. The environment binds container
placement to the host's `CGROUP_PARENT_DEV_GATES` value; it has no fallback.
The Run-Gate lane owns the container network boundary.

From the repository root, build the local bootstrap image with:

```sh
nice -n 19 tester-unified-rust/build-image.sh
```

The script requires the host-injected gates slice, runs CMRU's host-systemd
preflight, builds with the managed BuildKit builder, and verifies the image
labels against the current base and `hostobs/Cargo.lock`. Image builds use
network access for `cargo install` and `cargo fetch --locked`; lane containers
do not have network access.

In a Rust workspace, route all Cargo calls through a checked wrapper that
invokes `/usr/local/bin/verify-crate-cache` first, then sets
`CARGO_NET_OFFLINE=true` and calls Cargo with `--locked --offline`. The image
currently carries the hostobs lock closure. A different workspace lock must
be included in a reviewed image rebuild before its lane can pass.

The `selftest` lane in this project checks the precheck, lint-boundary guard,
crate-floor tool, and shell entrypoints. Hostobs's `lint` and `selftest` lanes
exercise the real Cargo toolchain and cache on `network = "none"`.
