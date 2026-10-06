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

# cmru depends on the RELEASED cli-extended wheel (KI-51 / CX-D1: never vendored,
# never from an index). `python -m cmru.handlers` imports it, so the bootstrap
# interpreter must have it BEFORE the first cmru wheel exists. It is verified by
# sha256 and unpacked (a wheel is a zip; no pip needed) into a private staging
# directory that goes on PYTHONPATH, so nothing is installed into
# ${python_bin} itself. Source: an operator-supplied wheel plus its digest
# (CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL + CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256, the
# offline path), else the sha256-verifying fetcher over the release pointer
# `cli-extended-latest/latest.json` (CX-D2).
cli_extended_floor="$("${python_bin}" -s - "${project_dir}/pyproject.toml" <<'PYEOF'
import re
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    dependencies = tomllib.load(handle)["project"]["dependencies"]
for requirement in dependencies:
    match = re.fullmatch(r"cli[-_.]extended\s*>=\s*([0-9][0-9.]*)", requirement.strip(), re.I)
    if match:
        print(match.group(1))
        break
else:
    raise SystemExit("cmru pyproject.toml declares no cli-extended>=<floor> dependency")
PYEOF
)" || {
    echo "[ERROR] could not read the cli-extended floor from ${project_dir}/pyproject.toml" >&2
    exit 2
}
cli_extended_stage="$(mktemp -d "${TMPDIR:-/tmp}/cmru-bootstrap-cli-extended.XXXXXX")"
trap 'rm -rf "${cli_extended_stage}"' EXIT
cli_extended_wheel="${CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL:-}"
if [[ -n "${cli_extended_wheel}" ]]; then
    cli_extended_sha="${CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256:-}"
    if [[ ! "${cli_extended_sha}" =~ ^[0-9a-f]{64}$ ]]; then
        echo "[ERROR] CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL needs CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256 (64 lowercase hex digits)" >&2
        exit 2
    fi
    actual_sha="$("${python_bin}" -s -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${cli_extended_wheel}")" || {
        echo "[ERROR] cannot read ${cli_extended_wheel}" >&2
        exit 2
    }
    if [[ "${actual_sha}" != "${cli_extended_sha}" ]]; then
        echo "[ERROR] sha256 mismatch for ${cli_extended_wheel}: expected ${cli_extended_sha}, got ${actual_sha}" >&2
        exit 2
    fi
else
    cli_extended_wheel="$("${python_bin}" -s "${repo_root}/tester-unified/fetch-cli-extended.py" \
        --dest "${cli_extended_stage}/download" --min-version "${cli_extended_floor}")" || {
        echo "[ERROR] could not fetch the released cli-extended wheel; supply CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL and CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256" >&2
        exit 2
    }
fi
"${python_bin}" -s -m zipfile -e "${cli_extended_wheel}" "${cli_extended_stage}/site" || {
    echo "[ERROR] could not unpack ${cli_extended_wheel}" >&2
    exit 2
}

# D2: cmru's CLI identity is the installed ``cmru`` DISTRIBUTION's metadata (no
# source-tree fallback). The first wheel does not exist yet, so stage a minimal
# ``cmru-<ver>.dist-info`` beside the unpacked cli-extended on PYTHONPATH. The
# version is derived exactly as the wheel build (setuptools-scm, tag_regex
# ^cmru-v...) derives it: the pretend-version override first, else the next-patch
# ``.devN+gHASH`` shape of ``git describe --long`` (the exact tag when N is 0).
cmru_version="${SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU:-${SETUPTOOLS_SCM_PRETEND_VERSION:-}}"
if [[ -z "${cmru_version}" ]]; then
    describe="$(git -C "${repo_root}" describe --tags --long --match 'cmru-v*' 2>/dev/null)" || describe=""
    if [[ "${describe}" =~ ^cmru-v([0-9]+)\.([0-9]+)\.([0-9]+)-([0-9]+)-g([0-9a-f]+)$ ]]; then
        if [[ "${BASH_REMATCH[4]}" == "0" ]]; then
            cmru_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
        else
            cmru_version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1)).dev${BASH_REMATCH[4]}+g${BASH_REMATCH[5]}"
        fi
    fi
fi
if [[ ! "${cmru_version}" =~ ^[0-9][0-9A-Za-z.+!-]*$ ]]; then
    echo "[ERROR] cannot derive the cmru version (need a cmru-vX.Y.Z tag in ${repo_root} or SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU)" >&2
    exit 2
fi
cmru_dist_info="${cli_extended_stage}/site/cmru-${cmru_version}.dist-info"
mkdir -p "${cmru_dist_info}"
printf 'Metadata-Version: 2.1\nName: cmru\nVersion: %s\n' "${cmru_version}" > "${cmru_dist_info}/METADATA"
printf 'bootstrap\n' > "${cmru_dist_info}/INSTALLER"
: > "${cmru_dist_info}/RECORD"

echo "[INFO] Building the standalone CMRU wheel from ${project_dir}" >&2
(
    cd "${project_dir}"
    # BG-10: cmru imports cli_extended (the verified released wheel unpacked
    # above) and worktree (a sibling library root); a fresh host has neither
    # installed. Run with -s so a stale user-site copy can never shadow the
    # checkout, and pin the epoch to the HEAD commit so the bootstrap wheel is
    # reproducible.
    export PYTHONPATH="${project_dir}/src:${cli_extended_stage}/site:${repo_root}/libraries/worktree/src${PYTHONPATH:+:${PYTHONPATH}}"
    SOURCE_DATE_EPOCH="$(git -C "${repo_root}" log -1 --format=%ct)" || {
        echo "[ERROR] could not read the HEAD commit time for SOURCE_DATE_EPOCH" >&2
        exit 2
    }
    export SOURCE_DATE_EPOCH
    export CMRU_WHEEL_BUILDER_IMAGE="${builder_image}"
    export CMRU_DOCKER_CGROUP_PARENT="${cgroup_parent}"
    exec "${python_bin}" -s -m cmru.handlers wheel-build --cwd .
)

shopt -s nullglob
wheels=("${project_dir}/dist"/cmru-*.whl)
if (( ${#wheels[@]} != 1 )); then
    echo "[ERROR] expected exactly one CMRU wheel in ${project_dir}/dist; found ${#wheels[@]}" >&2
    exit 1
fi

cp -f "${cli_extended_wheel}" "${project_dir}/dist/"
cli_extended_installed="${project_dir}/dist/$(basename "${cli_extended_wheel}")"

echo "" >&2
echo "[INFO] Built: ${wheels[0]}" >&2
echo "[INFO] Verified cli-extended wheel kept beside it: ${cli_extended_installed}" >&2
echo "" >&2
echo "[INFO] cmru requires cli-extended (a release asset, never on PyPI): install both" >&2
echo "       wheels offline, cli-extended first. Install into an isolated environment with:" >&2
echo "       cd ${repo_root}" >&2
echo "       python3 -m venv .venv-cmru" >&2
echo "       .venv-cmru/bin/python -m pip install --no-index --no-deps ${cli_extended_installed} ${wheels[0]}" >&2
echo "       export PATH=\"${repo_root}/.venv-cmru/bin:\$PATH\"" >&2
echo "" >&2
echo "[INFO] Install the wheels directly into \`.venv/bin/cmru\`:" >&2
echo "       python -m pip install --no-index --no-deps ${cli_extended_installed} ${wheels[0]}" >&2
