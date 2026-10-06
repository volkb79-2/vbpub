---
name: nyxloom-successor
description: Recover an Agent-tool subagent that was stopped by the user (SendMessage refuses) or whose session is lost -- build a mechanical extract of its transcript with `nyxloom extract --successor-brief`, check live state, and dispatch a FRESH successor with the original brief, the extract and a new order. Use when SendMessage says "stopped by the user and was not resumed", or after a restart. In any nyxloom-registered project.
---

# Successor from a stopped subagent

Evidence and measurements: `docs/design-context-lifecycle-experiments.md` E-020. Short form:
a subagent stopped by the CONTROLLER (TaskStop) resumes with `SendMessage`, context intact. A
subagent stopped by the USER (task list stop) is refused by policy, and the operator has no UI
path to message it either. Its transcript file survives on disk, so a fresh successor primed from
that file is the only recovery. `claude attach/respawn` act on `--bg` sessions, not on Agent-tool
subagents: do not use them here.

## 0. Decide that you need this
- `SendMessage(to=<agentId>)` first. A resume keeps full context (and usually a warm cache): always
  preferred. Only the refusal "stopped by the user and was not resumed", or a lost session
  (restart: still untested, E-020), means build a successor.
- `<transcript>.meta.json` carries `"stoppedByUser": true` for a user stop. The extract's stop state
  also shows it. A user stop and a controller-issued stop look the same in the transcript text (both
  render as a rejected tool call); the meta flag is the only distinction.
- The harness text says to treat user-stopped work as cancelled unless the user asks. The operator
  invoking this skill is that ask. If unsure, ask them.
- `TaskStop` the predecessor first if it might still be running (a watcher can re-wake a closed
  session). Never `SendMessage` a user-stopped agent. Never `Agent()` a duplicate of a live one.

## 1. Locate (read-only)
```
ID=<17-hex agentId>
ls ~/.claude/projects/*/*/subagents/agent-$ID.jsonl     # exactly one match, else stop
cat ~/.claude/projects/*/*/subagents/agent-$ID.meta.json   # model, agentType, description, stoppedByUser
```
`nyxloom extract` also resolves the bare id itself; the `ls` is to see the file path and meta.

## 2. Write the order, then extract (mechanical, no LLM)
Write the successor's order to a file first (scoped to exactly what is left; say what stands, what
is already done, the stop conditions). Then:
```
nyxloom extract --successor-brief --order @$SCRATCH/succ-$ID/ORDER.md $ID > $SCRATCH/succ-$ID/SUCCESSOR.md
```
`--successor-brief` implies `--preset successor`; the other presets are `--preset review` (audit an
agent at full fidelity), `--preset ledger` (only effects, files, stop state) and `--preset watch`
(operator + assistant prose of a live session; `--jsonl` for tooling). `nyxloom extract --help`
prints every expansion. Edit/Write tool calls carry no `description`, so an agent's edit intent is
only visible when it wrote an `Intent:` line in the same assistant message (best-effort pairing:
measured 0/3, 1/3, 5/5 without/with weaker/with a strict rule; NL-36). Re-verify the harness
assumptions on each new Claude Code version (see `session_extract/harness.py`); the extract warns
when the transcript version is not in `VERIFIED_HARNESS_VERSIONS`.
Defaults inside `--successor-brief` are the successor defaults: `--tool-calls intent-or-call`
(the tool's own description when present, else the call itself, one line, truncated),
`--tool-errors show` (failed results always rendered), whole-session ledger incl. the
external-effects bucket, and the stop state. Tune with `--tool-calls`, `--tool-errors`,
`--effect-pattern` (repeatable) / `--no-default-effect-patterns`, `--brief-max-chars`. Needs the
nyxloom release that ships these options (check `nyxloom extract --help | grep successor-brief`;
from a worktree: `PYTHONPATH=<worktree>/nyxloom/src python3 -c "from nyxloom.cli_harness import main; import sys; sys.exit(main())" extract ...`).

Read `SUCCESSOR.md` end to end before dispatch and check:
- the original brief section is the predecessor's own first prompt (verbatim, or a pointer + sha256
  when longer than `--brief-max-chars`);
- the stop state: cause, the in-flight call (it was rejected, NOT executed to completion), last
  assistant text = last intent;
- the ledger's `external effects`: each is a command the predecessor RAN (a failed or rejected one is
  annotated). The matching is heuristic (segment patterns); scan the timeline for effects it missed;
- every claim ("tests green", "pushed") is the PREDECESSOR'S OWN, not verified. Tool results are not
  in the extract except errors. Open the raw transcript around any result that matters.

## 3. Capture live state (the world moved, or the agent changed it)
From the `cwd` in the stop state / first record (a worktree path is the successor's working dir):
```
git status --short; git log --oneline -15; git diff --stat; git stash list
git worktree list; git log main..HEAD --oneline
```
Plus anything non-git the agent could have changed: containers (`docker ps` by EXACT name, never an
`ancestor=` filter), `ps` for orphan processes (a stop kills children; verify), snapshots/hosts via
the tool's read-only listing. Append the result to the order file or to a `STATE.txt` that the order
points at.

## 4. Dispatch
`Agent(subagent_type=<meta.agentType>, model=<meta.model>, run_in_background=true,
description="successor of <id>: <short>", prompt=<short>)` where the prompt only points at the file:
"Read `$SCRATCH/succ-$ID/SUCCESSOR.md` in full and follow it." The prompt must also carry the
standing line (see nyxloom-dispatch): "Every Bash tool call MUST set the tool's `description`
parameter to a short plain-language statement of what the command does and why; a successor reads
these." Plus the project's own dispatch rules (host-load rule, checkpoint clause, commit trailer).
The order must say:
- external effects already done (the ledger bucket, plus anything from step 3): VERIFY BY STATE,
  NEVER REPEAT; carry over the original brief's non-negotiables (never-touch lists, secrets rules);
- first action is a read-only state check; anything with external effect not authorized by the
  original brief or the order is reported before it is done.

## 5. Record
Predecessor id, stop cause, successor id, the SUCCESSOR.md path, the extract's cursor marker (the
`<!-- nyxloom-extract: ... marker=... -->` comment; it appears twice in the document, either works
for a later `--since-file`). Check the successor's liveness within about 1 hour.

## Secrets and hygiene
There is NO redaction in the extract: it summarizes what is in the transcript, and a prompt, a
Bash command or an error line may contain a token. Keep `SUCCESSOR.md` in the scratchpad (not in the
repo), point at the file instead of pasting its content into the Agent prompt (the dispatch is
logged in the parent transcript), and delete the scratch dir when the successor is done. If the
brief itself carries a secret, say so in the order and have the operator rotate it.

## Never
- never `SendMessage` a user-stopped agent; never retype the original brief (it comes from the file);
- never present a predecessor claim as verified; never repeat an external effect on the extract's
  word alone;
- never touch `~/.claude/skills` or the user-global `CLAUDE.md` from this skill.
