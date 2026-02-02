#!/usr/bin/env bash
set -euo pipefail

MANIFEST_PATH="/manifest.txt"

get_pkg_version() {
    local pkg="$1"
    python - <<PY
import importlib.metadata as m
try:
    print(m.version("$pkg"))
except m.PackageNotFoundError:
    print("unknown")
PY
}

get_cli_version() {
    local cmd="$1"
    if command -v "$cmd" >/dev/null 2>&1; then
        "$cmd" --version 2>/dev/null | head -n 1 || "$cmd" -v 2>/dev/null | head -n 1 || true
    else
        echo "not-installed"
    fi
}

PYTHON_VERSION="$(python -V 2>&1)"
PWMCP_CLIENT_VERSION="$(get_pkg_version "pwmcp-client")"
PWMCP_SHARED_VERSION="$(get_pkg_version "pwmcp-shared")"
PIP_VERSION="$(python -m pip --version 2>/dev/null || true)"

cat > "${MANIFEST_PATH}" <<EOF
# PWMCP Client Container Manifest
# Generated at build time

container=pwmcp-client
python=${PYTHON_VERSION}
pip=${PIP_VERSION}

pwmcp-client=${PWMCP_CLIENT_VERSION}
pwmcp-shared=${PWMCP_SHARED_VERSION}

cli.git=$(get_cli_version "git")
cli.jq=$(get_cli_version "jq")
cli.nc=$(get_cli_version "nc")
cli.netcat=$(get_cli_version "netcat")
cli.openssh=$(get_cli_version "ssh")
EOF
