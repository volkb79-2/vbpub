#!/usr/bin/env bash
# Check the btrfs behaviour docs/BTRFS-COW.md relies on, on a THROWAWAY filesystem (the [T] items of that guide).
#   mdt-cow-selfcheck.sh [--preflight-only] [--keep] [--workdir DIR] [--size-mb N]
# Builds a btrfs on a sparse loop file under DIR (default /var/tmp), mounts it only below that directory, runs the
# checks, then unmounts, detaches the loop device and deletes the directory (unless --keep). It touches nothing else:
# no fstab, no real mount point, no existing filesystem. It does load the btrfs kernel module if it is missing.
# Run it as root on a SCRATCH host first. --preflight-only only reports what is available and loads nothing.
# Output: PASS / FAIL for expectations the guide states, INFO for observations the guide could not pin down.
# Exit: 0 all expectations held, 1 at least one FAIL, 2 usage, 3 prerequisites missing.
set -uo pipefail

preflight_only=0 keep=0 workdir=/var/tmp size_mb=512
while [ $# -gt 0 ]; do
  case "$1" in
    --preflight-only) preflight_only=1 ;;
    --keep) keep=1 ;;
    --workdir) workdir="${2:-}"; shift ;;
    --size-mb) size_mb="${2:-}"; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$size_mb" in ''|*[!0-9]*) echo "--size-mb needs a number" >&2; exit 2 ;; esac

npass=0 nfail=0
pass() { npass=$((npass + 1)); echo "PASS  $1"; }
fail() { nfail=$((nfail + 1)); echo "FAIL  $1${2:+ -- $2}"; }
info() { echo "INFO  $1${2:+ -- $2}"; }
head_() { echo; echo "== $*"; }

# ---- prerequisites ---------------------------------------------------------------------------------
missing=()
for c in mkfs.btrfs btrfs losetup mount umount truncate chattr lsattr stat cp setpriv unshare findmnt mktemp mkfifo; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
btrfs_known=no
grep -qw btrfs /proc/filesystems 2>/dev/null && btrfs_known=loaded
[ "$btrfs_known" = no ] && command -v modinfo >/dev/null 2>&1 && modinfo btrfs >/dev/null 2>&1 && btrfs_known=loadable
echo "kernel: $(uname -r)   btrfs-progs: $(btrfs --version 2>/dev/null || echo missing)   btrfs kernel support: $btrfs_known"
echo "root: $([ "$(id -u)" = 0 ] && echo yes || echo no)   missing commands: ${missing[*]:-none}"
if [ "${#missing[@]}" -gt 0 ] || [ "$btrfs_known" = no ] || [ "$(id -u)" != 0 ]; then
  echo "prerequisites missing: need root, the commands above (apt install btrfs-progs util-linux) and btrfs support" >&2
  exit 3
fi
[ "$preflight_only" = 1 ] && { echo "preflight ok"; exit 0; }

