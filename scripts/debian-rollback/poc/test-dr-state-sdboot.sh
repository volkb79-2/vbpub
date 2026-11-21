#!/usr/bin/env bash
# Offline tests for the systemd-boot backend's pure decisions (no btrfs, ESP, guest or root).
# Hidden hooks in dr-state:
#   _sdboot-kernel-id KVER SHA256
#   _sdboot-entry-id STATE
#   _sdboot-state-from-id ENTRY_ID
#   _sdboot-entry-text STATE KVER UUID KERNEL_ID
#   _sdboot-entry-refs              (BLS entry text on stdin; kernel ids on stdout)
#   _sdboot-orphan-kernels EXISTING_CSV REFERENCED_CSV
#   _sdboot-space-check NEEDED FREE
#   _sdboot-loader-conf DEFAULT_ID[.conf]  (existing loader.conf on stdin; rewritten on stdout)
#   _sdboot-select-backend CONF_VALUE MARKER_VALUE
#   _sdboot-conf-backend FILE
#   _sdboot-marker-backend FILE
#   _sdboot-source-value LABEL PRESENT VALUE
#   _sdboot-entry-ids STATE...
#   _sdboot-byte-length TEXT
#   _sdboot-dir-alloc ROOT CLUSTER RELDIR...
#   _sdboot-alloc-sum CLUSTER SIZE...
#   _sdboot-clean-scratch KERNEL_DIR ENTRY_DIR LOADER_CONF MARKER
#   _snapshot-name SEQUENCE TIMESTAMP SOURCE REASON
#
# Workflow fixtures run the real gen-menu/set-default/prune commands with DR_STATE_TEST_MODE=1 and a disposable
# temporary root. They never mount or touch /boot/efi or /.top.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DR="$HERE/dr-state"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
case_count=0

bad() { echo "FAIL $*"; fail=1; }

expect_exit() { # expect_exit LABEL WANT_RC COMMAND...
  local label=$1 want=$2; shift 2
  "$@" >"$WORK/out" 2>"$WORK/err"; local got=$?
  case_count=$((case_count + 1))
  [ "$got" = "$want" ] || bad "$label: want exit $want, got $got ($(tr '\n' ' ' <"$WORK/err"))"
}

expect_text() { # expect_text LABEL WANT COMMAND...
  local label=$1 want=$2; shift 2
  "$@" >"$WORK/out" 2>"$WORK/err"; local rc=$?
  case_count=$((case_count + 1))
  local got; got=$(cat "$WORK/out")
  if [ "$rc" != 0 ] || [ "$got" != "$want" ]; then
    echo "FAIL $label: want rc=0 text='$want'; got rc=$rc text='$got' ($(tr '\n' ' ' <"$WORK/err"))"
    fail=1
  fi
}

workflow_run() { # workflow_run ROOT FREE CLUSTER COMMAND...
  local root=$1 free=$2 cluster=$3
  shift 3
  env DR_STATE_TEST_MODE=1 DR_STATE_TEST_ROOT="$root" \
    DR_STATE_TEST_ESP_FREE="$free" DR_STATE_TEST_ESP_CLUSTER="$cluster" "$DR" "$@"
}

workflow_run_fail() { # workflow_run_fail ROOT STEP COMMAND...
  local root=$1 step=$2
  shift 2
  env DR_STATE_TEST_MODE=1 DR_STATE_TEST_ROOT="$root" \
    DR_STATE_TEST_ESP_FREE=999999999 DR_STATE_TEST_ESP_CLUSTER=4096 \
    DR_STATE_TEST_FAIL_STEP="$step" "$DR" "$@"
}

workflow_fixture() { # workflow_fixture ROOT full|sparse
  local root=$1 shape=${2:-full}
  mkdir -p "$root/esp" "$root/top" "$root/etc" "$root/run" \
    "$root/sys/firmware/efi/efivars" "$root/top/.dr-state"
  printf '000001\n' >"$root/top/.dr-state/last-sequence"
  printf 'BOOTLOADER=systemd-boot\n' >"$root/etc/dr-state.conf"
  printf '%s / btrfs defaults,noatime 0 0\n' "$UUID" >"$root/etc/fstab"
  if [ "$shape" = full ]; then
    mkdir -p "$root/esp/EFI/dr/kernels" "$root/esp/loader/entries"
  fi
}

workflow_state() { # workflow_state ROOT STATE
  local root=$1 state=$2
  mkdir -p "$root/top/$state/boot"
  if [[ "$state" =~ ^@os-([0-9]{6})- ]]; then
    local stored current
    stored=${BASH_REMATCH[1]}
    current=$(cat "$root/top/.dr-state/last-sequence")
    if [ "$((10#$stored))" -gt "$((10#$current))" ]; then
      printf '%s\n' "$stored" >"$root/top/.dr-state/last-sequence"
    fi
  fi
  printf 'kernel for %s\n' "$state" >"$root/top/$state/boot/vmlinuz-$KVER"
  printf 'initrd for %s\n' "$state" >"$root/top/$state/boot/initrd.img-$KVER"
}

workflow_remove_state_counter() { # ROOT -> remove the fixture counter to exercise fresh initialization
  rm -f -- "$1/top/.dr-state/last-sequence"
}

workflow_two_states() { # workflow_two_states ROOT
  local root=$1
  workflow_state "$root" @os-000001-20261118-120000Z-install
  workflow_state "$root" @os-000002-20261118-120100Z-apt
}

workflow_stale_default_fixture() { # ROOT: old default @os-000002-20261118-120100Z-apt exists, then its state disappears from TOP
  local root=$1
  workflow_fixture "$root" full
  workflow_two_states "$root"
  workflow_run "$root" 999999999 4096 gen-menu --default @os-000002-20261118-120100Z-apt \
    >"$WORK/out" 2>"$WORK/err" \
    || { bad "workflow-stale-setup: gen-menu failed"; return 1; }
  [ -s "$root/esp/loader/entries/os-000002-20261118-120100Z-apt.conf" ] \
    || { bad "workflow-stale-setup: missing old default entry"; return 1; }
  mv "$root/top/@os-000002-20261118-120100Z-apt" "$root/removed-state-0002"
}

assert_loader_default_exists() { # assert_loader_default_exists ROOT LABEL
  local root=$1 label=$2 count default_id default_entry
  case_count=$((case_count + 1))
  [ -s "$root/esp/loader/loader.conf" ] \
    || { bad "$label: loader.conf is missing"; return; }
  count=$(awk '$1 == "default" {n++} END {print n+0}' "$root/esp/loader/loader.conf")
  [ "$count" = 1 ] || { bad "$label: loader.conf has $count defaults"; return; }
  default_id=$(awk '$1 == "default" {print $2; exit}' "$root/esp/loader/loader.conf")
  case "$default_id" in
    *.conf) default_entry="$root/esp/loader/entries/$default_id" ;;
    *) default_entry="$root/esp/loader/entries/$default_id.conf" ;;
  esac
  [ -s "$default_entry" ] \
    || bad "$label: default $default_id has no entry file"
}

install_failing_loader_mv() { # ROOT -> PATH stub that fails only the loader.conf publication rename
  local root=$1
  mkdir -p "$root/fail-bin"
  # shellcheck disable=SC2016  # the single-quoted strings are the literal body of the generated stub
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -u' \
    'dest=${!#}' \
    'if [ "$dest" = "$FAIL_LOADER_CONF" ]; then exit 73; fi' \
    'exec /bin/mv "$@"' \
    >"$root/fail-bin/mv"
  chmod 755 "$root/fail-bin/mv"
}

install_recording_bootctl() { # ROOT -> PATH stub that records the exact argv
  local root=$1
  mkdir -p "$root/boot-bin"
  # shellcheck disable=SC2016  # the single-quoted strings are the literal body of the generated stub
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -eu' \
    'printf "%s\n" "$*" >>"${BOOTCTL_LOG:?}"' \
    >"$root/boot-bin/bootctl"
  chmod 755 "$root/boot-bin/bootctl"
}

write_sdboot_oneshot() { # ROOT VALUE -> efivarfs content with a four-byte attributes prefix
  local root=$1 value=$2
  mkdir -p "$root/sys/firmware/efi/efivars"
  {
    printf '\x07\x00\x00\x00'
    printf '%s' "$value" | iconv -f UTF-8 -t UTF-16LE
  } >"$root/sys/firmware/efi/efivars/LoaderEntryOneShot-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
}

install_fake_snapshot_btrfs() { # ROOT -> btrfs stub that creates the requested snapshot directory
  local root=$1
  mkdir -p "$root/btrfs-bin"
  # shellcheck disable=SC2016  # the single-quoted strings are the literal body of the generated stub
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -eu' \
    '[ "${1:-}" = subvolume ] && [ "${2:-}" = snapshot ]' \
    'dest=${!#}' \
    'mkdir -p -- "$dest"' \
    >"$root/btrfs-bin/btrfs"
  chmod 755 "$root/btrfs-bin/btrfs"
}

install_fake_maintenance_btrfs() { # ROOT -> btrfs stub for snapshot and delete operations
  local root=$1
  mkdir -p "$root/btrfs-bin"
  # shellcheck disable=SC2016  # the single-quoted strings are the literal body of the generated stub
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -eu' \
    'op=${2:?}' \
    'dest=${!#}' \
    'case "$op" in' \
    '  snapshot) source=${3:?}; mkdir -p -- "$dest"; printf "fixture-%s\n" "${source##*/}" >"$dest/.fixture-parent-uuid" ;;' \
    '  show) [ -d "$dest" ] || exit 1; printf "Name: %s\nUUID: fixture-%s\n" "$dest" "${dest##*/}"; [ ! -f "$dest/.fixture-parent-uuid" ] || printf "Parent UUID: %s\n" "$(cat "$dest/.fixture-parent-uuid")" ;;' \
    '  delete) if [ "${FAKE_BTRFS_FAIL_DELETE:-0}" = 1 ]; then exit 73; fi; /bin/rm -rf -- "$dest" ;;' \
    '  *) exit 64 ;;' \
    'esac' \
    >"$root/btrfs-bin/btrfs"
  chmod 755 "$root/btrfs-bin/btrfs"
}

install_fake_grub_editenv() { # ROOT -> grub-editenv stub with create/set/list support and an argv log
  local root=$1
  mkdir -p "$root/grub-bin"
  # shellcheck disable=SC2016  # the single-quoted strings are the literal body of the generated stub
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -eu' \
    'file=${1:?}' \
    'op=${2:?}' \
    'printf "%s\n" "${*:2}" >>"${GRUBEDITENV_LOG:?}"' \
    'case "$op" in' \
    '  create) : >"$file" ;;' \
    '  set) printf "%s\n" "${3#saved_entry=}" >"${GRUBEDITENV_STATE:?}" ;;' \
    '  list) [ ! -s "${GRUBEDITENV_STATE:?}" ] || printf "saved_entry=%s\n" "$(cat -- "$GRUBEDITENV_STATE")" ;;' \
    '  *) exit 64 ;;' \
    'esac' \
    >"$root/grub-bin/grub-editenv"
  chmod 755 "$root/grub-bin/grub-editenv"
}

workflow_prune_form() { # ROOT DEFAULT_ID ONESHOT_ID LABEL: dry-run prune must protect both entries
  local root=$1 default_id=$2 oneshot_id=$3 label=$4 id bare rc got default_state oneshot_state
  local doomed=@os-000100-20261118-120000Z-manual-spare
  workflow_fixture "$root" full
  default_state=@${default_id%.conf}
  oneshot_state=@${oneshot_id%.conf}
  for id in @os-000001-20261118-120000Z-install "$default_state" "$oneshot_state" "$doomed"; do
    mkdir -p "$root/top/$id"
  done
  for id in "$default_id" "$oneshot_id"; do
    bare=${id%.conf}
    printf '# generated by dr-state sdboot; do not edit\ntitle fixture %s\n' "$id" \
      >"$root/esp/loader/entries/$bare.conf"
  done
  printf 'default %s\ntimeout 5\n' "$default_id" >"$root/esp/loader/loader.conf"
  write_sdboot_oneshot "$root" "$oneshot_id"
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$root" 999999999 4096 prune --keep 0 --dry-run \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  got=$(cat "$WORK/out")
  if [ "$rc" != 0 ]; then
    bad "workflow-prune-protect-$label: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif [ "$got" != "would delete $doomed" ]; then
    bad "workflow-prune-protect-$label: wrong protection set: ${got//$'\n'/ }"
  fi
}

