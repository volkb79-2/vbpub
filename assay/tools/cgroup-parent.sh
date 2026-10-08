#!/usr/bin/env bash
# Resolve and verify the host cgroup tier before Assay launches a gate
# container. systemd auto-creates transient .slice units for unknown names, so
# the target gates unit must be checked before any container uses it as parent.

set -euo pipefail

PROBE_IMAGE="${ASSAY_CGROUP_PROBE_IMAGE:-tester-unified:local}"

die() { printf 'cgroup-parent: %s\n' "$*" >&2; exit 1; }

memory_reserve_bytes=0
if (($#)); then
  [[ $# -eq 2 && "$1" == --memory-reserve-bytes ]] \
    || die 'usage: cgroup-parent.sh [--memory-reserve-bytes POSITIVE_INTEGER]'
  memory_reserve_bytes="$2"
  [[ "$memory_reserve_bytes" =~ ^[0-9]+$ ]] \
    || die 'memory reservation must be a non-negative byte count'
fi

slice="${CGROUP_PARENT_DEV_GATES:-}"
if [[ -z "$slice" ]]; then
  die 'CGROUP_PARENT_DEV_GATES is unset. Refusing to launch a gate
container without an explicitly declared gates cgroup tier.'
fi
[[ "$slice" =~ ^[A-Za-z0-9_.@-]+\.slice$ ]] \
  || die "\"$slice\" is not a valid systemd slice unit name"

probe_parent="${CGROUP_PARENT_DEV_INTERACTIVE:-}"
[[ -n "$probe_parent" ]] \
  || die 'CGROUP_PARENT_DEV_INTERACTIVE is unset; refusing to launch an unplaced host-unit probe'
[[ "$probe_parent" =~ ^[A-Za-z0-9_.@-]+\.slice$ ]] \
  || die "CGROUP_PARENT_DEV_INTERACTIVE \"$probe_parent\" is not a valid systemd slice unit name"

stem="${slice%.slice}"
case "$stem" in
  -*|*-) die "\"$slice\" has an empty hierarchy component" ;;
esac

command -v timeout >/dev/null 2>&1 \
  || die 'timeout is required to bound Docker cgroup probes'
[[ -r /proc/sys/kernel/hostname ]] \
  || die 'kernel hostname is unavailable; cannot identify the running cockpit container'
if cockpit_hostname="$(cat /proc/sys/kernel/hostname)"; then
  :
else
  die 'could not read the kernel hostname for the running cockpit container'
fi
[[ -n "$cockpit_hostname" && "$cockpit_hostname" != *$'\n'* \
  && "$cockpit_hostname" != *'|'* ]] \
  || die 'kernel hostname is empty or malformed; cannot identify the running cockpit container'

if current_container_facts="$(
  timeout --signal=TERM --kill-after=5s 30s docker inspect "$cockpit_hostname" \
    --format '{{.Id}}|{{.Config.Hostname}}|{{.HostConfig.CgroupParent}}|{{.State.Running}}'
)"; then
  :
else
  inspect_status=$?
  die "could not inspect the running container \"$cockpit_hostname\" before launching the host-unit probe (exit $inspect_status)"
fi
[[ "$current_container_facts" != *$'\n'* ]] \
  || die 'Docker inspect returned multiple records for the running container'
IFS='|' read -r current_container_id current_configured_hostname current_cgroup_parent current_running \
  <<<"$current_container_facts"
[[ "$current_container_id" =~ ^[0-9a-f]{64}$ \
  && "$current_configured_hostname" == "$cockpit_hostname" \
  && "$current_running" == true ]] \
  || die "Docker inspect did not identify one running cockpit container with kernel hostname $cockpit_hostname"
if current_namespaces="$(
  printf '%s|%s' "$(readlink /proc/self/ns/mnt)" "$(readlink /proc/self/ns/pid)"
)"; then
  :
else
  die 'could not read the running cockpit mount and PID namespace identities'
fi
[[ "$current_namespaces" =~ ^mnt:\[[0-9]+\]\|pid:\[[0-9]+\]$ ]] \
  || die 'running cockpit namespace identities are malformed'
if inspected_namespaces="$(
  timeout --signal=TERM --kill-after=5s 30s docker exec "$cockpit_hostname" sh -c \
    'printf "%s|%s\n" "$(readlink /proc/self/ns/mnt)" "$(readlink /proc/self/ns/pid)"'
)"; then
  :
else
  exec_status=$?
  die "could not verify that Docker inspected the running cockpit itself (exit $exec_status)"
fi
[[ "$inspected_namespaces" == "$current_namespaces" ]] \
  || die "Docker name lookup for kernel hostname $cockpit_hostname resolved to a different container namespace"
