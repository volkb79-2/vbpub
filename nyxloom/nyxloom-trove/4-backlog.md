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
- id: B-self-review-leg
  title: wire the independent SELF_REVIEW dispatched leg (beyond the prompt-level
    implementer self-review)
  type: feature
  component: dispatch
  context_estimate: medium
  folds_into: F005
---

# nyxloom — backlog

Un-scheduled items and sub-packages, each folding into a product-definition
feature (or standalone for ops). `context_estimate` is the carver's read-
context estimate (a scheduler input); `component` is the wave-grouping proxy.

