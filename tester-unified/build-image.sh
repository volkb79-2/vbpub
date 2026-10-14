#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wheel_dir="$repo_root/run-gate-project/dist"
image_tag="tester-unified:local"

usage() {
  echo "usage: $0 [--wheel-dir DIR] [--tag IMAGE]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wheel-dir) wheel_dir=${2:?}; shift 2 ;;
    --tag) image_tag=${2:?}; shift 2 ;;
    -h|--help)
      echo "usage: $0 [--wheel-dir DIR] [--tag IMAGE]"
      exit 0
      ;;
    *) usage ;;
  esac
done

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

docker buildx build --load \
  --build-context "run-gate-wheel=$wheel_stage" \
  --build-arg "RUN_GATE_TESTER_VERSION=$run_gate_version" \
  --build-arg "RUN_GATE_TESTER_WHEEL_SHA256=$run_gate_sha256" \
  -f "$repo_root/tester-unified/Dockerfile" \
  -t "$image_tag" "$repo_root"
