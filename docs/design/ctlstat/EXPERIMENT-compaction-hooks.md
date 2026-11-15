<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ctlstat-EXPERIMENT-compaction-hooks.md -->
<!-- relation: ctlstat compaction experiment -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

**Status: PARTIAL (E1-E5 complete and measured; E6 UNTESTED: `claude --bg` refuses to start without an interactive workspace-trust acceptance)**

# ctlstat experiment: external compaction, hook injection, process survival

Claude Code `2.1.319` (`claude --version`), model `haiku` (resolves to `claude-haiku-5-5`), headless `-p` only, 2026-11-09.
Everything ran in `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/exp-compact/` (call it `$X`), no estate files, no global settings edited (hooks attached with `--settings`). Total spend about USD 1.3 (the E2 auto-compaction loop with ~100 KB file reads was most of it).
Relates to `ctlstat-REQUIREMENTS.md` 12, 12.1, 12.2 (R12.2, R12.3, R12.5, R12.9).

## Verdict table

| Exp | Question | Verdict |
|---|---|---|
| E1 | `claude -p --resume <id> "/compact <instr>"` compacts externally | **PASS** (works under `-p`; prints nothing in text/json mode; transcript gets a boundary + summary; session continues from the summary) |
| E2 | PreCompact stdout steers the summary; PostCompact logs it | **PASS** (manual AND auto trigger; handover text honoured in 8/8 compactions) |
| E3 | PostToolUse `additionalContext` seen by model, throttle by state file | **PASS** (model quoted the markers; change-only throttle worked; no token field in hook input) |
| E4 | Context fill from JSONL usage; compaction events detectable | **PASS** (fill = input + cache_read + cache_creation of the last assistant message; `compact_boundary` entry carries pre/post tokens) |
| E5 | Process survival from a `-p` session | **PASS/mixed**: `nohup`/`setsid nohup` children survive `-p` exit, SIGTERM of claude, and later `/compact`; `run_in_background` Bash tasks do NOT survive (and `-p` blocks until they finish) |
| E6 | `claude --bg` session | **UNTESTED**: `Workspace not trusted. Run claude in <cwd> once and accept the trust prompt` (exit 1). Needs an interactive trust acceptance (writes global state), so skipped |

## E1 external compaction

Setup (4 turns, each a Read of a small fact file, then compaction):
```
cd $X/e1
claude -p --session-id $U --model haiku "Read the file fa.txt ... Reply with just 'ok A'."    # U = fresh uuid
claude -p --resume $U --model haiku "Read the file fb.txt ... "                                 # fb, fc, fd likewise
claude -p --resume $U --model haiku "/compact keep fact A and B only" </dev/null > out 2> err
```
- Exit code 0. stdout EMPTY. stderr only the harmless `Warning: no stdin data received in 3s` (redirect `</dev/null` to avoid the 3 s wait). `--output-format json` gives `{"type":"result","subtype":"success","result":"","num_turns":0,"is_error":false,"total_cost_usd":...}`; `--output-format stream-json --verbose` additionally emits the events below.
- `--output-format stream-json --verbose` events for a compaction, in order: `{"type":"system","subtype":"status","status":"compacting"}`, `rate_limit_event`, `{"type":"system","subtype":"status","status":null,"compact_result":"success"}`, `system/init`, then
  `{"type":"system","subtype":"compact_boundary","compact_metadata":{"trigger":"manual","pre_tokens":28411,"post_tokens":5622,"cumulative_dropped_tokens":50965,"duration_ms":7312,"preserved_segment":{...},"preserved_messages":{...}}}`, a `user` event carrying the full summary text, `user` event `<local-command-stdout>Compacted </local-command-stdout>`, final `result` (empty `result`, `num_turns` 0, usage zeros, but `total_cost_usd` is the cumulative session cost including the summary call).
