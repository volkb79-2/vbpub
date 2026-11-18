# agctl: agent control (design note, 2026-11-18, DRAFT for review)

Status: DRAFT. Requirements and measured facts are in `docs/design/ctlstat/` (REQUIREMENTS.md, HARNESS-COMPACTION-CHECKLIST.md, EXPERIMENT-compaction-*.md) and `nyxloom/docs/design-context-lifecycle*.md`; this note decides the shape of the project that unifies them and records the operator's decisions of 2026-11-17/18. Where this note and the ctlstat requirements differ, this note wins and says so.

## 1. Why one project, and its name

The controller work (state, wrappers, gates, host transport), the estate status tool (`ctlstat`: one mechanical call for an informed decision) and the session lifecycle service (compactor, restarter, primer, TUI) share one subject: **controlling long-lived agent sessions and their work**. They share identity (a controller or agent has a name), state (handoffs, run records, session logs), and consumers (the controller deciding what to do next). Name: **`agctl`** (operator, 2026-11-18). It is harness-neutral by contract and Claude-aware through one adapter.

What `agctl` does NOT own: campaigns and placement on hosts (`fleetd`), host protection (`thrashguard`), telemetry and profiling (`hostobs`), session-log extraction (`nyxloom extract`, the engine; depend or absorb is an open decision, section 9). `agctl` reads their sockets; it never probes hosts itself.

## 2. Components

| Component | Role | Today |
|---|---|---|
| `agctl-core` | controller identity and registry, state directories, run records, the agent wrappers (pi, codex, opencode), gate and remote-gate wrappers, host transport, doctor, optional state backup | `scripts/controller/` (imported 2026-11-18, vbpub 9dc48bf13) |
| `agctl status` (ctlstat) | one-call estate status, JSON and text, change-only output for injection | requirements only; prototype `ctl-status.sh` |
| `agctld` | resident service: watches registered controllers and agents through their session logs, computes context pressure, performs compaction, restart and priming at semantic boundaries | not built |
| adapters | per harness: log parser (context fill, tool calls, boundaries), optional injector (hooks), wake mechanism: claude, codex, opencode, pi | Claude hooks in `.controller-state/hooks/ctx.py`, `nyxloom extract` parsers |
| `agctl-tui` | operator view of all registered controllers/agents; attach to a controller | not built |
| skills | controller: `handover` (section 4); agent: `finalize` (section 5) | not built |

Migration is stepwise: `scripts/controller/` stays the home of the shell tools until a package moves a tool with its tests and mutants; nothing is rewritten for its own sake.

## 3. Identity and state

