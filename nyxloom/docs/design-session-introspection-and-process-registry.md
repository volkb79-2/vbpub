# Design: session self-introspection + a centralized process/resource registry

**Status:** proposal, not started. Written up for later revisiting, not for
immediate implementation — captured live during dstdns Wave B2 T1 (2026-09-17/18)
while the controller was running six real parallel implementers and hit every
problem this doc addresses, in production, in the same session.

**Origin:** operator-directed design discussion, `dstdns` session
`01HSSw2AWAN69Fg7fLQLJmMn`, 2026-09-18. Companion to
`design-context-lifecycle.md` / `design-context-lifecycle-experiments.md` —
read those first for the checkpoint/compaction background this doc builds on.
This doc is the "what should nyxloom grow to fix this" follow-on; the
experiments doc is the "what did we measure" record.

## 1. Motivating incidents (all real, all from one live wave)

These are not hypotheticals. Each is a concrete, dated, evidenced incident
from the same six-implementer dispatch, cited throughout this doc as the
justification for each proposed piece:

1. **Agents cannot know their own context size.** Verified empirically by
   reading a subagent's raw JSONL transcript directly
   (`agent-abb0678648ca91cd4.jsonl`, the P194 predecessor): the API's
   `usage` block (`input_tokens`, `cache_creation_input_tokens`,
   `cache_read_input_tokens`, `output_tokens`) exists as message-level
   *metadata* the harness writes to the transcript file, but a full-text
   search of every assistant message's actual visible content found **zero**
   instances of that data appearing as text the model itself reads. Every
   "~370k carried context, ~108 tool calls" figure an implementer reported
   this wave was an estimate extrapolated from tool-call counts and felt
   sense, not a measurement. This directly undermines the precision of the
   entire "ARM at ~250k" checkpoint discipline in `CLAUDE.md`'s "Long-running
   agent context discipline" section.
