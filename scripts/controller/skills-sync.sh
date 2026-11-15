#!/usr/bin/env bash
# Copy the repository's HAND-WRITTEN tool skills into the agent harness skill
# directories (~/.agents/skills and ~/.claude/skills).
#
# Skills a tool PACKAGES are not handled here: cmru, ciu, pwmcp and fleetd ship
# a `skills` verb (cli-extended) that stamps its own `.cli-extended-stamp.json`,
# and `<cli> skills check` already answers "is my installed copy current?".
# This script owns only the sources listed in SOURCES — the hand-written ones
# whose tool has no such verb — refuses to overwrite any directory that does
# not carry ITS stamp, never touches `~/.claude/skills/synced`, and is
# idempotent.
#
# Usage: skills-sync.sh [install|check|list] [--dest DIR] [--dry-run]
#                       [--overwrite-modified]
#
# States: current (stamp digest == source digest and bytes match), stale
# (source changed since the install), modified (bytes changed since the
# install), absent, unmanaged (no stamp, a symlink, or a file). Only `current`
# passes `check`; only `absent` and `stale` are installed without a flag.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)

STAMP=.controller-skills-stamp.json
# shellcheck disable=SC2100  # a literal name, not arithmetic
OWNER=tool-skills

# Repo-relative skill directories this script owns.
SOURCES=(
  run-gate-project/.claude/skills/run-gate-cli
  assay/.claude/skills/assay-cli
)

mode=install
dest_override=
dry_run=0
overwrite_modified=0
# Print the header comment block, minus the shebang. Derived (stop at the first
# non-comment line) rather than a fixed line range: a range is off by one the
# moment the header gains a line, and it silently dropped the script's first
# description line from `--help`.
usage() { awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "${BASH_SOURCE[0]}"; }
while (($#)); do
  case $1 in
    install|check|list) mode=$1 ;;
    --dest) shift
      # Not `$# == 1`: that rejected `--dest DIR --dry-run` (a value followed by
      # any other flag) and only worked with --dest last.
      [[ $# -ge 1 && $1 != -* ]] || { echo "--dest needs a directory" >&2; exit 2; }
      dest_override=$1 ;;
    --dest=*) dest_override=${1#--dest=} ;;
    --dry-run) dry_run=1 ;;
    --overwrite-modified) overwrite_modified=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown argument: $1 (expected install|check|list, --dest DIR, --dry-run)" >&2; exit 2 ;;
  esac
  shift
done

# The same two directories cli-extended installs into, honouring
# CLAUDE_CONFIG_DIR exactly as that library does. --dest targets one directory.
destinations() {
  if [[ -n $dest_override ]]; then
    printf '%s\n' "$dest_override"
    return 0
  fi
  : "${HOME:?HOME must be set}"
  printf '%s\n' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "$HOME/.agents/skills"
}

