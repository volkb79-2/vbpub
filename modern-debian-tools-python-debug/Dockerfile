# syntax=docker/dockerfile:1
# Devcontainer base image (pre-built)
# Supports multiple Debian + Python variants via build args.


ARG PYTHON_VERSION="3.14"
ARG DEBIAN_VERSION="trixie"
ARG BASE_IMAGE="mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION}-${DEBIAN_VERSION}"
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
ARG GRPCURL_VERSION
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
ARG INSTALL_REASONIX="true"
ARG INSTALL_DEEPCODE="true"
ARG INSTALL_OPENCLAW="true"
ARG GITHUB_USERNAME
ARG GITHUB_REPO
ARG CIU_WHEEL_TAG
ARG CIU_WHEEL_ASSET_NAME
# CIU_WHEEL_URL: direct download URL for the .whl from the immutable ciu-v<semver> release.
# Resolved by resolve-devcontainers-release.py from ciu-latest/latest.json (or ciu-v* scan).
# The ciu-latest release no longer holds the wheel; it holds only a latest.json pointer.
ARG CIU_WHEEL_URL
# CIU_WHEEL_SHA256: expected sha256 hex digest of the wheel (from latest.json or .sha256 sidecar).
ARG CIU_WHEEL_SHA256
ARG CIU_INSTALL_REQUIRED="false"
ARG PACKAGE_MANIFEST_SOURCE=""
ARG DEVCONTAINERS_RELEASE=""
ARG DEVCONTAINERS_VERSION=""
ARG SECONDARY_PYTHON_VERSIONS=""
ARG REASONIX_VERSION="latest"
ARG DEEPCODE_VERSION="latest"
ARG OPENCLAW_VERSION="latest"

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
COPY apt/packages.list /tmp/packages.list
COPY pip/wheels.list /tmp/pip/wheels.list
COPY pip/wheels/ /tmp/pip/wheels/
COPY requirements/toolkit.txt /tmp/requirements/toolkit.txt
COPY package-manifests-versioned /tmp/package-manifests-versioned
COPY scripts/manifest_sections.py /tmp/manifest_sections.py
COPY ai-cli-tools.list /tmp/ai-cli-tools.list
COPY scripts/install_ai_cli_tools.py /tmp/install_ai_cli_tools.py

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
    if [ -f /etc/apt/sources.list.d/yarn.list ]; then \
        rm -f /etc/apt/sources.list.d/yarn.list; \
    fi; \
    BACKPORTS_SUITE="${DEBIAN_VERSION}-backports"; \
    sed -e "s|@URI@|${BACKPORTS_URI}|g" -e "s|@SUITE@|${BACKPORTS_SUITE}|g" /tmp/backports.sources.in > "/etc/apt/sources.list.d/${BACKPORTS_SUITE}.sources"; \
    sed -e "s|@SUITE@|${BACKPORTS_SUITE}|g" /tmp/backports.pref.in > /etc/apt/preferences.d/backports; \
    apt-get update; \
    # Package list lives in apt/packages.list (one per line; '#' starts a comment). \
    sed 's/#.*//' /tmp/packages.list | xargs apt-get install -y --no-install-recommends; \
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
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    set -euo pipefail; \
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
    set -a; . "${STAGE_DIR}/tool-versions.env"; set +a; \
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
    require_file "${DOWNLOADS_DIR}/grpcurl-${GRPCURL_VER}.tar.gz"; \
    install_from_tar "${DOWNLOADS_DIR}/grpcurl-${GRPCURL_VER}.tar.gz" grpcurl /tmp/grpcurl-extract; \
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
    # Container inspection tools — installed from pre-staged + checksum-verified artifacts. \
    # dtop: Rust target triple naming; x86_64 → x86_64-unknown-linux-gnu, arm64 → aarch64-unknown-linux-gnu. \
    dtop_arch="$(uname -m)"; \
    case "${dtop_arch}" in \
        x86_64|amd64)   dtop_triple="x86_64-unknown-linux-gnu" ;; \
        aarch64|arm64)  dtop_triple="aarch64-unknown-linux-gnu" ;; \
        *)              echo "[ERROR] Unsupported arch for dtop: ${dtop_arch}" >&2; exit 1 ;; \
    esac; \
    require_file "${DOWNLOADS_DIR}/dtop-${DTOP_VER}-sha256.sum"; \
    require_file "${DOWNLOADS_DIR}/dtop-${DTOP_VER}.tar.gz"; \
    EXPECTED_DTOP_SHA256="$(grep "\*dtop-${dtop_triple}.tar.gz" "${DOWNLOADS_DIR}/dtop-${DTOP_VER}-sha256.sum" | awk '{print $1}')"; \
    echo "${EXPECTED_DTOP_SHA256}  ${DOWNLOADS_DIR}/dtop-${DTOP_VER}.tar.gz" | sha256sum -c -; \
    install_from_tar "${DOWNLOADS_DIR}/dtop-${DTOP_VER}.tar.gz" dtop /tmp/dtop-extract; \
    # lazydocker: uses uname -m arch naming (x86_64 / arm64 / armv7 / armv6). \
    lazydocker_arch="$(uname -m)"; \
    case "${lazydocker_arch}" in \
        x86_64|amd64)   lazydocker_arch="x86_64" ;; \
        aarch64|arm64)  lazydocker_arch="arm64" ;; \
        armv7l|armv7)   lazydocker_arch="armv7" ;; \
        armv6l|armv6)   lazydocker_arch="armv6" ;; \
    esac; \
    require_file "${DOWNLOADS_DIR}/lazydocker-${LAZYDOCKER_VER}-checksums.txt"; \
    require_file "${DOWNLOADS_DIR}/lazydocker-${LAZYDOCKER_VER}.tar.gz"; \
    EXPECTED_LAZYDOCKER_SHA256="$(grep "lazydocker_${LAZYDOCKER_VER}_Linux_${lazydocker_arch}.tar.gz\$" "${DOWNLOADS_DIR}/lazydocker-${LAZYDOCKER_VER}-checksums.txt" | awk '{print $1}')"; \
    echo "${EXPECTED_LAZYDOCKER_SHA256}  ${DOWNLOADS_DIR}/lazydocker-${LAZYDOCKER_VER}.tar.gz" | sha256sum -c -; \
    install_from_tar "${DOWNLOADS_DIR}/lazydocker-${LAZYDOCKER_VER}.tar.gz" lazydocker /tmp/lazydocker-extract; \
    # glances (PyPI): installed at build time with pinned version from tool-versions.env. \
    # NOTE: glances is the single network exception — its transitive dep closure (psutil, \
    # ujson, etc.) is platform+Python-specific; pre-staging all wheels is impractical. \
    python3 -m pip install --no-cache-dir "glances==${GLANCES_VER}"; \
    # dive: uses dpkg architecture naming (amd64 / arm64). \
    dive_arch="$(dpkg --print-architecture)"; \
    require_file "${DOWNLOADS_DIR}/dive-${DIVE_VER}-checksums.txt"; \
    require_file "${DOWNLOADS_DIR}/dive_${DIVE_VER}_linux_${dive_arch}.deb"; \
    (cd "${DOWNLOADS_DIR}" && grep "dive_${DIVE_VER}_linux_${dive_arch}.deb" "dive-${DIVE_VER}-checksums.txt" | sha256sum -c -); \
    dpkg -i "${DOWNLOADS_DIR}/dive_${DIVE_VER}_linux_${dive_arch}.deb"; \
    # syft: pre-staged tar.gz + checksum-verified; replaces insecure get.anchore.io | sh. \
    syft_arch="$(dpkg --print-architecture)"; \
    require_file "${DOWNLOADS_DIR}/syft-${SYFT_VER}-checksums.txt"; \
    require_file "${DOWNLOADS_DIR}/syft-${SYFT_VER}.tar.gz"; \
    EXPECTED_SYFT_SHA256="$(grep "syft_${SYFT_VER}_linux_${syft_arch}.tar.gz\$" "${DOWNLOADS_DIR}/syft-${SYFT_VER}-checksums.txt" | awk '{print $1}')"; \
    echo "${EXPECTED_SYFT_SHA256}  ${DOWNLOADS_DIR}/syft-${SYFT_VER}.tar.gz" | sha256sum -c -; \
    install_from_tar "${DOWNLOADS_DIR}/syft-${SYFT_VER}.tar.gz" syft /tmp/syft-extract; \
    python3 /tmp/install_ai_cli_tools.py root; \
    rm -rf \
        /tmp/codex-extract \
        /tmp/bat-extract \
        /tmp/grpcurl-extract \
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
        /tmp/dtop-extract \
        /tmp/lazydocker-extract \
        /tmp/syft-extract \
        /root/.cache \
        /tmp/awscli \
        /tmp/tool-artifacts-staging

