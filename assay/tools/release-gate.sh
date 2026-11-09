#!/usr/bin/env bash
set -euo pipefail

die() {
  local status="${2:-3}"
  printf 'assay release-gate: %s\n' "$1" >&2
  exit "$status"
}

[[ $# -eq 1 ]] || die 'usage: tools/release-gate.sh tester-unified|self-qualification-provisional' 2
lane="$1"
case "$lane" in
  tester-unified)
    local_argv=(bash tools/tester-unified-gate.sh ..)
    ;;
  self-qualification-provisional)
    local_argv=(run-gate self-qualification-provisional)
    ;;
  *)
    die "unsupported release-gate lane: $lane" 2
    ;;
esac

[[ -n ${ASSAY_RELEASE_GATE_HOST:-} ]] \
  || die 'ASSAY_RELEASE_GATE_HOST is unset; set it to local, v1001, or r1002'
host="$ASSAY_RELEASE_GATE_HOST"
case "$host" in
  local)
    exec "${local_argv[@]}"
    ;;
  v1001 | r1002)
    ;;
  *)
    die 'ASSAY_RELEASE_GATE_HOST must be local, v1001, or r1002' 2
    ;;
esac

commit="${CMRU_RELEASE_GATE_COMMIT:-}"
[[ -n "$commit" ]] \
  || die 'CMRU_RELEASE_GATE_COMMIT is unset; run this from the CMRU release gate'
[[ "$commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] \
  || die 'CMRU_RELEASE_GATE_COMMIT must be a full lowercase Git object ID'

worktree="$(git rev-parse --show-toplevel)" \
  || die 'cannot resolve the vbpub worktree root'
head="$(git rev-parse HEAD)" \
  || die 'cannot resolve the release-gate worktree HEAD'
[[ "$head" == "$commit" ]] \
  || die "CMRU_RELEASE_GATE_COMMIT ($commit) does not match worktree HEAD ($head)"

remote_gate="../scripts/netcup/runner/remote-gate.py"
[[ -f "$remote_gate" ]] \
  || die "remote-gate.py is missing at $remote_gate"

[[ -n ${HOME:-} ]] \
  || die 'HOME is unset; remote-gate needs ~/.config/remote-gate/hosts.json and ~/.ssh'

remote_gate_base_env=(
  -u GITHUB_PUSH_PAT
  -u GITHUB_TOKEN
  -u CMRU_GIT_AUTH_TOKEN
  -u GH_TOKEN
  -u GH_ENTERPRISE_TOKEN
  -i
  "PATH=$PATH"
  "HOME=$HOME"
)

if [[ -v REMOTE_GATE_OUT ]]; then
  [[ -n "$REMOTE_GATE_OUT" ]] \
    || die 'REMOTE_GATE_OUT is set but empty'
  remote_out="$REMOTE_GATE_OUT"
else
  remote_out="$(env "${remote_gate_base_env[@]}" python3 - "$remote_gate" <<'PYEOF'
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("assay_release_remote_gate", path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load remote-gate.py to resolve its output directory")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(module.DEFAULT_OUT)
PYEOF
)" || die 'cannot read remote-gate.py output-directory default'
  [[ -n "$remote_out" ]] || die 'remote-gate.py returned an empty output-directory default'
fi

remote_gate_env=(
  "${remote_gate_base_env[@]}"
  "REMOTE_GATE_OUT=$remote_out"
)
if [[ -v REMOTE_GATE_HOSTS ]]; then
  remote_gate_env+=("REMOTE_GATE_HOSTS=$REMOTE_GATE_HOSTS")
fi
if [[ -n ${SSH_AUTH_SOCK:-} ]]; then
  remote_gate_env+=("SSH_AUTH_SOCK=$SSH_AUTH_SOCK")
fi

run_remote_gate() {
  env "${remote_gate_env[@]}" python3 "$remote_gate" "$@"
}

# remote-gate bounds run IDs to 61 characters. Seven base-36 UTC epoch-second
# digits keep the full lane and 12-character commit prefix in that bound.
utc_seconds="$(python3 - <<'PYEOF'
from datetime import datetime, timezone

print(int(datetime.now(timezone.utc).timestamp()))
PYEOF
)" || die 'cannot read the current UTC time'
[[ "$utc_seconds" =~ ^[0-9]+$ ]] || die 'UTC time did not resolve to epoch seconds'
utc="$(python3 - "$utc_seconds" <<'PYEOF'
import sys

value = int(sys.argv[1])
digits = "0123456789abcdefghijklmnopqrstuvwxyz"
result = ""
while value:
    value, remainder = divmod(value, 36)
    result = digits[remainder] + result
result = result or "0"
if len(result) > 7:
    raise SystemExit("UTC epoch timestamp no longer fits remote-gate's run ID limit")
print(result.rjust(7, "0"))
PYEOF
)" || die 'cannot encode the current UTC time for a remote-gate run ID'
run_name="assay-rel-${commit:0:12}-${lane}-${utc}"
run_dir="$remote_out/$run_name"
remote_gate_pid=""
remote_gate_launching=false
pending_signal=""
pending_signal_status=""

