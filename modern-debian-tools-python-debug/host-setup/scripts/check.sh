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

# systemd derives child slice paths from their dash-separated unit names:
# dev-interactive.slice is /dev.slice/dev-interactive.slice in cgroupfs.
# Keep the filesystem path conversion in one place so every check reads the
# same hierarchy the runtime writers use.
_slice_path() {
  local slice="$1"
  if [ "$slice" = dev.slice ]; then
    printf '%s/dev.slice' "$CG"
  else
    printf '%s/dev.slice/%s' "$CG" "$slice"
  fi
}

# Bytes-suffix parser for memory.max/memory.high/memory.swap.max mismatch
# checks below -- systemd resource-control K/M/G/T suffixes are base-1024,
# same as install.sh's own render-time helper. bash integer arithmetic is a
# syntax error on any legal-systemd non-integer mantissa ("4.5G" -- and
# mdt's own wizard, kib_to_size_str(), rounds to the nearest half-GiB and
# emits exactly this form routinely), and a percentage ("50%", also legal
# systemd syntax) falls through unparsed and would then be compared verbatim
# against a byte count -- both cases hard-FAIL a correctly configured host.
# awk does floating point natively, so this is TOTAL: every input either
# resolves to a byte count, passes "" / "max" through verbatim, or -- for
# anything not byte-comparable (percentages, "infinity", garbage) --
# returns the literal string "?". Call sites treat "?" as "cannot check
# this form" (a `warn`), never as a byte value to compare (never a `fail`).
_bytes_of() { # _bytes_of "6G"|"4.5G"|""|"max"|"50%" -> byte count, "", "max"
              # verbatim, or "?" for anything not byte-comparable
  local v="${1:-}"
  case "$v" in
    ""|max) printf '%s' "$v"; return ;;
  esac
  awk -v v="$v" 'BEGIN{
    if (v !~ /^[0-9]+(\.[0-9]+)?[KMGT]?i?B?$/) { print "?"; exit }
    m = 1
    if (v ~ /K/) m = 1024; else if (v ~ /M/) m = 1024^2
    else if (v ~ /G/) m = 1024^3; else if (v ~ /T/) m = 1024^4
    printf "%d", (v + 0) * m
  }'
}

# _zswap_check <slice> <config-var-name>: compares the slice's live
# memory.zswap.writeback against the yes/no config var it was rendered
# from. Shared by every slice section below -- MemoryZSwapWriteback is a
# per-cgroup toggle that does NOT cascade (see units/dev.slice.in), so each
# slice needs its own independent check, not one check inherited from a
# parent's value.
_zswap_check() {
  local slice="$1" var="$2" path val want
  path="$(_slice_path "$slice")/memory.zswap.writeback"
  [ -e "$path" ] || { warn "$slice memory.zswap.writeback: file absent (kernel/systemd too old for the controller, or cgroup not active)"; return; }
  val=$(cat "$path" 2>/dev/null)
  case "${!var:-no}" in no|0|false) want=0 ;; *) want=1 ;; esac
  [ "$val" = "$want" ] && ok "$slice memory.zswap.writeback=$val (matches $var)" \
    || warn "$slice memory.zswap.writeback=$val (expected $want from $var — run mdt-apply-dev-caps.sh)"
}

# _cpu_quota_check <slice> <config-var-name>: prints cpu.max and, when the
# config var holds an explicit "N%" (rather than empty/auto-detect),
# verifies the kernel's own quota/period matches it.
_cpu_quota_check() {
  local slice="$1" var="$2" cur cfg period quota cur_quota want
  cur=$(cat "$(_slice_path "$slice")/cpu.max" 2>/dev/null)
  printf '  %-14s %s\n' cpu.max "$cur"
  cfg="${!var:-}"
  case "$cfg" in
    "")
      info "$var empty — CPUQuota auto-detected at install time (nproc minus the configured core reserve), not independently re-derivable here"
      ;;
    *%)
      quota="${cfg%\%}"
      cur_quota="${cur%% *}"
      period="${cur#* }"
      [ "$period" = "$cur" ] && period=100000
      if ! [ "$period" -gt 0 ] 2>/dev/null; then period=100000; fi
      want=$(( period * quota / 100 ))
      if [ "$cur_quota" = "$want" ]; then
        ok "$slice cpu.max quota matches $var=$cfg ($want of period $period)"
      else
        fail "$slice cpu.max=$cur but $var=$cfg implies quota=$want of period=$period — stale unit or partial install. Re-run install.sh."
      fi
      ;;
    *)
      warn "$var=$cfg is not a recognized CPUQuota form (N%%)"
      ;;
  esac
}

