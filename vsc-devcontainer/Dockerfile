# Devcontainer base image (pre-built)
# Supports multiple Debian + Python variants via build args.


ARG PYTHON_VERSION="3.13"
ARG DEBIAN_VERSION="trixie"
ARG BASE_IMAGE="mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}-${DEBIAN_VERSION}"
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-c"]

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
ARG GITHUB_USERNAME
ARG GITHUB_REPO
ARG CIU_LATEST_TAG
ARG CIU_LATEST_ASSET_NAME

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
    org.opencontainers.image.created="${OCI_CREATED}"

ENV DEBIAN_FRONTEND=noninteractive

USER root

COPY apt/backports.sources.in /tmp/backports.sources.in
COPY apt/backports.pref.in /tmp/backports.pref.in

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
        dnsutils \
        fuse3 \
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

# Modern tools installation
RUN set -euo pipefail; \
    get_latest_release() { \
        local repo="$1"; \
        local location=""; \
        location="$(curl -fsSLI "https://github.com/${repo}/releases/latest" \
            | awk -F': ' 'tolower($1)=="location"{print $2}' \
            | tail -1 \
            | tr -d '\r\n')"; \
        if [ -n "${location}" ]; then \
            basename "${location}" | sed 's/^v//' | tr -d '\r\n'; \
            return 0; \
        fi; \
        curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" \
            | jq -r '.tag_name // empty' \
            | sed 's/^v//' \
            | tr -d '\r\n'; \
    }; \
    resolve_version() { \
        local requested="$1"; \
        local repo="$2"; \
        if [ -n "${requested}" ] && [ "${requested}" != "latest" ]; then \
            echo "${requested}"; \
        else \
            get_latest_release "${repo}"; \
        fi; \
    }; \
    B2_VER="$(resolve_version "${B2_VERSION}" "Backblaze/B2_Command_Line_Tool")"; \
    BAT_VER="$(resolve_version "${BAT_VERSION}" "sharkdp/bat")"; \
    FD_VER="$(resolve_version "${FD_VERSION}" "sharkdp/fd")"; \
    RIPGREP_VER="$(resolve_version "${RIPGREP_VERSION}" "BurntSushi/ripgrep")"; \
    SHELLCHECK_VER="$(resolve_version "${SHELLCHECK_VERSION}" "koalaman/shellcheck")"; \
    FZF_VER="$(resolve_version "${FZF_VERSION}" "junegunn/fzf")"; \
    YQ_VER="$(resolve_version "${YQ_VERSION}" "mikefarah/yq")"; \
    CONSUL_VER="$(resolve_version "${CONSUL_VERSION}" "hashicorp/consul")"; \
    DELTA_VER="$(resolve_version "${DELTA_VERSION}" "dandavison/delta")"; \
    GH_VER="$(resolve_version "${GH_VERSION}" "cli/cli")"; \
    RGA_VER="$(resolve_version "${RGA_VERSION}" "phiresky/ripgrep-all")"; \
    VAULT_VER="$(resolve_version "${VAULT_VERSION}" "hashicorp/vault")"; \
    echo "B2_VER=${B2_VER}" > /tmp/tool-versions.env; \
    echo "BAT_VER=${BAT_VER}" >> /tmp/tool-versions.env; \
    echo "FD_VER=${FD_VER}" >> /tmp/tool-versions.env; \
    echo "RIPGREP_VER=${RIPGREP_VER}" >> /tmp/tool-versions.env; \
    echo "SHELLCHECK_VER=${SHELLCHECK_VER}" >> /tmp/tool-versions.env; \
    echo "FZF_VER=${FZF_VER}" >> /tmp/tool-versions.env; \
    echo "YQ_VER=${YQ_VER}" >> /tmp/tool-versions.env; \
    echo "CONSUL_VER=${CONSUL_VER}" >> /tmp/tool-versions.env; \
    echo "DELTA_VER=${DELTA_VER}" >> /tmp/tool-versions.env; \
    echo "GH_VER=${GH_VER}" >> /tmp/tool-versions.env; \
    echo "RGA_VER=${RGA_VER}" >> /tmp/tool-versions.env; \
    echo "VAULT_VER=${VAULT_VER}" >> /tmp/tool-versions.env

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v${B2_VER}/b2-linux" -o /tmp/b2-linux; \
    curl -fsSL "https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v${B2_VER}/b2-linux_hashes.txt" -o /tmp/b2-linux_hashes.txt; \
    EXPECTED_SHA256=$(grep "^sha256" /tmp/b2-linux_hashes.txt | awk '{print $2}'); \
    echo "${EXPECTED_SHA256}  /tmp/b2-linux" | sha256sum -c -; \
    install -m 755 /tmp/b2-linux /usr/local/bin/b2; \
    rm -f /tmp/b2-linux /tmp/b2-linux_hashes.txt

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    download_first_available() { \
        local output="$1"; \
        shift; \
        local url=""; \
        for url in "$@"; do \
            if curl -fsSL "${url}" -o "${output}"; then \
                echo "${url}"; \
                return 0; \
            fi; \
        done; \
        return 1; \
    }; \
    BAT_URL="$(download_first_available /tmp/bat.tar.gz \
        "https://github.com/sharkdp/bat/releases/download/v${BAT_VER}/bat-v${BAT_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/sharkdp/bat/releases/download/v${BAT_VER}/bat-${BAT_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/sharkdp/bat/releases/download/v${BAT_VER}/bat-v${BAT_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/sharkdp/bat/releases/download/v${BAT_VER}/bat-${BAT_VER}-x86_64-unknown-linux-musl.tar.gz" \
    )"; \
    DELTA_URL="$(download_first_available /tmp/delta.tar.gz \
        "https://github.com/dandavison/delta/releases/download/${DELTA_VER}/delta-${DELTA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/dandavison/delta/releases/download/v${DELTA_VER}/delta-${DELTA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/dandavison/delta/releases/download/${DELTA_VER}/delta-${DELTA_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/dandavison/delta/releases/download/v${DELTA_VER}/delta-${DELTA_VER}-x86_64-unknown-linux-musl.tar.gz" \
    )"; \
    FD_URL="$(download_first_available /tmp/fd.tar.gz \
        "https://github.com/sharkdp/fd/releases/download/v${FD_VER}/fd-v${FD_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/sharkdp/fd/releases/download/v${FD_VER}/fd-${FD_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/sharkdp/fd/releases/download/v${FD_VER}/fd-v${FD_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/sharkdp/fd/releases/download/v${FD_VER}/fd-${FD_VER}-x86_64-unknown-linux-musl.tar.gz" \
    )"; \
    RGA_URL="$(download_first_available /tmp/rga.tar.gz \
        "https://github.com/phiresky/ripgrep-all/releases/download/${RGA_VER}/ripgrep_all-v${RGA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/v${RGA_VER}/ripgrep_all-v${RGA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/${RGA_VER}/ripgrep_all-v${RGA_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/v${RGA_VER}/ripgrep_all-v${RGA_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/${RGA_VER}/ripgrep_all-${RGA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/v${RGA_VER}/ripgrep_all-${RGA_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/${RGA_VER}/ripgrep_all-${RGA_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/phiresky/ripgrep-all/releases/download/v${RGA_VER}/ripgrep_all-${RGA_VER}-x86_64-unknown-linux-musl.tar.gz" \
    )"; \
    RIPGREP_URL="$(download_first_available /tmp/ripgrep.tar.gz \
        "https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VER}/ripgrep-${RIPGREP_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/BurntSushi/ripgrep/releases/download/v${RIPGREP_VER}/ripgrep-${RIPGREP_VER}-x86_64-unknown-linux-gnu.tar.gz" \
        "https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VER}/ripgrep-${RIPGREP_VER}-x86_64-unknown-linux-musl.tar.gz" \
        "https://github.com/BurntSushi/ripgrep/releases/download/v${RIPGREP_VER}/ripgrep-${RIPGREP_VER}-x86_64-unknown-linux-musl.tar.gz" \
    )"; \
    if [ -z "${BAT_URL}" ] || [ -z "${DELTA_URL}" ] || [ -z "${FD_URL}" ] || [ -z "${RGA_URL}" ] || [ -z "${RIPGREP_URL}" ]; then \
        echo "[ERROR] Failed to download one or more release tarballs." >&2; \
        echo "[ERROR] BAT_URL=${BAT_URL}" >&2; \
        echo "[ERROR] DELTA_URL=${DELTA_URL}" >&2; \
        echo "[ERROR] FD_URL=${FD_URL}" >&2; \
        echo "[ERROR] RGA_URL=${RGA_URL}" >&2; \
        echo "[ERROR] RIPGREP_URL=${RIPGREP_URL}" >&2; \
        exit 1; \
    fi; \
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
    install_from_tar /tmp/bat.tar.gz bat /tmp/bat-extract; \
    install_from_tar /tmp/delta.tar.gz delta /tmp/delta-extract; \
    install_from_tar /tmp/fd.tar.gz fd /tmp/fd-extract; \
    install_from_tar /tmp/rga.tar.gz rga /tmp/rga-extract; \
    install_from_tar /tmp/ripgrep.tar.gz rg /tmp/ripgrep-extract; \
    rm -rf /tmp/bat.tar.gz /tmp/delta.tar.gz /tmp/fd.tar.gz /tmp/rga.tar.gz /tmp/ripgrep.tar.gz \
        /tmp/bat-extract /tmp/delta-extract /tmp/fd-extract /tmp/rga-extract /tmp/ripgrep-extract

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VER}/shellcheck-v${SHELLCHECK_VER}.linux.x86_64.tar.xz" -o /tmp/shellcheck.tar.xz; \
    tar -xf /tmp/shellcheck.tar.xz -C /tmp; \
    install -m 755 "/tmp/shellcheck-v${SHELLCHECK_VER}/shellcheck" /usr/local/bin/shellcheck; \
    rm -rf /tmp/shellcheck.tar.xz "/tmp/shellcheck-v${SHELLCHECK_VER}"

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://github.com/junegunn/fzf/releases/download/v${FZF_VER}/fzf-${FZF_VER}-linux_amd64.tar.gz" -o /tmp/fzf.tar.gz; \
    tar -xzf /tmp/fzf.tar.gz -C /tmp; \
    install -m 755 /tmp/fzf /usr/local/bin/fzf; \
    rm -rf /tmp/fzf.tar.gz /tmp/fzf

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://github.com/mikefarah/yq/releases/download/v${YQ_VER}/yq_linux_amd64" -o /usr/local/bin/yq; \
    chmod 755 /usr/local/bin/yq

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VER}/gh_${GH_VER}_linux_amd64.tar.gz" -o /tmp/gh.tar.gz; \
    tar -xzf /tmp/gh.tar.gz -C /tmp; \
    install -m 755 "/tmp/gh_${GH_VER}_linux_amd64/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VER}_linux_amd64"

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://releases.hashicorp.com/consul/${CONSUL_VER}/consul_${CONSUL_VER}_SHA256SUMS" -o /tmp/consul_SHA256SUMS; \
    curl -fsSL "https://releases.hashicorp.com/consul/${CONSUL_VER}/consul_${CONSUL_VER}_linux_amd64.zip" -o "/tmp/consul_${CONSUL_VER}_linux_amd64.zip"; \
    cd /tmp && grep "consul_${CONSUL_VER}_linux_amd64.zip" consul_SHA256SUMS | sha256sum -c -; \
    unzip "/tmp/consul_${CONSUL_VER}_linux_amd64.zip" -d /usr/local/bin; \
    chmod 755 /usr/local/bin/consul; \
    rm -f "/tmp/consul_${CONSUL_VER}_linux_amd64.zip" /tmp/consul_SHA256SUMS

