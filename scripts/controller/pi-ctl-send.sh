#!/usr/bin/env bash
# Queue one peer message into a pi sub-controller's inbox from any local shell.
# The extension's own `send_message` tool is the only path for a running pi
# session; this script is the same write for a caller that is not inside pi
# (main's shell, a controller script, an operator). Both produce one envelope
# file, so the receiving extension cannot tell them apart.
set -uo pipefail

usage() {
  cat >&2 <<'EOF'
usage: pi-ctl-send.sh [--from <sender>] <role> <text|->

Write one peer message into $PI_CTL_ROOT/inbox/<role>/ atomically and print its
message id. `-` reads the body from stdin. The sender defaults to `main`
(PI_CTL_SEND_FROM or --from changes it). Message ids are generated here; the
receiving extension confirms delivery by that id.
EOF
}

# Same patterns and limits the extension enforces, so a message this script
# writes is exactly one the extension would have written itself. A field the
# extension would refuse must be refused HERE too, with a reason: a quietly
# written file would only be quarantined later, away from the caller.
name_is_valid() {
  [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

refuse() {
  printf 'pi-ctl-send: %s\n' "$1" >&2
  exit 2
}

sender=${PI_CTL_SEND_FROM:-main}
role=
body_arg=
positional=0
while (($#)); do
  case $1 in
    --from)
      (($# >= 2)) || refuse '--from needs a sender name'
      sender=$2
      shift
      ;;
    --from=*)
      sender=${1#--from=}
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      while (($#)); do
        positional=$((positional + 1))
        if ((positional == 1)); then role=$1; else body_arg=$1; fi
        shift
      done
      break
      ;;
    -)
      # `-` is the stdin body marker, not an option; only valid in the body slot.
      positional=$((positional + 1))
      case $positional in
        1) role=- ;;
        2) body_arg=- ;;
        *) refuse 'expected exactly <role> and <text|->' ;;
      esac
      ;;
    -*)
      usage
      refuse "unknown option: $1"
      ;;
    *)
      positional=$((positional + 1))
      case $positional in
        1) role=$1 ;;
        2) body_arg=$1 ;;
        *) refuse 'expected exactly <role> and <text|->' ;;
      esac
      ;;
  esac
  shift
done
((positional == 2)) || { usage; refuse 'expected exactly <role> and <text|->'; }
[[ $body_arg == - || -n $body_arg ]] || refuse 'the message body is empty'
name_is_valid "$role" || refuse "invalid role name: $role"
name_is_valid "$sender" || refuse "invalid sender name: $sender"

: "${HOME:?HOME is required when PI_CTL_ROOT is unset}"
root=${PI_CTL_ROOT:-$HOME/controller/pi-ctl}
[[ $root == /* ]] || refuse "PI_CTL_ROOT must be an absolute path: $root"

max_send_bytes=${PI_CTL_MAX_SEND_BYTES:-65536}
[[ $max_send_bytes =~ ^[0-9]+$ ]] || refuse 'PI_CTL_MAX_SEND_BYTES must be an integer from 256 to 1048576'
((max_send_bytes >= 256 && max_send_bytes <= 1048576)) \
  || refuse 'PI_CTL_MAX_SEND_BYTES must be an integer from 256 to 1048576'

# A symlink anywhere on the configured path would make this script write into
# whatever the link points at, including a checkout; the extension refuses the
# same shape. `..` is followed by name rather than resolved through /proc, so
# the check reads the path as written.
reject_symlink_components() {
  local candidate=$1 current=/ rest component
  rest=${candidate#/}
  while [[ -n $rest ]]; do
    if [[ $rest == */* ]]; then
      component=${rest%%/*}
      rest=${rest#*/}
    else
      component=$rest
      rest=
    fi
    [[ -z $component || $component == . ]] && continue
    if [[ $component == .. ]]; then
      current=${current%/*}
      [[ -n $current ]] || current=/
      continue
    fi
    if [[ $current == / ]]; then current=/$component; else current=$current/$component; fi
    [[ ! -L $current ]] || refuse "symlink in the state path: $current"
  done
}
reject_symlink_components "$root"

# The extension requires the state root to live outside the repository, and a
# message written inside a checkout would be invisible to the running session.
repository_root() {
  local current=$PWD
  while :; do
    if [[ -e $current/.git ]]; then
      realpath -e -- "$current"
      return 0
    fi
    [[ $current == / ]] && return 0
    current=$(dirname -- "$current")
  done
}
repository=$(repository_root)
if [[ -n $repository ]]; then
  physical_root=$(realpath -m -- "$root")
  case $physical_root in
    "$repository" | "$repository"/*) refuse "PI_CTL_ROOT must be outside the repository: $root" ;;
  esac
fi

inbox=$root/inbox/$role
# Check before mkdir as well as before publication: mkdir -p must never walk
# through an inbox or role symlink. The repeated pre-publish check narrows the
# remaining check-then-use race but cannot eliminate it without directory fds.
reject_symlink_components "$inbox"
if [[ -e $inbox || -L $inbox ]]; then
  [[ -d $inbox && ! -L $inbox ]] || refuse "the inbox path is not a plain directory: $inbox"
else
  umask 077
  mkdir -p -- "$inbox" || refuse "cannot create the inbox directory: $inbox"
fi
chmod 0700 -- "$inbox" 2>/dev/null || true

# Two temporaries, neither of them ending in `.msg`: the receiving session scans
# for `*.msg`, so an interrupted write must not leave a claim it could pick up
# half-formed.
umask 077
body_temp=$(mktemp "$inbox/.pi-ctl-send-body.XXXXXXXX") || refuse "cannot create a temporary body file in $inbox"
message_temp=$(mktemp "$inbox/.pi-ctl-send.XXXXXXXX") || {
  rm -f -- "$body_temp"
  refuse "cannot create a temporary message file in $inbox"
}
cleanup() {
  [[ -z ${body_temp:-} ]] || rm -f -- "$body_temp"
  [[ -z ${message_temp:-} ]] || rm -f -- "$message_temp"
}
trap cleanup EXIT

if [[ $body_arg == - ]]; then
  cat > "$body_temp" || refuse 'cannot read the message body from stdin'
else
  printf '%s' "$body_arg" > "$body_temp" || refuse 'cannot write the message body'
fi
body_bytes=$(wc -c < "$body_temp") || refuse 'cannot measure the message body'
((body_bytes > 0)) || refuse 'the message body is empty'
((body_bytes <= max_send_bytes)) || refuse "message exceeds $max_send_bytes bytes"

id="msg-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
name_is_valid "$role" || refuse "invalid role name: $role"
[[ $id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || refuse "generated id is invalid: $id"
created=$(date -u +%Y-%m-%dT%H:%M:%S.000Z) || refuse 'cannot record the message creation time'
printf 'from=%s\nid=%s\nto=%s\ncreated=%s\n\n' "$sender" "$id" "$role" "$created" > "$message_temp" \
  || refuse 'cannot write the message envelope'
cat -- "$body_temp" >> "$message_temp" || refuse 'cannot append the message body'
target=$inbox/$id-from-$sender.msg
# A rename is the atomic publish: the receiver sees either no file or the whole
# envelope, never a header that a body is still being appended to.
reject_symlink_components "$inbox"
mv -- "$message_temp" "$target" || refuse "cannot publish the message to $target"
message_temp=
body_temp=
printf '%s\n' "$id"