# _swap_max_check <slice> <config-var-name>: prints/verifies
# memory.swap.max. An empty config var is NOT a finding — every tier's
# MemorySwapMax auto-detects from the swap cascade (DEV_SWAP_CASCADE_PCT)
# at install time when left unset, so there is no fixed number to compare
# against here without re-deriving the whole cascade.
_swap_max_check() {
  local slice="$1" var="$2" cur cfg cfg_bytes
  cur=$(cat "$(_slice_path "$slice")/memory.swap.max" 2>/dev/null)
  cfg="${!var:-}"
  if [ -z "$cfg" ]; then
    info "$slice memory.swap.max=$cur ($var empty — auto-detected from the swap cascade at install time)"
  else
    cfg_bytes="$(_bytes_of "$cfg")"
    if [ "$cfg_bytes" = "?" ]; then
      warn "$var=$cfg is not byte-comparable (percentage or non-size form) — effective memory.swap.max=$cur not checked"
    elif [ "$cur" = "$cfg_bytes" ]; then
      ok "$slice memory.swap.max=$cur matches $var=$cfg"
    else
      fail "$slice memory.swap.max=$cur but $var=$cfg (${cfg_bytes} bytes) — stale unit or partial install. Re-run install.sh."
    fi
  fi
}

# _iops_subceiling_check <slice>: dev-gates.slice/dev-buildkitd.slice each
# get a tighter IOPS-only sub-ceiling (DEV_SUBSLICE_IOPS_PCT% of whatever
# mdt-apply-dev-caps.sh just measured for dev.slice itself) applied at
# RUNTIME, not a static unit-file line — see units/dev-gates.slice.in and
# units/dev-buildkitd.slice.in. Bandwidth is deliberately left untouched.
_iops_subceiling_check() {
  local slice="$1" pct dev_iom sub_iom dev_riops dev_wiops sub_riops sub_wiops want_r want_w
  pct="${DEV_SUBSLICE_IOPS_PCT:-60}"
  dev_iom=$(cat "$(_slice_path dev.slice)/io.max" 2>/dev/null)
  sub_iom=$(cat "$(_slice_path "$slice")/io.max" 2>/dev/null)
  if [ -z "$dev_iom" ] || [ -z "$sub_iom" ]; then
    info "$slice IOPS sub-ceiling: dev.slice or $slice io.max not set yet — skipped"
    return
  fi
  dev_riops=$(printf '%s\n' "$dev_iom" | grep -oE 'riops=[0-9]+' | cut -d= -f2)
  dev_wiops=$(printf '%s\n' "$dev_iom" | grep -oE 'wiops=[0-9]+' | cut -d= -f2)
  sub_riops=$(printf '%s\n' "$sub_iom" | grep -oE 'riops=[0-9]+' | cut -d= -f2)
  sub_wiops=$(printf '%s\n' "$sub_iom" | grep -oE 'wiops=[0-9]+' | cut -d= -f2)
  if [ -z "$dev_riops" ] || [ -z "$dev_wiops" ] || [ -z "$sub_riops" ] || [ -z "$sub_wiops" ]; then
    info "$slice IOPS sub-ceiling: riops/wiops not numeric in io.max yet (mdt-apply-dev-caps.sh has not run, or no IO baseline) — skipped"
    return
  fi
  want_r=$(( dev_riops * pct / 100 ))
  want_w=$(( dev_wiops * pct / 100 ))
  if [ "$sub_riops" -eq "$want_r" ] && [ "$sub_wiops" -eq "$want_w" ]; then
    ok "$slice IOPS sub-ceiling riops=$sub_riops/wiops=$sub_wiops matches ${pct}% of dev.slice's own riops=$dev_riops/wiops=$dev_wiops"
  else
    warn "$slice IOPS sub-ceiling riops=$sub_riops/wiops=$sub_wiops does not match ${pct}% of dev.slice's own riops=$dev_riops/wiops=$dev_wiops (want riops=$want_r/wiops=$want_w) — run mdt-apply-dev-caps.sh"
  fi
}

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
INTERACTIVE_PATH="$(_slice_path dev-interactive.slice)"
if [ -d "$INTERACTIVE_PATH" ]; then
  # io.bfq.weight is listed deliberately: on a BFQ host it is what actually
  # schedules, and it is NOT io.weight — systemd rescales IOWeight 1..10000
  # into BFQ's 1..1000 (identity at <= 100, ~11x compression above).
  for f in memory.high memory.max memory.low cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$INTERACTIVE_PATH/$f" 2>/dev/null)"
  done
  _cpu_quota_check dev-interactive.slice DEV_INTERACTIVE_CPU_QUOTA
  _swap_max_check dev-interactive.slice DEV_INTERACTIVE_MEMORY_SWAP_MAX
  _zswap_check dev-interactive.slice DEV_INTERACTIVE_ZSWAP_WRITEBACK
