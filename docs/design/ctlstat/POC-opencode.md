<!-- origin: 2026-11-09; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ctlstat-POC-opencode.md -->
<!-- relation: ctlstat opencode PoC -->

**Status: COMPLETE**

# ctlstat PoC: opencode v2 (anomalyco/opencode) driven by a fake OpenAI-compatible model

Date 2026-11-09. Wall time about 30 minutes (budget 2 h). Follows `ctlstat-RESEARCH-harnesses.md` (PoC-B) with the goals of `ctlstat-REQUIREMENTS.md` 12-12.5 and the Claude Code baseline of `ctlstat-EXPERIMENT-compaction-hooks.md`. Everything ran in ONE disposable container `poc-oc1` (removed at the end by exact name; `docker ps -a --filter name=poc-oc1` returns nothing). No credentials were used or read, no estate repo, worktree or host was touched. Evidence files remain in the scratch dir `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/poc-opencode/` (`evidence-fake-requests.jsonl`, `ctlstat/plugin.log`, `serve-*.log`, `sse-*.txt`, `openapi.json`, plugin, fake model, driver scripts); the 258 MB binary copy and the source clone were deleted.

## Goals table

| # | Goal | Verdict | Evidence (details below) |
|---|---|---|---|
| 1 | Long-lived server owns sessions/jobs; client attach/detach without ending the session | **PASS** | A CLI client (`opencode run --server URL --session ID`) was SIGKILLed 3 s into an 8 s turn: the turn finished on the server and the next prompt worked. TUI (`opencode --server URL --session ID` under a pty) attached, a prompt typed into it landed in the same session, Ctrl-C detached, session stayed. SSE `GET /api/event` client attached/detached. Web app is served by the same server (`GET /` 200). Web/desktop rendering itself NOT exercised (UNVERIFIED) |
| 2 | Per-message usage (SQLite/API) so context fill can be derived | **PASS** | Every assistant message carries `tokens{input,output,reasoning,cache{read,write}}` in `GET /api/session/:id/message`, in SQLite `session_message.data` (`$.tokens`), and live as SSE `session.usage.updated`. Context fill = `input + cache.read + cache.write` of the last assistant message; it equalled the fake server's `prompt_tokens` exactly (5618 = 5618; with the fake reporting `cached_tokens=3000`: stored `input 2618 + cache.read 3000 = 5618`). Same formula as Claude E4 |
| 3 | Plugin injects a CHANGE-ONLY status diff after tool calls, state in the plugin | **PASS** | `tool.hook("execute.after")` appended text to `result.content`; 5 tool calls with status edits in between produced injections on calls 1 (`full #1`), 2 (`+gate=red`), 4 (`-gate=red`) and nothing on 3 and 5. The text is persisted in the stored tool part and was present in every later model request (checked on the fake side in turn 2 and after restart). No wrapper overhead (payload only, 30-60 chars), appended after the tool output |
| 4 | Unattended self-compaction: handover file, external HTTP compact, plugin supplies the summary (or steers), session continues | **PASS** (both variants) | SUPPLY: `POST /api/session/:id/compact` with the plugin `compaction` hook setting `event.result.summary` = mechanical skeleton + handover text: 0 requests reached the model server during compaction, the compaction message has the plugin text, the next prompt's request contained the handover fact and the fake answered from it. Also fired for AUTOMATIC compaction (`reason:"auto"`, mid-turn). No handover file: hook returns, opencode summarizes normally (fallback works). STEER: hook pushes the handover as an extra user message into `event.messages`; summarizer request (same 12 tools, same system, +3 messages) contained the handover and the summary carried the fact. Failed summary (template mismatch) leaves the session untouched (`status:"failed"` compaction message) |
| 5 | Wake an idle session with an HTTP call | **PASS** | `POST /prompt` (used throughout), `POST /synthetic {resume:true}` wakes with no user message (reply in about 300 ms), `{resume:false}` queues durably in `session_inbox` until the next wake. Bonus built-ins: `POST /api/experimental/schedule` with `if:{run:"cat status",changes:true}` woke the idle session on an external file change (baseline check then fire; min period 15 s), and the plugin `experimental.idle` hook admitted a synthetic message in the same execution |
| 6 | Server restart then resume | **PASS with a caveat** | Needs the MANAGED service (`opencode service start`): graceful TERM and `kill -9` of the service mid-turn, then `opencode service start` resumed the interrupted turn (synthetic notice "The server restarted while you were working. Continue from where you left off...", `resume_attempts=1`, model answered). A hand-started `opencode serve` is "unregistered" and does NOT sweep orphaned turns (mid-turn SIGKILL left the user message unanswered). Sessions, compaction summaries, queued inbox items survive both. Plugin in-memory state does not (see risks) |
| 7 | Detached jobs survive | **PASS for OS-detached, FAIL for harness-managed** | `setsid nohup` job started by an agent shell call survived compaction, server TERM, server SIGKILL and service restart (same pid, ppid 1, log lines continuous). The harness's own background shell (`shell` tool `background:true`, listed in `GET /api/experimental/job` and `/api/shell`) is a child of the server and DIES with it; after graceful service stop the job list is empty with no notice to the session; after an unmanaged-server stop the job row stays `running` (stale). Same lesson as Claude E5: independent jobs must be nohup/setsid |

