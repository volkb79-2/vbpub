#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s nullglob
umask 077

CANON="${CANON:-$HOME/.codex}"
ALT="${ALT:-$HOME/.codex2}"
SQLHOME="${SQLHOME:-$CANON/sqlite-shared}"
CODEX="${CODEX:-codex}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
BACKUP="${BACKUP:-$HOME/codex-unification-$STAMP}"
BOOTSTRAP_TIMEOUT="${BOOTSTRAP_TIMEOUT:-300}"

log(){ printf '\n==> %s\n' "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn(){ printf 'WARN: %s\n' "$*" >&2; }
onerr(){ local rc=$?; trap - ERR; printf '\nFAILED (rc=%s). Audit/backup: %s\n' "$rc" "$BACKUP" >&2; exit "$rc"; }
trap onerr ERR

for x in bash python3 rsync jq find cp mv ln readlink realpath pgrep ps awk df du timeout tee grep cat; do
  command -v "$x" >/dev/null || die "missing command: $x"
done
command -v "$CODEX" >/dev/null || die "Codex binary not found: $CODEX"
python3 - <<'PYMOD' >/dev/null || die "python3 needs stdlib modules sqlite3 and tomllib"
import sqlite3, tomllib
PYMOD

CANON="$(realpath -m "$CANON")"; ALT="$(realpath -m "$ALT")"
SQLHOME="$(realpath -m "$SQLHOME")"; BACKUP="$(realpath -m "$BACKUP")"
H="$(realpath -m "$HOME")"
[[ "$CANON" != "$ALT" ]] || die "CANON == ALT"
[[ "$CANON" == "$H"/* && "$ALT" == "$H"/* && "$SQLHOME" == "$CANON"/* ]] || die "unexpected paths"
[[ -d "$CANON" && ! -L "$CANON" && -d "$ALT" && ! -L "$ALT" ]] || die "both CODEX_HOME dirs must be physical directories"
[[ ! -e "$BACKUP" ]] || die "backup path already exists: $BACKUP"
if [[ -e "$SQLHOME" ]]; then
  [[ -d "$SQLHOME" && ! -L "$SQLHOME" ]] || die "$SQLHOME is not a physical directory"
  [[ -z "$(find "$SQLHOME" -mindepth 1 -print -quit)" ]] || die "$SQLHOME is not empty"
fi

quiescent(){
  local p="$(pgrep -x codex 2>/dev/null || true)"
  [[ -z "$p" ]] || { ps -fp $p >&2 || true; die "Codex processes still running"; }
  p="$(pgrep -f '/codex-code-mode-host([[:space:]]|$)' 2>/dev/null || true)"
  [[ -z "$p" ]] || { ps -fp $p >&2 || true; die "codex-code-mode-host still running"; }
}

preflight(){
python3 - "$CANON" "$ALT" <<'PY'
import filecmp,re,sys
from collections import defaultdict
from pathlib import Path
C,A=map(Path,sys.argv[1:])
rx=re.compile(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',re.I)
err=[]
def scan(home):
  ids=defaultdict(list); files={}
  for kind,name in [('active','sessions'),('archived','archived_sessions')]:
    root=home/name
    if not root.exists(): continue
    if root.is_symlink(): err.append(f'rollout root is already a symlink: {root}'); continue
    for p in root.rglob('*'):
      if p.is_symlink(): err.append(f'symlink inside rollout tree: {p}'); continue
      if not p.is_file(): continue
      files[(kind,p.relative_to(root).as_posix())]=p
      if p.name.startswith('rollout-'):
        m=rx.search(p.name)
        if not m: err.append(f'malformed rollout filename: {p}')
        else: ids[m.group(1).lower()].append(p)
  return ids,files
ci,cf=scan(C); ai,af=scan(A)
for label,d in [('canonical',ci),('alternate',ai)]:
  for tid,ps in d.items():
    if len(ps)>1: err.append(f'duplicate UUID inside {label}: {tid}: {ps}')
for tid in sorted(set(ci)&set(ai)):
  err.append(f'UUID exists in both profiles: {tid}: {ci[tid][0]} | {ai[tid][0]}')
for k in sorted(set(cf)&set(af)):
  same=filecmp.cmp(cf[k],af[k],shallow=False)
  err.append(f'relative-path collision ({"identical" if same else "DIFFERENT"}): {k}: {cf[k]} | {af[k]}')
if err:
  print('\n'.join('  '+x for x in err),file=sys.stderr); raise SystemExit(1)
print(f'canonical IDs={len(ci)} alternate IDs={len(ai)} expected unified={len(ci)+len(ai)}')
PY
}

backup_state(){
  mkdir -p "$BACKUP/codex" "$BACKUP/codex2"
  python3 - "$CANON" "$BACKUP/codex" "$ALT" "$BACKUP/codex2" <<'PY'
import csv,shutil,sqlite3,sys
from pathlib import Path
for i in (0,2):
  root,out=Path(sys.argv[i+1]),Path(sys.argv[i+2])
  for p in root.rglob('*'):
    if not p.is_file(): continue
    if '.sqlite' not in p.name and p.name not in {'config.toml','session_index.jsonl','.codex-global-state.json'}: continue
    dst=out/p.relative_to(root); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
  for db in root.rglob('state_*.sqlite'):
    try:
      con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
      cols=[r[1] for r in con.execute('pragma table_info(threads)')]
      if not cols: con.close(); continue
      dst=out/('__'.join(db.relative_to(root).parts)+'.threads.csv')
      with dst.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(cols); w.writerows(con.execute('select * from threads'))
      con.close()
    except Exception as e:
      (out/'state-export-errors.txt').open('a').write(f'{db}: {e}\n')
PY
  "$CODEX" --version > "$BACKUP/codex-version.txt"
}

rel_link(){ python3 - "$1" "$2" <<'PY'
import os,sys
from pathlib import Path
print(os.path.relpath(Path(sys.argv[1]),Path(sys.argv[2]).parent))
PY
}

merge_tree(){
  local name=$1 src="$ALT/$1" dst="$CANON/$1" saved="$ALT/$1.pre-unification-$STAMP"
  mkdir -p "$dst"
  [[ ! -L "$src" ]] || die "$src is already a symlink; aborting rather than guessing partial state"
  if [[ -e "$src" ]]; then
    [[ -d "$src" ]] || die "$src is not a directory"
    rsync -a --ignore-existing --itemize-changes "$src/" "$dst/" | tee "$BACKUP/rsync-$name.log"
    local diff
    diff="$(rsync -aicn --out-format='%i %n%L' "$src/" "$dst/")"
    [[ -z "$diff" ]] || { printf '%s\n' "$diff" >&2; die "$name copy verification failed"; }
    quiescent
    [[ ! -e "$saved" && ! -L "$saved" ]] || die "rollback path exists: $saved"
    mv "$src" "$saved"
  fi
  ln -s "$(rel_link "$dst" "$src")" "$src"
  [[ "$(realpath -m "$src")" == "$(realpath -m "$dst")" ]] || die "bad symlink: $src"
}

share_locks(){
  local dst="$CANON/thread-writer-locks" src="$ALT/thread-writer-locks" saved="$ALT/thread-writer-locks.pre-unification-$STAMP"
  mkdir -p "$dst"
  [[ ! -L "$src" ]] || die "$src is already a symlink; aborting rather than guessing partial state"
  if [[ -e "$src" ]]; then
    [[ -d "$src" ]] || die "$src is not a directory"
    [[ ! -e "$saved" && ! -L "$saved" ]] || die "rollback path exists: $saved"
    mv "$src" "$saved"
  fi
  ln -s "$(rel_link "$dst" "$src")" "$src"
}

merge_indexes(){
  local m="$BACKUP/session_index.merged.jsonl" args=() f
  for f in "$CANON/session_index.jsonl" "$ALT/session_index.jsonl"; do
    if [[ -e "$f" ]]; then [[ -f "$f" && ! -L "$f" ]] || die "$f must be a physical file"; jq -e . "$f" >/dev/null; args+=("$f"); fi
  done
  if ((${#args[@]})); then
    jq -cs 'unique_by([.id,.thread_name,.updated_at])|sort_by(.updated_at // "")|.[]' "${args[@]}" > "$m"
  else : > "$m"; fi
  cp "$m" "$CANON/session_index.jsonl"; cp "$m" "$ALT/session_index.jsonl"
}

quickcheck(){
python3 - "$1" <<'PY'
import sqlite3,sys
c=sqlite3.connect(f'file:{sys.argv[1]}?mode=ro',uri=True); r=c.execute('pragma quick_check').fetchone(); c.close()
if not r or r[0]!='ok': raise SystemExit(f'quick_check failed: {r}')
PY
}

bootstrap_db(){
  local out="$BACKUP/bootstrap.stdout.jsonl" err="$BACKUP/bootstrap.stderr.log" rc
  set +e
  { printf '%s\n' '{"method":"initialize","id":1,"params":{"clientInfo":{"name":"codex-unifier","version":"1"}}}';
    printf '%s\n' '{"method":"initialized","params":{}}'; } |
    timeout --signal=TERM --kill-after=10s "${BOOTSTRAP_TIMEOUT}s" \
      env CODEX_HOME="$CANON" CODEX_SQLITE_HOME="$SQLHOME" "$CODEX" app-server --listen stdio:// >"$out" 2>"$err"
  rc=$?; set -e
  ((rc!=124 && rc!=137)) || die "app-server bootstrap timed out; see $err"
  ((rc==0)) || warn "bootstrap exited $rc; doctor validation will decide whether state is usable"
  local dbs=("$SQLHOME"/state_*.sqlite)
  ((${#dbs[@]}==1)) || die "expected exactly one state_*.sqlite in $SQLHOME"
  quickcheck "${dbs[0]}"; printf '%s\n' "${dbs[0]}" > "$BACKUP/shared-state-db.path"
}

doctor(){
  local home=$1 mode=$2 out=$3 err=$4 rc
  set +e
  if [[ $mode == env ]]; then env CODEX_HOME="$home" CODEX_SQLITE_HOME="$SQLHOME" "$CODEX" doctor --json >"$out" 2>"$err"
  else env -u CODEX_SQLITE_HOME CODEX_HOME="$home" "$CODEX" doctor --json >"$out" 2>"$err"; fi
  rc=$?; set -e
  jq -e . "$out" >/dev/null || die "invalid doctor JSON for $home; see $err"
  ((rc==0)) || warn "doctor rc=$rc for $home; checking migration-specific state only"
}

validate_doctor(){
  local f=$1 label=$2
  jq -e '
    .checks["state.rollout_db_parity"].details as $d |
    (.checks["state.paths"].details["state DB integrity"] == "ok") and
    ([$d["rollout DB missing active rows"],$d["rollout DB missing archived rows"],
      $d["rollout DB stale rows"],$d["rollout DB archive mismatches"],
      $d["rollout DB duplicate rollout thread ids"],$d["rollout DB duplicate DB paths"],
      $d["rollout DB scan errors"],$d["rollout DB malformed file names"]]
      | all(. == "0")) and ($d["rollout DB scan cap reached"] == "false")
  ' "$f" >/dev/null || { jq '.checks["state.paths"],.checks["state.rollout_db_parity"]' "$f" >&2; die "$label state validation failed"; }
}

same_inventory(){
  jq -n --slurpfile a "$1" --slurpfile b "$2" '
    ["rollout DB active files","rollout DB archived files","rollout DB rows","rollout DB active rows","rollout DB archived rows"] as $k |
    all($k[]; $a[0].checks["state.rollout_db_parity"].details[.] == $b[0].checks["state.rollout_db_parity"].details[.])
  ' | grep -qx true || die "doctor inventories differ"
}

set_sqlhome(){
python3 - "$1" "$SQLHOME" <<'PY'
import json,os,re,sys,tempfile,tomllib
from pathlib import Path
p=Path(sys.argv[1]); val=sys.argv[2]; text=p.read_text() if p.exists() else ''
if text.strip(): tomllib.loads(text)
lines=text.splitlines(keepends=True); cut=next((i for i,x in enumerate(lines) if x.lstrip().startswith('[')),len(lines))
head=[x for x in lines[:cut] if not re.match(r'^\s*sqlite_home\s*=',x)]; new=f'sqlite_home = {json.dumps(val)}\n'+''.join(head+lines[cut:])
tomllib.loads(new); p.parent.mkdir(parents=True,exist_ok=True)
mode=(p.stat().st_mode & 0o777) if p.exists() else 0o600
fd,tmp=tempfile.mkstemp(prefix=p.name+'.',dir=p.parent)
try:
  with os.fdopen(fd,'w') as f: f.write(new); f.flush(); os.fsync(f.fileno())
  os.chmod(tmp,mode); os.replace(tmp,p)
finally:
  try: os.unlink(tmp)
  except FileNotFoundError: pass
PY
}

prepare_daemon_runtime(){
  local home=$1 tag=$2 f
  local out="$BACKUP/$tag-daemon-runtime-old"
  mkdir -p "$out"
  for f in \
    "$home/app-server-control/app-server-control.sock" \
    "$home/app-server-daemon/app-server.pid" \
    "$home/app-server-daemon/app-server-updater.pid" \
    "$home/app-server-daemon/daemon.pid" \
    "$home/app-server-daemon/daemon-updater.pid" \
    "$home/app-server-daemon/daemon-updater.sock"; do
    if [[ -e "$f" || -L "$f" ]]; then
      mv "$f" "$out/$(basename "$f")"
    fi
  done
}

start_daemon(){
  local home=$1 tag=$2
  env CODEX_HOME="$home" CODEX_SQLITE_HOME="$SQLHOME" "$CODEX" app-server daemon update --from-cli -y > "$BACKUP/$tag-daemon-update.json"
  jq -e . "$BACKUP/$tag-daemon-update.json" >/dev/null
  env CODEX_HOME="$home" CODEX_SQLITE_HOME="$SQLHOME" "$CODEX" app-server daemon start > "$BACKUP/$tag-daemon-start.json"
  jq -e . "$BACKUP/$tag-daemon-start.json" >/dev/null
  env CODEX_HOME="$home" CODEX_SQLITE_HOME="$SQLHOME" "$CODEX" app-server daemon version > "$BACKUP/$tag-daemon-version.json"
  jq -e '.status=="running" and .managedCodexVersion==.cliVersion and .appServerVersion==.cliVersion' "$BACKUP/$tag-daemon-version.json" >/dev/null || die "$tag daemon/version mismatch"
}

log "0/12 preflight"
quiescent; preflight
printf 'Codex: %s\nCANON=%s\nALT=%s\nSQLHOME=%s\nBACKUP=%s\n' "$($CODEX --version)" "$CANON" "$ALT" "$SQLHOME" "$BACKUP"
# Enough for source rollout duplication + 512 MiB headroom (DB backups add some overhead).
srcbytes=0
for d in "$ALT/sessions" "$ALT/archived_sessions"; do
  if [[ -d "$d" && ! -L "$d" ]]; then n=$(du -sb "$d" | awk '{print $1}'); srcbytes=$((srcbytes+n)); fi
done
dbbytes=$(python3 - "$CANON" "$ALT" <<'PYSPACE'
from pathlib import Path
import sys
n=0
for root in map(Path,sys.argv[1:]):
  for p in root.rglob('*'):
    try:
      if p.is_file() and ('.sqlite' in p.name or p.name in {'config.toml','session_index.jsonl','.codex-global-state.json'}): n+=p.stat().st_size
    except FileNotFoundError: pass
print(n)
PYSPACE
)
need=$((srcbytes+dbbytes+536870912)); avail=$(df -PB1 "$HOME"|awk 'NR==2{print $4}')
((avail>=need)) || die "insufficient free space (need about $need bytes, have $avail)"

log "1/12 backup old SQLite/config/index state"
backup_state
log "2/12 repeat collision/UUID preflight"
quiescent; preflight | tee "$BACKUP/rollout-preflight.txt"
log "3-4/12 merge+verify active sessions, retain old tree, create symlink"
merge_tree sessions
log "5/12 merge+verify archived sessions, retain old tree, create symlink"
merge_tree archived_sessions
log "6/12 share thread-writer-locks"
quiescent; share_locks
log "7/12 merge session_index.jsonl into two physical copies"
merge_indexes
log "8/12 create fresh shared SQLite home"
mkdir -p "$SQLHOME"; [[ -z "$(find "$SQLHOME" -mindepth 1 -print -quit)" ]] || die "$SQLHOME unexpectedly non-empty"
log "9/12 initialize/backfill shared state DB and verify canonical profile"
quiescent; bootstrap_db
doctor "$CANON" env "$BACKUP/doctor-codex-env.json" "$BACKUP/doctor-codex-env.err"; validate_doctor "$BACKUP/doctor-codex-env.json" canonical
log "10/12 verify alternate profile sees same inventory"
doctor "$ALT" env "$BACKUP/doctor-codex2-env.json" "$BACKUP/doctor-codex2-env.err"; validate_doctor "$BACKUP/doctor-codex2-env.json" alternate
same_inventory "$BACKUP/doctor-codex-env.json" "$BACKUP/doctor-codex2-env.json"
log "11/12 persist sqlite_home in both configs and re-verify without env override"
quiescent; set_sqlhome "$CANON/config.toml"; set_sqlhome "$ALT/config.toml"
doctor "$CANON" config "$BACKUP/doctor-codex-config.json" "$BACKUP/doctor-codex-config.err"; validate_doctor "$BACKUP/doctor-codex-config.json" canonical-config
doctor "$ALT" config "$BACKUP/doctor-codex2-config.json" "$BACKUP/doctor-codex2-config.err"; validate_doctor "$BACKUP/doctor-codex2-config.json" alternate-config
same_inventory "$BACKUP/doctor-codex-env.json" "$BACKUP/doctor-codex-config.json"; same_inventory "$BACKUP/doctor-codex-env.json" "$BACKUP/doctor-codex2-config.json"
log "12/12 clear stale daemon runtime records, align, and start separate app-server daemons"
quiescent
prepare_daemon_runtime "$CANON" codex
prepare_daemon_runtime "$ALT" codex2
start_daemon "$CANON" codex
start_daemon "$ALT" codex2
V1="$BACKUP/codex-daemon-version.json"; V2="$BACKUP/codex2-daemon-version.json"
S1="$(jq -r .socketPath "$V1")"; S2="$(jq -r .socketPath "$V2")"; [[ "$S1" != "$S2" ]] || die "daemon sockets unexpectedly identical"
[[ "$(realpath -m "$ALT/sessions")" == "$(realpath -m "$CANON/sessions")" ]] || die "sessions link invariant failed"
[[ "$(realpath -m "$ALT/archived_sessions")" == "$(realpath -m "$CANON/archived_sessions")" ]] || die "archive link invariant failed"
[[ "$(realpath -m "$ALT/thread-writer-locks")" == "$(realpath -m "$CANON/thread-writer-locks")" ]] || die "writer-lock link invariant failed"
quickcheck "$(cat "$BACKUP/shared-state-db.path")"
trap - ERR
log "SUCCESS"
printf 'shared sessions: %s\nshared SQLite: %s\nbackup/audit: %s\ndaemon A: %s\ndaemon B: %s\n' "$CANON/sessions" "$SQLHOME" "$BACKUP" "$S1" "$S2"
printf 'Old SQLite DBs were not deleted; old .codex2 rollout trees were retained as *.pre-unification-%s.\n' "$STAMP"


