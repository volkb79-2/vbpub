# Bootstrapping the estate with no published first-party releases

This procedure starts from a vbpub checkout whose first-party projects have no
GitHub Releases or package artifacts yet. It uses committed source from this
checkout for build and gate tools until each project has a published release.
External inputs such as the Python package index and Microsoft's base
devcontainer image are still required; this is a zero-first-party-release
procedure, not an offline build.

## What the graph means

`cmru dependencies` is the source of truth for releaseable artifact and gate
prerequisites. A project that invokes the CMRU CLI in its build or release
steps does not depend on the *published CMRU wheel*: the controller itself is
bootstrapped from this repository's source. Those tool invocations must not
create release-order edges.

CMRU's own wheel has two real prerequisites. Its runtime requires the
`cli-extended` wheel, and its release gate requires a published stable
Run-Gate wheel plus a tester image containing those exact Run-Gate bytes.
Run-Gate can be built and released with the source-bootstrapped CMRU controller,
so this is a one-way edge rather than a cycle.

The tester image normally uses the published MDT devcontainer. During the
zero-release bootstrap, use the upstream Python devcontainer base explicitly
with `tester-unified/build-image.sh --base-image ...`. The tester image builds
CMRU from the selected checkout, installs the explicitly digest-checked local
`cli-extended` wheel, and installs the exact Run-Gate wheel supplied by the
builder. No published vbpub image is needed.

The checked-in level view must match the graph:

```project-order
L0: cli-extended, cgroup-profiler, ciu, assay, topos, pwmcp, tls-edge
L1: run-gate-project, nyxloom
L2: cmru
L3: modern-debian-tools-python-debug
```

Within a level, `project_order` is the explicit order used by the release
transaction. The graph captures the release chain: Run-Gate follows CGProfile
because its full R2 qualification needs the contract-2 observer, and CMRU
follows Run-Gate because its own gate requires the published wheel. The test
`cmru/tests/test_dependency_levels_pyproject.py` checks both this level table
and the CGProfile → Run-Gate → CMRU gate prerequisites.

## Steps from an empty release set

Prerequisites: Docker/Buildx, a governed background parent for the standalone
wheel build (`CMRU_BOOTSTRAP_CGROUP_PARENT` or
`$CGROUP_PARENT_DEV_BACKGROUND`), `$CGROUP_PARENT_DEV_GATES` for tester-image
builds, gates and the host-slice probe's own placement, the configured
digest-pinned CMRU host-probe image pulled locally, the Python environment you
choose to own the `cmru` command (`ESTATE_PYTHON`, below; in the estate
cockpit `/home/vscode/.venv/bin/python`), and network access to the upstream
Python image, PyPI build backends, and GitHub. Both the bootstrap script and
`cmru handler wheel-build` verify that the build slice is a real host systemd
unit (`LoadState=loaded` with a non-empty `FragmentPath`, read through the
pinned probe container, never a local `systemctl`) **before** any builder
workload starts; a typo'd slice name refuses instead of becoming an unlimited
transient slice. Tester-gate deliberately runs the configured privileged probe
image with `--pull=never`, so pull its exact digest before the first build:

```bash
docker pull debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
```

This digest is also the `CMRU_TESTER_CGROUP_PROBE_IMAGE` value in
`cmru.orchestration.toml`. The bootstrap script builds `wheel-builder:local` from
that upstream image automatically when it is absent. Run builds serially.
Before publishing the first Run-Gate stable release, run `run-gate assay-r1`
from `run-gate-project/` on the reviewed source. The first-release selftest can
report diff coverage as `SKIPPED` because no earlier stable tag exists; R1
supplies the whole-source line-and-branch qualification.

1. **Build and install the source bootstrap.** No `cmru` command exists on a
   fresh host, so this step ends by installing the exact wheels it built:

   ```bash
   SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+bootstrap.source \
     CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh
   . cmru/dist/bootstrap-artifacts.env
   printf '%s  %s\n' \
     "$BOOTSTRAP_CLI_EXTENDED_WHEEL_SHA256" "$BOOTSTRAP_CLI_EXTENDED_WHEEL" \
     "$BOOTSTRAP_CMRU_WHEEL_SHA256" "$BOOTSTRAP_CMRU_WHEEL" | sha256sum -c -
   "${ESTATE_PYTHON:?set ESTATE_PYTHON to the Python that will own the cmru command}" \
     -m pip install --no-index --no-deps --force-reinstall \
     "$BOOTSTRAP_CLI_EXTENDED_WHEEL" "$BOOTSTRAP_CMRU_WHEEL"
   export PATH="$(dirname "$ESTATE_PYTHON"):$PATH"
   cmru --version
   ```

   The script builds `cli-extended` at CMRU's declared runtime floor with a
   `+bootstrap.source` suffix, then builds CMRU from this checkout using that
   wheel. The explicit CMRU local version is needed when the checkout has no
   `cmru-v*` tag. Wheel paths and SHA-256 digests are never typed by hand: the
   script writes the exact files it produced to the sourceable
   `cmru/dist/bootstrap-artifacts.env` (`BOOTSTRAP_CMRU_WHEEL[_SHA256]`,
   `BOOTSTRAP_CLI_EXTENDED_WHEEL[_SHA256]`), and the commands above verify and
   install exactly those files, `cli-extended` first, into the environment you
   selected. The default `release` mode still refuses when no `cli-extended`
   release exists; source mode is an explicit bootstrap choice, never an
   automatic fallback. Before building, the script verifies the build slice on
   the Docker host and checks the configured wheel-builder image's dedicated
   label; a missing or mismatched image is built from `wheel-builder/Dockerfile`
   on Microsoft's upstream Python base. A local MDT image tag cannot be reused
   as the builder by accident.

