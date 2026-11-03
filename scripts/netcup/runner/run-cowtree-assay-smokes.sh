#!/usr/bin/env bash
# Description: Run the cowtree Assay lane and focused Assay workspace tests in tester-unified with real runner CoW storage.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
assert_target() { identity_check; }
assert_target
repo="$(readlink -f "$RUNNER_HOME/src/vbpub-current")"
test -d "$repo/.git" || test -f "$repo/.git"
git -C "$repo" rev-parse --verify HEAD >/dev/null
require_gates_slice
docker_image=tester-unified:local
container=runner-cowtree-assay-smokes
container_command=$(cat <<'CONTAINER'
set -Eeuo pipefail
if ! getent group "$RUNNER_UID" >/dev/null; then
  groupadd --gid "$RUNNER_UID" runner
fi
if ! getent passwd "$RUNNER_UID" >/dev/null; then
  printf 'runner:x:%s:%s:runner:/home/runner:/bin/bash\n' "$RUNNER_UID" "$RUNNER_UID" >> /etc/passwd
fi
install -d -m 0700 -o "$RUNNER_UID" -g "$RUNNER_UID" /home/runner /home/runner/.cache /home/runner/.config /home/runner/.local/state
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0='*'
run_as_runner() {
  setpriv --reuid="$RUNNER_UID" --regid="$RUNNER_UID" --clear-groups env \
    HOME=/home/runner \
    XDG_CACHE_HOME=/home/runner/.cache \
    XDG_CONFIG_HOME=/home/runner/.config \
    XDG_STATE_HOME=/home/runner/.local/state \
    PATH=/opt/tester-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    "$@"
}
run_as_runner git config --global safe.directory '*'
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0='*'
git config --global --add safe.directory '*'
/opt/tester-venv/bin/pip install --no-deps --editable /workspaces/vbpub/assay
if test -e /workspaces/vbpub/assay/src/assay.egg-info; then
  chown -R "$RUNNER_UID:$RUNNER_UID" /workspaces/vbpub/assay/src/assay.egg-info
fi
run_as_runner mkdir -p /workspaces/vbpub/libraries/cowtree/.assay
chown -R "$RUNNER_UID:$RUNNER_UID" /workspaces/vbpub/libraries/cowtree/.assay
chmod 0700 /workspaces/vbpub/libraries/cowtree/.assay
failures=0
run_case() {
  name=$1
  shift
  printf '\n===== CASE %s =====\n' "$name"
  set +e
  "$@"
  rc=$?
  set -e
  printf 'CASE_EXIT %s=%s\n' "$name" "$rc"
  if (( rc != 0 )); then failures=$((failures + 1)); fi
}
cowtree_pytest_live() {
  run_as_runner env COWTREE_LIVE_ROOT="$COW_ROOT" PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
    /opt/tester-venv/bin/python3 -m pytest tests -q \
    --cov=cowtree --cov-branch --cov-report=json:/tmp/cowtree-coverage-runner.json \
    --cov-fail-under=100
}
assay_focused_without_cow_root() {
  run_as_runner env -u MDT_COW_ROOT \
    /opt/tester-venv/bin/python3 -m pytest \
    /workspaces/vbpub/assay/tests/core/test_workspace.py \
    /workspaces/vbpub/assay/tests/core/test_cow_workspace.py \
    -q -p no:cacheprovider
}
assay_focused_with_cow_root() {
  run_as_runner env MDT_COW_ROOT="$COW_ROOT" \
    /opt/tester-venv/bin/python3 -m pytest \
    /workspaces/vbpub/assay/tests/core/test_workspace.py \
    /workspaces/vbpub/assay/tests/core/test_cow_workspace.py \
    -q -p no:cacheprovider
}
cd /workspaces/vbpub/libraries/cowtree
findmnt -T /workspaces/vbpub/libraries/cowtree/src/cowtree -o FSTYPE,SOURCE
run_case cowtree-pytest-live cowtree_pytest_live
run_case assay-workspace-without-MDT_COW_ROOT assay_focused_without_cow_root
run_case assay-workspace-with-MDT_COW_ROOT assay_focused_with_cow_root
printf 'CASE_SUMMARY failures=%s\n' "$failures"
if (( failures != 0 )); then exit 1; fi
CONTAINER
)
assert_target
docker run -d --name "$container" --init \
  --cgroup-parent "$GATES_SLICE" \
  --cpus 3 --memory 2g --memory-swap 4g --pids-limit 4096 \
  --user 0:0 \
  --mount "type=bind,source=$repo,target=$repo" \
  --mount "type=bind,source=$repo,target=/workspaces/vbpub" \
  --mount "type=bind,source=$COW_ROOT,target=$COW_ROOT" \
  --env CGROUP_PARENT_DEV_GATES="$GATES_SLICE" --env RUNNER_UID="$RUNNER_UID" --env COW_ROOT="$COW_ROOT" \
  "$docker_image" bash -c "$container_command"
cleanup_container() {
  assert_target
  if docker container inspect "$container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null
  fi
}
trap cleanup_container EXIT
container_exit="$(docker wait "$container")"
docker logs "$container"
printf 'CONTAINER_EXIT=%s\n' "$container_exit"
assert_target
docker rm "$container" >/dev/null
trap - EXIT
[[ "$container_exit" =~ ^[0-9]+$ ]] || { echo 'docker wait returned a non-numeric exit status' >&2; exit 2; }
exit "$container_exit"
