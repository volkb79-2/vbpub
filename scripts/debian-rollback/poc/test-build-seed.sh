#!/usr/bin/env bash
# Offline test of build-seed.sh's backend marker, staging, real ISO packing and marker extraction.
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

assert_tmpdir_empty() { # assert_tmpdir_empty LABEL DIR
  local label=$1 dir=$2 remaining
  remaining=$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit)
  [ -z "$remaining" ] \
    || bad "$label: isolated TMPDIR retained $remaining"
}

# Exercise CLI cleanup independently of the real xorriso. The temporary staging directory must
# disappear whether packing succeeds or fails.
mkdir "$WORK/fake-xorriso-ok" "$WORK/fake-xorriso-fail" "$WORK/tmp-success" "$WORK/tmp-failure"
# shellcheck disable=SC2016  # these are literal expressions for the generated fake commands
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'out=' \
  'while [ "$#" -gt 0 ]; do' \
  '  case "$1" in' \
  '    -o) out=$2; shift 2 ;;' \
  '    *) shift ;;' \
  '  esac' \
  'done' \
  '[ -n "$out" ]' \
  'printf "fake iso\n" >"$out"' >"$WORK/fake-xorriso-ok/xorriso"
# shellcheck disable=SC2016  # exit status is the whole generated failure stub
printf '%s\n' '#!/bin/bash' 'exit 7' >"$WORK/fake-xorriso-fail/xorriso"
chmod +x "$WORK/fake-xorriso-ok/xorriso" "$WORK/fake-xorriso-fail/xorriso"

PATH="$WORK/fake-xorriso-ok:$PATH" TMPDIR="$WORK/tmp-success" \
  "$HERE/build-seed.sh" --bootloader systemd-boot "$WORK/cleanup-success.iso" \
  >"$WORK/cleanup-success.out" 2>"$WORK/cleanup-success.err"
rc=$?
[ "$rc" = 0 ] || bad "isolated success: build rc=$rc: $(tr '\n' ' ' <"$WORK/cleanup-success.err")"
assert_tmpdir_empty "isolated success" "$WORK/tmp-success"

PATH="$WORK/fake-xorriso-fail:$PATH" TMPDIR="$WORK/tmp-failure" \
  "$HERE/build-seed.sh" --bootloader grub "$WORK/cleanup-failure.iso" \
  >"$WORK/cleanup-failure.out" 2>"$WORK/cleanup-failure.err"
rc=$?
[ "$rc" != 0 ] || bad "isolated failure: failed xorriso stub was reported as success"
assert_tmpdir_empty "isolated failure" "$WORK/tmp-failure"

# The guest must require the generated marker and pass its selected backend to the installer.
grep -Fq '[ -f /mnt/seed/bootloader ]' "$USER_DATA" \
  || bad "user-data does not require /mnt/seed/bootloader"

# Execute the marker-selection block through its real installer invocation, with /mnt/seed
# redirected to a disposable fake seed. The mount, apt and disk commands are deliberately
# not extracted; the fake installer records the argv that the guest actually passes.
run_marker_block() { # run_marker_block LABEL SEED -> sets MARKER_RC and MARKER_OUT
  local label=$1 seed=$2
  MARKER_ARGS=$WORK/$label.argv
  mkdir -p "$seed/poc"
  # shellcheck disable=SC2016  # these are literal expressions for the generated fake installer
  printf '%s\n' '#!/bin/bash' 'printf "%s\n" "$@" > "$ARGV_OUT"' >"$seed/poc/install-btrfs.sh"
  chmod +x "$seed/poc/install-btrfs.sh"
  MARKER_OUT=$(awk -v seed="$seed" '
    /^      \[ -f \/mnt\/seed\/bootloader \]/ { block=1 }
    block {
      sub(/^      /, "")
      gsub("/mnt/seed", seed)
      print
      if ($0 ~ /^bash .*poc\/install-btrfs.sh/) exit
    }
    END { print "printf '\''BL:%s\\n'\'' \"$BL\"" }
  ' "$USER_DATA" | DEV=/dev/fake-target ARGV_OUT="$MARKER_ARGS" bash 2>&1)
  MARKER_RC=$?
  [ -n "$MARKER_OUT" ] || bad "$label: marker block produced no output"
}

assert_installer_argv() { # assert_installer_argv LABEL BOOTLOADER
  local label=$1 want=$2 arg
  local -a got=()
  local -a expected=(--device /dev/fake-target --yes --bootloader "$want")
  mapfile -t got <"$WORK/$label.argv" 2>/dev/null || got=()
  if [ "${#got[@]}" != "${#expected[@]}" ]; then
    bad "$label: installer argc=${#got[@]}, want=${#expected[@]}: ${got[*]}"
    return
  fi
  for arg in "${!expected[@]}"; do
    [ "${got[$arg]}" = "${expected[$arg]}" ] \
      || bad "$label: installer argv[$arg]='${got[$arg]}', want='${expected[$arg]}'"
  done
}

mkdir "$WORK/seed-absent" "$WORK/seed-grub" "$WORK/seed-systemd" "$WORK/seed-empty" "$WORK/seed-unknown"
printf 'grub\n' >"$WORK/seed-grub/bootloader"
printf 'systemd-boot\n' >"$WORK/seed-systemd/bootloader"
: >"$WORK/seed-empty/bootloader"
printf 'grub2\n' >"$WORK/seed-unknown/bootloader"

run_marker_block absent "$WORK/seed-absent"
if [ "$MARKER_RC" = 0 ] || ! grep -Fq 'DR-BUILD-FAIL missing-bootloader-marker' <<<"$MARKER_OUT"; then
  bad "marker-block-absent: rc=$MARKER_RC output='$MARKER_OUT'"
fi
[ ! -e "$WORK/absent.argv" ] || bad "marker-block-absent: installer ran without a bootloader marker"
run_marker_block explicit-grub "$WORK/seed-grub"
[ "$MARKER_RC" = 0 ] && [ "$MARKER_OUT" = "BL:grub" ] \
  || bad "marker-block-grub: rc=$MARKER_RC output='$MARKER_OUT'"
assert_installer_argv explicit-grub grub
run_marker_block systemd-boot "$WORK/seed-systemd"
[ "$MARKER_RC" = 0 ] && [ "$MARKER_OUT" = "BL:systemd-boot" ] \
  || bad "marker-block-systemd: rc=$MARKER_RC output='$MARKER_OUT'"
assert_installer_argv systemd-boot systemd-boot
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

if ! command -v xorriso >/dev/null 2>&1; then
  bad "xorriso is required in tester-unified for real seed ISO packing and extraction"
else
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
  covered="staging, isolated success/failure cleanup, executed user-data marker selection, installer pass-through, invalid-value refusal, ISO packing and extracted markers"
fi

[ "$fail" = 0 ] && echo "test-build-seed: OK ($covered)"
exit "$fail"
