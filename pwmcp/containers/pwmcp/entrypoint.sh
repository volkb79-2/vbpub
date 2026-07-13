#!/bin/sh
# pwmcp unified container entrypoint
# Resolves the chromium binary path at runtime and exports it for supervisord.
# This is needed because @playwright/mcp bundles its own playwright-core which
# may resolve a different chromium revision than the one in the base image.
#
# browser_mode plumbing (P03): PWMCP_BROWSER_MODE selects which supervisord
# config is used — "per-session" (default, byte-identical pre-P03 behavior:
# each MCP server launches its own browser) or "shared" (one persistent
# Chromium the MCP servers attach to over CDP). This selection happens HERE,
# at entrypoint time, not via divergent Dockerfiles — both config files are
# baked into every image; only the runtime choice differs.
#
# Strict validation: an unknown PWMCP_BROWSER_MODE value is a fatal error,
# not a silent fallback to a default.

set -e

# Read the chromium path baked into the image during build
if [ -f /etc/pwmcp-chromium-path.txt ]; then
  PWMCP_CHROMIUM_PATH="$(cat /etc/pwmcp-chromium-path.txt)"
  export PWMCP_CHROMIUM_PATH
fi

PWMCP_BROWSER_MODE="${PWMCP_BROWSER_MODE:-per-session}"

case "$PWMCP_BROWSER_MODE" in
  per-session)
    SUPERVISOR_CONF=/etc/supervisor/conf.d/pwmcp.conf
    ;;
  shared)
    SUPERVISOR_CONF=/etc/supervisor/conf.d/pwmcp-shared.conf
    # Defaults for shared-mode-only env vars, mirroring the ENV defaults set
    # in the Dockerfile for the per-session vars. ciu compose overrides these.
    : "${PWMCP_ADMIN_PORT:=8939}"
    : "${PWMCP_CDP_PORT:=9222}"
    : "${PWMCP_BROWSER_MAX_IDLE_S:=0}"
    export PWMCP_ADMIN_PORT PWMCP_CDP_PORT PWMCP_BROWSER_MAX_IDLE_S

    # Strict validation — reject nonsense values fatally, no silent clamp.
    case "$PWMCP_BROWSER_MAX_IDLE_S" in
      ''|*[!0-9]*)
        echo "FATAL: PWMCP_BROWSER_MAX_IDLE_S must be a non-negative integer (got: '${PWMCP_BROWSER_MAX_IDLE_S}')" >&2
        exit 1
        ;;
    esac
    case "$PWMCP_ADMIN_PORT" in
      ''|*[!0-9]*)
        echo "FATAL: PWMCP_ADMIN_PORT must be a positive integer (got: '${PWMCP_ADMIN_PORT}')" >&2
        exit 1
        ;;
    esac
    if [ "$PWMCP_ADMIN_PORT" -lt 1 ] || [ "$PWMCP_ADMIN_PORT" -gt 65535 ]; then
      echo "FATAL: PWMCP_ADMIN_PORT out of range 1-65535 (got: '${PWMCP_ADMIN_PORT}')" >&2
      exit 1
    fi
    ;;
  *)
    echo "FATAL: unknown PWMCP_BROWSER_MODE '${PWMCP_BROWSER_MODE}' (expected 'per-session' or 'shared')" >&2
    exit 1
    ;;
esac

exec supervisord -c "$SUPERVISOR_CONF" --nodaemon "$@"
