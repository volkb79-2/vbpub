#!/usr/bin/env bash
# ctl-clean.sh - remove finished-run scratch from the checkout's .run-gate.
#
# One recursive traversal decides activity: any descendant newer than the age
# window marks its top-level entry active. Apply first atomically moves eligible
# entries to a quarantine directory, rechecks them, and deletes only entries
# that stayed inactive. artifacts/, .worktrees/, evidence and history are
# report-only. The default age is max(48, longest hour lane budget + 1).
set -u -o pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_NAMESPACE

usage() {
  printf '%s\n' \
    'Usage: ctl-clean.sh [--apply] [--age-hours N] [--i-know-the-age]' \
    '                     [--repo DIR] [--prune-worktrees]' \
    '' \
    'Dry run by default. An entry is eligible only when no file inside it is' \
    'newer than --age-hours. Default: max(48, longest lane hour budget + 1).' \
    '' \
    '  --apply              quarantine, recheck and delete eligible entries' \
    '  --age-hours N        decimal integer 1..8760 (and >= longest budget + 1)' \
    '  --i-know-the-age     allow --apply below the longest lane budget + 1' \
    '  --repo DIR           worktree root to inspect (default: current root)' \
    '  --prune-worktrees    opt in to git worktree prune; use only from the shared' \
    '                       checkout when no session is creating or moving worktrees'
}
die() { printf 'ctl-clean: %s\n' "$*" >&2; exit 2; }
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

APPLY=0 PRUNE_WORKTREES=0 AGE_SET=0 KNOW_AGE=0 AGE_H=48
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
    --prune-worktrees) PRUNE_WORKTREES=1 ;;
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
  local status=$?
  if [[ -n $QUARANTINE && -d $QUARANTINE ]] && ! rmdir -- "$QUARANTINE" 2>/dev/null; then
    printf 'ctl-clean: WARNING quarantined entries remain at %s\n' "$QUARANTINE" >&2
  fi
  rm -rf -- "$TMP"
  return "$status"
}
trap cleanup EXIT

eligible=0 removed=0 failed=0 inspection_failed=0 prune_failed=0
summary() {
  printf 'ctl-clean: eligible %d, removed %d, failed %d, inspection-failed %d scratch entries\n' \
    "$eligible" "$removed" "$failed" "$inspection_failed"
}
fail_discovery() { printf 'ctl-clean: %s\n' "$*" >&2; summary; exit 1; }

BUDGET_H=0 BUDGET_FILE=
if ! find "$REPO" -maxdepth 3 -path "$REPO/.run-gate" -prune -o \
    -name run-gate.toml -type f -print0 > "$TMP/run-gates"; then
  fail_discovery 'cannot discover lane budgets from run-gate.toml files'
