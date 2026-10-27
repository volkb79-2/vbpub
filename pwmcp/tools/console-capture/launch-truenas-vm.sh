#!/bin/sh
# Launch ONE QEMU TCG UEFI guest for the TrueNAS console capture (no KVM here).
# Usage: launch-truenas-vm.sh <workdir> <iso|none>
# Same shape as scripts/debian-install-v2/testing/vm/vmctl (q35, tcg multi-thread, user networking).
set -eu
W="$1"; ISO="$2"
CD=""
if [ "$ISO" != "none" ]; then
  CD="-drive file=$ISO,format=raw,media=cdrom,readonly=on,if=none,id=cd0 -device ide-cd,drive=cd0,bootindex=1"
fi
exec nice -n 19 ionice -c3 timeout 14400 qemu-system-x86_64 \
  -name truenas-console-capture -machine q35 -accel tcg,thread=multi -cpu max \
  -smp 2 -m 3072 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file="$W/OVMF_VARS.fd" \
  -drive file="$W/disk.qcow2",format=qcow2,if=none,id=hd0,cache=writeback -device virtio-blk-pci,drive=hd0,bootindex=2 \
  $CD \
  -nic user,model=virtio-net-pci \
  -device "${VGA_DEV:-VGA}" -display none -monitor none \
  -qmp "unix:$W/qmp.sock,server=on,wait=off" \
  -pidfile "$W/qemu.pid" -serial "file:$W/serial.log"
