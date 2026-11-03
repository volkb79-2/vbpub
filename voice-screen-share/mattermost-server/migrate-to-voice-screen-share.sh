#!/usr/bin/env bash
# One-shot, idempotent migration of the GITIGNORED state of the Mattermost ciu
# project from <repo>/mattermost-server/ to <repo>/voice-screen-share/mattermost-server/.
#
# Git carries the tracked files (the merge of the move commit). Everything below
# exists only in the main checkout and is NOT in git:
#   .ciu/                      secrets (postgres password, webhook URLs, PATs) + overlay
#   vol-postgres-data/         postgres bind dir (uid 70, mode 0700): MOVED, never copied
#   vol-mattermost-config/     bind dir
#   vol-mattermost-logs/       bind dir
#   ciu.env, ciu.compose.yml, ciu.global.toml, ciu.instance.generated.toml
#                              generated + path/instance-id specific: set aside as
#                              <name>.pre-vss-move; `ciu env generate` re-creates them
# Optional (--copy-named-volumes, phase 2): the three docker named volumes are
# project-name prefixed and ciu derives the instance id from the physical path
# (3fnqqi -> new id), so they are copied to the new names.
#
# Usage (run in the MAIN checkout, after the branch is merged and the stack is down):
#   migrate-to-voice-screen-share.sh --dry-run            # print the plan, change nothing
#   migrate-to-voice-screen-share.sh --apply              # phase 1: move the state
#   (cd <repo> && ciu env generate --root-folder voice-screen-share/mattermost-server)
#   migrate-to-voice-screen-share.sh --copy-named-volumes # phase 2 (needs the new id)
#   migrate-to-voice-screen-share.sh --rollback [--dry-run]   # phase 1 undone
#
# Safety: only `mv` (rename inside one filesystem: ownership, modes and inodes are
# preserved, no data is read or copied), refuses to run while a container of the
# old instance runs, verifies destination metadata before reporting success, never
# deletes anything (the old directory is removed with `rmdir`, which only works when
# it is empty). Environment overrides exist for tests: REPO_ROOT, PHYS_ROOT, DOCKER,
# HOST_ESCAPE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}"
OLD="$REPO_ROOT/mattermost-server"
NEW="$REPO_ROOT/voice-screen-share/mattermost-server"
DOCKER="${DOCKER:-docker}"
HOST_ESCAPE="${HOST_ESCAPE:-host-escape}"
SUFFIX=".pre-vss-move"

# State that is MOVED as is.
MOVE_ITEMS=(.ciu vol-mattermost-config vol-mattermost-logs vol-postgres-data)
# Generated, instance/path specific: moved under a .pre-vss-move name.
ASIDE_ITEMS=(ciu.env ciu.compose.yml ciu.global.toml ciu.instance.generated.toml)
VOLUME_SUFFIXES=(mattermost-data mattermost-plugins mattermost-client-plugins)

MODE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --apply) MODE=apply ;;
    --rollback) MODE=rollback ;;
    --copy-named-volumes) MODE=volumes ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
DRY="${DRY:-0}"
[ "$MODE" = "" ] && [ "$DRY" = 1 ] && MODE=apply
[ -n "$MODE" ] || { echo "choose --dry-run, --apply, --copy-named-volumes or --rollback" >&2; exit 2; }

say() { printf '%s\n' "$*"; }
act() { if [ "$DRY" = 1 ]; then say "DRY-RUN: $*"; else say "RUN: $*"; fi; }

toml_value() { # toml_value FILE KEY -> value of `KEY = "value"`
  sed -n "s/^$2 *= *\"\(.*\)\"/\1/p" "$1" | head -n 1
}

meta() { stat -c '%u:%g %a' "$1"; }

host_path() { # translate a path under REPO_ROOT to its host path (PHYS_ROOT)
  local phys="${PHYS_ROOT:-}"
  if [ -z "$phys" ] && [ -f "$OLD/ciu.instance.generated.toml" ]; then
    phys="$(dirname "$(toml_value "$OLD/ciu.instance.generated.toml" physical_repo_root)")"
  fi
  [ -n "$phys" ] || { say "cannot determine PHYS_ROOT; set PHYS_ROOT" >&2; return 1; }
  printf '%s' "${1/#$REPO_ROOT/$phys}"
}

# rename src -> dst on one filesystem; fall back to a root rename via host-escape
# only when the plain rename is refused for permissions (postgres dir: uid 70 / 0700,
# a directory that changes parent needs write permission on itself).
move() {
  local src="$1" dst="$2"
  [ "$(stat -c %d "$src")" = "$(stat -c %d "$NEW")" ] || {
    say "REFUSE: $src and $dst are on different filesystems (a rename would become a copy)" >&2; exit 1; }
  act "mv -n -- $src $dst"
  [ "$DRY" = 1 ] && return 0
  if ! mv -n -- "$src" "$dst" 2>/dev/null; then
    say "plain mv refused; retrying as root via $HOST_ESCAPE (same-filesystem rename)"
    local hs hd
    hs="$(host_path "$src")"; hd="$(host_path "$dst")"
    "$HOST_ESCAPE" -- sh -c 'mv -n -- "$1" "$2"' sh "$hs" "$hd"
  fi
}

preflight_stopped() {
  local id=""
  [ -f "$OLD/ciu.instance.generated.toml" ] && id="$(toml_value "$OLD/ciu.instance.generated.toml" instance_id)"
  [ -n "$id" ] || return 0
  for name in "mattermost-$id-mattermost" "mattermost-$id-mattermost-db"; do
    if [ -n "$("$DOCKER" ps --filter "name=^$name\$" --format '{{.Names}}')" ]; then
      say "REFUSE: container $name is running; stop the old stack first" >&2; exit 1
    fi
  done
}