2. **The checkpoint threshold isn't scaled to the actual model.** The same
   transcript's own system reminder states the agent is running
   `Opus 5 (1M context)` — a 1M-token window. The ~250k arm figure (already
   revised up once from ~120k, per `CLAUDE.md`'s own history) was hit at 37%
   of that model's real ceiling, triggering a checkpoint+respawn cycle that
   was plausibly premature for that specific dispatch.
3. **`/compact` cannot be triggered on an Agent-tool subagent by any known
   mechanism.** Two independent findings converge on this: (a)
   `design-context-lifecycle-experiments.md` V1 addendum 2 — raw CLI
   `claude -p --resume <subagent-id> "/compact ..."` fails outright, because
   an Agent-tool subagent's id is not a resolvable CLI session UUID; (b) the
   same doc's line 1021-1025 — a `/compact`-prefixed prompt delivered as a
   plain message (via `ScheduleWakeup`, architecturally identical to how
   `SendMessage` delivers content to a subagent) does *nothing*: no
   `compact_boundary` written, no summary produced, context unchanged,
   because "a built-in local command is executed only when typed by the user
   at the prompt" — slash-command interception happens at the CLI's input
   layer, before text becomes a conversation message. The only proven
   mechanism for Agent-tool subagents is E-007's successor-brief respawn
   (fresh dispatch seeded with a self-authored BRIEF) — which pays a real,
   if partially-mitigated-by-good-BRIEFs, re-orientation cost every time.
4. **A subagent's self-armed `Monitor` on its own backgrounded gate silently
   never fired — a documented failure mode, reproduced live.** P199 (dstdns
   Wave B2, package B2-IO-MAIN) armed a `Monitor` on its own diagnostic gate
   and reported "waiting." File-timestamp forensics (`ls -la` on its own
   report/log files, cross-checked against `ps aux` and `ListAgents`) later
   showed the gate had genuinely *finished* 53 minutes earlier — a real
   result (`1 failed, 4054 passed`) sitting in a log file nobody read,
   because the agent's own wait mechanism never delivered the notification
   back to it. The agent was stuck indefinitely; the controller had no
   signal either, and only found it by actively going and checking file
   mtimes across every worktree. This is the *live* instance of the standing
   memory lesson `subagent-monitor-on-own-gate-can-silently-never-fire`
   (dstdns), not a new discovery — but it recurring on a real wave, at real
   cost (53 minutes of dead time on the wave's own critical path), is the
   concrete evidence this doc leans on for §3.
5. **`nyxloom extract`'s `manual_fresh` profile has an exact, quantified
   silent-truncation risk.** Read `session_extract/config.py` directly:
   `"manual_fresh": ExtractConfig(max_checkpoints=1_000_000, max_words=8_000,
   max_lifecycle_markers=-1)`. Checkpoint count and lifecycle markers are
   effectively disabled (they never trip); **the only live limit is an
   8,000-word budget**, and the backward walk stops there whether or not it
   has reached the true start of the log. Tested against the two longest
   real sessions available at the time (P194 predecessor, 640 JSONL lines /
   4 checkpoints / ~5.8k output tokens; P198, 1804 JSONL lines / 2
   checkpoints / ~4.6k output words) — the original dispatch prompt survived
   both times, but only because neither session's distilled content yet
   exceeded 8,000 words. **Neither test proves the failure mode is safe; both
   just didn't hit it yet.** The arithmetic says a session with more
   checkpoints (this pipeline's carve-review history alone regularly runs
   6-8 rounds) will start dropping content once total kept text exceeds the
   budget — and because the walk proceeds backward from the newest event,
   the **oldest** content (the original dispatch prompt — exactly the
   highest-value, hardest-to-reconstruct piece) is what goes first.

## 2. Goals

- **G1.** An agent (or its controller) should be able to learn its actual,
  measured context/token usage without guessing, and ideally without a
  dedicated extra round-trip that costs a full turn just to ask.
- **G2.** Background work a controller starts (a gate, a build, a long CLI
  child) should be observable through one consolidated query, not
  ad-hoc `ps aux` / `docker inspect` / log-tailing improvised per-incident —
  and its state history should survive even if nothing was actively
  watching at the moment it changed.
- **G3.** The specific, now-twice-proven "self-armed wait silently never
  fires" failure mode should become structurally hard to hit, not just
  documented as a lesson to remember next time.
- **G4.** `nyxloom extract`'s truncation behavior should fail loud, not
  silent, and should default to protecting the highest-value content (the
  original dispatch prompt) the same way `pack.py`'s "protected group"
  already protects `scope.touch` files from budget eviction.

## 3. Non-goals

- This doc does **not** propose reintroducing real `/compact` support for
  Agent-tool subagents — that's proven architecturally unavailable (§1.3),
  not a nyxloom problem to solve.
- This doc does not propose replacing the Agent-tool/`SendMessage`
  dispatch pattern wholesale. Its free completion-notification and genuine
  mid-flight steerability (real, load-bearing tonight — see the companion
  discussion in the session transcript: four real controller rulings
  delivered mid-flight only because the notification arrived immediately)
  are valuable properties a redesign should keep, not discard for the sake
  of gaining real compaction.
- No specific claim is made yet about whether Claude Code's own hook system
  can actually do what §4.2 proposes — that is flagged explicitly as
  needing verification, not asserted as working.

## 4. Proposed extension 1 — `nyxloom session-context`

### 4.1 The verb itself

A new `nyxloom` subcommand, `session-context <SESSION_LOG>` (same
positional-argument shape as `nyxloom extract` — a path, or a bare id that
`session_extract/locate.py`'s existing auto-discovery resolves), that reads
the *same* `usage` metadata `nyxloom extract`/the CLI's own `/context`
command already consume, and reports:

- Cumulative `input_tokens` + `cache_creation_input_tokens` +
  `cache_read_input_tokens` as of the most recent assistant message with a
  `usage` block (i.e., today's real, no-guessing figure for "how much
  context is this session actually carrying").
- The model's real context-window ceiling, read from the same system
  reminder text extract already has to parse to find "the model named X (Y
  context)" (this session's own transcripts prove that string is present:
  `"You are powered by the model named Opus 5 (1M context)"` /
  `"...Sonnet 5. The exact model ID is claude..."` — worth checking whether
  every model string reliably states the window size in dstdns's own logs,
  or whether a lookup table is needed for the ones that don't spell it out).
- A percentage-of-ceiling figure, so "you are at 37% of your real window"
  replaces a flat, unscaled "~250k" figure that means something different
  for a 1M-window Opus dispatch than a 200k-window one.
- Tool-call count and wall-clock elapsed, for parity with the qualitative
  signals agents currently guess from.

Output should be both a compact human line (for a hook to append, see §4.2)
and `--json` (for a controller/skill to parse programmatically) — same
convention `nyxloom extract` already uses.

### 4.2 Auto-injection after every tool call — the interesting idea, flagged as unverified

The operator's proposal: rather than an agent spending a whole extra
round-trip explicitly calling `nyxloom session-context` on itself, could
*every* tool result automatically have `nyxloom session-context`'s output
concatenated after it — so a single ordinary tool call (a `Bash` call it was
going to make anyway) always carries fresh context stats along for free?

**Mechanism candidate:** Claude Code's own hook system (distinct from the
CIU/compose hook system `AGENTS.md` §8 documents — do not confuse the two).
A `PostToolUse` hook, if Claude Code's hook API allows a hook to *append*
additional text to what the model sees after a tool result (rather than only
observe/log/block), could shell out to
`nyxloom session-context $CLAUDE_SESSION_ID --json` (or a lighter
text form) and inject its output as extra content following the tool's own
result.

**Open items, explicitly not resolved here:**
- Does Claude Code's `PostToolUse` hook actually support appending visible
  content to the tool result the model sees, or only side-channel actions
  (logging, blocking, exit-code-based control)? Needs checking against
  Claude Code's actual hook documentation/API before this is treated as
  buildable, not assumed from the general concept of "hooks exist."
- **Knowing the session's own id.** For a CLI child the controller launches
  with `--session-id <uuid>` (§5's dispatch pattern), the id is trivial to
  embed directly in the dispatch prompt's own text ("your own session id is
  `<uuid>`") since the controller picked it before launch — no discovery
  needed. For an Agent-tool subagent, the model does not appear to see its
  own `agentId` anywhere in its own context (it's assigned and returned to
  the *dispatcher* after the `Agent` tool call returns, not fed back into
  the subagent's own transcript) — a hook would need some other way to
  identify "which session is currently running" (e.g., an environment
  variable the harness itself exports per-session, if one exists — not yet
  checked).
- Cost: even if mechanically possible, this adds a `nyxloom session-context`
  subprocess invocation to *every single tool call* in the session, not
  just at checkpoint-adjacent moments. Worth measuring the wall-clock/CPU
  overhead before deciding this should be unconditional versus opt-in
  (e.g., only for sessions explicitly running under a checkpoint-discipline
  dispatch, flagged via an env var the dispatch prompt sets).

### 4.3 Alternatives considered

| Option | Pro | Con |
|---|---|---|
| **(a) Status quo — agents guess** | Zero implementation cost | Proven imprecise (§1.1); already produced at least one probably-premature respawn (§1.2) |
| **(b) `session-context` on demand, agent calls it explicitly** | Simple, no hook-API uncertainty | Costs a dedicated round-trip every time it's checked — the exact overhead G1 wants to avoid |
| **(c) Auto-append via `PostToolUse` hook** (proposed, §4.2) | No extra round-trip; rides an action the agent was already taking | Mechanism unverified; per-call overhead; needs a session-id discovery story for Agent-tool subagents |
| **(d) Controller polls externally, pushes down via `SendMessage`** | No nyxloom change needed, works today | Requires the controller to actively poll and remember to push — manual, easy to forget, doesn't make the *agent* self-aware |

**(c)** is the recommended target once the hook-API question is resolved;
**(d)** is a reasonable stopgap that could be piloted with zero new code
before investing in (c).

## 5. Proposed extension 2 — a centralized process/resource registry ("watch service")

### 5.1 Problem recap

Right now, every "wait for a background thing to finish" pattern in this
pipeline is bespoke and has already failed at least twice in documented,
independent incidents (memory `feedback-background-task-turn-boundary-kill`;
memory `subagent-monitor-on-own-gate-can-silently-never-fire`; §1.4 above is
a third, live recurrence of the second). The common shape: something gets
backgrounded (a `run_in_background` Bash call, a self-armed `Monitor`, a raw
`nohup`'d subprocess), and the *only* way to learn its outcome is whatever
specific, ad-hoc watching mechanism was set up for that one instance — which
has no persistence, no history, and no fallback if it fails to fire.

### 5.2 Proposed design

A registration + query service (either a new lightweight standalone daemon,
or a new subsystem inside the already-running `nyxloomd`, given it already
owns dispatch/monitoring/review for the factory-mode pipeline — see §5.4 for
the tradeoff):

- **`nyxloom watch register <handle> --pid <pid> | --container <name> | --gate <lane>:<worktree>`**
  — tell the service "this exists, I want its lifecycle tracked," returning
  a stable handle. Works for a bare PID, a Docker container name, or (as
  sugar) a `run-gate` lane invocation the service can correlate against
  `run-gate history`'s own store instead of re-implementing gate-specific
  tracking.
- **The service polls internally** (its own loop, independent of any
  particular controller session being alive to ask) — liveness
  (`kill -0` / `docker inspect .State.Status`), and periodic resource
  sampling reusing `cgprofile`'s existing cgroup-read logic (peak/avg
  CPU, memory, IO — no need to reinvent sampling, just centralize its
  *scheduling* and *storage*).
- **`nyxloom watch status <handle>`** (or `--all`) — one call returns:
  current liveness, exit code if finished, full state-transition history
  (registered → running → \[finished|killed\] with timestamps), and the
  resource-usage summary. This is the "one tool call gets you `ps` output +
  peak/avg resource use + status history" the operator asked for.
- **Notification, not just polling**: since the service already knows the
  instant something it's watching changes state, it's the natural place to
  also *push* — e.g., writing a small durable marker file a controller's
  own `ScheduleWakeup`-driven check-in can look for cheaply, or (longer-term)
  integrating with the harness's own task-notification channel if that's
  ever exposed to non-`Agent`-tool background work. Not required for a v1,
  but the registry's existence is the prerequisite for it either way.

### 5.3 Why this beats each current pattern

| Current pattern | Failure shown this session | What the registry fixes |
|---|---|---|
| Self-armed `Monitor` inside a subagent | Silently never fired, 53 min dead time (§1.4) | Watching lives *outside* the subagent's own turn lifecycle — a subagent's turn ending doesn't end the watch |
| `run_in_background` Bash + task-notification | Works when used correctly (P199's own recovery used it), but only for same-session Bash-launched processes, and only for the lifetime of that one Claude Code session | Registry entries persist independently of any one session's lifetime — a controller that got compacted or restarted can re-query by handle |
| Manual `ps aux` / `docker inspect` / log-tail forensics | Worked tonight, but only because the controller happened to think to check file timestamps — pure luck, not a designed signal | One `watch status` call replaces N ad-hoc commands and doesn't depend on the controller happening to notice something is stale |
| `run-gate history` (gate-specific, already exists) | Fine for gates specifically, but doesn't cover arbitrary registered PIDs/containers/CLI children | The registry can *wrap* `run-gate history` for gate handles rather than replace it — reuse, not duplicate |

### 5.4 Standalone daemon vs. `nyxloomd` subsystem

- **Standalone**: simpler to reason about and deploy independently of the
  factory-mode daemon's own lifecycle (which is explicitly paused/unpaused
  per-project and shouldn't gain unrelated responsibilities); a crash in one
  doesn't affect the other.
- **`nyxloomd` subsystem**: reuses an already-running, already-dashboarded
  process; avoids a second daemon to keep alive on a host already juggling
  multiple stacks. Given `nyxloomd` already tracks task state for
  daemon-dispatched work, extending it to ALSO track manually-registered
  PIDs/containers is a natural generalization rather than a new concept —
  but risks scope-creeping a component whose current job (factory dispatch)
  is deliberately separate from manual-controller work (per the existing
  "one dispatcher per project" rule).

No recommendation made yet — this is exactly the kind of choice worth a
short interview before implementation, not a unilateral pick baked into a
design doc no one has reviewed.

### 5.5 Alternatives considered

| Option | Pro | Con |
|---|---|---|
| **(a) Status quo, ad-hoc per-incident** | Zero cost | Proven fragile, twice, at real time cost |
| **(b) Discipline-only fix: ban self-armed `Monitor` inside subagents, mandate harness-native `run_in_background`** | Zero new code, immediately applicable (already done for P199 tonight) | Doesn't cover CLI children (no harness tracking at all, per `design-context-lifecycle-experiments.md`'s own finding); doesn't survive a controller restart/compaction; still requires the controller to remember to poll |
| **(c) Centralized registry service** (proposed) | Structural fix, not a discipline reminder; survives session boundaries; one query replaces N ad-hoc ones | Real implementation cost; a new thing to keep running and to secure/scope correctly |
| **(d) Extend `run-gate` itself to track arbitrary (non-gate) processes** | Reuses an existing, trusted, already-running mechanism | Scope creep on a tool whose whole identity is "gate execution," not general process supervision — probably the wrong owner |

**(b)** should happen immediately regardless of whether (c) is ever built —
it's free and already validated tonight. **(c)** is the real fix for G2/G3.

## 6. Supporting fix — harden `nyxloom extract` against the word-budget cliff (§1.5)

Concrete options, cheapest first:

1. **Immediate, no code change**: when invoking `extract --profile
   manual_fresh` for a session known to be long (many checkpoints), always
   pass an explicit `--max-words` override well above 8,000 (e.g., 20,000+)
   — the flag is documented as a separate axis from the profile and always
   wins. Zero-cost mitigation, applicable today.
2. **Loud-not-silent**: have the backward walk emit an explicit warning
   (to stderr, and/or a banner in the text output) whenever it stops due to
   `max_words` *before* reaching the true start of the log — i.e., whenever
   truncation actually happened, not just whenever the budget number was
   large. This doesn't prevent the drop, but converts it from indistinguishable-
   from-a-short-session into a visible, checkable fact — matching this
   whole pipeline's "a loud refusal beats a silent wrong answer" doctrine
   (same shape as `gate-base.sh`'s own case-3 refusal).
3. **Structural fix**: give the original dispatch prompt (the first kept
   record) the same "protected, never evicted" status `pack.py` already
   gives `scope.touch` files — i.e., the walk keeps a fixed word-budget for
   the middle/recent content but *always* keeps the earliest OPERATOR/
   dispatch-prompt-shaped record regardless of total length, only trimming
   the compressible middle harder to compensate. This is the most invasive
   but most correct option — matches this session's own hard-won pack.py
   lesson (protected-content-vs-evictable-content is exactly the same shape
   this doc's problem takes).

Recommend (1) as an immediate practice change (today, no nyxloom release
needed), (2) as a near-term small patch, (3) as the real fix if extract is
revisited with implementation time budgeted.

## 7. Phased implementation plan

- **Phase 0 (done — this doc).** Capture intent, alternatives, and the
  evidence base before any code changes, so a future session doesn't have
  to reconstruct tonight's reasoning from scratch.
- **Phase 1 (cheap, discipline-only, no nyxloom changes).**
  - Always pass an explicit `--max-words` override for long sessions
    (§6.1).
  - Standardize the dispatch-prompt commit trailer to name the *actual*
    model, not a hardcoded guess — this wave found the hardcoded
    `Claude Sonnet 5` trailer was wrong for both Opus-dispatched packages;
    a dispatch-prompt template should either omit a hardcoded model name
    entirely and instruct "use your own actual model name" or be generated
    per-dispatch from the known `model:` parameter.
  - Ban self-armed `Monitor` calls inside dispatch prompts for a subagent's
    own background gate; mandate the harness-native `run_in_background` +
    task-notification pattern instead (already applied to P199 tonight;
    should become the written rule in `nyxloom-dispatch`'s skill text, not
    just an ad-hoc correction).
- **Phase 2 (`nyxloom extract` hardening).** Implement §6 option (2) — the
  loud-truncation warning — as a small, low-risk patch.
- **Phase 3 (`nyxloom session-context`, §4.1).** New verb, reusing
  `session_extract`'s existing `usage`-block parsing and
  `locate.py`'s existing id-to-file resolution. No hook dependency — usable
  standalone (option (b) in §4.3) from day one.
- **Phase 4 (hook-based auto-injection, §4.2).** Contingent on resolving the
  open questions in §4.2 (hook API capability; session-id discovery for
  Agent-tool subagents). Treat as an experiment with a clear go/no-go
  checkpoint, not a committed deliverable.
- **Phase 5 (process/resource registry, §5).** The largest piece. Start
  with the standalone-vs-`nyxloomd` decision (§5.4) via a short interview,
  then implement register/status verbs against PIDs and containers first
  (gate-lane wrapping via `run-gate history` correlation can follow once
  the core registry is proven).
- **Phase 6 (roll into standing dispatch practice).** Update
  `CLAUDE.md`'s "Long-running agent context discipline" to reference real
  `session-context` figures scaled to the dispatched model's actual window
  (once Phase 3 exists) instead of one flat number for every dispatch;
  update `nyxloom-dispatch`'s skill text to route background waits through
  the Phase 5 registry instead of ad-hoc Monitor/`ps` improvisation.

## 8. Open questions carried forward

- Does every model's own system-reminder string reliably state its context
  window size in parseable form, or only some (Opus 5 said "(1M context)"
  explicitly — confirm whether every model does, or whether a lookup table
  is needed for the rest)?
- Does Claude Code's hook system actually support content-injection after a
  tool result, or only observe/block? (§4.2 — blocks Phase 4 entirely until
  answered.)
- Standalone watch daemon vs. `nyxloomd` subsystem (§5.4) — needs an
  explicit decision, not an implicit default.
- Is there an existing per-session identifier already exposed to a running
  Agent-tool subagent (an env var, a system-reminder field) that Phase 4
  could use instead of inventing a new discovery mechanism? Not checked
  yet — worth a focused grep across a subagent's env before assuming none
  exists.
