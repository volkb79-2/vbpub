#!/usr/bin/env bash
# build-iso.sh: remaster the official Debian live ISO into the debian-rollback unattended installer ISO.
#
#   build-iso.sh --base BASE.iso --out OUT.iso --bootloader grub|systemd-boot
#                [--config dr-install.conf] [--scratch PARENT_DIR] [--allow-unverified]
#
# No squashfs or initrd is rebuilt. The overlay is plain files added to the ISO9660 tree:
#   /dr/*                          the installer payload (dr-autoinstall, dr-plan, dr-pick-disk, install-btrfs.sh)
#   /dr/files/                     the complete target-image payload copied with modes
#   /dr/target-bootloader         immutable target choice selected at ISO build time
#   /dr/dr-install.conf            optional ISO-embedded config (--config); a CIDATA stick takes precedence
#   /live/config-hooks/10-dr-autoinstall   run by live-config when the command line has live-config.hooks=file:///run/live/medium/live/config-hooks/10-dr-autoinstall
#   /boot/grub/grub.cfg            two new first entries (unattended, and live with the payload), the rest unchanged
# The base ISO's boot catalog is replayed (`-boot_image any replay`), so UEFI booting works as in the base. The BIOS
# (isolinux) menu is unchanged: the installer is UEFI-only. md5sum.txt/sha256sum.txt on the medium describe the base
# files; they are not regenerated (the 'verify checksums' menu entry of the base will report the changed grub.cfg).
#
# Controller conditions: one process under nice -n 19 / ionice -c3, scratch only inside the given directory (default
# next to the output), no docker, no write to any device (output is a file), scratch removed on exit. The base ISO is
# read-only and its sha256 is checked against BASE.iso.sha256 when that file exists.
set -euo pipefail

BASE="" OUT="" CONF="" SCRATCH="" SCRATCH_PARENT="" BOOTLOADER="" ALLOW_UNVERIFIED=0
ORIG_ARGS=("$@")   # the option loop shifts $@ away; the nice re-exec below needs the original list
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "build-iso: $*" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE=${2:?}; shift 2 ;;
    --out) OUT=${2:?}; shift 2 ;;
    --bootloader) BOOTLOADER=${2:?}; shift 2 ;;
    --config) CONF=${2:?}; shift 2 ;;
    --scratch) SCRATCH_PARENT=${2:?}; shift 2 ;;   # a directory to work in; only a fresh subdirectory of it is created and removed
    --allow-unverified) ALLOW_UNVERIFIED=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -f "$BASE" ] || die "--base ISO not found"
