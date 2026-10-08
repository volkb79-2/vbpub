#!/usr/bin/env bash
# Resolve and verify the host cgroup tier before assay's nyxloom gate launches
# its container. A missing systemd slice name fails open into an unlimited
# transient slice, so accepting the spelling without inspecting the host
# cgroup tree is not verification.

set -euo pipefail

PROBE_IMAGE="${ASSAY_CGROUP_PROBE_IMAGE:-tester-unified:local}"
PROBE_ID=""
PROBE_NAME=""
PROBE_OWNER=""
PROBE_CIDFILE=""
PROBE_SCRATCH=""

die() { printf 'cgroup-parent: %s\n' "$*" >&2; exit 1; }

cleanup_probe() {
  local result=$? recorded="" inventory=""
  trap - EXIT
  if [[ -n "$PROBE_CIDFILE" && -s "$PROBE_CIDFILE" ]]; then
    recorded="$(<"$PROBE_CIDFILE")"
  fi
  if [[ "$recorded" =~ ^[0-9a-f]{64}$ ]]; then
    inventory="$(docker inspect --format \
      '{{.Id}}|{{.Name}}|{{index .Config.Labels "assay.cgroup-parent.owner"}}' \
      "$recorded" 2>/dev/null)" || inventory=""
    if [[ "$inventory" == "$recorded|/$PROBE_NAME|$PROBE_OWNER" ]]; then
      docker rm -f "$PROBE_NAME" >/dev/null 2>&1 || result=1
    fi
  fi
  if [[ -n "$PROBE_SCRATCH" ]]; then
    rm -rf -- "$PROBE_SCRATCH"
  fi
  exit "$result"
}

slice="${CGROUP_PARENT_DEV_GATES:-}"
if [[ -z "$slice" ]]; then
  die 'CGROUP_PARENT_DEV_GATES is unset. Refusing to launch a gate container without an explicitly declared gates cgroup tier.'
fi

case "$slice" in
  *.slice) ;;
  *) die "\"$slice\" does not name a systemd slice unit" ;;
esac

probe_parent="${CGROUP_PARENT_DEV_INTERACTIVE:-}"
[[ -n "$probe_parent" ]] \
  || die 'CGROUP_PARENT_DEV_INTERACTIVE is unset. Refusing to launch an unplaced slice-verification probe.'

cockpit_name="${HOSTNAME:-}"
[[ -n "$cockpit_name" ]] \
  || die 'HOSTNAME is unset. Cannot verify the trusted interactive cgroup parent.'
cockpit_parent="$(docker inspect --format '{{.HostConfig.CgroupParent}}' "$cockpit_name" 2>/dev/null)" \
  || die "could not read the cockpit container's actual Docker cgroup parent"
[[ "$cockpit_parent" == "$probe_parent" ]] \
  || die "trusted probe parent $probe_parent differs from the cockpit's actual Docker parent $cockpit_parent"

docker image inspect "$PROBE_IMAGE" >/dev/null 2>&1 \
  || die "probe image $PROBE_IMAGE is unavailable"

PROBE_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/assay-cgroup-parent.XXXXXXXX")" \
  || die 'could not create private probe ownership scratch'
PROBE_CIDFILE="$PROBE_SCRATCH/container.cid"
PROBE_NAME="assay-cgroup-parent-${BASHPID}-$(date +%s%N)-${RANDOM}"
PROBE_OWNER="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')" \
  || die 'could not create probe ownership token'
[[ "$PROBE_OWNER" =~ ^[0-9a-f]{64}$ ]] \
  || die 'could not create a valid probe ownership token'
trap cleanup_probe EXIT

# Keep this verifier unprivileged and namespace-private. `systemctl` uses
# systemd's local private transport even when DBUS_SYSTEM_BUS_ADDRESS is set;
# do not mount /run/systemd/private. busctl is given the mounted system-bus
# address explicitly and makes only GetUnit/GetProperty calls.
printf 'ASSAY_CGROUP_PARENT_PROBE_CONTAINER=%s\n' "$PROBE_NAME" >&2
probe_id="$(docker run -d \
  --cidfile "$PROBE_CIDFILE" \
  --label "assay.cgroup-parent.owner=$PROBE_OWNER" \
  --name "$PROBE_NAME" \
  --cgroup-parent="$probe_parent" \
  --cpus=3 \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user=1003:1003 \
  -w / \
  --mount type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly \
  --mount type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly \
  "$PROBE_IMAGE" \
  bash -c 'set -eu