workflow_gen_preserves_oneshot() { # ROOT ONESHOT_VALUE LABEL: stale one-shot entry survives gen-menu
  local root=$1 oneshot=$2 label=$3 rc
  workflow_fixture "$root" full
  workflow_two_states "$root"
  workflow_run "$root" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err" \
    || { bad "workflow-oneshot-$label: menu setup failed"; return; }
  mv "$root/top/@os-000002-20261118-120100Z-apt" "$root/removed-state-0002"
  write_sdboot_oneshot "$root" "$oneshot"
  case_count=$((case_count + 1))
  workflow_run "$root" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ]; then
    bad "workflow-oneshot-$label: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif [ ! -s "$root/esp/loader/entries/os-000002-20261118-120100Z-apt.conf" ]; then
    bad "workflow-oneshot-$label: gen-menu deleted the entry named by LoaderEntryOneShot"
  fi
}

workflow_snapshot_reason() { # ROOT REASON WANT_SUFFIX: real snapshot path with a fake btrfs operation
  local root=$1 reason=$2 want=$3 created forbidden rc
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install
  install_fake_snapshot_btrfs "$root"
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
    workflow_run "$root" 999999999 4096 snapshot --reason "$reason" \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  created=$(find "$root/top" -mindepth 1 -maxdepth 1 -name "@os-000002-*-*$want" -print -quit)
  forbidden=$(find "$root/top" -mindepth 1 -maxdepth 1 -name '@os-*.conf' -print -quit)
  if [ "$rc" != 0 ]; then
    bad "workflow-snapshot-reason-$want: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif [ -z "$created" ]; then
    bad "workflow-snapshot-reason-$want: no snapshot ending in -$want"
  elif [ -n "$forbidden" ]; then
    bad "workflow-snapshot-reason-$want: created invalid .conf state $forbidden"
  fi
}

workflow_snapshot_overlong_reason() { # ROOT REASON LABEL: the constructed name must fit the entry-id grammar
  local root=$1 reason=$2 label=$3 created id id_len rc
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install
  install_fake_snapshot_btrfs "$root"
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
    workflow_run "$root" 999999999 4096 snapshot --reason "$reason" \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  created=$(find "$root/top" -mindepth 1 -maxdepth 1 -name '@os-000002-*' -print -quit)
  if [ "$rc" != 0 ]; then
    bad "workflow-snapshot-$label: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif [ -z "$created" ]; then
    bad "workflow-snapshot-$label: no snapshot state was created"
 elif ! id=$("$DR" _sdboot-entry-id "${created##*/}" 2>/dev/null); then
    bad "workflow-snapshot-$label: created non-selectable state $created"
  else
    id_len=$(printf '%s' "$id" | wc -c)
    [ "$id_len" -le 240 ] || bad "workflow-snapshot-$label: entry id is $id_len bytes"
  fi
}

workflow_grub_conf_state_fixture() { # ROOT: GRUB backend with a legacy state whose name ends in .conf
  local root=$1
  workflow_fixture "$root" full
  printf 'BOOTLOADER=grub\n' >"$root/etc/dr-state.conf"
  workflow_state "$root" @os-000001-20261118-120000Z-install.conf
  workflow_state "$root" @os-000002-20261118-120100Z-apt
  install_fake_grub_editenv "$root"
}

workflow_invalid_conf_fixture() { # ROOT: one invalid selectable-name state and one valid state
  local root=$1
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install.conf
  workflow_state "$root" @os-000002-20261118-120100Z-apt
}

state_with_entry_id_length() { # state_with_entry_id_length N -> @os- followed by id padding
  local length=$1 prefix=@os- padding suffix
  padding=$((length - 3))
  [ "$padding" -ge 0 ] || return 1
  suffix=$(printf '%*s' "$padding" '' | tr ' ' a)
  printf '%s%s\n' "$prefix" "$suffix"
}

workflow_assert_entry() { # workflow_assert_entry ROOT STATE KVER
  local root=$1 state=$2 kver=$3 entry_id source_kernel source_initrd sha256 kid
  local entry linux_path initrd_path options_line
  case_count=$((case_count + 1))
  entry_id=$("$DR" _sdboot-entry-id "$state") || { bad "workflow-entry-id: cannot map $state"; return; }
  source_kernel="$root/top/$state/boot/vmlinuz-$kver"
  source_initrd="$root/top/$state/boot/initrd.img-$kver"
  sha256=$(sha256sum -- "$source_initrd" | awk '{print $1}') \
    || { bad "workflow-entry-source-hash: cannot hash $source_initrd"; return; }
  kid=$("$DR" _sdboot-kernel-id "$kver" "$sha256") \
    || { bad "workflow-entry-kernel-id: cannot map $state kernel $kver"; return; }
  entry="$root/esp/loader/entries/$entry_id.conf"
  linux_path="/EFI/dr/kernels/$kid/vmlinuz"
  initrd_path="/EFI/dr/kernels/$kid/initrd.img"
  options_line="options root=UUID=$UUID rootflags=subvol=$state ro net.ifnames=0 console=tty0 console=ttyS0,115200"

  [ -s "$entry" ] || { bad "workflow-entry: missing $entry"; return; }
  grep -Fqx -- "title Debian $state (kernel $kver)" "$entry" \
    || bad "workflow-entry-title: $entry names the wrong kernel/state"
  grep -Fqx -- "linux $linux_path" "$entry" \
    || bad "workflow-entry-linux: $entry names the wrong kernel path"
  grep -Fqx -- "initrd $initrd_path" "$entry" \
    || bad "workflow-entry-initrd: $entry names the wrong initrd path"
  grep -Fqx -- "$options_line" "$entry" \
    || bad "workflow-entry-options: $entry names the wrong root/subvolume"

  for copy in vmlinuz initrd.img; do
    [ -s "$root/esp/EFI/dr/kernels/$kid/$copy" ] \
      || bad "workflow-entry-copy: missing or empty $kid/$copy"
  done
  [ "$(sha256sum -- "$root/esp/EFI/dr/kernels/$kid/vmlinuz" | awk '{print $1}')" \
      = "$(sha256sum -- "$source_kernel" | awk '{print $1}')" ] \
    || bad "workflow-entry-copy-hash: vmlinuz differs for $state"
  [ "$(sha256sum -- "$root/esp/EFI/dr/kernels/$kid/initrd.img" | awk '{print $1}')" = "$sha256" ] \
    || bad "workflow-entry-copy-hash: initrd differs for $state"
}

HASH_A=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
HASH_B=fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210
KVER=6.12.117+deb13-amd64
ID_A=6.12.117+deb13-amd64-0123456789ab
UUID=11111111-2222-3333-4444-555555555555

# Backend selection: absence is the only path to the GRUB default; conflict and unknown values fail loudly.
expect_text backend-default grub "$DR" _sdboot-select-backend '' ''
expect_text backend-explicit-grub grub "$DR" _sdboot-select-backend grub ''
expect_text backend-conf-systemd systemd-boot "$DR" _sdboot-select-backend systemd-boot ''
expect_text backend-marker-systemd systemd-boot "$DR" _sdboot-select-backend '' systemd-boot
expect_text backend-both-agree systemd-boot "$DR" _sdboot-select-backend systemd-boot systemd-boot
expect_exit backend-conflict 1 "$DR" _sdboot-select-backend grub systemd-boot
expect_exit backend-unknown 1 "$DR" _sdboot-select-backend grub2 ''
expect_text backend-source-present grub "$DR" _sdboot-source-value '/etc/dr-state.conf BOOTLOADER' 1 grub
expect_exit backend-source-absent 0 "$DR" _sdboot-source-value '/etc/dr-state.conf BOOTLOADER' 0 ''
expect_exit backend-source-empty-config 1 "$DR" _sdboot-source-value '/etc/dr-state.conf BOOTLOADER' 1 ''
expect_exit backend-source-empty-marker 1 "$DR" _sdboot-source-value '/boot/efi/EFI/dr/bootloader' 1 ''
printf 'BOOTLOADER=\n' >"$WORK/conf-empty"
expect_exit backend-empty-config-file 1 "$DR" _sdboot-conf-backend "$WORK/conf-empty"
printf '' >"$WORK/marker-empty"
expect_exit backend-empty-marker-file 1 "$DR" _sdboot-marker-backend "$WORK/marker-empty"
case_count=$((case_count + 1))
"$DR" _sdboot-conf-backend "$WORK/conf-empty" >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'BOOTLOADER is present but empty' "$WORK/err" \
  || bad "backend-empty-config-message: missing clear empty-key refusal"
case_count=$((case_count + 1))
"$DR" _sdboot-marker-backend "$WORK/marker-empty" >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'bootloader marker is present but empty' "$WORK/err" \
  || bad "backend-empty-marker-message: missing clear empty-marker refusal"

# (a) BLS entry text.
ENTRY_A=$(cat <<EOF
# generated by dr-state sdboot; do not edit
title Debian @os-000001-20261118-120000Z-install (kernel 6.12.117+deb13-amd64)
linux /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/vmlinuz
initrd /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-000001-20261118-120000Z-install ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-000001-20261118-120000Z-install
EOF
)
expect_text entry-text-main "$ENTRY_A" "$DR" _sdboot-entry-text @os-000001-20261118-120000Z-install "$KVER" "$UUID" "$ID_A"

ENTRY_SNAP=$(cat <<EOF
# generated by dr-state sdboot; do not edit
title Debian @os-000605-20261118-131158Z-manual-s5 (kernel 6.12.107+deb13-amd64)
linux /EFI/dr/kernels/6.12.107+deb13-amd64-fedcba987654/vmlinuz
initrd /EFI/dr/kernels/6.12.107+deb13-amd64-fedcba987654/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-000605-20261118-131158Z-manual-s5 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-000605-20261118-131158Z-manual-s5
EOF
)
expect_text entry-text-snapshot "$ENTRY_SNAP" "$DR" _sdboot-entry-text @os-000605-20261118-131158Z-manual-s5 \
  6.12.107+deb13-amd64 "$UUID" 6.12.107+deb13-amd64-fedcba987654
ENTRY_BACKPORTS=$(cat <<EOF
# generated by dr-state sdboot; do not edit
title Debian @os-000001-20261118-120000Z-install (kernel 7.2.12+deb13-amd64)
linux /EFI/dr/kernels/7.2.12+deb13-amd64-fedcba987654/vmlinuz
initrd /EFI/dr/kernels/7.2.12+deb13-amd64-fedcba987654/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-000001-20261118-120000Z-install ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-000001-20261118-120000Z-install
EOF
)
expect_text entry-text-backports "$ENTRY_BACKPORTS" "$DR" _sdboot-entry-text @os-000001-20261118-120000Z-install \
  7.2.12+deb13-amd64 "$UUID" 7.2.12+deb13-amd64-fedcba987654
expect_exit entry-text-rejects-bad-uuid 1 "$DR" _sdboot-entry-text @os-000001-20261118-120000Z-install "$KVER" 'not/a-uuid' "$ID_A"
expect_exit entry-text-rejects-kver-id-mismatch 1 "$DR" _sdboot-entry-text @os-000001-20261118-120000Z-install "$KVER" "$UUID" \
  6.12.107+deb13-amd64-fedcba987654

# (b) content-addressed kernel id.
expect_text kernel-id-a "$ID_A" "$DR" _sdboot-kernel-id "$KVER" "$HASH_A"
expect_text kernel-id-b "6.12.107+deb13-amd64-fedcba987654" "$DR" _sdboot-kernel-id \
  6.12.107+deb13-amd64 "$HASH_B"
expect_exit kernel-id-short-hash 1 "$DR" _sdboot-kernel-id "$KVER" 0123456789ab
expect_exit kernel-id-bad-hash 1 "$DR" _sdboot-kernel-id "$KVER" \
  0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdeg
expect_exit kernel-id-uppercase-hash 1 "$DR" _sdboot-kernel-id "$KVER" \
  0123456789ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef
expect_exit kernel-id-bad-kver 1 "$DR" _sdboot-kernel-id '6.12/evil' "$HASH_A"
expect_exit kernel-id-empty-kver 1 "$DR" _sdboot-kernel-id '' "$HASH_A"

# (f) reversible state <-> entry-id mapping. `@os-*` maps to `os-*`; the mapping rejects unsafe names.
expect_text entry-id-main os-000001-20261118-120000Z-install "$DR" _sdboot-entry-id @os-000001-20261118-120000Z-install
expect_text entry-id-snapshot os-000605-20261118-131158Z-manual-s5 "$DR" _sdboot-entry-id @os-000605-20261118-131158Z-manual-s5
expect_text entry-id-punctuation os-000001-20261118-100000Z-manual-s5_test-v2 "$DR" _sdboot-entry-id @os-000001-20261118-100000Z-manual-s5_test-v2
expect_text state-from-id-main @os-000001-20261118-120000Z-install "$DR" _sdboot-state-from-id os-000001-20261118-120000Z-install
expect_text state-from-id-main-conf @os-000001-20261118-120000Z-install "$DR" _sdboot-state-from-id os-000001-20261118-120000Z-install.conf
expect_text state-from-id-snapshot @os-000605-20261118-131158Z-manual-s5 "$DR" _sdboot-state-from-id os-000605-20261118-131158Z-manual-s5
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit entry-id-roundtrip 0 bash -c 's=$("$1" _sdboot-entry-id "$2") || exit; t=$("$1" _sdboot-state-from-id "$s") || exit; [ "$t" = "$2" ]' \
  _ "$DR" @os-000001-20261118-100000Z-manual-a_b-c-d
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit entry-id-roundtrip-conf 0 bash -c 's=$("$1" _sdboot-entry-id "$2") || exit; t=$("$1" _sdboot-state-from-id "$s.conf") || exit; [ "$t" = "$2" ]' \
  _ "$DR" @os-000001-20261118-100000Z-manual-a_b-c-d
