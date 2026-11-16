#!/usr/bin/env bash
# Description: render and apply the gates-tier memory policy of a pure test host (operator decision 2026-11-16):
#              drop-ins only, no unit-file edits, and the slice is restarted only while it has no tasks.
#
# usage: provision-gates-limits.sh [--adopt] [--dry-run] [--meminfo FILE] [--systemd-dir DIR] [--cgroupfs DIR] [--procfs DIR]
#
# The policy this renders (values derived from the host, never hard-coded):
#   - no finite MemoryHigh: the decided policy has no throttling mark, so a fragment's own mark is overridden;
#   - MemoryMax     = the NOMINAL MemTotal - the control-plane reserve (the control plane keeps its measured floor);
#   - MemorySwapMax = the NOMINAL SwapTotal - the host's own swap share (swap exists from the install plan; this reads it);
#   - OOMPolicy=kill on every transient docker-<id>.scope, so an OOM inside one container kills that container's whole
#     process tree (memory.oom.group=1). Deliberately NOT on the slice: one runaway lane would kill every lane;
#   - OOMScoreAdjust=-900 on ssh, systemd-logind, systemd-journald, docker and cgprofiled: a cgroup floor never enters
#     the global OOM victim ranking, and this kernel scores an adj-0 2 MiB service and an adj-0 lane process alike.
# Nominal, not raw: see the derivation below (a host whose MemTotal reads 7935 MiB is an 8 GiB host).
# Idempotent: an unchanged drop-in is reported and left alone; a second run with a matching live cgroup changes nothing.
# The derivation, the overrides and their consequences are documented in runner/README.md ("Gates-tier memory policy").
#
# --adopt is the explicit one-time migration from a host an operator configured by hand (the 2026-11-16 measurement
# itself): a file at this tool's own path is recognised only when every non-comment line is a section header this file
# owns or a directive whose key belongs to the policy it renders. Its VALUES are not compared — a hand file legitimately
# writes `56G` where this tool renders `57344M` (the same amount, another spelling) — so the adopted file is rewritten as
# this tool's own rendering. Its content is COPIED to <file>.retired-<date> (a name systemd does not load) and never
# deleted. Without --adopt such a file is refused with exit 5, naming --adopt.
#
# Exit codes: 0 applied, already converged, or a loaded-but-inactive slice (nothing live to compare or restart);
#             2 usage or an invalid override;
#             3 a required host fact is unreadable (MemTotal, SwapTotal, the slice's cgroup, its process lists) or there
#               is no swap to page with (a swapless host refuses by design: this policy pages lanes instead of killing them);
#             4 refused: the slice has tasks, so a restart would reset the memory.events counters INCONCLUSIVE-OOM reads;
#             5 refused: a file or drop-in this tool does not own carries the key it would set (or is not a recognised
#               hand drop-in even with --adopt);
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
usage: provision-gates-limits.sh [--adopt] [--dry-run] [--meminfo FILE] [--systemd-dir DIR] [--cgroupfs DIR] [--procfs DIR]
  --adopt            one-time migration: retire a RECOGNISED hand drop-in at this tool's own path to <file>.retired-<date>
                     and replace it with the rendered policy (refused, with a message naming this flag, when absent)
  --dry-run          print the plan; write, reload, restart and set nothing
  --meminfo FILE     the meminfo the policy is derived from (default /proc/meminfo)
  --systemd-dir DIR  the systemd unit directory the drop-ins are written to (default /etc/systemd/system)
  --cgroupfs DIR     the cgroup2 mount the live slice limits are read from (default /sys/fs/cgroup)
  --procfs DIR       the procfs where a running unit's live OOM score is set in place (default /proc)
EOF
}

