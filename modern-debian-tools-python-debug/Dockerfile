# Devcontainer base image (pre-built)
# Supports multiple Debian + Python variants via build args.


ARG PYTHON_VERSION="3.14"
ARG DEBIAN_VERSION="trixie"
ARG BASE_IMAGE="mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}-${DEBIAN_VERSION}"
# BASE_IMAGE is set by `docker-bake.hcl` during build
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-c"]

ARG PYTHON_VERSION
ARG DEBIAN_VERSION

ARG BACKPORTS_URI
ARG OCI_TITLE=""
ARG OCI_DESCRIPTION=""
ARG OCI_SOURCE=""
ARG OCI_DOCUMENTATION=""
ARG OCI_URL=""
ARG OCI_LICENSES=""
ARG OCI_VENDOR=""
ARG OCI_VERSION=""
ARG OCI_REVISION=""
ARG OCI_CREATED=""
ARG AWSCLI_VERSION
ARG B2_VERSION
ARG BAT_VERSION
ARG CODEX_VERSION
ARG CLAUDE_CODE_VERSION
ARG ANTIGRAVITY_VERSION
ARG AIDER_VERSION
ARG DELTA_VERSION
ARG GH_VERSION
ARG CONSUL_VERSION
ARG FD_VERSION
ARG FZF_VERSION
ARG POSTGRESQL_CLIENT_VERSION
ARG REDIS_TOOLS_VERSION
ARG RIPGREP_VERSION
ARG RGA_VERSION
ARG SHELLCHECK_VERSION
ARG VAULT_VERSION
ARG YQ_VERSION
ARG INSTALL_CODEX="true"
ARG INSTALL_CLAUDE_CODE="true"
ARG INSTALL_ANTIGRAVITY="true"
ARG INSTALL_AIDER="true"
ARG GITHUB_USERNAME
ARG GITHUB_REPO
ARG CIU_WHEEL_TAG
ARG CIU_WHEEL_ASSET_NAME
ARG CIU_INSTALL_REQUIRED="false"
ARG PACKAGE_MANIFEST_SOURCE=""
ARG DEVCONTAINERS_RELEASE=""
ARG DEVCONTAINERS_VERSION=""

ENV DEBIAN_VERSION="${DEBIAN_VERSION}"
ENV BACKPORTS_URI="${BACKPORTS_URI}"

# OCI image metadata (https://github.com/opencontainers/image-spec/blob/main/annotations.md)
LABEL org.opencontainers.image.title="${OCI_TITLE}" \
    org.opencontainers.image.description="${OCI_DESCRIPTION}" \
    org.opencontainers.image.source="${OCI_SOURCE}" \
    org.opencontainers.image.documentation="${OCI_DOCUMENTATION}" \
    org.opencontainers.image.url="${OCI_URL}" \
    org.opencontainers.image.licenses="${OCI_LICENSES}" \
    org.opencontainers.image.vendor="${OCI_VENDOR}" \
    org.opencontainers.image.version="${OCI_VERSION}" \
    org.opencontainers.image.revision="${OCI_REVISION}" \
    org.opencontainers.image.created="${OCI_CREATED}" \
    net.volkb79.base-devcontainers-release="${DEVCONTAINERS_RELEASE}" \
    net.volkb79.base-devcontainers-version="${DEVCONTAINERS_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive

USER root

COPY apt/backports.sources.in /tmp/backports.sources.in
COPY apt/backports.pref.in /tmp/backports.pref.in
COPY package-manifests-versioned /tmp/package-manifests-versioned
COPY scripts/manifest_sections.py /tmp/manifest_sections.py

# Ensure vscode user exists (non-devcontainers bases)
RUN set -euo pipefail; \
    if ! id -u vscode >/dev/null 2>&1; then \
        if ! getent group vscode >/dev/null 2>&1; then \
            groupadd -g 1000 vscode; \
        fi; \
        useradd -m -s /bin/bash -u 1000 -g 1000 vscode; \
    fi

