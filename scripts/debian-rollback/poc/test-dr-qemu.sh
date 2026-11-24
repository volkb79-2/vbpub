#!/usr/bin/env bash
# Pins the hardening flags of dr-qemu.sh (copied from vm-lab's docker run argv) so a later
# edit cannot drop one silently. Uses --dry-run only: no docker, no QEMU needed.
# Each flag is checked TOGETHER with its value (two adjacent argv lines), so swapping
# --memory and --memory-swap or moving a value to the wrong flag is caught.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
bad() { echo "FAIL $*"; fail=1; }
pair() { # pair FLAG VALUE: the argv has FLAG immediately followed by VALUE
  printf '%s\n' "$OUT" | awk -v f="$1" -v v="$2" 'prev == f && $0 == v {ok=1} {prev=$0} END {exit !ok}' \
    || bad "argv lacks '$1 $2'"
}
single() { printf '%s\n' "$OUT" | grep -qxF -- "$1" || bad "argv lacks '$1'"; }
absent() { if printf '%s\n' "$OUT" | grep -qxF -- "$1"; then bad "argv must not contain '$1'"; fi; }
base_args=(start --dry-run --name drb1 --state-dir /s --image img:1 --network dr-poc-net
  --cgroup-parent dev-gates.slice --memory-mib 2048 --cpus 2 --ovmf-code /c --ovmf-vars /v
  --base-raw /b.raw --data-disk-size 8589934592)
args=("${base_args[@]}" --iso /seed.iso)
OUT=$("$HERE/dr-qemu.sh" "${args[@]}")

single --init
single --detach
single --read-only
pair --name drb1
pair --cgroup-parent dev-gates.slice
pair --cpus 2
pair --cpu-shares 64
pair --memory 2684354560
pair --memory-swap 2684354560
pair --pids-limit 128
pair --cap-drop ALL
pair --security-opt no-new-privileges
pair --tmpfs /tmp:rw,noexec,nosuid,size=64m
pair --network dr-poc-net
pair --user "$(id -u):$(id -g)"
pair --mount type=bind,src=/s,dst=/state
pair --mount type=bind,src=/c,dst=/ovmf/OVMF_CODE_4M.fd,readonly
pair --mount type=bind,src=/v,dst=/ovmf/OVMF_VARS_4M.fd,readonly
pair --mount type=bind,src=/b.raw,dst=/base/disk.raw,readonly
pair --env VM_BOOT_ORDER=c
pair --mount type=bind,src=/seed.iso,dst=/media/boot.iso,readonly
pair --env VM_CDROM_ISO=/media/boot.iso
pair --entrypoint /bin/sh
single img:1
# the optional config disk (CIDATA stick): absent by default, read-only when given
absent --env=VM_CONFIG_DISK=/media/config.img
if printf '%s\n' "$OUT" | grep -qF 'VM_CONFIG_DISK=/media/config.img'; then bad "config disk env present without --config-disk"; fi
OUT_CFG=$("$HERE/dr-qemu.sh" "${args[@]}" --config-disk /cfg.img)
printf '%s\n' "$OUT_CFG" | awk 'prev == "--mount" && $0 == "type=bind,src=/cfg.img,dst=/media/config.img,readonly" {ok=1} {prev=$0} END {exit !ok}' \
  || bad "config disk is not bind-mounted read-only"
printf '%s\n' "$OUT_CFG" | awk 'prev == "--env" && $0 == "VM_CONFIG_DISK=/media/config.img" {ok=1} {prev=$0} END {exit !ok}' \
  || bad "config disk env missing"
for needle in 'id=cfgdisk,format=raw,readonly=on' 'serial=drcfg001'; do
  printf '%s\n' "$OUT_CFG" | grep -qF -- "$needle" || bad "guest script lacks '$needle'"
done
for flag in --publish -p --privileged --cap-add --pid --ipc --device; do absent "$flag"; done
# the QEMU side: no monitor, no display, qmp only on a unix socket in the state dir, TCG without nested virt
for needle in '-machine q35,accel=tcg' '-cpu max,-vmx,-svm' '-monitor none -display none' \
              'cp --sparse=always /base/disk.raw /state/disk.raw' 'chmod u+w /state/disk.raw' \
              '-qmp unix:/state/qmp.sock,server=on,wait=off' 'cache=unsafe'; do
  printf '%s\n' "$OUT" | grep -qF -- "$needle" || bad "guest script lacks '$needle'"
done
boot_needle="-boot \"order=\$VM_BOOT_ORDER,menu=on\""
printf '%s\n' "$OUT" | grep -qF -- "$boot_needle" || bad "guest script lacks the validated QEMU boot order"
OUT_LIVE=$("$HERE/dr-qemu.sh" "${args[@]}" --boot-order dc)
printf '%s\n' "$OUT_LIVE" | awk 'prev == "--env" && $0 == "VM_BOOT_ORDER=dc" {ok=1} {prev=$0} END {exit !ok}' \
  || bad "Live ISO boot order is not CD-ROM then disk"
OUT_SEED_ALIAS=$("$HERE/dr-qemu.sh" "${base_args[@]}" --seed-iso /seed.iso)
printf '%s\n' "$OUT_SEED_ALIAS" | awk 'prev == "--env" && $0 == "VM_CDROM_ISO=/media/boot.iso" {ok=1} {prev=$0} END {exit !ok}' \
  || bad "legacy --seed-iso spelling no longer attaches its ISO"

# validation: each of these must be refused
refuse() {
  if "$HERE/dr-qemu.sh" "${args[@]}" "$@" >/dev/null 2>&1; then bad "accepted: $*"; fi
}
refuse_without_iso() {
  if "$HERE/dr-qemu.sh" "${base_args[@]}" "$@" >/dev/null 2>&1; then bad "accepted without ISO: $*"; fi
}
refuse --memory-mib 100
refuse --cpus 9
refuse --network host
refuse --network none
refuse --network container:other
refuse --state-dir /s,src=/etc
refuse --ovmf-code '/c with space'
refuse --config-disk /cfg,src=/etc
refuse --data-disk-size notanumber
refuse --forward 22
refuse --boot-order d
refuse --boot-order cdc
refuse_without_iso --boot-order dc
refuse --iso /seed.iso --seed-iso /seed.iso
refuse --base-qcow2 /b.qcow2   # exclusive with --base-raw

[ "$fail" = 0 ] && echo "test-dr-qemu: OK"
exit "$fail"
