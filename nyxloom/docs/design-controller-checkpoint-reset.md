# Controller checkpoint-reset — can the CONTROLLER run the loop its children now run?

Status: DESIGN + first probe data (V5.0 measured 2026-08-21). Origin: dstdns operator
directive 2026-08-21 — the interactive controller stops itself at ~350k context and hands the
human a `/compact` prompt + a `/loop` re-arm; the CLI-child implementers already do this
mechanically (V1 addendum 3). Question: what makes the controller self-driving, and does that
need an external helper service?

Siblings: `design-context-lifecycle.md` (patterns a/b, V1–V9), `design-context-lifecycle-experiments.md`
(E-001..E-008, V1/V3/V4 data), backlog **B46** (the compaction service), **B47** (this doc's item).
Distinction that matters: V1 solved *worker* checkpointing. A controller differs in three ways —
it never finishes (no `DONE`), it FANS OUT children, and it is the human's window into the run.

---

## 1. Settled facts (do not re-litigate)

| # | Fact | Evidence |
|---|---|---|
| F1 | `claude -p --resume <sid> "/compact <text>"` really compacts, and **works on a session that ORIGINATED as an interactive one**, driven by a *different* process while the interactive one is closed. | **V5.0, measured 2026-08-21**: forked interactive session `cc40ab77…`→`65d20b37…`, compact exit 0, `system/compact_boundary` written with `trigger: "manual"`, `preTokens 43,388 → postTokens 800`, `durationMs 7,291`, `cumulativeDroppedTokens 42,588`, `isCompactSummary` user line follows. |
| F2 | In-place (non-fork) headless resume of an interactive session id works and returns the SAME `session_id`; the session JSONL grows (20→31 lines). | V5.0 probe 4, same day. |
| F3 | The `-p` result envelope in CLI **2.1.238 has no `compact_result` field** (keys: `subtype,is_error,session_id,result,num_turns,stop_reason,usage,modelUsage,total_cost_usd,duration_ms,…`). The compact verdict must be read from the session JSONL's `compact_boundary` line, or from exit code. | V5.0 probe 2 — corrects `design-context-lifecycle.md` §1, which quotes `compact_result: success`. |
| F4 | **Price of a compact cycle, opus, real work**: dstdns P116 ran 3 chained compacts on one CLI child — `$1.68 / $1.85 / $1.85`, `133 / 177 / 148 s`, `num_turns: 0`, `usage` reported as zeros but `total_cost_usd` populated. The work runs between them cost `$9.79 / $14.83 / $12.65 / $22.10`. So **a compact is ~8–13 % of the work turn it protects**, and three chained compacts did not degrade the child (it kept producing). This closes the "price the compact call" TODO in V1 addendum 3. |
| F5 | Post-compact resume context: **44.2k cache_creation + 15.9k cache_read ≈ 60k** vs 236.7k on the last pre-compact turn (~4× reduction), with zero re-orientation reads before the first productive Edit. | V1 addendum 3. |
| F6 | **No model-callable compaction tool exists** in any mode. Probed headlessly: the model reports `wakeup: ScheduleWakeup, CronCreate` / `compact: NONE`. `/compact` is a user-submitted command, not a tool. | V5.0 probe 3; Agent SDK tool list. |
| F7 | **`PreCompact` cannot steer a compaction.** It *receives* `custom_instructions` (populated only for `manual`, empty for `auto`) and its only decision control is `block`. So "let auto-compact fire and have a hook inject the designed retention prompt" **is not a supported surface**. | `code.claude.com/docs/en/hooks.md` §PreCompact. |
| F8 | `Stop` hooks receive **`background_tasks`** (in-flight `subagent`/`monitor`/`workflow`/`shell` tasks with status) and **`session_crons`** (entries sourced from `ScheduleWakeup`, `CronCreate`, `/loop`), plus `stop_hook_active` and `last_assistant_message`; `decision: "block"` (or `additionalContext`) continues the turn, capped at 8 consecutive blocks. | hooks.md §Stop. This is the ready-made *"are my children still running?"* gate a checkpoint needs. |
| F9 | A hook can be `async` or **`asyncRewake`: runs in the background and WAKES an idle Claude on exit code 2**, showing its stderr to Claude as a system reminder. An external watcher can therefore push text into a live idle interactive session — but text, not a slash command. | hooks.md §Command hook fields + §Hook output delivery. |
| F10 | `claude agents --json` is a **poll-able registry of every live session** — interactive rows carry `pid`, `sessionId`, `name`, `status` (`busy`/`idle`/`waiting`+`waitingFor`), background rows carry `state`. No TTY required. | Run live, 2026-08-21. |
| F11 | Agent-tool subagent ids are NOT CLI session ids and cannot be resumed or compacted headlessly, by any file transformation tried. CLI children (`--session-id`) can. | V1 addenda 1–2. |
| F12 | A summary-minted **snapshot fork** costs ~0.6–0.8k creation warm, ~18.7k cold (the snapshot body), and parallel fan-out pays N × body — never free. | V3/V4. |

**Unsettled and load-bearing:** whether a `ScheduleWakeup` / `CronCreate` / `/loop` prompt that
*is* a slash command (`prompt: "/compact <retention>"`) is expanded and executed when the cron
fires. The hooks lifecycle shows `UserPromptExpansion` running on submitted prompts, and
`session_crons[].prompt` is documented as "prompt submitted when the cron fires" — if it
expands, **the controller can compact itself with its own designed prompt and no external
process at all**. This single unknown decides the whole design (V5.1).

---

## 2. The three architectures

### A — controller stays interactive and self-compacts

*Mechanism:* at its own checkpoint the controller writes `BRIEF.md` + `COMPACT.md` (it already
does), then schedules `ScheduleWakeup(delay≈60, prompt="/compact <retention>")`, and a second
wakeup after it carrying "continue from BRIEF.md". One process, one session id, forever.

- **Measured:** F4/F5 give the price and the shape of the reset; F1 proves the *compaction of an
  interactive-origin session* is real. The write-your-own-retention half already runs today
  (the human is only the keystroke).
- **Unknown:** F7 kills the auto-compact+hook variant outright. Everything now rests on the
  cron-prompt slash expansion (V5.1). Secondary unknown: does the `/loop` arming and the
  ScheduleWakeup slot survive a `/compact` **in the same process** (F8 says `session_crons` is a
  live registry, which suggests yes — but unverified, V5.5).
- **Cost/cycle:** one compact call (**$1.7–1.9 at opus/235k**) + ~44k cache_creation on the next
  turn (F5). No process restart, no re-orientation, no lost children.
- **Failure modes:** if the cron prompt is NOT expanded, the controller silently sends the
  literal text `/compact …` as a user message and *believes* it compacted — a false-green that
  must be checked against the JSONL `compact_boundary`, never against the model's belief. A
  wakeup that fires mid-fan-out compacts away in-flight coordination state. `/compact`'s
  `preservedMessages` tail is small (2–3 uuids observed), so anything not on disk is gone.
- **Variant A2 (no new primitive needed):** run the controller inside tmux and let a watcher
  `tmux send-keys` the `/compact <prompt>` line. Crude, fully mechanical, works today, and keeps
  the human's window. Its failure mode is keystroke-injection racing a busy session — gate it on
  `claude agents --json` `status: idle` (F10).

### B — headless controller under a thin helper loop

*Mechanism:* the helper (bash/python, or a nyxloomd verb) runs
`claude -p --session-id <uuid> --model opus --effort xhigh --output-format json <prompt>`,
waits for exit, reads the **first line of `result`** as the verdict —
`CHECKPOINT <hash>` | `DONE <hash>` | `BLOCKED <hash>` | `WAIT-<secs>` — and on `CHECKPOINT` runs
`-p --resume <sid> "/compact $(cat COMPACT.md)"` then `-p --resume <sid> "continue from BRIEF.md"`.
The helper loop **is** `/loop`; `WAIT-<secs>` **is** `ScheduleWakeup`.

- **Measured:** this is exactly the P116 child protocol, run 7 times over 3 compacts with real
  work (F4). Every mechanism is proven; only the *role* is new.
- **Unknown:** whether a `-p` session can use `ScheduleWakeup` at all (F6 says the tool is
  *offered*; the process exits at end of turn, so it is likely inert — V5.3). Whether two
  processes touching one sid ever collide (undocumented; the helper serializes, so moot).
- **Cost/cycle:** same compact + resume cost as A, **plus** the controller loses the harness
  surfaces it uses today: Agent-tool fan-out (F11), Monitor, and the human's live view. Each
  helper iteration is one model turn, so a chatty controller pays one process spawn per turn.
- **Failure modes:** *the hard one is children* (§3). Also: the human can no longer see or steer
  the run except through files and ntfy; a wedged `-p` needs an external timeout (`timeout -k`);
  and the verdict line is a contract the model can violate (mitigate: helper treats an
  unparseable first line as `CHECKPOINT` and re-reads state from disk, never guesses).

### C — checkpoint as a snapshot fork (V3 chain) instead of compact-in-place

*Mechanism:* no summarization call at all. The controller writes its iteration summary as
ordinary output; the helper forks the frozen snapshot, injects the summary + ack, freezes the new
snapshot, and forks *that* for the next cycle.

- **Measured:** V3 PASS on a synthetic 3-iteration chain (haiku): warm fork 641–776 creation, cold
  bounded by the snapshot body; zero pack re-reads; the chain really carried earlier items'
  contracts. V4: parallel forks pay N × body, never 0 (F12).
- **Unknown:** everything about applying it to a *controller*. C's premise is a small, monotone
  state that compresses into a per-iteration summary. A controller's state is broad (ledger,
  gates, package pipeline, three roles in flight) and non-linear; the snapshot grows every cycle
  and needs periodic re-orientation (E-005 thresholds are untested at controller breadth).
