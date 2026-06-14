#!/usr/bin/env bash
# Render the tls-edge templates (standalone, no ciu required).
# Usage: scripts/render.sh [--check] [--stamp] [--certs-only] [--defaults-only]
set -euo pipefail

if ! python3 -c 'import jinja2, tomllib' 2>/dev/null; then
    echo "error: python3 with jinja2 is required." >&2
    echo "  Debian/Ubuntu: sudo apt install python3-jinja2" >&2
    echo "  pip:           pip install --user jinja2" >&2
    exit 3
fi

exec python3 "$(dirname "$(readlink -f "$0")")/render_standalone.py" "$@"