- Transcript JSONL appended entries (see E4 for paths): `queue-operation` enqueue/dequeue with `content:"/compact keep fact A and B only"`; `{"type":"system","subtype":"compact_boundary","content":"Conversation compacted","parentUuid":null,"logicalParentUuid":<last pre-compaction uuid>,"compactMetadata":{trigger,preTokens,postTokens,durationMs,preservedSegment,preservedMessages,cumulativeDroppedTokens}}`; the summary as `{"type":"user","isCompactSummary":true,"isVisibleInTranscriptOnly":true,"message":{"role":"user","content":"This session is being continued from a previous conversation that ran out of context. ... Summary:\n1. Primary Request and Intent: ..."}}` (3.6 KB here; ends with "If you need specific details ... read the full transcript at: <path to the jsonl>"); then `<command-name>/compact</command-name>` and `<local-command-stdout>Compacted (ctrl+o to see full summary)</local-command-stdout>` user entries; then re-injected `attachment` entries (see surprise 1).
- Continuation: `claude -p --resume $U "What facts do you remember (A,B,C,D)?"` answered all four correctly (surprise 1 explains why C and D were still known). The next API request used a fresh context: first post-compaction assistant fill 28,129 vs 35,526 before (only the ~7k conversation part shrank; about 21k is fixed system prompt + tools + CLAUDE.md overhead in this setup).
- The summary text itself (9-section Claude Code template) is readable plain text (not opaque), 2.4-8.7 KB in all our runs, and honours the custom instruction wording ("Omitted from the summary per the instruction") for conversational facts. Relevant to R12.9: the official compaction is a readable, LLM-written, structured summary of a few KB and can be steered by instructions; a mechanical extract would have to beat that.
- `-p --continue` variants were not needed.

## E2 PreCompact / PostCompact hooks

Settings (`$X/settings-e2.json`, use absolute script paths):
```json
{"hooks":{
 "PreCompact": [{"hooks":[{"type":"command","command":"$X/hooks/precompact.sh"}]}],
 "PostCompact":[{"hooks":[{"type":"command","command":"$X/hooks/postcompact.sh"}]}]}}
```
`precompact.sh`: `cat > "$X/logs/precompact-$(date +%s%N).json"; cat "$X/e2/handover.txt"` (handover = `REMEMBER: the secret word is OTTER; drop everything about cats.`). `postcompact.sh`: `cat > "$X/logs/postcompact-...json"`.

Hook stdin JSON, exact keys:
- PreCompact: `session_id, transcript_path, cwd, prompt_id, hook_event_name:"PreCompact", trigger:"manual"|"auto", custom_instructions` (the `/compact` argument string, `null` for auto).
- PostCompact: `session_id, transcript_path, cwd, prompt_id, hook_event_name:"PostCompact", trigger, compact_summary`. (No `custom_instructions`, no token counts.) `compact_summary` begins with an `<analysis>...</analysis>` scratchpad block followed by `<summary>...</summary>`; the transcript copy has the analysis block stripped and a different wrapper (hook copy 1,904 chars vs transcript 2,480 chars for the same compaction). So the hook value is not byte-identical to the stored summary.

Results:
- Manual: `claude -p --settings $X/settings-e2.json --resume $U --model haiku "/compact keep codename and dog"` after turns stating BLUEHERON, dog Rex, cat Mimi (chat only, no files). The summary contained `Additional Instructions: keep codename and dog. REMEMBER: the secret word is OTTER; drop everything about cats.` i.e. hook stdout is appended to the user's custom instructions. Recall afterwards: codename BLUEHERON, dog Rex, cat `unknown`, secret word OTTER. The model had never seen OTTER in chat, so the hook output was honoured end to end. This realises R12.3 (agent writes a handover file, harness injects it as compaction instructions).
- Auto: `--autocompact 100k` (accepted; the flag takes `auto` or 100k-1M) plus Reads of 4 x 98 KB random-word files in the same session. Auto compaction fired 7 times (pre_tokens 72,702 / 70,876 / 96,210 / 150,096 / 93,657 / 150,885 / 94,372; post_tokens 7,823 / 8,081 / 48,896 / 6,943 / 49,108 / 7,229 / 49,360), PreCompact received `trigger:"auto"`, `custom_instructions:null`, and the handover stdout was still applied: OTTER present in 7/7 auto summaries (and 1/1 manual). Auto fires well below the configured window (around 70k-96k for a 100k window) and also mid-turn (post_tokens 49k = preserved tail kept).
- PostCompact `compact_summary` sizes: 1.9 KB (tiny chat), 6.2-8.7 KB (large-file session).
- Unverified: whether a non-zero PreCompact exit blocks compaction; whether stdout over 10,000 chars is truncated (cap is documented only); `custom_instructions` for a PreCompact on a `/compact` with no argument (auto gave null).

