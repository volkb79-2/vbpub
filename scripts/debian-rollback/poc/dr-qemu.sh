#!/usr/bin/env bash
# PoC-ONLY QEMU guest launcher for hosts where vm-lab cannot run (bare shell on v1001).
#
# vm-lab was NOT used for the PoC runs on v1001: its `start` needs to run inside a cockpit
# container (inspect_current_container(), vm-lab/src/vm_lab/core.py:682, which inspects every
# running container) and a container with host /sys/fs/cgroup and the D-Bus socket
# (find_cgroup_inspector(), core.py:~705). This script mirrors vm-lab's `docker run` argv
# (core.py build of create_argv, ~lines 1905-1950) and QEMU command (_HOST_SCRIPT_TEMPLATE,
# ~lines 1740-1830) as of main 5b107f4bd, WITHOUT its admission checks, its `--label` pair
# (vm-lab's cleanup and admission do not see these containers) and without `--rm` (a restart
# under the same name needs the old container removed by exact name). The caller does the
# admission by hand before every start (one guest, slice memory, PSI) and logs it.
# test-dr-qemu.sh pins the hardening flags so this copy cannot drift silently.
#
#   dr-qemu.sh start --name N --state-dir DIR --image IMG --network NET --cgroup-parent SLICE
#       --memory-mib M --cpus C --ovmf-code F --ovmf-vars F
#       (--base-raw FILE | --base-qcow2 FILE)  [--seed-iso FILE] [--data-disk-size BYTES]...
#       [--forward HOSTPORT:GUESTPORT]... [--cpu-shares 64] [--resume] [--dry-run]
#   dr-qemu.sh stop --name N
#
# State dir must be empty on a first start (disk.raw or disk-overlay.qcow2, OVMF vars copy,
# data-disk-N.qcow2, serial.log, qmp.sock end up there). Container memory = guest + 512 MiB,
# swap equal to memory, no published ports; forwards are reachable at the container address.
set -euo pipefail

die() { echo "dr-qemu: $*" >&2; exit 1; }

cmd=${1:-}; [ $# -gt 0 ] && shift
NAME="" STATE="" IMAGE="" NETWORK="" SLICE="" MEM="" CPUS="" OCODE="" OVARS=""
BASE_RAW="" BASE_QCOW2="" SEED="" SHARES=64 RESUME=0 DRY=0
declare -a SIZES=() FORWARDS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME=${2:?}; shift 2 ;;
    --state-dir) STATE=${2:?}; shift 2 ;;
    --image) IMAGE=${2:?}; shift 2 ;;
    --network) NETWORK=${2:?}; shift 2 ;;
    --cgroup-parent) SLICE=${2:?}; shift 2 ;;
    --memory-mib) MEM=${2:?}; shift 2 ;;
    --cpus) CPUS=${2:?}; shift 2 ;;
    --ovmf-code) OCODE=${2:?}; shift 2 ;;
    --ovmf-vars) OVARS=${2:?}; shift 2 ;;
    --base-raw) BASE_RAW=${2:?}; shift 2 ;;
    --base-qcow2) BASE_QCOW2=${2:?}; shift 2 ;;
    --seed-iso) SEED=${2:?}; shift 2 ;;
    --data-disk-size) SIZES+=("${2:?}"); shift 2 ;;
    --forward) FORWARDS+=("${2:?}"); shift 2 ;;
    --cpu-shares) SHARES=${2:?}; shift 2 ;;
    --resume) RESUME=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$cmd" in
  stop)
    [ -n "$NAME" ] || die "--name is required"
    exec docker stop --time 20 "$NAME" ;;
  start) ;;
  *) die "usage: dr-qemu.sh start|stop ..." ;;
esac

for v in NAME STATE IMAGE NETWORK SLICE MEM CPUS OCODE OVARS; do
  [ -n "${!v}" ] || die "missing --$(echo "$v" | tr '[:upper:]' '[:lower:]')"
done
[[ "$NAME" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "bad --name"
[[ "$MEM" =~ ^[0-9]+$ ]] && [ "$MEM" -ge 512 ] && [ "$MEM" -le 8192 ] || die "--memory-mib must be 512..8192"
[[ "$CPUS" =~ ^[1-4]$ ]] || die "--cpus must be 1..4"
case "$NETWORK" in host|none|bridge|container:*|"") die "--network must name a dedicated user-defined network, not '$NETWORK'" ;; esac
for p in "$STATE" "$OCODE" "$OVARS" "$BASE_RAW" "$BASE_QCOW2" "$SEED"; do
  case "$p" in *,*|*\ *|*=*) die "path with comma, space or '=' refused (would inject --mount options): $p" ;; esac
done
for s in "${SIZES[@]:-}"; do
  [ -z "$s" ] || [[ "$s" =~ ^[0-9]+$ ]] || die "--data-disk-size must be a number of bytes: $s"
done
[ -n "$BASE_RAW$BASE_QCOW2" ] || die "need --base-raw or --base-qcow2"
[ -z "$BASE_RAW" ] || [ -z "$BASE_QCOW2" ] || die "--base-raw and --base-qcow2 are exclusive"
[ "${#FORWARDS[@]}" -ge 1 ] || FORWARDS=(2222:22)

