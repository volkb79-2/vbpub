<!-- origin: 2026-11-15; status: design; canonical -->
<!-- relation: ctlstat compaction recovery; follows EXPERIMENT-compaction-hooks.md and EXPERIMENT-compaction-steering-2026-11.md -->
<!-- Evidence rule: every "measured" cell cites the experiment row it comes from; anything not measured says UNTESTED. -->

# Harness × situation compaction checklist

Which mechanism builds a readback, which mechanism makes a session ingest it,
and what is actually tested — per harness and per situation. Read
[`EXPERIMENT-compaction-hooks.md`](EXPERIMENT-compaction-hooks.md) (E1–E6) and
[`EXPERIMENT-compaction-steering-2026-11.md`](EXPERIMENT-compaction-steering-2026-11.md)
(X1–X6) for the measurements this table cites; nothing here is claimed beyond
those verdict tables.

The readback itself is the same document on every row: build it with
`nyxloom-harness extract <session> --preset readback --budget-tokens N --since <marker>`
(recent prose + delta), adding `--goal-prompt` or `--goal-file` when the
session's anchor is its original goal (a spawned or targeted session) and
omitting them for a controller session, whose anchor is its `plan.yaml` — see
the preset section of `nyxloom/docs/session-extract.md` and the two session
kinds in the root `AGENTS.md`. The controller's Claude Code hook script is
`scripts/controller/hooks/ctx-hooks.py`.

## Vocabulary used below

