#!/usr/bin/env bash
# Build tiny synthetic remasters to prove the target bootloader is fixed in
# each ISO payload, independent of dr-install.conf or optional config media.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/base/boot/grub" "$WORK/base/live"
cat >"$WORK/base/boot/grub/grub.cfg" <<'EOF'
source /boot/grub/config.cfg
# Live boot
menuentry "live" {
  linux /live/vmlinuz-test
  initrd /live/initrd-test
}
EOF
printf 'kernel\n' >"$WORK/base/live/vmlinuz-test"
printf 'initrd\n' >"$WORK/base/live/initrd-test"
xorriso -as mkisofs -R -J -o "$WORK/base.iso" "$WORK/base" >/dev/null 2>&1
sha256sum "$WORK/base.iso" >"$WORK/base.iso.sha256"
printf 'HOSTNAME=fixture\n' >"$WORK/dr-install.conf"

for bootloader in grub systemd-boot; do
  out="$WORK/debian-rollback-$bootloader.iso"
  "$HERE/build-iso.sh" --base "$WORK/base.iso" --out "$out" --bootloader "$bootloader" \
    --config "$WORK/dr-install.conf" >"$WORK/build-$bootloader.out"
  xorriso -osirrox on -indev "$out" -extract /dr/target-bootloader "$WORK/marker" >/dev/null 2>&1
  [ "$(cat "$WORK/marker")" = "$bootloader" ] \
    || { echo "FAIL $bootloader: ISO carries target '$(<"$WORK/marker")'" >&2; exit 1; }
  xorriso -osirrox on -indev "$out" -extract /boot/grub/grub.cfg "$WORK/grub.cfg" >/dev/null 2>&1
  grep -q '/live/vmlinuz-test' "$WORK/grub.cfg"
done

if "$HERE/build-iso.sh" --base "$WORK/base.iso" --out "$WORK/no-choice.iso" >"$WORK/missing.out" 2>&1; then
  echo "FAIL build without --bootloader unexpectedly succeeded" >&2
  exit 1
fi
grep -q -- '--bootloader is required' "$WORK/missing.out"
if "$HERE/build-iso.sh" --base "$WORK/base.iso" --out "$WORK/bad-choice.iso" --bootloader zfs \
    >"$WORK/invalid.out" 2>&1; then
  echo "FAIL build with an invalid --bootloader unexpectedly succeeded" >&2
  exit 1
fi
grep -q -- '--bootloader must be grub or systemd-boot' "$WORK/invalid.out"

echo "test-build-iso: OK"