## E3 PostToolUse additionalContext

Settings (matcher on Bash), `$X/settings-e3a.json`:
```json
{"hooks":{"PostToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"$X/hooks/posttool-every.sh"}]}]}}
```
Hook output (stdout, exit 0): `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[STATUS 3] ctx-marker-3"}}`.
- Run: `claude -p --settings $X/settings-e3a.json --allowedTools Bash --session-id $U --model haiku "Run three separate Bash tool calls: echo one / two / three. Then quote verbatim the most recent [STATUS n] marker ..."`. The model quoted `[STATUS 3] ctx-marker-3` and listed markers 1, 2, 3 in order, and said they came from "hook output attached to the Bash results". PASS.
- Where it is stored (two `attachment` entries per injection): (1) `attachment.type:"hook_success"` with `hookName:"PostToolUse:Bash"`, `toolUseID`, `stdout` (the raw JSON the hook printed), `command`, `durationMs`; transcript-only record. (2) `attachment.type:"hook_additional_context"`, `content:["[STATUS 2] ctx-marker-2"]`, `hookName`, `toolUseID`, plus `rendered:[{"content":"<system-reminder>\nPostToolUse:Bash hook additional context: [STATUS 2] ctx-marker-2\n</system-reminder>"}]`, `renderedRole:"system"`. The `rendered` text is what the model sees.
- Characters added to the model context per call = payload length + 79 (the `<system-reminder>\nPostToolUse:Bash hook additional context: ` + `\n</system-reminder>` wrapper). Example: 22-char payload -> 101-char reminder. The hook_success record adds roughly 300-600 bytes to the JSONL but not to model context.
- Hook stdin JSON for PostToolUse (full key list): `session_id, transcript_path, cwd, prompt_id, permission_mode ("bypassPermissions" in this -p run), effort:{level}, hook_event_name, tool_name, tool_input:{command,description}, tool_response:{stdout,stderr,interrupted,isImage,noOutputExpected}, tool_use_id, duration_ms`. **No token-usage / context-size field** (confirmed). The hook must read the `transcript_path` JSONL (E4) for fill.
- Throttled variant `posttool-throttled.sh`: compares `$X/state/status.txt` with `$X/state/last-<session_id>.txt`; prints nothing and exits 0 if unchanged, else stores it and prints the JSON. Run with 5 Bash calls where call 3 itself rewrote the status file: exactly 2 injections (`[STATUS-CHANGED] alpha` after call 1, `beta` after call 3), nothing after calls 2, 4, 5; silent hook calls leave NO transcript entry (only the 2 `hook_additional_context` entries exist). Model reported the same two markers at the right positions. PASS for change-only injection with a state file keyed on `session_id`.
- Observation: the same transcript shows a `total_tokens_reminder` system block `<total_tokens>14972934 tokens left</total_tokens>` (starts at 15,000,000 in `-p` sessions and counts down with cumulative usage across requests). It is a session token budget, not the context fill, and it is injected by the harness (not by our hook).

## E4 usage in the transcript

