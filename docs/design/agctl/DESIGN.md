# agctl: agent control (design note, rev 4, 2026-11-24)

Status: DRAFT rev 4 (P-plat-07, closes CTL-49); current design only, rev 3 and its measurements in `archive/DESIGN-rev3.md`. Requirements: `docs/design/ctlstat/`, `nyxloom/docs/design-context-lifecycle*.md` (this note wins). Inputs: T1 plan §2 B9-B16, the 2026-11-24 controller-tooling review (H1-H7, M1-M6).

## 1. Scope

agctl owns controller identity and handover, the run registry, routes, agent finalization and priming, and status. Not owned: placement (fleetd), host protection (thrashguard), telemetry (hostobs), log extraction (`nyxloom extract`, a dependency), Claude Code's `idleCompaction`/Monitor.

**Transport is the fleetd client** (P-fd-05); agctl has none of its own. Reason: one shared host runner (CTL-51). `hx.sh` stays until then.

## 2. The package (exists now)

- **Created now, not after finalize/prime.** Reason: heredoc Python cannot be imported, so parsers exist 3-4 times (H3); waiting grows bash.
- **`agctl/` at the repo root**, cmru-managed: `src/agctl/{config,routes,runs,streams,handover,cli}.py`, `tests/` (pytest, recorded streams), README, CHANGES, backlog `AGC-NN`, lane `tests`. Reason: the estate's tool shape; lanes and releases apply unchanged.
- **Stdlib plus cli-extended** (CLI, `register_skills_verbs`). Until T2 P7.0b (P-core-05) puts the wheel in tester-unified, the lane and shims set `PYTHONPATH=libraries/cli-extended/src`. Reason: a vendored copy is a second truth.
- **Bash freeze (B9):** no new bash in `scripts/controller/` beyond 50-line entry points (exception: P-plat-02).
- **Skills are package data** (`agctl skills install|check|list|uninstall`); `controller-handover` moves in, `skills-sync.sh` retires. Reason: a skill lives with its tool (operator 2026-11-18).
- **`agctl.streams` ports each parser once:** `lib.sh:228-344` (Codex usage-limit retry), `opendesign-accounts.py:202-283` (OD and pi resets) and `:317` (thread in stream), `codex-impl.sh:268` (Codex thread id), `pi-impl.sh:371` (pi session id, final message), `job-check.sh:37` (thread recovery). pi's outcome vocabulary (`pi-impl.sh:6-37,404-440`) becomes the shared classifier and `pi-live.py` becomes `live.json` for every harness. Reason: Codex runs only get `CODE-EXIT` and no liveness (M1).

## 3. Identity and controller state

- **`CONTROLLER_NAME`**, explicit in the launch environment; ctl-doctor warns when unset. Reason: hook input has no session name, and a controller outlives its sessions (the UUID is only the binding).
- **`~/controller-state/<name>/`** holds `HANDOFF.md`, `handoffs/`, `plan.yaml`, `operator-decisions.md`, `READY`, `binding.json`, hook state. Reason: outside every worktree, one dir per controller. The main controller migrates last, leaving a pointer in `.controller-state/`.
- **`binding.json`** (session id, transcript, last seen), written by the hook; a second live session under one name gets a warning, not a refusal. Reason: detection suffices for a few controllers; a lease needs `agctld`.
- **HANDOFF.md is the one current handover**, rewritten whole; `agctl handover archive` moves the previous one to `handoffs/NNNN-<utcYmdTHMS>.md`. It opens with a generated header (`agctl handover header`: branches and heads, in-flight runs, worktrees, monitors, leases), then the skill's prose. Reason: a successor must never merge superseding prose (H7: 394 KB, 39 blocks).
- **plan.yaml:** one `state` key (`open|running|blocked|parked|done`), schema-checked by `agctl handover check` (ctl-doctor until then); PROGRESS entries move to `.controller-state/archive/plan-progress-2026-11.yaml`. Reason: a plan is a backlog, not a journal.
- **`$CONTROLLER_HOME`** (default `~/controller`, per devcontainer): env file, host table, gate slots, `agctl.toml`, `routes.local.toml`, `runs/`.
- **No `.env.<name>` renames** (CTL-34 dropped); new config is TOML. Reason: renaming files that are going away is churn.

