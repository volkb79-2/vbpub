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

# A retired *_RESERVE_* key is refused, never read: the message names the new key.
# The refusal logic is mdt_capacity.py's (installed beside this script), the one
# implementation the installer and the wizard share.
_CAPACITY_PY="${CAPACITY_PY:-$(dirname "$0")/mdt_capacity.py}"
if [ -f "$CONF" ] && [ -f "$_CAPACITY_PY" ]; then
  python3 "$_CAPACITY_PY" refuse-renamed "$CONF" || { echo "result: 1 failure(s): $CONF holds retired keys"; exit 1; }
elif [ -f "$CONF" ]; then
  warn "$_CAPACITY_PY missing — the retired-key refusal and the capacity checks were skipped; re-run install.sh"
fi
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
# Shared IO-governance helpers (swap coverage); installed beside this script.
log(){ :; }
# shellcheck source=./mdt-container-io-caps.lib.sh
. "$(dirname "$0")/mdt-container-io-caps.lib.sh"
IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
IO_BASELINE_TESTFILE="${IO_BASELINE_TESTFILE:-/var/lib/mdt/iocost-coef-fio.testfile}"

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
    || warn "$slice memory.zswap.writeback=$val (expected $want from $var — run mdt-dev-governance-reconcile.sh)"
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
      info "$var empty — CPUQuota derived at install time (nproc minus MDT_EXCLUDED_CPU_CORES, minus DEV_TIER_EXCLUDED_CPU_CORES for a tier), not independently re-derivable here"
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
# mdt-dev-governance-reconcile.sh just measured for dev.slice itself) applied at
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
    info "$slice IOPS sub-ceiling: riops/wiops not numeric in io.max yet (mdt-dev-governance-reconcile.sh has not run, or no IO baseline) — skipped"
    return
  fi
  want_r=$(( dev_riops * pct / 100 ))
  want_w=$(( dev_wiops * pct / 100 ))
  # The reconcile applies min(pct% of dev.slice, the tier's ABSOLUTE pin DEV_<TIER>_IO_{RIOPS,WIOPS}_MAX): compute
  # the same min() here, or a correct state with a pin below the sub-ceiling would be reported as wrong.
  local tier pin_r="" pin_w="" basis="${pct}% of dev.slice's own riops=$dev_riops/wiops=$dev_wiops"
  case "$slice" in dev-gates.slice) tier=GATES ;; dev-buildkitd.slice) tier=BUILDKITD ;; *) tier="" ;; esac
  if [ -n "$tier" ]; then
    pin_r=$(_mdt_tier_pin "$tier" RIOPS 2>/dev/null) || pin_r=""
    pin_w=$(_mdt_tier_pin "$tier" WIOPS 2>/dev/null) || pin_w=""
  fi
  if [ -n "$pin_r" ] && [ "$pin_r" -lt "$want_r" ]; then want_r=$pin_r; basis="min(${pct}% of dev.slice's own riops=$dev_riops/wiops=$dev_wiops, the absolute ${tier} pin)"; fi
  if [ -n "$pin_w" ] && [ "$pin_w" -lt "$want_w" ]; then want_w=$pin_w; basis="min(${pct}% of dev.slice's own riops=$dev_riops/wiops=$dev_wiops, the absolute ${tier} pin)"; fi
  if [ "$sub_riops" -eq "$want_r" ] && [ "$sub_wiops" -eq "$want_w" ]; then
    ok "$slice IOPS sub-ceiling riops=$sub_riops/wiops=$sub_wiops matches $basis"
  else
    warn "$slice IOPS sub-ceiling riops=$sub_riops/wiops=$sub_wiops does not match $basis (want riops=$want_r/wiops=$want_w) — run mdt-dev-governance-reconcile.sh"
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
    || warn "no io.max on dev.slice — neither statics nor runtime caps applied (list what is really in force: mdt-cgroup-io-limits.sh dev.slice --systemd)"
  # D6: swap-out IO is charged to the page's memcg, so dev.slice's io.max
  # bounds it only if the disk holding the swap has an entry.
  _swap_uncapped=$(_mdt_uncapped_swap_devices 2>/dev/null)
  if [ -n "$_swap_uncapped" ]; then
    while read -r _sd _sm; do
      if [ "$_sm" = unresolved ]; then
        warn "swap device $_sd: whole disk could not be resolved — cannot confirm its IO is capped"
      else
        warn "UNCAPPED SWAP: $_sd lies on disk $_sm which has NO dev.slice io.max entry — a dev container that swaps can saturate it"
      fi
    done <<<"$_swap_uncapped"
  elif [ "$(_mdt_active_swap_count)" -gt 0 ]; then
    ok "every active swap disk has a dev.slice io.max entry"
  else
    info "no active swap — nothing to cap (swap-IO coverage is moot)"
  fi
  printf '  %-14s %s\n' io.bfq.weight "$(cat "$DEV_PATH/io.bfq.weight" 2>/dev/null)"
  _cpu_quota_check dev.slice DEV_CPU_QUOTA
  _swap_max_check dev.slice DEV_SWAP_MAX
  if [ "$(_mdt_host_role)" = dedicated-dev ]; then
    # HOST_ROLE=dedicated-dev (MDT-TESTHOST-MEM): the derived values, read through the one implementation.
    if _ded_mem=$(python3 "$_CAPACITY_PY" dedicated-memory "$CONF" --meminfo /proc/meminfo 2>&1); then
      for prop_label in "memory.high:DEDICATED_MEM_HIGH" "memory.max:DEDICATED_MEM_MAX"; do
        prop="${prop_label%%:*}"; want="$(printf '%s\n' "$_ded_mem" | sed -n "s/^${prop_label#*:}=//p")"
        eff="$(cat "$DEV_PATH/$prop" 2>/dev/null)"
        [ -n "$want" ] || want=max
        if [ "$eff" = "$want" ]; then ok "dev.slice $prop=$eff matches the HOST_ROLE=dedicated-dev derivation"
        else fail "dev.slice $prop=$eff but the HOST_ROLE=dedicated-dev derivation says $want (DEV_HOST_MEMORY_KEEP / pins). Re-run install.sh."; fi
      done
      # MDT-TESTHOST-MEM-2: dev-gates / dev-background yield first: MemoryHigh/Max = dev.slice's minus the protected tiers' MemoryLow.
      for _tier_label in "dev-gates.slice:GATES" "dev-background.slice:BACKGROUND"; do
        _tier_unit="${_tier_label%%:*}"; _tier_key="${_tier_label#*:}"; _tier_dir="$(_slice_path "$_tier_unit")"
        [ -d "$_tier_dir" ] || continue
        for prop_label in "memory.high:HIGH" "memory.max:MAX"; do
          prop="${prop_label%%:*}"; want="$(printf '%s\n' "$_ded_mem" | sed -n "s/^DEDICATED_${_tier_key}_${prop_label#*:}=//p")"
          eff="$(cat "$_tier_dir/$prop" 2>/dev/null)"
          [ -n "$want" ] || want=max
          if [ "$eff" = "$want" ]; then ok "$_tier_unit $prop=$eff matches the HOST_ROLE=dedicated-dev derivation (dev.slice minus the protected tiers' MemoryLow)"
          else fail "$_tier_unit $prop=$eff but the HOST_ROLE=dedicated-dev derivation says $want (dev.slice minus the protected tiers' MemoryLow, or a DEV_${_tier_key}_MEMORY_${prop_label#*:} pin). Re-run install.sh."; fi
        done
        # AD2-0: memory.max = dev.slice memory.high (checked above), memory.swap.max = dev.slice's minus the protected Lows' sum.
        _d_swap="$(cat "$DEV_PATH/memory.swap.max" 2>/dev/null)"; _t_swap="$(cat "$_tier_dir/memory.swap.max" 2>/dev/null)"
        if [ "${_d_swap:-max}" = max ]; then
          info "$_tier_unit memory.swap.max=$_t_swap (dev.slice has no swap ceiling: nothing to derive from)"
        elif _ded_swap=$(python3 "$_CAPACITY_PY" dedicated-memory "$CONF" --meminfo /proc/meminfo --dev-swap "$_d_swap" 2>&1); then
          _want_swap="$(printf '%s\n' "$_ded_swap" | sed -n 's/^DEDICATED_LOAD_SWAP=//p')"
          _cfg_swap_var="DEV_${_tier_key}_MEMORY_SWAP_MAX"
          if [ -n "${!_cfg_swap_var:-}" ] && [ "${!_cfg_swap_var}" != auto ]; then
            info "$_tier_unit memory.swap.max=$_t_swap ($_cfg_swap_var is a pin: not compared with the derivation)"
          elif [ "$_t_swap" = "$_want_swap" ]; then
            ok "$_tier_unit memory.swap.max=$_t_swap = dev.slice's $_d_swap minus the protected tiers' MemoryLow sum (AD2-0)"
          else
            fail "$_tier_unit memory.swap.max=$_t_swap but the HOST_ROLE=dedicated-dev derivation says $_want_swap (dev.slice's $_d_swap minus the protected tiers' MemoryLow sum). Re-run install.sh."
          fi
        else
          warn "HOST_ROLE=dedicated-dev load-tier swap derivation failed: $_ded_swap"
        fi
        _t_high="$(cat "$_tier_dir/memory.high" 2>/dev/null)"; _d_high="$(cat "$DEV_PATH/memory.high" 2>/dev/null)"
        if [ "$_t_high" != max ] && [ "$_d_high" != max ] && [ "$_t_high" -lt "$_d_high" ] 2>/dev/null; then
          ok "$_tier_unit memory.high=$_t_high is below dev.slice memory.high=$_d_high (the load yields before the protected tiers)"
        else warn "$_tier_unit memory.high=$_t_high is not below dev.slice memory.high=$_d_high: the protected tiers' MemoryLow is not shielded from this tier (pin or omitted?)"; fi
      done
    else
      warn "HOST_ROLE=dedicated-dev memory derivation failed: $_ded_mem"
    fi
    _sl_low="$(cat "$CG/system.slice/memory.low" 2>/dev/null)"
    if [ -n "$_sl_low" ] && [ "$_sl_low" != 0 ]; then ok "system.slice memory.low=$_sl_low (host memory protected from the dev tier)"
    else warn "system.slice memory.low is 0: the host's own memory is not protected from the dev tier (re-run install.sh)"; fi
    # The keep must cover the host's working set (README "Sizing the keep"): above it the host's pages are reclaimable.
    _keep_b="$(printf '%s\n' "${_ded_mem:-}" | sed -n 's/^DEDICATED_MEM_KEEP=//p')"
    _sys_cur="$(cat "$CG/system.slice/memory.current" 2>/dev/null)"
    if [ -n "$_keep_b" ] && [ -n "$_sys_cur" ]; then
      if [ "$_sys_cur" -gt "$_keep_b" ] 2>/dev/null; then
        warn "system.slice memory.current=$_sys_cur exceeds DEV_HOST_MEMORY_KEEP=$_keep_b: the part above the keep is reclaimable by the dev tier and can thrash; raise DEV_HOST_MEMORY_KEEP and re-run install.sh"
      else ok "system.slice memory.current=$_sys_cur is within DEV_HOST_MEMORY_KEEP=$_keep_b"; fi
    fi
    if [ -e /etc/systemd/system/ssh.service.d/50-mdt-dedicated-dev.conf ]; then
      fail "ssh.service.d/50-mdt-dedicated-dev.conf exists: sshd would hand OOMScoreAdjust=-900 to every login session after its next restart; re-run install.sh (it removes the file)"
    fi
  fi
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
    if [ "$(_mdt_host_role)" = dedicated-dev ]; then
      # HOST_ROLE=dedicated-dev: gates' value is derived (dev.slice minus the protected tiers' Low) and checked in the dev.slice block above.
      info "dev-gates.slice $prop=$eff (HOST_ROLE=dedicated-dev: derived, verified against the derivation under dev.slice)"
      continue
    fi
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
    if [ "$(_mdt_host_role)" = dedicated-dev ]; then
      # HOST_ROLE=dedicated-dev (MDT-TESTHOST-MEM): a tier carries NO memory ceiling of its own; dev.slice is the bound.
      if [ "$eff" = max ]; then ok "dev-buildkitd.slice $prop=max (HOST_ROLE=dedicated-dev: no per-tier ceiling)"
      else fail "dev-buildkitd.slice $prop=$eff but HOST_ROLE=dedicated-dev means no per-tier memory ceiling. Re-run install.sh."; fi
      continue
    fi
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
if [ -d "$CGPROFILE_DIR" ] && [ ! -L "$CGPROFILE_DIR" ]; then
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
  # installed. The control socket is the only carrier.
  if [ -S "$CGPROFILE_DIR/ctl.sock" ]; then
    info "daemon socket available ($CGPROFILE_DIR/ctl.sock)"
  else
    info "daemon socket absent -- cgpd.service may not be installed or has not started yet (cgpctl doctor names the cause)"
  fi
else
  fail "$CGPROFILE_DIR is missing or is not a real directory -- /etc/tmpfiles.d/mdt-cgprofile.conf missing or not applied; re-run install.sh (or 'sudo systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf' directly)"
fi

check_cgprofile_observer() {
  local control_dir="${CGPROFILE_DIR:-/run/cgprofile}"
  local observer_dir="${CGPROFILE_OBSERVER_DIR:-$control_dir/observer}"
  local tmpfiles_file="${CGPROFILE_OBSERVER_TMPFILES_FILE:-/etc/tmpfiles.d/mdt-cgprofile-observer.conf}"
  local sysusers_file="${CGPROFILE_OBSERVER_SYSUSERS_FILE:-/etc/sysusers.d/mdt-cgprofile.conf}"
  local docker_entry="" observer_entry="" docker_gid="" observer_gid=""
  local metadata="" tmpfiles_rules="" sysusers_rules=""
  local effective_tmpfiles="" effective_sysusers=""
  local active_observer_tmpfiles="" active_observer_sysusers=""

  if [ -L "$observer_dir" ] || [ ! -d "$observer_dir" ]; then
    fail "$observer_dir is missing or is not a real directory; resolve any existing path drift, then run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  if ! docker_entry="$(getent group docker 2>/dev/null)"; then
    fail "cannot resolve the host docker group while checking $observer_dir"
    return
  fi
  if ! observer_entry="$(getent group cgprofile-observer 2>/dev/null)"; then
    fail "cannot resolve cgprofile-observer; run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  IFS=: read -r _ _ docker_gid _ <<< "$docker_entry"
  IFS=: read -r _ _ observer_gid _ <<< "$observer_entry"
  if [[ ! "$docker_gid" =~ ^[1-9][0-9]*$ || ! "$observer_gid" =~ ^[1-9][0-9]*$ ]]; then
    fail "docker or cgprofile-observer has an invalid GID while checking $observer_dir"
    return
  fi
  if [ "$docker_gid" = "$observer_gid" ]; then
    fail "cgprofile-observer GID $observer_gid aliases docker; resolve the host group collision before observer lanes can run"
    return
  fi
  metadata="$(stat -c '%u %g %a' "$observer_dir" 2>/dev/null || true)"
  if [ "$metadata" != "0 $observer_gid 751" ]; then
    fail "$observer_dir has metadata '${metadata:-unreadable}' (expected 0 $observer_gid 751); resolve the path drift, then rerun 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  if [ -L "$tmpfiles_file" ] || [ ! -f "$tmpfiles_file" ]; then
    fail "$tmpfiles_file is missing or unsafe; run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  if [ -L "$sysusers_file" ] || [ ! -f "$sysusers_file" ]; then
    fail "$sysusers_file is missing or unsafe; run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  tmpfiles_rules="$(awk 'NF && $1 !~ /^#/ { print }' "$tmpfiles_file")"
  sysusers_rules="$(awk 'NF && $1 !~ /^#/ { print }' "$sysusers_file")"
  if [ "$tmpfiles_rules" != 'd /run/cgprofile/observer 0751 root cgprofile-observer -' ]; then
    fail "$tmpfiles_file does not contain exactly the required observer tmpfiles rule; run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  if [ "$sysusers_rules" != 'g cgprofile-observer - - -' ]; then
    fail "$sysusers_file does not contain exactly the required observer group rule; run 'sudo ./host-setup/install.sh --observer-only' from the vbpub root"
    return
  fi
  if ! effective_tmpfiles="$(systemd-tmpfiles --cat-config --no-pager 2>/dev/null)"; then
    fail "cannot read the merged systemd tmpfiles configuration while checking $observer_dir"
    return
  fi
  active_observer_tmpfiles="$(awk 'NF && $1 !~ /^#/ && $2 == "/run/cgprofile/observer" { print }' <<< "$effective_tmpfiles")"
  if [ "$active_observer_tmpfiles" != 'd /run/cgprofile/observer 0751 root cgprofile-observer -' ]; then
    fail "effective tmpfiles rules for /run/cgprofile/observer are not exactly the required rule; remove competing active rules, then rerun host-setup --check"
    return
  fi
  if ! effective_sysusers="$(systemd-sysusers --cat-config --no-pager 2>/dev/null)"; then
    fail "cannot read the merged systemd sysusers configuration while checking $observer_dir"
    return
  fi
  active_observer_sysusers="$(awk 'NF && $1 !~ /^#/ && $2 == "cgprofile-observer" { print }' <<< "$effective_sysusers")"
  if [ "$active_observer_sysusers" != 'g cgprofile-observer - - -' ]; then
    fail "effective sysusers rules for cgprofile-observer are not exactly the required rule; remove competing active rules, then rerun host-setup --check"
    return
  fi
  ok "$observer_dir is root:$observer_gid 0751, distinct from docker; observer sysusers/tmpfiles rules are present"
}

echo "== topos socket directory (/run/topos) =="
# The devcontainer templates bind this directory read-only via runArgs -v, which
# auto-creates a missing source as root:root 0755. That keeps old hosts starting
# but gives group docker no access, so a wrong owner/mode is a hard fail here.
TOPOS_DIR=/run/topos
if [ -d "$TOPOS_DIR" ] && [ ! -L "$TOPOS_DIR" ]; then
  tp_mode=$(stat -c '%a' "$TOPOS_DIR" 2>/dev/null)
  tp_owner=$(stat -c '%U:%G' "$TOPOS_DIR" 2>/dev/null)
  if [ "$tp_mode" = "750" ] && [ "$tp_owner" = "root:docker" ]; then
    ok "$TOPOS_DIR exists, mode 0750, owner root:docker"
  else
    fail "$TOPOS_DIR exists but mode=${tp_mode:-?} owner=${tp_owner:-?} (expected 750 root:docker; root:root 755 is Docker's auto-created source) -- /etc/tmpfiles.d/mdt-topos.conf missing/stale or not applied; re-run 'systemd-tmpfiles --create /etc/tmpfiles.d/mdt-topos.conf' or install.sh"
  fi
  if [ -S "$TOPOS_DIR/topos.sock" ]; then
    info "topos daemon socket available ($TOPOS_DIR/topos.sock)"
  else
    info "topos daemon socket absent -- the topos service may not be installed or running (host-setup installs no topos service)"
  fi
else
  fail "$TOPOS_DIR is missing or is not a real directory -- /etc/tmpfiles.d/mdt-topos.conf missing or not applied; re-run install.sh (or 'sudo systemd-tmpfiles --create /etc/tmpfiles.d/mdt-topos.conf' directly)"
fi

echo "== cgprofile resource observer (/run/cgprofile/observer, contract 2) =="
check_cgprofile_observer

# The native cgprofile service (MDT-055). CGPROFILE_ENABLE=1 asserts the pinned install: version directory,
# MANIFEST digest, `current`, rendered config and unit files with no drift, systemd-analyze verify, the
# service and slice active, both sockets (the control socket in the control directory's group), and
# `cgpctl doctor --json` without a `fail` (an `overrides` warning is reported, never asserted against).
# CGPROFILE_ENABLE=0/absent only discloses what is on the host. The paths are overridable for the tests.
check_cgprofile_service() {
  local opt="${CGP_OPT:-/opt/cgprofile}" etc="${CGP_ETC:-/etc/cgprofile}" unit_dir="${CGP_UNIT_DIR:-/etc/systemd/system}"
  local run_dir="${CGPROFILE_DIR:-/run/cgprofile}" systemctl_bin="${CGP_SYSTEMCTL:-systemctl}"
  local analyze_bin="${CGP_ANALYZE:-systemd-analyze}" installer="${CGP_INSTALLER:-$(dirname "$0")/mdt-cgprofile-install.sh}"
  local pin="${CGPROFILE_WHEEL:-}" version digest current venv got json verdict dir_group sock_group unit
  if [ "${CGPROFILE_ENABLE:-0}" != 1 ]; then
    if [ -L "$opt/current" ]; then
      info "cgprofile $(readlink "$opt/current") is installed but CGPROFILE_ENABLE is not 1: host-setup leaves it alone"
    else
      info "cgprofile service not managed by host-setup (CGPROFILE_ENABLE is not 1)"
    fi
    return 0
  fi
  if ! [[ "$pin" =~ ^([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*)@sha256:([0-9a-f]{64})$ ]]; then
    fail "CGPROFILE_ENABLE=1 but CGPROFILE_WHEEL='$pin' is not <version>@sha256:<hex>; fix $CONF and re-run install.sh"
    return 0
  fi
  version="${BASH_REMATCH[1]}"; digest="${BASH_REMATCH[2]}"
  current="$(readlink "$opt/current" 2>/dev/null || true)"
  venv="$opt/$version/venv"
  if [ "$current" = "$version" ]; then
    ok "$opt/current -> $version (the pin)"
  else
    fail "$opt/current points at '${current:-nothing}', the pin is $version -- re-run install.sh (or install.sh --cgprofile-only)"
    [ -f "$opt/PENDING" ] && warn "a pending version is recorded ($(cat "$opt/PENDING")): the last upgrade was refused at the drain deadline"
    return 0
  fi
  if grep -qx "cgprofile_sha256=$digest" "$opt/$version/MANIFEST" 2>/dev/null; then
    ok "$opt/$version/MANIFEST records the pinned wheel digest"
  else
    fail "$opt/$version/MANIFEST is missing or its cgprofile_sha256 differs from the pin"
  fi
  got="$("$venv/bin/cgpd" --version 2>&1 || true)"
  case "$got" in
    *"$version"*) ok "installed cgpd reports $version" ;;
    *) fail "installed cgpd reports '${got:-nothing}', the pin is $version" ;;
  esac
  if [ -f "$installer" ]; then
    if bash "$installer" --config "$CONF" --print-config 2>/dev/null | cmp -s - "$etc/cgpd.toml"; then
      ok "$etc/cgpd.toml equals the config rendered from host-setup.env"
    else
      fail "$etc/cgpd.toml differs from what host-setup.env renders (config drift) -- re-run install.sh"
    fi
  else
    warn "$installer missing -- the config drift check was skipped"
  fi
  if "$venv/bin/cgpd" config check --file "$etc/cgpd.toml" >/dev/null 2>&1; then
    ok "cgpd config check accepts $etc/cgpd.toml"
  else
    fail "cgpd config check refuses $etc/cgpd.toml"
  fi
  if "$venv/bin/cgpd" unit check --wheel-venv "/opt/cgprofile/current/venv" --config "$etc/cgpd.toml" \
    --installed "$unit_dir/cgprofile.slice" "$unit_dir/cgpd.service" >/dev/null 2>&1; then
    ok "cgprofile.slice and cgpd.service equal the wheel's rendered units (no unit drift)"
  else
    fail "cgprofile.slice / cgpd.service differ from 'cgpd unit print' (unit drift) -- re-run install.sh"
  fi
  if command -v "$analyze_bin" >/dev/null 2>&1; then
    if "$analyze_bin" verify "$unit_dir/cgprofile.slice" "$unit_dir/cgpd.service" >/dev/null 2>&1; then
      ok "systemd-analyze verify is clean on both units"
    else
      fail "systemd-analyze verify reports problems with cgprofile.slice / cgpd.service"
    fi
  else
    warn "systemd-analyze unavailable -- the unit verification was skipped"
  fi
  for unit in cgprofile.slice cgpd.service; do
    if "$systemctl_bin" is-active --quiet "$unit" 2>/dev/null; then ok "$unit is active"; else fail "$unit is not active"; fi
  done
  if [ -S "$run_dir/ctl.sock" ]; then
    dir_group="$(stat -c '%G' "$run_dir" 2>/dev/null || echo '?')"
    sock_group="$(stat -c '%G' "$run_dir/ctl.sock" 2>/dev/null || echo '?')"
    if [ "$dir_group" = "$sock_group" ]; then ok "ctl.sock exists in group $sock_group (the control directory's group)"
    else fail "ctl.sock group is $sock_group, the control directory's group is $dir_group"; fi
  else
    fail "$run_dir/ctl.sock is not a socket -- cgpd is not serving"
  fi
  if [ -S "$run_dir/observer/resource-observer.sock" ]; then ok "observer socket exists"; else fail "$run_dir/observer/resource-observer.sock is not a socket"; fi
  json="$("$venv/bin/cgpctl" doctor --json 2>/dev/null)"
  if [ -n "$json" ]; then
    verdict="$(printf '%s' "$json" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for c in doc["checks"]:
    if c["status"] == "warn" and c["name"] == "overrides":
        print("WARN " + c["summary"])
bad = [c["name"] for c in doc["checks"] if c["status"] == "fail"]
print("FAIL " + ", ".join(bad) if bad else "PASS")
' 2>&1)"
    while read -r kind rest; do
      case "$kind" in
        WARN) warn "cgpctl doctor: $rest" ;;
        FAIL) fail "cgpctl doctor reports failing checks: $rest" ;;
        PASS) ok "cgpctl doctor reports no failing check" ;;
        *) fail "cgpctl doctor output was not understood: $kind $rest" ;;
      esac
    done <<< "$verdict"
  else
    fail "cgpctl doctor produced no answer"
  fi
  if json="$("$venv/bin/cgpctl" status --json 2>/dev/null)" && printf '%s' "$json" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
