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
#     PROTECTION IS NEVER REDUCED. oom_score_adj is a ranking, so a LOWER number is MORE protective: -1000 (what sshd
#     keeps on its own listener) is stronger than the unit's -900, and -900 is stronger than a packaged -250. The live
#     fix-up therefore writes a MainPID score only when the current value is HIGHER (less protective) than the target,
#     and the persistent drop-in is written only when the unit's own fragment sets nothing or a less protective value; a
#     unit that is already at least as protected is reported and left alone, and a drop-in this tool rendered EARLIER is
#     retired and removed when the unit now protects itself (leaving it would keep the downgrade alive).
# Nominal, not raw: see the derivation below (a host whose MemTotal reads 7935 MiB is an 8 GiB host).
# Idempotent: an unchanged drop-in is reported and left alone; a second run with a matching live cgroup changes nothing.
# The derivation, the overrides and their consequences are documented in runner/README.md ("Gates-tier memory policy").
#
# --adopt is the explicit one-time migration from a host an operator configured by hand (the 2026-11-16 measurement
# itself): a file at this tool's own path is recognised only when every non-comment line is a section header this file
# owns or a directive whose key belongs to the policy it renders. Its VALUES are not compared - a hand file legitimately
# writes `56G` where this tool renders `57344M` (the same amount, another spelling) - so the adopted file is rewritten as
# this tool's own rendering. Its content is COPIED to <file>.retired-<date> (a name systemd does not load) and never
# deleted. Without --adopt such a file is refused with exit 5, naming --adopt. A recognised hand drop-in that would
# LOWER a unit's own stronger value is adopted too - retired, and then NOT replaced, because the never-reduce rule
# outranks the policy value (that is exactly the v1001 migration shape: a hand -900 drop-in over a unit file at -1000).
#
# Exit codes: 0 applied, already converged, or a loaded-but-inactive slice (nothing live to compare or restart);
#             2 usage or an invalid override;
#             3 a required host fact is unreadable (MemTotal, SwapTotal, the slice's cgroup, its process lists, a
#               protected unit's own fragment, or a foreign drop-in whose value this tool cannot read) or there is no
#               swap to page with (a swapless host refuses by design: this policy pages lanes instead of killing them);
#             4 refused: the slice has tasks, so a restart would reset the memory.events counters INCONCLUSIVE-OOM reads;
#             5 refused: a file or drop-in this tool does not own carries the key it would set (or is not a recognised
#               hand drop-in even with --adopt), or a protected unit's own fragment is already MORE protective than the
#               rendered value while this tool's drop-in (which wins over the unit file) is installed there;
#             6 not root; 7 the live cgroup still disagrees after a restart; 8 a live OOM score could not be read or set
#               in place (an unreadable live score is not 0: the comparison is what keeps this tool from lowering it).
set -Eeuo pipefail
umask 022
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check

slice=${GATES_SLICE:-}
: "${slice:?GATES_SLICE is not set (runner.env: the gates tier slice name)}"
[[ $slice =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.slice$ ]] || { echo "GATES_SLICE is not a slice unit name: $slice" >&2; exit 2; }

# ==== helper definitions: every function this file calls, defined before the main flow runs ====
# Bash runs a script top to bottom, so a function called before its definition is `command not found` at run time - a
# defect a fake-root test cannot see when the failure is swallowed. Every definition therefore lives above the main
# flow, and tests/test_provision_gates_limits.py asserts that ordering (and that this region runs no code at all).
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

is_positive_integer() { [[ $1 =~ ^[1-9][0-9]*$ ]]; }

