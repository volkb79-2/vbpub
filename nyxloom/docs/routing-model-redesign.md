# nyxloom routing-model redesign — design (capability-matched, cost-aware dispatch)

> Status: design · 2026-07-17, extended 2026-07-23 · decisions **D-R1..D-R15**
> captured from operator interviews. **Correction (2026-07-23):** the earlier
> note that the tier-rename (D-R1) "folds into P44" was wrong — P44 delivered
> only role-scoped *prompt text*; the tier taxonomy was still model-named in
> the live matrix (`sonnet5-high`, `flash-high`, …) at that time. **D-R1 has
> since landed (B16, 2026-07-23)** — see its section below for the concrete
> mapping; the rest of the capability/catalog/UI work (D-R12..D-R15) remains
> a bundle that is **file-disjoint from F5 (gap-engine) and therefore
> parallelizable with it**; only D-R3 (carver complexity→tier prediction)
> couples to the carve path and rides with F5. Companion to
> `nyxloom-operating-model.md`.

## Motivation

Today a *tier* is named by a model/effort proxy (`flash-high`, `flash-max`,
`terra-med`, `sonnet5-high`, `frontier-review`) and `Routes.for_tier(tier)[0]`
resolves it to the **first** route. This bakes the model into the tier name,
models no availability/cost/policy, and provides **no capability guarantee**
between an implementer and its reviewer. This redesign makes the *tier describe
the work* and the *route a swappable, policy-driven selection*.

## D-R1 — Tiers name the TASK, not the model · canonical vocabulary locked 2026-07-23

Tier = `<verb>-<band>` where verb ∈ {`implement`, `review`, `carve`, `intake`,
`decide`} and band ∈ {`1`, `2`, `3`} (1 = simplest, 3 = hardest). Examples:
`implement-2`, `review-3`, `carve-2`. A short legend lives beside the matrix
(band 1/2/3 ≈ low/medium/high complexity), since numeric bands are deliberately
compact rather than self-documenting (operator choice 2026-07-23). The
model/effort/provider becomes a **route**, selected at dispatch; multiple routes
per tier, ordered by the cost posture (D-R5), e.g.:
- `implement-1`: haiku-high, deepseek-flash-high, openrouter-free
- `implement-2`: sonnet-high, deepseek-flash-max
- `review-*` resolve to strictly stronger routes than the same-band impl tier (D-R2).

**This is a DATA migration, not a schema change.** The handoff frontmatter
*already* carries a single abstract `tier:` field (schema
`handoff-frontmatter.schema.json`: *"Never a CLI or provider name."*); only the
VALUES in use are still model proxies (`sonnet5-high` ×32, `flash-high` ×2,
`frontier-review` ×1 across the live troves). D-R1 is therefore:
1. rename the `[tiers.*]` keys in `routes.toml` (`sonnet5-high`→`implement-2`, …);
2. rewrite the `tier:` values in existing handoffs;
3. replace the 3 hardcoded `"frontier-review"` string literals
   (`reconcile.py:875`, `daemon.py:2428`, `daemon.py:3707`) with a per-role tier
   lookup;
4. wire the currently-**dead** `RouteDef.role_default` (`config.py:464`, read
   nowhere today) as that lookup's backing — a pre-cut socket for exactly this.

**Status: BUILT (B16, 2026-07-23).** `routes.host.toml`'s `[tiers.*]` keys are
verb-band names; all 20 live `nyxloom-trove/handoffs/*.md` carry a verb-band
`tier:` value; the 3 hardcoded `"frontier-review"` lookups now resolve via the
new `Routes.for_role(Role.FRONTIER_REVIEW.value)` (`config.py`, added right
after `for_tier`), backed by `RouteDef.role_default` (now wired, no longer
dead). Each consolidated tier kept its previously-standalone tier's route
PRIMARY, preserving today's dispatch behavior (cost-posture re-ranking is
still the separate, undone D-R5). The concrete mapping applied:

