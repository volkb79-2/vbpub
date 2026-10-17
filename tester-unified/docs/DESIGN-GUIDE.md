# tester-unified design guide

## Cockpit and gate boundary

The cockpit has editor tooling and Docker control, but its own cgroup and
Python environment are not release evidence. `tester-unified/run` makes the
boundary executable: it starts `tester-unified:local` detached with Docker's
init reaper in the declared background slice and waits for Docker's job status
before reading logs. The reaper is part of the lifecycle contract: a gate or
test command that leaves descendants behind must not turn them into zombies
that consume the shared gate cgroup's PID budget.

The workspace is mounted twice because linked-worktree gitfiles can name the
cockpit path while Docker resolves bind sources in the host namespace. A
temporary source directory beneath the worktree's ignored `.assay/` is mounted
at `/tmp`, then exported as `TMPDIR`, `TMP`, and `TEMP`. Tests that create
throwaway repositories can derive a real host path while remaining outside the
judged worktree's `.git` ancestor. When the workspace mount is itself `/tmp`,
the launcher selects `/var/tmp/tester-unified` instead; the workspace's own
host-backed `/tmp` remains available to an environment-scrubbing nested judge,
and the two mount destinations never collide.

The Docker socket is deliberate. Several product tests exercise real nested
container boundaries, including host-lane exit propagation. The socket's
numeric group is read from the socket and added to the tester process rather
than assumed. The image still supplies the complete uid 1003 passwd/group,
HOME, and XDG identity.

The tester image also carries Assay's build-system requirements and gives the
test uid write access to its venv. Internal vbpub lanes install the selected
worktree's Assay source with `--no-build-isolation`; baking only the backend
closure keeps that install offline and deterministic while still recording the
exact runtime Assay version in the verdict. The image does not install a
copied Assay tree, because the judged worktree is the source of truth.

## The installed Run-Gate CLI

Some CMRU steps invoke Run-Gate after CMRU has started tester-unified. The image
installs the exact Run-Gate wheel built from the selected checkout, exposes its
console script from `/opt/tester-venv`, and puts that environment first on
`PATH`. The supported image builder passes one wheel through a named BuildKit
context, verifies its digest during the build, and checks the installed
metadata version against the wheel's version. This keeps command lookup
independent of the base image's private `/home/vscode/.venv`, which uid 1003
cannot traverse.

The image build is a host workload too. `build-image.sh` requires the declared
gates slice (or an explicit override), verifies that slice through CMRU's
host-systemd probe, and passes it to Buildx. When an explicit `--base-image` is
selected, CMRU's unprivileged preflight runs from that same base. This breaks
the first-image bootstrap loop without weakening the separate digest-pinned,
pull-disabled host-systemd probe. The loaded image carries the exact Run-Gate
wheel version and digest as labels; the builder checks those labels before
reporting success. The release controller injects tester resource settings
from the estate configuration. Direct shell builds must provide those same
declared settings explicitly, and the pinned host probe must already be
present locally.

The default base is MDT's published devcontainer image for parity with the
cockpit. The explicit `--base-image` override supports first-release
bootstrapping: it lets the tester image start from the upstream Python
devcontainer while vbpub has no released images. The Dockerfile installs its
small host-tool set itself. A missing MDT release never triggers an implicit
fallback; the base choice is visible in the build command. The image also
records that choice as the `org.vbpub.tester-unified.base-image` label, and the
builder and Run-Gate's release preparation compare it with the requested base.
Buildx only warns about a build argument that no stage consumes, so a renamed
`ARG` would otherwise build the default base while the command named another.
The label records the reference, not a digest: a mutable tag still resolves at
build time.

The Docker client is installed by capability, not unconditionally. Debian's
`docker-cli` / `docker-compose` own the same files as Docker's `docker-ce-cli` /
`docker-compose-plugin` that the MDT base ships, so installing them over the
MDT base makes dpkg refuse the overwrite or, if apt resolved the clash, quietly
swaps the gate's Docker client. The step therefore installs `docker-cli` only
when `docker` is absent from PATH and `docker-compose` only when
`docker compose version` does not answer, then verifies both. Both base
variants are covered by an executable oracle
(`run-gate-project/tests/test_tester_unified_base_variants.py`) that runs the
Dockerfile's real RUN step against a dpkg-style ownership clash; building an
image on each base remains a release-lane activity.

The wheel supplies the command entrypoint; the bind-mounted checkout supplies
lane configuration and judged project files. CIU's release gate also compares
the installed Run-Gate module bytes with the selected checkout and refuses a
stale tester image. `tester-unified:local` is one mutable tag shared by
worktrees, so a rebuild and the exact-tree gate that consumes it must be
serialized. Distinct image tags are needed for concurrent exact-tree runs that
need different Run-Gate versions.

The launcher refuses missing cgroup configuration, workspace-root fallbacks,
high launch-time memory pressure, absent Docker access, missing images, more
than two concurrent tester containers, and any mismatch in Docker's accepted
user/cgroup/CPU/workdir/mount state. Count and launch are serialized by one
lock beneath the enclosing workspace's ignored `.assay/`, not by a per-worktree
lock. These are infrastructure outcomes, not functional test verdicts.

See [the user-facing overview](../README.md) and
[consumer commands](CONSUMERS.md#running-a-project-gate).

## Offline gates

`--network none` lets exact-OID wheel/self-hosting gates reuse this launcher
while retaining their existing offline container boundary. Reimplementing the
P4 namespace, identity, admission and transport recipe just to disable network
access would recreate the duplication the canonical launcher removed. Docker
must accept `NetworkMode=none`, and this fact is recorded with the launch
metadata; a mismatch aborts only the owned container. The only explicit network
value is `none`. Omitting the option preserves existing Docker selection;
the launcher does not invent a network name. The Docker socket remains mounted
for product boundary tests, so this is network isolation, not a security sandbox.
