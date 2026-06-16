#!/usr/bin/env bash
# ─── get.sh — tls-edge bootstrap installer / updater ────────────────────────
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Fresh install (run once on a new host):                                │
# │    curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/  │
# │      tls-edge/get.sh | sudo bash                                        │
# │                                                                         │
# │  Pin a specific release:                                                │
# │    TLS_EDGE_VERSION=tls-edge-v0.2.0 \                                  │
# │      curl -fsSL … | sudo bash                                           │
# │                                                                         │
# │  Update an existing install:                                            │
# │    tls-edge update                                                      │
# │    — or directly: sudo bash /opt/tls-edge-src/tls-edge/get.sh --update │
# │                                                                         │
# │  Air-gapped / dev (git clone fallback):                                 │
# │    TLS_EDGE_INSTALL_VIA=git \                                           │
# │      curl -fsSL … | sudo bash                                           │
# └─────────────────────────────────────────────────────────────────────────┘
#
# Environment controls:
#   TLS_EDGE_VERSION      Pin a specific tag, e.g. tls-edge-v0.2.0
#   TLS_EDGE_INSTALL_DIR  Override install root (default: /opt/tls-edge-src)
#   TLS_EDGE_INSTALL_VIA  Set to "git" to force the git-clone fallback path
#
set -euo pipefail

REPO_OWNER="volkb79-2"
REPO_NAME="vbpub"
REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
RELEASES_API="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases"

INSTALL_DIR="${TLS_EDGE_INSTALL_DIR:-/opt/tls-edge-src}"
SUBDIR="tls-edge"
TLS_EDGE_HOME="${INSTALL_DIR}/${SUBDIR}"
WRAPPER_BIN="/usr/local/bin/tls-edge"
TAG_PREFIX="tls-edge-v"

INSTALL_VIA="${TLS_EDGE_INSTALL_VIA:-artifact}"  # "artifact" or "git"

MODE="install"
[[ "${1:-}" == "--update" || "${1:-}" == "update" ]] && MODE="update"

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
for dep in curl docker; do
    command -v "$dep" &>/dev/null \
        || fatal "Required tool not found: $dep.  Install it and re-run."
done

# sha256sum is required for artifact verification
command -v sha256sum &>/dev/null \
    || fatal "sha256sum not found (install coreutils).  Alternatively, use TLS_EDGE_INSTALL_VIA=git."

# python3 is used for the semver resolver (one-liner, no deps needed)
command -v python3 &>/dev/null \
    || fatal "python3 not found.  Install it and re-run, or use TLS_EDGE_INSTALL_VIA=git."

# ─── Resolve the target tag from GitHub Releases API ─────────────────────────
# Fetches the releases JSON and picks the highest-semver tls-edge-v* tag.
# Pure curl + python3 (no jq dependency).
resolve_latest_tag_api() {
    local json
    json="$(curl -fsSL --retry 3 --retry-delay 2 \
        -H "Accept: application/vnd.github+json" \
        "${RELEASES_API}?per_page=100" 2>/dev/null)" \
        || { warn "Could not reach GitHub API."; return 1; }
    python3 - "$json" <<'PYEOF'
import sys, json

data = json.loads(sys.argv[1])
prefix = "tls-edge-v"

def semver_key(tag):
    ver = tag[len(prefix):]
    parts = []
    for chunk in ver.replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)

candidates = [
    r["tag_name"]
    for r in data
    if isinstance(r, dict)
       and r.get("tag_name", "").startswith(prefix)
       and not r.get("draft")
]

if not candidates:
    sys.exit(1)

print(max(candidates, key=semver_key))
PYEOF
}

# Fallback: resolve via git ls-remote (no API token needed, but requires git)
resolve_latest_tag_git() {
    command -v git &>/dev/null || return 1
    git ls-remote --tags --sort=-v:refname "$REPO_URL" \
            "refs/tags/${TAG_PREFIX}*" \
        2>/dev/null \
        | awk '{print $2}' \
        | grep -v '\^{}' \
        | head -1 \
        | sed 's|refs/tags/||'
}

resolve_latest_tag() {
    local tag
    tag="$(resolve_latest_tag_api 2>/dev/null)" && [[ -n "$tag" ]] && echo "$tag" && return 0
    warn "GitHub API unavailable; falling back to git ls-remote..."
    tag="$(resolve_latest_tag_git)" && [[ -n "$tag" ]] && echo "$tag" && return 0
    return 1
}