meminfo=/proc/meminfo
systemd_dir=/etc/systemd/system
cgroupfs=/sys/fs/cgroup
procfs=/proc
dry_run=0
adopt_requested=0
while (($#)); do
  case $1 in
    --adopt) adopt_requested=1; shift ;;
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
# Policy DEFAULTS, not placeholders: the measured control plane of a pure test host needs about 1.2 GiB
# (anon 0.1 + hot file 0.5 + kernel 0.3 + cockpit/run-gate 0.3), so the floor is 1024 MiB (rounded DOWN from the
# 1.2 GiB the operator measured) plus 512 MiB of slack = a 1536 MiB reserve. The nominal step is one GiB, the size
# grid a host is bought in: a kernel reserves a little, so a 8 GiB host reports 7935 MiB and is still an 8 GiB host.
# Every override pins a value the operator measured for THIS host; nothing here substitutes for a readable fact.
control_floor_mib=${GATES_CONTROL_FLOOR_MIB:-1024}
memory_slack_mib=${GATES_MEMORY_SLACK_MIB:-512}
nominal_step_mib=${GATES_NOMINAL_STEP_MIB:-1024}
swap_reserve_mib=${GATES_SWAP_RESERVE_MIB:-8192}
memory_max_pin=${GATES_MEMORY_MAX_MIB:-}
memory_swap_max_pin=${GATES_MEMORY_SWAP_MAX_MIB:-}
oom_score_adjust=-900
is_positive_integer() { [[ $1 =~ ^[1-9][0-9]*$ ]]; }
for value in "$control_floor_mib" "$memory_slack_mib" "$nominal_step_mib" "$swap_reserve_mib"; do
  is_positive_integer "$value" || {
    echo "GATES_CONTROL_FLOOR_MIB/GATES_MEMORY_SLACK_MIB/GATES_NOMINAL_STEP_MIB/GATES_SWAP_RESERVE_MIB must be positive integers (MiB), got '$value'" >&2
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
# NOMINAL sizes, never raw kB: the install plan's own sizes are what the operator reasoned about, and a host whose
# kernel reserves a little reports 7935 MiB for its 8 GiB. Following raw kB instead moves the ceiling by a MiB every
# kernel revision: measured 2026-11-16, this 8 GiB test host came out one MiB BELOW the figure the operator had chosen
# for it, because the raw total is a sampling artifact of that kernel. So each total is rounded UP to the next multiple
# of the nominal step (one GiB by default) and the reserve subtracted: a 7935 MiB report and a 7936 MiB report both
# derive the 8 GiB host's ceiling.
nominal_mib() { printf '%s\n' "$(( ($1 + $2 - 1) / $2 * $2 ))"; }
total_mib=$(( mem_total_kb / 1024 ))
swap_mib=$(( swap_total_kb / 1024 ))
nominal_total_mib=$(nominal_mib "$total_mib" "$nominal_step_mib")
nominal_swap_mib=$(nominal_mib "$swap_mib" "$nominal_step_mib")
reserve_mib=$(( control_floor_mib + memory_slack_mib ))
(( nominal_total_mib > reserve_mib + 1024 )) || {
  echo "MemTotal ${total_mib} MiB (nominal ${nominal_total_mib} MiB) leaves nothing above the control-plane floor ${control_floor_mib} MiB plus slack ${memory_slack_mib} MiB plus 1024 MiB headroom; refusing the policy" >&2
  exit 3
}
memory_max_mib=$(( nominal_total_mib - reserve_mib ))
if [[ -n $memory_max_pin ]]; then
  memory_max_mib=$memory_max_pin
fi
(( memory_max_mib < total_mib )) || {
  echo "MemoryMax ${memory_max_mib}M is not below MemTotal ${total_mib}M (the host's raw RAM), so it protects no control-plane floor; use the derived value or a smaller pin" >&2
  exit 3
}
(( swap_mib > swap_reserve_mib )) || {
  echo "SwapTotal ${swap_mib} MiB is not above this host's swap reserve ${swap_reserve_mib} MiB, so the paging-by-design ceiling cannot be rendered; a host with no swap refuses BY DESIGN (this policy pages an oversized lane instead of killing it) and only a measured swap share may lower the reserve (set GATES_SWAP_RESERVE_MIB to it)" >&2
  exit 3
}
memory_swap_max_mib=$(( nominal_swap_mib - swap_reserve_mib ))
if [[ -n $memory_swap_max_pin ]]; then
  memory_swap_max_mib=$memory_swap_max_pin
fi
(( memory_swap_max_mib < swap_mib )) || {
  echo "MemorySwapMax ${memory_swap_max_mib}M is not below SwapTotal ${swap_mib}M (the host's raw swap), so it leaves this host no swap of its own; use the derived value or a smaller pin" >&2
  exit 3
}
memory_max_bytes=$(( memory_max_mib * 1024 * 1024 ))
memory_swap_max_bytes=$(( memory_swap_max_mib * 1024 * 1024 ))
printf 'gates memory policy: MemTotal %s MiB (nominal %s MiB, step %s MiB), SwapTotal %s MiB (nominal %s MiB); floor %s MiB, slack %s MiB, swap reserve %s MiB\n' \
  "$total_mib" "$nominal_total_mib" "$nominal_step_mib" "$swap_mib" "$nominal_swap_mib" "$control_floor_mib" "$memory_slack_mib" "$swap_reserve_mib"
printf 'gates memory policy: MemoryHigh=infinity MemoryMax=%sM MemorySwapMax=%sM OOMScoreAdjust=%s\n' \
  "$memory_max_mib" "$memory_swap_max_mib" "$oom_score_adjust"

# ---- rendering -------------------------------------------------------------
marker='# RENDERED by scripts/netcup/runner/provision-gates-limits.sh'
changed=0
retired=0

# Own each drop-in exactly: rewrite it when its bytes differ, report an unchanged file so a second run is visibly a no-op.
# Ownership is the marker as a PREFIX of the first line: a file that merely mentions this script's path is not ours.
owned_file() { case $(head -n 1 "$1" 2>/dev/null) in "$marker"*) return 0 ;; *) return 1 ;; esac; }