# oom_score_adj is a RANKING, not a magnitude: a LOWER value is MORE protective (-1000 strongest, +1000 weakest).
# `oom_score_number` is the ONE place a score token becomes a number: the kernel's own range is -1000..1000, so at most
# four digits, forced to base 10. A leading zero would otherwise reach bash arithmetic as OCTAL (`0900` has no octal
# spelling and aborts the comparison), and a longer digit string can wrap a 64-bit integer back INTO the valid range;
# both are refused by the pattern, before any arithmetic runs. An unusable score is never folded into "not protected":
# that fold's only consequence is a write, the one operation that can LOWER a unit's protection.
oom_score_number() {  # $1 a score token; prints its value in base 10, 1 when the token is not a usable score at all
  local sign=1 digits
  case ${1-} in
    -*) sign=-1; digits=${1#-} ;;
    *) digits=${1-} ;;
  esac
  [[ $digits =~ ^[0-9]{1,4}$ ]] || return 1
  printf '%s\n' "$(( 10#$digits * sign ))"
}
usable_oom_score() { local n; n=$(oom_score_number "${1-}") || return 1; (( n >= -1000 && n <= 1000 )); }
# True when $1 protects at least as strongly as $2 (equal or lower), and never for an unusable value.
more_protective() { local a b; a=$(oom_score_number "${1-}") || return 1; b=$(oom_score_number "${2-}") || return 1; (( a <= b )); }

# FACT_VALUE is set by read_fact; its status is the answer (0 read, 1 absent or unreadable). "Cannot read" is never
# folded into "there is none": a host fact that exists elsewhere must not be replaced by an invented number.
read_fact() {
  local key=$1 value
  FACT_VALUE=
  [[ -r $meminfo ]] || return 1
  value=$(awk -v k="$key" '$1 == k { print $2; exit }' "$meminfo" 2>/dev/null) || return 1
  [[ -n $value && $value =~ ^[0-9]+$ ]] || return 1
  FACT_VALUE=$value
}

nominal_mib() { printf '%s\n' "$(( ($1 + $2 - 1) / $2 * $2 ))"; }

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

# The content of a file this tool replaces or withdraws is never deleted: it is copied to a name systemd does not load
# (`<name>.retired-<stamp>`), so an operator can still read what was superseded and restore it by hand.
retire_file() {  # $1 path
  local stamp target
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  target="$1.retired-$stamp"
  cp -p -- "$1" "$target"
  echo "gates memory policy: retired $1 -> $target (kept, not deleted; systemd does not load a .conf-less name)" >&2
  retired=1
}

