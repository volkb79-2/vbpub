# pwmcp unified image
# ====================
# Bundles BOTH the native Playwright run-server (connect() WS, port 3000) AND
# the @playwright/mcp HTTP/SSE server (MCP surface, port 8931) into a single
# container under a single hostname.
#
# Consumers reach both endpoints from any sibling container on the shared
# Docker network using ONE service alias "pwmcp":
#   ws://pwmcp:3000/          — Playwright connect() (full API, test suites)
#   http://pwmcp:8931/mcp     — MCP (AI clients, VS Code Copilot, etc.)
#
# PLAYWRIGHT_VERSION MUST match the base image tag AND the version connect()
# consumers install (`pip install playwright==<PLAYWRIGHT_VERSION>`).
ARG PLAYWRIGHT_VERSION=1.61.0
ARG PLAYWRIGHT_DISTRO=noble
# Base image digest pinned for reproducibility (resolved via
# 'docker buildx imagetools inspect mcr.microsoft.com/playwright:v<ver>-<distro>').
# Multi-arch manifest digest (linux/amd64 + linux/arm64):
#   mcr.microsoft.com/playwright:v1.61.0-noble@sha256:57b65fdc9ceabe0ef613124c7bbe2babcf9362c4d85e382fe3b03604e84b428a
# Update this digest whenever PLAYWRIGHT_VERSION or PLAYWRIGHT_DISTRO changes.
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}
ARG PLAYWRIGHT_VERSION

LABEL org.opencontainers.image.title="pwmcp" \
      org.opencontainers.image.description="Unified Playwright run-server + @playwright/mcp, single hostname" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"

# Browsers are already present at $PLAYWRIGHT_BROWSERS_PATH for this exact
# version, so skip the postinstall browser download entirely.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Default allowed-hosts for standalone usage (no ciu compose override).
# ciu compose will inject the deployment-specific names via this env var.
ENV PWMCP_MCP_ALLOWED_HOSTS="localhost:8931,127.0.0.1:8931"

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
RUN npm install -g "playwright@${PLAYWRIGHT_VERSION}" "@playwright/mcp@0.0.76" \
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
