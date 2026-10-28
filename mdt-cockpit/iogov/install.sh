#!/usr/bin/env bash
# iogov installer helper: performs README "Install steps" 1-4 for the REPORT-ONLY tick, idempotently, printing every action.
#
#   iogov/install.sh --dry-run                 # print every action, change nothing (run this first)
#   sudo iogov/install.sh                      # do it
#   sudo iogov/install.sh --enable-tick        # also `systemctl enable --now iogov-tick.timer` (README step 5; only after a manual plan + tick)
#
# What it does: preflight (`python3 -m iogov preflight`, stops on any missing package) -> service user -> /etc/iogov (root 0755), state and
# scratch dirs (service user 0700) -> a root-owned copy of the package in LIB_DIR -> config skeleton (never overwritten) -> unit/udev
# rendering, `systemd-analyze verify` -> units and the read-only ACL udev rule -> daemon-reload.
# What it never does: enable or start a bench timer or the watch service, enable the tick timer without --enable-tick, write a cgroup/io/block
# setting, run docker or touch any container, overwrite an existing config, or use the `disk` group.
# Exit: 0 installed, 1 error (nothing half-done that a rerun cannot finish), 3 installed up to the config: edit it, rerun.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC_PARENT=$(dirname "$HERE")
USER_NAME=iogov
LIB_DIR=/usr/local/lib/iogov-pkg   # a root-owned directory; /usr/local/lib is root:staff 2775 on Debian: use --lib-dir /opt/iogov for a stricter parent
ETC_DIR=/etc/iogov
CONFIG=$ETC_DIR/iogov.toml
KILL_SWITCH=$ETC_DIR/disabled
STATE_DIR=/var/lib/iogov
SCRATCH_DIR=$STATE_DIR/scratch
UNIT_DIR=/etc/systemd/system
RULES_DIR=/etc/udev/rules.d
DRY=0 STAGE="" ENABLE_TICK=0 SKIP_PRE=0 DISK_NODE="" ID_PATH="" PYBIN="" IO_WEIGHT=50 TICK_INTERVAL=5min

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { printf 'iogov install: %s\n' "$*" >&2; exit 1; }
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
[[ -n $STAGE ]] && STAGE=${STAGE%/}
[[ $USER_NAME =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid user name: $USER_NAME"
if ((!DRY)) && [[ -z $STAGE ]] && [[ $EUID -ne 0 ]]; then die "run as root (or use --dry-run, or --destdir for a staging tree)"; fi
[[ -n $PYBIN ]] || PYBIN=$(readlink -f "$(command -v python3)") || die "python3 not found (apt-get install python3)"

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

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
((DRY)) && say "DRY RUN: nothing below is executed; WOULD lines are the actions a real run performs."

say "== 1. preflight"
if ((SKIP_PRE)); then say "skipped (--skip-preflight)"
elif ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$SRC_PARENT "$PYBIN" -B -m iogov preflight; then
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
mkdir -p "$TMP/pkg/iogov"
cp "$HERE"/*.py "$HERE/iogov.toml.example" "$HERE/README.md" "$TMP/pkg/iogov/"
chmod 644 "$TMP/pkg/iogov"/* ; chmod 755 "$TMP/pkg" "$TMP/pkg/iogov"
DEST=$(t "$LIB_DIR")
if [[ -d $DEST/iogov ]] && diff -r -q "$TMP/pkg/iogov" "$DEST/iogov" >/dev/null 2>&1 && [[ $(stat -c %a "$DEST/iogov") == 755 ]]; then
  say "unchanged: $DEST/iogov"
else
  ensure_dir 755 root:root "$LIB_DIR"
  if [[ -e $DEST/iogov ]]; then run mv "$DEST/iogov" "$DEST/iogov.old.$$"; fi
  run cp -r "$TMP/pkg/iogov" "$DEST/iogov"
  chown_to root:root "$DEST/iogov"; [[ -z $STAGE ]] && run chown -R root:root "$DEST/iogov"
  if ((!DRY)) && [[ -e $DEST/iogov.old.$$ ]]; then run rm -rf "$DEST/iogov.old.$$"; elif ((DRY)) && [[ -e $DEST/iogov ]]; then say "WOULD: rm -rf $DEST/iogov.old.$$"; fi
fi

say "== 4. configuration skeleton (an existing config is never touched)"
CFG=$(t "$CONFIG")
if [[ -e $CFG ]]; then say "unchanged: $CFG exists"
else put_file "$HERE/iogov.toml.example" 644 "$CONFIG"; fi
if [[ ! -e $CFG || ! -s $CFG ]] || grep -q '^data_paths = \["/var/lib/example-volumes"\]' "$CFG" 2>/dev/null; then
  [[ -n $DISK_NODE ]] || { say "INCOMPLETE: $CONFIG still holds the EXAMPLE data_paths. Edit it (data_paths or devices, protected cgroups), check with"
    say "  PYTHONPATH=$LIB_DIR python3 -B -m iogov --config $CONFIG devices"; say "then rerun this script (or pass --disk-node and --id-path)."; exit 3; }
fi

say "== 5. the one disk: stable node and udev ID_PATH"
if [[ -z $DISK_NODE ]]; then
  [[ -n $STAGE ]] && CONF_ARG=("--config" "$CFG") || CONF_ARG=("--config" "$CONFIG")
  mapfile -t DISKS < <(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$([[ -d $DEST/iogov ]] && echo "$DEST" || echo "$SRC_PARENT") "$PYBIN" -B -m iogov "${CONF_ARG[@]}" devices 2>"$TMP/devices.err" | cut -f2)
  if ((${#DISKS[@]} != 1)); then
    sed 's/^/  /' "$TMP/devices.err" >&2 || true
    die "the config resolves ${#DISKS[@]} disk(s); the ACL rule grants exactly one. Fix data_paths/devices or pass --disk-node NODE"
  fi
  DISK_NODE=${DISKS[0]}
fi
[[ $DISK_NODE == /dev/* ]] || die "disk node must be a /dev path: $DISK_NODE"
if [[ -z $ID_PATH ]]; then
  ID_PATH=$(udevadm info -q property -n "$DISK_NODE" 2>/dev/null | sed -n 's/^ID_PATH=//p' | head -n1) || true
  [[ -n $ID_PATH ]] || die "no udev ID_PATH for $DISK_NODE (udevadm info -q property -n $DISK_NODE); pass --id-path"
fi
say "disk node $DISK_NODE   udev ID_PATH $ID_PATH   python $PYBIN"

say "== 6. render units and the udev rule"
mkdir -p "$TMP/out"
for f in "$HERE"/units/*.in; do
  name=$(basename "$f" .in)
  sed -e "s|@IOGOV_USER@|$USER_NAME|g" -e "s|@IOGOV_PYTHON@|$PYBIN|g" -e "s|@IOGOV_PYTHONPATH@|$LIB_DIR|g" \
      -e "s|@IOGOV_CONFIG@|$CONFIG|g" -e "s|@IOGOV_STATE_DIR@|$STATE_DIR|g" -e "s|@IOGOV_SCRATCH_DIR@|$SCRATCH_DIR|g" \
      -e "s|@IOGOV_KILL_SWITCH_FILE@|$KILL_SWITCH|g" -e "s|@IOGOV_DISK_NODE@|$DISK_NODE|g" -e "s|@IOGOV_DISK_ID_PATH@|$ID_PATH|g" \
      -e "s|@IOGOV_IO_WEIGHT@|$IO_WEIGHT|g" -e "s|@IOGOV_TICK_INTERVAL@|$TICK_INTERVAL|g" \
      -e "1s|^# TEMPLATE, NOT INSTALLED\.|# Rendered by iogov/install.sh from units/$name.in; change the template, not this file.|" "$f" >"$TMP/out/$name"
  if grep -n '@IOGOV_[A-Z_]*@' "$TMP/out/$name" | grep -v '^[0-9]*:#' >&2; then die "unrendered token left in $name"; fi
done
if ((DRY)) || [[ -n $STAGE ]]; then say "SKIP verify: systemd-analyze verify runs in a real install, after the service user exists"
else
  (cd "$TMP/out" && systemd-analyze verify --man=no ./*.service ./*.timer ./*.slice) || die "systemd-analyze verify rejected the rendered units"
  say "systemd-analyze verify: ok"
fi

say "== 7. install units (none of them enabled here) and the read-only ACL rule"
ensure_dir 755 root:root "$UNIT_DIR"
ensure_dir 755 root:root "$RULES_DIR"
for u in iogov-probe.slice iogov-tick.service iogov-tick.timer iogov-watch.service iogov-bench.service iogov-bench.timer; do
  put_file "$TMP/out/$u" 644 "$UNIT_DIR/$u"
done
put_file "$TMP/out/99-iogov-raw-read.rules" 644 "$RULES_DIR/99-iogov-raw-read.rules"
if [[ -n $STAGE ]]; then say "SKIP (staging): systemctl daemon-reload, udevadm trigger"
else
  run systemctl daemon-reload
  kname=$(basename "$(readlink -f "$DISK_NODE")")
  run udevadm trigger --action=change --subsystem-match=block --sysname-match="$kname"
fi

say "== 8. tick timer"
if ((ENABLE_TICK)); then
  if [[ -n $STAGE ]]; then say "SKIP (staging): systemctl enable --now iogov-tick.timer"; else run systemctl enable --now iogov-tick.timer; fi
else
  say "not enabled (iogov-tick.timer stays off). The bench timer and the watch service are never enabled by this script."
fi
cat <<NEXT

Next, by hand (README steps 3-5): verify the ACL as $USER_NAME:  sudo -u $USER_NAME dd if=$DISK_NODE bs=4k count=1 iflag=direct of=/dev/null ; sudo -u $USER_NAME test -w $DISK_NODE && echo WRITABLE-STOP
then  sudo -u $USER_NAME env PYTHONPATH=$LIB_DIR IOGOV_CONFIG=$CONFIG $PYBIN -B -m iogov plan   and   systemctl start iogov-tick.service
and only then  $0 --enable-tick.
NEXT