- **Cost/cycle:** cheapest in API terms (summary ≈ output tokens, ~$0.05) but each cycle re-pays
  the snapshot body cold (18.5–19.5k creation measured at a 37k snapshot; a real controller
  snapshot is larger).
- **Failure modes:** a new session id per cycle breaks every external reference (ntfy threads,
  task ids, the human's `/resume` list); anticipation risk (the summary omits what cycle N+2
  needed) — mitigated only because durable state is on disk.

---

## 3. The hard part: children do not survive a process boundary

A controller's job is fan-out. Agent-tool children (reviewers, pack assemblers, sweeps) live
**inside** the controller process and die with it (F11 — they are not even addressable
afterwards). So under B, a checkpoint is only legal when no Agent-tool child is in flight, and
under A2 the same is true for a keystroke that triggers compaction.

Three ways out, in order of preference:
1. **Join before cutting.** F8 hands this over for free: a `Stop` hook reads `background_tasks`
   and returns `decision: "block"` ("children still running — do not checkpoint") until the array
   is empty. This is the only option that needs no re-architecture.
2. **Make every child a CLI child.** `nohup claude -p --session-id … > run.json &` + pid file, the
   P116 pattern: the child survives the controller's death and its verdict is recoverable from
   disk. Cost: loses harness notifications (the controller must `Monitor` a pid), and reviewers
   become non-forkable from the controller's context — which the pipeline *wants* anyway (fresh
   reviewers, never forks).