| old tier (model-proxy) | new tier (verb-band) | primary route preserved |
|---|---|---|
| `sonnet5-high` | `implement-2` | `claude-sonnet5-high` |
| `flash-high` | `implement-1` | (folded in; `claude-haiku` stayed primary) |
| `flash-max` | `implement-2` | (folded in; `claude-sonnet5-high` stayed primary) |
| `haiku-low` | `implement-1` | `claude-haiku` |
| `luna-high` | `implement-2` | (folded in; `claude-sonnet5-high` stayed primary) |
| `terra-med` | `implement-1` | (folded in as a shared fallback route) |
| `frontier-review` | `review-3` | `claude-opus-high` |
| `free-high` | `implement-1-free` | (opt-in tier, unchanged route set) |

`schemas/routes.example.toml` was updated to the same taxonomy for consistency
(its own illustrative `luna-med`/`sol-med` tiers are outside this map and were
left as-is — they name routes that don't exist in the live matrix).

## D-R2 — Capability-matched review (invariants)

- **(a)** A reviewer must be capable enough to review.
- **(b)** A reviewer is **strictly more capable** than the implementer it reviews.
- **(c)** Review tier follows implementation tier by band
  (`implement-1`→`review-1`, …) — but within a band the review ROUTE resolves to
  a **stronger model** than the impl route, so (b) always holds
  (e.g. `implement-1`=haiku-high → `review-1`=sonnet-high).
- **(d)** Carve authority by review tier: `review-2`/`review-3` may carve any
  handoff; `review-1` may carve **only** `implement-1` handoffs. A follow-up
  carve cannot exceed the carver's own review capability.
- **(2026-07-23)** "Strictly more capable" is evaluated on the **per-axis
  capability vector** of D-R13: `review-*`/`carve-*` compare on the
  reasoning/agentic axis, not the coding axis a `implement-*` tier gates on.

## D-R3 — Tier PREDICTION is the crux (carver responsibility)

The hard problem the operator flagged: *knowing upfront what intelligence a task
needs.* The carver estimates task complexity → assigns the implementation tier
(which drives model + cost). Under-estimation is caught by the fail-closed net:
an under-provisioned agent hits **BLOCKED** → the reconciler escalates up a tier.
Over-estimation wastes money. So estimation quality is a first-class cost lever:
**track predicted-vs-actual** (did it BLOCK / need escalation / pass first try?)
to calibrate future predictions.

**(2026-07-23) This is the ONE routing decision that couples to F5/gap-engine** —
both write the carve path (gap-engine emits carve candidates; D-R3 stamps each
candidate's band). So D-R3 sequences **with** F5, while the rest of the routing
bundle (D-R1, D-R12..D-R15) is parallelizable ahead of it.

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
free-model use (openrouter free coding models). Price data is sourced from the
D-R13 capability catalog (Artificial Analysis pricing arrives in the same schema
as the scores).

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

## D-R12 — Benchmark/pricing sources → a pluggable registry

Two machine-readable sources checked live (2026-07-19), relevant to D-R3's
tier-prediction and D-R5's cost model:
- **Artificial Analysis Data API** (`GET /data/llms/models`,
  `x-api-key` auth, free tier 1,000 req/day) — returns BOTH per-axis capability
  scores (Intelligence / Coding / Agentic Index) and pricing per model in one
  schema; the natural backing table for tier→route scoring.
- **OpenRouter `/api/v1/models`** (public, no auth) — the right source for
  D-R4's availability layer to dynamically discover currently-`:free`-suffixed
  models instead of hand-curating `routes.host.toml`'s `[tiers.free-high]`
  block.
- **LMArena/Chatbot Arena** — no official API (only unofficial community
  mirrors); a secondary cross-check signal at most, never a hard dependency.

**Decision 2026-07-23:** sources are a **pluggable `BenchmarkSource` registry**
that mirrors `free_models.py`'s shipped `@register_kind` / `FreeModelSource`
pattern — Artificial Analysis, LMArena, Aider-polyglot, LiveBench, SWE-bench
each a swappable plugin; blend/prefer configurable per deployment. Only the
OpenRouter free-model-discovery half is built today (`free_models.py`); the
capability-scoring half is D-R13.

## D-R13 — Model capability catalog (per-axis vector; operator thresholds) — decided 2026-07-23

The concrete artifact D-R12's sources feed, and the **capability half** bolted
onto `free_models.py`'s **discovery half**. A persisted, refreshable catalog
maps EVERY model (free + paid, all providers) to a **capability vector**, not a
scalar:

- **Per-axis scores** (operator choice): distinct axes — Coding, Agentic,
  Intelligence/reasoning — kept **separate, never blended**. `implement-N` tiers
  gate on the **coding** axis; `review-N` / `carve-N` gate on the
  **reasoning/agentic** axis. A model can be `implement-3` yet only `review-2`.
  This is the faithful "work types" mapping the operator asked for (a strong
  coder is not automatically a strong reviewer).
- **Hard filters on top of scores:** `context_length` (already in
  `DiscoveredModel`) and capability flags (vision, tool-use/function-calling)
  hard-exclude a route when a task declares it needs them, regardless of score.
- **Band cutoffs = operator-set per-axis thresholds** (operator choice): config
  declares `band 3 = coding_index ≥ N`, etc. Deterministic, inspectable, and
  stable — a newly-discovered model bins itself without shifting everyone else's
  band (the rejected alternative, relative-ranking, would have drifted bands as
  the roster changed).
- **Band count = 3 now (1-3), mechanism open to 5** (operator question
  2026-07-23): three bands match the model market's natural strata — band 1
  cheap/mechanical (haiku/flash/free), band 2 mainstream workhorse
  (sonnet/deepseek-max/gpt-5.6-luna), band 3 frontier (opus/gpt-5.6-high) — and
  keep the carver's D-R3 band PREDICTION tractable (fewer bands → fewer
  mispredictions; the BLOCKED→escalate-up net cheaply corrects under-provisioning).
  Bands also multiply by role (3×{implement,review,carve}=9 tiers; 5×3=15). Because
  bands are threshold-defined (above), extending to 1-5 later is a config-only
  change (two more threshold rows + tier keys), never a schema migration — so we do
  not pay for granularity the noisy benchmark scores cannot yet resolve.
- **Role authority = hybrid, with an auto switch** (operator choice): the
  benchmark auto-sets the complexity BAND; role-eligibility (may-review /
  may-carve) is operator-confirmed before a model goes live in a review/carve
  tier. A config flag (`capability_map.role_gating = "auto"`) opts into
  fully-automatic role assignment for operators who trust the feed.
- **Managed-block writer**, exactly like `free_models.write_routes_toml`: the
  catalog is a sibling managed block; **`config.py` stays frozen core** (no edit).

Shape: extends `DiscoveredModel` → a `CapabilityRecord` (adds per-axis scores,
price, band-per-axis, `may_review`/`may_carve` flags). New module
`capability_map.py`; benchmark plugins live in `benchmark_sources.py` (the D-R12
registry).

## D-R14 — Routing/capability UI panel (read-only, inside the F012 dashboard) — decided 2026-07-23

A read-only view surfacing BOTH the catalog and the live resolution (operator
choice "catalog + live resolution + why"):
- **Catalog table:** every model × per-axis scores × price × privacy ×
  availability × band.
- **Per-tier resolution:** for each `<verb>-<band>` tier — the resolved winning
  route, the runners-up in order, and **which filters fired** (policy hard-block,
  availability/health, cost posture) — i.e. *why did `carve-3` pick THIS model.*

Renders from the same files the resolver reads; **no second aggregation engine**
(consistent with the north-star's thin-client rule). Folds into F012.

## D-R15 — Scheduled-jobs subsystem (daemon-owned cron) — decided 2026-07-23

The operator chose daemon-scheduled capability refresh (benchmarks move slowly;
availability/health stays a separate fast live probe) and, in doing so,
generalized it into a first-class subsystem — "the daemon kicking off its own
cron definitions is trivial":

- The daemon owns a set of scheduled jobs. **Capability-catalog refresh is the
  first consumer;** free-models refresh and `route doctor` probes (D-R9) are
  natural follow-ons.
- **Two job origins, one resolved conflict** (operator's design call):
  **config-driven** jobs (declared in toml, e.g. `capability_map.refresh_interval`)
  are the source of truth and render **read-only** in the UI; **user-driven**
  jobs added through the UI are mutable there. This dissolves the
  settings-interval-vs-hand-edited-cron ambiguity: config owns config-jobs, the
  UI owns UI-jobs, neither silently overwrites the other.
- The UI **shows all defined jobs** (both origins) with schedule + last-run; only
  user-driven rows are editable.

The underlying refresh operation stays a plain callable (a cron firing is just a
scheduled invocation), so it remains invocable ad-hoc via `exec-nyxloom` at zero
extra cost — same as `free-models refresh` today.

## D-R16 — Per-task permissions: separate the OS sandbox from the scope allowlist — decided 2026-07-23

Operator concern: past handoffs could not be completed because of "restrictions."
Diagnosis — there are TWO distinct restriction axes that fail differently, and
the one that historically bit is NOT the OS sandbox:

- **Axis A — OS/process sandbox** (codex `--sandbox`, claude
  `--dangerously-skip-permissions`, opencode `--auto`, reasonix
  `[sandbox]`/`[permissions]`): what the agent PROCESS may do. **These permission
  levels are LOCAL CONFIG per CLI, NOT vendor ship-defaults** (operator
  correction 2026-07-23): in this environment codex/claude were
  operator-configured wide-open (`danger-full-access` /
  `--dangerously-skip-permissions`), while reasonix shipped conservative
  (`bash=off`, `permissions.mode=ask`) and had to be relaxed (`bash=enforce`,
  `mode=allow`) before it could even self-test in the 2026-07-23 free-model
  trial. So "permissive" is a knob each CLI exposes, not a property of the CLI.
  D-R7's ciu/cgroup containerization ADDS isolation, but its job is to bound
  **blast radius** (host, other worktrees, secrets) — NOT to restrict the agent
  within its own workspace. Invariant: **inside its assigned worktree + declared
  environment the agent keeps full read/write/exec; the sandbox only walls off
  everything else** (exactly D-R7's "mount the worktree into the container"
  requirement).
- **Axis B — handoff `scope.touch`** (the per-task edit allowlist, enforced by
  review/lint, not the OS): which files the TASK permits editing. **This is what
  bit** — P26 + P31 failed because the handoff forbade a file the correct
  implementation needed, so the agent either faked a hollow workaround or
  hard-BLOCKED. Neither ships the work.

Decisions:
1. **nyxloom OWNS the OS-sandbox level per route/task — do NOT inherit each CLI's
   ambient local config.** Because the level is a per-CLI knob (above), a route
   must set it deliberately so the SAME task gets the SAME effective permissions
   regardless of which CLI runs it — otherwise a task lands wide-open on codex but
   blocked on a stock reasonix (exactly what happened in the free-model trial
   until reasonix was relaxed). Default it permissive-WITHIN-worktree for
   implementers (the luna/B17 trial needed `danger-full-access` to run its own
   tests; a read-only sandbox blocks a legitimate task), and tighten only where a
   task declares it (Decision 4).
2. **Authoring rule + lint:** every oracle must be satisfiable within
   `scope.touch`; a handoff whose oracle references a file outside the allowlist
   is an authoring defect. Add a lint rule to flag it (extends `nyxloom lint`).
3. **Scope-amendment escalation (the real fix):** when an agent discovers it
   genuinely needs a file outside scope, it emits a structured "needs file X
   because Y" request that the carver/operator cheaply approves (expanding the
   allowlist mid-flight) — a fast amendment, NOT a hard BLOCK + full re-carve.
   Folds into F005 (fail-closed correctness contract), bounded like D-R8's
   reviewer fixes.
4. **Both are per-task declared, not global:** the OS sandbox mode AND the scope
   breadth are handoff fields — a mechanical config edit gets a narrow allowlist +
   read-mostly; a cross-cutting refactor gets a broad allowlist + full access.
   Capability-match the PERMISSIONS to the task, same as the model.

## D-R17 — Transient-failure backoff-resume (resume, don't restart) — decided 2026-07-23

Operator question: on a transient provider failure (the 2026-07-23 free-model
trial hit both a 502 `ResourceExhausted / Worker local total request limit
reached` and an `Upstream idle timeout`), can a CLI resume after a while rather
than lose the run?

**Yes — the mechanism already exists.** Every route in `routes.toml` carries a
`resume` template + `session_capture`/`session_discover` for the session handle,
and the daemon already resumes sessions for three things (implementer
interrupt-resume `daemon.py:2054`; persistent carver D-R10; daemon-driven
`/compact` D-R11). What is missing is the *classification + scheduling* that
applies resume to transient failures:

1. **Classify by OUTPUT, not exit code.** Free routes exit 0 even on failure (the
   D-R9 gotcha, observed again in this trial). A failure whose output matches a
   transient-provider signature — HTTP 502/429, `ResourceExhausted`, `rate limit`,
   `idle timeout`, `Worker local total request limit` — is **provider-throttled**,
   NOT a contract BLOCKED and NOT a code failure.
2. **Back off, then RESUME the same session** (not a fresh dispatch): schedule a
   delayed `build_resume(session_handle)` with exponential backoff (e.g. 1/5/15
   min). Resume continues where the agent stopped; a fresh restart re-does the
   work and re-burns tokens.
3. **Bounded:** after N backoff-resumes on the same route still failing, escalate
   to the D-R4 availability layer (disable that provider) and re-route to the next
   route in the tier (a fresh dispatch on a healthy route).

**Why it matters most for free models:** the trial showed free endpoints fail on
CAPACITY, not capability — backoff-resume is exactly what makes a free/band-1
route viable for longer work despite throttling, instead of one 502 wasting the
whole run. Folds into F009 + the D-R4 availability layer; the detection half
shares D-R9's output-signature parsing, the resume half reuses D-R10/D-R11's
session machinery. Backlog B24.

## What folds where

- **North-star** (identity-level): capability-matched review (D-R2), the
  self-contained sandboxed runtime (D-R7), cost-aware/policy-driven routing
  (D-R1/R5/R6), and the human control/escalation surface. See north-star draft.
- **This design doc**: the full D-R1..R16 contract.
- **Spine features:** D-R1/R2/R3/R5/R6 → **F009** (capability-matched, cost-aware
  routing). D-R12/R13 → **F014** (model capability catalog — new 2026-07-23).
  D-R14 → **F012** (human control surface, routing panel). D-R15 → **F015**
  (scheduled-jobs subsystem — new 2026-07-23). D-R16 → **F005** (scope-amendment
  escalation, B21) + **F010** (per-task sandbox/scope fields, B23). D-R7 → F010.
  D-R8 → B4. D-R9 → B1.
- **Pulled forward (2026-07-19, operator directive):** D-R10 + D-R11 are
  P44 (role-scoped `build_dispatch`, closes #34/B7) + P45 (`READY_TO_CARVE`
  dead-end fix + review-initiated micro-carve routing, closes #25/#26/B8).
- **Build epic:** the **parallelizable bundle** (D-R1 tier rename; D-R12 benchmark
  registry; D-R13 capability catalog; D-R14 routing UI; D-R15 scheduled jobs) is
  file-disjoint from F5 and can be carved now. The **F5-coupled** piece (D-R3
  carver band-prediction) sequences with the gap-engine. Availability layer (D-R4),
  cost model (D-R5), per-project policy (D-R6), self-contained runtime (D-R7),
  reviewer-fix policy (D-R8), and `route doctor` (D-R9) are phased packages after
  the bundle lands.

## Sequencing

- **Now, parallelizable with F5:** D-R1 (tier rename — a small data migration +
  wiring the dead `role_default`), D-R12 (benchmark registry), D-R13 (capability
  catalog), D-R14 (routing UI), D-R15 (scheduled jobs). These touch new modules
  (`capability_map.py`, `benchmark_sources.py`), the dashboard, and `routes.toml`
  — none of the carve-path files F5 rewrites.
- **With F5:** D-R3 (carver complexity→tier prediction).
- **After the bundle:** D-R4/R5/R6 (availability + cost posture + policy), then
  D-R7 (self-contained runtime), D-R8 (reviewer fixes), D-R9 (`route doctor`).
- D-R10/D-R11 already built (P44/P45).