# A hand-configured drop-in is recognised, never assumed: every non-blank, non-comment line must be a section header
# naming EXACTLY the one section this file owns, or a directive whose key belongs to the policy this tool renders. An
# unknown section or key (CPUQuota, MemoryLow, a typo, a stray line) makes it foreign, so --adopt cannot overwrite a
# file whose meaning was never established.
recognised_hand_dropin() {  # $1 path, $2 expected section, $3 space-separated allowed keys
  awk -v want="$2" -v allowed=" $3 " '
    { line = $0; sub(/\r$/, "", line); sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line) }
    line == "" || line ~ /^#/ { next }
    line ~ /^\[[A-Za-z][A-Za-z0-9]*\]$/ {
      section = line
      if (line != "[" want "]") bad = 1
      next
    }
    {
      if (section == "") { bad = 1; next }
      eq = index(line, "=")
      if (eq < 2) { bad = 1; next }
      key = substr(line, 1, eq - 1); gsub(/[ \t]/, "", key)
      if (index(allowed, " " key " ") == 0) bad = 1
      seen = seen " " key
    }
    END { exit ((bad || seen == "") ? 1 : 0) }
  ' "$1"
}

# The content of a file this tool replaces is never deleted: it is copied to a name systemd does not load
# (`<name>.retired-<stamp>`), so an operator can still read what was superseded and restore it by hand.
retire_file() {  # $1 path
  local stamp target
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  target="$1.retired-$stamp"
  cp -p -- "$1" "$target"
  echo "gates memory policy: retired $1 -> $target (kept, not deleted; systemd does not load a .conf-less name)" >&2
  retired=1
}

