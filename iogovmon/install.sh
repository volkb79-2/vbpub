#!/usr/bin/env bash
# iogovmon installer helper: performs README "Install steps" for the REPORT-ONLY tick, idempotently, printing every action.
#
#   iogovmon/install.sh --dry-run              # print every action, change nothing (run this first)
#   sudo iogovmon/install.sh                   # do it
#   sudo iogovmon/install.sh --enable-tick     # also `systemctl enable --now iogovmond-tick.timer` (README step 5; only after a manual plan + tick)
#
# What it does: preflight (`iogovmonctl preflight`, stops on any missing package) -> service user -> /etc/iogovmon (root 0755), state and
# scratch dirs (service user 0700) -> a root-owned copy of the package in LIB_DIR -> `iogovmond` and `iogovmonctl` launchers -> config skeleton (never overwritten) -> unit/udev
# rendering, `systemd-analyze verify` -> units and the read-only ACL udev rule -> daemon-reload.
# What it never does: enable or start a bench timer or the watch service, enable the tick timer without --enable-tick, write a cgroup/io/block
# setting, run docker or touch any container, overwrite an existing config, or use the `disk` group.
# Exit: 0 installed, 1 error (nothing half-done that a rerun cannot finish), 3 installed up to the config: edit it, rerun.
set -euo pipefail
export LC_ALL=C   # ASCII-only regex classes below, whatever the caller's locale

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # the project directory: src/iogovmon/, units/, iogovmon.toml.example
SRC_PARENT=$HERE/src                                 # the directory that contains the importable `iogovmon/` package
USER_NAME=iogovmon
LIB_DIR=/usr/local/lib/iogovmon-pkg   # a root-owned directory; /usr/local/lib is root:staff 2775 on Debian: use --lib-dir /opt/iogovmon for a stricter parent
BIN_DIR=/usr/local/bin
ETC_DIR=/etc/iogovmon
CONFIG=$ETC_DIR/iogovmon.toml
KILL_SWITCH=$ETC_DIR/disabled
STATE_DIR=/var/lib/iogovmon
SCRATCH_DIR=$STATE_DIR/scratch
UNIT_DIR=/etc/systemd/system
RULES_DIR=/etc/udev/rules.d
DRY=0 STAGE="" ENABLE_TICK=0 SKIP_PRE=0 DISK_NODE="" ID_PATH="" PYBIN="" IO_WEIGHT=50 TICK_INTERVAL=5min

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { printf 'iogovmon/install.sh: %s\n' "$*" >&2; exit 1; }
while (($#)); do
  case $1 in
    --dry-run) DRY=1 ;;
    --enable-tick) ENABLE_TICK=1 ;;
    --skip-preflight) SKIP_PRE=1 ;;
    --destdir) STAGE=${2:?--destdir needs a directory}; shift ;;   # staging: lay the files out under DIR, run no useradd/chown/systemctl/udevadm
    --user) USER_NAME=${2:?}; shift ;;
    --lib-dir) LIB_DIR=${2:?}; shift ;;
    --python) PYBIN=${2:?}; shift ;;
    --disk-node) DISK_NODE=${2:?}; shift ;;
    --id-path) ID_PATH=${2:?}; shift ;;
    --io-weight) IO_WEIGHT=${2:?}; shift ;;
    --tick-interval) TICK_INTERVAL=${2:?}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done
