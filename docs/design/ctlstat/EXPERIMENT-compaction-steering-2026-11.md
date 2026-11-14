<!-- origin: 2026-11-14; status: design -->
<!-- relation: ctlstat steering experiment (follows EXPERIMENT-compaction-hooks.md, RESEARCH-harnesses.md, POC-pi.md, POC-opencode.md) -->
<!-- Evidence scrub 2026-11-14: scratch directories, session and thread ids, gateway provider names, hosts and credentials were replaced by placeholders; command output and verdict text are otherwise unchanged. -->

**Status: PARTIAL (X1, X2, X4, X5 measured with real models; X3 compaction event UNTESTED: not triggered under four settings; every result is a single observation unless stated)**

# ctlstat experiment: steering compaction from outside, headless controller, other harnesses

Date 2026-11-14. Claude Code `2.1.325` (model `haiku`, resolves to `claude-haiku-5-5`), Codex CLI `0.167.0`, opencode `2.2.6`, pi `1.5.0`. Everything ran in a throwaway scratch tree (`$X`, one subdirectory per experiment), no estate files, no global settings edited (hooks attached with `--settings`; pi and opencode used a scratch config/data directory; opencode ran its own private server on a loopback port that was stopped afterwards). Claude spend about USD 0.03 in total; the gateway requests for X3 and X4 were about 15 (cap given for X3+X4), X5 a few dozen (not counted exactly) because a deliberately tiny compaction threshold made pi compact after every turn (see X5).

Purpose: decide whether the controller can run headless under our own supervisor with compaction steered from outside, or must stay in the interactive TUI with hooks. Builds on `EXPERIMENT-compaction-hooks.md` (E1-E6), `RESEARCH-harnesses.md`, `POC-pi.md`, `POC-opencode.md`, `REQUIREMENTS.md` 12.

## Questions that were already answered (not redone)

| Question | Answer and where |
|---|---|
| Can `claude -p --resume <id> "/compact <instr>"` compact externally? | PASS, E1 in `EXPERIMENT-compaction-hooks.md` (one process per message). X1 below only adds the long-lived stream-json process. |
| Does PreCompact stdout steer the summary (manual and auto)? | PASS, E2 (8/8 compactions). X2 repeats it once inside the stream-json process. |
| Does PostToolUse `additionalContext` reach the model, with change-only throttling? | PASS, E3 (wrapper cost payload + 79 chars, no token field in hook input). Not repeated. |
| Do the transcript and the stream carry pre/post tokens of a compaction? | PASS, E4 (`compact_boundary` with `preTokens`/`postTokens`). X1 saw the same in the stream. |
| Do detached children survive the controller? | E5 (nohup/setsid yes, `run_in_background` no). Not repeated. |
| pi: RPC `compact` with `customInstructions`, `session_before_compact` supplying a summary, wake, resume | PASS against a fake model in `POC-pi.md`; X5 repeats the compaction parts with a real model. |
| opencode: `compaction` hook, `POST /compact`, SSE compaction events, restart recovery | PASS against a fake model in `POC-opencode.md`; X4 repeats compact and the SSE events with a real model. |
| Codex: compaction extension points | docs and source reading only in `RESEARCH-harnesses.md` (`ThreadCompactStartParams{thread_id}`, PreCompact stdout ignored); X3 is the first execution. |
| Agent SDK facts (resume, hooks per language, permission order, headless background wait, `--bg`) | docs only, see 'Hook behaviour in the live controller session' item 10. |

## Verdict table

