#!/bin/bash
# =============================================================================
# Voice2Text-AI Workspace Environment Setup (Wrapper)
# =============================================================================
# Generates .env.ciu for the voice2text-ai subtree by delegating to the
# repository-wide env-workspace-setup-generate.sh with an overridden workspace.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

export WORKSPACE_DIR="$REPO_ROOT"
export REPO_ROOT

bash "${SCRIPT_DIR}/../../env-workspace-setup-generate.sh"
