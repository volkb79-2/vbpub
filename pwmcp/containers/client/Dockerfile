FROM python:3.14-slim-trixie

ARG OCI_TITLE="pwmcp-client"
ARG OCI_DESCRIPTION="PWMCP client container with extended Python tooling for test automation across projects."
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
    DEBIAN_FRONTEND=noninteractive

WORKDIR /workspaces

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dnsutils \
        git \
        jq \
        netcat-openbsd \
        openssh-client \
        iputils-ping \
        gcc \
        g++ \
        make \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if ! getent group "${PWMCP_GID}" >/dev/null; then groupadd -g "${PWMCP_GID}" "${PWMCP_USER}"; fi; \
    if ! id -u "${PWMCP_USER}" >/dev/null 2>&1; then useradd -m -u "${PWMCP_UID}" -g "${PWMCP_GID}" "${PWMCP_USER}"; fi; \
    mkdir -p /workspaces; \
    chown -R "${PWMCP_UID}:${PWMCP_GID}" /workspaces

COPY containers/client/requirements.txt /tmp/requirements.txt
COPY containers/client/manifest.sh /tmp/manifest.sh

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

COPY dist/shared/*.whl /tmp/wheels/
COPY dist/client/*.whl /tmp/wheels/

RUN pip install --no-cache-dir /tmp/wheels/pwmcp_shared-*.whl /tmp/wheels/pwmcp_client-*.whl
RUN bash /tmp/manifest.sh

USER ${PWMCP_USER}

CMD ["bash"]