# Latest PostgreSQL and Redis clients (override versions via build args)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -euo pipefail; \
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

# ── Docker CLI (client + buildx + compose plugins; NO daemon) ─────────────────
# The mdt image is a cockpit that drives docker against a mounted socket
# (docker-outside-of-docker), so it needs the client + plugins but not dockerd.
# Official Docker apt repo (trixie is published); same keyring pattern as above.
# Source: https://docs.docker.com/engine/install/debian/
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -euo pipefail; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin; \
    rm -rf /var/lib/apt/lists/*

# ── Primary Python venv — full development toolkit ────────────────────────────
# Installed into /home/vscode/.venv using the base-image Python.
# Consumer-facing list: see README.md § "Primary Venv Packages".
USER vscode
RUN --mount=type=cache,target=/home/vscode/.cache/pip,uid=1000,gid=1000,sharing=locked \
    set -euo pipefail; \
    python3 -m venv /home/vscode/.venv; \
    /home/vscode/.venv/bin/python -m pip install --upgrade pip setuptools wheel --disable-pip-version-check --quiet; \
    /home/vscode/.venv/bin/python -m pip install --disable-pip-version-check --quiet \
        -r /tmp/requirements/toolkit.txt; \
    python3 /tmp/install_ai_cli_tools.py venv
# ── Secondary Python venvs — lean (uv + debugpy + ruff only) ─────────────────
# Driven by SECONDARY_PYTHON_VERSIONS build arg (space-separated dotted versions, e.g. "3.11 3.9").
# Each version gets /home/vscode/.venv-py{nodot} (e.g. .venv-py311, .venv-py39).
# Install project deps on demand: uv pip install -r requirements.txt --python .venv-py311/bin/python
# Consumer-facing docs: see README.md § "Multi-Python Devcontainer Variants".
RUN set -euo pipefail; \
    if [ -n "${SECONDARY_PYTHON_VERSIONS}" ]; then \
        for pyver in ${SECONDARY_PYTHON_VERSIONS}; do \
            venv_name=".venv-py${pyver//./}"; \
            echo "Installing secondary Python ${pyver} → /home/vscode/${venv_name}"; \
            /home/vscode/.venv/bin/uv python install "${pyver}"; \
            /home/vscode/.venv/bin/uv venv --python "${pyver}" "/home/vscode/${venv_name}"; \
            /home/vscode/.venv/bin/uv pip install \
                --python "/home/vscode/${venv_name}/bin/python" \
                --quiet \
                uv debugpy ruff; \
        done; \
    fi
# ── First-party wheels (pip/wheels.list) ─────────────────────────────────────
# Resolved and downloaded pre-build by scripts/resolve-devcontainers-release.py.
# pip/wheels/ holds the .whl files (ciu + cmru, from ciu-v* / cmru-v* GitHub Releases).
# Installed with --no-index --find-links (no build-time network): each wheel's
# runtime deps must already be in the venv — cmru has none; ciu's Jinja2 / PyYAML /
# tomli_w come from requirements/toolkit.txt (installed above).
# Skipped (non-fatal) when pip/wheels/ is empty (release not yet published).
# Source: https://github.com/volkb79-2/vbpub/releases — ciu-v* / cmru-v* releases.
RUN set -euo pipefail; \
    wheels_dir="/tmp/pip/wheels"; \
    wheels_list="/tmp/pip/wheels.list"; \
    if [ ! -d "${wheels_dir}" ] || [ -z "$(ls "${wheels_dir}"/*.whl 2>/dev/null || true)" ]; then \
        echo "[INFO] pip/wheels/ is empty — no first-party wheels to install (FINAL CUT will populate it)" >&2; \
    else \
        wheel_expected="$(sed 's/#.*//;/^[[:space:]]*$/d' "${wheels_list}" | tr '\n' ' ')"; \
        echo "[INFO] wheels.list declares: ${wheel_expected:-<none>}" >&2; \
        echo "[INFO] Installing resolved first-party wheels present in ${wheels_dir}:" >&2; \
        ls -1 "${wheels_dir}"/*.whl >&2; \
        /home/vscode/.venv/bin/python -m pip install \
            --disable-pip-version-check \
            --no-index \
            --find-links="${wheels_dir}" \
            "${wheels_dir}"/*.whl; \
    fi

