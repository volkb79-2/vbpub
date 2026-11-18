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
`build-image.sh --builder default ...`; the script never creates a builder.
`--cgroup-parent` is expected, but not yet verified, to be passed through by the
docker driver (the docker-container driver does not honour it). Verify on first
use: during a RUN step, the build's processes must appear under the requested
slice (`systemd-cgls` or `<slice>/cgroup.procs` on the host); a build that does
not land there is to be discarded. `--builder` changes nothing else:
`--cgroup-parent` stays required, the CMRU preflight, build arguments and label
checks are unchanged, and the script prints `builder=<name>` before it builds.

The CMRU preflight (`cmru tester-gate`, run by the script as its governance
check and never skipped) calls `discover_git_root` and refuses outside a git
worktree, so the checkout the script runs in must be a git repository or linked
worktree. A plain `git archive` export needs `git init && git add -A && git
commit` first (a local one-commit repository is enough, no remote). The
preflight also needs the `tester-unified:local` image (the default
`CMRU_TESTER_UNIFIED_IMAGE`, `cmru.orchestration.toml`) and the digest-pinned
probe image (`CMRU_TESTER_CGROUP_PROBE_IMAGE`) present or pullable on the
daemon, python3 >= 3.11 (`tomllib`), and `hostobs/tools/verify-crate-cache.py`
plus `cmru.orchestration.toml` in the checkout.

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
belong together. `tester-unified-rust:local` carries the whole-lock verifier,
`hostobs-Cargo.lock.sha256` and the old label, and it refuses any lock byte change.
Lane files that only call the baked `/usr/local/bin/verify-crate-cache "$PWD/Cargo.lock"`
work on both images (that call is unchanged), but a lock edit, even workspace-only,
passes only on an image built from this source.

The `tester-unified-rust` environment in `run-gate.root.toml` names
`tester-unified-rust:rs-20261118` (registry-set identity). `:local` is the older
whole-lock image; it is re-pointed to the new tag only after every Rust lane has passed
on it. A new tag is built with `build-image.sh --tag tester-unified-rust:<new>` and
adopted by changing that one `image` line.

The `selftest` lane in this project checks the precheck, lint-boundary guard,
crate-floor tool, and shell entrypoints. Hostobs's `lint` and `selftest` lanes
exercise the real Cargo toolchain and cache on `network = "none"`.