RUN set -euo pipefail; \
    . /tmp/tool-versions.env; \
    curl -fsSL "https://releases.hashicorp.com/vault/${VAULT_VER}/vault_${VAULT_VER}_SHA256SUMS" -o /tmp/vault_SHA256SUMS; \
    curl -fsSL "https://releases.hashicorp.com/vault/${VAULT_VER}/vault_${VAULT_VER}_linux_amd64.zip" -o "/tmp/vault_${VAULT_VER}_linux_amd64.zip"; \
    cd /tmp && grep "vault_${VAULT_VER}_linux_amd64.zip" vault_SHA256SUMS | sha256sum -c -; \
    unzip -q -o "/tmp/vault_${VAULT_VER}_linux_amd64.zip" -d /tmp/vault-extract; \
    install -m 755 /tmp/vault-extract/vault /usr/local/bin/vault; \
    rm -rf "/tmp/vault_${VAULT_VER}_linux_amd64.zip" /tmp/vault-extract /tmp/vault_SHA256SUMS /tmp/tool-versions.env

# AWS CLI v2 (latest) - no checksum file available
RUN set -euo pipefail; \
    if [ "${AWSCLI_VERSION}" = "latest" ]; then \
        AWSCLI_ZIP_URL="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"; \
    else \
        AWSCLI_ZIP_URL="https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip"; \
    fi; \
    curl -fsSL "${AWSCLI_ZIP_URL}" -o /tmp/awscliv2.zip; \
    unzip -q /tmp/awscliv2.zip -d /tmp/awscli; \
    /tmp/awscli/aws/install -i /usr/local/aws -b /usr/local/bin; \
    rm -rf /tmp/awscliv2.zip /tmp/awscli

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
        PyYAML \
        aiohttp \
        asyncpg \
        black \
        click \
        coverage \
        hvac \
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
        structlog \
        tomli_w