3. **Checkpoint only at pipeline boundaries** (after a merge, before the next carve), where fan-out
   is structurally empty. Cheapest to adopt, but boundary-poor: a controller can run for hours
   between merges, which is precisely the current problem.

---

## 4. Experiments (V5.x) — cheapest first, haiku where the mechanism is model-independent

| id | test | oracle | model | cost |
|---|---|---|---|---|
| **V5.0** | **DONE 2026-08-21.** External process resumes + compacts a session that originated interactive. | PASS — F1/F2/F3 above. | haiku | $0.13 |
| V5.1 | `ScheduleWakeup`/`CronCreate` with `prompt: "/compact <text>"` in a live interactive session. | A `compact_boundary` line with `trigger: "manual"` and a summary reflecting `<text>` appears in that session's JSONL; NOT a user message containing the literal string. | any (run it in a real session — the controller's own next checkpoint is the test) | ~$2 (the compact it wanted anyway) |
| V5.2 | `Stop` hook fires with a non-empty `background_tasks` while an Agent-tool child runs; `decision: "block"` defers the checkpoint. | Hook stdin shows the subagent entry with its `status`; the blocked turn continues; `stop_hook_active` flips true on the next fire. | haiku toy session | <$0.20 |
| V5.3 | Does `ScheduleWakeup` do anything in `-p`? Toy `-p` run calls it with 60 s. | Either the tool errors / the process exits and nothing re-fires (⇒ B must own its own loop), or a wake occurs (⇒ note the mechanism). | haiku | <$0.10 |
| V5.4 | `asyncRewake` hook exits 2 while the session is idle. | Claude wakes with the hook's stderr as a system reminder, with no human keystroke. | haiku | <$0.20 |
| V5.5 | `/loop` + a pending `ScheduleWakeup` across an in-process `/compact`, and across a headless `--resume`. | `session_crons` (read via a `Stop` hook) still lists the entry post-compact; after an external `-p --resume`, state either survives or is provably gone. | haiku | <$0.30 |
| V5.6 | Concurrency: external `-p --resume <sid>` while an interactive process holds the SAME sid live. | Either a clean refusal, or divergence/corruption in the JSONL — if the latter, the helper MUST gate on `claude agents --json` (F10) before touching a sid. | haiku, throwaway session | <$0.20 |
| V5.7 | Children across exit: nohup'd CLI child vs Agent-tool child when the parent `-p` exits. | CLI child pid alive and its `run.json` complete; Agent-tool child gone and unaddressable. Confirms §3. | haiku | <$0.20 |
| V5.8 | **End-to-end 3-cycle dry run**: haiku "controller" on a toy 3-item task under the helper loop, fanning out one CLI child per cycle, verdict protocol `CHECKPOINT|DONE|BLOCKED|WAIT-<secs>`. | 3 cycles complete unattended; every child's output recovered from disk; context at each cycle start ≤ 1.5× the brief; no human keystroke. | haiku | ~$1–2 |
| V5.9 | Chained-compact fidelity: grade cycle-3's summary against a checklist of facts cycle-4 needed. | ≤1 fact lost, and every loss recoverable by a disk re-read (the "summaries are indexes, not archives" rule). Fold into V2 grading. | haiku | ~$0.50 |

