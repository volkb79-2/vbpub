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
  # installed, or one whose daemon predates the socket listener. exec stays
  # the permanent fallback transport either way (design doc A2).
  if [ -S "$CGPROFILE_DIR/ctl.sock" ]; then
    info "socket carrier available ($CGPROFILE_DIR/ctl.sock)"
  else
    info "exec carrier only (daemon socket absent) -- docker exec still works; the daemon may not be installed, may predate the listener, or has not started it yet"
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

echo "== cgprofile resource observer (/run/cgprofile/observer, contract 2) =="
check_cgprofile_observer

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
