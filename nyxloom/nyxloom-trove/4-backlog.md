---
kind: backlog
schema_version: 1
items:
- id: B1
  title: route doctor verb (validate routes.toml + live-test each route)
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B2
  title: 'availability layer: disable a CLI/provider/model without removing config'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
- id: B3
  title: per-project route policy (no-china / no-openrouter / no-model-X)
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B4
  title: reviewer on-the-fly fixes (configurable, serial-favored)
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B5
  title: component-in-slug ID scheme + STANDARD update
  type: feature
  component: spine
  context_estimate: small
  folds_into: F001
- id: B6
  title: implementer self-review text (gated to IMPLEMENTER role)
  type: feature
  component: dispatch
  context_estimate: small
  folds_into: F005
- id: B8
  title: smart reject-triage — needs-human branch (tech-fixable half shipped via
    P45's exhausted-budget->READY_TO_CARVE re-carve route; the needs-human->D-NNN
    escalation branch is still unbuilt, reconcile.py item 10 has no such path)
  type: feature
  component: review
  context_estimate: medium
  folds_into: F005
- id: B9
  title: intake-over-ntfy chatbot (human-initiated new direction)
  type: feature
  component: control
  context_estimate: large
  folds_into: F012
- id: B10
  title: session-limit monitoring + per-job token estimation
  type: feature
  component: routing
  context_estimate: large
  folds_into: F009
- id: B11
  title: sweep stale daemon worktrees/branches (merge-status-checked)
  type: bugfix
  component: ops
  context_estimate: small
- id: B13
  title: runaway watchdog conflates a persistent-but-acknowledged condition with
    actively-worsening thrash -- review_rejections_by_area>=2 stays true for the
    FULL 7-day HISTORY_REJECTION_WINDOW_SECONDS regardless of an operator having
    already resumed once, so the SAME reconcile-thrash streak re-trips an
    auto-pause every ~13 reconcile passes (minutes, once unblocked) until the
    triggering rejections finally age out; needs the thrash-streak to reset (or
    not count) once an operator has resumed for this specific condition, not
    just dedupe the notification
  type: bugfix
  component: watchdog
  context_estimate: medium
  folds_into: F006
- id: B12
  title: carve-ahead drift/staleness guard -- input_revision is stamped by the
    carver but never re-validated against current main before an implementer
    attempt starts; raising carve_ahead_target increases exposure with no
    safety net (a CARVED task's premises can go stale while it waits)
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F008
- id: B-self-review-leg
  title: wire the independent SELF_REVIEW dispatched leg (beyond the prompt-level
    implementer self-review)
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F005
- id: B14
  title: 'onboarding must be interview-driven + content-preserving at ANY project
    maturity: never one-shot derive-from-code a canonical spine. Required design:
    an extensive user-in-the-loop interview PLUS a migration that absorbs existing
    curated docs (roadmap/backlog/product-definition) into the spine schema and
    retires the source docs afterward. Supersedes the F4b --questionnaire code-regen
    as the default (operator directive 2026-07-23; proven need by the dstdns/topos
    content-preserving migrations, which the code-regen path would have thinned).'
  type: feature
  component: onboarding
  context_estimate: large
  folds_into: F002
- id: B15
  title: 'free-models refresh follow-ups: (a) validate Tier-2 provider route
    addressing (groq/<model>, cerebras/<model>, ...) with a live probe before real
    traffic (folds into B1 route doctor); (b) honor an operator exclude-list so a
    refresh does not re-include manually vetted-out free models.'
  type: feature
  component: routing
  context_estimate: small
  folds_into: F009
- id: B16
  title: 'tier taxonomy rename to verb-band ({implement,review,carve}-{1,2,3}): a data
    migration, not a schema change. Rename the [tiers.*] keys in routes.toml, rewrite the
    tier: values in existing handoffs, replace the 3 hardcoded "frontier-review" string
    literals (reconcile.py:875, daemon.py:2428, daemon.py:3707) with a per-role tier
    lookup, and wire the currently-dead RouteDef.role_default (config.py:464) as its
    backing. Parallelizable with F5 (disjoint from the carve path).'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
- id: B17
  title: 'benchmark_sources.py: pluggable BenchmarkSource registry mirroring free_models.py
    @register_kind/FreeModelSource. Plugins for Artificial Analysis (capability + price in
    one schema), plus LMArena / Aider-polyglot / LiveBench / SWE-bench as swappable
    sources; blend/prefer configurable. All HTTP mocked in tests.'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F014
- id: B18
  title: 'capability_map.py: extend DiscoveredModel to a CapabilityRecord (per-axis coding/
    agentic/reasoning scores + price + band-per-axis + may_review/may_carve). Operator-set
    per-axis band thresholds; complexity band auto-assigned, role-eligibility operator-gated
    unless capability_map.role_gating=auto; context/flags hard-filter. Managed-block writer
    (frozen config.py untouched). This is the capability half onto free_models.py discovery.'
  type: feature
  component: routing
  context_estimate: large
  folds_into: F014