| Exp | Question | Verdict |
|---|---|---|
| X1 | One long-lived `claude -p --input-format stream-json` process driven by a script: multi-message, `/compact <instr>` as a stream message, send during a running turn, SIGINT | **PASS** (compaction works from the stream with `compact_boundary` and token counts; a message sent mid-turn is queued and delivered at the next tool-result boundary; SIGINT ends the process with rc 0, it does not just interrupt the turn) |
| X2 | Hooks under that process: which fire, which input fields, which outputs steer | **PASS** (PreCompact stdout steered, SessionStart[compact] `additionalContext` reached the model after the summary, UserPromptSubmit `additionalContext` worked, Stop `decision:block` continued the turn, Stop `systemMessage` appears as a stream event; `transcript_path` is the right file for the main agent) |
| X3 | Codex `exec --json` with a small auto-compact limit; compaction event shape; `exec resume` | **PARTIAL**: key found (`model_auto_compact_token_limit`), `exec resume` PASS (3 resumes); compaction event **UNTESTED** (no compaction item observed with limits 4000, 12000, 2000 and `model_post_turn_compact_threshold_percent=1`) |
| X4 | opencode: compaction triggered and observed | **PASS** (real model: `POST /api/session/:id/compact` produced a compaction in about 4 s, SSE `session.compaction.started/delta/ended` plus `session.usage.updated`, next prompt answered from the summary); the config key `compaction` and `opencode run` itself have no trigger (UNTESTED for `run`) |
| X5 | pi: json and rpc compaction events, `session_before_compact` reading a handover file | **PASS in rpc mode** (events `compaction_start`/`compaction_end`, manual `customInstructions` obeyed by a real model, auto `threshold` compaction fires `session_before_compact` without instructions, extension supplied a summary from a file with zero model calls and the next answer used it); **json (`-p`) compaction event UNTESTED** (session was too small to compact at that point) |

## Hook behaviour in the live controller session (2026-11-14, Claude Code 2.1.325)

Single observations from the operator's own interactive controller session, recorded as given; the measured experiments below are separate (headless) and do not cover item 7.

