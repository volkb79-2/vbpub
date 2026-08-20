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
| haiku orientation: tool calls | 29 (harness) |
| haiku orientation: total tokens (task accounting) | 78,848 |
| brief size (tokens est) | ~3.2k (target was ≤15k) |
| delta: files read beyond floor | 6 (auth model, pyproject package-data, __init__, dir listing, bake.hcl, full decisions.md) |
| delta: floor files judged useless | 0 (12/12 used) |
| controller lint: omissions found | 4 (see outcome) |
| controller carve: facts fetched outside brief | ~6 spot-check reads (lint itself; carve then needed 0 extra) |
| premium residual orientation: tool calls | _(pending — carve reviewer + implementer)_ |

**Outcome / prompt refinements.**
Brief was USABLE for the carve after controller lint. Lint findings (each → a template refinement):
1. **Line numbers systematically wrong** (test-runner 88–90 → actual 148–150; render seams 62–102/97 → 78–105/155; meta.py 11–27 → 108–127). Behavior-level claims all verified TRUE. → R1: seams cited as file + symbol + grep-able anchor string; a line number may appear ONLY if pasted from `grep -n` output.
2. **One scope-level error**: brief prescribed adding `Optional` unwrap logic to `_model_item_type`; reality = that logic already merged in P108 (`_optional_model_type`), the remaining defect is a docstring OVERCLAIM. The prompt's "if reality contradicts the task statement, SAY SO" duty was not honored — the agent rationalized the contradiction. → R3: for EVERY task item, require an explicit `already-done?` check against merged code, reported per-item.
3. **Gate commands paraphrased wrong** (generic `pytest tests/unit -m unit -q`; canonical mock argv + flock + schema gate omitted). → R4: gate commands must be copied verbatim from the project guide, never paraphrased.
4. **Self-estimated context off by 10×** (self: ~8k; harness: 78,848). → R5: drop self-estimates from delta.md; harness accounting only.
5. Delta's own top finding was real: the floor's docker-bake.hcl pointer misdirected (pattern is inline per-Dockerfile). → R2: prompt author verifies floor pointers, or phrases them as search instructions with fallback.
**Economics**: haiku 78.8k @ haiku price + ~10k controller lint reads vs. controller self-orienting (~80k @ premium). Brief (3.2k) now seeds carve + all premium dispatches.
