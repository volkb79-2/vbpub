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

run_dry grub-default
contains grub-default-package "$WORK/grub-default.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'
contains python3-package "$WORK/grub-default.err" 'systemd-sysv,python3,openssh-server'
contains grub-default-install "$WORK/grub-default.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state install-grub'
absent grub-default-no-state-helper "$WORK/grub-default.err" '_state-name'
absent grub-default-no-systemd-package "$WORK/grub-default.err" 'systemd-boot,'

run_dry grub-explicit --bootloader grub
contains grub-explicit-package "$WORK/grub-explicit.err" \
  '--include=linux-image-amd64,grub-efi-amd64,btrfs-progs'

run_dry sdboot --bootloader systemd-boot
contains sdboot-packages "$WORK/sdboot.err" \
  '--include=linux-image-amd64,systemd-boot,systemd-boot-efi,btrfs-progs'
contains sdboot-bootloader-install "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state install-systemd-boot'
contains sdboot-divert-dir "$WORK/sdboot.err" \
  'chroot /mnt/drtarget mkdir -p /etc/kernel/dr-disabled'
contains sdboot-divert-postinst "$WORK/sdboot.err" \
  'chroot /mnt/drtarget dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postinst-zz-systemd-boot /etc/kernel/postinst.d/zz-systemd-boot'
contains sdboot-divert-postrm "$WORK/sdboot.err" \
  'chroot /mnt/drtarget dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postrm-zz-systemd-boot /etc/kernel/postrm.d/zz-systemd-boot'
contains sdboot-disable-kernel-install-bls-dir "$WORK/sdboot.err" \
  '+ mkdir -p /mnt/drtarget/etc/kernel/install.d'
contains sdboot-disable-kernel-install-bls "$WORK/sdboot.err" \
  '+ ln -s /dev/null /mnt/drtarget/etc/kernel/install.d/90-loaderentry.install'
contains restricted-esp-install-mount "$WORK/sdboot.err" 'mount -o umask=0077'
absent sdboot-no-grub-package "$WORK/sdboot.err" 'linux-image-amd64,grub-efi-amd64'
absent sdboot-no-grub-installer "$WORK/sdboot.err" 'dr-state install-grub'
contains sdboot-menu-lesson "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state gen-menu --default @os-'
contains initial-state-captured "$WORK/sdboot.err" \
  'chroot /mnt/drtarget /usr/local/sbin/dr-state initialize-state @os-000001-'
contains normalized-payload-owners "$WORK/sdboot.err" \
  '--owner=0 --group=0'
contains normalized-payload-extract "$WORK/sdboot.err" \
  'tar -C /mnt/drtarget --same-owner -xf -'
absent payload-copy-preserves-builder "$WORK/sdboot.err" 'cp -a '
absent grub-no-systemd-hook-diversion "$WORK/grub-default.err" 'dpkg-divert --local --add --rename'
contains apt-preinstall-hook "$INSTALL" 'DPkg::Pre-Install-Pkgs { "/usr/local/sbin/dr-state apt-pre-install-pkgs"; };'
contains apt-plan-version "$INSTALL" 'DPkg::Tools::Options::/usr/local/sbin/dr-state::Version "2";'
contains apt-plan-fd "$INSTALL" 'DPkg::Tools::Options::/usr/local/sbin/dr-state::InfoFD "3";'
contains apt-preinvoke-fallback "$INSTALL" 'dr-state snapshot --reason apt || exit 100'

# Assemble the archive with the production helper as the lane's non-root user. The archive records root ownership;
# extraction as root then reproduces it. Existing destination directories are omitted so extraction cannot alter them.
cases=$((cases + 1))
[ "$(id -u)" != 0 ] || bad "payload ownership test requires the lane's non-root uid"
sed -n '/^# BEGIN payload-copy helpers$/,/^# END payload-copy helpers$/p' \
  "$INSTALL" >"$WORK/payload-helpers.sh"
# shellcheck disable=SC1091  # extracted from the installer under test
source "$WORK/payload-helpers.sh"

PAYLOAD_SOURCE=$WORK/payload-source
PAYLOAD_TARGET=$WORK/payload-target
mkdir -p \
  "$PAYLOAD_SOURCE/usr/local/sbin" \
  "$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state" \
  "$PAYLOAD_SOURCE/etc/apt/apt.conf.d" \
  "$PAYLOAD_TARGET/usr/local/lib" \
  "$PAYLOAD_TARGET/usr/local/sbin" \
  "$PAYLOAD_TARGET/etc/apt"
printf 'launcher\n' >"$PAYLOAD_SOURCE/usr/local/sbin/dr-state"
printf 'core\n' >"$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state/core.py"
printf 'hook\n' >"$PAYLOAD_SOURCE/etc/apt/apt.conf.d/80dr-state"
chmod 0755 "$PAYLOAD_SOURCE/usr/local/sbin/dr-state"
chmod 0644 "$PAYLOAD_SOURCE/usr/local/lib/dr-state/dr_state/core.py"
mkdir -p "$PAYLOAD_TARGET/usr/local/__pycache__"
printf 'stale\n' >"$PAYLOAD_TARGET/usr/local/__pycache__/keep.pyc"
chmod 0711 "$PAYLOAD_TARGET/usr"
chmod 2775 "$PAYLOAD_TARGET/usr/local"
chmod 0700 "$PAYLOAD_TARGET/usr/local/sbin"
chmod 0750 "$PAYLOAD_TARGET/etc"

