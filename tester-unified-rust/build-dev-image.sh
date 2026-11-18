#!/usr/bin/env bash
# Build the developer layer image (tester-unified-rust/Dockerfile.dev) on top of an existing
# tester-unified-rust image. HOST WORK: needs network for apt and rustup, needs the base image
# in the local daemon, and is started only on the controller's go (docs/DEV.md).
#
# usage: build-dev-image.sh [--tag IMAGE] [--base IMAGE] [--cgroup-parent SLICE] [--builder NAME] [--record FILE]
#
# BuildKit builder: --builder, then TESTER_UNIFIED_RUST_BUILDER, then `default` (the docker
# driver, the only kind that can see a base image that exists only in the local daemon).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
image_tag="tester-unified-rust-dev:local"
base_image="tester-unified-rust:local"
builder="${TESTER_UNIFIED_RUST_BUILDER:-default}"
record_file=""
cgroup_parent="${CGROUP_PARENT_DEV_GATES:-}"
usage() {
  printf 'usage: %s [--tag IMAGE] [--base IMAGE] [--cgroup-parent SLICE] [--builder NAME] [--record FILE]\n' "$0" >&2
  printf '  --builder NAME (or TESTER_UNIFIED_RUST_BUILDER) selects the buildx builder; default: default\n' >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) [[ $# -ge 2 ]] || usage; image_tag=$2; shift 2 ;;
    --base) [[ $# -ge 2 ]] || usage; base_image=$2; shift 2 ;;
    --builder) [[ $# -ge 2 ]] || usage; builder=$2; shift 2 ;;
    --record) [[ $# -ge 2 ]] || usage; record_file=$2; shift 2 ;;
    --cgroup-parent) [[ $# -ge 2 ]] || usage; cgroup_parent=$2; shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

valid_builder_name() {
  local LC_ALL=C
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}
valid_builder_name "$builder" || {
  printf 'invalid buildx builder name: %q\n' "$builder" >&2
  exit 2
}
[[ -n "$cgroup_parent" ]] || {
  printf 'CGROUP_PARENT_DEV_GATES or --cgroup-parent is required (the estate rule for every image build); refusing to build without it\n' >&2
  exit 2
}
[[ -f "$repo_root/tester-unified-rust/Dockerfile.dev" ]] || {
  printf 'tester-unified-rust/Dockerfile.dev is missing\n' >&2
  exit 2
}

# The base must already exist locally: this layer is built ON the gate image so it shares its
# exact toolchain and crate cache; it never rebuilds either. Its immutable ID is recorded next to
# the (mutable) tag.
base_id="$(docker image inspect --format '{{.Id}}' "$base_image" 2>/dev/null)" || {
  printf 'base image %s is not in the local daemon; build it first (tester-unified-rust/build-image.sh) or load it\n' "$base_image" >&2
  exit 2
}
[[ "$base_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  printf 'unexpected image id for %s: %s\n' "$base_image" "$base_id" >&2
  exit 2
}

# The Dockerfile has no COPY/ADD, so the build context is an empty directory.
context="$(mktemp -d)"
trap 'rmdir "$context" 2>/dev/null || true' EXIT

printf 'builder=%s\n' "$builder"
# What --cgroup-parent does and does not do (measured on v1001, docker cgroup driver systemd): containers
# started with `docker run --cgroup-parent dev-gates.slice` land in the governed dev.slice/dev-gates.slice,
# but the RUN-step processes of a docker-driver BuildKit build land in a root-level, UNGOVERNED
# /sys/fs/cgroup/dev-gates.slice/buildkit (memory.max = max). The flag below is passed because the estate
# requires it, not because it governs this build. Watch the build's memory yourself (docs/DEV.md).
printf 'NOTE: on the docker-driver builder --cgroup-parent does NOT place this build in the governed slice: the build runs in an ungoverned cgroup (v1001: /sys/fs/cgroup/dev-gates.slice/buildkit); watch its memory\n' >&2
docker buildx inspect "$builder" >/dev/null
# The build starts FROM a content-named tag of the exact base image id: a moved tag cannot change it,
# and it is a plain reference every builder accepts (a bare `sha256:<id>` in FROM is not, on every
# driver). The script creates this tag (exact name, same id: re-running is idempotent) and never removes it.
base_tag="tester-unified-rust-base:${base_id#sha256:}"
docker tag "$base_id" "$base_tag"
docker buildx build \
  --builder "$builder" \
  --load \
  --progress plain \
  --cgroup-parent "$cgroup_parent" \
  --network default \
  --build-arg "BASE_IMAGE=$base_tag" \
  --build-arg "BASE_IMAGE_TAG=$base_image" \
  --build-arg "BASE_IMAGE_ID=$base_id" \
  -f "$repo_root/tester-unified-rust/Dockerfile.dev" \
  -t "$image_tag" \
  "$context"

actual_base="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified-rust-dev.base-image"}}' "$image_tag")"
actual_base_id="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified-rust-dev.base-image-id"}}' "$image_tag")"
if [[ "$actual_base" != "$base_image" || "$actual_base_id" != "$base_id" ]]; then
  printf 'loaded dev image does not record the requested base\n' >&2
  printf 'expected: %s %s\nobserved: %s %s\n' "$base_image" "$base_id" "${actual_base:-<no label>}" "${actual_base_id:-<no label>}" >&2
  exit 2
fi
# The labels only echo the build args. The layer stack does not: the dev image must start with every
# layer of the base image, in order.
base_layers="$(docker image inspect --format '{{json .RootFS.Layers}}' "$base_id")"
dev_layers="$(docker image inspect --format '{{json .RootFS.Layers}}' "$image_tag")"
if [[ -z "$base_layers" || "$base_layers" == "null" || "$dev_layers" != "${base_layers%]},"* ]]; then
  printf 'loaded dev image is not built on the layers of base image %s\n' "$base_id" >&2
  exit 2
fi

# Everything this layer added or upgraded, with its version: the DIFF of `dpkg-query -W` between the
# base image and the dev image (the dependency closure of gdb and friends included), plus the
# toolchain lines. The probe containers are started with `docker run --cgroup-parent`, which DOES place
# them in the governed slice (unlike the build above); they have no network and no capabilities.
probe() {
  docker run --rm --pull never --network none --cap-drop ALL \
    --security-opt no-new-privileges --cgroup-parent "$cgroup_parent" \
    --memory 512m --memory-swap 512m --pids-limit 128 "$@"
}
# shellcheck disable=SC2016 # dpkg-query format, not shell
package_list() { probe "$1" dpkg-query -W -f '${Package}\t${Version}\n' | LC_ALL=C sort; }
base_packages="$(mktemp)"
dev_packages="$(mktemp)"
trap 'rmdir "$context" 2>/dev/null || true; rm -f "$base_packages" "$dev_packages"' EXIT
package_list "$base_id" > "$base_packages"
package_list "$image_tag" > "$dev_packages"
added="$(LC_ALL=C comm -13 "$base_packages" "$dev_packages")"

printf 'packages added or upgraded by the dev layer (name, version):\n%s\n' "$added"
printf 'toolchain:\n'
probe "$image_tag" rustc --version
probe "$image_tag" rust-analyzer --version

if [[ -n "$record_file" ]]; then
  build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf '# tester-unified-rust-dev vetting record: name<TAB>version<TAB>build date (UTC)\n'
    printf '# the date column is the BUILD date; a release date cannot be checked offline and is filled in by the host work\n'
    printf '# image=%s base=%s base_id=%s\n' "$image_tag" "$base_image" "$base_id"
    while IFS=$'\t' read -r name version; do
      [[ -z "$name" ]] || printf '%s\t%s\t%s\n' "$name" "$version" "$build_date"
    done <<< "$added"
    printf 'rustc\t%s\t%s\n' "$(probe "$image_tag" rustc --version)" "$build_date"
    printf 'rust-analyzer\t%s\t%s\n' "$(probe "$image_tag" rust-analyzer --version)" "$build_date"
    probe "$image_tag" rustup component list --installed | while IFS= read -r component; do
      printf 'component\t%s\t%s\n' "$component" "$build_date"
    done
  } > "$record_file"
  printf 'recorded %s\n' "$record_file"
fi

printf 'image=%s base=%s base_id=%s\n' "$image_tag" "$actual_base" "$actual_base_id"
