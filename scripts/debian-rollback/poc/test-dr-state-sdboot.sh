#!/usr/bin/env bash
# Offline black-box workflows. Every case runs the Python launcher with --sysroot;
# pure selectors, fit accounting, and fault ordering are Python unit tests.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DR_PY="$HERE/../files/usr/local/sbin/dr-state"
DR="$DR_PY"
FIXTURE_BOOT=${FIXTURE_BOOT:-systemd-boot}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
case_count=0

bad() { echo "FAIL $*"; fail=1; }

pair_id() { # KVER KERNEL_SHA256 INITRD_SHA256
  printf '%s-%s\n' "$1" \
    "$(printf '%s%s' "$2" "$3" | sha256sum | awk '{print $1}')"
}

workflow_run() { # workflow_run ROOT FREE CLUSTER COMMAND...
  local root=$1 free=$2 cluster=$3
  shift 3
  # FREE and CLUSTER remain in the call shape used by the historical harness,
  # but --sysroot fixtures observe real filesystem space instead.
  : "$free" "$cluster"
  if [ -n "${DR_STATE_TEST_OS_RELEASE+x}" ]; then
    printf 'PRETTY_NAME="%s"\nID=%s\nVERSION_ID=%s\n' \
      "${DR_STATE_TEST_OS_RELEASE:-}" "${DR_STATE_TEST_OS_ID:-debian}" \
      "${DR_STATE_TEST_OS_VERSION_ID:-13}" >"$root/etc/os-release"
  fi
  if [ -n "${DR_STATE_TEST_KERNEL+x}" ]; then
    printf '%s\n' "${DR_STATE_TEST_KERNEL:-}" >"$root/proc/sys/kernel/osrelease"
  fi
  if [ -n "${DR_STATE_TEST_PACKAGES+x}" ]; then
    printf '%s\n' "${DR_STATE_TEST_PACKAGES:-}" >"$root/test-bin/packages"
  fi
  env -u DR_STATE_TEST_MODE -u DR_STATE_TEST_ROOT \
    -u DR_STATE_TEST_ESP_FREE -u DR_STATE_TEST_ESP_CLUSTER \
    -u DR_STATE_TEST_FAIL_STEP -u DR_STATE_TEST_OS_RELEASE \
    -u DR_STATE_TEST_OS_ID -u DR_STATE_TEST_OS_VERSION_ID \
    -u DR_STATE_TEST_KERNEL -u DR_STATE_TEST_PACKAGES \
    GRUBEDITENV_LOG="${GRUBEDITENV_LOG:-$root/grub-editenv.log}" \
    GRUBEDITENV_STATE="${GRUBEDITENV_STATE:-$root/grub-editenv.state}" \
    PATH="$root/test-bin:$PATH" PYTHONDONTWRITEBYTECODE=1 \
    /usr/bin/python3 -B -I "$DR" --sysroot "$root" "$@"
}