# A path this run has decided to take away is RECORDED during the decision phase and mutated only afterwards, once every
# own-path pre-flight has passed: a run that refuses must leave the host exactly as it found it.
plan_retire() { retire_paths+=("$1|$2"); }
# A refusal is RECORDED, never raised where it is discovered: a run names every file at this tool's own paths that it
# cannot touch and exits once at the end of the pre-flight, so an operator sees the whole list in one run.
plan_refusal() {  # $1 path, $2 the reason (each line echoed exactly as it is)
  refusal_paths+=("$1")
  local line
  while IFS= read -r line; do
    echo "$line" >&2
  done <<< "$2"
}
scheduled_for_retire() {  # $1 path; true when the decision phase already planned to take that file away
  local entry
  for entry in "${retire_paths[@]}"; do
    [[ ${entry%%|*} == "$1" ]] && return 0
  done
  return 1
}
apply_retires() {
  local entry path reason
  for entry in "${retire_paths[@]}"; do
    path=${entry%%|*}
    reason=${entry#*|}
    if ((dry_run)); then
      echo "gates memory policy: would remove $path (retiring its content to $path.retired-<stamp>; $reason)"
    else
      retire_file "$path"
      rm -f -- "$path"
      rmdir --ignore-fail-on-non-empty "$(dirname -- "$path")" 2>/dev/null || true
      echo "gates memory policy: removed $path (retiring its content; $reason)" >&2
    fi
    changed=1
  done
}

render_file() {  # $1 path, $2 section, $3 allowed keys, $4 content
  local path=$1 section=$2 keys=$3 content=$4 tmp
  if [[ -e $path ]]; then
    if ! owned_file "$path"; then
      if ((adopt_requested)) && recognised_hand_dropin "$path" "$section" "$keys"; then
        if ((dry_run)); then
          : # announced by the pre-flight check, which names every adoptable file before anything is written
        else
          retire_file "$path"
        fi
      else
        # Defence in depth: the pre-flight above already refused this exact condition before anything was written. It
        # is kept here so the write path itself can never overwrite a file whose meaning was not established.
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

# The value the named SECTION of a file sets for one key (the last one wins, as systemd reads it). Status 1 means the
# file itself could not be read - which is NOT the same answer as "the section sets no such key" (that printed nothing),
# because only the second one lets a caller conclude there is no competing policy there.
unit_section_value() {  # $1 file, $2 section, $3 key; prints the value (empty when absent), 1 when the file is unreadable
  local file=$1 value
  [[ -r $file ]] || return 1
  value=$(awk -v want="$2" -v key="$3" '
    { line = $0; sub(/\r$/, "", line); sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line) }
    line == "" || line ~ /^#/ || line ~ /^;/ { next }
    line ~ /^\[[A-Za-z][A-Za-z0-9]*\]$/ { inside = (line == "[" want "]"); next }
    inside && index(line, key "=") == 1 { value = substr(line, length(key) + 2) }
    END { if (value != "") printf "%s\n", value }
  ' "$file") || return 1
  printf '%s\n' "$value"
}

# The OOMScoreAdjust a unit's OWN unit file sets, empty when it sets none.
unit_oom_own_fragment() {  # $1 file
  awk '
    { line = $0; sub(/\r$/, "", line); sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line) }
    line == "" || line ~ /^#/ || line ~ /^;/ { next }
    line ~ /^\[[A-Za-z][A-Za-z0-9]*\]$/ { service = (line == "[Service]"); next }
    service && line ~ /^OOMScoreAdjust=/ { value = line; sub(/^OOMScoreAdjust=/, "", value) }
    END { if (value != "") printf "%s\n", value }
  ' "$1" 2>/dev/null
}
# The OWN value only: `systemctl show -p FragmentPath` names the unit file itself, so a drop-in this tool rendered for
# an earlier run is not mistaken for the unit's own protection. Only a unit without a readable fragment (which is how a
# not-installed unit answers) falls back to the effective property, and that fallback can only make this tool write
# less, never more.
own_oom_score() {  # $1 unit; prints the value its own fragment sets (may be empty), 1 when it is unreadable
  local unit=$1 fragment
  fragment=$(systemctl show "$unit" -p FragmentPath --value 2>/dev/null) || return 1
  if [[ -n $fragment ]]; then
    [[ -r $fragment ]] || return 1
    unit_oom_own_fragment "$fragment" || return 1
    return 0
  fi
  systemctl show "$unit" -p OOMScoreAdjust --value 2>/dev/null || return 1
}
# The EFFECTIVE score systemd would apply: the unit file MERGED with every drop-in, including one under /usr/lib that
# this tool never writes to and therefore cannot see in `$systemd_dir`. It is used for ONE decision only - whether a
# rendered drop-in would LOWER a protection that already exists - and never as the answer to "does the unit protect
# itself?" (that is the own fragment's answer, because the effective value already includes a drop-in this tool
# rendered on an earlier run). Status 1 means systemd could not answer.
effective_oom_score() {  # $1 unit; prints the effective value (empty when nothing sets one), 1 when systemd cannot answer
  systemctl show "$1" -p OOMScoreAdjust --value 2>/dev/null
}

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
  # The status of this read is kept only so `set -e` does not abort on it: the VALUE is validated below, and an
  # unreadable one is an error (exit 8), never an assumed 0.
  current=$(<"$adj_file") || true
  # An unreadable score is not "unprotected 0": the only thing this comparison gates is a WRITE, and a write on an
  # assumed 0 is exactly how a value nobody read gets replaced by a weaker one. Refuse instead.
  if ! usable_oom_score "$current"; then
    echo "cannot read the live oom_score_adj of $unit (pid $pid, $adj_file said '${current}'): refusing to overwrite a score" >&2
    echo "this tool could not read. Its drop-in is installed and applies at its next start." >&2
    exit 8
  fi
  if more_protective "$current" "$oom_score_adjust"; then
    # Already at least as protected as the policy: sshd keeps its own listener at -1000 (it protects itself), containerd
    # at -999, and an equal -900 needs no write either (writing the value it already holds would be a pointless write on
    # a live process). Not one of them is "converged" down to -900: a re-run leaves a stronger value alone and SAYS so.
    if [[ $current != "$oom_score_adjust" ]]; then
      echo "gates memory policy: $unit (pid $pid) is already at least as protected as the policy: oom_score_adj $current"
      echo "gates memory policy: (>= $oom_score_adjust protection; left alone, never raised)"
    fi
    return 0
  fi
  if ((dry_run)); then
    echo "gates memory policy: would set $unit (pid $pid) oom_score_adj $current -> $oom_score_adjust"
    return 0
  fi
  if ! printf '%s\n' "$oom_score_adjust" > "$adj_file" 2>/dev/null; then
    echo "cannot set the live OOM score of $unit (pid $pid, $adj_file); its drop-in is installed and applies at its next start" >&2
    exit 8
  fi
  echo "gates memory policy: set $unit (pid $pid) oom_score_adj $current -> $oom_score_adjust in place (no restart)"
}