[[ "$current_cgroup_parent" == "$probe_parent" ]] \
  || die "CGROUP_PARENT_DEV_INTERACTIVE does not match the running container's Docker CgroupParent (configured=$probe_parent, actual=$current_cgroup_parent)"

rel=""
acc=""
IFS='-' read -r -a parts <<<"$stem"
for part in "${parts[@]}"; do
  [ -n "$part" ] || die "\"$slice\" has an empty hierarchy component"
  if [ -z "$acc" ]; then acc="$part"; else acc="$acc-$part"; fi
  rel="$rel/$acc.slice"
done

# Query the Docker host's systemd manager through its read-only system-bus
# socket. The cockpit's local systemctl may be only a shim, and launching the
# candidate probe under `slice` before this check could instantiate a typo.
# Run detached and read `docker wait`'s container exit status separately from
# the Docker CLI transport status; attached `docker run` can report transport
# success after the command inside the container failed.
unit_probe_suffix="${BASHPID:-$$}-${RANDOM}-${RANDOM}"
unit_probe_name="assay-cgroup-unit-probe-$unit_probe_suffix"
unit_probe_owner="$unit_probe_suffix"
unit_probe_id=""
inspect_owned_unit_probe() {
  local target="$1" facts inspected_id inspected_name inspected_owner
  facts="$(timeout --signal=TERM --kill-after=5s 30s docker inspect "$target" \
    --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "io.assay.cgroup-parent.owner"}}')" \
    || return 1
  [[ "$facts" != *$'\n'* ]] || return 1
  IFS='|' read -r inspected_id inspected_name inspected_owner <<<"$facts"
  [[ "$inspected_id" =~ ^[0-9a-f]{64}$ \
    && "$inspected_name" == "/$unit_probe_name" \
    && "$inspected_owner" == "$unit_probe_owner" ]] || return 1
  if [[ "$unit_probe_id" =~ ^[0-9a-f]{64}$ \
    && "$inspected_id" != "$unit_probe_id" ]]; then
    return 1
  fi
  printf '%s' "$inspected_id"
}
remove_owned_unit_probe() {
  local target="$1" owned_id
  owned_id="$(inspect_owned_unit_probe "$target")" \
    || die 'Docker-host systemd query container identity could not be reconciled; refusing to remove it'
  timeout --signal=TERM --kill-after=5s 30s docker rm "$owned_id" \
    >/dev/null \
    || die 'could not remove the verified Docker-host systemd query container'
  unit_probe_id=""
  unit_probe_name=""
  unit_probe_owner=""
}
cleanup_unit_probe() {
  local status=$? target owned_id
  trap - EXIT
  if [[ -n "$unit_probe_name" && -n "$unit_probe_owner" ]]; then
    target="$unit_probe_name"
    if [[ "$unit_probe_id" =~ ^[0-9a-f]{64}$ ]]; then
      target="$unit_probe_id"
    fi
    if owned_id="$(inspect_owned_unit_probe "$target")"; then
      timeout --signal=TERM --kill-after=5s 30s docker rm --force "$owned_id" \
        >/dev/null 2>&1 \
        || printf 'cgroup-parent: could not remove verified probe container %s\n' "$owned_id" >&2
    else
      printf 'cgroup-parent: could not verify probe ownership; no container was removed\n' >&2
    fi
  fi
  return "$status"
}
trap cleanup_unit_probe EXIT

if unit_probe_id="$(
  timeout --signal=TERM --kill-after=5s 30s docker run --detach \
    --name="$unit_probe_name" \
    --label="io.assay.cgroup-parent.owner=$unit_probe_owner" \
    --cgroup-parent="$probe_parent" \
    --cgroupns=private --network=none --cpus=0.25 --memory=128m \
    --memory-swap=128m --pids-limit=64 --read-only --cap-drop=ALL \
    --security-opt=no-new-privileges --user=1003:1003 \
    --tmpfs=/tmp:rw,noexec,nosuid,size=16m \
    --mount=type=bind,source=/run/systemd/system,target=/run/systemd/system,readonly \
    --mount=type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly \
    -e DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/host-system-bus \
    "$PROBE_IMAGE" sh -c \
    'set -eu
     for unit do
       printf "ASSAY_UNIT_BEGIN=%s\\n" "$unit"
       systemctl show "$unit" --property=Id,LoadState,FragmentPath --no-pager
       printf "ASSAY_UNIT_END=%s\\n" "$unit"
     done' \
    assay-cgroup-unit-probe "$slice" "$probe_parent"
)"; then
  :
else
  run_status=$?
  die "could not start Docker-host systemd query container before any container uses \"$slice\" (exit $run_status)"
fi
[[ "$unit_probe_id" =~ ^[0-9a-f]{64}$ ]] \
  || die 'Docker-host systemd query did not return one container ID'
