#!/usr/bin/env bash
# Offline test of build-seed.sh's backend marker and staging. The staging function is
# tested directly; when xorriso is installed, the two complete CLI paths are also
# packed and their /bootloader files are extracted again.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_DATA=${DR_BUILD_USER_DATA:-$HERE/seed/user-data}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0

bad() { echo "FAIL $*"; fail=1; }

# Source the builder so its staging function can be called without creating an ISO.
# shellcheck disable=SC1091  # the path is derived from this test file's location
source "$HERE/build-seed.sh"

assert_stage() { # assert_stage LABEL BOOTLOADER
  local label=$1 bootloader=${2:-grub} stage=$WORK/stage-$1
  stage_seed "$stage" "$bootloader" >/dev/null 2>"$WORK/$label.err"
  local rc=$?
  if [ "$rc" != 0 ]; then
    bad "$label: staging failed with rc=$rc: $(tr '\n' ' ' <"$WORK/$label.err")"
    return
  fi
  cmp -s "$HERE/seed/user-data" "$stage/user-data" || bad "$label: user-data differs"
  cmp -s "$HERE/seed/meta-data" "$stage/meta-data" || bad "$label: meta-data differs"
  cmp -s "$HERE/install-btrfs.sh" "$stage/poc/install-btrfs.sh" || bad "$label: installer differs"
  cmp -s "$HERE/dr-state" "$stage/poc/dr-state" || bad "$label: dr-state differs"
  printf '%s\n' "$bootloader" >"$WORK/$label.marker"
  cmp -s "$WORK/$label.marker" "$stage/bootloader" || bad "$label: bootloader marker differs"
  local want
  want=$(printf '/%s\n' bootloader meta-data poc/dr-state poc/install-btrfs.sh user-data)
  local got
  got=$(find "$stage" -type f -printf '/%P\n' | sort)
  [ "$got" = "$want" ] || bad "$label: unexpected staging contents: ${got//$'\n'/ }"
}

assert_stage explicit-grub grub
assert_stage systemd-boot systemd-boot
assert_stage default-grub
diff -r "$WORK/stage-explicit-grub" "$WORK/stage-default-grub" >/dev/null \
  || bad "default staging is not byte-identical to explicit grub"

# The guest must read the marker, keep grub as the absent-file fallback, and pass the
# selected backend to the installer.
grep -Fq '/mnt/seed/bootloader' "$USER_DATA" \
  || bad "user-data does not read /mnt/seed/bootloader"
grep -Fq 'BL=grub' "$USER_DATA" \
  || bad "user-data has no grub fallback"
# shellcheck disable=SC2016  # the literal variable reference is what user-data must contain
grep -Fq -- '--bootloader "$BL"' "$USER_DATA" \
  || bad "user-data does not pass --bootloader to install-btrfs.sh"

# Execute the marker-selection block itself, with /mnt/seed redirected to a disposable
# fake seed. The mount, apt, package, disk and installer commands are deliberately not
# extracted: only the fallback, marker read and validation case are under test.
run_marker_block() { # run_marker_block LABEL SEED -> sets MARKER_RC and MARKER_OUT
  local label=$1 seed=$2
  MARKER_OUT=$(awk -v seed="$seed" '
    /^      bash \/mnt\/seed\/poc\/install-btrfs.sh/ { exit }
    /^      BL=grub$/ { block=1 }
    block {
      sub(/^      /, "")
      gsub("/mnt/seed", seed)
      print
    }
    END { print "printf '\''BL:%s\\n'\'' \"$BL\"" }
  ' "$USER_DATA" | bash 2>&1)
  MARKER_RC=$?
  [ -n "$MARKER_OUT" ] || bad "$label: marker block produced no output"
}

mkdir "$WORK/seed-absent" "$WORK/seed-grub" "$WORK/seed-systemd" "$WORK/seed-empty" "$WORK/seed-unknown"
printf 'grub\n' >"$WORK/seed-grub/bootloader"
printf 'systemd-boot\n' >"$WORK/seed-systemd/bootloader"
: >"$WORK/seed-empty/bootloader"
printf 'grub2\n' >"$WORK/seed-unknown/bootloader"

run_marker_block absent "$WORK/seed-absent"
[ "$MARKER_RC" = 0 ] && [ "$MARKER_OUT" = "BL:grub" ] \
  || bad "marker-block-absent: rc=$MARKER_RC output='$MARKER_OUT'"
run_marker_block explicit-grub "$WORK/seed-grub"
[ "$MARKER_RC" = 0 ] && [ "$MARKER_OUT" = "BL:grub" ] \
  || bad "marker-block-grub: rc=$MARKER_RC output='$MARKER_OUT'"
run_marker_block systemd-boot "$WORK/seed-systemd"
[ "$MARKER_RC" = 0 ] && [ "$MARKER_OUT" = "BL:systemd-boot" ] \
  || bad "marker-block-systemd: rc=$MARKER_RC output='$MARKER_OUT'"
run_marker_block empty "$WORK/seed-empty"
if ! { [ "$MARKER_RC" != 0 ] && grep -Fq 'DR-BUILD-FAIL unknown-bootloader:' <<<"$MARKER_OUT"; }; then
  bad "marker-block-empty: rc=$MARKER_RC output='$MARKER_OUT'"
fi
run_marker_block unknown "$WORK/seed-unknown"
if ! { [ "$MARKER_RC" != 0 ] && grep -Fq 'DR-BUILD-FAIL unknown-bootloader:grub2' <<<"$MARKER_OUT"; }; then
  bad "marker-block-unknown: rc=$MARKER_RC output='$MARKER_OUT'"
fi

# Unknown values are refused before staging or packing.
"$HERE/build-seed.sh" --bootloader grub2 "$WORK/unknown.iso" >"$WORK/unknown.out" 2>"$WORK/unknown.err"
rc=$?
[ "$rc" != 0 ] || bad "unknown bootloader accepted"
grep -Fq 'grub|systemd-boot' "$WORK/unknown.err" \
  || bad "unknown bootloader refusal does not name the accepted values"
[ ! -e "$WORK/unknown.iso" ] || bad "unknown bootloader created an ISO"

if command -v xorriso >/dev/null 2>&1; then
  for backend in grub systemd-boot; do
    iso=$WORK/$backend.iso
    "$HERE/build-seed.sh" --bootloader "$backend" "$iso" >"$WORK/$backend.pack.out" 2>"$WORK/$backend.pack.err"
    rc=$?
    if [ "$rc" != 0 ]; then
      bad "$backend ISO: build failed with rc=$rc: $(tr '\n' ' ' <"$WORK/$backend.pack.err")"
      continue
    fi
    xorriso -osirrox on -indev "$iso" -extract /bootloader "$WORK/$backend.extracted" \
      >"$WORK/$backend.extract.out" 2>"$WORK/$backend.extract.err"
    rc=$?
    if [ "$rc" != 0 ]; then
      bad "$backend ISO: xorriso could not extract /bootloader: $(tr '\n' ' ' <"$WORK/$backend.extract.err")"
      continue
    fi
    printf '%s\n' "$backend" >"$WORK/$backend.want"
    cmp -s "$WORK/$backend.want" "$WORK/$backend.extracted" \
      || bad "$backend ISO: extracted marker differs"
  done
  covered="staging, executed user-data marker selection, installer pass-through, invalid-value refusal, ISO packing and extracted markers"
else
  covered="staging, executed user-data marker selection, installer pass-through and invalid-value refusal; xorriso absent, so ISO packing and extracted markers are NOT covered"
fi

[ "$fail" = 0 ] && echo "test-build-seed: OK ($covered)"
exit "$fail"