workflow_fixture() { # workflow_fixture ROOT full|sparse
  local root=$1 shape=${2:-full}
  mkdir -p "$root/esp" "$root/top" "$root/etc" "$root/run" \
    "$root/sys/firmware/efi/efivars" "$root/top/.dr-state" \
    "$root/boot" "$root/proc/sys/kernel" "$root/test-bin"
  ln -sfn ../esp "$root/boot/efi"
  ln -sfn top "$root/.top"
  printf '000001\n' >"$root/top/.dr-state/last-sequence"
  mkdir -p "$root/esp/EFI/dr"
  printf '%s\n' "$FIXTURE_BOOT" >"$root/esp/EFI/dr/bootloader"
  printf 'UUID=%s / btrfs defaults,noatime 0 0\n' "$UUID" >"$root/etc/fstab"
  printf 'PRETTY_NAME="Debian GNU/Linux 13 (fixture)"\nID=debian\nVERSION_ID=13\n' \
    >"$root/etc/os-release"
  printf '%s\n' "$KVER" >"$root/proc/sys/kernel/osrelease"
  # shellcheck disable=SC2016  # literal stub body
  printf '%s\n' '#!/usr/bin/env bash' 'printf "btrfs\n"' >"$root/test-bin/findmnt"
  # shellcheck disable=SC2016  # literal stub body
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
    >"$root/test-bin/grub-editenv"
  # shellcheck disable=SC2016  # literal stub body
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -eu' \
    '[ -f "${0%/*}/packages" ] && cat "${0%/*}/packages" || true' \
    >"$root/test-bin/dpkg-query"
  chmod 755 "$root/test-bin/findmnt" "$root/test-bin/grub-editenv" "$root/test-bin/dpkg-query"
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

workflow_grub_fixture() { # ROOT: GRUB backend with two ordinary managed states
  local root=$1
  workflow_fixture "$root" full
  printf 'grub\n' >"$root/esp/EFI/dr/bootloader"
  workflow_state "$root" @os-000001-20261118-120000Z-install
  workflow_state "$root" @os-000002-20261118-120100Z-apt
}

workflow_prune_delete_fixture() { # ROOT: four states, newest default, with two removable states
  local root=$1
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install
  workflow_state "$root" @os-000002-20261118-120100Z-apt
  workflow_state "$root" @os-000003-20261118-120200Z-apt
  workflow_state "$root" @os-000004-20261118-120300Z-apt
  workflow_run "$root" 999999999 4096 gen-menu --default @os-000004-20261118-120300Z-apt \
    >"$WORK/out" 2>"$WORK/err" \
    || { bad "workflow-prune-delete-setup: gen-menu failed"; return 1; }
  install_fake_maintenance_btrfs "$root"
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
    'uuid_for() { hash=$(printf "%s" "$1" | sha256sum | awk "{print \$1}"); printf "%s-%s-%s-%s-%s\n" "${hash:0:8}" "${hash:8:4}" "${hash:12:4}" "${hash:16:4}" "${hash:20:12}"; }' \
    'case "$op" in' \
    '  snapshot) source=${3:?}; mkdir -p -- "$dest"; uuid_for "$source" >"$dest/.fixture-parent-uuid" ;;' \
    '  show) [ -d "$dest" ] || exit 1; printf "Name: %s\nUUID: %s\n" "$dest" "$(uuid_for "$dest")"; [ ! -f "$dest/.fixture-parent-uuid" ] || printf "Parent UUID: %s\n" "$(cat "$dest/.fixture-parent-uuid")" ;;' \
    '  list) : ;;' \
    '  delete) state=${dest##*/}; entry="${FAKE_BLS_ENTRY_DIR:-}/${state#@}.conf"; if [ -n "${FAKE_BLS_ENTRY_DIR:-}" ] && [ -e "$entry" ]; then echo "BLS entry still exposes doomed state $state" >&2; exit 74; fi; if [ "${FAKE_BTRFS_FAIL_DELETE:-0}" = 1 ]; then exit 73; fi; /bin/rm -rf -- "$dest" ;;' \
    '  *) exit 64 ;;' \
    'esac' \
    >"$root/btrfs-bin/btrfs"
  chmod 755 "$root/btrfs-bin/btrfs"
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
 elif [[ "${created##*/}" != @os-* ]]; then
    bad "workflow-snapshot-$label: created non-selectable state $created"
  else
    id=${created#@}
    id_len=$(printf '%s' "$id" | wc -c)
    [ "$id_len" -le 240 ] || bad "workflow-snapshot-$label: entry id is $id_len bytes"
  fi
}

workflow_assert_entry() { # workflow_assert_entry ROOT STATE KVER
  local root=$1 state=$2 kver=$3 entry_id source_kernel source_initrd kernel_sha256 initrd_sha256 kid
  local entry linux_path initrd_path options_line
  case_count=$((case_count + 1))
  entry_id=${state#@}
  source_kernel="$root/top/$state/boot/vmlinuz-$kver"
  source_initrd="$root/top/$state/boot/initrd.img-$kver"
  kernel_sha256=$(sha256sum -- "$source_kernel" | awk '{print $1}') \
    || { bad "workflow-entry-source-hash: cannot hash $source_kernel"; return; }
  initrd_sha256=$(sha256sum -- "$source_initrd" | awk '{print $1}') \
    || { bad "workflow-entry-source-hash: cannot hash $source_initrd"; return; }
  kid=$(pair_id "$kver" "$kernel_sha256" "$initrd_sha256") \
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
  [ "$(sha256sum -- "$root/esp/EFI/dr/kernels/$kid/initrd.img" | awk '{print $1}')" = "$initrd_sha256" ] \
    || bad "workflow-entry-copy-hash: initrd differs for $state"
}

KVER=6.12.117+deb13-amd64
UUID=11111111-2222-3333-4444-555555555555
_LONG_REASON=$(printf '%*s' 400 '' | tr ' ' a)
APT_PLAN_V2="$WORK/apt-plan-v2"
cat >"$APT_PLAN_V2" <<'EOF'
VERSION 2
APT::Get::CommandLine::AsString=apt%20install%20hello

hello - < 2.10-3 /var/cache/apt/archives/hello.deb
libc6:amd64 2.36-9 < 2.37-1 /var/cache/apt/archives/libc6.deb
oldpkg 1.0 > - **REMOVE**
manualpkg 1.0 = 1.0 **CONFIGURE**
EOF

# Both backends use the same safe short slug and enumerate the result.
FIXTURE_BOOT=grub
WF_GRUB_REASON="$WORK/wf-grub-reason"
workflow_grub_fixture "$WF_GRUB_REASON"
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
workflow_grub_fixture "$WF_GRUB_SPACE_REASON"
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

FIXTURE_BOOT=systemd-boot

# Production workflow: cleanup is called before generation, and foreign look-alikes survive the real command.
WF_CLEAN="$WORK/wf-clean"
workflow_fixture "$WF_CLEAN" full
workflow_two_states "$WF_CLEAN"
mkdir -p "$WF_CLEAN/esp/EFI/dr/kernels/.dr-state.tmp.copied" \
  "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours"
printf x >"$WF_CLEAN/esp/EFI/dr/kernels/.dr-state.tmp.copied/vmlinuz"
printf x >"$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours/vmlinuz"
printf x >"$WF_CLEAN/esp/loader/.dr-state.tmp.loader"
printf x >"$WF_CLEAN/esp/loader/loader.conf.new.backup"
printf x >"$WF_CLEAN/esp/loader/entries/.dr-state.tmp.entry"
printf x >"$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.backup"
printf x >"$WF_CLEAN/esp/EFI/dr/.dr-state.tmp.marker"
printf x >"$WF_CLEAN/esp/EFI/dr/bootloader.new.backup"
printf x >"$WF_CLEAN/top/.dr-state/.dr-state.tmp.control"
printf x >"$WF_CLEAN/top/.dr-state/.dr-state-entry.new.backup"
case_count=$((case_count + 1))
workflow_run "$WF_CLEAN" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-cleanup: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  for path in "$WF_CLEAN/esp/EFI/dr/kernels/.dr-state.tmp.copied" \
    "$WF_CLEAN/esp/loader/.dr-state.tmp.loader" \
    "$WF_CLEAN/esp/loader/entries/.dr-state.tmp.entry" \
    "$WF_CLEAN/esp/EFI/dr/.dr-state.tmp.marker" \
    "$WF_CLEAN/top/.dr-state/.dr-state.tmp.control"; do
    [ ! -e "$path" ] || bad "workflow-cleanup: own scratch survived: $path"
  done
  for path in "$WF_CLEAN/esp/EFI/dr/kernels/.tmp.not-ours" \
    "$WF_CLEAN/esp/loader/loader.conf.new.backup" \
    "$WF_CLEAN/esp/loader/entries/.dr-state-entry.new.backup" \
    "$WF_CLEAN/esp/EFI/dr/bootloader.new.backup" \
    "$WF_CLEAN/top/.dr-state/.dr-state-entry.new.backup"; do
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

# Two states can have the same kernel version and initrd but different vmlinuz bytes. Pair-addressed ESP IDs must
# keep both bootable kernel files separate.
WF_SAME_INITRD="$WORK/wf-same-initrd"
workflow_fixture "$WF_SAME_INITRD" full
workflow_two_states "$WF_SAME_INITRD"
cp -- "$WF_SAME_INITRD/top/@os-000001-20261118-120000Z-install/boot/initrd.img-$KVER" \
  "$WF_SAME_INITRD/top/@os-000002-20261118-120100Z-apt/boot/initrd.img-$KVER"
case_count=$((case_count + 1))
workflow_run "$WF_SAME_INITRD" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-same-initrd: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  workflow_assert_entry "$WF_SAME_INITRD" @os-000001-20261118-120000Z-install "$KVER"
  workflow_assert_entry "$WF_SAME_INITRD" @os-000002-20261118-120100Z-apt "$KVER"
  kernel_sha_1=$(sha256sum -- "$WF_SAME_INITRD/top/@os-000001-20261118-120000Z-install/boot/vmlinuz-$KVER" | awk '{print $1}')
  kernel_sha_2=$(sha256sum -- "$WF_SAME_INITRD/top/@os-000002-20261118-120100Z-apt/boot/vmlinuz-$KVER" | awk '{print $1}')
  initrd_sha=$(sha256sum -- "$WF_SAME_INITRD/top/@os-000001-20261118-120000Z-install/boot/initrd.img-$KVER" | awk '{print $1}')
  kid_1=$(pair_id "$KVER" "$kernel_sha_1" "$initrd_sha")
  kid_2=$(pair_id "$KVER" "$kernel_sha_2" "$initrd_sha")
  [ "$kid_1" != "$kid_2" ] || bad "workflow-same-initrd: distinct vmlinuz files shared one ESP ID"
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

# list resolves the default state from the canonical systemd-boot entry id.
WF_LIST="$WORK/wf-list"
workflow_fixture "$WF_LIST" full
workflow_two_states "$WF_LIST"
printf 'default os-000002-20261118-120100Z-apt.conf\ntimeout 5\n' \
  >"$WF_LIST/esp/loader/loader.conf"
case_count=$((case_count + 1))
workflow_run "$WF_LIST" 999999999 4096 list >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-list-default: list rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- "@os-000002-20261118-120100Z-apt (default) kernel=$KVER" "$WORK/out"; then
  bad "workflow-list-default: list did not mark @os-000002-20261118-120100Z-apt as default"
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

# Prune protects both the default and pending one-shot state.
workflow_prune_form "$WORK/wf-prune-form" os-000100-20261118-120000Z-manual-default-conf.conf \
  os-000100-20261118-120000Z-manual-next-conf.conf suffixed

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

# A one-shot pending state remains even when its entry does not map to an existing state.
workflow_gen_preserves_oneshot "$WORK/wf-oneshot-suffixed" os-000002-20261118-120100Z-apt.conf suffixed

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

# Production prune route removes an unreferenced ESP kernel after regenerating the BLS entries.
WF_PRUNE="$WORK/wf-prune"
workflow_fixture "$WF_PRUNE" full
workflow_two_states "$WF_PRUNE"
workflow_run "$WF_PRUNE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err" \
  || bad "workflow-prune-setup: could not generate the valid default menu"
orphan=9.9.9+deb13-amd64-deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
mkdir -p "$WF_PRUNE/esp/EFI/dr/kernels/$orphan"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/$orphan/vmlinuz"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/$orphan/initrd.img"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_PRUNE" 999999999 4096 prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ -e "$WF_PRUNE/esp/EFI/dr/kernels/$orphan" ]; then
  bad "workflow-prune: unreferenced kernel directory survived"
else
  assert_loader_default_exists "$WF_PRUNE" "workflow-prune-success"
fi

# Foreign BLS entries use VFAT case-insensitive paths and may have an uppercase .CONF extension. Their kernel pair
# must remain protected while orphan ESP kernels are pruned.
WF_PRUNE_CASE="$WORK/wf-prune-vfat-case"
workflow_fixture "$WF_PRUNE_CASE" full
workflow_two_states "$WF_PRUNE_CASE"
workflow_run "$WF_PRUNE_CASE" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err" \
  || bad "workflow-prune-vfat-case: menu setup failed"
foreign_pair=Foreign-Kernel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
mkdir -p "$WF_PRUNE_CASE/esp/EFI/dr/kernels/$foreign_pair"
printf 'foreign kernel\n' >"$WF_PRUNE_CASE/esp/EFI/dr/kernels/$foreign_pair/vmlinuz"
printf 'foreign initrd\n' >"$WF_PRUNE_CASE/esp/EFI/dr/kernels/$foreign_pair/initrd.img"
cat >"$WF_PRUNE_CASE/esp/loader/entries/foreign-entry.CONF" <<'EOF'
title foreign entry
linux /efi/DR/KERNELS/foreign-kernel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/VMLINUZ
initrd /EFI/dr/kernels/FOREIGN-KERNEL-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/INITRD.IMG
EOF
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_PRUNE_CASE" 999999999 4096 prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune-vfat-case: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ ! -d "$WF_PRUNE_CASE/esp/EFI/dr/kernels/$foreign_pair" ]; then
  bad "workflow-prune-vfat-case: removed a kernel referenced by a foreign uppercase .CONF entry"
elif [ ! -s "$WF_PRUNE_CASE/esp/loader/entries/foreign-entry.CONF" ]; then
  bad "workflow-prune-vfat-case: removed a foreign BLS entry"
fi

# Two doomed states must both disappear from the menu before the first Btrfs delete command runs.
WF_PRUNE_DELETE="$WORK/wf-prune-delete-order"
workflow_prune_delete_fixture "$WF_PRUNE_DELETE" \
  || bad "workflow-prune-delete-order: fixture setup failed"
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install \
  FAKE_BLS_ENTRY_DIR="$WF_PRUNE_DELETE/esp/loader/entries" \
  PATH="$WF_PRUNE_DELETE/btrfs-bin:$PATH" \
  workflow_run "$WF_PRUNE_DELETE" 999999999 4096 prune --keep 0 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune-delete-order: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
else
  for state in @os-000002-20261118-120100Z-apt @os-000003-20261118-120200Z-apt; do
    [ ! -e "$WF_PRUNE_DELETE/top/$state" ] || bad "workflow-prune-delete-order: $state subvolume survived"
    [ ! -e "$WF_PRUNE_DELETE/esp/loader/entries/${state#@}.conf" ] \
      || bad "workflow-prune-delete-order: $state remained in the boot menu"
  done
  [ -d "$WF_PRUNE_DELETE/top/@os-000004-20261118-120300Z-apt" ] \
    || bad "workflow-prune-delete-order: selected default state was deleted"
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

boot_agnostic_cases() { # TAG BINARY MARKER
  local tag=$1 binary=$2 marker=$3 prior_dr=$DR prior_marker=$FIXTURE_BOOT
  local root created created_again deleted_exists required rc snap_rc selected_before \
    selected_after snapshots apt_state apt_input
  DR=$binary
  FIXTURE_BOOT=$marker

  # Snapshot reasons and the overlong-name boundary are backend-independent.
  root="$WORK/bag-$tag-reason"
  workflow_snapshot_reason "$root-rollback" rollback.conf manual-rollback-conf
  workflow_snapshot_reason "$root-apt" apt.conf manual-apt-conf
  workflow_snapshot_reason "$root-kernel" kernel.conf manual-kernel-conf
  workflow_snapshot_reason "$root-space" 'bad name' manual-bad-name
  workflow_snapshot_reason "$root-slash" 'bad/name' manual-bad-name
  workflow_snapshot_reason "$root-at" 'bad@name' manual-bad-name
  workflow_snapshot_reason "$root-control" $'bad\tname' manual-bad-name
  workflow_snapshot_overlong_reason "$root-overlong" "$_LONG_REASON" overlong

  # Metadata, list/show, delete-metadata and no sequence reuse.
  root="$WORK/bag-$tag-metadata"
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install
  install_fake_maintenance_btrfs "$root"
  case_count=$((case_count + 1))
  DR_STATE=@os-000001-20261118-120000Z-install \
  DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' \
  DR_STATE_TEST_OS_ID=debian DR_STATE_TEST_OS_VERSION_ID=13 \
  DR_STATE_TEST_KERNEL=6.12.117-fixture DR_STATE_TEST_PACKAGES=$'apt\t3.0\ndpkg\t1.22' \
  PATH="$root/btrfs-bin:$PATH" \
    workflow_run "$root" 999999999 4096 snapshot --reason manual \
      --note $'before apt install hello\noperator context' >"$WORK/out" 2>"$WORK/err"; rc=$?
  created=$(find "$root/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-manual' -printf '%f\n' | head -n 1)
  if [ "$rc" != 0 ] || [[ ! "$created" =~ ^@os-000002-[0-9]{8}-[0-9]{6}Z-manual$ ]]; then
    bad "workflow-snapshot-metadata: rc=$rc state='$created' ($(tr '\n' ' ' <"$WORK/err"))"
  else
    DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$root" 999999999 4096 show "$created" \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
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
    DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$root" 999999999 4096 list \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
    if [ "$rc" != 0 ] || ! grep -Fq "$created" "$WORK/out" \
      || ! grep -Fq 'created=' "$WORK/out" || ! grep -Fq 'reason=manual' "$WORK/out" \
      || ! grep -Fq 'note=before apt install hello operator context' "$WORK/out"; then
      bad "workflow-snapshot-list: report omitted its snapshot summary: $(tr '\n' ' ' <"$WORK/out")"
    fi
    DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$root" 999999999 4096 list --long \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
    if [ "$rc" != 0 ] || ! grep -Fq 'key-package-versions:' "$WORK/out" \
      || ! grep -Fq 'operator context' "$WORK/out"; then
      bad "workflow-snapshot-list-long: detailed report omitted packages or note: $(tr '\n' ' ' <"$WORK/out")"
    fi
    DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
      workflow_run "$root" 999999999 4096 delete "$created" --yes \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
    if [ "$rc" != 0 ] || [ -e "$root/top/.dr-state/metadata/$created.meta" ] \
      || [ "$(cat "$root/top/.dr-state/last-sequence")" != 000002 ]; then
      bad "workflow-snapshot-delete-metadata: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
    fi
    case_count=$((case_count + 1))
    DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
      workflow_run "$root" 999999999 4096 snapshot --reason before-upgrade \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
    created_again=$(find "$root/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000003-*-*Z-manual-before-upgrade' -printf '%f\n' | head -n 1)
    if [ -e "$root/top/$created" ]; then deleted_exists=yes; else deleted_exists=no; fi
    if [ "$rc" != 0 ] || [ -z "$created_again" ] \
      || [ -e "$root/top/$created" ]; then
      bad "workflow-sequence-not-reused-after-delete: rc=$rc created='$created_again' deleted='$created' deleted-exists=$deleted_exists ($(tr '\n' ' ' <"$WORK/err"))"
    fi
  fi

  if [ "$marker" = grub ]; then
    # A configured GRUB default must survive the next snapshot's publication.
    root="$WORK/bag-$tag-configured-default"
    workflow_fixture "$root" full
    workflow_two_states "$root"
    install_fake_maintenance_btrfs "$root"
    case_count=$((case_count + 1))
    DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
      workflow_run "$root" 999999999 4096 set-default @os-000002-20261118-120100Z-apt \
      >"$WORK/out" 2>"$WORK/err"; rc=$?
    selected_before=$(cat "$root/grub-editenv.state" 2>/dev/null)
    DR_STATE=@os-000001-20261118-120000Z-install PATH="$root/btrfs-bin:$PATH" \
      workflow_run "$root" 999999999 4096 snapshot --reason before-upgrade \
      >"$WORK/out" 2>"$WORK/err"; snap_rc=$?
    selected_after=$(cat "$root/grub-editenv.state" 2>/dev/null)
    if [ "$rc" != 0 ] || [ "$snap_rc" != 0 ] \
      || [ "$selected_before" != @os-000002-20261118-120100Z-apt ] \
      || [ "$selected_after" != "$selected_before" ]; then
      bad "workflow-set-default-survives-snapshot: set-default=$rc snapshot=$snap_rc before='$selected_before' after='$selected_after' ($(tr '\n' ' ' <"$WORK/err"))"
    fi
  fi

  # Fresh initialization and its idempotent repeat.
  root="$WORK/bag-$tag-initialize"
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261121-120000Z-install
  workflow_remove_state_counter "$root"
  case_count=$((case_count + 1))
  DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' DR_STATE_TEST_KERNEL=6.12.117-fixture \
    workflow_run "$root" 999999999 4096 initialize-state \
      @os-000001-20261121-120000Z-install 20261121120000 >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || [ "$(cat "$root/top/.dr-state/last-sequence" 2>/dev/null)" != 000001 ] \
    || [ ! -f "$root/top/.dr-state/metadata/@os-000001-20261121-120000Z-install.meta" ]; then
    bad "workflow-initialize-state: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  else
    DR_STATE=@os-000001-20261121-120000Z-install workflow_run "$root" 999999999 4096 \
      show @os-000001-20261121-120000Z-install >"$WORK/out" 2>"$WORK/err"; rc=$?
    if [ "$rc" != 0 ] || ! grep -Fqx 'sequence: 000001' "$WORK/out" \
      || ! grep -Fqx 'origin-slug: install' "$WORK/out" \
      || ! grep -Fqx 'source: install' "$WORK/out"; then
      bad "workflow-initialize-state-metadata: rc=$rc report=$(tr '\n' ' ' <"$WORK/out")"
    fi
    case_count=$((case_count + 1))
    DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' DR_STATE_TEST_KERNEL=6.12.117-fixture \
      workflow_run "$root" 999999999 4096 initialize-state \
        @os-000001-20261121-120000Z-install 20261121120000 >"$WORK/out" 2>"$WORK/err"; rc=$?
    [ "$rc" = 0 ] && [ "$(cat "$root/top/.dr-state/last-sequence")" = 000001 ] \
      || bad "workflow-initialize-state-idempotent: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  fi

  # One APT snapshot, then the same-window hook appends unique package actions.
  root="$WORK/bag-$tag-apt"
  workflow_fixture "$root" full
  workflow_state "$root" @os-000001-20261118-120000Z-install
  install_fake_maintenance_btrfs "$root"
  for required in 1 2; do
    if [ "$required" = 1 ]; then
      apt_input=$APT_PLAN_V2
    else
      apt_input="$root/apt-plan-second"
      printf 'VERSION 2\nAPT::Get::CommandLine::AsString=apt%%20upgrade\n\nbar 1.0 < 2.0 /var/cache/apt/archives/bar.deb\n' \
        >"$apt_input"
    fi
    case_count=$((case_count + 1))
    (
      exec 3<"$apt_input"
      DR_STATE=@os-000001-20261118-120000Z-install APT_HOOK_INFO_FD=3 \
      DR_STATE_TEST_OS_RELEASE='Debian GNU/Linux 13 (fixture)' \
      DR_STATE_TEST_KERNEL=6.12.117-fixture DR_STATE_TEST_PACKAGES=$'apt\t3.0' \
      PATH="$root/btrfs-bin:$PATH" workflow_run "$root" 999999999 4096 apt-pre-install-pkgs
    ) >"$WORK/out" 2>"$WORK/err"; rc=$?
    [ "$rc" = 0 ] || bad "workflow-apt-plan-round-$required: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
  done
  snapshots=$(find "$root/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-apt' -printf '%f\n' | wc -l)
  apt_state=$(find "$root/top" -mindepth 1 -maxdepth 1 -type d -name '@os-000002-*-*Z-apt' -printf '%f\n' | head -n 1)
  DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$root" 999999999 4096 show "$apt_state" \
    >"$WORK/out" 2>"$WORK/err"; rc=$?
  if [ "$rc" != 0 ] || [ "$snapshots" != 1 ] \
    || ! grep -Fqx 'source: apt' "$WORK/out" \
    || ! grep -Fqx 'reason: apt' "$WORK/out" \
    || ! grep -Fq 'install hello - -> 2.10-3' "$WORK/out" \
    || ! grep -Fq 'upgrade libc6:amd64 2.36-9 -> 2.37-1' "$WORK/out" \
    || ! grep -Fq 'upgrade bar 1.0 -> 2.0' "$WORK/out"; then
    bad "workflow-apt-plan-metadata: rc=$rc snapshots=$snapshots report=$(tr '\n' ' ' <"$WORK/out")"
  fi

  DR=$prior_dr
  FIXTURE_BOOT=$prior_marker
}

boot_agnostic_cases python "$DR_PY" grub
boot_agnostic_cases systemd-boot "$DR_PY" systemd-boot

# D9 replacements: unmanaged objects are listed, never published, and never mutation targets.
DR="$DR_PY"
FIXTURE_BOOT=grub
WF_UNMANAGED="$WORK/wf-unmanaged"
workflow_fixture "$WF_UNMANAGED" full
workflow_two_states "$WF_UNMANAGED"
workflow_state "$WF_UNMANAGED" @os-0001
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_UNMANAGED" 999999999 4096 gen-menu \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || ! grep -Fq '@os-0001 (unmanaged)' <(workflow_run "$WF_UNMANAGED" 999999999 4096 list 2>/dev/null) \
  || grep -Fq 'Debian @os-0001 ' "$WF_UNMANAGED/esp/EFI/dr/grub.cfg"; then
  bad "workflow-unmanaged-listed: unmanaged state was not listed or entered the menu ($(tr '\n' ' ' <"$WORK/err"))"
fi
case_count=$((case_count + 1))
DR_STATE=@os-000001-20261118-120000Z-install workflow_run "$WF_UNMANAGED" 999999999 4096 \
  delete @os-0001 --yes >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 3 ] || ! grep -Fq 'unmanaged' "$WORK/err" || [ ! -d "$WF_UNMANAGED/top/@os-0001" ]; then
  bad "workflow-unmanaged-listed: unmanaged delete wanted exit 3 and no change, got $rc"
fi

WF_UNMANAGED_RUN="$WORK/wf-unmanaged-running"
workflow_fixture "$WF_UNMANAGED_RUN" full
workflow_state "$WF_UNMANAGED_RUN" @os-0001
install_fake_snapshot_btrfs "$WF_UNMANAGED_RUN"
counter_before=$(cat "$WF_UNMANAGED_RUN/top/.dr-state/last-sequence")
case_count=$((case_count + 1))
DR_STATE=@os-0001 PATH="$WF_UNMANAGED_RUN/btrfs-bin:$PATH" \
  workflow_run "$WF_UNMANAGED_RUN" 999999999 4096 snapshot --reason before-upgrade \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
counter_after=$(cat "$WF_UNMANAGED_RUN/top/.dr-state/last-sequence")
if [ "$rc" != 3 ] || ! grep -Fq 'unmanaged' "$WORK/err" \
  || [ "$counter_before" != "$counter_after" ] \
  || find "$WF_UNMANAGED_RUN/top" -mindepth 1 -maxdepth 1 -name '@os-000002-*' -print -quit | grep -q .; then
  bad "workflow-unmanaged-running-snapshot: understood running state or changed storage (rc=$rc)"
fi
if [ "$fail" = 0 ]; then
  echo "test-dr-state-sdboot: OK ($case_count cases)"
fi
exit "$fail"