unit_probe_id="$(inspect_owned_unit_probe "$unit_probe_id")" \
  || die 'Docker-host systemd query container identity does not match its generated name and ownership label'

if unit_probe_exit_status="$(
  timeout --signal=TERM --kill-after=5s 30s docker wait "$unit_probe_id"
)"; then
  :
else
  wait_status=$?
  die "could not capture Docker-host systemd query container exit status (docker wait exit $wait_status)"
fi
if unit_properties="$(
  timeout --signal=TERM --kill-after=5s 30s docker logs "$unit_probe_id"
)"; then
  :
else
  logs_status=$?
  die "could not read Docker-host systemd query output (docker logs exit $logs_status)"
fi
remove_owned_unit_probe "$unit_probe_id"
[[ "$unit_probe_exit_status" =~ ^[0-9]+$ ]] \
  || die "Docker-host systemd query returned an invalid container exit status \"$unit_probe_exit_status\""
[[ "$unit_probe_exit_status" == 0 ]] \
  || die "Docker-host systemd query container exited with status $unit_probe_exit_status for \"$slice\""

verify_unit_frames() {
  local output="$1" first_unit="$2" second_unit="$3"
  if ! printf '%s\n' "$output" | awk \
    -v first="$first_unit" -v second="$second_unit" '
      BEGIN { expected[1] = first; expected[2] = second; next_unit = 1; active = 0 }
      {
        if (!active) {
          if (next_unit > 2 || $0 != "ASSAY_UNIT_BEGIN=" expected[next_unit]) exit 1
          active = 1
          next
        }
        if ($0 ~ /^ASSAY_UNIT_BEGIN=/) exit 1
        if ($0 ~ /^ASSAY_UNIT_END=/) {
          if ($0 != "ASSAY_UNIT_END=" expected[next_unit]) exit 1
          active = 0
          next_unit++
        }
      }
      END { if (active || next_unit != 3) exit 1 }
    '; then
    die "Docker-host systemd query returned incomplete or out-of-order frames for \"$first_unit\" and \"$second_unit\""
  fi
}
verify_unit_frames "$unit_properties" "$slice" "$probe_parent"

unit_property() {
  local unit="$1" property="$2" value
  value="$(printf '%s\n' "$unit_properties" | awk \
    -v unit="$unit" -v property="$property" '
      $0 == "ASSAY_UNIT_BEGIN=" unit { active = 1; next }
      $0 == "ASSAY_UNIT_END=" unit { active = 0; next }
      active && index($0, property "=") == 1 {
        print substr($0, length(property) + 2)
        count++
      }
      END { if (count != 1) exit 1 }
    ')" || die "Docker-host systemd query returned an incomplete $property for \"$unit\""
  printf '%s' "$value"
}