# Deterministic digest of a whole skill tree (relative path + bytes, sorted).
tree_digest() {
  local dir=$1 file rel
  [[ -d $dir ]] || { printf 'none\n'; return 0; }
  while IFS= read -r file; do
    rel=${file#"$dir"/}
    printf '%s %s\n' "$rel" "$(sha256sum "$file" | cut -d' ' -f1)"
  done < <(find "$dir" -type f ! -name "$STAMP" -print | LC_ALL=C sort) |
    sha256sum | cut -d' ' -f1
}

# Print "<tool> <digest>", or "none"/"unreadable".
stamp_fields() {
  local dest=$1
  [[ -f $dest/$STAMP ]] || { printf 'none none\n'; return 0; }
  python3 - "$dest/$STAMP" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    print("unreadable none")
else:
    print(f"{data.get('tool') or 'unreadable'} {data.get('digest') or 'none'}")
PY
}

# current | stale | modified | absent | unmanaged
state_of() {
  local dest=$1 want=$2 tool stamped bytes
  if [[ ! -e $dest && ! -L $dest ]]; then printf 'absent\n'; return 0; fi
  if [[ -L $dest || ! -d $dest ]]; then printf 'unmanaged\n'; return 0; fi
  read -r tool stamped < <(stamp_fields "$dest")
  [[ $tool == "$OWNER" ]] || { printf 'unmanaged\n'; return 0; }
  [[ $stamped != none ]] || { printf 'unmanaged\n'; return 0; }
  bytes=$(tree_digest "$dest")
  # Bytes changed since the install: someone edited the copy.
  [[ $bytes == "$stamped" ]] || { printf 'modified\n'; return 0; }
  [[ $stamped == "$want" ]] || { printf 'stale\n'; return 0; }
  printf 'current\n'
}

install_one() {
  local src=$1 dest=$2 want=$3 base=$4 name=$5 label=$6 source=$7 stage
  if ((dry_run)); then
    printf 'would %-8s %s -> %s\n' "$label" "$name" "$base"
    return 0
  fi
  # Build the copy beside the target and swap it in only when every step
  # worked: a failed copy (read-only home, full disk) must neither report
  # success nor destroy the previous copy.
  if ! mkdir -p -- "$base" || ! stage=$(mktemp -d -- "$base/.$name.stage.XXXXXX"); then
    echo "ERROR cannot stage $name in $base" >&2; failed=1; return 1
  fi
  if chmod 755 -- "$stage" &&
     cp -R -- "$src"/. "$stage"/ &&
     printf '{"tool":"%s","name":"%s","source":"%s","digest":"%s"}\n' \
       "$OWNER" "$name" "$source" "$want" > "$stage/$STAMP" &&
     rm -rf -- "$dest" && mv -- "$stage" "$dest"; then
    printf '%-12s %s -> %s\n' "$label" "$name" "$base"
  else
    rm -rf -- "$stage"
    echo "ERROR $label failed: $name -> $base" >&2
    failed=1
    return 1
  fi
}

changed=0
failed=0
for source in "${SOURCES[@]}"; do
  name=${source##*/}
  src=$REPO_ROOT/$source
  if [[ ! -f $src/SKILL.md ]]; then
    echo "ERROR missing skill source: $source/SKILL.md" >&2
    failed=1
    continue
  fi
  want=$(tree_digest "$src")
  while IFS= read -r base; do
    dest=$base/$name
    # `synced` is another system's managed area; never write below it.
    case /${base#/}/ in
      */synced/*)
        echo "REFUSED $name -> $base: inside a 'synced' directory (not ours)" >&2
        failed=1; continue ;;
    esac
    state=$(state_of "$dest" "$want")
    if [[ $mode != install ]]; then
      printf '%-12s %s -> %s\n' "$state" "$name" "$base"
      [[ $state == current ]] || failed=1
      continue
    fi
    case $state in
      current) printf '%-12s %s -> %s\n' current "$name" "$base" ;;
      absent) install_one "$src" "$dest" "$want" "$base" "$name" installed "$source"; changed=1 ;;
      stale) install_one "$src" "$dest" "$want" "$base" "$name" updated "$source"; changed=1 ;;
      modified)
        if ((overwrite_modified)); then
          install_one "$src" "$dest" "$want" "$base" "$name" replaced "$source"; changed=1
        else
          echo "REFUSED $name -> $base: locally modified; pass --overwrite-modified to replace it" >&2
          failed=1
        fi ;;
      unmanaged)
        echo "REFUSED $name -> $base: present without $OWNER stamp (or is a symlink); leaving it alone" >&2
        failed=1 ;;
    esac
  done < <(destinations)
done

case $mode in
  check) ((failed)) && { echo "FAIL $failed target(s) are not current" >&2; exit 1; }; exit 0 ;;
  list) exit 0 ;;
esac
((failed)) && exit 1
((dry_run)) && { echo 'Dry run: no changes made.'; exit 0; }
((changed)) || echo 'INFO every owned skill is already current'
exit 0
