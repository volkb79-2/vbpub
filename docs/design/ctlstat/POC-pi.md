<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ctlstat-POC-pi.md -->
<!-- relation: ctlstat pi PoC -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

**Status: COMPLETE** (all 7 goals exercised against a deterministic fake model; model-dependent claims marked "not shown with a fake model")

# ctlstat PoC: pi coding agent (earendil-works/pi) as an extensible harness

Date 2026-11-09. Elapsed about 1 h. Follows the PoC-A proposal in `ctlstat-RESEARCH-harnesses.md`; requirements in `ctlstat-REQUIREMENTS.md` 12-12.5; what pi must replicate comes from `ctlstat-EXPERIMENT-compaction-hooks.md` (E1-E5).

## Verdicts

| # | Goal | Verdict | Evidence (details below) |
|---|---|---|---|
| 1 | Session log is JSONL with per-message usage; context fill via tiny parser | **PASS** | `ctxfill.mjs` (28 lines) reads the JSONL only. Last assistant `usage.totalTokens` = 1696 equals RPC `get_session_stats.contextUsage.tokens` = 1696. Handles compaction (fill is `null` until the next assistant message). |
| 2 | Extension injects CHANGE-ONLY status diff after tool calls | **PASS** | 4 bash calls, status changed once between: injected on call 1 and 3, nothing on 2 and 4 (ext.log). Injection is persisted in the session as an extra text part of the `toolResult` and reached the model (fake model quoted the last marker). |
| 3 | Unattended self-compaction: agent writes handover and ends; external script resumes over RPC, `compact` with handover as `customInstructions`, session continues | **PASS** (plumbing) | Process 1 wrote `handover.md` and exited 0. Process 2 resumed the file, sent `{"type":"compact","customInstructions":<handover>}`. The summariser request contained `Additional focus: <handover>`. After compaction the probe saw `f_ALPHA-7` (kept) and no `f_BETA-9` (dropped; it was visible before). Next prompt worked. Whether a REAL model honours the instructions: not shown with a fake model (the fake obeys KEEP/DROP by script). |
| 4 | `session_before_compact` supplies own mechanical summary | **PASS** | Hook returned `{compaction:{summary: skeleton+handover, firstKeptEntryId, tokensBefore}}`. Entry has `fromHook:true`, `session_compact.fromExtension=true`, zero summariser model calls (fake-llm request count +3 = my three probe prompts only), RPC response has no `usage`. Hook also fires for `reason=threshold` (auto) in RPC mode and for `ctx.compact()`. |
| 5 | Wake/continue an idle session by an external event | **PASS** | (a) Live idle process: an external `echo >> trigger.txt` made the extension call `pi.sendMessage(..,{triggerTurn:true})`; RPC stdout emitted `agent_start...agent_settled` with NO stdin command; custom message persisted. (b) RPC `prompt` to the idle process: `disposition:"started"`. (c) Stopped session: new process on the same file (goal 6). Extension-side compaction by event (`ctx.compact()` from the trigger) also worked. |
| 6 | Headless resume after the process was killed | **PASS** | SIGKILL while idle, SIGKILL mid bash tool call, then new `pi --mode rpc --session f.jsonl` and `pi -p --session f.jsonl`: full history present (fact visible). Dangling `toolCall` without result: pi sends a synthetic `tool` message `"No result provided"` on resume (seen in the request body), nothing is written to the JSONL. |
| 7 | Detached jobs started by the agent survive session end | **PASS** | Three jobs (plain `( ... ) &`, `nohup`, `setsid nohup`) started from one bash tool call; the tool returned immediately. All three finished after: graceful stdin close, SIGKILL of pi, SIGTERM to pi's process group. Even the plain background job survived (pi's bash tool does not put it in pi's group). Container stop would of course kill them. |

Not shown (needs a real model or UI): summary quality and instruction obedience; prompt-cache reuse (pi docs say summarisation requests disable cache writes, so it is NOT the warm-cache path of Claude `/compact`); multi-client; TUI behaviour; Anthropic-subscription use.

