#!/usr/bin/env bash
# Build the first CMRU wheel from a fresh vbpub checkout.
#
# This script deliberately does not invoke an installed cmru command: the
# command is what this script is creating. The wheel-builder image is an
# independent toolchain and receives the source project through a bind mount.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "${project_dir}/.." && pwd -P)"
python_bin="${CMRU_BOOTSTRAP_PYTHON:-python3}"
builder_image="${CMRU_WHEEL_BUILDER_IMAGE:-}"

if ! command -v "${python_bin}" >/dev/null 2>&1; then
    echo "[ERROR] bootstrap Python not found: ${python_bin}" >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "[ERROR] Docker is required to build the standalone CMRU wheel." >&2
    exit 2
fi

# The project contract is authoritative for the builder image.  The bootstrap
# cannot import CMRU before it has built CMRU, but Python's stdlib TOML reader
# can read that one source fact without duplicating or defaulting it here.
if [[ -z "${builder_image}" ]]; then
    builder_image="$("${python_bin}" - "${project_dir}/cmru.toml" <<'PYEOF'
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    document = tomllib.load(handle)
value = document.get("env", {}).get("CMRU_WHEEL_BUILDER_IMAGE", "")
if not isinstance(value, str) or not value.strip():
    raise SystemExit("cmru.toml [env].CMRU_WHEEL_BUILDER_IMAGE must be a non-empty string")
print(value.strip())
PYEOF
    )" || {
        echo "[ERROR] could not read CMRU_WHEEL_BUILDER_IMAGE from ${project_dir}/cmru.toml" >&2
        exit 2
    }
fi

# Docker build/run must remain in the governed development tier. There is no
# safe default here: an absent value would place the bootstrap workload beside
# production containers.
cgroup_parent="${CMRU_BOOTSTRAP_CGROUP_PARENT:-${CGROUP_PARENT_DEV_BACKGROUND:-}}"
if [[ -z "${cgroup_parent}" ]]; then
    echo "[ERROR] no governed cgroup parent found; set CMRU_BOOTSTRAP_CGROUP_PARENT or CGROUP_PARENT_DEV_BACKGROUND" >&2
    exit 2
fi

if ! docker image inspect "${builder_image}" >/dev/null 2>&1; then
    echo "[INFO] Building ${builder_image} from wheel-builder/Dockerfile" >&2
    docker build \
        --cgroup-parent "${cgroup_parent}" \
        -f "${repo_root}/wheel-builder/Dockerfile" \
        -t "${builder_image}" \
        "${repo_root}"
fi

echo "[INFO] Building the standalone CMRU wheel from ${project_dir}" >&2
(
    cd "${project_dir}"
    export PYTHONPATH="${project_dir}/src${PYTHONPATH:+:${PYTHONPATH}}"
    export CMRU_WHEEL_BUILDER_IMAGE="${builder_image}"
    export CMRU_DOCKER_CGROUP_PARENT="${cgroup_parent}"
    exec "${python_bin}" -m cmru.handlers wheel-build --cwd .
)

shopt -s nullglob
wheels=("${project_dir}/dist"/cmru-*.whl)
if (( ${#wheels[@]} != 1 )); then
    echo "[ERROR] expected exactly one CMRU wheel in ${project_dir}/dist; found ${#wheels[@]}" >&2
    exit 1
fi

echo "" >&2
echo "[INFO] Built: ${wheels[0]}" >&2
echo "" >&2
echo "[INFO] Install it into an isolated environment with:" >&2
echo "       cd ${repo_root}" >&2
echo "       python3 -m venv .venv-cmru" >&2
echo "       .venv-cmru/bin/python -m pip install --no-deps ${wheels[0]}" >&2
echo "       export PATH=\"${repo_root}/.venv-cmru/bin:\$PATH\"" >&2
echo "" >&2
echo "[INFO] Install wheel directly into \`.venv/bin/cmru\`:" >&2
echo "       python -m pip install --no-deps ${wheels[0]}" >&2
