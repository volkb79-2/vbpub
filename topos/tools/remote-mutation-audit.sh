#!/usr/bin/env bash
# Start a commit-addressed Topos mutation audit on a dedicated remote host.
set -euo pipefail
root=$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --show-toplevel)
exec "$root/nyxloom/tools/remote-mutation-audit-host.sh" \
  --manifest-rel topos/tools/remote-mutation-audit.toml "$@"
