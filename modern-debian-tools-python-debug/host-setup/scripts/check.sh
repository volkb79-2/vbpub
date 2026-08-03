#!/usr/bin/env bash
# mdt host-setup verification — read-only health check for the dev-tier
# governance. Run as root ON THE HOST (installed as mdt-host-check.sh).
# Exit code: 0 = no failures (warnings allowed), 1 = at least one failure.
set -uo pipefail
CG="${CG:-/sys/fs/cgroup}"
CONF="${CONF:-/etc/mdt/host-setup.env}"
FAIL=0; WARN=0
ok(){   printf '  OK   %s\n' "$*"; }
warn(){ printf '  WARN %s\n' "$*"; WARN=$((WARN+1)); }
fail(){ printf '  FAIL %s\n' "$*"; FAIL=$((FAIL+1)); }

# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"

echo "== cgroup2 mount flags =="
OPTS=$(findmnt -no OPTIONS "$CG" 2>/dev/null)
case "$OPTS" in
  *memory_recursiveprot*) ok "memory_recursiveprot present ($OPTS)" ;;
  *) fail "memory_recursiveprot MISSING ($OPTS) — slice MemoryLow/MemoryMin protect NOTHING below them. Fix: mount -o remount,nsdelegate,memory_recursiveprot $CG" ;;
esac

echo "== slice units =="
for s in dev dev-interactive dev-background; do
  st=$(systemctl show "$s.slice" -p ActiveState --value 2>/dev/null)
  fp=$(systemctl show "$s.slice" -p FragmentPath --value 2>/dev/null)
  if [ -z "$fp" ]; then
    fail "$s.slice has no unit file — members land in a transient UNLIMITED slice"
  elif [ "$st" = "active" ]; then
    ok "$s.slice active ($fp)"
  else
    warn "$s.slice inactive (activates with its first member container)"
  fi
done

echo "== dev-interactive.slice effective values =="
if [ -d "$CG/dev-interactive.slice" ]; then
  # io.bfq.weight is listed deliberately: on a BFQ host it is what actually
  # schedules, and it is NOT io.weight — systemd rescales IOWeight 1..10000
  # into BFQ's 1..1000 (identity at <= 100, ~11x compression above).
  for f in memory.high memory.max memory.low cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$CG/dev-interactive.slice/$f" 2>/dev/null)"
  done
  wb=$(cat "$CG/dev-interactive.slice/memory.zswap.writeback" 2>/dev/null)
  case "${DEV_INTERACTIVE_ZSWAP_WRITEBACK:-no}" in no|0|false) want=0 ;; *) want=1 ;; esac
  [ "$wb" = "$want" ] && ok "memory.zswap.writeback=$wb" \
    || warn "memory.zswap.writeback=$wb (expected $want — run mdt-apply-dev-caps.sh)"
else
  warn "dev-interactive.slice cgroup absent (no member started yet)"
fi

echo "== dev.slice IO caps (shared by dev-interactive + dev-background) =="
if [ -d "$CG/dev.slice" ]; then
  iom=$(cat "$CG/dev.slice/io.max" 2>/dev/null)
  [ -n "$iom" ] && echo "  io.max: $iom" \
    || warn "no io.max on dev.slice — neither statics nor runtime caps applied"
  printf '  %-14s %s\n' io.bfq.weight "$(cat "$CG/dev.slice/io.bfq.weight" 2>/dev/null)"
else
  warn "dev.slice cgroup absent (no member started yet)"
fi

echo "== docker-.scope.d default-limits backstop (D-G8) =="
if [ -f /etc/systemd/system/docker-.scope.d/50-default-limits.conf ]; then
  ok "backstop drop-in installed"
else
  warn "no docker-.scope.d/50-default-limits.conf — a typo'd/missing --cgroup-parent fails OPEN (unbounded), unmitigated"
fi
# Independent of the slice: a missing/stale baseline means the caps in force are
# the tight unit statics, whether or not any member has started.
if [ -f "$IO_BASELINE_ENV" ]; then
  age_days=$(( ($(date +%s) - $(stat -c %Y "$IO_BASELINE_ENV")) / 86400 ))
  [ "$age_days" -le 30 ] && ok "baseline present (${age_days}d old, $IO_BASELINE_ENV)" \
    || warn "baseline is ${age_days}d old — re-run mdt-io-baseline.py --force in a quiet window"
else
  warn "no baseline ($IO_BASELINE_ENV) — tight unit statics in force; run mdt-io-baseline.py"
fi

echo "== IO scheduler (weights need BFQ; io.max works on any) =="
found_bfq=0
for f in /sys/block/*/queue/scheduler; do
  d=${f#/sys/block/}; d=${d%%/*}
  case "$d" in loop*|sr*|ram*|zram*) continue ;; esac
  sched=$(cat "$f")
  printf '  %-8s %s\n' "$d" "$sched"
  case "$sched" in *"[bfq]"*) found_bfq=1 ;; esac
done
[ "$found_bfq" = 1 ] && ok "at least one disk uses BFQ" \
  || warn "no disk uses BFQ — IOWeight is inert, only the io.max caps enforce. Expected on NVMe (the shipped udev rule matches vd*/sd* only, on purpose); on vd*/sd* it means the rule or the module did not load"

echo "== sweep service/timer =="
systemctl is-enabled mdt-host-slices.timer >/dev/null 2>&1 \
  && ok "mdt-host-slices.timer enabled ($(systemctl show mdt-host-slices.timer -p NextElapseUSecRealtime --value 2>/dev/null))" \
  || warn "mdt-host-slices.timer not enabled — new buildkit/devcontainer containers stay uncapped until reboot"

echo "== container placement (informational) =="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker ps --format '{{.Names}}' | while read -r n; do
    p=$(docker inspect -f '{{.HostConfig.CgroupParent}}' "$n" 2>/dev/null)
    printf '  %-45s %s\n' "$n" "${p:-<daemon default>}"
  done
  echo "  (devcontainers should show dev-interactive.slice; test/build/gate stacks"
  echo "   dev-background.slice (explicitly, or <daemon default> if the daemon.json"
  echo "   cgroup-parent applies); placement is CREATE-time only — recreate a"
  echo "   container to move it)"
else
  warn "docker unavailable — skipped placement listing"
fi

echo
echo "result: $FAIL failure(s), $WARN warning(s)"
[ "$FAIL" -eq 0 ]