sys.exit(0 if doc["daemon"]["version"] == sys.argv[1] and doc["config"].get("last_reload_error") is None else 1)
' "$version"; then
    ok "the running daemon reports version $version with config.last_reload_error null"
  else
    fail "the running daemon does not report version $version with a clean config (cgpctl status --json)"
  fi
}

echo "== cgprofile native service (MDT-055) =="
check_cgprofile_service

echo "== optional DAMON_STAT host policy =="
case "${DAMON_STAT_DISABLE_DEFAULT:-}" in
  yes)
    if [ ! -f /etc/tmpfiles.d/mdt-damon-stat.conf ] \
      || ! grep -Fxq 'w /sys/module/damon_stat/parameters/enabled - - - - N' /etc/tmpfiles.d/mdt-damon-stat.conf; then
      fail "DAMON_STAT disable policy is selected but /etc/tmpfiles.d/mdt-damon-stat.conf is missing or stale; re-run install.sh"
    fi
    if [ -e /sys/module/damon_stat/parameters/enabled ] \
      && [ "$(cat /sys/module/damon_stat/parameters/enabled 2>/dev/null)" != N ]; then
      fail "DAMON_STAT disable policy is selected but the live kernel parameter is not N; re-run install.sh"
    fi
    ok "DAMON_STAT is disabled by MDT policy when the kernel exposes the parameter"
    ;;
  no)
    if [ -e /etc/tmpfiles.d/mdt-damon-stat.conf ]; then
      fail "DAMON_STAT policy is set to keep the kernel default but MDT's disable override remains; re-run install.sh"
    else
      ok "DAMON_STAT remains at the kernel's configured default"
    fi
    ;;
  "")
    fail "DAMON_STAT policy is missing from $CONF; re-run install.sh --wizard or add DAMON_STAT_DISABLE_DEFAULT from host-setup.env.example"
    ;;
  *)
    fail "DAMON_STAT_DISABLE_DEFAULT must be yes or no (got '$DAMON_STAT_DISABLE_DEFAULT')"
    ;;