# Core system packages
RUN set -euo pipefail; \
    if [ "${DEBIAN_VERSION}" = "trixie" ]; then \
        NETCAT_PKG="netcat-openbsd"; \
    else \
        NETCAT_PKG="netcat-traditional"; \
    fi; \
    if [ -f /etc/apt/sources.list.d/yarn.list ]; then \
        rm -f /etc/apt/sources.list.d/yarn.list; \
    fi; \
    BACKPORTS_SUITE="${DEBIAN_VERSION}-backports"; \
    sed -e "s|@URI@|${BACKPORTS_URI}|g" -e "s|@SUITE@|${BACKPORTS_SUITE}|g" /tmp/backports.sources.in > "/etc/apt/sources.list.d/${BACKPORTS_SUITE}.sources"; \
    sed -e "s|@SUITE@|${BACKPORTS_SUITE}|g" /tmp/backports.pref.in > /etc/apt/preferences.d/backports; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash-completion \
        ca-certificates \
        locales \
        curl \
        bind9-dnsutils \
        fuse3 \
        gdb \
        git \
        git-lfs \
        gnupg \
        gzip \
        htop \
        httpie \
        iputils-ping \
        jq \
        less \
        lsb-release \
        lsof \
        man-db \
        mc \
        "${NETCAT_PKG}" \
        ncdu \
        openssl \
        python3-venv \
        psmisc \
        rsync \
        sqlite3 \
        strace \
        sshfs \
        tar \
        tree \
        unzip \
        vim \
        wget \
        xz-utils \
        pandoc \
        poppler-utils \
        p7zip-full \
    ; \
    sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen; \
    locale-gen en_US.UTF-8; \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8; \
    echo "user_allow_other" >> /etc/fuse.conf; \
    rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

COPY build/tool-artifacts-staging /tmp/tool-artifacts-staging

# If PACKAGE_MANIFEST_SOURCE was provided but not present in the build context,
# try downloading it from the documented blob URL (OCI_DOCUMENTATION).
RUN set -euo pipefail; \
    if [ -n "${PACKAGE_MANIFEST_SOURCE:-}" ]; then \
        TARGET="/tmp/${PACKAGE_MANIFEST_SOURCE}"; \
        if [ ! -f "$TARGET" ]; then \
            if [ -n "${OCI_DOCUMENTATION:-}" ]; then \
                mkdir -p "$(dirname "$TARGET")"; \
                echo "[INFO] Manifest not found in build context: $TARGET"; \
                echo "[INFO] Attempting to download from: ${OCI_DOCUMENTATION}"; \
                curl -fsSL "${OCI_DOCUMENTATION}" -o "$TARGET"; \
                echo "[INFO] Downloaded manifest to: $TARGET"; \
            else \
                echo "[WARN] PACKAGE_MANIFEST_SOURCE set but OCI_DOCUMENTATION empty; skipping manifest download"; \
            fi; \
        else \
            echo "[INFO] Manifest already present in build context: $TARGET"; \
        fi; \
    fi