expect_exit entry-id-rejects-no-prefix 1 "$DR" _sdboot-entry-id os-000001-20261118-120000Z-install
expect_exit entry-id-rejects-slash 1 "$DR" _sdboot-entry-id '@os-pre-2026/evil'
expect_exit entry-id-rejects-space 1 "$DR" _sdboot-entry-id '@os-pre-2026 evil'
expect_exit entry-id-rejects-dotdot 1 "$DR" _sdboot-entry-id '@os-pre-2026..evil'
expect_exit entry-id-rejects-embedded-at 1 "$DR" _sdboot-entry-id '@os-pre-2026@evil'
expect_exit entry-id-rejects-conf-suffix 1 "$DR" _sdboot-entry-id '@os-000001-20261118-120000Z-install.conf'
expect_exit state-from-id-rejects-at 1 "$DR" _sdboot-state-from-id '@os-000001-20261118-120000Z-install'
expect_exit state-from-id-rejects-conf-suffix 1 "$DR" _sdboot-state-from-id os-000001-20261118-120000Z-install.conf.conf
expect_exit state-from-id-rejects-slash 1 "$DR" _sdboot-state-from-id 'os-pre/evil'
expect_exit state-from-id-rejects-empty-tail 1 "$DR" _sdboot-state-from-id 'os-'
expect_text entry-ids-distinct "os-000001-20261118-120000Z-install
os-000605-20261118-131158Z-manual-s5" "$DR" _sdboot-entry-ids @os-000001-20261118-120000Z-install @os-000605-20261118-131158Z-manual-s5
expect_exit entry-ids-case-collision 1 "$DR" _sdboot-entry-ids @os-lower @os-LOWER
expect_exit entry-ids-conf-suffix 1 "$DR" _sdboot-entry-ids @os-000001-20261118-120000Z-install.conf
expect_exit entry-ids-unsafe-state 1 "$DR" _sdboot-entry-ids @os-ok '@os-bad/name'
case_count=$((case_count + 1))
"$DR" _sdboot-entry-ids @os-lower @os-LOWER >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'case-insensitively collide' "$WORK/err" \
  || bad "entry-ids-case-message: collision refusal does not explain the collision"

# Snapshot IDs use one origin/reason slug on both bootloaders; notes retain the full description.
expect_text snapshot-name-ordinary @os-000123-20261118-120000Z-manual-rollback \
  "$DR" _snapshot-name 000123 20261118120000 manual rollback
expect_text snapshot-name-conf-suffix @os-000123-20261118-120000Z-manual-rollback-conf \
  "$DR" _snapshot-name 000123 20261118120000 manual rollback.conf
expect_text snapshot-name-space @os-000123-20261118-120000Z-manual-bad-name \
  "$DR" _snapshot-name 000123 20261118120000 manual 'bad name'
expect_text snapshot-name-slash @os-000123-20261118-120000Z-manual-bad-name \
  "$DR" _snapshot-name 000123 20261118120000 manual 'bad/name'
expect_text snapshot-name-embedded-at @os-000123-20261118-120000Z-manual-bad-name \
  "$DR" _snapshot-name 000123 20261118120000 manual 'bad@name'
expect_text snapshot-name-control @os-000123-20261118-120000Z-manual-bad-name \
  "$DR" _snapshot-name 000123 20261118120000 manual $'bad\tname'
expect_text snapshot-name-grub-dot @os-000123-20261118-120000Z-manual-v1-2 \
  "$DR" _snapshot-name 000123 20261118120000 manual v1.2
expect_text snapshot-name-apt @os-000123-20261118-120000Z-apt \
  "$DR" _snapshot-name 000123 20261118120000 apt apt
expect_text initial-state-name @os-000001-20261118-120000Z-install \
  "$DR" _state-name 000001 20261118120000 install
expect_exit state-name-zero-sequence 1 "$DR" _state-name 000000 20261118120000 install
expect_exit state-name-invalid-date 1 "$DR" _state-name 000002 20261340120000 apt
expect_exit state-name-invalid-slug 1 "$DR" _state-name 000002 20261118120000 'manual/unsafe'
expect_exit snapshot-name-empty 1 "$DR" _snapshot-name 000123 20261118120000 manual ''
_LONG_REASON=$(printf '%*s' 400 '' | tr ' ' a)
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit snapshot-name-overlong-valid 0 bash -c \
  'n=$("$1" _snapshot-name "$2" "$3" manual "$4") || exit; i=$("$1" _sdboot-entry-id "$n") || exit; [ "${#i}" -le 240 ]' \
  _ "$DR" 000123 20261118120000 "$_LONG_REASON"
# A bounded slug keeps the ID portable on either backend.
_LONG_CONF_REASON="$(printf '%*s' 207 '' | tr ' ' a).confxxxxxxxxxxxxxxxxxxxx"
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit snapshot-name-overlong-conf-boundary 0 bash -c \
  'n=$("$1" _snapshot-name "$2" "$3" manual "$4") || exit; "$1" _sdboot-entry-id "$n" >/dev/null || exit; case "$n" in *.conf) exit 1 ;; esac' \
  _ "$DR" 000123 20261118120000 "$_LONG_CONF_REASON"
APT_PLAN_V2="$WORK/apt-plan-v2"
cat >"$APT_PLAN_V2" <<'EOF'
VERSION 2
APT::Get::CommandLine::AsString=apt%20install%20hello

hello - < 2.10-3 /var/cache/apt/archives/hello.deb
libc6:amd64 2.36-9 < 2.37-1 /var/cache/apt/archives/libc6.deb
oldpkg 1.0 > - **REMOVE**
manualpkg 1.0 = 1.0 **CONFIGURE**
EOF
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_text apt-plan-version2 $'install hello - -> 2.10-3\nupgrade libc6:amd64 2.36-9 -> 2.37-1\nremove oldpkg 1.0 -> -\nconfigure manualpkg 1.0 -> 1.0' \
  bash -c '"$1" _apt-plan-summary <"$2"' _ "$DR" "$APT_PLAN_V2"
APT_PLAN_V3="$WORK/apt-plan-v3"
cat >"$APT_PLAN_V3" <<'EOF'
VERSION 3

foo - - none < 1.0 amd64 same /var/cache/apt/archives/foo.deb
EOF
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_text apt-plan-version3 'install foo - -> 1.0' \
  bash -c '"$1" _apt-plan-summary <"$2"' _ "$DR" "$APT_PLAN_V3"
APT_PLAN_BAD="$WORK/apt-plan-bad"
printf 'VERSION 2\n\nnot-an-action\n' >"$APT_PLAN_BAD"
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit apt-plan-malformed-refused 1 bash -c '"$1" _apt-plan-summary <"$2"' _ "$DR" "$APT_PLAN_BAD"

# The same sanitizer protects snapshot callers, including hook-composed reasons.
workflow_snapshot_reason "$WORK/snapshot-reason-rollback" rollback.conf manual-rollback-conf
workflow_snapshot_reason "$WORK/snapshot-reason-apt" apt.conf manual-apt-conf
workflow_snapshot_reason "$WORK/snapshot-reason-kernel" kernel.conf manual-kernel-conf
workflow_snapshot_reason "$WORK/snapshot-reason-space" 'bad name' manual-bad-name
workflow_snapshot_reason "$WORK/snapshot-reason-slash" 'bad/name' manual-bad-name
workflow_snapshot_reason "$WORK/snapshot-reason-at" 'bad@name' manual-bad-name
workflow_snapshot_reason "$WORK/snapshot-reason-control" $'bad\tname' manual-bad-name
workflow_snapshot_overlong_reason "$WORK/snapshot-reason-overlong" "$_LONG_REASON" overlong

# Both backends use the same safe short slug and enumerate the result.
WF_GRUB_REASON="$WORK/wf-grub-reason"
workflow_grub_conf_state_fixture "$WF_GRUB_REASON"
install_fake_snapshot_btrfs "$WF_GRUB_REASON"
case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_REASON/grub-editenv.log" GRUBEDITENV_STATE="$WF_GRUB_REASON/grub-editenv.state" \
  DR_STATE=@os-000002-20261118-120100Z-apt PATH="$WF_GRUB_REASON/btrfs-bin:$WF_GRUB_REASON/grub-bin:$PATH" \
  workflow_run "$WF_GRUB_REASON" 999999999 4096 snapshot --reason v1.2 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
created=$(find "$WF_GRUB_REASON/top" -mindepth 1 -maxdepth 1 -name '@os-000003-*' -printf '%f\n' | head -n1)
if [ "$rc" != 0 ]; then
  bad "workflow-snapshot-grub-reason: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [[ "$created" != *-manual-v1-2 ]]; then
  bad "workflow-snapshot-grub-reason: expected the shared readable slug, got '$created'"
fi

WF_GRUB_SPACE_REASON="$WORK/wf-grub-space-reason"
workflow_grub_conf_state_fixture "$WF_GRUB_SPACE_REASON"
install_fake_snapshot_btrfs "$WF_GRUB_SPACE_REASON"
case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_SPACE_REASON/grub-editenv.log" \
GRUBEDITENV_STATE="$WF_GRUB_SPACE_REASON/grub-editenv.state" \
  DR_STATE=@os-000002-20261118-120100Z-apt PATH="$WF_GRUB_SPACE_REASON/btrfs-bin:$WF_GRUB_SPACE_REASON/grub-bin:$PATH" \
  workflow_run "$WF_GRUB_SPACE_REASON" 999999999 4096 snapshot --reason 'old-build' \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
created=$(find "$WF_GRUB_SPACE_REASON/top" -mindepth 1 -maxdepth 1 -name '@os-000003-*' -printf '%f\n' | head -n1)
if [ "$rc" != 0 ]; then
  bad "workflow-snapshot-grub-space-reason: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [[ "$created" != *-manual-old-build ]]; then
  bad "workflow-snapshot-grub-space-reason: reason slug is wrong: '$created'"
else
  workflow_state "$WF_GRUB_SPACE_REASON" "$created"
  GRUBEDITENV_LOG="$WF_GRUB_SPACE_REASON/grub-editenv.log" \
  GRUBEDITENV_STATE="$WF_GRUB_SPACE_REASON/grub-editenv.state" \
    PATH="$WF_GRUB_SPACE_REASON/grub-bin:$PATH" \
    workflow_run "$WF_GRUB_SPACE_REASON" 999999999 4096 gen-menu \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ]; then
    bad "workflow-snapshot-grub-menu: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif ! grep -Fq -- "menuentry \"Debian $created " "$WF_GRUB_SPACE_REASON/esp/EFI/dr/grub.cfg"; then
    bad "workflow-snapshot-grub-menu: snapshot is missing from the generated GRUB menu"
  fi
fi