## Setup (all inside one disposable container)

- Host side: one container `poc-pi-main` (removed at the end), image `node:26.12.1-trixie-slim` (already local), `--memory 1g --cpus 1 --pids-limit 512 --user 1003:1003`, one bind mount of the scratch dir `.../scratchpad/poc-pi/` (via its HOST path, see memory devcontainer-docker-environment) at `/work`, default bridge network only, no privileged, no secrets. Peak memory seen 46 MiB.
- Versions: pi `@earendil-works/pi-coding-agent` **1.5.0** (`npm install --prefix /work/pi @earendil-works/pi-coding-agent@1.5.0`, installed from the registry inside the container), node **v26.12.1**. No python in the image; drivers are node.
- Run (everything from `/work`): `node fake-llm.mjs &` (port 8089), then `node test_a.mjs` (goals 1,2), `test_b1.mjs` + `test_b2.mjs` and `MECH=1 node test_b2.mjs` (goals 3,4), `test_g5.mjs`, `test_g6.mjs`, `test_g7.mjs close|kill9|killpg`, `test_x.mjs`/`test_x1.mjs` (extras). pi is always launched as `node /work/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js --mode rpc --session <file>` with `HOME=/work/home PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1`.
- Files (scratch dir `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/poc-pi/`): `fake-llm.mjs` (92 lines), `rpc.mjs` (RPC driver, 28 lines), `ctxfill.mjs`, `test_*.mjs`, `home/.pi/agent/{models.json,settings.json,extensions/ctlstat.ts}`, `sessions/*.jsonl` (evidence), `state/ext.log`, `fake-llm.requests.jsonl` (every request body the model server saw).

### Fake model server
OpenAI-compatible `POST /v1/chat/completions`, streaming SSE with tool-call deltas and a final usage chunk. Scripted by the last user message: `RUN: <cmd>` (one bash tool call, then `OK: <output>`), `MULTI: a ;; b`, `SAY: text`, `WHAT FACTS?` (answers which `FACT-X-n` tokens, summary and `[CTLSTAT]` markers are present in the context it was sent: a deterministic probe of what is in the context), and a summariser mode (recognised by the summarisation system prompt) that returns a fixed summary containing the facts whose NAME is listed on the `KEEP:` line of the `Additional focus:` text and not on `DROP:`. Prompt tokens = chars/4 of the request. pi config for it (`models.json`): provider `fake`, `api:"openai-completions"`, `baseUrl:"http://<host-address>:8089/v1"`, a literal dummy `apiKey`, one model with `contextWindow:32000`; `settings.json`: `defaultProvider/defaultModel`, `compaction:{enabled:false, keepRecentTokens:150}` (see surprises: small keepRecentTokens is needed for tiny test sessions).

## Extension that worked (`~/.pi/agent/extensions/ctlstat.ts`, 66 lines, TS loaded as is, no build)

```ts
// Goal 2: change-only injection after tool calls (state = last injected value, persisted in a file)
pi.on("tool_result", async (event, _ctx) => {
  const cur = read(STATE_FILE), last = read(LAST_FILE);
  if (!cur || cur === last) return;                       // nothing when unchanged
  fs.writeFileSync(LAST_FILE, cur);
  return { content: [...event.content, { type: "text", text: `\n--- [CTLSTAT] ${cur} ---` }] };
});
// Goal 4: own (mechanical) summary instead of the model summary
pi.on("session_before_compact", async (event, _ctx) => {
  const { preparation, customInstructions, reason } = event as any;   // reason: manual | threshold | overflow
  const skeleton = ["## [CTLSTAT mechanical skeleton L0]",
    `messages summarised: ${preparation.messagesToSummarize.length}`, `tokensBefore: ${preparation.tokensBefore}`,
    "## [L1 handover prose from agent]", read(HANDOVER) || "(none)"].join("\n");
  return { compaction: { summary: skeleton, firstKeptEntryId: preparation.firstKeptEntryId, tokensBefore: preparation.tokensBefore } };
});
// Goal 5: external event wakes an idle session; "compact" event runs ctx.compact with the handover
watcher = fs.watch(TRIGGER, () => { const c = read(TRIGGER); if (!c) return; fs.writeFileSync(TRIGGER, "");
  if (c.startsWith("compact")) { sctx.compact({ customInstructions: read(HANDOVER), onComplete: ..., onError: ... }); return; }
  pi.sendMessage({ customType: "ctlstat-wake", content: `External event: ${c}`, display: true }, { triggerTurn: true }); });
watcher.unref();   // REQUIRED, see surprises
pi.on("session_shutdown", async () => watcher?.close());
```

