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
checkout lock's registry-set digest equals the image's baked identity, the
lock's crates.io package set equals the cache manifest, and each archive
matches both the lock checksum and the baked manifest digest.

The identity is the registry-set digest, not the whole lock file, because
what the image cache depends on is the downloaded packages (name, version,
checksum, source) and the lock format version. A path dependency between
workspace crates only rewrites the `dependencies` arrays of workspace members
(packages without a `source`), which changes no downloaded crate, so it must
not force an image rebuild. Nothing is weakened: any added, removed or
changed registry or git package, checksum or source, and a changed lock
`version`, still changes the digest and fails closed, and a non-crates.io
source is still refused. Package order in the file is irrelevant (the lines
are sorted). One function in `hostobs/tools/verify-crate-cache.py` defines
the digest; the Dockerfile and `build-image.sh` call it through
`--print-registry-digest` instead of re-implementing it in shell.

`[[patch.unused]]` entries in the lock are not part of the digest: Cargo fetches
nothing for an unused patch. If such a patch later became used, `cargo --locked`
rejects the stale lock before any build.

BuildKit and gate containers use `CGROUP_PARENT_DEV_GATES`; host policy owns
their CPU and memory capacity. The image and lane files do not set per-machine
resource ceilings.

Every lane selects `network = "none"` and uses `--locked --offline`. A lock
change of the registry set therefore requires a reviewed image rebuild (a
workspace-member-only edit does not); a missing crate or altered archive is a
named precheck failure, before Cargo can attempt a network request.
