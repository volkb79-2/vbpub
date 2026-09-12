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
for s in dev dev-interactive dev-background dev-infra dev-gates; do
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

echo "== dev-infra.slice effective values (host-level daemons, RG-55 D-18) =="
if [ -d "$CG/dev-infra.slice" ]; then
  for f in memory.min memory.high memory.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$CG/dev-infra.slice/$f" 2>/dev/null)"
  done
else
  warn "dev-infra.slice cgroup absent (no member started yet — expected until cgprofile-host-daemon is installed/recreated with --cgroup-parent=dev-infra.slice)"
fi

echo "== dev-gates.slice effective values (gate/lane containers, the admission capacity object, RG-55 D-19) =="
if [ -d "$CG/dev-gates.slice" ]; then
  for f in memory.high memory.max memory.swap.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$CG/dev-gates.slice/$f" 2>/dev/null)"
  done
else
  warn "dev-gates.slice cgroup absent (no member started yet — expected until a gate/lane container is created with --cgroup-parent=dev-gates.slice)"
fi

echo "== dev.slice MemoryMin ancestor-chain (dev-infra + dev-memory_min_guaranteed) =="
# CGROUP-NOTES.md "the one invariant that actually matters": dev.slice's own
# MemoryMin must equal the SUM of every child that declares its own,
# EXACTLY — dev-infra.slice's always-on floor (RG-55 D-18) and
# dev-memory_min_guaranteed.slice's opt-in ceiling. Any surplus at dev.slice
# beyond what its children actually claim is handed down by cgroup v2's
# proportional redistribution to dev-interactive.slice and
# dev-background.slice too; any shortfall silently leaves one or both
# children's own floor inert — `systemctl show` reports every value happily
# and nothing else notices they have drifted apart. All three are rendered
# from install.sh's own DEV_SLICE_MEMORY_MIN sum, so a mismatch means a
# hand-edited unit, a partial install, or a stale /etc/systemd/system copy
# from before a config change.
_min_val() { # _min_val <slice> -> byte count, 0 if unset/absent
  local v
  v=$(systemctl show "$1" -p MemoryMin --value 2>/dev/null)
  case "${v:-0}" in
    0|""|"[not set]") echo 0 ;;
    *) echo "$v" ;;
  esac
}
imin=$(_min_val dev-infra.slice)
gmin=$(_min_val dev-memory_min_guaranteed.slice)
dmin=$(_min_val dev.slice)
expected=$(( imin + gmin ))
if [ "$dmin" -eq "$expected" ]; then
  if [ "$expected" -eq 0 ]; then
    ok "inert (dev-infra.slice and dev-memory_min_guaranteed.slice both unset/absent, dev.slice MemoryMin=0 — expected before either unit is installed)"
  else
    ok "dev.slice MemoryMin=$dmin == dev-infra.slice($imin) + dev-memory_min_guaranteed.slice($gmin) — no protection leaks to dev-interactive/dev-background"
  fi
elif [ "$dmin" -gt "$expected" ]; then
  fail "MemoryMin MISMATCH: dev.slice=$dmin > dev-infra.slice($imin) + dev-memory_min_guaranteed.slice($gmin)=$expected — the surplus ($((dmin - expected)) bytes) redistributes to dev-interactive.slice/dev-background.slice, the exact leak this tier design exists to prevent. Fix: re-run install.sh (dev.slice's MemoryMin is computed from the same DEV_INFRA_MEMORY_MIN/DEV_MEMORY_MIN_GUARANTEED_CEILING vars in $CONF)"
else
  fail "MemoryMin MISMATCH: dev.slice=$dmin < dev-infra.slice($imin) + dev-memory_min_guaranteed.slice($gmin)=$expected — one or both children's own floor is silently inert (systemctl show reports the value, nothing is actually protected). Fix: re-run install.sh (dev.slice's MemoryMin is computed from the same DEV_INFRA_MEMORY_MIN/DEV_MEMORY_MIN_GUARANTEED_CEILING vars in $CONF)"
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
  echo "  (devcontainers should show dev-interactive.slice; long-running dev stacks"
  echo "   dev-background.slice (explicitly, or <daemon default> if the daemon.json"
  echo "   cgroup-parent applies); cgprofile-host-daemon should show dev-infra.slice;"
  echo "   gate/lane containers should show dev-gates.slice; placement is CREATE-time"
  echo "   only — recreate a container to move it)"
else
  warn "docker unavailable — skipped placement listing"
fi

echo
echo "result: $FAIL failure(s), $WARN warning(s)"
[ "$FAIL" -eq 0 ]
