#!/usr/bin/env bash
# Start the iomon eBPF collector container (detached, time-boxed, removed on exit).
#   run.sh [--hours N] [--name NAME] [--out DIR] <container-id-or-name> [<container-id-or-name>]
# The container is the ONLY thing started: fixed name, --rm, run under the dev background
# slice, root, read-only rootfs, host cgroup tree/tracefs/btf read-only, one writable output
# bind.  Watched containers are only resolved with `docker inspect --format {{.Id}} <exact>`.
# Stop: touch <out>/STOP-BPF (or `docker stop <name>` by exact name).
set -euo pipefail
HOURS=8; NAME=iomon-bpf-20261027; OUT=/tmp/iomon-bpf-20261027; IMAGE=${IOMON_BPF_IMAGE:-iomon-bpf:20261027}
while [ $# -gt 0 ]; do case "$1" in
  --hours) HOURS=$2; shift 2;; --name) NAME=$2; shift 2;; --out) OUT=$2; shift 2;; *) break;; esac; done
[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: $0 [opts] ID_OR_NAME [ID_OR_NAME]" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")/.." && pwd)
args=()
for c in "$@"; do
  id=$(docker inspect --format '{{.Id}}' "$c"); [[ $id =~ ^[0-9a-f]{64}$ ]] || { echo "bad id for $c" >&2; exit 1; }
  args+=(--container "$id")
done
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
mkdir -p "$OUT"
HOST_OUT=$(hostpath "$OUT"); HOST_SCRIPTS=$(hostpath "$HERE")
echo "starting $NAME (image $IMAGE, out $HOST_OUT, scripts $HOST_SCRIPTS, ${HOURS}h)"
setsid nohup timeout -s TERM -k 60 "$(python3 -c "print(int(float('$HOURS')*3600)+120)")" \
  docker run --rm --name "$NAME" --user 0 --init --pid=host --cpus 1 --memory 1g --pids-limit 256 \
  --cgroup-parent="${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}" \
  --cap-add SYS_ADMIN --cap-add BPF --cap-add PERFMON --cap-add SYS_RESOURCE --cap-add SYSLOG \
  --security-opt apparmor=unconfined --read-only --tmpfs /tmp -w /tmp \
  -v /sys/kernel/tracing:/sys/kernel/tracing:ro -v /sys/kernel/debug:/sys/kernel/debug:ro \
  -v /sys/kernel/btf:/sys/kernel/btf:ro -v /sys/fs/cgroup:/hostcg:ro \
  -v "$HOST_OUT":/out -v "$HOST_SCRIPTS":/scripts:ro \
  "$IMAGE" nice -n 10 python3 /scripts/iomon_bpf.py --out /out --max-hours "$HOURS" "${args[@]}" \
  >"$OUT/container.out" 2>&1 < /dev/null &
disown
