#!/usr/bin/env bash
# Archive the NON-rebuildable controller state into $CONTROLLER_BACKUP_DIR.
#
# usage: ctl-state-backup.sh [--list] [--include-large] [--max-file-bytes <n>]
#        ctl-state-backup.sh --verify <archive>
# Saved (member names are prefixed repo/, home/ or runs/):
#   repo/  .controller-state/ and plan.yaml of the repository root
#   home/  $CONTROLLER_HOME/*.local.* (env file, host table), reports/, remote-gates/<run>/result.json and meta.json
#   runs/  $CONTROLLER_RUNS_DIR/*.thread, *.state.md, *.done
# Never saved: *.jsonl session streams, .git, worktrees, logs, remote-gate artifacts, and any file larger than the per-file cap
# (default 5 MiB) unless --include-large. Symlinks are stored AS links, never followed.
# --list prints what would be saved (size and name, then the skipped large files) and writes nothing. Refuses when
# CONTROLLER_BACKUP_DIR is unset, relative, or inside any git work tree (a backup next to the state it protects is no backup).
# The archive is zstd when `zstd` is installed, else gzip; mode 0600; written to a temporary name and renamed; a `.sha256`
# sidecar is written beside it. --verify checks the compression, lists the members and compares the sidecar.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

die() { echo "ctl-state-backup.sh: $*" >&2; exit "${2:-2}"; }
usage() { die 'usage: ctl-state-backup.sh [--list] [--include-large] [--max-file-bytes <n>] | --verify <archive>'; }

list_only=0 include_large=0 cap=5242880 verify=''
while [[ $# -gt 0 ]]; do
  case $1 in
    --list) list_only=1; shift ;;
    --include-large) include_large=1; shift ;;
    --max-file-bytes) [[ $# -ge 2 && $2 =~ ^[0-9]+$ ]] || usage; cap=$2; shift 2 ;;
    --verify) [[ $# -ge 2 && -n $2 ]] || usage; verify=$2; shift 2 ;;
    *) usage ;;
  esac
done

if [[ -n $verify ]]; then
  [[ -f $verify ]] || die "archive not found: $verify"
  case $verify in
    *.tar.zst) command -v zstd >/dev/null 2>&1 || die 'zstd is needed to verify a .tar.zst archive'; zstd -t -q -- "$verify" || die 'zstd integrity check FAILED' 1 ;;
    *.tar.gz) gzip -t -- "$verify" || die 'gzip integrity check FAILED' 1 ;;
    *) die 'not a .tar.zst or .tar.gz archive' ;;
  esac
  members=$(tar -tf "$verify") || die 'tar cannot list the archive' 1
  count=$(printf '%s\n' "$members" | grep -c .)
  actual=$(sha256sum -- "$verify" | cut -d' ' -f1)
  if [[ -f $verify.sha256 ]]; then
    [[ $(cut -d' ' -f1 "$verify.sha256") == "$actual" ]] || die "sha256 differs from $verify.sha256" 1
    echo "verified: $count member(s), sha256 $actual matches the sidecar"
  else
    echo "verified: $count member(s), sha256 $actual (no sidecar to compare)"
  fi
  exit 0
fi

