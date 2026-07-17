# nyxloom operating model — the guided dark factory

> Status: architecture · 2026-07-16 · from operator co-design.
> Supersedes/absorbs `steering-loop-design.md` (its decisions D-S1..D-S3 carry).
> This is the STRATEGIC layer above the mechanical handoff workflow.

## The two layers

- **Mechanical substrate (exists today):** carve → implement → self-review (P40)
  → frontier-review → merge. Pure execution. It does not decide *what* to build
  or *why*, and it has no notion of "done."
- **Guided dark factory (this doc):** establishes and maintains DIRECTION, works
  at ANY project maturity, closes the loop with the human, and drives the
  substrate toward a measurable target. **Carves and the backlog are means to an
  end** — the direction spine is the end.

nyxloom's current weakness is that it has only the substrate: the carver infers
direction ad-hoc each run (README + roadmap + backlog), with no measurable target
and no reliable "we need human direction" signal. This layer fixes that.

## 1. The direction spine

The intent side is a lightweight hierarchy; it is paired with a reality side
derived from the code, and the **gap between them is the roadmap**:

```
  INTENT (what SHOULD be)                         REALITY (what IS)
  ─────────────────────────                       ──────────────────
  north-star / vision        the invariant WHY
      ↓ derives
  product-definition (vN)    features + accept.   ⟵ gap ⟶   code
      ↓ minus reality =                                     ↓ documents
  roadmap (milestones)       path to v(N+1)                 spec-from-code
      ∥ parallel
  backlog                    features + bugfixes ledger, folded into versions
```

- **north-star / vision** — the invariant why; rarely changes. For an empty repo
  it is defined FIRST (onboarding starts here).
- **product-definition, VERSIONED** — what the product IS at vN (features +
  acceptance). "vN fully realized" (gap→0) is the measured signal to escalate for
  v(N+1) direction — this is how "are we done / do we need direction" becomes a
  measurement, not a guess.
- **roadmap** — ordered milestones from current-state to the next product-def
  version. Derived as `product-def(vN) − current-state`.
- **backlog** — the parallel "don't forget" ledger (new features, bugfixes,
  review follow-ups, typed blocks); items get folded into a version/milestone.

**Properties:**
- **Not pure waterfall** — code-state and backlog feed back UP; the human
  reshapes ANY level (shuffle / shift / expand / downsize) and edits **cascade
  down** (a north-star change re-derives product-def → roadmap).
- **Ownership (D-S1):** nyxloom **ticks** progress + **proposes** milestones;
  the **human approves new direction**.
- **Thin-early:** levels may be minimal at first (a one-line north-star + a few
  backlog items is a valid start) and fill in via guided interaction. The spine
  must **never become bureaucratic overhead** — thin-early + guided reshaping is
  the mitigation.

## 2. Onboarding at any maturity (works in every scenario)

One onboarding **engine**, three **surfaces**: **CLI** (`exec-nyxloom onboard
<project>` — cockpit/scriptable), **UI** (dashboard "Onboard" / *loom
onboarding*), **ntfy/intake** channel. Flow:

1. **Non-AI wizard (deterministic, menu-driven, scriptable):** the user selects
   maturity (empty · partial · mature), docs (present · absent), **mode**
   (*derive-from-code* · *code-good-docs-absent* · *greenfield-define-it*), and
   which paths to scan. No AI — this clears the way for steps 2/3.
2. **Wizard answers enrich the scan/Q&A prompts** — the scan agent is told e.g.
   "mature codebase, no docs, derive the north-star from the code"; the answers
   are context add-ons, not just routing.
3. **`/review`-style assessment scan** (read-only agent, only if there is code):
   reads the selected areas → a STRUCTURED assessment (maturity, what exists,
   intent-implied-by-code, gaps). Skipped for an empty repo.
4. **Guided questionnaire** (extends `intake_chat`, over the chosen surface):
   fills the spine's gaps — **north-star FIRST** if missing (carved in user
   interaction), then product-def, seed roadmap + backlog. Wizard mode sets depth.
5. **Output:** a populated (possibly thin) spine the gap-engine runs against.

**nyxloom never assumes docs/spec/roadmap exist — it establishes them.** An empty
repo (no files at all) is a first-class case: onboarding → north-star first.

## 3. Gap-engine + reconciliation

- `product-def(vN) − current-state = gap` → milestones → carves.
- **Regular reconciliation:** tick milestones as work merges; fold backlog into
  versions; re-derive the roadmap when the spine changes. This REPLACES the
  carver's ad-hoc inference (§intro) and is what makes nyxloom efficient and
  north-star-oriented.

## 4. Guided human-steering loop

