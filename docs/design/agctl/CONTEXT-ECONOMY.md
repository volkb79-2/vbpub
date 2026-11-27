# Context economy of the controller sessions (P-plat-22c)

Status: design note and measurement, 2026-11-24 (fix round 1 after the Opus r1 review). Binding input:
`.controller-state/operator-decisions.md` "## 2026-11-24 ~10:50Z". Items A/B (resume-read.sh, 200/250/300 hook
policy) are P-plat-22a; the keep-alive watcher (section 2) and the hygiene notifier (section 5) are P-plat-22b and
are specified here with no design choice left open. This note ships no product code; the measurement scripts are
`docs/design/agctl/measure/measure.py` and `docs/design/agctl/measure/gaps.py` (throwaway, read-only, kept for
repeatability).

Tags: [measured] = observed here on 2026-11-24 (or in the named experiment Xn); [docs: file] = taken only from
documentation or `--help`, UNVERIFIED; [UNTESTED] = inferred, nobody ran it.

## 1. Measurement (F): what a restart costs

### Method

- Sessions: the seven controller sessions of PROGRAM-2026-11-24, found by `customTitle`/`agentName` in
  `~/.claude/projects/-workspaces-vbpub/*.jsonl` (newest file per name). `/clear` starts a new file; `/compact` writes a
  `system/compact_boundary` record with `compactMetadata` into the same file.
- Restart measured = the LAST `compact_boundary` with at least 10 requests after it, else the file start (a `/clear`
  restart). Repeatable cut-off: truncate the file (`head -n <line>`) before the next `compact_boundary` after the one
  in the table; the r1 review reproduced the table this way (core cut at L5459, rollback at L2788).
- A request = one assistant `message.id` (one JSONL line per content block, all with the same usage; `jsonl-metrics.py
  curve` counts lines, so its call counts are 1.5-2x the request count). Context size = `input_tokens +
  cache_read_input_tokens + cache_creation_input_tokens`.
- Split of request 1: the measured `cache_read` is the cached prefix (system prompt + tool schemas; 26,577 in all
  seven, one prefix shared across sessions). The rest (`cache_creation + input`) is apportioned by character share of
  the records between the restart and the first response: the `isCompactSummary` message, `attachment` records of
  type `file`/`compact_file_reference` (re-attached files), `instructions` (CLAUDE.md + MEMORY.md), listings
  (`skill_listing`, `deferred_tools_delta`, `agent_listing_delta`, `mcp_instructions_delta`), hook output,
  prompts/preserved tail, other. The uncached part comes out at about 2.4 chars/token; the system+tools share by
  characters would be 29.6-31.7k against the measured 26.6k, so the split is good to about +-15%.
- Token conversions: orientation read sizes use chars/4 (the `jsonl-metrics.py orient` estimate); listing and
  instruction sizes in finding 6 use the request-1 split (about 2.4 chars/token), where chars/4 would give about 30%
  less (skills: 8k against 5.5k).
- Orientation = requests 1-10. Read set = tool name plus file paths from the tool input (`extract_bash_paths` from
  `nyxloom/tools/jsonl-metrics.py`, imported unchanged, with a path-token fallback). Growth after orientation =
  median/mean per-request delta up to the next restart, plus tokens per hour.
- Commands (read-only, run in `~/.claude/projects/-workspaces-vbpub/`): `python3 -I measure.py <jsonl>...` and
  `python3 -I gaps.py <jsonl>...`.

### Results (tokens; k = 1000; measured at 10:57Z)