JSON path: for each entry with `"type":"assistant"` and `"isSidechain":false`: `message.usage.{input_tokens, cache_read_input_tokens, cache_creation_input_tokens, output_tokens}` (also `message.usage.cache_creation.{ephemeral_1h_input_tokens,ephemeral_5m_input_tokens}`, `message.usage.iterations[]`, `message.id`, `message.model`, `message.stop_reason`, `requestId`, `apiBlockIndex`).
- **Context fill** = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` of the LAST assistant message (output of that call becomes next input). Verified: `compactMetadata.preTokens` == last fill + that message's `output_tokens` exactly (35,526 + 4 = 35,530; 28,407 + 4 = 28,411; 23,267 + 4 = 23,271).
- **Dedupe**: a single API message with several content blocks (text/thinking/tool_use) is written as several `assistant` lines with the same `message.id` and the same usage; dedupe on `message.id` (or count only once per `requestId`) before summing anything cumulative.
- Raw `input_tokens` is small when the cache is warm but can be large (e.g. 11,382) right after a compaction/cache miss, so always add all three fields.
- **Compaction detection** (any one is sufficient): `type=="system" && subtype=="compact_boundary"` (fields `compactMetadata.{trigger,preTokens,postTokens,durationMs,cumulativeDroppedTokens,preservedSegment,preservedMessages}`; it has `parentUuid:null` and `logicalParentUuid` pointing at the last pre-compaction message, so the uuid chain restarts there), or `type=="user" && isCompactSummary==true` (the summary). The `/compact` invocation itself shows as `queue-operation` enqueue with `content:"/compact ..."` and `user` entries with `<command-name>/compact</command-name>`. Auto compactions have the same boundary with `trigger:"auto"` and no `/compact` command entries.
- Before/after (E1): last fill before = 35,526 (`preTokens` 35,530); `postTokens` = 7,354 is the compacted conversation only (excludes the ~21k fixed system/tool overhead); first assistant message after = 28,129. So "fill right after compaction" cannot be read from the JSONL until the next assistant message; estimate it as `postTokens + overhead`, where overhead = (first post fill) - `postTokens` of the previous compaction. E2 auto: 150,096 -> postTokens 6,943 -> next fill 26,463.
- Other useful entries: `{"type":"cost-state", totalCostUSD, modelUsage{<model>:{inputTokens,outputTokens,cacheReadInputTokens,cacheCreationInputTokens,costUSD}}}` (cumulative, written at turn end), `tools-used`, `api-request`/`api-request-blob`. `--output-format json` `usage` / `modelUsage` give the cumulative per-process-call view; `modelUsage.<model>.contextWindow` is reported (1,000,000 here).
- Parser used: `$X/ctxfill.py <session.jsonl>` (30 lines; prints per-message usage + fill, COMPACT and SUMMARY_MSG rows).

JSONL locations (kept, not deleted): `~/.claude/projects/-tmp-claude-1003--workspaces-vbpub-c9129a68-573f-4fa3-972d-cddb9bcf26c0-scratchpad-exp-compact-{e1,e2,e3,e5}/`
- E1 `9fc24006-3948-4b78-9c10-8c3d67d90257.jsonl`; E2 `5d965ce0-4fd3-4764-8293-33d4a5d5d4db.jsonl`; E3 `1b7c1255-6c0f-4db7-bc1b-4a8fc9aea86b.jsonl` (every-call), `40ccfc33-b32f-4691-ae27-7b5f6f0f4895.jsonl` (throttled); E5 `1cb87e01-b267-4661-a124-823c698cf69d.jsonl`.
- Hook logs: `$X/logs/` (precompact-*.json, postcompact-*.json, posttool-every.jsonl, posttool-throttled.jsonl). Copies: `$X/e1/{pre,post}-compact.jsonl`, `$X/e1/compact2.stream.jsonl`, `$X/e2/e2-full.jsonl`.

## E5 process survival

Run: `timeout 100 claude -p --allowedTools Bash --session-id $U --model haiku --output-format json "<3 Bash calls>"` where the calls were (a) `setsid nohup sh -c 'echo $$ > pid_a.txt; exec sleep 600' >/dev/null 2>&1 &`, (b) same without `setsid` (`sleep 601`), (c) Bash with `run_in_background:true`, `sh -c 'echo $$ > pid_c.txt; exec sleep 602'`. PIDs came from the pid files (1990535, 1990570, 1990618); killed at the end by exact PID after confirming `ps -o args= -p PID` was `sleep 60x`.
- (c): `claude -p` did NOT exit: it blocked until my `timeout 100` SIGTERMed it (100.6 s). Second test with a short bg job (`sleep 45`, resumed session): `-p` waited 50 s, the model was re-woken by the task completion notification, replied, exit 0. So under `-p`, a `run_in_background` task keeps the process alive until it finishes, and if claude is killed first the task dies with it (c was gone when I checked).
- (a) and (b): both alive after claude was SIGTERMed (ppid 1, `sleep 600` pgid=sid=own pid for (a); (b) had its own pgid/sid too, the Bash tool apparently puts each command in a new session, so plain `nohup cmd &` already survived). Both still alive after a following `claude -p --resume $U --model haiku "/compact keep nothing special"` (7.8 s). So compaction does not kill detached children; and `-p` termination (SIGTERM) does not kill nohup'd children. Not tested: SIGKILL of claude, host reboot, a command started WITHOUT nohup/`&` redirection (e.g. holding the Bash tool's stdout), interactive-session Ctrl-C/exit, Agent-tool subagents (R12.5 subagent case still open).
- Implication for R12.5: detached `nohup ... & disown` jobs (as codex-impl does) survive both a controller kill and an external `/compact`; session-bound `run_in_background` tasks and anything waiting on them do not.

## E6 `claude --bg`

`claude --bg --model haiku --name exp-compact-bg "..."` in `$X/e6` -> `Workspace not trusted. Run \`claude\` in <cwd> once and accept the trust prompt, then retry.` (exit 1, 0.75 s). Starting one needs an interactive trust acceptance that persists in global user state; skipped per the safety rules. Relevant help text (`claude --help`, `claude attach|logs|stop --help`): `--bg` "continues that session in the background under the same ID with `--resume <session-id>`"; `claude logs <id>` prints recent terminal output; `claude stop <id>` stops it, "conversation is kept; resume later with `claude attach <id>`". Whether a `--bg` session survives the launching shell, and whether it can be compacted externally, remain UNVERIFIED.

