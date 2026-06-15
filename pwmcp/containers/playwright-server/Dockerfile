# pwmcp playwright-server
# =======================
# Official Playwright image (browsers + OS deps) + the matching playwright JS
# package, so `playwright run-server` runs OFFLINE. The base image ships only
# the browser binaries under /ms-playwright, not the JS package, so a plain
# `npx playwright run-server` would fetch it from npm at runtime — which we must
# avoid in a hardened, egress-restricted container.
#
# PLAYWRIGHT_VERSION MUST match the base image tag AND the version connect()
# consumers install (`pip install playwright==<PLAYWRIGHT_VERSION>`).
ARG PLAYWRIGHT_VERSION=1.60.0
ARG PLAYWRIGHT_DISTRO=noble
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}
ARG PLAYWRIGHT_VERSION

LABEL org.opencontainers.image.title="pwmcp-playwright" \
      org.opencontainers.image.description="Playwright run-server (official base + pinned playwright JS pkg) for PWMCP Playwright-as-a-Service" \
      org.opencontainers.image.source="https://github.com/volkb79-2/vbpub" \
      org.opencontainers.image.documentation="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"

# Browsers are already present at $PLAYWRIGHT_BROWSERS_PATH for this exact
# version, so skip the postinstall browser download entirely.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm install -g "playwright@${PLAYWRIGHT_VERSION}" \
 && npm cache clean --force

# Run as the non-root uid shipped by the base image (compose may override).
USER 1000:1000

ENTRYPOINT ["playwright"]
CMD ["run-server", "--port", "3000", "--host", "0.0.0.0"]
