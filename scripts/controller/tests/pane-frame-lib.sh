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
draft_frame() { # <dest> DRAFT_FIX: continuation below the empty row; DRAFT_VERIFY: continuation above it
  local dest=$1 kind=$2 rule; rule=$(printf '─%.0s' {1..40})
  case $kind in
    DRAFT_FIX) printf 'output\n%s\n❯\xc2\xa0\n  - Fix the tests\n%s\n  ctx 41%%\n' "$rule" "$rule" >"$dest" ;;
    DRAFT_VERIFY) printf 'output\n%s\n  - verify it\n❯\xc2\xa0\n%s\n  ctx 41%%\n' "$rule" "$rule" >"$dest" ;;
  esac
}
# H1RULE: the LIVE main pane's prompt-box TOP rule, as recorded in `.controller-state/successor-runs/main-compact{18,19,20,21}`
# (2026-11-26): 20+ U+2500, then the label `◉ xhigh · /effort  vbpub-main-controller`, then one closing U+2500. The old
# `is_label_rule` refuses it (`◉`, `·`, `/` are outside its label set), so before H1 the helpers saw no box and waited.
H1RULE="$(printf '─%.0s' {1..40}) ◉ xhigh · /effort  vbpub-main-controller ─"
# stray_frame <dest> <kind>: the shapes that are NOT a ready prompt box. The resume helpers must read EVERY one of them
# as busy (waiting, never typing), which is what the M1/M2/H1/L2 rules pin:
#   EMPTY    a failed or empty capture (a busy pane is not known, so it is not ready)
#   QMENU    a column-0 question menu: no numbered cursor and no footer, so even dialog_open does not see it; no box
#   BANG     a bash-mode `!` input row, which is not the prompt box's `❯` row; no box
#   NORULE   a `❯` row with no box rule above it (ordinary output instead); the rule BELOW alone must not admit it
#   DRAFT    an EMPTY first row with a continuation line under it (a multi-line draft, Shift+Enter or a pasted newline)
#   DRAFT_FIX / DRAFT_VERIFY: dash-prefixed continuation lines in the lower-rule and label-rule positions
#   MENUBOX  the permission menu ABOVE an intact, empty prompt box (only the dialog check stops it)
#   H1I      the live main pane's labelled top rule, idle: this one MUST read ready and be typed into (the H1 regression)
#   H1B      ... the same, with `esc to interrupt` on the box's mode line: it must NOT be typed into
#   SPIN     an L2 spinner `✶ Compacting …` above the top rule, outside the bottom window
#   SPIN_EDGE the same spinner on the sixth non-blank line above the top rule (the scan boundary)
#   STALESPIN a recognized idle prompt box with an earlier spinner-shaped transcript line beyond the active-spinner window
#   SPIN_PROSE `● Compacting now.` above the box: transcript prose, not a spinner
#   PROSE    the same place, prose that merely CONTAINS `Compacting`: it must not make the pane busy
stray_frame() {
  local dest=$1 rule; rule=$(printf '─%.0s' {1..40})   # the plain prompt-box rule, as the caller's own RULE
  case $2 in
    EMPTY) : >"$dest" ;;
    QMENU) printf '%b\n' 'Do you want to proceed?' '1. Yes' '2. No' '' >"$dest" ;;
    BANG) printf '%b\n' 'output' "$rule" '! echo hi' "$rule" '  ctx 41%' >"$dest" ;;
    NORULE) printf '%b\n' 'output' '❯\xc2\xa0' "$rule" '  ctx 41%' >"$dest" ;;
    DRAFT) printf '%b\n' 'output' "$rule" '❯\xc2\xa0' '  some text' "$rule" '  ctx 41%' >"$dest" ;;
    DRAFT_FIX|DRAFT_VERIFY) draft_frame "$dest" "$2" ;;
    MENUBOX) { printf '%b\n' "$MENU" ''; printf '%b\n' "$rule" '❯\xc2\xa0' "$rule" '  ctx 41%'; } >"$dest" ;;
    H1I) printf '%b\n' 'output' "$H1RULE" '❯\xc2\xa0' "$rule" '  ctx 41%' '  ⏵⏵ bypass permissions on · 1 monitor · ← 8 agents · ↓ to manage' >"$dest" ;;
    H1B) printf '%b\n' 'output' "$H1RULE" '❯\xc2\xa0' "$rule" '  ctx 41%' '  ⏵⏵ bypass permissions on · esc to interrupt · ← 8 agents' >"$dest" ;;
    SPIN|SPIN_EDGE|SPIN_PROSE|PROSE) # the Compacting line is never directly above the top rule: compact-resume's 6-line bottom
      # window reaches that one row (and rightly reads any `Compacting` there as busy), so only the L2 scan sees these lines
      if [[ $2 == SPIN ]]; then
        printf '%b\n' 'output' '  ✔ Update installed · restart to apply' \
          '  ✶ Compacting conversation… (3s)…' \
          '  notice: 3 background tasks' '  notice: 8 agents' '  notice: ← worktree clean' >"$dest"
      elif [[ $2 == SPIN_EDGE ]]; then
        printf '%b\n' '  ✶ Compacting conversation… (3s)…' '  notice: 1' '  notice: 2' \
          '  notice: 3' '  notice: 4' '  notice: 5' >"$dest"
      elif [[ $2 == SPIN_PROSE ]]; then
        printf '%b\n' 'output' '  ● Compacting now.' '  notice: 1' >"$dest"
      else
        printf '%b\n' 'output' '  The agent is Compacting a summary right now.' '  notice: 1' >"$dest"
      fi
      printf '%b\n' "$rule" '❯\xc2\xa0' "$rule" '  ctx 41%' '  ⏵⏵ bypass permissions on · ← 8 agents' >>"$dest" ;;
    STALESPIN) # a complete idle prompt box, with the stale spinner ten non-blank rows above its top rule
      printf '%b\n' '  ✶ Compacting conversation… (3s)…' '  notice: 1' '  notice: 2' \
        '  notice: 3' '  notice: 4' '  notice: 5' '  notice: 6' '  notice: 7' '  notice: 8' \
        'output' "$rule" '❯\xc2\xa0' "$rule" '  ctx 41%' '  ⏵⏵ bypass permissions on · ← 8 agents' >"$dest" ;;
  esac
}
# menu_frame <dest> <extra panel rows>: an MB layout, built from the REAL capture's last section: the permission menu above
# the capture's own top box rule, its empty box, its mode line and its panel plus N extra rows
menu_frame() {
  local dest=$1 rows=$2 first k
  first=$(grep -nFm1 -- '❯' "$PANELFIX" | cut -d: -f1)   # -m1: grep stops itself, no `| head` SIGPIPE under pipefail
  { printf '%b\n' "$MENU"; tail -n "+$((first - 1))" "$PANELFIX"; for ((k = 1; k <= rows; k++)); do printf '  \e[38;5;246m◯ general-purpose\e[39m  \e[38;5;246magent %s running a foreground tool\e[39m\n' "$k"; done; } >"$dest"
}
