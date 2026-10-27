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
installs one exact Run-Gate wheel (a developer build from the selected checkout via
`--wheel-dir`, or the published release via `--run-gate-published` for the image that
gates releases, see below), exposes its console script from `/opt/tester-venv`, and
puts that environment first on `PATH`. The supported image builder passes one wheel through a named BuildKit
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

The default base is `tester-base:local` (MDTC-02): the minimal, digest-pinned gate base, not the
floating 8 GB MDT cockpit image, so a cockpit release can no longer change the gate environment
without a commit. After tester-base's first release the controller switches the default to its
published digest (the one pin site is the Dockerfile's `ARG TESTER_UNIFIED_BASE_IMAGE=`, an ARG name
that build-image.sh and Run-Gate's release preparation parse). The explicit `--base-image` override
supports first-release bootstrapping: it lets the tester image start from the upstream Python
devcontainer while vbpub has no released images. The Dockerfile installs its
small host-tool set itself. A missing base never triggers an implicit
fallback; the base choice is visible in the build command. The image also
records that choice as the `org.vbpub.tester-unified.base-image` label, and the
builder and Run-Gate's release preparation compare it with the requested base.
Buildx only warns about a build argument that no stage consumes, so a renamed
`ARG` would otherwise build the default base while the command named another.
The reference label records a name, which can be a mutable tag (`tester-base:local`), so a second
label, `org.vbpub.tester-unified.base-image-id`, records the identity (image id or digest) the
reference resolved to when `build-image.sh` built the image; the builder refuses a base it cannot
identify and re-reads the label after the build. Two images built on different content of the same
tag are therefore distinguishable.

The Docker client is installed by capability, not unconditionally. Debian's
`docker-cli` owns the same file as Docker's `docker-ce-cli` that tester-base and an MDT base ship,
so installing it over them makes dpkg refuse the overwrite or, if apt resolved the clash, quietly
swaps the gate's Docker client. The host-tools step therefore installs `docker-cli` only when
`docker` is absent from PATH. Compose is a separate step (below). Every base variant is covered
by an executable oracle (`run-gate-project/tests/test_tester_unified_base_variants.py`) that runs
the Dockerfile's real RUN steps against shim tools and a dpkg-style ownership clash; building an
image on each base remains a release-lane activity.

## Userland added on top of the base
Beyond the Docker client step, this image adds (all before the source-built CMRU wheel, so the cmru
Dockerfile oracle that forbids a later layer rewriting that wheel still holds): `zsh` (the mdt-cockpit
login-hook tests exercise it), Node.js with npm and npx copied from a digest-pinned official node image
stage (it is only a `FROM <image>@sha256:` stage, which the oracle asserts) and the **real `systemctl`
client** from Debian's `systemd` package.

Why `systemctl`: the cgroup-profiler placement probe (`scripts/cgroup-profiler/tools/gate.sh`,
`lib/access.py` `_PLACEMENT_PROBE`, "placement verifier image must provide systemctl") starts a probe
container from this image and runs `systemctl show <slice> --property=LoadState,FragmentPath,ControlGroup`
inside it against the HOST system bus (`/run/dbus/system_bus_socket` and `/run/systemd/system` bind-mounted
read-only), the estate proof that a slice name is a real unit and not a fail-open transient one. The old
cockpit base carried the binary; tester-base does not, and without it the probe exits 127. What it can do:
with the bus and `/run/systemd/system` mounted it answers for the host. What it cannot: in a plain
gate container there is no systemd, so it exits 1 (`Failed to connect to system scope bus`, or `System has
not been booted with systemd`); tests must not rely on it (topos treats an unavailable systemctl as
`unavailable`, ciu tests inject the lookup, assay's cgroup-parent tests fake it). No stub is installed
(MDTC-02 D-001), and the package also puts `busctl`, `systemd-run` and `systemd-tmpfiles` on PATH: the
one test that skips when `systemd-tmpfiles` is absent is `os_integration`-marked and deselected by every
lane that runs its suite. The revert path is a dedicated probe image for the cgroup probes.

The Docker compose layer (INT-01 D-006, decided): real `docker compose` is needed in this image (ciu has
16 test files that drive it, plus topos, nyxloom, cmru and run-gate-project). tester-base keeps the plugin
out (T2), so on it this image installs Docker's OWN `docker-compose-plugin` at an exact version, not
Debian's `docker-compose` (an unpinned, differently built 2.26.1). The package is downloaded from Docker's
apt pool by exact version and checked against a pinned sha256 per architecture before `dpkg -i`, so it needs
neither the base's apt source nor a "latest" lookup, and a bump is a reviewed edit of the three
`DOCKER_COMPOSE_PLUGIN_*` ARGs in the Dockerfile. Pin evidence under the 14-day freshness rule: docker/compose
v5.6.0 was published 2026-10-02 (17 days before the 2026-10-19 pin) and Docker's apt index serves it as
`5.6.0-1~debian.13~trixie`. The step only runs when `docker compose version` does not already answer (an MDT
base ships the plugin) and the image build fails unless it does afterwards. The oracle
`cmru/tests/test_tester_unified_image.py::test_compose_is_dockers_pinned_plugin_not_debians_package`
refuses a floating or missing version, a missing sha256, any bare `docker-compose` package name and an apt
install of the plugin; the shim test above executes the step, including the checksum-mismatch stop.

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

## The image that gates a release: published inputs

The release gate binds three identities: the host's installed Run-Gate judge, the published GitHub release it
came from, and the selected tester image's Run-Gate labels (`cmru/tools/run_release_gate.py`,
`_check_run_gate_release_artifacts` and `_check_tester_image_identity`). A tester image therefore has to carry
the published wheel, byte for byte. `build-image.sh --run-gate-published` stages exactly that through
`fetch-run-gate-wheel.py`, which requires the `latest.json` pointer, the release's `.sha256` sidecar and the
downloaded bytes to agree before anything is written (the redirect policy is the one `fetch-cli-extended.py`
enforces). No age window applies to this first-party wheel: the gate demands the published release, not an aged
one. CMRU stays source-built (`+tester.unified`) because it is installed only for its dependency closure while the
suites run against the bound worktree; Assay is not installed at all (run-gate installs the worktree's `assay/`
at lane runtime), so no published Assay or CMRU wheel is needed in the image.

Third-party packages follow the estate freshness rule instead: nothing is pinned, and pip's
`--uploaded-prior-to` (pip 26.0 or newer, which the base's bundled pip satisfies) restricts every index-capable
install to releases uploaded at least `PIP_MIN_RELEASE_AGE` (default `P14D`) ago. A build argument rather than a
file of pins keeps the "newest, but vetted" policy in one place; the cost is that two builds of the same
Dockerfile on different days differ, which is why the age is recorded as an image label and a rebuild uses
`--no-cache`. The duration is pip's own vocabulary (`P<n>D`, whole days; `build-image.sh` only pre-checks the
spelling), the cutoff is computed when pip runs, and pip fails closed: a link without `upload-time` metadata (an
index that is not PyPI-compatible on that point, such as a bare mirror) is an installation error, not a silently
ignored window. `--no-index` installs are outside it, which is what makes the first-party wheels exempt. The
Debian/Python base digest and the node image digest are the deliberate exceptions: they are
digest-pinned and age-checked by `cmru versions` (`tester-base/versions/`). `wheel-builder` ages the two installs
baked into it the same way; the build backends that `python -m build` installs at run time are a separate matter
(tester-base backlog TB-08).

Three checks bind an image to a Run-Gate and agree at one point only (the consumer runbook,
[where the rebuild sits in the release order](CONSUMERS.md#where-the-rebuild-sits-in-the-release-order-and-the-bootstrap),
spells out the order): cmru's gate wants the published bytes, ciu's preflight wants the checkout's bytes, and
run-gate's own release builds its candidate image from the candidate wheel with the previous image as the
preflight host. The last is the bootstrap; the first two coincide while the checkout is at the released commit.