# ─── Download + verify artifact ──────────────────────────────────────────────
download_and_verify() {
    local tag="$1" workdir="$2"
    local asset_name="${tag}.tar.xz"
    local sidecar_name="${asset_name}.sha256"
    local dl_base="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/download/${tag}"

    info "Downloading ${asset_name} ..."
    curl -fsSL --retry 3 --retry-delay 2 \
        -o "${workdir}/${asset_name}" \
        "${dl_base}/${asset_name}" \
        || fatal "Download failed: ${dl_base}/${asset_name}"

    info "Downloading ${sidecar_name} ..."
    curl -fsSL --retry 3 --retry-delay 2 \
        -o "${workdir}/${sidecar_name}" \
        "${dl_base}/${sidecar_name}" \
        || fatal "Download failed: ${dl_base}/${sidecar_name}"

    info "Verifying SHA256 checksum..."
    (cd "$workdir" && sha256sum -c "$sidecar_name") \
        || fatal "SHA256 mismatch on ${asset_name} — refusing to install.  Download may be corrupt or tampered."
    ok "Checksum verified."
}

# ─── Write the installed CLI wrapper ─────────────────────────────────────────
write_wrapper() {
    install -m 755 "$TLS_EDGE_HOME/scripts/wrapper.sh" "$WRAPPER_BIN"
    sed -i "s|^TLS_EDGE_HOME=.*|TLS_EDGE_HOME=${TLS_EDGE_HOME}|" "$WRAPPER_BIN"
    ok "CLI wrapper installed: $WRAPPER_BIN"
}

# ─── Git-clone install (fallback for air-gapped / dev installs) ──────────────
# Activated by: TLS_EDGE_INSTALL_VIA=git
# No checksum verification — you are trusting the git transport.
git_install() {
    local tag="$1"
    command -v git &>/dev/null || fatal "git not found (required for git-clone fallback)."

    info "Git-clone install: ${tag}"
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        info "Updating existing clone..."
        git -C "$INSTALL_DIR" fetch --tags --quiet
        git -C "$INSTALL_DIR" checkout --quiet "$tag"
    else
        git clone \
            --filter=blob:none \
            --sparse \
            --quiet \
            "$REPO_URL" \
            "$INSTALL_DIR"
        git -C "$INSTALL_DIR" sparse-checkout set "$SUBDIR"
        git -C "$INSTALL_DIR" fetch --tags --quiet
        git -C "$INSTALL_DIR" checkout --quiet "$tag"
    fi
}

# ─── Artifact-based install ───────────────────────────────────────────────────
artifact_install() {
    local tag="$1"
    local workdir
    workdir="$(mktemp -d)"
    # Ensure cleanup on exit (trap must use single-quotes to defer expansion).
    trap 'rm -rf "$workdir"' EXIT

    download_and_verify "$tag" "$workdir"

    info "Extracting to ${TLS_EDGE_HOME} ..."
    mkdir -p "$TLS_EDGE_HOME"
    tar -xJf "${workdir}/${tag}.tar.xz" \
        --strip-components=1 \
        -C "$TLS_EDGE_HOME"
    ok "Extracted."

    # Record installed tag for update comparisons.
    echo "${tag#${TAG_PREFIX}}" > "$TLS_EDGE_HOME/VERSION"
}

# ─── INSTALL ─────────────────────────────────────────────────────────────────
do_install() {
    hr
    echo -e "  ${BLD}tls-edge${RST}  ·  get.sh"
    hr

    # Already installed? Run update instead.
    if [[ -d "$TLS_EDGE_HOME" && -f "$TLS_EDGE_HOME/VERSION" ]]; then
        warn "Existing install found at ${TLS_EDGE_HOME} — running update instead."
        do_update
        return
    fi

    # Resolve target tag
    local TARGET_TAG
    if [[ -n "${TLS_EDGE_VERSION:-}" ]]; then
        # Normalise: accept bare semver (0.2.0) or full tag (tls-edge-v0.2.0)
        if [[ "$TLS_EDGE_VERSION" == ${TAG_PREFIX}* ]]; then
            TARGET_TAG="$TLS_EDGE_VERSION"
        else
            TARGET_TAG="${TAG_PREFIX}${TLS_EDGE_VERSION#v}"
        fi
        info "Pinned version: ${BLD}${TARGET_TAG}${RST}"
    else
        info "Resolving latest release..."
        TARGET_TAG="$(resolve_latest_tag)" \
            || fatal "Could not resolve latest tls-edge release.  Check network access or set TLS_EDGE_VERSION."
        [[ -n "$TARGET_TAG" ]] \
            || fatal "No tls-edge releases found.  Publish a 'tls-edge-v*' release first."
        info "Latest release: ${BLD}${TARGET_TAG}${RST}"
    fi

    mkdir -p "$INSTALL_DIR"

    # Install via artifact (default) or git-clone (TLS_EDGE_INSTALL_VIA=git)
    if [[ "$INSTALL_VIA" == "git" ]]; then
        warn "Using git-clone fallback (TLS_EDGE_INSTALL_VIA=git).  No checksum verification."
        git_install "$TARGET_TAG"
    else
        artifact_install "$TARGET_TAG"
    fi

    write_wrapper

    hr
    ok "tls-edge ${TARGET_TAG} installed."
    echo
    echo "  Next step:"
    echo "    tls-edge install"
    echo
}

