#!/usr/bin/env bash
# Description: provision a runner host's runner account, Btrfs CoW area, and local job helpers (run as root, detached).
set -Eeuo pipefail
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"

# A fresh installation has no /etc/vbpub/server-id; the controller verified the provider identity (API id, name,
# hostname, address) before starting this job, so only the hostname can be checked here.
test "$(hostname -f)" = "$RUNNER_FQDN"
test "$(hostnamectl --static)" = "$RUNNER_SHORT"
if test ! -e /etc/vbpub/server-id; then
  install -d -m 0755 /etc/vbpub
  printf '%s\n' "$RUNNER_SERVER_ID" > /etc/vbpub/server-id
  chmod 0644 /etc/vbpub/server-id
fi
identity_check

export DEBIAN_FRONTEND=noninteractive
identity_check
apt-get update
identity_check
apt-get install -y btrfs-progs parted python3.13 python3.13-venv git rsync tmux jq curl ca-certificates

# The runner uid must equal the tester-unified lane uid (1003): checkouts, the CoW root and the lane user then share
# one owner (Run-Gate RG-150, netcup NC-04). Refuse to silently keep or change a different existing uid.
identity_check
if getent passwd "$RUNNER_USER" >/dev/null; then
  if test "$(id -u "$RUNNER_USER")" != "$RUNNER_UID" && test "${RUNNER_UID_ALLOW_CHANGE:-0}" != 1; then
    echo "$RUNNER_USER exists with uid $(id -u "$RUNNER_USER"), expected $RUNNER_UID; stop its containers, then re-run with RUNNER_UID_ALLOW_CHANGE=1 (see NC-04)" >&2
    exit 3
  fi
  if test "$(id -u "$RUNNER_USER")" != "$RUNNER_UID"; then
    test -z "$(ps -u "$RUNNER_USER" -o pid= | tr -d ' ')"
    groupmod -g "$RUNNER_UID" "$RUNNER_USER"
    usermod -u "$RUNNER_UID" -g "$RUNNER_UID" "$RUNNER_USER"
    chown -R -h "$RUNNER_UID:$RUNNER_UID" "$RUNNER_HOME"
    if test -d "$COW_ROOT"; then chown -R -h "$RUNNER_UID:$RUNNER_UID" "$COW_ROOT"; fi
  fi
else
  if getent passwd "$RUNNER_UID" >/dev/null; then
    echo "UID $RUNNER_UID is occupied by an account other than $RUNNER_USER; refusing to change it." >&2
    exit 3
  fi
  useradd --create-home --uid "$RUNNER_UID" --user-group --shell /bin/bash "$RUNNER_USER"
fi
getent group docker >/dev/null || groupadd docker
usermod -aG docker "$RUNNER_USER"

identity_check
# shellcheck source=cow-carve.sh
source "$here/cow-carve.sh"
# Idempotence is keyed on the PARTITION LABEL (PARTLABEL=MDT_COW or a btrfs filesystem labelled MDT_COW), never on a
# device number: partition numbering differs between layouts, and a re-run must never carve a second cow.
cow_rc=0
cow_found="$(lsblk -P -o PATH,PARTLABEL,FSTYPE,LABEL "$COW_PARENT_DISK" | cow_select_device)" || cow_rc=$?
if test "$cow_rc" = 3; then
  echo "more than one MDT_COW candidate on $COW_PARENT_DISK; resolve by hand" >&2
  exit 6
elif test "$cow_rc" = 0; then
  COW_DEVICE=$cow_found
elif test -b "$COW_DEVICE" && test "$(lsblk -dn -o PKNAME "$COW_DEVICE")" = "$(basename "$COW_PARENT_DISK")"; then
  # legacy: an unlabelled partition at the configured device; the filesystem checks below refuse anything foreign
  :
