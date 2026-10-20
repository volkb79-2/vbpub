#!/usr/bin/env bash
# Apply the io.cost model and QoS for the root disk at boot (mdt-io-cost.service) from /etc/mdt/host-setup.env:
# IO_COST_MODEL and IO_COST_QOS, never literals. The same function install.sh calls (MDT-HS-07), so the boot path
# and the install path cannot drift; it writes io.cost.model, then io.cost.qos, and reads both back.
set -uo pipefail
CONF="${CONF:-/etc/mdt/host-setup.env}"
LIB="${LIB:-/usr/local/sbin/mdt-container-io-caps.lib.sh}"
log() { echo "[mdt-io-cost] $*" >&2; }
[ -r "$CONF" ] || { log "ERROR: cannot read $CONF"; exit 2; }
[ -r "$LIB" ] || { log "ERROR: cannot read $LIB"; exit 2; }
# shellcheck disable=SC1090
. "$LIB"
# shellcheck disable=SC1090
. "$CONF"
_mdt_apply_iocost