## Setup and exact commands

Versions: opencode `2.2.6` (binary copied from `/opt/mdt/npm/lib/node_modules/@opencode/cli/bin/opencode.exe` into the scratch dir), `@opencode/plugin` 2.2.6 (npm tarball, types read), source read at anomalyco/opencode `f997cef` (shallow clone, deleted), node `26.12.1` (image `node:26.12.1-trixie-slim`, already local), docker 29.9.2.

Container (scratch dir is the only mount; host path resolved from the devcontainer's own mount of `/tmp`):
```
docker run -d --name poc-oc1 --memory 1500m --cpus 1 --cap-drop ALL --security-opt no-new-privileges --user 1003:1003 \
  -v <host-scratch-dir>/poc-opencode:/poc -w /poc/work \
  -e HOME=/poc/home -e XDG_DATA_HOME=/poc/xdg/data -e XDG_CONFIG_HOME=/poc/xdg/config -e XDG_STATE_HOME=/poc/xdg/state -e XDG_CACHE_HOME=/poc/xdg/cache \
  -e OPENCODE_DISABLE_AUTOUPDATE=1 node:26.12.1-trixie-slim sleep 14400
```
Resource use at the end: 356 MiB RSS of 1.46 GiB, 0.3% CPU idle, 31 pids (server plus a second helper `opencode serve` process that the server spawns).

Processes (all inside the container): `node /poc/fake/fake-model.mjs` (fake model on `<host-address>:9100`), and the opencode server, first as `opencode serve --hostname <host-address> --port 4096` (fixed password via `OPENCODE_SERVER_PASSWORD`), later as the managed service (`opencode service start`, random port, password in `$XDG_STATE_HOME/opencode/service.json`). Clients: `node oc.mjs METHOD PATH [JSON]` (Basic auth `opencode:<password>`), `t2.mjs` (prompt + wait + dump), `driver.mjs` (compaction driver), `wake.mjs`, `sse.mjs`, `opencode run --server URL --session ID --format json`, TUI under `script -qec`.

Provider config (`$XDG_CONFIG_HOME/opencode/opencode.json`, native key `providers`; the bundled package `@opencode/ai/providers/openai-compatible` needs no npm install). The built-in free `opencode` provider (zen, network) is denied by policy so no accidental traffic:
```json
{ "providers": { "fake": { "name": "Fake", "package": "@opencode/ai/providers/openai-compatible",
    "settings": { "baseURL": "http://<host-address>:9100/v1", "apiKey": "not-a-secret" },
    "models": { "fake-1": { "name": "Fake 1", "limit": { "context": 30000, "output": 2000 } },
                "fake-small": { "name": "Fake small", "limit": { "context": 7600, "output": 1000 } } } } },
  "model": "fake/fake-1", "snapshots": false,
  "policies": [ { "action": "provider.use", "resource": "opencode", "effect": "deny" } ],
  "compaction": { "auto": true, "buffer": "15%", "keep": { "tokens": 300 } },
  "experimental": { "schedules": true } }
```
`opencode reload` applies config and plugin changes to the running server (re-runs plugin `setup`, 2-3 times per reload).

Fake model (`fake/fake-model.mjs`, about 90 lines, `POST /v1/chat/completions`, SSE streaming incl. tool calls and a final `usage` chunk): deterministic directives read from the latest user message: `@@text X`, `@@bash CMD`, `@@script CMD1 ;; CMD2`, `@@chain N`, `@@slow SEC`, `@@detach`, `@@bg CMD`, `@@recall`. It logs every request (`fake-requests.jsonl`: kind, message count, tool names, markers found in the request, prompt tokens, whether the handover fact is present) which is what proves "the model saw X" and "no summarizer call was made". Summary requests are recognised by the final user message ("You MUST summarize the conversation above...") and answered with the 7-section template opencode demands (a non-conforming reply makes compaction fail cleanly: found by accident).

## Plugin that worked (Promise API, file `$XDG_CONFIG_HOME/opencode/plugins/ctlstat.ts`)

Loading: a `.ts` file in `plugins/` of the global config dir (or `<project>/.opencode/plugins/`) is picked up with no package.json; `import { Plugin } from "@opencode/plugin"` is resolved by the host; default export `Plugin.define({ id, setup })`. Hooks are registered imperatively in `setup`. Trimmed to the load-bearing parts:
```ts
import { Plugin } from "@opencode/plugin"
import fs from "node:fs"
const DIR = "/poc/ctlstat", STATUS = `${DIR}/status.txt`
const read = (p: string) => { try { return fs.readFileSync(p, "utf8").trim() } catch { return "" } }
type Per = { last: string[]; n: number; needFull: boolean }
const state = new Map<string, Per>()                       // plugin-held state per sessionID

export default Plugin.define({
  id: "ctlstat",
  setup: async (ctx) => {
    // (3) change-only injection into the tool result (persisted in the session)
    await ctx.tool.hook("execute.after", (e) => {
      if (e.status !== "completed") return
      const cur = read(STATUS).split("\n").filter(Boolean)
      const per = state.get(e.sessionID) ?? { last: [], n: 0, needFull: true }
      let text: string | undefined
      if (per.needFull) text = `[CTLSTAT full #${++per.n}] ${cur.join(" | ") || "(empty)"}`
      else {
        const add = cur.filter((l) => !per.last.includes(l)), del = per.last.filter((l) => !cur.includes(l))
        if (add.length || del.length) text = `[CTLSTAT diff #${++per.n}] ${[...add.map((l) => "+" + l), ...del.map((l) => "-" + l)].join(" | ")}`
      }
      per.last = cur; per.needFull = false; state.set(e.sessionID, per)
      if (!text) return
      const c = e.result.content
      e.result.content = [...(typeof c === "string" ? [{ type: "text", text: c }] : (c ?? [])), { type: "text", text }] as any
    })

    // (4) compaction: supply our summary (skeleton + agent handover) and skip the model call, or steer
    await ctx.session.hook("compaction", (e) => {
      const handover = read(`${DIR}/${e.sessionID}.handover.md`)
      if (!handover) return                                  // fall back to opencode's own summarizer
      const calls: string[] = []; let users = 0              // mechanical skeleton from e.messages (no model call)
      for (const m of e.messages as any[]) {
        if (m.role === "user") users++
        for (const p of Array.isArray(m.content) ? m.content : []) if (p?.type === "tool-call") calls.push(`${p.name}:${JSON.stringify(p.input).slice(0, 60)}`)
      }
      const skeleton = `## Skeleton (mechanical, ctlstat)\n- user messages: ${users}\n- tool calls: ${calls.length}\n- status: ${read(STATUS).replace(/\n/g, " | ")}`
      if (read(`${DIR}/mode`) === "steer") {                 // STEER variant: extra instruction before the summary prompt
        ;(e.messages as any[]).push({ role: "user", content: [{ type: "text", text: `Compaction instructions from the agent's own handover (follow them):\n${handover}` }] })
        return
      }
      e.result = { summary: `${skeleton}\n\n## Handover (written by the agent)\n${handover}`, metadata: { ctlstat: true } }   // SUPPLY variant
      state.forEach((p) => { p.needFull = true })            // full snapshot again after compaction
    })

    // wake / external-event loop: runs when a turn ends with nothing pending; a synthetic admitted here continues the same execution
    await ctx.session.hook("experimental.idle", async (e) => {
      const t = read(`${DIR}/${e.sessionID}.wake`); if (!t) return
      fs.unlinkSync(`${DIR}/${e.sessionID}.wake`)
      await ctx.session.synthetic({ sessionID: e.sessionID, text: t })
    })

    // probe: does a `context` hook edit persist? (answer: NO, request-only; see below)
    await ctx.session.hook("context", (e) => { /* push a user message into e.messages */ })
  },
})
```
Compaction hook input keys: `sessionID, model, system, messages, options, agent, tools`. `event.result = { summary, native?, metadata?, tokens? }` ends the compaction without a request (`SessionCompactionResult`, optional `tokens` is billed to the session). A plain-object message pushed to `event.messages` was accepted without constructing the Effect `Message` class. The hook sees the request WITHOUT the summary prompt (opencode appends it afterwards), so a steering message lands before it.

## Compaction driver (the future ctlstat action), HTTP only

`driver.mjs <sessionID> "<continue text>"`:
```
POST /api/experimental/session/:id/wait        # idle barrier
POST /api/session/:id/compact   {}             # returns the queued compaction message (delivery:"steer"); no instruction field exists
POST /api/experimental/session/:id/wait
GET  /api/session/:id/context                  # after: [compaction, idle] (+ kept tail if any), summary text in the compaction message
POST /api/session/:id/prompt    {"text":"continue ..."}
```
Measured with the fake model: whole compaction round trip 0.1 s (supply mode, no model call); summary also carries `metadata.keepID`/`recent` (the kept tail, `compaction.keep.tokens`). Cost record: the compaction message has `tokens`/`cost` only when a model call happened or the hook returned `tokens` (steer: `{"input":6667,"output":83}`; supply: none).

The agent side of the flow (agent writes `<sessionID>.handover.md` at a boundary and ends) was driven by a scripted shell call (`cp prepared-handover.md <sessionID>.handover.md`); a real agent would write the file with its normal write tool and end its turn.

## Session store layout (observed)

- `$XDG_DATA_HOME/opencode/opencode.db` (SQLite, WAL; read-only access with `node:sqlite` `readOnly:true` worked while the server ran), `.../shell/<projectID>/sh_*.out` (background shell output), `.../log/`.
- Tables: `session_v2` (id, directory, title, `tokens_input/output/reasoning/cache_read/cache_write` totals, `cost`, `model`, `agent`, `time_busy`, `time_idle`, `idle_outcome`, `time_compacting`, `time_suspended` = the write-ahead execution claim, `resume_attempts`), `session_message` (id, session_id, `type` in user/assistant/synthetic/compaction/idle/system/agent-switched/model-switched, `seq`, JSON `data`), `session_inbox` (durable queued items), `job` (background shell/subagent jobs), `schedule`, `event`, `session_part` (both empty by default: `part_rows` flag off, events not persisted), `kv`, `credential`, `project`, `worktree`, ...
- Assistant message JSON: `{type:"assistant", agent, model:{id,providerID}, content:[{type:"text"|"tool", ...state{status,input,content[],metadata}}], finish, tokens{input,output,reasoning,cache{read,write}}, cost, time{created,streamed,completed}}`. Idle markers carry `outcome`. Compaction message: `{type:"compaction", status:"completed"|"failed", reason:"manual"|"auto", summary, metadata, tokens?, cost?}`.
- After a compaction `GET /context` returns only the messages after it (`compaction, idle, ...`).

## API endpoints used (HTTP, Basic auth user `opencode`; full spec at `GET /openapi.json`, 170 operations; `opencode api METHOD PATH` is the CLI equivalent)

`GET /api/info`, `GET /api/session`, `GET /api/session/active` (running/idle map), `POST /api/session` (`title`, `model`), `POST /api/session/:id/prompt` (`text`, `delivery`, `resume`), `POST /api/session/:id/synthetic` (`text`, `resume`), `POST /api/session/:id/compact`, `GET /api/session/:id/context`, `GET /api/session/:id/message`, `GET /api/session/:id/inbox`, `POST /api/experimental/session/:id/wait`, `GET /api/experimental/session/stats`, `GET /api/experimental/job`, `GET /api/shell`, `POST|GET|DELETE /api/experimental/schedule[...]`, `GET /api/event` (SSE), `GET /api/plugin`, `GET /api/model`, `GET /api/provider`, `GET /api/config`, `POST /api/session/:id/interrupt` (not exercised), CLI `opencode reload|service start|status|restart|stop`, `opencode run --server`.
SSE event names seen during a compaction turn: `session.inbox.enqueued/delivered`, `session.execution.started/succeeded`, `session.compaction.started/ended`, `session.usage.updated`, `session.step.started/streamed/ended`, `session.text.started/delta/ended`, `session.tool.input.started/ended`, `session.tool.called/progress/success`, `shell.created/exited`. That answers the open question in the harness research (event names): ctlstat can follow context fill and compaction live from `/api/event`, without parsing the DB.

## Findings that change the design

- **Persistence rules (research open question answered).** (a) `execute.after` result edits ARE persisted in the stored tool part and replayed in every later request. (b) `session.hook("context")` edits are REQUEST-ONLY: the marker reached the model on each request but no `session_message` row contains it (opencode's own Plan plugin pairs the hook with `ctx.session.synthetic` to persist). (c) `ctx.session.synthetic` is persisted. For change-only injection use (a); use (c) for events that arrive while idle.
- **Plugins load per Location (project directory).** A plugin in `<project>/.opencode/plugins/` is NOT active for sessions in another directory. The managed service's default location is `$HOME`, so my first service-mode runs had no plugin (injection silently missing). Put the plugin in the global config dir `$XDG_CONFIG_HOME/opencode/plugins/`.
- **Managed service vs `serve`.** Restart recovery, PID registration and the lock protocol belong to `opencode service`. `serve` works for development but is "unregistered": no orphan-turn sweep. Production = `opencode service` (or `serve --service`, untested). `opencode service restart` raced once ("Server process N is still running" thrown by `service.stop`, the server then exited and was NOT restarted; `service start` fixed it): script `stop` + wait + `start` and verify with `status`.
- **Credentials of the server.** The service password sits in plaintext in `config/service.json` and `state/service.json`; clients read it from there. Treat like a local socket secret; ctlstat should read it, not copy it.
- **Plugin in-memory state is per process and per `setup` call.** `setup` ran 2-3 times per reload and state resets on server restart (first tool call after restart injects a `full` snapshot again, which is the safe direction). A real adapter should persist the per-session "last injected" snapshot via `ctx.storage` (exists in the API, not exercised) or derive it from the session history.
- **Warm cache.** Structural evidence only: in STEER mode the summary request is the normal request (same system, same 12 tools, same messages) plus the appended messages, i.e. prefix reuse by design, as the source comments say. In SUPPLY mode there is no summary call at all, but the continuation after compaction starts a new prefix (cold): the cost is the first request on the compacted context, not the summary. The fake model cannot show cache-hit pricing or summary quality.
- **Token field convention.** opencode stores `input` WITHOUT cached tokens (`prompt_tokens - cached_tokens`) and `cache.read` separately; fill must add them back (as done above).
- **Mid-turn auto compaction** happened between two tool calls of one turn (context 6460 of a 7600 window, buffer 15%) and the plugin supplied the summary there too; the scripted directive was lost afterwards only because my fake model keys on the user message (a real model continues from the summary).
- Not exercised: permission prompts (the `build` agent ran shell calls unprompted in this config), subagents, `ctx.storage`, `--auto`, ACP, the web/desktop UIs, Windows/macOS, and whether the binary contacts the network on its own (models catalog, update check; `OPENCODE_DISABLE_AUTOUPDATE=1` was set; the container had default egress; no traffic to a model other than the fake was observed in the fake log, but the server's own HTTP calls were not captured).

## Stability observations of the `experimental.*` surface

Used and working in 2.2.6: hook `experimental.idle` (documented in `@opencode/plugin` README as "name and shape may still change"), `/api/experimental/session/:id/wait`, `/api/experimental/schedule`, `/api/experimental/job`, `/api/experimental/session/stats`, config flag `experimental.schedules` (and `background_compaction`, not used). Not experimental (stable-looking names, in the main `SessionHooks`/`ToolHooks` types): `session.hook` `prompt|context|compaction|generate|title|model.request|http.request|retry`, `tool.hook` `execute.before|after`, `session.synthetic|prompt|compact|context`. The `experimental` namespace is large (about 45 operations) and the Effect-4 `Plugin` type surface moves with every release, so the adapter should depend ONLY on the non-experimental hooks plus: SSE `/api/event`, `GET /message`, `POST /prompt|/synthetic|/compact`, and treat `wait` as replaceable by `/api/session/active` polling or SSE `session.execution.succeeded`. Version 2.2.5 on 2026-10-30, 2.2.6 on 2026-11-06 (per research), so expect weekly churn: pin the version and run this PoC as a regression test before each bump (the scripts are small and deterministic).

## Effort estimate for a real adapter (ctlstat-opencode)

| Piece | Work | Days |
|---|---|---|
| Log/usage reader: API + SSE (`/message`, `/api/event`, `session.usage.updated`), SQLite read-only fallback; fill formula, boundaries, compaction events | small, formula verified here | 1-1.5 |
| Plugin `ctlstat-opencode`: `execute.after` injection with persisted per-session snapshot (`ctx.storage`), diff/full policy from config (R12.7), `compaction` hook (skeleton L0 + handover L1, optional external summarizer call), global install and version pin | the code above is the core; add types, storage, config, error handling | 3 |
| Driver/service integration: idle barrier, compact, continue, wake (`synthetic`/schedule), cost record, retry of failed compaction, watchdog | `driver.mjs` is the skeleton | 2 |
| Deployment: managed `opencode service`, password handling, project/global plugin placement, restart procedure | pitfalls found above | 1 |
| Tests: deterministic fake-model regression suite from this PoC, upgrade gate per opencode release, one live-model smoke | reuse `fake-model.mjs` + scenarios | 2 |
| Total | | about 9-10 person-days; first useful slice (reader + injection + driver) about 4 days |

## Risks

1. v2 is young (2.2.x, weekly releases, about 4k open issues): hook types are Effect-4 and `experimental.*` can rename; mitigate with a pinned version, non-experimental surface, and this PoC as an upgrade gate.
2. Restart/jobs: harness-managed background shells die with the server and nothing notifies the session; agent jobs MUST be `setsid nohup` (same rule as Claude/Codex). Managed `service` required for turn recovery; `service restart` flaked once.
3. Plugin state and `setup` multiplicity (see above); a plugin bug in a hook fails the execution ("a hook that fails, fails the execution like any other session hook"), so every hook needs a try/catch that degrades to no-op.
4. Plugin scoping per project directory (silent no-op if misplaced) and the plaintext service password.
5. Not proven with a real model: summary quality of skeleton + handover (the 5-arm replay of R12.9 is still the test), prompt-cache pricing, behaviour with a gateway that reports usage differently (the `input - cached` convention must be re-checked per provider), and whether Anthropic subscriptions work in third-party harnesses (the research marks it unverified; API billing assumed).
6. Auto compaction threshold is a percentage buffer of the model window (`compaction.buffer`), per-task intended context sizes (R12.4) map to a per-model `limit.context` override in config (verified with `fake-small`), not to a per-session setting; per-session limits would need one model entry per band.

## Verdict for the decision (R12.10)

The opencode v2 route is technically viable for the unattended flow: server-owned sessions with attachable clients, exact per-message usage, a persisted change-only injection point, a compaction hook that can fully replace or steer the summary, HTTP/schedule/idle-hook wakes, and orphan-turn recovery after a crash. The two real caveats are churn of the experimental surface and the fact that harness-managed jobs are not independent of the server. No patch to opencode was needed.