# The legacy-name restriction is systemd-boot-only: GRUB keeps enumerating and selecting a .conf-named state.
WF_GRUB_CONF="$WORK/wf-grub-conf"
workflow_grub_conf_state_fixture "$WF_GRUB_CONF"
case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_CONF/grub-editenv.log" GRUBEDITENV_STATE="$WF_GRUB_CONF/grub-editenv.state" \
  PATH="$WF_GRUB_CONF/grub-bin:$PATH" workflow_run "$WF_GRUB_CONF" 999999999 4096 gen-menu \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-grub-conf-gen-menu: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- 'menuentry "Debian @os-000001-20261118-120000Z-install.conf (kernel ' "$WF_GRUB_CONF/esp/EFI/dr/grub.cfg"; then
  bad "workflow-grub-conf-gen-menu: legacy .conf state was omitted"
elif ! grep -Fq -- 'menuentry "Debian @os-000002-20261118-120100Z-apt (kernel ' "$WF_GRUB_CONF/esp/EFI/dr/grub.cfg"; then
  bad "workflow-grub-conf-gen-menu: ordinary state was omitted"
fi

case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_CONF/grub-editenv.log" GRUBEDITENV_STATE="$WF_GRUB_CONF/grub-editenv.state" \
  PATH="$WF_GRUB_CONF/grub-bin:$PATH" workflow_run "$WF_GRUB_CONF" 999999999 4096 list \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-grub-conf-list: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- '@os-000001-20261118-120000Z-install.conf kernel=' "$WORK/out"; then
  bad "workflow-grub-conf-list: legacy .conf state was omitted"
fi

case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_CONF/grub-editenv.log" GRUBEDITENV_STATE="$WF_GRUB_CONF/grub-editenv.state" \
  PATH="$WF_GRUB_CONF/grub-bin:$PATH" workflow_run "$WF_GRUB_CONF" 999999999 4096 \
  set-default @os-000001-20261118-120000Z-install.conf >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-grub-conf-set-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- 'set saved_entry=@os-000001-20261118-120000Z-install.conf' "$WF_GRUB_CONF/grub-editenv.log"; then
  bad "workflow-grub-conf-set-default: grub-editenv did not receive the legacy state"
fi

case_count=$((case_count + 1))
GRUBEDITENV_LOG="$WF_GRUB_CONF/grub-editenv.log" GRUBEDITENV_STATE="$WF_GRUB_CONF/grub-editenv.state" \
  PATH="$WF_GRUB_CONF/grub-bin:$PATH" workflow_run "$WF_GRUB_CONF" 999999999 4096 \
  gen-menu --default @os-000001-20261118-120000Z-install.conf >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-grub-conf-gen-menu-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- 'set saved_entry=@os-000001-20261118-120000Z-install.conf' "$WF_GRUB_CONF/grub-editenv.log"; then
  bad "workflow-grub-conf-gen-menu-default: gen-menu did not save the legacy state"
fi

case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install.conf GRUBEDITENV_LOG="$WF_GRUB_CONF/grub-editenv.log" \
  GRUBEDITENV_STATE="$WF_GRUB_CONF/grub-editenv.state" PATH="$WF_GRUB_CONF/grub-bin:$PATH" \
  workflow_run "$WF_GRUB_CONF" 999999999 4096 prune --keep 10 --dry-run \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-grub-conf-prune: legacy running state was refused ($(tr '\n' ' ' <"$WORK/err"))"
fi

# (d) ESP space check: needed bytes versus free bytes.
expect_exit space-needed-less 0 "$DR" _sdboot-space-check 1000 2000
expect_exit space-needed-equal 0 "$DR" _sdboot-space-check 2000 2000
expect_exit space-needed-wins 1 "$DR" _sdboot-space-check 2001 2000
expect_exit space-zero-needed 0 "$DR" _sdboot-space-check 0 0
expect_exit space-no-free 1 "$DR" _sdboot-space-check 1 0
expect_exit space-bad-needed 2 "$DR" _sdboot-space-check nope 2000
expect_exit space-negative-needed 2 "$DR" _sdboot-space-check -1 2000
expect_exit space-bad-free 2 "$DR" _sdboot-space-check 1000 nope
expect_text byte-length-ascii 3 "$DR" _sdboot-byte-length abc
expect_text byte-length-utf8 3 "$DR" _sdboot-byte-length 'aé'
expect_text alloc-sum-rounds 16 "$DR" _sdboot-alloc-sum 4 1 3 5
expect_text alloc-sum-zero 0 "$DR" _sdboot-alloc-sum 4 0
expect_exit alloc-sum-bad-size 1 "$DR" _sdboot-alloc-sum 4 3 nope
mkdir -p "$WORK/alloc-root"
expect_text dir-alloc-empty-tree 12 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 EFI/dr/kernels
expect_text dir-alloc-shared-parent 16 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 \
  EFI/dr/kernels EFI/dr/other
mkdir -p "$WORK/alloc-root/EFI"
expect_text dir-alloc-existing-parent 8 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 EFI/dr/kernels
mkdir -p "$WORK/alloc-root/EFI/dr/kernels"
expect_text dir-alloc-all-existing 0 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 EFI/dr/kernels
expect_text dir-alloc-loader 8 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 loader/entries
mkdir -p "$WORK/alloc-root/not-a-dir-parent"
printf x >"$WORK/alloc-root/not-a-dir-parent/file"
expect_exit dir-alloc-nondirectory 1 "$DR" _sdboot-dir-alloc "$WORK/alloc-root" 4 \
  not-a-dir-parent/file/child
case_count=$((case_count + 1))
"$DR" _sdboot-space-check 2001 2000 >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'need 2001 bytes, only 2000 bytes free' "$WORK/err" \
  || bad "space-refusal-message: missing need/free explanation"
case_count=$((case_count + 1))
"$DR" _sdboot-space-check nope 2000 >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'invalid needed-byte count' "$WORK/err" || bad "space-malformed-message: missing field name"

# (e) loader.conf rewrite: one default and timeout 5; every unrelated line survives verbatim.
LOADER_IN=$(cat <<'EOF'
# local settings
default os-old.conf
timeout 10
auto-entries yes
auto-firmware no
EOF
)
LOADER_WANT=$(cat <<'EOF'
# local settings
auto-entries yes
auto-firmware no
default os-000001-20261118-120000Z-install.conf
timeout 5
EOF
)
got=$(printf '%s\n' "$LOADER_IN" | "$DR" _sdboot-loader-conf os-000001-20261118-120000Z-install 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT" ]; then
  echo "FAIL loader-replace: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_WANT_ADD=$(cat <<'EOF'
default os-000001-20261118-120000Z-install.conf
timeout 5
EOF
)
got=$(printf 'timeout 10\n' | "$DR" _sdboot-loader-conf os-000001-20261118-120000Z-install 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT_ADD" ]; then
  echo "FAIL loader-add-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_WANT_NEW=$(cat <<'EOF'
default os-000001-20261118-120000Z-install.conf
timeout 5
EOF
)
got=$(printf '' | "$DR" _sdboot-loader-conf os-000001-20261118-120000Z-install 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT_NEW" ]; then
  echo "FAIL loader-new: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_DUP_IN=$(cat <<'EOF'
default os-a
keep=first
default os-b.conf
timeout 1
keep=second
timeout 9
EOF
)
LOADER_DUP_WANT=$(cat <<'EOF'
keep=first
keep=second
default os-new.conf
timeout 5
EOF
)
got=$(printf '%s\n' "$LOADER_DUP_IN" | "$DR" _sdboot-loader-conf os-new 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_DUP_WANT" ]; then
  echo "FAIL loader-duplicates: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_WANT_SUFFIX_ARG=$(cat <<'EOF'
default os-000001-20261118-120000Z-install.conf
timeout 5
EOF
)
got=$(printf 'timeout 10\n' | "$DR" _sdboot-loader-conf os-000001-20261118-120000Z-install.conf 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT_SUFFIX_ARG" ]; then
  echo "FAIL loader-suffix-arg: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_MIGRATE_IN=$(cat <<'EOF'
default os-old
timeout 10
EOF
)
LOADER_MIGRATE_WANT=$(cat <<'EOF'
default os-000001-20261118-120000Z-install.conf
timeout 5
EOF
)
got=$(printf '%s\n' "$LOADER_MIGRATE_IN" | "$DR" _sdboot-loader-conf os-000001-20261118-120000Z-install 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_MIGRATE_WANT" ]; then
  echo "FAIL loader-migrate-suffixless: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi
expect_exit loader-bad-default-id 1 bash -c "printf '' | \"\$1\" _sdboot-loader-conf '@os-000001-20261118-120000Z-install'" _ "$DR"
expect_exit loader-empty-without-default 1 bash -c "printf '' | \"\$1\" _sdboot-loader-conf ''" _ "$DR"
expect_exit loader-malformed-default-line 1 bash -c "printf 'default\\n' | \"\$1\" _sdboot-loader-conf 'os-000001-20261118-120000Z-install'" _ "$DR"
expect_exit loader-malformed-timeout-line 1 bash -c "printf 'timeout later\\n' | \"\$1\" _sdboot-loader-conf 'os-000001-20261118-120000Z-install'" _ "$DR"

# (c) only unreferenced ESP kernel directories are pruned; any entry reference protects one.
REFS_IN=$(cat <<EOF
$ENTRY_A
linux /EFI/dr/kernels/initrd-only-i-0123456789ab/initrd.img
initrd /EFI/dr/kernels/linux-only-l-fedcba987654/vmlinuz
linux /boot/vmlinuz-foreign
linux /EFI/dr/kernels/path/extra/vmlinuz
EOF
)
REFS_WANT=$(cat <<EOF
$ID_A
initrd-only-i-0123456789ab
linux-only-l-fedcba987654
EOF
)
got=$(printf '%s\n' "$REFS_IN" | "$DR" _sdboot-entry-refs 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$REFS_WANT" ]; then
  echo "FAIL entry-refs: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

MULTI_REFS_IN=$(cat <<'EOF'
linux /EFI/dr/kernels/multi-a-111111111111/vmlinuz /EFI/dr/kernels/multi-b-222222222222/vmlinuz
initrd /EFI/dr/kernels/multi-b-222222222222/initrd.img /EFI/dr/kernels/multi-c-333333333333/initrd.img
EOF
)
MULTI_REFS_WANT=$(cat <<'EOF'
multi-a-111111111111
multi-b-222222222222
multi-c-333333333333
EOF
)
got=$(printf '%s\n' "$MULTI_REFS_IN" | "$DR" _sdboot-entry-refs 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$MULTI_REFS_WANT" ]; then
  echo "FAIL entry-refs-multi-path: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

expect_text orphan-basic old-a-aaaaaaaaaaaa "$DR" _sdboot-orphan-kernels \
  'old-a-aaaaaaaaaaaa,keep-b-bbbbbbbbbbbb' 'old-c-cccccccccccc,keep-b-bbbbbbbbbbbb'
expect_text orphan-none "" "$DR" _sdboot-orphan-kernels \
  'keep-a-aaaaaaaaaaaa,keep-b-bbbbbbbbbbbb' 'keep-a-aaaaaaaaaaaa,keep-b-bbbbbbbbbbbb'
expect_text orphan-all "delete-a-aaaaaaaaaaaa
delete-b-bbbbbbbbbbbb" "$DR" _sdboot-orphan-kernels \
  'delete-a-aaaaaaaaaaaa,delete-b-bbbbbbbbbbbb' ''
expect_text orphan-order "z-old-dddddddddddd
a-old-eeeeeeeeeeee" "$DR" _sdboot-orphan-kernels \
  'z-old-dddddddddddd,keep-f-ffffffffffff,a-old-eeeeeeeeeeee' 'keep-f-ffffffffffff'
expect_exit orphan-bad-existing 1 "$DR" _sdboot-orphan-kernels '../escape' ''
expect_exit orphan-bad-reference 1 "$DR" _sdboot-orphan-kernels 'safe' '../escape'
expect_exit entry-refs-empty 0 bash -c "printf 'title only\\n' | \"\$1\" _sdboot-entry-refs" _ "$DR"
expect_text entry-refs-dedupe "$ID_A" bash -c "printf 'linux /EFI/dr/kernels/$ID_A/vmlinuz\\ninitrd /EFI/dr/kernels/$ID_A/initrd.img\\n' | \"\$1\" _sdboot-entry-refs" _ "$DR"

# CRLF is legal in BLS text arriving from foreign editors; a retained CR must not hide a referenced kernel.
printf 'linux /EFI/dr/kernels/crlf-a-111111111111/vmlinuz\r\ninitrd /EFI/dr/kernels/crlf-b-222222222222/initrd.img\r\n' \
  >"$WORK/crlf-single"
got=$("$DR" _sdboot-entry-refs <"$WORK/crlf-single" 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != $'crlf-a-111111111111\ncrlf-b-222222222222' ]; then
  echo "FAIL entry-refs-crlf-single: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

printf 'linux /EFI/dr/kernels/crlf-a-111111111111/vmlinuz\r\ninitrd /EFI/dr/kernels/crlf-a-111111111111/initrd.img /EFI/dr/kernels/crlf-b-222222222222/initrd.img\r\n' \
  >"$WORK/crlf-multi"
got=$("$DR" _sdboot-entry-refs <"$WORK/crlf-multi" 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != $'crlf-a-111111111111\ncrlf-b-222222222222' ]; then
  echo "FAIL entry-refs-crlf-multi: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

# Interrupted copies use known dr-state scratch names; cleanup removes those and leaves every unrelated file alone.
SCRATCH="$WORK/scratch"
mkdir -p "$SCRATCH/kernels/.tmp.Ab12Cd" "$SCRATCH/kernels/.tmp.not-ours" \
  "$SCRATCH/kernels/.tmp.Ab12Cd.extra" "$SCRATCH/kernels/os-keep-111111111111" \
  "$SCRATCH/entries" "$SCRATCH/loader" "$SCRATCH/efi-dr"
printf x >"$SCRATCH/kernels/.tmp.Ab12Cd/vmlinuz"
printf x >"$SCRATCH/kernels/.tmp.not-ours/vmlinuz"
printf x >"$SCRATCH/kernels/.tmp.Ab12Cd.extra/vmlinuz"
printf x >"$SCRATCH/kernels/.foreign-hidden"
printf x >"$SCRATCH/kernels/os-keep-111111111111/vmlinuz"
printf x >"$SCRATCH/kernels/os-keep-111111111111/initrd.img"
printf x >"$SCRATCH/kernels/os-keep-111111111111/.vmlinuz.tmp.123"
printf x >"$SCRATCH/kernels/os-keep-111111111111/.vmlinuz.tmp.123.bak"
printf x >"$SCRATCH/kernels/os-keep-111111111111/.initrd.img.tmp.123"
printf x >"$SCRATCH/kernels/os-keep-111111111111/.other.tmp.123"
printf x >"$SCRATCH/entries/.os-old.conf.new.123"
printf x >"$SCRATCH/entries/.dr-state-entry.new.123"
printf x >"$SCRATCH/entries/.os-old.conf.new.backup"
printf x >"$SCRATCH/entries/.dr-state-entry.new.backup"
printf x >"$SCRATCH/entries/.os-old.tmp.123"
printf x >"$SCRATCH/entries/.notes"
printf x >"$SCRATCH/entries/os-old.conf"
printf x >"$SCRATCH/loader/loader.conf.new.123"
printf x >"$SCRATCH/loader/loader.conf.new.backup"
printf x >"$SCRATCH/loader/other.new.123"
printf x >"$SCRATCH/loader/loader.conf"
printf x >"$SCRATCH/efi-dr/bootloader.new.123"
printf x >"$SCRATCH/efi-dr/bootloader.new.backup"
printf x >"$SCRATCH/efi-dr/bootloader"

expect_exit clean-scratch-requires-test-mode 1 env -u DR_STATE_TEST_MODE "$DR" _sdboot-clean-scratch \
  "$SCRATCH/kernels" "$SCRATCH/entries" "$SCRATCH/loader/loader.conf" "$SCRATCH/efi-dr/bootloader"
expect_exit fail-step-requires-test-mode 1 env -u DR_STATE_TEST_MODE DR_STATE_TEST_FAIL_STEP=loader-write \
  "$DR" gen-menu
case_count=$((case_count + 1))
DR_STATE_TEST_MODE=1 DR_STATE_TEST_ROOT="$SCRATCH" "$DR" _sdboot-clean-scratch "$SCRATCH/kernels" "$SCRATCH/entries" \
  "$SCRATCH/loader/loader.conf" "$SCRATCH/efi-dr/bootloader" >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "clean-scratch: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  for path in \
    "$SCRATCH/kernels/.tmp.Ab12Cd" \
    "$SCRATCH/kernels/os-keep-111111111111/.vmlinuz.tmp.123" \
    "$SCRATCH/kernels/os-keep-111111111111/.initrd.img.tmp.123" \
    "$SCRATCH/entries/.os-old.conf.new.123" \
    "$SCRATCH/entries/.dr-state-entry.new.123" \
    "$SCRATCH/loader/loader.conf.new.123" \
    "$SCRATCH/efi-dr/bootloader.new.123"; do
    [ ! -e "$path" ] || bad "clean-scratch: known scratch survived: $path"
  done
  for path in \
    "$SCRATCH/kernels/.tmp.not-ours" \
    "$SCRATCH/kernels/.tmp.Ab12Cd.extra" \
    "$SCRATCH/kernels/.foreign-hidden" \
    "$SCRATCH/kernels/os-keep-111111111111/vmlinuz" \
    "$SCRATCH/kernels/os-keep-111111111111/initrd.img" \
    "$SCRATCH/kernels/os-keep-111111111111/.vmlinuz.tmp.123.bak" \
    "$SCRATCH/kernels/os-keep-111111111111/.other.tmp.123" \
    "$SCRATCH/entries/.os-old.conf.new.backup" \
    "$SCRATCH/entries/.dr-state-entry.new.backup" \
    "$SCRATCH/entries/.os-old.tmp.123" \
    "$SCRATCH/entries/.notes" \
    "$SCRATCH/entries/os-old.conf" \
    "$SCRATCH/loader/loader.conf.new.backup" \
    "$SCRATCH/loader/other.new.123" \
    "$SCRATCH/loader/loader.conf" \
    "$SCRATCH/efi-dr/bootloader.new.backup" \
    "$SCRATCH/efi-dr/bootloader"; do
    [ -e "$path" ] || bad "clean-scratch: unrelated file was removed: $path"
  done
fi

# Production workflow: cleanup is called before generation, and foreign look-alikes survive the real command.
WF_CLEAN="$WORK/wf-clean"
workflow_fixture "$WF_CLEAN" full
workflow_two_states "$WF_CLEAN"
mkdir -p "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.Ab12Cd" "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours"
printf x >"$WF_CLEAN/esp/EFI/dr/kernels/.tmp.Ab12Cd/vmlinuz"
printf x >"$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours/vmlinuz"
printf x >"$WF_CLEAN/esp/loader/loader.conf.new.123"
printf x >"$WF_CLEAN/esp/loader/loader.conf.new.backup"
printf x >"$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.123"
printf x >"$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.backup"
printf x >"$WF_CLEAN/esp/EFI/dr/bootloader.new.123"
printf x >"$WF_CLEAN/esp/EFI/dr/bootloader.new.backup"
case_count=$((case_count + 1))
workflow_run "$WF_CLEAN" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-cleanup: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  for path in "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.Ab12Cd" \
    "$WF_CLEAN/esp/loader/loader.conf.new.123" \
    "$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.123" \
    "$WF_CLEAN/esp/EFI/dr/bootloader.new.123"; do
    [ ! -e "$path" ] || bad "workflow-cleanup: own scratch survived: $path"
  done
  for path in "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours" \
    "$WF_CLEAN/esp/loader/loader.conf.new.backup" \
    "$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.backup" \
    "$WF_CLEAN/esp/EFI/dr/bootloader.new.backup"; do
    [ -e "$path" ] || bad "workflow-cleanup: foreign look-alike removed: $path"
  done
fi

# Production workflow core output: every BLS entry and both copied files must match the selected state and source.
WF_CORE="$WORK/wf-core"
workflow_fixture "$WF_CORE" full
workflow_two_states "$WF_CORE"
case_count=$((case_count + 1))
workflow_run "$WF_CORE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-core: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ ! -s "$WF_CORE/esp/loader/loader.conf" ]; then
  bad "workflow-core: loader.conf missing"
else
  workflow_assert_entry "$WF_CORE" @os-000001-20261118-120000Z-install "$KVER"
  workflow_assert_entry "$WF_CORE" @os-000002-20261118-120100Z-apt "$KVER"
fi

# Selection prefers the newest COMPLETE pair; a newer zero-byte kernel cannot hide the older bootable pair.
WF_OLDER="$WORK/wf-older-pair"
KVER_OLD=6.12.107+deb13-amd64
KVER_NEW=6.12.117+deb13-amd64
workflow_fixture "$WF_OLDER" full
mkdir -p "$WF_OLDER/top/@os-000001-20261118-120000Z-install/boot"
printf 'older kernel\n' >"$WF_OLDER/top/@os-000001-20261118-120000Z-install/boot/vmlinuz-$KVER_OLD"
printf 'older initrd\n' >"$WF_OLDER/top/@os-000001-20261118-120000Z-install/boot/initrd.img-$KVER_OLD"
: >"$WF_OLDER/top/@os-000001-20261118-120000Z-install/boot/vmlinuz-$KVER_NEW"
printf 'newer initrd only\n' >"$WF_OLDER/top/@os-000001-20261118-120000Z-install/boot/initrd.img-$KVER_NEW"
case_count=$((case_count + 1))
workflow_run "$WF_OLDER" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-latest-complete: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- "title Debian @os-000001-20261118-120000Z-install (kernel $KVER_OLD)" \
  "$WF_OLDER/esp/loader/entries/os-000001-20261118-120000Z-install.conf"; then
  bad "workflow-latest-complete: did not select the older complete pair"
else
  workflow_assert_entry "$WF_OLDER" @os-000001-20261118-120000Z-install "$KVER_OLD"
fi

# Entry-id boundary: 240 characters is accepted through a short fixed temp basename; 241 is refused before copying.
WF_LONG_OK="$WORK/wf-long-ok"
WF_LONG_BAD="$WORK/wf-long-bad"
LONG_ID_240=$(state_with_entry_id_length 240)
LONG_ID_241=$(state_with_entry_id_length 241)
workflow_fixture "$WF_LONG_OK" full
workflow_state "$WF_LONG_OK" "$LONG_ID_240"
case_count=$((case_count + 1))
workflow_run "$WF_LONG_OK" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-entry-id-240: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  workflow_assert_entry "$WF_LONG_OK" "$LONG_ID_240" "$KVER"
fi

workflow_fixture "$WF_LONG_BAD" full
workflow_state "$WF_LONG_BAD" "$LONG_ID_241"
case_count=$((case_count + 1))
workflow_run "$WF_LONG_BAD" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-entry-id-241: want exit 1, got $rc"
elif [ -n "$(find "$WF_LONG_BAD/esp/EFI/dr/kernels" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  bad "workflow-entry-id-241: copied a kernel before refusing the entry id"
elif [ -e "$WF_LONG_BAD/esp/loader/loader.conf" ]; then
  bad "workflow-entry-id-241: wrote loader.conf before refusing the entry id"
fi

# Production workflow: a foreign case/extension alias is refused before any desired entry is overwritten.
WF_COLLISION="$WORK/wf-collision"
workflow_fixture "$WF_COLLISION" full
workflow_state "$WF_COLLISION" @os-000001-20261118-120000Z-install
printf 'title foreign owner\n' >"$WF_COLLISION/esp/loader/entries/OS-000001-20261118-120000Z-INSTALL.CONF"
case_count=$((case_count + 1))
workflow_run "$WF_COLLISION" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-collision: want exit 1, got $rc"
elif ! grep -Fq 'case-insensitively collides' "$WORK/err"; then
  bad "workflow-collision: refusal did not explain the case alias"
elif [ "$(cat "$WF_COLLISION/esp/loader/entries/OS-000001-20261118-120000Z-INSTALL.CONF")" != 'title foreign owner' ]; then
  bad "workflow-collision: foreign entry was changed"
elif [ -e "$WF_COLLISION/esp/loader/loader.conf" ]; then
  bad "workflow-collision: loader.conf was written despite refusal"
fi

# Production workflow: even an exact filename is refused when it does not carry the dr-state ownership marker.
WF_FOREIGN="$WORK/wf-foreign-exact"
workflow_fixture "$WF_FOREIGN" full
workflow_state "$WF_FOREIGN" @os-000001-20261118-120000Z-install
printf 'title foreign exact name\n' >"$WF_FOREIGN/esp/loader/entries/os-000001-20261118-120000Z-install.conf"
case_count=$((case_count + 1))
workflow_run "$WF_FOREIGN" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-foreign-exact: want exit 1, got $rc"
elif ! grep -Fq 'is not owned by dr-state' "$WORK/err"; then
  bad "workflow-foreign-exact: refusal did not explain ownership"
elif [ "$(cat "$WF_FOREIGN/esp/loader/entries/os-000001-20261118-120000Z-install.conf")" != 'title foreign exact name' ]; then
  bad "workflow-foreign-exact: foreign entry was changed"
fi

# Production workflow: no requested existing default still emits exactly one valid default; set-default rewrites it.
WF_DEFAULT="$WORK/wf-default"
workflow_fixture "$WF_DEFAULT" full
workflow_two_states "$WF_DEFAULT"
case_count=$((case_count + 1))
workflow_run "$WF_DEFAULT" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-default-first: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_DEFAULT/esp/loader/loader.conf")" != os-000001-20261118-120000Z-install.conf ]; then
  bad "workflow-default-first: first sorted entry was not selected"
fi
case_count=$((case_count + 1))
workflow_run "$WF_DEFAULT" 999999999 4096 set-default @os-000002-20261118-120100Z-apt >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-set-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_DEFAULT/esp/loader/loader.conf")" != os-000002-20261118-120100Z-apt.conf ]; then
  bad "workflow-set-default: loader.conf did not select os-000002-20261118-120100Z-apt"
fi

# A pre-existing state whose STATE name ends in .conf is not selectable. gen-menu warns and keeps every valid state.
WF_CONF_STATE="$WORK/wf-conf-state"
workflow_invalid_conf_fixture "$WF_CONF_STATE"
case_count=$((case_count + 1))
workflow_run "$WF_CONF_STATE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-state-gen-menu: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- '@os-000001-20261118-120000Z-install.conf' "$WORK/err" || ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-state-gen-menu: invalid state was not skipped with a clear warning"
elif [ -e "$WF_CONF_STATE/esp/loader/entries/os-000001-20261118-120000Z-install.conf.conf" ]; then
  bad "workflow-conf-state-gen-menu: wrote a BLS entry for the invalid state"
elif [ ! -s "$WF_CONF_STATE/esp/loader/entries/os-000002-20261118-120100Z-apt.conf" ]; then
  bad "workflow-conf-state-gen-menu: valid entry was not written"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_CONF_STATE/esp/loader/loader.conf")" != os-000002-20261118-120100Z-apt.conf ]; then
  bad "workflow-conf-state-gen-menu: default did not select the valid state"
else
  assert_loader_default_exists "$WF_CONF_STATE" "workflow-conf-state-gen-menu"
fi

case_count=$((case_count + 1))
workflow_run "$WF_CONF_STATE" 999999999 4096 list >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-state-list: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-state-list: invalid state was not skipped with a clear warning"
elif grep -Fq -- '@os-000001-20261118-120000Z-install.conf' "$WORK/out"; then
  bad "workflow-conf-state-list: invalid state was listed as selectable"
elif ! grep -Fqx -- "@os-000002-20261118-120100Z-apt (default) kernel=$KVER" "$WORK/out"; then
  bad "workflow-conf-state-list: valid default state was not listed"
fi

case_count=$((case_count + 1))
workflow_run "$WF_CONF_STATE" 999999999 4096 set-default @os-000001-20261118-120000Z-install.conf \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-conf-state-set-default: want refusal exit 1, got $rc"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-state-set-default: refusal did not explain the invalid state name"
fi

WF_CONF_DEFAULT="$WORK/wf-conf-default"
workflow_invalid_conf_fixture "$WF_CONF_DEFAULT"
case_count=$((case_count + 1))
workflow_run "$WF_CONF_DEFAULT" 999999999 4096 gen-menu --default @os-000001-20261118-120000Z-install.conf \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-conf-state-default: want refusal exit 1, got $rc"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-state-default: refusal did not explain the invalid state name"
elif [ -e "$WF_CONF_DEFAULT/esp/loader/loader.conf" ]; then
  bad "workflow-conf-state-default: loader.conf was written despite refusal"
fi

WF_CONF_BOOT="$WORK/wf-conf-boot"
workflow_invalid_conf_fixture "$WF_CONF_BOOT"
install_recording_bootctl "$WF_CONF_BOOT"
case_count=$((case_count + 1))
BOOTCTL_LOG="$WF_CONF_BOOT/bootctl-invalid.log" PATH="$WF_CONF_BOOT/boot-bin:$PATH" \
  workflow_run "$WF_CONF_BOOT" 999999999 4096 boot-once @os-000001-20261118-120000Z-install.conf \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-conf-state-boot-once-invalid: want refusal exit 1, got $rc"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-state-boot-once-invalid: refusal did not explain the invalid state name"
elif [ -e "$WF_CONF_BOOT/bootctl-invalid.log" ]; then
  bad "workflow-conf-state-boot-once-invalid: bootctl received a selector for the invalid state"
fi

case_count=$((case_count + 1))
BOOTCTL_LOG="$WF_CONF_BOOT/bootctl-valid.log" PATH="$WF_CONF_BOOT/boot-bin:$PATH" \
  workflow_run "$WF_CONF_BOOT" 999999999 4096 boot-once @os-000002-20261118-120100Z-apt \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-state-boot-once-valid: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- 'set-oneshot os-000002-20261118-120100Z-apt.conf' "$WF_CONF_BOOT/bootctl-valid.log"; then
  bad "workflow-conf-state-boot-once-valid: one-shot did not name the valid entry file"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_CONF_BOOT/esp/loader/loader.conf")" != os-000002-20261118-120100Z-apt.conf ]; then
  bad "workflow-conf-state-boot-once-valid: loader.conf did not name the valid entry file"
elif [ -e "$WF_CONF_BOOT/esp/loader/entries/os-000001-20261118-120000Z-install.conf.conf" ]; then
  bad "workflow-conf-state-boot-once-valid: invalid state gained a BLS entry"
fi

# Stale loader/one-shot selectors for the invalid state are ignored; the rewritten menu and stale cleanup stay safe.
case_count=$((case_count + 1))
printf '# generated by dr-state sdboot; do not edit\ntitle stale invalid state\n' \
  >"$WF_CONF_BOOT/esp/loader/entries/os-000001-20261118-120000Z-install.conf.conf"
printf 'default os-000001-20261118-120000Z-install.conf.conf\ntimeout 5\n' >"$WF_CONF_BOOT/esp/loader/loader.conf"
write_sdboot_oneshot "$WF_CONF_BOOT" os-000001-20261118-120000Z-install.conf.conf
workflow_run "$WF_CONF_BOOT" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-state-stale-selectors: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- 'loader.conf default' "$WORK/err" || ! grep -Fq -- 'LoaderEntryOneShot' "$WORK/err"; then
  bad "workflow-conf-state-stale-selectors: invalid selectors were not reported"
elif [ -e "$WF_CONF_BOOT/esp/loader/entries/os-000001-20261118-120000Z-install.conf.conf" ]; then
  bad "workflow-conf-state-stale-selectors: stale invalid entry survived"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_CONF_BOOT/esp/loader/loader.conf")" != os-000002-20261118-120100Z-apt.conf ]; then
  bad "workflow-conf-state-stale-selectors: loader.conf did not resolve to the valid entry file"