- id: B19
  title: 'routing/capability dashboard panel (read-only): catalog table (model x per-axis
    scores x price x privacy x availability x band) plus per-tier resolution (winner,
    runners-up, which filters fired). Renders from the files the resolver reads; no second
    aggregation engine.'
  type: feature
  component: control
  context_estimate: medium
  folds_into: F012
- id: B20
  title: 'scheduled-jobs subsystem: daemon-owned cron with capability-catalog refresh as
    first consumer. Config-driven jobs are read-only in the UI (config is source of truth);
    user-driven jobs added via UI are editable there; neither origin overwrites the other.
    Every job stays invocable ad-hoc. Generalizes B10s session-limit monitoring cadence.'
  type: feature
  component: control
  context_estimate: medium
  folds_into: F015
- id: B21
  title: 'scope-amendment escalation: when an implementer genuinely needs a file outside its
    scope.touch allowlist, it emits a structured "needs file X because Y" request the carver/
    operator cheaply approves (mid-flight allowlist expansion), instead of a hard BLOCK +
    full re-carve. Bounded like D-R8 reviewer fixes; re-gate after. Fixes the P26/P31
    forbidden-needed-file failure mode (D-R16 Axis B).'
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F005
- id: B22
  title: 'lint rule: a handoff whose oracle references a file outside its scope.touch
    allowlist is an authoring defect (every oracle must be satisfiable within scope).
    Extends nyxloom lint carve-quality rules; catches the D-R16 Axis-B failure at carve
    time rather than mid-implementation.'
  type: feature
  component: lint
  context_estimate: small
  folds_into: F005
- id: B23
  title: 'per-task permission fields: make the OS sandbox mode (read-only / workspace-write
    / full-access) AND the scope breadth declared handoff fields, capability-matched to the
    task, rather than a global route/CLI default. Narrow + read-mostly for mechanical edits,
    broad + full-access for cross-cutting refactors (D-R16 Axis A/B unification).'
  type: feature
  component: runtime
  context_estimate: medium
  folds_into: F010
- id: B24
  title: 'transient-failure backoff-resume: classify a provider-throttled attempt (502/429/
    ResourceExhausted/rate-limit/idle-timeout — matched by OUTPUT, not exit code, since free
    routes exit 0 on failure) as retryable-via-resume, NOT BLOCKED or fresh-restart. Schedule
    a delayed build_resume(session_handle) with exponential backoff; after N failed resumes
    escalate to the D-R4 availability layer + re-route to the next route in the tier. Reuses
    the resume templates + D-R10/D-R11 session machinery already in place. Makes
    capacity-throttled free models viable for longer work (D-R17).'
  type: feature
  component: routing
  context_estimate: medium
  folds_into: F009
---

# nyxloom — backlog

Un-scheduled items and sub-packages, each folding into a product-definition
feature (or standalone for ops). `context_estimate` is the carver's read-
context estimate (a scheduler input); `component` is the wave-grouping proxy.
