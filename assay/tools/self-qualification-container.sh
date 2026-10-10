#!/usr/bin/env bash
# Registered B105 runner. run-gate owns lane admission, budgets, tree checks,
# and artifact collection; this Assay-owned outer command launches the inner
# qualification in the cgroup-visible tester-unified namespace B145 requires.
set -euo pipefail

die() {
  local exit_status=1
  if [[ "${lane:-}" == b110-pilot && "${prior_pilot_evidence_secured:-false}" != true ]]; then
    # The lane maps exit 3 to run-gate ERROR, which is excluded from the
    # per-commit success history. Do not let a host refusal before prior
    # evidence is secured replace the pass needed to archive that evidence.
    exit_status=3
  fi
  printf 'self-qualification-container: %s\n' "$*" >&2
  exit "$exit_status"
}

assay_git() {
  command git \
    -c maintenance.auto=false \
    -c maintenance.autoDetach=false \
    -c gc.autoDetach=false \
    "$@"
}

run_source_host_checker() {
  assay_git -C "$worktree" show "$source_commit:assay/tools/b110_pilot_host_check.py" \
    | python3 - "$@"
}

clear_prior_outputs() {
  local output_lane="$1" marker
  local expected_assay_args=()
  if [[ "$assay_state_present" == true ]]; then
    expected_assay_args=(
      --expected-assay-device "$assay_state_device"
      --expected-assay-inode "$assay_state_inode"
    )
  else
    expected_assay_args=(--expected-assay-absent)
  fi
  if ! marker="$(run_source_host_checker \
    --project-root "$project" \
    --expected-project-device "$project_device" \
    --expected-project-inode "$project_inode" \
    "${expected_assay_args[@]}" \
    --clear-prior-outputs "$output_lane")"; then
    die "cannot safely clear prior $output_lane outputs before launcher admission"
  fi
  local marker_lines=()
  mapfile -t marker_lines <<<"$marker"
  [[ ${#marker_lines[@]} -eq 2 && "${marker_lines[0]}" =~ ^B110_PRIOR_OUTPUTS_CLEARED=[0-9]+$ ]] \
    || die 'host returned malformed stale-output cleanup markers'
  [[ "${marker_lines[1]}" =~ ^B110_ASSAY_STATE_IDENTITY=([0-9]+):([0-9]+)$ ]] \
    || die 'host returned a malformed prepared .assay identity'
  assay_state_device="${BASH_REMATCH[1]}"
  assay_state_inode="${BASH_REMATCH[2]}"
  assay_state_present=true
}

usage() {
  die 'usage: self-qualification-container.sh WORKTREE self-qualification|self-qualification-preflight|b110-pilot|b110-screen|analysis-r2-pilot'
}

[[ $# -eq 2 ]] || usage
worktree="$1"
lane="$2"
case "$lane" in
  self-qualification|self-qualification-preflight|b110-pilot|b110-screen|analysis-r2-pilot) ;;
  *) usage ;;
esac

case "$worktree" in
  /workspaces/vbpub|/workspaces/vbpub/.worktrees/*) ;;
  *) die "worktree $worktree is outside /workspaces/vbpub" ;;
esac
worktree="$(cd -- "$worktree" && pwd -P)" \
  || die 'cannot resolve the selected worktree path'

project="$worktree/assay"
[[ -d "$project" && ! -L "$project" && -f "$project/pyproject.toml" ]] \
  || die "selected worktree has no real assay project directory: $project"
project_identity="$(stat -c '%d:%i' -- "$project")" \
  || die 'cannot capture the Assay project directory identity before container startup'
[[ "$project_identity" =~ ^([0-9]+):([0-9]+)$ ]] \
  || die 'stat returned a malformed Assay project directory identity'
project_device="${BASH_REMATCH[1]}"
project_inode="${BASH_REMATCH[2]}"
[[ "$(assay_git -C "$worktree" rev-parse --show-toplevel)" == "$worktree" ]] \
  || die "selected worktree path does not resolve to its own repository root"
source_commit="$(assay_git -C "$worktree" rev-parse HEAD)" \
  || die "cannot resolve selected worktree HEAD"
source_tree="$(assay_git -C "$worktree" rev-parse 'HEAD^{tree}')" \
  || die "cannot resolve selected worktree tree"

for required_command in realpath flock chmod python3 stat; do
  command -v "$required_command" >/dev/null 2>&1 \
    || die "required host command is unavailable: $required_command"
done
if [[ "$lane" != b110-pilot ]]; then
  for required_command in rm mkdir mv; do
    command -v "$required_command" >/dev/null 2>&1 \
      || die "required host command is unavailable: $required_command"
  done
fi

# The run-gate `resources.shared` flock is scoped to its caller's /tmp. B105
# callers in separate container namespaces can therefore race on one Docker
# daemon. Use the Git common directory, which every linked Assay worktree
# shares through the workspace bind, for a kernel-released cross-caller lock.
git_common_dir="$(assay_git -C "$worktree" rev-parse --path-format=absolute --git-common-dir)" \
  || die 'cannot resolve the selected worktree Git common directory'
git_common_dir="$(realpath -e -- "$git_common_dir")" \
  || die 'cannot resolve the selected worktree Git common directory path'
case "$git_common_dir/" in
  /workspaces/vbpub/*) ;;
  *) die 'the selected worktree Git common directory is outside /workspaces/vbpub' ;;
esac
host_lock_path="$git_common_dir/assay-b105-self-qualification.lock"
[[ ! -L "$host_lock_path" ]] \
  || die 'the B105 host lock path is a symlink; refusing to follow it'
if ! (umask 077; : >>"$host_lock_path"); then
  die 'cannot create or open the B105 host lock in the Git common directory'
fi
chmod 600 "$host_lock_path" \
  || die 'cannot restrict the B105 host lock permissions'
exec {host_lock_fd}>>"$host_lock_path" \
  || die 'cannot hold the B105 host lock'
if ! flock -n "$host_lock_fd"; then
  printf 'ASSAY_GATE_INCONCLUSIVE=another B105 host check holds the shared Git-directory lock\n' >&2
  exit 3
fi

# Acquire the shared lock before checks that can refuse ordinary admission. It
# protects a live screen from verdict cleanup and prevents pilot output cleanup
# from racing another caller. The run-gate artifact paths must not expose a
# previous result as this attempt's when host or Docker admission refuses.
if [[ "$lane" == b110-screen || "$lane" == b110-pilot || "$lane" == analysis-r2-pilot ]]; then
  assay_state_dir="$project/.assay"
  [[ ! -L "$assay_state_dir" ]] \
    || die 'B110 state directory is a symlink; refusing stale-artifact cleanup'
  if [[ -e "$assay_state_dir" && ! -d "$assay_state_dir" ]]; then
    die 'B110 state path exists but is not a directory'
  fi
  assay_state_present=false
  assay_state_device=""
  assay_state_inode=""
  if [[ -d "$assay_state_dir" ]]; then
    assay_state_identity="$(stat -c '%d:%i' -- "$assay_state_dir")" \
      || die 'cannot capture the B110 state directory identity before cleanup'
    [[ "$assay_state_identity" =~ ^([0-9]+):([0-9]+)$ ]] \
      || die 'stat returned a malformed B110 state directory identity'
    assay_state_device="${BASH_REMATCH[1]}"
    assay_state_inode="${BASH_REMATCH[2]}"
    assay_state_present=true
  fi
  if [[ "$lane" == b110-pilot && "$assay_state_present" == true ]]; then
      prior_snapshot="$assay_state_dir/b110-pilot-evidence"
      prior_receipt="$assay_state_dir/b110-pilot-evidence.receipt.json"
      prior_attestation="$assay_state_dir/b110-pilot-evidence.attestation.json"
      prior_pending="$assay_state_dir/b110-pilot-evidence.pending.json"
      prior_stage_found=false
      for prior_stage in "$assay_state_dir"/.b110-pilot-evidence.stage.*; do
        if [[ -e "$prior_stage" || -L "$prior_stage" ]]; then
          prior_stage_found=true
          break
        fi
      done
      if [[ -e "$prior_snapshot" || -L "$prior_snapshot" \
        || -e "$prior_receipt" || -L "$prior_receipt" \
        || -e "$prior_attestation" || -L "$prior_attestation" \
        || -e "$prior_pending" || -L "$prior_pending" \
        || "$prior_stage_found" == true ]]; then
        if prior_archive_marker="$(run_source_host_checker \
          --project-root "$project" \
          --expected-project-device "$project_device" \
          --expected-project-inode "$project_inode" \
          --expected-assay-device "$assay_state_device" \
          --expected-assay-inode "$assay_state_inode" \
          --expected-commit "$source_commit" \
          --expected-tree "$source_tree" \
          --archive-prior-snapshot)"; then
          prior_archive_status=0
        else
          prior_archive_status=$?
        fi
        if [[ "$prior_archive_status" == 3 ]]; then
          printf '%s\n' 'ASSAY_GATE_INCONCLUSIVE=prior B110 pilot evidence is unavailable; preserving it for retry'
          exit 3
        elif [[ "$prior_archive_status" != 0 ]]; then
          die 'host could not archive or quarantine the prior B110 pilot publication'
        fi
        [[ "$prior_archive_marker" =~ ^B110_PILOT_PRIOR_(ARCHIVED=[0-9]+-[0-9a-f]{16}-[0-9a-f]{64}|QUARANTINED=(b110-pilot-evidence-incomplete|b110-pilot-evidence-unverified(-[0-9a-f]{32})?)/[0-9]+-[0-9a-f]{12})$ ]] \
          || die 'host returned a malformed prior B110 pilot disposition marker'
      fi
  fi
  if [[ "$lane" == b110-pilot ]]; then
    prior_pilot_evidence_secured=true
    # Preserve any prior successful snapshot before a source-cleanliness or
    # later host prerequisite refusal can replace its same-commit run-gate
    # history entry. Earlier launcher refusals use exit 3, mapped to ERROR.
    source_status="$(assay_git -C "$worktree" status --porcelain --untracked-files=all)" \
      || die 'cannot read selected worktree status before B105 qualification'
    [[ -z "$source_status" ]] \
      || die "selected worktree is not clean before B105 qualification"
    for required_command in rm mkdir mv; do
      command -v "$required_command" >/dev/null 2>&1 \
        || die "required host command is unavailable: $required_command"
    done
    clear_prior_outputs b110-pilot
  elif [[ "$lane" == b110-screen ]]; then
    clear_prior_outputs b110-screen
  else
    if [[ -e "$assay_state_dir/verdict-analysis-r2.json" \
      || -L "$assay_state_dir/verdict-analysis-r2.json" ]]; then
      printf '%s\n' 'B131 refuses to overwrite a pre-existing analysis-r2 verdict; preserve or archive it first' >&2
      exit 2
    fi
    rm -f -- \
      "$assay_state_dir/analysis-r2-pilot-plan.json" \
      "$assay_state_dir/analysis-r2-pilot-candidates.txt" \
      "$assay_state_dir/analysis-r2-pilot-selection.json" \
      "$assay_state_dir/analysis-r2-pilot-summary.json" \
      "$assay_state_dir/analysis-r2-pilot-run.log" \
      || die 'cannot remove prior B131 pilot outputs before launcher admission'
  fi
fi

if [[ "$lane" != b110-pilot ]]; then
  source_status="$(assay_git -C "$worktree" status --porcelain --untracked-files=all)" \
    || die 'cannot read selected worktree status before B105 qualification'
  [[ -z "$source_status" ]] \
    || die "selected worktree is not clean before B105 qualification"
fi

command -v findmnt >/dev/null 2>&1 \
  || die 'required host command is unavailable: findmnt'
workspace_target="$(findmnt --target "$worktree" --noheadings --output TARGET)" \
  || die 'could not derive the selected worktree mount target'
workspace_fsroot="$(findmnt --target "$worktree" --noheadings --output FSROOT)" \
  || die 'could not derive the selected worktree host FSROOT'
[[ "$workspace_target" == /workspaces/vbpub && "$workspace_fsroot" == /* \
  && "$workspace_fsroot" != / ]] \
  || die 'the selected worktree is not inside the declared /workspaces/vbpub bind'
case "$worktree/" in
  "$workspace_target"/*) ;;
  *) die 'the selected worktree is outside the derived workspace mount target' ;;
esac
host_workspace_root="${ASSAY_GATE_HOST_WORKSPACE_ROOT:-$workspace_fsroot}"
[[ "$host_workspace_root" == "$workspace_fsroot" ]] \
  || die 'ASSAY_GATE_HOST_WORKSPACE_ROOT differs from the findmnt workspace bind source'
[[ "$host_workspace_root" == /* && "$host_workspace_root" != "/" \
  && "$host_workspace_root" != *$'\n'* && "$host_workspace_root" != *,* ]] \
  || die 'the host workspace bind source is not a usable Docker mount source'

if ! command -v docker >/dev/null 2>&1; then
  die 'Docker is required to launch the cgroup-visible tester-unified runner'
fi
for required_command in timeout setsid od tr paste rg grep sort sed; do
  command -v "$required_command" >/dev/null 2>&1 \
    || die "required host command is unavailable: $required_command"
done

# Bound every Docker client call, not only the long-running container wait.
# An unhealthy daemon must not pin the wrapper past its admission budget.
docker_bounded() {
  timeout --signal=TERM --kill-after=5s 30s docker "$@"
}

case "${ASSAY_GATE_ALLOW_SHARED_HOST:-}" in
  '') ;;
  1)
    printf 'ASSAY_GATE_INCONCLUSIVE=Assay qualification requires an exclusive gates host; ASSAY_GATE_ALLOW_SHARED_HOST=1 is unsupported\n' >&2
    exit 3
    ;;
  *) die 'ASSAY_GATE_ALLOW_SHARED_HOST must be unset or empty for B105 qualification' ;;
esac

docker_bounded image inspect tester-unified:local >/dev/null 2>&1 \
  || die 'tester-unified:local is unavailable; build the declared tester image first'

b105_memory_limit=2g
b105_memory_reserve_bytes=2147483648
cgroup_parent="$("$project/tools/cgroup-parent.sh" \
  --memory-reserve-bytes "$b105_memory_reserve_bytes")" \
  || die 'the host gates cgroup tier or its 2 GiB RAM headroom is absent or unverified'
[[ -n "$cgroup_parent" ]] || die 'the verified gates cgroup tier is empty'

# This is an admission snapshot, not a host-wide lock: the repository lock
# excludes competing B105 callers, and the serial gate coordinator must keep
# other projects' registered gates out for the duration. The scan catches a
# gate already running; a cross-project gate launched after the scan remains an
# unresolved evidence risk and invalidates this qualification attempt.
if ! active_names="$(docker_bounded ps --no-trunc --format '{{.Names}}')"; then
  printf 'ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun\n' >&2
  exit 3
fi
active_gates="$(printf '%s\n' "$active_names" | rg '^run-gate-' || true)"
if [[ -n "$active_gates" ]]; then
  printf 'ASSAY_GATE_INCONCLUSIVE=host busy — rerun: %s\n' \
    "$(printf '%s' "$active_gates" | paste -sd, -)" >&2
  exit 3
fi

scratch="$(mktemp -d "${TMPDIR:-/tmp}/assay-b105-container.XXXXXXXX")" \
  || die 'could not create private container ownership scratch'
container_id=""
container_name=""
ownership_token=""
ownership_file=""
log_pid=""
wait_pid=""
launch_pid=""
launch_attempted=0
preserve_scratch=0

reconcile_owned_id() {
  local cidfile_id="" name_id identity
  if [[ -s "$ownership_file" ]]; then
    cidfile_id="$(<"$ownership_file")"
    [[ "$cidfile_id" =~ ^[0-9a-f]{64}$ ]] || return 1
  fi
  if [[ -n "$container_id" ]]; then
    [[ "$container_id" =~ ^[0-9a-f]{64}$ ]] || return 1
  fi
  name_id="$(docker_bounded inspect "$container_name" --format '{{.Id}}' 2>/dev/null)" \
    || return 1
  [[ "$name_id" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -z "$cidfile_id" || "$cidfile_id" == "$name_id" ]] || return 1
  [[ -z "$container_id" || "$container_id" == "$name_id" ]] || return 1
  identity="$(docker_bounded inspect "$name_id" \
    --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "assay.b105-gate.owner"}}' \
    2>/dev/null)" || return 1
  [[ "$identity" == "$name_id|/$container_name|$ownership_token" ]] || return 1
  container_id="$name_id"
}

stop_process_group() {
  local pid="$1"
  [[ -n "$pid" ]] || return 0
  kill -TERM -- "-$pid" >/dev/null 2>&1 || true
  # The timeout client and its Docker child share this private session. A
  # second signal prevents a client ignoring TERM from outliving cleanup.
  kill -KILL -- "-$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  local result=$? cleanup_failed=0 running stop_status
  trap - EXIT INT TERM

  stop_process_group "$launch_pid"
  stop_process_group "$log_pid"
  stop_process_group "$wait_pid"

  if ((launch_attempted)); then
    if reconcile_owned_id; then
      running="$(docker_bounded inspect "$container_id" --format '{{.State.Running}}' 2>/dev/null)" \
        || running=unknown
      if [[ "$running" == true ]]; then
        if docker_bounded stop -t 10 "$container_id" >/dev/null 2>&1; then
          stop_status=0
        else
          stop_status=$?
          printf 'self-qualification-container: graceful stop failed (exit %s); trying bounded force removal\n' \
            "$stop_status" >&2
        fi
        running="$(docker_bounded inspect "$container_id" --format '{{.State.Running}}' 2>/dev/null)" \
          || running=unknown
      fi
      if [[ "$running" == false ]]; then
        if ! docker_bounded rm "$container_id" >/dev/null 2>&1; then
          docker_bounded rm -f "$container_id" >/dev/null 2>&1 \
            || cleanup_failed=1
        fi
      else
        # The launch ID and ownership token were reconciled above. If the
        # daemon still reports a running/unknown state, force-remove only that
        # verified ID so a wait timeout cannot leave the B105 child running.
        docker_bounded rm -f "$container_id" >/dev/null 2>&1 \
          || cleanup_failed=1
      fi
    else
      printf 'self-qualification-container: launch ownership cannot be reconciled by full ID, name, and token; refusing removal\n' >&2
      cleanup_failed=1
    fi
  fi

  if ((cleanup_failed)); then preserve_scratch=1; fi
  if ((preserve_scratch)); then
    printf 'self-qualification-container: preserving ownership evidence at %s\n' "$scratch" >&2
  else
    rm -rf -- "$scratch" || cleanup_failed=1
  fi
  if ((cleanup_failed && result == 0)); then result=1; fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ownership_token="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')" \
  || die 'could not create a container ownership token'
[[ "$ownership_token" =~ ^[0-9a-f]{64}$ ]] || die 'container ownership token is malformed'
container_name="run-gate-assay-${lane}-${BASHPID}-${RANDOM}-$(date +%s)"
ownership_file="$scratch/container.cid"
printf 'worktree=%s\ncommit=%s\ntree=%s\nworkspace_bind_source=%s\ncontainer_name=%s\nownership_token=%s\n' \
  "$worktree" "$source_commit" "$source_tree" "$host_workspace_root" \
  "$container_name" "$ownership_token" >"$scratch/ownership.txt"
forwarded_env=()
if [[ -n "${CGROUP_PARENT_DEV_BACKGROUND:-}" ]]; then
  forwarded_env+=(-e "CGROUP_PARENT_DEV_BACKGROUND=$CGROUP_PARENT_DEV_BACKGROUND")
fi
b110_state_env=()
if [[ "$lane" == b110-pilot || "$lane" == b110-screen ]]; then
  [[ "$assay_state_present" == true ]] || die 'B110 did not prepare its .assay state directory'
  b110_state_env+=(
    -e "ASSAY_B110_ASSAY_STATE_DEVICE=$assay_state_device"
    -e "ASSAY_B110_ASSAY_STATE_INODE=$assay_state_inode"
  )
fi

mount_args=(--mount "type=bind,src=$host_workspace_root,dst=$host_workspace_root")
if [[ "$host_workspace_root" != "/workspaces/vbpub" ]]; then
  mount_args+=(--mount "type=bind,src=$host_workspace_root,dst=/workspaces/vbpub")
fi

case "$lane" in
  self-qualification)
    wait_timeout_label=7h40m
    wait_timeout_seconds=27600
    inner_argv=(
      timeout --verbose --signal=TERM --kill-after=30s 27000s
      bash "$project/tools/self-qualification-gate.sh" "$worktree" "$lane"
    )
    ;;
  self-qualification-preflight)
    wait_timeout_label=65m
    wait_timeout_seconds=3900
    inner_argv=(bash "$project/tools/self-qualification-gate.sh" "$worktree" "$lane")
    ;;
  b110-pilot)
    # The build and venv setup happen before the 90-minute campaign-attempt
    # cap. The inner cap covers campaign init, plan, selection, Assay, report
    # checking and final markers; docker wait is the larger cleanup failsafe.
    wait_timeout_label=2h15m
    wait_timeout_seconds=8100
    inner_argv=(bash "$project/tools/self-qualification-gate.sh" "$worktree" "$lane")
    ;;
  b110-screen)
    wait_timeout_label=7h15m
    wait_timeout_seconds=26100
    inner_argv=(bash "$project/tools/self-qualification-gate.sh" "$worktree" "$lane")
    ;;
  analysis-r2-pilot)
    wait_timeout_label=2h15m
    wait_timeout_seconds=8100
    inner_argv=(bash "$project/tools/self-qualification-gate.sh" "$worktree" "$lane")
    ;;
esac

if [[ "$lane" == analysis-r2-pilot ]]; then
  printf 'ASSAY_ANALYSIS_R2_PILOT_GATE_WAIT_TIMEOUT=%s\n' "$wait_timeout_label"
  printf 'ASSAY_ANALYSIS_R2_PILOT_GATE_CONTAINER=%s\n' "$container_name"
else
  printf 'ASSAY_B105_GATE_WAIT_TIMEOUT=%s\n' "$wait_timeout_label"
  printf 'ASSAY_B105_GATE_CONTAINER=%s\n' "$container_name"
fi
launch_attempted=1
launch_output="$scratch/docker.run.stdout"
launch_error="$scratch/docker.run.stderr"
setsid timeout --signal=TERM --kill-after=5s 60s docker run -d \
  --cidfile "$ownership_file" \
  --label "assay.b105-gate.owner=$ownership_token" \
  --name "$container_name" \
  --init \
  --cgroupns=host \
  --cgroup-parent="$cgroup_parent" \
  --cpus=3 \
  --memory="$b105_memory_limit" \
  --memory-swap=8g \
  --network=none \
  -e "CGROUP_PARENT_DEV_GATES=$cgroup_parent" \
  -e "ASSAY_B105_GATE_EXPECTED_COMMIT=$source_commit" \
  -e "ASSAY_B105_GATE_EXPECTED_TREE=$source_tree" \
  "${b110_state_env[@]}" \
  "${forwarded_env[@]}" \
  "${mount_args[@]}" \
  -w "$worktree" \
  tester-unified:local \
  bash -lc 'git config --global --replace-all safe.directory "*" && exec "$@"' \
  assay-b105-inner "${inner_argv[@]}" >"$launch_output" 2>"$launch_error" &
launch_pid=$!
if wait "$launch_pid"; then
  launch_status=0
else
  launch_status=$?
fi
launch_pid=""
if [[ -s "$launch_output" ]]; then
  launch_stdout="$(<"$launch_output")"
  if [[ "$launch_stdout" =~ ^[0-9a-f]{64}$ ]]; then
    container_id="$launch_stdout"
  fi
fi
if ((launch_status != 0)); then
  if [[ -s "$launch_error" ]]; then cat "$launch_error" >&2; fi
  printf 'self-qualification-container: docker run failed (exit %s)\n' "$launch_status" >&2
  exit "$launch_status"
fi

if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ || ! -f "$ownership_file" ]]; then
  die 'Docker launch did not produce a valid full ID and cidfile'
fi
cidfile_id="$(<"$ownership_file")"
if [[ "$cidfile_id" != "$container_id" ]]; then
  die 'Docker returned an ID that does not match the cidfile'
fi

ownership_identity="$(docker_bounded inspect "$container_id" \
  --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "assay.b105-gate.owner"}}')" \
  || die 'could not verify B105 container ownership'
if [[ "$ownership_identity" != "$container_id|/$container_name|$ownership_token" ]]; then
  die 'B105 container ID, name, or ownership token does not match the launch record'
fi

identity="$(docker_bounded inspect "$container_id" \
  --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "assay.b105-gate.owner"}}|{{.Config.User}}|{{.HostConfig.CgroupParent}}|{{.HostConfig.CgroupnsMode}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.HostConfig.MemorySwap}}|{{.HostConfig.NetworkMode}}')" \
  || die 'could not inspect the launched B105 container'
expected_identity="$container_id|/$container_name|$ownership_token|1003|$cgroup_parent|host|3000000000|2147483648|8589934592|none"
if [[ "$identity" != "$expected_identity" ]]; then
  die "B105 container launch verification mismatch: $identity"
fi

mounts="$(docker_bounded inspect "$container_id" \
  --format '{{range .Mounts}}{{printf "%s\t%s\n" .Source .Destination}}{{end}}')" \
  || die 'could not inspect B105 container mounts'
expected_mounts="$host_workspace_root"$'\t'"$host_workspace_root"
if [[ "$host_workspace_root" != "/workspaces/vbpub" ]]; then
  expected_mounts+=$'\n'"$host_workspace_root"$'\t'"/workspaces/vbpub"
fi
if [[ "$(printf '%s\n' "$mounts" | sed '/^$/d' | sort)" != \
      "$(printf '%s\n' "$expected_mounts" | sort)" ]] \
  || printf '%s\n' "$mounts" | grep -Fq '/var/run/docker.sock'; then
  die 'B105 container mounts do not match the dual-mount, no-Docker-socket contract'
fi

container_env="$(docker_bounded inspect "$container_id" \
  --format '{{range .Config.Env}}{{println .}}{{end}}')" \
  || die 'could not inspect B105 container environment'
for expected in \
  "CGROUP_PARENT_DEV_GATES=$cgroup_parent" \
  "ASSAY_B105_GATE_EXPECTED_COMMIT=$source_commit" \
  "ASSAY_B105_GATE_EXPECTED_TREE=$source_tree"; do
  if ! printf '%s\n' "$container_env" | grep -Fqx "$expected"; then
    die "B105 container is missing required environment fact: ${expected%%=*}"
  fi
done
if [[ "$lane" == b110-pilot || "$lane" == b110-screen ]]; then
  for expected in \
    "ASSAY_B110_ASSAY_STATE_DEVICE=$assay_state_device" \
    "ASSAY_B110_ASSAY_STATE_INODE=$assay_state_inode"; do
    if ! printf '%s\n' "$container_env" | grep -Fqx "$expected"; then
      die "B110 container is missing its admitted .assay identity: ${expected%%=*}"
    fi
  done
fi
if [[ -n "${CGROUP_PARENT_DEV_BACKGROUND:-}" ]] \
  && ! printf '%s\n' "$container_env" | grep -Fqx \
    "CGROUP_PARENT_DEV_BACKGROUND=$CGROUP_PARENT_DEV_BACKGROUND"; then
  die 'B105 container is missing the declared background cgroup tier'
fi
log_timeout_seconds=$((wait_timeout_seconds + 90))
setsid timeout --signal=TERM --kill-after=5s "${log_timeout_seconds}s" \
  docker logs --follow "$container_id" &
log_pid=$!
wait_output="$scratch/docker.wait"
wait_error="$scratch/docker.wait.stderr"
setsid timeout --signal=TERM --kill-after=30s "${wait_timeout_seconds}s" \
  docker wait "$container_id" >"$wait_output" 2>"$wait_error" &
wait_pid=$!
if wait "$wait_pid"; then
  wait_rc=0
else
  wait_rc=$?
fi
wait_pid=""
if ((wait_rc != 0)); then
  if [[ -s "$wait_error" ]]; then cat "$wait_error" >&2; fi
  die "docker wait failed or exceeded $wait_timeout_label (exit $wait_rc)"
fi
[[ -f "$wait_output" ]] || die 'docker wait exited without a status artifact'
wait_status="$(<"$wait_output")"
[[ "$wait_status" =~ ^[0-9]+$ ]] || die "docker wait returned a non-decimal status: $wait_status"

log_follower_was_running=0
if kill -0 "$log_pid" >/dev/null 2>&1; then
  log_follower_was_running=1
  stop_process_group "$log_pid"
  logs_status=0
else
  if wait "$log_pid"; then
    logs_status=0
  else
    logs_status=$?
  fi
fi
log_pid=""
if ((log_follower_was_running == 0 && logs_status != 0)); then
  die "B105 container log stream ended early (exit $logs_status)"
fi

docker_bounded logs "$container_id" >"$scratch/container.log" 2>&1 \
  || die 'could not collect the completed B105 container log'
[[ "$wait_status" == 0 ]] || die "Assay qualification container exited with status $wait_status"
case "$lane" in
  self-qualification)
    marker=ASSAY_SELF_QUALIFICATION_VERIFIED=1
    ;;
  self-qualification-preflight)
    marker=ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1
    ;;
  b110-pilot)
    if grep -Eq '^B110_SCREEN_(EXIT|VERIFIED|TIMEOUT_FAILSAFE|VERDICT)=' "$scratch/container.log"; then
      die 'B110 pilot log contains a screen-mode marker'
    fi
    if grep -Fxq 'B110_PILOT_INIT_REFUSED=1' "$scratch/container.log"; then
      die 'B110 pilot campaign initialization was refused'
    fi
    if grep -Fxq 'B110_PILOT_TIMEOUT_FAILSAFE=1' "$scratch/container.log"; then
      die 'B110 pilot exceeded its campaign failsafe'
    fi
    mapfile -t pilot_exit_markers < <(grep -E '^B110_PILOT_EXIT=(0|[1-9][0-9]{0,2})$' "$scratch/container.log" || true)
    [[ ${#pilot_exit_markers[@]} -eq 1 && "${pilot_exit_markers[0]}" == 'B110_PILOT_EXIT=6' ]] \
      || die 'B110 pilot did not report exactly one complete exit status (6)'
    mapfile -t pilot_completion_markers < <(grep -Fx 'B110_PILOT_COMPLETED=1' "$scratch/container.log" || true)
    [[ ${#pilot_completion_markers[@]} -eq 1 ]] \
      || die 'B110 pilot exited 6 without its completion marker'
    mapfile -t pilot_verified_markers < <(grep -Fx 'B110_PILOT_VERIFIED=1' "$scratch/container.log" || true)
    [[ ${#pilot_verified_markers[@]} -eq 1 ]] \
      || die 'B110 pilot has no independently verified artifact marker'
    mapfile -t pilot_attestation_markers < <(grep -E '^B110_PILOT_ATTESTATION_SHA256=[0-9a-f]{64}$' "$scratch/container.log" || true)
    [[ ${#pilot_attestation_markers[@]} -eq 1 ]] \
      || die 'B110 pilot did not report exactly one attestation digest'
    pilot_attestation_sha256="${pilot_attestation_markers[0]#B110_PILOT_ATTESTATION_SHA256=}"
    marker=B110_PILOT_COMPLETED=1
    ;;
  b110-screen)
    if grep -Eq '^B110_PILOT_(EXIT|COMPLETED|VERIFIED|ATTESTATION_SHA256|TIMEOUT_FAILSAFE|INIT_REFUSED|SUMMARY)=' "$scratch/container.log"; then
      die 'B110 screen log contains a pilot-mode marker'
    fi
    if grep -Fxq 'B110_SCREEN_TIMEOUT_FAILSAFE=1' "$scratch/container.log"; then
      die 'B110 screen exceeded its failsafe timeout'
    fi
    mapfile -t screen_exit_markers < <(grep -E '^B110_SCREEN_EXIT=(0|[1-9][0-9]{0,2})$' "$scratch/container.log" || true)
    [[ ${#screen_exit_markers[@]} -eq 1 ]] \
      || die 'B110 screen did not report exactly one run exit status'
    screen_status="${screen_exit_markers[0]#B110_SCREEN_EXIT=}"
    ((10#$screen_status < 124)) || die 'B110 screen run status is a timeout failsafe'
    mapfile -t screen_completion_markers < <(grep -Fx 'B110_SCREEN_VERIFIED=1' "$scratch/container.log" || true)
    [[ ${#screen_completion_markers[@]} -eq 1 ]] \
      || die 'B110 screen has no verified complete verdict marker'
    marker=B110_SCREEN_VERIFIED=1
    ;;
  analysis-r2-pilot)
    if grep -Eq '^B110_(SCREEN|PILOT)_' "$scratch/container.log"; then
      die 'B131 pilot log contains a B110-mode marker'
    fi
    if grep -Fxq 'ANALYSIS_R2_PILOT_INIT_REFUSED=1' "$scratch/container.log"; then
      die 'B131 pilot campaign initialization was refused'
    fi
    if grep -Fxq 'ANALYSIS_R2_PILOT_TIMEOUT_FAILSAFE=1' "$scratch/container.log"; then
      die 'B131 pilot exceeded its campaign failsafe'
    fi
    mapfile -t analysis_pilot_exit_markers < <(grep -E '^ANALYSIS_R2_PILOT_EXIT=(0|[1-9][0-9]{0,2})$' "$scratch/container.log" || true)
    [[ ${#analysis_pilot_exit_markers[@]} -eq 1 && "${analysis_pilot_exit_markers[0]}" == 'ANALYSIS_R2_PILOT_EXIT=6' ]] \
      || die 'B131 pilot did not report exactly one complete exit status (6)'
    if grep -Fxq 'ANALYSIS_R2_PILOT_VERIFIED=1' "$scratch/container.log"; then
      die 'B131 pilot container log contains the outer completion marker'
    fi
    mapfile -t analysis_pilot_checker_markers < <(grep -Fx 'ANALYSIS_R2_PILOT_CHECKER_VERIFIED=1' "$scratch/container.log" || true)
    [[ ${#analysis_pilot_checker_markers[@]} -eq 1 ]] \
      || die 'B131 pilot exit 6 lacks its source-bound evidence verification marker'
    marker=ANALYSIS_R2_PILOT_VERIFIED=1
    ;;
esac
container_marker="$marker"
if [[ "$lane" == analysis-r2-pilot ]]; then
  container_marker=ANALYSIS_R2_PILOT_CHECKER_VERIFIED=1
fi
grep -Fxq "$container_marker" "$scratch/container.log" \
  || die "Assay qualification container exited zero without $container_marker"

[[ "$(assay_git -C "$worktree" rev-parse HEAD)" == "$source_commit" ]] \
  || die 'selected worktree HEAD changed during B105 qualification'
[[ "$(assay_git -C "$worktree" rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
  || die 'selected worktree tree changed during B105 qualification'
final_status="$(assay_git -C "$worktree" status --porcelain --untracked-files=all)" \
  || die 'cannot read selected worktree status after B105 qualification'
[[ -z "$final_status" ]] \
  || die 'selected worktree became dirty during B105 qualification'

if [[ "$lane" == b110-pilot ]]; then
  if ! pilot_snapshot_receipt="$(run_source_host_checker \
    --project-root "$project" \
    --expected-project-device "$project_device" \
    --expected-project-inode "$project_inode" \
    --expected-assay-device "$assay_state_device" \
    --expected-assay-inode "$assay_state_inode" \
    --campaign "b110-pilot-${source_commit:0:12}" \
    --commit "$source_commit" \
    --tree "$source_tree" \
    --manifest-sha256 "$pilot_attestation_sha256" \
    --container-exit "$wait_status")"; then
    die 'host rejected or could not publish the B110 pilot evidence snapshot'
  fi
  [[ "$pilot_snapshot_receipt" =~ ^B110_PILOT_SNAPSHOT_SHA256=([0-9a-f]{64})$ ]] \
    || die 'host returned a malformed B110 pilot evidence snapshot receipt'
  pilot_snapshot_sha256="${BASH_REMATCH[1]}"

  # All marker lines are provisional until the command exits zero. The final
  # source-bound host check runs after these writes so an expiry or snapshot
  # change during marker output turns the registered lane into failure.
  printf 'B110_PILOT_EXIT=6\n'
  printf 'B110_PILOT_VERIFIED=1\n'
  printf 'B110_PILOT_ATTESTATION_SHA256=%s\n' "$pilot_attestation_sha256"
  printf 'B110_PILOT_SNAPSHOT_SHA256=%s\n' "$pilot_snapshot_sha256"
  printf 'B110_PILOT_COMPLETED=1\n'
  printf 'ASSAY_B110_GATE_CONTAINER_EXIT=%s\n' "$wait_status"
  printf 'ASSAY_B110_GATE_COMPLETE=%s\n' "$lane"

  [[ "$(assay_git -C "$worktree" rev-parse HEAD)" == "$source_commit" ]] \
    || die 'selected worktree HEAD changed during B110 snapshot publication'
  [[ "$(assay_git -C "$worktree" rev-parse 'HEAD^{tree}')" == "$source_tree" ]] \
    || die 'selected worktree tree changed during B110 snapshot publication'
  final_status="$(assay_git -C "$worktree" status --porcelain --untracked-files=all)" \
    || die 'cannot read selected worktree status after B110 snapshot publication'
  [[ -z "$final_status" ]] \
    || die 'selected worktree became dirty during B110 snapshot publication'
  run_source_host_checker \
    --project-root "$project" \
    --expected-project-device "$project_device" \
    --expected-project-inode "$project_inode" \
    --expected-assay-device "$assay_state_device" \
    --expected-assay-inode "$assay_state_inode" \
    --campaign "b110-pilot-${source_commit:0:12}" \
    --commit "$source_commit" \
    --tree "$source_tree" \
    --manifest-sha256 "$pilot_attestation_sha256" \
    --verify-published-snapshot \
    --snapshot-sha256 "$pilot_snapshot_sha256" \
    || die 'final host verification rejected the B110 pilot snapshot or an expired deadline'
  exit 0
fi

case "$lane" in
  b110-pilot|b110-screen)
    printf '%s\n' "$marker"
    printf 'ASSAY_B110_GATE_CONTAINER_EXIT=%s\n' "$wait_status"
    printf 'ASSAY_B110_GATE_COMPLETE=%s\n' "$lane"
    ;;
  analysis-r2-pilot)
    printf 'ASSAY_ANALYSIS_R2_PILOT_GATE_CONTAINER_EXIT=%s\n' "$wait_status"
    printf '%s\n' "$marker"
    printf 'ASSAY_ANALYSIS_R2_PILOT_GATE_COMPLETE=%s\n' "$lane"
    ;;
  *)
    printf '%s\n' "$marker"
    printf 'ASSAY_B105_GATE_CONTAINER_EXIT=%s\n' "$wait_status"
    printf 'ASSAY_B105_GATE_COMPLETE=%s\n' "$lane"
    ;;
esac