2. **Build the first tester image from source inputs.** Copy the one
   `cli_extended-*.whl` produced above into the git-ignored
   `tester-unified/local-wheel/`. Build the Run-Gate wheel from the selected
   commit using the planned stable version (the example is for the current
   24.0.0 candidate); its path is derived from what the build produced:

   ```bash
   mkdir -p tester-unified/local-wheel
   cp "$BOOTSTRAP_CLI_EXTENDED_WHEEL" tester-unified/local-wheel/
   SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=24.0.0 \
     cmru handler wheel-build --cwd run-gate-project
   shopt -s nullglob
   run_gate_wheels=("$PWD"/run-gate-project/dist/run_gate-24.0.0-*.whl)
   test "${#run_gate_wheels[@]}" -eq 1
   RUN_GATE_CANDIDATE_WHEEL="${run_gate_wheels[0]}"
   CMRU_TESTER_MEMORY=1g \
   CMRU_TESTER_MEMORY_SWAP=16g \
   CMRU_TESTER_CPUS=2.5 \
   CMRU_TESTER_PIDS_LIMIT=4096 \
   CMRU_TESTER_CGROUP_PROBE_IMAGE='debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132' \
   tester-unified/build-image.sh \
     --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
     --cli-extended-resolve local \
     --cli-extended-wheel-sha256 "$BOOTSTRAP_CLI_EXTENDED_WHEEL_SHA256" \
     --wheel-dir run-gate-project/dist \
     --tag tester-unified:bootstrap
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:bootstrap)"
   export RUN_GATE_TESTER_UNIFIED_IMAGE
   "$ESTATE_PYTHON" -m pip install --force-reinstall --no-deps "$RUN_GATE_CANDIDATE_WHEEL"
   ```

   The builder checks the supplied Run-Gate wheel and image labels. The
   explicit upstream base is also used for the builder's unprivileged CMRU
   preflight, so this step does not require a pre-existing
   `tester-unified:local` image. The privileged host-systemd slice probe still
   uses its separately configured digest-pinned image. The
   Dockerfile explicitly installs the host-facing tools it needs, so the
   upstream Python base does not need to inherit a vbpub or MDT release. The
   host venv install makes this exact local candidate available to the
   registered gates and release steps; it is built from the checkout and does
   not need a published Run-Gate tag.

