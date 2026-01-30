#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

log_info() {
    echo "[INFO] $*"
}

log_success() {
    echo "[SUCCESS] $*"
}

log_error() {
    echo "[ERROR] $*" >&2
}

CIU_ROOT="$REPO_ROOT/ciu"
VSC_ROOT="$REPO_ROOT/vsc-devcontainer"
MCP_ROOT="$REPO_ROOT/playwright-mcp"

if [[ ! -d "$CIU_ROOT" ]]; then
    log_error "Missing CIU directory: $CIU_ROOT"
    exit 1
fi

if [[ ! -d "$VSC_ROOT" ]]; then
    log_error "Missing vsc-devcontainer directory: $VSC_ROOT"
    exit 1
fi

if [[ ! -d "$MCP_ROOT" ]]; then
    log_error "Missing playwright-mcp directory: $MCP_ROOT"
    exit 1
fi

log_info "CIU: run tests"
bash "$CIU_ROOT/run-ciu-tests.sh"

log_info "CIU: publish wheel and validate latest"
bash "$CIU_ROOT/publish-and-validate.sh"

log_info "vsc-devcontainer: build images"
bash "$VSC_ROOT/build-images.sh"

log_info "vsc-devcontainer: push images"
bash "$VSC_ROOT/push-images.sh"

log_info "playwright-mcp-client: run tests"
bash "$MCP_ROOT/run-client-tests.sh"
log_info "playwright-mcp-client: build wheel"
bash "$MCP_ROOT/build-client-wheel.sh"
log_info "playwright-mcp-client: publish wheel and validate latest"
bash "$MCP_ROOT/publish-client-wheel.sh"
log_info "playwright-mcp: build images"
bash "$MCP_ROOT/build-images.sh"

log_info "playwright-mcp: push images"
bash "$MCP_ROOT/push-images.sh"

log_success "All artifacts built and pushed"