| Session (file) | Restart (UTC) | req 1 total | cached sys+tools | summary | re-attached files | instructions | listings | other | req 10 | growth req 1-10 | after: median / mean per req, per hour |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main-controller (b0e61dd5) | /compact 10:53, 264.7k -> 13.8k, 28 s | 60.7k | 26.6k | 2.7k | 3.2k (5 files) | 8.0k | 12.8k | 7.5k | 79.7k | 2.1k | 0.86k / 1.21k, 171k/h (a) |
| platform (adef3ef1) | /compact 09:23, 442.4k -> 20.6k, 42 s | 73.5k | 26.6k | 3.7k | 9.5k (5 files) | 9.9k | 13.3k | 10.5k | 86.4k | 1.4k | 0.93k / 1.15k, 114k/h (146 req) |
| core (8a5b6d1c) | /compact 09:46, 584.5k -> 15.9k, 30 s | 66.5k | 26.6k | 3.0k | 5.9k (5 files) | 9.8k | 13.2k | 8.0k | 82.3k | 1.8k | 0.96k / 1.46k, 143k/h (109 req) |
| hostobs (5932c288) | /compact 10:52, 338.6k -> 15.1k, 31 s | 62.7k | 26.6k | 3.3k | 4.8k (5 files) | 8.8k | 14.1k | 5.1k | 73.6k | 1.2k | 0.91k / 1.20k, 65k/h (b) |
| assay (0b7364ec) | /clear 07:23 | 53.6k | 26.6k | - | - | 10.5k | 14.8k | 1.7k | 66.8k | 1.5k | 0.79k / 1.04k, 72k/h (244 req) |
| edge (f61c3dd5) | /clear 08:35 | 53.9k | 26.6k | - | - | 10.9k | 14.8k | 1.6k | 87.6k | 3.7k | 0.94k / 1.30k, 115k/h (204 req) |
| rollback (172665b9) | /clear 07:36 | 53.8k | 26.6k | - | - | 10.7k | 14.8k | 1.7k | 96.9k | 4.8k | 1.01k / 1.45k, 90k/h (204 req) |

(a) and (b): mixed windows. Request 1 and request 10 are from the newest compaction (10:53 / 10:52), which had fewer
than 20 later requests at measurement time; the steady-growth column is from the previous window of the same file
(main: the 09:51 compaction, 142 requests; hostobs: the 06:40 `/clear` start, 223 requests). The main controller's
09:51 compaction (466.8k -> 13.6k) measured 62.6k at request 1 and 92.3k at request 10.

"other" after a compaction is mostly a `deferred_tools_record` attachment (15.0k chars, about 5k tokens), plus
`invoked_skills` (platform, 5.1k chars) and `task_status`.

