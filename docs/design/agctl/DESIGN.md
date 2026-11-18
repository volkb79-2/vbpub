# agctl: agent control (design note, 2026-11-18, rev 3: interview decisions folded into the body)

Status: DRAFT, reviewed once (Opus, 2026-11-18; corrections folded in below and marked "rev 2"). Rev 3 (2026-11-18, after the operator interview, section 11) replaces the fixed ceiling and the fixed 200k wording by soft steering from `agctl.toml`, and places the E6 and collision tests after the devcontainer rebuild (plan item AG-2); where a body section still says "fixed ceiling" the rev 3 wording in sections 4, 5, 8 and 9 wins. Requirements and measured facts are in `docs/design/ctlstat/` (REQUIREMENTS.md, HARNESS-COMPACTION-CHECKLIST.md, EXPERIMENT-compaction-*.md) and `nyxloom/docs/design-context-lifecycle*.md`; this note decides the shape of the project that unifies them and records the operator's decisions of 2026-11-17/18. Where this note and the ctlstat requirements differ, this note wins and says so.

## 1. Why one project, and its name

The controller work (state, wrappers, gates, host transport), the estate status tool (`ctlstat`: one mechanical call for an informed decision) and the session lifecycle service (compactor, restarter, primer, TUI) share one subject: **controlling long-lived agent sessions and their work**. They share identity (a controller or agent has a name), state (handoffs, run records, session logs) and consumers (the controller deciding what to do next). Name: **`agctl`** (operator, 2026-11-18). Harness-neutral by contract, Claude-aware through one adapter.

Not owned by `agctl`: campaigns and placement on hosts (`fleetd`), host protection (`thrashguard`), telemetry and profiling (`hostobs`), session-log extraction (`nyxloom extract`, kept as a dependency: it is shipped, tested, and already has the `readback` and `--successor-brief` presets). `agctl` reads their sockets; it never probes hosts itself. Claude Code already has `idleCompaction` and `asyncRewake`/Monitor: `agctld` must not duplicate them (rev 2).

## 2. Components

