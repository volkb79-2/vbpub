---
name: controller-handover
description: Write the controller's own handover, the prose that primes its next session after a compaction, a restart, a devcontainer rebuild or a successor takeover: binding decisions with reasons, state of everything in flight with how to check it, the forward plan with its reasoning, open operator questions, and a read-first list. Use when the context nears the ceiling, before /compact or a rebuild, when the operator says "prepare for my /compact", "write the handoff", "readback" or "prime the next session", when a context-pressure hook note appears, or when a controller session is replaced. For implementers and reviewers use the agent finalization contract, not this skill.
---

# Controller handover

Design and rationale: `docs/design/agctl/DESIGN.md` sections 4 and 5. A controller's value is **prose**: decisions with their reasons, the progress ledger, what is in flight and how to check it, and the reasoning about what comes next. Tool output, logs and file contents are re-readable: point to them, never paste them. The handover is authored by the one who knows (a) the whole past and (b) the plan, at the moment he is about to lose (a). Nothing mechanical (the transcript extract, `readback-N.md`) replaces the judgment parts; the extract complements them.

## 0. Use it when, and not when
- Use: before `/compact` (manual or because a hook says pressure), before a devcontainer rebuild or any planned stop, when handing the controller role to a successor, after a user stop of the controller.
- Not for implementers/reviewers: they return `DONE | PARTIAL | BLOCKED` with a finalization (DESIGN.md section 5). Not a substitute for `plan.yaml` (items) or `operator-decisions.md` (verbatim turns): it points to them.
- Timing is not "as early as possible". A large warm context is cheap; the cost is the tail. Compact at the configured ceiling, warm (inside the cache TTL), and before a planned tool-heavy fan-out. This skill defines what you write at that moment, not when.

## 1. Where things live (one fact, one place)
| What | File | Rule |
|---|---|---|
| Items, status, next | `plan.yaml` | update item statuses; no narrative |
| Operator turns, verbatim | `operator-decisions.md` | append every new turn BEFORE you act on it, with the decisions derived |
| The handover | `HANDOFF.md`, newest `## RESUME HERE #N` block | supersedes older blocks; forward-oriented only |
| Per-agent living state | `codex/runs/<run>.state.md` | agents keep it; you only point to it |
| Pointer for the next session | `MEMORY.md` controller line | read order and current N |
| Mechanical transcript readback | `readback-N.md` (hook-built) | complement, not the handover |
Layout today: `.controller-state/` in the main checkout, `plan.yaml` at its root. Planned: `~/controller-state/<name>/` per controller (DESIGN.md section 3); follow whatever `CONTROLLER_STATE_DIR` says once it exists.

## 2. Procedure
1. **Reach a boundary.** Nothing half-edited; your own worktrees clean; every running agent has a living state file; a gate or monitor you started is noted with its marker. If an agent's result is minutes away, wait for it or note exactly what to do on arrival.
2. **Check reality, do not write from memory.** Run, in this order, and keep only the facts: `git fetch -q origin; git log origin/main --oneline -8`; `git worktree list`; running agents (`ListAgents`, run records without `.done`); `pgrep -af 'run-gate|cmru release'`; gate logs of anything in flight (verdict line AND `run-gate.commit`); host units (read-only checks over the identity-checked transport); `date -u` for every timestamp you write (estimated times were wrong in the past: never estimate).
3. **Update `plan.yaml`**: statuses of the items you touched; add the next items. Items, not stories.
4. **Append the operator's unrecorded turns** to `operator-decisions.md`, verbatim, with the derived decisions under each. Corrections you accepted (including "that was wrong") are recorded as the current rule, not as an incident.
5. **Write the `RESUME HERE #N` block** (template below). Forward-oriented: current state, next steps, standing rules, live risks. No history of old mistakes; fix the process instead and move on.
6. **Update the `MEMORY.md` controller line** (the read order, the new N, the one-sentence headline) and any memory file whose fact changed. Do not duplicate what the repo records.
7. **If the read-first list changed**, update the session-start hook text (`READ_ORDER` in `scripts/controller/hooks/ctx-hooks.py` and the live copy) so the next session is told to read the right files in the right order.
8. **Cold-read test.** Pretend to know nothing: follow only the READ-FIRST list. Does every path exist (`ls`)? Does every command run (dry run where it mutates)? Can the next action be taken without asking? Are hashes and ids copy-pasteable? Fix gaps now. No tokens, keys or host addresses anywhere.

## 3. The RESUME HERE block (template)
```
## RESUME HERE #N (<date -u>, <controller name>) SUPERSEDES #N-1 where they differ
HEADLINE: where we are in two or three sentences, and what the operator wants now.
DECISIONS (binding, with reasons; operator wording where contested): ...
STATE:
  merged since #N-1: <merge number> <hash> <what>; next merge number: ...
  in flight (one line each): <what> | where (worktree/branch/log/unit/agent id) | how to check (exact command) | what to do on arrival
  host/experiments: units, timetable, stop and cleanup commands, what must be checked after a reboot
NEXT STEPS, ordered, each with why this order: ...
OPEN QUESTIONS for the operator: exact question, my recommended default, what depends on it.
STANDING RULES that apply: pointers (AGENTS.md section, memory file names), not restatements.
WATCH (live risks only): ...
READ FIRST, in order, each with why: 1) ... 2) ...
AFTER A RESTART OF THE ENVIRONMENT: what dies (agents, monitors, scratch), what lives, the commands to re-establish (doctor, monitors, reconnects).
```

## 4. Content rules
- **Include:** decisions and the reasons that make them binding; hashes, ids, run names, exact commands; for each in-flight item the check command and the next action; the operator's open questions with a default; the plan's order with reasoning; live risks.
- **Exclude:** tool output, logs, file contents (point to them), transcripts of reasoning, finished history, past-mistake narratives, anything re-derivable from git or the logs within one command.
- **Prose over lists** for reasoning (the next session must understand why), lists for facts (ids, commands).
- **Drop on the fly:** the handover is your chance to decide what the next session does NOT need. If a fact is not needed to act, it does not go in.
- Never state a number, hash or status you did not just check in step 2.

## 5. Variants
- **Before a devcontainer rebuild:** add the AFTER-A-RESTART section: what dies (Agent-tool subagents, Monitors, background Bash tasks, scratchpad files), what lives (home, `/tmp`, the artifacts directory, host-side units and their logs), and the first commands: `ctl-doctor.sh`, re-arm one monitor, check each host unit, re-check observer-lane availability after the rebuild (drop the profile-off default if the observer works).
- **Successor takeover (context-full or user stop):** also run `nyxloom extract --preset readback` for the mechanical complement and name the transcript path; adopt the controller name instead of creating one.
- **Before the operator's `/compact`:** same procedure; the PreCompact hook steers the summary toward the handover and the session-start hook prints the read order. Keep the handover as a FILE: text passed through the hook is re-summarised and capped (10,000 characters).

## 6. Quality checklist
1. Every claim re-checked in step 2. 2. Timestamps from `date -u`. 3. Operator turns recorded verbatim before use. 4. Each in-flight item has a check command and a next action. 5. Next steps are ordered with reasons. 6. Open questions carry a default. 7. READ-FIRST paths exist. 8. No history or incident narratives. 9. No secrets or addresses. 10. The memory line and the hook read order match the block.

## 7. Known limits
Not yet tested end to end through the hooks with a real compaction; whether a multi-KB handover through the PreCompact hook survives is unmeasured (DESIGN.md section 10), which is why the file path is the primary channel. The per-controller state directory is a plan; until it exists the layout in section 1 applies.
