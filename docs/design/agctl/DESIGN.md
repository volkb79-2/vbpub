# agctl: agent control (design note, rev 4, 2026-11-24)

Status: DRAFT rev 4 (P-plat-07, closes CTL-49); rev 3 in `archive/DESIGN-rev3.md`. Requirements: `docs/design/ctlstat/`, `nyxloom/docs/design-context-lifecycle*.md` (this note wins). Inputs: T1 plan §2 B9-B16, the 2026-11-24 controller-tooling review (H1-H7, M1-M6).

## 1. Scope

agctl owns controller identity and handover, the run registry, routes, agent finalization and priming, and status. Not owned: placement (fleetd), host protection (thrashguard), telemetry (hostobs), log extraction (`nyxloom extract`, a dependency), Claude Code's `idleCompaction`/Monitor.

**Transport is the fleetd client** (P-fd-05, CTL-51); `hx.sh` stays until then.

## 2. The package (exists now)

- **Created now, not after finalize/prime:** heredoc Python cannot be imported, so parsers exist 3-4 times (H3).
- **`agctl/` at the repo root**, cmru-managed: `src/agctl/{config,routes,runs,streams,handover,cli}.py`, `tests/` (pytest, recorded streams), README, CHANGES, backlog `AGC-NN`, lane `tests`.
- **Stdlib plus cli-extended** (CLI, `register_skills_verbs`); until T2 P7.0b (P-core-05) puts the wheel in tester-unified, the lane and shims set `PYTHONPATH=libraries/cli-extended/src` (no vendored copy).
- **Bash freeze (B9):** no new bash in `scripts/controller/` beyond 50-line entry points (exception: P-plat-02).
- **Skills are package data** (`agctl skills install|check|list|uninstall`); `controller-handover` moves in, `skills-sync.sh` retires (operator 2026-11-18).
- **`agctl.streams` ports each parser once:** `lib.sh:228-344` (usage-limit and OD/pi 402 resets), `opendesign-accounts.py:202-283` (OD and pi resets) and `:317` (thread in stream), `codex-impl.sh:268` (Codex thread id), `pi-impl.sh:371` (pi session id, final message), `job-check.sh:37` (thread recovery). pi's outcome vocabulary (`pi-impl.sh:6-37,404-440`) becomes the shared classifier and `pi-live.py` becomes `live.json` for every harness. Codex mapping: `turn.completed` with a final agent message → `stop`; reasoning-only end → EMPTY-COMPLETION; `turn.failed` "usage limit" / `insufficient_balance` → USAGE-LIMIT; `tier_limit_exceeded` → CONCURRENCY-LIMIT; anything else, or a nonzero exit → ERROR. (M1)

## 3. Identity and controller state