else
  assert_loader_default_exists "$WF_CONF_BOOT" "workflow-conf-state-stale-selectors"
fi

# Existing suffix-less defaults are accepted and migrated to the canonical systemd-boot spelling.
WF_MIGRATE="$WORK/wf-migrate"
workflow_fixture "$WF_MIGRATE" full
workflow_two_states "$WF_MIGRATE"
printf 'default os-000002-20261118-120100Z-apt\ntimeout 10\n' >"$WF_MIGRATE/esp/loader/loader.conf"
case_count=$((case_count + 1))
workflow_run "$WF_MIGRATE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-migrate-default: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_MIGRATE/esp/loader/loader.conf")" != os-000002-20261118-120100Z-apt.conf ]; then
  bad "workflow-migrate-default: suffix-less default was not migrated"
else
  assert_loader_default_exists "$WF_MIGRATE" "workflow-migrate-default"
fi

# list resolves the default state from either spelling even though new writes always use .conf.
WF_LIST="$WORK/wf-list"
workflow_fixture "$WF_LIST" full
workflow_two_states "$WF_LIST"
for default_id in os-000002-20261118-120100Z-apt.conf os-000002-20261118-120100Z-apt; do
  printf 'default %s\ntimeout 5\n' "$default_id" >"$WF_LIST/esp/loader/loader.conf"
  case_count=$((case_count + 1))
  workflow_run "$WF_LIST" 999999999 4096 list >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ]; then
    bad "workflow-list-default-$default_id: list rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif ! grep -Fqx -- "@os-000002-20261118-120100Z-apt (default) kernel=$KVER" "$WORK/out"; then
    bad "workflow-list-default-$default_id: list did not mark @os-000002-20261118-120100Z-apt as default"
  fi
