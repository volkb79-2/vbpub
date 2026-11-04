#!/usr/bin/env bash
# Shared Docker-host path translation for linked worktrees.
#
# Callers provide a path as seen by the cockpit. The Docker daemon sees the
# source side of the longest enclosing mount instead. No filesystem probe is
# made on the translated result: that path belongs to the Docker host.

host_path_from_mounts() {
  local logical_path="$1"
  local self mount_source mount_destination
  local best_destination="" best_source="" candidate

  self="$(cat /etc/hostname 2>/dev/null || true)"
  [ -n "$self" ] || return 0
  while IFS=$'\t' read -r mount_source mount_destination; do
    [ -n "$mount_source" ] || continue
    case "$logical_path" in
      "$mount_destination"|"$mount_destination"/*)
        if [ "${#mount_destination}" -gt "${#best_destination}" ]; then
          best_destination="$mount_destination"
          best_source="$mount_source"
        fi
        ;;
    esac
  done < <(docker inspect "$self" \
    --format '{{range .Mounts}}{{printf "%s\t%s\n" .Source .Destination}}{{end}}' \
    2>/dev/null || true)

  [ -n "$best_destination" ] || return 0
  candidate="$best_source${logical_path#"$best_destination"}"
  printf '%s\n' "$candidate"
}
