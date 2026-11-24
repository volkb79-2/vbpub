#!/usr/bin/env bash
# ─── tls-edge certbot deploy hook ────────────────────────────────────────────
# Purpose : reload Traefik after a host-certbot renewal (static mode).
# Install : /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh
#
# A ciu instance mounts its certificate tree through rendered configfile
# overlay mounts.  There is no synced conf.d file the hook may rewrite, so the
# supported action is a graceful Traefik restart.  Discovery matches
# scripts/verify.sh: first by the Compose service label and container-name
# prefix, then by the installed instance's Compose label and name pattern.
#
# Restarting Traefik takes about 3 seconds; certificate renewals are rare.
# Exit non-zero only on a real Docker failure.
set -euo pipefail

_discover() {
    docker ps --filter "label=com.docker.compose.service=traefik" \
        --filter "name=edge-traefik" --format '{{.Names}}' 2>/dev/null | sed -n '1p'
    docker ps --filter "label=com.docker.compose.service=traefik" \
        --filter "name=tls-edge-.*-traefik" --format '{{.Names}}' 2>/dev/null | sed -n '1p'
}

TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-$(_discover | sed -n '1p')}"
if [[ -z "$TRAEFIK_CONTAINER" ]]; then
    echo "tls-edge: no running Traefik container found — skipping certificate reload"
    exit 0
fi

echo "tls-edge: restarting $TRAEFIK_CONTAINER to reload certificates (about 3 s)"
docker restart --time 5 "$TRAEFIK_CONTAINER"
