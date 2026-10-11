#!/usr/bin/env bash
# Resolve and verify the host cgroup tier before Assay launches a gate
# container. systemd auto-creates transient .slice units for unknown names, so
# the target gates unit must be checked before any container uses it as parent.

set -euo pipefail

PROBE_IMAGE="${ASSAY_CGROUP_PROBE_IMAGE:-tester-unified:local}"

die() { printf 'cgroup-parent: %s\n' "$*" >&2; exit 1; }

# Bash arithmetic is signed and can wrap on an oversized external value.
fits_memory_arithmetic() {
  [[ "$1" =~ ^[0-9]{1,18}$ ]]
}

memory_reserve_bytes=0
if (($#)); then
  [[ $# -eq 2 && "$1" == --memory-reserve-bytes ]] \
    || die 'usage: cgroup-parent.sh [--memory-reserve-bytes POSITIVE_INTEGER]'
  memory_reserve_bytes="$2"
  [[ "$memory_reserve_bytes" =~ ^[0-9]+$ ]] \
    || die 'memory reservation must be a non-negative byte count'
  fits_memory_arithmetic "$memory_reserve_bytes" \
    || die 'memory reservation exceeds the safe RAM-admission arithmetic range'
  memory_reserve_bytes=$((10#$memory_reserve_bytes))
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
    --mount=type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly \
    "$PROBE_IMAGE" sh -c '
set -eu
bus_address=unix:path=/tmp/host-system-bus
busctl_json() {
  timeout --kill-after=2s 5s /usr/bin/busctl \
    --address="$bus_address" --no-pager --json=short "$@"
}
json_value() {
  /usr/bin/python3 -I -c '\''
import json, sys
payload = json.load(sys.stdin)
value = payload["data"]
if isinstance(value, list):
    if len(value) != 1:
        raise SystemExit("expected one D-Bus value")
    value = value[0]
if isinstance(value, str):
    if "\n" in value or "\r" in value:
        raise SystemExit("D-Bus string contains a line break")
elif type(value) is not int or value < 0:
    raise SystemExit("D-Bus value is not a string or unsigned integer")
print(value)
'\''
}
get_unit_path() {
  local unit="$1" result
  result="$(busctl_json call org.freedesktop.systemd1 \
    /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager GetUnit s "$unit")"
  printf "%s\n" "$result" | json_value
}
get_property() {
  local object_path="$1" interface="$2" property="$3" result
  result="$(busctl_json get-property org.freedesktop.systemd1 \
    "$object_path" "$interface" "$property")"
  printf "%s\n" "$result" | json_value
}
emit_unit() {
  local unit="$1" object_path unit_id load_state fragment_path
  local control_group memory_max memory_high memory_swap_max memory_current
  local cpu_quota cpu_weight io_weight
  object_path="$(get_unit_path "$unit")"
  case "$object_path" in
    /org/freedesktop/systemd1/unit/*) ;;
    *) printf "MISSING unit=%s unsafe-object-path=%s\n" "$unit" "$object_path" >&2; exit 1 ;;
  esac
  unit_id="$(get_property "$object_path" org.freedesktop.systemd1.Unit Id)"
  load_state="$(get_property "$object_path" org.freedesktop.systemd1.Unit LoadState)"
  fragment_path="$(get_property "$object_path" org.freedesktop.systemd1.Unit FragmentPath)"
  printf "ASSAY_UNIT_BEGIN=%s\nId=%s\nLoadState=%s\nFragmentPath=%s\n" \
    "$unit" "$unit_id" "$load_state" "$fragment_path"
  if [ "$unit" = "$gates_slice" ]; then
    control_group="$(get_property "$object_path" org.freedesktop.systemd1.Slice ControlGroup)"
    memory_max="$(get_property "$object_path" org.freedesktop.systemd1.Slice MemoryMax)"
    memory_high="$(get_property "$object_path" org.freedesktop.systemd1.Slice MemoryHigh)"
    memory_swap_max="$(get_property "$object_path" org.freedesktop.systemd1.Slice MemorySwapMax)"
    memory_current="$(get_property "$object_path" org.freedesktop.systemd1.Slice MemoryCurrent)"
    cpu_quota="$(get_property "$object_path" org.freedesktop.systemd1.Slice CPUQuotaPerSecUSec)"
    cpu_weight="$(get_property "$object_path" org.freedesktop.systemd1.Slice CPUWeight)"
    io_weight="$(get_property "$object_path" org.freedesktop.systemd1.Slice IOWeight)"
    printf "ControlGroup=%s\nMemoryMax=%s\nMemoryHigh=%s\nMemorySwapMax=%s\nMemoryCurrent=%s\nCPUQuotaPerSecUSec=%s\nCPUWeight=%s\nIOWeight=%s\n" \
      "$control_group" "$memory_max" "$memory_high" "$memory_swap_max" \
      "$memory_current" "$cpu_quota" "$cpu_weight" "$io_weight"
  fi
  printf "ASSAY_UNIT_END=%s\n" "$unit"
}
gates_slice="$1"
interactive_slice="$2"
for unit in "$gates_slice" "$interactive_slice"; do emit_unit "$unit"; done
' \
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

# The cockpit has no host system bus. Query the Docker host's systemd manager
# through its read-only system-bus socket, keeping the probe's cgroup namespace
# private and exposing no host cgroup filesystem. The MDT host-side initialize
# command verifies the interactive unit before it permits Docker to create the
# cockpit. At runtime, kernel-hostname plus mount/PID namespace checks bind the
# Docker lookup to this cockpit before its explicit parent is trusted. The
# bootstrap container also has independent CPU, memory, and PID caps.
verify_installed_slice "$probe_parent"
verify_installed_slice "$slice"

# The same private systemd query provides configured controls and current usage;
# never switch the probe to the host cgroup namespace or mount host cgroupfs.
control_group="$(unit_property "$slice" ControlGroup)"
memory_max="$(unit_property "$slice" MemoryMax)"
memory_high="$(unit_property "$slice" MemoryHigh)"
memory_swap_max="$(unit_property "$slice" MemorySwapMax)"
memory_current="$(unit_property "$slice" MemoryCurrent)"
cpu_quota="$(unit_property "$slice" CPUQuotaPerSecUSec)"
cpu_weight="$(unit_property "$slice" CPUWeight)"
io_weight="$(unit_property "$slice" IOWeight)"
[[ "$control_group" == "$rel" ]] \
  || die "Docker-host systemd query returned an unexpected ControlGroup for \"$slice\" (ControlGroup=$control_group expected=$rel)"
for property_value in "$memory_max" "$memory_high" "$memory_swap_max" "$cpu_quota"; do
  [[ "$property_value" =~ ^[0-9]+$ ]] \
    || die "Docker-host systemd query returned a malformed memory or CPU quota property for \"$slice\""
done
[[ "$memory_current" =~ ^[0-9]+$ && "$cpu_weight" =~ ^[0-9]+$ \
  && "$io_weight" =~ ^[0-9]+$ ]] \
  || die "Docker-host systemd query returned a malformed current-usage or weight property for \"$slice\""
[[ "$cpu_weight" =~ ^[1-9][0-9]*$ && "$io_weight" =~ ^[1-9][0-9]*$ ]] \
  || die "Docker-host systemd query returned a non-positive CPU/I/O weight for \"$slice\""
valid_weight() {
  case "$1" in
    [1-9]|[1-9][0-9]|[1-9][0-9][0-9]|[1-9][0-9][0-9][0-9]|10000) return 0 ;;
    *) return 1 ;;
  esac
}
valid_weight "$cpu_weight" && valid_weight "$io_weight" \
  || die "Docker-host systemd query returned a CPU/I/O weight outside the supported range for \"$slice\""

uint64_max=18446744073709551615
[[ "$memory_max" != "$uint64_max" ]] || memory_max=max
[[ "$memory_high" != "$uint64_max" ]] || memory_high=max
[[ "$memory_swap_max" != "$uint64_max" ]] || memory_swap_max=max
[[ "$cpu_quota" != "$uint64_max" ]] || cpu_quota=max

configured=0
[[ "$memory_max" == max ]] || configured=1
[[ "$memory_high" == max ]] || configured=1
[[ "$memory_swap_max" == max ]] || configured=1
[[ "$cpu_quota" == max ]] || configured=1
[[ "$cpu_weight" == 100 ]] || configured=1
[[ "$io_weight" == 100 ]] || configured=1
[[ "$configured" == 1 ]] \
  || die "host slice \"$slice\" has only systemd-default resource controls"

if ((memory_reserve_bytes > 0)); then
  [[ "$memory_max" =~ ^[0-9]+$ ]] \
    || die "cannot establish finite RAM headroom for \"$slice\" (memory.max=$memory_max)"
  fits_memory_arithmetic "$memory_max" && fits_memory_arithmetic "$memory_current" \
    && fits_memory_arithmetic "$memory_reserve_bytes" \
    || die "Docker-host systemd query or reserve exceeds the safe RAM-admission arithmetic range for \"$slice\""
  ((10#$memory_current <= 10#$memory_max)) \
    || die "slice \"$slice\" reports memory.current above memory.max"
  available_bytes=$((10#$memory_max - 10#$memory_current))
  ((available_bytes >= 10#$memory_reserve_bytes)) \
    || die "slice \"$slice\" has $available_bytes bytes of RAM headroom; B105 requires $memory_reserve_bytes bytes"
  printf 'cgroup-parent: RAM admission OK for %s: current=%s limit=%s reserve=%s bytes\n' \
    "$slice" "$memory_current" "$memory_max" "$memory_reserve_bytes" >&2
fi

printf '%s\n' "$slice"
