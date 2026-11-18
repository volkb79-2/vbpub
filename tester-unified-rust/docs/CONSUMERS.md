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
labels against the current base and the registry-set digest of
`hostobs/Cargo.lock`. Image builds use network access for `cargo install` and
`cargo fetch --locked`; lane containers do not have network access.

The builder is selectable: `--builder NAME`, else the environment variable
`TESTER_UNIFIED_RUST_BUILDER`, else the default `mdt-managed` (NAME matches
`^[A-Za-z0-9][A-Za-z0-9._-]*$`; anything else is refused with exit 2 before any
docker call). Hosts without the managed builder, such as the gate host v1001 and
its cockpit container, which only have the `default` docker-driver builder, run
`build-image.sh --builder default ...`; the script never creates a builder. As
documented buildx behaviour, the `default` docker-driver builder honours
`--cgroup-parent` for RUN steps; verify on first use: the build's cgroup
placement. `--builder` changes nothing else: `--cgroup-parent` stays required, the
CMRU preflight, build arguments and label checks are unchanged, and the script
prints `builder=<name>` before it builds.

In a Rust workspace, route all Cargo calls through a checked wrapper that
invokes `/usr/local/bin/verify-crate-cache` first, then sets
`CARGO_NET_OFFLINE=true` and calls Cargo with `--locked --offline`. The image
carries the hostobs lock's crate closure. A different workspace lock must be
included in a reviewed image rebuild before its lane can pass, with one
exception by design: a lock edit that only touches workspace members (a path
dependency between workspace crates) leaves the image identity unchanged and
needs no rebuild.

### Image identity

The identity baked into the image is the registry-set digest of the lock: the
sha256 of `lock-version N` followed by one sorted line per package that has a
`source` (`name version checksum`, plus the source text when it is not
crates.io; `-` for a missing checksum). Workspace members have no `source` and
are excluded. `python3 hostobs/tools/verify-crate-cache.py
--print-registry-digest --lock PATH` prints it; `build-image.sh` and the
Dockerfile both call that one function. Names: build arg
`HOSTOBS_LOCK_REGISTRY_SHA256`, image label
`org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256`, baked file
`/usr/local/share/hostobs-Cargo.lock.registry-sha256`, wrapper flag
`--baked-registry-sha256`. The crates.io-only rule, the per-archive manifest
check, the "lock registry set equals the baked manifest set" check and the
on-disk archive sha256 recheck are unchanged.

### Which image runs what

An image is self-consistent: its baked verifier and its baked digest file
belong together. The currently deployed `tester-unified-rust:local` was built
before this change: it carries the old whole-lock verifier, `hostobs-Cargo.lock.sha256`
and the old label, and it refuses any lock byte change. Lane files that only call the
baked `/usr/local/bin/verify-crate-cache "$PWD/Cargo.lock"` work on both images
(that call is unchanged), but a lock edit, even workspace-only, passes only on
an image built from this source.

Rollout: a NEW tag is built on a host later with `build-image.sh --tag
tester-unified-rust:<new>`; `run-gate.root.toml` is switched to it by a
separate commit; the old tag stays until every Rust lane has passed on the new
one; then `:local` is re-pointed. Nothing here claims that a new image exists
yet.

The `selftest` lane in this project checks the precheck, lint-boundary guard,
crate-floor tool, and shell entrypoints. Hostobs's `lint` and `selftest` lanes
exercise the real Cargo toolchain and cache on `network = "none"`.