1. PostToolUse hooks with `additionalContext` reach the model with no extra roundtrip and fire inside Agent-tool subagents (`agent_id`/`agent_type` in the input).
2. INSIDE A SUBAGENT the hook input's `transcript_path` is the PARENT session's transcript, not the subagent's: its own file is `<parent dir>/<session_id>/subagents/agent-<agent_id>.jsonl`. Reporting the parent's size caused a false "context past 200k" note that made a subagent stop early.
3. A Stop hook printing `{"systemMessage": "[HH:MMZ ctx NNk]"}` shows in the interactive scrollback after each reply, user-visible only.
4. A `statusLine` command works for time and context size.
5. Hook stdout/`additionalContext` is capped at 10,000 characters (docs).
6. User settings already carry `autoCompactWindow` per model.
7. PENDING at the next controller compaction (not claimed): placement of SessionStart[compact] output relative to the summary, and whether PreCompact stdout reaches the summary (probe token `READ-ORDER-PROBE-7`). The headless measurements X1/X2 answer both for `-p` only.
8. pi: `pi -p --mode json` hangs forever unless stdin is closed (`< /dev/null`); its JSONL events are `session`, `agent_start`, `turn_start`, `message_start/update/end`, `tool_execution_*`, `turn_end`, `agent_end`, `agent_settled`; every assistant message carries `usage{input,output,cacheRead,totalTokens}` and `stopReason`; `models.json` providers take `apiKey` as `"$ENVNAME"`.
9. Open Design gateway: no usage/quota endpoint (all probed paths 404); a 402 body is `{"error":{"code":"insufficient_balance","details":{"coding_plan_exhausted":true,"reset_at":"<UTC>"},...}}` and Codex surfaces it as plain text `unexpected status 402 Payment Required: [code=insufficient_balance] ... resets at <UTC>` in top-level `error`/`turn.failed` events.
10. Claude Agent SDK docs (sessions, hooks, permissions, agent-view and headless pages under `code.claude.com/docs/en/agent-sdk/*.md`, `headless.md`, `agent-view.md`): `ClaudeSDKClient.query()` multi-turn or TypeScript `continue:true`; resume by `session_id`; hooks PreToolUse/PostToolUse/Stop/SubagentStart/SubagentStop/PreCompact/PermissionRequest/Notification in both SDKs, SessionStart/SessionEnd/PostCompact TypeScript-only; PostToolUse `additionalContext` supported; permission evaluation order hooks, deny, ask, mode, allow, then `canUseTool` callback; headless `-p` waits for background commands/subagents/Monitor up to 10 min (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`); `claude --bg`, `claude agents --json`, attach is terminal-only (no programmatic attach); NOT documented: sending input into a RUNNING turn via `--input-format stream-json`, `/compact` as a prompt in SDK streaming mode (X1 measures both for the CLI process).
11. Agent SDK auth: docs say "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication methods". That is about products offering subscription login; whether a harness such as pi or opencode may use a user's own subscription is NOT settled by the docs (a third-party article said blocked: unverified).

## X1 long-lived stream-json process

Command shape (a ~60-line Python driver, stdlib `subprocess`, one reader thread logging every stdout line with a timestamp):
```
claude -p --input-format stream-json --output-format stream-json --verbose --model haiku \
       --settings $X/settings.json --allowedTools Bash          # cwd = scratch dir, stdin kept open
# send one JSON line per message:
{"type":"user","message":{"role":"user","content":"<text>"}}
```
Scenario `basic`: message 1 (remember two facts), wait for the `result` event, message 2 (one Bash call), then the stream message `/compact keep ALPHA-7 only, drop FOO-9`, then message 3 (recall).

Observations:
- Each message ends with its own `result` event (`subtype:"success"`, `num_turns` 1 or 2); the process stays alive between messages; the driver closing stdin ends it with rc 0 and a `SessionEnd` hook (`reason:"other"`).
- The `/compact` stream message works as in E1: event order `system/status`, `system/hook_started+hook_response` (SessionStart, source `compact`), `system/status`, `system/init`, `system/compact_boundary` with `compact_metadata{trigger:"manual", pre_tokens:27560, post_tokens:6823, cumulative_dropped_tokens:20737, duration_ms:5286, preserved_segment, ...}`, a `user` event carrying the summary text, a `user` event `<local-command-stdout>Compacted ...`, and a `result` with an empty `result`, `num_turns` 0 and zero usage (a wrapper must not read the usage of that result as fill). A second run gave 26812 -> 6952. The recall answer after the compaction honoured the instruction ("codeword: ALPHA-7; the secret is not available").
- Per-message usage in the `result` event: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` (fill = the sum of the first three, as in E4).
- Sending a message WHILE a turn runs (`interleave`: message A asks for a 15 s `sleep` in Bash; message B sent 5 s later): B is accepted, written to the transcript as `queue-operation enqueue`, delivered at the next tool-result boundary as an `attachment` of type `queued_command` and `queue-operation remove`; it does NOT start its own turn and produces NO separate `result` (one `result`, `num_turns` 2). The model answered B only (it did not also produce A's reply), a single observation of model behaviour. What happens when no tool boundary comes before the turn ends was not tested.
- SIGINT idle (`sigint`, first run): process exits rc 0 immediately, `SessionEnd`. SIGINT during a running Bash tool (`sleep 20`, signal after 6 s): the tool result becomes "The user doesn't want to proceed with this tool use", a `user` event `[Request interrupted by user for tool use]`, a final `result` with `subtype:"error_during_execution"`, `is_error:true`, `terminal_reason:"aborted_tools"`, then the process EXITS rc 0. So SIGINT is "end the process", not "interrupt the turn and keep the stream"; continuation needs a new `--resume <session_id>` process. The Bash child (`sleep 20`) was still alive about 2 s after claude exited (single observation).
- The Bash tool of this environment rejected a `sleep 40` command ("Blocked: sleep 40 followed by ... use Monitor"), so a long foreground tool must be built from a shorter command or a background command.
- Which hook events appear in the stream without extra flags: only SessionStart `system/hook_started` and `hook_response`; the other hooks ran (logged by the script) but were not echoed as stream events, except Stop `systemMessage` (X2).

Verdict: **PASS**. Not tested: `--include-partial-messages`, a message sent when no tool boundary follows, hours-long soak, the SDK client classes themselves (docs only).

## X2 hooks under the same process

Settings: a passive logging command for SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStart, SubagentStop, PreCompact, PostCompact, Notification (run `basic`); a steering script for SessionStart[compact], UserPromptSubmit, PostToolUse, Stop, PreCompact (run `x2`). Hook scripts printed JSON as in E3 (`{"hookSpecificOutput":{"hookEventName":"<Event>","additionalContext":"<text>"}}`).

Observations (headless stream-json):
- Fired: SessionStart (`source` `startup`, then `compact`; the latter also carries `model`), UserPromptSubmit (carries `prompt`, `prompt_id`), PreToolUse, PostToolUse (`tool_name`, `tool_input`, `tool_response`, `tool_use_id`, `duration_ms`), Stop (`stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons`), PreCompact (`trigger`, `custom_instructions` = the `/compact` argument), PostCompact (`trigger`, `compact_summary`), SessionEnd (`reason`). Not fired in this run: SubagentStart, Notification (no occasion).
- `SubagentStop` fires once per manual compaction (the compaction runs as an internal agent: `agent_id` set, `agent_type` empty, and a separate `agent_transcript_path` of the form `<parent dir>/<session_id>/subagents/agent-<agent_id>.jsonl`; no `SubagentStart` for it). Together with item 2 above this means subagent-aware hooks should use `agent_transcript_path` where the event offers it.
- `transcript_path` is correct for the main agent in headless mode: the session file `<claude projects dir>/<cwd-slug>/<session_id>.jsonl`, which the stream's `session_id` also names.
- PreCompact stdout steered the summary: a printed `REMEMBER: the secret word is OTTER; drop everything about cats.` was honoured (recall answered OTTER, the cat name was withheld) and the hook text is echoed in the `<local-command-stdout>` user event (`Compacted PreCompact [<hook>] completed successfully: <stdout>`).
- SessionStart[compact] `additionalContext` (`SS-COMPACT-PROBE-ZEBRA`) reached the model; in the transcript it lands AFTER the summary entries and the re-attached `deferred_tools_delta`/`skill_listing`/`environment`/`model` attachments (`hook_success` then `hook_additional_context` entries), before the next user prompt. The hook's raw JSON is also echoed in the stream `system/hook_response` event.
- UserPromptSubmit `additionalContext` (`UPS-MARK-1`) reached the model. A PostToolUse marker injected before the compaction (`PTU-MARK-1`) was NOT listed by the model after the compaction (single observation: injected context is not kept verbatim through a compaction; E3 had left that open).
- Stop hook printing `{"decision":"block","reason":"..."}` (once, via a flag file): the harness appended a user text `Stop hook feedback: <reason>` and the same turn continued; the final `result` came from the second assistant message (`BLOCKED`, `num_turns` 2); the stream carried a `system/notification` `stop-hook-error`. A Stop hook printing `{"systemMessage":"..."}` produced a stream event `{"type":"system","subtype":"informational","content":"Stop says: <text>","level":"notice"}` before each `result`, and a `hook_system_message` attachment in the transcript. So a status line for a headless UI can be taken from that event instead of the TUI scrollback.
- Not measured: whether PreCompact exit code 2 blocks compaction; the 10,000-char cap (docs only); `asyncRewake`.

Verdict: **PASS**.

## X3 Codex exec

Config key: `model_auto_compact_token_limit` (also present in the binary's config schema: `model_auto_compact_token_limit_scope`, `model_post_turn_compact_threshold_percent`, `model_context_window`, `compact_prompt`; the accepted values of `_scope` were not found, the Codex docs available locally list none). The model catalog entry of the test model carries `context_window` 328000 and `effective_context_window_percent` 95 and no `auto_compact_token_limit`.

Command shape (profile home and sqlite home exported as in the estate's route wrapper, API key sourced from the user env file without printing):
```
CODEX_HOME=<route-home> CODEX_SQLITE_HOME=<shared-sqlite-home> \
codex exec -m deepseek-v4.1-flash-eco -s <sandbox> --json --skip-git-repo-check \
  -c model_auto_compact_token_limit=<N> "<task>" < /dev/null
codex exec ... -c model_auto_compact_token_limit=<N> resume <thread_id> "<prompt>" < /dev/null
```
Observations:
- `-s read-only` did NOT work in this devcontainer: every shell item failed with `bwrap: Can't mount proc on /proc: Operation not permitted` (exit 1), and the model retried. The remaining runs used `-s danger-full-access` in the scratch directory with read-only tasks (cat of three generated 8 KB files).
- Four settings were tried, each on a task or resume that kept the context around 9-16k tokens: `model_auto_compact_token_limit` 4000, 12000, 2000 and `model_post_turn_compact_threshold_percent=1`. The `--json` stream showed only `thread.started`, `turn.started`, `item.started/completed` (`command_execution`, `agent_message`) and `turn.completed` (`usage{input_tokens,cached_input_tokens,cache_write_input_tokens,output_tokens,reasoning_output_tokens}`). No compaction item or event appeared, and no size drop. `turn.completed.usage` grew monotonically across resumes (37,072 -> 46,172 -> 60,130 -> 74,207 input tokens), which looks like a thread-cumulative counter; the two last resumes added about 14k each versus 9k for the one before, which could mean one extra request per turn, but this cannot be told from the JSON alone and the rollout files under the Codex home were deliberately not read.
- `codex exec resume <thread_id>` worked every time (3 resumes, the model answered facts from the earlier turn).
- Whether the limit is ignored below some floor, applies only to a different token count, needs the `_scope` key, or compaction ran silently: UNKNOWN. Gateway quota (about 15 requests) did not allow a larger sweep.

Verdict: **PARTIAL**: key name and `exec resume` established; compaction event shape UNTESTED. A next try should use a task that really grows the context (tens of thousands of tokens) with the key at a value just below the measured per-request size, and read the compaction item shape from the app-server (`contextCompaction` thread item exists in the binary) instead of `exec --json`.

## X4 opencode

Command shape (private server so the production service and database stay untouched; provider config read from the user config directory, env key sourced from the user env file; scratch `XDG_DATA_HOME/STATE_HOME/CACHE_HOME`):
```
opencode serve --hostname 127.0.0.1 --port <p>            # OPENCODE_SERVER_PASSWORD set, plugin dir empty
curl -N -u opencode:<pw> http://127.0.0.1:<p>/api/event > sse.txt &
opencode run --server http://127.0.0.1:<p> -m <od-provider>/deepseek-v4.1-flash-eco --format json --archived "<prompt>"
curl -X POST -u opencode:<pw> http://127.0.0.1:<p>/api/session/<id>/compact -d '{}'
opencode run --server ... --session <id> -m ... --format json "<recall question>"
```
Observations:
- `run --format json` emits one JSON object per line with `type` `step_start`, `text`, `step_finish` (`part.tokens{input,output,reasoning,cache{read,write}}`, `part.reason`), each carrying `sessionID` (usable to find the session for the HTTP call). It prints no compaction event of its own.
- `POST /api/session/:id/compact` with `{}` returned immediately the queued message `{"type":"compaction","payload":{},"delivery":"steer"}`; the compaction ran with the real model in about 4 s. SSE events seen: `session.inbox.enqueued`, `session.execution.started`, `session.compaction.started`, about 40 `session.compaction.delta` (streamed summary text), `session.compaction.ended` (`reason:"manual"`, `model`, full `text`), `session.usage.updated` (session-cumulative totals: 3656 input before, 4299 after, i.e. the compaction call added 643 input), `session.execution.succeeded`. `GET /api/session/:id/context` then returned the `compaction` message (`status`, `reason`, `summary`, and its own `tokens`: input 643, output 139, reasoning 120, cache read 7680) plus an `idle` marker.
- Summary format is the 7-section template (Objective, Requirements, Decisions, Work State, Next Move, Relevant Files, Important Context); it kept the codeword; a following `opencode run --session <id>` answered the codeword from the summary (one request).
- The compact endpoint has no instruction field (as in `POC-opencode.md`; not retested). Steering stays with the plugin `compaction` hook plus a handover file (PoC).
- Not tested: the config `compaction` block (`auto`, `buffer`, `keep.tokens`) with the real model (the fake-model PoC covered auto and mid-turn), `opencode run` with a `/compact` text, session commands via `--session`.

Verdict: **PASS** (HTTP trigger plus SSE observation with a real model).

## X5 pi

Setup: a scratch agent directory (`PI_CODING_AGENT_DIR`) holding a copy of `models.json` (providers use `"apiKey":"$ENVNAME"`, so the copy holds no secret), its own `settings.json` (`compaction.reserveTokens` set so the threshold is `contextWindow - reserveTokens`, `keepRecentTokens` small) and an extension `extensions/ctxsteer.ts` that logs `session_before_compact` / `session_compact` and, when a `mode` file says `supply`, returns `{compaction:{summary:"## Handover (from file)\n<handover.md>", firstKeptEntryId, tokensBefore}}`. Provider/model `<od-provider>/deepseek-v4.1-flash-eco`.

Command shapes:
```
pi -p --provider <od-provider> --model deepseek-v4.1-flash-eco --mode json --session-dir $X/sess "<prompt>" < /dev/null
pi --mode rpc --provider ... --model ... --session $X/sess/<file>.jsonl      # stdin/stdout JSONL, driven by a script
{"type":"compact","customInstructions":"<text>"}
```
Observations:
- `-p --mode json` events in order: `session`, `agent_start`, `turn_start`, `message_start/end` for the system and user messages, `message_start`, `message_update` x N, `message_end` (assistant, with `usage.totalTokens` = context fill, `stopReason`), `turn_end`, `agent_end`, `agent_settled`. It does not exit if stdin stays open.
- Compaction is refused for small sessions: RPC `compact` returned `success:false` with `Nothing to compact (session too small)` (5 messages, 1771 context tokens, `keepRecentTokens` 200 and 50) and, right after a compaction, `Already compacted`. The `compaction_start`/`compaction_end` events are still emitted for the failed attempt (`compaction_end` with `errorMessage`, no `result`). So the json-mode auto event could not be observed with the first two prompts; **json (`-p`) compaction event shape: UNTESTED**.
- After a tool call (a `cat` of an 8 KB file) the session was compactable. With `reserveTokens` such that the threshold was about 1000 tokens, pi compacted automatically after EVERY turn (6 `threshold` compactions in one run) and the model, having lost the task details in each summary, started to explore the working directory and even read the handover file by itself: a tiny threshold is a loop hazard, not a test setup to copy. Event shapes in rpc mode: `{"type":"compaction_start","reason":"threshold"|"manual"}` and `{"type":"compaction_end","reason":...,"aborted":false,"willRetry":false,"result":{summary,firstKeptEntryId,tokensBefore,estimatedTokensAfter,usage,details}}`; the RPC response of `compact` carries the same `data`. The summary request's `usage` (e.g. input 2439, output 2631) comes with it, so the cost record needs no log parsing.
- `session_before_compact` fired for `reason:"threshold"` with `customInstructions:null` and for `reason:"manual"` with the RPC `customInstructions` string; `tokensBefore` and `willRetry` are in the event. The extension could read the handover file in both cases (97 chars read), so auto compactions CAN be steered by a file convention (as `POC-pi.md` found with the fake model).
- Real-model obedience: manual `customInstructions` "Mention the word WALRUS in the Critical Context section." appeared in the summary; `tokensBefore` 3644 -> `estimatedTokensAfter` 3117 (the kept tail dominated in this tiny session).
- Supply mode: with `mode=supply` the extension returned the handover as summary; `session_compact` reported `fromExtension:true`, the compaction entry has `fromHook:true`, no `usage` (zero model calls; `tokensBefore` 3564 -> `estimatedTokensAfter` 1659), and the next real-model answer used it ("PELICAN is mentioned; secret ZETA-8 is resolved and must be dropped").
- Transcript: each compaction is a `compaction` entry (`summary`, `firstKeptEntryId`, `tokensBefore`, `usage`, `fromHook`, `details{readFiles,modifiedFiles}`), 8 in the run.
- `get_session_stats.contextUsage.tokens` is `null` right after a compaction until the next assistant message (as in the PoC).

Verdict: **PASS in rpc mode**; json-mode compaction event UNTESTED.

## Surprises

1. A message sent into a running stream-json turn is merged into the turn at the next tool boundary and gets no separate `result`; a supervisor that counts `result` events per message will be off by one.
2. SIGINT mid-tool ends the whole headless process (rc 0), so "interrupt, then send the next message" needs resume-by-session-id, not the same process.
3. `SubagentStop` fires for the internal compaction agent; hook consumers that treat it as "a user subagent finished" will misfire on every `/compact`.
4. `Stop` `decision:block` is a working "do one more thing before ending" lever, and `systemMessage` is a stream event in headless mode: both are candidates for the ctlstat status channel.
5. Codex `--json` shows no compaction, and a very small `model_auto_compact_token_limit` had no visible effect; this is the one harness where the compaction trigger and event remain unproven.
6. pi's small-session guard (`Nothing to compact`, `Already compacted`) and its tiny-threshold loop (X5) mean thresholds must be sized against the system prompt plus tools (about 1.7k tokens here), and `keepRecentTokens` against the session.
7. `codex -s read-only` fails inside this devcontainer (nested bwrap); worker runs here need an explicit sandbox choice.

## Decision inputs

Question: can the controller run headless under our supervisor with steered compaction, and what is proven?

Proven (headless Claude Code 2.1.325, one long-lived `-p --input-format stream-json --output-format stream-json --verbose` process, or one `-p --resume` process per message):
- Drive: multi-message conversation, one `result` per message (queued mid-turn messages merge into the running turn); `session_id` in every event, so a restarted supervisor can resume.
- Compaction on demand: a `/compact <instructions>` user message in the stream; `compact_boundary` with `pre_tokens`/`post_tokens`; PreCompact stdout appended to the instructions (handover file as steering text); SessionStart[compact] `additionalContext` as a post-compaction re-attach channel placed after the summary.
- Pressure notes and boundaries: PostToolUse and UserPromptSubmit `additionalContext`, Stop `decision:block` to demand a boundary action, Stop `systemMessage` as a stream event for a status line; `transcript_path` correct for the main agent, `agent_transcript_path` on SubagentStop; fill from the usage fields of the stream `result` and the transcript (E4).
- Process hygiene: detached `nohup`/`setsid` children survive (E5); SIGINT ends the process cleanly and resume works.

Not proven (UNTESTED): the Agent SDK client classes (docs only: `ClaudeSDKClient`, TypeScript-only SessionStart/SessionEnd/PostCompact hooks); `/compact` as an SDK `query()` prompt; a multi-hour stream-json soak (memory, rate-limit events); auto-compaction inside the stream-json process (E2 proved it for `-p`); PostToolUse hooks inside Agent-tool subagents in headless mode; permission prompts for tools not pre-allowed (the runs saw `bypassPermissions` with `--allowedTools`); whether the subscription may be used when a supervisor of ours launches the first-party `claude` binary (the SDK docs forbid third-party products offering subscription login; our own use is not addressed, so it needs an operator decision); the `claude --bg` route (trust prompt, E6).

What the headless controller loses compared with the TUI:
- The operator cannot type into or attach to the live session; `claude attach` is terminal-only and applies to `--bg` sessions (E-020), so an attach UI is ours to build (or the operator talks to the supervisor, which sends stream messages that are queued into the running turn).
- Interactive permission prompts, `/` commands that are interactive-only, `ctrl+o` transcript view, the status line and the scrollback `systemMessage`: each needs a supervisor-side rendering of the stream events (`system/informational`, `system/notification`, `assistant` text, tool events).
- SIGINT is not "interrupt this turn": it ends the process (resume needed); the TUI keeps the session.
- The sturdiness of the TUI session as one long process is replaced by a supervisor that must restart `claude` on crash and resume by id.

Other routes, one line each with the measured facts:
- pi (`--mode rpc`, one client over stdin/stdout): the whole steering loop works with a real DeepSeek model (manual `customInstructions` obeyed, threshold compactions steerable through a handover file read by an extension, supplied summaries with zero model calls, usage per compaction in the response). Trade-offs: no Claude models without API-key billing (subscription use by a third-party harness is not settled by the docs), pi summaries do not reuse the warm prompt cache, no server and no attach UI, the extension API moves between minors, and thresholds need sizing (loop hazard, X5).
- opencode server (HTTP): compaction trigger and SSE events proven on a real model; no instruction field, so steering goes through the plugin hook and a file; the server keeps sessions when a client dies, which is the only route that gives the operator an attach UI without our own build.
- Codex: stays the worker harness; trigger key found but no compaction event observed yet (X3).

Net: a headless Claude Code controller under our supervisor is feasible on measured facts for drive, compaction steering, injection and status; the open items are an operator decision on subscription use and the missing UI. The TUI-with-hooks route keeps the operator's direct control and loses only the automatic supervisor-triggered compaction (needs an external `claude -p --resume ... /compact`, which E1 proved for an idle session; whether it can run on a session the TUI still holds open is untested).

## UNVERIFIED / not done

- Everything listed under 'Not proven' above; item 7 of the live-session section.
- Codex compaction event and trigger (X3), pi json-mode compaction event, opencode `compaction` config with a real model, `opencode run` slash commands.
- Whether queued mid-turn messages are delivered when no tool call follows; the behaviour with several queued messages.
- Cost of repeated injection and of the queue path in cache terms (not isolated).
- Hook behaviour of the Agent SDK (TypeScript/Python) clients versus the CLI flags used here.
