#!/usr/bin/env bash
# pi-ctl-send.sh: one peer message, written the way the extension would write it.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CONTROLLER_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
SEND=${PI_CTL_SEND_SCRIPT:-$CONTROLLER_DIR/pi-ctl-send.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-ctl-send.XXXXXX")
cleanup() { rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
STATE=$ROOT/state
WORK=$ROOT/work
mkdir -p "$STATE" "$WORK"
chmod 0700 "$ROOT" "$STATE" "$WORK"
# The tool refuses to write into a checkout; the working directory under test is
# deliberately a plain directory, with its own fake repository beside it.
REPO=$ROOT/repo
mkdir -p "$REPO"
printf 'gitdir: elsewhere\n' > "$REPO/.git"

# run_send <cwd> [env assignment...] -- <argv...>
# Later assignment wins in `env`, so a caller overrides PI_CTL_ROOT explicitly.
run_send() {
  local cwd=$1
  shift
  local -a envs=(HOME="$ROOT/home" PATH="/usr/bin:/bin" PI_CTL_ROOT="$STATE")
  while (($#)); do
    [[ $1 == -- ]] && { shift; break; }
    envs+=("$1")
    shift
  done
  ( cd -- "$cwd" && env -i "${envs[@]}" "$SEND" "$@" )
}
mkdir -p "$ROOT/home"

check_envelope_and_id() {
  local id out=$ROOT/send.out
  run_send "$WORK" -- hostobs 'hello from main' > "$out" 2>&1 || fail 'a plain send refused'
  id=$(<"$out")
  [[ $id =~ ^msg-[0-9a-f]{32}$ ]] || fail "the script did not print a message id: $id"
  local file=$STATE/inbox/hostobs/$id-from-main.msg
  [[ -f $file ]] || fail "the expected envelope file was not written: $file"
  [[ $(stat -c '%a' -- "$file") == 600 ]] || fail 'the message envelope is not mode 0600'
  [[ $(stat -c '%a' -- "$STATE/inbox/hostobs") == 700 ]] || fail 'the inbox directory is not mode 0700'
  # The extension parses `from`, `id`, `to`, `created`, a blank line, then the body.
  local expected
  printf -v expected 'from=main\nid=%s\nto=hostobs\ncreated=' "$id"
  head -c "${#expected}" -- "$file" | grep -qF -- "$expected" || fail 'the envelope header is not in the extension format'
  sed -n '4p' -- "$file" | grep -Eq '^created=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.000Z$' \
    || fail 'the created field is not an ISO-8601 timestamp'
  [[ $(sed -n '5p' -- "$file") == '' ]] || fail 'the header is not terminated by a blank line'
  [[ $(tail -n +6 -- "$file") == 'hello from main' ]] || fail 'the body is not the text argument'
  # No temporary file may survive, and none may end in .msg.
  local leftover
  for leftover in "$STATE"/inbox/hostobs/.*; do
    [[ -e $leftover ]] || continue
    fail "a temporary file survived: $leftover"
  done
  [[ $(find "$STATE/inbox/hostobs" -maxdepth 1 -name '*.msg' | wc -l) == 1 ]] || fail 'exactly one message file was expected'
}

check_stdin_body() {
  local id out=$ROOT/stdin.out
  printf 'line one\nline two\n' | run_send "$WORK" -- hostobs - > "$out" 2>&1 || fail 'a stdin send refused'
  id=$(<"$out")
  local file=$STATE/inbox/hostobs/$id-from-main.msg
  [[ -f $file ]] || fail 'the stdin body did not produce an envelope'
  [[ $(tail -n +6 -- "$file") == $'line one\nline two' ]] || fail 'the stdin body was not preserved verbatim'
}

check_sender_override() {
  local id out=$ROOT/sender.out
  run_send "$WORK" -- --from platform hostobs 'from a named sender' > "$out" 2>&1 \
    || fail 'a --from send refused'
  id=$(<"$out")
  [[ -f $STATE/inbox/hostobs/$id-from-platform.msg ]] || fail '--from did not change the file name'
  grep -qF -- 'from=platform' "$STATE/inbox/hostobs/$id-from-platform.msg" || fail '--from did not change the envelope sender'

  out=$ROOT/env-sender.out
  run_send "$WORK" PI_CTL_SEND_FROM=edge -- hostobs 'from the environment' > "$out" 2>&1 \
    || fail 'PI_CTL_SEND_FROM was not honoured'
  id=$(<"$out")
  [[ -f $STATE/inbox/hostobs/$id-from-edge.msg ]] || fail 'PI_CTL_SEND_FROM did not change the file name'
}

check_name_and_body_refusals() {
  local out=$ROOT/refuse.out
  for role in '../escape' 'bad role' '.hidden' '' "$(printf 'x%.0s' {1..65})"; do
    if run_send "$WORK" -- "$role" 'text' > "$out" 2>&1; then
      fail "an invalid role was accepted: $role"
    fi
    contains "$out" 'invalid role name' 'an invalid role name is refused with its reason'
  done
  for sender in '../escape' 'bad role' "$(printf 'y%.0s' {1..65})"; do
    if run_send "$WORK" -- --from "$sender" hostobs 'text' > "$out" 2>&1; then
      fail "an invalid sender was accepted: $sender"
    fi
    contains "$out" 'invalid sender name' 'an invalid sender name is refused with its reason'
  done
  if run_send "$WORK" -- hostobs '' > "$out" 2>&1; then fail 'an empty body was accepted'; fi
  contains "$out" 'the message body is empty' 'an empty body argument is refused'
  if printf '' | run_send "$WORK" -- hostobs - > "$out" 2>&1; then fail 'an empty stdin body was accepted'; fi
  contains "$out" 'the message body is empty' 'an empty stdin body is refused'
  if run_send "$WORK" -- hostobs > "$out" 2>&1; then fail 'a send without a body was accepted'; fi
  if run_send "$WORK" -- hostobs one two > "$out" 2>&1; then fail 'a third positional argument was accepted'; fi
  contains "$out" 'expected exactly <role> and <text|->' 'extra arguments are refused'
  if run_send "$WORK" -- --bogus hostobs text > "$out" 2>&1; then fail 'an unknown option was accepted'; fi
  contains "$out" 'unknown option' 'an unknown option is refused'
  # A refusal must leave no message behind: a half-valid write would be a
  # message the receiving session could act on.
  # Four messages exist by now: the plain send, the stdin send and the two
  # sender-override sends above. A refusal must not add a fifth.
  [[ $(find "$STATE/inbox" -name '*.msg' | wc -l) == 4 ]] || fail 'a refused send still wrote a message file'
}

check_size_limit() {
  local out=$ROOT/size.out
  local body
  body=$(printf 'z%.0s' {1..300})
  if run_send "$WORK" PI_CTL_MAX_SEND_BYTES=256 -- hostobs "$body" > "$out" 2>&1; then
    fail 'a body over the configured limit was accepted'
  fi
  contains "$out" 'message exceeds 256 bytes' 'an oversized body is refused with the limit'
  if run_send "$WORK" PI_CTL_MAX_SEND_BYTES=abc -- hostobs 'text' > "$out" 2>&1; then
    fail 'an unparsable size limit was accepted'
  fi
  contains "$out" 'PI_CTL_MAX_SEND_BYTES must be an integer' 'an invalid size limit is refused'
  if run_send "$WORK" PI_CTL_MAX_SEND_BYTES=1 -- hostobs 'text' > "$out" 2>&1; then
    fail 'a size limit below the extension minimum was accepted'
  fi
  contains "$out" 'PI_CTL_MAX_SEND_BYTES must be an integer' 'a size limit below the minimum is refused'
  [[ $(find "$STATE/inbox" -name '*.msg' | wc -l) == 4 ]] || fail 'a refused size still wrote a message file'
  # The limit is honoured, not just parsed: a body exactly at the limit passes and
  # the message after it is counted.
  local exact
  exact=$(printf 'e%.0s' {1..256})
  if ! run_send "$WORK" PI_CTL_MAX_SEND_BYTES=256 -- hostobs "$exact" > "$out" 2>&1; then
    fail 'a body exactly at the configured limit was refused'
  fi
  [[ $(find "$STATE/inbox" -name '*.msg' | wc -l) == 5 ]] || fail 'an accepted body did not produce exactly one message file'
}

check_path_refusals() {
  local out=$ROOT/path.out
  if run_send "$WORK" PI_CTL_ROOT=relative -- hostobs 'text' > "$out" 2>&1; then
    fail 'a relative PI_CTL_ROOT was accepted'
  fi
  contains "$out" 'PI_CTL_ROOT must be an absolute path' 'a relative root is refused'
  # A root inside a checkout would write where the running session cannot see it.
  if run_send "$REPO" PI_CTL_ROOT="$REPO/state" -- hostobs 'text' > "$out" 2>&1; then
    fail 'a root inside the repository was accepted'
  fi
  contains "$out" 'outside the repository' 'a root inside the repository is refused'
  [[ ! -e $REPO/state ]] || fail 'a refused root inside the repository created a directory'
  # A symlinked state root would write through the link.
  local target=$ROOT/link-target
  mkdir -p "$target"
  ln -s "$target" "$ROOT/state-link"
  if run_send "$WORK" PI_CTL_ROOT="$ROOT/state-link" -- hostobs 'text' > "$out" 2>&1; then
    fail 'a symlinked state root was accepted'
  fi
  contains "$out" 'symlink in the state path' 'a symlinked root is refused'
  [[ -z $(find "$target" -mindepth 1) ]] || fail 'a refused symlinked root still received files'
}

check_inbox_not_a_directory() {
  local out=$ROOT/inbox-shape.out
  mkdir -p "$STATE/inbox"
  printf 'not a directory\n' > "$STATE/inbox/plainrole"
  if run_send "$WORK" -- plainrole 'text' > "$out" 2>&1; then
    fail 'a non-directory inbox was accepted'
  fi
  contains "$out" 'not a plain directory' 'a non-directory inbox path is refused'
  mkdir -p "$STATE/inbox/linkedrole-target"
  ln -s "$STATE/inbox/linkedrole-target" "$STATE/inbox/linkedrole"
  if run_send "$WORK" -- linkedrole 'text' > "$out" 2>&1; then
    fail 'a symlinked inbox was accepted'
  fi
  contains "$out" 'symlink in the state path' 'a symlinked inbox path is refused'
  [[ -z $(find "$STATE/inbox/linkedrole-target" -mindepth 1) ]] || fail 'a refused symlinked inbox still received files'
  rm -f -- "$STATE/inbox/plainrole"
  rm -f -- "$STATE/inbox/linkedrole"

  # An intermediate `inbox` symlink must be refused before mkdir or mktemp
  # writes anything through it.
  local linked_root=$ROOT/intermediate-state target=$ROOT/intermediate-target
  mkdir -p "$linked_root" "$target"
  ln -s "$target" "$linked_root/inbox"
  if run_send "$WORK" PI_CTL_ROOT="$linked_root" -- hostobs 'text' > "$out" 2>&1; then
    fail 'a symlinked inbox component was accepted'
  fi
  contains "$out" 'symlink in the state path' 'a symlinked inbox component is refused'
  [[ -z $(find "$target" -mindepth 1) ]] || fail 'a refused inbox component symlink still received files'
}

check_message_is_claimed_by_the_extension() {
  # The written envelope must be one the receiving extension accepts: the node
  # core parses the same header the extension's tool writes.
  local out=$ROOT/claim.out
  run_send "$WORK" -- hostobs 'claim me' > "$out" 2>&1 || fail 'the claim fixture send refused'
  local id
  id=$(<"$out")
  # shellcheck disable=SC2016 # the node source must not expand in the test shell.
  node --input-type=module -e '
    import fs from "node:fs";
    import path from "node:path";
    const dir = process.argv[1];
    const id = process.argv[2];
    const file = path.join(dir, `${id}-from-main.msg`);
    const raw = fs.readFileSync(file, "utf8");
    const separator = raw.indexOf("\n\n");
    if (separator < 0) throw new Error("no envelope separator");
    const header = Object.fromEntries(raw.slice(0, separator).split("\n").map((line) => {
      const index = line.indexOf("=");
      return index < 0 ? ["", ""] : [line.slice(0, index), line.slice(index + 1)];
    }));
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(header.from)) throw new Error("from is invalid");
    if (header.to !== "hostobs") throw new Error("to is wrong");
    if (header.id !== id) throw new Error("id is wrong");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(header.id)) throw new Error("id field is invalid");
    if (raw.slice(separator + 2) !== "claim me") throw new Error("body was not carried verbatim");
  ' "$STATE/inbox/hostobs" "$id" || fail 'the written envelope does not parse as an extension envelope'
}

check_atomic_publish() {
  # The receiving session scans `*.msg`, so a temporary name must never end
  # there: a crash mid-write leaves nothing it could claim.
  local pattern
  pattern=$(grep -c "pi-ctl-send-body\.XXXXXXXX" "$SEND") || fail 'the body temporary is not a hidden dot-prefixed name'
  ((pattern >= 1)) || fail 'the body temporary is not a hidden dot-prefixed name'
  grep -qF "pi-ctl-send.*\.msg" "$SEND" && fail 'a temporary file name ends in .msg'
  # shellcheck disable=SC2016 # the pattern is the literal shell text under test.
  grep -qF 'mv -- "$message_temp"' "$SEND" || fail 'the message is not published by a single rename'
}

check_envelope_and_id
check_stdin_body
check_sender_override
check_name_and_body_refusals
check_size_limit
check_path_refusals
check_inbox_not_a_directory
check_message_is_claimed_by_the_extension
check_atomic_publish
echo 'PASS pi-ctl-send: envelope format, atomic publish, refusals with reasons and one printed id'
