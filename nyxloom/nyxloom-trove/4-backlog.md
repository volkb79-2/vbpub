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
---

# nyxloom — backlog

Un-scheduled items and sub-packages, each folding into a product-definition
feature (or standalone for ops). `context_estimate` is the carver's read-
context estimate (a scheduler input); `component` is the wave-grouping proxy.
