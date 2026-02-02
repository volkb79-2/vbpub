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
PWMCP_SERVER_VERSION="$(get_pkg_version "pwmcp-server")"
PWMCP_SHARED_VERSION="$(get_pkg_version "pwmcp-shared")"
PLAYWRIGHT_VERSION="$(get_pkg_version "playwright")"
PIP_VERSION="$(python -m pip --version 2>/dev/null || true)"

cat > "${MANIFEST_PATH}" <<EOF
# PWMCP Server Container Manifest
# Generated at build time

container=pwmcp-server
python=${PYTHON_VERSION}
pip=${PIP_VERSION}

pwmcp-server=${PWMCP_SERVER_VERSION}
pwmcp-shared=${PWMCP_SHARED_VERSION}
playwright=${PLAYWRIGHT_VERSION}

cli.curl=$(get_cli_version "curl")
cli.nc=$(get_cli_version "nc")
cli.netcat=$(get_cli_version "netcat")
EOF