unit=$1
bus_address=unix:path=/tmp/host-system-bus
busctl_json() {
  timeout --kill-after=2s 5s /usr/bin/busctl \
    --address="$bus_address" --no-pager --json=short "$@"
}
json_string() {
  /usr/bin/python3 -I -c "import json,sys; value=json.load(sys.stdin)[\"data\"]; value=value[0] if isinstance(value,list) and len(value)==1 else value; assert isinstance(value,str); print(value)"
}
unit_json="$(busctl_json call org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager GetUnit s "$unit")"
unit_path="$(printf "%s\\n" "$unit_json" | json_string)"
case "$unit_path" in
  /org/freedesktop/systemd1/unit/*) ;;
  *) printf "MISSING unit=%s has unsafe D-Bus object path=%s\\n" "$unit" "$unit_path"; exit 1 ;;
esac
state_json="$(busctl_json get-property org.freedesktop.systemd1 "$unit_path" org.freedesktop.systemd1.Unit LoadState)"
fragment_json="$(busctl_json get-property org.freedesktop.systemd1 "$unit_path" org.freedesktop.systemd1.Unit FragmentPath)"
control_group_json="$(busctl_json get-property org.freedesktop.systemd1 "$unit_path" org.freedesktop.systemd1.Slice ControlGroup)"
state="$(printf "%s\\n" "$state_json" | json_string)"
fragment="$(printf "%s\\n" "$fragment_json" | json_string)"
control_group="$(printf "%s\\n" "$control_group_json" | json_string)"
if [[ "$state" != loaded || -z "$fragment" ]]; then
  printf "MISSING unit=%s LoadState=%s FragmentPath=%s\n" "$unit" "$state" "$fragment"
  exit 1
fi
case "$fragment" in
  /run/systemd/transient/*)
    printf "MISSING transient-unit=%s\n" "$fragment"
    exit 1
    ;;
esac
case "$control_group" in
  /*) ;;
  *) printf "MISSING unit=%s has unsafe ControlGroup=%s\n" "$unit" "$control_group"; exit 1 ;;
esac
case "$control_group" in
  /|*//*|*/./*|*/../*|*/.|*/..|*/)
    printf "MISSING unit=%s has unsafe ControlGroup=%s\n" "$unit" "$control_group"
    exit 1
    ;;
esac
d="/hostcg$control_group"
if [[ ! -d "$d" ]]; then printf "MISSING %s\n" "$d"; exit 1; fi
read_or() { if [[ -r "$1" ]]; then cat "$1"; else printf "%s" "$2"; fi; }
configured=0
[[ "$(read_or "$d/memory.max" max)" != max ]] && configured=1
[[ "$(read_or "$d/memory.high" max)" != max ]] && configured=1
[[ "$(read_or "$d/memory.swap.max" max)" != max ]] && configured=1
[[ "$(read_or "$d/cpu.max" "max 100000")" != "max 100000" ]] && configured=1
[[ "$(read_or "$d/cpu.weight" 100)" != 100 ]] && configured=1
[[ "$(read_or "$d/io.weight" "default 100")" != "default 100" ]] && configured=1
if [[ "$configured" != 1 ]]; then printf "UNCONFIGURED %s\n" "$d"; exit 1; fi
printf "OK unit=%s LoadState=%s ControlGroup=%s\n" "$unit" "$state" "$control_group"
sleep 2
' _ "$slice")" || die 'could not start the detached slice-verification probe'

# The initial cap is present in docker run; update it immediately as required
# for every container started from the cockpit.
docker update --cpus=3 "$PROBE_NAME" >/dev/null \
  || die 'could not apply the slice-verification probe CPU cap'
if [[ ! "$probe_id" =~ ^[0-9a-f]{64}$ ]]; then
  id_prefix="${probe_id:0:80}"
  printf -v id_prefix_quoted '%q' "$id_prefix"
  die "Docker returned a malformed slice-verification container ID (characters=${#probe_id}, prefix=${id_prefix_quoted})"
fi
PROBE_ID="$probe_id"
[[ -s "$PROBE_CIDFILE" && "$(<"$PROBE_CIDFILE")" == "$PROBE_ID" ]] \
  || die 'slice-verification container ID does not match its cidfile'

inventory="$(docker inspect --format \
  '{{.Id}}|{{.Name}}|{{index .Config.Labels "assay.cgroup-parent.owner"}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.CgroupParent}}' \
  "$PROBE_ID" 2>/dev/null)" \
  || die 'could not verify slice-probe ownership and placement'
[[ "$inventory" == "$PROBE_ID|/$PROBE_NAME|$PROBE_OWNER|3000000000|$probe_parent" ]] \
  || die "slice-probe ownership, cap, or parent mismatch: $inventory"

probe_status="$(timeout --signal=TERM --kill-after=5s 30s docker wait "$PROBE_ID")" \
  || die 'could not collect slice-verification probe exit status'
probe_output="$(timeout --kill-after=5s 15s docker logs "$PROBE_ID" 2>&1)" \
  || die 'could not collect slice-verification probe output'
[[ "$probe_status" == 0 ]] || die "host slice verification failed: $probe_output"
case "$probe_output" in
  OK\ *) ;;
  MISSING\ *) die "requested host slice is not a loaded, instantiated slice: $probe_output" ;;
  UNCONFIGURED\ *) die "requested host slice has only kernel-default resource knobs: $probe_output" ;;
  *) die "could not verify requested host slice: $probe_output" ;;
esac

docker rm "$PROBE_NAME" >/dev/null \
  || die 'could not remove the slice-verification probe container'
PROBE_ID=""
PROBE_CIDFILE=""
printf '%s\n' "$slice"
