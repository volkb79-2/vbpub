#!/usr/bin/env bash
# Description: render and apply the gates-tier memory policy of a pure test host (operator decision 2026-11-16):
#              drop-ins only, no unit-file edits, and the slice is restarted only while it has no tasks.
#
# usage: provision-gates-limits.sh [--meminfo FILE] [--systemd-dir DIR] [--cgroupfs DIR] [--procfs DIR] [--dry-run]
#
# The policy this renders (values derived from the host, never hard-coded):
#   - no finite MemoryHigh: the decided policy has no throttling mark, so a fragment's own mark is overridden;
#   - MemoryMax     = MemTotal - control-plane floor - slack  (the control plane keeps its measured floor);
#   - MemorySwapMax = SwapTotal - the host's own swap share    (swap exists from the install plan; this only reads it);
#   - OOMPolicy=kill on every transient docker-<id>.scope, so an OOM inside one container kills that container's whole
#     process tree (memory.oom.group=1). Deliberately NOT on the slice: one runaway lane would kill every lane;
#   - OOMScoreAdjust=-900 on ssh, systemd-logind, systemd-journald, docker and cgprofiled: a cgroup floor never enters
#     the global OOM victim ranking, and this kernel scores an adj-0 2 MiB service and an adj-0 lane process alike.
# Idempotent: an unchanged drop-in is reported and left alone; a second run with a matching live cgroup changes nothing.
# The derivation, the overrides and their consequences are documented in runner/README.md ("Gates-tier memory policy").
#
# Exit codes: 0 applied or already converged; 2 usage or an invalid override;
#             3 a required host fact is unreadable (MemTotal, SwapTotal, the slice's cgroup) or there is no swap to page with;
#             4 refused: the slice has tasks, so a restart would reset the memory.events counters INCONCLUSIVE-OOM reads;
#             5 refused: a file or drop-in this tool does not own carries the key it would set;
#             6 not root; 7 the live cgroup still disagrees after a restart; 8 a live OOM score could not be set.
set -Eeuo pipefail
umask 022
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check

slice=${GATES_SLICE:-}
: "${slice:?GATES_SLICE is not set (runner.env: the gates tier slice name)}"
[[ $slice =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.slice$ ]] || { echo "GATES_SLICE is not a slice unit name: $slice" >&2; exit 2; }

usage() {
  cat >&2 <<'EOF'
usage: provision-gates-limits.sh [--meminfo FILE] [--systemd-dir DIR] [--cgroupfs DIR] [--procfs DIR] [--dry-run]
  --meminfo FILE     the meminfo the policy is derived from (default /proc/meminfo)
  --systemd-dir DIR  the systemd unit directory the drop-ins are written to (default /etc/systemd/system)
  --cgroupfs DIR     the cgroup2 mount the live slice limits are read from (default /sys/fs/cgroup)
  --procfs DIR       the procfs where a running unit's live OOM score is set in place (default /proc)
  --dry-run          print the plan; write, reload, restart and set nothing
EOF
}

