#!/usr/bin/env bash
# =============================================================================
# linux-desktop-usb.sh
#
# USB-stick launcher for linux-desktop-analysis.py
#
# Downloads the latest version of linux-desktop-analysis.py from the public
# GitHub repository and executes it locally.  Suitable for running directly
# from a USB stick without a full clone of the repository.
#
# Usage:
#   ./linux-desktop-analysis-usb.sh [-- ARGS...]
#
# Any arguments after "--" are forwarded to the Python script, e.g.:
#   ./linux-desktop-analysis-usb.sh -- --scale 2 --non-interactive
#
# Requirements:
#   - bash 4+
#   - python3
#   - curl or wget
# =============================================================================

set -euo pipefail

REPO="volkb79-2/vbpub"
BRANCH="main"
SCRIPT_PATH="scripts/linux-desktop-analysis.py"
RAW_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/${SCRIPT_PATH}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '\033[1;34m[*]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[✓]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

# ---------------------------------------------------------------------------
# Parse arguments — collect anything after "--" to pass to the script
# ---------------------------------------------------------------------------

SCRIPT_ARGS=()
forwarding=0
for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        forwarding=1
        continue
    fi
    if [[ "$forwarding" -eq 1 ]]; then
        SCRIPT_ARGS+=("$arg")
    fi
done

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

require_cmd python3

if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    die "Neither curl nor wget is available. Install one and retry."
fi

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

TMP_SCRIPT="$(mktemp /tmp/linux-desktop-analysis-XXXXXX.py)"
# Ensure cleanup on exit
trap 'rm -f "$TMP_SCRIPT"' EXIT INT TERM

info "Downloading linux-desktop-analysis.py from ${REPO} (branch: ${BRANCH})..."

if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location --retry 3 \
        "$RAW_URL" -o "$TMP_SCRIPT" || die "Download failed (curl). URL: $RAW_URL"
else
    wget --quiet --tries=3 "$RAW_URL" -O "$TMP_SCRIPT" || die "Download failed (wget). URL: $RAW_URL"
fi

ok "Downloaded to ${TMP_SCRIPT}"

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

info "Running linux-desktop-analysis.py${SCRIPT_ARGS[*]:+ with args: ${SCRIPT_ARGS[*]}}..."
python3 "$TMP_SCRIPT" "${SCRIPT_ARGS[@]+"${SCRIPT_ARGS[@]}"}"
