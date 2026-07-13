# pwmcp unified image
# ====================
# Bundles the native Playwright run-server (connect() WS, port 3000),
# @playwright/mcp HTTP/SSE server (MCP surface, port 8931), AND
# chrome-devtools-mcp (CDP-based profiling, port 8932 via mcp-proxy proxy) into
# a single container under a single hostname.
#
# Consumers reach all endpoints from any sibling container on the shared
# Docker network using ONE service alias "pwmcp":
#   ws://pwmcp:3000/              — Playwright connect() (full API, test suites)
#   http://pwmcp:8931/mcp         — @playwright/mcp (AI clients, VS Code Copilot, etc.)
#   http://pwmcp:8932/mcp         — chrome-devtools-mcp (performance tracing, CDP tools)
#
# PLAYWRIGHT_VERSION MUST match the base image tag AND the version connect()
# consumers install (`pip install playwright==<PLAYWRIGHT_VERSION>`).
ARG PLAYWRIGHT_VERSION=1.61.0
ARG PLAYWRIGHT_DISTRO=noble

# Pin @playwright/mcp (templated ARG, was hardcoded 0.0.76).
ARG PLAYWRIGHT_MCP_VERSION=0.0.76

# Pin chrome-devtools-mcp (stdio-only CDP MCP server).
ARG CHROME_DEVTOOLS_MCP_VERSION=1.5.0

# Pin mcp-proxy (stdio→streamable-HTTP proxy for chrome-devtools-mcp).
ARG MCP_PROXY_VERSION=6.5.2
# Base image digest pinned for reproducibility (resolved via
# 'docker buildx imagetools inspect mcr.microsoft.com/playwright:v<ver>-<distro>').
# Multi-arch manifest digest (linux/amd64 + linux/arm64):
#   mcr.microsoft.com/playwright:v1.61.0-noble@sha256:57b65fdc9ceabe0ef613124c7bbe2babcf9362c4d85e382fe3b03604e84b428a
# Update this digest whenever PLAYWRIGHT_VERSION or PLAYWRIGHT_DISTRO changes.
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}
ARG PLAYWRIGHT_VERSION
ARG PLAYWRIGHT_MCP_VERSION
ARG CHROME_DEVTOOLS_MCP_VERSION
ARG MCP_PROXY_VERSION

LABEL org.opencontainers.image.title="pwmcp" \
      org.opencontainers.image.description="Unified Playwright run-server + @playwright/mcp + chrome-devtools-mcp, single hostname" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"

# Browsers are already present at $PLAYWRIGHT_BROWSERS_PATH for this exact
# version, so skip the postinstall browser download entirely.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Default allowed-hosts for standalone usage (no ciu compose override).
# ciu compose will inject the deployment-specific names via this env var.
ENV PWMCP_MCP_ALLOWED_HOSTS="localhost:8931,127.0.0.1:8931"

# Default allowed-hosts for chrome-devtools-mcp (via mcp-proxy, port 8932).
# mcp-proxy does not have a native --allowed-hosts flag; this env var tracks
# the expected hosts for documentation / external-mode Traefik rules.
ENV PWMCP_DEVTOOLS_ALLOWED_HOSTS="localhost:8932,127.0.0.1:8932"

# Run-as-uid–agnostic cache/home. The image's default USER is 1000:1000 (home
# /home/ubuntu, writable by 1000), but consumers commonly OVERRIDE `user:` to a
# hardened uid (e.g. 10001 + cap_drop=ALL). For that uid the base image's home is
# owned by 1000 mode 0750 → unwritable, and @playwright/mcp — which writes its
# per-session browser profile under `XDG_CACHE_HOME || os.homedir()/.cache` —
# fails to launch chromium with EACCES on ~/.cache/ms-playwright-mcp.
# Pin HOME + the XDG cache base to world-writable /tmp (mode 1777) so the image
# works under ANY run-as uid without the consumer needing a compose-level override.
# (Same /tmp-as-writable-runtime pattern already used for supervisord above.)
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

# Install playwright JS package (run-server needs it; base image ships only
# browser binaries), @playwright/mcp (MCP HTTP/SSE server), and supervisord
# (PID-1 process manager that reaps children and forwards SIGTERM).
#
# @playwright/mcp is pinned to 0.0.76 (bundles playwright-core for chromium-1226,
# verified to work with playwright 1.61.0 base images). Track upstream and update
# this pin when bumping PLAYWRIGHT_VERSION — check the @playwright/mcp changelog.
#
# TODO(repro): pin supervisor=<ver> — determine the exact apt package version for
# ubuntu noble (24.04) by running: apt-cache show supervisor | grep Version
RUN npm install -g "playwright@${PLAYWRIGHT_VERSION}" "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" "chrome-devtools-mcp@${CHROME_DEVTOOLS_MCP_VERSION}" "mcp-proxy@${MCP_PROXY_VERSION}" \
 && npm cache clean --force \
 && apt-get update \
 && apt-get install -y --no-install-recommends supervisor \
 && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Resolve the chromium binary path from the globally-installed playwright JS
# package and write it to a file read by the entrypoint. @playwright/mcp bundles
# its own playwright-core which may target a different chromium revision than the
# one installed in the base image. Using --executable-path bypasses the bundled
# chromium discovery and points directly at the binary that is actually present.
RUN node -e "const {chromium}=require('/usr/lib/node_modules/playwright');process.stdout.write(chromium.executablePath())" \
    > /etc/pwmcp-chromium-path.txt

# supervisord needs a writable runtime dir when running as uid 1000.
# Use /tmp-based paths (already world-writable) instead of /var/run.
RUN mkdir -p /var/log/supervisor \
 && chmod 1777 /var/log/supervisor

COPY containers/pwmcp/supervisord.conf /etc/supervisor/conf.d/pwmcp.conf
COPY containers/pwmcp/entrypoint.sh /usr/local/bin/pwmcp-entrypoint.sh
RUN chmod +x /usr/local/bin/pwmcp-entrypoint.sh

# Run as the non-root uid shipped by the base image (compose may override).
USER 1000:1000

# The entrypoint script exports PWMCP_CHROMIUM_PATH then execs supervisord.
# supervisord is PID 1 — it reaps children and forwards SIGTERM to both programs.
ENTRYPOINT ["/usr/local/bin/pwmcp-entrypoint.sh"]
