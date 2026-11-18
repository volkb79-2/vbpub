#!/usr/bin/env bash
# Offline dry-run test of install-btrfs.sh's bootloader selection. No disk is touched and no command from the printed
# install plan is executed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/install-btrfs.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
cases=0

bad() { echo "FAIL $*"; fail=1; }

run_dry() { # run_dry LABEL ARGS...
  local label=$1; shift
  cases=$((cases + 1))
  "$INSTALL" --dry-run --yes --device /dev/dr-not-used "$@" >"$WORK/$label.out" 2>"$WORK/$label.err"
  local rc=$?
  [ "$rc" = 0 ] || bad "$label: expected exit 0, got $rc"
}

contains() { # contains LABEL FILE NEEDLE
  cases=$((cases + 1))
  grep -Fq -- "$3" "$2" || bad "$1: '$2' lacks '$3'"
}

absent() {
  cases=$((cases + 1))
  if grep -Fq -- "$3" "$2"; then bad "$1: '$2' unexpectedly contains '$3'"; fi
}

exact_line() { # exact_line LABEL FILE LINE
  cases=$((cases + 1))
  grep -Fqx -- "$3" "$2" || bad "$1: '$2' lacks exact line '$3'"
}

run_dry grub-default
contains grub-default-package "$WORK/grub-default.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'
contains grub-default-install "$WORK/grub-default.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state install-grub'
absent grub-default-no-systemd-package "$WORK/grub-default.err" 'systemd-boot,'
absent grub-default-no-systemd-config "$WORK/grub-default.out" 'BOOTLOADER=systemd-boot'

run_dry grub-explicit --bootloader grub
contains grub-explicit-package "$WORK/grub-explicit.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'

run_dry sdboot --bootloader systemd-boot
contains sdboot-packages "$WORK/sdboot.err" \
  '--include=linux-image-amd64,systemd-boot,systemd-boot-efi,btrfs-progs'
contains sdboot-bootctl "$WORK/sdboot.err" \
  'chroot /mnt/drtarget bootctl install --esp-path=/boot/efi --no-variables'
contains sdboot-config "$WORK/sdboot.out" 'BOOTLOADER=systemd-boot'
exact_line sdboot-marker "$WORK/sdboot.out" 'systemd-boot'
absent sdboot-no-grub-package "$WORK/sdboot.err" 'linux-image-amd64,grub-efi-amd64'
absent sdboot-no-grub-installer "$WORK/sdboot.err" 'dr-state install-grub'
contains sdboot-menu-lesson "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state gen-menu --default @os-0001'

# bootctl must run after initramfs generation and before the menu is generated from the copied kernels.
cases=$((cases + 1))
bc=$(grep -nF 'bootctl install --esp-path=/boot/efi --no-variables' "$WORK/sdboot.err" | cut -d: -f1)
gm=$(grep -nF 'gen-menu --default @os-0001' "$WORK/sdboot.err" | cut -d: -f1)
[ -n "$bc" ] && [ -n "$gm" ] && [ "$bc" -lt "$gm" ] || bad "sdboot bootctl must precede gen-menu"

cases=$((cases + 1))
if "$INSTALL" --dry-run --yes --device /dev/dr-not-used --bootloader grub2 >/dev/null 2>&1; then
  bad "invalid bootloader accepted"
fi

[ "$fail" = 0 ] && echo "test-install-btrfs-bootloader: OK ($cases cases)"
exit "$fail"
