# Playwright MCP Standalone Service
FROM python:3.14-slim-trixie

ARG OCI_TITLE="playwright-mcp"
ARG OCI_DESCRIPTION="Standalone Playwright MCP + WebSocket service for multi-project browser automation."
ARG OCI_SOURCE="https://github.com/volkb79-2/vbpub"
ARG OCI_DOCUMENTATION="https://github.com/volkb79-2/vbpub/tree/main/playwright-mcp"
ARG OCI_URL="https://github.com/volkb79-2/vbpub/tree/main/playwright-mcp"
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

# If you want to switch to OS-supplied Chromium instead of Playwright-managed browsers:
# 1) Install chromium from apt and set executable path.
# 2) Skip Playwright browser download and keep install-deps.
#
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#         ca-certificates \
#         chromium \
#         curl \
#         fonts-noto-color-emoji \
#         netcat-openbsd \
#     && rm -rf /var/lib/apt/lists/*
#
# ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
#     PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium

COPY requirements.root.txt ./

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.root.txt && \
    python -m playwright install --with-deps

RUN mkdir -p /screenshots /workspaces /certs && \
    chmod 777 /screenshots /workspaces /certs /ms-playwright && \
    chmod -R 777 /ms-playwright

COPY mcp_server.py /app/mcp_server.py
COPY ws_server.py /app/ws_server.py
COPY start_services.py /app/start_services.py
COPY health_server.py /app/health_server.py

EXPOSE 3000 8765

VOLUME ["/screenshots", "/workspaces", "/certs"]

CMD ["python3", "/app/start_services.py"]