- A controller has a stable **name** (default: the session's own name, for example `ciu-release-and-backlog-triage`). The session UUID is only the current **binding**, because a controller outlives its sessions (hard stop, restart, context-full successor, user-stopped session). State keyed by session UUID would be orphaned at every handover.
- Registry `~/controller-state/registry.json`: name -> {harness, session id, transcript path, started, last seen, intended context limit, state dir}. The session-start hook records the binding (the hook sees the session id); a successor **adopts** a name; a new controller on a disjoint topic creates a new name. Several controllers per devcontainer and per repository are supported.
- Per-controller state `~/controller-state/<name>/`: `HANDOFF.md`, `plan.yaml`, `operator-decisions.md` (verbatim operator turns), readbacks, hook state. Outside every work tree: no `git clean` hazard, so no scheduled backup (operator 2026-11-18; `ctl-state-backup.sh` stays an optional tool; `/tmp` and `~` persist in the cockpit mount).
- Shared per devcontainer (`~/controller/`): env file, host table, gate locks, remote-gate locks and work directories, run records with per-controller name prefixes. Collisions designed out: merge numbers `merge-<name>-qNN`, worktree, run and monitor name prefixes, one plan per controller; gates are shared slots (`CONTROLLER_GATE_SLOTS`, default 2, because the devcontainer is memory capped and was OOM-killed once).
- Env file naming follows `.env.<name>` so files group and sort: local `.env.controller` (untracked), sample `.env.controller.example`; the runner sample becomes `.env.runner.example` (today `runner.env.example`). The loader falls back to the old names for one release with a deprecation warning.

## 4. Controller context policy and compaction

**Role difference (operator, 2026-11-18).** A controller's value is prose: decisions, progress, open questions, reasoning about what comes next, and pointers. Implementers and reviewers need file contents, exact paths, error strings and tool results.

**When to compact: not by size alone.** An earlier draft said "compact early at a semantic boundary". That is wrong as a rule, for the reasons the operator gave: compaction loses prose context; the prompt cache makes carrying a large context cheap (cache reads are about a tenth of the input price, and a warm prefix is mostly free); restarting from a 30k readback at 100k context buys nothing. What is expensive is the **tail**: very long contexts re-billed every turn, slower turns, and the degradation of attention. So the trigger is a model, not a threshold:

- context growth rate (tokens per turn, recent window) and the expected remaining work (from `plan.yaml`);
- the cost of staying (cache-read bill of the projected growth) against the cost of compacting (summary generation plus the first cold turns and the loss risk);
- boundary availability (state written, nothing half-edited) and bulk (how much of the context is re-readable tool output);
- a hard ceiling for the tail (configurable per task, requirement R12.4: the limit is the task's intended context, not the model's maximum).

`agctl status` reports these numbers (fill, growth rate, projection, boundary available); the controller or `agctld` decides. A slowly growing controller at 150k with a short plan should not compact; a controller at 150k that fans out tool-heavy work should pre-compact.

**Compaction is authored by the one who knows (a) the whole past and (b) the future plan.** The controller writes its own handover at compaction time, as a **skill** (`handover`): (A) a prose summary of the past: decisions with reasons, the progress ledger, what was learned, what is in flight (agents, hosts, experiments) with how to check each; (B) forward-looking hints and reasoning derived from `plan.yaml`: what to do next, in which order, what to watch, which decisions are pending with the operator; (C) read-first pointers (files, in order). It drops what is re-readable (tool outputs, logs, file contents). The PreCompact hook steers the compaction prompt to use the handover (measured: handover text honoured in 8 of 8 compactions, E2) and the session-start hook ingests the readback. This is requirement R12.3 (self-compaction prompt at a semantic boundary) made concrete.

**Implementers and reviewers are not compacted; they are bounded and succeeded** (section 5). Extraction presets already differ the same way: `readback` for controllers, `--successor-brief` for agents.

## 5. Agent return contract, finalization and priming

**Observed defect (2026-11-18).** Sonnet implementers ended with "ran out of context after about two big units" as if blocked. Two bugs: the briefs allowed stopping for budget without a defined handoff, and the return value had no way to say "partially done, here is how to continue".

**Return contract (goes into every brief template).**
- `status: DONE | PARTIAL | BLOCKED`. `BLOCKED` means an external obstacle only (a decision, a missing permission, a failing dependency). Running low on context is never BLOCKED.
- An agent that senses its context filling continues until a **semantic boundary** (a green commit, a finished item), then returns `PARTIAL` with: done up to (commit, items), deferred (items, with reasons), tips for the successor (pitfalls found, decisions taken), the read-first list (files in order, with why), the living state file, the exact commands to resume tests, and what it would do next.
- `DONE` still lists "not done / known limits".

**Finalization tool.** `agctl finalize` (agent-side skill and CLI) collects the above mechanically as far as it can (commits since base, dirty tree, state file, test commands from the session, files read most and written) and asks the agent only for the judgment parts (deferred, tips, next). Output: `finalization.json` plus a human-readable `finalization.md` in the run directory.

**Priming a successor: one controller call when the finalization is complete.** `agctl prime <run> --mode ...`:
1. `fresh`: a new session whose first prompt is the successor brief generated from the finalization (task, constraints, read-first list, what is done, what remains). Default for implementers.
2. `fork`: fork a frozen, oriented session (`claude --resume <sid> --fork-session`, nyxloom pattern (b), experiment E-003: warm forks pay about 10% cache-read) and add only the new goal plus the successor brief. For many successors on the same repository orientation.
3. `resume`: external `/compact` of the same session with the agent's handover (nyxloom pattern (a), measured E1), for agents that must keep their warm session.
The controller may override: edit the generated brief before priming. If the finalization is incomplete (no semantic boundary, dirty tree), `prime` refuses and says what is missing.

## 6. Session control without an interactive-only controller

Measured (docs/design/ctlstat/EXPERIMENT-compaction-*.md): external `claude -p --resume <id> "/compact <instr>"` works (E1); a long-lived `claude -p --input-format stream-json` driver accepts `/compact` as a message, queues mid-turn messages and fires hooks (X1/X2); a TUI-held session picks up the readback on its next turn through the session-start hook; the collision of a TUI and an external writer on the same session is UNVERIFIED; `claude --bg` with `claude attach/logs/stop/respawn` is the native server-plus-TUI model and is UNTESTED (E6 stopped at a one-time workspace-trust prompt in a throwaway directory).

Options: **A** interactive TUI plus hooks plus external `/compact` while idle (works today); **B** `claude --bg` plus `attach` (closest to a controller that does not own the terminal; test E6 in the trusted repository directory with a cheap model first); **C** a supervisor over stream-json or the Agent SDK with an own TUI (heavy; only if uniform multi-harness handling needs it). The controller need not exit to be compacted: it writes its handover and a `READY-TO-COMPACT` marker in its state directory and idles; `agctld` sees the marker and idleness in the session log, performs `/compact` (or starts a successor), updates the registry binding, and wakes the controller. Running agents are independent processes (detached wrappers) and are unaffected.

## 7. Integration with the rest of the estate

- `fleetd`: work requests and campaigns carry `owner=controller:<name>`; stall and result alerts go to that controller's inbox in its state directory; `remote-gate.sh` becomes fleetd's first generic-lane client. fleetd arbitrates host capacity across controllers (the reason it is central).
- `thrashguard` (host actuator, separate note): registrants (run-gate, assay, run-job) push a shed score per unit over its write socket; `agctl status` reads its read socket. Victim rules (newest by default, highest refault rate, smallest enough) are its own configuration. assay consumes its freeze/thaw events to correct the wall time of the interrupted candidate and re-runs only killed, timed-out or hung candidates that overlapped a freeze.
- `hostobs`: telemetry source for `agctl status` host sections after its migration (migration only when hostobs is feature complete; all consumers listed in its plan v3 section 1.1 are adapted then).
- Sockets follow the common design: group-gated write and read sockets, no authentication inside the tool.

## 8. Packages, in order

1. Controller package A (small): env naming (`.env.controller`, `.env.runner.example` rename with loader fallback), `CONTROLLER_REPORTS_DIR`, docs fix (backup optional), `CONTROLLER_NAME` plus per-controller state directory plus registry (design first, migration of the running session last).
2. Agent return contract: brief template and finalizer prompts updated (docs), then `agctl finalize` and `agctl prime fresh`.
3. `agctl status` (ctlstat) with the pressure model and change-only output.
4. Experiment E6 (`claude --bg` plus attach) and the TUI-plus-external-writer collision test.
5. `agctld` marker protocol, then fork mode and adapters beyond Claude.
6. TUI.
Each package ships with tests and mutants like the controller tools, and is reviewed independently.

## 9. Open decisions (operator)

- Project home and layout: top-level `agctl/` project (cmru-managed) with `scripts/controller/` migrated into it, versus keeping the shell tools where they are until `agctld` exists.
- `nyxloom extract`: dependency or absorbed.
- Default controller name = session name, and who creates a name for a controller that has none.
- Which compaction trigger default: model-based (section 4) with a hard ceiling; the ceiling value for a controller.
- Whether `agctld` may compact a controller without the operator's present approval, and the wake mechanism.

## 10. Known limits and unverified facts

E6 (`--bg`) and the TUI-plus-external-writer collision are unmeasured. Whether hook `additionalContext` survives a compaction is unmeasured. The cost model of section 4 needs real measurements of growth rates in controller sessions (the hook log `hook-log.jsonl` has the data). Compaction of implementers is deliberately avoided, not forbidden.
