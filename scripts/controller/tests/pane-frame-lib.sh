# shellcheck shell=bash disable=SC2034 # sourced by the controller tests: they use these frames and the capture marker
# pane-frame-lib.sh: the frames built from the sanitized REAL Claude Code agents-panel capture, shared by
# tests/test-ctl-keepalive.sh, tests/test-clear-resume.sh and tests/test-compact-resume.sh. Source it, never run it.
# One fixture keeps the three tests honest: the resume helpers and the keep-alive watcher must measure the SAME pane.
PANELFIX=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/fixtures/keepalive/idle-agents-panel.ansi   # the sanitized REAL idle agents-panel capture
MENU=' Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n Esc to cancel'
# panel_frame <dest> <extra panel rows> <busy 0|1> <unfocused hint 0|1> [mode text]: a temp copy of the REAL capture, edited
# line by line. `· esc to interrupt` goes BEFORE ` · ← ` when busy (the real mode line puts it there, see the recordings),
# ` · ↓ to manage` is removed when the panel has focus, N agent rows are appended below the mode line, and [mode text]
# replaces the bypass-mode footer, so a pane in another permission mode (no BOX_FOOT_MARKER anywhere) can be framed.
panel_frame() {
  local dest=$1 rows=$2 busy=$3 hint=$4 mode=${5:-} k mode_line
  mode_line=$(grep -nFm1 -- '⏵⏵ bypass permissions on' "$PANELFIX" | cut -d: -f1)
  cp -- "$PANELFIX" "$dest"
  [[ -z $mode ]] || sed -i "${mode_line}s|⏵⏵ bypass permissions on|$mode|" "$dest"
  if (( busy )); then sed -i "${mode_line}s/ · ← / · esc to interrupt · ← /" "$dest"; fi
  if (( ! hint )); then sed -i "${mode_line}s/ · ↓ to manage//" "$dest"; fi
  for ((k = 1; k <= rows; k++)); do printf '  \e[38;5;246m◯ general-purpose\e[39m  \e[38;5;246magent %s running a foreground tool\e[39m\n' "$k" >>"$dest"; done
}
# typed_row <dest> <text>: the read-back shape: the capture's EMPTY input row now holds the text
typed_row() { sed -i "s/❯$(printf '\xc2\xa0')/❯ $2/" "$1"; }
# draft_row <dest>: a continuation line right under the live input row (a multi-line draft)
draft_row() { perl -i -pe 'if (!$d && /\xe2\x9d\xaf\xc2\xa0/) { $_ .= "  foo\n"; $d = 1 }' "$1"; }
# menu_frame <dest> <extra panel rows>: an MB layout, built from the REAL capture's last section: the permission menu above
# the capture's own top box rule, its empty box, its mode line and its panel plus N extra rows
menu_frame() {
  local dest=$1 rows=$2 first k
  first=$(grep -nFm1 -- '❯' "$PANELFIX" | cut -d: -f1)   # -m1: grep stops itself, no `| head` SIGPIPE under pipefail
  { printf '%b\n' "$MENU"; tail -n "+$((first - 1))" "$PANELFIX"; for ((k = 1; k <= rows; k++)); do printf '  \e[38;5;246m◯ general-purpose\e[39m  \e[38;5;246magent %s running a foreground tool\e[39m\n' "$k"; done; } >"$dest"
}
