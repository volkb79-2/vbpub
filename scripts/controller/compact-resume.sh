#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2034,SC2154 # settings and helpers come from the sourced resume-lib.sh
set -u
# compact-resume.sh <tmux-pane-id> <compact-guidance-file> <resume-prompt-file> <log-file>
# DEFAULT restart of an interactive Claude Code session in a tmux pane: wait until the pane is
# idle, send "/compact <guidance>" (the file's newlines become spaces), wait until the compaction
# has finished (pane busy, then idle again; max 60 min), then type the resume prompt and Enter.
# The session is kept as a summary and its background work (subagents, Monitors, background
# shells, codex runs) survives, unlike /clear (clear-resume.sh). Measured 2026-11-24: 442k -> ~60k
# tokens in ~46 s. Launch it DETACHED (`setsid nohup ... &`, never Bash run_in_background), then
# end the turn. Procedure: AGENTS.md "Successor restart of an interactive session".
#
# Exit codes: 0 resume prompt sent (a compaction that never showed busy only logs WARN); 2 bad
# arguments, unreadable guidance/prompt file, unwritable log or no such pane (nothing sent);
# 3 pane never idle within 30 min (nothing sent); 4 compaction not finished within 60 min (the
# /compact was sent, the resume prompt was not; an UNRECOGNISED layout - no prompt box, a draft, a
# dialog or a failed capture - counts as busy here too, so the exit does not prove a compaction was
# running); 5 the input line never emptied (someone has text there, or the pane stayed busy before
# the resume prompt; checked before /compact and before the prompt, nothing is typed over it); 6 a
# send-keys call failed (text may be partly typed, nothing more is sent); 7 the per-pane lock (also
# taken by ctl-keepalive.sh) was not obtained within 2 min (nothing sent). A pane that is not ready
# for a reason the helper cannot read logs that reason once per wait, with the footer.
# Progress goes to <log-file>. Footer markers and the shared idle logic: resume-lib.sh. Test seams
# (tests/test-compact-resume.sh): COMPACT_RESUME_{POLL,IDLE_TRIES,TYPE_PAUSE,FINAL_WAIT,START_TRIES,DONE_TRIES,SETTLE,LOCK_DIR,LOCK_WAIT}.
RESUME_PREFIX=COMPACT_RESUME
# shellcheck source=resume-lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/resume-lib.sh"
BUSY_MARKERS+=("$COMPACTING_MARKER")
FOOT_LINES=6                   # the compaction spinner sits higher than the working one
start_tries=$(seam START_TRIES 60) done_tries=$(seam DONE_TRIES 1800) settle=$(seam SETTLE 3)
[[ $# -eq 4 ]] || { echo "usage: ${0##*/} <tmux-pane-id> <compact-guidance-file> <resume-prompt-file> <log-file>" >&2; exit 2; }
pane=$1; guidance_file=$2; prompt_file=$3; log=$4
check_log
[[ -r $guidance_file ]] || { say "ERROR guidance file unreadable: $guidance_file"; exit 2; }
[[ -r $prompt_file ]] || { say "ERROR prompt file unreadable: $prompt_file"; exit 2; }
check_pane
resolve_pane   # the target may be a session name: lock and send on the canonical %pane id
lock_pane      # the keep-alive watcher pings under the same per-pane lock; a held lock is waited for (max 2 min)
say "start pane=$pane guidance=$guidance_file prompt=$prompt_file"
idle_or_exit "$idle_tries" 3 "pane never idle within 30 min"
guidance=$(cat -- "$guidance_file"); guidance=${guidance//$'\n'/ }
say "pane idle, input empty; footer before: $(foot | grep -o 'ctx [0-9?]*k\?' | tail -1)"
send_text "/compact $guidance"
started=0; not_ready_said=0   # one not-ready line for this loop too (see resume-lib.sh say_once)
for _ in $(seq "$start_tries"); do if busy; then started=1; break; fi; sleep "$poll"; done
if [[ $started -eq 1 ]]; then say "compaction running"; else say "WARN compaction never showed busy"; fi
wait_idle "$done_tries" busy-only || { say "ERROR compaction not finished within 60 min"; exit 4; }
say "compaction done; footer: $(foot | tr '\n' '|')"
sleep "$settle"
idle_or_exit "$idle_tries" 5 "pane busy for 30 min after the compaction; resume prompt NOT sent"
send_text "$(cat -- "$prompt_file")"
sleep "$final_wait"
say "resume prompt sent; footer: $(foot | tr '\n' '|')"
