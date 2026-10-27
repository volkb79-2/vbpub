# tester-unified consumers

## Running a project gate

From a vbpub checkout or linked worktree, keep the gate command after `--` so
its argv is passed without a second shell parse:

Build the Run-Gate wheel from the checkout, then use the supported image
builder (see the [image build steps](../README.md#tester-unified)). The builder
passes the exact wheel bytes through a named BuildKit context, checks its
version and digest, and installs that wheel into the image. It requires
`$CGROUP_PARENT_DEV_GATES` or `--cgroup-parent`, verifies the host slice through
CMRU's `tester-gate` probe, passes the same parent to Buildx, and checks the
loaded image labels against the wheel's version and SHA-256. With an explicit
`--base-image`, the unprivileged preflight uses that image, so the first tester
image does not depend on an existing `tester-unified:local` image. The
privileged host-systemd probe remains digest-pinned and pull-disabled.
CMRU release steps inject the required tester settings; a direct shell build
must export `CMRU_TESTER_MEMORY`, `CMRU_TESTER_MEMORY_SWAP`,
`CMRU_TESTER_CPUS`, `CMRU_TESTER_PIDS_LIMIT`, and
`CMRU_TESTER_CGROUP_PROBE_IMAGE` from `cmru.orchestration.toml`. The probe
image must already be available locally.

The default base is the local `tester-base:local` (`docker buildx build --load -t tester-base:local
tester-base/`, public upstream inputs only; a missing base fails the build). For a zero-release
bootstrap you may instead choose the upstream Python base explicitly, so building the gate image does
not require any published vbpub image. Bootstrap CMRU
and cli-extended from this checkout first, then pass the digest that command
prints for its source-built cli-extended wheel:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+bootstrap.source \
  CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh
cp libraries/cli-extended/dist/cli_extended-*.whl tester-unified/local-wheel/
CLI_EXTENDED_SHA256='<source-wheel sha256 printed by the bootstrap command>'
tester-unified/build-image.sh \
  --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
  --cli-extended-resolve local \
  --cli-extended-wheel-sha256 "$CLI_EXTENDED_SHA256" \
  --wheel-dir run-gate-project/dist \
  --tag tester-unified:bootstrap
```

Every image carries an `org.vbpub.cli-extended.wheel-sha256` label naming the exact
`cli-extended` wheel it installed, and `build-image.sh` fails if that label differs from the
expected digest. Its `org.vbpub.tester-unified.base-image` label names the base the image
was built FROM, and the builder likewise fails unless it equals `--base-image` (or the
Dockerfile's default base when that flag is omitted):

```bash
docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified.base-image"}}' \
  tester-unified:bootstrap
```

After a real `cli-extended` release, rebuild with
`--cli-extended-resolve latest --cli-extended-wheel-sha256 "$PUBLISHED_SHA256" --no-cache`
(`latest` is refused without the published digest) or with `pinned` once the Dockerfile's
pinned defaults name the released asset.

Before running a release gate, point Run-Gate at the immutable image ID:

```bash
RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:bootstrap)"
export RUN_GATE_TESTER_UNIFIED_IMAGE
```

```bash
tester-unified/run --workdir run-gate-project -- run-gate selftest
tester-unified/run --workdir run-gate-project -- run-gate --base BASE_SHA assay-r1
```

### Invoking Run-Gate from a CMRU step

When CMRU has already started this image for a declared workflow step, invoke
the installed console script directly:

```console
$ run-gate enroll
```

The image installs the exact Run-Gate wheel into `/opt/tester-venv`; that venv
is first on `PATH`. The selected checkout provides `run-gate.toml` and the
worktree being judged. This console command is available to the non-root tester
user and does not depend on a script path inside the project's directory.

Assay's offline wheel/self-hosting phases can run through the same launcher:

```bash
tester-unified/run --network none --workdir "$WORKTREE/assay" -- \
  bash "$WORKTREE/assay/tools/tester-unified-gate.sh" --inner "$WORKTREE"
```

`WORKTREE` is the absolute, committed checkout to judge. The `--inner` driver
builds its exact-OID wheel and temporary environments within this container;
it launches no second outer gate container. Preserve the resulting launcher
directory and consume it with `assay analyze launcher DIRECTORY
--expected-commit HEAD` or as a named input to `assay analyze receipt`.
`--network none` is the only explicit network selection, is verified against
Docker inspect and recorded in `launch.txt`. See
[why offline gates share the launcher](DESIGN-GUIDE.md#offline-gates).

For a linked worktree, invoke that worktree's launcher or give an absolute
workdir. The launcher derives the enclosing workspace bind and records the
exact commit, temp root, and container facts it used.

The image must be rebuilt from the same checkout after changing Assay's
`[build-system]` requirements. Internal vbpub lanes leave `assay_command` and
`pins` out of `run-gate.toml`; run-gate installs the selected worktree's
`assay/` source into the image's writable `/opt/tester-venv` and the verdict
records the runtime version and source commit.

`tester-unified:local` is a mutable tag shared across worktrees, so a changed
`run-gate-project/` checkout can otherwise be judged by an older wheel. From
the repository root, build the wheel from the selected checkout and rebuild
the image using the supported procedure in the
[tester-unified README](../README.md). CIU's registered lane compares installed
module bytes and console-entry-point targets with the selected checkout and
refuses a stale image.

Alternatively, add a source-match preflight to the consumer's gate. CIU runs
that preflight before its suite and enrollment commands; the failure message
gives the exact rebuild command. If concurrent gates need different Run-Gate
revisions, use distinct image tags; gates using the shared
`tester-unified:local` tag must serialize image rebuilds.

To place evidence somewhere else, use an explicit path:

```bash
tester-unified/run --evidence-dir /workspaces/vbpub/.assay/my-gate \
  --workdir run-gate-project -- run-gate doctor
```

Do not invoke pytest in the cockpit and do not reproduce the launch with a
hand-written `docker run`: the launcher owns the detached `--init` reaper as
well as the cgroup, identity, mounts, and wait/log collection. If the launcher
refuses pressure, cgroup, mount,
socket, image, or concurrency preflight, correct that fact and retry; the
refusal is not a product pass or failure.

See [what the launcher guarantees](../README.md) and
[why those guarantees are required](DESIGN-GUIDE.md#cockpit-and-gate-boundary).

## Rebuilding the gate image against published tools (release waves)

Run this after a Run-Gate release (and after the tester-base release, so the base is a registry reference)
and before the next gated release of any other tool. Why the order matters: CMRU's release gate compares the
host's installed Run-Gate judge with the published release and requires the selected image's
`org.vbpub.run-gate.version` / `.wheel-sha256` labels to be that same release (see the
[design guide](DESIGN-GUIDE.md#the-image-that-gates-a-release-published-inputs)); an image built from a
developer `run-gate-project/dist` wheel can never pass it.

1. Preconditions: the Run-Gate release is published (`gh release view run-gate-v<X.Y.Z>` lists the wheel and its
   `.sha256`), the host's Run-Gate judge is that version (`run-gate --version`), and the tester-base release is
   published. After the first tester-base release replace the three base pin sites with its digest reference
   (`ghcr.io/volkb79-2/tester-base@sha256:<digest>`, as reported by `cmru`): the `ARG TESTER_UNIFIED_BASE_IMAGE=`
   default in `tester-unified/Dockerfile`, the `ARG WHEEL_BUILDER_BASE_IMAGE=` default in
   `wheel-builder/Dockerfile` and `[environments.host].image` in `run-gate.root.toml`; run
   `cmru/tests/test_tester_unified_image.py` and `tester-base/tests/test_docs_in_sync.py` and commit that on its own.
2. Export the supported builder's environment (the values are those of `cmru.orchestration.toml`):
   `BUILDX_BUILDER=default` (only the docker-driver builder sees a local base; not needed for a registry base),
   `CGROUP_PARENT_DEV_GATES`, `CMRU_TESTER_MEMORY`, `CMRU_TESTER_MEMORY_SWAP`, `CMRU_TESTER_CPUS`,
   `CMRU_TESTER_PIDS_LIMIT`, `CMRU_TESTER_CGROUP_PROBE_IMAGE`, `CMRU_TESTER_CGROUP_PARENT`, and
   `CMRU_TESTER_UNIFIED_IMAGE` naming an image that still exists (the preflight probes in it).
3. Build to a branch-specific tag, never the shared one, from a clean checkout of the merged tools:
   `tester-unified/build-image.sh --run-gate-published latest --no-cache --tag tester-unified:wave-<date>`
   (about 4 minutes). It fetches the published wheel, verifies pointer, sidecar and bytes, builds CMRU from the
   checked-out source, resolves every third-party package at least `PIP_MIN_RELEASE_AGE` (14 days) old, and
   re-reads the identity labels. `wheel-builder` follows the same rule:
   `docker buildx build --load --no-cache --cgroup-parent "$CGROUP_PARENT_DEV_GATES" -f wheel-builder/Dockerfile -t wheel-builder:wave-<date> .`
4. Verify before promoting: the labels equal the published release
   (`docker image inspect --format '{{index .Config.Labels "org.vbpub.run-gate.version"}}' tester-unified:wave-<date>`,
   and the sha256 label against the release's `.sha256` file), then run a project's registered gate against the
   exact candidate with `RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:wave-<date> run-gate <lane>` (the cmru
   `coverage` lane exercises the image-contract tests and the whole suite).
5. Promote: keep the rollback (`docker tag tester-unified:local tester-unified:prev-<date>`), then
   `docker tag tester-unified:wave-<date> tester-unified:local`. Remove nothing by pattern; use exact tag names.
6. Re-run step 3 to 5 whenever the host's Run-Gate judge changes version, and when a build must pick up newer
   aged third-party releases (`--no-cache` is what re-resolves them).