RUN set -euo pipefail; \
    if [ -n "${GITHUB_USERNAME}" ] && [ -n "${GITHUB_REPO}" ]; then \
        wheel_dir="/tmp/ciu-wheel"; \
        install -d -m 0755 "${wheel_dir}"; \
        wheel_path="${wheel_dir}/ciu.whl"; \
        ciu_latest_tag="${CIU_LATEST_TAG:-ciu-wheel-latest}"; \
        ciu_asset_name="${CIU_LATEST_ASSET_NAME:-}"; \
        if [ -z "${ciu_asset_name}" ]; then \
            ciu_asset_name="$(curl -fsSL "https://api.github.com/repos/${GITHUB_USERNAME}/${GITHUB_REPO}/releases/tags/${ciu_latest_tag}" \
                | jq -r '.assets[].name | select(endswith(".whl"))' \
                | head -n 1)"; \
        fi; \
        if [ -z "${ciu_asset_name}" ]; then \
            echo "[ERROR] Failed to resolve CIU wheel asset name for tag ${ciu_latest_tag}" >&2; \
            exit 1; \
        fi; \
        ciu_url="https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/releases/download/${ciu_latest_tag}/${ciu_asset_name}"; \
        curl -fsSL "${ciu_url}" -o "${wheel_path}"; \
        wheel_name="$(/home/vscode/.venv/bin/python -c 'import sys, zipfile; from pathlib import Path; wheel_path=Path(sys.argv[1]); zf=zipfile.ZipFile(wheel_path); metadata_files=[name for name in zf.namelist() if name.endswith(".dist-info/METADATA")]; wheel_files=[name for name in zf.namelist() if name.endswith(".dist-info/WHEEL")]; assert metadata_files, "[ERROR] Missing METADATA in CIU wheel"; assert wheel_files, "[ERROR] Missing WHEEL metadata in CIU wheel"; metadata=zf.read(metadata_files[0]).decode("utf-8", "replace"); name=next((line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Name:")), None); version=next((line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Version:")), None); wheel_meta=zf.read(wheel_files[0]).decode("utf-8", "replace"); tag=next((line.split(":", 1)[1].strip() for line in wheel_meta.splitlines() if line.startswith("Tag:")), None); assert name and version and tag, "[ERROR] Missing Name/Version/Tag in CIU wheel metadata"; dist=name.replace("-", "_"); print(f"{dist}-{version}-{tag}.whl"); zf.close()' "${wheel_path}")"; \
        if [ -z "${wheel_name}" ]; then \
            echo "[ERROR] Failed to derive CIU wheel filename from metadata" >&2; \
            exit 1; \
        fi; \
        wheel_dest="${wheel_dir}/${wheel_name}"; \
        mv "${wheel_path}" "${wheel_dest}"; \
        /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check "${wheel_dest}"; \
        rm -rf "${wheel_dir}"; \
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
USER vscode