esac

echo "== dedicated Docker API socket and pending daemon restart (MDT-037) =="
# BEGIN docker-api-socket (tests extract this block; the MDT_DOCKER_* overrides exist for them)
_docker_api_sock="${MDT_DOCKER_API_SOCK:-/run/docker-api/docker.sock}"
_docker_dropin="${MDT_DOCKER_DROPIN:-/etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf}"
_docker_daemon_json="${MDT_DOCKER_DAEMON_JSON:-/etc/docker/daemon.json}"
if [ -f "$_docker_dropin" ]; then
  ok "$_docker_dropin installed"
else
  fail "$_docker_dropin missing — new devcontainers bind-mount /run/docker-api and will not start; re-run install.sh"
fi
if [ -S "$_docker_api_sock" ]; then
  _sock_meta=$(stat -c '%U:%G %a' "$_docker_api_sock" 2>/dev/null || echo "")
  if [ "$_sock_meta" = "root:docker 660" ]; then
    ok "$_docker_api_sock listening, root:docker 0660"
  else
    fail "$_docker_api_sock is '$_sock_meta', expected root:docker 660"
  fi
else
  fail "$_docker_api_sock is not a listening socket — restart docker.socket and docker.service in a maintenance window (the setup was applied but not activated)"
fi
# Same test install.sh uses: daemon.json newer than the running dockerd's start (unknown start counts as pending).
_dpid=$(systemctl show -p MainPID --value docker.service 2>/dev/null || true)
_dstarted=""
if [ -n "${MDT_DOCKER_STARTED_EPOCH:-}" ]; then
  _dstarted="$MDT_DOCKER_STARTED_EPOCH"
