# Wave 1 dispatch log — 2026-07-15

Carve commit: `59b495d`. All 11 packages dispatched in parallel (user waived
the 5-agent cap for this wave). Implementer tiers: haiku (bounded packages),
sonnet (P09 daemon, P11 adversarial tests). Controller = the frontier session
itself this wave (carver/reviewer/merger); agents are harness-tracked with
resume handles recorded session-side.

| pkg | scope | tier | state |
| --- | --- | --- | --- |
| P01 | frontmatter + lint + golden corpus | haiku | DISPATCHED |
| P02 | reconcile planner | haiku | DISPATCHED |
| P03 | route adapters | haiku | DISPATCHED |
| P04 | attempt wrapper | haiku | DISPATCHED |
| P05 | dashboard renderer | haiku | DISPATCHED |
| P06 | notifications | haiku | DISPATCHED |
| P07 | decisions inbox | haiku | DISPATCHED |
| P08 | doctor | haiku | DISPATCHED |
| P09 | handoffd daemon + HTTP/SSE | sonnet | DISPATCHED |
| P10 | operator CLI | haiku | DISPATCHED |
| P11 | property tests + crash drills | sonnet | DISPATCHED |

Review gate: frontier review of every diff against its handoff oracles +
full-suite run from the repo, then batch commit. BLOCKED receipts route to
re-carve or tier escalation per v2 §7.
