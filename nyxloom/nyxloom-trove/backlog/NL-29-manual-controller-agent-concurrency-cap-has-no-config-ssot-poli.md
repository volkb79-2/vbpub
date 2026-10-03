---
kind: backlog-entry
schema_version: 1
id: NL-29
title: "manual-controller agent-concurrency cap has no config SSOT: [policy] is daemon-only, so caps live in drifting decision records"
status: open
type: "feature"
severity: "medium"
component: "policy"
provenance: "dstdns tooling-boundary pass 2026-10-03 (dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md Q2)"
filed_by: "Claude (dstdns architect session)"
spec_owner: "nyxloom-trove/nyxloom.toml [policy] schema; nyxloom-dispatch skill"
filed_date: "2026-10-03"
---

**Observed mechanism.**
- A project's agent-concurrency cap lives in `nyxloom-trove/nyxloom.toml [policy] max_active_tasks`. Only the daemon reads it.
- A manual controller session, which is the sanctioned mode while the daemon is paused (see NL-18), has no reader for it. Its caps therefore live in prose decision records, and those drift.
- dstdns, 2026-10-03, carries three live values for one fact:
  - `[policy] max_active_tasks = 3` (`dstdns/nyxloom-trove/nyxloom.toml:677`)
  - D-570, "up to 6 agents in parallel", which CLAUDE.md's table still cites as "≤6 parallel agents"
  - D-636, "at most 2 running agents of any role", which supersedes D-632's serial regime and is the one actually followed
- Gate and stack caps are hand-copied into the same records too: "≤2 gate invocations per runner" (D-570/D-636), and "2-3 stacks" (GUIDE §3.0) against `[ciu.worktree] max_concurrent_instances = 5` in `ciu.global.defaults.toml.j2:1468`, whose comment claims the host is the sole occupant while GUIDE §3.0 says it is shared with a production game server.

**Why nyxloom owns this, not the consumer.** Agent-level concurrency is pipeline policy, and nyxloom is the pipeline product. The manual-controller skills (`nyxloom-dispatch`) are nyxloom's. A consumer can only restate a number in prose, and an operator change of mind then needs a decision record plus an edit in every place the number was copied.

**Proposed contract.**
- (1) `[policy]` is the single source for agent concurrency in BOTH modes. Add `max_parallel_agents` if `max_active_tasks` is daemon-task-specific.
- (2) `nyxloom policy show [--json]` (daemon-free, walking up to the trove, like `backlog`) prints the effective values. The dispatch skill reads it before every dispatch, together with a running-agent count it is given.
- (3) Caps that belong to other tools are REFERENCED, never restated: gate concurrency belongs to run-gate (`run-gate.toml` environment `max_concurrent`, run-gate RG-67) and stack concurrency to ciu (`[ciu.worktree] max_concurrent_instances`). `nyxloom policy show` displays them read-through, labelled with their owner.
- (4) An operator directive changes the config value in the same commit as its decision record. `nyxloom lint` (or a trove check) warns when a decision record's text names a cap that differs from config.

**Oracles.**
1. `nyxloom policy show --json` in dstdns prints `max_parallel_agents` from `[policy]` and the run-gate/ciu caps with their source files.
2. The dispatch template refuses a third dispatch when the configured value is 2 and two agents are running.
3. Changing only the config value changes the refusal point, with no prose edit.
4. Controlled wrong implementation: a dispatch path reading a constant must fail oracle 3.

**v8 relation.** ciu v8 `[ciu.instances] max_concurrent` (SPEC-V8 S14.6) is the stack cap nyxloom references, not a value it duplicates.
