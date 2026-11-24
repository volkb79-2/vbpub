#!/usr/bin/env bash
# P-plat-19g: design note 5.3 precedence check. A container declared --memory 2G must read memory.max = 2G
# before AND after `systemctl daemon-reload` (the docker-.scope 8G drop-in must not win). Root, on the host, INSIDE the
# step-0 drain: a reload can make systemd write a lower memory.max below memory.current, so it refuses (exit 3, nothing
# done) while a lowering is deferred or systemd's MemoryMax and the kernel's memory.max differ. A FAIL means the reload
# re-applied the 8G drop-in to the declared-cap scopes (it lowers any declared cap above 8G).
# Exit 0 pass, 1 fail, 3 refused or setup failure. Test hooks: MDT_ACCEPT_CGROUP, MDT_ACCEPT_DEFERRED, MDT_ACCEPT_IMAGE, MDT_ACCEPT_LOCK, MDT_ACCEPT_POLL_STEP.
set -uo pipefail
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}; NAME=mdt-accept-memcheck; WANT=2147483648
DEFERRED=${MDT_ACCEPT_DEFERRED:-/run/mdt/reconcile/memory-deferred}
IMAGE=${MDT_ACCEPT_IMAGE:-${1:-tester-unified:local}}
exec 9>"${MDT_ACCEPT_LOCK:-/run/mdt-accept.lock}" || exit 3   # the lock accept-run.sh holds: a reload never happens under a live run
flock -n 9 || { echo "daemon-reload-check: an accept run holds the lock; refusing, nothing done" >&2; exit 3; }
if [ -e "$DEFERRED" ]; then echo "daemon-reload-check: $DEFERRED exists (a memory lowering is deferred); refusing, nothing done" >&2; exit 3; fi
for s in dev.slice dev.slice/dev-gates.slice dev.slice/dev-background.slice; do
  want=$(systemctl show -p MemoryMax --value "${s##*/}" 2>/dev/null); have=$(cat "$CG/$s/memory.max" 2>/dev/null)
  [ "$want" = infinity ] && want=max
  if [ "$(systemctl show -p NeedDaemonReload --value "${s##*/}" 2>/dev/null)" = yes ]; then   # an on-disk drop-in not loaded yet: the reload would apply it
    echo "daemon-reload-check: ${s##*/} NeedDaemonReload=yes; refusing, nothing done" >&2; exit 3
  fi
  if [ -z "$want" ] || [ "$want" != "$have" ]; then
    echo "daemon-reload-check: ${s##*/} MemoryMax systemd='$want' kernel='$have' differ; refusing, nothing done" >&2; exit 3
  fi
done
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT; trap 'exit 143' TERM; trap 'exit 130' INT
docker rm -f "$NAME" >/dev/null 2>&1   # stale container of a killed run, exact name
id=$(docker run -d --rm --network none --name "$NAME" --memory 2G --memory-swap 4G "$IMAGE" sleep 600) || { echo "daemon-reload-check: container did not start" >&2; exit 3; }
read_max() {
  local f
  for f in "$CG"/*/docker-"$id".scope/memory.max "$CG"/*/*/docker-"$id".scope/memory.max; do
    [ -r "$f" ] && { cat "$f"; return; }
  done
  echo "missing"
}
before=$(read_max)
systemctl daemon-reload || { echo "daemon-reload-check: daemon-reload failed" >&2; exit 3; }
for _ in 1 2 3 4 5 6 7 8 9 10; do   # systemd answers the reload before its cgroup queue re-applied the properties: poll, stop early on a change
  after=$(read_max); [ "$after" != "$before" ] && break; sleep "${MDT_ACCEPT_POLL_STEP:-1}"
done
echo "systemd MemoryMax of the scope: $(systemctl show -p MemoryMax --value "docker-$id.scope" 2>/dev/null)"
if [ "$before" = "$WANT" ] && [ "$after" = "$WANT" ]; then echo "PASS memory.max before=$before after=$after (want $WANT)"; exit 0; fi
echo "FAIL memory.max before=$before after=$after (want $WANT): the docker-.scope drop-in outranks the declared cap"; exit 1
