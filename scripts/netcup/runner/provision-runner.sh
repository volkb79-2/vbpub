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
if test -b "$COW_DEVICE"; then
  test "$(lsblk -dn -o PKNAME "$COW_DEVICE")" = "$(basename "$COW_PARENT_DISK")"
else
  free_row="$(parted -m -s "$COW_PARENT_DISK" unit MiB print free | awk -F: 'tolower($5) ~ /^free/ {start=$2; end=$3} END {if (start != "") print start ":" end}')"
  test -n "$free_row"
  free_start=${free_row%%:*}
  parted -s "$COW_PARENT_DISK" mkpart primary "$free_start" 100%
  partprobe "$COW_PARENT_DISK"
  udevadm settle
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