[ -n "$OUT" ] || die "--out is required"
[ -n "$BOOTLOADER" ] || die "--bootloader is required; build a separate ISO for each target bootloader"
case "$BOOTLOADER" in grub|systemd-boot) ;; *) die "--bootloader must be grub or systemd-boot" ;; esac
case "$OUT" in /dev/*) die "refusing to write to a device path: $OUT" ;; esac
[ ! -e "$OUT" ] || [ -f "$OUT" ] || die "$OUT exists and is not a regular file"
[ -z "$CONF" ] || [ -f "$CONF" ] || die "--config file not found"
command -v xorriso >/dev/null || die "xorriso missing"

if [ "${BUILD_ISO_NICED:-}" != 1 ]; then BUILD_ISO_NICED=1 exec nice -n 19 ionice -c3 bash "$(readlink -f "$0")" "${ORIG_ARGS[@]}"; fi

if [ -f "$BASE.sha256" ]; then
  want=$(cut -d' ' -f1 <"$BASE.sha256"); got=$(sha256sum "$BASE" | cut -d' ' -f1)
  [ "$want" = "$got" ] || die "base ISO sha256 does not match $BASE.sha256"
elif [ "$ALLOW_UNVERIFIED" = 1 ]; then
  echo "build-iso: WARNING no $BASE.sha256, base ISO NOT verified (--allow-unverified)" >&2
else
  die "no $BASE.sha256 next to the base ISO; verify the download and add it, or pass --allow-unverified"
fi

# the scratch is always a fresh directory created here; only that directory is removed on exit, never --scratch itself
[ -n "$SCRATCH_PARENT" ] || SCRATCH_PARENT=$(dirname "$OUT")
mkdir -p "$SCRATCH_PARENT"
SCRATCH=$(mktemp -d "$SCRATCH_PARENT/dr-build.XXXXXX")
trap 'rm -rf "$SCRATCH"' EXIT
STAGE=$SCRATCH/stage
mkdir -p "$STAGE/dr/files" "$STAGE/hooks"
printf '%s\n' "$BOOTLOADER" >"$STAGE/dr/target-bootloader"

for f in dr-autoinstall dr-plan; do install -m 0755 "$HERE/$f" "$STAGE/dr/$f"; done
install -m 0755 "$HERE/../poc/dr-pick-disk" "$STAGE/dr/dr-pick-disk"
install -m 0755 "$HERE/../poc/install-btrfs.sh" "$STAGE/dr/install-btrfs.sh"
cp -a "$HERE/../files/." "$STAGE/dr/files/"
find "$STAGE/dr/files" \
  \( -type d -name __pycache__ -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
  -prune -exec rm -rf -- {} +
install -m 0755 "$HERE/config-hook" "$STAGE/hooks/10-dr-autoinstall"
[ -z "$CONF" ] || install -m 0644 "$CONF" "$STAGE/dr/dr-install.conf"
[ -z "$CONF" ] || "$HERE/dr-plan" --check-config --config "$CONF" >/dev/null 2>"$SCRATCH/conf.err" \
  || { cat "$SCRATCH/conf.err" >&2; die "the embedded config does not validate"; }

# grub.cfg: new first entries; kernel and initrd names are read from the base (the version changes per release)
xorriso -osirrox on -indev "$BASE" -extract /boot/grub/grub.cfg "$SCRATCH/grub.base" >/dev/null 2>&1 || die "cannot read /boot/grub/grub.cfg from the base"
chmod u+w "$SCRATCH/grub.base"
kernel=$(sed -n 's|^[[:space:]]*linux[[:space:]]*\(/live/vmlinuz[^ ]*\).*|\1|p' "$SCRATCH/grub.base" | head -n1)
initrd=$(sed -n 's|^[[:space:]]*initrd[[:space:]]*\(/live/initrd[^ ]*\).*|\1|p' "$SCRATCH/grub.base" | head -n1)
[ -n "$kernel" ] && [ -n "$initrd" ] || die "cannot find the live kernel/initrd in the base grub.cfg"
grep -q '^# Live boot' "$SCRATCH/grub.base" || die "base grub.cfg has no '# Live boot' marker line to insert before"
{
  echo 'set default=0'
  echo 'set timeout=10'
  awk -v k="$kernel" -v i="$initrd" '
    /^# Live boot/ && !done {
      print "# debian-rollback entries (added by build-iso.sh)"
      print "menuentry \"debian-rollback: UNATTENDED install to the disk named by the config (UEFI)\" {"
      print "\tlinux\t" k " boot=live components live-config.hooks=file:///run/live/medium/live/config-hooks/10-dr-autoinstall dr.mode=auto console=tty0 console=ttyS0,115200n8"
      print "\tinitrd\t" i
      print "}"
      print "menuentry \"debian-rollback: live system with the installer payload in /run/live/medium/dr\" {"
      print "\tlinux\t" k " boot=live components live-config.hooks=file:///run/live/medium/live/config-hooks/10-dr-autoinstall console=tty0 console=ttyS0,115200n8"
      print "\tinitrd\t" i
      print "}"
      done=1
    }
    { print }' "$SCRATCH/grub.base"
} >"$SCRATCH/grub.cfg"
# the base file starts with `source /boot/grub/config.cfg` which may set its own timeout/default: ours comes first and
# config.cfg is sourced after it, so also keep our values after that line
grep -qx 'source /boot/grub/config.cfg' "$SCRATCH/grub.cfg" || die "base grub.cfg has no 'source /boot/grub/config.cfg' line to re-assert default/timeout after"
sed -i 's|^source /boot/grub/config.cfg$|source /boot/grub/config.cfg\nset default=0\nset timeout=10|' "$SCRATCH/grub.cfg"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT.part"
xorriso -indev "$BASE" -outdev "$OUT.part" -boot_image any replay \
  -map "$STAGE/dr" /dr \
  -map "$STAGE/hooks" /live/config-hooks \
  -map "$SCRATCH/grub.cfg" /boot/grub/grub.cfg \
  -chown_r 0 /dr/files -- \
  -chgrp_r 0 /dr/files -- \
  -commit >"$SCRATCH/xorriso.log" 2>&1 || { tail -n 20 "$SCRATCH/xorriso.log" >&2; rm -f "$OUT.part"; die "xorriso failed"; }
mv "$OUT.part" "$OUT"
echo "$OUT $(stat -c %s "$OUT") bytes (target bootloader: $BOOTLOADER)"
