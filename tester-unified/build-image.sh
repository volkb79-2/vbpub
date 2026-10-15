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
  echo "usage: $0 [--wheel-dir DIR] [--tag IMAGE] [--base-image IMAGE] [--cli-extended-resolve pinned|latest|local] [--cli-extended-wheel-sha256 HEX (required for latest and local)] [--no-cache] [--cgroup-parent SLICE]" >&2
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
# The digest the installed cli-extended wheel must have, whatever the mode. The
# image labels it (org.vbpub.cli-extended.wheel-sha256) and the build verifies the
# installed wheel file against it; this script re-reads the label after the build.
pinned_digest() {
  sed -n 's/^ARG CLI_EXTENDED_WHEEL_SHA256=\([0-9a-f]\{64\}\)$/\1/p' \
    "$repo_root/tester-unified/Dockerfile"
}
case "$cli_extended_resolve" in
  ""|pinned)
    [[ -z "$cli_extended_sha256" ]] || {
      echo "--cli-extended-wheel-sha256 is valid only with --cli-extended-resolve local or latest" >&2
      exit 2
    }
    [[ -z "$cli_extended_resolve" ]] || build_args+=(--build-arg "CLI_EXTENDED_RESOLVE=pinned")
    expected_cli_extended_sha256="$(pinned_digest)"
    ;;
  latest|local)
    [[ "$cli_extended_sha256" =~ ^[0-9a-f]{64}$ ]] || {
      echo "--cli-extended-resolve $cli_extended_resolve requires --cli-extended-wheel-sha256 with 64 lowercase hex digits" \
           "(for latest: the published wheel's digest, so no unverified bytes can be installed)" >&2
      exit 2
    }
    build_args+=(--build-arg "CLI_EXTENDED_RESOLVE=$cli_extended_resolve")
    build_args+=(--build-arg "CLI_EXTENDED_WHEEL_SHA256=$cli_extended_sha256")
    expected_cli_extended_sha256="$cli_extended_sha256"
    ;;
  *)
    echo "--cli-extended-resolve must be pinned, latest or local" >&2
    exit 2
    ;;
esac
[[ "$expected_cli_extended_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "could not determine the expected cli-extended wheel digest" >&2
  exit 2
}

if [[ -z "$cgroup_parent" ]]; then
  echo "CGROUP_PARENT_DEV_GATES or --cgroup-parent is required; refusing an ungoverned image build" >&2
  exit 2
fi

# Reuse CMRU's supported host-systemd probe before BuildKit starts. A malformed
# slice name can be auto-created by systemd as an unlimited transient unit, so
# passing it through to Buildx without this host-side check is not sufficient.
cd "$repo_root"
tester_gate_args=(--cwd run-gate-project --cgroup-parent "$cgroup_parent")
if [[ -n "$base_image" ]]; then
  # During a zero-release bootstrap, tester-unified:local does not exist yet.
  # Probe the declared upstream base instead; CMRU's ordinary tester-gate
  # launch pulls a missing unprivileged workload image as needed. The separate
  # privileged host-systemd probe remains digest-pinned and pull=never.
  tester_gate_args+=(--image "$base_image")
fi
cmru tester-gate "${tester_gate_args[@]}" /bin/true

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

actual_cli_extended_sha256="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.cli-extended.wheel-sha256"}}' "$image_tag")"
if [[ "$actual_cli_extended_sha256" != "$expected_cli_extended_sha256" ]]; then
  echo "loaded tester image does not carry the expected cli-extended wheel" >&2
  echo "expected: sha256=$expected_cli_extended_sha256" >&2
  echo "observed: sha256=${actual_cli_extended_sha256:-<no label>}" >&2
  exit 2
fi
