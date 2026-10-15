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

# cmru depends on the cli-extended WHEEL (KI-51 / CX-D1: never vendored, never from
# an index; normally the RELEASED one, or the explicit zero-release source build
# below). `python -m cmru.handlers` imports it, so the bootstrap
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
#
# CMRU_BOOTSTRAP_CLI_EXTENDED selects the source EXPLICITLY (default `release`):
#   release        the paths above. With no release/pointer and no wheel+digest this
#                  FAILS (exit 2) and names the zero-release path; it never falls
#                  back to a source build, because a silent switch would hide an
#                  availability or supply-chain problem.
#   source         ZERO-RELEASE PATH. Builds the wheel from this checkout's
#                  cli-extended library with cmru's own wheel builder
#                  (`cmru.handlers wheel-build`, the wheel-builder image; for that
#                  one build step only, the library source is on PYTHONPATH),
#                  version CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION (default
#                  <floor>+bootstrap.source; the +local segment keeps it distinct
#                  from any release), records its sha256 in this log and stages it
#                  exactly like a released wheel.
#   <wheel-path>   a prebuilt wheel; CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256 is required.
# See docs/BOOTSTRAP-FROM-ZERO.md.
cli_extended_mode="${CMRU_BOOTSTRAP_CLI_EXTENDED:-release}"
cli_extended_wheel="${CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL:-}"
if [[ "${cli_extended_mode}" != "release" && "${cli_extended_mode}" != "source" ]]; then
    if [[ -n "${cli_extended_wheel}" ]]; then
        echo "[ERROR] CMRU_BOOTSTRAP_CLI_EXTENDED=<wheel-path> and CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL are both set; use one" >&2
        exit 2
    fi
    cli_extended_wheel="${cli_extended_mode}"
    cli_extended_mode="wheel"
fi
if [[ "${cli_extended_mode}" == "source" ]]; then
    if [[ -n "${cli_extended_wheel}" ]]; then
        echo "[ERROR] CMRU_BOOTSTRAP_CLI_EXTENDED=source cannot be combined with CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL" >&2
        exit 2
    fi
    # Built below, once the staged cmru identity exists (the builder is cmru's own).
    cli_extended_source_version="${CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION:-${cli_extended_floor}+bootstrap.source}"
    if [[ ! "${cli_extended_source_version}" =~ ^[0-9][0-9.]*\+[0-9A-Za-z.]+$ ]]; then
        echo "[ERROR] CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION=${cli_extended_source_version} must be X.Y.Z+local (a source build is never version-identical to a release)" >&2
        exit 2
    fi
elif [[ -n "${cli_extended_wheel}" ]]; then
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
        echo "[ERROR] could not fetch the released cli-extended wheel; supply CMRU_BOOTSTRAP_CLI_EXTENDED_WHEEL and CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256," >&2
        echo "        or, when NO cli-extended release exists yet (zero-release estate), set CMRU_BOOTSTRAP_CLI_EXTENDED=source (see docs/BOOTSTRAP-FROM-ZERO.md)" >&2
        exit 2
    }
fi
unpack_cli_extended() {
    "${python_bin}" -s -m zipfile -e "${cli_extended_wheel}" "${cli_extended_stage}/site" || {
        echo "[ERROR] could not unpack ${cli_extended_wheel}" >&2
        exit 2
    }
}
if [[ "${cli_extended_mode}" != "source" ]]; then
    unpack_cli_extended
fi

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

# Host placement is proven BEFORE any Docker/Buildx workload starts. systemd
# auto-vivifies an unlimited transient slice for a typo'd name and Docker then
# starts the build there without complaint. This is CMRU's own host-side probe
# contract (LoadState=loaded + non-empty FragmentPath, read from the Docker
# HOST's systemd by a pinned privileged probe container; never a local
# systemctl): the same check `cmru handler wheel-build` repeats for each build.
# It needs the staged cli-extended + cmru identity above, which is why the
# builder image is built only after them. Probe image and probe placement come
# from CMRU_TESTER_CGROUP_PROBE_IMAGE / CMRU_TESTER_CGROUP_PARENT (environment,
# else cmru.orchestration.toml [env]); a missing input or any uncertainty refuses.
verify_pythonpath="${project_dir}/src:${cli_extended_stage}/site:${repo_root}/libraries/worktree/src"
if [[ "${cli_extended_mode}" == "source" ]]; then
    # Zero-release path only: cli-extended does not exist as a wheel yet.
    verify_pythonpath="${verify_pythonpath}:${repo_root}/libraries/cli-extended/src"
fi
PYTHONPATH="${verify_pythonpath}" "${python_bin}" -s -c '
import sys
from pathlib import Path
from cmru import handlers
handlers.require_installed_cgroup_parent(sys.argv[1], Path(sys.argv[2]))
' "${cgroup_parent}" "${project_dir}" || {
    echo "[ERROR] cgroup parent ${cgroup_parent} is not a verified host slice; no build was started" >&2
    exit 2
}

builder_kind=""
if docker image inspect "${builder_image}" >/dev/null 2>&1; then
    builder_kind="$(docker image inspect \
        --format '{{index .Config.Labels "org.vbpub.wheel-builder.kind"}}' \
        "${builder_image}" 2>/dev/null || true)"
