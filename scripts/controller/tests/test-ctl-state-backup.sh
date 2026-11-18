#!/usr/bin/env bash
set -uo pipefail
# Every check builds its own controller root; the caller's ambient configuration must not decide anything.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE CONTROLLER_BACKUP_DIR
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BACKUP=${CTL_STATE_BACKUP_SCRIPT:-$SCRIPT_DIR/../ctl-state-backup.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-state-backup-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$ROOT/home" "$ROOT/outside"
printf 'OUTSIDE-SECRET-CONTENT\n' > "$ROOT/outside/secret"
fixture() {
  # $1 name; sets R (repo), H (controller home), B (backup dir, not created)
  R=$ROOT/$1/repo H=$ROOT/$1/ctl B=$ROOT/$1/backups
  mkdir -p "$R/.controller-state/sub/.git" "$H/reports" "$H/remote-gates/rgw-1/artifacts" "$H/remote-gate-work/wt" "$H/logs" "$H/runs"
  git init -q "$R"
  printf 'handoff\n' > "$R/.controller-state/HANDOFF.md"
  printf 'x\n' > "$R/.controller-state/sub/.git/config"
  printf '{}\n' > "$R/.controller-state/stream.jsonl"
  ln -s "$ROOT/outside/secret" "$R/.controller-state/escape"
  printf 'plan\n' > "$R/plan.yaml"
  printf 'A=1\n' > "$H/controller.local.env"; printf '{}\n' > "$H/hosts.local.json"; printf 'notes\n' > "$H/notes.txt"
  printf 'report\n' > "$H/reports/r.md"
  printf '{}\n' > "$H/remote-gates/rgw-1/result.json"; printf '{}\n' > "$H/remote-gates/rgw-1/meta.json"
  printf 'log\n' > "$H/remote-gates/rgw-1/job.log"; printf '{}\n' > "$H/remote-gates/rgw-1/artifacts/a.json"
  printf 'w\n' > "$H/remote-gate-work/wt/f"; printf 'log\n' > "$H/logs/x.log"
  for ext in thread state.md 'done' jsonl err; do printf 'r\n' > "$H/runs/a.$ext"; done
}
run_backup() { env -i HOME="$ROOT/home" PATH="$PATH" CONTROLLER_HOME="$H" CONTROLLER_REPO="$R" CONTROLLER_BACKUP_DIR="${BKV-$B}" "$BASH" "$BACKUP" "$@" > "$ROOT/out" 2> "$ROOT/err"; RC=$?; }
expect_rc() { [[ $RC == "$1" ]] || { cat "$ROOT/out" "$ROOT/err" >&2; fail "$2: exit $RC, wanted $1"; }; }
said() { grep -qF -- "$1" "$ROOT/out" "$ROOT/err" || { cat "$ROOT/out" "$ROOT/err" >&2; fail "$2 (missing: $1)"; }; }
EXPECT='home/controller.local.env
home/hosts.local.json
home/remote-gates/rgw-1/meta.json
home/remote-gates/rgw-1/result.json
home/reports/r.md
repo/.controller-state/HANDOFF.md
repo/.controller-state/escape
repo/plan.yaml
runs/a.done
runs/a.state.md
runs/a.thread'

# --- refusals
fixture refuse
BKV='' run_backup; expect_rc 2 'unset CONTROLLER_BACKUP_DIR'; said 'CONTROLLER_BACKUP_DIR is not set' 'unset dir'
BKV=relative/dir run_backup; expect_rc 2 'relative CONTROLLER_BACKUP_DIR'; said 'must be an absolute path' 'relative dir'
git init -q "$ROOT/wt"
BKV=$ROOT/wt/sub/backups run_backup; expect_rc 2 'a backup directory inside a work tree'; said 'inside a git work tree' 'inside a work tree'
[[ ! -e $ROOT/wt/sub ]] || fail 'a refused backup must not create the directory'
BKV=$R/.controller-state/x run_backup; expect_rc 2 'a backup directory inside the repository'
run_backup --bogus; expect_rc 2 'unknown argument'
run_backup --max-file-bytes abc; expect_rc 2 'a non-numeric cap'

# --- --list: what would be saved, writes nothing
fixture list
head -c 300 /dev/zero > "$R/.controller-state/big.bin"
run_backup --list --max-file-bytes 100
expect_rc 0 '--list'
[[ ! -e $B ]] || fail '--list must not create the backup directory'
[[ $(sed -n -E 's,^[0-9]+ ((home|repo|runs)/.*)$,\1,p' "$ROOT/out" | LC_ALL=C sort) == "$EXPECT" ]] || { cat "$ROOT/out" >&2; fail 'the --list entries differ from the expected state set'; }
said 'SKIPPED (over 100 bytes; --include-large): 300 repo/.controller-state/big.bin' 'a large file is reported as skipped'
said 'total: 11 file(s)' 'the list total'
grep -qE '^[0-9]+ home/controller.local.env$' "$ROOT/out" || fail 'a list entry is "<size> <name>"'
run_backup --list --include-large --max-file-bytes 100
grep -q 'repo/.controller-state/big.bin' "$ROOT/out" || fail '--include-large keeps the large file'
! grep -q SKIPPED "$ROOT/out" || fail '--include-large skips nothing'

# --- the archive
fixture arch
head -c 300 /dev/zero > "$R/.controller-state/big.bin"
run_backup --max-file-bytes 100
expect_rc 0 'backup'
archive=$(sed -n 's/^archive: //p' "$ROOT/out")
[[ -f $archive ]] || fail "no archive at: $archive"
[[ $(stat -c %a "$archive") == 600 ]] || fail 'the archive must be mode 0600'
[[ $(stat -c %a "$archive.sha256") == 600 ]] || fail 'the checksum sidecar must be mode 0600'
for leftover in "$B"/.[!.]* "$B"/*.tmp*; do [[ ! -e $leftover ]] || fail "a temporary file was left in the backup directory: $leftover"; done
sum=$(sha256sum "$archive" | cut -d' ' -f1)
said "sha256:  $sum" 'the printed sha256 is the archive checksum'
said "$(stat -c %s "$archive") byte(s), 11 file(s), 1 skipped as large" 'the printed size'
[[ $(tar -tf "$archive" | LC_ALL=C sort) == "$EXPECT" ]] || { tar -tf "$archive" >&2; fail 'the archive members differ from the expected state set (no jsonl, .git, worktrees, logs, artifacts, large files)'; }
mkdir -p "$ROOT/restore"; tar -xf "$archive" -C "$ROOT/restore"
[[ -L $ROOT/restore/repo/.controller-state/escape ]] || fail 'a symlink must be archived as a link'
! grep -rqF 'OUTSIDE-SECRET-CONTENT' "$ROOT/restore" || fail 'a symlink must never be followed into the archive'
run_backup --include-large --max-file-bytes 100
archive2=$(sed -n 's/^archive: //p' "$ROOT/out")
tar -tf "$archive2" | grep -qx 'repo/.controller-state/big.bin' || fail '--include-large must archive the large file'

# --- --verify
run_backup --verify "$archive"; expect_rc 0 'verify'; said '11 member(s)' 'verify counts the members'; said 'matches the sidecar' 'verify compares the sidecar'
printf '%s  x\n' 0000000000000000000000000000000000000000000000000000000000000000 > "$archive.sha256"
run_backup --verify "$archive"; expect_rc 1 'a sidecar that differs'; said 'sha256 differs' 'verify refuses a changed checksum'
head -c 40 "$archive" > "$ROOT/truncated.${archive##*.}"; case $archive in *.zst) ext=tar.zst ;; *) ext=tar.gz ;; esac
mv -- "$ROOT/truncated.${archive##*.}" "$ROOT/cut.$ext"
run_backup --verify "$ROOT/cut.$ext"; expect_rc 1 'a truncated archive'
run_backup --verify "$ROOT/none.tar.gz"; expect_rc 2 'a missing archive'

# --- nothing to save
fixture empty
rm -rf "$R/.controller-state" "$R/plan.yaml" "$H/controller.local.env" "$H/hosts.local.json" "$H/reports" "$H/remote-gates" "$H/runs"/a.*
run_backup; expect_rc 1 'an empty state'; said 'nothing to save' 'empty state'

echo 'PASS ctl-state-backup'