# daemon-reload alone does NOT re-apply cgroup limits to an active slice (measured 2026-11-16), and a slice restart
# resets its memory.events counters - the ones remote-gate's INCONCLUSIVE-OOM check reads as a delta. So the restart
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

# A value systemd understands is compared in BYTES; anything this tool cannot convert (a percentage, a typo) is reported,
# because the tool cannot show it is harmless.
memory_bytes() {  # $1 systemd memory value; prints bytes, 1 when the value is not a plain size this tool can convert
  local value=$1 number unit multiplier
  if [[ $value =~ ^([0-9]+)([KMGTkmgt])$ ]]; then
    number=${BASH_REMATCH[1]}
    unit=${BASH_REMATCH[2]^^}
    case $unit in
      K) multiplier=1024 ;;
      M) multiplier=$(( 1024 * 1024 )) ;;
      G) multiplier=$(( 1024 * 1024 * 1024 )) ;;
      T) multiplier=$(( 1024 * 1024 * 1024 * 1024 )) ;;
    esac
    printf '%s' $(( number * multiplier ))
    return 0
  fi
  [[ $value =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$value"
}
same_memory_amount() {  # $1, $2; true when both are the same number of bytes (or the same non-size token)
  [[ $1 == "$2" ]] && return 0
  local a b
  a=$(memory_bytes "$1") || return 1
  b=$(memory_bytes "$2") || return 1
  [[ $a == "$b" ]]
}
# A fragment that carries a memory key would be OVERRIDDEN by this drop-in (a drop-in is merged after the unit file), so
# an operator deserves to read that. But a note printed on every run of a converged host is noise nobody reads, and
# "differs" is a VALUE comparison, not the existence of the file: a fragment may spell the same amount another way
# (`6G` vs `6144M`) and a hand-drawn fragment may carry only CPU keys.
slice_fragment_override_note() {
  local fragment key want own
  fragment=$(systemctl show "$slice" -p FragmentPath --value 2>/dev/null || true)
  [[ -n $fragment ]] || return 0
  for spec in "MemoryHigh:infinity" "MemoryMax:${memory_max_mib}M" "MemorySwapMax:${memory_swap_max_mib}M"; do
    key=${spec%%:*}
    want=${spec#*:}
    [[ -r $fragment ]] || return 0
    own=$(unit_section_value "$fragment" Slice "$key") || return 0
    [[ -n $own ]] || continue
    same_memory_amount "$own" "$want" && continue
    echo "gates memory policy: $slice has a fragment ($fragment) that sets $key=$own; this drop-in deliberately overrides it"
    echo "gates memory policy: with the 2026-11-16 pure-test-host policy value $key=$want (a foreign drop-in that sets it is refused above)"
    return 0
  done
  return 0
}
# ==== end of helper definitions =============================================================

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

# ---- rendering: what this tool owns ----------------------------------------
marker='# RENDERED by scripts/netcup/runner/provision-gates-limits.sh'
changed=0
retired=0
slice_dropin="$systemd_dir/$slice.d/20-memory-policy.conf"
scope_dropin="$systemd_dir/docker-.scope.d/20-oom-group.conf"
protected_units='ssh.service systemd-logind.service systemd-journald.service docker.service cgprofiled.service'
oom_protect_units=()   # units that get the rendered drop-in
retire_paths=()        # `path|reason` for files this run withdraws (decided here, mutated at the end of the pre-flight)
refusal_paths=()       # own paths this run refuses to touch; every one is named before the single exit
autoadopt=0            # own paths whose recognised hand content is adopted by the render below

# ---- host facts ------------------------------------------------------------
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

# ---- foreign files: named before anything is written ------------------------
# A drop-in from another owner that sets a key this file owns would silently compete with it: a memory key is refused
# outright (its policy must be retired consciously), while a scope/service key is only refused when it disagrees.
if [[ -d $systemd_dir/$slice.d ]]; then
  # Every competing FILE is named before the refusal: an operator who is told about one of five hand files fixes one and
  # meets the next on the following run. The loop completes before the exit, so a plain --dry-run lists them all too.
  foreign_slice=()
  while IFS=$'\t' read -r other value; do
    [[ -n $other ]] || continue
    echo "refusing: $other also sets the gates-slice memory policy ($value); delete that file deliberately (for example the hand drop-in the 2026-11-16 decision supersedes, or adopt it with --adopt when it is at this tool's own path) and re-run" >&2
    [[ " ${foreign_slice[*]-} " == *" $other "* ]] || foreign_slice+=("$other")
  done < <(other_dropins_setting "$systemd_dir/$slice.d" "$slice_dropin" 'MemoryHigh|MemoryMax|MemorySwapMax|MemoryOOMGroup|ManagedOOMMemoryPressure')
  if (( ${#foreign_slice[@]} )); then
    echo "gates memory policy: ${#foreign_slice[@]} competing file(s) named above; nothing was written. Run --adopt --dry-run to see every recognised hand drop-in at once" >&2
    exit 5
  fi
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
# score would override this drop-in or be overridden by it (a later fragment wins), so only a disagreeing value is refused.
# A MORE protective value in another owner's drop-in is already stronger than the policy (for example a packaged unit
# setting -1000 on its listener): it is reported as protection kept, never "corrected" down to the policy value.
for unit in $protected_units; do
  while IFS=$'\t' read -r other value; do
    [[ -n $other ]] || continue
    if [[ $value != "$oom_score_adjust" ]] && ! more_protective "$value" "$oom_score_adjust"; then
      echo "refusing: $other sets OOMScoreAdjust=$value on $unit, which contradicts OOMScoreAdjust=$oom_score_adjust; delete that file deliberately and re-run" >&2
      exit 5
    fi
    if more_protective "$value" "$oom_score_adjust" && [[ $value != "$oom_score_adjust" ]]; then
      echo "gates memory policy: $other keeps $unit more protected (OOMScoreAdjust=$value) than the policy value $oom_score_adjust; kept, never lowered" >&2
    else
      echo "gates memory policy: $other also protects $unit with OOMScoreAdjust=$value; kept as a duplicate" >&2
    fi
  done < <(other_dropins_setting "$systemd_dir/$unit.d" "$systemd_dir/$unit.d/30-oom-protect.conf" 'OOMScoreAdjust')
done

# ---- the unit's OWN value: protection is never reduced ----------------------
# A drop-in WINS over the unit's own fragment (systemd merges the unit file first and the drop-in later), so writing
# `OOMScoreAdjust=-900` into a unit whose packaged fragment already says -1000 or -999 REPLACES a stronger value with a
# weaker one. That is a downgrade that neither the convergence report nor a live cgroup read can reveal, so it is
# decided here, once per unit, from the value the unit's OWN fragment sets (never the effective value, which already
# includes a drop-in this tool may have written earlier):
#   own value unset/0    -> render the policy (nothing is being weakened);
#   own value <= target  -> render NOTHING and say so, because -1000 (sshd protects its own listener) and -999
#                           (containerd's) must survive a re-run instead of being "converged" down to -900. A file at
#                           this tool's own path is then either WITHDRAWN (this tool rendered it earlier, so it would
#                           otherwise keep the downgrade alive) or, with --adopt, an adopted hand file is retired and
#                           withdrawn too; another owner's equal-or-more-protective file is kept, and a less protective
#                           one is refused (it would replace the unit's stronger value at the next start);
#   own value > target   -> render the path only when this tool already owns a drop-in there (the value then came from
#                           an earlier run of this tool) or --adopt retires a recognised hand file; another owner's drop-in
#                           at that path competes and is refused;
#   not installed        -> stage the drop-in anyway (it is inert until the unit appears, so no bring-up loses it);
#   installed, unreadable-> exit 3: a value that exists somewhere else is never replaced by an invented one.
# Every decision is taken BEFORE any file is written, so a refusal still leaves no half-written policy behind.
for unit in $protected_units; do
  ours="$systemd_dir/$unit.d/30-oom-protect.conf"
  if ! systemctl cat "$unit" >/dev/null 2>&1; then
    oom_protect_units+=("$unit")  # not installed: the drop-in is inert until the unit appears
    continue
  fi
  if ! own=$(own_oom_score "$unit"); then
    echo "cannot read the OOMScoreAdjust of $unit's own unit file: refusing to render a drop-in that might replace a" >&2
    echo "protection this tool never read. Its installed drop-in (if any) is untouched; repair the unit and re-run." >&2
    exit 3
  fi
  [[ -n $own ]] || own=0
  if ! usable_oom_score "$own"; then
    echo "the OOMScoreAdjust of $unit's own unit file is not a usable score ('$own'): refusing to render a drop-in that might" >&2
    echo "replace it. Repair the unit file and re-run." >&2
    exit 3
  fi
  if more_protective "$own" "$oom_score_adjust"; then
    if [[ $own != 0 ]]; then
      echo "gates memory policy: $unit's own unit file sets OOMScoreAdjust=$own, at least as protective as the policy value" >&2
      echo "gates memory policy: $oom_score_adjust; no drop-in is rendered for it (a drop-in would REPLACE that value and lower its" >&2
      echo "gates memory policy: protection). The live score still follows its own rule: written only when it is less protective." >&2
    fi
    if [[ ! -e $ours ]]; then
      continue
    fi
    if owned_file "$ours"; then
      # Our own file from an earlier run would still win over the unit's own stronger value at the next start: leaving it
      # there would keep the very downgrade this decision refuses. It is WITHDRAWN (its content retired, never deleted).
      plan_retire "$ours" "this tool rendered it earlier; $unit now protects itself at $own"
      continue
    fi
    if ((adopt_requested)) && recognised_hand_dropin "$ours" Service OOMScoreAdjust; then
      # The v1001 migration shape: a hand -900 drop-in at this tool's own path over a unit file that sets -1000. The
      # never-reduce rule outranks the policy value, so the hand file is adopted (retired) and then NOT replaced.
      echo "gates memory policy: adopting the hand drop-in $ours (its content is retired), and NOT replacing it: $unit's own" >&2
      echo "gates memory policy: unit file sets OOMScoreAdjust=$own, which a rendered $oom_score_adjust would weaken." >&2
      plan_retire "$ours" "adopted hand drop-in; $unit's own $own is stronger, so no drop-in replaces it"
      continue
    fi
    if ! foreign=$(unit_section_value "$ours" Service OOMScoreAdjust); then
      echo "cannot read $ours, the drop-in at this tool's own path for $unit: refusing to decide whether it would lower that" >&2
      echo "unit's own OOMScoreAdjust=$own. Repair or retire that file and re-run." >&2
      exit 3
    fi
    if [[ -z $foreign ]]; then
      plan_refusal "$ours" "refusing to override $ours: its first line is not this tool's marker '$marker' and its content is not the policy this tool renders.
gates memory policy: if this is the hand-configured 2026-11-16 drop-in of this host, adopt it once with --adopt (its content is kept as $ours.retired-<stamp>); otherwise retire or relocate that file deliberately and re-run"
      continue
    fi
    if more_protective "$foreign" "$own"; then
      echo "gates memory policy: $ours keeps $unit at OOMScoreAdjust=$foreign, equal to or more protective than the unit's own $own; kept, never lowered" >&2
      continue
    fi
    plan_refusal "$ours" "refusing: $ours (another owner's drop-in) sets OOMScoreAdjust=$foreign, which is less protective than $unit's own $own and would replace it at the next start; resolve that file deliberately and re-run"
    continue
  fi
  # own > target: the unit's own file does not protect it as strongly as the policy - but a protection may still
  # exist in a drop-in this tool does not own (a packaged one under /usr/lib/<unit>.d, which this tool never writes to
  # and cannot enumerate from $systemd_dir). Rendering -900 there would REPLACE that stronger value whenever our file
  # sorts later, so the effective score systemd would apply is read once, and when it is already at least as protective
  # as the policy nothing is rendered: the same never-lower rule, applied to a file this tool cannot see.
  if [[ ! -e $ours ]]; then
    if ! effective=$(effective_oom_score "$unit"); then
      echo "cannot read the effective OOMScoreAdjust of $unit, so this tool cannot tell whether a drop-in it does not own" >&2
      echo "already protects it more strongly than the policy value $oom_score_adjust; refusing to render a drop-in that" >&2
      echo "might replace that protection. Nothing was written; re-run when 'systemctl show' answers for this unit." >&2
      exit 3
    fi
    # An empty or unusable answer is an UNKNOWN, not "unprotected": the only thing this comparison gates is a write, and
    # writing here would replace a protection nobody read. systemd always reports this property (0 when nothing sets it).
    if ! usable_oom_score "$effective"; then
      echo "the effective OOMScoreAdjust of $unit is not a usable score ('${effective}'): refusing to render a drop-in that" >&2
      echo "might replace a protection this tool never read. Nothing was written; repair the unit and re-run." >&2
      exit 3
    fi
    if more_protective "$effective" "$oom_score_adjust" && [[ $effective != "$oom_score_adjust" ]]; then
      echo "gates memory policy: $unit is already more protected than the policy value $oom_score_adjust by a drop-in this" >&2
      echo "gates memory policy: tool does not own (effective OOMScoreAdjust=$effective); no drop-in is rendered for it, because" >&2
      echo "gates memory policy: a rendered $oom_score_adjust would replace that stronger value. Check /usr/lib/systemd/system/$unit.d." >&2
      continue
    fi
  fi
  # The policy STRENGTHENS the unit, so the drop-in is rendered - but only over a file this tool already
  # owns, or one --adopt may legitimately retire (a recognised hand drop-in: its value is weaker than the policy, so
  # adopting it loses no protection). Anyone else's file at that path is a competing policy and is refused.
  if [[ -e $ours ]] && ! owned_file "$ours"; then
    if ((adopt_requested)) && recognised_hand_dropin "$ours" Service OOMScoreAdjust; then
      oom_protect_units+=("$unit")  # the render retires the adopted hand file and writes the rendered policy there
      continue
    fi
    plan_refusal "$ours" "refusing: $unit's own unit file sets OOMScoreAdjust=$own, which is less protective than the policy value $oom_score_adjust, and another owner's drop-in at $ours is in the way; adopt it once with --adopt when it is the hand-configured 2026-11-16 file, otherwise resolve it deliberately and re-run"
    continue
  fi
  oom_protect_units+=("$unit")
done

# Own paths are inspected ONCE, before anything is written: a plain --dry-run names every foreign file at this tool's
# own paths (the slice policy, the docker-scope policy and every protected unit) instead of stopping at the first one (an
# operator who fixes one file and meets the next on the following run learns nothing about the rest), and an adoptable
# hand drop-in is announced in the plan. The render below then either retires the adopted file or writes the rendered
# one; the pre-flight is what reports the plan.
own_paths=("$slice_dropin|Slice|MemoryHigh MemoryMax MemorySwapMax" "$scope_dropin|Scope|OOMPolicy")
for unit in "${oom_protect_units[@]}"; do
  own_paths+=("$systemd_dir/$unit.d/30-oom-protect.conf|Service|OOMScoreAdjust")
done
for entry in "${own_paths[@]}"; do
  path=${entry%%|*}
  rest=${entry#*|}
  section=${rest%%|*}
  keys=${rest#*|}
  [[ -e $path ]] || continue
  owned_file "$path" && continue
  scheduled_for_retire "$path" && continue
  if ((adopt_requested)) && recognised_hand_dropin "$path" "$section" "$keys"; then
    autoadopt=$(( autoadopt + 1 ))
    if ((dry_run)); then
      echo "gates memory policy: would adopt the recognised hand drop-in $path (its content would be retired to $path.retired-<stamp>)"
    fi
    continue
  fi
  plan_refusal "$path" "refusing to override $path: its first line is not this tool's marker '$marker' and its content is not the policy this tool renders.
gates memory policy: if this is the hand-configured 2026-11-16 drop-in of this host, adopt it once with --adopt (its content is kept as $path.retired-<stamp>); otherwise retire or relocate that file deliberately and re-run"
done
# One refusal for every own path, after every pre-flight: nothing was written yet, and an operator sees the whole list.
if (( ${#refusal_paths[@]} )); then
  echo "gates memory policy: ${#refusal_paths[@]} file(s) at this tool's own paths cannot be adopted, and nothing was written; run --adopt --dry-run to review every recognised hand drop-in at once" >&2
  exit 5
fi

# ---- the mutations the decisions asked for ---------------------------------
apply_retires
if ((autoadopt)) && ((!dry_run)); then
  echo "gates memory policy: adopting $autoadopt recognised hand drop-in(s); their content is retired, never deleted" >&2
fi

render_file "$slice_dropin" 'Slice' 'MemoryHigh MemoryMax MemorySwapMax' "$marker - do not edit; re-run the provisioning step to converge.
# Operator decision 2026-11-16 for a pure test host: protect only the measured control plane. No finite MemoryHigh
# (a high mark throttles the tier before the floor is ever at risk), so a fragment's own mark is overridden here.
# RAM is capped so the control plane keeps its floor; swap is capped to this host's own share so a lane larger than
# RAM PAGES by design instead of being killed.
# Derived from the NOMINAL size of this host (MemTotal rounded UP to the ${nominal_step_mib} MiB step: a host whose
# kernel reports ${total_mib} MiB is the ${nominal_total_mib} MiB host the operator bought) minus the control-plane reserve
# (floor ${control_floor_mib} MiB + slack ${memory_slack_mib} MiB); and SwapTotal (${swap_mib} MiB, nominal ${nominal_swap_mib} MiB) minus this host's own
# swap share of ${swap_reserve_mib} MiB. Raw-kB arithmetic would move the ceiling with every kernel revision.
# CPUQuota/TasksMax are host policy and are NOT rendered here.
[Slice]
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
for unit in "${oom_protect_units[@]}"; do
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
# Every protected unit, not only the ones due a drop-in: a main process that is unprotected NOW is brought to the policy
# value even when the unit's own file protects it stronger (that write RAISES protection), while apply_live_score itself
# refuses to lower anything already at least as protected.
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
load_state=$(systemctl show "$slice" -p LoadState --value 2>/dev/null || true)
if [[ $load_state != loaded ]]; then
  echo "gates memory policy: $slice is not installed on this host (LoadState '${load_state:-unknown}'), so nothing was applied to a"
  echo "gates memory policy: live slice. The drop-ins above take effect when the unit appears: mdt host-setup renders and reloads"
  echo "gates memory policy: it (P5/P6), or the marker-unit step of the no-host-setup path installs it. Then re-run this step."
else
  slice_fragment_override_note
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
