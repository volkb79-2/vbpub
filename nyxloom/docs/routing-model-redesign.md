# nyxloom routing-model redesign — design (capability-matched, cost-aware dispatch)

> Status: design · 2026-07-17 · decisions **D-R1..D-R9** captured from the
> operator interview. **Build AFTER F5** (gap-engine); the tier-rename (D-R1)
> folds into task #34 (role-scoped `build_dispatch`), which already touches the
> dispatch path. Companion to `nyxloom-operating-model.md`.

## Motivation

Today a *tier* is named by a model/effort proxy (`flash-high`, `flash-max`,
`terra-med`, `sonnet5-high`, `frontier-review`) and `Routes.for_tier(tier)[0]`
resolves it to the **first** route. This bakes the model into the tier name,
models no availability/cost/policy, and provides **no capability guarantee**
between an implementer and its reviewer. This redesign makes the *tier describe
the work* and the *route a swappable, policy-driven selection*.

## D-R1 — Tiers name the TASK, not the model

Tier = `<task-type>-<complexity>`:
`implementation-{easy,average,complex}`, `review-{easy,average,complex}`
(carve / intake / decision keep task-typed tiers too). The model/effort/provider
becomes a **route**, selected at dispatch. Multiple routes per tier, e.g.:
- `implementation-easy`: haiku-high, deepseek-flash-high, openrouter-free
- `implementation-average`: sonnet-high, deepseek-flash-max
- `review-*` resolve to strictly stronger routes than the same-band impl tier (D-R2).

## D-R2 — Capability-matched review (invariants)

- **(a)** A reviewer must be capable enough to review.
- **(b)** A reviewer is **strictly more capable** than the implementer it reviews.
- **(c)** Review tier follows implementation tier by complexity band
  (impl-easy→review-easy, impl-average→review-average, impl-complex→review-complex)
  — but within a band the review ROUTE resolves to a **stronger model** than the
  impl route, so (b) always holds (e.g. impl-easy=haiku-high → review-easy=sonnet-high).
- **(d)** Carve authority by review tier: `review-average`/`review-complex` may
  carve any handoff; `review-easy` may carve **only** `implementation-easy`
  handoffs. A follow-up carve cannot exceed the carver's own review capability.

## D-R3 — Tier PREDICTION is the crux (carver responsibility)

The hard problem the operator flagged: *knowing upfront what intelligence a task
needs.* The carver estimates task complexity → assigns the implementation tier
(which drives model + cost). Under-estimation is caught by the fail-closed net:
an under-provisioned agent hits **BLOCKED** → the reconciler escalates up a tier.
Over-estimation wastes money. So estimation quality is a first-class cost lever:
**track predicted-vs-actual** (did it BLOCK / need escalation / pass first try?)
to calibrate future predictions.

## D-R4 — Availability layer (temporary disable, config preserved)

Independently toggle-able enabled/health state; the toml config is **not removed**:
- **CLI tool** disabled (bugged / unavailable).
- **Provider** disabled (no credits, session-limit reached, high error rate).
- **Model** disabled (surfaced security issue, changed cost).

A disabled entity is skipped during route selection; `reconcile.py`'s existing
"no-healthy-route" check (`reconcile.py:931`) extends to consult it. Health is
observed (probe + error-rate + session-limit) and/or operator-set.

## D-R5 — Cost model (configurable posture)

Route selection among *available* routes for a tier is driven by a **configurable
objective** (global default + per-project override):
- **prepaid-first** (likely default): burn included subscription tokens
  (Anthropic/OpenAI plans, resetting session limits) up to a **per-plan reserve
  threshold** (leave minimal self-use) before any per-use API spend; among
  per-use, cheapest viable.
- **reliability-first**: prefer native/most-reliable; cost is a tiebreaker only.
- **cost-min**: free/cheapest first, escalate on failure/BLOCKED.

