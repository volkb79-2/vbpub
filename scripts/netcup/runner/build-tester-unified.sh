#!/usr/bin/env bash
# Description: build the published-Run-Gate tester-unified image on the runner in its loaded gate slice.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
repo=$(source_repo)
require_gates_slice
export PATH=$RUNNER_HOME/venvs/tools/bin:$PATH
export CGROUP_PARENT_DEV_GATES=$GATES_SLICE
# The managed rootless BuildKit cannot see locally loaded base images: use the docker driver. The MDT buildx shim
# otherwise rewrites `default` to the managed builder (mdt-cockpit MDT-049), so pass it through explicitly.
export BUILDX_BUILDER=default
export MDT_BUILDX_PASSTHROUGH=1
export CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local
export CMRU_TESTER_MEMORY=1g
export CMRU_TESTER_MEMORY_SWAP=16g
export CMRU_TESTER_CPUS=2.5
export CMRU_TESTER_PIDS_LIMIT=4096
export CMRU_TESTER_CGROUP_PROBE_IMAGE=$CGROUP_PROBE_IMAGE
export CMRU_TESTER_CGROUP_PARENT=$GATES_SLICE
export CMRU_TESTER_CGROUP_FORWARD_GATES_VAR=CGROUP_PARENT_DEV_GATES
docker pull "$CGROUP_PROBE_IMAGE"
cd "$repo"
tester-unified/build-image.sh \
  --base-image tester-base:local \
  --run-gate-published "$RUN_GATE_VERSION" \
  --no-cache \
  --tag tester-unified:local \
  --cgroup-parent "$GATES_SLICE"
test "$(docker image inspect --format '{{index .Config.Labels "org.vbpub.run-gate.version"}}' tester-unified:local)" = "$RUN_GATE_VERSION"
