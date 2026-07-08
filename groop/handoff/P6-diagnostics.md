# P6 — Diagnostics engine (pressure score + findings rules)

**Cut:** v1. **Depends:** P1 merged (model), P4 useful (drift rule), UI panel
lands via P5/P7. Branch: `feat/groop-p6-diagnostics`.
Follow `groop/README.md` workflow protocol.

## Goal

Interpretation over columns: a sortable per-entity `pressure` score and the
fixed 8-rule findings engine that renders "why this row is red". Deterministic,
registry-backed, thresholds from config — no ML, no learned state.

## Spec references

§3.4a (the authoritative section — score inputs and the eight rules verbatim),
§3.2 (registry: `pressure` and findings are derived metrics), §7
([thresholds]), §6.5 (drift severity policy).

## Scope — in

1. `diag/score.py`: weighted score from §3.4a inputs (memory PSI full/some,
   IO PSI full/some, CPU PSI some, rf_d/s, rf_f/s, memory.events high delta,
   OOM kills, io.max cap saturation, net drops/retransmits when attributable).
   Weights + normalization in config with shipped defaults; output 0–100 +
   per-input contribution breakdown (drill-down shows it).
2. `diag/rules.py`: the eight §3.4a rules as data-driven rule objects
   (predicate over Frame + entity, severity, message template, remedy hint).
   Rule 6 (drift) consumes P4's governance block. Each `Finding` carries:
   rule id, severity (info/warn/red), message, remedy, source metrics with
   their confidence.
3. Frame integration: `EntityFrame.findings` + `pressure` MetricValue filled
   by a post-collect pass (`diag.annotate(frame, config)`) — pure function,
   callable from collector loop AND from replay (recorded frames without
   findings get them recomputed on replay; frames with findings keep them).
4. Game-agnostic copy: message templates use "protected service" /
   "latency-critical workload" wording (§3.4a note).
5. Tests: one fixture frame per rule that fires it, plus a healthy frame
   firing none; score monotonicity tests (more PSI → higher score); breakdown
   sums to score.

## Scope — out

UI rendering (P5 drill-down panel + banner top-pressure consume your data),
alert transport/paging, historical trend rules (v2).

## Acceptance

- `diag.annotate` on the gstammtisch golden frame: game entity gets
  pressure≈0 and zero findings; the synthetic pressure fixture fires the
  expected rules with correct severities.
- `pressure` + finding-related metrics registered in REGISTRY with glossary.
- pytest green; report per README protocol.
