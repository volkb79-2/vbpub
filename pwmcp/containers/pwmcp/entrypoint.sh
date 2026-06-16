#!/bin/sh
# pwmcp unified container entrypoint
# Resolves the chromium binary path at runtime and exports it for supervisord.
# This is needed because @playwright/mcp bundles its own playwright-core which
# may resolve a different chromium revision than the one in the base image.

set -e

# Read the chromium path baked into the image during build
if [ -f /etc/pwmcp-chromium-path.txt ]; then
  PWMCP_CHROMIUM_PATH="$(cat /etc/pwmcp-chromium-path.txt)"
  export PWMCP_CHROMIUM_PATH
fi

exec supervisord -c /etc/supervisor/conf.d/pwmcp.conf --nodaemon "$@"
