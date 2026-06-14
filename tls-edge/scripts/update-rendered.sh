#!/usr/bin/env bash
# Maintainer tool: regenerate the COMMITTED default-mode artifacts in
# edge-proxy/ from the ciu-stack/ templates (ignoring any local ciu.toml.j2).
# Run after every template change and commit the result; scripts/verify.sh
# fails on drift between templates and committed artifacts.
set -euo pipefail
here="$(dirname "$(readlink -f "$0")")"

"$here/render.sh" --defaults-only
echo
echo "Done. Review and commit:  git -C $(dirname "$here") diff edge-proxy/"
