#!/usr/bin/env bash
# CTL-33: gate-run.sh with a configurable number of local slots (CONTROLLER_GATE_SLOTS, 1..8, default 2).
# Real flock holders are synchronised through FIFOs and a fake `sleep` that releases a holder on a chosen call, so no assertion
# depends on a clock or on how fast the host is.
set -uo pipefail
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CONTROLLER_GATE_SLOTS CONTROLLER_GATE_LOCK CONTROLLER_GATE2_LOCK CONTROLLER_GATE_LOCK_DIR RUN_GATE_PROFILE
unset CONTROLLER_GATE_PSI_FILE CONTROLLER_GATE_PSI_WAIT_S
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
GATE_RUN_SRC=${GATE_RUN_SCRIPT:-$SCRIPT_DIR/../gate-run.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-gate-slots-test.XXXXXX")
HOLDER_PIDS=()
cleanup() {
  # A failing check leaves its lock holders blocked on their release FIFOs: end them with the test.
  local pid
  for pid in "${HOLDER_PIDS[@]}"; do [[ -z $pid ]] || kill "$pid" 2>/dev/null; done
  [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() {
  [[ -e $1 ]] || return 0
  grep -qF -- "$2" "$1"
  local grep_rc=$?
  case $grep_rc in
    0) fail "$3 (unexpected: $2)" ;;
    1) return 0 ;;
    *) fail "$3 (could not read: $1)" ;;
  esac
}

mkdir -p "$ROOT/home" "$ROOT/controller" "$ROOT/bin" "$ROOT/fakebin"
RUN_GATE_LOG=$ROOT/run-gate.log
FAKE_SLEEP_COUNT=$ROOT/sleep-count
FAKE_PSI_SLEEP_COUNT=$ROOT/psi-sleep-count
export RUN_GATE_LOG FAKE_SLEEP_COUNT FAKE_PSI_SLEEP_COUNT
# CTL-57: every case but the memory-PSI ones runs against a quiet PSI file, so the real host's pressure decides nothing.
PSI_QUIET=$ROOT/psi-quiet
printf 'some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n' > "$PSI_QUIET"
export CONTROLLER_GATE_PSI_FILE=$PSI_QUIET
# run-gate records its cwd and arguments and says whether the lock file it was told about is held at that moment: the slot lock
# must stay held for as long as the gate runs.
cat > "$ROOT/bin/run-gate" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s|%s\n' "$PWD" "$*" >> "$RUN_GATE_LOG"
if [[ -n ${PROBE_LOCK:-} ]]; then
  if flock -n "$PROBE_LOCK" true; then echo "probe: free $PROBE_LOCK" >> "$RUN_GATE_LOG.probe"; else echo "probe: held $PROBE_LOCK" >> "$RUN_GATE_LOG.probe"; fi
fi
exit "${FAKE_RUN_GATE_RC:-0}"
SH
cat > "$ROOT/fakebin/sleep" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
# the memory-PSI wait sleeps 10 s per round: count those rounds apart, and make the host quiet at a chosen round
if [[ $1 == 10 ]]; then
  psi_count=$(cat "$FAKE_PSI_SLEEP_COUNT" 2>/dev/null || echo 0)
  psi_count=$((psi_count + 1))
  printf '%s\n' "$psi_count" > "$FAKE_PSI_SLEEP_COUNT"
  if [[ -n ${FAKE_PSI_QUIET_AT:-} ]] && (( psi_count == FAKE_PSI_QUIET_AT )); then cp "$FAKE_PSI_QUIET_FILE" "$CONTROLLER_GATE_PSI_FILE"; fi
  if (( psi_count > 3000 )); then echo 'fake sleep: gate-run still waits on memory PSI' >&2; kill -TERM "$PPID"; fi
  exit 0
