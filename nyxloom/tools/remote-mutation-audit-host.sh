#!/usr/bin/env bash
# Commit-addressed host launcher for remote_mutation_audit.py.
set -euo pipefail

usage() { echo "usage: $0 --manifest-rel PATH [--repo URL] [--ref origin/main] [--reports-root DIR] [--tester-image REF@sha256:DIGEST] [--allow-infra] [--allow-docker-socket] [--max-mutants N] [--start-at N] [--shard-index N --shard-count N] [--cgroup-parent NAME] [--zfs-allowed-prefix DATASET] [--zfs-sudo]" >&2; exit 2; }
manifest_rel=""; repo=""; ref="origin/main"; reports_root="${MUTATION_AUDIT_REPORT_ROOT:-$PWD/mutation-audit-reports}"
allow_infra=""; allow_docker_socket=""; max_mutants=""; start_at=""; shard_index=""; shard_count=""; cgroup_parent="${MUTATION_AUDIT_CGROUP_PARENT:-}"
zfs_allowed_prefix=""; zfs_sudo=""; tester_image=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest-rel) manifest_rel=${2:?}; shift 2 ;;
    --repo) repo=${2:?}; shift 2 ;;
    --ref) ref=${2:?}; shift 2 ;;
    --reports-root) reports_root=${2:?}; shift 2 ;;
    --tester-image) tester_image=${2:?}; shift 2 ;;
    --allow-infra) allow_infra=1; shift ;;
    --allow-docker-socket) allow_docker_socket=1; shift ;;
    --max-mutants) max_mutants=${2:?}; shift 2 ;;
    --start-at) start_at=${2:?}; shift 2 ;;
    --shard-index) shard_index=${2:?}; shift 2 ;;
    --shard-count) shard_count=${2:?}; shift 2 ;;
    --cgroup-parent) cgroup_parent=${2:?}; shift 2 ;;
    --zfs-allowed-prefix) zfs_allowed_prefix=${2:?}; shift 2 ;;
    --zfs-sudo) zfs_sudo=1; shift ;;
    *) usage ;;
  esac
