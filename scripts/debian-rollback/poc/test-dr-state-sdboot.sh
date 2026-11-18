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
#   _sdboot-loader-conf DEFAULT_ID  (existing loader.conf on stdin; rewritten on stdout)
#   _sdboot-select-backend CONF_VALUE MARKER_VALUE
#   _sdboot-conf-backend FILE
#   _sdboot-marker-backend FILE
#   _sdboot-source-value LABEL PRESENT VALUE
#   _sdboot-entry-ids STATE...
#   _sdboot-byte-length TEXT
#   _sdboot-dir-alloc ROOT CLUSTER RELDIR...
#   _sdboot-alloc-sum CLUSTER SIZE...
#   _sdboot-clean-scratch KERNEL_DIR ENTRY_DIR LOADER_CONF MARKER
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
    "$root/sys/firmware/efi/efivars"
  printf 'BOOTLOADER=systemd-boot\n' >"$root/etc/dr-state.conf"
  printf '%s / btrfs defaults,noatime 0 0\n' "$UUID" >"$root/etc/fstab"
  if [ "$shape" = full ]; then
    mkdir -p "$root/esp/EFI/dr/kernels" "$root/esp/loader/entries"
  fi
}

workflow_state() { # workflow_state ROOT STATE
  local root=$1 state=$2
  mkdir -p "$root/top/$state/boot"
  printf 'kernel for %s\n' "$state" >"$root/top/$state/boot/vmlinuz-$KVER"
  printf 'initrd for %s\n' "$state" >"$root/top/$state/boot/initrd.img-$KVER"
}

workflow_two_states() { # workflow_two_states ROOT
  local root=$1
  workflow_state "$root" @os-0001
  workflow_state "$root" @os-0002
}

workflow_stale_default_fixture() { # ROOT: old default @os-0002 exists, then its state disappears from TOP
  local root=$1
  workflow_fixture "$root" full
  workflow_two_states "$root"
  workflow_run "$root" 999999999 4096 gen-menu --default @os-0002 \
    >"$WORK/out" 2>"$WORK/err" \
    || { bad "workflow-stale-setup: gen-menu failed"; return 1; }
  [ -s "$root/esp/loader/entries/os-0002.conf" ] \
    || { bad "workflow-stale-setup: missing old default entry"; return 1; }
  mv "$root/top/@os-0002" "$root/removed-state-0002"
}

assert_loader_default_exists() { # assert_loader_default_exists ROOT LABEL
  local root=$1 label=$2 count default_id
  case_count=$((case_count + 1))
  [ -s "$root/esp/loader/loader.conf" ] \
    || { bad "$label: loader.conf is missing"; return; }
  count=$(awk '$1 == "default" {n++} END {print n+0}' "$root/esp/loader/loader.conf")
  [ "$count" = 1 ] || { bad "$label: loader.conf has $count defaults"; return; }
  default_id=$(awk '$1 == "default" {print $2; exit}' "$root/esp/loader/loader.conf")
  [ -s "$root/esp/loader/entries/$default_id.conf" ] \
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
title Debian @os-0001 (kernel 6.12.117+deb13-amd64)
linux /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/vmlinuz
initrd /EFI/dr/kernels/6.12.117+deb13-amd64-0123456789ab/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-0001 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-0001
EOF
)
expect_text entry-text-main "$ENTRY_A" "$DR" _sdboot-entry-text @os-0001 "$KVER" "$UUID" "$ID_A"

ENTRY_SNAP=$(cat <<EOF
# generated by dr-state sdboot; do not edit
title Debian @os-pre-20261118131158-605-s5 (kernel 6.12.107+deb13-amd64)
linux /EFI/dr/kernels/6.12.107+deb13-amd64-fedcba987654/vmlinuz
initrd /EFI/dr/kernels/6.12.107+deb13-amd64-fedcba987654/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-pre-20261118131158-605-s5 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-pre-20261118131158-605-s5
EOF
)
expect_text entry-text-snapshot "$ENTRY_SNAP" "$DR" _sdboot-entry-text @os-pre-20261118131158-605-s5 \
  6.12.107+deb13-amd64 "$UUID" 6.12.107+deb13-amd64-fedcba987654
ENTRY_BACKPORTS=$(cat <<EOF
# generated by dr-state sdboot; do not edit
title Debian @os-0001 (kernel 7.2.12+deb13-amd64)
linux /EFI/dr/kernels/7.2.12+deb13-amd64-fedcba987654/vmlinuz
initrd /EFI/dr/kernels/7.2.12+deb13-amd64-fedcba987654/initrd.img
options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol=@os-0001 ro net.ifnames=0 console=tty0 console=ttyS0,115200
sort-key os-0001
EOF
)
expect_text entry-text-backports "$ENTRY_BACKPORTS" "$DR" _sdboot-entry-text @os-0001 \
  7.2.12+deb13-amd64 "$UUID" 7.2.12+deb13-amd64-fedcba987654