fi
[[ $1 == 5 ]] || { echo "fake sleep: expected 5 or 10, got $1" >&2; exit 2; }
count=$(cat "$FAKE_SLEEP_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\n' "$count" > "$FAKE_SLEEP_COUNT"
if (( count == ${FAKE_RELEASE_AT:-1} )); then printf 'release\n' > "$FAKE_RELEASE_FIFO"; fi
# A gate-run that never takes the released slot would poll forever: stop it after a round count (not a clock) so it fails.
if (( count > ${FAKE_RELEASE_AT:-1} + 3000 )); then echo 'fake sleep: gate-run still polls long after the release' >&2; kill -TERM "$PPID"; fi
SH
chmod +x "$ROOT/bin/run-gate" "$ROOT/fakebin/sleep"

gate() {
  # gate <output file> <NAME=value>...: gate-run.sh with the fake tools; the caller's ambient slot settings never leak in.
  local out=$1
  shift
  env PATH="$ROOT/bin:$ROOT/fakebin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" "$@" \
    "$GATE_RUN_SRC" suite --case > "$out" 2>&1
}
log_lines() { if [[ -f $RUN_GATE_LOG ]]; then wc -l < "$RUN_GATE_LOG"; else echo 0; fi; }

hold_lock() {
  # hold_lock <lock file> <ready fifo> <release fifo>: a background holder of an flock, released by writing to the release FIFO.
  local lock=$1 ready=$2 release=$3
  mkfifo "$ready" "$release"
  (
    exec 8>"$lock"
    flock 8
    printf 'locked\n' > "$ready"
    IFS= read -r _ < "$release"
  ) &
  HOLDER_PIDS+=("$!")
  IFS= read -r _ < "$ready" || fail "lock holder did not start for $lock"
}
release_holder() {
  # release_holder <index into HOLDER_PIDS> <release fifo>
  printf 'release\n' > "$2"
  wait "${HOLDER_PIDS[$1]}" || fail "gate lock holder $1 did not exit"
  HOLDER_PIDS[$1]=''
}

reap_holder() {
  # reap_holder <index into HOLDER_PIDS>: that holder was released by the fake sleep from inside the poll loop. A gate-run that
  # ended without ever sleeping took a slot that was busy, and the holder would never be released: fail instead of waiting.
  [[ -e $FAKE_SLEEP_COUNT ]] || fail 'gate-run ended without polling although every slot it may use was busy'
  wait "${HOLDER_PIDS[$1]}" || fail "holder $1 did not exit after its release"
  HOLDER_PIDS[$1]=''
}

# Slot lock files of one case: slot 1 and 2 have free names, slots 3.. are gate<N>.lock beside slot 1.
case_dir() { printf '%s/case-%s' "$ROOT" "$1"; }
lock_of() {
  # lock_of <case dir> <slot>
  case $2 in
    1) printf '%s/locks/gate-one.lock' "$1" ;;
    2) printf '%s/locks/gate-two.lock' "$1" ;;
    *) printf '%s/gate%s.lock' "${DERIVED_DIR:-$1/locks}" "$2" ;;
  esac
}
slot_env() {
  # slot_env <case dir> <slots>: the environment assignments for that many slots
  local dir=$1 slots=$2
  printf 'CONTROLLER_GATE_SLOTS=%s\nCONTROLLER_GATE_LOCK=%s\nCONTROLLER_GATE2_LOCK=%s\n' "$slots" "$(lock_of "$dir" 1)" "$(lock_of "$dir" 2)"
}
wait_line() {
  # The line gate-run prints when every slot is busy.
  case $1 in
    1) echo 'gate-run: the only slot is busy, polling gate.lock every 5s' ;;
    2) echo 'gate-run: both slots busy, polling gate.lock and gate2.lock every 5s' ;;
    3) echo 'gate-run: all 3 slots busy, polling gate.lock, gate2.lock and gate3.lock every 5s' ;;
    4) echo 'gate-run: all 4 slots busy, polling gate.lock, gate2.lock, gate3.lock and gate4.lock every 5s' ;;
    8) echo 'gate-run: all 8 slots busy, polling gate.lock, gate2.lock, gate3.lock, gate4.lock, gate5.lock, gate6.lock, gate7.lock and gate8.lock every 5s' ;;
    *) fail "no expected wait line for $1 slots" ;;
  esac
}
progress_line() {
  case $1 in
    1) echo 'gate-run: still waiting after 300s; gate.lock is busy' ;;
    2) echo 'gate-run: still waiting after 300s; gate.lock and gate2.lock are both busy' ;;
    3) echo 'gate-run: still waiting after 300s; gate.lock, gate2.lock and gate3.lock are all busy' ;;
    4) echo 'gate-run: still waiting after 300s; gate.lock, gate2.lock, gate3.lock and gate4.lock are all busy' ;;
    *) fail "no expected progress line for $1 slots" ;;
  esac
}
slot_label() { if [[ $1 == 1 ]]; then echo gate.lock; else echo "gate$1.lock"; fi; }

