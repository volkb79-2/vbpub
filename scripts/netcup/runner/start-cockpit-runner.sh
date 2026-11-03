#!/usr/bin/env bash
# Description: create the cockpit container from the host-folder devcontainer.json (no devcontainer CLI on the host).
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check
: "${COCKPIT_NAME:?}" "${COCKPIT_CONFIG_DIR:?}" "${COCKPIT_HOME_DIR:?}" "${COCKPIT_TMP_DIR:?}" "${COCKPIT_IMAGE:?}" "${INTERACTIVE_SLICE:?}" "${BACKGROUND_SLICE:?}"
cfg=$COCKPIT_CONFIG_DIR
home=$COCKPIT_HOME_DIR
tmpd=$COCKPIT_TMP_DIR
docker rm -f "$COCKPIT_NAME" >/dev/null 2>&1 || true
docker run -d --init --user 0:0 --name "$COCKPIT_NAME" --hostname "$COCKPIT_NAME" \
  --group-add "$(getent group docker | cut -d: -f3)" --cap-add SYS_ADMIN --device /dev/fuse \
  --security-opt apparmor=unconfined --security-opt seccomp=unconfined \
  --cgroup-parent="$INTERACTIVE_SLICE" \
  --mount type=bind,source="$RUNNER_HOME/checkouts",target=/workspaces,consistency=cached \
  --mount type=bind,source=/run/docker-api,target=/run/docker-api \
  --mount type=bind,source=/run/cgprofile,target=/run/cgprofile \
  --mount type=bind,source=/run/mdt-buildkitd,target=/run/mdt-buildkitd \
  --mount type=bind,source=/sys/kernel/debug,target=/sys/kernel/debug,readonly \
  --mount type=bind,source="$RUNNER_HOME/.ssh",target=/run/mdt/host-ssh,readonly \
  --mount type=bind,source="$home",target=/home/vscode \
  --mount type=bind,source="$tmpd",target=/tmp \
  --mount type=bind,source="$cfg/.devcontainer",target=/run/mdt/devcontainer,readonly \
  --mount type=bind,source="$COW_ROOT",target="$COW_ROOT",bind-propagation=rslave \
  -e DOCKER_HOST=unix:///run/docker-api/docker.sock \
  -e CGROUP_PARENT_DEV_INTERACTIVE="$INTERACTIVE_SLICE" -e CGROUP_PARENT_DEV_BACKGROUND="$BACKGROUND_SLICE" \
  -e CGROUP_PARENT_DEV_GATES="$GATES_SLICE" -e MDT_DEVCONTAINER_DIR=/run/mdt/devcontainer \
  -e BUILDX_CONFIG=/home/vscode/.config/docker/buildx -e BUILDX_BUILDER=mdt-managed \
  -e BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock -e CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared \
  -e MDT_COW_ROOT="$COW_ROOT" \
  "$COCKPIT_IMAGE" sleep infinity
# Align the cockpit login user with the host runner uid/gid (the devcontainer "updateRemoteUserUID" step the docker-run
# equivalent lacks): checkouts, the CoW root and the tester-unified lane user (uid 1003) must share one owner, or Git and
# Assay refuse the tree as "dubious ownership" (Run-Gate RG-150, mdt-cockpit MDT-052).
runner_uid=$(id -u "$RUNNER_USER"); runner_gid=$(id -g "$RUNNER_USER")
docker exec -u root "$COCKPIT_NAME" bash -c "set -e; groupmod -g $runner_gid vscode; usermod -u $runner_uid -g $runner_gid vscode; test \"\$(id -u vscode)\" = $runner_uid"
docker exec -u vscode -w /workspaces "$COCKPIT_NAME" finalize_container_environment.py