Requires: (1) **session-limit monitoring** per prepaid plan; (2) **per-job token
estimation** to decide whether remaining session budget suffices; (3) **provider
price awareness** — OpenRouter (≤ +5.5% fee) vs native, noting the **cache-hit
asymmetry**: native deepseek cache-hit input `$0.0028`/M vs openrouter/deepinfra
`$0.018`/M, but OpenRouter is cheaper on cache-*miss* input + output. Cost-optimal
therefore depends on the **cache-hit ratio** of the workload. (4) optional
free-model use (openrouter free coding models).

Reference prices (per 1M tokens, 2026-07-17):

| Provider [via]                  | input (cache hit) | input (cache miss) | output |
|---------------------------------|-------------------|--------------------|--------|
| deepseek [deepseek]             | 0.0028            | 0.14               | 0.28   |
| deepseek [openrouter/deepinfra] | 0.018             | 0.09               | 0.18   |
| deepseek [openrouter/streamlake]| 0.019             | 0.097              | 0.193  |

## D-R6 — Per-project route policy (hard + soft)

A per-project filter over the route pool, applied **before** cost/availability selection:
- **Hard** constraints: no-china-models, no-openrouter, no-model-X, data-protection.
- **Soft** preferences: prefer-X.

## D-R7 — Self-contained, sandboxed agent runtime (accepted; replaces manual env)

nyxloom **ships and version-manages the agent CLIs in containers** (ciu-managed,
cgroup-protected), holds its own API tokens (secrets), and manages deps —
replacing today's "reuse host-preconfigured CLIs + manually-provided env."

- **REQUIREMENT (operator):** the managed repos / worktrees **must be mounted
  into the CLI containers** — agents read the code and write their worktree. This
  is the concrete ciu coupling (bind-mount the project tree + `.worktrees/`).
- "Borrow the plumbing (ciu containers + cgroups), keep the moat" applied to the
  runtime itself: the system is protected from a misbehaving agent, and a run is
  reproducible rather than host-dependent.
- **Migration:** incremental — containerize the CLIs into the stack first, keep
  tokens external initially, then internalize credential management.

## D-R8 — Reviewer on-the-fly fixes (configurable, serial-favored)

**Configurable** policy. When enabled, an already-engaged reviewer is **encouraged
to fix issues it finds — even beyond its original task scope** — when that saves a
carve/dispatch round-trip (a real time + cost saver). **Safest in serialized
operation**; in parallel/batch scheduling, out-of-scope reviewer edits risk
conflicts, so the policy **couples to the scheduling mode**: serial → inline-fix
on/encouraged; batch → bounded (reviewed-diff files only) or off. A reviewer that
fixes MUST re-gate and record what it changed (trust-git-not-receipts still holds).

## D-R9 — `route doctor` verb (supporting)

A CLI verb to (a) **validate** `routes.host.toml` syntax/content against the
schema (extends `nyxloom lint`), and (b) **actively test** each route end-to-end
(CLI present + `--version`, model reachable, auth valid, usage_source parses).
Surfaces unhealthy routes before dispatch instead of discovering them mid-carve.
Feeds the D-R4 availability layer.

## D-R10 — Persistent strategic carver (single session, resumed across cycles)

**Verified against current code (2026-07-19):** every `CarveDispatch` (P16)
mints a brand-new synthetic task + `Attempt` + fresh `adapters.build_dispatch`
call — the carver has **no memory across cycles**; each cycle re-reads
backlog/roadmap/decisions cold. The `claude-opus-high` route (inherits
`claude-sonnet5-high`) already carries `session_capture = "newest-jsonl"` and a
full `resume` template — the CLI-level plumbing for session reuse already
exists (used today only for the IMPLEMENTER interrupt-resume path,
`daemon.py:2054`), just never applied to CARVER.

