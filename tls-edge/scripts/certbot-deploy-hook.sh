#!/usr/bin/env bash
# ─── tls-edge certbot deploy hook ────────────────────────────────────────────
# Purpose : reload Traefik after a host-certbot renewal (static mode).
# Install : /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh
#
# scripts/install.sh injects the real path of the edge-proxy directory into
# EDGE_PROXY_DIR below.  If the placeholder is still present (manual copy, dev
# environment) the script auto-detects the path from its own location.
#
# Reload strategy
# ───────────────
# PRIMARY (zero-downtime):
#   conf.d/certs.yml is bind-mounted :ro into the container.  The :ro flag
#   blocks only container-side writes — host-side writes propagate through
#   the bind mount and trigger Traefik's file-provider inotify watcher,
#   causing hot certificate reload with no downtime.  We rewrite the
#   "# rendered:" timestamp line to guarantee a content change; a bare
#   `touch` (attribute-only event) may be silently ignored by inotify.
#
# FALLBACK (container restart):
#   certs.yml absent → restart the container if it is running.
#
# No SIGUSR2.  Exit non-zero only on real failure.
set -euo pipefail

# ─── configuration ────────────────────────────────────────────────────────────
# install.sh replaces __EDGE_PROXY_DIR__ with the physical path.  If it is
# still the literal placeholder, fall back to auto-detection.
EDGE_PROXY_DIR="${EDGE_PROXY_DIR:-__EDGE_PROXY_DIR__}"
if [[ "$EDGE_PROXY_DIR" == "__EDGE_PROXY_DIR__" ]]; then
    EDGE_PROXY_DIR="$(readlink -f "$(dirname "$0")/../edge-proxy")"
fi

TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-edge-traefik}"

CERTS_YML="$EDGE_PROXY_DIR/conf.d/certs.yml"

# ─── main logic ───────────────────────────────────────────────────────────────
if [[ -f "$CERTS_YML" ]]; then
    # Primary path: rewrite the rendered timestamp to trigger inotify.
    # conf.d is bind-mounted :ro — host writes propagate; container cannot write back.
    NEW_STAMP="$(date -Is)"
    sed -i "s|^# rendered:.*|# rendered: $NEW_STAMP|" "$CERTS_YML"
    echo "tls-edge: updated $CERTS_YML timestamp ($NEW_STAMP) — Traefik file-provider will hot-reload"
else
    # Fallback: certs.yml missing; restart the container if it is running.
    _RUNNING="$(docker inspect -f '{{.State.Running}}' "$TRAEFIK_CONTAINER" 2>/dev/null || true)"
    if [[ "$_RUNNING" == "true" ]]; then
        echo "tls-edge: $CERTS_YML not found — restarting $TRAEFIK_CONTAINER (graceful, --time 5)"
        docker restart --time 5 "$TRAEFIK_CONTAINER"
    else
        echo "tls-edge: $CERTS_YML not found and $TRAEFIK_CONTAINER is not running — skipping"
        exit 0
    fi
fi
