# nyxloom steering loop & roadmap basis — design

> Status: design proposal · 2026-07-16 · decisions D-S1..D-S3 from operator Q&A.
> Unifies the "no-dead-ends", "intake-over-ntfy", and "roadmap/proactive-direction"
> threads into one system: **a project never silently stalls — every state either
> progresses autonomously or produces a human-legible ask, and nyxloom raises that
> ask BEFORE it is blocking.**

## 1. The problem this solves

Over a long horizon a project must know: what work remains, when it is "done",
and **when it needs fresh human direction** — without stranding tasks or spinning
on low-value work. Today nyxloom has no systematic answer:

- The carver infers direction **ad-hoc each carve** from README/CLAUDE.md +
  roadmap + backlog + recent review follow-ups (`daemon.py` carve prompt). That
  is an LLM guess, not a structured comparison.
- There is **no product-definition / vision / feature-list** to diff against the
  current code. So nyxloom cannot tell "80% of the intended product exists" from
  "we are out of ideas", and cannot proactively say "the roadmap is nearly
  exhausted — I need direction."
- Rejected/blocked tasks can **strand** (REVIEW_REJECTED has a legal→QUEUED edge
  but no reconcile logic performs it — P31 demonstrated it live).

## 2. The basis: a product-definition north star

Each project gets a structured **product-definition** artifact — the north star,
*what SHOULD exist*: vision + feature-list + acceptance milestones. It is
**human-approved** (D-S1): nyxloom may DRAFT it (synthesised from README / SPEC /
ROADMAP / the code itself), the human refines/approves it, and milestones can be
added or refined interactively in Q&A (D-S3).

Two poles, and the gap between them is the work:

| Pole | What it is | Who owns it |
|---|---|---|
| **Product-definition** (north star) | what SHOULD be — vision, features, acceptance | human-approved (nyxloom drafts) |
| **Current state** | what IS — code + spec-from-code + merged/carving state | derived, mechanical |
| **The GAP** | north-star − current-state = remaining work | nyxloom computes |

(Distinct from the spec-from-code SoT initiative, which documents *what IS*. The
product-definition is *what SHOULD be*; their difference is the roadmap.)

## 3. The gap-analysis loop (the engine)

1. Compute the **GAP** = product-definition − current-state (features/acceptance
   not yet satisfied by code + merged work).
2. Project the gap into **ordered milestones** = the roadmap; each milestone
   decomposes into carve-able handoffs.
3. nyxloom **ticks** milestones done as their work merges — mechanical, no new
   direction (D-S1).
4. nyxloom **proposes** new milestones from the gap; a **human approves** any new
   direction (D-S1). Approval happens over the feedback channel / Q&A.
5. **Escalate EARLY** (D-S2, leading indicators) when: the gap shrinks toward
   zero (near-done); a milestone needs a product call (ambiguity); repeated
   rejects on a theme; open decisions piling up; ready-queue can't refill with
   high-value work.

This replaces the carver's ad-hoc inference with a structured
north-star-vs-state diff — and it is what makes proactive direction possible
(you can only "get ahead of it" if you can measure how much intended product is
left).

## 4. Three steering channels (all over ntfy `feedback`, reusing decision_chat)

The transport already exists: `commands.py CommandListener` polls the feedback
topic; `decision_chat.py` (P18) is a working bidirectional ntfy↔agent bridge
(read-only tool allowlist, `cfg.redact()` on every reply, ntfy-tag loop-guard).

1. **Human → nyxloom (new direction / define milestones):** intake-over-ntfy —
   route feedback-channel messages to `intake_chat.advance_intake` (today UI-only)
   for a new-direction interview → brief → P41 direct-carve. Can also define/refine
   product-definition milestones in Q&A (D-S3).
2. **nyxloom → human (needs direction):** proactive escalation — a "needs
   direction" digest built from **typed fields only** (never raw agent/log prose)
   when leading indicators fire.
3. **Mid-flight decisions:** decision-chat (exists) — a specific `D-NNN`.

## 5. No-dead-ends invariant

Every non-terminal state progresses or escalates — never silently strands:

- **REVIEW_REJECTED** → auto-requeue with the rejection feedback attached
  (bounded by `max_attempts_per_task`) → on exhaustion, escalate (a `needs
  direction` / `D-NNN`), do not strand.
- **BLOCKED** (typed blocker) → escalate if unresolved past a threshold.
- **max-attempts exhausted** → escalate, not silent terminal.

## 6. Decisions (from operator Q&A, 2026-07-16)

- **D-S1 — roadmap ownership:** nyxloom **ticks progress + proposes** next
  milestones; a **human approves new direction**. Not fully-autonomous roadmap
  edits; not human-only-read.
- **D-S2 — escalation cadence:** **early / leading-indicators** ("get ahead of
  it"), not only-when-blocked.
- **D-S3 — milestones are definable in Q&A** via the intake/decision chat.

## 7. Bootstrapping (how a project gets a basis)

nyxloom **drafts** a product-definition from the project's existing
README/SPEC/ROADMAP + a scan of the code, presents it over Q&A, the human
refines/approves; then the gap-analysis loop runs against it. **Start with
nyxloom itself** (dogfood; its SPEC/ARCHITECTURE/ROADMAP give a rich draft
source), then dstdns as the real-world test.

## 8. Phased carve plan

- **Phase A — product-definition artifact + schema:** the north-star doc format
  (vision + features + acceptance/milestones), a `[refs]` entry, a lint that it
  exists + parses. (Bootstraps §2.)
- **Phase B — gap-analysis:** compute product-definition − current-state → propose
  milestones; feed it into the carve sources (replaces the ad-hoc §1 inference).
- **Phase C — no-dead-ends:** REVIEW_REJECTED auto-requeue-with-feedback +
  escalation on exhaustion (§5).
- **Phase D — intake-over-ntfy:** feedback-channel → `intake_chat` bridge (§4.1).
- **Phase E — proactive escalation:** leading-indicator detector → typed "needs
  direction" digest (§4.2, §3.5).

Phases C/D/E are independently valuable and can land before A/B; A/B are the
foundation that makes "when do we need direction" answerable by measurement
rather than guess.