controller_init
backup_dir=${CONTROLLER_BACKUP_DIR:-}
[[ -n $backup_dir ]] || die 'CONTROLLER_BACKUP_DIR is not set: name an existing or creatable directory OUTSIDE every git work tree (controller.local.env)'
[[ $backup_dir == /* ]] || die 'CONTROLLER_BACKUP_DIR must be an absolute path'
anchor=$backup_dir
while [[ ! -d $anchor ]]; do anchor=$(dirname -- "$anchor"); done
if git -C "$anchor" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "CONTROLLER_BACKUP_DIR is inside a git work tree ($anchor): a git clean could remove the backup with the state"
fi
repo_root=$(git -C "$CONTROLLER_REPO" rev-parse --show-toplevel 2>/dev/null) || repo_root=''

stage=$(mktemp -d "${TMPDIR:-/tmp}/ctl-state-backup.XXXXXX") || die 'cannot create a staging directory'
trap 'rm -rf -- "$stage"' EXIT
files=() skipped=() roots=() total=0
add_root() {
  # $1 label, $2 root directory, remaining arguments: find options, start paths and a name filter, relative to the root
  local label=$1 root=$2 rec size path id=${#roots[@]}
  shift 2
  [[ -d $root ]] || return 0
  : > "$stage/$id.list"
  while IFS= read -r -d '' rec; do
    size=${rec%%$'\t'*}
    path=${rec#*$'\t'}
    if [[ -f $root/$path && ! -L $root/$path ]] && (( size > cap && ! include_large )); then
      skipped+=("$size $label/${path#./}")
      continue
    fi
    printf '%s\0' "$path" >> "$stage/$id.list"
    files+=("$size $label/${path#./}")
    total=$((total + size))
  done < <(cd -- "$root" && find "$@" \( -type f -o -type l \) ! -name '*.jsonl' ! -path '*/.git/*' -printf '%s\t%p\0' 2>/dev/null)
  roots+=("$id|$label|$root")
}
[[ -z $repo_root ]] || add_root repo "$repo_root" .controller-state plan.yaml
add_root home "$CONTROLLER_HOME" -maxdepth 1 -name '*.local.*'
add_root home "$CONTROLLER_HOME" reports
add_root home "$CONTROLLER_HOME" remote-gates -mindepth 2 -maxdepth 2 \( -name result.json -o -name meta.json \)
add_root runs "$CONTROLLER_RUNS_DIR" -maxdepth 1 \( -name '*.thread' -o -name '*.state.md' -o -name '*.done' \)

if (( list_only )); then
  printf '%s\n' ${files[@]+"${files[@]}"} | sort -k2 | grep . || echo '(nothing to save)'
  for entry in ${skipped[@]+"${skipped[@]}"}; do echo "SKIPPED (over ${cap} bytes; --include-large): $entry"; done
  echo "total: ${#files[@]} file(s), $total byte(s); destination $backup_dir"
  exit 0
fi

(( ${#files[@]} > 0 )) || die 'nothing to save (no state files found)' 1
mkdir -p -- "$backup_dir" || die "cannot create $backup_dir"
chmod 700 -- "$backup_dir" 2>/dev/null
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tarfile=$stage/state.tar
first=1
for entry in "${roots[@]}"; do
  IFS='|' read -r id label root <<< "$entry"
  [[ -s $stage/$id.list ]] || continue
  if (( first )); then mode=c; first=0; else mode=r; fi
  tar -${mode}f "$tarfile" --null --transform "s,^\./,,;s,^,$label/," -C "$root" -T "$stage/$id.list" || die "tar failed for $label" 1
done
if command -v zstd >/dev/null 2>&1; then ext=tar.zst; zstd -q -c -- "$tarfile" > "$stage/out" || die 'zstd failed' 1
else ext=tar.gz; gzip -c -- "$tarfile" > "$stage/out" || die 'gzip failed' 1; fi
final=$backup_dir/controller-state-$stamp.$ext
# Two backups in the same second must not overwrite each other.
n=1
while [[ -e $final ]]; do final=$backup_dir/controller-state-$stamp-$n.$ext; n=$((n + 1)); done
tmp=$backup_dir/.controller-state-$stamp.$ext.tmp.$$
if ! { cp -- "$stage/out" "$tmp" && chmod 600 -- "$tmp" && mv -f -- "$tmp" "$final"; }; then die "cannot write $final" 1; fi
sum=$(sha256sum -- "$final" | cut -d' ' -f1)
printf '%s  %s\n' "$sum" "$(basename -- "$final")" > "$final.sha256" && chmod 600 -- "$final.sha256"
echo "archive: $final"
echo "size:    $(stat -c %s -- "$final") byte(s), ${#files[@]} file(s), ${#skipped[@]} skipped as large"
echo "sha256:  $sum"
