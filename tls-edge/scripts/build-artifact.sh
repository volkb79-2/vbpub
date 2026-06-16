#!/usr/bin/env bash
# ─── scripts/build-artifact.sh — package tls-edge runtime artifact ───────────
#
# Builds: tls-edge/dist/tls-edge-v<VERSION>.tar.xz
# Top-level dir inside the tarball: tls-edge-v<VERSION>/
# so consumers can extract cleanly with:
#   tar -xJf tls-edge-v<ver>.tar.xz --strip-components=1 -C /destination
#
# INCLUDE:
#   VERSION
#   scripts/  (all except update-rendered.sh)
#   ciu-stack/ (*.j2 + conf.d/{certs.yml.j2,options.yml,middlewares.yml})
#   edge-proxy/ (docker-compose.yml, traefik.yml, .env.example, conf.d/*)
#   consumer-examples/
#   README.md, ARCHITECTURE.md, CONSUMER_GUIDE.md, KNOWN_ISSUES.md
#
# EXCLUDE:
#   get.sh (lives on raw GitHub; must NOT be in the tarball)
#   scripts/update-rendered.sh (maintainer-only)
#   .release-vars, .claude/, .git/
#   Gitignored runtime files: ciu-stack/ciu.toml.j2, ciu-stack/ciu.toml,
#   ciu-stack/ciu.compose.yml, ciu-stack/traefik.yml, edge-proxy/.env,
#   *.bak, certs-dev/, .ciu/
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TLS_EDGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION_FILE="$TLS_EDGE_ROOT/VERSION"
DIST_DIR="$TLS_EDGE_ROOT/dist"

# ─── Colour helpers ──────────────────────────────────────────────────────────
GRN='\033[0;32m'; CYN='\033[0;36m'; RED='\033[0;31m'; RST='\033[0m'
ok()    { echo -e "${GRN}  ✓${RST}  $*"; }
info()  { echo -e "${CYN}==>${RST} $*"; }
fatal() { echo -e "${RED}  ✗${RST}  $*" >&2; exit 1; }

# ─── Read VERSION ─────────────────────────────────────────────────────────────
[[ -f "$VERSION_FILE" ]] || fatal "VERSION file not found: $VERSION_FILE"
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
[[ -n "$VERSION" ]] || fatal "VERSION file is empty."
TAG="tls-edge-v${VERSION}"
TARBALL="${DIST_DIR}/${TAG}.tar.xz"

info "Building artifact: ${TAG}.tar.xz  (VERSION=${VERSION})"

# ─── Ensure dist/ exists ─────────────────────────────────────────────────────
mkdir -p "$DIST_DIR"

# ─── Build file list ─────────────────────────────────────────────────────────
# We build an explicit include list so nothing slips in from gitignored files.
# All paths are relative to TLS_EDGE_ROOT.

TMP_LIST="$(mktemp)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"; rm -f "$TMP_LIST"' EXIT

{
    # Top-level single files
    for doc in VERSION README.md ARCHITECTURE.md CONSUMER_GUIDE.md KNOWN_ISSUES.md; do
        [[ -f "$TLS_EDGE_ROOT/$doc" ]] && echo "$doc"
    done

    # scripts/ — everything except update-rendered.sh
    find "$TLS_EDGE_ROOT/scripts" -type f | sort | while IFS= read -r f; do
        rel="${f#$TLS_EDGE_ROOT/}"
        [[ "$(basename "$f")" == "update-rendered.sh" ]] && continue
        echo "$rel"
    done

    # ciu-stack/ — committed template files; exclude gitignored ciu render outputs
    # Excluded bases: ciu.toml, ciu.toml.j2 (user overrides), ciu.compose.yml,
    # traefik.yml (these are rendered outputs written back by ciu, gitignored in root)
    find "$TLS_EDGE_ROOT/ciu-stack" -type f | sort | while IFS= read -r f; do
        rel="${f#$TLS_EDGE_ROOT/}"
        base="$(basename "$f")"
        dir="$(dirname "$rel")"
        case "$base" in
            ciu.toml|ciu.toml.j2|ciu.compose.yml|traefik.yml)
                # Only exclude these from the ciu-stack root (not conf.d/)
                [[ "$dir" == "ciu-stack" ]] && continue
                ;;
        esac
        echo "$rel"
    done

    # edge-proxy/ — committed defaults only; exclude .env and *.bak
    find "$TLS_EDGE_ROOT/edge-proxy" -type f | sort | while IFS= read -r f; do
        rel="${f#$TLS_EDGE_ROOT/}"
        base="$(basename "$f")"
        case "$base" in
            .env|*.bak) continue ;;
        esac
        echo "$rel"
    done

    # consumer-examples/ — all committed files; exclude secrets dirs and *.bak
    find "$TLS_EDGE_ROOT/consumer-examples" -type f | sort | while IFS= read -r f; do
        rel="${f#$TLS_EDGE_ROOT/}"
        base="$(basename "$f")"
        case "$base" in
            *.bak) continue ;;
        esac
        # skip gitignored consumer-examples/**/secrets/* (except .gitignore placeholder)
        case "$rel" in
            consumer-examples/*/secrets/*)
                [[ "$base" == ".gitignore" ]] || continue
                ;;
        esac
        echo "$rel"
    done
} | sort -u > "$TMP_LIST"

info "Files in manifest: $(wc -l < "$TMP_LIST")"

# ─── Stage files ──────────────────────────────────────────────────────────────
STAGE_DIR="${STAGE}/${TAG}"
mkdir -p "$STAGE_DIR"

info "Staging files..."
while IFS= read -r relpath; do
    src="$TLS_EDGE_ROOT/$relpath"
    dst="$STAGE_DIR/$relpath"
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
done < "$TMP_LIST"

# Deterministic mtime: clamp all files to the same timestamp
FIXED_TS="$(date '+%Y%m%d%H%M.%S')"
find "$STAGE_DIR" -type f -exec touch -t "$FIXED_TS" {} +
find "$STAGE_DIR" -type d -exec touch -t "$FIXED_TS" {} +

# ─── Pack the tarball ─────────────────────────────────────────────────────────
info "Creating tarball: $TARBALL"
tar -C "$STAGE" \
    --create \
    --xz \
    --file "$TARBALL" \
    "$TAG"

ok "Artifact ready: $TARBALL"
echo "  Size: $(du -sh "$TARBALL" | cut -f1)"
echo "  Next: python3 scripts/publish-release.py"