| Term | Meaning |
|---|---|
| **interactive controller** | a session a person sits in. Nobody outside compacts or restarts it, so it cannot terminate itself; it relies on auto-compaction (or the operator's manual compaction) plus the recovery files. |
| **spawned session** | a subagent or a headless job. Nobody applies a read order for it, so it self-terminates at a semantic boundary with a continuation brief; the controller then either compacts+resumes it or starts a successor. |
| **builds** | who runs the readback command. |
| **ingests** | who actually reads the readback and continues. |
| **test status** | `OFFLINE` = tested offline in this package (`nyxloom/tests/test_session_extract_layout.py`, `nyxloom/tests/test_session_extract_readback.py`, `scripts/controller/tests/test-ctx-hooks.sh`) — that is a test of THIS side, not of the harness; `MEASURED` = observed live in the named experiment; `SCHEMA-ONLY` = endpoint/field names established from schema or docs but not executed; `UNTESTED` = not measured and not covered offline. |

## Claude Code — interactive controller

| Situation | What triggers it | Builds | Ingests | Best handling | Test status | Command lines |
|---|---|---|---|---|---|---|
| **Auto-compaction** (harness fires by itself) | the harness, at its own threshold; `PreCompact` receives `trigger:"auto"`, `custom_instructions:null` (E2) | `PreCompact` hook → `ctx-hooks.py pre-compact`, which runs the extract and writes `readback-N.md` | `SessionStart[compact]` hook prints the read order, the newest readback path and size, capped at 10,000 chars; the readback is then read as step (2) of that order | let the harness compact; the hook only steers and records. Never block the compaction | **MEASURED**: PreCompact stdout reaches the summariser in 8/8 compactions (E2, X2); SessionStart[compact] output lands AFTER the summary and the kept messages (X2). **OFFLINE**: the hook's own build/numbering/failure/timeout paths | `printf '%s' "$HOOK_JSON" \| scripts/controller/hooks/ctx-hooks.py pre-compact --state-dir .controller-state`; `… session-start --state-dir .controller-state` |
| **Manual compaction** (operator types `/compact`) | operator; `PreCompact` receives `trigger:"manual"` and `custom_instructions` (E2) | same `PreCompact` hook (identical path to auto) | same `SessionStart[compact]` hook (`source:"compact"`) | nothing extra; the hook is trigger-agnostic, so a manual compaction gets the same readback | **MEASURED** for the trigger and stdout steering (E2 manual run); **OFFLINE** for the hook | `/compact <notes>` in the TUI, or `claude -p --resume <id> "/compact <notes>"` (next row) |
| **External compaction of a session the TUI still holds** | an outside process issues `/compact` | the outside process (one `-p` process per message) | the session itself on its next turn, via the `SessionStart[compact]` hook | the known route is external `-p --resume`; it is proven only for an IDLE session | **UNTESTED** for a RUNNING interactive session (E1 proved the idle case; the running/TUI-held case is listed under UNVERIFIED in the hooks experiment) | `claude -p --resume <id> "/compact <instr>" </dev/null` |
| **Long-lived headless driver instead of the TUI** | a supervisor sends `/compact` as a stream message | the supervisor (or the same `PreCompact` hook, which fires in the stream: X2) | the process, from `SessionStart[compact]` `additionalContext` (X2) | one `-p --input-format stream-json` process; a mid-turn message is queued to the next tool boundary | **MEASURED** (X1 drive + compaction, X2 hooks); live multi-hour soak **UNTESTED** | `claude -p --input-format stream-json --output-format stream-json --verbose --settings <settings.json>` then `{"type":"user","message":{"role":"user","content":"/compact <instr>"}}` |
| **Hook or builder failure** (extract missing, slow, or erroring) | the hook's own subprocess | the hook attempts it, then reports failure | `SessionStart` prints the exact manual command; `readback-state.json` carries the failure | the hook must never fail or block the session: exit 0, log one line, 20 s hard cap. The session builds the readback by hand as its first action | **OFFLINE** (success, extract failure, timeout, missing transcript, subagent input, N increment, no-raise) | `nyxloom-harness extract <session> --preset readback --budget-tokens 30000 --tail-prose-tokens 10000 [--since <marker>] > .controller-state/readback-N.md` |
| **Subagent** (Agent tool) | a PostToolUse/other hook inside a subagent | for a pressure note: nothing; for a readback: the SUBAGENT's own file | the subagent, if it is resumed | use `<parent dir>/<session_id>/subagents/agent-<agent_id>.jsonl`; never report the parent's fill (a false "past 200k" note made a subagent stop early) | **MEASURED** (live-session item 2, 2026-11-14); **OFFLINE** (the hook resolves the path and reports nothing when absent) | `nyxloom-harness extract <…>/subagents/agent-<id>.jsonl --preset readback …` |
| **Pressure note** (not a compaction) | every tool call, when the fill crosses a 50k step from 100k on | — | the model, as `PostToolUse` `additionalContext` | note every 50k from 100k; finishing wording below 200k, stop-at-a-boundary wording from 200k. No tool-call count | **MEASURED** (E3 delivery and cost, X2 under stream-json); **OFFLINE** for the ladder arithmetic, the wording bands and the re-arm after a shrink | `… post-tool-use --state-dir .controller-state` |

## Claude Code — headless (`claude -p`) and a spawned session

| Situation | What triggers it | Builds | Ingests | Best handling | Test status | Command lines |
|---|---|---|---|---|---|---|
| **External compaction of a spawned session** | the controller, after the session ended or while it is idle | the controller (the session itself has ended) | the resumed session, on its first turn, from the readback file the controller names | resume with the readback as the first message (the warm cache is the reason to resume rather than succeed it) | **MEASURED** (E1: `-p --resume … "/compact <instr>"` works, transcript gets a boundary and summary); the readback-ingest step is **OFFLINE** (the document) and **UNTESTED** as a whole loop | `claude -p --resume <id> "/compact <instr>" </dev/null` then `claude -p --resume <id> "Read <readback-N.md> in full first, then continue."` |
| **Self-terminate at a boundary + successor** | the session's own pressure note from 200k on | the controller (successor brief: `nyxloom extract --successor-brief`, or its own brief) | the fresh successor | the session writes a forward-oriented brief, commits, returns; the controller starts a successor seeded with it | **MEASURED** for the general mechanism (`--successor-brief` is a shipped, tested nyxloom path: **OFFLINE**), the termination itself is policy not measured | `nyxloom-harness extract <session> --successor-brief --order @ORDER.md > successor-brief.md` |

## opencode

| Situation | What triggers it | Builds | Ingests | Best handling | Test status | Command lines |
|---|---|---|---|---|---|---|
| **Compaction of a spawned opencode session** | the controller, over HTTP | the controller (`--preset readback` needs a Claude Code transcript today, so a non-Claude controller uses `--profile all --agent-comms full` with its own budget) | the session, when it is prompted after the compaction | `POST /api/session/:id/compact`; the endpoint has no instruction field, so steering goes through the plugin `compaction` hook reading a handover file, or by pushing a steering message into `event.messages` | **MEASURED** (X4: compaction in ~4 s with a real model, SSE `session.compaction.started/delta/ended`, next prompt answered from the summary); plugin steering **MEASURED** with a fake model in `POC-opencode.md` | `curl -X POST -u opencode:<pw> http://127.0.0.1:<p>/api/session/<id>/compact -d '{}'` |
| **Auto-compaction inside an opencode session** | the harness's own `compaction` config block | — (the harness summarises) | the session | the plugin `compaction` hook supplies or steers the summary from a handover file | the config block with a REAL model is **UNTESTED**; the fake-model PoC covered auto and mid-turn (**POC**) | plugin hook `ctx.session.hook("compaction", …)` |
| **Hook/builder/ingest failure** | any of the above | the controller, by hand | the controller records the failure in its own state and re-runs | nothing in the session can repair it; the controller's state files are the fallback (they are read before the readback, outside its budget) | **OFFLINE** for the controller side only | rebuild with `nyxloom-harness extract …` and resume |

## pi

| Situation | What triggers it | Builds | Ingests | Best handling | Test status | Command lines |
|---|---|---|---|---|---|---|
| **Manual compaction of a spawned pi session** | the controller, over RPC | the controller | the session, on resume | `{"type":"compact","customInstructions":"<handover>"}` | **MEASURED** (X5: `customInstructions` obeyed by a real model, `compaction_start`/`compaction_end` events with `usage`) | `pi --mode rpc --session <file>.jsonl` then `{"type":"compact","customInstructions":"<text>"}` |
| **Auto-compaction inside a pi session** | the harness's `compaction` threshold | the harness, unless the extension supplies a summary | the session | an extension `session_before_compact` handler reads a handover file and either steers (`customInstructions`) or SUPPLIES the summary with zero model calls | **MEASURED** (X5: threshold compaction fires the event, supply mode used the handover and the next answer used it) | extension `pi.on("session_before_compact", …)` returning `{compaction:{summary, firstKeptEntryId, tokensBefore}}` |
| **Readback ingest on resume** | the controller, after a compaction | the controller | the session | send the readback path as the first resumed prompt, as on Claude Code | the ingest step is **UNTESTED** for pi; the document side is **OFFLINE** | `pi -p --session <file>.jsonl "Read <readback-N.md> in full first, then continue." < /dev/null` |
| **json (`-p`) mode compaction event** | the harness | — | — | not needed if the controller steers over RPC | **UNTESTED** (X5: the session was too small to compact) | — |

## Codex

X6 (2026-11-15, Codex CLI `0.167.0`, merged as `aab5d96df`/`a8f8fe2cd`) measured
the route below on the DeepSeek model route, so these rows are no longer
schema-only. What stays UNTESTED is named in each cell.

| Situation | What triggers it | Builds | Ingests | Best handling | Test status | Command lines |
|---|---|---|---|---|---|---|
| **Compaction of a Codex thread from outside** | the controller, over the app-server (stdio JSON-RPC) | the controller (a non-Claude transcript: `--profile all --agent-comms full`) | the resumed thread (`thread/resume`, or a fresh process resuming the thread) | `thread/compact/start {threadId}`; it returns `{}` at once, so wait for `item/completed` of the `contextCompaction` item AND `turn/completed`; the summary is NOT in any event, only in the rollout file's `compacted.message` | **MEASURED** 4/4 (one model request each, 2.0-7.6 s; resume answered task, serial and last decision after an assistant-only fact was compacted). The readback-then-resume loop as a *controller workflow* is **UNTESTED** | `thread/compact/start {threadId}` → wait `item/completed {type: contextCompaction}` |
| **Steering that summary** | the same `thread/compact/start`, no instruction field | — | — | `compact_prompt` is the lever: `-c compact_prompt="<text>"` at launch, or `config:{"compact_prompt":"<text>"}` in `thread/resume` before the trigger | **MEASURED** both ways (run7 launch, run8 per thread). Persistence of a per-thread value for LATER automatic compactions: **UNTESTED** | `codex -c compact_prompt="<text>" app-server` |
| **What the compacted history keeps** | — | — | — | plan for it: user messages are retained VERBATIM, assistant messages are dropped, the new summary subsumes the old one, and the summary's structure follows Codex's own prompt | **MEASURED** (X6 (c)/(d), including four chained compactions of one thread). Retention size cap on tens-of-thousands of user tokens: **UNTESTED** | — |
| **`exec resume <id> "/compact"`** | — | — | — | do NOT use it | **MEASURED FAIL** (stored as an ordinary user message, no `compacted` record, one request spent) | — |
| **TUI `/compact`** | the operator | the harness | the session | works, but it needs a human at the pane | **MEASURED** (tmux, "Context compacted" within 11 s) | `/compact` in the TUI |
| **Automatic threshold compaction** | `model_auto_compact_token_limit` (and the untested `_scope`) | the harness | the session | unproven; prefer an explicit trigger or a successor | **UNTESTED** (X3 saw no compaction item under limits 4000/12000/2000 and `model_post_turn_compact_threshold_percent=1`; the `total` vs `body_after_prefix` scope has never been run) | `-c model_auto_compact_token_limit=<N>` |
| **Thread goal as a promise carrier** | `thread/goal/*` | — | — | a candidate carrier that compaction cannot drop, but it has no measured effect yet | **SCHEMA-ONLY** (`thread/goal/get` returned `{goal: null}` on a test thread; `thread/goal/set` and whether a goal is injected after a compaction are UNTESTED) | `thread/goal/set {threadId, objective}` |
| **Self-terminate + successor** | the session's own pressure note | the controller (successor brief) | a fresh Codex job | the established route for spawned Codex work: the session returns a brief, the controller starts the successor | the brief itself is **OFFLINE** (`--successor-brief`); the termination is policy, not measured | `nyxloom-harness extract <rollout> --successor-brief --order @ORDER.md` |

## Reading the readback on a non-Claude transcript

`--preset readback` is Claude Code only today (its intent and agent-communication
surface depends on that adapter). A controller on another harness uses:

```bash
nyxloom-harness extract <session> --profile all --agent-comms full > readback-N.md
```

Until [NL-65](../../../nyxloom/nyxloom-trove/backlog/NL-65-readback-weights-older-agent-comms.md)
extends the preset to those adapters, that fallback has no layout at all: no
recent-prose tail and no `[readback marker: …]` line. The controller then
records the source marker itself, and a spawned session's goal prompt lives in
the controller's own state files (which are read before the readback and are
outside its budget).

## What is NOT tested anywhere

- An external compaction of a RUNNING interactive Claude Code session (the TUI
  holds the file); only the idle case is measured (E1).
- A multi-hour headless stream-json soak (memory, rate limits).
- The readback INGEST step end to end on any harness: the document, the hook
  that points at it and the file write are tested; "the resumed model read it
  and continued correctly" is measured only as the E2/X2 summary-steering
  observations.
- opencode `compaction` config with a real model; pi json-mode compaction
  events; every Codex row beyond the X6 trigger/item pair.
