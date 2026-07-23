# Plan — plan-intake ("drop a plan → reconcile → decompose → dispatch")

Status: DRAFT · 2026-07-23 · operator-driven. This is the concrete design for the
"carve-from-plan" bridge previously only noted as a missing feature.

## Problem (operator, 2026-07-23)
A **plan doc is the natural output of a human–agent design discussion.** nyxloom cannot
consume one today:
- The **carver carves from the SPINE** — one backlog *line* per item. "Distill the plan into
  backlog items" is lossy: a plan's detail (decisions, DAG, oracles, file lists) does not fit
  a line.
- Handoffs must be **hand-authored to a strict schema.** "Hand-author the handoffs" pushes the
  entire spec-adherence burden onto the human for every plan.
- A plan arrives at **varying completeness/quality** (different authors, different planning
  depth) and may **contradict standing product decisions or even the north star.**

Goal: **drop a coherent plan and have the machine process it in its entirety — no per-session
hand-holding — while never silently overriding product direction.**

## Principle (operator-locked 2026-07-23)
1. **Human owns DIRECTION; machine owns MECHANICS.** The machine adheres to the handoff spec
   (via `nyxloom lint`) so the human never writes frontmatter.
2. **Surface, never silently resolve, direction conflicts.** Any clash between a plan and the
   north star / a standing `D-NNN` decision becomes a **decision-request** to the human (ntfy
   `feedback`), not an agent judgment call. Decomposition proceeds only after a ruling — which
   is recorded as a new `D-NNN`.
3. **Too thin to decompose → route back to planning**, not decomposed into guesses (serves the
   "varying quality" reality).

The operator's instinct — *"just redirect the carver to work the designated plan"* — is
correct. Two stages sit in front of that redirect: an **intake** and, load-bearingly, a
**reconciliation** (principle 2).

## Pipeline
```
discussion → plan doc (human prose, any quality) → [drop into intake inbox]
  1. INTAKE       register the plan, assign id + lifecycle (mirrors the handoff watcher)
  2. RECONCILE    plan × {north-star, decisions.md, spine} → reconciliation report:
                  each plan claim classified ALIGNED / CONTRADICTION / GAP / REDUNDANT.
                  · CONTRADICTION or judgment-needing GAP → decision-request (ntfy feedback)
                    → human rules → new D-NNN → resume
                  · verdict UNDERSPECIFIED → back to planning (F4 questionnaire/decision_chat)
                  · REDUNDANT (already built) → dropped from scope
  3. CARVE-FROM-PLAN   reconciled plan → a LINKED handoff series. Frontmatter derived from the
                  plan itself: tier from its tier hints, scope.touch/forbid from its file
                  lists, oracles from its acceptance criteria, depends_on from its DAG.
                  `nyxloom lint` gates; a lint failure loops to the carver (self-repair) — the
                  human is never asked to fix frontmatter.
  4. DISPATCH     the series enters the normal pipeline (route by tier → worktree → gate →
                  review → merge) — unchanged.
```

Why this dissolves the two rejected options:
- **"hand-author is too spec-bound"** → the *machine* does spec-adherence (stage 3 + lint);
  the human writes prose.
- **"a backlog line loses detail"** → the *plan itself* is the carver's source of truth
  (stage 3 reads the whole doc). A backlog item, if present at all, is a **pointer**
  ("carve from `plan-X.md`"), not a lossy summary.

## Reconciliation report (stage 2 — the new, load-bearing artifact)
Per plan claim / decision / package:
| verdict | meaning | machine action |
|---|---|---|
| ALIGNED | consistent with north star + standing decisions | carry into decomposition |
| CONTRADICTION | conflicts with the north star or a `D-NNN` | **halt → decision-request** (human: keep plan / revise plan / revise the standing decision / revise the north star) |
| GAP | assumes something not yet decided | decision-request *or* proceed under a stated assumption + a confirming `D-NNN` (see D-PI1) |
| REDUNDANT | already built/decided | drop from scope, note it |

This is roadmap-consistency-checking at intake time — the same muscle as #18 (roadmap
ownership), applied to an incoming plan.

## Plan is a first-class tracked entity (lifecycle in the SQLite store)
`DRAFT → RECONCILING → DECISIONS_PENDING → DECOMPOSED → DISPATCHED → COMPLETED`
(+ `NEEDS_PLANNING` for the underspecified route-back). Sits alongside tasks in state.

## What to build
- **F-PI1 plan-intake watcher** — detect a dropped/changed plan in `<trove>/plans/inbox/`;
  assign id + lifecycle. Mirrors the handoff watcher.
- **F-PI2 reconcile-plan stage** — the reconciler agent + report schema + decision-request
  emission. Reuses the existing `intake_chat`/`decision_chat`/`D-NNN`/ntfy-`feedback`
  machinery. *The heart of this feature.*
- **F-PI3 carve-from-plan stage** — extend the carver/`spine_writer` to take a plan as source
  and emit a lint-valid handoff series with self-repair.
- **F-PI4 plan lifecycle** in the store (plans beside tasks).
- **F-PI5 (opt)** proactive reconciliation: plan↔plan conflict + roadmap drift (folds into #18).

## Relationship to existing backlog
- **#17 (intake-over-ntfy chatbot)** = the human-ruling surface for stage-2 decision-requests.
- **#18 (roadmap ownership)** = the same consistency check, proactive rather than at intake.
- Supersedes the `nyxloom-plan-to-handoff-pipeline` memory's "carve-from-plan" note with this
  concrete design.
- **First three real inputs** to validate F-PI2/F-PI3 against: `plan-benchmark-ingest.md`
  (in flight now) and the two `wings-cgroups/*.md` implementation-go specs (rich, low
  reconciliation friction — good first test) plus a deliberately-thin plan (to exercise the
  UNDERSPECIFIED route-back).

## Open decision
- **D-PI1 — reconciliation authority default.** *Recommended:* **BLOCKING** on north-star /
  standing-decision CONTRADICTIONs (halt until ruled), **advisory** on GAPs (proceed under a
  stated assumption + a confirming `D-NNN`). Alternative: fully-advisory (report only, always
  proceed) — faster, but lets a plan quietly fight the north star, which is exactly what the
  operator said must not happen. Defaulting to the recommended hybrid unless overridden.
```
```