Startup instructions and listings (rollback at 07:36, same layout in all seven): MEMORY.md 18.1k chars (14.8k after
the main controller's pruning), `~/.claude/CLAUDE.md` 4.8k, `vbpub/CLAUDE.md` 2.5k; skill listing 21.8k chars for 42
skills (62% of the listings); 100 deferred tool names (3.4k chars), 72 of them `mcp__claude_ai_*` connector tools; MCP
server instructions for 4 servers (Claude Docs 1.9k, Higgsfield 0.6k, platform docs 0.6k, easel 0.1k chars);
agent-type listing 4.6k chars.

What each loaded in orientation (requests 1-10; tool-result size in tokens):

- main (10:53): one Bash over `.controller-state/HANDOFF.md`, `PRIME-main-controller.md`, `PROGRAM.md` and git log
  (3.1k). At 09:51: HANDOFF.md + PROGRAM.md + a compact-resume log (5.8k), ListAgents, 6 SendMessage, git checks.
- platform: three background-task output files + `platform/HANDOFF.md` + a suite log (1.5k), reports and run logs
  (0.1-0.7k each), `platform/STATE.md` (0.3k), `operator-decisions.md` (0.2k); orientation +12.8k.
- core: run-gate state and `.run-gate` listings (0.2-1.6k), `PROGRAM.md` + `c5/HANDOFF.md` (0.4k), `run-gate.toml`,
  `assay.toml`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`; +15.8k.
- hostobs (10:52): `handovers/vbpub-hostobs-successor.md` + `hostobs/STATE.md` + a gate log, ListAgents; +11.0k.
- assay: `handovers/vbpub-assay-successor.md` (1.8k), `assay/STATE.md` (1.9k), `~/controller/remote-gates` (1.0k),
  `assay/run-gate.toml`; +13.2k.
- edge: `edge/HANDOVER.md` (1.8k), `PLAN-edge.md` + `PRIME-edge.md` + `edge/STATE.md` (0.6k), then a full Read of a
  harness-persisted tool-result file `tool-results/b6ipkgywq.txt` (8.8k, request delta 15.5k); +33.7k.
- rollback: `handovers/vbpub-rollback-successor.md` (3.6k), `rollback/STATE.md` + runs (6.7k),
  `scripts/controller/remote-gate.sh` (4.5k), the successor file again + `PLAN-rollback.md` + a prompt +
  `DESIGN-CORE-SPLIT.md` (3.9k); +43.1k.

Idle gaps and the TTL (gaps.py and one extra check):
- No gap over 55 min in the seven current files.
- In the previous week's controller files every gap over an hour was a full miss. After 282-7017 min idle, the next
  request re-created 117k-335k tokens (`2bd3b531`: 265k, 255k, 270k; `56311814`: 117k; `a887211a`: 141k, 335k), each
  at the 1-hour write price. A shared 27.8-30.0k prefix stayed warm across those gaps.
- A cache read renews the 1-hour TTL [measured]. In the main controller's 09:51-10:53 window, the 62.6k written at
  request 1 was never re-written. Ten requests more than 60 min after request 1 still read it: cache_read of at least
  252.7k, cache_creation of at most 2.4k.
- The r1 review found a hit share of 0.97 or more after three gaps of 30-55 min.
- This renewal is what makes a keep-alive ping work.

### Findings

1. The floor is fixed: a `/clear` restart starts at 53.6-53.9k, a `/compact` restart at 60.7-73.5k. Half of it is the
   shared cached prefix (26.6k, read at 0.1x); the controllable part is instructions (8-11k, mostly MEMORY.md) and
   listings (13-15k, mostly skills and connector tools).
2. The compaction summary itself is small (2.7-3.7k). Claude Code adds up to five recently read files (3.2-9.5k)
   chosen by recency, not relevance: core and hostobs got their own STATE/successor files back (useful), main got
   `controller.local.env` and `compact-resume.sh` (noise).
3. Orientation is the largest variable cost: +11k to +43k in ten requests. Whole-file reads of successor files,
   state files, scripts and a persisted tool output cost edge and rollback 34-43k; main at 10:53 oriented with one
   sliced call (3.1k result). `resume-read.sh <role>` (22a) with per-slice caps is the lever; a 15k orientation budget
   would have saved 19-28k in edge and rollback.
4. After orientation every controller grows by about 0.8-1.0k per request (median; mean 1.0-1.5k), 65-171k per hour.
   From about 80k a busy controller reaches 200k in one to two hours, so the 200/250/300 policy fires several times a
   day per controller and the restart size is paid each time.
5. Cost shape (Anthropic pricing page: cache read 0.1x, 1-hour cache write 2x base input, output 5x): a request at
   context C costs about 0.1*C plus 2x its new tokens plus its output. Every 10k saved at restart saves 1k
   base-input-equivalents on each later request until the next restart (109-244 requests measured): 110-240k per 10k
   saved, per restart.
6. Disabling the unused connectors removes about 72 deferred tool names and 2-3 MCP instruction blocks, about 1.5-2k
   tokens per session; trimming unused skills from the listing is worth up to 8k. Both enter every restart of all
   seven sessions.

## 2. Keep-alive watcher (D), specification for P-plat-22b

Why not an AGENTS.md rule (operator): a `ScheduleWakeup` is armed by the model at the end of a turn, at an interval
the model picks (285 s and 59-60 min were both seen), and is not cancelled when the session becomes active again, so
wakeups fired on top of later work and each one was a full model turn. The watcher reads the real TTL anchor (the
last API request, from the JSONL), acts only on measured idleness, derives its ping count from the JSONL (no state to
lose or reset wrongly), and has its interval and cap as constants in code.

Program: `scripts/controller/ctl-keepalive.sh` (bash, sourcing `resume-lib.sh` for `foot`/`send_text` conventions),
one process for all panes, started detached (`setsid nohup`) by hand; ctl-doctor reports `keepalive: running|absent`
and prints the start command, but never starts the watcher; singleton via
`flock -n ~/controller/locks/keepalive.lock`; pid file `~/controller/keepalive.pid`; log
`~/controller/logs/keepalive.log` (one line per decision: UTC time, name, sessionId prefix, action, reason). It
survives every controller restart because no controller owns it.

Constants: `KEEPALIVE_POLL=60` s; `KEEPALIVE_IDLE_MIN=3300` s; `KEEPALIVE_IDLE_MAX=3540` s; `KEEPALIVE_MAX=3`
(open question for the operator, default 3); `KEEPALIVE_CTX_MAX=300000`; `KEEPALIVE_NAMES` = `vbpub-main-controller
vbpub-platform vbpub-core vbpub-assay vbpub-hostobs vbpub-edge vbpub-rollback` (other sessions are never pinged).

Pane -> session mapping (do NOT use `#{pane_title}`):
- Mechanism: `~/.claude/sessions/<pid>.json`, one file per running Claude Code process, with the fields `pid`,
  `procStart`, `sessionId`, `name`, `cwd`, `tmux` (`<tmux-session>:@<window>.%<pane>`), `status`, `version`.
- For each file whose `name` is in KEEPALIVE_NAMES, all of the following must hold, else skip and log:
  - the pid is alive and `/proc/<pid>/comm` is `claude`;
  - `procStart` equals field 22 of `/proc/<pid>/stat` (pid-reuse guard);
  - the pane is the `%<pane>` part of `tmux`;
  - the JSONL is `~/.claude/projects/<cwd with every "/" replaced by "-">/<sessionId>.jsonl` and exists.
- If two live files carry the same name, skip both and log.
- Verified [measured, 2026-11-24 ~11:15Z, Claude Code 2.1.331]:
  - all 9 Claude panes have such a file;
  - each `sessionId` names an existing JSONL;
  - after a `/clear` the `sessionId` points at the new file (edge, assay, hostobs, rollback);
  - `comm` is `claude` for the child of the pane's shell (the pane's own `pane_current_command` is `claude`, but
    `pane_pid` is the shell);
  - `procStart` equals stat field 22 (2 of 2 checked).