expect_exit entry-text-rejects-bad-uuid 1 "$DR" _sdboot-entry-text @os-0001 "$KVER" 'not/a-uuid' "$ID_A"
expect_exit entry-text-rejects-kver-id-mismatch 1 "$DR" _sdboot-entry-text @os-0001 "$KVER" "$UUID" \
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
expect_text entry-id-main os-0001 "$DR" _sdboot-entry-id @os-0001
expect_text entry-id-snapshot os-pre-20261118131158-605-s5 "$DR" _sdboot-entry-id @os-pre-20261118131158-605-s5
expect_text entry-id-punctuation os-pre-20261118100000-1-s5_test.v2 "$DR" _sdboot-entry-id @os-pre-20261118100000-1-s5_test.v2
expect_text state-from-id-main @os-0001 "$DR" _sdboot-state-from-id os-0001
expect_text state-from-id-snapshot @os-pre-20261118131158-605-s5 "$DR" _sdboot-state-from-id os-pre-20261118131158-605-s5
# shellcheck disable=SC2016  # the child bash expands its positional parameters
expect_exit entry-id-roundtrip 0 bash -c 's=$("$1" _sdboot-entry-id "$2") || exit; t=$("$1" _sdboot-state-from-id "$s") || exit; [ "$t" = "$2" ]' \
  _ "$DR" @os-pre-20261118100000-1-a_b.c-d
expect_exit entry-id-rejects-no-prefix 1 "$DR" _sdboot-entry-id os-0001
expect_exit entry-id-rejects-slash 1 "$DR" _sdboot-entry-id '@os-pre-2026/evil'
expect_exit entry-id-rejects-space 1 "$DR" _sdboot-entry-id '@os-pre-2026 evil'
expect_exit entry-id-rejects-dotdot 1 "$DR" _sdboot-entry-id '@os-pre-2026..evil'
expect_exit entry-id-rejects-embedded-at 1 "$DR" _sdboot-entry-id '@os-pre-2026@evil'
expect_exit state-from-id-rejects-at 1 "$DR" _sdboot-state-from-id '@os-0001'
expect_exit state-from-id-rejects-slash 1 "$DR" _sdboot-state-from-id 'os-pre/evil'
expect_exit state-from-id-rejects-empty-tail 1 "$DR" _sdboot-state-from-id 'os-'
expect_text entry-ids-distinct "os-0001
os-pre-20261118131158-605-s5" "$DR" _sdboot-entry-ids @os-0001 @os-pre-20261118131158-605-s5
expect_exit entry-ids-case-collision 1 "$DR" _sdboot-entry-ids @os-lower @os-LOWER
expect_exit entry-ids-unsafe-state 1 "$DR" _sdboot-entry-ids @os-ok '@os-bad/name'
case_count=$((case_count + 1))
"$DR" _sdboot-entry-ids @os-lower @os-LOWER >"$WORK/out" 2>"$WORK/err" || true
grep -Fq 'case-insensitively collide' "$WORK/err" \
  || bad "entry-ids-case-message: collision refusal does not explain the collision"

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
default os-old
timeout 10
auto-entries yes
auto-firmware no
EOF
)
LOADER_WANT=$(cat <<'EOF'
# local settings
auto-entries yes
auto-firmware no
default os-0001
timeout 5
EOF
)
got=$(printf '%s\n' "$LOADER_IN" | "$DR" _sdboot-loader-conf os-0001 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT" ]; then
  echo "FAIL loader-replace: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_WANT_ADD=$(cat <<'EOF'
default os-0001
timeout 5
EOF
)
got=$(printf 'timeout 10\n' | "$DR" _sdboot-loader-conf os-0001 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT_ADD" ]; then
  echo "FAIL loader-add-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_WANT_NEW=$(cat <<'EOF'
default os-0001
timeout 5
EOF
)
got=$(printf '' | "$DR" _sdboot-loader-conf os-0001 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_WANT_NEW" ]; then
  echo "FAIL loader-new: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi

LOADER_DUP_IN=$(cat <<'EOF'
default os-a
keep=first
default os-b
timeout 1
keep=second
timeout 9
EOF
)
LOADER_DUP_WANT=$(cat <<'EOF'
keep=first
keep=second
default os-new
timeout 5
EOF
)
got=$(printf '%s\n' "$LOADER_DUP_IN" | "$DR" _sdboot-loader-conf os-new 2>"$WORK/err"); rc=$?
case_count=$((case_count + 1))
if [ "$rc" != 0 ] || [ "$got" != "$LOADER_DUP_WANT" ]; then
  echo "FAIL loader-duplicates: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"; echo "$got"; fail=1
fi
expect_exit loader-bad-default-id 1 bash -c "printf '' | \"\$1\" _sdboot-loader-conf '@os-0001'" _ "$DR"
expect_exit loader-empty-without-default 1 bash -c "printf '' | \"\$1\" _sdboot-loader-conf ''" _ "$DR"
expect_exit loader-malformed-default-line 1 bash -c "printf 'default\\n' | \"\$1\" _sdboot-loader-conf 'os-0001'" _ "$DR"
expect_exit loader-malformed-timeout-line 1 bash -c "printf 'timeout later\\n' | \"\$1\" _sdboot-loader-conf 'os-0001'" _ "$DR"

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
  workflow_assert_entry "$WF_CORE" @os-0001 "$KVER"
  workflow_assert_entry "$WF_CORE" @os-0002 "$KVER"
fi

# Selection prefers the newest COMPLETE pair; a newer zero-byte kernel cannot hide the older bootable pair.
WF_OLDER="$WORK/wf-older-pair"
KVER_OLD=6.12.107+deb13-amd64
KVER_NEW=6.12.117+deb13-amd64
workflow_fixture "$WF_OLDER" full
mkdir -p "$WF_OLDER/top/@os-0001/boot"
printf 'older kernel\n' >"$WF_OLDER/top/@os-0001/boot/vmlinuz-$KVER_OLD"
printf 'older initrd\n' >"$WF_OLDER/top/@os-0001/boot/initrd.img-$KVER_OLD"
: >"$WF_OLDER/top/@os-0001/boot/vmlinuz-$KVER_NEW"
printf 'newer initrd only\n' >"$WF_OLDER/top/@os-0001/boot/initrd.img-$KVER_NEW"
case_count=$((case_count + 1))
workflow_run "$WF_OLDER" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-latest-complete: gen-menu rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif ! grep -Fqx -- "title Debian @os-0001 (kernel $KVER_OLD)" \
  "$WF_OLDER/esp/loader/entries/os-0001.conf"; then
  bad "workflow-latest-complete: did not select the older complete pair"
else
  workflow_assert_entry "$WF_OLDER" @os-0001 "$KVER_OLD"
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
workflow_state "$WF_COLLISION" @os-0001
printf 'title foreign owner\n' >"$WF_COLLISION/esp/loader/entries/OS-0001.CONF"
case_count=$((case_count + 1))
workflow_run "$WF_COLLISION" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-collision: want exit 1, got $rc"
elif ! grep -Fq 'case-insensitively collides' "$WORK/err"; then
  bad "workflow-collision: refusal did not explain the case alias"
elif [ "$(cat "$WF_COLLISION/esp/loader/entries/OS-0001.CONF")" != 'title foreign owner' ]; then
  bad "workflow-collision: foreign entry was changed"
elif [ -e "$WF_COLLISION/esp/loader/loader.conf" ]; then
  bad "workflow-collision: loader.conf was written despite refusal"
fi

# Production workflow: even an exact filename is refused when it does not carry the dr-state ownership marker.
WF_FOREIGN="$WORK/wf-foreign-exact"
workflow_fixture "$WF_FOREIGN" full
workflow_state "$WF_FOREIGN" @os-0001
printf 'title foreign exact name\n' >"$WF_FOREIGN/esp/loader/entries/os-0001.conf"
case_count=$((case_count + 1))
workflow_run "$WF_FOREIGN" 999999999 4096 gen-menu >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 1 ]; then
  bad "workflow-foreign-exact: want exit 1, got $rc"
