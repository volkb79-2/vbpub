# nyxloom roadmap

Status: **design / pilot**. No milestone is complete because it appears here;
evidence links land when implementation begins. Ordering principle: every
milestone must ship a standalone benefit to the *current* md workflow, and the
LLM controller is retired as early as safety allows — not at the end.

Each milestone is sized to be built as bounded handoff packages by ladder-tier
implementers under the existing protocol (dogfood rule, SPEC §14.6).

## M0 — frontmatter + lint (days, not weeks)

- Frontmatter schema; converter for the existing v2 §7 blockquote headers.
- `nyxloom lint` with L1–L12; golden corpus from the P69/P78/P84/P85
  incidents; pre-commit hook for carve commits.
- `nyxloom doctor`: read-only drift audit over in-place files (draft 1's
  importer findings list, minus the import).

Exit: current groop + dstdns open handoffs lint clean (or produce accepted
findings); a deliberately broken carve is rejected.
**Benefit realized: carve-quality regression protection for the manual
workflow, before any automation exists.**

## M1 — status + dashboard (shadow, read-only)

- Statefiles bootstrapped from frontmatter + git + existing LOG/REPORT files
  (in place — nothing imported, nothing moved).
- `nyxloom status` (CLI table) and `render` (static www/: index, history,
  dag, timeline, task drill-down; cost pane appears in M2).
- Runs while the Sonnet controller still drives — its slot table and the
  dashboard must agree (draft 1's shadow idea, one week, cheap).

Exit: no unexplained disagreement with operator truth across a representative
wave. **Benefit: the always-on visibility surface exists; zero tokens to use.**

## M2 — dispatch, wrapper, tick: the controller retires

- Attempt wrapper (detached launch, log tee, typed receipt, usage extraction
  verified per CLI, flock leases).
- `routes.toml` + preflight probes; `tick` scan/dispatch/collect/stall/notify
  for `Stack/mutex: none` packages; pause flag; budget enforcement.
- ntfy notification adapter; `NEEDS_OPERATOR` path.
- Cost ledger + prices.toml; dashboard cost pane + quality (per-tier) pane.

Exit: one full wave (≥4 packages) dispatched, monitored, collected, and
review-packeted by the tick with the LLM controller **absent**; crash drills
per SPEC §14.4 pass. **Benefit: the standing controller session, its dispatch
docs, and its heartbeat ticks go to zero tokens; stall handling stops
depending on a model reading logs.**

## M3 — review orchestration + decision loop

- Wave assembly (≤3 diffs), packet generation, frontier review leg launched by
  the tick; findings/receipt typed; `flagged_by_pass_1: acted|mentioned|no`.
- Carver outcome ingestion (the 7 outcomes); progress ratchet; SPEC_ATTENTION
  triggers 1–3, 7.
- `decide` / `discuss` commands; DECISION_OPENED/RESOLVED wired to
  notifications and dependency holds.

Exit: a wave flows carve → implement → review → merge-ready with the tick
doing all plumbing; merge itself remains a frontier/human act.
**Benefit: packet assembly and status reporting tokens go to zero; decisions
get push + one-command resolution instead of "check the file occasionally."**

## M4 — multi-project + dstdns resources

- Project registry (groop, pwmcp, dstdns on one tick); host fairness caps.
- dstdns adapter: test-runner gate declaration, stack mutex as flock (retire
  `.STACK_LOCK`/`.CARVE_LOCK`), infra-glob auto-exclusive rule (lint L9),
  pwmcp resource declaration.
- Remaining SPEC_ATTENTION triggers (4–6, 8); digest mode.

Exit: a `Stack: exclusive` dstdns package completes with kernel-enforced
exclusivity and canonical-gate evidence; cross-project WIP caps hold under
load. **Benefit: the whole landscape runs on one deterministic control loop.**

## M5 — graduation options (each a separate user decision)

- Resident daemon + SSE live logs — only against ARCHITECTURE §9 criteria.
- Guarded automatic merge — draft 1 Phase-7 preconditions inherited verbatim
  (several clean waves, provenance, crash recovery, protected branches,
  security review). Manual merge may remain permanent policy.
- Remote dashboard exposure behind authenticated TLS proxy.

## Deferred (inherited from draft 1, unchanged)

Multi-host scheduling, cloud dashboard, model-based status summaries, generic
arbitrary command execution, autonomous product prioritization, automated
spec/roadmap edits.
