#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wheel_dir="$repo_root/run-gate-project/dist"
run_gate_release=""
image_tag="tester-unified:local"
cgroup_parent="${CGROUP_PARENT_DEV_GATES:-}"
base_image=""
cli_extended_resolve=""
cli_extended_sha256=""
no_cache=0

usage() {
  echo "usage: $0 [--wheel-dir DIR | --run-gate-release X.Y.Z] [--tag IMAGE] [--base-image IMAGE] [--cli-extended-resolve pinned|latest|local] [--cli-extended-wheel-sha256 HEX (required for latest and local)] [--no-cache] [--cgroup-parent SLICE]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wheel-dir) wheel_dir=${2:?}; shift 2 ;;
    --run-gate-release) run_gate_release=${2:?}; shift 2 ;;
    --tag) image_tag=${2:?}; shift 2 ;;
    --base-image) base_image=${2:?}; shift 2 ;;
    --cli-extended-resolve) cli_extended_resolve=${2:?}; shift 2 ;;
    --cli-extended-wheel-sha256) cli_extended_sha256=${2:?}; shift 2 ;;
    --no-cache) no_cache=1; shift ;;
    --cgroup-parent) cgroup_parent=${2:?}; shift 2 ;;
    -h|--help)
      echo "usage: $0 [--wheel-dir DIR | --run-gate-release X.Y.Z] [--tag IMAGE] [--base-image IMAGE] [--cli-extended-resolve pinned|latest|local] [--cli-extended-wheel-sha256 HEX] [--no-cache] [--cgroup-parent SLICE]"
      exit 0
      ;;
    *) usage ;;
  esac
done

build_args=()
if [[ -n "$run_gate_release" ]]; then
  # The image for ANOTHER tool's release gate must carry the PUBLISHED Run-Gate wheel (CMRU's gate
  # compares the labels with the published release of the installed Run-Gate). The default source
  # wheel (run-gate-project/dist) is a development version and is refused there.
  [[ "$wheel_dir" == "$repo_root/run-gate-project/dist" ]] || {
    echo "--run-gate-release and --wheel-dir are mutually exclusive" >&2
    exit 2
  }
  wheel_dir="$(mktemp -d "${TMPDIR:-/tmp}/tester-unified-run-gate-release.XXXXXX")"
  trap 'rm -rf "$wheel_dir"' EXIT
  python3 "$repo_root/tester-unified/fetch-run-gate-release.py" --version "$run_gate_release" --dest "$wheel_dir" >/dev/null
fi
if [[ -n "$base_image" ]]; then
  build_args+=(--build-arg "TESTER_UNIFIED_BASE_IMAGE=$base_image")
  expected_base_image="$base_image"
else
  expected_base_image="$(sed -n 's/^ARG TESTER_UNIFIED_BASE_IMAGE=\(.*\)$/\1/p' \
    "$repo_root/tester-unified/Dockerfile")"
fi
[[ -n "$expected_base_image" ]] || {
  echo "could not determine the expected tester-unified base image" >&2
  exit 2
}
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
trap 'rm -rf "$wheel_stage" ${run_gate_release:+"$wheel_dir"}' EXIT
cp -- "$wheel_dir/$run_gate_filename" "$wheel_stage/$run_gate_filename"
printf '%s  %s\n' "$run_gate_sha256" "$wheel_stage/$run_gate_filename" \
  | sha256sum --check --status || {
    echo "Run-Gate wheel changed while preparing the tester-unified build context" >&2
    exit 2
  }

# The base is named by a reference that can be a mutable tag (tester-base:local). Record the identity
# it resolved to at build time as a second label, so two images built on different content of the same
# tag are distinguishable. A digest reference is its own identity; a local image is resolved with
# `docker image inspect`; a registry reference that is not local is pulled explicitly and then identified;
# a registry-less tag that is absent refuses with how to build it (guessing would record a made-up value).
base_ref="${base_image:-$expected_base_image}"
case "$base_ref" in
  *@sha256:*) expected_base_image_id="sha256:${base_ref##*@sha256:}" ;;
  *)
    if ! expected_base_image_id="$(docker image inspect --format '{{.Id}}' "$base_ref" 2>/dev/null)"; then
      if [[ "$base_ref" == */* ]]; then
        # A registry reference (the explicit zero-release --base-image, a published tester-base digest
        # tag): pull it explicitly, then identify what was pulled. The base is a named, explicit input,
        # so this is not a silent fallback, and buildx then uses exactly the image identified here.
        echo "base image $base_ref is not present locally; pulling it" >&2
        docker pull "$base_ref" >&2 || {
          echo "cannot pull base image $base_ref" >&2
          exit 2
        }
        expected_base_image_id="$(docker image inspect --format '{{.Id}}' "$base_ref" 2>/dev/null)" || {
          echo "cannot identify base image $base_ref after pulling it" >&2
          exit 2
        }
      else
        # A registry-less tag (tester-base:local) exists only if someone built it; there is nothing to pull.
        echo "base image $base_ref is not present locally and has no registry to pull from" >&2
        echo "build it first (tester-base: docker buildx build --load -t tester-base:local tester-base/, or run cmru/build-initial-standalone.sh)" >&2
        exit 2
      fi
    fi
    ;;
esac
[[ "$expected_base_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "unexpected base image identity '$expected_base_image_id' for $base_ref" >&2
  exit 2
}
build_args+=(--build-arg "TESTER_UNIFIED_BASE_IMAGE_ID=$expected_base_image_id")

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

actual_base_image="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified.base-image"}}' "$image_tag")"
if [[ "$actual_base_image" != "$expected_base_image" ]]; then
  echo "loaded tester image was not built from the requested base" >&2
  echo "expected: $expected_base_image" >&2
  echo "observed: ${actual_base_image:-<no label>}" >&2
  exit 2
fi

actual_base_image_id="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified.base-image-id"}}' "$image_tag")"
if [[ "$actual_base_image_id" != "$expected_base_image_id" ]]; then
  echo "loaded tester image does not record the identity of the base it was built on" >&2
  echo "expected: $expected_base_image_id" >&2
  echo "observed: ${actual_base_image_id:-<no label>}" >&2
  exit 2
fi