## Reusable command lines

```
# external compaction (cold-prompt-free, any session id):
claude -p --resume $SID --model $M "/compact <instructions>" </dev/null            # stdout empty, exit 0
claude -p --resume $SID --output-format stream-json --verbose "/compact ..." </dev/null   # emits compact_boundary with pre/post tokens
# provoke / tune auto compaction:
claude -p --autocompact 100k ...                       # window 100k..1M or `auto`
# hooks without touching global settings:
claude -p --settings /path/settings.json --allowedTools Bash ...
```
Hook that turns a handover file into compaction instructions (PreCompact, exit 0, stdout = instructions): `cat >/dev/null; cat /path/to/handover.txt`.

## Surprises

1. **File re-attachment after compaction**: the compaction re-injects the most recent Read files as `attachment` (`type:"file"`) entries after the summary (fa-fd, 4 files here). Facts that lived in files (C, D) therefore survived `/compact keep fact A and B only`; only chat-held facts are filtered by the instructions. Controllers that need a true reset must not rely on instruction text to drop file content (and the re-attached content costs context after compaction). Also `deferred_tools_delta`, `skill_listing` (34 KB!), `mcp_instructions_delta`, `agent_listing_delta`, `environment`, `model` attachments are re-emitted after each compaction (fixed overhead of about 20k tokens in this environment).
2. The summary is generated WITHOUT seeing the preserved tail messages (the summary says "The summary was written without seeing them"); the last user message(s) are kept verbatim after it (`preservedMessages`).
3. `-p` `/compact` prints nothing, so a wrapper must read the transcript or use stream-json to confirm it ran.
4. Auto compaction fires far below the nominal window (about 70k for `--autocompact 100k`) and can fire repeatedly in one turn; PreCompact fires for each, the handover applied every time.
5. `PostCompact.compact_summary` != stored summary (includes `<analysis>` block, different wrapper).
6. `run_in_background` makes `-p` wait; killing claude kills the task, but plain `nohup` children survive.
7. A harness-injected `<total_tokens>N tokens left</total_tokens>` budget reminder (15M start) exists in `-p` sessions; it is a cumulative budget, not context fill.
8. Hook input says `permission_mode:"bypassPermissions"` in these `-p` runs although only `--allowedTools Bash` was passed.

## UNVERIFIED / not done

- E6 entirely (needs interactive trust acceptance); `claude attach`/`respawn` on anything.
- Interactive-session behaviour: whether an external process can compact a RUNNING interactive session; idle compaction; a finished background Bash waking an idle interactive session; Ctrl-C/exit effects on bg tasks.
- Agent-tool subagent survival when the controller is terminated and resumed (R12.5); SIGKILL / host restart survival of nohup children.
- PreCompact non-zero exit semantics (block vs ignore); 10,000-char cap on PreCompact stdout and on `additionalContext`; behaviour of UserPromptSubmit/SessionStart injection (only PostToolUse tested).
- Cache-cost of repeated injection (only chars/entry measured; cache read/creation deltas per injection not isolated); additionalContext persistence across a compaction (whether it is summarised or dropped).
- Quality comparison of the official summary vs a mechanical `nyxloom extract` compaction (R12.9): only the shape and size of the official summary were recorded (2.4-8.7 KB, 9-section readable text, steerable by instructions, hook-steerable).
- Codex/opencode/pi/reasonix equivalents (Claude-only experiment).
- Live context-fill inside a hook: confirmed not provided in hook stdin; status-line bridge not tested.