else
  warn "dev-interactive.slice cgroup absent (no member started yet)"
fi

echo "== dev-background.slice effective values =="
BACKGROUND_PATH="$(_slice_path dev-background.slice)"
if [ -d "$BACKGROUND_PATH" ]; then
  for f in memory.high memory.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$BACKGROUND_PATH/$f" 2>/dev/null)"
  done
  _cpu_quota_check dev-background.slice DEV_BACKGROUND_CPU_QUOTA
  _swap_max_check dev-background.slice DEV_BACKGROUND_MEMORY_SWAP_MAX
  _zswap_check dev-background.slice DEV_BACKGROUND_ZSWAP_WRITEBACK
else
  warn "dev-background.slice cgroup absent (no member started yet)"
fi

echo "== dev.slice effective values (shared root — IO/CPU/swap ceiling for every child combined) =="
DEV_PATH="$(_slice_path dev.slice)"
if [ -d "$DEV_PATH" ]; then
  iom=$(cat "$DEV_PATH/io.max" 2>/dev/null)
  [ -n "$iom" ] && echo "  io.max: $iom" \
    || warn "no io.max on dev.slice — neither statics nor runtime caps applied"
  printf '  %-14s %s\n' io.bfq.weight "$(cat "$DEV_PATH/io.bfq.weight" 2>/dev/null)"
  _cpu_quota_check dev.slice DEV_CPU_QUOTA
  _swap_max_check dev.slice DEV_SWAP_MAX
  # dev.slice's own MemoryZSwapWriteback is a safety anchor only (its FILE
  # value does not cascade to children — see units/dev.slice.in), but its
  # ENFORCEMENT does: a 0 here silently disables writeback for the whole
  # subtree regardless of what each child's own file says. Worth checking
  # with the same rigor as every child's own value.
  _zswap_check dev.slice DEV_ZSWAP_WRITEBACK
else
  warn "dev.slice cgroup absent (no member started yet)"
fi

echo "== dev-gates.slice effective values (gate/lane containers, the admission capacity object, RG-55 D-19) =="
GATES_PATH="$(_slice_path dev-gates.slice)"
if [ -d "$GATES_PATH" ]; then
  for f in memory.high memory.max memory.swap.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$GATES_PATH/$f" 2>/dev/null)"
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
    eff="$(cat "$GATES_PATH/$prop" 2>/dev/null)"
    cfg="${!var:-}"
    if [ -z "$cfg" ]; then
      warn "$var not set in $CONF -- dev-gates.slice's effective $prop ($eff) is whatever the installed unit carries, unchecked"
    else
      cfg_bytes="$(_bytes_of "$cfg")"
      if [ "$cfg_bytes" = "?" ]; then
        # round-2 review B7: not byte-comparable (a percentage, "infinity",
        # or anything else outside \d+(\.\d+)?[KMGT]?i?B?$) is a DIFFERENT
        # finding from "byte-comparable but does not match" -- warn naming
        # the form, never fail, since we genuinely cannot tell whether this
        # is correct or not from here.
        warn "$var=$cfg is not byte-comparable (percentage or non-size form) -- effective $prop=$eff not checked"
      elif [ "$eff" = "$cfg_bytes" ]; then
        ok "dev-gates.slice $prop=$eff matches $var=$cfg"
      else
        fail "dev-gates.slice $prop=$eff but $var=$cfg (${cfg_bytes} bytes) -- config says bounded, the kernel's effective value differs (a stale unit, a partial install, or \$CONF predating this key, B3). Re-run install.sh."
      fi
    fi
  done
  _cpu_quota_check dev-gates.slice DEV_GATES_CPU_QUOTA
  _swap_max_check dev-gates.slice DEV_GATES_MEMORY_SWAP_MAX
  _zswap_check dev-gates.slice DEV_GATES_ZSWAP_WRITEBACK
  _iops_subceiling_check dev-gates.slice