| Component | Role | Today |
|---|---|---|
| `agctl-core` | controller identity and registry, state directories, run records, agent wrappers (pi, codex, opencode), gate and remote-gate wrappers, host transport, doctor, optional state backup | `scripts/controller/` (imported 2026-11-18, vbpub 9dc48bf13) |
| `agctl status` (ctlstat) | one-call estate status, JSON and text, change-only output; read-only | requirements only; prototype `ctl-status.sh` |
| `agctl finalize`, `agctl prime` | agent finalization collector/validator; successor priming (section 5) | not built; **first slice** |
| `agctld` | resident service: watches registered controllers and agents through their session logs, marker protocol, compaction/restart/priming | not built; deferred |
| adapters | per harness: log parser, optional injector (hooks), wake: claude, codex, opencode, pi | Claude hooks (`scripts/controller/hooks/ctx-hooks.py` is the canonical, tracked hook; the live `.controller-state/hooks/ctx.py` is the controller's working copy and must be reconciled with it), `nyxloom extract` parsers |
| `agctl-tui` | operator view of all registered controllers/agents | not built; deferred |
| skills | controller `controller-handover` (section 4); agent `finalize` (section 5). Decision (operator 2026-11-18): a skill lives with its tool, as package data of the `agctl` distribution, registered through cli-extended `register_skills_verbs` and installed with `agctl skills install|check|list|uninstall` (the way ciu, cmru, cgprofile, fleetd and the pwmcp client ship theirs; the frontmatter must pass the cli-extended source schema) | handover skill written; the interim copy lives in `.claude/skills/controller-handover/` and moves into the package when it exists; finalize not built |

Migration is stepwise: `scripts/controller/` stays the home of the shell tools until `finalize` and `prime` exist as a Python CLI; then a cmru-managed `agctl/` project is created and tools move with their tests and mutants. Nothing is rewritten for its own sake.

## 3. Identity and state

- A controller has a stable **name**. The hook input carries no session name, so the name is **explicit**: `CONTROLLER_NAME` in the launch environment, or `agctl adopt --new <name>`; `ctl-doctor` warns when it is unset (rev 2; the earlier "default = session name" cannot be read by a hook). The session UUID is only the current **binding**, because a controller outlives its sessions (hard stop, restart, context-full successor, user-stopped session): state keyed by UUID would be orphaned at every handover.
- Registry `~/controller-state/registry.json`: name -> {harness, session id, pid, transcript path, started, last seen, intended context limit, state dir}. Writes use flock plus atomic rename. A name is held by a **lease** (session id, pid, last seen); a successor may **adopt** a name only when the lease is stale (no live pid and last seen older than N minutes) or with an explicit `--force` (rev 2: otherwise two controllers race for one name). A dead controller is shown as `orphaned` by status and never restarted automatically. Several controllers per devcontainer and per repository are supported.
- Per-controller state `~/controller-state/<name>/`: `HANDOFF.md`, `plan.yaml`, `operator-decisions.md` (verbatim operator turns), readbacks, hook state. Outside every work tree: no `git clean` hazard, so no scheduled backup (operator 2026-11-18; `ctl-state-backup.sh` stays an optional tool; `/tmp` and `~` persist in the cockpit mount, as stated by the operator).
- Shared per devcontainer (`~/controller/`, not per controller): env file, host table, gate locks, remote-gate locks and work directories, run records with per-controller name prefixes. Collisions designed out: merge numbers `merge-<name>-qNN`, worktree, run and monitor name prefixes, one plan per controller; gates are shared slots (`CONTROLLER_GATE_SLOTS`, default 2, because the devcontainer is memory capped and was OOM-killed once).
- Env naming follows `.env.<name>` so files group and sort: local `.env.controller` (untracked), sample `.env.controller.example`; the runner sample becomes `.env.runner.example` (today `runner.env.example`). Loaders fall back to the old names for one release with a deprecation warning. Cosmetic: scheduled after the first slice.

## 4. Controller context policy and compaction

**Role difference (operator, 2026-11-18).** A controller's value is prose: decisions, progress, open questions, reasoning about what comes next, and pointers. Implementers and reviewers need file contents, exact paths, error strings and tool results.

**Compaction is not "early" (rev 1 said it was; wrong).** Compaction loses prose context, and the prompt cache makes carrying context cheap but not free. Measured against this controller's own hook log (2,391 main-session tool calls, 14-18 Nov, review 2026-11-18):
- cache reads cost **0.05x** the input price on Sonnet 5.5 and Opus 5.5 (0.025x on Fable), not 0.1x; cache **writes** cost 1.25x (5-minute TTL) or **2x (1-hour TTL, which this controller uses)**. At 465k context a call costs about $0.047 in cache reads on Sonnet 5.5, more than a typical turn's output: "mostly free" is wrong at long contexts.
- growth per tool call: median 874 tokens, mean 1.5k, p90 4.1k; about 20 calls per active hour; auto-compaction fires at about 465k, every 220-230 calls (11-13 active hours, about 1.5 times a day).
- the **post-compaction floor is about 115k** (six compactions left 77k-144k), not the 30k of the readback: system and tools overhead (about 20k), re-attached files and the preserved tail are part of it.
- a steady-state model (floor F, ceiling, growth g, compaction cost about $0.55 including re-creating the floor at 2x) puts the optimum ceiling at about 245k and shows the curve flat between 250k and 350k; today's practice is about 29% above the optimum (about $2.5 a day). The **floor is the bigger lever**: cutting it from 115k to 60k saves as much per call as tuning the ceiling.

So the rule is simple, not a live model (rev 3: **soft steering inside a hard ceiling**). The **hard ceiling** is the per-model auto-compaction window of the harness (about 300k for the controller, set through `autoCompactWindow`; a hook cannot change it). Below it, the **soft ceiling** in `agctl.toml` (default about 300k, with a note step, a growth window and a floor target) makes the hook tell the controller or agent its current fill, its growth per turn and the projected turns to the hard ceiling, and advise to finish the current task to a clean semantic boundary instead of starting a new large unit; the agent decides. Rules: **compact warm, never cold** (before an idle gap longer than the cache TTL, or keep the 55-minute heartbeat), **compact before a planned tool-heavy fan-out**, and a **lean floor** (no re-attached files, a short readback of 15k or less, plan pointers only, floor target 60k or less). `agctl status` reports fill, growth rate, projection and boundary availability as advice; it does not decide. Not costed and noted: compaction quality loss grows with frequency; attention and latency degrade at 400k+; a mid-turn auto-compaction is not at a semantic boundary; if usage is a subscription, the scarce resource is the usage window, not dollars (open question 1).

**Compaction is authored by the one who knows (a) the whole past and (b) the future plan.** The controller writes its own handover as a **skill** (`controller-handover`): (A) a prose summary of the past: decisions with reasons, the progress ledger, what was learned, what is in flight (agents, hosts, experiments) with how to check each; (B) forward-looking hints and reasoning derived from `plan.yaml`: what to do next, in which order, what to watch, which decisions are pending with the operator; (C) read-first pointers in order. It drops what is re-readable (tool outputs, logs, file contents). Delivery (rev 2): the PreCompact hook output is appended as **instructions to the summariser**, so prose passed through it is summarised again, is capped at 10,000 characters, and was only measured with a one-sentence probe on Haiku (8 of 8, E2); a multi-KB handover through PreCompact is **untested**. The robust path is therefore the handover as a **file** that the session-start hook tells the resumed session to read first (the ingest path of the existing readback), with PreCompact steering as a hint only. This is requirement R12.3 made concrete.

**Implementers and reviewers are not compacted; they are bounded and succeeded** (section 5).

## 5. Agent return contract, finalization and priming

**Observed defect (2026-11-18).** Sonnet implementers ended with "ran out of context after about two big units" as if blocked. Two causes: the briefs allowed stopping for budget without a defined handoff, and the return value had no way to say "partially done, here is how to continue". Partly self-inflicted (rev 2): from 200k the controller hook tells agents to stop and make their report the handoff, but agents had no status word to say it with. The hook wording changes with the contract.

**Return contract (goes into every brief template and the hook's steering notes, which replace the fixed 200k message in rev 3).**
- `status: DONE | PARTIAL | BLOCKED`. `BLOCKED` means an external obstacle only (a decision, a missing permission, a failing dependency; a red gate the agent cannot fix is `PARTIAL` with a blocked-on list). Running low on context is never BLOCKED.
- An agent that senses its context filling continues until a **semantic boundary**, then returns `PARTIAL` with: done up to (commit, items), deferred (items, reasons), tips for the successor (pitfalls, decisions taken), the read-first list (files in order, with why), the living state file, the exact commands to resume tests, and what it would do next. `DONE` still lists "not done / known limits".
- Format (rev 2): a YAML block at the end of the final message plus `finalization.json` in the run directory; the file is authoritative.
- "Semantic boundary" is **mechanical** (rev 2): the last commit landed, the tree is clean, the focused tests are green; `finalize` checks it. An agent that never returns PARTIAL (hard limit, stall) gets `agctl finalize --from-log`, built from `nyxloom extract --successor-brief`, the `job-check.sh` stall vocabulary and git state, with `status: UNREPORTED`.

**Finalization tool.** `agctl finalize` (agent-side skill and CLI) validates the boundary and collects mechanically what it can (commits since base, dirty tree, state file, test commands from the session, files read most and written), and asks the agent only for the judgment parts (deferred, tips, next). Output: `finalization.json` plus `finalization.md`.

**Priming a successor: one controller call when the finalization is complete.** `agctl prime <run> --mode ...`:
1. `fresh` (default; the **first slice**): a new session whose first prompt is the successor brief generated from the finalization (task, constraints, read-first list, done, remaining). The controller may edit the brief before priming; with an incomplete finalization `prime` refuses and says what is missing; it is automatic only for `fresh` with a complete finalization (rev 2 default: the controller confirms otherwise).
2. `fork` (deferred): `claude --resume <sid> --fork-session` of a **clean frozen orientation session**, never the implementer's bloated one (nyxloom pattern (b); the mechanism is E-003, the measurement L24: cache read about 5% on current models). Prefix invariants must hold (same dynamic-sections flag, model, effort, toolset), the orientation is pinned to a base commit so the successor needs the `X..HEAD` delta, and it is warm only within the cache TTL (a cold fork re-creates the prefix at 2x and can cost more than `fresh`). Not available for Codex luna or Agent-tool subagents (they cannot be forked this way); for Codex "resume" means app-server `thread/compact/start` with `compact_prompt` (X6).
3. `resume`: external `/compact` of the same session with the agent's handover (nyxloom pattern (a), E1), for sessions no live process holds.

## 6. Session control without an interactive-only controller

Measured (docs/design/ctlstat/EXPERIMENT-compaction-*.md): external `claude -p --resume <id> "/compact <instr>"` works (E1, idle, small sizes, Haiku; a 747k compaction took about 90 s); a long-lived `claude -p --input-format stream-json` driver accepts `/compact` as a message, queues mid-turn messages and fires hooks (X1/X2); `claude --bg` with `claude attach/logs/stop` is the native server-plus-TUI model and is UNTESTED (E6 stopped at a one-time workspace-trust prompt in a throwaway directory).

**Untested and the main risk (rev 2):** a TUI that holds an in-memory history while an outside process appends a compaction to the same JSONL could fork or clobber the chain; that the readback is picked up by a running interactive session on its next turn is also untested end to end. Until the collision test passes, **external compaction applies only to sessions no live process holds**; the interactive controller stays on harness auto-compaction plus hooks, which already meets the unattended requirement (R12.11).

Options: **A** interactive TUI plus hooks plus auto-compaction (works today); **B** `claude --bg` plus `attach` (test E6 in the trusted repository directory with a cheap model; cheap, decides B); **C** a stream-json/Agent-SDK supervisor with an own TUI (heavy; only if multi-harness uniformity needs it).

**Ready-marker protocol (for later, `agctld`).** The controller writes its handover and a `READY-TO-COMPACT` marker carrying session id, last message uuid and handover hash, then idles. `agctld` claims it by rename (`.claimed.<pid>`), checks preconditions (last entry `end_turn`, no pending tool_use, no queued messages, no `run_in_background` tasks: they block or die, E5), performs the compaction, and the marker is done when a `compact_boundary` appears after that uuid; on restart it reconciles a claimed marker without a boundary. 429 or an exhausted usage window: leave the marker, back off, notify. It tails JSONL incrementally (40 MB files, 8 GiB devcontainer), takes a pidfile flock, and **fails open**: if `agctld` is dead, harness auto-compaction remains the limit. Running agents are independent processes (detached wrappers) and are unaffected.

## 7. Integration with the rest of the estate

- `fleetd`: work requests and campaigns carry `owner=controller:<name>`; stall and result alerts go to that controller's inbox in its state directory; `remote-gate.sh` becomes its first generic-lane client; fleetd arbitrates host capacity across controllers (the reason it is central).
- `thrashguard` (host actuator, separate note): registrants (run-gate, assay, run-job) push a shed score per unit over its write socket; `agctl status` reads its read socket. Victim rules (newest by default, highest refault rate, smallest enough) are its configuration; no non-starvation logic is built, only documented as known limits. assay consumes its freeze/thaw events to correct the wall time of an interrupted candidate and re-runs only killed, timed-out or hung candidates that overlapped a freeze.
- `hostobs`: telemetry source for status host sections after its migration (only when hostobs is feature complete; all consumers of plan v3 section 1.1 are adapted then).
- Sockets (rev 2): owner-only with an `SO_PEERCRED` check, not merely group-gated: all agent wrappers run as the controller's uid, so a prompt-injected agent could otherwise compact or prime its own controller. Agents may write only their own finalization. Agent-authored text (handover, finalization) flows into successor prompts and compaction instructions: it is data, linted, never trusted as instructions about permissions. The operator acts through the TUI.

## 8. Packages, in order (rev 2)

**First slice (needs almost no new code):**
1. Return contract in `BRIEF-TEMPLATE.md`, `rules.md`, the finisher prompts, and the 200k wording of the controller hook.
2. `agctl finalize` as validator plus mechanical collector (+ `--from-log` fallback).
3. `agctl prime fresh` (thin wrapper over `nyxloom extract --successor-brief` plus the finalization).
4. `agctl.toml` (soft ceiling, hard ceiling per model, note step, growth window, floor target), read by the controller hook with `tomllib`; the hook's steering notes carry fill, growth per turn and the advice above; `autoCompactWindow` about 300k for the controller with the operator's permission; trim the post-compaction floor (target 60k or less); reconcile the two ctx hook copies. Debug/learning mode from the start: every steering action and its outcome is logged and the controller reviews the log (or hands it to a feedback agent) before the automation is trusted.
5. E6 (`claude --bg` plus attach) and the TUI-plus-external-writer collision test (plan item AG-2): run after the devcontainer rebuild in a throwaway session on the cheapest model in the trusted repository directory; not part of the first-slice implementation package.
**Then:** `agctl status` (read-only) with the advisory pressure numbers; controller identity (`CONTROLLER_NAME`, lease registry, per-controller state directories; migration of the running session last, with a pointer left behind); env renames (cosmetic).
**Deferred until needed:** `agctld`, fork mode, adapters beyond Claude and Codex, the TUI, anything in the registry beyond a name and a state directory.
Each package ships with tests and mutants like the controller tools and is reviewed independently.

## 9. Decisions (operator; recommended defaults after review)

- Project home: keep `scripts/controller/` until `finalize` and `prime` exist as a Python CLI, then a cmru-managed `agctl/` project.
- `nyxloom extract`: dependency.
- Controller name: explicit `CONTROLLER_NAME`.
- Compaction trigger (rev 3): hard ceiling = harness auto-compaction window (controller about 300k), soft steering from `agctl.toml` below it, warm-only, pre-fan-out; the model's numbers are advisory and the agent decides when to stop at a semantic boundary.
- Compaction without approval: yes for spawned sessions that set the marker; no for the interactive controller until the collision test passes.

## 10. Known limits and unverified facts

E6 (`--bg`), the TUI-plus-external-writer collision and the end-to-end ingest of a readback by a running interactive session are unmeasured. Whether hook `additionalContext` survives a compaction is unmeasured. A multi-KB handover through PreCompact is untested. How a subscription weights cache reads against its usage windows is unknown (`nyxloom usage calibrate` can tell). Compaction of implementers is deliberately avoided, not forbidden.

## 11. Interview decisions (operator, 2026-11-18)

- **Cost basis:** subscription usage windows; API-equivalent prices are only the proxy (`nyxloom usage calibrate` measures how cache reads count).
- **Ceiling:** about 300k with a lean floor (target 60k or less), plus **soft steering**: the controller and every agent are told their current context fill and its growth per turn, and decide for themselves to finish a task to reach a clean semantic boundary before the hard ceiling. Growth speed is part of the advice (what is efficient to start depends on the projected turns to the ceiling). Configuration lives in `agctl.toml` (soft ceiling, hard ceiling, note step, growth window, floor target); the hook is Python and reads it with `tomllib`. The hard ceiling is the harness's per-model auto-compaction window (a Claude Code setting that a hook cannot change; applied through the settings with the operator's permission).
- **External `/compact` of the interactive controller:** no, for now. The E6 test (`claude --bg` plus attach) and the TUI-versus-external-writer collision test run after the resume in the new devcontainer (plan item AG-2).
- **First slice:** return contract, `agctl finalize`, `agctl prime fresh`.
- **Priming approval:** the controller confirms; automatic only for `fresh` with a complete finalization. Plus a **debug/learning mode** at the start: every steering action and its outcome is logged, and the controller reviews the results carefully (or hands the log to another agent that gives feedback) before the automation is trusted.
- **Project home:** keep `scripts/controller/` until `finalize` and `prime` exist as a CLI, then a cmru-managed `agctl/` project. `nyxloom extract` stays a separate project as it is; parts may be re-implemented in Rust for `agctl` because the Python session extraction is slow.
- **Skills:** with the tool (section 2, skills row).
