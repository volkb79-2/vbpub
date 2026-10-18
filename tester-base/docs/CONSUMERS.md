# tester-base — consumers guide (HOW)

## Bootstrap order (nothing published yet)
```bash
# 1. the base - public upstream inputs only
docker buildx build --load -t tester-base:local tester-base/
# 2. the gate image on top of it (repo-root context, tester-unified's own README)
BUILDX_BUILDER=default docker buildx build --load -f tester-unified/Dockerfile \
  --build-arg TESTER_BASE=tester-base:local -t tester-unified:local .
# 3. gates / cmru work
TESTER_BASE_IMAGE=tester-base:local CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES \
  ./tester-base/run-gate.py contract
# 4. cmru releases tester-base (controller)
```
**Builder note.** `FROM tester-base:local` resolves the base in the *local* image store, which only
the `docker` driver builder (`BUILDX_BUILDER=default`) can see; a remote builder such as this
estate's `mdt-managed` fails to resolve it (measured: the base would be pulled from a registry
instead, and a local-only tag does not exist there). That is why step 2 selects the default builder,
and why `--load` is needed on a non-`docker` builder in step 1. After the release the base is a
registry reference and any builder works.

After the first release the controller replaces the three pin sites with the published digest:
the `ARG TESTER_BASE=` defaults in `tester-unified/Dockerfile` and `wheel-builder/Dockerfile`, and
`[environments.host].image` in `run-gate.root.toml`
(`ghcr.io/volkb79-2/tester-base@sha256:<digest>` as reported by cmru).

## Using it as a base
```dockerfile
ARG TESTER_BASE=tester-base:local
FROM ${TESTER_BASE}
USER root
# add your venv and your uid-1003 identity here; the base creates none
```

## The contract lane
```bash
TESTER_BASE_IMAGE=<ref> CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES ./run-gate.py contract
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

The `FROM` digest is the one `cmru versions resolve tester-base` records under the 14-day age
window (see the DESIGN-GUIDE "Version pins").

## Releasing
`cmru release` builds the image ONCE, tests that image, and pushes it: see the DESIGN-GUIDE "One build,
one image". Nobody types a ref or a digest.

## When a gate needs another tool
First ask which side owns it. A real low-level OS binary: mark the test `os_integration` and run
it in the VM lane. A userland package a registered gate lane executes: add it to the Dockerfile
with the failing lane's evidence, update the contract lane and the DESIGN-GUIDE "Rejected" table.