else
  warn "dev-gates.slice cgroup absent (no member started yet — expected until a gate/lane container is created with --cgroup-parent=dev-gates.slice)"
fi

echo "== dev-buildkitd.slice effective values (host-managed BuildKit worker) =="
BUILDKITD_PATH="$(_slice_path dev-buildkitd.slice)"
if [ -d "$BUILDKITD_PATH" ]; then
  for f in memory.high memory.max memory.swap.max cpu.weight io.weight io.bfq.weight; do
    printf '  %-14s %s\n' "$f" "$(cat "$BUILDKITD_PATH/$f" 2>/dev/null)"
  done
  for prop_label in "memory.max:DEV_BUILDKITD_MEMORY_MAX" "memory.high:DEV_BUILDKITD_MEMORY_HIGH"; do
    prop="${prop_label%%:*}"; var="${prop_label#*:}"
    eff="$(cat "$BUILDKITD_PATH/$prop" 2>/dev/null)"
    cfg="${!var:-}"
    if [ -z "$cfg" ]; then
      warn "$var not set in $CONF -- dev-buildkitd.slice's effective $prop ($eff) is whatever the installed unit carries, unchecked"
    else
      cfg_bytes="$(_bytes_of "$cfg")"
      if [ "$cfg_bytes" = "?" ]; then
        warn "$var=$cfg is not byte-comparable (percentage or non-size form) -- effective $prop=$eff not checked"
      elif [ "$eff" = "$cfg_bytes" ]; then
        ok "dev-buildkitd.slice $prop=$eff matches $var=$cfg"
      else
        fail "dev-buildkitd.slice $prop=$eff but $var=$cfg (${cfg_bytes} bytes) -- config says bounded, the kernel's effective value differs (a stale unit or partial install). Re-run install.sh."
      fi
    fi
  done
  _cpu_quota_check dev-buildkitd.slice DEV_BUILDKITD_CPU_QUOTA
  _swap_max_check dev-buildkitd.slice DEV_BUILDKITD_MEMORY_SWAP_MAX
  _zswap_check dev-buildkitd.slice DEV_BUILDKITD_ZSWAP_WRITEBACK
  _iops_subceiling_check dev-buildkitd.slice
else
  warn "dev-buildkitd.slice cgroup absent (no member started yet — expected until mdt-buildkitd.service starts its container)"
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
# socket. install.sh installs + applies units/mdt-cgprofile.conf; this
# is this directory's own fail-open invariant, so like MemoryMin above it
# gets a hard `fail`, not a printout.
CGPROFILE_DIR=/run/cgprofile
if [ -d "$CGPROFILE_DIR" ]; then
  cg_mode=$(stat -c '%a' "$CGPROFILE_DIR" 2>/dev/null)
  cg_owner=$(stat -c '%U:%G' "$CGPROFILE_DIR" 2>/dev/null)
  if [ "$cg_mode" = "770" ] && [ "$cg_owner" = "root:docker" ]; then
    ok "$CGPROFILE_DIR exists, mode 0770, owner root:docker"
  else
    fail "$CGPROFILE_DIR exists but mode=${cg_mode:-?} owner=${cg_owner:-?} (expected 770 root:docker) -- /etc/tmpfiles.d/mdt-cgprofile.conf missing/stale or not applied; re-run 'systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf' or install.sh"
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
  fail "$CGPROFILE_DIR does not exist -- /etc/tmpfiles.d/mdt-cgprofile.conf missing or not applied; re-run install.sh (or 'sudo systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf' directly)"
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
  || warn "mdt-host-slices.timer not enabled — this sweep is the BACKSTOP; without it, only whatever mdt-io-cap-watcher.service already caught stays capped"
systemctl is-active mdt-io-cap-watcher.service >/dev/null 2>&1 \
  && ok "mdt-io-cap-watcher.service active (docker-events reactive per-container IO caps)" \
  || warn "mdt-io-cap-watcher.service not active — buildx_buildkit_*/test-runner/devcontainer containers only get capped on the next mdt-host-slices.timer sweep, not instantly on start"
systemctl is-active mdt-dev-cap-watcher.service >/dev/null 2>&1 \
  && ok "mdt-dev-cap-watcher.service active (inotify reactive per-container memory/swap caps)" \
  || warn "mdt-dev-cap-watcher.service not active — unlabelled containers in dev-interactive.slice/dev-background.slice/dev-gates.slice only get a default MemoryMax on the next sweep, not instantly on start"

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
