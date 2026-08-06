#!/usr/bin/env bash
# go-base.sh — ensure the shared Go gate base image exists, and print its name.
#
#   base="$(tools/go-base.sh [cgroup-parent])"
#
# The Go toolchain half of gate/Dockerfile was promoted to the shared
# vbpub/tester-unified-go image (assay decisions.md A-043), so both srdm gate
# targets now build FROM it. Two scripts need it — tools/gate.sh and
# tools/canary-run.sh — so the "build it if absent" rule lives here once
# instead of being copied into both. Same shape as cgroup-parent.sh: it prints
# one value on stdout and everything else on stderr.
#
# It is deliberately build-if-absent and NEVER rebuild-if-present. A rebuild
# would silently change the toolchain under a coverage profile or a binary the
# gate is about to judge; changing the pin is an explicit act (edit the base
# Dockerfile, then `docker image rm tester-unified-go:local`).

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$here")"
repo_root="$(cd "$project_dir/.." && pwd)"

cgroup_parent="${1:-}"

die() { printf 'go-base: %s\n' "$*" >&2; exit 1; }

image="${SRDM_GO_BASE_IMAGE:-tester-unified-go:local}"
base_dir="$repo_root/tester-unified-go"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  [ -f "$base_dir/Dockerfile" ] || die "the shared Go gate base is missing and cannot be built:
  no Dockerfile at $base_dir.
  Set SRDM_GO_BASE_IMAGE to an image that already exists, or restore
  vbpub/tester-unified-go/Dockerfile."
  printf 'go-base: building %s\n' "$image" >&2
  build_args=()
  [ -n "$cgroup_parent" ] && build_args+=(--cgroup-parent="$cgroup_parent")
  docker build "${build_args[@]}" -f "$base_dir/Dockerfile" -t "$image" "$base_dir" >&2
fi

printf '%s\n' "$image"