else
  # The debian-install-v2 layout (disk_layout=dev-host) leaves the cow area + reserve UNALLOCATED between root and
  # the END-of-disk swap, so the cow partition is carved from the LARGEST free row (never the last row, which is a
  # sliver after swap), ends at the row end, and takes COW_SIZE_GIB (default: a third of the disk, 8 GiB steps,
  # capped to the row). The rest stays unallocated reserve directly behind root.
  disk_mib=$(( $(blockdev --getsize64 "$COW_PARENT_DISK") / 1048576 ))
  cow_gib=${COW_SIZE_GIB:-$(( disk_mib / 1024 / 3 / 8 * 8 ))}
  # The free row must hold the whole cow (no silent clipping) and at least COW_MIN_GIB (default 20): a tail sliver
  # or a small leftover is refused, never turned into a useless partition.
  if test "$cow_gib" -lt "${COW_MIN_GIB:-20}"; then cow_gib=${COW_MIN_GIB:-20}; fi
  cow_mib=$(( cow_gib * 1024 ))
  free_row="$(parted -m -s "$COW_PARENT_DISK" unit MiB print free | cow_pick_free_row "$cow_mib")"
  free_start_mib=${free_row%%:*}
  free_end_mib=${free_row##*:}
  # The cow partition is placed at the END of the gap (right before swap); the reserve stays directly behind root,
  # so root can grow into it (sfdisk + resize2fs) and cow, a disposable cache, is re-created larger if needed.
  cow_range="$(cow_plan_range "$free_start_mib" "$free_end_mib" "$cow_mib")"
  cow_start_mib=${cow_range%%:*}
  echo "carving $COW_LABEL: ${cow_start_mib}..${free_end_mib} MiB ($(( (free_end_mib - cow_start_mib) / 1024 )) GiB) from the free row ${free_start_mib}..${free_end_mib} MiB"
  parted -s "$COW_PARENT_DISK" mkpart "$COW_LABEL" btrfs "${cow_start_mib}MiB" "${free_end_mib}MiB"
  partprobe "$COW_PARENT_DISK"
  udevadm settle
  COW_DEVICE="$(lsblk -P -o PATH,PARTLABEL,FSTYPE,LABEL "$COW_PARENT_DISK" | cow_select_device)" \
    || { echo "the new $COW_LABEL partition is not visible after partprobe" >&2; exit 7; }
  # Stale signatures from an earlier install in the carved range (a provider reinstall need not zero the disk):
  # refuse and give the range back unless the operator allows wiping.
  stale="$(wipefs -n -p "$COW_DEVICE" | awk '!/^#/ && NF')"
  if test -n "$stale"; then
    if test "${COW_WIPE_STALE:-0}" = 1; then
      wipefs -a "$COW_DEVICE"
    else
      echo "stale signature(s) inside the new cow partition $COW_DEVICE: $stale" >&2
      parted -s "$COW_PARENT_DISK" rm "$(cat "/sys/class/block/$(basename "$COW_DEVICE")/partition")"
      echo "partition removed again; re-run with COW_WIPE_STALE=1 after checking the old data is disposable" >&2
      exit 4
    fi
  fi
fi
test -b "$COW_DEVICE"
fs_type="$(blkid -s TYPE -o value "$COW_DEVICE" 2>/dev/null || true)"
if test -z "$fs_type"; then
  mkfs.btrfs -L MDT_COW "$COW_DEVICE"
elif test "$fs_type" = btrfs && test "$(blkid -s LABEL -o value "$COW_DEVICE")" = MDT_COW; then
  :
else
  echo "refusing to overwrite $COW_DEVICE with existing filesystem type '${fs_type:-unknown}'" >&2
  exit 4
fi

identity_check
install -d -m 0755 "$COW_MOUNT"
uuid="$(blkid -s UUID -o value "$COW_DEVICE")"
expected="UUID=$uuid $COW_MOUNT btrfs noatime,user_subvol_rm_allowed 0 0"
existing="$(awk -v mp="$COW_MOUNT" '$2 == mp && $1 !~ /^#/ {print}' /etc/fstab)"
if test -n "$existing"; then
  test "$existing" = "$expected"
else
  printf '%s\n' "$expected" >> /etc/fstab
fi
if ! findmnt -rn --mountpoint "$COW_MOUNT" >/dev/null; then
  mount "$COW_MOUNT"
fi
test "$(findmnt -n -o FSTYPE --target "$COW_MOUNT")" = btrfs
mount_options=",$(findmnt -n -o OPTIONS --target "$COW_MOUNT"),"
case "$mount_options" in *,noatime,*) ;; *) echo "noatime missing from $COW_MOUNT mount" >&2; exit 5 ;; esac
case "$mount_options" in *,user_subvol_rm_allowed,*) ;; *) echo "user_subvol_rm_allowed missing from $COW_MOUNT mount" >&2; exit 5 ;; esac

identity_check
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$COW_ROOT"
profile=/etc/profile.d/mdt-cow-root.sh
profile_line="export MDT_COW_ROOT=$COW_ROOT"
if test -e "$profile"; then
  grep -Fxq "$profile_line" "$profile"
else
  printf '%s\n' "$profile_line" > "$profile"
  chmod 0644 "$profile"
fi

identity_check
test -s /root/.ssh/authorized_keys
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$RUNNER_HOME/.ssh"
install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0600 /root/.ssh/authorized_keys "$RUNNER_HOME/.ssh/authorized_keys"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$RUNNER_HOME/bin" "$RUNNER_HOME/incoming" "$RUNNER_HOME/checkouts" \
  "$RUNNER_HOME/src" "$RUNNER_HOME/jobs" "$RUNNER_HOME/venvs" "$RUNNER_HOME/.local/share/uv"

# install -d creates missing parents as root; hand ~/.local back to the runner.
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.local"
# The tester uid traverses /checkouts; its entries are made world-readable by sync-from-devcontainer.
chmod 0711 "$RUNNER_HOME" "$RUNNER_HOME/checkouts"

install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$here/run-job" "$RUNNER_HOME/bin/run-job"
install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$here/sync-from-devcontainer" "$RUNNER_HOME/bin/sync-from-devcontainer"

identity_check
# uv searches the cwd's parents for uv.toml; /root is unreadable to the runner.
cd "$RUNNER_HOME"
if test ! -x "$RUNNER_HOME/venvs/tools/bin/python"; then
  runuser -u "$RUNNER_USER" -- /usr/bin/python3.13 -m venv "$RUNNER_HOME/venvs/tools"
fi
runuser -u "$RUNNER_USER" -- "$RUNNER_HOME/venvs/tools/bin/python" -m pip install --disable-pip-version-check --upgrade uv
runuser -u "$RUNNER_USER" -- env UV_PYTHON_INSTALL_DIR="$RUNNER_HOME/.local/share/uv/python" "$RUNNER_HOME/venvs/tools/bin/uv" python install 3.14

identity_check
printf 'PROVISIONING_OK_UTC=%s\n' "$(date -u +%FT%TZ)"
printf 'COW_DEVICE=%s COW_UUID=%s COW_BYTES=%s\n' "$COW_DEVICE" "$uuid" "$(blockdev --getsize64 "$COW_DEVICE")"
printf 'RUNNER_UID=%s DOCKER_GROUP=%s PYTHON_TOOL=%s\n' "$(id -u "$RUNNER_USER")" "$(id -nG "$RUNNER_USER" | tr ' ' ',')" "$("$RUNNER_HOME/venvs/tools/bin/uv" --version)"