RPC driver essentials: spawn `pi --mode rpc --session F`, write one JSON object per line, split stdout on LF only, correlate by `id`; a `prompt` response only means accepted (`data.disposition: "started"`), wait for the `agent_settled` event. Compaction: `{"type":"compact","customInstructions":"..."}` returns `{summary, firstKeptEntryId, tokensBefore, estimatedTokensAfter, usage?, details}`; events `compaction_start`/`compaction_end` follow.

## Key evidence

- Goal 2 ext.log: `tool_result bash: injected "build=GREEN jobs=1"`, `unchanged, nothing injected`, `injected "build=RED jobs=2"`, `unchanged ...`. JSONL: toolResult 1 and 3 have a second text part `--- [CTLSTAT] ... ---`, 2 and 4 do not.
- Goal 3: compaction response `tokensBefore 2176 -> estimatedTokensAfter 1560`, summariser usage `{input 1151, output 116, cost 0.0013}` (wall 0.46 s with the fake); `get_session_stats.contextUsage.tokens` is `null` right after compaction until the next assistant message. Probe before: `f_BETA-9,f_ALPHA-7`; after: `f_ALPHA-7`, summary present.
- Goal 4 compaction entry: `"fromHook": true`, summary = our skeleton text; no `usage`.
- Auto-compaction: with `compaction.enabled:true` and a threshold below the session size, `session_before_compact reason=threshold customInstructions=undefined` fired in RPC mode (5 times in a row at an artificially tiny threshold). So an extension must read the handover file itself for auto compactions; only manual/RPC compaction carries `customInstructions`.
- Goal 7 timing: jobs sleep 6 s; checked 8.5 s after session end; all three result files present in all three end modes.

## Session store layout

- Default: `$HOME/.pi/agent/sessions/--<cwd with / replaced by ->--/<ISO-ts>_<uuidv7>.jsonl` (seen: `~/.pi/agent/sessions/--work-ws--/2026-11-09T18-32-39-857Z_01a1c195-....jsonl`). `--session <file>` or `--session-dir` / `PI_CODING_AGENT_SESSION_DIR` overrides. Config dir `PI_CODING_AGENT_DIR` (default `~/.pi/agent`: `models.json`, `settings.json`, `auth.json`, `models-store.json`, `extensions/`). One file per session; no sidecar.
- File: line 1 `{"type":"session","version":3,"id","timestamp","cwd"}`; then entries with `id`/`parentId` (8 hex chars; tree, append-only): `model_change`, `thinking_level_change`, `message` (roles `system` (first request: prompt sections + tool declarations, about 5 KB), `user`, `assistant`, `toolResult`, custom), `custom_message`, `compaction` (`summary`, `firstKeptEntryId`, `tokensBefore`, `usage`, `fromHook`, and a full copy of the system message with tool declarations), and others per docs. Assistant messages carry `usage{input,output,cacheRead,cacheWrite,reasoning,totalTokens,cost{...}}`, `stopReason`, `model`, `provider`, `durationMs`, `responseId`. Context fill = the last assistant `usage.totalTokens` on the active branch after the latest compaction (equal to input+cacheRead+cacheWrite+output here). Our E4 rule for Claude (dedupe by `message.id`) is not needed: one entry per assistant message.
- Env passed to tool commands: `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`, `PI_MODEL` (docs), so a bash-tool `ctlstat` call can find its session without a hook payload.