fi
if [[ "${builder_kind}" != "python-wheel-builder" ]]; then
    echo "[INFO] Building ${builder_image} from wheel-builder/Dockerfile (image label is ${builder_kind:-missing})" >&2
    docker buildx build --load --pull \
        --cgroup-parent "${cgroup_parent}" \
        -f "${repo_root}/wheel-builder/Dockerfile" \
        -t "${builder_image}" \
        "${repo_root}"
    builder_kind="$(docker image inspect \
        --format '{{index .Config.Labels "org.vbpub.wheel-builder.kind"}}' \
        "${builder_image}" 2>/dev/null)" || {
        echo "[ERROR] cannot verify the rebuilt wheel-builder image ${builder_image}" >&2
        exit 2
    }
    if [[ "${builder_kind}" != "python-wheel-builder" ]]; then
        echo "[ERROR] rebuilt image ${builder_image} has unexpected wheel-builder label ${builder_kind:-missing}" >&2
        exit 2
    fi
fi

# Zero-release path (CMRU_BOOTSTRAP_CLI_EXTENDED=source): build cli-extended with
# the SAME builder every estate wheel uses (`cmru.handlers wheel-build`, i.e. the
# wheel-builder image). cmru can only run from this checkout, so for THIS build
# step ONLY the library checkout's source is on PYTHONPATH (as before KI-51);
# the cmru wheel build below uses the staged wheel like any other mode. The
# version is injected via setuptools-scm's pretend variable (the handler forwards
# it into the container) because no cli-extended-v* tag exists yet.
if [[ "${cli_extended_mode}" == "source" ]]; then
    cli_extended_library="${repo_root}/libraries/cli-extended"
    echo "[INFO] cli-extended: building from the checkout with cmru's wheel builder (source mode, version ${cli_extended_source_version})" >&2
    (
        cd "${project_dir}"
        export PYTHONPATH="${project_dir}/src:${cli_extended_stage}/site:${repo_root}/libraries/worktree/src:${cli_extended_library}/src"
        SOURCE_DATE_EPOCH="$(git -C "${repo_root}" log -1 --format=%ct)" || {
            echo "[ERROR] could not read the HEAD commit time for SOURCE_DATE_EPOCH" >&2
            exit 2
        }
        export SOURCE_DATE_EPOCH
        export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED="${cli_extended_source_version}"
        export CMRU_WHEEL_BUILDER_IMAGE="${builder_image}"
        export CMRU_DOCKER_CGROUP_PARENT="${cgroup_parent}"
        "${python_bin}" -s -m cmru.handlers wheel-build --cwd "${cli_extended_library}"
    ) || {
        echo "[ERROR] could not build cli-extended from source with cmru's wheel builder" >&2
        exit 2
    }
    shopt -s nullglob
    source_wheels=("${cli_extended_library}/dist"/cli_extended-*.whl)
    shopt -u nullglob
    if (( ${#source_wheels[@]} != 1 )); then
        echo "[ERROR] expected exactly one cli-extended wheel in ${cli_extended_library}/dist; found ${#source_wheels[@]}" >&2
        exit 2
    fi
    cli_extended_wheel="${source_wheels[0]}"
    source_sha="$("${python_bin}" -s -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${cli_extended_wheel}")" || {
        echo "[ERROR] cannot read ${cli_extended_wheel}" >&2
        exit 2
    }
    echo "[INFO] cli-extended source wheel sha256=${source_sha} file=$(basename "${cli_extended_wheel}")" >&2
    unpack_cli_extended
fi

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

sha256_of() {
    "${python_bin}" -s -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}
cmru_wheel_sha="$(sha256_of "${wheels[0]}")" || { echo "[ERROR] cannot read ${wheels[0]}" >&2; exit 2; }
cli_extended_sha_installed="$(sha256_of "${cli_extended_installed}")" || { echo "[ERROR] cannot read ${cli_extended_installed}" >&2; exit 2; }

# The exact artifacts this run produced, as a sourceable record. A later step
# (install, tester-image build) reads the paths and digests from HERE instead of
# re-typing a wheel name or a digest: docs/BOOTSTRAP-FROM-ZERO.md sources it.
identity_file="${project_dir}/dist/bootstrap-artifacts.env"
{
    printf 'BOOTSTRAP_CMRU_WHEEL=%q\n' "${wheels[0]}"
    printf 'BOOTSTRAP_CMRU_WHEEL_SHA256=%q\n' "${cmru_wheel_sha}"
    printf 'BOOTSTRAP_CLI_EXTENDED_WHEEL=%q\n' "${cli_extended_installed}"
    printf 'BOOTSTRAP_CLI_EXTENDED_WHEEL_SHA256=%q\n' "${cli_extended_sha_installed}"
} > "${identity_file}"

echo "" >&2
echo "[INFO] Built: ${wheels[0]} sha256=${cmru_wheel_sha}" >&2
echo "[INFO] Verified cli-extended wheel kept beside it: ${cli_extended_installed} sha256=${cli_extended_sha_installed}" >&2
echo "[INFO] Exact artifact paths and digests: ${identity_file}" >&2
echo "" >&2
echo "[INFO] cmru requires cli-extended (a release asset, never on PyPI): install both" >&2
echo "       wheels offline, cli-extended first, into the Python environment you select" >&2
echo "       (no cmru command exists until this is done):" >&2
echo "       . ${identity_file}" >&2
echo "       <python-of-the-chosen-environment> -m pip install --no-index --no-deps \\" >&2
echo "           \"\$BOOTSTRAP_CLI_EXTENDED_WHEEL\" \"\$BOOTSTRAP_CMRU_WHEEL\"" >&2
