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
info(){ printf '  INFO %s\n' "$*"; }   # neither pass nor fail -- disclosure only, not counted

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
for s in dev dev-interactive dev-background dev-gates; do
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

echo "== dev.slice IO caps (shared by every dev.slice child) =="
if [ -d "$CG/dev.slice" ]; then
  iom=$(cat "$CG/dev.slice/io.max" 2>/dev/null)
  [ -n "$iom" ] && echo "  io.max: $iom" \
    || warn "no io.max on dev.slice — neither statics nor runtime caps applied"
  printf '  %-14s %s\n' io.bfq.weight "$(cat "$CG/dev.slice/io.bfq.weight" 2>/dev/null)"
else
  warn "dev.slice cgroup absent (no member started yet)"
fi

echo "== dev-gates.slice effective values (gate/lane containers, the admission capacity object, RG-55 D-19) =="
# Bytes-suffix parser for the memory.max/memory.high mismatch check below --
# systemd resource-control K/M/G/T suffixes are base-1024, same as
# install.sh's own render-time helper (withdrawn with dev-infra.slice, so
# re-declared locally here rather than sourced from anywhere).
_bytes_of() { # _bytes_of "6G"|""|"max" -> byte count, or the input verbatim
              # for "" and "max" (not numeric, compared specially below)
  local v="${1:-}" num
  case "$v" in
    ""|max) printf '%s' "$v"; return ;;
    *K) num="${v%K}"; printf '%s' "$(( num * 1024 ))"; return ;;
    *M) num="${v%M}"; printf '%s' "$(( num * 1024 * 1024 ))"; return ;;
    *G) num="${v%G}"; printf '%s' "$(( num * 1024 * 1024 * 1024 ))"; return ;;
    *T) num="${v%T}"; printf '%s' "$(( num * 1024 * 1024 * 1024 * 1024 ))"; return ;;
    *)  printf '%s' "$v"; return ;;
  esac
}
if [ -d "$CG/dev-gates.slice" ]; then
  for f in memory.high memory.max memory.swap.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$CG/dev-gates.slice/$f" 2>/dev/null)"
  done
  # B3 (RG-55 P8 review round 1): a config that predates DEV_GATES_MEMORY_MAX/
  # _HIGH renders the directive DROPPED (render()'s own rule), so the slice
  # exists and is active -- everything above still prints happily -- while
  # memory.max is the kernel default "max" (unbounded). This directory's own
  # convention for a fail-open invariant is a hard `fail`, not a printout
  # (see the MemoryMin ancestor-chain check below); the same convention now
  # applies here. A genuinely-unset $CONF value is a `warn`, not a `fail` --
  # only "config sets it, kernel does not have it" is a `fail`.
  for prop_label in "memory.max:DEV_GATES_MEMORY_MAX" "memory.high:DEV_GATES_MEMORY_HIGH"; do
    prop="${prop_label%%:*}"; var="${prop_label#*:}"
    eff="$(cat "$CG/dev-gates.slice/$prop" 2>/dev/null)"
    cfg="${!var:-}"
    if [ -z "$cfg" ]; then
      warn "$var not set in $CONF -- dev-gates.slice's effective $prop ($eff) is whatever the installed unit carries, unchecked"
    else
      cfg_bytes="$(_bytes_of "$cfg")"
      if [ "$eff" = "$cfg_bytes" ]; then
        ok "dev-gates.slice $prop=$eff matches $var=$cfg"
      else
        fail "dev-gates.slice $prop=$eff but $var=$cfg (${cfg_bytes} bytes) -- config says bounded, the kernel's effective value differs (a stale unit, a partial install, or \$CONF predating this key, B3). Re-run install.sh."
      fi
    fi
  done
else
  warn "dev-gates.slice cgroup absent (no member started yet — expected until a gate/lane container is created with --cgroup-parent=dev-gates.slice)"
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

echo "== cgprofile socket carrier (/run/cgprofile, RG-55 A2/D-30, M5) =="
# The devcontainer template bind-mounts this directory (Docker --mount,
# which refuses a missing source) to reach the profiler daemon's control
# socket. install.sh installs + applies units/tmpfiles-cgprofile.conf; this
# is this directory's own fail-open invariant, so like MemoryMin above it
# gets a hard `fail`, not a printout.
CGPROFILE_DIR=/run/cgprofile
if [ -d "$CGPROFILE_DIR" ]; then
  cg_mode=$(stat -c '%a' "$CGPROFILE_DIR" 2>/dev/null)
  cg_owner=$(stat -c '%U:%G' "$CGPROFILE_DIR" 2>/dev/null)
  if [ "$cg_mode" = "770" ] && [ "$cg_owner" = "root:docker" ]; then
    ok "$CGPROFILE_DIR exists, mode 0770, owner root:docker"
  else
    fail "$CGPROFILE_DIR exists but mode=${cg_mode:-?} owner=${cg_owner:-?} (expected 770 root:docker) -- /etc/tmpfiles.d/cgprofile.conf missing/stale or not applied; re-run 'systemd-tmpfiles --create /etc/tmpfiles.d/cgprofile.conf' or install.sh"
  fi
  # Disclosure only (INFO, not WARN/FAIL): the daemon owns the socket's own
  # lifecycle (a separate package, out of this project's scope) -- its
  # absence here is expected and fine on a host without cgroup-profiler
  # installed, or one whose daemon predates the socket listener. exec stays
  # the permanent fallback transport either way (design doc A2).
  if [ -S "$CGPROFILE_DIR/ctl.sock" ]; then
    info "socket carrier available ($CGPROFILE_DIR/ctl.sock)"
  else
    info "exec carrier only (daemon socket absent) -- docker exec still works; the daemon may not be installed, may predate the listener, or has not started it yet"
  fi
else
  fail "$CGPROFILE_DIR does not exist -- /etc/tmpfiles.d/cgprofile.conf missing or not applied; re-run install.sh (or 'sudo systemd-tmpfiles --create /etc/tmpfiles.d/cgprofile.conf' directly)"
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
  echo "   cgroup-parent applies); gate/lane containers should show dev-gates.slice;"
  echo "   placement is CREATE-time only — recreate a container to move it)"
else
  warn "docker unavailable — skipped placement listing"
fi

echo
echo "result: $FAIL failure(s), $WARN warning(s)"
[ "$FAIL" -eq 0 ]