render_file() {  # $1 path, $2 section, $3 allowed keys, $4 content
  local path=$1 section=$2 keys=$3 content=$4 tmp
  if [[ -e $path ]]; then
    if ! owned_file "$path"; then
      if ((adopt_requested)) && recognised_hand_dropin "$path" "$section" "$keys"; then
        if ((dry_run)); then
          echo "gates memory policy: would adopt the recognised hand drop-in $path (its content would be retired to $path.retired-<stamp>)"
        else
          retire_file "$path"
        fi
      else
        echo "refusing to override $path: its first line is not this tool's marker '$marker' and its content is not the policy this tool renders." >&2
        echo "gates memory policy: if this is the hand-configured 2026-11-16 drop-in of this host, adopt it once with --adopt (its content is kept as $path.retired-<stamp>); otherwise retire or relocate that file deliberately and re-run" >&2
        exit 5
      fi
    elif cmp -s -- "$path" <(printf '%s' "$content"); then
      echo "gates memory policy: unchanged $path"
      return 0
    fi
  fi
  if ((dry_run)); then
    echo "gates memory policy: would write $path"
    changed=1  # the plan changes something, so the reload it implies is printed too
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
scope_dropin="$systemd_dir/docker-.scope.d/20-oom-group.conf"
protected_units='ssh.service systemd-logind.service systemd-journald.service docker.service cgprofiled.service'

# Foreign files are checked BEFORE anything is written, so a refused run leaves no drop-in of ours behind (and no
# half-written policy an operator would have to notice in order to undo).
# A drop-in from another owner that sets a key this file owns would silently compete with it: a memory key is refused
# outright (its policy must be retired consciously), while a scope/service key is only refused when it disagrees.
other_dropins_setting() {  # $1 dir, $2 our file, $3 key regex; prints `path<TAB>value` for every other file setting it
  local dir=$1 ours=$2 pattern=$3 other
  [[ -d $dir ]] || return 0
  for other in "$dir"/*.conf; do
    [[ -e $other ]] || continue
    [[ $other == "$ours" ]] && continue
    owned_file "$other" && continue
    awk -v pat="^($pattern)=(.*)$" -v path="$other" '
      { line = $0; sub(/\r$/, "", line); sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line) }
      line ~ pat { value = line; sub(/^[^=]*=/, "", value); printf "%s\t%s\n", path, value }
    ' "$other"
  done
}
if [[ -d $systemd_dir/$slice.d ]]; then
  while IFS=$'\t' read -r other value; do
    [[ -n $other ]] || continue
    echo "refusing: $other also sets the gates-slice memory policy ($value); delete that file deliberately (for example the hand drop-in the 2026-11-16 decision supersedes, or adopt it with --adopt when it is at this tool's own path) and re-run" >&2
    exit 5
  done < <(other_dropins_setting "$systemd_dir/$slice.d" "$slice_dropin" 'MemoryHigh|MemoryMax|MemorySwapMax|MemoryOOMGroup|ManagedOOMMemoryPressure')
fi
# docker-.scope.d holds a fragment-rendered backstop (50-default-limits.conf) that sets memory but not OOMPolicy: only a
# file setting OOMPolicy to something ELSE competes with this drop-in. Equal values coexist and are reported.
while IFS=$'\t' read -r other value; do
  [[ -n $other ]] || continue
  if [[ $value != kill ]]; then
    echo "refusing: $other sets OOMPolicy=$value for transient docker scopes, which contradicts OOMPolicy=kill; delete that file deliberately and re-run" >&2
    exit 5
  fi
  echo "gates memory policy: $other also sets OOMPolicy=kill; kept as a duplicate of the same policy" >&2
done < <(other_dropins_setting "$systemd_dir/docker-.scope.d" "$scope_dropin" 'OOMPolicy')
# The dedicated-dev host protection of mdt host-setup renders the same -900 into a 50-*.conf of these units; any OTHER
# score would override this drop-in (a later fragment wins), so only a disagreeing value is refused.
for unit in $protected_units; do
  while IFS=$'\t' read -r other value; do
    [[ -n $other ]] || continue
    if [[ $value != "$oom_score_adjust" ]]; then
      echo "refusing: $other sets OOMScoreAdjust=$value on $unit, which contradicts OOMScoreAdjust=$oom_score_adjust; delete that file deliberately and re-run" >&2
      exit 5
    fi
    echo "gates memory policy: $other also protects $unit with OOMScoreAdjust=$value; kept as a duplicate" >&2
  done < <(other_dropins_setting "$systemd_dir/$unit.d" "$systemd_dir/$unit.d/30-oom-protect.conf" 'OOMScoreAdjust')
done

render_file "$slice_dropin" 'Slice' 'MemoryHigh MemoryMax MemorySwapMax' "$marker - do not edit; re-run the provisioning step to converge.
# Operator decision 2026-11-16 for a pure test host: protect only the measured control plane. No finite MemoryHigh
# (a high mark throttles the tier before the floor is ever at risk), so a fragment's own mark is overridden here.
# RAM is capped so the control plane keeps its floor; swap is capped to this host's own share so a lane larger than
# RAM PAGES by design instead of being killed.
# Derived from the NOMINAL size of this host (MemTotal rounded UP to the ${nominal_step_mib} MiB step: a host whose
# kernel reports ${total_mib} MiB is the ${nominal_total_mib} MiB host the operator bought) minus the control-plane reserve
# (floor ${control_floor_mib} MiB + slack ${memory_slack_mib} MiB); and SwapTotal (${swap_mib} MiB, nominal ${nominal_swap_mib} MiB) minus this host's own
# swap share of ${swap_reserve_mib} MiB. Raw-kB arithmetic would move the ceiling with every kernel revision.
# CPUQuota/TasksMax are host policy and are NOT rendered here.[Slice]
MemoryHigh=infinity
MemoryMax=${memory_max_mib}M
MemorySwapMax=${memory_swap_max_mib}M
"

render_file "$scope_dropin" 'Scope' 'OOMPolicy' "$marker - do not edit; re-run the provisioning step to converge.
# memory.oom.group=1 for every transient docker-<id>.scope: an OOM inside ONE container kills that container's whole
# process tree (one lane) instead of an arbitrary process of it. Deliberately NOT on the gates slice: a slice-level
# group would kill every lane in the tier when a single runaway exhausts it.
[Scope]
OOMPolicy=kill
"

# The control-plane drop-ins are rendered even for a unit that is not installed yet (cgprofiled arrives later in the
# bring-up): a drop-in for an absent unit is inert and applies when the unit appears, so no reinstall loses it.
for unit in $protected_units; do
  render_file "$systemd_dir/$unit.d/30-oom-protect.conf" 'Service' 'OOMScoreAdjust' "$marker - do not edit; re-run the provisioning step to converge.
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
    echo 'gates memory policy: would run systemctl daemon-reload at the end of the plan'
  else
    systemctl daemon-reload
    echo 'gates memory policy: reloaded systemd (daemon-reload)'
  fi
fi
if ((retired)) && ((!dry_run)); then
  echo 'gates memory policy: adopted a hand-configured drop-in (see the retired copy); the operator can now re-run this step as a no-op'
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
  else
    # Reported, not silently skipped: the drop-in above is staged regardless, so a unit that arrives later in the bring-up
    # (cgprofiled) still gets it, and an operator is told why no live score was written now.
    echo "gates memory policy: $unit is not installed on this host; its drop-in is staged and applies when the unit appears"
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
# Three outcomes, because "cannot tell whether it is idle" is not "it is idle": 0 has tasks (a restart is refused),
# 1 provably idle (every cgroup.procs under it reports no pid), 2 undeterminable (a directory is missing, unreadable, or a
# process list could not be read). A restart is the only operation that needs 1, so an unknown state fails closed.
slice_procs_state() {
  local cgdir=$1 file pid
  [[ -d $cgdir ]] || return 2
  local -a files=()
  mapfile -d '' -t files < <(find "$cgdir" -type f -name cgroup.procs -print0 2>/dev/null)
  (( ${#files[@]} > 0 )) || return 2
  for file in "${files[@]}"; do
    pid=
    IFS= read -r pid < "$file" || { [[ -e $file ]] || return 2; }
    [[ -n $pid ]] && return 0
  done
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
  cgdir=$(slice_cgroup) || {
    # A loaded unit without an active cgroup is a slice that has not been started yet (a freshly installed host, or a slice
    # deactivated after a completed run): there is no live limit to disagree with and no counter to reset, so nothing to do.
    if [[ $(systemctl show "$slice" -p ActiveState --value 2>/dev/null || true) == inactive ]]; then
      echo "gates memory policy: $slice is loaded but inactive (no live cgroup): the rendered drop-ins apply when it is first"
      echo "gates memory policy: started. Nothing to compare, nothing to restart, and no memory.events counter to reset."
    else
      echo "cannot resolve the cgroup of $slice (ControlGroup unreadable while ActiveState is '$(systemctl show "$slice" -p ActiveState --value 2>/dev/null || true)'): refusing to report a policy as applied" >&2
      exit 3
    fi
    cgdir=
  }
  if [[ -n $cgdir ]] && live_limits_ok "$cgdir"; then
    echo "gates memory policy: $slice live limits already match the policy ($cgdir)"
  elif [[ -n $cgdir ]] && ((dry_run)); then
    echo "gates memory policy: live limits disagree ($cgdir):"
    report_live "$cgdir"
    echo "gates memory policy: would restart $slice while idle (only if it has no tasks) to apply memory.high/max/swap.max"
  elif [[ -n $cgdir ]]; then
    # captured through an `if`, so a non-zero (idle or undeterminable) answer is a value here, not an exit under `set -e`
    if slice_procs_state "$cgdir"; then state=0; else state=$?; fi
    if ((state == 2)); then
      echo "gates memory policy: cannot determine whether $slice is idle (a cgroup.procs under $cgdir is missing or unreadable);" >&2
      echo "gates memory policy: refusing to restart it: an unknown state is not an idle state, and a restart would reset its" >&2
      echo "gates memory policy: memory.events oom_kill counter (what the INCONCLUSIVE-OOM check reads). Re-run when the tier is idle." >&2
      exit 3
    fi
    if ((state == 0)); then
      echo "gates memory policy: refusing to restart $slice: it has tasks ($cgdir). A restart would reset its memory.events oom_kill" >&2
      echo "gates memory policy: counter (what the INCONCLUSIVE-OOM check reads) and kill the running lane. Re-run when the tier is idle." >&2
      echo "gates memory policy: idle detection reads cgroup.procs under the slice; a task created between that read and the" >&2
      echo "gates memory policy: restart is not covered, so re-run only while the tier is known quiet (the 20 s window is why this" >&2
      echo "gates memory policy: step is not part of lane startup)." >&2
      exit 4
    fi
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