elif [ -n "$_dpid" ] && [ "$_dpid" != 0 ]; then
  _dlstart=$(ps -o lstart= -p "$_dpid" 2>/dev/null || true)
  [ -n "$_dlstart" ] && _dstarted=$(date -d "$_dlstart" +%s 2>/dev/null || true)
fi
if [ -f "$_docker_daemon_json" ]; then
  _djmtime=$(stat -c %Y "$_docker_daemon_json" 2>/dev/null || echo 0)
  if [ -z "$_dstarted" ]; then
    warn "$_docker_daemon_json exists but docker.service's start time is unknown — a daemon restart may be pending"
  elif [ "$_djmtime" -gt "$_dstarted" ]; then
    warn "$_docker_daemon_json is newer than the running dockerd — a daemon restart is pending (systemctl restart docker.socket docker.service, in a window)"
  else
    ok "running dockerd is newer than $_docker_daemon_json (no restart pending)"
  fi
fi
# END docker-api-socket

echo "== Buildx guard confirmation =="
if [ "${BUILDX_ACCIDENTAL_CONTAINER_POLICY:-}" = terminate ]; then
  if _mdt_guard_confirmed /var/lib/mdt/guard-terminate.confirmed terminate "${DEV_BUILDKITD_IMAGE:-}"; then
    ok "policy=terminate has a valid confirmation record for this policy, image and host"
  else
    warn "policy=terminate has NO valid confirmation record (/var/lib/mdt/guard-terminate.confirmed): the guard runs report-only until install.sh --wizard confirms it"
  fi
else
  info "guard policy is ${BUILDX_ACCIDENTAL_CONTAINER_POLICY:-unset}: no terminate confirmation needed"
fi

echo "== docker-.scope.d default-limits backstop (D-G8) =="
if [ -f /etc/systemd/system/docker-.scope.d/50-default-limits.conf ]; then
  ok "backstop drop-in installed"
else
  warn "no docker-.scope.d/50-default-limits.conf — a typo'd/missing --cgroup-parent fails OPEN (unbounded), unmitigated"
fi
# Independent of the slice: a missing/stale baseline means the caps in force are
# the tight unit statics, whether or not any member has started.
if [ -f "$IO_BASELINE_ENV" ] && [ -x /usr/local/sbin/mdt-io-baseline.py ]; then
  if baseline_status=$(python3 /usr/local/sbin/mdt-io-baseline.py --check-results \
      --output "$IO_BASELINE_ENV" --testfile "$IO_BASELINE_TESTFILE" 2>&1); then
    ok "current io.cost baseline ($IO_BASELINE_ENV; device and target identity match)"
  else
    warn "no current io.cost baseline ($IO_BASELINE_ENV): $baseline_status"
  fi
