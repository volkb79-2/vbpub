# Context-lifecycle experiments — measurement log

Sibling of `design-context-lifecycle.md` (the design; V1–V9 + V2b/V2c define the
questions). This file is the **append-only run log**: one `E-NNN` section per
run, pre-registered (protocol written BEFORE the run), measurements filled in
after. Design stays stable; data accumulates here.

Conventions: token numbers come from the harness task accounting where
available, else the agent's self-report (marked `self`). "Delta" = the diff
between the prescribed must-read floor and what the agent actually consumed.

---

## E-001 · 2026-08-20 · tiered orientation, dstdns `ddcli-deploy-wiring` (V2c first data point)

**Question.** Can a cheap model (haiku) author an orientation brief good enough
that (a) the controller carves from it without re-reading the floor itself, and
(b) a premium carve-reviewer/implementer starts from the brief with near-zero
residual self-orientation?

**Setup.** dstdns main `4d3be28e`, wave complete. Upcoming package:
ddcli-deploy-wiring (D-114 B2 disposition; artifacts pre-named by the reviewer —
verification-shaped, favorable first case for a cheap model).

**Protocol (pre-registered).**
1. Controller authors task-aware orientation prompt: must-read floor +
   explicit freedom to read more + upcoming-task statement + delta-report duty.
   Prompt file: dstdns `nyxloom-trove/orientation/ddcli-deploy-wiring/prompt.md`.
2. Run on **haiku**. Deliverables: `brief.md` (target ≤15k tokens for a single
   package; wave-level target stays 20–30k) + `delta.md` (floor vs actually-read,
   per-file verdict used/useless/missing, tool-call count, self-estimated context).
3. Controller lints the brief against the floor checklist (does it carry every
   fact the carve needs?) — record omissions here; each omission is a prompt
   refinement, fed back into the prompt template.
4. Controller carves FROM THE BRIEF, noting every fact it had to fetch itself
   (= brief miss, the primary quality metric).
5. Premium agents (carve reviewer, later implementer) receive the brief path in
   their dispatch; monitor their residual orientation tool calls (count + what
   they re-read anyway = second quality metric, feeds V2c).

**Measurements (fill after run).**
| metric | value |
|---|---|
| haiku orientation: tool calls | |
| haiku orientation: total tokens (task accounting) | |
| brief size (tokens est) | |
| delta: files read beyond floor | |
| delta: floor files judged useless | |
| controller lint: omissions found | |
| controller carve: facts fetched outside brief | |
| premium residual orientation: tool calls | |

**Outcome / prompt refinements.** _(pending)_
