#!/usr/bin/env bash
# Bounded worker pool for scripts/controller mutation checks.
#
# The lane inherits a three-CPU tester-unified limit. Keep the default and
# explicit width within that bound; a missing memory cap is not a reason to
# invent a larger worker count.

MUTATION_RUNNER_MAX_JOBS=3

mutation_runner_configure_width() {
  local value detected

  if [[ ${CONTROLLER_MUTATION_JOBS+x} ]]; then
    value=$CONTROLLER_MUTATION_JOBS
    if [[ ! $value =~ ^[0-9]+$ ]]; then
      return 1
    fi
    while [[ ${#value} -gt 1 && ${value:0:1} == 0 ]]; do value=${value#0}; done
    [[ $value != 0 ]] || return 1
    # A normalized multi-digit positive value is above this runner's cap.
    [[ ${#value} == 1 ]] || return 1
    (( value <= MUTATION_RUNNER_MAX_JOBS )) || return 1
    MUTATION_RUNNER_JOBS=$value
    return 0
  fi

  detected=$(nproc 2>/dev/null) || return 1
  [[ $detected =~ ^[0-9]+$ ]] || return 1
  while [[ ${#detected} -gt 1 && ${detected:0:1} == 0 ]]; do detected=${detected#0}; done
  [[ $detected != 0 ]] || return 1
  if [[ ${#detected} -gt 1 ]] || (( detected > MUTATION_RUNNER_MAX_JOBS )); then
    MUTATION_RUNNER_JOBS=$MUTATION_RUNNER_MAX_JOBS
  else
    MUTATION_RUNNER_JOBS=$detected
  fi
}

mutation_runner_init() {
  local root=${1-}
  if [[ -z $root ]]; then
    printf 'ERROR: mutation runner needs a result root\n' >&2
    return 2
  fi
  MUTATION_RUNNER_JOBS=0
  if ! mutation_runner_configure_width; then
    if [[ ${CONTROLLER_MUTATION_JOBS+x} ]]; then
      printf 'ERROR: CONTROLLER_MUTATION_JOBS must be a positive integer from 1 to %s\n' \
        "$MUTATION_RUNNER_MAX_JOBS" >&2
    else
      printf 'ERROR: nproc did not return a positive CPU count; set CONTROLLER_MUTATION_JOBS to 1..%s\n' \
        "$MUTATION_RUNNER_MAX_JOBS" >&2
    fi
    return 2
  fi

  MUTATION_RUNNER_ROOT=$root
  MUTATION_RUNNER_RESULTS=$root/mutation-results
  MUTATION_RUNNER_REGISTERED=$root/mutation-registered
  MUTATION_RUNNER_STARTED=$root/mutation-started
  MUTATION_RUNNER_STOP=$root/mutation-survivor-seen
  if ! mkdir -p -- "$MUTATION_RUNNER_RESULTS" "$MUTATION_RUNNER_REGISTERED" \
    "$MUTATION_RUNNER_STARTED"; then
    printf 'ERROR: cannot create mutation runner state under %s\n' "$root" >&2
    return 1
  fi
  if [[ ${MUTATION_RUNNER_LOCK_FD+x} ]]; then
    exec {MUTATION_RUNNER_LOCK_FD}>&-
    unset MUTATION_RUNNER_LOCK_FD
  fi
  MUTATION_RUNNER_LOCK_FILE=$root/mutation-runner.lock
  if ! : > "$MUTATION_RUNNER_LOCK_FILE" || ! exec {MUTATION_RUNNER_LOCK_FD}>"$MUTATION_RUNNER_LOCK_FILE"; then
    printf 'ERROR: cannot create mutation runner admission lock under %s\n' "$root" >&2
    return 1
  fi

  MUTATION_RUNNER_PIDS=()
  MUTATION_RUNNER_PID_LABELS=()
  MUTATION_RUNNER_PID_GROUPS=()
  MUTATION_RUNNER_FAILURE_LABELS=()
  MUTATION_RUNNER_FAILURE_REASONS=()
  MUTATION_RUNNER_REGISTERED_COUNT=0
  MUTATION_RUNNER_STARTED_COUNT=0
  MUTATION_RUNNER_REPORTED_COUNT=0
  MUTATION_RUNNER_KILLED_COUNT=0
  MUTATION_RUNNER_REAPED_COUNT=0
  MUTATION_RUNNER_FAILURE_COUNT=0
  MUTATION_RUNNER_SURVIVOR_OBSERVED=0
  MUTATION_RUNNER_STARTED_SECONDS=$SECONDS
}

mutation_runner_fail() {
  local label=${1-<runner>} reason=${2-unknown failure}
  MUTATION_RUNNER_FAILURE_LABELS+=("$label")
  MUTATION_RUNNER_FAILURE_REASONS+=("$reason")
  MUTATION_RUNNER_FAILURE_COUNT=$((MUTATION_RUNNER_FAILURE_COUNT + 1))
}

mutation_runner_observe_survivor() {
  if [[ -e $MUTATION_RUNNER_STOP ]]; then
    MUTATION_RUNNER_SURVIVOR_OBSERVED=1
  fi
}

mutation_runner_group_for_command() {
  local argument basename
  MUTATION_RUNNER_COMMAND_GROUP=
  MUTATION_RUNNER_COMMAND_EXCLUSIVE=0
  for argument in "$@"; do
    basename=${argument##*/}
    case $basename in
      # Short wall-clock checks are isolated from all other pool work.
      test-ctl-status.sh|test-ctx-hooks.sh|test-pi-review.sh|test_remote_gate_cache.sh)
        MUTATION_RUNNER_COMMAND_GROUP=exclusive:$basename
        MUTATION_RUNNER_COMMAND_EXCLUSIVE=1
        return 0
        ;;
      # Longer event polls are serialized by suite; each still has the worker
      # pool available for checks from independent test files.
      test-wrappers.sh|test-pi-impl.sh|test-remote-gate.sh|test_dev.py)
        MUTATION_RUNNER_COMMAND_GROUP=$basename
        return 0
        ;;
    esac
  done
  return 0
}

mutation_runner_group_active() {
  local wanted=$1 index
  for (( index = 0; index < ${#MUTATION_RUNNER_PID_GROUPS[@]}; index++ )); do
    [[ ${MUTATION_RUNNER_PID_GROUPS[index]} == "$wanted" ]] && return 0
  done
  return 1
}

mutation_runner_exclusive_active() {
  local index
  for (( index = 0; index < ${#MUTATION_RUNNER_PID_GROUPS[@]}; index++ )); do
    [[ ${MUTATION_RUNNER_PID_GROUPS[index]} == exclusive:* ]] && return 0
  done
  return 1
}

mutation_runner_unlock_parent() {
  flock -u "$MUTATION_RUNNER_LOCK_FD"
}

mutation_runner_command_available() {
  local command_name=${1-} lookup_path=${PATH-} path_overridden=0
  [[ -n $command_name ]] || return 1

  if [[ ${command_name##*/} == env ]]; then
    shift
    while (( $# > 0 )); do
      case $1 in
        --)
          shift
          break
          ;;
        -i|-0|--ignore-environment|--null)
          shift
          ;;
        -u|--unset|-C|--chdir|--argv0)
          (( $# >= 2 )) || return 1
          shift 2
          ;;
        --unset=*|--chdir=*|--argv0=*)
          shift
          ;;
        *=*)
          if [[ $1 == PATH=* ]]; then
            lookup_path=${1#PATH=}
            path_overridden=1
          fi
          shift
          ;;
        -*)
          return 1
          ;;
        *)
          break
          ;;
      esac
    done
    command_name=${1-}
  fi

  [[ -n $command_name ]] || return 1
  if (( path_overridden )); then
    PATH=$lookup_path command -v "$command_name" >/dev/null 2>&1
  else
    command -v "$command_name" >/dev/null 2>&1
  fi
}

mutation_runner_worker() {
  local label=$1 log=$2 status outcome result_tmp stop_rc=0 log_fd
  shift 2

  # The test command must not inherit the runner's admission descriptor.
  exec {MUTATION_RUNNER_LOCK_FD}>&-
  if ! exec {log_fd}>"$log"; then
    printf 'WORKER ERROR: could not open log for %s\n' "$label" >&2
    return 69
  fi

  # Resolve command startup separately: an executed command's nonzero status,
  # including 126 or 127, keeps the original KILLED meaning.
  if ! mutation_runner_command_available "$@"; then
    status=127
    outcome=SPAWN_ERROR
    printf 'WORKER ERROR: test command could not be started: %s\n' "$1" >&"$log_fd"
  elif "$@" >&"$log_fd" 2>&1; then
    status=0
    outcome=SURVIVED
    local worker_lock_fd
    if exec {worker_lock_fd}>"$MUTATION_RUNNER_LOCK_FILE"; then
      if flock -x "$worker_lock_fd"; then
        : > "$MUTATION_RUNNER_STOP" || stop_rc=$?
        flock -u "$worker_lock_fd" || stop_rc=$?
      else
        stop_rc=$?
      fi
      exec {worker_lock_fd}>&-
    else
      stop_rc=$?
    fi
  else
    status=$?
    outcome=KILLED
  fi
  exec {log_fd}>&-

  result_tmp=$MUTATION_RUNNER_RESULTS/$label.result.tmp.$BASHPID
  if ! printf '%s %s\n' "$outcome" "$status" > "$result_tmp"; then
    printf 'WORKER ERROR: could not write result for %s\n' "$label" >&2
    return 70
  fi
  if ! mv -- "$result_tmp" "$MUTATION_RUNNER_RESULTS/$label.result"; then
    printf 'WORKER ERROR: could not publish result for %s\n' "$label" >&2
    return 71
  fi

  if [[ $outcome == SURVIVED ]]; then
    printf 'SURVIVED: %s\n' "$label" >&2
  elif [[ $outcome == SPAWN_ERROR ]]; then
    printf 'WORKER ERROR: test command could not be started for %s (status %s)\n' \
      "$label" "$status" >&2
  else
    printf 'KILLED: %s\n' "$label"
  fi
  (( stop_rc == 0 )) || return 72
  return 0
}

mutation_runner_reap_one() {
  local pid label wait_status=0 result_file outcome status extra
  if (( ${#MUTATION_RUNNER_PIDS[@]} == 0 )); then
    mutation_runner_fail '<runner>' 'attempted to wait with no registered worker'
    return 1
  fi

  pid=${MUTATION_RUNNER_PIDS[0]}
  label=${MUTATION_RUNNER_PID_LABELS[0]}
  wait "$pid" || wait_status=$?
  MUTATION_RUNNER_PIDS=("${MUTATION_RUNNER_PIDS[@]:1}")
  MUTATION_RUNNER_PID_LABELS=("${MUTATION_RUNNER_PID_LABELS[@]:1}")
  MUTATION_RUNNER_PID_GROUPS=("${MUTATION_RUNNER_PID_GROUPS[@]:1}")
  MUTATION_RUNNER_REAPED_COUNT=$((MUTATION_RUNNER_REAPED_COUNT + 1))

  if (( wait_status != 0 )); then
    mutation_runner_fail "$label" "worker exited with status $wait_status"
  fi

  result_file=$MUTATION_RUNNER_RESULTS/$label.result
  if [[ ! -f $result_file ]]; then
    mutation_runner_fail "$label" 'worker ended without writing a result'
    mutation_runner_observe_survivor
    return 1
  fi
  MUTATION_RUNNER_REPORTED_COUNT=$((MUTATION_RUNNER_REPORTED_COUNT + 1))

  outcome='' status='' extra=''
  IFS=' ' read -r outcome status extra < "$result_file" || outcome=
  if [[ -n $extra ]]; then outcome=; fi
  case $outcome in
    KILLED)
      if [[ $status =~ ^[0-9]+$ ]] && (( status > 0 )); then
        MUTATION_RUNNER_KILLED_COUNT=$((MUTATION_RUNNER_KILLED_COUNT + 1))
      else
        mutation_runner_fail "$label" "invalid KILLED result status: ${status:-missing}"
      fi
      ;;
    SURVIVED)
      if [[ $status == 0 ]]; then
        MUTATION_RUNNER_SURVIVOR_OBSERVED=1
        mutation_runner_fail "$label" 'mutant survived'
      else
        mutation_runner_fail "$label" "invalid SURVIVED result status: ${status:-missing}"
      fi
      ;;
    SPAWN_ERROR)
      if [[ $status == 127 ]]; then
        mutation_runner_fail "$label" 'test command could not be started'
      else
        mutation_runner_fail "$label" "invalid SPAWN_ERROR result status: ${status:-missing}"
      fi
      ;;
    *)
      mutation_runner_fail "$label" "invalid worker result: $(<"$result_file")"
      ;;
  esac
  mutation_runner_observe_survivor
  return 0
}

mutation_runner_progress() {
  printf 'PROGRESS: registered=%s started=%s reaped=%s width=%s\n' \
    "$MUTATION_RUNNER_REGISTERED_COUNT" "$MUTATION_RUNNER_STARTED_COUNT" \
    "$MUTATION_RUNNER_REAPED_COUNT" "$MUTATION_RUNNER_JOBS" >&2
}

mutation_runner_should_stop() {
  mutation_runner_observe_survivor
  (( MUTATION_RUNNER_SURVIVOR_OBSERVED != 0 ))
}

expect_killed() {
  local label=${1-} log pid group
  if (( $# < 2 )); then
    mutation_runner_fail "${label:-<missing-label>}" 'check has no command'
    return 0
  fi
  shift
  mutation_runner_group_for_command "$@"
  group=$MUTATION_RUNNER_COMMAND_GROUP
  MUTATION_RUNNER_REGISTERED_COUNT=$((MUTATION_RUNNER_REGISTERED_COUNT + 1))

  if [[ ! $label =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    mutation_runner_fail '<runner>' "invalid check label: $label"
    mutation_runner_progress
    return 0
  fi
  if [[ -e $MUTATION_RUNNER_REGISTERED/$label ]]; then
    mutation_runner_fail "$label" 'duplicate check label would share a log and result path'
    mutation_runner_progress
    return 0
  fi
  if ! : > "$MUTATION_RUNNER_REGISTERED/$label"; then
    mutation_runner_fail "$label" 'could not register check label'
    mutation_runner_progress
    return 0
  fi

  if mutation_runner_should_stop; then
    mutation_runner_progress
    return 0
  fi

  if (( MUTATION_RUNNER_COMMAND_EXCLUSIVE )); then
    while (( ${#MUTATION_RUNNER_PIDS[@]} > 0 )); do
      mutation_runner_reap_one || :
      mutation_runner_observe_survivor
      if (( MUTATION_RUNNER_SURVIVOR_OBSERVED )); then
        mutation_runner_progress
        return 0
      fi
    done
  else
    while mutation_runner_exclusive_active; do
      mutation_runner_reap_one || :
      mutation_runner_observe_survivor
      if (( MUTATION_RUNNER_SURVIVOR_OBSERVED )); then
        mutation_runner_progress
        return 0
      fi
    done
  fi

  while (( ${#MUTATION_RUNNER_PIDS[@]} >= MUTATION_RUNNER_JOBS )); do
    mutation_runner_reap_one || :
    mutation_runner_observe_survivor
    if (( MUTATION_RUNNER_SURVIVOR_OBSERVED )); then
      mutation_runner_progress
      return 0
    fi
  done

  while [[ -n $group ]] && mutation_runner_group_active "$group"; do
    mutation_runner_reap_one || :
    mutation_runner_observe_survivor
    if (( MUTATION_RUNNER_SURVIVOR_OBSERVED )); then
      mutation_runner_progress
      return 0
    fi
  done

  if ! flock -x "$MUTATION_RUNNER_LOCK_FD"; then
    mutation_runner_fail "$label" 'could not acquire the worker admission lock'
    mutation_runner_progress
    return 0
  fi
  if mutation_runner_should_stop; then
    mutation_runner_unlock_parent || mutation_runner_fail '<runner>' 'could not release the worker admission lock'
    mutation_runner_progress
    return 0
  fi
  if ! : > "$MUTATION_RUNNER_STARTED/$label"; then
    mutation_runner_unlock_parent || mutation_runner_fail '<runner>' 'could not release the worker admission lock'
    mutation_runner_fail "$label" 'could not record worker start'
    mutation_runner_progress
    return 0
  fi
  log=$MUTATION_RUNNER_ROOT/$label.log
  if ! : > "$log"; then
    mutation_runner_unlock_parent || mutation_runner_fail '<runner>' 'could not release the worker admission lock'
    mutation_runner_fail "$label" 'could not create per-check log'
    mutation_runner_progress
    return 0
  fi
  ( mutation_runner_worker "$label" "$log" "$@" ) &
  pid=$!
  if [[ ! $pid =~ ^[0-9]+$ ]] || [[ $pid == 0 ]]; then
    mutation_runner_unlock_parent || mutation_runner_fail '<runner>' 'could not release the worker admission lock'
    mutation_runner_fail "$label" 'worker spawn did not return a usable PID'
    mutation_runner_progress
    return 0
  fi
  MUTATION_RUNNER_PIDS+=("$pid")
  MUTATION_RUNNER_PID_LABELS+=("$label")
  MUTATION_RUNNER_PID_GROUPS+=("$group")
  MUTATION_RUNNER_STARTED_COUNT=$((MUTATION_RUNNER_STARTED_COUNT + 1))
  mutation_runner_unlock_parent || mutation_runner_fail '<runner>' 'could not release the worker admission lock'
  mutation_runner_progress
  return 0
}

mutation_runner_print_failures() {
  local index label log
  for (( index = 0; index < ${#MUTATION_RUNNER_FAILURE_LABELS[@]}; index++ )); do
    label=${MUTATION_RUNNER_FAILURE_LABELS[index]}
    printf 'FAIL: %s: %s\n' "$label" "${MUTATION_RUNNER_FAILURE_REASONS[index]}" >&2
    if [[ $label =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
      log=$MUTATION_RUNNER_ROOT/$label.log
      if [[ -f $log ]]; then
        printf '%s\n' "--- tail: $log ---" >&2
        tail -n 40 -- "$log" >&2
      fi
    fi
  done
}

mutation_runner_finish() {
  local registered_file label result_file outcome status extra reported=0
  local old_nullglob=0
  local -a result_files=()

  while (( ${#MUTATION_RUNNER_PIDS[@]} > 0 )); do
    mutation_runner_reap_one || :
  done

  if shopt -q nullglob; then old_nullglob=1; fi
  shopt -s nullglob
  result_files=("$MUTATION_RUNNER_RESULTS"/*.result)
  (( old_nullglob )) || shopt -u nullglob
  reported=${#result_files[@]}

  for registered_file in "$MUTATION_RUNNER_REGISTERED"/*; do
    [[ -f $registered_file ]] || continue
    label=${registered_file##*/}
    result_file=$MUTATION_RUNNER_RESULTS/$label.result
    if [[ ! -e $MUTATION_RUNNER_STARTED/$label ]]; then
      if (( MUTATION_RUNNER_SURVIVOR_OBSERVED )) || [[ -e $MUTATION_RUNNER_STOP ]]; then
        mutation_runner_fail "$label" 'not started after a survivor was observed'
      else
        mutation_runner_fail "$label" 'registered check was not started'
      fi
      continue
    fi
    if [[ ! -f $result_file ]]; then
      mutation_runner_fail "$label" 'registered worker has no result file'
      continue
    fi
    outcome='' status='' extra=''
    IFS=' ' read -r outcome status extra < "$result_file" || outcome=
    if [[ -n $extra ]]; then outcome=; fi
    if [[ $outcome == SURVIVED && $status == 0 ]]; then
      # The reap path records the named survivor. Keep this audit as an
      # independent final guard if a caller reaches finish with a result file
      # that was not accounted for by the worker list.
      if (( MUTATION_RUNNER_SURVIVOR_OBSERVED == 0 )); then
        mutation_runner_fail "$label" 'survivor result was not observed by the parent'
      fi
    elif [[ $outcome == SPAWN_ERROR && $status == 127 ]]; then
      : # The reap path has already recorded this as a failed command spawn.
    elif [[ $outcome != KILLED || ! $status =~ ^[0-9]+$ ]] || (( status == 0 )); then
      mutation_runner_fail "$label" 'worker result did not say the check was killed'
    fi
  done

  if (( MUTATION_RUNNER_REGISTERED_COUNT != reported )); then
    mutation_runner_fail '<runner>' \
      "registered $MUTATION_RUNNER_REGISTERED_COUNT checks but found $reported reported results"
  fi
  if (( MUTATION_RUNNER_REGISTERED_COUNT != MUTATION_RUNNER_REPORTED_COUNT )); then
    mutation_runner_fail '<runner>' \
      "registered $MUTATION_RUNNER_REGISTERED_COUNT checks but collected $MUTATION_RUNNER_REPORTED_COUNT results"
  fi

  mutation_runner_print_failures
  local elapsed=$((SECONDS - MUTATION_RUNNER_STARTED_SECONDS))
  if (( MUTATION_RUNNER_FAILURE_COUNT > 0 )); then
    printf 'SUMMARY: FAIL checks=%s reported=%s killed=%s elapsed_seconds=%s\n' \
      "$MUTATION_RUNNER_REGISTERED_COUNT" "$reported" \
      "$MUTATION_RUNNER_KILLED_COUNT" "$elapsed"
    return 1
  fi
  printf 'SUMMARY: PASS checks=%s reported=%s killed=%s elapsed_seconds=%s\n' \
    "$MUTATION_RUNNER_REGISTERED_COUNT" "$reported" \
    "$MUTATION_RUNNER_KILLED_COUNT" "$elapsed"
  return 0
}