elif [ -f "$IO_BASELINE_ENV" ]; then
  warn "baseline exists but its validator is not installed ($IO_BASELINE_ENV) — run mdt-io-baseline.py"
else
  warn "no baseline ($IO_BASELINE_ENV) — tight unit statics in force; run mdt-io-baseline.py"
fi

echo "== IO scheduler and weight mechanism =="
for f in /sys/block/*/queue/scheduler; do
  d=${f#/sys/block/}; d=${d%%/*}
  case "$d" in loop*|sr*|ram*|zram*) continue ;; esac
  printf '  %-8s %s\n' "$d" "$(cat "$f" 2>/dev/null)"
done
# BEGIN io-sched-check (extracted verbatim by tests/test_hs07.py)
# The weight mechanism ACTIVE on the root disk, derived from the live files and never assumed (MDT-HS-07):
# io.cost enabled (the root io.cost.qos line for the disk says enable=1) enforces io.weight; the bfq scheduler
# enforces io.bfq.weight; neither = IOWeight is inert and only the io.max caps enforce.
_check_cost_one() { # <label> <wanted-line> <live-file> <maj:min> <persisted 0|1>
  local label="$1" want="$2" file="$3" mm="$4" persisted="$5" live
  if [ ! -r "$file" ]; then
    warn "$label: could not read $file — cannot tell whether the intended setting is in force"
    return 0
  fi
  live=$(grep -m1 "^$mm " "$file" 2>/dev/null || true)
  if [ -z "$live" ]; then
    fail "$label MISMATCH: $file has no line for $mm, but host-setup.env intends: ${want#"$mm "} — re-run install.sh"
  elif ! _mdt_iocost_match "$want" "$live"; then
    fail "$label MISMATCH: live '${live#"$mm "}', host-setup.env intends '${want#"$mm "}' — re-run install.sh (read-back source: $file)"
  elif [ "$persisted" = 1 ]; then
    ok "$label verified: ${live#"$mm "} (matches host-setup.env, re-applied at boot by mdt-io-cost.service)"
  else
    warn "$label = ${live#"$mm "} is LIVE ONLY, NOT PERSISTED: mdt-io-cost.service is not enabled, so a reboot loses it — re-run install.sh"
  fi
}
# the re-assert pause (MDT-HS-07): shown, never hidden
check_io_pause() {
  local msg rc
  msg=$(_mdt_io_pause_state); rc=$?
  case "$rc" in
    0) warn "re-assert PAUSED $msg — the scheduler, queue values, vm.dirty_* and io.cost are not being re-asserted (io.max caps still enforced); end it with: mdt-io-reassert-pause --clear" ;;
    2) warn "re-assert pause is ignored: $msg" ;;
    *) info "re-assert: active (no pause)" ;;
  esac
}
# the managed BuildKit: does the RUNNING service match the rendered unit + config? (H-66)
check_buildkitd_pending() {
  local why
  if ! systemctl is-active --quiet mdt-buildkitd.service 2>/dev/null; then
    info "mdt-buildkitd.service is not running; nothing to compare with the rendered files"
  elif why=$(_mdt_buildkitd_pending); then
    warn "mdt-buildkitd.service RESTART PENDING: $why — restart it when no build is running (systemctl restart mdt-buildkitd.service, or install.sh --restart-buildkitd)"
  else
    ok "mdt-buildkitd.service runs the rendered unit and config (start stamp matches)"
  fi
}
check_io_scheduler() {
  local sysroot="${MDT_SYSFS_ROOT:-/sys}" cgroot="${MDT_CGROUP_ROOT:-/sys/fs/cgroup}" ud="${MDT_UDEV_RULES_D:-/etc/udev/rules.d}"
  local node dev mm cur qos enable want persisted=0 sched rule after
  if ! node=$(_mdt_root_disk_node 2>&1) || ! mm=$(_mdt_root_disk_majmin 2>&1); then
    warn "root disk: could not derive the whole disk under / (${node:-}${mm:-}) — the weight mechanism and the scheduler/io.cost settings cannot be verified"
    return 0
  fi
  dev=${node#/dev/}
  cur=$(_mdt_io_sched_current "$dev" 2>/dev/null || true)
  qos=$(grep -m1 "^$mm " "$cgroot/io.cost.qos" 2>/dev/null || true)
  enable=$(printf '%s' "$qos" | sed -n 's/.*[[:space:]]enable=\([01]\).*/\1/p')
  if [ -z "$cur" ]; then
    warn "$dev: could not read the live scheduler ($sysroot/block/$dev/queue/scheduler) — the weight mechanism cannot be determined"
  elif [ "$enable" = 1 ] && [ "$cur" = bfq ]; then
    warn "$dev weight mechanism: io.cost is enabled AND the scheduler is bfq — both enforce weights (io.weight and io.bfq.weight); choose one (IO_ROOT_SCHEDULER / IO_COST_QOS)"
  elif [ "$enable" = 1 ]; then
    ok "$dev weight mechanism: io.cost (io.cost.qos enable=1, scheduler $cur) — io.weight 1..10000 is enforced"
  elif [ "$cur" = bfq ]; then
    ok "$dev weight mechanism: bfq (scheduler bfq) — io.bfq.weight is enforced; keep IOWeight <= 100"
  elif [ ! -r "$cgroot/io.cost.qos" ]; then
    warn "$dev: scheduler $cur, and could not read $cgroot/io.cost.qos — cannot tell whether io.cost enforces io.weight"
  else
    warn "$dev weight mechanism: NONE — scheduler $cur and io.cost not enabled (${qos:-no io.cost.qos line for $mm}); IOWeight is inert, only the io.max caps enforce"
  fi
  # scheduler against IO_ROOT_SCHEDULER
  sched="${IO_ROOT_SCHEDULER:-}"
  case "$sched" in
    "") info "$dev scheduler: not managed (IO_ROOT_SCHEDULER not set; a legacy BFQ rule may still apply)" ;;
    kernel)
      info "$dev scheduler: not managed (kernel; the kernel's choice is kept)"
      while IFS='|' read -r rule after; do
        [ -n "$rule" ] || continue
        warn "NOTICE: $rule (not installed by MDT) still assigns a scheduler to $dev on every udev change event although IO_ROOT_SCHEDULER=kernel"
      done < <(_mdt_io_foreign_sched_rules "$dev" "$MDT_IO_SCHED_UDEV_NAME")
      ;;
    *)
      if [ -z "$cur" ]; then
        warn "$dev scheduler: could not read the live value — cannot verify IO_ROOT_SCHEDULER=$sched"
      elif [ "$cur" != "$sched" ]; then
        fail "$dev scheduler MISMATCH: live $cur, host-setup.env says $sched — re-run install.sh"
      elif grep -qF "ATTR{queue/scheduler}=\"$sched\"" "$ud/$MDT_IO_SCHED_UDEV_NAME" 2>/dev/null; then
        ok "$dev scheduler = $cur (matches host-setup.env, persisted in $ud/$MDT_IO_SCHED_UDEV_NAME)"
      else
        warn "$dev scheduler = $cur is LIVE ONLY, NOT PERSISTED: $ud/$MDT_IO_SCHED_UDEV_NAME does not carry it, so a reboot loses it — re-run install.sh"
      fi
      while IFS='|' read -r rule after; do
        [ -n "$rule" ] || continue
        if [ "$after" = 1 ]; then
          warn "NOTICE: $rule (not installed by MDT) also sets the scheduler for $dev and sorts after MDT's $MDT_IO_SCHED_UDEV_NAME, so it overrides it on every udev change event"
        else
          warn "NOTICE: $rule (not installed by MDT) also sets the scheduler for $dev; it sorts before MDT's rule, which wins, but removing MDT's rule would let it apply"
        fi
      done < <(_mdt_io_foreign_sched_rules "$dev" "$MDT_IO_SCHED_UDEV_NAME")
      ;;
  esac
  # io.cost model and QoS against IO_COST_MODEL / IO_COST_QOS
  systemctl is-enabled --quiet mdt-io-cost.service 2>/dev/null && persisted=1
  if want=$(_mdt_iocost_model_line "$mm"); then
    _check_cost_one "io.cost model" "$want" "$cgroot/io.cost.model" "$mm" "$persisted"
  else
    info "io.cost model: not managed (IO_COST_MODEL not set)"
  fi
  if want=$(_mdt_iocost_qos_line "$mm"); then
    _check_cost_one "io.cost qos" "$want" "$cgroot/io.cost.qos" "$mm" "$persisted"
  else
    info "io.cost qos: not managed (IO_COST_QOS not set)"
  fi
}
check_io_pause
check_buildkitd_pending
check_io_scheduler
# END io-sched-check