# ---- scratch filesystem ----------------------------------------------------------------------------
[ -d "$workdir" ] || { echo "workdir $workdir is not a directory" >&2; exit 2; }
[ "$btrfs_known" = loadable ] && { modprobe btrfs || { echo "cannot load the btrfs module" >&2; exit 3; }; }
WORK=$(mktemp -d "$workdir/mdt-cow-selfcheck.XXXXXX") || exit 3
chmod 0755 "$WORK"
LOOP=""
cleanup() {
  cd / || true
  # unmount everything below $WORK, deepest first, then free the loop device
  findmnt -rno TARGET 2>/dev/null | grep -F "$WORK/" | sort -r | while read -r t; do umount -l "$t" 2>/dev/null; done
  [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
  if [ "$keep" = 1 ]; then echo "kept: $WORK"; else rm -rf -- "$WORK"; fi
}
trap cleanup EXIT

truncate -s "${size_mb}M" "$WORK/fs.img" || exit 3
LOOP=$(losetup --find --show "$WORK/fs.img") || { echo "no loop device" >&2; exit 3; }
mkfs.btrfs -q -L mdtcowtest -m dup -d single "$LOOP" >/dev/null || { echo "mkfs.btrfs failed" >&2; exit 3; }
MNT="$WORK/top"; mkdir -p "$MNT"
mount -o noatime,discard=async,user_subvol_rm_allowed "$LOOP" "$MNT" || { echo "mount failed" >&2; exit 3; }
echo "scratch filesystem: $LOOP at $MNT"

NOBODY_UID=$(id -u nobody 2>/dev/null || echo 65534)
NOBODY_GID=$(id -g nobody 2>/dev/null || echo 65534)
as_user() { setpriv --reuid="$NOBODY_UID" --regid="$NOBODY_GID" --clear-groups "$@"; }
prop() { btrfs property get "$1" compression 2>/dev/null | sed 's/^compression=//'; }
data_used() { btrfs filesystem df -b "$MNT" 2>/dev/null | sed -n 's/^Data,.*used=\([0-9]*\).*/\1/p' | head -1; }

# ---- 1. IDs and subvolume lookup -------------------------------------------------------------------
head_ "IDs and how subvol= resolves (guide 2.2)"
if btrfs subvolume show "$MNT" 2>/dev/null | grep -Eq 'Subvolume ID:[[:space:]]+5$'; then pass "top level has subvolume ID 5"
else fail "top level has subvolume ID 5" "$(btrfs subvolume show "$MNT" 2>&1 | head -3 | tr '\n' ' ')"; fi
btrfs subvolume create "$MNT/svc" >/dev/null || fail "create subvolume svc"
id_svc=$(btrfs subvolume list "$MNT" 2>/dev/null | sed -n 's/^ID \([0-9]*\) .* path svc$/\1/p')
if [ -n "$id_svc" ] && [ "$id_svc" -ge 256 ]; then pass "new subvolume gets an ID >= 256 (got $id_svc)"
else fail "new subvolume gets an ID >= 256" "got '${id_svc:-none}'"; fi

SUB="$WORK/sub"; mkdir -p "$SUB"
if mount -o subvol=svc "$LOOP" "$SUB"; then
  echo hello >"$SUB/f"
  if findmnt -no SOURCE "$SUB" | grep -q '\[/svc\]$'; then pass "subvol=svc mounts the subvolume (findmnt shows [/svc])"
  else fail "findmnt shows the subvolume" "$(findmnt -no SOURCE "$SUB")"; fi
  [ -e "$MNT/svc/f" ] && pass "files written through the subvol mount appear under the top level path svc/" || fail "same data via top level"
  [ ! -e "$SUB/svc" ] && pass "the subvolume mount does not show the top level or siblings" || fail "subvolume mount isolation"
else fail "mount -o subvol=svc"; fi

BARE="$WORK/bare"; mkdir -p "$BARE"
mount "$LOOP" "$BARE" 2>/dev/null
[ -d "$BARE/svc" ] && pass "a mount without subvol= shows the top level (default subvolume is ID 5)" || fail "bare mount shows the top level"
umount "$BARE" 2>/dev/null

# ---- 2. filesystem-wide options and a later mount (guide 2.3) --------------------------------------
head_ "Filesystem-wide options on a second mount (guide 2.3)"
OPT2="$WORK/opt2"; mkdir -p "$OPT2"
if mount -o subvol=svc,nodiscard "$LOOP" "$OPT2" 2>"$WORK/opt2.err"; then
  info "second mount with a conflicting discard option was ACCEPTED" "now: $(findmnt -no OPTIONS "$OPT2" | tr ',' '\n' | grep -E '^(no)?discard' | tr '\n' ' ')"
  info "options of the first mount afterwards" "$(findmnt -no OPTIONS "$MNT" | tr ',' '\n' | grep -E '^(no)?discard' | tr '\n' ' ')"
  umount "$OPT2"
else info "second mount with a conflicting discard option was REFUSED" "$(tr '\n' ' ' <"$WORK/opt2.err")"; fi
umount "$SUB" 2>/dev/null

# ---- 3. unprivileged use (guide 7) ------------------------------------------------------------------
head_ "Unprivileged snapshot, delete, reflink, property (guide 7, 6.1)"
U="$MNT/u"; install -d -o "$NOBODY_UID" -g "$NOBODY_GID" -m 0750 "$U"
as_user btrfs subvolume create "$U/base" >/dev/null 2>&1 && pass "non-root creates a subvolume" || fail "non-root creates a subvolume"
as_user btrfs property set "$U/base" compression zstd >/dev/null 2>&1 && pass "non-root sets a compression property on its subvolume" || fail "non-root property set"
as_user sh -c "echo data >'$U/base/f'" 2>/dev/null
as_user btrfs subvolume snapshot "$U/base" "$U/snap" >/dev/null 2>&1 && pass "non-root snapshots its subvolume" || fail "non-root snapshot"
as_user cp --reflink=always "$U/base/f" "$U/base/g" 2>/dev/null && pass "non-root cp --reflink=always" || fail "non-root reflink"
as_user btrfs subvolume delete "$U/snap" >/dev/null 2>&1 && pass "non-root deletes a snapshot (user_subvol_rm_allowed)" || fail "non-root subvolume delete"
as_user btrfs subvolume list "$MNT" >/dev/null 2>&1 && info "non-root 'subvolume list' works" || info "non-root 'subvolume list' needs privilege"
as_user mkdir "$U/fresh" 2>/dev/null
as_user chattr +C "$U/fresh" 2>/dev/null && info "non-root chattr +C works on a fresh directory" || info "non-root chattr +C refused on a fresh directory"
as_user chattr +C "$U/base" 2>/dev/null && info "chattr +C accepted on a directory that has a compression property" || info "chattr +C REFUSED on a directory that has a compression property (NOCOW and compression exclude each other)"
as_user chattr +i "$U/fresh" 2>/dev/null && info "non-root chattr +i works" || info "non-root chattr +i refused (needs root)"

# ---- 3b. read-only snapshots, nested subvolumes, directory to subvolume conversion -------------------
head_ "Read-only snapshots, nesting, conversion (guide 4, 6.1)"
as_user btrfs subvolume snapshot -r "$U/base" "$U/rosnap" >/dev/null 2>&1 || fail "non-root read-only snapshot"
as_user btrfs subvolume delete "$U/rosnap" >/dev/null 2>&1 && info "non-root can delete a read-only snapshot directly" || info "non-root cannot delete a read-only snapshot directly (needs 'property set -ts ... ro false' first)"
as_user btrfs property set -ts "$U/rosnap" ro false >/dev/null 2>&1; as_user btrfs subvolume delete "$U/rosnap" >/dev/null 2>&1 && pass "non-root deletes a snapshot after flipping ro off" || fail "delete after ro=false"
as_user btrfs subvolume create "$U/outer" >/dev/null 2>&1; as_user btrfs subvolume create "$U/outer/inner" >/dev/null 2>&1; as_user sh -c "echo A >'$U/outer/inner/f'"
as_user btrfs subvolume snapshot "$U/outer" "$U/outer-snap" >/dev/null 2>&1
if [ -d "$U/outer-snap/inner" ] && [ ! -e "$U/outer-snap/inner/f" ]; then pass "a snapshot shows a nested subvolume as an EMPTY directory (nested content is not included)"
else fail "nested subvolume is excluded from a snapshot of its parent"; fi
as_user mkdir "$U/olddir"; as_user sh -c "head -c 2000000 /dev/urandom >'$U/olddir/f'"
as_user btrfs subvolume create "$U/newsv" >/dev/null 2>&1
as_user cp -a --reflink=always "$U/olddir/." "$U/newsv/" 2>/dev/null && pass "directory converted into a subvolume with cp -a --reflink=always (extents shared)" || fail "reflink conversion of a directory"

# ---- 4. properties across subvolumes and snapshots (guide 4) ----------------------------------------
head_ "Compression property: inheritance and snapshots (guide 4)"
mkdir "$MNT/p"; btrfs property set "$MNT/p" compression zstd; echo source-data >"$MNT/p/src"
btrfs subvolume create "$MNT/p/sv" >/dev/null
if [ "$(prop "$MNT/p/sv")" = zstd ]; then info "a new subvolume INHERITS its parent directory's compression property"
else info "a new subvolume does NOT inherit its parent directory's property" "(set it on the subvolume itself, as the guide does)"; fi
btrfs property set "$MNT/p/sv" compression zstd
btrfs subvolume snapshot "$MNT/p/sv" "$MNT/p/sv2" >/dev/null
[ "$(prop "$MNT/p/sv2")" = zstd ] && pass "a snapshot carries the compression property" || fail "a snapshot carries the property" "got '$(prop "$MNT/p/sv2")'"
if btrfs property set "$MNT/p" compression zstd:3 2>/dev/null; then info "per-directory level 'zstd:3' accepted by property set" "reads back as '$(prop "$MNT/p")'"
else info "per-directory level 'zstd:3' REJECTED by property set"; fi
btrfs property set "$MNT/p" compression zstd 2>/dev/null

mkdir "$MNT/c-zstd" "$MNT/c-none"; btrfs property set "$MNT/c-zstd" compression zstd; btrfs property set "$MNT/c-none" compression none
sync; used0=$(data_used)
head -c 48M /dev/zero | tr '\0' 'a' >"$MNT/c-none/blob"; sync; used1=$(data_used)
head -c 48M /dev/zero | tr '\0' 'a' >"$MNT/c-zstd/blob"; sync; used2=$(data_used)
if [ -n "$used0" ] && [ -n "$used1" ] && [ -n "$used2" ]; then
  d_none=$((used1 - used0)); d_zstd=$((used2 - used1))
  if [ "$d_zstd" -lt "$((d_none / 2))" ]; then pass "compressible data takes much less space under compression zstd" "(none: $d_none B, zstd: $d_zstd B)"
  else fail "compression property reduces allocated data" "none: $d_none B, zstd: $d_zstd B"; fi
else info "data usage not readable from 'btrfs filesystem df'"; fi

# ---- 5. immutable mount point (guide 3.1) -----------------------------------------------------------
head_ "chattr +i on the mount point (guide 3.1)"
IMM="$WORK/imm"; mkdir -p "$IMM"; chattr +i "$IMM"
lsattr -d "$IMM" 2>/dev/null | grep -q -- '-i-' && pass "immutable flag set on the empty directory" || fail "immutable flag set"
touch "$IMM/x" 2>/dev/null && fail "write to the unmounted immutable directory is refused" || pass "write to the unmounted immutable directory is refused (EPERM)"
if mount -o subvol=svc "$LOOP" "$IMM" 2>/dev/null; then
  pass "a filesystem can be mounted over an immutable directory"
  touch "$IMM/ok" 2>/dev/null && pass "writing works once mounted" || fail "writing works once mounted"
  umount "$IMM"
  touch "$IMM/x" 2>/dev/null && fail "write refused again after unmount" || pass "write refused again after unmount"
else fail "mount over an immutable directory"; fi
chattr -i "$IMM" 2>/dev/null

# ---- 6. device numbers, mv, links, du -x (guide 2.4) -------------------------------------------------
head_ "Cross-subvolume behaviour (guide 2.4)"
X="$MNT/x"; mkdir "$X"; btrfs subvolume create "$X/a" >/dev/null; btrfs subvolume create "$X/b" >/dev/null
head -c 4M /dev/urandom >"$X/a/f"
ln "$X/a/f" "$X/b/l" 2>/dev/null && fail "hard link across subvolumes fails (EXDEV)" || pass "hard link across subvolumes fails (EXDEV)"
cp --reflink=always "$X/a/f" "$X/b/r" 2>/dev/null && pass "cp --reflink=always works across subvolumes" || fail "reflink across subvolumes"
mv "$X/a/f" "$X/b/moved" 2>/dev/null && [ -e "$X/b/moved" ] && [ ! -e "$X/a/f" ] && pass "mv across subvolumes succeeds (as copy and delete)" || fail "mv across subvolumes"
sz=$(du -sxk "$X" | cut -f1)
[ "${sz:-99999}" -lt 1024 ] && pass "du -x stops at nested subvolumes (counted ${sz} KiB of ~8 MiB)" || fail "du -x skips nested subvolumes" "counted ${sz} KiB"
[ "$(stat -c %d "$X/a")" != "$(stat -c %d "$X/b")" ] && pass "each subvolume has its own st_dev" || fail "distinct st_dev per subvolume"

# ---- 7. reflink and snapshot through a bind of a SUBDIRECTORY (guide 7) -----------------------------
head_ "Bind mount of a user subdirectory (guide 7)"
BIND="$WORK/bind"; mkdir -p "$BIND"
mount --bind "$U" "$BIND" && pass "bind mount of <user> subdirectory" || fail "bind mount of subdirectory"
cp --reflink=always "$MNT/p/src" "$BIND/base/rf" 2>/dev/null && pass "reflink from outside into the bind (same superblock)" || fail "reflink into a bind of a subdirectory"
as_user btrfs subvolume snapshot "$BIND/base" "$BIND/snap-b" >/dev/null 2>&1 && pass "non-root snapshot through the bind" || fail "snapshot through the bind"
as_user btrfs subvolume delete "$BIND/snap-b" >/dev/null 2>&1 && pass "non-root delete through the bind" || fail "delete through the bind"
umount "$BIND"

# ---- 8. what a host-side unmount does to a bind of a subdirectory in another mount namespace --------
head_ "Host unmount vs. a container-style bind (guide 7, rslave)"
P="$WORK/prop"; C="$WORK/ctr"; mkdir -p "$P" "$C"
# no other host mount may keep the superblock alive, or the result says nothing about the bind
umount "$MNT" 2>/dev/null || info "could not unmount the main scratch mount first: the result below is not conclusive"
mount "$LOOP" "$P" && install -d "$P/ud" && echo data >"$P/ud/f"
ready="$WORK/ready"; go="$WORK/go"; done_="$WORK/done"; out="$WORK/prop.out"
rm -f "$ready" "$go" "$done_" "$out"; mkfifo "$go"
unshare -m --propagation slave bash -c '
  mount --bind "$1/ud" "$2" && mount --make-rslave "$2" || exit 3
  : >"$3"
  read -r _ <"$4"
  [ -e "$2/f" ] && echo "container still sees the original data" || echo "container lost the original data"
  [ -e "$2/new" ] && echo "container sees a file written after the host remount (same superblock)" || echo "container does NOT see a file written after the host remount"
  findmnt -no FSTYPE,SOURCE "$2" 2>&1 || echo "no mount left at the bind target"
' _ "$P" "$C" "$ready" "$go" >"$out" 2>&1 &
nspid=$!
for _ in $(seq 1 100); do [ -e "$ready" ] && break; sleep 0.1; done
if [ -e "$ready" ]; then
  if umount "$P" 2>"$WORK/umount.err"; then info "host 'umount' of the whole filesystem SUCCEEDED while the container bind was held"
  else info "host 'umount' was REFUSED while the container bind was held" "$(tr '\n' ' ' <"$WORK/umount.err")"; fi
  info "host mounts of the device right after the host umount" "$(findmnt -rno TARGET -S "$LOOP" 2>/dev/null | tr '\n' ' ')"
  if mount "$LOOP" "$P" 2>/dev/null; then echo new >"$P/ud/new"; info "host remount joined the old superblock" "$(findmnt -rno OPTIONS "$P" | tr ',' '\n' | grep -E '^(no)?discard' | tr '\n' ' ')"
  else info "host remount failed"; fi
  echo go >"$go"
  wait "$nspid"
  while IFS= read -r line; do info "inside the container namespace" "$line"; done <"$out"
else fail "could not set up the bind in a new mount namespace" "$(cat "$out" 2>/dev/null)"; kill "$nspid" 2>/dev/null; fi

# ---- summary -----------------------------------------------------------------------------------------
echo; echo "summary: $npass passed, $nfail failed (INFO lines are observations, not pass/fail)"
[ "$nfail" -eq 0 ]
