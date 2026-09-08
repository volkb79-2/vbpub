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

echo "== dev-memory_min_guaranteed.slice (opt-in memory.min tier) =="
# CGROUP-NOTES.md "the one invariant that actually matters": dev.slice's own
# MemoryMin must equal dev-memory_min_guaranteed.slice's own, EXACTLY. Any
# surplus at dev.slice beyond what the guaranteed tier claims is handed down
# by cgroup v2's proportional redistribution to dev-interactive.slice and
# dev-background.slice too — recreating the exact leak the guaranteed tier
# exists to prevent, one level up, and silently: `systemctl show` reports
# both values happily and nothing else notices they have drifted apart.
# Both are rendered from the SAME DEV_MEMORY_MIN_GUARANTEED_CEILING var, so
# a mismatch means a hand-edited unit, a partial install, or a stale
# /etc/systemd/system copy from before a config change.
gmin=$(systemctl show dev-memory_min_guaranteed.slice -p MemoryMin --value 2>/dev/null)
gfp=$(systemctl show dev-memory_min_guaranteed.slice -p FragmentPath --value 2>/dev/null)
case "${gmin:-0}" in
  0|""|"[not set]") gmin_set=0 ;;
  *) gmin_set=1 ;;
esac
if [ -z "$gfp" ] || [ "$gmin_set" = 0 ]; then
  # install.sh's render() DROPS a directive whose value resolves to empty, so
  # an unset DEV_MEMORY_MIN_GUARANTEED_CEILING leaves both slices without a
  # MemoryMin at all. That is the shipped default and the expected state on
  # most hosts — the mechanism is opt-in per container, so not opting in is
  # not a finding. BUT the two units render from the same var and must drift
  # together — if dev.slice's own MemoryMin is somehow set nonzero while the
  # guaranteed tier's is unset, that is the maximal form of the exact leak
  # this check exists to catch (ALL of dev.slice's floor redistributes to
  # dev-interactive/dev-background, none of it reaches the guaranteed tier),
  # so check dev.slice too rather than assuming "guaranteed tier unset" means
  # "mechanism fully inert".
  dmin=$(systemctl show dev.slice -p MemoryMin --value 2>/dev/null)
  case "${dmin:-0}" in
    0|""|"[not set]") dmin_set=0 ;;
    *) dmin_set=1 ;;
  esac
  if [ "$dmin_set" = 1 ]; then
    fail "MemoryMin MISMATCH: dev.slice=$dmin vs dev-memory_min_guaranteed.slice=<unset> — dev.slice has a floor but the guaranteed tier does not, so the ENTIRE surplus redistributes to dev-interactive.slice/dev-background.slice (the leak the guaranteed tier exists to prevent) and the guaranteed tier's own floor is silently inert. Fix: set DEV_MEMORY_MIN_GUARANTEED_CEILING in $CONF and re-run install.sh (both units render from that one var)"
  else
    ok "inert (unit not installed or MemoryMin unset on both slices — the mechanism is opt-in, this is the default and expected state on most hosts)"
  fi
else
  dmin=$(systemctl show dev.slice -p MemoryMin --value 2>/dev/null)
  if [ "$dmin" = "$gmin" ]; then
    ok "MemoryMin pinned equal on dev.slice and dev-memory_min_guaranteed.slice ($gmin) — no protection leaks to dev-interactive/dev-background"
  else
    fail "MemoryMin MISMATCH: dev.slice=${dmin:-<unset>} vs dev-memory_min_guaranteed.slice=$gmin — these MUST be exactly equal. If dev.slice is HIGHER, the surplus redistributes to dev-interactive.slice/dev-background.slice (the leak the guaranteed tier exists to prevent); if LOWER, the guaranteed tier's own floor is silently inert. Fix: set DEV_MEMORY_MIN_GUARANTEED_CEILING in $CONF and re-run install.sh (both units render from that one var)"
  fi
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
