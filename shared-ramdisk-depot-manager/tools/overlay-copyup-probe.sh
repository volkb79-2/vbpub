#!/bin/bash
# overlayfs copy-up probe, v2.
#
# v1 reported st_size and concluded "data duplicated" — wrong measurement.
# A metacopy'd upper file MUST report the lower file's size (the merged view
# has to be correct), while allocating no blocks at all. Apparent size cannot
# tell the two apart; ALLOCATED BLOCKS can. That is what this measures.

set -u
hr() { printf '\n=== %s ===\n' "$*"; }

# blocks: st_blocks in 512B units. 0 => nothing allocated => data still in lower.
usage() {
  local f="$1"
  if [ ! -e "$f" ]; then echo "absent"; return; fi
  printf 'apparent=%s bytes, allocated=%s KiB' \
    "$(stat -c '%s' "$f")" "$(( $(stat -c '%b' "$f") / 2 ))"
}

xattrs() {
  getfattr -d -m '.*' --absolute-names "$1" 2>/dev/null | grep -v '^#' \
    | sed 's/=.*//' | tr '\n' ' '
}

probe() {
  local label="$1" extra="$2" base="$3"
  hr "$label"
  umount "$base/merged" 2>/dev/null
  rm -rf "$base"; mkdir -p "$base"/{lower,upper,work,merged}

  dd if=/dev/urandom of="$base/lower/big.bin" bs=1M count=8 status=none
  chown 1000:1000 "$base/lower/big.bin"; chmod 644 "$base/lower/big.bin"

  local opts="lowerdir=$base/lower,upperdir=$base/upper,workdir=$base/work$extra"
  if ! mount -t overlay overlay -o "$opts" "$base/merged" 2>&1; then
    echo "  MOUNT FAILED"; return
  fi
  echo "  upper backing fs: $(stat -f -c %T "$base/upper")   opts:$extra"
  echo "  lower file      : $(usage "$base/lower/big.bin")"

  run() {
    local what="$1"; shift
    umount "$base/merged" 2>/dev/null
    rm -rf "$base"/{upper,work}; mkdir -p "$base"/{upper,work}
    mount -t overlay overlay -o "$opts" "$base/merged"
    "$@" >/dev/null 2>&1
    local verdict
    if [ ! -e "$base/upper/big.bin" ]; then
      verdict="NO COPY-UP"
    elif [ "$(stat -c '%b' "$base/upper/big.bin")" -eq 0 ]; then
      verdict="metadata-only (0 blocks allocated — data still shared from lower)"
    else
      verdict="FULL DATA COPY"
    fi
    printf '  %-22s -> %-58s [%s] %s\n' "$what" "$verdict" \
      "$(usage "$base/upper/big.bin")" "$(xattrs "$base/upper/big.bin")"
  }

  run "no-op chown"  chown 1000:1000 "$base/merged/big.bin"
  run "no-op chmod"  chmod 644       "$base/merged/big.bin"
  run "touch (mtime)" touch          "$base/merged/big.bin"
  run "real chown"   chown 1001:1001 "$base/merged/big.bin"
  run "real chmod"   chmod 600       "$base/merged/big.bin"
  run "read only"    cat             "$base/merged/big.bin"
  run "actual write" bash -c "echo x >> $base/merged/big.bin"

  umount "$base/merged" 2>/dev/null
}

hr "environment"
uname -r
echo "union fs offered by the kernel:"; grep -E 'overlay|aufs' /proc/filesystems
echo "aufs in modules.dep: $(grep -c aufs /lib/modules/$(uname -r)/modules.dep 2>/dev/null || echo 'no modules.dep visible')"
echo "overlay defaults:"
for p in /sys/module/overlay/parameters/*; do
  printf '   %-22s = %s\n' "$(basename "$p")" "$(cat "$p" 2>/dev/null)"
done

# Upper on a real disk-backed filesystem (a host bind mount, rw).
probe "DISK upper, default opts"   ""             /disk/probe-default
probe "DISK upper, metacopy=on"    ",metacopy=on" /disk/probe-metacopy

mkdir -p /probe-tmpfs && mount -t tmpfs tmpfs /probe-tmpfs
probe "TMPFS upper, default opts"  ""             /probe-tmpfs/default
probe "TMPFS upper, metacopy=on"   ",metacopy=on" /probe-tmpfs/metacopy
umount /probe-tmpfs 2>/dev/null

hr "SIMULATED WINGS CHOWN WALK over a whole tree (default opts, disk upper)"
rm -rf /disk/walk; mkdir -p /disk/walk/{lower,upper,work,merged}
for i in $(seq 1 40); do
  dd if=/dev/urandom of=/disk/walk/lower/f$i.bin bs=256k count=1 status=none
done
chown -R 1000:1000 /disk/walk/lower
mount -t overlay overlay -o lowerdir=/disk/walk/lower,upperdir=/disk/walk/upper,workdir=/disk/walk/work /disk/walk/merged
echo "  lower tree: $(du -sh /disk/walk/lower | cut -f1), all already owned 1000:1000"
echo "  upper before walk: $(du -sh /disk/walk/upper | cut -f1)"
# exactly what vanilla Wings does: chown every entry, unconditionally
find /disk/walk/merged -exec chown 1000:1000 {} \; 2>/dev/null
echo "  upper AFTER a no-op-in-intent chown walk: $(du -sh /disk/walk/upper | cut -f1)"
echo "  files duplicated into upper: $(find /disk/walk/upper -type f | wc -l) of 40"
umount /disk/walk/merged 2>/dev/null

hr "done"
