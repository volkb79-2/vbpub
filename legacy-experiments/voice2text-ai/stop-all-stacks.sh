#!/bin/bash
# ============================================================================
# Voice2Text AI - Multi-Stack Shutdown Script
# ============================================================================
# Stops all voice2text-ai stacks in reverse dependency order
#
# Usage:
#   ./stop-all-stacks.sh [--remove-network] [--yes]
#
# Options:
#   --remove-network    Remove shared network after stopping
#   --yes, -y          Non-interactive mode (auto-answer yes)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Parse command line arguments
REMOVE_NETWORK=false
NON_INTERACTIVE=false

#!/bin/bash
# =============================================================================
# Voice2Text AI - Multi-Stack Shutdown (ciu-deploy)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/.env.ciu" ]]; then
    echo "[ERROR] .env.ciu missing in ${SCRIPT_DIR}" >&2
  echo "[ERROR] Run: bash ${SCRIPT_DIR}/env-workspace-setup-generate.sh" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/../.python3 scripts/ciu/ciu-deploy.py" \
  --root-folder "${SCRIPT_DIR}" \
  --stop
    log_step "Stopping stack: $stack_name"
    
    if [[ ! -d "$stack_dir" ]]; then
        log_warn "Stack directory not found: $stack_dir"
        return 0
    fi
    
    cd "$stack_dir"
    
    if [[ -f "docker-compose.yml" ]]; then
        docker compose down || log_warn "Failed to stop $stack_name (may already be stopped)"
    else
        log_info "No docker-compose.yml found, skipping $stack_name"
    fi
    
    cd "$SCRIPT_DIR"
    log_success "Stack $stack_name stopped"
}

# Main execution
main() {
    echo "========================================"
    echo "  Voice2Text AI - Multi-Stack Shutdown "
    echo "========================================"
    echo ""
    
    # Stop stacks in reverse order
    log_info "Stopping stacks in reverse dependency order..."
    echo ""
    
    # 4. Reverse Proxy (first to stop)
    stop_stack "reverse-proxy" "reverse-proxy"
    
    # 3. Speech-to-Copilot (web interface)
    stop_stack "speech-to-copilot" "speech-to-copilot"
    
    # 2. Oobabooga-LLM (language model service) - DEACTIVATED
    # Uncomment to re-enable: stop_stack "oobabooga-llm" "oobabooga-llm"
    log_info "Skipping oobabooga-llm (deactivated)"
    
    # 1. Whisper-Trans (transcription service)
    stop_stack "whisper-trans" "whisper-trans"
    
    echo ""
    log_success "All stacks stopped successfully!"
    
    # Optional: remove network
    if [[ "$REMOVE_NETWORK" == "true" ]]; then
        echo ""
        local should_remove=false
        
        if [[ "$NON_INTERACTIVE" == "true" ]]; then
            should_remove=true
            log_info "Auto-removing network (non-interactive mode)"
        else
            read -p "Remove shared network '$NETWORK_NAME'? (y/N) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                should_remove=true
            fi
        fi
        
        if [[ "$should_remove" == "true" ]]; then
            docker network rm "$NETWORK_NAME" 2>/dev/null && \
                log_success "Network removed" || \
                log_info "Network not found or in use"
        fi
    fi
}

# Run main
main "$@"
