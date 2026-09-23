---
kind: backlog-entry
schema_version: 1
id: NL-20
title: "Decide whether to build the session-context verb + process/resource registry (design doc undecided since 2026-09-18)"
status: open
type: "feature"
severity: "medium"
provenance: "dstdns controller session, 2026-09-22, re-surfaced during a session reflection; original design doc committed here at vbpub@fd67aece (docs/design-session-introspection-and-process-registry.md)"
filed_date: "2026-09-22"
---

## Observed mechanism

`docs/design-session-introspection-and-process-registry.md` (written
2026-09-18, ~24.6k bytes, cross-referenced from
`docs/design-context-lifecycle-experiments.md`) is a fully-reasoned design
proposal that has never been decided on or implemented — confirmed zero of
its 6 phases exist (`nyxloom session-context` is not a recognized verb as of
nyxloom 0.8.0). Filing this so the design doesn't sit undiscovered in `docs/`
indefinitely (it was found again purely by chance during an unrelated
dstdns session's reflection pass, ~4 days after being written).

## Why this matters (from the doc's own §1, evidence already gathered)

Five concrete incidents motivated it, all confirmed live during a real dstdns
Wave B2 dispatch session:
- Agents cannot know their own context size — proven, not guessed. The API's
  `usage` block (`input_tokens`, `cache_read_input_tokens`, etc.) is
  sideband message metadata the model itself never sees in its own generated
  text; every "~370k carried context" self-report an agent gave was an
  estimate from call-count/felt-sense, never a measurement.
- A 1M-context Opus dispatch was checkpointed against a ~250k threshold
  calibrated for a 200k-class model — respawned at only 37% of its actual
  ceiling, a real efficiency loss from not scaling the checkpoint threshold
  to the dispatched model's real window.
- `/compact` is proven unavailable for Agent-tool subagents (SendMessage
  can't resolve a subagent's id through the session index) — the only proven
  recovery path is a fresh-successor respawn seeded with a self-authored
  BRIEF+COMPACT.
- A subagent's own self-armed Monitor on its own background gate silently
  never fired (reproduced live, cost ~53 minutes before caught via file-
  timestamp forensics) — completion notices route to the dispatcher, not the
  subagent.
- `nyxloom extract`'s `manual_fresh` profile (`max_checkpoints=1_000_000,
  max_words=8_000, max_lifecycle_markers=-1`) has an exact, previously-
  undocumented 8000-word cliff: past that budget the backward walk drops the
  OLDEST kept content first, meaning a long enough session's original
  dispatch prompt is exactly what gets silently dropped. `--max-words`
  overrides the profile and always wins, but nothing warns when the default
  profile is about to truncate load-bearing content.

## Proposed contract (the doc's own, not decided)

Two new capabilities, deliberately left as separate phases so either can be
dropped without blocking the other:

1. **`nyxloom session-context` verb** (§4.1) — return a live/finished
   session's actual token usage (reusing `session_extract`'s existing
   `usage`-block parsing + `locate.py`'s id-to-file resolution). Lets an
   agent (or its dispatcher) ask for real numbers instead of guessing.
   Companion idea (§4.2, explicitly flagged **unverified**): auto-append this
   after every tool call via a hook, so a single ordinary tool call always
   carries fresh context stats — contingent on two open questions below.

2. **A centralized process/resource-registry service** (§5) — register a
   started process/container once, then query it (not each individual PID/
   gate) for full state history (`ps`-equivalent, resource peaks, status
   transitions), reusing `cgprofile`'s sampling and `run-gate history`
   instead of duplicating either. Standalone daemon vs. a `nyxloomd`
   subsystem is explicitly left undecided (§5.4).

## Behavioral oracles / what "done" would look like (per the doc's §6-7)

- `session-context`: given a session id, returns the SAME numbers a manual
  `jsonl-metrics.py curve` derivation would, for both a live and a finished
  session — no separate parsing logic per surface.
- Hook auto-injection (if pursued): a planted tool call under `PostToolUse`
  either does or does not carry appended content in the next turn's context —
  this is a factual capability question, answerable directly rather than
  inferred, and should be answered BEFORE any implementation work (§4.2).
- `nyxloom extract` truncation: a session engineered to exceed 8000 words of
  kept checkpoint content under `manual_fresh` should, post-fix, emit a loud
  warning (or refuse) rather than silently drop the oldest section — §6
  option (2), named as "a small, low-risk patch" and the cheapest of three
  tiered options considered.

## Spec section that owns this behavior

None yet — this would be new nyxloom capability, not a fix to documented
behavior. The design doc itself (§2-§7) is the closest thing to a spec today;
whichever phase is picked up first should promote the relevant section into
`SPEC.md`/`ARCHITECTURE.md` rather than leaving the design doc as the only
record (matching this backlog's own "spec-from-code" discipline elsewhere in
the estate).

## Disposition

Not asking for implementation — asking for the design doc to get an actual
go/no-go instead of sitting undecided. Two of its own open questions (§8)
need a human answer before phase 4 (hook injection) could even be attempted:
whether Claude Code's hook system supports content-injection after a tool
result (vs. observe/block only), and whether every dispatched model's own
system-reminder reliably states its context window size in parseable form.
Phase 1 (of 6) is explicitly free — discipline-only, applicable today,
requires no code.
