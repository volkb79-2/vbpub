#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wheel_dir="$repo_root/run-gate-project/dist"
image_tag="tester-unified:local"
cgroup_parent="${CGROUP_PARENT_DEV_GATES:-}"
base_image=""
cli_extended_resolve=""
cli_extended_sha256=""
no_cache=0

usage() {
  echo "usage: $0 [--wheel-dir DIR] [--tag IMAGE] [--base-image IMAGE] [--cli-extended-resolve pinned|latest|local] [--cli-extended-wheel-sha256 HEX] [--no-cache] [--cgroup-parent SLICE]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wheel-dir) wheel_dir=${2:?}; shift 2 ;;
    --tag) image_tag=${2:?}; shift 2 ;;
    --base-image) base_image=${2:?}; shift 2 ;;
    --cli-extended-resolve) cli_extended_resolve=${2:?}; shift 2 ;;
    --cli-extended-wheel-sha256) cli_extended_sha256=${2:?}; shift 2 ;;
    --no-cache) no_cache=1; shift ;;
    --cgroup-parent) cgroup_parent=${2:?}; shift 2 ;;
    -h|--help)
      echo "usage: $0 [--wheel-dir DIR] [--tag IMAGE] [--base-image IMAGE] [--cli-extended-resolve pinned|latest|local] [--cli-extended-wheel-sha256 HEX] [--no-cache] [--cgroup-parent SLICE]"
      exit 0
      ;;
    *) usage ;;
  esac
done

build_args=()
if [[ -n "$base_image" ]]; then
  build_args+=(--build-arg "TESTER_UNIFIED_BASE_IMAGE=$base_image")
fi
if (( no_cache )); then
  build_args+=(--no-cache)
fi
case "$cli_extended_resolve" in
  "")
    [[ -z "$cli_extended_sha256" ]] || {
      echo "--cli-extended-wheel-sha256 requires --cli-extended-resolve local" >&2
      exit 2
    }
    ;;
  pinned|latest)
    [[ -z "$cli_extended_sha256" ]] || {
      echo "--cli-extended-wheel-sha256 is valid only with --cli-extended-resolve local" >&2
      exit 2
    }
    build_args+=(--build-arg "CLI_EXTENDED_RESOLVE=$cli_extended_resolve")
    ;;
  local)
    [[ "$cli_extended_sha256" =~ ^[0-9a-f]{64}$ ]] || {
      echo "--cli-extended-resolve local requires --cli-extended-wheel-sha256 with 64 lowercase hex digits" >&2
      exit 2
    }
    build_args+=(--build-arg "CLI_EXTENDED_RESOLVE=local")
    build_args+=(--build-arg "CLI_EXTENDED_WHEEL_SHA256=$cli_extended_sha256")
    ;;
  *)
    echo "--cli-extended-resolve must be pinned, latest or local" >&2
    exit 2
    ;;
esac

if [[ -z "$cgroup_parent" ]]; then
  echo "CGROUP_PARENT_DEV_GATES or --cgroup-parent is required; refusing an ungoverned image build" >&2
  exit 2
fi

# Reuse CMRU's supported host-systemd probe before BuildKit starts. A malformed
# slice name can be auto-created by systemd as an unlimited transient unit, so
# passing it through to Buildx without this host-side check is not sufficient.
cd "$repo_root"
cmru tester-gate --cwd run-gate-project --cgroup-parent "$cgroup_parent" /bin/true

identity_json="$(python3 "$repo_root/tester-unified/run-gate-wheel-version.py" "$wheel_dir" --json)"
mapfile -t identity < <(python3 -c '
import json,sys
value=json.load(sys.stdin)
print(value["version"])
print(value["sha256"])
print(value["filename"])
' <<< "$identity_json")
[[ "${#identity[@]}" -eq 3 ]] || { echo "could not read Run-Gate wheel identity" >&2; exit 2; }
run_gate_version=${identity[0]}
run_gate_sha256=${identity[1]}
run_gate_filename=${identity[2]}

wheel_stage="$(mktemp -d "${TMPDIR:-/tmp}/tester-unified-run-gate-wheel.XXXXXX")"
trap 'rm -rf "$wheel_stage"' EXIT
cp -- "$wheel_dir/$run_gate_filename" "$wheel_stage/$run_gate_filename"
printf '%s  %s\n' "$run_gate_sha256" "$wheel_stage/$run_gate_filename" \
  | sha256sum --check --status || {
    echo "Run-Gate wheel changed while preparing the tester-unified build context" >&2
    exit 2
  }

docker buildx build --load --cgroup-parent "$cgroup_parent" \
  "${build_args[@]}" \
  --build-context "run-gate-wheel=$wheel_stage" \
  --build-arg "RUN_GATE_TESTER_VERSION=$run_gate_version" \
  --build-arg "RUN_GATE_TESTER_WHEEL_SHA256=$run_gate_sha256" \
  -f "$repo_root/tester-unified/Dockerfile" \
  -t "$image_tag" "$repo_root"

actual_version="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.run-gate.version"}}' "$image_tag")"
actual_sha256="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.run-gate.wheel-sha256"}}' "$image_tag")"
if [[ "$actual_version" != "$run_gate_version" || "$actual_sha256" != "$run_gate_sha256" ]]; then
  echo "loaded tester image identity does not match the Run-Gate wheel used to build it" >&2
  echo "expected: version=$run_gate_version sha256=$run_gate_sha256" >&2
  echo "observed: version=$actual_version sha256=$actual_sha256" >&2
  exit 2
fi