- Assumed: the cwd-to-directory rule beyond `/workspaces/vbpub`, and that the file is rewritten on `/rename`.
- `status` (seen: `busy`, `shell`) is NOT used: its meaning is unverified, and one idle-looking session showed `busy`
  with a 1.7 h old `statusUpdatedAt`.

Per poll, per mapped session:

1. Anchor = timestamp of the newest `api-request` or assistant record in the JSONL (tail read of the last 400 kB, as
   `ctx.py ctx_size` does). Skip unless `KEEPALIVE_IDLE_MIN <= now - anchor < KEEPALIVE_IDLE_MAX`. After 59 min do
   nothing: the cache is gone and the next real turn pays anyway. The JSONL mtime is only a fast path ("younger than
   55 min: skip"); it is never a reset, since metadata records are rewritten without a request.
2. Ping count N, stateless:
   - Scan the user-prompt records back from the end of the JSONL. These are `type == "user"` records, not `isMeta`,
     whose content is a string or whose first text block is not a `tool_result`.
   - N = the number of those whose text starts with `[keepalive`, counted until the first one that does not. A real
     prompt or a task notification ends the count.
   - Skip if N >= KEEPALIVE_MAX. Restarting the watcher changes nothing, and a reply that calls a tool does not reset N.
3. Context from the last assistant usage (ctx.py rule); skip if >= KEEPALIVE_CTX_MAX (that session must compact, not
   be kept warm).
4. Lock: `flock -n ~/controller/locks/<pane>.lock`; compact-resume.sh and clear-resume.sh take the same lock (22b
   adds that to both). Busy lock: skip. The helpers take this watcher's `pane_view` and add checks of their own
   (`busy_beside_box` in resume-lib.sh, P-plat-22c): the box anchor (at or below the last prompt-box top rule, whose top
   matcher accepts the live main pane's `◉ xhigh · /effort` label), a spinner from `·✢✳✶✻✽*` followed by `Compacting`
   and an ellipsis within the 6 non-blank lines just above that rule, and a ready answer only for an input row with a top
   rule above AND a plain rule below it. The
   helpers do NOT run the watcher's pane-width check (80 columns -> skip), its 6-line `BOX_LINES` window, or its
   type-then-read-back ping sequence (the helper types only after three consecutive ready polls and reads nothing
   back); they treat a dialog, a draft, a missing box and a failed capture as not ready.
   KNOWN LIMITS:
   - The dialog check searches the last 15 non-blank lines for menu text without anchoring it to captured menu geometry.
     Transcript prose quoting `Esc to cancel` or a numbered `❯ 1.` option can therefore conservatively make an idle pane
     not ready until the text scrolls out of the window.
   - The resume helpers deliberately have no width floor: busy markers are per-line SUBSTRINGS, so a pane narrow enough
     to WRAP or TRUNCATE `esc to interrupt` can read idle. A helper that waited on width could strand an idle controller;
     the keep-alive watcher's 80-column skip remains the only width rule.
5. Pane checks on ONE `tmux capture-pane -e -p -t <pane>` (ANSI kept), all required:
   - ONE view first (`pane_view`): the box and every bottom window below are measured from the MODE line
     (`⏵⏵ bypass permissions on`), so the agents panel below it can never push a marker out of a window. The panel is
     trimmed only while the mode line carries the unfocused hint `↓ to manage`; a focused panel may look identical, is
     not captured yet, and is left untrimmed so that the strict box geometry refuses it.
   - Not busy (the WATCHER's own check, unchanged): no `esc to interrupt` and no `Compacting` in the last 6 non-blank
     lines of that view (`FOOT_LINES=6` in ctl-keepalive.sh). After the mode-line trim this window starts at the mode
     line, so an agents panel below it can no longer push a running turn's marker out of it (22b-E). The resume helpers
     add `busy_beside_box` on top of this (step 4); `busy_beside_box` has one caller, `pane_state`, and the watcher never
     calls it.
   - No dialog. None of the last 15 ANSI-stripped lines of that view may match `^\s*❯\s*[0-9]+\.\s`, and none may
     contain `Esc to cancel`. These are the cursor-on-numbered-option and footer of the permission and question
     menus; UNVERIFIED on 2.1.331, so 22b records a fixture from a real menu capture.
   - The input row is the prompt box's own row. It is the last line matching `^(\x1b\[[0-9;]*m)*❯`. It must be
     within the last 6 non-blank lines, the nearest non-blank line above it must be a box rule (ANSI-stripped, only
     `─`, at least 20 of them) or the same rule carrying ONE tmux session label and nothing else, and the line below
     it must be a plain rule (the label is accepted only above, so a labelled or extended line below still refuses).
     UNVERIFIED layout; fixture required.
   - The input row is empty by the exact method of `compact-resume.sh` `input_empty`
     (`.controller-state/PROGRAM-2026-11-24/compact-resume.sh`):
     - `grep -a $'^\(\x1b\[[0-9;]*m\)*❯' | tail -1`;
     - `perl -pe 's/\e\[2m.*?(\e\[(0|22)m|$)//g; s/\e\[[0-9;]*m//g'` drops the dim prompt suggestion that idle
       panes show, then all other SGR codes;
     - strip the leading `❯`, then NBSP (`\xc2\xa0`), then whitespace;
     - the result must be empty.
   - Pane width at least 80 (`#{pane_width}`), so the ping text cannot wrap.
6. Send with read-back:
   - Type the ping text with `tmux send-keys -l -- "<text>"`, wait 1 s, capture again (same `-e` capture), and
     extract the input row as in step 5. Keep the dim stripping, but strip only the trailing whitespace and NBSP, not
     the inner spaces.
   - Press Enter only if the row equals `❯ <text>` exactly.
   - If the row is a strict prefix of `❯ <text>`, only our partial typing is there: send `C-u` and log `abort-cleared`.
   - In any other case (foreign text present, e.g. the operator typed in between, or a dialog opened), send nothing
     more and log `abort-foreign`, so operator text is never erased.
   - In all three abort cases write `~/controller/keepalive/<sessionId>.abort` containing the anchor; that session
     is skipped while its anchor is unchanged.
7. Ping text: `[keepalive N/3] cache refresh only: reply ok, no tools` with N = count + 1. It is under 60 characters
   and starts with `[`, not `/`, `!` or `#`, so it can never be read as a slash command, a bash-mode line or a memory
   note.

Cost: the ping request reads the whole context from cache at 0.1*C and renews the 1-hour TTL (section 1, measured).
It writes about 100 new tokens at 2x and returns a one-word answer, plus whatever thinking the session's effort adds
(5x, small). A miss re-creates C minus the shared 27-30k prefix at 2x. At C = 250k a ping costs about 25k
base-equivalents and a miss about 440k. One ping pays for itself if the session resumes within the hour with
probability above about 6%, and three pings (75k) are cheaper than one miss.

Failure modes:
- The watcher is dead: no pings. That costs money but does no harm, and ctl-doctor shows it.
- The operator is typing, a dialog is open, the box layout is unknown or the session mapping is ambiguous: skip,
  retried on the next poll inside the 4-min window.
- The operator types during the 1-s read-back: `abort-foreign`, nothing more is sent.
- The ping hits a usage limit or an API error: the prompt record still counts toward N, so pings stop at the cap.
- A compaction or resume script is running on the pane: the per-pane lock makes the watcher skip.
- A Claude Code release changes the footer, menu or box markers: the markers live as constants next to
  resume-lib.sh's, with fixture tests in `tests/test-ctl-keepalive.sh` (dim suggestion, numbered menu, older `❯` rows
  in the scrollback, operator text, wrap).
- Clock: all times are UTC epoch seconds, and the anchor comes from the JSONL, not from the pane.

Not covered: Agent-tool subagents (no pane) and pi/Codex panes (open questions in section 4).

## 3. pi and Codex in the controller role (E)

Both run DeepSeek here [measured: local config]:
- pi uses the `opendesign_*` providers: `deepseek-v4.1-flash` and `-eco`, context window 328,000, max output 128,000,
  no `promptCache` declared (`~/.pi/agent/models.json`).
- Codex uses the `~/.codex-opendesign-*` profiles: `deepseek-v4.1-flash-eco`, catalog `context_window` 328,000 and
  `effective_context_window_percent` 95 = 311.6k. The openrouter profile is a second route.
- The 300k force point is 91% of a 328k window, so the policy sits just below the harness's own auto-compaction
  (open question 1).

pi 1.6.0 (docs at `/opt/mdt/npm/lib/node_modules/@earendil-works/pi-coding-agent/docs/`):
- Compaction: `/compact [instructions]` [docs: compaction.md]. Rpc `compact` with `customInstructions` [measured X5,
  pi 1.5.0]. Extension `ctx.compact({ customInstructions })` [docs: extensions.md].
- Auto-compaction: `compaction.enabled` (true locally) when `contextTokens > contextWindow - reserveTokens`. The
  default reserve is 16384, so 311.6k. `keepRecentTokens` is 20000, with per-model `compaction.modelOverrides`
  [docs: settings.md, compaction.md].
- Policy hooks: an extension, not shell hooks.
  - A `tool_result` handler can modify the result. It would carry the 200k and 250k notices, using
    `ctx.getContextUsage()` [docs: extensions.md; UNTESTED for this use].
  - `tool_call` can block (`{block, reason}`) for the 300k yield [docs: extensions.md; UNTESTED].
  - `session_before_compact` supplies the handover file as the summary with zero model calls [measured X5].
- Successor: in process, `ctx.newSession()` + `sendUserMessage("scripts/controller/resume-read.sh <role>")` [docs:
  extensions.md; UNTESTED]. Alternatively a fresh `pi --name vbpub-<topic>` with that first prompt, or `--session
  <id>` / `--continue` / `--fork` [docs: `pi --help`]. No pane typing is needed, so compact-resume.sh does not apply.
- Keep-alive: `cacheWarming: "idle"` (set locally) warms for up to 30 min between runs, but only for models that
  declare `promptCache` [docs: settings.md, models.md]. The DeepSeek models declare none, so pi does not warm them.
  Whether DeepSeek's provider cache outlives an idle hour is unsourced (open question 2).

Codex 0.171.0:
- Compaction: TUI `/compact` and app-server `thread/compact/start` [measured X6, 0.167.0]. `exec resume <id>
  "/compact"` is not a trigger [measured X6]. Steering only through `compact_prompt` (`-c` at launch or per thread
  on `thread/resume`); user messages survive verbatim and assistant messages are dropped [measured X6].
- Auto: `model_auto_compact_token_limit` (+ `_scope`; `model_post_turn_compact_threshold_percent` appears in the
  0.171 binary strings). No automatic compaction was seen under four settings [measured X3, 0.167.0]; UNTESTED on
  0.171.
- Hooks: feature `hooks` is stable/on [measured: `codex features list`]. The binary carries `hooks.json`,
  SessionStart, PreToolUse, PostToolUse, UserPromptSubmit, Stop, PreCompact, PostCompact and
  `hookSpecificOutput`/`additionalContext` [measured: binary strings]. A Claude-Code-shaped hook surface is likely
  but UNTESTED (no `hooks.json` in any of the five CODEX_HOMEs). If it holds, ctx-hooks.py ports with a
  transcript-size function for the rollout's token-count events.
- Without hooks: an external watcher over the rollout file delivers the notices with `codex queue --thread <id>
  --message <text>` [docs: `codex queue --help`; UNTESTED]. The backstop is `model_auto_compact_token_limit` plus a
  `compact_prompt` ending in "first run scripts/controller/resume-read.sh <role>" [UNTESTED].
- Successor: `codex resume <id|name> "<prompt>"` [docs: `codex resume --help`; UNTESTED] or `codex exec resume
  <thread> "<prompt>"` [measured X3], with the resume-read.sh line as the prompt; `codex fork` for a copy [docs:
  `codex --help`].
- Keep-alive: whether Codex/DeepSeek need one is unsourced (open question 2).

## 4. Open questions for the controller / operator

1. Absolute or window-relative 200/250/300 on the 328k DeepSeek routes (for example 60/75/85%)?
2. Do pi and Codex controllers on DeepSeek need a keep-alive? That needs DeepSeek's cache lifetime from its provider
   docs or a measured idle gap; until then the watcher pings only Claude Code sessions.
3. KEEPALIVE_MAX: default 3 (about 3 h 45 min of idle cover); the operator may set 4.

## 5. Hygiene notifier (C), specification for P-plat-22b, report-only

Measured now: MEMORY.md 80 lines, 14,844 B (was 18,141 B at 07:36), 1 "IN PROGRESS" line (dated 2026-11-05);
`.controller-state/HANDOFF.md` 29.5 kB with 3 `## RESUME HERE` blocks; `platform/HANDOFF.md` 51.9 kB with 7;
STATE.md files 6.6-22.3 kB; `handovers/` 87 kB in 7 files.

Program `scripts/controller/ctl-hygiene.sh`, read-only. It is run by `ctl-doctor.sh` (one call, its lines appended to
the doctor report) and by hand; not by resume-read.sh, which must stay small. Output: one line per finding,
`HYGIENE <rule> <path>: <value> (threshold <t>)`; always exit 0; it never moves or edits a file. "Today" = UTC date.

| Rule | Files | Finding when |
|---|---|---|
| M1 size | `~/.claude/projects/-workspaces-vbpub/memory/MEMORY.md` | > 12,288 B or > 70 lines (WARN); > 150 lines (ALERT; Claude Code loads only the first 200 lines of the index) |
| M2 stale marker | same | a line containing `IN PROGRESS`, `CURRENT` or `TEMPORARY` whose first date matching `\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b` is more than 3 days before today; such a line with no date: `undated` |
| M3 dead link | same | a `](<name>.md)` target that does not exist in the memory directory |
| H1 blocks | `.controller-state/HANDOFF.md`; `.controller-state/{platform,assay}/HANDOFF.md` if present; `.controller-state/edge/HANDOVER.md`; `.controller-state/{platform,core,assay,hostobs,edge,rollback}/STATE.md` | more than 2 blocks; a block starts at a line matching `^## (RESUME HERE #[0-9]+\|UPDATE )` and runs to the next such line or EOF, so its `(#N ...)` update lines belong to it; archive target to name in the message: `.controller-state/archive/<topic>-<YYYY-MM-DD>/` |
| H2 size | the H1 files | > 24,576 B |
| S1 stale successor | `.controller-state/handovers/vbpub-<topic>-successor.md` for topic in platform, core, assay, hostobs, edge, rollback | mtime more than 24 h older than `.controller-state/<topic>/STATE.md`; any other file in `handovers/` once as `not-a-programme-controller` |
| B1 closed backlog | `git ls-files '*KNOWN_ISSUES_TODO_BACKLOG.md' '*/nyxloom-trove/4-backlog.md'` | the closed entries exceed 30% of the file's bytes. An entry starts at a line matching `^#{2,3} [A-Z][A-Z0-9]*-[0-9]+` and runs to the next such line. It is closed if its first line matching `^\s*(\*\*)?Status:?(\*\*)?:?\s*(\*\*)?([A-Z-]+)` has a value in DONE, FIXED, CLOSED, RESOLVED, IMPLEMENTED, SUPERSEDED, WONTFIX |

Tests (`scripts/controller/tests/test-ctl-hygiene.sh`, fake tree): each rule at threshold and one above, an undated
marker line, a dead link, edge's UPDATE blocks, a backlog entry with no Status line (counts as open).

## 6. Deferred (r1 LOW findings not done in this round)

- `--until <line>` cut-off option for measure.py (finding 8): the scripts were moved verbatim; the reproduction
  recipe in section 1 (truncate before the next boundary) covers it.