# ─── UPDATE ──────────────────────────────────────────────────────────────────
do_update() {
    [[ -d "$TLS_EDGE_HOME" && -f "$TLS_EDGE_HOME/VERSION" ]] \
        || fatal "No install found at ${TLS_EDGE_HOME}.  Run the bootstrap installer first."

    local CURRENT_VERSION CURRENT_TAG LATEST_TAG
    CURRENT_VERSION="$(cat "$TLS_EDGE_HOME/VERSION" 2>/dev/null || echo "unknown")"
    CURRENT_TAG="${TAG_PREFIX}${CURRENT_VERSION}"

    # Resolve target tag (honour pin)
    if [[ -n "${TLS_EDGE_VERSION:-}" ]]; then
        if [[ "$TLS_EDGE_VERSION" == ${TAG_PREFIX}* ]]; then
            LATEST_TAG="$TLS_EDGE_VERSION"
        else
            LATEST_TAG="${TAG_PREFIX}${TLS_EDGE_VERSION#v}"
        fi
        info "Pinned version: ${BLD}${LATEST_TAG}${RST}"
    else
        info "Resolving latest release..."
        LATEST_TAG="$(resolve_latest_tag)" \
            || fatal "Could not resolve latest tls-edge release.  Check network access or set TLS_EDGE_VERSION."
        [[ -n "$LATEST_TAG" ]] || fatal "No tls-edge releases found."
    fi

    if [[ "$LATEST_TAG" == "$CURRENT_TAG" ]]; then
        ok "Already up to date (${LATEST_TAG})."
        return
    fi

    info "Updating ${CURRENT_TAG} → ${LATEST_TAG} ..."

    # Back up user config that may be gitignored / not in the new artifact.
    local cfg bak
    local BACKED_UP=()
    for cfg in \
        "${TLS_EDGE_HOME}/ciu-stack/ciu.toml.j2" \
        "${TLS_EDGE_HOME}/edge-proxy/.env"; do
        if [[ -f "$cfg" ]]; then
            bak="${cfg}.pre-update"
            cp -p "$cfg" "$bak"
            BACKED_UP+=("$cfg")
            info "Backed up: $cfg → ${bak##*/}"
        fi
    done

    if [[ "$INSTALL_VIA" == "git" ]]; then
        warn "Using git-clone fallback (TLS_EDGE_INSTALL_VIA=git).  No checksum verification."
        git_install "$LATEST_TAG"
    else
        artifact_install "$LATEST_TAG"
    fi

    # Restore any user config that the new artifact does not provide.
    for cfg in "${BACKED_UP[@]}"; do
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

    # Print release notes from the GitHub API if available.
    local RELEASE_NOTES
    RELEASE_NOTES="$(curl -fsSL --retry 2 \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/${LATEST_TAG}" \
        2>/dev/null \
        | python3 -c "
import sys, json
data = json.load(sys.stdin)
body = data.get('body', '').strip()
if body:
    lines = body.splitlines()
    for line in lines[:20]:
        print(line)
    if len(lines) > 20:
        print('...(see GitHub for full release notes)')
" 2>/dev/null || true)"
    if [[ -n "$RELEASE_NOTES" ]]; then
        info "Release notes for ${LATEST_TAG}:"
        echo "$RELEASE_NOTES" | sed 's/^/    /'
    fi
    echo
}

# ─── Dispatch ────────────────────────────────────────────────────────────────
case "$MODE" in
    install) do_install ;;
    update)  do_update ;;
esac
