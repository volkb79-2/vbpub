#!/usr/bin/env bash
# Commit-addressed host launcher for remote_mutation_audit.py.
set -euo pipefail

usage() { echo "usage: $0 --manifest-rel PATH [--repo URL] [--ref origin/main] [--reports-root DIR] [--allow-infra] [--max-mutants N]" >&2; exit 2; }
manifest_rel=""; repo=""; ref="origin/main"; reports_root="${MUTATION_AUDIT_REPORT_ROOT:-$PWD/mutation-audit-reports}"; allow_infra=""; max_mutants=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest-rel) manifest_rel=${2:?}; shift 2 ;;
    --repo) repo=${2:?}; shift 2 ;;
    --ref) ref=${2:?}; shift 2 ;;
    --reports-root) reports_root=${2:?}; shift 2 ;;
    --allow-infra) allow_infra=1; shift ;;
    --max-mutants) max_mutants=${2:?}; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$manifest_rel" ]] || usage
[[ -n "$repo" ]] || repo=$(git config --get remote.origin.url) || { echo "no --repo and no origin remote" >&2; exit 2; }

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
checkout=$(mktemp -d "${TMPDIR:-/tmp}/nyxloom-mutation-audit.XXXXXX")
cleanup() {
  local rc=$?
  [[ -n "${relay_container:-}" ]] && docker rm -f "$relay_container" >/dev/null 2>&1 || true
  [[ -n "${audit_container:-}" ]] && docker rm -f "$audit_container" >/dev/null 2>&1 || true
  [[ -n "${stage_container:-}" ]] && docker rm -f "$stage_container" >/dev/null 2>&1 || true
  [[ -n "${report_volume:-}" ]] && docker volume rm "$report_volume" >/dev/null 2>&1 || true
  [[ -n "${source_volume:-}" ]] && docker volume rm "$source_volume" >/dev/null 2>&1 || true
  [[ -n "${image_tag:-}" ]] && docker image rm "$image_tag" >/dev/null 2>&1 || true
  [[ -d "$checkout" ]] && git -C "$checkout" worktree prune >/dev/null 2>&1 || true
  [[ -d "$checkout" ]] && find "$checkout" -depth -delete >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT INT TERM

git clone --no-checkout "$repo" "$checkout"
# Operators naturally say origin/main for their local tracking ref, while a
# remote server exposes that branch as main.  Fetch the latter but record the
# exact FETCH_HEAD SHA, never the moving branch label.
fetch_ref=${ref#origin/}
git -C "$checkout" fetch --no-tags origin "$fetch_ref"
sha=$(git -C "$checkout" rev-parse FETCH_HEAD)
git -C "$checkout" checkout --detach "$sha"
[[ -f "$checkout/$manifest_rel" ]] || { echo "manifest missing at audited commit: $manifest_rel" >&2; exit 2; }
project=$(awk -F'"' '/^project = / {print $2; exit}' "$checkout/$manifest_rel")
[[ -n "$project" ]] || { echo "could not read [audit].project" >&2; exit 2; }
report_dir="$reports_root/$project/$sha/$run_id"
mkdir -p "$report_dir" && chmod a+rwx "$report_dir" "$checkout"
find "$checkout" -type d -exec chmod a+rwx {} +
image_tag="tester-unified:mutation-audit-${sha:0:12}"
docker build -f "$checkout/tester-unified/Dockerfile" -t "$image_tag" "$checkout"

# Do not bind the caller's paths into Docker.  A remote host's Docker daemon
# often cannot see a devcontainer's /workspaces or /tmp mount namespace.  Copy
# the exact detached checkout into a private named volume, run there, then copy
# the immutable report volume back to the caller.  This also avoids the base
# tester image's /audit tmpfs mount.
volume_suffix=$(printf '%s' "$run_id" | tr -cd '[:alnum:]')
source_volume="nyxloom-mutation-src-${volume_suffix}"
report_volume="nyxloom-mutation-report-${volume_suffix}"
docker volume create "$source_volume" >/dev/null
docker volume create "$report_volume" >/dev/null
stage_container=$(docker create -v "$source_volume:/remote-audit" "$image_tag" /bin/true)
docker cp "$checkout/." "$stage_container:/remote-audit"
docker rm "$stage_container" >/dev/null
stage_container=""
# Docker creates named volumes as root.  The tester image deliberately runs as
# its unprivileged test uid, which needs to make disposable worktrees and write
# reports; fix ownership before the audit starts.
docker run --rm --user 0 -v "$source_volume:/remote-audit" -v "$report_volume:/reports" "$image_tag" /bin/sh -lc \
  'chown -R 1003:994 /remote-audit && chown 1003:994 /reports'

docker_args=(docker create --cgroup-parent=nyxloom-gates.slice -v "$source_volume:/remote-audit" -v "$report_volume:/reports" -w /remote-audit)
if [[ -n "$allow_infra" ]]; then
  docker_args+=(-v /var/run/docker.sock:/var/run/docker.sock)
fi
container_args=("${docker_args[@]}" "$image_tag" /opt/tester-venv/bin/python nyxloom/tools/remote_mutation_audit.py --manifest "/remote-audit/$manifest_rel" --report-dir /reports)
[[ -n "$max_mutants" ]] && container_args+=(--max-mutants "$max_mutants")
[[ -n "$allow_infra" ]] && container_args+=(--allow-infra)
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
exit "$audit_rc"