**Decision:** exactly ONE carver identity, resumed via `attempt.session_handle`
+ `adapters.build_resume` for as long as the current `carve_authority ==
'branch'` carve branch/worktree is still live and un-admitted. A NEW
branch/worktree — and therefore a fresh session — is minted only once the
prior carve branch is admitted (merged) or explicitly abandoned. Review agents
never gain carve authority themselves: D-R8's on-the-fly fixes stay bounded to
the reviewed diff; anything bigger becomes a `REVIEW_REJECTED` task that (see
P45) transitions to `READY_TO_CARVE` — a state that already exists in
`types.py`'s frozen `TASK_TRANSITIONS` but has no handler (a pinned
`xfail(strict=True)` in `tests/test_invariants.py`, filed 2026-07-17,
untracked until now) — which only the single persistent carver ever consumes.
This is the concrete shape of "review agents may flag/propose carve-worthy
work; the single carver remains the sole carve authority."

**Open design question, deliberately NOT built in P44/P45:** the exact
staleness/rotation check ("is the prior carve branch still live and
unmerged?" — worktree-exists + `git merge-base --is-ancestor` against
`default_branch`) needs its own careful package, tracked as a follow-on
(P46-designate) rather than guessed at here.

## D-R11 — External, daemon-driven context compaction

**Verified against current docs (2026-07-19):** there is no hook or API
letting an agent compact its own context mid-turn — Anthropic tracks this as
an open feature request (`anthropics/claude-code#38925`). The only two
triggers are the context window actually filling up, or an external caller
sending the literal string `/compact` as the next prompt to a **resumed**
session. Since D-R10 already makes the daemon the external caller holding the
carver's `session_handle`, the daemon can do exactly what a human does
manually: after each carve cycle, read usage via the route's `usage_source`
(`output-format-json` for `claude` routes, `session-json` for `opencode`
routes), and once cumulative usage crosses a configurable threshold, issue one
`build_resume(..., prompt="/compact")` call before the next real carve packet.
This is the daemon-side mechanism **B10** ("session-limit monitoring +
per-job token estimation") was already scoped to cover — D-R11 folds into B10
rather than adding a new backlog item.

## D-R12 — Benchmark/pricing-API-driven route scoring

Two machine-readable sources checked live (2026-07-19), relevant to D-R3's
tier-prediction and D-R5's cost model:
- **Artificial Analysis Data API** (`GET /data/llms/models`,
  `x-api-key` auth, free tier 1,000 req/day) — returns BOTH a capability score
  (Intelligence/Coding/Agentic Index) and pricing per model in one schema; the
  natural backing table for tier→route scoring.
- **OpenRouter `/api/v1/models`** (public, no auth) — the right source for
  D-R4's availability layer to dynamically discover currently-`:free`-suffixed
  models instead of hand-curating `routes.host.toml`'s `[tiers.free-high]`
  block.
- **LMArena/Chatbot Arena** — no official API (only unofficial community
  mirrors); a secondary cross-check signal at most, never a hard dependency.

## What folds where

- **North-star** (identity-level): capability-matched review (D-R2), the
  self-contained sandboxed runtime (D-R7), cost-aware/policy-driven routing
  (D-R1/R5/R6), and the human control/escalation surface. See north-star draft.
- **This design doc**: the full D-R1..R12 contract.
- **Pulled forward (2026-07-19, operator directive):** D-R10 + D-R11 are
  P44 (role-scoped `build_dispatch`, closes #34/B7) + P45 (`READY_TO_CARVE`
  dead-end fix + review-initiated micro-carve routing, closes #25/#26/B8),
  ahead of the "after F5" sequencing below.
- **Build epic** (still after F5): tier-taxonomy rename (D-R1); availability
  layer, cost model, per-project policy, self-contained runtime, reviewer-fix
  policy, `route doctor`, and D-R12's benchmark-API scoring are phased
  packages.

## Sequencing

Design now (this doc). D-R10/D-R11 build **now** (P44/P45, operator-prioritized
2026-07-19). Everything else builds **after F5** (gap-engine). D-R1 (tier
rename) folds into #34 (role-scoped `build_dispatch`, delivered by P44); the
rest is a phased epic to be carved once F5 lands.