# Modern tools installation from local pre-staged artifacts.
RUN set -euo pipefail; \
    STAGE_DIR="/tmp/tool-artifacts-staging"; \
    DOWNLOADS_DIR="${STAGE_DIR}/downloads"; \
    if [ ! -f "${STAGE_DIR}/tool-versions.env" ]; then \
        echo "[ERROR] Missing staged tool version file: ${STAGE_DIR}/tool-versions.env" >&2; \
        exit 1; \
    fi; \
    if [ ! -d "${DOWNLOADS_DIR}" ]; then \
        echo "[ERROR] Missing staged downloads directory: ${DOWNLOADS_DIR}" >&2; \
        exit 1; \
    fi; \
    . "${STAGE_DIR}/tool-versions.env"; \
    require_file() { \
        local path="$1"; \
        if [ ! -f "${path}" ]; then \
            echo "[ERROR] Missing staged artifact: ${path}" >&2; \
            exit 1; \
        fi; \
    }; \
    install_from_tar() { \
        local tarball="$1"; \
        local bin_name="$2"; \
        local workdir="$3"; \
        mkdir -p "${workdir}"; \
        tar -xzf "${tarball}" -C "${workdir}"; \
        local bin_path=""; \
        bin_path="$(find "${workdir}" -type f -name "${bin_name}" -perm -111 | head -1)"; \
        if [ -z "${bin_path}" ]; then \
            echo "[ERROR] Failed to locate ${bin_name} in ${tarball}" >&2; \
            exit 1; \
        fi; \
        install -m 755 "${bin_path}" "/usr/local/bin/${bin_name}"; \
    }; \
    if [ "${INSTALL_CODEX}" = "true" ]; then \
        require_file "${DOWNLOADS_DIR}/codex-${CODEX_VER}.tar.gz"; \
        require_file "${DOWNLOADS_DIR}/codex-${CODEX_VER}-SHA256SUMS"; \
        EXPECTED_CODEX_SHA256="$(awk '$2 == "codex-package-x86_64-unknown-linux-musl.tar.gz" {print $1; exit}' "${DOWNLOADS_DIR}/codex-${CODEX_VER}-SHA256SUMS")"; \
        if [ -z "${EXPECTED_CODEX_SHA256}" ]; then \
            echo "[ERROR] Missing Codex checksum entry in staged SHA256SUMS" >&2; \
            exit 1; \
        fi; \
        echo "${EXPECTED_CODEX_SHA256}  ${DOWNLOADS_DIR}/codex-${CODEX_VER}.tar.gz" | sha256sum -c -; \
        install_from_tar "${DOWNLOADS_DIR}/codex-${CODEX_VER}.tar.gz" codex /tmp/codex-extract; \
    fi; \
    if [ "${INSTALL_CLAUDE_CODE}" = "true" ]; then \
        require_file "${DOWNLOADS_DIR}/claude-${CLAUDE_CODE_VER}-linux-x64"; \
        install -m 755 "${DOWNLOADS_DIR}/claude-${CLAUDE_CODE_VER}-linux-x64" /usr/local/bin/claude; \
    fi; \
    if [ "${INSTALL_ANTIGRAVITY}" = "true" ]; then \
        require_file "${DOWNLOADS_DIR}/antigravity-${ANTIGRAVITY_VER}.tar.gz"; \
        install_from_tar "${DOWNLOADS_DIR}/antigravity-${ANTIGRAVITY_VER}.tar.gz" antigravity /tmp/antigravity-extract; \
        install -m 755 /usr/local/bin/antigravity /usr/local/bin/agy; \
    fi; \
    require_file "${DOWNLOADS_DIR}/b2-${B2_VER}-linux"; \
    require_file "${DOWNLOADS_DIR}/b2-${B2_VER}-linux_hashes.txt"; \
    EXPECTED_B2_SHA256="$(grep '^sha256' "${DOWNLOADS_DIR}/b2-${B2_VER}-linux_hashes.txt" | awk '{print $2}')"; \
    echo "${EXPECTED_B2_SHA256}  ${DOWNLOADS_DIR}/b2-${B2_VER}-linux" | sha256sum -c -; \
    install -m 755 "${DOWNLOADS_DIR}/b2-${B2_VER}-linux" /usr/local/bin/b2; \
    require_file "${DOWNLOADS_DIR}/bat-${BAT_VER}.tar.gz"; \
    require_file "${DOWNLOADS_DIR}/delta-${DELTA_VER}.tar.gz"; \
    require_file "${DOWNLOADS_DIR}/fd-${FD_VER}.tar.gz"; \
    require_file "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz"; \
    require_file "${DOWNLOADS_DIR}/ripgrep-${RIPGREP_VER}.tar.gz"; \
    install_from_tar "${DOWNLOADS_DIR}/bat-${BAT_VER}.tar.gz" bat /tmp/bat-extract; \
    install_from_tar "${DOWNLOADS_DIR}/delta-${DELTA_VER}.tar.gz" delta /tmp/delta-extract; \
    install_from_tar "${DOWNLOADS_DIR}/fd-${FD_VER}.tar.gz" fd /tmp/fd-extract; \
    install_from_tar "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz" rga /tmp/rga-extract; \
    install_from_tar "${DOWNLOADS_DIR}/ripgrep-${RIPGREP_VER}.tar.gz" rg /tmp/ripgrep-extract; \
    require_file "${DOWNLOADS_DIR}/shellcheck-${SHELLCHECK_VER}.tar.xz"; \
    tar -xf "${DOWNLOADS_DIR}/shellcheck-${SHELLCHECK_VER}.tar.xz" -C /tmp; \
    install -m 755 "/tmp/shellcheck-v${SHELLCHECK_VER}/shellcheck" /usr/local/bin/shellcheck; \
    require_file "${DOWNLOADS_DIR}/fzf-${FZF_VER}.tar.gz"; \
    tar -xzf "${DOWNLOADS_DIR}/fzf-${FZF_VER}.tar.gz" -C /tmp; \
    install -m 755 /tmp/fzf /usr/local/bin/fzf; \
    require_file "${DOWNLOADS_DIR}/yq-${YQ_VER}-linux_amd64"; \
    install -m 755 "${DOWNLOADS_DIR}/yq-${YQ_VER}-linux_amd64" /usr/local/bin/yq; \
    require_file "${DOWNLOADS_DIR}/gh-${GH_VER}.tar.gz"; \
    tar -xzf "${DOWNLOADS_DIR}/gh-${GH_VER}.tar.gz" -C /tmp; \
    install -m 755 "/tmp/gh_${GH_VER}_linux_amd64/bin/gh" /usr/local/bin/gh; \
    require_file "${DOWNLOADS_DIR}/consul_${CONSUL_VER}_SHA256SUMS"; \
    require_file "${DOWNLOADS_DIR}/consul_${CONSUL_VER}_linux_amd64.zip"; \
    (cd "${DOWNLOADS_DIR}" && grep "consul_${CONSUL_VER}_linux_amd64.zip" "consul_${CONSUL_VER}_SHA256SUMS" | sha256sum -c -); \
    unzip -q -o "${DOWNLOADS_DIR}/consul_${CONSUL_VER}_linux_amd64.zip" -d /tmp/consul-extract; \
    install -m 755 /tmp/consul-extract/consul /usr/local/bin/consul; \
    require_file "${DOWNLOADS_DIR}/vault_${VAULT_VER}_SHA256SUMS"; \
    require_file "${DOWNLOADS_DIR}/vault_${VAULT_VER}_linux_amd64.zip"; \
    (cd "${DOWNLOADS_DIR}" && grep "vault_${VAULT_VER}_linux_amd64.zip" "vault_${VAULT_VER}_SHA256SUMS" | sha256sum -c -); \
    unzip -q -o "${DOWNLOADS_DIR}/vault_${VAULT_VER}_linux_amd64.zip" -d /tmp/vault-extract; \
    install -m 755 /tmp/vault-extract/vault /usr/local/bin/vault; \
    require_file "${DOWNLOADS_DIR}/awscli-${AWSCLI_VER}.zip"; \
    unzip -q "${DOWNLOADS_DIR}/awscli-${AWSCLI_VER}.zip" -d /tmp/awscli; \
    /tmp/awscli/aws/install -i /usr/local/aws -b /usr/local/bin; \
    rm -rf \
        /tmp/codex-extract \
        /tmp/bat-extract \
        /tmp/delta-extract \
        /tmp/fd-extract \
        /tmp/antigravity-extract \
        /tmp/rga-extract \
        /tmp/ripgrep-extract \
        "/tmp/shellcheck-v${SHELLCHECK_VER}" \
        /tmp/fzf \
        "/tmp/gh_${GH_VER}_linux_amd64" \
        /tmp/consul-extract \
        /tmp/vault-extract \
        /tmp/awscli \
        /tmp/tool-artifacts-staging

