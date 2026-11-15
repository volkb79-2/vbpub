#!/usr/bin/env bash
# Description: build the repository's gate base image on the runner inside its loaded gate slice.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
repo=$(source_repo)
require_gates_slice
base_hash="$(cd "$repo/tester-base" && sha256sum Dockerfile ksm-optin.c | sha256sum | awk '{print $1}')"
cd "$repo"
docker buildx build --load \
  --cgroup-parent "$GATES_SLICE" \
  --build-arg "TESTER_BASE_SOURCE_HASH=$base_hash" \
  -t tester-base:local tester-base/
test "$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-base.kind"}}' tester-base:local)" = tester-base
test "$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-base.source-hash"}}' tester-base:local)" = "$base_hash"
