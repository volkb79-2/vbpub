#!/usr/bin/env bash
# wings-cg-reconcile.sh — apply systemd resource properties to slices and
# Docker container scopes from /etc/wings-cg-profiles.conf, idempotently and
# through the daemon-reload-safe channel (systemctl set-property --runtime).
#
# Properties only. This cannot and does not move containers between slices.
set -euo pipefail

CONF="${WINGS_CG_PROFILES:-/etc/wings-cg-profiles.conf}"
LOGTAG="wings-cg-reconcile"

log() { logger -t "$LOGTAG" -- "$*" 2>/dev/null || echo "$LOGTAG: $*"; }

[[ -r "$CONF" ]] || { log "config $CONF not readable, nothing to do"; exit 0; }

apply_unit() {
    local unit="$1"; shift
    # set-property is idempotent; --runtime keeps host /etc clean for scopes
    # (transient units cannot take persistent properties anyway).
    if systemctl set-property --runtime "$unit" "$@" 2>/dev/null; then
        log "applied to $unit: $*"
    else
        log "WARN: failed to apply to $unit: $*"
    fi
}

while read -r match props; do
    [[ -z "$match" || "$match" == \#* ]] && continue
    # shellcheck disable=SC2086
    if [[ "$match" == slice:* ]]; then
        unit="${match#slice:}"
        systemctl cat "$unit" >/dev/null 2>&1 || systemctl start "$unit" 2>/dev/null || true
        apply_unit "$unit" $props
    else
        # Container-name glob: resolve running containers to their scopes.
        while read -r cid cname; do
            [[ -z "$cid" ]] && continue
            # shellcheck disable=SC2254
            case "$cname" in
                $match) apply_unit "docker-${cid}.scope" $props ;;
            esac
        done < <(docker ps --no-trunc --format '{{.ID}} {{.Names}}' 2>/dev/null)
    fi
done < "$CONF"
