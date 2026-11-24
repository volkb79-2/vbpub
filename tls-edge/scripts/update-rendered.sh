#!/usr/bin/env bash
# Maintainer/dev helper: regenerate the ignored default-mode trial tree under
# edge-proxy/ from the ciu-stack/ templates. This output is never committed.
set -euo pipefail
here="$(dirname "$(readlink -f "$0")")"

"$here/render.sh" --defaults-only --out "$here/../edge-proxy"
echo
echo "Done. edge-proxy/ is ignored trial output; do not commit it."
