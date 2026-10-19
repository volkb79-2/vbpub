# tester-base — consumers guide (HOW)

## Bootstrap order (nothing published yet)
```bash
# 1. the base - public upstream inputs only (cmru/build-initial-standalone.sh does the same and adds the
#    source-hash label that lets it reuse the tag safely)
docker buildx build --load -t tester-base:local tester-base/
# 2. the gate image on top of it, with the supported builder: it supplies the Run-Gate wheel context
#    and the base's resolved identity (tester-unified's own README has the full flow). In the ZERO state
#    there is no tester-unified:local yet, so the base is passed explicitly: cmru's tester-gate preflight
#    then probes in tester-base itself. Prerequisites: cmru installed (step 1 of docs/BOOTSTRAP-FROM-ZERO.md),
#    the Run-Gate wheel built in run-gate-project/dist, and the CMRU_TESTER_* variables of that guide exported.
BUILDX_BUILDER=default tester-unified/build-image.sh --base-image tester-base:local \
  --wheel-dir run-gate-project/dist --tag tester-unified:local
#    wheel-builder shares the base: cmru/build-initial-standalone.sh builds tester-base:local first
#    when it is absent and then wheel-builder:local FROM it
# 3. gates / cmru work
TESTER_BASE_IMAGE=tester-base:local CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES \
  (cd tester-base && run-gate contract)
# 4. cmru releases tester-base (controller)
```
**Builder note.** `FROM tester-base:local` resolves the base in the *local* image store, which only
the `docker` driver builder (`BUILDX_BUILDER=default`) can see; a remote builder such as this
estate's `mdt-managed` fails to resolve it (measured: the base would be pulled from a registry
instead, and a local-only tag does not exist there). That is why step 2 selects the default builder,
and why `--load` is needed on a non-`docker` builder in step 1. After the release the base is a
registry reference and any builder works.

After the first release the controller replaces the three pin sites with the published digest:
the `ARG TESTER_UNIFIED_BASE_IMAGE=` default in `tester-unified/Dockerfile`, the
`ARG WHEEL_BUILDER_BASE_IMAGE=` default in `wheel-builder/Dockerfile`, and
`[environments.host].image` in `run-gate.root.toml`
(`ghcr.io/volkb79-2/tester-base@sha256:<digest>` as reported by cmru).

## Using it as a base
```dockerfile
ARG TESTER_UNIFIED_BASE_IMAGE=tester-base:local
FROM ${TESTER_UNIFIED_BASE_IMAGE}
USER root
# add your venv and your uid-1003 identity here; the base creates none
```

## The contract lane
```bash
TESTER_BASE_IMAGE=<ref> CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES run-gate contract
```
There is no default image; an unset `TESTER_BASE_IMAGE` is a configuration error. Every container
is placed in the gate tier, offline, `--init`, uniquely named and removed by exact name.

## Build arguments (the pins, and how they change)
Every pin is a reviewed edit of the Dockerfile, never a build-time surprise:

| Build arg | Pins | Bump by |
|---|---|---|
| `DOCKER_APT_PRIMARY_FPR` | the only primary key the Docker apt key file may hold (the SET of primary fingerprints must equal exactly this) | a deliberate key rotation |
| `DOCKER_CE_CLI_VERSION` | the exact `docker-ce-cli` package version (the contract lane asserts the installed version equals it) | editing the line to a newer version Docker's repository serves |
| `SHELLCHECK_VERSION`, `SHELLCHECK_SHA256_X86_64`, `SHELLCHECK_SHA256_AARCH64` | the shellcheck release and the sha256 of each architecture's upstream asset | the three together |
| `TESTER_BASE_SOURCE_HASH` | not a pin: the sha256 of `tester-base/Dockerfile`, written to the `org.vbpub.tester-base.source-hash` label (next to `org.vbpub.tester-base.kind=tester-base`) by `cmru/build-initial-standalone.sh` so a local `tester-base:local` is reused only when it matches the current source, rebuilt when stale, refused when it is not a tester-base build; a plain `docker build` leaves it empty, which reads as stale | the bootstrap script supplies it |

The `FROM` digest is the one `cmru versions resolve tester-base` records under the 14-day age
window (see the DESIGN-GUIDE "Version pins").

## Releasing
`cmru release` runs the steps in `cmru.toml`: build once, test that image, then (after cmru wrote the
new `VERSION`) tag and push it. Nobody types a ref, a version or a digest, and the push step needs
`GITHUB_USERNAME` and `GITHUB_PUSH_PAT` in its environment (cmru's `required_env`); without them it
refuses before any registry call. See the DESIGN-GUIDE "One build, one image" for the order and for what a
failed push leaves behind.

### Cleaning up abandoned release candidates
Each release attempt builds `tester-base:release-<source-commit>`; abandoned retries leave their tags
behind. List them and remove each one by its exact tag (never by a filter):

```bash
docker image ls --format '{{.Repository}}:{{.Tag}}' tester-base | grep '^tester-base:release-'
docker rmi tester-base:release-4b233baa4cfb        # one exact tag per command
```

## When a gate needs another tool
First ask which side owns it. A real low-level OS binary: mark the test `os_integration` and run
it in the VM lane. A userland package a registered gate lane executes: add it to the Dockerfile
with the failing lane's evidence, update the contract lane and the DESIGN-GUIDE "Rejected" table.
