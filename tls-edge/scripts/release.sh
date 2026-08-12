#!/usr/bin/env bash
# ─── scripts/release.sh — explicit CMRU release entry point ──────────────────
#
# This has no private tag/build/publish implementation.  It preserves the
# familiar project-local command while delegating the complete source-first
# transaction to the installed CMRU package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TLS_EDGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$#" -ne 1 ]]; then
    echo "Usage: $0 <version>  (for example: $0 0.2.0)" >&2
    exit 2
fi

version="${1#v}"
exec cmru release --config "$TLS_EDGE_ROOT/cmru.toml" --project tls-edge --set-version "$version"
