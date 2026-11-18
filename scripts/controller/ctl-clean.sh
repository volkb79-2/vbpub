#!/usr/bin/env bash
# ctl-clean.sh - remove finished-run scratch from the checkout's .run-gate.
#
# One recursive traversal decides mtime activity. Top-level entries are then
# classified by a mount snapshot and one /proc scan. Apply quarantines entries,
# rechecks activity and live process references, restores anything still active,
# and deletes the rest without crossing a filesystem boundary.
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_NAMESPACE

usage() {
  printf '%s\n' \
    'Usage: ctl-clean.sh [--apply] [--age-hours N] [--i-know-the-age] [--repo DIR]' \
    '' \
    'Dry run by default. Default age: max(48, longest lane budget + 1) hours.' \
    'Activity is any file below an entry newer than the age. Apply is for a' \
    'quiet host with no gate running.' \
    '' \
    '  --apply              quarantine, recheck and delete inactive entries' \
    '  --age-hours N        decimal integer 1..8760, subject to the budget floor' \
    '  --i-know-the-age     lower only the lane-budget floor when applying' \
    '  --repo DIR           worktree root to inspect (default: current root)'
}

die() { printf 'ctl-clean: %s\n' "$*" >&2; exit 2; }
inspect_fail() {
  inspection_failed=$((inspection_failed + 1))
  printf 'ctl-clean: %s\n' "$*" >&2
  summary
  exit 1
}
parse_age_hours() {
  local raw=$1 zeros digits
  [[ -n $raw ]] || die '--age-hours requires a non-empty integer from 1 to 8760'
  case $raw in *[!0-9]*) die "--age-hours must be an integer from 1 to 8760 (got '$raw')" ;; esac
  zeros=${raw%%[!0]*}
  digits=${raw#"$zeros"}
  [[ -n $digits ]] || digits=0
  ((${#digits} <= 4)) || die "--age-hours must be an integer from 1 to 8760 (got '$raw')"
  AGE_H=$((10#$digits))
  ((AGE_H > 0 && AGE_H <= 8760)) ||
    die "--age-hours must be an integer from 1 to 8760 (got '$raw')"
}
under() { [[ $1 == "$2" || $1 == "$2/"* ]]; }

APPLY=0 AGE_SET=0 KNOW_AGE=0 AGE_H=48
START_DIR=$(pwd -P) || die 'cannot determine the current directory'
REPO=$(git -C "$START_DIR" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$START_DIR")
while (($# > 0)); do
  case $1 in
    --apply) APPLY=1 ;;
    --age-hours)
      (($# >= 2)) || die '--age-hours requires an integer from 1 to 8760'
      parse_age_hours "$2"; AGE_SET=1; shift ;;
    --i-know-the-age) KNOW_AGE=1 ;;
    --repo)
      (($# >= 2)) || die '--repo requires a directory'
      [[ -n $2 ]] || die '--repo requires a non-empty directory'
      case $2 in -*) die "--repo must not start with '-' (got '$2')" ;; esac
      REPO=$2; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
  shift
done

requested=$(cd -- "$REPO" 2>/dev/null && pwd -P) || die "--repo is not a directory: $REPO"
root=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) ||
  die "--repo is not inside a Git worktree: $REPO"
root=$(cd -- "$root" 2>/dev/null && pwd -P) || die "cannot resolve the Git worktree root for: $REPO"
[[ $root == "$requested" ]] ||
  die "--repo must name the worktree root, not a subdirectory or unrelated path (got '$requested', root is '$root')"
REPO=$requested
cd -- "$REPO" || die "cannot enter repo: $REPO"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/ctl-clean.XXXXXX") || die 'cannot create a temporary directory'
QUARANTINE=
cleanup() {
  local status=$? path
  if [[ -n $QUARANTINE && -d $QUARANTINE ]] && ! rmdir -- "$QUARANTINE" 2>/dev/null; then
    printf 'ctl-clean: WARNING quarantined entries remain at %s\n' "$QUARANTINE" >&2
    path=$QUARANTINE
    printf 'ctl-clean: remove with: rm -rf --one-file-system -- %q\n' "$path" >&2
  fi
  rm -rf -- "$TMP"
  return "$status"
}
trap cleanup EXIT

eligible=0 removed=0 failed=0 inspection_failed=0
summary() {
  printf 'ctl-clean: eligible %d, removed %d, failed %d, inspection-failed %d scratch entries\n' \
    "$eligible" "$removed" "$failed" "$inspection_failed"
}

RG=$REPO/.run-gate
if [[ -L $RG ]]; then
  inspect_fail '.run-gate is a symlink; refusing to traverse or delete through it'
elif [[ -e $RG && ! -d $RG ]]; then
  inspect_fail '.run-gate is not a directory'
fi

declare -A LEFTOVER=() CANDIDATE=() ACTIVE=() MOUNTED=() PROC_ACTIVE=() ELIGIBLE=()
if [[ -d $RG ]]; then
  if ! find "$RG" -mindepth 1 -maxdepth 1 -name '.ctl-clean-quarantine-*' -print0 \
      > "$TMP/leftovers"; then
    inspect_fail 'cannot inspect existing ctl-clean quarantine entries'
  fi
  mapfile -d '' -t rows < "$TMP/leftovers" ||
    inspect_fail 'cannot read existing ctl-clean quarantine entries'
  for name in "${rows[@]}"; do LEFTOVER[${name#"$RG"/}]=1; done
fi

BUDGET_REQUIRED=48 BUDGET_DURATION='' BUDGET_FILE='' BUDGET_HOURS=''
if ! find "$REPO" -maxdepth 3 -path "$RG" -prune -o \
    \( -name run-gate.toml -o -name assay.toml \) \( -type f -o -type l \) \
    -print0 > "$TMP/budget-files"; then
  inspect_fail 'cannot discover lane budget files'
fi
if [[ -s $TMP/budget-files ]]; then
  command -v python3 >/dev/null 2>&1 ||
    inspect_fail 'python3 is required to read lane budgets'
  if ! python3 - "$TMP/budget-files" > "$TMP/budgets" 2> "$TMP/budget.err" <<'PY'
from pathlib import Path
import math
import re
import sys
import tomllib

kind = {"s": 1 / 3600, "m": 1 / 60, "h": 1, "d": 24}
longest = None


def walk(value, source):
    global longest
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "budget":
                if not isinstance(child, str):
                    raise ValueError(f"{source}: budget {child!r} is not a string duration")
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([smhd])", child)
                if match is None:
                    raise ValueError(
                        f"{source}: budget {child!r} is not a positive duration ending in s/m/h/d"
                    )
                number = float(match.group(1))
                if number <= 0:
                    raise ValueError(f"{source}: budget {child!r} must be greater than zero")
                hours = number * kind[match.group(2)]
                if longest is None or hours > longest[0]:
                    longest = (hours, child, source)
            else:
                walk(child, source)
    elif isinstance(value, list):
        for child in value:
            walk(child, source)


try:
    files = Path(sys.argv[1]).read_bytes().split(b"\0")
except OSError as exc:
    raise SystemExit(f"cannot read lane budget file list: {exc}")
for raw in files:
    if not raw:
        continue
    source = raw.decode("utf-8", "surrogateescape")
    try:
        with open(source, "rb") as stream:
            parsed = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"cannot parse lane budget {source}: {exc}")
    try:
        walk(parsed, source)
    except ValueError as exc:
        raise SystemExit(str(exc))
if longest is not None:
    hours, duration, source = longest
    hours_text = f"{hours:.6f}".rstrip("0").rstrip(".")
    required = math.ceil(hours + 1)
    sys.stdout.write(f"{required}\0{duration}\0{hours_text}\0{source}\0")
PY
  then
    cat "$TMP/budget.err" >&2
    inspect_fail 'cannot read or parse lane budgets'
  fi
  mapfile -d '' -t budget_records < "$TMP/budgets" ||
    inspect_fail 'cannot read parsed lane budgets'
  ((${#budget_records[@]} == 0 || ${#budget_records[@]} % 4 == 0)) ||
    inspect_fail 'parsed lane budget output is malformed'
  if ((${#budget_records[@]} > 0)); then
    BUDGET_REQUIRED=${budget_records[0]}
    [[ $BUDGET_REQUIRED =~ ^[0-9]+$ ]] ||
      inspect_fail 'parsed lane budget age is malformed'
    BUDGET_DURATION=${budget_records[1]}
    BUDGET_HOURS=${budget_records[2]}
    BUDGET_FILE=${budget_records[3]}
  fi
fi

((BUDGET_REQUIRED >= 48)) || BUDGET_REQUIRED=48
if ((AGE_SET == 0)); then
  AGE_H=$BUDGET_REQUIRED
  ((AGE_H <= 8760)) ||
    die "longest lane budget ${BUDGET_DURATION} (${BUDGET_HOURS}h) needs an age above the supported 8760h limit"
fi
if [[ -n $BUDGET_DURATION ]] && ((AGE_H < BUDGET_REQUIRED && KNOW_AGE == 0)); then
  ((APPLY == 0)) ||
    die "--age-hours ${AGE_H}h is below the longest lane budget ${BUDGET_DURATION} (${BUDGET_HOURS}h) + 1 (needs ${BUDGET_REQUIRED}h; ${BUDGET_FILE})"
  printf 'ctl-clean: WARNING --age-hours %sh is below the longest lane budget %s (%sh) + 1 from %s (needs %sh)\n' \
    "$AGE_H" "$BUDGET_DURATION" "$BUDGET_HOURS" "$BUDGET_FILE" "$BUDGET_REQUIRED" >&2
fi
printf 'ctl-clean: target=%s age=%sh mode=%s\n' "$REPO" "$AGE_H" \
  "$([[ $APPLY == 1 ]] && printf APPLY || printf dry-run)"
if [[ -n $BUDGET_DURATION ]]; then
  printf 'ctl-clean: longest lane budget=%s (%sh; needs age>=%sh) (%s)\n' \
    "$BUDGET_DURATION" "$BUDGET_HOURS" "$BUDGET_REQUIRED" "$BUDGET_FILE"
fi

if ((${#LEFTOVER[@]} > 0)); then
  printf 'ctl-clean: WARNING existing quarantine entries block apply until an operator removes them:\n'
  for name in "${!LEFTOVER[@]}"; do printf '    %s\n' "$name"; done
  printf 'ctl-clean: remove them with: find %q -mindepth 1 -maxdepth 1 -name %q -exec rm -rf --one-file-system -- {} +\n' \
    "$RG" '.ctl-clean-quarantine-*'
  if ((APPLY)); then summary; exit 1; fi
fi

CLASSES=('run-gate-gitcfg-*' 'run-gate-test-bin-*' 'run-gate-test-invoke*' 'assay-liveness-*')
if [[ -d $RG ]]; then
  candidate_expr=(-name "${CLASSES[0]}")
  for ((idx = 1; idx < ${#CLASSES[@]}; idx++)); do
    candidate_expr+=(-o -name "${CLASSES[idx]}")
  done
  if ! find "$RG" -mindepth 1 -maxdepth 1 \( "${candidate_expr[@]}" \) -print0 \
      > "$TMP/candidates"; then
    inspect_fail 'cannot discover scratch candidates'
  fi
  mapfile -d '' -t rows < "$TMP/candidates" ||
    inspect_fail 'cannot read scratch candidates'
  for name in "${rows[@]}"; do CANDIDATE[${name#"$RG"/}]=1; done

  if ! find "$RG" -mindepth 1 -newermt "$AGE_H hours ago" -printf '%P\0' \
      > "$TMP/active"; then
    inspect_fail 'cannot inspect .run-gate activity'
  fi
  mapfile -d '' -t rows < "$TMP/active" ||
    inspect_fail 'cannot read .run-gate activity'
  for relative in "${rows[@]}"; do ACTIVE[${relative%%/*}]=1; done

  if ! findmnt -rn -o TARGET > "$TMP/mounts"; then
    inspect_fail 'cannot inspect mount points'
  fi
  mapfile -t mounts < "$TMP/mounts" ||
    inspect_fail 'cannot read mount points'
  for name in "${!CANDIDATE[@]}"; do
    for target in "${mounts[@]}"; do
      if under "$target" "$RG/$name"; then MOUNTED[$name]=1; break; fi
    done
  done
fi

scan_processes() {
  local base=$1 pid link target rest name rc=0
  local -a links=()
  shopt -s nullglob
  for pid in /proc/[0-9]*; do
    for link in "$pid/cwd" "$pid/root"; do
      [[ -e $link || -L $link ]] || continue
      if ! target=$(readlink -- "$link" 2>/dev/null); then
        if [[ -e $link || -L $link ]]; then rc=1; fi
        continue
      fi
      if under "$target" "$base" && [[ $target != "$base" ]]; then
        rest=${target#"$base"/}
        name=${rest%%/*}
        [[ -z ${CANDIDATE[$name]+x} ]] || PROC_ACTIVE[$name]=1
      fi
    done
    links=("$pid"/fd/*)
    for link in "${links[@]}"; do
      [[ -e $link || -L $link ]] || continue
      if ! target=$(readlink -- "$link" 2>/dev/null); then
        if [[ -e $link || -L $link ]]; then rc=1; fi
        continue
      fi
      if under "$target" "$base" && [[ $target != "$base" ]]; then
        rest=${target#"$base"/}
        name=${rest%%/*}
        [[ -z ${CANDIDATE[$name]+x} ]] || PROC_ACTIVE[$name]=1
      fi
    done
  done
  shopt -u nullglob
  return "$rc"
}

class_counts() {
  local pattern=$1 name
  c_total=0 c_eligible=0 c_rule=0 c_process=0 c_mount=0
  c_members=()
  for name in "${!CANDIDATE[@]}"; do
    # The class name is deliberately a glob.
    # shellcheck disable=SC2254
    case $name in $pattern) ;; *) continue ;; esac
    c_total=$((c_total + 1))
    [[ -n ${ACTIVE[$name]+x} ]] && c_rule=$((c_rule + 1))
    [[ -n ${MOUNTED[$name]+x} ]] && c_mount=$((c_mount + 1))
    [[ -n ${PROC_ACTIVE[$name]+x} ]] && c_process=$((c_process + 1))
    if [[ -n ${ELIGIBLE[$name]+x} ]]; then
      c_eligible=$((c_eligible + 1)); c_members+=("$name")
    fi
  done
}

show_active() {
  local kind=$1 pattern=$2 name
  for name in "${!CANDIDATE[@]}"; do
    # The class name is deliberately a glob.
    # shellcheck disable=SC2254
    case $name in $pattern) ;; *) continue ;; esac
    case $kind in
      mounted) [[ -n ${MOUNTED[$name]+x} ]] && printf '    mounted: %s\n' "$name" ;;
      process) [[ -n ${PROC_ACTIVE[$name]+x} ]] && printf '    active-process: %s\n' "$name" ;;
    esac
  done
}

print_class() {
  local pattern=$1 size_text=$2
  class_counts "$pattern"
  if [[ -z $size_text ]]; then
    printf '  %-26s eligible %d of %d; active-by-mtime %d, active-process %d, mounted %d; older than %sh (newest file inside included)\n' \
      "$pattern" "$c_eligible" "$c_total" "$c_rule" "$c_process" "$c_mount" "$AGE_H"
  else
    printf '  %-26s eligible %d of %d; active-by-mtime %d, active-process %d, mounted %d; older than %sh (newest file inside included); %s\n' \
      "$pattern" "$c_eligible" "$c_total" "$c_rule" "$c_process" "$c_mount" "$AGE_H" "$size_text"
  fi
  show_active mounted "$pattern"
  show_active process "$pattern"
}

examples() {
  local order=$1 stamp name record count sort_arg
  shift
  (($# > 0)) || return 0
  : > "$TMP/ex"
  for name in "$@"; do
    [[ -n ${NEWEST[$name]+x} ]] || inspect_fail "cannot report timestamp for $name"
    printf '%s\t%s\0' "${NEWEST[$name]}" "$name" >> "$TMP/ex" ||
      inspect_fail 'cannot build report examples'
  done
  if [[ $order == oldest ]]; then sort_arg=-n; else sort_arg=-nr; fi
  LC_ALL=C sort -z "$sort_arg" "$TMP/ex" > "$TMP/ex.sorted" ||
    inspect_fail 'cannot sort report examples'
  mapfile -d '' -t records < "$TMP/ex.sorted" ||
    inspect_fail 'cannot read report examples'
  count=0
  for record in "${records[@]}"; do
    ((count++ < 3)) || break
    [[ $record == *$'\t'* ]] || inspect_fail 'report example is malformed'
    stamp=${record%%$'\t'*}
    name=${record#*$'\t'}
    printf '    %s: %s  %s\n' "$order" \
      "$(date -u -d "@$stamp" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '%s' "$stamp")" "$name"
  done
}

if ((APPLY == 0)); then
  if ((${#CANDIDATE[@]} > 0)); then
    scan_processes "$RG" ||
      inspect_fail 'cannot inspect live process references under .run-gate'
  fi
  for name in "${!CANDIDATE[@]}"; do
    if [[ -z ${ACTIVE[$name]+x} && -z ${MOUNTED[$name]+x} && -z ${PROC_ACTIVE[$name]+x} ]]; then
      ELIGIBLE[$name]=1; eligible=$((eligible + 1))
    fi
  done
  if ((${#ELIGIBLE[@]} > 0)); then
    if ! find "$RG" -mindepth 1 -printf '%T@\t%s\t%P\0' > "$TMP/meta"; then
      inspect_fail 'cannot inspect scratch sizes and timestamps'
    fi
    mapfile -d '' -t records < "$TMP/meta" ||
      inspect_fail 'cannot read scratch sizes and timestamps'
    declare -A SIZE=() NEWEST=()
    for record in "${records[@]}"; do
      [[ $record == *$'\t'* && ${record#*$'\t'} == *$'\t'* ]] ||
        inspect_fail 'scratch metadata record is malformed'
      stamp=${record%%$'\t'*}
      rest=${record#*$'\t'}
      bytes=${rest%%$'\t'*}
      top=${rest#*$'\t'}
      top=${top%%/*}
      [[ $stamp =~ ^[0-9]+(\.[0-9]+)?$ && $bytes =~ ^[0-9]+$ ]] ||
        inspect_fail 'scratch metadata values are malformed'
      [[ -n ${ELIGIBLE[$top]+x} ]] || continue
      SIZE[$top]=$(( ${SIZE[$top]:-0} + bytes ))
      whole=${stamp%%.*}
      if [[ -z ${NEWEST[$top]+x} || ${NEWEST[$top]%%.*} -lt $whole ]]; then
        NEWEST[$top]=$stamp
      fi
    done
  fi
  for ((idx = 0; idx < ${#CLASSES[@]}; idx++)); do
    class_counts "${CLASSES[idx]}"
    total=0
    for name in "${c_members[@]}"; do total=$((total + ${SIZE[$name]:-0})); done
    if ((${#c_members[@]} == 0)); then size_text='0 bytes'; else size_text="$total bytes"; fi
    print_class "${CLASSES[idx]}" "$size_text"
    examples oldest "${c_members[@]}"
    examples newest "${c_members[@]}"
  done
else
  printf 'ctl-clean: quiet-host check: run this with no gate running; apply is an operator action meant for a quiet host\n'
  moved=()
  if ((${#CANDIDATE[@]} > 0)); then
    pending=()
    for name in "${!CANDIDATE[@]}"; do
      [[ -z ${ACTIVE[$name]+x} && -z ${MOUNTED[$name]+x} ]] && pending+=("$name")
    done
    if ((${#pending[@]} > 0)); then
      eligible=$((eligible + ${#pending[@]}))
      QUARANTINE=$RG/.ctl-clean-quarantine-$$-$RANDOM
      if ! mkdir -m 700 -- "$QUARANTINE"; then
        failed=${#pending[@]}
        printf 'ctl-clean: FAILED to create quarantine under %s\n' "$RG" >&2
        summary; exit 1
      fi
      for name in "${pending[@]}"; do
        if mv -- "$RG/$name" "$QUARANTINE/$name"; then
          moved+=("$name")
        else
          failed=$((failed + 1))
          printf 'ctl-clean: FAILED to quarantine %s\n' "$RG/$name" >&2
        fi
      done
    fi
  fi

  if ((${#moved[@]} > 0)); then
    if ! find "$QUARANTINE" -mindepth 1 -newermt "$AGE_H hours ago" -printf '%P\0' \
        > "$TMP/qactive"; then
      for name in "${moved[@]}"; do
        mv -- "$QUARANTINE/$name" "$RG/$name" 2>/dev/null ||
          { failed=$((failed + 1)); printf 'ctl-clean: FAILED to restore %s; it remains at %s\n' "$RG/$name" "$QUARANTINE/$name" >&2; }
      done
      inspect_fail 'cannot recheck quarantined activity'
    fi
    mapfile -d '' -t rows < "$TMP/qactive" ||
      inspect_fail 'cannot read quarantined activity'
    declare -A Q_ACTIVE=()
    for relative in "${rows[@]}"; do Q_ACTIVE[${relative%%/*}]=1; done
    if ! scan_processes "$QUARANTINE"; then
      for name in "${moved[@]}"; do
        mv -- "$QUARANTINE/$name" "$RG/$name" 2>/dev/null ||
          { failed=$((failed + 1)); printf 'ctl-clean: FAILED to restore %s; it remains at %s\n' "$RG/$name" "$QUARANTINE/$name" >&2; }
      done
      inspect_fail 'cannot inspect live process references under quarantine'
    fi

    safe=()
    for name in "${moved[@]}"; do
      if [[ -n ${Q_ACTIVE[$name]+x} ]]; then
        eligible=$((eligible - 1))
        failed=$((failed + 1))
        if [[ -e $RG/$name || -L $RG/$name ]] || ! mv -- "$QUARANTINE/$name" "$RG/$name"; then
          printf 'ctl-clean: FAILED to restore %s; it remains at %s\n' "$RG/$name" "$QUARANTINE/$name" >&2
        else
          printf 'ctl-clean: keeping %s (activity changed after selection)\n' "$RG/$name" >&2
        fi
      elif [[ -n ${PROC_ACTIVE[$name]+x} ]]; then
        eligible=$((eligible - 1))
        if [[ -e $RG/$name || -L $RG/$name ]] || ! mv -- "$QUARANTINE/$name" "$RG/$name"; then
          failed=$((failed + 1))
          printf 'ctl-clean: FAILED to restore %s; it remains at %s\n' "$RG/$name" "$QUARANTINE/$name" >&2
        else
          printf 'ctl-clean: keeping %s (active-process)\n' "$RG/$name" >&2
        fi
      else
        ELIGIBLE[$name]=1
        safe+=("$QUARANTINE/$name")
      fi
    done

    command_failed=0
    if ((${#safe[@]} > 0)); then
      delete_cmd=(rm -rf --one-file-system --)
      if command -v nice >/dev/null && command -v ionice >/dev/null; then
        printf '%s\0' "${safe[@]}" |
          nice -n 19 ionice -c3 xargs -0 -r "${delete_cmd[@]}"
      else
        printf 'ctl-clean: INFO nice/ionice unavailable; using plain xargs rm\n' >&2
        printf '%s\0' "${safe[@]}" | xargs -0 -r "${delete_cmd[@]}"
      fi
      rm_status=$?
      ((rm_status == 0)) || command_failed=1
      if ! find "$QUARANTINE" -mindepth 1 -maxdepth 1 -printf '%P\0' > "$TMP/qremain"; then
        inspect_fail 'cannot verify quarantine deletion results'
      fi
      mapfile -d '' -t rows < "$TMP/qremain" ||
        inspect_fail 'cannot read quarantine deletion results'
      declare -A Q_REMAIN=()
      for name in "${rows[@]}"; do Q_REMAIN[$name]=1; done
      for path in "${safe[@]}"; do
        name=${path#"$QUARANTINE"/}
        if [[ -n ${Q_REMAIN[$name]+x} ]]; then
          failed=$((failed + 1))
          printf 'ctl-clean: FAILED to remove %s\n' "$RG/$name" >&2
        else
          removed=$((removed + 1))
        fi
      done
      if ((command_failed && failed == 0)); then failed=1; fi
      ((command_failed == 0)) || printf 'ctl-clean: removal command reported failure\n' >&2
    fi
  fi

  [[ -z $QUARANTINE || ! -d $QUARANTINE ]] || rmdir -- "$QUARANTINE" 2>/dev/null || true
  for ((idx = 0; idx < ${#CLASSES[@]}; idx++)); do
    print_class "${CLASSES[idx]}" ''
  done
fi

if [[ -d $RG ]]; then
  if ! find "$RG" -mindepth 1 -maxdepth 1 -print0 > "$TMP/recount"; then
    inspect_fail 'cannot recount .run-gate entries'
  fi
  mapfile -d '' -t recount < "$TMP/recount" ||
    inspect_fail 'cannot read .run-gate recount'
  printf '  .run-gate entries now: %d (history.json and unlisted classes are kept)\n' "${#recount[@]}"
else
  printf '  no .run-gate here\n'
fi

printf 'report (never deleted by this script):\n'
if git -C "$REPO" worktree list > "$TMP/worktrees" 2>/dev/null; then
  mapfile -t wt < "$TMP/worktrees"
  printf '  worktrees: %d' "${#wt[@]}"
else
  inspection_failed=$((inspection_failed + 1))
  printf '  worktrees: unavailable'
fi
if git -C "$REPO" branch --merged origin/main > "$TMP/merged" 2>/dev/null; then
  mapfile -t merged < "$TMP/merged"
  printf '   local branches merged into origin/main: %d\n' "${#merged[@]}"
else
  printf '   local branches merged into origin/main: unavailable\n'
fi
printf '   (see the worktree-sprawl recipe)\n'

report_dir() {
  local dir=$1 size rc output
  [[ -d $dir ]] || return 0
  if output=$(timeout 60 du -sh -- "$dir" 2>/dev/null); then
    [[ $output == *$'\t'* ]] || inspect_fail "cannot parse size for $dir"
    size=${output%%$'\t'*}
  else
    rc=$?
    inspection_failed=$((inspection_failed + 1))
    ((rc == 124)) && size='(size not computed: timed out)' || size='(size not computed)'
  fi
  printf '  %-12s %s (use dust -d 1 %s to drill down)\n' "$(basename -- "$dir")" "$size" "$dir"
}
report_dir "$REPO/artifacts"
report_dir "$REPO/.worktrees"
summary
((failed == 0 && inspection_failed == 0)) || exit 1