phase_apply() {
  [ -f "$NEW/ciu.defaults.toml.j2" ] || { say "REFUSE: $NEW has no tracked files: merge the move commit first" >&2; exit 1; }
  [ -d "$OLD" ] || { say "nothing to do: $OLD does not exist (already migrated)"; return 0; }
  preflight_stopped
  local item moved=0
  for item in "${MOVE_ITEMS[@]}" "${ASIDE_ITEMS[@]}"; do
    local dst="$NEW/$item"
    case " ${ASIDE_ITEMS[*]} " in *" $item "*) dst="$NEW/$item$SUFFIX" ;; esac
    if [ ! -e "$OLD/$item" ] && [ ! -L "$OLD/$item" ]; then
      [ -e "$dst" ] && say "skip $item: already at destination" || say "skip $item: absent in old tree"
      continue
    fi
    if [ -e "$dst" ]; then
      say "REFUSE: both $OLD/$item and $dst exist; resolve by hand" >&2; exit 1
    fi
    local before; before="$(meta "$OLD/$item")"
    move "$OLD/$item" "$dst"
    if [ "$DRY" = 0 ]; then
      [ -e "$dst" ] && [ ! -e "$OLD/$item" ] || { say "VERIFY FAILED for $item" >&2; exit 1; }
      [ "$(meta "$dst")" = "$before" ] || { say "VERIFY FAILED: metadata of $item changed ($before -> $(meta "$dst"))" >&2; exit 1; }
      [ "$item" != vol-postgres-data ] || [ -e "$dst/PG_VERSION" ] || say "note: PG_VERSION not readable as this user (expected for uid 70 / 0700)"
      say "ok $item ($before)"
    fi
    moved=$((moved + 1))
  done
  # Anything else left behind is reported, never touched.
  if [ "$DRY" = 1 ]; then
    act "rmdir $OLD (only if empty after the moves)"
  elif [ -d "$OLD" ]; then
    local left; left="$(ls -A "$OLD" 2>/dev/null || true)"
    if [ -n "$left" ]; then say "left in $OLD (not touched): $(printf '%s ' $left)"
    else act "rmdir $OLD"; rmdir "$OLD"; fi
  fi
  say "phase 1 complete ($moved item(s)). Next: cd $REPO_ROOT && ciu env generate --root-folder voice-screen-share/mattermost-server"
}

phase_rollback() {
  local item
  for item in "${MOVE_ITEMS[@]}" "${ASIDE_ITEMS[@]}"; do
    local from="$NEW/$item" to="$OLD/$item"
    case " ${ASIDE_ITEMS[*]} " in *" $item "*) from="$NEW/$item$SUFFIX" ;; esac
    [ -e "$from" ] || { say "skip $item: not at the new location"; continue; }
    [ -e "$to" ] && { say "REFUSE: $to exists" >&2; exit 1; }
    [ -d "$OLD" ] || { act "mkdir $OLD"; [ "$DRY" = 1 ] || mkdir "$OLD"; }
    move "$from" "$to"
  done
  say "rollback complete; the old tree must also have its tracked files back (git checkout of the pre-merge commit)"
}

phase_volumes() {
  local gen="$NEW/ciu.instance.generated.toml" oldgen="$NEW/ciu.instance.generated.toml$SUFFIX"
  [ -f "$gen" ] || { say "REFUSE: run 'ciu env generate --root-folder voice-screen-share/mattermost-server' first" >&2; exit 1; }
  [ -f "$oldgen" ] || { say "REFUSE: $oldgen missing: phase 1 has not run" >&2; exit 1; }
  local oid nid
  oid="$(toml_value "$oldgen" instance_id)"; nid="$(toml_value "$gen" instance_id)"
  [ -n "$oid" ] && [ -n "$nid" ] || { say "cannot read instance ids" >&2; exit 1; }
  [ "$oid" != "$nid" ] || { say "ids equal ($oid): nothing to copy"; return 0; }
  local v
  for v in "${VOLUME_SUFFIXES[@]}"; do
    local src="mattermost-$oid-mattermost-server_$v" dst="mattermost-$nid-mattermost-server"
    local dstvol="${dst}_$v"
    if ! "$DOCKER" volume inspect "$src" >/dev/null 2>&1; then say "skip $v: source volume $src absent"; continue; fi
    if "$DOCKER" volume inspect "$dstvol" >/dev/null 2>&1; then say "skip $v: $dstvol already exists"; continue; fi
    act "docker volume create $dstvol (compose labels) + cp -a $src -> $dstvol"
    [ "$DRY" = 1 ] && continue
    "$DOCKER" volume create --label "com.docker.compose.project=$dst" --label "com.docker.compose.volume=$v" "$dstvol" >/dev/null
    "$DOCKER" run --rm --name "vss-migrate-$v" -v "$src:/from:ro" -v "$dstvol:/to" --entrypoint sh alpine:3 \
      -c 'cp -a /from/. /to/ && [ "$(find /from | wc -l)" = "$(find /to | wc -l)" ]' \
      || { say "VERIFY FAILED copying $v; remove only the NEW volume $dstvol by its exact name and retry" >&2; exit 1; }
    say "ok $v copied and entry counts match"
  done
  say "old containers/volumes/network are left in place; remove them later BY EXACT NAME only (see MIGRATION.md)"
}

case "$MODE" in
  apply) phase_apply ;;
  rollback) phase_rollback ;;
  volumes) phase_volumes ;;
esac
