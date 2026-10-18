# tester-base — consumers guide (HOW)

## Bootstrap order (nothing published yet)
```bash
# 1. the base - public upstream inputs only
docker build -t tester-base:local tester-base/
# 2. the gate image on top of it (repo-root context, tester-unified's own README)
docker build -f tester-unified/Dockerfile --build-arg TESTER_BASE=tester-base:local \
  -t tester-unified:local .
# 3. gates / cmru work
TESTER_BASE_IMAGE=tester-base:local CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES \
  ./tester-base/run-gate.py contract
# 4. cmru releases tester-base (controller)
```
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

## When a gate needs another tool
First ask which side owns it. A real low-level OS binary: mark the test `os_integration` and run
it in the VM lane. A userland package a registered gate lane executes: add it to the Dockerfile
with the failing lane's evidence, update the contract lane and the DESIGN-GUIDE "Rejected" table.