3. **Release `cli-extended` first, then rebuild the tester image from the
   released bytes.** Its initial stable version must satisfy CMRU's declared
   `cli-extended>=0.3.0` floor:

   ```bash
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:bootstrap)" \
     cmru release cli-extended --set-version 0.3.0
   ```

   This release is gated with the source-built `cli-extended` wheel above. The
   source suffix distinguishes the bootstrap wheel from the stable artifact.
   The command pins the image built in step 2 even if you run the code blocks
   from different shells.

   The stable wheel is a *different file* from the bootstrap one, and the
   Dockerfile's embedded `pinned` default still names whatever `cli-extended`
   asset was current when it was last edited. Do not build a tester image in
   `pinned` mode now without checking. `cmru release` printed the published
   wheel's identity (`CLI-EXTENDED_WHEEL_SHA256=` and
   `CLI-EXTENDED_WHEEL_ASSET_URL=`); carry that digest into the check below. It
   refuses when the `cli-extended-latest` pointer does not name that same
   digest (it never reads an unreachable or stale pointer as "nothing
   released"), and otherwise prints the resolve mode that is safe for the
   Dockerfile's current pin:

   ```bash
   CLI_EXTENDED_RELEASED_SHA256='<CLI-EXTENDED_WHEEL_SHA256 printed by cmru release>'
   unset CLI_EXTENDED_RESOLVE
   identity="$(python3 cmru/tools/cli_extended_release_identity.py \
     --released-sha256 "$CLI_EXTENDED_RELEASED_SHA256" \
     --dockerfile tester-unified/Dockerfile)"
   eval "$identity"
   echo "Dockerfile pin: $CLI_EXTENDED_DOCKERFILE_PIN; resolve mode: $CLI_EXTENDED_RESOLVE"
   tester-unified/build-image.sh \
     --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
     --cli-extended-resolve "${CLI_EXTENDED_RESOLVE:?the identity check above must succeed}" --no-cache \
     --wheel-dir run-gate-project/dist \
     --tag tester-unified:cli-extended-released
   python3 cmru/tools/cli_extended_release_identity.py \
     --released-sha256 "$CLI_EXTENDED_RELEASED_SHA256" \
     --dockerfile tester-unified/Dockerfile > /dev/null
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:cli-extended-released)"
   export RUN_GATE_TESTER_UNIFIED_IMAGE
   ```

   A stale pin yields `latest`: the image's fetcher downloads the asset the
   pointer names and verifies that asset's own SHA-256 before installing it, and
   the check run again after the build proves the pointer still names the
   digest `cmru release` printed. Once the Dockerfile's three pinned defaults
   are updated to the released URL and digest in a reviewed commit, the same
   check reports `current` and `pinned` is valid again. Use the released-bytes
   image from here on; the bootstrap image carries only the source wheel.

4. **Release CGProfile and then Run-Gate with the source-bootstrapped CMRU
   controller.** CGProfile uses the candidate Run-Gate wheel and the
   released-bytes tester image from step 3. The graph places Run-Gate after
   CGProfile because Run-Gate's R2 qualification uses CGProfile's observer. For
   the Run-Gate release, its CMRU gate rebuilds the candidate wheel and tester
   image from the exact release transaction checkout; pass the resolve mode
   from step 3 through `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE` (a stale pin must
   not make that rebuild reinstall the old bytes), and use the upstream base
   explicitly. Do not switch back to the source bootstrap wheel:

   ```bash
   RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE="${CLI_EXTENDED_RESOLVE:?run the step 3 identity check first}" \
     cmru release cgroup-profiler --set-version 1.0.0
   RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE="${CLI_EXTENDED_RESOLVE:?run the step 3 identity check first}" \
   RUN_GATE_TESTER_UNIFIED_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie \
     cmru release run-gate-project --set-version 24.0.0
   ```

   The first Run-Gate release publishes the artifact that CMRU's own release
   gate requires. CMRU retains the exact published wheel in the release
   transaction's `artifacts/` output and prints its path. Use that retained
   file to rebuild the image and install the host CLI; use the exact Run-Gate
   wheel, while the checked resolve mode keeps the released cli-extended
   runtime wheel:

   ```bash
   RUN_GATE_RELEASE_WHEEL='<exact wheel path printed by cmru>'
   "$ESTATE_PYTHON" -m pip install --force-reinstall --no-deps "$RUN_GATE_RELEASE_WHEEL"
   tester-unified/build-image.sh \
     --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
     --cli-extended-resolve "${CLI_EXTENDED_RESOLVE:?run the step 3 identity check first}" --no-cache \
     --wheel-dir "$(dirname "$RUN_GATE_RELEASE_WHEEL")" \
     --tag tester-unified:run-gate-release
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:run-gate-release)"
   export RUN_GATE_TESTER_UNIFIED_IMAGE
   ```

   The builder verifies and installs the exact retained Run-Gate wheel. Its
   version and digest labels then match the published stable artifact, which
   CMRU checks before running its own lanes.

5. **Release CMRU, then the remaining projects in graph order.** The
   configured `gate-provisional` release gate queries origin with CMRU's
   scoped Git auth. When it finds no published stable CMRU tag, it uses the
   registered whole-source R0/R1/R3 lane instead of requiring a nonexistent
   changed-lines baseline. The gate is allowed to make that first-release
   choice only inside the exact-commit CMRU release transaction. It records
   whole-source R2 as deferred, with an explicit post-release lane:

   ```bash
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:run-gate-release)" \
     cmru release cmru --set-version 0.1.0
   ```

   The post-release R2 follow-up declares every production module and uses no
   prior tag. It also needs the CGProfile host observer to be deployed and
   reachable. After releasing CIU, deploy the released CGProfile image through
   the declared CIU workflow and verify the observer before running
   `run-gate assay-first-release-r2` from `cmru/`. This qualification can stay
   deferred until that deployment exists; do not interpret the provisional
   release gate as an R2 result. Release the remaining projects in graph order;
   MDT is last because its published image stages the first-party wheels from
   earlier levels. Keep each release transaction scoped to one project so
   CMRU can verify the newly published prerequisites before moving to its
   consumers.

## Bootstrap boundaries

- `cli-extended` is a true runtime artifact dependency of CMRU and remains an
  explicit edge in the graph. The local `+bootstrap.source` wheel is only for
  bootstrapping and gating; release the stable wheel before releasing CMRU.
- Run-Gate is a true CMRU release-gate prerequisite and is now an explicit
  graph edge. CMRU releases after Run-Gate.
- CMRU's first stable release uses source-complete whole-target Assay lanes;
  its post-release whole-source R2 lane does not require an earlier CMRU tag.
  Origin access failures never count as an empty first-release baseline.
- CMRU as the command used to run another project's release is source-built
  tooling, not a dependency on the published CMRU wheel.
- The upstream Python base is a named, explicit bootstrap input. Normal builds
  keep using the configured MDT image; a missing MDT image never causes an
  implicit fallback.
- This process still uses network-fetched third-party build backends and the
  upstream Python base image. Offline bootstrap is a separate requirement.