done
[[ -n "$manifest_rel" ]] || usage
[[ "$manifest_rel" != /* && "/$manifest_rel/" != *"/../"* && "$manifest_rel" != *$'\n'* ]] || { echo "manifest path must stay beneath the checkout" >&2; exit 2; }
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { echo "unsafe --ref" >&2; exit 2; }
[[ -z "$allow_docker_socket" || -n "$allow_infra" ]] || { echo "--allow-docker-socket requires --allow-infra" >&2; exit 2; }
[[ -n "$repo" ]] || repo=$(git config --get remote.origin.url) || { echo "no --repo and no origin remote" >&2; exit 2; }

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
checkout=$(mktemp -d "${TMPDIR:-/tmp}/nyxloom-mutation-audit.XXXXXX")
# shellcheck disable=SC2329  # invoked indirectly by trap
cleanup() {
  local rc=$?
  [[ -n "${relay_container:-}" ]] && docker rm -f "$relay_container" >/dev/null 2>&1 || true
  [[ -n "${audit_container:-}" ]] && docker rm -f "$audit_container" >/dev/null 2>&1 || true
  [[ -n "${stage_container:-}" ]] && docker rm -f "$stage_container" >/dev/null 2>&1 || true
  if [[ -n "${broker_pid:-}" ]] && kill -0 "$broker_pid" >/dev/null 2>&1; then
    kill "$broker_pid" >/dev/null 2>&1 || true
    wait "$broker_pid" >/dev/null 2>&1 || true
  fi
  [[ -n "${report_volume:-}" ]] && docker volume rm "$report_volume" >/dev/null 2>&1 || true
  [[ -n "${source_volume:-}" ]] && docker volume rm "$source_volume" >/dev/null 2>&1 || true
  [[ -n "${owned_image_tag:-}" ]] && docker image rm "$owned_image_tag" >/dev/null 2>&1 || true
  [[ -d "$checkout" ]] && git -C "$checkout" worktree prune >/dev/null 2>&1 || true
  [[ -d "$checkout" ]] && find "$checkout" -depth -delete >/dev/null 2>&1 || true
  if [[ -n "${broker_dir:-}" && -d "$broker_dir" && "$broker_dir" == "${TMPDIR:-/tmp}/nyxloom-zfs-broker."* ]]; then
    find "$broker_dir" -depth -delete >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

git clone --no-checkout -- "$repo" "$checkout"
# Operators naturally say origin/main for their local tracking ref, while a
# remote server exposes that branch as main.  Fetch the latter but record the
# exact FETCH_HEAD SHA, never the moving branch label.
fetch_ref=${ref#origin/}
git -C "$checkout" fetch --no-tags origin "$fetch_ref"
sha=$(git -C "$checkout" rev-parse FETCH_HEAD)
git -C "$checkout" checkout --detach "$sha"
[[ -f "$checkout/$manifest_rel" ]] || { echo "manifest missing at audited commit: $manifest_rel" >&2; exit 2; }
manifest_values=$(python3 -c 'import re,sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb")); p=str(d.get("audit",{}).get("project","")); z=d.get("zfs",{}); re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",p) or sys.exit("unsafe [audit].project"); print(p); print("1" if z.get("enabled") is True else "0"); print(str(z.get("container_mount","")))' "$checkout/$manifest_rel")
mapfile -t manifest_fields <<< "$manifest_values"
project=${manifest_fields[0]:-}
zfs_enabled=${manifest_fields[1]:-0}
zfs_container_mount=${manifest_fields[2]:-}
if [[ "$zfs_enabled" == 1 ]]; then
  [[ -n "$allow_infra" ]] || { echo "ZFS lifecycle requires --allow-infra" >&2; exit 2; }
  [[ -n "$zfs_allowed_prefix" ]] || { echo "ZFS lifecycle requires host-authorized --zfs-allowed-prefix" >&2; exit 2; }
  [[ "$zfs_container_mount" == /* && "$zfs_container_mount" != / && "/$zfs_container_mount/" != *"/../"* ]] || { echo "[zfs].container_mount must be a non-root absolute path" >&2; exit 2; }
elif [[ -n "$zfs_allowed_prefix" || -n "$zfs_sudo" ]]; then
  echo "ZFS launcher options require [zfs].enabled=true" >&2
  exit 2
fi
report_dir="$reports_root/$project/$sha/$run_id"
mkdir -p "$report_dir"
volume_suffix=$(printf '%s' "$run_id" | tr -cd '[:alnum:]')
if [[ -n "$tester_image" ]]; then
  [[ "$tester_image" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "--tester-image must be content-addressed with @sha256:DIGEST" >&2; exit 2; }
  docker image inspect "$tester_image" >/dev/null 2>&1 || docker pull "$tester_image"
  image_tag="$tester_image"
  image_reference="$tester_image"
else
  owned_image_tag="tester-unified:mutation-audit-${sha:0:12}-${volume_suffix}"
  image_tag="$owned_image_tag"
  image_reference="locally-built:$owned_image_tag"
  docker build -f "$checkout/tester-unified/Dockerfile" -t "$image_tag" "$checkout"
fi
image_id=$(docker image inspect --format '{{.Id}}' "$image_tag")
manifest_sha256=$(sha256sum "$checkout/$manifest_rel" | awk '{print $1}')

python3 -c 'import json,sys; json.dump({"project":sys.argv[1],"commit":sys.argv[2],"image_id":sys.argv[3],"image_reference":sys.argv[4],"manifest_sha256":sys.argv[5],"ref":sys.argv[6],"started_at":sys.argv[7]},open(sys.argv[8],"w"),indent=2,sort_keys=True); open(sys.argv[8],"a").write("\n")' \
  "$project" "$sha" "$image_id" "$image_reference" "$manifest_sha256" "$ref" "$started_at" "$report_dir/host.json"

if [[ "$zfs_enabled" == 1 ]]; then
  broker_dir=$(mktemp -d "${TMPDIR:-/tmp}/nyxloom-zfs-broker.XXXXXX")
  chmod 0755 "$broker_dir"
  broker_socket="$broker_dir/zfs.sock"
  broker_ready="$broker_dir/ready.json"
  zfs_broker_token=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  snapshot_name="nyxloom-${volume_suffix}"
  broker_script=$(cd "$(dirname "$0")" && pwd)/remote_mutation_zfs_broker.py
  broker_args=(python3 "$broker_script" \
    --manifest "$checkout/$manifest_rel" --socket "$broker_socket" --ready "$broker_ready" \
    --events "$report_dir/zfs-events.jsonl" --snapshot-name "$snapshot_name" \
    --allowed-prefix "$zfs_allowed_prefix")
  [[ -n "$zfs_sudo" ]] && broker_args+=(--sudo)
  NYXLOOM_ZFS_BROKER_TOKEN="$zfs_broker_token" "${broker_args[@]}" &
  broker_pid=$!
  for _ in $(seq 1 100); do
    [[ -f "$broker_ready" ]] && break
    kill -0 "$broker_pid" >/dev/null 2>&1 || { wait "$broker_pid"; exit 2; }
    sleep 0.1
  done
  [[ -f "$broker_ready" ]] || { echo "ZFS broker did not become ready" >&2; exit 2; }
  zfs_mountpoint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mountpoint"])' "$broker_ready")
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); json.dump(data,open(sys.argv[2],"w"),indent=2,sort_keys=True); open(sys.argv[2],"a").write("\n")' \
    "$broker_ready" "$report_dir/zfs.json"
fi

# Do not bind the caller's paths into Docker.  A remote host's Docker daemon
# often cannot see a devcontainer's /workspaces or /tmp mount namespace.  Copy
# the exact detached checkout into a private named volume, run there, then copy
# the immutable report volume back to the caller.  This also avoids the base
# tester image's /audit tmpfs mount.
source_volume="nyxloom-mutation-src-${volume_suffix}"
report_volume="nyxloom-mutation-report-${volume_suffix}"
docker volume create --label nyxloom.role=mutation-audit --label nyxloom.run="$run_id" "$source_volume" >/dev/null
docker volume create --label nyxloom.role=mutation-audit --label nyxloom.run="$run_id" "$report_volume" >/dev/null
stage_container=$(docker create -v "$source_volume:/remote-audit" "$image_tag" /bin/true)
docker cp "$checkout/." "$stage_container:/remote-audit"
docker rm "$stage_container" >/dev/null
stage_container=""
# Docker creates named volumes as root.  The tester image deliberately runs as
# its unprivileged test uid, which needs to make disposable worktrees and write
# reports; fix ownership before the audit starts.
docker run --rm --user 0 -v "$source_volume:/remote-audit" -v "$report_volume:/reports" "$image_tag" /bin/sh -lc \
  'chown -R 1003:994 /remote-audit && chown 1003:994 /reports'

docker_args=(docker create --label nyxloom.role=mutation-audit --label nyxloom.run="$run_id" -v "$source_volume:/remote-audit" -v "$report_volume:/reports" -w /remote-audit)
[[ -n "$cgroup_parent" ]] && docker_args+=(--cgroup-parent="$cgroup_parent")
if [[ -n "$allow_docker_socket" ]]; then
  docker_args+=(-v /var/run/docker.sock:/var/run/docker.sock)
fi
if [[ "$zfs_enabled" == 1 ]]; then
  docker run --rm -v "$broker_dir:/host-lifecycle:ro" "$image_tag" /bin/sh -lc 'test -S /host-lifecycle/zfs.sock' || { echo "ZFS mode must run directly on the Docker host so its broker socket is bind-visible" >&2; exit 2; }
  docker_args+=(-v "$broker_dir:/host-lifecycle:ro" -v "$zfs_mountpoint:$zfs_container_mount")
  docker_args+=(-e "NYXLOOM_ZFS_BROKER_TOKEN=$zfs_broker_token")
fi
docker_args+=(-e "NYXLOOM_AUDIT_IMAGE_ID=$image_id" -e "NYXLOOM_AUDIT_IMAGE_REFERENCE=$image_reference" -e "NYXLOOM_AUDIT_MANIFEST_SHA256=$manifest_sha256" -e "NYXLOOM_AUDIT_WORKER_COMMIT=$sha")
container_args=("${docker_args[@]}" "$image_tag" /opt/tester-venv/bin/python nyxloom/tools/remote_mutation_audit.py --manifest "/remote-audit/$manifest_rel" --report-dir /reports)
[[ -n "$max_mutants" ]] && container_args+=(--max-mutants "$max_mutants")
[[ -n "$start_at" ]] && container_args+=(--start-at "$start_at")
[[ -n "$shard_index" ]] && container_args+=(--shard-index "$shard_index")
[[ -n "$shard_count" ]] && container_args+=(--shard-count "$shard_count")
[[ -n "$allow_infra" ]] && container_args+=(--allow-infra)
[[ "$zfs_enabled" == 1 ]] && container_args+=(--host-lifecycle-socket /host-lifecycle/zfs.sock)
audit_container=$("${container_args[@]}")
if docker start -a "$audit_container"; then
  audit_rc=0
else
  audit_rc=$?
fi
relay_container=$(docker create -v "$report_volume:/reports" "$image_tag" /bin/true)
docker cp "$relay_container:/reports/." "$report_dir"
docker rm "$relay_container" >/dev/null
relay_container=""
python3 -c 'import json,sys; p=sys.argv[1]; data=json.load(open(p)); data.update({"finished_at":sys.argv[2],"audit_exit_code":int(sys.argv[3])}); json.dump(data,open(p,"w"),indent=2,sort_keys=True); open(p,"a").write("\n")' \
  "$report_dir/host.json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$audit_rc"
exit "$audit_rc"
