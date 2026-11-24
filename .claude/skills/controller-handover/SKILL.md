---
name: controller-handover
description: Controller sessions only (the session that dispatches agents and owns plan.yaml). Write the controller's own handover, the prose that primes its next session after a compaction, restart, devcontainer rebuild or takeover - first action, binding decisions with reasons, temporary constraints, what is in flight and how to check it, the merge queue, the forward plan with its reasoning, open operator questions and a read-first list. Use when a context pressure note for the controller appears, before /compact or a rebuild, or when the operator asks to prepare for a compact, write the handoff or readback, or prime the next session. Not for implementers or reviewers (they return DONE, PARTIAL or BLOCKED with a finalization). Interim home - the packaged source will live in the agctl package and be installed with agctl skills install.
---

# Controller handover

Rationale: `docs/design/agctl/DESIGN.md` sections 4-5. The controller's value is prose: decisions with their reasons, what is in flight, what comes next and why. Tool output, logs and file contents are re-readable: point to them, never paste. You write it because you know (a) the whole past and (b) the plan, at the moment you are about to lose (a). This skill defines WHAT to write; the ceiling and timing are configured elsewhere (a big warm context is cheap, the tail is the cost).

## 1. The block (write this first, fill it from step 2 facts)
Append to `HANDOFF.md` (today `.controller-state/`; later `~/controller-state/<name>/`), at most 8,000 characters; point to files for the rest. The newest block supersedes older ones.
```
## RESUME HERE #N (<date -u>, <controller name>) SUPERSEDES #N-1 where they differ
FIRST ACTION: the one thing to do before anything else (a hazard to revert, a lease to renew), or "none".
HEADLINE: where we are, what the operator wants now (2-3 sentences).
DECISIONS (binding, with reasons; operator wording where contested; corrections are the current rule, not an incident).
TEMPORARY CONSTRAINTS: what, until when, who lifts it (e.g. gate profile off until the rebuilt container; no docker during a cut).
STATE
  merged since #N-1: <merge number> <hash> <what>; next merge number
  in flight, one line each: what | where (worktree/branch/log/unit/agent id) | check command | on arrival do X | agent: resume route (SendMessage, or successor if user-stopped) and expected DONE/PARTIAL/BLOCKED + finalization path
  hosts/experiments: units, timetable, stop and cleanup commands, what to check after a reboot
  PEERS and reservations: other sessions, keep-out paths, host leases, what each waits for
  MERGE QUEUE with conditions (e.g. q57 after lane PASS bound to the head, then q58)
  MONITORS: id, prefix, expiry, re-arm name. FINISHED agents: nothing to stop.
NEXT STEPS, ordered, each with why this order.
OPEN QUESTIONS for the operator: exact question, my default, what depends on it.
UNKNOWNS: what you did not verify; do not assume it.
RULES: pointers (AGENTS.md sections, memory file names), not restatements.
READ FIRST, in order, each with why.
AFTER A RESTART OF THE ENVIRONMENT: what dies (subagents, monitors, background tasks, scratch), what lives, the first commands.
```
Between blocks append `(#N HH:MMZ) ...` update lines from `date -u`; the next block folds them in. Keep merged-since hashes and the merge numbering; keep a lesson only as a rule; drop incident narratives, tool output, file contents, re-derivable facts.

## 2. Before writing: check reality (one pass, keep only facts)
```
date -u; git fetch -q origin; git log origin/main --oneline -8; git worktree list
for each in-flight worktree: git -C <wt> status --short; git -C <wt> rev-parse HEAD
ListAgents + run records without .done; armed monitors (id, expiry); pgrep -af 'run-gate|cmru release'
gate logs: verdict line AND run-gate.commit; remote-gates/<id>/result.json
scripts/controller/job-check.sh <run>; ctl-status.sh; ctl-doctor.sh
host units: read-only checks through hx.sh (failed units, timers, the experiment's own log tail)
docker: exact container names only; grep operator-decisions.md for unanswered questions
```
Every number, hash and status in the block comes from this pass; a fact from a peer or an agent is tagged `(peer said)` / `(agent said)`. Never estimate a timestamp.

## 3. Procedure
1. Reach a boundary: nothing half-edited, your worktrees clean, each agent has a living state file. 2. Run the reality pass. 3. Update `plan.yaml` item statuses (items, not stories). 4. Append unrecorded operator turns verbatim to `operator-decisions.md` with the derived decisions. 5. Write the block. 6. Update the `MEMORY.md` controller line (read order, N, headline). 7. If the read-first list changed, update the session-start hook text (`READ_ORDER`): edit the live `.controller-state/hooks/ctx.py` and file a CTL item to reconcile the tracked `scripts/controller/hooks/ctx-hooks.py` (a repo change needs its own gate and review). 8. Cold-read test: follow only READ FIRST as a stranger: every path exists, every command runs (dry where it mutates), the next action needs no question, no secrets or host addresses anywhere, and the hook read-order output stays under 9,000 characters.

## 3a. Restarting into the successor without the operator
After the block is written and checked: write a short prompt file (name the handover and its READ FIRST list; ask peers to resend anything sent around the restart) and run `scripts/controller/self-resume.sh <pane-id> <prompt-file> <log>`. Your own pane: launch it DETACHED (`setsid nohup ... >/dev/null 2>&1 &`, never `run_in_background`) and end the turn; a sub-controller's pane: the controller runs it after the sub-controller reports "at boundary, ready for successor". It waits for idle, sends `/clear`, then types the prompt. Details and limits (footer markers tied to Claude Code 2.1.331, the untested ~20 s message gap): AGENTS.md "Successor restart of an interactive session".

## 4. Variants
Rebuild: fill AFTER A RESTART fully (first commands: `ctl-doctor.sh`, re-arm one monitor, check each host unit, re-test observer-lane availability and drop the profile-off default if it works). Successor takeover: add `nyxloom extract --preset readback` as the mechanical complement and name the transcript; adopt a controller name only with a stale lease or `--force`. Before the operator's `/compact`: the handover stays a FILE (text through the PreCompact hook is re-summarised and capped at 10,000 characters).
