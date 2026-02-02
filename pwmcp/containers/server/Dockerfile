FROM python:3.14-slim-trixie

ARG OCI_TITLE="pwmcp-server"
ARG OCI_DESCRIPTION="PWMCP server (WebSocket + MCP + health endpoints) with Playwright automation."
ARG OCI_SOURCE="https://github.com/volkb79-2/vbpub"
ARG OCI_DOCUMENTATION="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
ARG OCI_URL="https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
ARG OCI_LICENSES="MIT"
ARG OCI_VENDOR="volkb79-2"
ARG OCI_VERSION="latest"
ARG OCI_REVISION="unknown"
ARG OCI_CREATED="unknown"

LABEL org.opencontainers.image.title=$OCI_TITLE \
      org.opencontainers.image.description=$OCI_DESCRIPTION \
      org.opencontainers.image.source=$OCI_SOURCE \
      org.opencontainers.image.documentation=$OCI_DOCUMENTATION \
      org.opencontainers.image.url=$OCI_URL \
      org.opencontainers.image.licenses=$OCI_LICENSES \
      org.opencontainers.image.vendor=$OCI_VENDOR \
      org.opencontainers.image.version=$OCI_VERSION \
      org.opencontainers.image.revision=$OCI_REVISION \
      org.opencontainers.image.created=$OCI_CREATED

ARG PWMCP_USER="vscode"
ARG PWMCP_UID="1000"
ARG PWMCP_GID="1000"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-noto-color-emoji \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if ! getent group "${PWMCP_GID}" >/dev/null; then groupadd -g "${PWMCP_GID}" "${PWMCP_USER}"; fi; \
    if ! id -u "${PWMCP_USER}" >/dev/null 2>&1; then useradd -m -u "${PWMCP_UID}" -g "${PWMCP_GID}" "${PWMCP_USER}"; fi

COPY dist/shared/*.whl /tmp/wheels/
COPY dist/server/*.whl /tmp/wheels/
COPY containers/server/manifest.sh /tmp/manifest.sh

RUN pip install --upgrade pip && \
    pip install --no-cache-dir /tmp/wheels/pwmcp_shared-*.whl /tmp/wheels/pwmcp_server-*.whl && \
    python -m playwright install --with-deps && \
    bash /tmp/manifest.sh

RUN mkdir -p /workspaces/artifacts /certs && \
    chmod 777 /workspaces /workspaces/artifacts /certs /ms-playwright && \
    chmod -R 777 /ms-playwright

EXPOSE 3000 8765 8081

VOLUME ["/workspaces", "/certs"]

USER ${PWMCP_USER}

CMD ["pwmcp-server"]
