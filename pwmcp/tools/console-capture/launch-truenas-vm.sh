#!/bin/sh
# Launch ONE QEMU TCG UEFI guest for the TrueNAS console capture (no KVM here).
#
# Usage: [VM_NAME=<vm-name>] [VGA_DEV=virtio-vga] launch-truenas-vm.sh [<workdir>] <iso|none>
#
# <workdir> is optional. Omitted, it is the MAIN checkout's pwmcp/artifacts/vm-lab/<vm-name> (the installed
# disk exists once; resolved via git --git-common-dir, so a .worktrees/ checkout shares it; VM_NAME defaults
# to truenas-27.0.0-rc1-installed). Refuses to start when <workdir>/qemu.pid is alive. With ONE argument that argument is <iso|none>.
# <iso> given as a bare file name (no "/") is resolved in the downloads dir: $MDT_DOWNLOADS_DIR, else
# <main checkout>/downloads. A path (any "/") is used as given. "none" boots without an ISO.
#
# <workdir> is created, and OVMF_VARS.fd copied in when missing. disk.qcow2 is NEVER created (refused with
# instructions): qemu-img create -q -f qcow2 <workdir>/disk.qcow2 20G, or fork with cp --reflink=always.
# It creates: qmp.sock (in a short $XDG_RUNTIME_DIR//tmp dir if the path would exceed 107 bytes; the
# actual path is printed), qemu.pid, serial.log. Same shape as
# scripts/debian-install-v2/testing/vm/vmctl (q35, tcg multi-thread, user networking).
#
# Resource caps: nice -n 19, ionice -c3, -smp 2, -m 3072, timeout 14400 s. There is NO cgroup/slice cap:
# the devcontainer has no systemd, so `systemd-run --slice=dev-background.slice` (used by the
# repository runner on the host) is not available here. On a host that has it, wrap this script
# in it. Run ONE guest at a time and watch load / MemAvailable.
#
# VGA_DEV defaults to virtio-vga: with std `VGA` QMP screendump of the TrueNAS kernel console
# returned a garbage image under QEMU 10.0.13.
set -eu
USAGE="usage: launch-truenas-vm.sh [<workdir>] <iso|none>"
HERE="$(cd "$(dirname "$0")" && pwd)"
PWMCP_DIR="$(cd "$HERE/../.." && pwd)"
REPO_ROOT="$(cd "$PWMCP_DIR/.." && pwd)"
PWMCP_REL="${PWMCP_DIR#"$REPO_ROOT"/}"
VM_NAME="${VM_NAME:-truenas-27.0.0-rc1-installed}"

# "Repo root" for downloads and for the stateful VM disk is the MAIN checkout (shared by all worktrees):
# the parent of `git rev-parse --git-common-dir` when that is named .git. Without git, fall back to this
# script's own checkout and say so.
MAIN_ROOT="$REPO_ROOT"
GITCOMMON="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -n "$GITCOMMON" ] && [ "$(basename "$GITCOMMON")" = ".git" ]; then
  MAIN_ROOT="$(dirname "$GITCOMMON")"
else
  echo "launch: INFO: main checkout not resolvable via git; using script-relative root $REPO_ROOT" >&2
fi
DOWNLOADS_DIR="${MDT_DOWNLOADS_DIR:-$MAIN_ROOT/downloads}"
case $# in
  1) W="$MAIN_ROOT/$PWMCP_REL/artifacts/vm-lab/$VM_NAME"; ISO="$1" ;;
  2) W="$1"; ISO="$2" ;;
  *) echo "$USAGE" >&2; exit 2 ;;