meminfo=/proc/meminfo
systemd_dir=/etc/systemd/system
cgroupfs=/sys/fs/cgroup
procfs=/proc
dry_run=0
while (($#)); do
  case $1 in
    --meminfo) (($# >= 2)) || { usage; exit 2; }; meminfo=$2; shift 2 ;;
    --systemd-dir) (($# >= 2)) || { usage; exit 2; }; systemd_dir=$2; shift 2 ;;
    --cgroupfs) (($# >= 2)) || { usage; exit 2; }; cgroupfs=$2; shift 2 ;;
    --procfs) (($# >= 2)) || { usage; exit 2; }; procfs=$2; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
for value in "$meminfo" "$systemd_dir" "$cgroupfs" "$procfs"; do
  [[ $value == /* ]] || { echo "paths must be absolute: $value" >&2; exit 2; }
done
systemd_dir=${systemd_dir%/}
cgroupfs=${cgroupfs%/}
procfs=${procfs%/}
if ((dry_run == 0)) && [[ $systemd_dir == /etc/systemd/system ]] && [[ $(id -u) != 0 ]]; then
  echo 'writing drop-ins under /etc/systemd/system must run as root' >&2
  exit 6
fi

# ---- host-owned numbers ----------------------------------------------------
# A policy DEFAULT, not a placeholder: the measured control plane of a pure test host needs about 1.2 GiB
# (anon 0.1 + hot file 0.5 + kernel 0.3 + cockpit/run-gate 0.3), so the floor is 1024 MiB and the slack 256 MiB.
# Every override pins a value the operator measured for THIS host; nothing here substitutes for a readable fact.
control_floor_mib=${GATES_CONTROL_FLOOR_MIB:-1024}
memory_slack_mib=${GATES_MEMORY_SLACK_MIB:-256}
swap_reserve_mib=${GATES_SWAP_RESERVE_MIB:-8192}
memory_max_pin=${GATES_MEMORY_MAX_MIB:-}
memory_swap_max_pin=${GATES_MEMORY_SWAP_MAX_MIB:-}
oom_score_adjust=-900
is_positive_integer() { [[ $1 =~ ^[1-9][0-9]*$ ]]; }
for value in "$control_floor_mib" "$memory_slack_mib" "$swap_reserve_mib"; do
  is_positive_integer "$value" || {
    echo "GATES_CONTROL_FLOOR_MIB/GATES_MEMORY_SLACK_MIB/GATES_SWAP_RESERVE_MIB must be positive integers (MiB), got '$value'" >&2
    exit 2
  }
done
for value in "$memory_max_pin" "$memory_swap_max_pin"; do
  [[ -z $value ]] || is_positive_integer "$value" || {
    echo "a pinned MemoryMax/MemorySwapMax must be a positive integer number of MiB, got '$value'" >&2
    exit 2
  }
done

# ---- host facts ------------------------------------------------------------
# FACT_VALUE is set by read_fact; its status is the answer (0 read, 1 absent or unreadable). "Cannot read" is never
# folded into "there is none": a host fact that exists elsewhere must not be replaced by an invented number.
FACT_VALUE=
read_fact() {
  local key=$1 value
  FACT_VALUE=
  [[ -r $meminfo ]] || return 1
  value=$(awk -v k="$key" '$1 == k { print $2; exit }' "$meminfo" 2>/dev/null) || return 1
  [[ -n $value && $value =~ ^[0-9]+$ ]] || return 1
  FACT_VALUE=$value
}
read_fact 'MemTotal:' || { echo "cannot read MemTotal: from $meminfo; refusing to invent a memory policy" >&2; exit 3; }
mem_total_kb=$FACT_VALUE
read_fact 'SwapTotal:' || { echo "cannot read SwapTotal: from $meminfo; refusing to invent a swap ceiling (a host fact that could not be read is not a host with no swap)" >&2; exit 3; }
swap_total_kb=$FACT_VALUE
is_positive_integer "$mem_total_kb" || { echo "MemTotal: in $meminfo is not a usable amount of memory (${mem_total_kb} kB)" >&2; exit 3; }
total_mib=$(( mem_total_kb / 1024 ))
swap_mib=$(( swap_total_kb / 1024 ))
reserve_mib=$(( control_floor_mib + memory_slack_mib ))
(( total_mib > reserve_mib + 1024 )) || {
  echo "MemTotal ${total_mib} MiB leaves nothing above the control-plane floor ${reserve_mib} MiB plus 1024 MiB headroom; refusing the policy" >&2
  exit 3
}
memory_max_mib=$(( total_mib - reserve_mib ))
if [[ -n $memory_max_pin ]]; then
  memory_max_mib=$memory_max_pin
fi
(( memory_max_mib < total_mib )) || {
  echo "MemoryMax ${memory_max_mib}M is not below MemTotal ${total_mib}M, so it protects no control-plane floor; use the derived value or a smaller pin" >&2
  exit 3
}
(( swap_mib > swap_reserve_mib )) || {
  echo "SwapTotal ${swap_mib} MiB is not above this host's swap reserve ${swap_reserve_mib} MiB, so the paging-by-design ceiling cannot be rendered; a host with no swap keeps its lanes RAM-bound (set GATES_SWAP_RESERVE_MIB to this host's own swap share only when that share is measured)" >&2
  exit 3
}
memory_swap_max_mib=$(( swap_mib - swap_reserve_mib ))
if [[ -n $memory_swap_max_pin ]]; then
  memory_swap_max_mib=$memory_swap_max_pin
fi
memory_max_bytes=$(( memory_max_mib * 1024 * 1024 ))
memory_swap_max_bytes=$(( memory_swap_max_mib * 1024 * 1024 ))
printf 'gates memory policy: MemTotal %s MiB, SwapTotal %s MiB; floor %s MiB, slack %s MiB, swap reserve %s MiB\n' \
  "$total_mib" "$swap_mib" "$control_floor_mib" "$memory_slack_mib" "$swap_reserve_mib"
printf 'gates memory policy: MemoryHigh=infinity MemoryMax=%sM MemorySwapMax=%sM OOMScoreAdjust=%s\n' \
  "$memory_max_mib" "$memory_swap_max_mib" "$oom_score_adjust"

# ---- rendering -------------------------------------------------------------
marker='# RENDERED by scripts/netcup/runner/provision-gates-limits.sh'
changed=0

# Own each drop-in exactly: rewrite it when its bytes differ, refuse a file that another owner rendered (its policy must
# be retired consciously), and report an unchanged file so a second run is visibly a no-op.
# Ownership is the marker as a PREFIX of the first line: a file that merely mentions this script's path is not ours.
owned_file() { case $(head -n 1 "$1" 2>/dev/null) in "$marker"*) return 0 ;; *) return 1 ;; esac; }
render_file() {
  local path=$1 content=$2 tmp
  if [[ -e $path ]]; then
    if ! owned_file "$path"; then
      echo "refusing to override $path: its first line does not start with this tool's marker '$marker'; retire or relocate that file deliberately and re-run" >&2
      exit 5
    fi
    if cmp -s -- "$path" <(printf '%s' "$content"); then
      echo "gates memory policy: unchanged $path"
      return 0
    fi
  fi
  if ((dry_run)); then
    echo "gates memory policy: would write $path"
    return 0
  fi
  install -d -m 0755 "$(dirname -- "$path")"
  tmp="$path.tmp.$$"
  printf '%s' "$content" > "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$path"
  echo "gates memory policy: wrote $path"
  changed=1
}

slice_dropin="$systemd_dir/$slice.d/20-memory-policy.conf"
render_file "$slice_dropin" "$marker - do not edit; re-run the provisioning step to converge.
# Operator decision 2026-11-16 for a pure test host: protect only the measured control plane. No finite MemoryHigh
# (a high mark throttles the tier before the floor is ever at risk), so a fragment's own mark is overridden here.
# RAM is capped so the control plane keeps its floor; swap is capped to this host's own share so a lane larger than
# RAM PAGES by design instead of being killed. Derived: MemTotal - (floor ${control_floor_mib} MiB + slack ${memory_slack_mib} MiB), and
# SwapTotal - swap reserve ${swap_reserve_mib} MiB. CPUQuota/TasksMax are host policy and are NOT rendered here.
[Slice]
MemoryHigh=infinity
MemoryMax=${memory_max_mib}M
MemorySwapMax=${memory_swap_max_mib}M
"

# A drop-in from another owner that sets a key this file owns would silently compete with it.
if [[ -d $systemd_dir/$slice.d ]]; then
  for other in "$systemd_dir/$slice.d"/*.conf; do
    [[ -e $other && $other != "$slice_dropin" ]] || continue
    head -n 1 "$other" 2>/dev/null | grep -qF -- "$marker" && continue
    if grep -qE '^(MemoryHigh|MemoryMax|MemorySwapMax|MemoryOOMGroup|ManagedOOMMemoryPressure)=' -- "$other"; then
      echo "refusing: $other also sets the gates-slice memory policy; delete that file deliberately (for example the hand drop-in the 2026-11-16 decision supersedes) and re-run" >&2
      exit 5
    fi
  done
fi

render_file "$systemd_dir/docker-.scope.d/20-oom-group.conf" "$marker - do not edit; re-run the provisioning step to converge.
# memory.oom.group=1 for every transient docker-<id>.scope: an OOM inside ONE container kills that container's whole
# process tree (one lane) instead of an arbitrary process of it. Deliberately NOT on the gates slice: a slice-level
# group would kill every lane in the tier when a single runaway exhausts it.
[Scope]
OOMPolicy=kill
"

# The control-plane drop-ins are rendered even for a unit that is not installed yet (cgprofiled arrives later in the
# bring-up): a drop-in for an absent unit is inert and applies when the unit appears, so no reinstall loses it.
protected_units='ssh.service systemd-logind.service systemd-journald.service docker.service cgprofiled.service'
for unit in $protected_units; do
  render_file "$systemd_dir/$unit.d/30-oom-protect.conf" "$marker - do not edit; re-run the provisioning step to converge.
# Keep this control-plane unit out of the global OOM victim ranking: a cgroup floor (MemoryLow) never enters that
# ranking, and this kernel scores every adj-0 process about alike regardless of size, so a lane made of many small
# processes would otherwise tie with, or win against, the host's own services.
# Accepted consequence of the 2026-11-16 decision: ssh sessions inherit this score, so a heavy process started directly
# in an ssh session is hard for the global OOM killer to pick. Lane processes are unaffected: Docker gives a container
# the container's own (0) score.
[Service]
OOMScoreAdjust=${oom_score_adjust}
"
done

if ((changed)); then
  if ((dry_run)); then
    echo 'gates memory policy: would run systemctl daemon-reload'
  else
    systemctl daemon-reload
    echo 'gates memory policy: reloaded systemd (daemon-reload)'
  fi
fi

# ---- control-plane OOM scores, in place -----------------------------------
# A drop-in takes effect at the unit's next start; writing /proc/<MainPID>/oom_score_adj reproduces the measured state
# now WITHOUT restarting ssh/logind/journald/docker (a restart drops live sessions and, for docker, every cockpit).
apply_live_score() {
  local unit=$1 pid adj_file current
  pid=$(systemctl show "$unit" -p MainPID --value 2>/dev/null || true)
  if ! is_positive_integer "$pid"; then
    echo "gates memory policy: $unit has no running main process; its drop-in applies at its next start"
    return 0
  fi
  adj_file="$procfs/$pid/oom_score_adj"
  if [[ ! -e $adj_file ]]; then
    echo "gates memory policy: cannot read $adj_file; $unit's drop-in applies at its next start"
    return 0
  fi
  current=$(<"$adj_file")
  [[ $current == "$oom_score_adjust" ]] && return 0
  if ((dry_run)); then
    echo "gates memory policy: would set $unit (pid $pid) oom_score_adj $current -> $oom_score_adjust"
    return 0
  fi
  if ! printf '%s\n' "$oom_score_adjust" > "$adj_file" 2>/dev/null; then
    echo "cannot set the live OOM score of $unit (pid $pid, $adj_file); its drop-in is installed and applies at its next start" >&2
    exit 8
  fi
  echo "gates memory policy: set $unit (pid $pid) oom_score_adj -> $oom_score_adjust in place (no restart)"
}
for unit in $protected_units; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    apply_live_score "$unit"
  fi
done

# ---- live slice limits -----------------------------------------------------
# daemon-reload alone does NOT re-apply cgroup limits to an active slice (measured 2026-11-16), and a slice restart
# resets its memory.events counters — the ones remote-gate's INCONCLUSIVE-OOM check reads as a delta. So the restart
# happens only while the tier has no tasks, and only when the live values disagree with the rendered policy.
slice_cgroup() {
  local cg
  cg=$(systemctl show "$slice" -p ControlGroup --value 2>/dev/null || true)
  [[ $cg == /* ]] || return 1
  printf '%s\n' "$cgroupfs$cg"
}
slice_has_tasks() {
  local cgdir=$1 file pid
  [[ -d $cgdir ]] || return 1
  while IFS= read -r -d '' file; do
    pid=
    IFS= read -r pid < "$file" || true
    [[ -n $pid ]] && return 0
  done < <(find "$cgdir" -type f -name cgroup.procs -print0 2>/dev/null)
  return 1
}
# An unreadable limit is not a missing limit: it is reported as unreadable and never compared as equal.
read_limit() { cat -- "$1" 2>/dev/null || echo unreadable; }
live_limits_ok() {
  local cgdir=$1
  [[ -d $cgdir ]] || return 1
  [[ $(read_limit "$cgdir/memory.high") == max ]] || return 1
  [[ $(read_limit "$cgdir/memory.max") == "$memory_max_bytes" ]] || return 1
  [[ $(read_limit "$cgdir/memory.swap.max") == "$memory_swap_max_bytes" ]] || return 1
}
report_live() {
  local cgdir=$1 file
  for file in memory.high memory.max memory.swap.max; do
    printf '  %s=%s\n' "$file" "$(read_limit "$cgdir/$file")" >&2
  done
  printf '  expected memory.high=%s memory.max=%s memory.swap.max=%s\n' max "$memory_max_bytes" "$memory_swap_max_bytes" >&2
}

load_state=$(systemctl show "$slice" -p LoadState --value 2>/dev/null || true)
if [[ $load_state != loaded ]]; then
  echo "gates memory policy: $slice is not installed on this host (LoadState '${load_state:-unknown}'), so nothing was applied to a"
  echo "gates memory policy: live slice. The drop-ins above take effect when the unit appears: mdt host-setup renders and reloads"
  echo "gates memory policy: it (P5/P6), or the marker-unit step of the no-host-setup path installs it. Then re-run this step."
else
  fragment=$(systemctl show "$slice" -p FragmentPath --value 2>/dev/null || true)
  if [[ -n $fragment ]]; then
    echo "gates memory policy: $slice has a fragment ($fragment); this drop-in deliberately overrides its MemoryHigh/MemoryMax/"
    echo "gates memory policy: MemorySwapMax with the 2026-11-16 pure-test-host policy (a foreign drop-in that sets them is refused above)"
  fi
  cgdir=$(slice_cgroup) || { echo "cannot resolve the cgroup of $slice (ControlGroup unreadable); refusing to report a policy as applied" >&2; exit 3; }
  if live_limits_ok "$cgdir"; then
    echo "gates memory policy: $slice live limits already match the policy ($cgdir)"
  elif ((dry_run)); then
    echo "gates memory policy: live limits disagree ($cgdir):"
    report_live "$cgdir"
    echo "gates memory policy: would restart $slice while idle (only if it has no tasks) to apply memory.high/max/swap.max"
  elif slice_has_tasks "$cgdir"; then
    echo "gates memory policy: refusing to restart $slice: it has tasks ($cgdir). A restart would reset its memory.events oom_kill" >&2
    echo "gates memory policy: counter (what the INCONCLUSIVE-OOM check reads) and kill the running lane. Re-run when the tier is idle." >&2
    exit 4
  else
    systemctl restart "$slice"
    echo "gates memory policy: restarted $slice while idle (its memory.events counters start over: never restart it mid-run)"
    if ! live_limits_ok "$cgdir"; then
      echo "gates memory policy: $slice still disagrees with the rendered policy after the restart ($cgdir):" >&2
      report_live "$cgdir"
      exit 7
    fi
    echo "gates memory policy: applied and verified in $cgdir (memory.high=max, memory.max=$memory_max_bytes, memory.swap.max=$memory_swap_max_bytes)"
  fi
fi

printf 'GATES_MEMORY_POLICY=MemoryHigh=infinity MemoryMax=%sM MemorySwapMax=%sM OOMScoreAdjust=%s\n' \
  "$memory_max_mib" "$memory_swap_max_mib" "$oom_score_adjust"