fi
while IFS= read -r -d '' file; do
  if ! mapfile -t lines < "$file"; then
    fail_discovery "cannot read lane budgets from $file"
  fi
  for line in "${lines[@]}"; do
    [[ $line =~ ^[[:space:]]*budget[[:space:]]*=[[:space:]]*\"([0-9]+)h\" ]] || continue
    digits=${BASH_REMATCH[1]#"${BASH_REMATCH[1]%%[!0]*}"}
    [[ -n $digits ]] || digits=0
    ((${#digits} <= 4)) && n=$((10#$digits)) || n=9999
    if ((n > BUDGET_H)); then BUDGET_H=$n BUDGET_FILE=$file; fi
  done
done < "$TMP/run-gates"
required_age=$((BUDGET_H + 1))
((required_age >= 48)) || required_age=48
if ((AGE_SET == 0)); then
  ((required_age <= 8760)) ||
    die "longest lane budget ${BUDGET_H}h requires --age-hours above the supported 8760h limit"
  AGE_H=$required_age
fi
if ((BUDGET_H > 0 && AGE_H < BUDGET_H + 1 && KNOW_AGE == 0)); then
  ((APPLY == 0)) ||
    die "--age-hours ${AGE_H}h is below the longest lane budget ${BUDGET_H}h + 1 (${BUDGET_FILE})"
  printf 'ctl-clean: WARNING --age-hours %sh is below lane budget %sh + 1 from %s\n' \
    "$AGE_H" "$BUDGET_H" "$BUDGET_FILE" >&2
fi
printf 'ctl-clean: target=%s age=%sh mode=%s prune-worktrees=%s\n' "$REPO" "$AGE_H" \
  "$([[ $APPLY == 1 ]] && printf APPLY || printf dry-run)" \
  "$([[ $PRUNE_WORKTREES == 1 ]] && printf opt-in || printf off)"
((BUDGET_H == 0)) || printf 'ctl-clean: longest lane hour budget=%sh (%s)\n' "$BUDGET_H" "$BUDGET_FILE"

RG=.run-gate
CLASSES=('run-gate-gitcfg-*' 'run-gate-test-bin-*' 'run-gate-test-invoke*' 'assay-liveness-*')
declare -A ELIGIBLE=() ACTIVE=() SIZE=() NEWEST=()
if [[ -L $RG ]]; then
  fail_discovery '.run-gate is a symlink; refusing to traverse or delete through it'
elif [[ -e $RG && ! -d $RG ]]; then
  fail_discovery '.run-gate is not a directory'
elif [[ -d $RG ]]; then
  for idx in "${!CLASSES[@]}"; do
    find "$RG" -mindepth 1 -maxdepth 1 -name "${CLASSES[idx]}" \
      -printf '%P\0' > "$TMP/candidates-$idx" ||
      fail_discovery "cannot discover ${CLASSES[idx]} candidates"
  done
  if ! find "$RG" -mindepth 1 -newermt "$AGE_H hours ago" -printf '%P\0' > "$TMP/active"; then
    inspection_failed=1
    fail_discovery 'cannot inspect .run-gate activity'
  fi
  while IFS= read -r -d '' relative; do ACTIVE[${relative%%/*}]=1; done < "$TMP/active"
  for idx in "${!CLASSES[@]}"; do
    while IFS= read -r -d '' name; do
      if [[ -z ${ACTIVE[$name]+x} ]]; then ELIGIBLE[$name]=1; eligible=$((eligible + 1)); fi
    done < "$TMP/candidates-$idx"
  done
fi

META_OK=0
if ((APPLY == 0)) && [[ -d $RG ]]; then
  if find "$RG" -mindepth 1 -printf '%T@\t%s\t%P\0' > "$TMP/meta"; then
    META_OK=1
    while IFS= read -r -d '' record; do
      stamp=${record%%$'\t'*}
      rest=${record#*$'\t'}
      bytes=${rest%%$'\t'*}
      top=${rest#*$'\t'}
      top=${top%%/*}
      [[ -n ${ELIGIBLE[$top]+x} ]] || continue
      SIZE[$top]=$(( ${SIZE[$top]:-0} + bytes ))
      whole=${stamp%%.*}
      if [[ -z ${NEWEST[$top]+x} || ${NEWEST[$top]%%.*} -lt $whole ]]; then NEWEST[$top]=$stamp; fi
    done < "$TMP/meta"
  else
    printf 'ctl-clean: WARNING cannot inspect scratch sizes and timestamps\n' >&2
  fi
fi

examples() {
  local order=$1 stamp name record count sort_arg label
  shift
  (($# > 0)) || return 0
  for name in "$@"; do printf '%s\t%s\0' "${NEWEST[$name]}" "$name"; done > "$TMP/ex"
  if [[ $order == oldest ]]; then sort_arg=-n label=oldest; else sort_arg=-nr label=newest; fi
  LC_ALL=C sort -z "$sort_arg" "$TMP/ex" > "$TMP/ex.sorted" || return 0
  count=0
  while IFS= read -r -d '' record; do
    ((count++ < 3)) || break
    stamp=${record%%$'\t'*}
    name=${record#*$'\t'}
    printf '    %s: %s  %s\n' "$label" \
      "$(date -u -d "@$stamp" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf '%s' "$stamp")" "$name"
  done < "$TMP/ex.sorted"
}

USE_PRIORITY=0
if ((APPLY)); then
  if command -v nice >/dev/null && command -v ionice >/dev/null; then USE_PRIORITY=1
  else printf 'ctl-clean: INFO nice/ionice unavailable; using plain xargs rm\n' >&2; fi
fi
moved=()
if ((APPLY && eligible)); then
  QUARANTINE=$RG/.ctl-clean-quarantine-$$-$RANDOM
  if ! mkdir -m 700 -- "$QUARANTINE"; then
    failed=$eligible
    printf 'ctl-clean: FAILED to quarantine %d eligible entries under %s\n' "$eligible" "$RG" >&2
    summary
    exit 1
  fi
fi

for idx in "${!CLASSES[@]}"; do
  [[ -d $RG ]] || break
  mapfile -d '' -t names < "$TMP/candidates-$idx"
  ((${#names[@]})) || continue
  selected=()
  for name in "${names[@]}"; do [[ -n ${ELIGIBLE[$name]+x} ]] && selected+=("$name"); done
  if ((APPLY)); then
    printf '  %-26s eligible %d of %d; older than %sh (newest file inside included)\n' \
      "${CLASSES[idx]}" "${#selected[@]}" "${#names[@]}" "$AGE_H"
    for name in "${selected[@]}"; do
      if mv -- "$RG/$name" "$QUARANTINE/$name"; then moved+=("$name")
      else failed=$((failed + 1)); printf 'ctl-clean: FAILED to quarantine %s\n' "$RG/$name" >&2; fi
    done
  else
    total=0
    for name in "${selected[@]}"; do total=$((total + ${SIZE[$name]:-0})); done
    if ((META_OK == 0)); then size_text='(size not computed)'
    elif ((${#selected[@]} == 0)); then size_text='0 bytes'
    else size_text="$total bytes"; fi
    printf '  %-26s eligible %d of %d; older than %sh (newest file inside included); %s\n' \
      "${CLASSES[idx]}" "${#selected[@]}" "${#names[@]}" "$AGE_H" "$size_text"
    ((META_OK)) && examples oldest "${selected[@]}"
    ((META_OK)) && examples newest "${selected[@]}"
  fi
done

if ((APPLY && ${#moved[@]})); then
  declare -A Q_ACTIVE=() Q_REMAIN=()
  q_ok=1
  if ! find "$QUARANTINE" -mindepth 1 -newermt "$AGE_H hours ago" -printf '%P\0' > "$TMP/qactive"; then
    q_ok=0
    printf 'ctl-clean: cannot recheck quarantined activity\n' >&2
  else
    while IFS= read -r -d '' relative; do Q_ACTIVE[${relative%%/*}]=1; done < "$TMP/qactive"
  fi
  safe=()
  for name in "${moved[@]}"; do
    if ((q_ok == 0 || ${Q_ACTIVE[$name]:-0})); then
      failed=$((failed + 1))
      if [[ -e $RG/$name || -L $RG/$name ]] || ! mv -- "$QUARANTINE/$name" "$RG/$name"; then
        printf 'ctl-clean: FAILED to restore %s; it remains at %s\n' "$RG/$name" "$QUARANTINE/$name" >&2
      else
        printf 'ctl-clean: keeping %s (activity changed after selection)\n' "$RG/$name" >&2
      fi
    else
      safe+=("$QUARANTINE/$name")
    fi
  done
  if ((${#safe[@]})); then
    if ((USE_PRIORITY)); then
      printf '%s\0' "${safe[@]}" | nice -n 19 ionice -c3 xargs -0 -r rm -rf --
    else
      printf '%s\0' "${safe[@]}" | xargs -0 -r rm -rf --
    fi
    rm_status=$?
    if ! find "$QUARANTINE" -mindepth 1 -maxdepth 1 -printf '%P\0' > "$TMP/qremain"; then
      failed=$((failed + ${#safe[@]}))
      printf 'ctl-clean: cannot verify deletion results; counted all selected entries as failed\n' >&2
    else
      while IFS= read -r -d '' name; do Q_REMAIN[$name]=1; done < "$TMP/qremain"
      for path in "${safe[@]}"; do
        name=${path#"$QUARANTINE"/}
        if [[ -n ${Q_REMAIN[$name]+x} ]]; then
          failed=$((failed + 1)); printf 'ctl-clean: FAILED to remove %s\n' "$RG/$name" >&2
        else removed=$((removed + 1)); fi
      done
    fi
    ((rm_status == 0)) || printf 'ctl-clean: removal command reported failure\n' >&2
  fi
fi
[[ -z $QUARANTINE || ! -d $QUARANTINE ]] || rmdir -- "$QUARANTINE" 2>/dev/null || true

if [[ -d $RG ]]; then
  find "$RG" -mindepth 1 -maxdepth 1 -print0 > "$TMP/recount" ||
    fail_discovery 'cannot recount .run-gate entries'
  mapfile -d '' -t recount < "$TMP/recount"
  printf '  .run-gate entries now: %d (history.json and unlisted classes are kept)\n' "${#recount[@]}"
else
  printf '  no .run-gate here\n'
fi

if ((PRUNE_WORKTREES)); then
  if ((APPLY)); then prune_args=(worktree prune -v) label=prune
  else prune_args=(worktree prune -n -v) label='would prune'; fi
  if prune_output=$(git -C "$REPO" "${prune_args[@]}" 2>&1); then :; else prune_failed=1; fi
  if [[ -n $prune_output ]]; then
    while IFS= read -r line; do printf '  %s: %s\n' "$label" "$line"; done <<< "$prune_output"
  fi
  ((prune_failed == 0)) || printf 'ctl-clean: git worktree prune failed\n' >&2
fi

printf 'report (never deleted by this script):\n'
mapfile -t wt < <(git -C "$REPO" worktree list 2>/dev/null || true)
mapfile -t merged < <(git -C "$REPO" branch --merged origin/main 2>/dev/null || true)
printf '  worktrees: %d   local branches merged into origin/main: %d   (see the worktree-sprawl recipe)\n' \
  "${#wt[@]}" "${#merged[@]}"
report_dir() {
  local dir=$1 size rc output
  [[ -d $dir ]] || return 0
  if output=$(timeout 60 du -sh -- "$dir" 2>/dev/null); then size=${output%%$'\t'*}
  else rc=$?; ((rc == 124)) && size='(size not computed: timed out)' || size='(size not computed)'; fi
  printf '  %-12s %s (use dust -d 1 %s to drill down)\n' "$(basename -- "$dir")" "$size" "$dir"
}
report_dir "$REPO/artifacts"
report_dir "$REPO/.worktrees"
summary
((failed == 0 && inspection_failed == 0 && prune_failed == 0)) || exit 1