# Manifest of included tool versions
RUN set -euo pipefail; \
    manifest="/home/vscode/devcontainer-manifest.txt"; \
    if [ "${DEBIAN_VERSION}" = "trixie" ]; then \
        NETCAT_PKG="netcat-openbsd"; \
    else \
        NETCAT_PKG="netcat-traditional"; \
    fi; \
    b2_version_output="$(b2 version 2>&1 | head -1 || true)"; \
    if [ -z "$b2_version_output" ]; then \
        b2_version_output="$(b2 --version 2>&1 | head -1 || true)"; \
    fi; \
    shellcheck_version_output="$(shellcheck --version 2>&1 | awk -F': ' '/version/ {print $2; exit}' || true)"; \
    if [ -z "$shellcheck_version_output" ]; then \
        shellcheck_version_output="$(shellcheck --version 2>&1 | head -1 || true)"; \
    fi; \
    { \
        echo "image.debian=${DEBIAN_VERSION}"; \
        echo "image.python=$(python3 --version 2>&1)"; \
        echo "tool.b2=${b2_version_output}"; \
        echo "tool.bat=$(bat --version 2>&1 | head -1)"; \
        echo "tool.delta=$(delta --version 2>&1 | head -1)"; \
        echo "tool.fd=$(fd --version 2>&1 | head -1)"; \
        echo "tool.ripgrep=$(rg --version 2>&1 | head -1)"; \
        echo "tool.rga=$(rga --version 2>&1 | head -1)"; \
        echo "tool.gh=$(gh --version 2>&1 | head -1)"; \
        echo "tool.shellcheck=${shellcheck_version_output}"; \
        echo "tool.fzf=$(fzf --version 2>&1)"; \
        echo "tool.yq=$(yq --version 2>&1)"; \
        echo "tool.consul=$(consul version 2>&1 | head -1)"; \
        echo "tool.vault=$(vault version 2>&1 | head -1)"; \
        echo "tool.psql=$(psql --version 2>&1)"; \
        echo "tool.redis_cli=$(redis-cli --version 2>&1)"; \
        echo "tool.aws=$(aws --version 2>&1)"; \
        echo "pip.packages:"; \
        /home/vscode/.venv/bin/pip list --format=freeze; \
        echo "dpkg.versions:"; \
        dpkg-query -W -f='${Package}=${Version}\n' \
            bash-completion ca-certificates curl dnsutils fuse3 git git-lfs gnupg gzip htop httpie iputils-ping jq less lsb-release lsof man-db mc \
            "${NETCAT_PKG}" ncdu openssl pandoc p7zip-full poppler-utils python3-venv psmisc rsync sqlite3 strace sshfs tar tree unzip vim wget xz-utils \
                postgresql-client redis-tools; \
            } > "${manifest}"; \
            chown vscode:vscode "${manifest}"