echo "== sweep service/timer =="
systemctl is-enabled mdt-dev-governance-reconcile.timer >/dev/null 2>&1 \
  && ok "mdt-dev-governance-reconcile.timer enabled ($(systemctl show mdt-dev-governance-reconcile.timer -p NextElapseUSecRealtime --value 2>/dev/null))" \
  || warn "mdt-dev-governance-reconcile.timer not enabled — this sweep is the BACKSTOP; without it, only whatever mdt-container-io-events-watcher.service already caught stays capped"
systemctl is-active mdt-container-io-events-watcher.service >/dev/null 2>&1 \
  && ok "mdt-container-io-events-watcher.service active (docker-events reactive per-container IO caps)" \
  || warn "mdt-container-io-events-watcher.service not active — governed Docker scopes and out-of-tree Buildx workers only get capped on the next mdt-dev-governance-reconcile.timer sweep, not instantly on start"
# BEGIN guard-status (extracted verbatim by tests/test_guard_events.py)
check_buildkit_guard() {
  local unit=mdt-buildkit-guard.service load active sub restarts jrc malformed out
  load=$(systemctl show "$unit" -p LoadState --value 2>/dev/null) || load=""
  if [ "$load" != loaded ]; then
    info "$unit is not installed (LoadState=${load:-unknown}) — the BuildKit guard is not enforcing anything on this host"
    return 0
  fi
  active=$(systemctl show "$unit" -p ActiveState --value 2>/dev/null)
  sub=$(systemctl show "$unit" -p SubState --value 2>/dev/null)
  restarts=$(systemctl show "$unit" -p NRestarts --value 2>/dev/null)
  case "$restarts" in
    ''|*[!0-9]*) warn "$unit: could not read NRestarts (got '${restarts}')"; restarts="" ;;
  esac
  if [ "$active" != active ]; then
    warn "$unit is installed but NOT running (ActiveState=${active:-unknown} SubState=${sub:-unknown}, ${restarts:-?} restarts) — created containers are not being checked"
  elif [ "$restarts" = 0 ]; then
    ok "$unit active, has not restarted"
  elif [ -n "$restarts" ]; then
    warn "$unit active but restarted ${restarts} times — a restart is a window in which created containers are not seen; read its journal (MDT-HS-03 crash-looped on Docker 29 events)"
  fi
  out=$(journalctl -u "$unit" --since "-1h" -q --no-pager 2>/dev/null); jrc=$?
  if [ "$jrc" -ne 0 ]; then
    warn "$unit: could not read its journal (journalctl exit $jrc) — malformed docker events in the last hour are unknown"
    return 0
  fi
  malformed=$(printf '%s\n' "$out" | grep -c 'malformed docker event')
  if [ "${malformed:-0}" = 0 ]; then
    ok "$unit logged no malformed docker events in the last hour"
  else
    warn "$unit logged ${malformed} malformed docker events in the last hour — the daemon's event shape may have changed"
  fi
}
check_buildkit_guard
# END guard-status
# Every build must reach mdt-managed whoever starts it (docs/BUILD-ARCHITECTURE.md "Making every
# build use mdt-managed"): the shim must be the plugin Docker resolves, /etc/environment must carry the
# MDT block, and a user with an EMPTY environment must resolve the managed remote builder.
check_buildx_default_builder() {
  local shim=/usr/local/lib/docker/cli-plugins/docker-buildx resolved probe_home out
  if [ ! -x "$shim" ]; then
    warn "docker-buildx shim $shim is not installed — only shells that read /etc/profile.d or /etc/environment select BUILDX_BUILDER=mdt-managed; VS Code's SSH server, cron and systemd units build on Docker's built-in builder (outside dev-buildkitd.slice). Run host-setup/install.sh on this host."
    return 0
  fi
  resolved=$(docker info --format '{{range .ClientInfo.Plugins}}{{if eq .Name "buildx"}}{{.Path}}{{end}}{{end}}' 2>/dev/null)
  if [ "$resolved" = "$shim" ]; then
    ok "docker resolves the buildx plugin to the MDT shim ($shim)"
  else
    warn "docker resolves the buildx plugin to '${resolved:-<unknown>}', not the MDT shim $shim — a plugin earlier in Docker's search path (e.g. ~/.docker/cli-plugins) bypasses the managed-builder default"
  fi
  if grep -q '^# BEGIN mdt-buildkit ' /etc/environment 2>/dev/null; then
    ok "/etc/environment carries the MDT BuildKit block (BUILDX_BUILDER for ssh, sudo, su, login and cron sessions)"
  else
    warn "/etc/environment has no '# BEGIN mdt-buildkit' block (BUILDX_BUILDER, BUILDKIT_HOST) — ssh/sudo/su/cron sessions rely on the docker-buildx shim alone, and tools that read BUILDX_BUILDER or BUILDKIT_HOST themselves (buildctl, scripts) see nothing; run host-setup/install.sh"
  fi
  probe_home=$(mktemp -d "${TMPDIR:-/tmp}/mdt-check-home.XXXXXXXX") || { warn "could not create a probe home to test a fresh user's builder"; return 0; }
  out=$(env -i PATH="$PATH" HOME="$probe_home" docker buildx inspect --bootstrap 2>&1)
  rm -rf -- "$probe_home"
  if printf '%s\n' "$out" | grep -Eq '^Name:[[:space:]]+mdt-managed$' && printf '%s\n' "$out" | grep -Eq '^Driver:[[:space:]]+remote$'; then
    ok "a fresh user with an empty environment resolves mdt-managed (remote driver)"
  else
    fail "a fresh user with an empty environment does NOT resolve mdt-managed (remote driver) — builds started from VS Code's SSH server, cron or systemd leave dev-buildkitd.slice. Check the shim line above and 'systemctl status mdt-buildkitd.service'. docker said: $(printf '%s' "$out" | head -n 3 | tr '\n' ' ')"
  fi
}
check_buildx_default_builder
# BEGIN io-writeback-check (extracted verbatim by tests/test_hs05.py)
# Writeback and root-queue settings (MDT-HS-05): the live value against host-setup.env, each state
# distinct: OK, MISMATCH, not managed (`kernel`/empty), could not read (never reported as OK).
_check_io_one() { # <label> <wanted> <live-file> <persist-file> <persist-regex>
  local label="$1" want="$2" file="$3" pfile="$4" pre="$5" live
  case "$want" in
    ""|kernel) info "$label: not managed (host-setup.env says kernel or nothing; the kernel's own value is kept)"; return 0 ;;
  esac
  if ! live=$(tr -d '[:space:]' < "$file" 2>/dev/null) || [ -z "$live" ]; then
    warn "$label: could not read $file — cannot tell whether the intended $want is in force"
    return 0
  fi
  if [ "$live" != "$want" ]; then
    fail "$label MISMATCH: live $live, host-setup.env says $want — re-run install.sh (read-back source: $file)"
  elif grep -qE "$pre" "$pfile" 2>/dev/null; then
    ok "$label = $live (matches host-setup.env, persisted in $pfile)"
  else
    warn "$label = $live is LIVE ONLY, NOT PERSISTED: $pfile does not carry it, so a reboot loses it — re-run install.sh"
  fi
}
# a file that sorts AFTER MDT's own and sets the same key overrides it at boot (sysctl.d / udev rules: the
# later file wins; /etc/sysctl.conf is applied last)
_check_io_override() { # <scan-dirs ':'-list> <glob> <ours-basename> <grep-regex> <what>
  local dirs="$1" glob="$2" ours="$3" re="$4" what="$5" d f b
  for d in ${dirs//:/ }; do
    [ -d "$d" ] || continue
    for f in "$d"/$glob; do
      [ -f "$f" ] || continue
      b=$(basename "$f")
      [ "$b" != "$ours" ] || continue
      [ "$(printf '%s\n%s\n' "$ours" "$b" | LC_ALL=C sort | tail -n 1)" = "$b" ] || continue
      grep -qE "$re" "$f" 2>/dev/null \
        && warn "NOTICE: $f also sets $what and sorts after MDT's $ours, so it overrides MDT's value at boot"
    done
  done
}
check_io_writeback() {
  local proc="${MDT_PROC_ROOT:-/proc}" sysroot="${MDT_SYSFS_ROOT:-/sys}" node dev n m qattrs=""
  local sctl="${MDT_SYSCTL_D:-/etc/sysctl.d}/$MDT_IO_WB_SYSCTL_FILE_NAME" rule="${MDT_UDEV_RULES_D:-/etc/udev/rules.d}/$MDT_IO_WB_UDEV_FILE_NAME" managed=""
  _check_io_one vm.dirty_bytes "${IO_WRITEBACK_DIRTY_BYTES:-}" "$proc/sys/vm/dirty_bytes" "$sctl" "^[[:space:]]*vm\\.dirty_bytes[[:space:]]*=[[:space:]]*${IO_WRITEBACK_DIRTY_BYTES:-x}[[:space:]]*\$"
  _check_io_one vm.dirty_background_bytes "${IO_WRITEBACK_DIRTY_BACKGROUND_BYTES:-}" "$proc/sys/vm/dirty_background_bytes" "$sctl" "^[[:space:]]*vm\\.dirty_background_bytes[[:space:]]*=[[:space:]]*${IO_WRITEBACK_DIRTY_BACKGROUND_BYTES:-x}[[:space:]]*\$"
  case "${IO_WRITEBACK_DIRTY_BYTES:-}" in ""|kernel) ;; *) managed="${managed}vm\\.dirty_bytes|" ;; esac
  case "${IO_WRITEBACK_DIRTY_BACKGROUND_BYTES:-}" in ""|kernel) ;; *) managed="${managed}vm\\.dirty_background_bytes|" ;; esac
  if [ -n "$managed" ]; then
    _check_io_override "${MDT_SYSCTL_SCAN_DIRS:-/etc/sysctl.d:/run/sysctl.d:/usr/lib/sysctl.d:/lib/sysctl.d}" "*.conf" \
      "$MDT_IO_WB_SYSCTL_FILE_NAME" "^[[:space:]]*(${managed%|})[[:space:]]*=" "a key MDT manages ($(echo "${managed%|}" | tr -d '\\'))"
    if [ -f "${MDT_SYSCTL_CONF:-/etc/sysctl.conf}" ] && grep -qE "^[[:space:]]*(${managed%|})[[:space:]]*=" "${MDT_SYSCTL_CONF:-/etc/sysctl.conf}"; then
      warn "NOTICE: ${MDT_SYSCTL_CONF:-/etc/sysctl.conf} also sets a key MDT manages and is applied after every sysctl.d file, so it overrides MDT's value at boot"
    fi
  fi
  n="${IO_ROOT_QUEUE_NR_REQUESTS:-}"; m="${IO_ROOT_QUEUE_MAX_SECTORS_KB:-}"
  case "$n$m" in
    ""|kernel|kernelkernel)
      info "root disk nr_requests: not managed (kernel)"; info "root disk max_sectors_kb: not managed (kernel)"; return 0 ;;
  esac
  if ! node=$(_mdt_root_disk_node 2>&1); then
    warn "root disk queue: could not derive the whole disk under / ($node) — nr_requests/max_sectors_kb cannot be verified"
    return 0
  fi
  dev=${node#/dev/}
  _check_io_one "$dev nr_requests" "$n" "$sysroot/block/$dev/queue/nr_requests" "$rule" "ATTR\\{queue/nr_requests\\}=\"${n:-x}\""
  _check_io_one "$dev max_sectors_kb" "$m" "$sysroot/block/$dev/queue/max_sectors_kb" "$rule" "ATTR\\{queue/max_sectors_kb\\}=\"${m:-x}\""
  case "$n" in ""|kernel) ;; *) qattrs="${qattrs}queue/nr_requests|" ;; esac
  case "$m" in ""|kernel) ;; *) qattrs="${qattrs}queue/max_sectors_kb|" ;; esac
  _check_io_override "${MDT_UDEV_SCAN_DIRS:-/etc/udev/rules.d:/run/udev/rules.d:/usr/lib/udev/rules.d:/lib/udev/rules.d}" "*.rules" \
    "$MDT_IO_WB_UDEV_FILE_NAME" "ATTR\\{(${qattrs%|})\\}" "a queue attribute MDT manages (${qattrs%|})"
}
echo "== writeback and root-queue settings =="
check_io_writeback
# END io-writeback-check
memory_leaf_policy_configured=0
for key in \
  WATCHER_INTERACTIVE_MEMORY_HIGH WATCHER_INTERACTIVE_MEMORY_MAX \
  WATCHER_BACKGROUND_MEMORY_HIGH WATCHER_BACKGROUND_MEMORY_MAX \
  WATCHER_GATES_MEMORY_HIGH WATCHER_GATES_MEMORY_MAX; do
  value="${!key:-}"
  case "$value" in ""|-) ;; *) memory_leaf_policy_configured=1 ;; esac