- **Proactive early escalation (D-S2):** ping for direction on leading indicators
  (gap→0, repeated rejects on a theme, decisions piling up, queue can't refill)
  — before stalling.
- **Intake-over-ntfy:** human opens a new-direction chat → brief → carve.
- **Guided spine-reshaping** + **D-NNN decisions** over the feedback channel.
- All channels reuse `decision_chat`'s read-only + redacted transport.

## 5. Self-correction of the mechanical substrate (hands-off correctness)

The factory must be self-correcting without a human catching mechanical slips:

- **No silent stranding (#16):** `REVIEW_REJECTED` → re-queue-with-feedback (if
  attempts budget remains) or escalate/`BLOCKED` — never a dead-end (P31 had to
  be re-queued by hand).
- **Robust verdict derivation:** do NOT depend on an exact review filename.
  Today `_parse_review_verdict` does one rigid `git show
  {branch}:{reports_dir}/{task-id}-REVIEW.md`; when the P42 reviewer wrote
  `P42-REVIEW.md`, the daemon found no `VERDICT:` and fail-safed to REJECTED — a
  genuinely-approved task **wrongly rejected, and hands-off it would then strand.**
  Fix: scan the branch for the task's verdict, and/or validate the reviewer wrote
  the file where expected and **fail the REVIEW LEG for a retry** rather than
  silently rejecting good work. Pair with #16 so even a wrong reject re-queues.
- **Backlog closes the loop:** review follow-ups + typed blocks → backlog → carve.
- **Borrowed invariant — durable waits:** "awaiting review/decision" is a
  first-class, crash-safe, non-stranding wait (Temporal/LangGraph *invariant*,
  implemented in our own event log, not the engine). It re-opens whenever fresh
  work arrives; it cannot storm or strand. Pin it with an F0 property test.

## 6. The carver as scheduler (parallelism, grouping, merge strategy)

The carver does not just write handoffs — it **plans execution** for a set of
related work, bounded by config (operator co-design 2026-07-17):

- **Merge strategy** — serial (impl A → review+merge → impl B …: zero conflict,
  slow) vs. **parallel-then-batch-review-merge** (A|B|C concurrent → ONE review
  resolves a 3–5 commit merge: trades a few review "thinking credits" + merge
  complexity for wall-clock). A config knob favours/forces `serial | batch |
  auto`; the carver picks per work-set from parallelizability + conflict risk.
- **Wave grouping = shared CONTEXT, not just component.** Batch handoffs into one
  review/session when they read the SAME large docs + surrounding code (component
  is a cheap proxy; even cross-component jobs with large identical context are
  worth one review/merge). Never batch disjoint context — the reviewer drags
  irrelevant context and gets confused. `max_wave_size = 5` default. The handoff
  author **estimates context size + overlap + conflict risk** (a `context_estimate`
  field) so the scheduler groups smartly.
- **`wave_id` is EPHEMERAL** (a per-review-pass batch), never persistent task
  state — the stale-`wave_id` strand (fixed 2026-07-17) came from treating it as
  durable. "Awaiting review" is the per-task durable wait above.

## Why a spine + reconciliation (the affirmation)

It turns "what next / are we done / do we need direction?" from an LLM guess into
a **measurement** (gap vs product-def). That is the difference between efficient,
north-star-oriented autonomy and drift. Risk: over-bureaucratizing — mitigated by
thin-early levels + guided reshaping.

## Foundational carves (phased)

- ~~**F6 — self-correction FIRST**~~ — **DONE** (2026-07-16/17: robust verdict,
  reject-loop #16, runaway watchdog, windowed/deduped spec-attention, +
  stale-wave_id + post-merge-validation strands found by F0 & fixed).
- ~~**F0 — behavioral test harness**~~ — **DONE** (scriptable fake agent + lifecycle
  + property/invariant tests; found the two strands above).
- ~~**F1 — spine schema + validator + config**~~ — **DONE** (c245a81: 4 spine
  schemas, non-AI S1–S4 validator, config keys, own trove migrated + lint-green).
- ~~**F2 — onboarding engine + non-AI wizard + surfaces** (CLI first)~~ — **DONE**
  (f15cf0d→0af17a2: `WizardAnswers`, `run_wizard` scaffolds trove + instantiates
  missing spine docs + records answers; idempotent; gate-green).
- ~~**F3 — `/review`-style assessment scan agent**~~ — **DONE** (92ed415:
  `run_assessment_scan` = read-only+redacted frontier-review agent → fail-closed
  structured `AssessmentResult`; greenfield short-circuits with no dispatch;
  stored in trove for F4; `onboard --scan` CLI flag; 13 non-hollow tests).
- ~~**F4 — guided questionnaire** → populate spine (north-star-first)~~ — **DONE**
  in two packages: **F4a** (30e48ac: `spine_writer` — the populating writer F1's
  validator/F2's placeholders lacked) + **F4b** (1bd976b:
  `onboarding_questionnaire.run_questionnaire` — read-only north-star-first
  dispatch → two fail-closed gates (envelope-parse + write-then-self-lint with
  bytewise restore) → drafts the spine via F4a; `onboard --questionnaire` flag).
  Consumes F3's `AssessmentResult`. NEXT: running the flow drafts nyxloom's own
  north-star for operator approval (decision **D-S1**, the one human gate) — the
  *content* is the operator's product vision, not machine-authored.
- **F5 — gap-engine + reconciliation** (replaces ad-hoc carve inference) — **NEXT.**
- **F7 — proactive escalation** (leading indicators → typed "needs direction").
