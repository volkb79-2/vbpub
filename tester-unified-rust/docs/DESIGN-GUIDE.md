# tester-unified-rust design

Rust uses a sibling image because adding the toolchain to `tester-unified`
would rebuild the shared Python gate environment for a separate language
consumer. The base and cargo-tool versions match the measured R-0 image.
CMRU's reviewed Rust version resolver and lock/image refresh are later R-5
work; this bootstrap image preserves the R-0 pins as explicit Docker build
arguments.

The image fetches the hostobs lock during build, then stores only the Cargo
tool binaries, registry index, and `.crate` archives. It does not vendor
dependencies in Git. The precheck binds three facts before Cargo runs: the
checkout lock bytes equal the image's baked lock identity, the lock's
crates.io package set equals the cache manifest, and each archive matches
both the lock checksum and the baked manifest digest.

BuildKit and gate containers use `CGROUP_PARENT_DEV_GATES`; host policy owns
their CPU and memory capacity. The image and lane files do not set per-machine
resource ceilings.

Every lane selects `network = "none"` and uses `--locked --offline`. A lock
change therefore requires a reviewed image rebuild; a missing crate or
altered archive is a named precheck failure, before Cargo can attempt a
network request.