MEM_BYTES=$(( (MEM + 512) * 1024 * 1024 ))
# shellcheck disable=SC2054  # the commas belong to docker's --tmpfs option syntax
declare -a argv=(
  docker run --detach --init --name "$NAME"
  --cgroup-parent "$SLICE"
  --cpus "$CPUS"
  --cpu-shares "$SHARES"
  --memory "$MEM_BYTES"
  --memory-swap "$MEM_BYTES"
  --pids-limit 128
  --cap-drop ALL
  --security-opt no-new-privileges
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=64m
  --network "$NETWORK"
  --user "$(id -u):$(id -g)"
  --mount "type=bind,src=$STATE,dst=/state"
  --mount "type=bind,src=$OCODE,dst=/ovmf/OVMF_CODE_4M.fd,readonly"
  --mount "type=bind,src=$OVARS,dst=/ovmf/OVMF_VARS_4M.fd,readonly"
  --env "VM_NAME=$NAME"
  --env "VM_MODE=$([ "$RESUME" = 1 ] && echo resume || echo create)"
  --env "VM_MEMORY_MIB=$MEM" --env "VM_CPUS=$CPUS"
  --env "VM_EXTRA_DISK_SIZES_BYTES=$(IFS=,; echo "${SIZES[*]:-}")"
)
if [ -n "$BASE_RAW" ]; then
  argv+=(--mount "type=bind,src=$BASE_RAW,dst=/base/disk.raw,readonly" --env VM_DISK_FORMAT=raw)
else
  argv+=(--mount "type=bind,src=$BASE_QCOW2,dst=/base/disk.qcow2,readonly" --env VM_DISK_FORMAT=qcow2)
fi
if [ -n "$SEED" ]; then
  argv+=(--mount "type=bind,src=$SEED,dst=/media/seed.iso,readonly" --env VM_READONLY_MEDIA=/media/seed.iso)
fi
hostfwd=""
for f in "${FORWARDS[@]}"; do
  [[ "$f" =~ ^[0-9]+:[0-9]+$ ]] || die "bad --forward $f"
  hostfwd+="hostfwd=tcp::${f%%:*}-:${f##*:},"
done
argv+=(--env "VM_HOSTFWD=${hostfwd%,}")

# Same guest script as vm-lab's (create/resume, disks on high PCI slots so OVMF boot paths
# stay stable, serial log + socket, qmp socket, no display); the disk is cloned inside the
# container because the host (v1001) has no reflink.
read -r -d '' SCRIPT <<'EOF' || true
set -eu
command -v qemu-system-x86_64 >/dev/null
command -v qemu-img >/dev/null
if [ "$VM_MODE" = "create" ]; then
  cp /ovmf/OVMF_CODE_4M.fd /state/OVMF_CODE_4M.fd
  cp /ovmf/OVMF_VARS_4M.fd /state/OVMF_VARS_4M.fd
  if [ "$VM_DISK_FORMAT" = "raw" ]; then
    test ! -e /state/disk.raw
    cp --sparse=always /base/disk.raw /state/disk.raw
  else
    test ! -e /state/disk-overlay.qcow2
    qemu-img create -q -f qcow2 -F qcow2 -b /base/disk.qcow2 /state/disk-overlay.qcow2
  fi
fi
if [ "$VM_DISK_FORMAT" = "raw" ]; then
  disk_args="file=/state/disk.raw,if=virtio,format=raw,cache=unsafe"
else
  disk_args="file=/state/disk-overlay.qcow2,if=virtio,format=qcow2,cache=unsafe"
fi
set --
index=0
old_ifs=$IFS; IFS=,
for size in ${VM_EXTRA_DISK_SIZES_BYTES:-}; do
  index=$((index + 1))
  disk="/state/data-disk-${index}.qcow2"
  if [ "$VM_MODE" = "create" ]; then qemu-img create -q -f qcow2 -o preallocation=off "$disk" "$size"; fi
  set -- "$@" \
    -drive "file=$disk,if=none,id=data${index},format=qcow2,cache=unsafe" \
    -device "virtio-blk-pci,drive=data${index},serial=drdata$(printf '%03d' "$index"),addr=$(printf '0x%x' "$((7 + index))")"
done
IFS=$old_ifs
if [ -n "${VM_READONLY_MEDIA:-}" ]; then set -- "$@" -cdrom "$VM_READONLY_MEDIA"; fi
umask 077
exec qemu-system-x86_64 \
  -name "$VM_NAME" -machine q35,accel=tcg -cpu max,-vmx,-svm \
  -smp "$VM_CPUS" -m "$VM_MEMORY_MIB" -rtc base=utc,clock=host \
  -drive if=pflash,format=raw,readonly=on,file=/state/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=/state/OVMF_VARS_4M.fd \
  -drive "$disk_args" \
  -netdev "user,id=net0,$VM_HOSTFWD" -device virtio-net-pci,netdev=net0 \
  -boot order=c,menu=on -monitor none -display none \
  "$@" \
  -chardev socket,id=serial0,path=/state/serial.sock,server=on,wait=off,logfile=/state/serial.log,logappend=on -serial chardev:serial0 \
  -qmp unix:/state/qmp.sock,server=on,wait=off
EOF
argv+=(--entrypoint /bin/sh "$IMAGE" -eu -c "$SCRIPT")

if [ "$DRY" = 1 ]; then printf '%s\n' "${argv[@]}"; exit 0; fi
[ -d "$STATE" ] || die "state dir $STATE does not exist"
if [ "$RESUME" != 1 ] && [ -n "$(ls -A "$STATE")" ]; then die "state dir is not empty (first start needs an empty one)"; fi
exec "${argv[@]}"
