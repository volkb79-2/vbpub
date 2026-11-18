#!/usr/bin/env bash
# Build the developer layer image (tester-unified-rust/Dockerfile.dev) on top of an existing
# tester-unified-rust image. HOST WORK: needs network for apt and rustup, needs the base image
# in the local daemon, and is started only on the controller's go (docs/DEV.md).
#
# usage: build-dev-image.sh [--tag IMAGE] [--base IMAGE] [--cgroup-parent SLICE] [--builder NAME] [--record FILE]
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
image_tag="tester-unified-rust-dev:local"
base_image="tester-unified-rust:local"
builder="mdt-managed"
record_file=""
cgroup_parent="${CGROUP_PARENT_DEV_GATES:-}"
usage() {
  printf 'usage: %s [--tag IMAGE] [--base IMAGE] [--cgroup-parent SLICE] [--builder NAME] [--record FILE]\n' "$0" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) image_tag=${2:?}; shift 2 ;;
    --base) base_image=${2:?}; shift 2 ;;
    --builder) builder=${2:?}; shift 2 ;;
    --record) record_file=${2:?}; shift 2 ;;
    --cgroup-parent) cgroup_parent=${2:?}; shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

[[ -n "$cgroup_parent" ]] || {
  printf 'CGROUP_PARENT_DEV_GATES or --cgroup-parent is required; refusing an ungoverned image build\n' >&2
  exit 2
}
[[ -f "$repo_root/tester-unified-rust/Dockerfile.dev" ]] || {
  printf 'tester-unified-rust/Dockerfile.dev is missing\n' >&2
  exit 2
}

# The base must already exist locally: this layer is built ON the gate image so it shares its
# exact toolchain and crate cache; it never rebuilds either.
docker image inspect "$base_image" >/dev/null 2>&1 || {
  printf 'base image %s is not in the local daemon; build it first (tester-unified-rust/build-image.sh) or load it\n' "$base_image" >&2
  exit 2
}

# The Dockerfile has no COPY/ADD, so the build context is an empty directory.
context="$(mktemp -d)"
trap 'rmdir "$context" 2>/dev/null || true' EXIT

docker buildx inspect "$builder" >/dev/null
docker buildx build \
  --builder "$builder" \
  --load \
  --progress plain \
  --cgroup-parent "$cgroup_parent" \
  --network default \
  --build-arg "BASE_IMAGE=$base_image" \
  -f "$repo_root/tester-unified-rust/Dockerfile.dev" \
  -t "$image_tag" \
  "$context"

actual_base="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified-rust-dev.base-image"}}' "$image_tag")"
if [[ "$actual_base" != "$base_image" ]]; then
  printf 'loaded dev image does not record the requested base\n' >&2
  printf 'expected: %s\nobserved: %s\n' "$base_image" "${actual_base:-<no label>}" >&2
  exit 2
fi

# Versions as installed by the base image's Debian suite, printed into the build log and, with
# --record, written to a file for the controller's 14-day vetting (docs/DEV.md). The probe
# container has no network, no capabilities and is placed in the same governed slice.
probe=(docker run --rm --pull never --network none --cap-drop ALL
  --security-opt no-new-privileges --cgroup-parent "$cgroup_parent"
  --memory 512m --memory-swap 512m --pids-limit 128 "$image_tag")
printf 'dev image package versions (dpkg-query -W):\n'
"${probe[@]}" dpkg-query -W gdb strace procps less ca-certificates
printf 'toolchain:\n'
"${probe[@]}" rustc --version
"${probe[@]}" rust-analyzer --version

if [[ -n "$record_file" ]]; then
  build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf '# tester-unified-rust-dev vetting record (name<TAB>version<TAB>build date, UTC)\n'
    printf '# image=%s base=%s\n' "$image_tag" "$base_image"
    # shellcheck disable=SC2016 # dpkg-query format, not shell
    "${probe[@]}" dpkg-query -W -f '${Package}\t${Version}\n' gdb strace procps less ca-certificates \
      | while IFS=$'\t' read -r name version; do printf '%s\t%s\t%s\n' "$name" "$version" "$build_date"; done
    printf 'rust-analyzer\t%s\t%s\n' "$("${probe[@]}" rust-analyzer --version)" "$build_date"
    printf 'rustc\t%s\t%s\n' "$("${probe[@]}" rustc --version)" "$build_date"
  } > "$record_file"
  printf 'recorded %s\n' "$record_file"
fi

printf 'image=%s base=%s\n' "$image_tag" "$actual_base"
