#!/usr/bin/env bash
# P-plat-19g: design note 5.3 precedence check. A container declared --memory 2G must read memory.max = 2G
# before AND after `systemctl daemon-reload` (the docker-.scope 8G drop-in must not win). Root, on the host.
# Exit 0 pass, 1 fail, 3 setup failure. Test hooks: MDT_ACCEPT_CGROUP, MDT_ACCEPT_IMAGE.
set -uo pipefail
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}; NAME=mdt-accept-memcheck; WANT=2147483648
IMAGE=${MDT_ACCEPT_IMAGE:-${1:-tester-unified:local}}
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT; trap 'exit 143' TERM; trap 'exit 130' INT
docker rm -f "$NAME" >/dev/null 2>&1   # stale container of a killed run, exact name
id=$(docker run -d --name "$NAME" --memory 2G --memory-swap 4G "$IMAGE" sleep 600) || { echo "daemon-reload-check: container did not start" >&2; exit 3; }
read_max() {
  local f
  for f in "$CG"/system.slice/docker-"$id".scope/memory.max "$CG"/*/docker-"$id".scope/memory.max "$CG"/*/*/docker-"$id".scope/memory.max; do
    [ -r "$f" ] && { cat "$f"; return; }
  done
  echo "missing"
}
before=$(read_max)
systemctl daemon-reload || { echo "daemon-reload-check: daemon-reload failed" >&2; exit 3; }
after=$(read_max)
if [ "$before" = "$WANT" ] && [ "$after" = "$WANT" ]; then echo "PASS memory.max before=$before after=$after (want $WANT)"; exit 0; fi
echo "FAIL memory.max before=$before after=$after (want $WANT): the docker-.scope drop-in outranks the declared cap"; exit 1