elif ! grep -Fq 'is not owned by dr-state' "$WORK/err"; then
  bad "workflow-foreign-exact: refusal did not explain ownership"
elif [ "$(cat "$WF_FOREIGN/esp/loader/entries/os-0001.conf")" != 'title foreign exact name' ]; then
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
elif [ "$(awk '$1 == "default" {print $2}' "$WF_DEFAULT/esp/loader/loader.conf")" != os-0001 ]; then
  bad "workflow-default-first: first sorted entry was not selected"
fi
case_count=$((case_count + 1))
workflow_run "$WF_DEFAULT" 999999999 4096 set-default @os-0002 >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-set-default: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ "$(awk '$1 == "default" {print $2}' "$WF_DEFAULT/esp/loader/loader.conf")" != os-0002 ]; then
  bad "workflow-set-default: loader.conf did not select os-0002"
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
DR_STATE=@os-0001 FAIL_LOADER_CONF="$WF_PRUNE_ORDER/esp/loader/loader.conf" \
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
DR_STATE=@os-0001 workflow_run_fail "$WF_PRUNE_STALE" stale-delete prune --keep 10 \
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
mkdir -p "$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe/vmlinuz"
printf x >"$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe/initrd.img"
case_count=$((case_count + 1))
DR_STATE=@os-0001 workflow_run "$WF_PRUNE" 999999999 4096 prune --keep 10 \
  >"$WORK/out" 2>"$WORK/err"; rc=$?
if [ "$rc" != 0 ]; then
  bad "workflow-prune: rc=$rc ($(tr '\n' ' ' <"$WORK/err"))"
elif [ -e "$WF_PRUNE/esp/EFI/dr/kernels/9.9.9+deb13-amd64-deadbeefcafe" ]; then
  bad "workflow-prune: unreferenced kernel directory survived"
else
  assert_loader_default_exists "$WF_PRUNE" "workflow-prune-success"
fi

if [ "$fail" = 0 ]; then
  echo "test-dr-state-sdboot: OK ($case_count cases)"
fi
exit "$fail"