handle_remote_signal() {
  local signal_name="$1" exit_status="$2" cancel_status
  if [[ -z "$remote_gate_pid" && "$remote_gate_launching" == true ]]; then
    pending_signal="$signal_name"
    pending_signal_status="$exit_status"
    return 0
  fi

  trap ':' INT TERM
  if [[ -n "$remote_gate_pid" ]]; then
    kill -s "$signal_name" "$remote_gate_pid" 2>/dev/null || true
  fi
  if [[ -f "$run_dir/meta.json" ]]; then
    printf 'assay release-gate: interrupted; attempting to cancel remote run %s\n' \
      "$run_name" >&2
    if run_remote_gate cancel "$run_name"; then
      printf 'assay release-gate: remote cancellation was requested: %s\n' "$run_name" >&2
    else
      cancel_status=$?
      printf 'assay release-gate: cancel attempt for %s failed (exit %s); the host job may remain active\n' \
        "$run_name" "$cancel_status" >&2
    fi
  fi
  if [[ -n "$remote_gate_pid" ]]; then
    wait "$remote_gate_pid" 2>/dev/null || true
  fi
  exit "$exit_status"
}

trap 'handle_remote_signal INT 130' INT
trap 'handle_remote_signal TERM 143' TERM

remote_gate_launching=true
env "${remote_gate_env[@]}" python3 "$remote_gate" run \
  "$host" "$worktree" "$lane" \
  --project-dir assay --repo vbpub --name "$run_name" &
remote_gate_pid=$!
remote_gate_launching=false
if [[ -n "$pending_signal" ]]; then
  handle_remote_signal "$pending_signal" "$pending_signal_status"
fi
if wait "$remote_gate_pid"; then
  remote_status=0
else
  remote_status=$?
fi
remote_gate_pid=""

if receipt="$(python3 - "$run_dir/meta.json" "$run_dir/result.json" <<'PYEOF'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        meta = json.load(stream)
    with open(sys.argv[2], encoding="utf-8") as stream:
        result = json.load(stream)
except (OSError, json.JSONDecodeError) as exc:
    print(f"cannot read remote-gate result files: {exc}", file=sys.stderr)
    raise SystemExit(2)

sha = meta.get("sha")
verdict = result.get("verdict")
exit_code = result.get("exit_code")
if not isinstance(sha, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha) is None:
    print("remote-gate meta.json has no valid full Git object ID", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(verdict, str) or not verdict:
    print("remote-gate result.json has no verdict", file=sys.stderr)
    raise SystemExit(2)
if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
    print("remote-gate result.json has no valid lane exit code", file=sys.stderr)
    raise SystemExit(2)
print(f"{sha}\t{verdict}\t{exit_code}")
PYEOF
)"; then
  IFS=$'\t' read -r meta_sha verdict lane_status <<<"$receipt"
else
  if (( remote_status != 0 )); then
    if [[ -f "$run_dir/meta.json" && ! -f "$run_dir/result.json" ]]; then
      printf 'assay release-gate: remote-gate exited without a result; attempting to cancel unattended remote run %s\n' \
        "$run_name" >&2
      if run_remote_gate cancel "$run_name"; then
        printf 'assay release-gate: remote run cancellation was requested: %s\n' \
          "$run_name" >&2
      else
        cancel_status=$?
        printf 'assay release-gate: cancel attempt for %s failed (exit %s); the host job may remain active\n' \
          "$run_name" "$cancel_status" >&2
      fi
    fi
    printf 'assay release-gate: remote-gate produced no valid result for %s (exit %s)\n' \
      "$run_name" "$remote_status" >&2
    exit "$remote_status"
  fi
  die "remote-gate produced no valid result for $run_name"
fi

[[ "$meta_sha" == "$commit" ]] \
  || die "remote-gate meta.json SHA mismatch: expected $commit, got $meta_sha"
[[ "$remote_status" == "$lane_status" ]] \
  || die "remote-gate exit status $remote_status differs from result.json lane exit code $lane_status"

if [[ "$verdict" == PASS && "$lane_status" == 0 ]]; then
  exit "$lane_status"
fi

printf 'assay release-gate: refusing %s verdict %s (lane exit code %s)\n' \
  "$lane" "$verdict" "$lane_status" >&2
if (( lane_status != 0 )); then
  exit "$lane_status"
fi
exit 3
