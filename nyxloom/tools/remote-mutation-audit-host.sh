#!/usr/bin/env bash
# Commit-addressed host launcher for remote_mutation_audit.py.
set -u -o pipefail

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

docker_args=(docker run --rm --cgroup-parent=nyxloom-gates.slice -v "$checkout:/audit" -v "$report_dir:/reports" -w /audit)
if [[ -n "$allow_infra" ]]; then
  docker_args+=(-v /var/run/docker.sock:/var/run/docker.sock)
fi
container_args=("${docker_args[@]}" "$image_tag" /opt/tester-venv/bin/python nyxloom/tools/remote_mutation_audit.py --manifest "/audit/$manifest_rel" --report-dir /reports)
[[ -n "$max_mutants" ]] && container_args+=(--max-mutants "$max_mutants")
[[ -n "$allow_infra" ]] && container_args+=(--allow-infra)
"${container_args[@]}"