done

# A snapshot records the system facts and free-text note from before a change. Its sidecar stays outside the OS
# subvolume, and list/show expose the captured facts. Deleting the state removes its metadata but never reuses its ID.
WF_METADATA="$WORK/wf-metadata"
workflow_fixture "$WF_METADATA" full
workflow_state "$WF_METADATA" @os-000001-20261118-120000Z-install
install_fake_maintenance_btrfs "$WF_METADATA"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' \
DR_STATE_TEST_OS_ID=debian DR_STATE_TEST_OS_VERSION_ID=13 DR_STATE_TEST_KERNEL=6.12.117-fixture \
DR_STATE_TEST_PACKAGES=$'apt\t3.0\ndpkg\t1.22' PATH="$WF_METADATA/btrfs-bin:$PATH" \
  workflow_run "$WF_METADATA" 999999999 4096 snapshot --reason manual \
    --note $'before apt install hello\noperator context' >"$WORK/out" 2>"$WORK/err"; rc=$?
created=$(find "$WF_METADATA/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-manual' -printf '%f\n' | head -n 1)
if [ "$rc" != 0 ] || [[ ! "$created" =~ ^@os-000002-[0-9]{8}-[0-9]{6}Z-manual$ ]]; then
  bad "workflow-snapshot-metadata: rc=$rc state='$created' ($(tr '\n' ' ' <"$WORK/err"))"
else
  DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_METADATA" 999999999 4096 show "$created" >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || ! grep -Fqx 'source: manual' "$WORK/out" \
    || ! grep -Fqx 'sequence: 000002' "$WORK/out" \
    || ! grep -Fqx 'origin-slug: manual' "$WORK/out" \
    || ! grep -Fqx 'reason: manual' "$WORK/out" \
    || ! grep -Fqx 'os-at-snapshot: Debian GNU/Linux 13 (fixture)' "$WORK/out" \
    || ! grep -Fqx 'running-kernel-at-snapshot: 6.12.117-fixture' "$WORK/out" \
    || ! grep -Eq '^apt[[:space:]]+3\.0$' "$WORK/out" \
    || ! grep -Fq 'before apt install hello' "$WORK/out" \
    || ! grep -Fq 'operator context' "$WORK/out"; then
    bad "workflow-snapshot-show: report omitted captured facts or note: $(tr '\n' ' ' <"$WORK/out")"
  fi
  DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_METADATA" 999999999 4096 list >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || ! grep -Fq "$created" "$WORK/out" \
    || ! grep -Fq 'created=' "$WORK/out" || ! grep -Fq 'reason=manual' "$WORK/out" \
    || ! grep -Fq 'note=before apt install hello operator context' "$WORK/out"; then
    bad "workflow-snapshot-list: report omitted its snapshot summary: $(tr '\n' ' ' <"$WORK/out")"
  fi
  DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_METADATA" 999999999 4096 list --long >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || ! grep -Fq 'key-package-versions:' "$WORK/out" \
    || ! grep -Fq 'operator context' "$WORK/out"; then
    bad "workflow-snapshot-list-long: detailed report omitted packages or note: $(tr '\n' ' ' <"$WORK/out")"
  fi
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_METADATA/btrfs-bin:$PATH" workflow_run "$WF_METADATA" 999999999 4096 delete "$created" --yes \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || [ -e "$WF_METADATA/top/.dr-state/metadata/$created.meta" ] \
    || [ "$(cat "$WF_METADATA/top/.dr-state/last-sequence")" != 000002 ]; then
    bad "workflow-snapshot-delete-metadata: rc=$rc state_exists=$( [ -e "$WF_METADATA/top/$created" ] && echo yes || echo no ) sidecar_exists=$( [ -e "$WF_METADATA/top/.dr-state/metadata/$created.meta" ] && echo yes || echo no ) stdout='$(tr '\n' ' ' <"$WORK/out")' stderr='$(tr '\n' ' ' <"$WORK/err")'"
  fi
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_METADATA/btrfs-bin:$PATH" \
    workflow_run "$WF_METADATA" 999999999 4096 snapshot --reason before-upgrade \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  created_again=$(find "$WF_METADATA/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000003-*-*Z-manual-before-upgrade' -printf '%f\n' | head -n 1)
  if [ "$rc" != 0 ] || [ -z "$created_again" ] || [ -e "$WF_METADATA/top/$created" ]; then
    bad "workflow-sequence-not-reused-after-delete: rc=$rc created='$created_again' deleted_id_exists=$( [ -e "$WF_METADATA/top/$created" ] && echo yes || echo no ) ($(tr '\n' ' ' <"$WORK/err"))"
  fi
fi

# Fresh installation initializes sequence 000001 only after the install state has a complete kernel pair.
WF_INITIALIZE="$WORK/wf-initialize"
workflow_fixture "$WF_INITIALIZE" full
workflow_state "$WF_INITIALIZE" @os-000001-20261121-120000Z-install
workflow_remove_state_counter "$WF_INITIALIZE"
case_count=$((case_count + 1))
DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' DR_STATE_TEST_KERNEL=6.12.117-fixture \
  workflow_run "$WF_INITIALIZE" 999999999 4096 initialize-state \
    @os-000001-20261121-120000Z-install 20261121120000 >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ] || [ "$(cat "$WF_INITIALIZE/top/.dr-state/last-sequence" 2>/dev/null)" != 000001 ] \
  || [ ! -f "$WF_INITIALIZE/top/.dr-state/metadata/@os-000001-20261121-120000Z-install.meta" ]; then
  bad "workflow-initialize-state: rc=$rc counter=$(cat "$WF_INITIALIZE/top/.dr-state/last-sequence" 2>/dev/null) ($(tr '\n' ' ' <"$WORK/err"))"
else
  DR_STATE=@os-000001-20261121-120000Z-install workflow_run "$WF_INITIALIZE" 999999999 4096 \
    show @os-000001-20261121-120000Z-install >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || ! grep -Fqx 'sequence: 000001' "$WORK/out" \
    || ! grep -Fqx 'origin-slug: install' "$WORK/out" || ! grep -Fqx 'source: install' "$WORK/out"; then
    bad "workflow-initialize-state-metadata: rc=$rc report=$(tr '\n' ' ' <"$WORK/out")"
  fi
  case_count=$((case_count + 1))
  DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' DR_STATE_TEST_KERNEL=6.12.117-fixture \
    workflow_run "$WF_INITIALIZE" 999999999 4096 initialize-state \
      @os-000001-20261121-120000Z-install 20261121120000 >"$WORK/out" 2>"$WORK/err"; rc=$?
  [ "$rc" = 0 ] && [ "$(cat "$WF_INITIALIZE/top/.dr-state/last-sequence")" = 000001 ] \
    || bad "workflow-initialize-state-idempotent: rc=$rc counter=$(cat "$WF_INITIALIZE/top/.dr-state/last-sequence") ($(tr '\n' ' ' <"$WORK/err"))"
fi

# Snapshot allocation must fail closed without a valid high-water mark and must not make an ID reuse guess.
for counter_value in missing malformed symlink; do
  WF_COUNTER="$WORK/wf-counter-$counter_value"
  workflow_fixture "$WF_COUNTER" full
  workflow_state "$WF_COUNTER" @os-000001-20261118-120000Z-install
  install_fake_snapshot_btrfs "$WF_COUNTER"
  case "$counter_value" in
    malformed) printf 'not-a-number\n' >"$WF_COUNTER/top/.dr-state/last-sequence" ;;
    symlink)
      workflow_remove_state_counter "$WF_COUNTER"
      printf '000001\n' >"$WF_COUNTER/counter-target"
      ln -s "$WF_COUNTER/counter-target" "$WF_COUNTER/top/.dr-state/last-sequence" ;;
    missing) workflow_remove_state_counter "$WF_COUNTER" ;;
  esac
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_COUNTER/btrfs-bin:$PATH" \
    workflow_run "$WF_COUNTER" 999999999 4096 snapshot --reason before-upgrade \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" = 0 ] || find "$WF_COUNTER/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*' -print -quit | grep -q .; then
    bad "workflow-counter-$counter_value-fails-closed: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  fi