done
memory_watcher_active=0
memory_watcher_enabled=0
systemctl is-active --quiet mdt-container-memory-inotify-watcher.service && memory_watcher_active=1
systemctl is-enabled --quiet mdt-container-memory-inotify-watcher.service && memory_watcher_enabled=1
case "${WATCHER_MEMORY_POLICY:-}" in
  enabled)
    if [ "$memory_leaf_policy_configured" != 1 ]; then
      fail "WATCHER_MEMORY_POLICY=enabled but all per-container memory values are '-' — choose at least one cap or disable the policy"
    elif [ "$memory_watcher_active" = 1 ]; then
      ok "mdt-container-memory-inotify-watcher.service active (applies explicitly configured per-container memory/swap caps)"
    else
      warn "mdt-container-memory-inotify-watcher.service not active — explicitly configured per-container memory controls are not applied to new scopes; there is no periodic memory-cap fallback, while parent slice limits remain in force"
    fi
    ;;
  disabled)
    if [ "$memory_leaf_policy_configured" = 1 ]; then
      fail "per-container memory values are configured while WATCHER_MEMORY_POLICY=disabled; set the policy to enabled or change those values to '-'"
    elif [ "$memory_watcher_active" = 1 ] || [ "$memory_watcher_enabled" = 1 ]; then
      warn "mdt-container-memory-inotify-watcher.service is active or enabled while WATCHER_MEMORY_POLICY=disabled — run install.sh to stop and disable it"
    else
      ok "mdt-container-memory-inotify-watcher.service disabled; no per-container memory controls are configured and parent slice limits remain in force"
    fi
    ;;
  *)
    fail "WATCHER_MEMORY_POLICY must be exactly enabled or disabled"
    ;;
esac

echo "== inotify watch budget (VS Code workspace watcher, MDT-003) =="
# BEGIN inotify-budget (tests extract this block; the MDT_INOTIFY_* overrides exist for them)
# The threshold is the POLICY minimum: the shipped etc/sysctl.d/99-inotify.conf when run from a
# checkout, 524288 when installed. Deliberately NOT the installed file: a file lowered by hand
# would otherwise certify itself (both checks below would compare against its own value).
_inotify_installed="${MDT_INOTIFY_SYSCTL_FILE:-/etc/sysctl.d/99-mdt-inotify.conf}"
_inotify_shipped="${MDT_INOTIFY_SHIPPED_FILE:-$(dirname "$0")/../etc/sysctl.d/99-inotify.conf}"
_inotify_proc="${MDT_INOTIFY_PROC:-/proc/sys/fs/inotify/max_user_watches}"
_inotify_value() { awk -F= '/^[[:space:]]*fs\.inotify\.max_user_watches[[:space:]]*=/ {gsub(/[[:space:]]/,"",$2); print $2}' "$1" 2>/dev/null | tail -1; }
want_watches=""
[ -r "$_inotify_shipped" ] && want_watches=$(_inotify_value "$_inotify_shipped")
case "$want_watches" in
  ''|*[!0-9]*) [ -n "$want_watches" ] && warn "shipped fs.inotify.max_user_watches threshold '$want_watches' is not a number; using 524288"; want_watches=524288 ;;