- **`CONTROLLER_NAME`**, explicit in the launch environment (hook input has no session name); ctl-doctor warns when unset.
- **`~/controller-state/<name>/`** holds `HANDOFF.md`, `handoffs/` (the controller's own archive, distinct from `.controller-state/handovers/` successor briefs), `plan.yaml`, `operator-decisions.md`, `READY`, hook state. Outside every worktree. The main controller migrates last (today `.controller-state/` and `/workspaces/vbpub/plan.yaml`), leaving a pointer.
- **HANDOFF.md is the one current handover**, rewritten whole; `agctl handover archive` moves the previous one to `handoffs/NNNN-<utcYmdTHMS>.md`. It opens with a generated header (`agctl handover header`), then the skill's prose. Header sources: branches, heads and worktrees from `git worktree list --porcelain`; runs from the registry (non-ended); leases = held gate-slot and remote-gate lock files under `$CONTROLLER_HOME`; monitors are not readable by agctl, so the header prints `monitors: see prose`. A successor never merges superseding prose (H7).
- **plan.yaml**, schema-checked by `agctl handover check` (ctl-doctor until then): top level `rules: [str]`, `items: [{id (unique), title, state, owner, next?, depends_on? [ids], source?, priority?}]`; `state` is `open|running|blocked|parked|done`; unknown keys are errors. Map: planned → open; awaiting-review, fix-round, mergeable, active, in_progress → running; decision → blocked; merged, released → done. PROGRESS entries move to `.controller-state/archive/plan-progress-2026-11.yaml`.- **`$CONTROLLER_HOME`** (default `~/controller`, per devcontainer): env file, host table, gate slots, `agctl.toml`, `routes.local.toml`, `runs/`. No `.env.<name>` renames (CTL-34 dropped).

## 4. Run registry

- **One runs dir, `$CONTROLLER_HOME/runs`** (H1: three dirs made status read the wrong one).
- **Generated id `<label>-<utcYmdTHMS>-<rand4>`** (e.g. `p12-20261124T101502-3fa9`, rand4 hex from `secrets`); the caller passes only `--label`. Label `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, no `..` (the legacy name grammar, so shims pass names through). The dir is created with exclusive `mkdir`; on EEXIST a new rand4 is drawn. Reuse is impossible (CTL-01), so FORCE/retire go away.
- **`runs/<id>/`:** `run.json`, `stream.jsonl`, `stderr`, `events.jsonl`, `last.txt` (always derived from the stream, every harness; CTL-38), `live.json`, `lock` (kernel flock held by the wrapper, fd closed before the child exec), later `finalization.json`.
- **`run.json`**, rewritten whole via temp file, fsync, rename. **Fields** (`schema: "agctl-run/1"`): `id, label, route, harness, model, owner` str; `effort, thread, resumes, report` str|null; `worktree, prompt` absolute paths; `prompt_sha256, base, head` hex (base and head resolved at start; `--base`, default `origin/main`); `pid, exit, continues` int|null; `started, ended` ISO-8601 UTC `Z`|null; `state` `launching|running|ended`; `outcome` class|null. Only the lock holder writes it.
- **Outcomes:** pi's classes, CONCURRENCY-LIMIT (OD `tier_limit_exceeded`; fixture `edge-p01-053626.jsonl`; never marks the account blocked) and DIED, which readers derive (lock free, state not `ended`) and never write. USAGE-LIMIT and CONCURRENCY-LIMIT are the `on_limit` cases. A route's `continues` (in-run same-thread continues for EMPTY-COMPLETION / reasoning-only ends) stays inside one run and appends to `stream.jsonl` and `events.jsonl`; `--resume` is the only new-record path.
- **`agctl run --route R --label L --worktree W --prompt P [--base REF] [--report PATH] [--detach]`.** Every refusal (unknown route, missing key, bad worktree, unreadable prompt, unreadable or unsafe catalog) is synchronous, exit 2, and creates no run dir. The parent takes `flock` on `runs/<id>/lock`, writes `state=launching`, prints `RUN <id>` as stdout line 1 (flushed), then forks. The child inherits the lock, calls `setsid` and takes stdin `/dev/null`; the parent exits. `--detach` replaces `nohup … &`. (H1)
- **`agctl run --resume REF`** creates a new run with `resumes=<id>`, same thread and account. REF is a run id or a thread id found in a run.json or legacy `.thread`; route, account, worktree and label come from that run; `--prompt` is required.
- **Exit codes:** `run`: 2 refusal; `--detach` 0 after `RUN`; foreground: the child's exit. `check`: 0 clean, 1 red flag, 2 usage. `handover check`: 0, 1 drift, 2 usage. `route show`/`status`/`limits`: 0, 2 unknown route or bad config.
- **Readers** take an id or a label (label → newest run): `agctl status` (a row per run); `agctl check` (job-check's evidence half: trailer, report, clean tree, reviewed head vs run.json head as a field compare; flags direct `hx.sh`/docker/pytest/cargo in implementer streams, CTL-46); `agctl limits` (read-only, covers pi).
- **One-release transition:** readers also read legacy flat `<name>.*` files in `[runs] legacy_dirs` (agctl.toml), default `CONTROLLER_RUNS_DIR`, `$CONTROLLER_HOME/runs` (flat files) and `~/c5-runs`; `runs/` subdirs without a valid `run.json` are ignored. `codex-impl.sh`, `pi-impl.sh` and `codex-review.sh` become shims, switched in 12b with the readers. Shim map: `CODEX_ROUTE`/`PI_ROUTE` → `--route` (defaults `luna`/`deepseek-pi`); `codex-review.sh` → `--route sol`; run name → `--label`; `--report`/`PI_REPORT` → `--report`; `CODEX_MODEL|CODEX_EFFORT|PI_MODEL|PI_THINKING|CONTROLLER_RUN_FORCE` ignored with one stderr line (CTL-39); `resume <thread>` → `--resume <thread>`. Limit: `job-status`, `ctl-status` and `resume-usage-limited` see legacy runs only; limited runs are resumed by the controller with `agctl run --resume`.

## 5. Routes

- **Tracked `agctl/src/agctl/routes.toml` (package data) plus overlay `$CONTROLLER_HOME/routes.local.toml`**; an overlay-only table is a new route or model. The overlay replaces whole field values (lists too), and `[alias]` entries likewise. Load fails with exit 2 naming file, route and field on an unknown field or model, or on a selected route missing a required one (a Codex route needs `home`). (H2: a route took nine edits.)
- **Readers:** agctl run, the shims, job-check (trailers), opendesign-accounts, `agctl limits`; bash via `agctl route show <r> --format nul|json` (`nul`: `field=value\0` in schema order, absent fields omitted; `trailer` from the model). Docs link, never copy.
- **Fields of `[route.<name>]`:** `harness` (`codex|pi|opencode`), `account`, `home` (Codex) or `provider` (pi), `model` (key of a `[model.<id>]` table holding `trailer`, str, full line; quote ids with dots), `effort` (omitted = catalog default; no wrapper override, CTL-39), `key_var` (the one variable loaded from `~/.env.ai` into the child, length-checked, never printed; B12), `catalog`, `family` (`openai|deepseek|anthropic`), `window` (`openai-subscription|opendesign-balance`), `continues`, `concurrency` (account cap, shown, never enforced), `on_limit` (`resume` everywhere today: the run ends with the limit outcome and resumes on the same thread once the window or cap clears; never a fresh relaunch). Types: `harness, family, window, on_limit` enums; `account, model, provider, effort, key_var` str; `home, catalog` path (`~` expanded); `continues` int ≥0 (luna/sol 0, deepseek and pi 2); `concurrency` int.
- **Routes today:**

| Route | Harness | Account | Model | Effort | `key_var` | Home / provider |
|---|---|---|---|---|---|---|
| luna | codex | openai | gpt-6-luna | xhigh | none | `~/.codex2` (this overlay; example `~/.codex`) |
| sol (review) | codex | openai | gpt-6-sol | xhigh | none | `~/.codex` |
| deepseek-{a} | codex | a | deepseek-v4.1-flash-eco | catalog (max) | `OPENDESIGN_API_KEY_{A}` | `~/.codex-opendesign-{a}` |
| deepseek-pi-{a} | pi | a | same | catalog | same | `opendesign_{a}` |

  a = volker or github2 (A = upper case). **Trailer rule (operator 2026-11-24):** the trailer names the MODEL only; the harness is irrelevant: `Co-Authored-By: <model display name> <noreply@<model provider domain>>`, e.g. `Co-Authored-By: DeepSeek V4.1 Flash <noreply@deepseek.com>` for every DeepSeek route (Codex and pi alike); Claude keeps its existing lines. routes.toml holds it once per model (`[model.<id>] trailer`), routes reference the model; job-check's trailer check reads it from there. volker: `concurrency = 10`.
- **Aliases** `deepseek`, `deepseek-pi` are `[alias]` lists (volker, github2). Try the first unblocked entry in list order; blocked entries go last, ordered by earliest `blocked_until`. No failover inside one launch. Never a refusal for "blocked": the provider's 402 is the authority (B11; `pick_available` exit 3 goes, H5).
- **A missing key is a refusal** (configuration, not usage); each route loads only its own key.
- **Per-session budgets are a main-controller decision, not routes.toml data** (today: drain volker first, then +1 on github2 per sub-controller).
- **Usage never blocks a launch** (operator 2026-11-18): no preflight. On volker `tier_limit_exceeded` (10 sessions estate-wide) the run ends CONCURRENCY-LIMIT and is resumed on the same thread later: a fresh run loses the thread's work and adds a session against the cap. Picker: P-plat-22.

## 6. Controller context and handover

**Decision (operator 2026-11-18 22:50Z): the controller never relies on auto-compaction** (measured: no model turn follows the trigger; HANDOFF.md lagged one by 39 minutes).

1. **Below the soft threshold: keep working** (never idle); notes give fill, growth and projected calls.
2. **Past it: finish the current step, then once:** the `controller-handover` skill writes the prose (pointers, no re-attached files) under the generated header; sync `plan.yaml`; `agctl handover check` (drift against git, registry, worktrees or plan schema); `agctl handover ready` writes `READY` by atomic rename (name, handover path and sha256, repo head, session id, UTC); end the turn.
3. **Fresh session, started by the operator** (new session or `/clear`): a controller-only SessionStart[startup|clear] hook (only with `CONTROLLER_NAME`) checks `READY`'s hash and head, injects "read <handover> first (head <sha>, written <utc>)", and renames it `READY.consumed.<session>`; missing or mismatched, the note says so and gives HANDOFF's age.
4. **Auto-compaction is only the backstop** (window above soft); SessionStart[compact] warns the handover predates it and asks for a new one.

## 6a. Successor restart (the mechanism of the handover cycle)

Step 3 of section 6 ("fresh session, started by the operator") has a mechanical form today (tmux; bash, grandfathered under the CTL-40 freeze as proven entry points sharing `resume-lib.sh`). **Default:** `scripts/controller/compact-resume.sh <pane> <guidance-file> <prompt-file> <log>` waits for an idle pane, sends `/compact <guidance>`, waits for the compaction to end and types the prompt file; the context stays as a summary and background work survives (measured 2026-11-24: 442k -> ~60k in ~46 s). **Clean slate:** `clear-resume.sh <pane> <prompt-file> <log>` sends `/clear` instead (proven 5 times). A session or its controller can thus restart it after `agctl handover ready` without a click; the prompt is the "read <handover> first" line the SessionStart hook would otherwise inject. Footer markers tied to Claude Code 2.1.331; the message gap is untested (AGENTS.md "Successor restart of an interactive session"). **Future verb:** `agctl successor <pane> <prompt-file> [--clear]` (Python port, compact by default, `--clear` for the clean slate, the same idle/type contract and exit codes, the markers in `agctl.toml`).

## 7. Agents: return contract, finalization, priming (slice 2)

- **Implementers and reviewers are bounded and succeeded, never compacted.**
- **Return contract** (docs now, P-plat-03): `DONE|PARTIAL|BLOCKED`; BLOCKED = external obstacle only, never a budget stop. PARTIAL at a mechanical boundary (commit landed, tree clean, focused tests green) with done-up-to, deferred, read-first list, resume commands, next step; `runs/<id>/finalization.json` is authoritative.
- **`agctl finalize`** (P-plat-21) collects commits, dirty state and test commands mechanically and asks the agent only for judgement; `--from-log` uses `nyxloom extract --successor-brief`, the classifier and git. **`agctl prime <id> --mode fresh`** starts a new run from the brief; refuses an incomplete finalization.

## 8. Slices, in order

1. **Slice 1** (the costly incidents were controller-side, M6): (a) **run registry**: P-plat-09, routes P-plat-10/11 (P-plat-11 acceptance also: a non-OD key in ~/.env.ai is not visible to the child), `agctl run`, `status|check|limits` (P-plat-12a/b); (b) **handover cycle**: P-plat-13, then header/check/ready, primer, `CONTROLLER_NAME`, state dirs (P-plat-15), main controller last; (c) **hook**: P-plat-05 (no agctl code); (d) **estate header** (operator 2026-11-24; P-plat-15, hook side in `ctx-hooks.py`): a compact block in tool-call results via the hook's `additionalContext`: merge queue (the MERGE QUEUE line of the main controller's current handover; absent: unmerged worktree branches and heads), leases, live runs (registry), each peer's last hand-over (from the `~/controller-state/<name>/` dirs), HANDOFF age. Sources are the §3 header sources. Emitted only when its content changed since this session's last emission (a digest per session in the hook's state dir); hard cap ~1k tokens (4 KB), truncated with a `[truncated]` marker, because every injection costs context. Return-contract docs P-plat-03.
2. **Slice 2:** P-plat-21. **Then:** P-plat-20 (after 1b), P-plat-16, P-plat-23 (`run --after`, `skills`, `worktrees prune`), picker P-plat-22 last. Each package: tests, a lane, at most 2 review rounds.

## 8a. The hook: one threshold

The tracked `scripts/controller/hooks/ctx-hooks.py` is wired (operator re-wires `settings.local.json`); `.controller-state/hooks/ctx.py` is deleted after one clean day (H6: untracked, contradicted 22:50Z).

- **Config** `$CONTROLLER_HOME/agctl.toml` (override `CTX_AGCTL_TOML`): `[context.controller] soft = 650000`, `[context.spawned] soft = 200000` (tokens). `CTX_SOFT` overrides the session's own kind (`CTX_SESSION_KIND`); `CTX_CONTROLLER_SOFT` stays an alias for the controller kind. A bad or missing file is logged once; defaults apply. The soft threshold is the only decision point (M6).
- **Harness window** (ceiling, informational; operator set 800k): `modelSettings.<model>.autoCompactWindow` in `~/.claude/settings.json`, else the top-level value; shown in notes, warning when at or below `soft`.
- **Constants:** arm 100000, a note every 50000, growth = mean per tool call over the session's own last 20 calls, projection `(soft - fill) / growth` tool calls.
- **Notes:** below soft "Context {fill}k of soft {soft}k (harness window {win}k); growth {g}k per tool call; about {n} tool calls to the soft threshold." Controller past soft: "Past the soft threshold. Finish the current step, then write the handover (controller-handover skill), sync plan.yaml, run `agctl handover check` and `agctl handover ready`, and end the turn. Do not start new work." Spawned past soft: "Reach a semantic boundary and return PARTIAL with a finalization; a budget stop is never BLOCKED." The read order comes from the handover's read-first list, never hard-coded plan ids.
- **Estate header** (§8 1d): the same hook appends it to `additionalContext` on change only, digest in its state dir, cap 4 KB.
- **Learning log** `steering-log.jsonl` beside `hook-log.jsonl` (ts, kind, fill, growth, projected calls, note id, later outcome), reviewed before automation is trusted.

## 9. Deferred, limits, unverified

- **Deferred:** `agctld` (incl. claiming READY by rename), the TUI, fork/resume priming, name lease/adopt and `binding.json` duplicate-session detection, the opencode adapter (`opencode-impl.sh` unchanged until then), a Rust session extractor; a successor without the operator's click is AG-2 E6 (P-plat-20); external `/compact` of a live session: never.
- **Limit:** estate-wide OD concurrency is not counted in v1 (`concurrency` sees only this devcontainer's registry); per-sub-controller budgets set by the main controller are the mechanism.
- **Unverified:** E6 `--bg`; whether SessionStart `additionalContext` reliably primes a fresh session; whether pi accepts `PI_THINKING=max`.

## 10. Open questions

None. (Closed 2026-11-24: the pi trailer question is decided by the §5 trailer rule.)
