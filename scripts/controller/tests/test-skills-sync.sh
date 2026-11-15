#!/usr/bin/env bash
# skills-sync.sh: idempotent install, honest check, no overwrite of a directory
# this script does not own, and a real source change is told apart from a local
# edit. Runs against a COPY of the repository's two owned skill sources so a
# test can change the source without touching the checkout.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REAL_SCRIPT=${SKILLS_SYNC_SCRIPT:-$SCRIPT_DIR/../skills-sync.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/skills-sync-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }

[[ -f $REAL_SCRIPT ]] || fail "no skills-sync.sh at $REAL_SCRIPT"
repo=$ROOT/repo
mkdir -p -- "$repo/scripts/controller"
cp -- "$REAL_SCRIPT" "$repo/scripts/controller/skills-sync.sh"
for source in run-gate-project/.claude/skills/run-gate-cli \
              assay/.claude/skills/assay-cli; do
  name=${source##*/}
  mkdir -p -- "$repo/${source%/*}"
  cp -R -- "$SCRIPT_DIR/../../../$source" "$repo/${source%/*}/"
  [[ -f $repo/$source/SKILL.md ]] || fail "could not stage $source"
done
script=$repo/scripts/controller/skills-sync.sh
dest=$ROOT/home
mkdir -p -- "$dest"
export HOME=$ROOT
run() { bash "$script" "$@" --dest "$dest" >"$ROOT/out" 2>"$ROOT/err"; }

# 1. install into an empty destination installs every owned skill and stamps it.
run install || fail "first install exited non-zero: $(cat "$ROOT/err")"
for name in run-gate-cli assay-cli; do
  [[ -f $dest/$name/SKILL.md ]] || fail "install did not create $name"
  [[ -f $dest/$name/.controller-skills-stamp.json ]] || fail "$name has no stamp"
done

# 2. a second install is a no-op that says so.
run install || fail "second install exited non-zero"
contains "$ROOT/out" "INFO every owned skill is already current" "second install is not idempotent"

# 3. check passes while current.
run check || fail "check refused a current install: $(cat "$ROOT/err")"

# 4. a locally edited copy is REFUSED, not clobbered, and check fails.
printf '\nlocal edit\n' >>"$dest/assay-cli/SKILL.md"
run check && fail "check accepted a modified copy"
contains "$ROOT/err" "FAIL" "check did not name its failure"
cp "$dest/assay-cli/SKILL.md" "$ROOT/edited.txt"
run install && fail "install replaced a locally modified copy without the flag"
cmp -s "$ROOT/edited.txt" "$dest/assay-cli/SKILL.md" || fail "the modified copy was overwritten anyway"

# 5. an unstamped (or foreign) directory is never touched.
rm -rf -- "$dest/run-gate-cli"
mkdir -p -- "$dest/run-gate-cli"
printf 'not ours\n' >"$dest/run-gate-cli/SKILL.md"
run install && fail "install exited zero despite an unmanaged directory"
contains "$ROOT/err" "REFUSED run-gate-cli" "an unstamped directory was not refused"
contains "$dest/run-gate-cli/SKILL.md" "not ours" "the unstamped directory was overwritten"

# 5b. a directory stamped by a DIFFERENT owner is unmanaged too, even when its
#     own contents match its own stamp (a foreign tool's skill, not ours).
#     Ownership is the stamp's `tool` field, not merely "a stamp exists".
tree_digest_of() {
  local dir=$1 file rel
  while IFS= read -r file; do
    rel=${file#"$dir"/}
    printf '%s %s\n' "$rel" "$(sha256sum "$file" | cut -d' ' -f1)"
  done < <(find "$dir" -type f ! -name .controller-skills-stamp.json -print | LC_ALL=C sort) |
    sha256sum | cut -d' ' -f1
}
rm -rf -- "$dest/run-gate-cli"
mkdir -p -- "$dest/run-gate-cli"
printf 'owned by another tool\n' >"$dest/run-gate-cli/SKILL.md"
foreign=$(tree_digest_of "$dest/run-gate-cli")
printf '{"tool":"ciu","name":"run-gate-cli","source":"elsewhere","digest":"%s"}\n' "$foreign" \
  >"$dest/run-gate-cli/.controller-skills-stamp.json"
run install && fail "install exited zero despite a foreign-owned stamp"
contains "$ROOT/err" "REFUSED run-gate-cli" "a foreign-owned stamp was not refused"
contains "$dest/run-gate-cli/SKILL.md" "owned by another tool" "a foreign-owned directory was overwritten"

# 6. a symlink is unmanaged too.
rm -rf -- "$dest/run-gate-cli"
mkdir -p -- "$ROOT/elsewhere"
printf 'elsewhere\n' >"$ROOT/elsewhere/SKILL.md"
ln -s "$ROOT/elsewhere" "$dest/run-gate-cli"
run install && fail "install exited zero despite a symlinked target"
[[ -L $dest/run-gate-cli ]] || fail "the symlink was replaced"

# 6a. a DANGLING symlink is unmanaged, not absent. `absent` is the branch that
#     installs, so folding the two would quietly replace a link this script never
#     created (its target may be a home that is not mounted yet).
rm -rf -- "$dest/run-gate-cli"
ln -s "$ROOT/not-mounted-yet" "$dest/run-gate-cli"
run install && fail "install exited zero despite a dangling symlink"
[[ -L $dest/run-gate-cli ]] || fail "the dangling symlink was replaced"
[[ ! -e $ROOT/not-mounted-yet ]] || fail "install created the dangling target"

# 6b. …including a symlink to a directory that IS current and stamped: a link
#     is refused as a target shape, not merely because it lacks a stamp.
rm -rf -- "$dest/run-gate-cli"
run install --overwrite-modified || fail "install did not restore run-gate-cli before step 6b"
rm -rf -- "$ROOT/installed"
bash "$script" install --dest "$ROOT/installed" >"$ROOT/out" 2>"$ROOT/err" || fail "a nested --dest install failed"
cp -R -- "$ROOT/installed/run-gate-cli" "$ROOT/linked-run-gate-cli"
rm -rf -- "$dest/run-gate-cli"
ln -s "$ROOT/linked-run-gate-cli" "$dest/run-gate-cli"
run install && fail "install wrote through a symlink to a stamped, current copy"
[[ -L $dest/run-gate-cli ]] || fail "the stamped symlink was replaced"
contains "$ROOT/err" "REFUSED run-gate-cli" "a stamped symlink was not refused"
# Step 6b repaired the tree with --overwrite-modified, which also restored
# assay-cli; re-apply the local edit so step 7 still judges it.
cp -- "$ROOT/edited.txt" "$dest/assay-cli/SKILL.md"

# 7. --overwrite-modified replaces a stamped but locally edited copy AND
#    restores a skill whose directory was removed.
rm -rf -- "$dest/run-gate-cli"
cmp -s "$ROOT/edited.txt" "$dest/assay-cli/SKILL.md" || fail "the local edit was lost before step 7"
run install --overwrite-modified || fail "--overwrite-modified did not repair the tree"
cmp -s "$ROOT/edited.txt" "$dest/assay-cli/SKILL.md" && fail "the edited copy was not replaced"
[[ -f $dest/run-gate-cli/SKILL.md ]] || fail "a missing stamped skill was not reinstalled"
run check || fail "check failed after repair"

# 8. a SOURCE change is stale (not "modified"), and a plain install updates it.
printf '\nupstream change\n' >>"$repo/assay/.claude/skills/assay-cli/SKILL.md"
run check && fail "check accepted a stale copy"
contains "$ROOT/out" "stale" "a changed source was not reported stale"
run install || fail "install refused a merely stale copy"
contains "$ROOT/out" "updated" "install did not report the stale copy as updated"
run check || fail "check failed after the stale copy was updated"

# 9. --dry-run changes nothing.
#    The tree is made to NEED work first (one copy edited, one removed, one
#    source moved): a dry run over an already-current tree changes nothing
#    even when it is broken, so it proves nothing.
printf '\nlocal edit before dry run\n' >>"$dest/assay-cli/SKILL.md"
rm -rf -- "$dest/run-gate-cli"
printf '\nsecond upstream change\n' >>"$repo/assay/.claude/skills/assay-cli/SKILL.md"
before=$(find "$dest" -print | LC_ALL=C sort | xargs -d '\n' ls -ld --time-style=+ | sha256sum; find "$dest" -type f | LC_ALL=C sort | xargs sha256sum | sha256sum)
run install --dry-run --overwrite-modified || fail "dry run exited non-zero"
after=$(find "$dest" -print | LC_ALL=C sort | xargs -d '\n' ls -ld --time-style=+ | sha256sum; find "$dest" -type f | LC_ALL=C sort | xargs sha256sum | sha256sum)
[[ $before == "$after" ]] || fail "dry run changed the destination"
contains "$ROOT/out" "would" "dry run did not list what it would do"
contains "$ROOT/out" "Dry run: no changes made" "dry run did not announce itself"

# 9a. `--dest` takes its value wherever it sits (the `run` helper always puts
#     it last, which once hid `--dest DIR --dry-run` being rejected), and a
#     flag-looking value is refused rather than used as a directory.
bash "$script" install --dest "$ROOT/fresh" --dry-run >"$ROOT/out" 2>"$ROOT/err" \
  || fail "--dest followed by another flag was rejected: $(cat "$ROOT/err")"
[[ ! -e $ROOT/fresh ]] || fail "a dry run created its destination"
bash "$script" install --dest --dry-run >"$ROOT/out" 2>"$ROOT/err" \
  && fail "--dest swallowed a flag as its directory"

# 9c. a failed install is a failure, not "installed" (here the destination's
#     parent is a regular file, so nothing can be created), and a skill dir
#     below a `synced` directory is refused outright.
printf 'x\n' >"$ROOT/not-a-dir"
bash "$script" install --dest "$ROOT/not-a-dir" >"$ROOT/out" 2>"$ROOT/err" \
  && fail "install exited zero although it could not write anything"
grep -q '^installed' "$ROOT/out" && fail "a failed install was reported as installed"
bash "$script" install --dest "$ROOT/home/synced" >"$ROOT/out" 2>"$ROOT/err" \
  && fail "install wrote below a synced directory"
contains "$ROOT/err" "synced" "the synced refusal was not explained"
[[ ! -e $ROOT/home/synced ]] || fail "a synced directory was created"

# 9b. `--help` prints the WHOLE header: durable facts (states, flags) must not
#     silently vanish from it when the header grows a line.
usage=$(bash "$script" --help)
for needle in   'Usage: skills-sync.sh [install|check|list]'   'States: current'   'unmanaged (no stamp, a symlink, or a file)'; do
  grep -qF -- "$needle" <<<"$usage" || fail "--help omits: $needle"
done

# 10. The three documents that must stay in sync name the script, and each of
#     the sources it owns appears in the README (the WHAT), while the README
#     links to the guides instead of re-arguing the WHY.
repo_scripts=${SKILLS_SYNC_REPO_SCRIPTS:-$SCRIPT_DIR/..}
docs_root=$repo_scripts
for doc in README.md docs/DESIGN-GUIDE.md docs/CONSUMERS.md; do
  [[ -f $docs_root/$doc ]] || fail "missing $doc beside the controller scripts"
done
contains "$docs_root/README.md" 'skills-sync.sh' 'README names the skills sync script'
contains "$docs_root/README.md" 'docs/DESIGN-GUIDE.md#copy-hand-written-skills-instead-of-giving-them-a-skills-verb' \
  'README links the skills design rationale'
contains "$docs_root/README.md" 'docs/CONSUMERS.md#install-the-tool-skills-an-agent-will-read' \
  'README links the skills adoption guide'
for source in run-gate-project/.claude/skills/run-gate-cli \
              assay/.claude/skills/assay-cli; do
  name=${source##*/}
  contains "$docs_root/README.md" "$name" "README names the owned skill $name"
done
# The README's packaged-vs-hand-written split must match the script's SOURCES:
# a tool that adopts `skills install` leaves SOURCES, and a README that still
# lists it as hand-written is exactly the drift this test exists for (fleetd
# adopted the verb while this refresh was in flight). Both directions are
# checked: the packaged tools are named with their verb, and every SOURCES
# entry is named as one of the two copied skills.
for tool in cmru ciu pwmcp fleet; do
  contains "$docs_root/README.md" "\`$tool skills install\`" "README names the packaged install for $tool"
done
sources=$(sed -n '/^SOURCES=(/,/^)/p' "$script" | grep -o '[a-zA-Z0-9._-]*/skills/[a-zA-Z0-9._-]*' | sed 's#.*/##')
for name in $sources; do
  contains "$docs_root/README.md" "\`$name\`" "README names the copied skill $name"
done
# The two anchors the README points at must be real headings, not guesses.
anchor_of() { sed -n 's/^\(## \)//p' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g' | grep -qxF "$2"; }
anchor_of "$docs_root/docs/DESIGN-GUIDE.md" 'copy-hand-written-skills-instead-of-giving-them-a-skills-verb' || \
  fail 'the linked design-guide anchor has no matching heading'
anchor_of "$docs_root/docs/CONSUMERS.md" 'install-the-tool-skills-an-agent-will-read' || \
  fail 'the linked consumers anchor has no matching heading'
echo "PASS skills-sync"
