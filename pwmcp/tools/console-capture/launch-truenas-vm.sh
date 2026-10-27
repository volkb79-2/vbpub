#!/bin/sh
# Launch ONE QEMU TCG UEFI guest for the TrueNAS console capture (no KVM here).
#
# Usage: [VGA_DEV=virtio-vga] launch-truenas-vm.sh <workdir> <iso|none>
#
# <workdir> must already contain (create them once, per run):
#   disk.qcow2     qemu-img create -q -f qcow2 <workdir>/disk.qcow2 20G
#   OVMF_VARS.fd   cp /usr/share/OVMF/OVMF_VARS_4M.fd <workdir>/OVMF_VARS.fd   (the guest's UEFI NVRAM)
# It creates: qmp.sock, qemu.pid, serial.log. Same shape as
# scripts/debian-install-v2/testing/vm/vmctl (q35, tcg multi-thread, user networking).
#
# Resource caps: nice -n 19, ionice -c3, -smp 2, -m 3072, timeout 14400 s. There is NO cgroup/slice cap:
# the devcontainer has no systemd, so `systemd-run --slice=dev-background.slice` (used by the
# repository runner on the host) is not available here. On a host that has it, wrap this script
# in it. Run ONE guest at a time and watch load / MemAvailable.
#
# VGA_DEV defaults to virtio-vga: with std `VGA` QMP screendump of the TrueNAS kernel console
# returned a garbage image under QEMU 10.0.13.
set -eu
W="${1:?usage: launch-truenas-vm.sh <workdir> <iso|none>}"
ISO="${2:?usage: launch-truenas-vm.sh <workdir> <iso|none>}"
VGA_DEV="${VGA_DEV:-virtio-vga}"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"

[ -r "$OVMF_CODE" ] || { echo "launch: OVMF firmware not found: $OVMF_CODE (apt install ovmf, or set OVMF_CODE)" >&2; exit 1; }
[ -f "$W/OVMF_VARS.fd" ] || { echo "launch: missing $W/OVMF_VARS.fd (cp /usr/share/OVMF/OVMF_VARS_4M.fd '$W/OVMF_VARS.fd')" >&2; exit 1; }
[ -f "$W/disk.qcow2" ] || { echo "launch: missing $W/disk.qcow2 (qemu-img create -q -f qcow2 '$W/disk.qcow2' 20G)" >&2; exit 1; }
if [ "$ISO" != "none" ]; then
  [ -r "$ISO" ] || { echo "launch: ISO not readable: $ISO" >&2; exit 1; }
fi

# Build the argument list in "$@" so every path stays one quoted word.
set --
if [ "$ISO" != "none" ]; then
  set -- -drive "file=$ISO,format=raw,media=cdrom,readonly=on,if=none,id=cd0" -device ide-cd,drive=cd0,bootindex=1
fi

exec nice -n 19 ionice -c3 timeout 14400 qemu-system-x86_64 \
  -name truenas-console-capture -machine q35 -accel tcg,thread=multi -cpu max \
  -smp 2 -m 3072 \
  -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
  -drive "if=pflash,format=raw,file=$W/OVMF_VARS.fd" \
  -drive "file=$W/disk.qcow2,format=qcow2,if=none,id=hd0,cache=writeback" -device virtio-blk-pci,drive=hd0,bootindex=2 \
  "$@" \
  -nic user,model=virtio-net-pci \
  -device "$VGA_DEV" -display none -monitor none \
  -qmp "unix:$W/qmp.sock,server=on,wait=off" \
  -pidfile "$W/qemu.pid" -serial "file:$W/serial.log"
