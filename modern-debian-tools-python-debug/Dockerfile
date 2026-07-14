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
ARG TARGET=""
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
ARG INSTALL_OPENCLAW="true"
ARG INSTALL_COPILOT="true"
ARG INSTALL_OPENCODE="true"
ARG INSTALL_PHP="false"
ARG PHP_VERSION
ARG NVIM_VER=""
ARG NVCHAD_VER=""
ARG NODE_MAJOR
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
ARG OPENCLAW_VERSION="latest"
ARG COPILOT_VERSION="latest"
ARG OPENCODE_VERSION="latest"

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
COPY customization /tmp/customization
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

# Skopeo: try to get the latest version from Debian testing.
# The trixie release ships skopeo 1.18.x; testing has 1.22.x.
# We add testing as a temporary source with pin-priority 501 for skopeo ONLY,
# so no other packages are affected.  If this fails (e.g. testing repo
# unavailable, dependency conflict), we keep the trixie version silently.
RUN set -euo pipefail; \
    echo "deb http://deb.debian.org/debian testing main" > /etc/apt/sources.list.d/testing.list; \
    printf 'Package: skopeo\nPin: release a=testing\nPin-Priority: 501\n' > /etc/apt/preferences.d/skopeo-testing; \
    apt-get update; \
    if apt-get install --dry-run -y skopeo 2>/dev/null | grep -q "^Inst skopeo "; then \
        apt-get install -y --no-install-recommends skopeo && \
        echo "[INFO] skopeo installed from Debian testing: $(skopeo --version)"; \
    else \
        echo "[INFO] skopeo from testing not available — keeping trixie version"; \
    fi; \
    rm -f /etc/apt/sources.list.d/testing.list /etc/apt/preferences.d/skopeo-testing; \
    rm -rf /var/lib/apt/lists/*; \
    skopeo --version || echo "[WARN] skopeo not installed"

# Upstream Node.js runtime for npm-based AI tooling.
RUN set -euo pipefail; \
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    node --version; \
    npm --version; \
    rm -rf /var/lib/apt/lists/*

# User-owned npm prefix: npm-based AI CLIs can upgrade themselves as vscode.
ENV NPM_CONFIG_PREFIX="/home/vscode/.local"
ENV PATH="/home/vscode/.local/bin:${PATH}"

COPY build/tool-artifacts-staging /tmp/tool-artifacts-staging

# Neovim and NvChad are shipped from upstream GitHub releases/tags.
RUN set -euo pipefail; \
    STAGE_DIR="/tmp/tool-artifacts-staging"; \
    DOWNLOADS_DIR="${STAGE_DIR}/downloads"; \
    if [ -f "${STAGE_DIR}/tool-versions.env" ]; then \
        set -a; . "${STAGE_DIR}/tool-versions.env"; set +a; \
    fi; \
    : "${NVIM_VER:?Missing NVIM_VER build arg or staged tool version}"; \
    : "${NVCHAD_VER:?Missing NVCHAD_VER build arg or staged tool version}"; \
    nvim_archive="${DOWNLOADS_DIR}/nvim-${NVIM_VER}.tar.gz"; \
    nvchad_archive="${DOWNLOADS_DIR}/nvchad-${NVCHAD_VER}.tar.gz"; \
    if [ ! -f "${nvim_archive}" ]; then \
        echo "[ERROR] Missing staged Neovim archive: ${nvim_archive}" >&2; \
        exit 1; \
    fi; \
    if [ ! -f "${nvchad_archive}" ]; then \
        echo "[ERROR] Missing staged NvChad archive: ${nvchad_archive}" >&2; \
        exit 1; \
    fi; \
    rm -rf /opt/nvim-linux-x86_64 /home/vscode/.config/nvim; \
    tar -C /opt -xzf "${nvim_archive}"; \
    nvim_root="$(find /opt -maxdepth 1 -type d -name 'nvim-linux-x86_64' | head -1)"; \
    if [ -z "${nvim_root}" ] || [ ! -x "${nvim_root}/bin/nvim" ]; then \
        echo "[ERROR] Failed to unpack Neovim from ${nvim_archive}" >&2; \
        exit 1; \
    fi; \
    ln -sfn "${nvim_root}/bin/nvim" /usr/local/bin/nvim; \
    install -D -m 0644 "${nvim_root}/share/man/man1/nvim.1" /usr/local/share/man/man1/nvim.1; \
    mkdir -p /tmp/nvchad-extract; \
    tar -C /tmp/nvchad-extract -xzf "${nvchad_archive}"; \
    nvchad_root="$(find /tmp/nvchad-extract -mindepth 1 -maxdepth 1 -type d | head -1)"; \
    if [ -z "${nvchad_root}" ] || [ ! -f "${nvchad_root}/init.lua" ]; then \
        echo "[ERROR] Failed to unpack NvChad from ${nvchad_archive}" >&2; \
        exit 1; \
    fi; \
    install -d -m 0755 /home/vscode/.config/nvim; \
    cp -a "${nvchad_root}/." /home/vscode/.config/nvim/; \
    rm -rf /tmp/nvchad-extract; \
    nvim --version | head -1; \
    chown -R vscode:vscode /home/vscode/.config/nvim

# PHP 8.5 runtime for the php85 flavor. Debian trixie ships PHP 8.4, so the
# 8.5 packages come from packages.sury.org.
RUN set -euo pipefail; \
    if [ "${INSTALL_PHP}" != "true" ]; then \
        echo "[INFO] PHP flavor disabled — skipping PHP runtime install."; \
    else \
        install -d -m 0755 /etc/apt/keyrings; \
        curl -fsSL https://packages.sury.org/php/apt.gpg | gpg --dearmor -o /etc/apt/keyrings/sury-php.gpg; \
        chmod a+r /etc/apt/keyrings/sury-php.gpg; \
        echo "deb [signed-by=/etc/apt/keyrings/sury-php.gpg] https://packages.sury.org/php/ $(. /etc/os-release && echo \"${VERSION_CODENAME}\") main" > /etc/apt/sources.list.d/sury-php.list; \
        apt-get update; \
        apt-get install -y --no-install-recommends \
            "php${PHP_VERSION}-cli" \
            "php${PHP_VERSION}-fpm" \
            "php${PHP_VERSION}-mbstring" \
            "php${PHP_VERSION}-xml" \
            "php${PHP_VERSION}-curl" \
            "php${PHP_VERSION}-zip" \
            "php${PHP_VERSION}-gd" \
            "php${PHP_VERSION}-intl" \
            "php${PHP_VERSION}-xdebug" \
            composer; \
        php --version; \
        composer --version; \
        rm -rf /var/lib/apt/lists/*; \
    fi

RUN set -euo pipefail; \
    share_dir="/usr/local/share/modern-debian-tools-python-debug"; \
    user_customization_dir="/home/vscode/.config/modern-debian-tools-python-debug"; \
    install -d -m 0755 "${share_dir}" "${user_customization_dir}" /home/vscode/.config/mc /home/vscode/.config/nano /home/vscode/.config/htop; \
    install -m 0755 /tmp/customization/profile.sh "${share_dir}/profile.sh"; \
    install -m 0755 /tmp/customization/lesspipe.sh "${share_dir}/lesspipe.sh"; \
    install -m 0644 /tmp/customization/README.md "${share_dir}/README.md"; \
    install -m 0644 /tmp/customization/aliases.sh "${share_dir}/aliases.sh"; \
    install -m 0644 /tmp/customization/aliases.sh.example "${share_dir}/aliases.sh.example"; \
    install -m 0644 /tmp/customization/ai.env.example "${share_dir}/ai.env.example"; \
    install -m 0644 /tmp/customization/shell.env "${share_dir}/shell.env"; \
    install -m 0644 /tmp/customization/zshrc "${share_dir}/zshrc"; \
    install -m 0644 /tmp/customization/htoprc "${share_dir}/htoprc"; \
    install -m 0644 /tmp/customization/mc.ini "${share_dir}/mc.ini"; \
    install -m 0644 /tmp/customization/nanorc "${share_dir}/nanorc"; \
    cp -f /tmp/customization/README.md "${user_customization_dir}/README.md"; \
    cp -f /tmp/customization/aliases.sh "${user_customization_dir}/aliases.sh"; \
    cp -f /tmp/customization/aliases.sh.example "${user_customization_dir}/aliases.sh.example"; \
    cp -f /tmp/customization/ai.env.example "${user_customization_dir}/ai.env.example"; \
    cp -f /tmp/customization/shell.env "${user_customization_dir}/shell.env"; \
    cp -f /tmp/customization/zshrc "${user_customization_dir}/zshrc"; \
    cp -f /tmp/customization/htoprc "${user_customization_dir}/htoprc"; \
    cp -f /tmp/customization/mc.ini "${user_customization_dir}/mc.ini"; \
    cp -f /tmp/customization/nanorc "${user_customization_dir}/nanorc"; \
    install -m 0755 /tmp/customization/lesspipe.sh "${user_customization_dir}/lesspipe.sh"; \
    ln -sfn "${user_customization_dir}/nanorc" /home/vscode/.nanorc; \
    ln -sfn "${user_customization_dir}/nanorc" /home/vscode/.config/nano/nanorc; \
    ln -sfn "${user_customization_dir}/htoprc" /home/vscode/.config/htop/htoprc; \
    ln -sfn "${user_customization_dir}/mc.ini" /home/vscode/.config/mc/ini; \
    chown -R vscode:vscode "${user_customization_dir}" /home/vscode/.config/mc /home/vscode/.config/nano /home/vscode/.config/htop; \
    install -d -m 0755 /etc/profile.d; \
    printf '%s\n' '#!/bin/sh' '# shellcheck source=/dev/null' '. /usr/local/share/modern-debian-tools-python-debug/profile.sh' > /etc/profile.d/50-modern-debian-tools.sh; \
    chmod 0644 /etc/profile.d/50-modern-debian-tools.sh

# Install a common zsh configuration in both image families.  The VSC base
# image already supplies ~/.zshrc through its common-utils setup, so append a
# guarded source line there; the plain/native image gets a complete minimal
# ~/.zshrc.  This leaves prompt ownership with Oh My Zsh (when present).
RUN set -euo pipefail; \
    zshrc_source='source "$HOME/.config/modern-debian-tools-python-debug/zshrc"'; \
    if [ -f /home/vscode/.zshrc ]; then \
        if ! grep -Fqx "$zshrc_source" /home/vscode/.zshrc; then \
            printf '\n# Modern Debian Tools interactive zsh defaults.\n%s\n' "$zshrc_source" >> /home/vscode/.zshrc; \
        fi; \
    else \
        printf '%s\n' '# Modern Debian Tools interactive zsh defaults.' "$zshrc_source" > /home/vscode/.zshrc; \
    fi; \
    chown vscode:vscode /home/vscode/.zshrc

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

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
    php_system_packages=(); \
    if [ "${INSTALL_PHP}" = "true" ]; then \
        php_system_packages=( \
            "composer" \
            "php${PHP_VERSION}-cli" \
            "php${PHP_VERSION}-fpm" \
            "php${PHP_VERSION}-mbstring" \
            "php${PHP_VERSION}-xml" \
            "php${PHP_VERSION}-curl" \
            "php${PHP_VERSION}-zip" \
            "php${PHP_VERSION}-gd" \
            "php${PHP_VERSION}-intl" \
            "php${PHP_VERSION}-xdebug" \
        ); \
    fi; \
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
    install_manpage_from_tree() { \
        local tree="$1"; \
        local man_name="$2"; \
        local destination="$3"; \
        local man_path=""; \
        man_path="$(find "${tree}" -type f -name "${man_name}" -print -quit)"; \
        if [ -z "${man_path}" ]; then \
            echo "[ERROR] Missing expected manpage ${man_name} in ${tree}" >&2; \
            exit 1; \
        fi; \
        install -D -m 0644 "${man_path}" "${destination}"; \
    }; \
    install_shell_completion() { \
        local tree="$1"; \
        local source_name="$2"; \
        local destination="$3"; \
        local completion_path=""; \
        completion_path="$(find "${tree}" -type f -name "${source_name}" -print -quit)"; \
        if [ -z "${completion_path}" ]; then \
            echo "[ERROR] Missing expected shell completion ${source_name} in ${tree}" >&2; \
            exit 1; \
        fi; \
        install -D -m 0644 "${completion_path}" "${destination}"; \
    }; \
    build_htop_from_source() { \
        local tarball="$1"; \
        local workdir="/tmp/htop-build"; \
        local build_deps=(autoconf automake build-essential gettext libtool pkg-config libncurses-dev); \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${build_deps[@]}"; \
        rm -rf "${workdir}"; \
        mkdir -p "${workdir}"; \
        tar -xzf "${tarball}" -C "${workdir}" --strip-components=1; \
        cd "${workdir}"; \
        if [ -x ./autogen.sh ]; then \
            ./autogen.sh; \
        fi; \
        ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var; \
        make -j"$(nproc)"; \
        make install; \
        cd /; \
        rm -rf "${workdir}"; \
        DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove "${build_deps[@]}"; \
        rm -rf /var/lib/apt/lists/*; \
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
    install_manpage_from_tree /tmp/bat-extract bat.1 /usr/local/share/man/man1/bat.1; \
    install_shell_completion /tmp/bat-extract bat.bash /usr/share/bash-completion/completions/bat; \
    install_shell_completion /tmp/bat-extract bat.fish /usr/share/fish/vendor_completions.d/bat.fish; \
    install_shell_completion /tmp/bat-extract bat.zsh /usr/share/zsh/vendor-completions/_bat; \
    install_from_tar "${DOWNLOADS_DIR}/delta-${DELTA_VER}.tar.gz" delta /tmp/delta-extract; \
    install_from_tar "${DOWNLOADS_DIR}/fd-${FD_VER}.tar.gz" fd /tmp/fd-extract; \
    install_manpage_from_tree /tmp/fd-extract fd.1 /usr/local/share/man/man1/fd.1; \
    ln -sfn fd /usr/local/bin/fdfind; \
    ln -sfn fd.1 /usr/local/share/man/man1/fdfind.1; \
    install_shell_completion /tmp/fd-extract fd.bash /usr/share/bash-completion/completions/fd; \
    install_shell_completion /tmp/fd-extract fd.fish /usr/share/fish/vendor_completions.d/fd.fish; \
    install_shell_completion /tmp/fd-extract _fd /usr/share/zsh/vendor-completions/_fd; \
    install_from_tar "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz" rga /tmp/rga-extract; \
    install_from_tar "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz" rga-fzf /tmp/rga-extract; \
    install_from_tar "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz" rga-fzf-open /tmp/rga-extract; \
    install_from_tar "${DOWNLOADS_DIR}/rga-${RGA_VER}.tar.gz" rga-preproc /tmp/rga-extract; \
    install_from_tar "${DOWNLOADS_DIR}/ripgrep-${RIPGREP_VER}.tar.gz" rg /tmp/ripgrep-extract; \
    install_manpage_from_tree /tmp/ripgrep-extract rg.1 /usr/local/share/man/man1/rg.1; \
    install_shell_completion /tmp/ripgrep-extract rg.bash /usr/share/bash-completion/completions/rg; \
    install_shell_completion /tmp/ripgrep-extract rg.fish /usr/share/fish/vendor_completions.d/rg.fish; \
    install_shell_completion /tmp/ripgrep-extract _rg /usr/share/zsh/vendor-completions/_rg; \
    require_file "${DOWNLOADS_DIR}/htop-${HTOP_VER}.tar.gz"; \
    build_htop_from_source "${DOWNLOADS_DIR}/htop-${HTOP_VER}.tar.gz"; \
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
    install -d -m 0755 /usr/local/share/man; \
    cp -a "/tmp/gh_${GH_VER}_linux_amd64/share/man/." /usr/local/share/man/; \
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
    # Security & Debug Tools — installed from pre-staged + checksum-verified artifacts. \
    # hadolint: single static binary (no archive); asset has no version in the name. \
    # The per-asset .sha256 file contains a single line: "<hex>  hadolint-Linux-x86_64". \
    # Extract the hash and reform the check against the renamed local path. \
    require_file "${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}.sha256"; \
    require_file "${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}"; \
    EXPECTED_HADOLINT_SHA256="$(grep "hadolint-linux-x86_64" "${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}.sha256" | awk '{print $1}')"; \
    echo "${EXPECTED_HADOLINT_SHA256}  ${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}" | sha256sum -c -; \
    install -m 755 "${DOWNLOADS_DIR}/hadolint-${HADOLINT_VER}" /usr/local/bin/hadolint; \
    hadolint --version; \
    # grype: vulnerability scanner (anchore/grype); tar.gz + checksums file. \
    # Saved locally with a version-prefixed name; extract hash and reform check line. \
    grype_arch="$(dpkg --print-architecture)"; \
    require_file "${DOWNLOADS_DIR}/grype-${GRYPE_VER}-checksums.txt"; \
    require_file "${DOWNLOADS_DIR}/grype-${GRYPE_VER}.tar.gz"; \
    EXPECTED_GRYPE_SHA256="$(grep "grype_${GRYPE_VER}_linux_${grype_arch}.tar.gz\$" "${DOWNLOADS_DIR}/grype-${GRYPE_VER}-checksums.txt" | awk '{print $1}')"; \
    echo "${EXPECTED_GRYPE_SHA256}  ${DOWNLOADS_DIR}/grype-${GRYPE_VER}.tar.gz" | sha256sum -c -; \
    install_from_tar "${DOWNLOADS_DIR}/grype-${GRYPE_VER}.tar.gz" grype /tmp/grype-extract; \
    grype version; \
    # cdebug: container debugging tool (iximiuz/cdebug); tar.gz + checksums.txt. \
    # Upstream asset names have NO version embedded (cdebug_linux_amd64.tar.gz). \
    # Saved locally with a version prefix; extract hash and reform check against local name. \
    cdebug_arch="$(dpkg --print-architecture)"; \
    require_file "${DOWNLOADS_DIR}/cdebug-${CDEBUG_VER}-checksums.txt"; \
    require_file "${DOWNLOADS_DIR}/cdebug-${CDEBUG_VER}.tar.gz"; \
    EXPECTED_CDEBUG_SHA256="$(grep "cdebug_linux_${cdebug_arch}.tar.gz\$" "${DOWNLOADS_DIR}/cdebug-${CDEBUG_VER}-checksums.txt" | awk '{print $1}')"; \
    echo "${EXPECTED_CDEBUG_SHA256}  ${DOWNLOADS_DIR}/cdebug-${CDEBUG_VER}.tar.gz" | sha256sum -c -; \
    install_from_tar "${DOWNLOADS_DIR}/cdebug-${CDEBUG_VER}.tar.gz" cdebug /tmp/cdebug-extract; \
    cdebug --version; \
    mandb -q; \
    cp "${STAGE_DIR}/tool-versions.env" /tmp/ai-tool-versions.env; \
    python3 /tmp/install_ai_cli_tools.py root; \
    rm -rf \
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
        /tmp/grype-extract \
        /tmp/cdebug-extract \
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
RUN --mount=type=cache,target=/home/vscode/.npm,uid=1000,gid=1000,sharing=locked \
    set -euo pipefail; \
    set -a; . /tmp/ai-tool-versions.env; set +a; \
    python3 /tmp/install_ai_cli_tools.py user; \
    rm -f /tmp/ai-tool-versions.env
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

# ── In-container devcontainer finalize (postCreateCommand) ──────────────────
# Baked into the image so any mdt-based repo wires only:
#   "postCreateCommand": "finalize_container_environment.py"
# It runs generic, ciu-AGNOSTIC setup (PATH, aliases, vscode settings, tool
# verification) and brackets it with the consumer's own hooks discovered from
# its .devcontainer/finalize.{pre,post}.d/*.sh — that is where repo-specific (and
# any ciu) setup lives, so mdt ships+encourages ciu without ENFORCING it. The
# symmetric host-side script (initialize_container_environment.py) is NOT baked in
# — it runs on the host before the container exists, so it ships as a vendorable
# template under templates/. See DEVCONTAINER-LIFECYCLE.md.
COPY scripts/finalize_container_environment.py /usr/local/bin/finalize_container_environment.py
RUN chmod 0755 /usr/local/bin/finalize_container_environment.py

# Copy staged-artifact metadata for manifest generation (digests, source URLs).
# Note: this is a separate COPY because build/tool-artifacts-staging/ is cleaned
# up in an earlier RUN layer.
COPY build/tool-artifacts-staging/metadata.json /tmp/tool-artifacts-metadata.json

# Canonical user-facing manifest generated directly from live probes
# (pip freeze, dpkg-query, php -m, tool versions). The same manifest is
# extracted post-build into package-manifests-versioned/ so the committed
# and in-image versions are always identical (see build-push.py).
RUN set -euo pipefail; \
    manifest_dir="/usr/local/share/modern-debian-tools-python-debug"; \
    canonical_manifest="${manifest_dir}/manifest.md"; \
    user_manifest="/home/vscode/mdt-manifest.md"; \
    inventory_manifest="${manifest_dir}/installed-tools-manifest.md"; \
    mkdir -p "${manifest_dir}"; \
    \
    # Derive target package name from PACKAGE_MANIFEST_SOURCE if set
    # (e.g. "package-manifests-versioned/modern-debian-tools-python-debug/...").
    _pkg_name=""; \
    _manifest_path="${PACKAGE_MANIFEST_SOURCE:-}"; \
    if [ -n "${_manifest_path}" ]; then \
        _pkg_name="$(echo "${_manifest_path}" | cut -d/ -f2)"; \
    fi; \
    if [ -z "${_pkg_name}" ]; then \
        _pkg_name="${OCI_TITLE:-}"; \
    fi; \
    \
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
    php_extensions_file="$(mktemp /tmp/php-extensions.XXXXXX)"; \
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
        if command -v openclaw >/dev/null 2>&1; then \
            echo "openclaw=$(openclaw --version 2>&1 | head -1 || true)"; \
        else \
            echo "openclaw=not-installed"; \
        fi; \
        if command -v copilot >/dev/null 2>&1; then \
            echo "copilot=$(copilot --version 2>&1 | head -1 || true)"; \
        else \
            echo "copilot=not-installed"; \
        fi; \
        if command -v opencode >/dev/null 2>&1; then \
            echo "opencode=$(opencode --version 2>&1 | head -1 || true)"; \
        else \
            echo "opencode=not-installed"; \
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
        if command -v hadolint >/dev/null 2>&1; then \
            echo "hadolint=$(hadolint --version 2>&1 | head -1)"; \
        else \
            echo "hadolint=not-installed"; \
        fi; \
        if command -v grype >/dev/null 2>&1; then \
            echo "grype=$(grype version 2>&1 | head -1)"; \
        else \
            echo "grype=not-installed"; \
        fi; \
        if command -v cdebug >/dev/null 2>&1; then \
            echo "cdebug=$(cdebug --version 2>&1 | head -1)"; \
        else \
            echo "cdebug=not-installed"; \
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
        echo "nvim=$(nvim --version 2>&1 | head -1 | awk '{print $2}' | sed 's/^v//')"; \
        echo "htop=$(htop --version 2>&1 | head -1)"; \
        echo "nvchad=${NVCHAD_VER}"; \
        echo "node=$(node --version 2>&1 | head -1)"; \
        echo "npm=$(npm --version 2>&1 | head -1)"; \
        echo "grpcurl=$(grpcurl --version 2>&1 | head -1)"; \
        echo "psql=$(psql --version 2>&1)"; \
        echo "redis-cli=$(redis-cli --version 2>&1)"; \
        echo "rga=$(rga --version 2>&1 | head -1)"; \
        echo "ripgrep=$(rg --version 2>&1 | head -1)"; \
        echo "shellcheck=${shellcheck_version_output}"; \
        echo "vault=$(vault version 2>&1 | head -1)"; \
        echo "yq=$(yq --version 2>&1)"; \
        if [ "${INSTALL_PHP}" = "true" ]; then \
            echo "php=$(php --version 2>&1 | head -1)"; \
            if command -v composer >/dev/null 2>&1; then \
                echo "composer=$(composer --version 2>&1 | head -1)"; \
            fi; \
        fi; \
    } > "${custom_tooling_file}"; \
    /home/vscode/.venv/bin/pip list --format=freeze > "${python_packages_file}"; \
    dpkg-query -W -f='${Package}=${Version}\n' \
        bash-completion ca-certificates locales curl bind9-dnsutils fuse3 gdb git git-lfs gnupg gzip httpie iputils-ping jq less lsb-release lsof man-db mc nano \
        netcat-openbsd ncdu openssl python3-venv psmisc rsync sqlite3 strace sshfs tar tree unzip vim w3m wget xz-utils skopeo \
        postgresql-client redis-tools > "${system_packages_file}"; \
    if [ "${INSTALL_PHP}" = "true" ]; then \
        dpkg-query -W -f='${Package}=${Version}\n' \
            composer "php${PHP_VERSION}-cli" "php${PHP_VERSION}-fpm" "php${PHP_VERSION}-mbstring" "php${PHP_VERSION}-xml" "php${PHP_VERSION}-curl" "php${PHP_VERSION}-zip" "php${PHP_VERSION}-gd" "php${PHP_VERSION}-intl" "php${PHP_VERSION}-xdebug" \
            >> "${system_packages_file}"; \
    fi; \
    if [ "${INSTALL_PHP}" = "true" ]; then \
        php -m 2>/dev/null | tail -n +2 > "${php_extensions_file}"; \
    fi; \
    python3 /tmp/manifest_sections.py unified \
        --output "${canonical_manifest}" \
        --debian-version "${DEBIAN_VERSION}" \
        --python-version "${PYTHON_VERSION}" \
        --image-version "${OCI_VERSION}" \
        --devcontainers-release "${DEVCONTAINERS_RELEASE}" \
        --devcontainers-version "${DEVCONTAINERS_VERSION}" \
        --custom-tooling-file "${custom_tooling_file}" \
        --python-packages-file "${python_packages_file}" \
        --system-packages-file "${system_packages_file}" \
        --artifact-metadata "/tmp/tool-artifacts-metadata.json" \
        --description "${OCI_DESCRIPTION_BASE:-${OCI_DESCRIPTION:-}}" \
        --target "${TARGET:-}" \
        --package-name "${_pkg_name}" \
        --username "${GITHUB_USERNAME}" \
        --repo "${GITHUB_REPO}" \
        --install-php "${INSTALL_PHP:-false}" \
        --php-extensions-file "${php_extensions_file}"; \
    python3 /tmp/manifest_sections.py installed \
        --output "${inventory_manifest}" \
        --debian-version "${DEBIAN_VERSION}" \
        --python-version "${PYTHON_VERSION}" \
        --image-version "${OCI_VERSION}" \
        --devcontainers-release "${DEVCONTAINERS_RELEASE}" \
        --devcontainers-version "${DEVCONTAINERS_VERSION}" \
        --custom-tooling-file "${custom_tooling_file}" \
        --python-packages-file "${python_packages_file}" \
        --system-packages-file "${system_packages_file}" \
        --artifact-metadata "/tmp/tool-artifacts-metadata.json"; \
    ln -sfn "${canonical_manifest}" "${user_manifest}"; \
    rm -f "${custom_tooling_file}" "${python_packages_file}" "${system_packages_file}" "${php_extensions_file}" "/tmp/tool-artifacts-metadata.json"; \
    rm -f /tmp/manifest_sections.py; \
    chown vscode:vscode "${canonical_manifest}" "${inventory_manifest}" "${user_manifest}"; \
    chmod 644 "${canonical_manifest}" "${inventory_manifest}"; \
    printf '\nIMAGE_MANIFEST=%s\nINVENTORY_MANIFEST=%s\n' "${canonical_manifest}" "${inventory_manifest}" >> /etc/os-release

USER vscode