# Latest PostgreSQL and Redis clients (override versions via build args)
RUN set -euo pipefail; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /etc/apt/keyrings/redis-archive-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/redis.list; \
    apt-get update; \
    if [ -n "${POSTGRESQL_CLIENT_VERSION}" ] && [ "${POSTGRESQL_CLIENT_VERSION}" != "latest" ]; then \
        apt-get install -y --no-install-recommends "postgresql-client-${POSTGRESQL_CLIENT_VERSION}"; \
    else \
        apt-get install -y --no-install-recommends postgresql-client; \
    fi; \
    if [ -n "${REDIS_TOOLS_VERSION}" ] && [ "${REDIS_TOOLS_VERSION}" != "latest" ]; then \
        apt-get install -y --no-install-recommends "redis-tools=${REDIS_TOOLS_VERSION}"; \
    else \
        apt-get install -y --no-install-recommends redis-tools; \
    fi; \
    rm -rf /var/lib/apt/lists/*

USER vscode
RUN set -euo pipefail; \
    python3 -m venv /home/vscode/.venv; \
    /home/vscode/.venv/bin/python -m pip install --upgrade pip setuptools wheel --disable-pip-version-check --quiet; \
    /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check --quiet \
        boto3 \
        debugpy \
        PyYAML \
        aiohttp \
        asyncpg \
        black \
        click \
        coverage \
        hvac \
        ipdb \
        ipython \
        isort \
        jinja2 \
        mypy \
        pydantic \
        pydantic-settings \
        pytest \
        pytest-asyncio \
        pytest-cov \
        redis \
        requests \
        rich \
        ruff \
        structlog \
        tomli_w; \
    if [ "${INSTALL_AIDER}" = "true" ]; then \
        if [ "${AIDER_VERSION}" = "main" ]; then \
            /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check --quiet "aider-chat @ git+https://github.com/Aider-AI/aider.git@main"; \
        elif [ "${AIDER_VERSION}" = "latest" ]; then \
            /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check --quiet aider-chat; \
        else \
            /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check --quiet "aider-chat==${AIDER_VERSION}"; \
        fi; \
    fi
RUN set -euo pipefail; \
    if [ -n "${GITHUB_USERNAME}" ] && [ -n "${GITHUB_REPO}" ]; then \
        skip_ciu=0; \
        wheel_dir="/tmp/ciu-wheel"; \
        install -d -m 0755 "${wheel_dir}"; \
        wheel_path="${wheel_dir}/ciu.whl"; \
        ciu_wheel_tag="${CIU_WHEEL_TAG:-ciu-wheel-latest}"; \
        ciu_asset_name="${CIU_WHEEL_ASSET_NAME:-}"; \
        if [ -z "${ciu_asset_name}" ]; then \
            ciu_asset_name="$(curl -fsSL "https://api.github.com/repos/${GITHUB_USERNAME}/${GITHUB_REPO}/releases/tags/${ciu_wheel_tag}" \
                | jq -r '.assets[].name | select(endswith(".whl"))' \
                | head -n 1 || true)"; \
        fi; \
        if [ -z "${ciu_asset_name}" ]; then \
            if [ "${CIU_INSTALL_REQUIRED}" = "true" ]; then \
                echo "[ERROR] Failed to resolve CIU wheel asset name for tag ${ciu_wheel_tag}" >&2; \
                exit 1; \
            fi; \
            echo "[WARN] CIU wheel asset not found for tag ${ciu_wheel_tag}; skipping CIU install" >&2; \
            skip_ciu=1; \
        fi; \
        if [ "${skip_ciu}" -eq 0 ]; then \
            ciu_url="https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/releases/download/${ciu_wheel_tag}/${ciu_asset_name}"; \
            if ! curl -fsSL "${ciu_url}" -o "${wheel_path}"; then \
                if [ "${CIU_INSTALL_REQUIRED}" = "true" ]; then \
                    echo "[ERROR] Failed to download CIU wheel from ${ciu_url}" >&2; \
                    exit 1; \
                fi; \
                echo "[WARN] Failed to download CIU wheel from ${ciu_url}; skipping CIU install" >&2; \
                skip_ciu=1; \
            fi; \
        fi; \
        if [ "${skip_ciu}" -eq 0 ]; then \
            wheel_name="$(/home/vscode/.venv/bin/python -c 'import sys, zipfile; from pathlib import Path; wheel_path=Path(sys.argv[1]); zf=zipfile.ZipFile(wheel_path); metadata_files=[name for name in zf.namelist() if name.endswith(".dist-info/METADATA")]; wheel_files=[name for name in zf.namelist() if name.endswith(".dist-info/WHEEL")]; assert metadata_files, "[ERROR] Missing METADATA in CIU wheel"; assert wheel_files, "[ERROR] Missing WHEEL metadata in CIU wheel"; metadata=zf.read(metadata_files[0]).decode("utf-8", "replace"); name=next((line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Name:")), None); version=next((line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Version:")), None); wheel_meta=zf.read(wheel_files[0]).decode("utf-8", "replace"); tag=next((line.split(":", 1)[1].strip() for line in wheel_meta.splitlines() if line.startswith("Tag:")), None); assert name and version and tag, "[ERROR] Missing Name/Version/Tag in CIU wheel metadata"; dist=name.replace("-", "_"); print(f"{dist}-{version}-{tag}.whl"); zf.close()' "${wheel_path}")"; \
            if [ -z "${wheel_name}" ]; then \
                echo "[ERROR] Failed to derive CIU wheel filename from metadata" >&2; \
                exit 1; \
            fi; \
            wheel_dest="${wheel_dir}/${wheel_name}"; \
            mv "${wheel_path}" "${wheel_dest}"; \
            /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check "${wheel_dest}"; \
            rm -rf "${wheel_dir}"; \
        fi; \
    else \
        echo "GITHUB_USERNAME/GITHUB_REPO not set; skipping CIU install"; \
    fi

ENV VIRTUAL_ENV="/home/vscode/.venv"
ENV PATH="/home/vscode/.venv/bin:${PATH}"

USER root
RUN set -euo pipefail; \
        cat > /etc/profile.d/dstdns-venv.sh << 'EOF'
if [ -n "${BASH_VERSION:-}" ] && [[ $- == *i* ]] && [ -d /home/vscode/.venv ]; then
    # shellcheck source=/dev/null
    source /home/vscode/.venv/bin/activate
fi
EOF
USER root

# Midnight Commander defaults for better readability in terminal sessions.
RUN set -euo pipefail; \
    install -d -m 0755 /home/vscode/.config/mc; \
    cat > /home/vscode/.config/mc/ini << 'EOF'
[Midnight-Commander]
skin=nicedark
EOF

RUN set -euo pipefail; \
    chown -R vscode:vscode /home/vscode/.config/mc

# Repository-sourced release manifest plus installed tool inventory.
RUN set -euo pipefail; \
    manifest_dir="/usr/local/share/modern-debian-tools-python-debug"; \
    manifest="${manifest_dir}/manifest.md"; \
    installed_manifest="${manifest_dir}/installed-tools-manifest.md"; \
    mkdir -p "${manifest_dir}"; \
    if [ -n "${PACKAGE_MANIFEST_SOURCE:-}" ] && [ -f "/tmp/${PACKAGE_MANIFEST_SOURCE}" ]; then \
        install -m 0644 "/tmp/${PACKAGE_MANIFEST_SOURCE}" "${manifest}"; \
    else \
        { \
            echo "# Image Manifest"; \
            echo ""; \
            echo "Repository-hosted versioned manifest was not available during build."; \
            echo ""; \
            echo "- Image version: ${OCI_VERSION}"; \
            echo "- Debian: ${DEBIAN_VERSION}"; \
            echo "- Python: ${PYTHON_VERSION}"; \
            echo "- Installed tools manifest: /usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md"; \
        } > "${manifest}"; \
    fi; \
    if [ "${DEBIAN_VERSION}" = "trixie" ]; then \
        NETCAT_PKG="netcat-openbsd"; \
    else \
        NETCAT_PKG="netcat-traditional"; \
    fi; \
    b2_version_output="$(b2 version 2>&1 | head -1 || true)"; \
    if [ -z "${b2_version_output}" ]; then \
        b2_version_output="$(b2 --version 2>&1 | head -1 || true)"; \
    fi; \
    shellcheck_version_output="$(shellcheck --version 2>&1 | awk -F': ' '/version/ {print $2; exit}' || true)"; \
    if [ -z "${shellcheck_version_output}" ]; then \
        shellcheck_version_output="$(shellcheck --version 2>&1 | head -1 || true)"; \
    fi; \
    ciu_version_output="$(/home/vscode/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("ciu"))' 2>/dev/null || true)"; \
    custom_tooling_file="$(mktemp /tmp/custom-tooling.XXXXXX)"; \
    python_packages_file="$(mktemp /tmp/python-packages.XXXXXX)"; \
    system_packages_file="$(mktemp /tmp/system-packages.XXXXXX)"; \
    { \
        if [ -n "${ciu_version_output}" ]; then \
            echo "CIU=${ciu_version_output}"; \
        else \
            echo "CIU=not-installed"; \
        fi; \
        if command -v aider >/dev/null 2>&1; then \
            echo "aider=$(aider --version 2>&1 | head -1)"; \
        else \
            echo "aider=not-installed"; \
        fi; \
        if command -v agy >/dev/null 2>&1; then \
            echo "antigravity=$(agy --version 2>&1 | head -1)"; \
        else \
            echo "antigravity=not-installed"; \
        fi; \
        echo "awscli=$(aws --version 2>&1)"; \
        echo "b2=${b2_version_output}"; \
        echo "bat=$(bat --version 2>&1 | head -1)"; \
        if command -v claude >/dev/null 2>&1; then \
            echo "claude=$(claude --version 2>&1 | head -1)"; \
        else \
            echo "claude=not-installed"; \
        fi; \
        if command -v codex >/dev/null 2>&1; then \
            echo "codex=$(codex --version 2>&1 | head -1)"; \
        else \
            echo "codex=not-installed"; \
        fi; \
        echo "consul=$(consul version 2>&1 | head -1)"; \
        echo "delta=$(delta --version 2>&1 | head -1)"; \
        echo "fd=$(fd --version 2>&1 | head -1)"; \
        echo "fzf=$(fzf --version 2>&1)"; \
        echo "gh=$(gh --version 2>&1 | head -1)"; \
        echo "psql=$(psql --version 2>&1)"; \
        echo "redis-cli=$(redis-cli --version 2>&1)"; \
        echo "rga=$(rga --version 2>&1 | head -1)"; \
        echo "ripgrep=$(rg --version 2>&1 | head -1)"; \
        echo "shellcheck=${shellcheck_version_output}"; \
        echo "vault=$(vault version 2>&1 | head -1)"; \
        echo "yq=$(yq --version 2>&1)"; \
    } > "${custom_tooling_file}"; \
    /home/vscode/.venv/bin/pip list --format=freeze > "${python_packages_file}"; \
    dpkg-query -W -f='${Package}=${Version}\n' \
        bash-completion ca-certificates curl bind9-dnsutils fuse3 git git-lfs gnupg gzip htop httpie iputils-ping jq less lsb-release lsof man-db mc \
        "${NETCAT_PKG}" ncdu openssl pandoc p7zip-full poppler-utils python3-venv psmisc rsync sqlite3 strace sshfs tar tree unzip vim wget xz-utils \
        postgresql-client redis-tools > "${system_packages_file}"; \
    python3 /tmp/manifest_sections.py installed \
        --output "${installed_manifest}" \
        --debian-version "${DEBIAN_VERSION}" \
        --python-version "${PYTHON_VERSION}" \
        --image-version "${OCI_VERSION}" \
        --devcontainers-release "${DEVCONTAINERS_RELEASE}" \
        --devcontainers-version "${DEVCONTAINERS_VERSION}" \
        --custom-tooling-file "${custom_tooling_file}" \
        --python-packages-file "${python_packages_file}" \
        --system-packages-file "${system_packages_file}"; \
    rm -f "${custom_tooling_file}" "${python_packages_file}" "${system_packages_file}"; \
    rm -f /tmp/manifest_sections.py; \
    chmod 644 "${manifest}" "${installed_manifest}"

USER vscode
