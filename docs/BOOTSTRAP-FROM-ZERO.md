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
builds and gates, and network access to the upstream Python image, PyPI build
backends, and GitHub. The bootstrap script builds `wheel-builder:local` from
that upstream image automatically when it is absent. Run builds serially.
Before publishing the first Run-Gate stable release, run `run-gate assay-r1`
from `run-gate-project/` on the reviewed source. The first-release selftest can
report diff coverage as `SKIPPED` because no earlier stable tag exists; R1
supplies the whole-source line-and-branch qualification.

1. **Build and install the source bootstrap.**

   ```bash
   SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+bootstrap.source \
     CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh
   ```

   The script builds `cli-extended` at CMRU's declared runtime floor with a
   `+bootstrap.source` suffix, then builds CMRU from this checkout using that
   wheel. The explicit CMRU local version is needed when the checkout has no
   `cmru-v*` tag. It prints exact commands to install both wheels. The wheel SHA-256 is
   printed in the log and is needed for the tester image below. The default
   `release` mode still refuses when no `cli-extended` release exists; source
   mode is an explicit bootstrap choice, never an automatic fallback.

2. **Build the first tester image from source inputs.** Copy the one
   `cli_extended-*.whl` produced above into the git-ignored
   `tester-unified/local-wheel/`. Build the Run-Gate wheel from the selected
   commit using the planned stable version (the example is for the current
   24.0.0 candidate):

   ```bash
   SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=24.0.0 \
     cmru handler wheel-build --cwd run-gate-project
   CLI_EXTENDED_SHA256='<source-wheel sha256 printed by the bootstrap command>'
   tester-unified/build-image.sh \
     --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
     --cli-extended-resolve local \
     --cli-extended-wheel-sha256 "$CLI_EXTENDED_SHA256" \
     --wheel-dir run-gate-project/dist \
     --tag tester-unified:bootstrap
   RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:bootstrap)"
   export RUN_GATE_TESTER_UNIFIED_IMAGE
   RUN_GATE_CANDIDATE_WHEEL="$PWD/run-gate-project/dist/run_gate-24.0.0-py3-none-any.whl"
   test -f "$RUN_GATE_CANDIDATE_WHEEL"
   /home/vscode/.venv/bin/python -m pip install --force-reinstall --no-deps \
     "$RUN_GATE_CANDIDATE_WHEEL"
   ```

   The builder checks the supplied Run-Gate wheel and image labels. The
   Dockerfile explicitly installs the host-facing tools it needs, so the
   upstream Python base does not need to inherit a vbpub or MDT release. The
   host venv install makes this exact local candidate available to the
   registered gates and release steps; it is built from the checkout and does
   not need a published Run-Gate tag.

3. **Release `cli-extended` first.** Its initial stable version must satisfy
   CMRU's declared `cli-extended>=0.3.0` floor:

   ```bash
   cmru release cli-extended --set-version 0.3.0
   ```

   This release is gated with the source-built `cli-extended` wheel above. The
   source suffix distinguishes the bootstrap wheel from the stable artifact.

4. **Release CGProfile and then Run-Gate with the source-bootstrapped CMRU
   controller.** CGProfile uses the candidate Run-Gate wheel and tester image
   from step 2. The graph places Run-Gate after CGProfile because Run-Gate's
   R2 qualification uses CGProfile's observer. For the Run-Gate release, its CMRU gate rebuilds the candidate
   wheel and tester image from the exact release transaction checkout. Set the
   upstream base and explicit source mode so the gate does not try to fetch a
   first-party image or cli-extended release:

   ```bash
   cmru release cgroup-profiler --set-version 1.0.0
   RUN_GATE_TESTER_UNIFIED_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie \
   RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE=source \
     cmru release run-gate-project --set-version 24.0.0
   ```

   The first Run-Gate release publishes the artifact that CMRU's own release
   gate requires. CMRU retains the exact published wheel in the release
   transaction's `artifacts/` output and prints its path. Use that retained
   file to rebuild the image and install the host CLI; use the exact Run-Gate
   wheel, while the now-published cli-extended pointer supplies its released
   runtime wheel:

   ```bash
   RUN_GATE_RELEASE_WHEEL='<exact wheel path printed by cmru>'
   /home/vscode/.venv/bin/python -m pip install --force-reinstall --no-deps "$RUN_GATE_RELEASE_WHEEL"
   tester-unified/build-image.sh \
     --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
     --cli-extended-resolve latest --no-cache \
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
   cmru release cmru --set-version 0.1.0
   cd cmru
   run-gate assay-first-release-r2
   ```

   Run the R2 campaign against the newly released source commit; it declares
   every production module and uses no prior tag. Then return to the repository
   root and release the remaining changed projects in graph order, for example
   `cmru release ciu,assay`. MDT is last because its published image stages the
   first-party wheels from earlier levels. Each normal transaction remains one
   project at a time, with CMRU responsible for tagging, building, publishing,
   and promoting each release.

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
