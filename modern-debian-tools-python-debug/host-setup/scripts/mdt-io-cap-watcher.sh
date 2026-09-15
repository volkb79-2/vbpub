#!/usr/bin/env bash
# mdt host-setup — instant per-container IO caps via `docker events`.
#
# Primary mechanism for capping buildx_buildkit_*/test-runner/devcontainer
# scopes; mdt-apply-dev-caps.sh's per-container sweep is the BACKSTOP — see
# host-setup/README.md "Persistence model" layer 2. Containers matching
# TESTRUNNER_IMAGE_PATTERNS/BUILDKIT_NAME_PATTERNS/DEVCONTAINER_NAME_PATTERNS
# get their caps applied the moment `docker events` reports their `start`,
# not up to WATCHER_INTERVAL later. Uses the SAME _mdt_match/
# _mdt_apply_container_caps/_mdt_classify_and_apply as the sweep
# (mdt-container-caps.lib.sh) — one definition of "what gets capped and
# how", two triggers.
#
# Long-running by design (docker-events watcher). This script does NOT
# retry its own `docker events` connection internally: on any exit (docker
# restart, a dropped pipe, an unexpected error) it exits and lets
# mdt-io-cap-watcher.service's `Restart=always` bring it back — systemd's
# restart machinery is better tested for this than a hand-rolled retry loop,
# and the periodic sweep still catches anything created during a restart gap.
#
# Why `docker events`, not inotify on cgroupfs like mdt-dev-cap-watcher.py
# (the sibling reactive watcher, for MemoryMax): that one watches a FIXED,
# already-known directory (dev-interactive.slice/dev-background.slice/
# dev-gates.slice) because its targets ARE reliably placed there at create
# time via --cgroup-parent — it only reacts to attributes WITHIN a cgroup
# Docker already placed correctly (its own docstring says so). buildx_buildkit_*
# workers are the opposite case: Buildx's own cgroup-parent driver-opt is
# unreliable under the systemd cgroup driver (host-setup/README.md,
# docs/BUILD-ARCHITECTURE.md), so there IS no fixed, known parent directory to
# inotify-watch for them — verified live 2026-09-12, one landed at a
# malformed, unnested `system.slice/dev-background.slice:docker:<id>`. An
# inotify watch requires a real path that exists before you can watch it;
# `docker events` doesn't, because it comes from the daemon's own bookkeeping
# regardless of where a container's cgroup ended up. Same reasoning applies
# to test-runner-image and devcontainer matches here even though THEY are
# reliably placed (keeping all three categories on one mechanism, rather than
# splitting into "inotify for two of these, docker-events for the third", is
# the smaller total surface to reason about — see host-setup/README.md).
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
CONF="${CONF:-/etc/mdt/host-setup.env}"
log(){ echo "[mdt-io-cap-watcher] $*"; }

# shellcheck source=./mdt-container-caps.lib.sh
. "$(dirname "$0")/mdt-container-caps.lib.sh"

if ! command -v docker >/dev/null 2>&1; then
  log "docker not found — nothing to watch"
  exit 0
fi
# Block (not poll) until the daemon answers — at boot this unit can start
# before dockerd is actually accepting connections even with After=/Requires=
# docker.service (the unit is up; the API socket isn't always immediately).
for _ in $(seq 1 30); do
  docker info >/dev/null 2>&1 && break
  sleep 1
done
if ! docker info >/dev/null 2>&1; then
  log "docker did not become ready within 30s — exiting for systemd to restart"
  exit 1
fi

_mdt_load_config
_mdt_discover_io_dev_path
_mdt_load_baseline
_mdt_derive_watcher_caps

if [ "${WATCHER_SKIP:-0}" = 1 ]; then
  # Keep the Docker-events connection alive without imposing an invented
  # transient cap. The periodic host-slices service will apply measured caps
  # once a valid baseline exists; an operator who creates the baseline later
  # can restart this service to refresh its one-time configuration load.
  log "no current baseline — watching Docker events, but per-container IO caps remain disabled until the baseline is installed and this service is restarted"
fi

# Catch whatever is already running before the first `docker events` line
# ever arrives — a systemd restart or reboot must not leave already-running
# containers waiting for the backstop sweep's next interval.
log "watching docker events for buildkit/test-runner/devcontainer container starts (${WATCHER_SRC})"
# Start the event pipeline before the reconciliation snapshot. --since covers
# the short process-start/API-attach interval as well; duplicate starts are
# harmless because applying the same systemd properties is idempotent.
# Docker's event object carries the container ID under Actor.ID.  Using the
# top-level .ID asks Docker 29.8 for a field that does not exist and makes the
# long-running watcher terminate before it can cap a new container.
WATCH_SINCE=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
docker events --since "$WATCH_SINCE" --filter type=container --filter event=start --format '{{.Actor.ID}}' |
while IFS= read -r cid; do
  [ -n "$cid" ] || continue
  _mdt_classify_and_apply "$cid"
done &
EVENTS_PIPELINE_PID=$!

log "initial pass over running containers"
for c in $(docker ps -q 2>/dev/null); do
  _mdt_classify_and_apply "$c"
done

if wait "$EVENTS_PIPELINE_PID"; then
  EVENTS_STATUS=0
else
  EVENTS_STATUS=$?
fi
log "docker events stream ended (status=$EVENTS_STATUS) — exiting for systemd to restart"
exit 1
