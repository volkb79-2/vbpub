#!/usr/bin/env bash
# ─── get.sh — tls-edge bootstrap installer / updater ────────────────────────
#
# Fresh install (run once on a new host):
#   curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.sh | sudo bash
#
# Pin a specific release:
#   TLS_EDGE_VERSION=tls-edge-v0.2.0 curl -fsSL ... | sudo bash
#
# Update an existing install:
#   tls-edge update
#   — or directly: sudo bash /opt/tls-edge-src/tls-edge/get.sh --update
#
set -euo pipefail

REPO_URL="https://github.com/volkb79-2/vbpub.git"
INSTALL_DIR="${TLS_EDGE_INSTALL_DIR:-/opt/tls-edge-src}"
SUBDIR="tls-edge"
TLS_EDGE_HOME="${INSTALL_DIR}/${SUBDIR}"
WRAPPER_BIN="/usr/local/bin/tls-edge"
TAG_PREFIX="tls-edge-v"

MODE="install"
[[ "${1:-}" == "--update" ]] && MODE="update"

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'
BLD='\033[1m'; RST='\033[0m'
info()  { echo -e "${CYN}==>${RST} $*"; }
ok()    { echo -e "${GRN}  ✓${RST} $*"; }
warn()  { echo -e "${YEL}  ⚠${RST}  $*"; }
err()   { echo -e "${RED}  ✗${RST}  $*" >&2; }
fatal() { err "$*"; exit 1; }
hr()    { echo "──────────────────────────────────────────────────────────────────────────────"; }

# ─── Root check ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || fatal "This script must be run as root.  Use: sudo bash get.sh"

# ─── OS check ────────────────────────────────────────────────────────────────
[[ "$(uname -s)" == "Linux" ]] || fatal "tls-edge requires Linux."

# ─── Dependency check ────────────────────────────────────────────────────────
for dep in git docker; do
    command -v "$dep" &>/dev/null \
        || fatal "Required tool not found: $dep.  Install it and re-run."
done

# ─── Resolve latest tag via ls-remote (no clone needed) ──────────────────────
resolve_latest_tag() {
    git ls-remote --tags --sort=-v:refname "$REPO_URL" "refs/tags/${TAG_PREFIX}*" \
        2>/dev/null \
        | awk '{print $2}' \
        | grep -v '\^{}' \
        | head -1 \
        | sed 's|refs/tags/||'
}

# ─── Write the installed CLI wrapper ─────────────────────────────────────────
write_wrapper() {
    install -m 755 "$TLS_EDGE_HOME/scripts/wrapper.sh" "$WRAPPER_BIN"
    sed -i "s|^TLS_EDGE_HOME=.*|TLS_EDGE_HOME=${TLS_EDGE_HOME}|" "$WRAPPER_BIN"
    ok "CLI wrapper installed: $WRAPPER_BIN"
}

# ─── INSTALL ─────────────────────────────────────────────────────────────────
do_install() {
    hr
    echo -e "  ${BLD}tls-edge${RST}  ·  get.sh"
    hr

    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        warn "Existing install found at ${INSTALL_DIR} — running update instead."
        do_update
        return
    fi

    if [[ -n "${TLS_EDGE_VERSION:-}" ]]; then
        LATEST_TAG="$TLS_EDGE_VERSION"
        info "Pinned version: ${BLD}${LATEST_TAG}${RST}"
    else
        info "Resolving latest release tag..."
        LATEST_TAG=$(resolve_latest_tag) \
            || fatal "Could not query tags from $REPO_URL. Check network access."
        [[ -n "$LATEST_TAG" ]] \
            || fatal "No tls-edge releases found.  Push a 'tls-edge-v*' tag first."
        info "Latest release: ${BLD}${LATEST_TAG}${RST}"
    fi

    info "Cloning into ${INSTALL_DIR} (sparse, blobless)..."
    git clone \
        --filter=blob:none \
        --sparse \
        --quiet \
        "$REPO_URL" \
        "$INSTALL_DIR"

    git -C "$INSTALL_DIR" sparse-checkout set "$SUBDIR"
    git -C "$INSTALL_DIR" fetch --tags --quiet
    git -C "$INSTALL_DIR" checkout --quiet "$LATEST_TAG"

    write_wrapper

    hr
    ok "tls-edge ${LATEST_TAG} installed."
    echo
    echo "  Next step:"
    echo "    tls-edge install"
    echo
}

# ─── UPDATE ──────────────────────────────────────────────────────────────────
do_update() {
    [[ -d "${INSTALL_DIR}/.git" ]] \
        || fatal "No install found at ${INSTALL_DIR}.  Run the bootstrap installer first."

    info "Fetching tags..."
    git -C "$INSTALL_DIR" fetch --tags --quiet

    LATEST_TAG=$(git -C "$INSTALL_DIR" tag -l "${TAG_PREFIX}*" | sort -V | tail -1)
    [[ -n "$LATEST_TAG" ]] || fatal "No tls-edge release tags found."

    CURRENT_TAG=$(git -C "$INSTALL_DIR" describe --tags --exact-match 2>/dev/null \
                      || git -C "$INSTALL_DIR" describe --tags 2>/dev/null \
                      || echo "(unknown)")

    if [[ "$LATEST_TAG" == "$CURRENT_TAG" ]]; then
        ok "Already up to date (${LATEST_TAG})."
        return
    fi

    info "Updating ${CURRENT_TAG} → ${LATEST_TAG} ..."

    # Belt-and-suspenders: back up gitignored user config before checkout.
    # In practice git checkout will not touch these files (they are gitignored),
    # but this ensures they survive even if the .gitignore changes between versions.
    for cfg in \
        "${TLS_EDGE_HOME}/ciu-stack/ciu.toml.j2" \
        "${TLS_EDGE_HOME}/edge-proxy/.env"; do
        if [[ -f "$cfg" ]]; then
            cp -p "$cfg" "${cfg}.pre-update"
        fi
    done

    git -C "$INSTALL_DIR" checkout --quiet "$LATEST_TAG"

    # Restore any user config that checkout may have removed.
    for cfg in \
        "${TLS_EDGE_HOME}/ciu-stack/ciu.toml.j2" \
        "${TLS_EDGE_HOME}/edge-proxy/.env"; do
        bak="${cfg}.pre-update"
        [[ -f "$bak" ]] || continue
        if [[ ! -f "$cfg" ]]; then
            mv "$bak" "$cfg"
            info "Restored: $cfg"
        else
            rm -f "$bak"
        fi
    done

    write_wrapper

    ok "Updated to ${LATEST_TAG}."

    CHANGELOG=$(git -C "$INSTALL_DIR" log --oneline "${CURRENT_TAG}..${LATEST_TAG}" \
                    -- "${SUBDIR}/" 2>/dev/null || true)
    if [[ -n "$CHANGELOG" ]]; then
        info "Changes in this release:"
        echo "$CHANGELOG" | sed 's/^/    /'
    fi
    echo
}

# ─── Dispatch ────────────────────────────────────────────────────────────────
case "$MODE" in
    install) do_install ;;
    update)  do_update ;;
esac