## Surprises and gotchas

1. An extension `fs.watch()` keeps `pi -p` (print mode) alive forever (my first smoke run hung); fix `watcher.unref()` plus closing in `session_shutdown`. Same for any timer/socket an extension opens.
2. `compaction.keepRecentTokens` (default 20k) means a compaction of a small session summarises nothing; tests need a small value (150 here). Also the cut can fall inside a turn: pi then summarises twice and concatenates ("Turn Context (split turn)"), i.e. two summariser calls per compaction.
3. Project-level `<cwd>/.pi/settings.json` did NOT take effect in my run (auto-compaction stayed off); the same keys in the global `settings.json` worked. Likely a project-trust gate (not investigated, UNVERIFIED reason). Put policy in global settings or `PI_CODING_AGENT_DIR` per task.
4. `fs.watch` fired twice per write (two "trigger fired" log lines for one event); a real wake channel needs idempotent consumption (rename-based inbox or sequence ids).
5. Every compaction entry embeds a full copy of the system message and tool declarations (about 8 KB each); with a compaction per few turns the JSONL grows noticeably. The `get_session_stats.contextUsage` is `null` right after compaction.
6. Wake of an idle process works only for a live process owned by one supervisor; RPC is stdin/stdout of the child (no socket, one client). A supervisor that wants "operator attaches later" needs more (tmux, or a future pi server).
7. pi repairs dangling tool calls at request-build time only (synthetic "No result provided"), the file keeps the unmatched call.
8. `--mode rpc` and `-p` both load the global extension automatically; extension errors surface only in our own log (stderr file was empty here).

## Effort estimate for a real `ctlstat` pi adapter

- Log parser (`ctxfill` generalised: active branch, compaction, cost, tool-call counts, boundaries): 0.5 day.
- Extension `ctlstat-pi` (tool_result change-only injection calling `ctlstat status --since-last --json` keyed by `PI_SESSION_ID`, `turn_end` pressure report via `ctx.getContextUsage()`, `session_before_compact` reading `<session>.handover.md` plus optional external summariser with `usage`, inbox watcher with idempotent rename-based consumption): 1.5 days including tests.
- Supervisor (resume, `compact` with handover, post-compact `continue` prompt, restart/kill recovery, detached process hygiene, cost record from `compact` response): 1.5 days.
- Real-model validation (instruction obedience, summary quality, cost of hybrid L0+L1, the 5-arm replay of R12.9 through pi as arm E3): 1-2 days, needs a gateway key which this PoC deliberately did not use.
- Total about 4-6 days for an adapter at parity with the Claude Code one, plus pin-and-regression-test upkeep (run these `test_*.mjs` as a smoke suite before each pi upgrade).

## Risks

- Churn: weekly minors, extension API documented as changing between minors (TypeBox import change noted in 1.5.0); mitigate by pinning, running this PoC as regression, depending only on `tool_result`, `session_before_compact`, `session_start/shutdown`, `turn_end`, RPC `compact`/`get_session_stats`/`prompt`.
- No server/attach: controller must be headless under our supervisor (single RPC client); an operator UI is out of scope until upstream ships one.
- Summarisation does not reuse the prompt cache in pi (cache writes disabled for summary requests); the warm-cache saving seen with Claude (`$0.54` vs `$2.14`) is NOT available here; the mechanical skeleton path (goal 4) avoids the model call entirely.
- Auto-compaction passes no instructions: handover must be found by file convention.
- Maintainer concentration and the Earendil commercial direction (from the earlier research).
- Anthropic subscription use from pi still UNVERIFIED (API key billing assumed).
- This PoC proves plumbing against a scripted model; real providers may differ in tool-call streaming quirks (pi has per-provider `compat` flags) and in how they treat the injected extra tool-result text part.

## Cleanup

Container `poc-pi-main` removed by exact name at the end. Scratch dir kept for inspection (contains only fake-model data, pi install, and files above; `auth.json` files are pi-created empty `{}`).
