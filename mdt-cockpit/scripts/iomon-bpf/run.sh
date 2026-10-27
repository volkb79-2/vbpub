#!/usr/bin/env bash
# Start the iomon eBPF collector container (detached, time-boxed, removed on exit).
#   run.sh [--hours N] [--name NAME] [--out DIR] [--link PATH] [--slice NAME]... [--ptrace]
#          [--apparmor-unconfined] <container-id-or-name> [<container-id-or-name>]
# The container is the ONLY thing started: fixed name, --rm, run under the dev background
# slice, root, read-only rootfs, host cgroup tree/tracefs/btf read-only, one writable output
# bind.  Watched containers are only resolved with `docker inspect --format {{.Id}} -- <exact>`.
# --out defaults to a fresh `mktemp -d` under /tmp (printed; /tmp must also be visible to the
# Docker daemon as a host path).  --link PATH symlinks the output dir there (e.g. OUT/bpf so
# `iomon.py analyze|stop` find it).
# Stop: touch <out>/STOP-BPF (or `docker stop -t 60 <name>` by exact name).
# NOTE the residual power: root + --pid=host + CAP_SYS_ADMIN is host-root equivalent.
set -euo pipefail
die() { echo "run.sh: $*" >&2; exit 2; }
HOURS=8; NAME=iomon-bpf-20261027; OUT=; LINK=; PTRACE=0; UNCONFINED=0; SLICES=()
IMAGE=${IOMON_BPF_IMAGE:-iomon-bpf:20261027}
while [ $# -gt 0 ]; do case "$1" in
  --hours) [ $# -ge 2 ] || die "--hours needs a value"; HOURS=$2; shift 2;;
  --name) [ $# -ge 2 ] || die "--name needs a value"; NAME=$2; shift 2;;
  --out) [ $# -ge 2 ] || die "--out needs a value"; OUT=$2; shift 2;;
  --link) [ $# -ge 2 ] || die "--link needs a value"; LINK=$2; shift 2;;
  --slice) [ $# -ge 2 ] || die "--slice needs a value"; SLICES+=("$2"); shift 2;;
  --ptrace) PTRACE=1; shift;;
  --apparmor-unconfined) UNCONFINED=1; shift;;
  --) shift; break;;
  -*) die "unknown option $1";;
  *) break;; esac; done
[ $# -ge 1 ] && [ $# -le 2 ] || die "usage: $0 [opts] ID_OR_NAME [ID_OR_NAME]"
# Strict validation: nothing below is ever interpolated into code.
awk -v h="$HOURS" 'BEGIN { exit !(h ~ /^[0-9]+(\.[0-9]+)?$/ && h + 0 > 0 && h + 0 <= 168) }' \
  || die "--hours must be a positive number <= 168, got '$HOURS'"
[[ $NAME =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "bad --name '$NAME'"
for s in "${SLICES[@]}"; do [[ $s =~ ^[A-Za-z0-9_.@:-]+\.slice$ ]] || die "bad --slice '$s'"; done
for c in "$@"; do [[ $c != -* && -n $c ]] || die "bad container reference '$c'"; done
if [ -z "$OUT" ]; then OUT=$(mktemp -d /tmp/iomon-bpf.XXXXXX); fi
# --mount takes comma-separated key=value pairs, so ',' (and ':') in a path cannot be quoted.
check_path() { case "$2" in *,*|*:*|*$'\n'*) die "$1 must not contain ',' ':' or a newline: $2";; esac; }
check_path --out "$OUT"
[ -z "$LINK" ] || check_path --link "$LINK"
HERE=$(cd "$(dirname "$0")/.." && pwd)
args=()
for c in "$@"; do
  id=$(docker inspect --format '{{.Id}}' -- "$c"); [[ $id =~ ^[0-9a-f]{64}$ ]] || { echo "bad id for $c" >&2; exit 1; }
  args+=(--container "$id")
done
for s in "${SLICES[@]}"; do args+=(--slice "$s"); done
# The Docker daemon resolves bind sources on the HOST: translate devcontainer paths via mountinfo.
hostpath() { python3 - "$1" <<'PY'
import os, sys
p = os.path.realpath(sys.argv[1]); best = None
for line in open("/proc/self/mountinfo"):
    f = line.split()
    root, mp = f[3], f[4]
    if (p == mp or p.startswith(mp.rstrip("/") + "/")) and (best is None or len(mp) > len(best[1])):
        best = (root, mp)
if best is None:
    sys.exit("no mount found for " + p)
print(os.path.normpath(best[0] + "/" + os.path.relpath(p, best[1])))
PY
}
mkdir -p -- "$OUT"
[ -z "$LINK" ] || ln -sfn -- "$(cd "$OUT" && pwd)" "$LINK"
HOST_OUT=$(hostpath "$OUT"); HOST_SCRIPTS=$(hostpath "$HERE")
check_path "host out path" "$HOST_OUT"; check_path "host scripts path" "$HOST_SCRIPTS"
# Capabilities: only what bpftrace needs.  SYS_PTRACE (reading /proc/<pid>/maps of other users'
# processes for the eatmydata check) and apparmor=unconfined are opt-in; without them the
# preload check reports readable=false for processes of other uids.
caps=(--cap-add SYS_ADMIN --cap-add BPF --cap-add PERFMON --cap-add SYS_RESOURCE --cap-add SYSLOG)
[ "$PTRACE" = 0 ] || caps+=(--cap-add SYS_PTRACE)
secopt=(); [ "$UNCONFINED" = 0 ] || secopt=(--security-opt apparmor=unconfined)
echo "starting $NAME (image $IMAGE, out $HOST_OUT, scripts $HOST_SCRIPTS, ${HOURS}h)"
echo "OUT=$OUT"
secs=$(awk -v h="$HOURS" 'BEGIN { printf "%d", h * 3600 + 120 }')
setsid nohup timeout -s TERM -k 60 "$secs" \
  docker run --rm --name "$NAME" --user 0 --init --pid=host --cpus 1 --memory 1g --pids-limit 256 \
  --cgroup-parent="${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}" \
  "${caps[@]}" "${secopt[@]}" --read-only --tmpfs /tmp -w /tmp \
  -v /sys/kernel/tracing:/sys/kernel/tracing:ro -v /sys/kernel/debug:/sys/kernel/debug:ro \
  -v /sys/kernel/btf:/sys/kernel/btf:ro -v /sys/fs/cgroup:/hostcg:ro \
  --mount "type=bind,src=$HOST_OUT,dst=/out" --mount "type=bind,src=$HOST_SCRIPTS,dst=/scripts,readonly" \
  "$IMAGE" nice -n 10 python3 /scripts/iomon_bpf.py --out /out --max-hours "$HOURS" "${args[@]}" \
  >"$OUT/container.out" 2>&1 < /dev/null &
disown