done

# APT's pre-install package plan runs before dpkg. The first hook snapshots the running root; later dpkg passes in the
# same transaction append unique package actions to that snapshot without making a second copy.
WF_APT_PLAN="$WORK/wf-apt-plan"
workflow_fixture "$WF_APT_PLAN" full
workflow_state "$WF_APT_PLAN" @os-000001-20261118-120000Z-install
install_fake_maintenance_btrfs "$WF_APT_PLAN"
for round in 1 2; do
  if [ "$round" = 1 ]; then APT_INPUT="$APT_PLAN_V2"; else APT_INPUT="$WORK/apt-plan-second"; fi
  if [ "$round" = 2 ]; then
    printf 'VERSION 2\nAPT::Get::CommandLine::AsString=apt%%20upgrade\n\nbar 1.0 < 2.0 /var/cache/apt/archives/bar.deb\n' \
      >"$APT_INPUT"
  fi
  case_count=$((case_count + 1))
  (
    exec 3<"$APT_INPUT"
    DR_STATE=@os-000001-20261118-120000Z-install APT_HOOK_INFO_FD=3 DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' \
    DR_STATE_TEST_KERNEL=6.12.117-fixture DR_STATE_TEST_PACKAGES=$'apt\t3.0' \
    PATH="$WF_APT_PLAN/btrfs-bin:$PATH" \
      workflow_run "$WF_APT_PLAN" 999999999 4096 apt-pre-install-pkgs
  ) >"$WORK/out" 2>"$WORK/err"; rc=$?
  [ "$rc" = 0 ] || bad "workflow-apt-plan-round-$round: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
done
apt_snapshots=$(find "$WF_APT_PLAN/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-apt' -printf '%f\n' | wc -l)
apt_state=$(find "$WF_APT_PLAN/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-apt' -printf '%f\n' | head -n 1)
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_APT_PLAN" 999999999 4096 show "$apt_state" >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ] || [ "$apt_snapshots" != 1 ] \
  || ! grep -Fqx 'source: apt' "$WORK/out" \
  || ! grep -Fqx 'reason: apt' "$WORK/out" \
  || ! grep -Fq 'install hello - -> 2.10-3' "$WORK/out" \
  || ! grep -Fq 'upgrade libc6:amd64 2.36-9 -> 2.37-1' "$WORK/out" \
  || ! grep -Fq 'upgrade bar 1.0 -> 2.0' "$WORK/out"; then
  bad "workflow-apt-plan-metadata: rc=$rc snapshots=$apt_snapshots report=$(tr '\n' ' ' <"$WORK/out")"
fi

# boot-once passes the canonical type-1 entry id (WITH .conf) to the single bootctl writer.
WF_BOOT_ONCE="$WORK/wf-boot-once"
workflow_fixture "$WF_BOOT_ONCE" full
workflow_two_states "$WF_BOOT_ONCE"
install_recording_bootctl "$WF_BOOT_ONCE"
BOOTCTL_LOG="$WF_BOOT_ONCE/bootctl.log" PATH="$WF_BOOT_ONCE/boot-bin:$PATH" \
  workflow_run "$WF_BOOT_ONCE" 999999999 4096 boot-once @os-000002-20261118-120100Z-apt \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ]; then
  bad "workflow-boot-once-conf: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- 'set-oneshot os-000002-20261118-120100Z-apt.conf' "$WF_BOOT_ONCE/bootctl.log"; then
  bad "workflow-boot-once-conf: bootctl did not receive the suffixed entry id"
fi

# Prune protects both the default and pending one-shot state from either accepted spelling.
for form in suffixed suffixless; do
  WF_PRUNE_FORM="$WORK/wf-prune-form-$form"
  if [ "$form" = suffixed ]; then
    workflow_prune_form "$WF_PRUNE_FORM" os-000100-20261118-120000Z-manual-default-conf.conf \
      os-000100-20261118-120000Z-manual-next-conf.conf "$form"
  else
    workflow_prune_form "$WF_PRUNE_FORM" os-000100-20261118-120000Z-manual-default \
      os-000100-20261118-120000Z-manual-next "$form"
  fi
done

# Prune protects every valid default selector and validates every default/timeout directive before deletion.
WF_MULTI_DEFAULT="$WORK/wf-prune-multiple-defaults"
workflow_fixture "$WF_MULTI_DEFAULT" full
workflow_two_states "$WF_MULTI_DEFAULT"
workflow_state "$WF_MULTI_DEFAULT" @os-000100-20261118-120000Z-manual-default-a-conf
workflow_state "$WF_MULTI_DEFAULT" @os-000101-20261118-120000Z-manual-default-b-conf
workflow_state "$WF_MULTI_DEFAULT" @os-000102-20261118-120000Z-manual-doomed
printf '%s\n' \
  'default os-000100-20261118-120000Z-manual-default-a-conf.conf' \
  'default os-000101-20261118-120000Z-manual-default-b-conf.conf' \
  'timeout 5' >"$WF_MULTI_DEFAULT/esp/loader/loader.conf"
install_fake_maintenance_btrfs "$WF_MULTI_DEFAULT"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_MULTI_DEFAULT/btrfs-bin:$PATH" \
  workflow_run "$WF_MULTI_DEFAULT" 999999999 4096 prune --keep 0 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune-multiple-defaults: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ ! -d "$WF_MULTI_DEFAULT/top/@os-000100-20261118-120000Z-manual-default-a-conf" ] \
  || [ ! -d "$WF_MULTI_DEFAULT/top/@os-000101-20261118-120000Z-manual-default-b-conf" ]; then
  bad "workflow-prune-multiple-defaults: deleted a state named by a valid default"
elif [ -d "$WF_MULTI_DEFAULT/top/@os-000102-20261118-120000Z-manual-doomed" ]; then
  bad "workflow-prune-multiple-defaults: did not delete the unprotected state"
fi

for malformed in second-default timeout missing empty timeout-only; do
  WF_BAD_LOADER="$WORK/wf-prune-invalid-loader-$malformed"
  workflow_fixture "$WF_BAD_LOADER" full
  workflow_two_states "$WF_BAD_LOADER"
  workflow_state "$WF_BAD_LOADER" @os-000100-20261118-120000Z-manual-protected
  workflow_state "$WF_BAD_LOADER" @os-000101-20261118-120000Z-manual-doomed
  if [ "$malformed" = second-default ]; then
    printf '%s\n' 'default os-000100-20261118-120000Z-manual-protected-conf.conf' 'default' 'timeout 5' \
      >"$WF_BAD_LOADER/esp/loader/loader.conf"
  elif [ "$malformed" = timeout ]; then
    printf '%s\n' 'default os-000100-20261118-120000Z-manual-protected-conf.conf' 'timeout later' \
      >"$WF_BAD_LOADER/esp/loader/loader.conf"
  elif [ "$malformed" = empty ]; then
    : >"$WF_BAD_LOADER/esp/loader/loader.conf"
  elif [ "$malformed" = timeout-only ]; then
    printf 'timeout 5\n' >"$WF_BAD_LOADER/esp/loader/loader.conf"
  fi
  install_fake_maintenance_btrfs "$WF_BAD_LOADER"
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_BAD_LOADER/btrfs-bin:$PATH" \
    workflow_run "$WF_BAD_LOADER" 999999999 4096 prune --keep 0 \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" = 0 ]; then
    bad "workflow-prune-invalid-loader-$malformed: invalid loader.conf was accepted"
  elif [ ! -d "$WF_BAD_LOADER/top/@os-000101-20261118-120000Z-manual-doomed" ]; then
    bad "workflow-prune-invalid-loader-$malformed: deleted a state before validating loader.conf"
  fi
done

# A one-shot pending state remains independently of whether its EFI variable carries .conf or the legacy spelling.
workflow_gen_preserves_oneshot "$WORK/wf-oneshot-suffixed" os-000002-20261118-120100Z-apt.conf suffixed
workflow_gen_preserves_oneshot "$WORK/wf-oneshot-suffixless" os-000002-20261118-120100Z-apt suffixless

# A selector that predates the guest finding names os-X.conf.conf for the legacy state @os-X.conf. Prune must protect
# that legacy state instead of deleting the next/default boot target.
WF_CONF_PRUNE="$WORK/wf-conf-prune"
workflow_fixture "$WF_CONF_PRUNE" full
mkdir -p "$WF_CONF_PRUNE/top/@os-000001-20261118-120000Z-install" \
  "$WF_CONF_PRUNE/top/@os-000100-20261118-120000Z-manual-rollback.conf"
printf 'default os-000100-20261118-120000Z-manual-rollback.conf.conf\ntimeout 5\n' \
  >"$WF_CONF_PRUNE/esp/loader/loader.conf"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_CONF_PRUNE" 999999999 4096 prune --keep 0 --dry-run \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-state-prune: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fq -- 'protecting legacy default state @os-000100-20261118-120000Z-manual-rollback.conf' "$WORK/err"; then
  bad "workflow-conf-state-prune: legacy selector target was not protected"
elif [ -n "$(cat "$WORK/out")" ]; then
  bad "workflow-conf-state-prune: protected legacy state was marked for deletion"
fi

# Non-dry prune: the default and one-shot both name a canonical state whose reason slug ends in "conf".
WF_CONF_PROTECTED="$WORK/wf-conf-protected-prune"
workflow_fixture "$WF_CONF_PROTECTED" full
workflow_state "$WF_CONF_PROTECTED" @os-000001-20261118-120000Z-install
workflow_state "$WF_CONF_PROTECTED" @os-000100-20261118-120000Z-manual-protected-conf
workflow_state "$WF_CONF_PROTECTED" @os-000101-20261118-120000Z-manual-unprotected-conf
printf 'default os-000100-20261118-120000Z-manual-protected-conf.conf\ntimeout 5\n' \
  >"$WF_CONF_PROTECTED/esp/loader/loader.conf"
write_sdboot_oneshot "$WF_CONF_PROTECTED" os-000100-20261118-120000Z-manual-protected-conf.conf
install_fake_maintenance_btrfs "$WF_CONF_PROTECTED"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install PATH="$WF_CONF_PROTECTED/btrfs-bin:$PATH" \
  workflow_run "$WF_CONF_PROTECTED" 999999999 4096 prune --keep 0 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-conf-protected-prune: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ ! -d "$WF_CONF_PROTECTED/top/@os-000100-20261118-120000Z-manual-protected-conf" ]; then
  bad "workflow-conf-protected-prune: deleted the selected default/one-shot state"
elif [ -e "$WF_CONF_PROTECTED/top/@os-000101-20261118-120000Z-manual-unprotected-conf" ]; then
  bad "workflow-conf-protected-prune: unprotected canonical state survived"
fi

case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install.conf workflow_run "$WF_CONF_PRUNE" 999999999 4096 prune --keep 10 --dry-run \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-conf-running-prune: want refusal exit 1, got $rc"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-running-prune: refusal did not explain the invalid running state"
fi