PAYLOAD_ARCHIVE_UNDER_TEST=$WORK/payload.tar
files_payload_archive "$PAYLOAD_SOURCE" "$PAYLOAD_TARGET" "$PAYLOAD_ARCHIVE_UNDER_TEST"
tar --numeric-owner -tvf "$PAYLOAD_ARCHIVE_UNDER_TEST" >"$WORK/payload.attrs"
tar -tf "$PAYLOAD_ARCHIVE_UNDER_TEST" >"$WORK/payload.names"
for member in \
  usr/local/sbin/dr-state \
  usr/local/lib/dr-state/dr_state/core.py \
  etc/apt/apt.conf.d/80dr-state; do
  cases=$((cases + 1))
  line=$(grep -F " $member" "$WORK/payload.attrs" || true)
  case "$line" in
    *" 0/0 "*) ;;
    *) bad "payload archive member $member is not uid/gid 0: '$line'" ;;
  esac
done
for existing in usr usr/local usr/local/sbin etc; do
  cases=$((cases + 1))
  grep -Fxq "$existing" "$WORK/payload.names" \
    && bad "payload archive would rewrite existing target directory $existing"
done
for pair in \
  "usr/local/sbin/dr-state:-rwxr-xr-x" \
  "usr/local/lib/dr-state/dr_state/core.py:-rw-r--r--"; do
  member=${pair%:*}; want=${pair##*:}
  cases=$((cases + 1))
  got=$(awk -v member="$member" '$NF == member { print $1 }' "$WORK/payload.attrs")
  [ "$got" = "$want" ] || bad "payload archive member $member mode is '$got', want '$want'"
done

tar -C "$PAYLOAD_TARGET" --no-same-owner -xf "$PAYLOAD_ARCHIVE_UNDER_TEST"
for pair in "usr:711" "usr/local:2775" "usr/local/sbin:700" "etc:750"; do
  path=${pair%:*}; want=${pair##*:}
  cases=$((cases + 1))
  got=$(stat -c %a "$PAYLOAD_TARGET/$path")
  [ "$got" = "$want" ] || bad "payload copy changed existing directory $path mode $want -> $got"
done
cases=$((cases + 1))
[ -f "$PAYLOAD_TARGET/usr/local/__pycache__/keep.pyc" ] \
  || bad "payload copy deleted a foreign destination file"
cases=$((cases + 1))
[ -z "$(find "$PAYLOAD_TARGET" -type d -name __pycache__ ! -path "$PAYLOAD_TARGET/usr/local/__pycache__" -print -quit)" ] \
  || bad "payload copy introduced generated bytecode"

# The BLS override must precede debootstrap's systemd-boot/kernel package configuration; hook diversions and
# dr-state installation follow package installation but precede state capture and menu generation.
cases=$((cases + 1))
kd=$(grep -nF 'mkdir -p /mnt/drtarget/etc/kernel/install.d' "$WORK/sdboot.err" | cut -d: -f1)
kp=$(grep -nF 'ln -s /dev/null /mnt/drtarget/etc/kernel/install.d/90-loaderentry.install' "$WORK/sdboot.err" | cut -d: -f1)
dp=$(grep -nF 'debootstrap --include=linux-image-amd64,systemd-boot,systemd-boot-efi' "$WORK/sdboot.err" | cut -d: -f1)
bc=$(grep -nF 'install-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
si=$(grep -nF 'initialize-state @os-000001-' "$WORK/sdboot.err" | cut -d: -f1)
dd=$(grep -nF 'mkdir -p /etc/kernel/dr-disabled' "$WORK/sdboot.err" | cut -d: -f1)
pi=$(grep -nF 'dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postinst-zz-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
pr=$(grep -nF 'dpkg-divert --local --add --rename --divert /etc/kernel/dr-disabled/postrm-zz-systemd-boot' "$WORK/sdboot.err" | cut -d: -f1)
gm=$(grep -nF 'gen-menu --default @os-000001-' "$WORK/sdboot.err" | cut -d: -f1)
[ -n "$kd" ] && [ -n "$kp" ] && [ -n "$dp" ] && [ -n "$bc" ] && [ -n "$dd" ] && [ -n "$pi" ] && [ -n "$pr" ] && [ -n "$si" ] && [ -n "$gm" ] \
  && [ "$kd" -lt "$kp" ] && [ "$kp" -lt "$dp" ] && [ "$dp" -lt "$bc" ] \
  && [ "$bc" -lt "$dd" ] && [ "$dd" -lt "$pi" ] && [ "$pi" -lt "$pr" ] \
  && [ "$pr" -lt "$si" ] && [ "$si" -lt "$gm" ] \
  || bad "sdboot BLS override must precede debootstrap, and bootloader setup/state capture must precede gen-menu"

cases=$((cases + 1))
if "$INSTALL" --dry-run --yes --device /dev/dr-not-used --bootloader grub2 >/dev/null 2>&1; then
  bad "invalid bootloader accepted"
fi

[ "$fail" = 0 ] && echo "test-install-btrfs-bootloader: OK ($cases cases)"
exit "$fail"