esac
have_watches=$(cat "$_inotify_proc" 2>/dev/null || echo "")
if [ -z "$have_watches" ]; then
  warn "fs.inotify.max_user_watches unreadable"
elif [ "$have_watches" -ge "$want_watches" ]; then
  ok "fs.inotify.max_user_watches=$have_watches (>= $want_watches)"
else
  fail "fs.inotify.max_user_watches=$have_watches < $want_watches — VS Code reports 'unable to watch for file changes' on large workspaces; re-run install.sh"
fi
if [ -f "$_inotify_installed" ]; then
  # Present AND carrying the key at or above the policy minimum: an edited, lowered or truncated
  # file must not pass as "persists".
  persisted_watches=$(_inotify_value "$_inotify_installed")
  case "$persisted_watches" in
    ''|*[!0-9]*) warn "$_inotify_installed present but sets no numeric fs.inotify.max_user_watches; re-run install.sh" ;;
    *) if [ "$persisted_watches" -ge "$want_watches" ]; then
         ok "$_inotify_installed sets fs.inotify.max_user_watches=$persisted_watches (>= $want_watches, persists across reboots)"
       else
         fail "$_inotify_installed sets fs.inotify.max_user_watches=$persisted_watches < $want_watches (lowered by hand?) — the next boot drops below the policy; re-run install.sh"
       fi ;;
  esac
else
  warn "$_inotify_installed missing — the value will not survive a reboot; re-run install.sh"
fi
# END inotify-budget

echo "== capacity unavailable to MDT (disk swap, dev.slice MemoryMax vs RAM_MDT) =="
if [ ! -f "$CONF" ] || [ ! -f "$_CAPACITY_PY" ]; then
  info "capacity checks skipped ($CONF or $_CAPACITY_PY missing)"
else
  _cap_nproc=$(nproc 2>/dev/null || echo 1)
  _cap_out=$(python3 "$_CAPACITY_PY" check-config "$CONF" --nproc "$_cap_nproc" 2>&1) || _cap_out="WARN capacity check failed to run: $_cap_out"
  if printf '%s' "$_cap_out" | grep -q '^WARN'; then
    while IFS= read -r _line; do
      case "$_line" in WARN\ *) warn "${_line#WARN }" ;; esac
    done <<<"$_cap_out"
  else
    ok "MDT has disk swap and dev.slice MemoryMax is within RAM_MDT"
  fi
fi

echo "== protected units (MDT_PROTECTED_UNITS; verify only) =="
if [ -z "${MDT_PROTECTED_UNITS:-}" ]; then
  info "MDT_PROTECTED_UNITS is empty — no host/production unit is verified (the capacity unavailable to MDT is not checked against real protection)"
elif [ ! -f "$_CAPACITY_PY" ]; then
  warn "$_CAPACITY_PY missing — cannot verify MDT_PROTECTED_UNITS; re-run install.sh"
else
  _prot_out=$(python3 "$_CAPACITY_PY" verify-protection "$CONF" --cgroup-root "$CG" 2>&1) || _prot_out="WARN protection verification failed to run: $_prot_out"
  if printf '%s' "$_prot_out" | grep -q '^WARN'; then
    while IFS= read -r _line; do
      case "$_line" in WARN\ *) warn "${_line#WARN }" ;; esac
    done <<<"$_prot_out"
  else
    ok "memory.min of MDT_PROTECTED_UNITS covers MDT_EXCLUDED_RAM; weights are at least 4x dev.slice's"
  fi
fi

echo "== dev-cache.slice and the shared tree cache (AD2-2, HOST_ROLE=dedicated-dev) =="
# One implementation (mdt_tree_cache.py check): slice exists with the configured MemoryLow, both tmpfs areas are
# mounted with the configured size/inodes/gid/mode, the GC timer is active, host-capacity.json carries tree_cache.
_TREE_PY="${TREE_CACHE_PY:-$(dirname "$0")/mdt_tree_cache.py}"
[ -f "$_TREE_PY" ] || _TREE_PY="$(dirname "$0")/../mdt_tree_cache.py"
if [ "$(_mdt_host_role)" != dedicated-dev ]; then
  info "tree cache not applicable (HOST_ROLE is not dedicated-dev)"
elif [ ! -f "$_TREE_PY" ]; then
  warn "$_TREE_PY missing — cannot verify the tree cache; re-run install.sh"
else
  _tree_out=$(python3 "$_TREE_PY" check --config "$CONF" --role dedicated-dev 2>&1) || _tree_out="WARN the tree cache check failed to run: $_tree_out"
  while IFS= read -r _line; do
    case "$_line" in
      OK\ *)   ok "${_line#OK }" ;;
      FAIL\ *) fail "${_line#FAIL }" ;;
      WARN\ *) warn "${_line#WARN }" ;;
      INFO\ *) info "${_line#INFO }" ;;
      "") ;;
      *)       warn "tree cache check: $_line" ;;
    esac
  done <<<"$_tree_out"
fi

echo "== run-gate gates admission cap (MDT-077) =="
# One implementation (mdt_admission.py check): the Docker daemon publishes the cap run-gate's count admission reads
# (ciu-admission-<g>, label gates.max_concurrent). A reinstalled host has none and every lane of a project with
# [admission] enabled ends NOT_RUN no-headroom, so a missing cap is a FAIL; a value that differs from the host policy
# is a WARN (a manual `run-gate admission set`) or, with ADMISSION_MAX_CONCURRENT pinned, a FAIL. Read-only.
_ADMISSION_PY="${ADMISSION_PY:-$(dirname "$0")/mdt_admission.py}"
[ -f "$_ADMISSION_PY" ] || _ADMISSION_PY="$(dirname "$0")/../mdt_admission.py"
if [ ! -f "$_ADMISSION_PY" ] || [ ! -f "$CONF" ]; then
  warn "mdt_admission.py or $CONF missing — the gates admission cap was not checked; re-run install.sh"
else
  _adm_out=$(python3 "$_ADMISSION_PY" check "$CONF" --meminfo /proc/meminfo --nproc "$(nproc)" 2>&1) \
    || _adm_out="FAIL the gates admission check failed to run: $_adm_out"
  while IFS= read -r _line; do
    case "$_line" in
      OK\ *)   ok "${_line#OK }" ;;
      WARN\ *) warn "${_line#WARN }" ;;
      FAIL\ *) fail "${_line#FAIL }" ;;
      INFO\ *) info "${_line#INFO }" ;;
      "") ;;
      *)       warn "gates admission report: $_line" ;;
    esac
  done <<<"$_adm_out"
fi

echo "== per-host CPU benchmark (HB-3) =="
# The persisted /var/lib/mdt/host-facts/cpu-benchmark.json (measured once, never by this check): missing,
# partial (no scores) or STALE (cpu_model / vCPU count changed) is a WARN with the command to run.
_CPUBENCH_PY="${CPUBENCH_PY:-$(dirname "$0")/mdt-host-cpu-benchmark.py}"
if [ ! -f "$_CPUBENCH_PY" ]; then
  warn "$_CPUBENCH_PY missing — cannot report the CPU benchmark; re-run install.sh"
else
  _cpub_out=$(python3 "$_CPUBENCH_PY" doctor 2>&1) || _cpub_out="WARN the CPU benchmark report failed to run: $_cpub_out"
  while IFS= read -r _line; do
    case "$_line" in
      OK\ *)   ok "${_line#OK }" ;;
      WARN\ *) warn "${_line#WARN }" ;;
      INFO\ *) info "${_line#INFO }" ;;
      "") ;;
      *)       warn "CPU benchmark report: $_line" ;;
    esac
  done <<<"$_cpub_out"
fi

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