WF_CONF_SNAPSHOT="$WORK/wf-conf-snapshot-source"
workflow_fixture "$WF_CONF_SNAPSHOT" full
mkdir -p "$WF_CONF_SNAPSHOT/top/@os-000001-20261118-120000Z-install.conf/boot"
printf 'kernel\n' >"$WF_CONF_SNAPSHOT/top/@os-000001-20261118-120000Z-install.conf/boot/vmlinuz-$KVER"
printf 'initrd\n' >"$WF_CONF_SNAPSHOT/top/@os-000001-20261118-120000Z-install.conf/boot/initrd.img-$KVER"
install_fake_snapshot_btrfs "$WF_CONF_SNAPSHOT"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install.conf PATH="$WF_CONF_SNAPSHOT/btrfs-bin:$PATH" \
  workflow_run "$WF_CONF_SNAPSHOT" 999999999 4096 snapshot --reason manual \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-conf-running-snapshot: want refusal exit 1, got $rc"
elif ! grep -Fq -- 'ends in .conf' "$WORK/err"; then
  bad "workflow-conf-running-snapshot: refusal did not explain the invalid running state"
fi

# Interruption ordering: an old default names a stale entry that the next menu would remove. Failure before
# loader.conf publication must leave that old default entry present; failure after publication must leave the new
# default entry present. In every case the default named by loader.conf must resolve to a real BLS file.
for fail_step in entry-write entry-publish loader-write loader-publish stale-delete; do
  WF_ORDER="$WORK/wf-order-$fail_step"
  workflow_stale_default_fixture "$WF_ORDER" \
    || bad "workflow-order-$fail_step: stale-default fixture setup failed"
  case_count=$((case_count + 1))
  workflow_run_fail "$WF_ORDER" "$fail_step" gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 1 ]; then
    bad "workflow-order-$fail_step: want injected failure exit 1, got $rc"
  fi
  assert_loader_default_exists "$WF_ORDER" "workflow-order-$fail_step"
done

WF_LOADER_MV="$WORK/wf-order-loader-publish"
workflow_stale_default_fixture "$WF_LOADER_MV" \
  || bad "workflow-order-loader-publish: stale-default fixture setup failed"
install_failing_loader_mv "$WF_LOADER_MV"
case_count=$((case_count + 1))
FAIL_LOADER_CONF="$WF_LOADER_MV/esp/loader/loader.conf" PATH="$WF_LOADER_MV/fail-bin:$PATH" \
  workflow_run "$WF_LOADER_MV" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 73 ]; then
  bad "workflow-order-loader-publish: want failing mv exit 73, got $rc"
fi
assert_loader_default_exists "$WF_LOADER_MV" "workflow-order-loader-publish"

# Prune uses the same menu publication. A failing loader.conf install must leave the old default entry intact, and a
# later stale-entry cleanup failure must leave the newly published default entry intact.
WF_PRUNE_ORDER="$WORK/wf-prune-order"
workflow_stale_default_fixture "$WF_PRUNE_ORDER" \
  || bad "workflow-prune-order: stale-default fixture setup failed"
install_failing_loader_mv "$WF_PRUNE_ORDER"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install FAIL_LOADER_CONF="$WF_PRUNE_ORDER/esp/loader/loader.conf" \
  PATH="$WF_PRUNE_ORDER/fail-bin:$PATH" \
  workflow_run "$WF_PRUNE_ORDER" 999999999 4096 prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 73 ]; then
  bad "workflow-prune-order-loader-publish: want failing mv exit 73, got $rc"
fi
assert_loader_default_exists "$WF_PRUNE_ORDER" "workflow-prune-order-loader-publish"

WF_PRUNE_STALE="$WORK/wf-prune-order-stale"
workflow_stale_default_fixture "$WF_PRUNE_STALE" \
  || bad "workflow-prune-order-stale: stale-default fixture setup failed"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run_fail "$WF_PRUNE_STALE" stale-delete prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-prune-order-stale: want injected failure exit 1, got $rc"
fi
assert_loader_default_exists "$WF_PRUNE_STALE" "workflow-prune-order-stale"

# Integrated near-full workflow: the reported need must include parent/record reserves, and one byte on either side
# of the actual threshold must fail or succeed before any ESP write.
WF_SPACE_BASE="$WORK/wf-space-base"
WF_SPACE_OVER="$WORK/wf-space-over"
WF_SPACE_UNDER="$WORK/wf-space-under"
for root in "$WF_SPACE_BASE" "$WF_SPACE_OVER" "$WF_SPACE_UNDER"; do
  workflow_fixture "$root" sparse
  workflow_two_states "$root"
done
case_count=$((case_count + 1))
workflow_run "$WF_SPACE_BASE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
needed=$(sed -n 's/.*ESP space ok: need \([0-9][0-9]*\) bytes.*/\1/p' "$WORK/err" | tail -n1)
if [ "$rc" != 0 ] || [ -z "$needed" ]; then
  bad "workflow-space-baseline: rc=$rc needed='$needed' ($(tr '\n' ' ' <"$WORK/err"))"
elif [ "$needed" -lt $((64 * 4096)) ]; then
  bad "workflow-space-reserve: need $needed omits conservative parent/FAT-record reserve"
else
  case_count=$((case_count + 1))
  workflow_run "$WF_SPACE_OVER" "$((needed - 1))" 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 1 ]; then
    bad "workflow-space-over: want exit 1 at $((needed - 1)) bytes, got $rc"
  elif [ -e "$WF_SPACE_OVER/esp/loader/loader.conf" ] || [ -e "$WF_SPACE_OVER/esp/EFI/dr/kernels" ]; then
    bad "workflow-space-over: ESP was changed despite refusal"
  fi
  case_count=$((case_count + 1))
  workflow_run "$WF_SPACE_UNDER" "$needed" 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ]; then
    bad "workflow-space-under: want exit 0 at $needed bytes, got $rc ($(tr '\n' ' ' <"$WORK/err"))"
  elif [ ! -s "$WF_SPACE_UNDER/esp/loader/loader.conf" ]; then
    bad "workflow-space-under: loader.conf missing"
  fi
fi

# Production prune route removes an unreferenced ESP kernel after regenerating the BLS entries.
WF_PRUNE="$WORK/wf-prune"
workflow_fixture "$WF_PRUNE" full
workflow_two_states "$WF_PRUNE"
workflow_run "$WF_PRUNE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err" \
  || bad "workflow-prune-setup: could not generate the valid default menu"
mkdir -p "$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe/vmlinuz"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe/initrd.img"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_PRUNE" 999999999 4096 prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ -e "$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe" ]; then
  bad "workflow-prune: unreferenced kernel directory survived"
else
  assert_loader_default_exists "$WF_PRUNE" "workflow-prune-success"
fi

# Explicit deletion can remove a broken base state after the operator has booted and selected a healthy snapshot.
WF_DELETE="$WORK/wf-delete"
workflow_fixture "$WF_DELETE" full
workflow_state "$WF_DELETE" @os-000001-20261118-120000Z-install
workflow_state "$WF_DELETE" @os-000002-20261118-120100Z-apt
workflow_state "$WF_DELETE" @os-000100-20261118-120000Z-manual-recovered
workflow_run "$WF_DELETE" 999999999 4096 gen-menu --default @os-000100-20261118-120000Z-manual-recovered \
  >"$WORK/out" 2>"$WORK/err" || bad "workflow-delete-setup: could not generate the healthy default menu"
install_fake_maintenance_btrfs "$WF_DELETE"
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered \
  PATH="$WF_DELETE/btrfs-bin:$PATH" workflow_run "$WF_DELETE" 999999999 4096 \
  delete @os-000001-20261118-120000Z-install --dry-run >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ] || [ "$(cat "$WORK/out")" != 'would delete @os-000001-20261118-120000Z-install' ] \
    || [ ! -d "$WF_DELETE/top/@os-000001-20261118-120000Z-install" ]; then
  bad "workflow-delete-dry-run: rc=$rc output='$(tr '\n' ' ' <"$WORK/out")'"
fi

# A target named by an outstanding one-shot cannot be deleted, even when it is neither running nor default.
write_sdboot_oneshot "$WF_DELETE" os-000001-20261118-120000Z-install.conf
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered \
  PATH="$WF_DELETE/btrfs-bin:$PATH" workflow_run "$WF_DELETE" 999999999 4096 \
  delete @os-000001-20261118-120000Z-install --dry-run >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || [ ! -d "$WF_DELETE/top/@os-000001-20261118-120000Z-install" ] \
    || ! grep -Fq -- 'pending systemd-boot one-shot' "$WORK/err"; then
  bad "workflow-delete-oneshot-protected: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
fi
rm -f "$WF_DELETE/sys/firmware/efi/efivars/LoaderEntryOneShot-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"

# Dry-run needs no confirmation; a destructive exact-target request does.
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered \
  PATH="$WF_DELETE/btrfs-bin:$PATH" workflow_run "$WF_DELETE" 999999999 4096 \
  delete @os-000001-20261118-120000Z-install >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || [ ! -d "$WF_DELETE/top/@os-000001-20261118-120000Z-install" ] \
    || ! grep -Fq -- 'requires --yes' "$WORK/err"; then
  bad "workflow-delete-confirmation: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
fi

# The running state and every configured default remain protected.
case_count=$((case_count + 1))
DR_STATE=@os-000002-20261118-120100Z-apt PATH="$WF_DELETE/btrfs-bin:$PATH" \
  workflow_run "$WF_DELETE" 999999999 4096 delete @os-000002-20261118-120100Z-apt --dry-run \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || ! grep -Fq -- 'running state' "$WORK/err"; then
  bad "workflow-delete-running-protected: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
fi
case_count=$((case_count + 1))
DR_STATE=@os-000002-20261118-120100Z-apt PATH="$WF_DELETE/btrfs-bin:$PATH" \
  workflow_run "$WF_DELETE" 999999999 4096 \
  delete @os-000100-20261118-120000Z-manual-recovered --dry-run >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || ! grep -Fq -- 'configured systemd-boot default' "$WORK/err"; then
  bad "workflow-delete-default-protected: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
fi

# Malformed loader state must fail before changing the menu or deleting the target.
WF_DELETE_BAD="$WORK/wf-delete-bad-loader"
workflow_fixture "$WF_DELETE_BAD" full
workflow_state "$WF_DELETE_BAD" @os-000001-20261118-120000Z-install
workflow_state "$WF_DELETE_BAD" @os-000100-20261118-120000Z-manual-recovered
printf '%s\n' 'default os-000100-20261118-120000Z-manual-recovered-conf.conf' 'default os-missing.conf' 'timeout 5' \
  >"$WF_DELETE_BAD/esp/loader/loader.conf"
install_fake_maintenance_btrfs "$WF_DELETE_BAD"
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered PATH="$WF_DELETE_BAD/btrfs-bin:$PATH" \
  workflow_run "$WF_DELETE_BAD" 999999999 4096 delete @os-000001-20261118-120000Z-install --dry-run \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || [ ! -d "$WF_DELETE_BAD/top/@os-000001-20261118-120000Z-install" ] \
    || [ "$(cat "$WF_DELETE_BAD/esp/loader/loader.conf")" != "$(printf '%s\n' 'default os-000100-20261118-120000Z-manual-recovered-conf.conf' 'default os-missing.conf' 'timeout 5')" ]; then
  bad "workflow-delete-invalid-selector: rc=$rc target/menu changed ($(tr '\n' ' ' <"$WORK/err"))"
fi

# Publish-before-delete means an ESP/menu failure keeps the subvolume intact.
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered \
  PATH="$WF_DELETE/btrfs-bin:$PATH" workflow_run_fail "$WF_DELETE" loader-publish delete @os-000001-20261118-120000Z-install --yes \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" = 0 ] || [ ! -d "$WF_DELETE/top/@os-000001-20261118-120000Z-install" ]; then
  bad "workflow-delete-menu-failure: rc=$rc state was removed despite menu failure"
fi

# Successful deletion removes only the requested OS subvolume and its now-unused BLS/kernel files.
case_count=$((case_count + 1))
DR_STATE=@os-000100-20261118-120000Z-manual-recovered \
  PATH="$WF_DELETE/btrfs-bin:$PATH" workflow_run "$WF_DELETE" 999999999 4096 \
  delete @os-000001-20261118-120000Z-install --yes >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ] || [ -e "$WF_DELETE/top/@os-000001-20261118-120000Z-install" ] \
    || [ -e "$WF_DELETE/esp/loader/entries/os-000001-20261118-120000Z-install.conf" ] \
    || [ ! -d "$WF_DELETE/top/@os-000100-20261118-120000Z-manual-recovered" ]; then
  bad "workflow-delete-success: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  assert_loader_default_exists "$WF_DELETE" "workflow-delete-success"
  if ! grep -Fqx -- 'deleted @os-000001-20261118-120000Z-install' "$WORK/out"; then
    bad "workflow-delete-success: confirmation output missing"
  fi
fi

if [ "$fail" = 0 ]; then
  echo "test-dr-state-sdboot: OK ($case_count cases)"
fi
exit "$fail"