[[ -z $STAGE ]] || STAGE=${STAGE%/}
# Every value that reaches a path, a sed expression or a unit file is checked here, once; nothing below escapes anything.
abs_dir() { # name value: absolute, no empty/. /.. components, a conservative character set
  [[ $2 =~ ^/[A-Za-z0-9._+/-]*$ ]] || die "$1 must be an absolute path of [A-Za-z0-9._+/-]: $2"
  local q=${2%/}/
  [[ $q != *//* && $q != */../* && $q != */./* ]] || die "$1 must not contain '..', '.' or empty components: $2"
}
[[ -z $STAGE ]] || abs_dir --destdir "$STAGE"
abs_dir --lib-dir "$LIB_DIR"; LIB_DIR=${LIB_DIR%/}
[[ -n $LIB_DIR ]] || die "--lib-dir must not be / (it would replace the root directory's iogovmon package slot)"
[[ $IO_WEIGHT =~ ^[0-9]{1,5}$ ]] || die "invalid --io-weight: $IO_WEIGHT"
[[ $TICK_INTERVAL =~ ^[0-9]+[a-z]*$ ]] || die "invalid --tick-interval: $TICK_INTERVAL"
[[ $USER_NAME =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid user name: $USER_NAME"
if ((!DRY)) && [[ -z $STAGE ]] && [[ $EUID -ne 0 ]]; then die "run as root (or use --dry-run, or --destdir for a staging tree)"; fi
[[ -n $PYBIN ]] || PYBIN=$(readlink -f "$(command -v python3)") || die "python3 not found (apt-get install python3)"
abs_dir --python "$PYBIN"
[[ $DISK_NODE != *..* ]] || die "--disk-node must not contain '..': $DISK_NODE"
[[ -z $DISK_NODE || $DISK_NODE =~ ^/dev/[A-Za-z0-9/:._-]+$ ]] || die "invalid --disk-node (want /dev/ plus [A-Za-z0-9/:._-]): $DISK_NODE"
[[ -z $ID_PATH || $ID_PATH =~ ^[A-Za-z0-9:._-]+$ ]] || die "invalid --id-path (want [A-Za-z0-9:._-]): $ID_PATH"
SYSFS=/sys
[[ -z $STAGE ]] || SYSFS=${IOGOVMON_SYSFS_ROOT:-/sys}   # test hook, honoured ONLY with --destdir: a real install always reads /sys

say() { printf '%s\n' "$*"; }
run() { if ((DRY)); then say "WOULD: $*"; else say "DO:    $*"; "$@"; fi; }
t() { printf '%s%s' "$STAGE" "$1"; }                       # a target path, under the staging root if any
chown_to() { # owner:group path...
  local o=$1; shift
  if [[ -n $STAGE ]]; then say "SKIP (staging): chown $o $*"; else run chown "$o" "$@"; fi
}
ensure_dir() { # mode owner:group path
  local mode=$1 own=$2 d; d=$(t "$3")
  if [[ -d $d && $(stat -c %a "$d") == "$mode" && ( -n $STAGE || $(stat -c %U:%G "$d") == "$own" ) ]]; then say "unchanged: $d"; return; fi
  run mkdir -p "$d"; run chmod "$mode" "$d"; chown_to "$own" "$d"
}
put_file() { # source mode dest-path   (root-owned, replaced only if the content differs)
  local src=$1 mode=$2 d; d=$(t "$3")
  if [[ -f $d ]] && cmp -s "$src" "$d" && [[ $(stat -c %a "$d") == "$mode" ]]; then say "unchanged: $d"; return; fi
  run install -m "$mode" "$src" "$d"; chown_to root:root "$d"
}
control() { # source-root argv...; use the package's operator entry point during installation
  local source_root=$1; shift
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$source_root "$PYBIN" -B -c \
    'from iogovmon.cli import control_main; raise SystemExit(control_main())' "$@"
}

TMP=$(mktemp -d)
NEW="" OLD="" DEST=""
cleanup() {
  rm -rf "$TMP"
  [[ -n $NEW ]] && rm -rf "$NEW"
  if [[ -n $OLD && -e $OLD && ! -e $DEST/iogovmon ]]; then mv "$OLD" "$DEST/iogovmon"; fi   # a swap that died midway: put the old package back
  return 0
}
trap cleanup EXIT
((DRY)) && say "DRY RUN: nothing below is executed; WOULD lines are the actions a real run performs."

say "== 1. preflight"
if ((SKIP_PRE)); then say "skipped (--skip-preflight)"
elif ! control "$SRC_PARENT" preflight; then
  die "preflight failed: install the packages it lists, then rerun"
fi

say "== 2. service user and directories"
if id -u "$USER_NAME" >/dev/null 2>&1; then say "unchanged: user $USER_NAME exists"
elif [[ -n $STAGE ]]; then say "SKIP (staging): useradd $USER_NAME"
else run useradd --system --no-create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$USER_NAME"; fi
ensure_dir 755 root:root "$ETC_DIR"
ensure_dir 700 "$USER_NAME:$USER_NAME" "$STATE_DIR"
ensure_dir 700 "$USER_NAME:$USER_NAME" "$SCRATCH_DIR"

say "== 3. root-owned copy of the package in $LIB_DIR"
mkdir -p "$TMP/pkg/iogovmon"
cp "$SRC_PARENT"/iogovmon/*.py "$HERE/iogovmon.toml.example" "$HERE/README.md" "$TMP/pkg/iogovmon/"
chmod 644 "$TMP/pkg/iogovmon"/* ; chmod 755 "$TMP/pkg" "$TMP/pkg/iogovmon"
DEST=$(t "$LIB_DIR")
# recover from an interrupted earlier swap: a stale iogovmon.old.* is the previous package (restored if iogovmon is missing, else removed)
shopt -s nullglob
for stale in "$DEST"/iogovmon.new.*; do run rm -rf "$stale"; done
for stale in "$DEST"/iogovmon.old.*; do
  if [[ ! -e $DEST/iogovmon ]]; then run mv "$stale" "$DEST/iogovmon"; else run rm -rf "$stale"; fi
done
shopt -u nullglob
if [[ -d $DEST/iogovmon ]] && diff -r -q "$TMP/pkg/iogovmon" "$DEST/iogovmon" >/dev/null 2>&1 && [[ $(stat -c %a "$DEST/iogovmon") == 755 ]]; then
  say "unchanged: $DEST/iogovmon"
else
  ensure_dir 755 root:root "$LIB_DIR"
  # copy to a sibling first, then swap: a failed copy leaves the installed package untouched (cleanup removes the sibling)
  NEW=$DEST/iogovmon.new.$$
  run cp -r "$TMP/pkg/iogovmon" "$NEW"
  run chmod 755 "$NEW"
  chown_to root:root "$NEW"; [[ -n $STAGE ]] || run chown -R root:root "$NEW"
  if [[ -e $DEST/iogovmon ]]; then OLD=$DEST/iogovmon.old.$$; run mv "$DEST/iogovmon" "$OLD"; fi
  run mv "$NEW" "$DEST/iogovmon"
  if ((DRY)); then [[ -z $OLD ]] || say "WOULD: rm -rf $OLD"; else NEW=""; [[ -z $OLD ]] || run rm -rf "$OLD"; fi
  OLD=""
fi

say "== 3a. daemon and operator commands"
ensure_dir 755 root:root "$BIN_DIR"
cat >"$TMP/iogovmond" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB_DIR"
exec "$PYBIN" -B -c 'from iogovmon.cli import daemon_main; raise SystemExit(daemon_main())' "\$@"
EOF
cat >"$TMP/iogovmonctl" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB_DIR"
exec "$PYBIN" -B -c 'from iogovmon.cli import control_main; raise SystemExit(control_main())' "\$@"
EOF
put_file "$TMP/iogovmond" 755 "$BIN_DIR/iogovmond"
put_file "$TMP/iogovmonctl" 755 "$BIN_DIR/iogovmonctl"

say "== 4. configuration skeleton (an existing config is never touched)"
CFG=$(t "$CONFIG")
if [[ -e $CFG ]]; then say "unchanged: $CFG exists"
else put_file "$HERE/iogovmon.toml.example" 644 "$CONFIG"; fi
if [[ ! -e $CFG || ! -s $CFG ]] || grep -q '^data_paths = \["/var/lib/example-volumes"\]' "$CFG" 2>/dev/null; then
  [[ -n $DISK_NODE ]] || { say "INCOMPLETE: $CONFIG still holds the EXAMPLE data_paths. Edit it (data_paths or devices, protected cgroups), check with"
    say "  iogovmonctl --config $CONFIG devices"; say "then rerun this script (or pass --disk-node and --id-path)."; exit 3; }
fi

say "== 5. the one disk: stable node and udev ID_PATH"
if [[ -z $DISK_NODE ]]; then
  [[ -n $STAGE ]] && CONF_ARG=("--config" "$CFG") || CONF_ARG=("--config" "$CONFIG")
  mapfile -t DISKS < <(control "$([[ -d $DEST/iogovmon ]] && echo "$DEST" || echo "$SRC_PARENT")" "${CONF_ARG[@]}" devices 2>"$TMP/devices.err" | cut -f2)
  if ((${#DISKS[@]} != 1)); then
    sed 's/^/  /' "$TMP/devices.err" >&2 || true
    die "the config resolves ${#DISKS[@]} disk(s); the ACL rule grants exactly one. Fix data_paths/devices or pass --disk-node NODE"
  fi
  DISK_NODE=${DISKS[0]}
fi
[[ $DISK_NODE =~ ^/dev/[A-Za-z0-9/:._-]+$ ]] || die "disk node must be a /dev path of [A-Za-z0-9/:._-]: $DISK_NODE"
KNAME_NODE=$(basename "$(readlink -f "$DISK_NODE")")
[[ $KNAME_NODE != dm-* && $KNAME_NODE != md* ]] || die "disk node $DISK_NODE is a mapped/raid device ($KNAME_NODE), not a whole disk; pass the underlying physical disk"
[[ ! -e $SYSFS/class/block/$KNAME_NODE/partition ]] || die "disk node $DISK_NODE is a partition ($KNAME_NODE); the ACL rule needs the WHOLE disk"
UDEV_ID_PATH=""
if [[ -z $STAGE ]]; then
  UDEV_ID_PATH=$(udevadm info -q property -n "$DISK_NODE" 2>/dev/null | sed -n 's/^ID_PATH=//p' | head -n1) || true
fi
if [[ -z $ID_PATH ]]; then
  ID_PATH=$UDEV_ID_PATH
  [[ -n $ID_PATH ]] || die "no udev ID_PATH for $DISK_NODE (udevadm info -q property -n $DISK_NODE); pass --id-path"
elif [[ -n $UDEV_ID_PATH && $UDEV_ID_PATH != "$ID_PATH" ]]; then
  die "--id-path $ID_PATH does not match udev's ID_PATH $UDEV_ID_PATH for $DISK_NODE"
elif [[ -z $STAGE && -z $UDEV_ID_PATH ]]; then
  if ((DRY)); then say "WARNING: --id-path $ID_PATH is UNVERIFIED: udev reported no ID_PATH for $DISK_NODE (a real install refuses this)"
  else die "udev cannot confirm --id-path $ID_PATH for $DISK_NODE (udevadm missing or no ID_PATH property); refusing"; fi
fi
[[ $ID_PATH =~ ^[A-Za-z0-9:._-]+$ ]] || die "udev ID_PATH has characters outside [A-Za-z0-9:._-]: $ID_PATH"
say "disk node $DISK_NODE   udev ID_PATH $ID_PATH   python $PYBIN"

say "== 6. render units and the udev rule"
mkdir -p "$TMP/out"
for f in "$HERE"/units/*.in; do
  name=$(basename "$f" .in)
  sed -e "s|@IOGOVMON_USER@|$USER_NAME|g" -e "s|@IOGOVMON_PYTHONPATH@|$LIB_DIR|g" \
      -e "s|@IOGOVMON_CONFIG@|$CONFIG|g" -e "s|@IOGOVMON_STATE_DIR@|$STATE_DIR|g" -e "s|@IOGOVMON_SCRATCH_DIR@|$SCRATCH_DIR|g" \
      -e "s|@IOGOVMON_KILL_SWITCH_FILE@|$KILL_SWITCH|g" -e "s|@IOGOVMON_DISK_NODE@|$DISK_NODE|g" -e "s|@IOGOVMON_DISK_ID_PATH@|$ID_PATH|g" \
      -e "s|@IOGOVMON_IO_WEIGHT@|$IO_WEIGHT|g" -e "s|@IOGOVMON_TICK_INTERVAL@|$TICK_INTERVAL|g" \
      -e "1s|^# TEMPLATE, NOT INSTALLED\.|# Rendered by iogovmon/install.sh from units/$name.in; change the template, not this file.|" "$f" >"$TMP/out/$name"
  if grep -n '@IOGOVMON_[A-Z_]*@' "$TMP/out/$name" | grep -v '^[0-9]*:#' >&2; then die "unrendered token left in $name"; fi
done
if ((DRY)) || [[ -n $STAGE ]]; then say "SKIP verify: systemd-analyze verify runs in a real install, after the service user exists"
else
  (cd "$TMP/out" && systemd-analyze verify --man=no ./*.service ./*.timer ./*.slice) || die "systemd-analyze verify rejected the rendered units"
  say "systemd-analyze verify: ok"
fi

say "== 7. install units (none of them enabled here) and the read-only ACL rule"
ensure_dir 755 root:root "$UNIT_DIR"
ensure_dir 755 root:root "$RULES_DIR"
for u in iogovmon.slice iogovmond-tick.service iogovmond-tick.timer iogovmond.service iogovmond-bench.service iogovmond-bench.timer; do
  put_file "$TMP/out/$u" 644 "$UNIT_DIR/$u"
done
put_file "$TMP/out/99-iogovmon-raw-read.rules" 644 "$RULES_DIR/99-iogovmon-raw-read.rules"
if [[ -n $STAGE ]]; then say "SKIP (staging): systemctl daemon-reload, udevadm trigger"
else
  run systemctl daemon-reload
  kname=$(basename "$(readlink -f "$DISK_NODE")")
  run udevadm trigger --action=change --subsystem-match=block --sysname-match="$kname"
fi

say "== 8. tick timer"
if ((ENABLE_TICK)); then
  if [[ -n $STAGE ]]; then say "SKIP (staging): systemctl enable --now iogovmond-tick.timer"; else run systemctl enable --now iogovmond-tick.timer; fi
else
  say "not enabled (iogovmond-tick.timer stays off). The bench timer and iogovmond.service are never enabled by this script."
fi
cat <<NEXT

Next, by hand (README steps 3-5): verify the ACL as $USER_NAME:  sudo -u $USER_NAME dd if=$DISK_NODE bs=4k count=1 iflag=direct of=/dev/null ; sudo -u $USER_NAME test -w $DISK_NODE && echo WRITABLE-STOP
then  sudo -u $USER_NAME env IOGOVMON_CONFIG=$CONFIG iogovmonctl plan   and   systemctl start iogovmond-tick.service
and only then  $0 --enable-tick.
NEXT