verify_installed_slice() {
  local unit="$1" unit_id load_state fragment_path
  unit_id="$(unit_property "$unit" Id)"
  load_state="$(unit_property "$unit" LoadState)"
  fragment_path="$(unit_property "$unit" FragmentPath)"
  [[ "$unit_id" == "$unit" ]] \
    || die "Docker-host query for \"$unit\" returned a different unit ID (Id=$unit_id)"
  [[ "$load_state" == loaded && "$fragment_path" == /* \
    && "$fragment_path" != *$'\n'* ]] \
    || die "Docker-host unit \"$unit\" is not a loaded installed slice (LoadState=$load_state, FragmentPath=$fragment_path)"
  case "$fragment_path" in
    /run/systemd/*)
      die "Docker-host unit \"$unit\" is runtime-generated, not an installed slice (FragmentPath=$fragment_path)" ;;
  esac
}

# The cockpit has no host system bus or host cgroup namespace, so a bounded,
# read-only query container is needed to inspect the Docker host's systemd
# manager. The MDT host-side initialize command verifies the interactive unit
# before it permits Docker to create the cockpit. At runtime, the kernel
# hostname identifies the inspected container, and mount/PID namespace checks
# bind that lookup to this cockpit before its explicit parent is trusted. The
# bootstrap container also has independent CPU, memory, and PID caps. Verify
# both units from the host manager before launching any container under the
# gates tier.
verify_installed_slice "$probe_parent"
verify_installed_slice "$slice"

# Only after the host's installed unit is proven do we place a small probe in
# it. That creates the slice cgroup when it is inactive and lets us read the
# kernel's effective resource controls and point-in-time RAM headroom.
if verdict="$(
  timeout --signal=TERM --kill-after=5s 30s docker run --rm -i \
    --cgroupns=host --cgroup-parent="$slice" --network=none \
    --cpus=0.25 --memory=128m --memory-swap=128m --pids-limit=64 \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --user=1003:1003 --tmpfs=/tmp:rw,noexec,nosuid,size=16m \
    -e "CG_REL=$rel" \
    -e "CG_MEMORY_RESERVE_BYTES=$memory_reserve_bytes" \
    -e CGROUP_PARENT_DEV_GATES="$slice" \
    "$PROBE_IMAGE" sh -s <<'PROBE'
set -eu
d="/sys/fs/cgroup${CG_REL}"
if [ ! -d "$d" ]; then
  printf 'MISSING %s\n' "$d" >&2
  exit 1
fi

read_required() {
  file="$d/$1"
  if [ ! -r "$file" ]; then
    printf 'READ_ERROR %s is absent or unreadable\n' "$file" >&2
    exit 1
  fi
  if value="$(cat "$file" 2>/dev/null)"; then :; else
    printf 'READ_ERROR could not read %s\n' "$file" >&2
    exit 1
  fi
  if [ -z "$value" ]; then
    printf 'READ_ERROR %s is empty\n' "$file" >&2
    exit 1
  fi
  printf '%s' "$value"
}

is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

is_limit() {
  [ "$1" = max ] || is_uint "$1"
}

memory_max="$(read_required memory.max)"
memory_high="$(read_required memory.high)"
memory_swap_max="$(read_required memory.swap.max)"
memory_current="$(read_required memory.current)"
cpu_weight="$(read_required cpu.weight)"
io_weight="$(read_required io.weight)"

is_limit "$memory_max" || { printf 'INVALID memory.max\n' >&2; exit 1; }
is_limit "$memory_high" || { printf 'INVALID memory.high\n' >&2; exit 1; }
is_limit "$memory_swap_max" || { printf 'INVALID memory.swap.max\n' >&2; exit 1; }
is_uint "$memory_current" || { printf 'INVALID memory.current\n' >&2; exit 1; }
is_uint "$cpu_weight" || { printf 'INVALID cpu.weight\n' >&2; exit 1; }
if ! printf '%s\n' "$io_weight" | awk '
  NF == 2 && ($1 == "default" || $1 ~ /^[0-9]+:[0-9]+$/) \
      && $2 ~ /^[0-9]+$/ && $2 >= 1 && $2 <= 10000 { next }
  { invalid = 1 }
  END { if (NR == 0 || invalid) exit 1 }
'; then
  printf 'INVALID io.weight\n' >&2
  exit 1
fi

configured=0
[ "$memory_max" = max ] || configured=1
[ "$memory_high" = max ] || configured=1
[ "$memory_swap_max" = max ] || configured=1
[ "$cpu_weight" = 100 ] || configured=1
[ "$io_weight" = 'default 100' ] || configured=1
if [ "$configured" != 1 ]; then
  printf 'UNCONFIGURED %s\n' "$d" >&2
  exit 1
fi

printf 'ASSAY_CGROUP_MEMORY_MAX=%s\n' "$memory_max"
printf 'ASSAY_CGROUP_MEMORY_CURRENT=%s\n' "$memory_current"
printf 'ASSAY_CGROUP_PARENT_PROBE=OK\n'
PROBE
)"; then
  :
else
  probe_status=$?
  die "could not verify host cgroup slice \"$slice\" and its resource controls (probe exit $probe_status; output: ${verdict:-<empty>})"
fi

probe_marker="$(printf '%s\n' "$verdict" | sed -n 's/^ASSAY_CGROUP_PARENT_PROBE=//p')"
memory_max="$(printf '%s\n' "$verdict" | sed -n 's/^ASSAY_CGROUP_MEMORY_MAX=//p')"
memory_current="$(printf '%s\n' "$verdict" | sed -n 's/^ASSAY_CGROUP_MEMORY_CURRENT=//p')"
[[ "$probe_marker" == OK && "$memory_max" != *$'\n'* \
  && "$memory_current" =~ ^[0-9]+$ ]] \
  || die "cgroup probe returned an incomplete or malformed report for \"$slice\""

if ((memory_reserve_bytes > 0)); then
  [[ "$memory_max" =~ ^[0-9]+$ ]] \
    || die "cannot establish finite RAM headroom for \"$slice\" (memory.max=$memory_max)"
  ((10#$memory_current <= 10#$memory_max)) \
    || die "slice \"$slice\" reports memory.current above memory.max"
  available_bytes=$((10#$memory_max - 10#$memory_current))
  ((available_bytes >= 10#$memory_reserve_bytes)) \
    || die "slice \"$slice\" has $available_bytes bytes of RAM headroom; B105 requires $memory_reserve_bytes bytes"
  printf 'cgroup-parent: RAM admission OK for %s: current=%s limit=%s reserve=%s bytes\n' \
    "$slice" "$memory_current" "$memory_max" "$memory_reserve_bytes" >&2
fi

printf '%s\n' "$slice"