V5.1 gates the architecture choice; V5.2 + V5.7 gate the children question; V5.8 is the adoption
gate for a helper. Everything before V5.8 is under $4 total.

---

## 5. Recommendation

1. **Run V5.1 first, in the controller's own next checkpoint — it costs a compaction the
   controller already wanted.** If a cron-submitted `/compact <retention>` is expanded, adopt **A**:
   the controller stays interactive, keeps its Agent-tool fan-out, keeps the human's window, keeps
   one session id, and the whole "helper service" question collapses to *nothing at all* — plus a
   `Stop` hook (V5.2) that blocks a checkpoint while `background_tasks` is non-empty. Verify every
   cycle against the JSONL `compact_boundary`, never the model's belief (F3, and D-142's rule that
   a verdict is read in a separate step).
2. **If V5.1 fails, adopt A2 before B**: a ~60-line watcher that polls `claude agents --json`
   for `status: idle` + a deposited `CHECKPOINT` marker file, then `tmux send-keys` the compact
   line. Same properties as A, one crude primitive, no re-architecture of fan-out.
3. **B is the fallback, and it is a real cost**, not a neutral change: it trades the controller's
   Agent-tool fan-out and human visibility for mechanical determinism. Take it only if A and A2
   both fail, and take it with §3 option 2 (every child a CLI child) already in place.
4. **C is not the controller's pattern.** Keep it where V3 proved it: worker chains and role
   fan-out off one frozen snapshot. A controller's breadth and its stable external identity are
   exactly what C gives up.
5. **Is a helper service required? No — a helper *loop* is, and only under B/A2.** It is ~150
   lines (launch, watch exit, parse a first-line verdict, compact, resume) and belongs in **B46
   generalized**: B46 already owns `compact <sid> --prompt-file` and `advance-chain <task>`; this
   adds a third verb, `run-loop <sid> --prompt-file --brief <path>`, and one new capability —
   *watching a session it did not start* (F10 makes that a poll, not an integration). Build it as
   a standalone script proven on dstdns first; fold into nyxloomd only after V5.8 passes, so the
   daemon never becomes the thing standing between the operator and a wedged controller.