ENV VIRTUAL_ENV="/home/vscode/.venv"
ENV PATH="/home/vscode/.venv/bin:${PATH}"

USER root
RUN rm -f /tmp/install_ai_cli_tools.py /tmp/ai-cli-tools.list
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

# Canonical user-facing manifest plus internal inventory snapshot.
RUN set -euo pipefail; \
    canonical_manifest="/home/vscode/mdt-manifest.md"; \
    inventory_manifest="/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md"; \
    source_manifest=""; \
    if [ -n "${PACKAGE_MANIFEST_SOURCE:-}" ] && [ -f "/tmp/${PACKAGE_MANIFEST_SOURCE}" ]; then \
        source_manifest="/tmp/${PACKAGE_MANIFEST_SOURCE}"; \
    fi; \
    mkdir -p /usr/local/share/modern-debian-tools-python-debug; \
    if [ -n "${source_manifest}" ]; then \
        install -m 0644 "${source_manifest}" "${canonical_manifest}"; \
    else \
        { \
            echo "# Image Manifest"; \
            echo ""; \
            echo "Repository-hosted versioned manifest was not available during build."; \
            echo ""; \
            echo "- Image version: ${OCI_VERSION}"; \
            echo "- Debian: ${DEBIAN_VERSION}"; \
            echo "- Python: ${PYTHON_VERSION}"; \
            echo "- Canonical manifest: ${canonical_manifest}"; \
        } > "${canonical_manifest}"; \
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
    cmru_version_output="$(/home/vscode/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("cmru"))' 2>/dev/null || true)"; \
    custom_tooling_file="$(mktemp /tmp/custom-tooling.XXXXXX)"; \
    python_packages_file="$(mktemp /tmp/python-packages.XXXXXX)"; \
    system_packages_file="$(mktemp /tmp/system-packages.XXXXXX)"; \
    { \
        if [ -n "${cmru_version_output}" ]; then \
            echo "cmru=${cmru_version_output}"; \
        else \
            echo "cmru=not-installed"; \
        fi; \
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
        if command -v reasonix >/dev/null 2>&1; then \
            echo "reasonix=$(reasonix --version 2>&1 | head -1 || true)"; \
        else \
            echo "reasonix=not-installed"; \
        fi; \
        if command -v deepcode >/dev/null 2>&1; then \
            echo "deepcode=$(deepcode --version 2>&1 | head -1 || true)"; \
        else \
            echo "deepcode=not-installed"; \
        fi; \
        if command -v openclaw >/dev/null 2>&1; then \
            echo "openclaw=$(openclaw --version 2>&1 | head -1 || true)"; \
        else \
            echo "openclaw=not-installed"; \
        fi; \
        if command -v agy >/dev/null 2>&1; then \
            echo "antigravity=$(agy --version 2>&1 | head -1)"; \
        else \
            echo "antigravity=not-installed"; \
        fi; \
        if command -v dtop >/dev/null 2>&1; then \
            echo "dtop=$(dtop --version 2>&1 | head -1)"; \
        else \
            echo "dtop=not-installed"; \
        fi; \
        if command -v lazydocker >/dev/null 2>&1; then \
            echo "lazydocker=$(lazydocker --version 2>&1 | head -1)"; \
        else \
            echo "lazydocker=not-installed"; \
        fi; \
        if command -v glances >/dev/null 2>&1; then \
            echo "glances=$(glances --version 2>&1 | head -1)"; \
        else \
            echo "glances=not-installed"; \
        fi; \
        if command -v dive >/dev/null 2>&1; then \
            echo "dive=$(dive --version 2>&1 | head -1)"; \
        else \
            echo "dive=not-installed"; \
        fi; \
        if command -v syft >/dev/null 2>&1; then \
            echo "syft=$(syft --version 2>&1 | head -1)"; \
        else \
            echo "syft=not-installed"; \
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
        echo "grpcurl=$(grpcurl --version 2>&1 | head -1)"; \
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
        bash-completion ca-certificates locales curl bind9-dnsutils fuse3 gdb git git-lfs gnupg gzip htop httpie iputils-ping jq less lsb-release lsof man-db mc \
        netcat-openbsd ncdu openssl python3-venv psmisc rsync sqlite3 strace sshfs tar tree unzip vim w3m wget xz-utils nodejs npm skopeo \
        postgresql-client redis-tools > "${system_packages_file}"; \
    python3 /tmp/manifest_sections.py unified \
        --output "${inventory_manifest}" \
        --source-manifest "${source_manifest}" \
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
    chown vscode:vscode "${canonical_manifest}" "${inventory_manifest}"; \
    chmod 644 "${canonical_manifest}" "${inventory_manifest}"; \
    printf '\nIMAGE_MANIFEST=%s\nINVENTORY_MANIFEST=%s\n' "${canonical_manifest}" "${inventory_manifest}" >> /etc/os-release

USER vscode
