#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
image_tag="tester-unified-rust:local"
cgroup_parent="${CGROUP_PARENT_DEV_GATES:-}"
# BuildKit builder: --builder, then TESTER_UNIFIED_RUST_BUILDER, then the managed default.
builder="${TESTER_UNIFIED_RUST_BUILDER:-mdt-managed}"
usage() {
  printf 'usage: %s [--tag IMAGE] [--cgroup-parent SLICE]\n' "$0" >&2
  printf '  --builder NAME (or TESTER_UNIFIED_RUST_BUILDER) selects the buildx builder; default mdt-managed\n' >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) image_tag=${2:?}; shift 2 ;;
    --cgroup-parent) cgroup_parent=${2:?}; shift 2 ;;
    --builder) [[ $# -ge 2 ]] || usage; builder=$2; shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

[[ "$builder" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  printf 'invalid buildx builder name: %q\n' "$builder" >&2
  usage
}
[[ -n "$cgroup_parent" ]] || {
  printf 'CGROUP_PARENT_DEV_GATES or --cgroup-parent is required; refusing an ungoverned image build\n' >&2
  exit 2
}
[[ -f "$repo_root/hostobs/Cargo.lock" ]] || {
  printf 'hostobs/Cargo.lock is missing; the Rust image needs a committed lock\n' >&2
  exit 2
}

# The image identity is the registry-set digest, computed by the verifier script's own
# function (the one the image bakes and checks), never re-implemented here.
lock_registry_sha256="$(python3 "$repo_root/hostobs/tools/verify-crate-cache.py" --print-registry-digest --lock "$repo_root/hostobs/Cargo.lock")"
[[ "$lock_registry_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  printf 'could not compute the registry-set digest of hostobs/Cargo.lock\n' >&2
  exit 2
}
expected_base="$(sed -n 's/^ARG RUST_IMAGE=\(.*\)$/\1/p' "$repo_root/tester-unified-rust/Dockerfile")"
[[ -n "$expected_base" ]] || {
  printf 'could not read the digest-pinned RUST_IMAGE from Dockerfile\n' >&2
  exit 2
}

# CMRU's tester-gate is the supported host-systemd preflight used by the
# existing tester-unified build. Resolve its required bounds from the
# authoritative estate config when the caller has not supplied overrides.
tester_gate_defaults="$(python3 - "$repo_root/cmru.orchestration.toml" <<'PY'
import os
import re
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
document = tomllib.loads(path.read_text(encoding="utf-8"))
defaults = document["orchestration"]["defaults"]["env"]
keys = (
    "CMRU_TESTER_UNIFIED_IMAGE",
    "CMRU_TESTER_MEMORY",
    "CMRU_TESTER_MEMORY_SWAP",
    "CMRU_TESTER_CPUS",
    "CMRU_TESTER_PIDS_LIMIT",
    "CMRU_TESTER_CGROUP_PROBE_IMAGE",
)
expression = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)(?::-(.*))?\}$")
values = []
for key in keys:
    if key in os.environ:
        value = os.environ[key]
    else:
        value = defaults[key]
        if not isinstance(value, str):
            raise SystemExit(f"{key} in {path} must be a string")
        if value.startswith("${"):
            match = expression.fullmatch(value)
            if match is None:
                raise SystemExit(f"unsupported CMRU environment expression for {key}: {value!r}")
            variable, fallback = match.groups()
            if variable in os.environ:
                value = os.environ[variable]
            elif fallback is not None:
                value = fallback
            else:
                raise SystemExit(f"{key} requires {variable} to be set")
    if not isinstance(value, str) or not value or "\n" in value:
        raise SystemExit(f"{key} is missing or malformed")
    values.append(f"{key}={value}")
print("\n".join(values))
PY
)"
mapfile -t tester_gate_values <<< "$tester_gate_defaults"
[[ "${#tester_gate_values[@]}" -eq 6 ]] || {
  printf 'could not resolve CMRU tester-gate defaults from cmru.orchestration.toml\n' >&2
  exit 2
}
# tester-gate resolves every bound from its CMRU_TESTER_* variable when the matching
# option is absent (its documented explicit > environment precedence), which is how
# `cmru release` hands the same bounds to it. Passing them in the environment keeps
# the invocation below free of shell-expanded option values, so the estate argv check
# (cmru/tests/test_w2_integ.py) parses it literally and tester-gate validates the
# real values at run time.
for tester_gate_assignment in "${tester_gate_values[@]}"; do
  [[ "$tester_gate_assignment" =~ ^CMRU_TESTER_[A-Z_]+=. ]] || {
    printf 'malformed CMRU tester-gate default: %s\n' "$tester_gate_assignment" >&2
    exit 2
  }
  # shellcheck disable=SC2163 # the variable holds a whole NAME=value assignment on purpose
  export "$tester_gate_assignment"
done

cd "$repo_root"
cmru tester-gate \
  --cwd run-gate-project \
  --cgroup-parent "$cgroup_parent" \
  /bin/true

printf 'builder=%s\n' "$builder"
docker buildx inspect "$builder" >/dev/null
docker buildx build \
  --builder "$builder" \
  --load \
  --cgroup-parent "$cgroup_parent" \
  --network default \
  --build-arg "RUST_IMAGE=$expected_base" \
  --build-arg "HOSTOBS_LOCK_REGISTRY_SHA256=$lock_registry_sha256" \
  -f "$repo_root/tester-unified-rust/Dockerfile" \
  -t "$image_tag" \
  "$repo_root/hostobs"

actual_lock_registry_sha256="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256"}}' "$image_tag")"
if [[ "$actual_lock_registry_sha256" != "$lock_registry_sha256" ]]; then
  printf 'loaded Rust image does not record the current hostobs Cargo.lock registry set\n' >&2
  printf 'expected: %s\nobserved: %s\n' "$lock_registry_sha256" "${actual_lock_registry_sha256:-<no label>}" >&2
  exit 2
fi

actual_base="$(docker image inspect --format '{{index .Config.Labels "org.vbpub.tester-unified-rust.base-image"}}' "$image_tag")"
if [[ "$actual_base" != "$expected_base" ]]; then
  printf 'loaded Rust image does not record the digest-pinned base\n' >&2
  printf 'expected: %s\nobserved: %s\n' "$expected_base" "${actual_base:-<no label>}" >&2
  exit 2
fi

printf 'image=%s base=%s hostobs_lock_registry_sha256=%s\n' "$image_tag" "$actual_base" "$actual_lock_registry_sha256"