new_case() {
  # new_case <name>: a fresh directory, a fresh run-gate log; sets CASE
  CASE=$(case_dir "$1")
  mkdir -p "$CASE/locks"
  rm -f -- "$RUN_GATE_LOG" "$RUN_GATE_LOG.probe" "$FAKE_SLEEP_COUNT" "$FAKE_PSI_SLEEP_COUNT"
  HOLDER_PIDS=()
}
hold_slots() {
  # hold_slots <slot>...: one holder per listed slot; HELD_SLOTS maps the holder index to the slot
  local slot
  HELD_SLOTS=()
  for slot in "$@"; do
    hold_lock "$(lock_of "$CASE" "$slot")" "$CASE/ready-$slot" "$CASE/release-$slot"
    HELD_SLOTS+=("$slot")
  done
}
release_all() {
  local index
  for index in "${!HELD_SLOTS[@]}"; do
    [[ -n ${HOLDER_PIDS[$index]} ]] || continue
    release_holder "$index" "$CASE/release-${HELD_SLOTS[$index]}"
  done
}
holder_index_of() {
  local index
  for index in "${!HELD_SLOTS[@]}"; do [[ ${HELD_SLOTS[$index]} == "$1" ]] && { echo "$index"; return 0; }; done
  return 1
}

for n in 1 2 3 4 8; do
  # 1. holders on slots 1..k: the first free slot k+1 is taken, in order, with no waiting
  for (( k = 0; k < n; k++ )); do
    new_case "first-$n-$k"
    held=()
    for (( s = 1; s <= k; s++ )); do held+=("$s"); done
    hold_slots "${held[@]}"
    mapfile -t env_lines < <(slot_env "$CASE" "$n")
    target=$((k + 1))
    gate "$CASE/out" "${env_lines[@]}" PROBE_LOCK="$(lock_of "$CASE" "$target")" || fail "$n slots, first $k held: gate-run failed"
    contains "$CASE/out" "gate-run: slot $(slot_label "$target")" "$n slots, first $k held: the first free slot is taken"
    not_contains "$CASE/out" 'busy, polling' "$n slots, first $k held: a free slot needs no polling"
    [[ $(log_lines) == 1 ]] || fail "$n slots, first $k held: run-gate must start exactly once"
    contains "$RUN_GATE_LOG.probe" "probe: held $(lock_of "$CASE" "$target")" "$n slots, first $k held: the slot lock stays held while the gate runs"
    [[ ! -e $FAKE_SLEEP_COUNT ]] || fail "$n slots, first $k held: gate-run slept although a slot was free"
    release_all
  done

  # 2. every slot but one held: that one is taken on the first pass, wherever it sits
  for (( j = 1; j <= n; j++ )); do
    new_case "hole-$n-$j"
    held=()
    for (( s = 1; s <= n; s++ )); do [[ $s == "$j" ]] || held+=("$s"); done
    if (( ${#held[@]} > 0 )); then hold_slots "${held[@]}"; else HELD_SLOTS=(); fi
    mapfile -t env_lines < <(slot_env "$CASE" "$n")
    gate "$CASE/out" "${env_lines[@]}" PROBE_LOCK="$(lock_of "$CASE" "$j")" || fail "$n slots, only $j free: gate-run failed"
    contains "$CASE/out" "gate-run: slot $(slot_label "$j")" "$n slots, only $j free: that slot is taken"
    not_contains "$CASE/out" 'busy, polling' "$n slots, only $j free: no polling"
    [[ $(log_lines) == 1 ]] || fail "$n slots, only $j free: run-gate must start exactly once"
    contains "$RUN_GATE_LOG.probe" "probe: held $(lock_of "$CASE" "$j")" "$n slots, only $j free: the slot lock stays held while the gate runs"
    release_all
  done

  # 3. every slot held, then exactly one is released from inside the poll loop: the wait line is printed, every slot is polled
  for (( j = 1; j <= n; j++ )); do
    new_case "poll-$n-$j"
    held=()
    for (( s = 1; s <= n; s++ )); do held+=("$s"); done
    hold_slots "${held[@]}"
    mapfile -t env_lines < <(slot_env "$CASE" "$n")
    gate "$CASE/out" "${env_lines[@]}" FAKE_RELEASE_FIFO="$CASE/release-$j" PROBE_LOCK="$(lock_of "$CASE" "$j")" || fail "$n slots, $j released while polling: gate-run failed"
    index=$(holder_index_of "$j") || fail 'test bug: holder index'
    reap_holder "$index"
    contains "$CASE/out" "$(wait_line "$n")" "$n slots: the all-busy wait line"
    contains "$CASE/out" "gate-run: slot $(slot_label "$j")" "$n slots, $j released while polling: the released slot is taken"
    [[ $(log_lines) == 1 ]] || fail "$n slots, $j released while polling: run-gate must start exactly once"
    contains "$RUN_GATE_LOG.probe" "probe: held $(lock_of "$CASE" "$j")" "$n slots, $j released while polling: the slot lock stays held while the gate runs"
    release_all
  done
done

# 4. the 300 s progress line names the configured slots
for n in 1 2 3 4; do
  new_case "progress-$n"
  held=()
  for (( s = 1; s <= n; s++ )); do held+=("$s"); done
  hold_slots "${held[@]}"
  mapfile -t env_lines < <(slot_env "$CASE" "$n")
  gate "$CASE/out" "${env_lines[@]}" FAKE_RELEASE_AT=61 FAKE_RELEASE_FIFO="$CASE/release-$n" || fail "$n slots: the progress case failed"
  index=$(holder_index_of "$n") || fail 'test bug: holder index'
  reap_holder "$index"
  contains "$CASE/out" "$(progress_line "$n")" "$n slots: the progress line names every configured slot"
  not_contains "$CASE/out" 'still waiting after 600s' "$n slots: only one progress line before the release"
  release_all
done

# 5. invalid slot counts are refused with exit 2, before any lock or run-gate
for bad in 0 9 10 x 2.5 -1 08 '+2' ' 3' '3 '; do
  new_case refuse-slots
  mapfile -t env_lines < <(slot_env "$CASE" 2)
  gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_SLOTS="$bad"
  rc=$?
  [[ $rc == 2 ]] || fail "CONTROLLER_GATE_SLOTS='$bad' must exit 2, got $rc"
  contains "$CASE/out" 'CONTROLLER_GATE_SLOTS must be an integer from 1 to 8' "CONTROLLER_GATE_SLOTS='$bad' names the rule"
  [[ $(log_lines) == 0 ]] || fail "run-gate started with CONTROLLER_GATE_SLOTS='$bad'"
  [[ ! -e $(lock_of "$CASE" 1) ]] || fail "a lock file was created with CONTROLLER_GATE_SLOTS='$bad'"
done

check_locale_safe_slot_digits() {
  local locale_name utf8_locale='' arabic_three rc=0
  # Run under an available UTF-8 locale; the explicit ASCII digit list must
  # refuse a non-ASCII digit regardless of locale collation.
  while IFS= read -r locale_name; do
    case ${locale_name,,} in
      *.utf8|*.utf-8)
        if LC_ALL=$locale_name locale charmap 2>/dev/null | grep -Fx UTF-8 >/dev/null; then
          utf8_locale=$locale_name
          break
        fi
        ;;
    esac
  done < <(locale -a)
  [[ -n $utf8_locale ]] || fail 'no available UTF-8 locale can exercise the slot-count guard'
  arabic_three=$(printf '\u0663')
  new_case locale-digit
  mapfile -t env_lines < <(slot_env "$CASE" 2)
  gate "$CASE/out" "${env_lines[@]}" LC_ALL="$utf8_locale" CONTROLLER_GATE_SLOTS="$arabic_three" || rc=$?
  [[ $rc == 2 ]] || fail "a non-ASCII digit under LC_ALL=$utf8_locale must exit 2, got $rc"
  contains "$CASE/out" 'CONTROLLER_GATE_SLOTS must be an integer from 1 to 8' \
    'a non-ASCII digit under a Unicode locale names the slot-count rule'
  [[ $(log_lines) == 0 ]] || fail 'run-gate started with a non-ASCII slot count'
  [[ ! -e $(lock_of "$CASE" 1) ]] || fail 'a lock file was created with a non-ASCII slot count'
}
check_locale_safe_slot_digits

# 6. empty means unset: the default of two slots (so slot 2 is still required)
new_case empty-slots
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS= CONTROLLER_GATE_LOCK="$(lock_of "$CASE" 1)" || rc=$?
[[ $rc == 2 ]] || fail 'an empty CONTROLLER_GATE_SLOTS must behave as the default 2 (CONTROLLER_GATE2_LOCK required)'
contains "$CASE/out" 'must both be configured as absolute paths' 'an empty CONTROLLER_GATE_SLOTS is the default two-slot mode'
mapfile -t env_lines < <(slot_env "$CASE" 2)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_SLOTS= || fail 'an empty CONTROLLER_GATE_SLOTS must run like the default'
contains "$CASE/out" 'gate-run: slot gate.lock' 'an empty CONTROLLER_GATE_SLOTS runs in the first slot'

# an unset or empty CONTROLLER_GATE_SLOTS is two slots: with both busy the gate polls and never invents a third slot
for slots_setting in unset empty; do
  new_case "default-two-$slots_setting"
  hold_slots 1 2
  mapfile -t env_lines < <(slot_env "$CASE" 2)
  env_lines=("${env_lines[@]:1}")   # drop the CONTROLLER_GATE_SLOTS line: the default decides
  [[ $slots_setting == unset ]] || env_lines+=(CONTROLLER_GATE_SLOTS=)
  gate "$CASE/out" "${env_lines[@]}" FAKE_RELEASE_FIFO="$CASE/release-2" || fail "default slots ($slots_setting): gate-run failed"
  # Both slots were busy, so the gate must have polled (slept) until the fake sleep released slot 2; a third slot would end it at once.
  reap_holder 1
  contains "$CASE/out" "$(wait_line 2)" "default slots ($slots_setting): two slots are polled"
  contains "$CASE/out" 'gate-run: slot gate2.lock' "default slots ($slots_setting): the released second slot is taken"
  [[ ! -e $CASE/locks/gate3.lock ]] || fail "default slots ($slots_setting): a third slot lock was opened"
  release_all
done
# the shared derivation rule itself: only slots 3..8 exist
new_case derive-rule
LIB_SRC=$(dirname -- "$GATE_RUN_SRC")/lib.sh
for number in 3 8; do
  [[ $(CONTROLLER_GATE_LOCK=/x/y/gate.lock bash -c '. "$1"; controller_gate_derived_lock "$2"' _ "$LIB_SRC" "$number") == "/x/y/gate$number.lock" ]] \
    || fail "slot $number must be derived next to CONTROLLER_GATE_LOCK"
done
for number in 0 1 2 9 x ''; do
  if CONTROLLER_GATE_LOCK=/x/y/gate.lock bash -c '. "$1"; controller_gate_derived_lock "$2"' _ "$LIB_SRC" "$number" > "$CASE/derive.out" 2>&1; then
    fail "slot '$number' must not be derivable"
  fi
done

# 7. one slot: CONTROLLER_GATE2_LOCK is not required, and is not used even when it is set (relative or not)
new_case one-slot
gate "$CASE/out" CONTROLLER_GATE_SLOTS=1 CONTROLLER_GATE_LOCK="$(lock_of "$CASE" 1)" || fail 'one slot must not need CONTROLLER_GATE2_LOCK'
contains "$CASE/out" 'gate-run: slot gate.lock' 'one slot runs in gate.lock'
gate "$CASE/out" CONTROLLER_GATE_SLOTS=1 CONTROLLER_GATE_LOCK="$(lock_of "$CASE" 1)" CONTROLLER_GATE2_LOCK=relative/two.lock \
  || fail 'one slot must ignore a CONTROLLER_GATE2_LOCK it does not use'
[[ ! -e relative ]] || fail 'one slot opened CONTROLLER_GATE2_LOCK'
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=1 CONTROLLER_GATE_LOCK=relative-one.lock || rc=$?
[[ $rc == 2 ]] || fail "one slot with a relative CONTROLLER_GATE_LOCK must exit 2, got $rc"
contains "$CASE/out" 'CONTROLLER_GATE_LOCK must be configured as an absolute path' 'one slot still needs an absolute CONTROLLER_GATE_LOCK'
not_contains "$CASE/out" 'CONTROLLER_GATE2_LOCK' 'one slot does not mention the second lock'
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=1 || rc=$?
[[ $rc == 2 ]] || fail "one slot without CONTROLLER_GATE_LOCK must exit 2, got $rc"

# 8. more than two slots still require the first two lock variables
for n in 2 3 8; do
  new_case "need-two-$n"
  rc=0
  gate "$CASE/out" CONTROLLER_GATE_SLOTS="$n" CONTROLLER_GATE_LOCK="$(lock_of "$CASE" 1)" || rc=$?
  [[ $rc == 2 ]] || fail "$n slots without CONTROLLER_GATE2_LOCK must exit 2, got $rc"
  contains "$CASE/out" 'CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK must both be configured as absolute paths' "$n slots need the first two variables"
done

# 9. CONTROLLER_GATE_LOCK_DIR: absolute (checked from three slots on), and it decides where slots 3.. live
new_case lock-dir-relative
mapfile -t env_lines < <(slot_env "$CASE" 3)
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_LOCK_DIR=relative/dir || rc=$?
[[ $rc == 2 ]] || fail "a relative CONTROLLER_GATE_LOCK_DIR must exit 2, got $rc"
contains "$CASE/out" 'CONTROLLER_GATE_LOCK_DIR must be an absolute directory path' 'the lock directory must be absolute'
[[ $(log_lines) == 0 ]] || fail 'run-gate started with a relative CONTROLLER_GATE_LOCK_DIR'
mapfile -t env_lines < <(slot_env "$CASE" 2)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_LOCK_DIR=relative/dir || fail 'a CONTROLLER_GATE_LOCK_DIR that two slots never use must not matter'

# default place: beside CONTROLLER_GATE_LOCK. Slots 1 and 2 and the file gate3.lock beside slot 1 are held: slot 4 is taken.
new_case derive-default
mkdir -p "$CASE/other"
hold_slots 1 2 3
mapfile -t env_lines < <(slot_env "$CASE" 4)
gate "$CASE/out" "${env_lines[@]}" || fail 'derived slot 4 was not taken'
contains "$CASE/out" 'gate-run: slot gate4.lock' 'slot 3 is gate3.lock beside CONTROLLER_GATE_LOCK'
[[ -e $CASE/locks/gate4.lock ]] || fail 'slot 4 is gate4.lock beside CONTROLLER_GATE_LOCK'
release_all
# with CONTROLLER_GATE_LOCK_DIR: gate3.lock in that directory; a held gate3.lock beside slot 1 no longer matters
new_case derive-dir
mkdir -p "$CASE/other"
hold_slots 1 2 3
mapfile -t env_lines < <(slot_env "$CASE" 4)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_LOCK_DIR="$CASE/other" || fail 'slot 3 in CONTROLLER_GATE_LOCK_DIR was not taken'
contains "$CASE/out" 'gate-run: slot gate3.lock' 'CONTROLLER_GATE_LOCK_DIR moves slots 3.. away from CONTROLLER_GATE_LOCK'
[[ -e $CASE/other/gate3.lock ]] || fail 'slot 3 is gate3.lock in CONTROLLER_GATE_LOCK_DIR'
release_all
# and the other way round: gate3.lock in CONTROLLER_GATE_LOCK_DIR busy, slot 4 is taken (the default place proves nothing here)
new_case derive-dir-busy
mkdir -p "$CASE/other"
DERIVED_DIR=$CASE/other
hold_slots 1 2 3
mapfile -t env_lines < <(slot_env "$CASE" 4)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_LOCK_DIR="$CASE/other" || fail 'slot 4 was not taken with slot 3 busy in CONTROLLER_GATE_LOCK_DIR'
contains "$CASE/out" 'gate-run: slot gate4.lock' 'a busy gate3.lock in CONTROLLER_GATE_LOCK_DIR moves the gate to slot 4'
[[ -e $CASE/other/gate4.lock ]] || fail 'slot 4 is gate4.lock in CONTROLLER_GATE_LOCK_DIR'
release_all
unset DERIVED_DIR

# 10. every configured lock path must differ; the derived ones count
new_case distinct
d=$CASE/locks
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=3 CONTROLLER_GATE_LOCK="$d/a.lock" CONTROLLER_GATE2_LOCK="$d/gate3.lock" || rc=$?
[[ $rc == 2 ]] || fail "slot 2 colliding with the derived slot 3 must exit 2, got $rc"
contains "$CASE/out" 'gate lock paths must be distinct' 'a collision with a derived slot is refused'
contains "$CASE/out" "gate-run: slots 2 and 3 both use $d/gate3.lock" 'the collision names the slots and the path'
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=3 CONTROLLER_GATE_LOCK="$d/gate3.lock" CONTROLLER_GATE2_LOCK="$d/b.lock" || rc=$?
[[ $rc == 2 ]] || fail "slot 1 colliding with the derived slot 3 must exit 2, got $rc"
contains "$CASE/out" "gate-run: slots 1 and 3 both use $d/gate3.lock" 'slot 1 against a derived slot'
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=2 CONTROLLER_GATE_LOCK="$d/a.lock" CONTROLLER_GATE2_LOCK="$d/a.lock" || rc=$?
[[ $rc == 2 ]] || fail "two equal lock paths must exit 2, got $rc"
[[ $(cat "$CASE/out") == 'gate lock paths must be distinct' ]] || fail 'the two-slot distinct message must stay exactly one line'
# a trailing slash on CONTROLLER_GATE_LOCK_DIR names the same file, and the root directory has no doubled slash
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=3 CONTROLLER_GATE_LOCK="$d/a.lock" CONTROLLER_GATE2_LOCK="$d/gate3.lock" CONTROLLER_GATE_LOCK_DIR="$d/" || rc=$?
[[ $rc == 2 ]] || fail "a trailing slash on CONTROLLER_GATE_LOCK_DIR must not hide a collision, got $rc"
contains "$CASE/out" "gate-run: slots 2 and 3 both use $d/gate3.lock" 'a trailing slash is not doubled'
rc=0
gate "$CASE/out" CONTROLLER_GATE_SLOTS=3 CONTROLLER_GATE_LOCK="$d/a.lock" CONTROLLER_GATE2_LOCK=/gate3.lock CONTROLLER_GATE_LOCK_DIR=/ || rc=$?
[[ $rc == 2 ]] || fail "the root directory as CONTROLLER_GATE_LOCK_DIR must not double the slash, got $rc"
contains "$CASE/out" 'gate-run: slots 2 and 3 both use /gate3.lock' 'the root directory gives /gate3.lock'
[[ $(log_lines) == 0 ]] || fail 'run-gate started after a distinctness refusal'

# 11. the gate's own exit code passes through from a derived slot, and the default arguments are untouched
new_case exit-code
mapfile -t env_lines < <(slot_env "$CASE" 3)
rc=0
gate "$CASE/out" "${env_lines[@]}" FAKE_RUN_GATE_RC=7 || rc=$?
[[ $rc == 7 ]] || fail "gate-run returned $rc instead of the gate exit 7"
contains "$RUN_GATE_LOG" "|suite --case" 'run-gate gets the arguments unchanged'
# the same exit code from the poll loop
new_case exit-code-poll
hold_slots 1 2
mapfile -t env_lines < <(slot_env "$CASE" 2)
rc=0
gate "$CASE/out" "${env_lines[@]}" FAKE_RUN_GATE_RC=9 FAKE_RELEASE_FIFO="$CASE/release-2" || rc=$?
[[ $rc == 9 ]] || fail "gate-run returned $rc from the poll loop instead of the gate exit 9"
reap_holder 1
release_all

# 12. CTL-57: a held slot is not an admission: memory PSI full avg60 >= 10 (or unreadable) holds the gate back
psi_case() {
  # psi_case <name> <full avg60 text or '-' for no file> [<full avg10 text>]: a fresh case with that PSI file; sets PSI.
  # avg10 (default 0.00) and the `some` line (always above the threshold) must decide nothing.
  new_case "$1"
  PSI=$CASE/psi
  if [[ $2 == - ]]; then rm -f -- "$PSI"; else printf 'some avg10=99.00 avg60=99.00 avg300=0.00 total=0\nfull avg10=%s avg60=%s avg300=0.00 total=0\n' "${3:-0.00}" "$2" > "$PSI"; fi
}
psi_lines() { grep -c 'gate-run: waiting for host admission' "$CASE/out" || true; }
# quiet (and just below the threshold): starts at once, no wait, even during an avg10 spike
for quiet_value in 0.00 9.99; do
  psi_case "psi-quiet-$quiet_value" "$quiet_value" 99.00
  mapfile -t env_lines < <(slot_env "$CASE" 2)
  gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" || fail "quiet memory PSI $quiet_value: gate-run failed"
  [[ $(log_lines) == 1 ]] || fail "quiet memory PSI $quiet_value: the gate did not start"
  [[ ! -e $FAKE_PSI_SLEEP_COUNT ]] || fail "quiet memory PSI $quiet_value: gate-run waited"
done
# busy (exactly at the threshold) then quiet: starts after, and says why it waited
psi_case psi-busy-then-quiet 10.00
printf 'full avg10=99.00 avg60=0.50 avg300=0.00 total=0\n' > "$CASE/psi-later"
mapfile -t env_lines < <(slot_env "$CASE" 2)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" FAKE_PSI_QUIET_AT=2 FAKE_PSI_QUIET_FILE="$CASE/psi-later" || fail 'busy then quiet: gate-run failed'
[[ $(log_lines) == 1 ]] || fail 'busy then quiet: the gate did not start after the host went quiet'
[[ $(cat "$FAKE_PSI_SLEEP_COUNT") == 2 ]] || fail "busy then quiet: expected 2 wait rounds, got $(cat "$FAKE_PSI_SLEEP_COUNT")"
contains "$CASE/out" 'gate-run: waiting for host admission: memory PSI full avg60=10.00 >= 10, 0s of 1800s' 'the wait names the value and the bound'
# the second place a slot is taken: the poll loop (both slots busy, then one released) waits as well
psi_case psi-poll-path 14.50
printf 'full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n' > "$CASE/psi-later"
hold_slots 1 2
mapfile -t env_lines < <(slot_env "$CASE" 2)
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" FAKE_RELEASE_FIFO="$CASE/release-2" FAKE_PSI_QUIET_AT=1 FAKE_PSI_QUIET_FILE="$CASE/psi-later" \
  || fail 'the poll path did not wait for memory PSI and then start'
reap_holder 1
release_all
contains "$CASE/out" 'waiting for host admission: memory PSI full avg60=14.50 >= 10' 'a gate taken from the poll loop waits for memory PSI too'
[[ $(log_lines) == 1 ]] || fail 'poll path: the gate did not start'
# busy until the bound: exit 75, one line a minute, nothing ran, and never a lane verdict
psi_case psi-timeout 15.25
mapfile -t env_lines < <(slot_env "$CASE" 2)
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" CONTROLLER_GATE_PSI_WAIT_S=130 FAKE_RUN_GATE_RC=1 || rc=$?
[[ $rc == 75 ]] || fail "busy memory PSI past the bound must exit 75, got $rc"
[[ $(log_lines) == 0 ]] || fail 'busy memory PSI past the bound: run-gate ran'
[[ $(psi_lines) == 3 ]] || fail "expected one wait line a minute (0s, 60s, 120s), got $(psi_lines)"
contains "$CASE/out" 'gate-run: not admitted (memory PSI full avg60=15.25 >= 10 for 130s); nothing ran' 'the refusal says nothing ran'
# an unreadable file is refused at once (no wait), like dev host-admission/1
psi_case psi-unreadable -
mapfile -t env_lines < <(slot_env "$CASE" 2)
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" CONTROLLER_GATE_PSI_WAIT_S=30 || rc=$?
[[ $rc == 75 ]] || fail "an unreadable memory PSI file must exit 75, got $rc"
[[ $(log_lines) == 0 ]] || fail 'an unreadable memory PSI file started the gate'
[[ ! -e $FAKE_PSI_SLEEP_COUNT ]] || fail 'an unreadable memory PSI file was waited on instead of refused at once'
contains "$CASE/out" "gate-run: not admitted (memory PSI unreadable: $PSI); nothing ran" 'unreadable is named as such'
# a file without a full line, and a malformed value, are unreadable too
psi_case psi-garbled 'x1'
mapfile -t env_lines < <(slot_env "$CASE" 2)
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" CONTROLLER_GATE_PSI_WAIT_S=10 || rc=$?
[[ $rc == 75 ]] || fail "a malformed memory PSI value must exit 75, got $rc"
[[ ! -e $FAKE_PSI_SLEEP_COUNT ]] || fail 'a malformed memory PSI value was waited on'
# the file turning unreadable while waiting is refused at once as well
psi_case psi-vanishes 14.00
printf 'full avg10=0.00 avg60=garbled\n' > "$CASE/psi-later"
mapfile -t env_lines < <(slot_env "$CASE" 2)
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_FILE="$PSI" FAKE_PSI_QUIET_AT=1 FAKE_PSI_QUIET_FILE="$CASE/psi-later" || rc=$?
[[ $rc == 75 ]] || fail "a memory PSI file that turns unreadable while waiting must exit 75, got $rc"
[[ $(log_lines) == 0 ]] || fail 'a memory PSI file that turned unreadable while waiting started the gate'
[[ $(cat "$FAKE_PSI_SLEEP_COUNT") == 1 ]] || fail 'the wait went on after the PSI value became unreadable'
contains "$CASE/out" "gate-run: not admitted (memory PSI unreadable: $PSI); nothing ran" 'unreadable while waiting is named as such'
rc=0
gate "$CASE/out" "${env_lines[@]}" CONTROLLER_GATE_PSI_WAIT_S=ten || rc=$?
[[ $rc == 2 ]] || fail "a non-numeric CONTROLLER_GATE_PSI_WAIT_S must exit 2, got $rc"

echo 'PASS gate slots: 1..8 configurable slots, derived locks, refusals, wait and progress wording'