## 4. Run registry

- **One runs dir, `$CONTROLLER_HOME/runs`.** Reason: three dirs (35/211/431 runs) made status read the wrong one (H1).
- **Generated id `<label>-<utcYmdTHMS>-<rand4>`** (e.g. `p12-20261124T101502-3fa9`, rand4 hex from `secrets`); the caller passes only `--label`. Reason: a reused name was refused into an unread log and the old result read as new (CTL-01); reuse is now impossible, so FORCE/retire go away.
- **`runs/<id>/`:** `run.json`, `stream.jsonl`, `stderr`, `last.txt` (always derived from the stream, every harness; CTL-38), `live.json`, `lock` (kernel flock held by the wrapper, fd closed before the child exec), later `finalization.json`.
- **`run.json`** (`agctl-run/1`): id, label, route, harness, model, effort, worktree, base (`--base`, default `origin/main`) and head resolved at start, prompt path and `prompt_sha256`, owner (`CONTROLLER_NAME`), thread, `resumes`, pid, started, ended, state, outcome, exit; rewritten whole via temp file, fsync, rename. Reason: no torn reads; one record answers "which run, which head".
- **State** `launching → running → ended`, or `limited`. **Outcome:** pi's classes plus CONCURRENCY-LIMIT (OD `tier_limit_exceeded`) and DIED (lock free, no end recorded).
- **`agctl run --route R --label L --worktree W --prompt P [--base REF] [--report PATH] [--detach]`.** Every refusal (unknown route, missing key, bad worktree, unreadable prompt) is synchronous, exit 2, and creates no run dir. Then the dir is created, `RUN <id>` is printed, and only then does `--detach` fork (replacing `nohup … &`). Reason: the caller sees the refusal or the id before it stops watching (H1).
- **`agctl run --resume <id>`** creates a new run with `resumes=<id>`, same thread and account. Reason: one record per child keeps writes and the lock simple; the thread links attempts.
- **Readers:** `agctl status` (a row per run); `agctl check <id>` (job-check's evidence half: trailer, report, clean tree, reviewed head vs run.json head as a field compare; flags direct `hx.sh`/docker/pytest/cargo in implementer streams, CTL-46); `agctl limits` (read-only, covers pi).
- **One-release transition:** readers also read legacy flat `<name>.*` files in `[runs] legacy_dirs` (default `CONTROLLER_RUNS_DIR`, `~/c5-runs`); `runs/` subdirs without a valid `run.json` are ignored (other tools use `runs/`). `codex-impl.sh`, `pi-impl.sh` and `codex-review.sh` become shims mapping their run name to `--label`. Reason: pre-named briefs keep working one release (review Q4).

## 5. Routes

- **Tracked `agctl/src/agctl/routes.toml` (package data) plus overlay `$CONTROLLER_HOME/routes.local.toml`** (`~/controller/routes.local.toml`), merged key by key; an overlay-only table is a new route. Reason: a new route took about nine edits (H2); per-machine paths stay out of git.
- **Readers:** agctl run, the shims, job-check (trailers), opendesign-accounts, `agctl limits`; bash via `agctl route show <r> --format nul|json`. Docs link, never copy.
- **Fields of `[route.<name>]`:** `harness` (`codex|pi|opencode`), `account`, `home` (Codex, overlay) or `provider` (pi), `model`, `effort` (omitted = catalog default; no wrapper override, CTL-39), `key_var` (the one variable loaded from `~/.env.ai` into the child, length-checked, never printed; B12), `catalog`, `family` (`openai|deepseek|anthropic`), `trailer` (exact line), `window` (`openai-subscription|opendesign-balance`), `concurrency` (account cap, shown, never enforced), `on_limit` (`resume` everywhere today: the run ends `limited` and resumes on the same thread once the window or cap clears; never a fresh relaunch).
- **Routes today** (homes in the overlay):

| Route | Harness | Account | Model | Effort | `key_var` | Home / provider |
|---|---|---|---|---|---|---|
| luna | codex | openai | gpt-6-luna | xhigh | none | `~/.codex2` |
| sol (review) | codex | openai | gpt-6-sol | xhigh | none | `~/.codex` |
| deepseek-{A} | codex | A | deepseek-v4.1-flash-eco | catalog (max) | `OPENDESIGN_API_KEY_{A}` | `~/.codex-opendesign-{a}` |
| deepseek-pi-{A} | pi | A | same | catalog | same | `opendesign_{a}` |

  A = VOLKER or GITHUB2 (a = lower case). Trailers: `Codex gpt-6-luna`, `Codex gpt-6-sol`, `Codex deepseek-v4.1-flash` (pi too, §11). volker: `concurrency = 10`.
- **Aliases** `deepseek`, `deepseek-pi` are `[alias]` lists in drain order (volker, then github2), reordered by OD account state (soonest reset, then least recently blocked); never a refusal for "blocked". Reason: local "blocked" is stale; the provider's 402 is the authority (B11; `pick_available` exit 3 goes, H5).
- **A missing key is a synchronous refusal** (configuration, not usage). Both OD keys exist in `~/.env.ai`; each route loads only its own.
- **Per-session budgets are a main-controller decision, not routes.toml data** (today: drain volker first, then +1 concurrent on github2 per sub-controller).
- **Usage never blocks a launch** (operator 2026-11-18): no refusing or warning preflight; limits are information. On volker `tier_limit_exceeded` (10 sessions estate-wide) the run ends CONCURRENCY-LIMIT and is resumed on the same thread later, never relaunched fresh. Reason: a fresh run loses the thread's work and adds a session against the cap.
- **Picker last** (P-plat-22): `agctl route pick`, advisory, soonest-reset allowance first (generalising `CONTROLLER_OD_PREFER_EXPIRING`), reviews on another `family`.

## 6. Controller context and handover

**Decision (operator 2026-11-18 22:50Z): auto-compaction is not a safe handover; the controller never relies on it.** Reason (measured): no model turn follows the trigger, a hook can only append to the summary prompt, and HANDOFF.md lagged one by 39 minutes. The controller writes one handover plus a plan sync, ends, and a fresh session continues:

1. **Below the soft threshold: keep working** (operator 2026-11-15, never idle); notes give fill, growth and projected calls to the threshold.
2. **Past it: finish the current step, then once** (not per merge batch, rejected as an extra call each time): the `controller-handover` skill writes the prose under the generated header; sync `plan.yaml`; `agctl handover check` (non-zero on drift against git, registry, worktrees or plan schema); `agctl handover ready` writes `READY` by atomic rename (name, handover path and sha256, repo head, session id, UTC); end the turn.
3. **Fresh session, started by the operator** (new session or `/clear`). A controller-only SessionStart[startup|clear] hook (only with `CONTROLLER_NAME`) reads `READY`, checks hash and head, injects "read <handover> first (head <sha>, written <utc>)", and renames it `READY.consumed.<session>`. Missing or mismatched: the note says so and gives the current HANDOFF's age.
4. **Auto-compaction is only the backstop** (window above the soft threshold, set by the operator). If it fires, SessionStart[compact] warns the handover predates it and asks for a new one first.

- **A successor without the operator's click** is AG-2 E6's question (`claude --bg` plus attach, P-plat-20); slice 1 builds no TUI.
- **External `/compact` of a live interactive session: never** (collision untested, not needed).
- **`agctld` claiming READY by rename** (after `end_turn`, no pending `tool_use`, no background tasks; fail open) is deferred.
- **Lean handover:** pointers, no re-attached files.

## 7. Agents: return contract, finalization, priming (slice 2)

- **Implementers and reviewers are bounded and succeeded, never compacted.**
- **Return contract** (docs now, P-plat-03): `DONE|PARTIAL|BLOCKED`; BLOCKED = external obstacle only; a budget stop is never BLOCKED. PARTIAL at a mechanical boundary (commit landed, tree clean, focused tests green) with done-up-to, deferred, tips, read-first list, state file, resume commands, next step; `runs/<id>/finalization.json` is authoritative.
- **`agctl finalize`** (P-plat-21) checks the boundary, collects commits since base, dirty state and test commands mechanically, and asks the agent only for judgement; `--from-log` (`UNREPORTED`) uses `nyxloom extract --successor-brief`, the classifier and git.
- **`agctl prime <id> --mode fresh`**: a new run from the successor brief; refuses an incomplete finalization; automatic only when complete, else the controller confirms. Fork/resume modes deferred.

## 8. Slices, in order

1. **Slice 1:** (a) **run registry**: skeleton P-plat-09, routes P-plat-10/11, then `agctl run` and `status|check|limits` (P-plat-12a/b); (b) **handover cycle**: single HANDOFF and plan schema (P-plat-13), then header/check/ready, primer, `CONTROLLER_NAME`, state dirs (P-plat-15), main controller last; (c) **hook**: done separately in P-plat-05 (no agctl code; may land first; its note names manual steps until P-plat-15). Return-contract docs now (P-plat-03).
2. **Slice 2:** `finalize --from-log`, `prime fresh` (P-plat-21).
3. **Then:** AG-2 E6 (P-plat-20, after 1b); backlog generator (P-plat-16); scheduling glue replaced by `agctl run --after`, `agctl skills`, `agctl worktrees prune` (P-plat-23); **picker last** (P-plat-22).

Reason: the week's costly incidents were all controller-side (M6). Each package: tests, a lane, at most 2 review rounds.

## 8a. The hook: one threshold

The tracked `scripts/controller/hooks/ctx-hooks.py` is wired (operator re-wires `settings.local.json`); `.controller-state/hooks/ctx.py` is deleted after one clean day. Reason: continuity-critical code was untracked and contradicted 22:50Z (H6).

- **Config** `$CONTROLLER_HOME/agctl.toml` (path override `CTX_AGCTL_TOML`): `[context.controller] soft = 650000`, `[context.spawned] soft = 200000` (tokens; main controller 2026-11-24). `CTX_SOFT` overrides the session's own kind (`CTX_SESSION_KIND`); `CTX_CONTROLLER_SOFT` is kept as an alias for the controller kind. A bad or missing file is logged once and defaults apply. Reason: rev 3 specified seven keys before one note was measured (M6); the soft threshold is the only decision point.
- **Harness window** (the ceiling, informational; the operator set 800k): `modelSettings.<model>.autoCompactWindow` in `~/.claude/settings.json`, else the top-level value; shown in notes, with a warning when it is at or below `soft`.
- **Constants:** arm 100000, a note every 50000, growth = mean per tool call over the session's own last 20 calls, projection `(soft - fill) / growth` tool calls.
- **Notes:** below soft "Context {fill}k of soft {soft}k (harness window {win}k); growth {g}k per tool call; about {n} tool calls to the soft threshold." Controller past soft: "Past the soft threshold. Finish the current step, then write the handover (controller-handover skill), sync plan.yaml, run `agctl handover check` and `agctl handover ready`, and end the turn. Do not start new work." Spawned past soft: "Reach a semantic boundary and return PARTIAL with a finalization; a budget stop is never BLOCKED." The read order comes from the handover's read-first list, never hard-coded plan ids.
- **Learning log** `steering-log.jsonl` beside `hook-log.jsonl` (ts, kind, fill, growth, projected calls, note id, later outcome), reviewed before automation is trusted (operator 2026-11-18).

## 9. Operator decisions this note rests on

2026-11-18: name, skills with the tool, `CONTROLLER_NAME`, learning log; usage never blocks a launch; 22:50Z handover. 2026-11-24: review defaults accepted; volker cap 10, same-thread resume; github2 keyed; soft 650k/200k.

## 10. Deferred, limits, unverified

- **Deferred:** `agctld`, the TUI, fork/resume priming, name lease/adopt, the opencode adapter (`opencode-impl.sh` unchanged until then), a Rust session extractor.
- **Limit:** estate-wide OD concurrency is not counted in v1 (`concurrency` sees only this devcontainer's registry); per-sub-controller budgets set by the main controller are the mechanism.
- **Unverified:** E6 `--bg`; whether SessionStart `additionalContext` reliably primes a fresh session; whether pi accepts `PI_THINKING=max`.

## 11. Open questions

1. **pi trailer (operator):** H2 says pi should not carry the Codex DeepSeek trailer; pi keeps today's value until the operator picks a line.