esac
case "$ISO" in
  none|*/*) ;;
  *) ISO="$DOWNLOADS_DIR/$ISO" ;;
esac
VGA_DEV="${VGA_DEV:-virtio-vga}"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"

[ -r "$OVMF_CODE" ] || { echo "launch: OVMF firmware not found: $OVMF_CODE (apt install ovmf, or set OVMF_CODE)" >&2; exit 1; }
mkdir -p "$W"
if [ ! -f "$W/OVMF_VARS.fd" ]; then
  cp /usr/share/OVMF/OVMF_VARS_4M.fd "$W/OVMF_VARS.fd" || { echo "launch: cannot create $W/OVMF_VARS.fd from /usr/share/OVMF/OVMF_VARS_4M.fd" >&2; exit 1; }
fi
# Never create disk.qcow2 here: this VM's value is its INSTALLED disk; a blank one would silently boot an installer.
if [ ! -f "$W/disk.qcow2" ]; then
  echo "launch: no installed disk at $W/disk.qcow2." >&2
  echo "  to install from scratch: qemu-img create -q -f qcow2 '$W/disk.qcow2' 20G, then pass the ISO." >&2
  echo "  to reuse the installed VM: run it from the main checkout ($MAIN_ROOT) or pass its work directory explicitly;" >&2
  echo "  to fork it: cp --reflink=always <installed>/disk.qcow2 '$W/disk.qcow2'." >&2
  exit 1
fi
# One writer per disk: refuse when another checkout's QEMU already holds it.
if [ -f "$W/qemu.pid" ]; then
  OLDPID="$(cat "$W/qemu.pid" 2>/dev/null || true)"
  case "$OLDPID" in ''|*[!0-9]*) ;; *)
    if kill -0 "$OLDPID" 2>/dev/null; then
      echo "launch: VM $VM_NAME already running (pid $OLDPID, $W/qemu.pid)" >&2; exit 1
    fi ;;
  esac
fi
if [ "$ISO" != "none" ]; then
  [ -f "$ISO" ] || { echo "launch: ISO not a readable file: $ISO (downloads dir: $DOWNLOADS_DIR; fetch it or set MDT_DOWNLOADS_DIR; 1-arg form takes <iso|none>, not a directory)" >&2; exit 1; }
  [ -r "$ISO" ] || { echo "launch: ISO not readable: $ISO (downloads dir: $DOWNLOADS_DIR)" >&2; exit 1; }
fi
FSTYPE="$(stat -f -c %T "$W" 2>/dev/null || echo unknown)"
[ "$FSTYPE" = "btrfs" ] || echo "launch: INFO: $W is on $FSTYPE (no CoW); fine, scripts do not depend on it" >&2

# AF_UNIX sun_path is 108 bytes (incl. NUL). A long work dir (e.g. under .worktrees/) gets its QMP socket in a
# short per-work-dir runtime dir instead; pass that path to console_capture.py --qmp.
QMP_SOCK="$W/qmp.sock"
if [ "${#QMP_SOCK}" -gt 107 ]; then
  KEY="$(printf '%s' "$W" | cksum | cut -d' ' -f1)"
  QDIR="${XDG_RUNTIME_DIR:-/tmp}/pwmcp-vm-$(id -u)-$KEY"
  mkdir -p "$QDIR" && chmod 700 "$QDIR"
  QMP_SOCK="$QDIR/qmp.sock"
  [ "${#QMP_SOCK}" -le 107 ] || { echo "launch: QMP socket path too long ($QMP_SOCK); set a shorter XDG_RUNTIME_DIR" >&2; exit 1; }
fi
echo "launch: QMP socket: $QMP_SOCK" >&2

# Build the argument list in "$@" so every path stays one quoted word.
set --
if [ "$ISO" != "none" ]; then
  set -- -drive "file=$ISO,format=raw,media=cdrom,readonly=on,if=none,id=cd0" -device ide-cd,drive=cd0,bootindex=1
fi

exec nice -n 19 ionice -c3 timeout 14400 qemu-system-x86_64 \
  -name truenas-console-capture -machine q35 -accel tcg,thread=multi -cpu max \
  -smp 2 -m 3072 \
  -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
  -drive "if=pflash,format=raw,file=$W/OVMF_VARS.fd" \
  -drive "file=$W/disk.qcow2,format=qcow2,if=none,id=hd0,cache=writeback" -device virtio-blk-pci,drive=hd0,bootindex=2 \
  "$@" \
  -nic user,model=virtio-net-pci \
  -device "$VGA_DEV" -display none -monitor none \
  -qmp "unix:$QMP_SOCK,server=on,wait=off" \
  -pidfile "$W/qemu.pid" -serial "file:$W/serial.log"
