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
| premium carve-reviewer (opus): tokens / calls | 168,169 / 47 |
| premium reviewer reads saved by brief | **0** (role-structural — see below) |
| premium implementer (sonnet, 354k tok / 266 calls) residual orientation | **24 files** before first edit; brief spared "effectively nothing" (cautionary value only) |

**Outcome / prompt refinements.**
Brief was USABLE for the carve after controller lint. Lint findings (each → a template refinement):
1. **Line numbers systematically wrong** (test-runner 88–90 → actual 148–150; render seams 62–102/97 → 78–105/155; meta.py 11–27 → 108–127). Behavior-level claims all verified TRUE. → R1: seams cited as file + symbol + grep-able anchor string; a line number may appear ONLY if pasted from `grep -n` output.
2. **One scope-level error**: brief prescribed adding `Optional` unwrap logic to `_model_item_type`; reality = that logic already merged in P108 (`_optional_model_type`), the remaining defect is a docstring OVERCLAIM. The prompt's "if reality contradicts the task statement, SAY SO" duty was not honored — the agent rationalized the contradiction. → R3: for EVERY task item, require an explicit `already-done?` check against merged code, reported per-item.
3. **Gate commands paraphrased wrong** (generic `pytest tests/unit -m unit -q`; canonical mock argv + flock + schema gate omitted). → R4: gate commands must be copied verbatim from the project guide, never paraphrased.
4. **Self-estimated context off by 10×** (self: ~8k; harness: 78,848). → R5: drop self-estimates from delta.md; harness accounting only.
5. Delta's own top finding was real: the floor's docker-bake.hcl pointer misdirected (pattern is inline per-Dockerfile). → R2: prompt author verifies floor pointers, or phrases them as search instructions with fallback.
**Economics**: haiku 78.8k @ haiku price + ~10k controller lint reads vs. controller self-orienting (~80k @ premium). Brief (3.2k) now seeds carve + all premium dispatches.


**E-001 addendum — premium-side results (carve reviewer, 2026-08-20).**
The fresh adversarial carve reviewer (opus, seeded with the brief) reported the brief saved
ZERO reads: every line number was wrong in every checked case, and independent re-verification
is the role's essence — it must open every cited file regardless. Its value there was "a
to-read list", and that list omitted every artifact behind the review's 5 blocking findings.
- **R6 (role-targeting):** orientation-brief reuse value concentrates in CARVER + IMPLEMENTER;
  do not count reviewer reads as brief failure, and do not expect reviewer savings — seed
  reviewers with the brief only as a cheap map, price their full re-read in.
- **Layered-defense datum:** three layers caught DISJOINT defect sets — haiku brief (seam map,
  found nothing wrong), controller lint (phantom work item, gate-command drift, missed compose
  template), adversarial carve review (dead-code renderer with an arity TypeError, tracked
  legacy artifact whose deletion P108 had deferred to this very package, out-of-scope env
  readers, inert-Dockerfile-edit gate trap). No single layer subsumes another; the brief's
  errors did NOT propagate into the dispatched carve because the downstream layers re-verify.
- **B3 lesson (generalized rule, dstdns D-119 #2):** a package whose oracles need a
  gate-runner dependency gets it PRE-LANDED on main by the controller — a branch-side
  Dockerfile edit is inert when the gate builds/reuses images from main.


**E-001 CONCLUSION (2026-08-20, all three roles measured).**
With a brief of THIS quality (3.2k tokens, ~5× under target density; wrong line numbers
throughout; two wrong prescriptions), premium context savings were **~zero for every role**:
reviewer 0 reads saved (role-structural, R6), implementer 0 of 24 residual reads saved (brief
"too shallow and partly incorrect to write code from"), carver saved only the initial
discovery sweep (the controller still spot-checked ~6 claims and found the misses that
mattered). What the experiment DID validate: (a) the layered pipeline fully contained the
cheap model's errors — nothing wrong shipped; (b) the haiku run itself was cheap (78.8k @
haiku price) and its delta.md correctly identified a real prompt defect; (c) the implementer
self-checkpointed cleanly at ~354k. **The open question moves, not closes**: E-002 must test
whether a brief built under R1–R5 (grep-anchored seams, per-item already-done checks, verbatim
gate commands, density at target, floor pointers verified) changes the savings — or whether
brief value is intrinsically capped because implementation-grade work always re-reads its
seams (in which case the tiered-orientation win reduces to: cheap discovery sweep + a
to-read list + open-questions surfacing, which is still positive ROI at haiku prices but NOT
the "premium never pays orientation" claim in §3 of the design doc — that claim should be
softened pending E-002).

---

## E-001 addendum 3 · 2026-08-20 · session-JSONL anatomy of the P109 roles (feeds the E-002 redesign)

Mechanical analysis of the three real role sessions (extractor: parse tool_use/tool_result
blocks; work-start = first content-writing call, excluding worktree setup):

| metric | implementer | code reviewer | carve reviewer |
|---|---|---|---|
| session JSONL | 2,267,717 B / 749 lines | 769,510 B / 191 lines | — |
| orientation slice (to first write) | 349,972 B (15%), **23 tool calls** | interleaved (diff-driven, no clean boundary) | whole session IS orientation-shaped |
| orientation tool-result content | 131,899 B | — | — |
| distinct files read (whole session) | 50 (20 re-read) | 12 (undercount — reads via `git diff`, not cats) | 26 |
| on-disk union of orientation reads | 135,392 B (~34k tok) | — | — |

**Overlap:** impl∩carve-rev = **24** (near-total — both consume carve-time facts);
impl∩code-rev = 8; three-role union = **55 files**.

**The two real findings:**
1. **Content was already near-lossless** — 131.9 KB of tool results vs a 135.4 KB on-disk
   union: the implementer barely read anything it didn't need. The E-001 brief failed
   precisely because it tried to compress content that does not compress (implementation
   needs the actual bytes). Operator call (2026-08-20): STOP condensing; the orientation
   payload IS the read content.
2. **The waste is roundtrips, not bytes** — 23 orientation calls ≈ one per file. A cleaned
   equivalent (prompt + ONE batched read turn carrying the same 135 KB) is ~140 KB of
   history: ~2.5× smaller than the raw orientation slice in bytes, ~10% smaller in content
   tokens (framing overhead), but **23→1 API turns** — the latency + per-turn prefix
   re-read is the real cost, and a single-turn prefix is maximally cache-stable.

## E-002 (REDEFINED after E-001) · orientation pack — model-free build, single-call load

Supersedes the R1–R5 "better brief" plan: no brief at all.
1. **Read-list derivation (no model):** handoff "Context to read first" + the carve-time
   union (carve reviewer read-set ≈ 24/26 shared with impl) + prior-session extractor
   output for similar tasks. The list is data, produced by scripts.
2. **Pack build (no model):** concatenate the files verbatim with `=== <path> @<rev> ===`
   headers into `orientation-pack.md`. Zero tokens spent. Regenerate per input_revision —
   never stale, never lossy.
3. **Worker dispatch:** "FIRST action: read <pack> in ONE call; then the handoff; then
   work." Measure: residual orientation reads beyond the pack (target ≤5), orientation
   roundtrips (target ≤3 vs baseline 23), total session tokens vs the 354k E-001 baseline.
4. Cheap-model orientation survives ONLY for genuine list-DISCOVERY on unexplored
   components — and its deliverable is then the LIST (verified paths), never prose.

## E-003 · same-model frozen orientation session (the validated B1 fork mechanism + batching)

For CLI-session lanes: orientation session batch-reads the pack/list in ≤3 calls
(`claude -p`, same model+effort+toolset as workers, --exclude-dynamic-system-prompt-sections),
terminates with a bare ack; every role forks it (`--resume <sid> --fork-session`) = pure
cache reuse over a single-turn, maximally-stable prefix. This composes E-002's batching
with the already-validated L24 mechanism. Per-model minting still applies.

## E-004 (conditional) · mechanical JSONL rewrite → synthetic clean orientation history

Rewriting a messy exploratory session's JSONL into a synthetic minimal history
(prompt + batched-read turns, valid uuid/parentUuid chain) is UNVALIDATED and carries a
known precedent: direct forging of compact-boundary/summary lines FAILED (resume
leaf-selection skipped synthetic lines, 2026-08-20). A full-chain rewrite is a different
shape and may work — but E-002/E-003 make it mostly unnecessary by PREVENTING the mess
(sessions born clean). Only worth validating if we need to salvage large exploratory
sessions. Park until a concrete need.

## E-005 · orientation delta-extension (operator-proposed 2026-08-20) — reuse across base movement

A frozen orientation (pack or session) at rev R stays valid as main moves to R′ by APPENDING
a script-built delta instead of re-orienting:
- `git diff --name-only R..R′` ∩ read-list → those files' diffs VERBATIM (changed content
  directly known — no in-model git archaeology);
- changed files OFF the read-list → names only (awareness);
- controller re-orients fresh past mechanical thresholds (>20 changed files since R, or
  >10 commits per orientation — tune from data).
Economics: for session lanes the frozen prefix stays byte-identical → cache-read pricing
(~10%) on every warm fork, delta = only new tokens. COLD forks re-create the prefix cache
once either way — the unconditional wins are prefix stability across N dispatches and zero
model tokens spent deriving what changed. Validation: run a wave where carve→carve-review→
impl→code-review all consume ONE pack@R + growing delta; measure delta sizes, threshold
trips, and any staleness-caused defect (target: zero).

## Sizing rule — breadth ∝ expected forks ÷ expected turns-per-fork (operator, 2026-08-20)

Pack breadth is an economics dial, not a quality one: broad content is paid once per fork
at load, then RE-BILLED (at cache rates) every turn that fork lives.
- Many SHORT, diverging tasks off one base (sweeps, per-file fixes, review fan-out) →
  broader shared pack: amortizes across forks, avoids per-fork custom builds.
- Few LONG-RUNNING spawns (a full implementer) → narrow, task-cut pack: dead weight
  compounds over hundreds of turns.
Record per experiment: pack tokens, forks served, mean turns/fork → cost/fork; refine the
dial from measurements.


**E-002 first live run (2026-08-20, dstdns P110 carve — controller role).**
Read-list: haiku discovery (60.8k tok, 41 calls — IGNORED the ≤12-call batching instruction:
prompt-compliance finding; list itself was good, 21 paths + honest §E2 unresolved). Naive
full-file pack would be 1.06MB ≈ 265k tok → curation applied (full small files + grep/sed
slices of 4 giants) → pack 107.7KB = **42k real tokens** (ⅷ NB: bytes÷4 UNDERcounts code —
measured 2.56 B/tok). Load: **2 Read calls** (harness Read caps at 25k tok/call — "single-call
load" has a ceiling; still 23→2 roundtrips vs baseline). Controller residual after pack: **1
batched call** (4 facts the pack's slices missed: AuthSection fields, R6 field existence, UI
page paths, consul_layer surface) → carve D-122 + lint-clean handoff authored with zero
further reads. Tooling TODO: pack.sh range support (path:start-end) so curated packs are
list-driven, not hand-built. E-005 delta first live use: carve reviewer receives pack@6e76813b
+ script-built delta (threshold logic already validated on the P109 window).


**E-002 addendum (P110 carve review round, 2026-08-20) — the consumer dimension.**
Reviewer (opus, 182.3k tok, 35 calls) seeded with pack+delta: pack saved **9 read-sets**, all
4 spot-verified pack claims accurate — the verbatim-pack thesis holds for reviewers (vs the
E-001 brief's 0). BUT all **10 blocking carve defects came from OUTSIDE the pack's read-list**:
React UI consumers, nav partial + 27 rendered pages, three dying test files, the real [queues]
table's reader-less status, loader-private scope machinery, four L0 files, GUIDE §2's
environment contract. **Structural rule (D-123 #11): a read-list must carry TWO dimensions —
what changes AND what consumes it.** Derivation gains a mandatory reverse-dependency sweep:
for every symbol/route/file being deleted or moved, grep callers/importers/renderers/tests
across the repo (script-able, model-free) and pack the hit sites. A pack without the consumer
dimension reliably produces a carve that deletes half a dual path. Also measured: reviewer
orientation ≈35 calls despite the pack — consumer-sweep content would have converted most of
its 32 Bash calls into pack reads.

### E-002 addendum 4 — implementer telemetry (P110, the pending metric)

Source: `dstdns nyxloom-trove/reports/dstdns-P110-REPORT.md` §"E-002 telemetry" (branch
`p110-config-plane`, opus implementer, 599,953 subagent tokens / 341 tool calls / 74 min).

- **Orientation calls before first edit: 12** (vs 23 for the pre-pack P10x implementer baseline —
  ~half). Pack itself cost 2 Read calls (25k-tok/call cap, again).
- **Pack accuracy: 100%** — "nothing it claimed was wrong"; verbatim content replaced **~9 file
  reads** for the implementer role too (route inventories, deleted route bodies, QueuesSection,
  two full test files incl. the 526-line `test_reconfig.py` whose coverage O7 had to inherit).
- **Gap 1 — consumer dimension, confirmed at scale a second time**: all 13 consumer files beyond
  the enumerated set were found by the implementer's OWN reverse-dependency grep, not the pack.
  (Same failure axis as the 10/10 carve blockers.) The reverse-dep sweep MUST become part of
  read-list derivation, not a per-agent rediscovery.
- **Gap 2 — NEW: slice-vs-edit mismatch.** Route-body slices suffice for files being DELETED or
  reviewed, but every file being EDITED needed a full read anyway — the dead wiring lives in the
  module head (docstring, imports, `set_*_dependencies`). Rule: **pack full files for the edit
  set; slices only for read-only/deletion context.**
- 10 beyond-pack reads itemized in the REPORT; largest cluster = the MOVE source + loader import
  graph (the pack had no loader content — a curation miss, not a model limit).

Verdict: pack value now measured positive for BOTH roles (reviewer ~9 reads, implementer ~9 reads
+ call count halved). The two gaps are curation rules, both mechanical/scriptable.

### E-002 addendum 5 — P111 carve round (pack under adversarial audit)

- **Pack accuracy again 100%** on the carve reviewer's spot-checks (byte-diff of slices, every
  line ref re-derived). Accuracy has now held across four consumers (carve reviewer x2,
  implementer, fix-verification).
- **The consumer-dimension gap survived a deliberate countermeasure**: the P111 carve RAN the
  reverse-dep sweep (26 files classified) and still missed 2 of 6 live test consumers + the
  second-order class (callers of `_auth_enabled()` that never name an env var). Reviewer's
  formulation, now ledger doctrine (dstdns D-128 #10): a sweep counts only when (a)
  all-tracked-file-types, (b) symbol-level not var-level, (c) its RESULT TABLE is written into
  the carve. "A grep executed but not tabulated is an assertion, not a measurement."
- **New pack-curation rule (gate-adjacent artifacts)**: the pack must carry the *gates the
  package can trip* — coverage-lane configs (assay), oracle-coupled test machinery
  (completeness-oracle match logic, intent-doc coverage lists) — not just the edit set +
  consumers. All three were audit findings (D-128 B3/B6/E1).
- **Slice titling matters**: a slice whose title claims content outside its range (D-129 R5)
  costs the implementer a wrong assumption; title = what the byte range actually holds.
- **Process (not pack) lesson worth porting to the design doc**: a handoff repair is unfinished
  until the machine-read frontmatter and the prose body agree — the D-129 blocker was a repair
  that landed 100% in the body while reviewers verify against the YAML (dstdns D-129).

### E-002 addendum 6 — P111 implementer telemetry

Source: branch REPORT §12 (opus, 479,721 subagent tokens / 210 calls / 46 min; P110 was 599,953/341/74min on a bigger package).

- **29 orientation calls before first edit** (vs P110's 12): 10 pack loads (1 cat overflow + 9 Reads — the 236KB pack exceeded single-cat), 8 handoff+decisions (2 "output too large" while heading-hunting), 11 own measurement (consumer sweep, import-graph, read-site map).
- **NEW GAP CLASS — "content right, bytes wrong":** the pack showed the sections.py/mains regions accurately, but `Edit` needs byte-exact CURRENT strings, so the implementer re-read every EDIT-target region anyway ("the pack proves what a file CONTAINS, not what editing it COSTS"). Rule: for edit-set files the pack saves comprehension reads but NOT the pre-edit read; only full-file pack entries at the exact input_revision can substitute, and only if the tool can trust line offsets. Slices of edit targets are comprehension-only value.
- Second confirmed gap: import-graph cost ("what importing sections.py COSTS") is invisible to content packs — 4 calls.
- Sweep-tabulation doctrine worked: the implementer's OWN tabulated sweep caught a 4th missed consumer (comment-only), absorbed by directory-scope rather than enumeration — the D-128 B1 defense-in-depth held.
- GUIDE.md deliberately unpacked (standing doc) — correct call, but O5 unrunnable without it; standing-doc reads are a fixed per-package orientation cost the pack cannot amortize.

## E-006 — context-growth curves, checkpoint simulation, reviewer read-set overlap (P110/P111, measured 2026-08-20)

**Tool.** `dstdns nyxloom-trove/orientation/jsonl-metrics.py` (subcommands `curve`,
`simulate`, `readset`, `overlap`) — reusable for later E-NNN runs; never reads a
transcript into a model's own context, only prints small aggregate tables.

**Method note.** The six source transcripts (subagent JSONLs under
`~/.claude/projects/-workspaces-dstdns/…/subagents/`) were **still being appended to
live** during measurement (`ae3da96851c90d08c` grew 194→245→250 lines across three
touches — an in-flight fix-verification resume). All numbers below are computed
against a **frozen snapshot taken 2026-08-20T18:57:58Z**, copied before any
measurement ran, so every table is internally consistent to that instant.

Cost model: `normalized_cost = input×1.0 + cache_read×0.1 + cache_creation×1.25 +
output×5.0`, in input-token-equivalent units.

### Task A1/A2 — per-agent summary

| transcript | role | calls | wall min | final context | cache-hit ratio | normalized cost |
|---|---|---:|---:|---:|---:|---:|
| `ade3ee8341f502776` | P110 implementer (opus) | 504 | 74.2 | 595,315 | 0.9952 | 21,110,074.5 |
| `ae091ba50b2174b26` | P110 code reviewer (+resume) | 173 | 27.0 | 242,727 | 0.9677 | 4,137,711.9 |
| `a7a50a660ded0597c` | P111 carve reviewer (+resume) | 162 | 20.9 | 232,667 | 0.9475 | 3,856,345.4 |
| `a5398dae84bd7dbbd` | P111 implementer (opus) | 329 | 46.1 | 477,084 | 0.9928 | 11,812,360.8 |
| `ae3da96851c90d08c` | P111 code reviewer | 155 | 26.6 | 252,662 | 0.9525 | 4,260,349.0 |
| `a6b1163277d943422` | P110 repair successor (sonnet) | 166 | 9.7 | 141,108 | 0.9793 | 2,188,199.7 |

### Task A3 — growth shape

`frac@N%` = context size at that fraction of calls, as a fraction of final context. Knee = the single call with the largest one-step context jump.

| transcript | shape | knee (call, % of run, jump) | frac@25% | frac@50% | frac@75% |
|---|---|---|---:|---:|---:|
| `ade3ee8341f502776` | roughly linear | call 7, 1.4%, +26,919 | 0.415 | 0.661 | 0.850 |
| `ae091ba50b2174b26` | roughly linear | call 3, 1.7%, +9,600 | 0.489 | 0.691 | 0.867 |
| `a7a50a660ded0597c` | roughly linear | call 141, 87.0%, +12,403 | 0.413 | 0.560 | 0.714 |
| `a5398dae84bd7dbbd` | front-loaded | call 322, 97.9%, +17,303 | 0.506 | 0.673 | 0.839 |
| `ae3da96851c90d08c` | roughly linear | call 3, 1.9%, +14,782 | 0.445 | 0.700 | 0.853 |
| `a6b1163277d943422` | front-loaded | call 9, 5.4%, +6,230 | 0.532 | 0.702 | 0.829 |

### Task A4 — checkpoint-restart simulation (brief = 25,000 cache_creation tokens)

Savings % = `(actual_full_cost − restart_cost) / actual_full_cost`, where restart_cost = actual cost through the checkpoint + a one-time 25k brief + the remaining calls re-priced with context rebuilt from 25k using the SAME per-call context deltas as the real run.

| transcript | savings@25% | savings@50% | savings@75% | break-even (first call, context, savings) |
|---|---:|---:|---:|---|
| `ade3ee8341f502776` | 40.50% | 44.25% | 28.82% | call 1, 40,712 tok, 5.35% |
| `ae091ba50b2174b26` | 43.62% | 42.89% | 31.19% | call 1, 42,433 tok, 25.26% |
| `a7a50a660ded0597c` | 44.24% | 42.40% | 33.75% | call 1, 41,711 tok, 33.22% |
| `a5398dae84bd7dbbd` | 46.07% | 41.85% | 26.04% | call 1, 41,336 tok, 6.88% |
| `ae3da96851c90d08c` | 44.80% | 46.41% | 35.59% | call 1, 42,687 tok, 32.43% |
| `a6b1163277d943422` | 30.24% | 28.72% | 17.55% | call 1, 47,615 tok, 25.74% |

### Task B — reviewer read-set vs orientation pack

Pack fileset = `read-list.txt` ∪ files resolved from `grep '^=== ' pack.md` slice headers. Extras (`read_set∖pack`) exclude the reviewer's own reads of `pack.md`/`read-list.txt`.

| reviewer | pack | pack files | read-set | ∩ | read_set∖pack | pack∖read_set (dead) | ∩/read_set | ∩/pack |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| P110 code reviewer (`ae091ba`) | config-plane-api | 17 | 26 | 4 | 22 | 13 | 15.4% | 23.5% |
| P111 carve reviewer (`a7a50a`) | auth-config-cutover | 28 | 34 | 19 | 15 | 9 | 55.9% | 67.9% |
| P111 code reviewer (`ae3da9`) | auth-config-cutover | 28 | 15 | 7 | 8 | 21 | 46.7% | 25.0% |

`read_set∖pack` by category (heuristic label on exact counts — gate/test artifact = filename matches `LOG.md`/`REPORT.md`/test-runner patterns; sweep/probe = everything else, split by the Bash subtool used):

| reviewer | gate/test artifact | sweep/probe (grep) | sweep/probe (cat/head/tail/sed) | sweep/probe (Read) |
|---|---:|---:|---:|---:|
| P110 code reviewer | 2 | 8 | 11 | 1 |
| P111 carve reviewer | 1 | 4 | 10 | 0 |
| P111 code reviewer | 2 | 2 | 3 | 1 |

Recurring `read_set∖pack` files across reviewers: prior-round `LOG.md`/`REPORT.md` (own gate evidence), `nyxloom-trove/GUIDE.md` + `decisions.md` (standing cross-reference), `docs/ARCHITECTURE-OVERVIEW.md` / `docs/product/SCREEN-API-MAP.md` / `docs/PRODUCT-CAPABILITIES-AND-ROADMAP.md` / `docs/DESIGN-AUTHORITY.md` / `docs/CORE-WORKFLOW-STATUS.md` (authority docs), and adjacency/test files never in the edit set (`landscape.py`, `config_endpoints.py`, `test_scope_authority.py`, `test_consul_deploy_control.py`, `scripts/schema-gate.sh`).

### Conclusions

- **Growth is continuous, not batch-then-flat, in all 6 transcripts.** 4/6 classify as "roughly linear" by the script's heuristic; the 2 "front-loaded" agents (P111 implementer, P110 repair) still only reach frac@25%≈0.51–0.53 — no agent front-loads more than ~half its final context in the first quarter, so there's no clean "read everything, then work" phase to target for a mid-run intervention; a checkpoint mechanism has to work against continuous growth, not a one-time spike.
- **Cache is doing its job.** Hit ratio ranges 0.9475–0.9952 across all 6 agents regardless of role or final context size (141k–595k) — caching absorbs the repeated-context cost as designed; the growth-curve cost pressure comes from cache_creation (new content) and output, not redundant cache_read pricing.
- **Mid-run checkpointing pays for itself on every measured agent, and early.** All 6 transcripts show restart savings turning positive by roughly call 1 (context ~40–48k) under the stated cost weights, and land at 25–46% savings by the 25% checkpoint. This is real given the weights (cache_read priced 12.5× cheaper than cache_creation, 50× cheaper than output) — but the near-call-1 break-even is a **model artifact**: it reflects that any constant reduction in the ongoing cache_read tail compounds over however many calls remain, not a claim that restarting after one real call is operationally sound (the 25k brief doesn't model catch-up reads, coordination cost, or interrupting mid-reasoning).
- **Savings peak in the 25–50% checkpoint band and fall off by 75%.** Every agent's 75%-checkpoint savings is its lowest of the three (17.6–35.6% vs 26–46% at 25/50%) — later checkpoints leave less "restart runway" to recoup the one-time brief, so **checkpoint earlier rather than later** is the actionable read, not "checkpoint as soon as possible."
- **The two long opus runs (504 and 329 calls) show the biggest absolute payoff**: `ade3ee8341f502776` (P110 implementer, 21.1M normalized cost) and `a5398dae84bd7dbbd` (P111 implementer, 11.8M) both save >40% of total run cost at their best checkpoint — roughly half of a long implementer run's cost is attributable to carrying an ever-growing cache_read tail rather than new work.
- **Reviewer pack overlap is bimodal, not uniform.** The P111 carve reviewer used the auth-config-cutover pack heavily (19/28 pack files touched, 55.9% of its own read-set came from the pack); the P110 code reviewer barely touched the config-plane-api pack (4/17 files, 15.4% of its read-set). The P111 code reviewer's small 15-file read-set was 46.7% pack-derived despite only covering 25% of the pack — NOT a fork artifact — every reviewer in this program is a fresh session by rule; the small read-set is the pack itself absorbing most orientation needs, so few direct reads remained (controller correction 2026-08-20: the original draft mis-attributed this to the B1 fork mechanism).
- **Dead weight is the dominant failure mode, not missing content.** 13/17 (76%) and 21/28 (75%) of pack files went untouched by the P110 and P111 code reviewers respectively — an implementer-scoped pack (full edit-set comprehension + consumer files) is mostly irrelevant to a reviewer who is verifying a diff, not building a mental model of the whole subsystem from scratch.
- **What a reviewer-tailored pack must add, per the recurring `read_set∖pack` files:** (a) the prior round's own gate evidence (`LOG.md`/`REPORT.md`) — every reviewer reads it to check claims against reality; (b) 5–7 standing cross-reference docs (`GUIDE.md`, `decisions.md`, and the architecture/product/design-authority docs) that reviewers consult for context an implementer pack never needed; (c) adjacency/consumer files outside the edit set that reviewers independently rediscover via grep every time (this repeats E-002 addendum 4/5's "consumer dimension" gap, now confirmed from the reviewer side too).
- **Recommendation:** a reviewer-tailored pack variant should be `{files actually changed in the diff}` + `{prior-round LOG/REPORT}` + `{the standing cross-reference set}`, with the implementer's comprehension-only slices dropped and the consumer/adjacency sweep pre-tabulated into the pack rather than left for the reviewer to rediscover ad hoc — this is the same "sweep must be tabulated, not just executed" rule already codified in dstdns D-128 #10, extended to pack curation itself.

## E-007 — multi-checkpoint schedules (uniform vs DP-optimal), refreshed final numbers (P110/P111/P112, measured 2026-08-20)

**Tool.** `jsonl-metrics.py` gained a `simulate-multi` subcommand (dstdns
`5ea52d37`): (a) **uniform schedules** — N checkpoints at equal call-count
spacing, N = 1..12; (b) **per-N optimal placement** via dynamic programming
(`D_k[j] = brief + min_m [S(j,m) + D_{k-1}[m]]` over precomputed
segment-replay cost rows, argmin chains recovered for placements); (c) an
**unrestricted-N optimum**. Cost model unchanged from E-006 (input×1.0,
cache_read×0.1, cache_creation×1.25, output×5.0); each checkpoint costs one
brief-sized cache_creation and resets carried context to the brief size, after
which the real run's per-call context deltas replay. DP output was
cross-checked two ways: recomputing every recovered placement with an
independent schedule-cost function (exact match), and uniform-N=1 against the
old `simulate` at 50% (exact match).

**Method note.** E-006's numbers were computed while several agents were still
running. All measured agents are now **finished**; a fresh frozen snapshot of
all nine JSONLs was copied before any measurement at **2026-08-20T20:20:16Z**.
Three P112 agents join the set, labeled from their `meta.json` descriptions:
`ab163ba31e6f0b2e6` "P112 adversarial carve review", `ad419503d91b5fe18`
"Tabulated queues consumer sweep", `ae5a8000da305395e` "Assemble P112
orientation pack".

### Refreshed final per-agent summary (E-006 table, final numbers)

| transcript | role | calls | wall min | final context | cache-hit ratio | normalized cost | vs E-006 |
|---|---|---:|---:|---:|---:|---:|---|
| `ade3ee8341f502776` | P110 implementer (opus) | 504 | 74.2 | 595,315 | 0.9952 | 21,110,074.5 | unchanged |
| `ae091ba50b2174b26` | P110 code reviewer (+resume) | 173 | 27.0 | 242,727 | 0.9677 | 4,137,711.9 | unchanged |
| `a7a50a660ded0597c` | P111 carve reviewer (+resume) | 162 | 20.9 | 232,667 | 0.9475 | 3,856,345.4 | unchanged |
| `a5398dae84bd7dbbd` | P111 implementer (opus) | 329 | 46.1 | 477,084 | 0.9928 | 11,812,360.8 | unchanged |
| `ae3da96851c90d08c` | P111 code reviewer | 157 | 27.3 | 253,066 | 0.9534 | 4,330,300.0 | **CHANGED** (was 155 calls / 252,662 / 4,260,349.0 — the one in-flight agent; +1.6% cost) |
| `a6b1163277d943422` | P110 repair successor (sonnet) | 166 | 9.7 | 141,108 | 0.9793 | 2,188,199.7 | unchanged |
| `ab163ba31e6f0b2e6` | P112 carve reviewer | 104 | 19.0 | 191,196 | 0.9401 | 2,405,535.1 | new |
| `ad419503d91b5fe18` | P112 consumer-sweep agent | 81 | 5.4 | 90,277 | 0.9292 | 920,278.7 | new |
| `ae5a8000da305395e` | P112 pack assembler | 36 | 5.9 | 105,582 | 0.8792 | 773,973.0 | new |

Only one E-006 row moved (`ae3da96851c90d08c`, the agent that was mid-resume at
the E-006 snapshot); its single-checkpoint savings shift by <1 point (44.49 /
46.37 / 35.08 vs 44.80 / 46.41 / 35.59). Every other E-006 number reproduces
exactly — E-006's conclusions stand on final data.

### Multi-checkpoint headline (brief = 25k)

`1-ckpt best` = optimal single checkpoint (any placement — slightly better than
E-006's fixed 25/50/75% grid). `best uniform` / `optimal (N≤12)` = the best row
of each N-sweep. `unrestricted` = DP optimum with N unconstrained.

| transcript | 1-ckpt best | best uniform (N) | optimal N≤12 (N) | unrestricted (N) | unrestricted placements (% of calls) |
|---|---:|---:|---:|---:|---|
| `ade3ee` P110 impl | 46.29% | 77.34% (12) | 79.06% (12) | **79.75% (19)** | 0,2,4,8,12,17,23,29,36,42,46,53,59,65,70,76,81,88,94 |
| `ae091b` P110 code rev | 44.97% | 61.43% (8) | 65.63% (8) | 65.63% (8) | 1,10,17,27,38,50,66,82 |
| `a7a50a` P111 carve rev | 45.03% | 57.26% (7) | 62.17% (7) | 62.17% (7) | 1,6,24,44,61,78,92 |
| `a5398d` P111 impl | 46.53% | 72.40% (9) | 74.65% (12) | **74.68% (14)** | 0,4,8,10,18,24,33,44,50,58,65,73,79,89 |
| `ae3da9` P111 code rev | 51.26% | 64.13% (7) | 69.07% (7) | 69.07% (7) | 1,10,22,35,42,62,83 |
| `a6b116` P110 repair | 31.89% | 42.24% (6) | 51.38% (5) | 51.38% (5) | 1,10,31,53,79 |
| `ab163b` P112 carve rev | 42.52% | 50.46% (5) | 57.12% (4) | 57.12% (4) | 1,18,43,68 |
| `ad4195` P112 sweep | 21.09% | 23.80% (3) | 31.48% (3) | 31.48% (3) | 1,21,59 |
| `ae5a80` P112 pack | 35.01% | 19.87% (2) | 38.61% (2) | 38.61% (2) | 3,53 |

### Savings as a function of N (optimal-placement savings %, brief = 25k)

Rows are agents, columns are exact checkpoint counts — read across to see the
marginal value of each extra checkpoint.

| transcript | N=1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ade3ee` (504 calls) | 46.3 | 60.9 | 68.5 | 71.5 | 73.8 | 75.5 | 76.5 | 77.2 | 77.9 | 78.4 | 78.7 | 79.1 |
| `ae091b` (173) | 45.0 | 55.6 | 59.4 | 63.0 | 64.5 | 65.4 | 65.6 | **65.6** | 65.4 | 65.0 | 64.6 | 64.1 |
| `a7a50a` (162) | 45.0 | 52.8 | 57.4 | 59.7 | 61.0 | 61.8 | **62.2** | 62.1 | 61.9 | 61.5 | 61.0 | 60.4 |
| `a5398d` (329) | 46.5 | 62.2 | 67.4 | 69.9 | 71.8 | 73.0 | 73.5 | 74.0 | 74.2 | 74.4 | 74.6 | 74.7 |
| `ae3da9` (157) | 51.3 | 59.4 | 64.2 | 66.9 | 68.4 | 69.0 | **69.1** | 68.8 | 68.6 | 68.2 | 67.8 | 67.3 |
| `a6b116` (166) | 31.9 | 44.4 | 49.5 | 50.6 | **51.4** | 51.2 | 50.6 | 49.7 | 48.7 | 47.6 | 46.5 | 45.4 |
| `ab163b` (104) | 42.5 | 51.6 | 55.6 | **57.1** | 56.9 | 56.6 | 56.1 | 55.4 | 54.5 | 53.5 | 52.5 | 51.4 |
| `ad4195` (81) | 21.1 | 28.7 | **31.5** | 31.0 | 29.7 | 27.2 | 24.5 | 21.7 | 18.7 | 15.6 | 12.6 | 9.4 |
| `ae5a80` (36) | 35.0 | **38.6** | 36.4 | 33.8 | 30.4 | 26.7 | 23.0 | 19.2 | 15.4 | 11.6 | 7.7 | 3.7 |

The corresponding best-uniform savings trail the optimal by 2–6 points for the
long runs (e.g. `ade3ee` uniform-10 = 76.5 vs optimal-10 = 78.4) but by up to
20 points on the shortest run (`ae5a80` uniform-1 = 14.6 vs optimal-1 = 35.0 —
its whole payoff sits in one early checkpoint right after a 47k orientation
spike that uniform spacing misses).

### Sensitivity: brief cost 50k and 100k (unrestricted optimum)

| transcript | 25k: N / savings | 50k: N / savings | 100k: N / savings |
|---|---:|---:|---:|
| `ade3ee` P110 impl | 19 / 79.75% | 13 / 71.48% | 9 / 56.66% |
| `ae091b` P110 code rev | 8 / 65.63% | 6 / 50.44% | 3 / 27.51% |
| `a7a50a` P111 carve rev | 7 / 62.17% | 5 / 46.50% | 2 / 25.28% |
| `a5398d` P111 impl | 14 / 74.68% | 8 / 65.04% | 5 / 49.06% |
| `ae3da9` P111 code rev | 7 / 69.07% | 5 / 55.79% | 2 / 34.78% |
| `a6b116` P110 repair | 5 / 51.38% | 3 / 26.32% | 0 / 0% |
| `ab163b` P112 carve rev | 4 / 57.12% | 4 / 41.22% | 1 / 19.46% |
| `ad4195` P112 sweep | 3 / 31.48% | 1 / 6.36% | 0 / 0% |
| `ae5a80` P112 pack | 2 / 38.61% | 1 / 19.67% | 0 / 0% |

### Caveat — these are upper bounds, and the operator's objection is correct

The 25k-brief model prices a checkpoint as exactly one cache_creation write and
nothing else. A real successor **re-reads the files the brief references**
(E-006's own reviewer data shows 15–34-file read-sets; the P112 pack-consumer
data in E-005 shows even a good pack leaves per-file catch-up reads), pays
**coordination latency** (the controller must notice, spawn, and seed the
successor), and a mid-run checkpoint can land **mid-reasoning**, destroying
in-flight chain-of-thought that the brief cannot carry. All three omissions
scale with N, so large-N savings are systematically overstated: the numbers
above are an **upper bound**, and the true optimum sits at smaller N than the
25k row suggests. The 50k/100k rows are a crude proxy for "brief + catch-up"
total restart cost: moving the effective restart cost from 25k to 100k drops
the 504-call agent's optimal N from 19 to 9 and zeroes out every run under
~170 calls. What the model cannot do at all is say **where** a checkpoint is
*coherent* — the DP places its first checkpoint at 0–4% of the run purely
because early context is cheap to abandon, which no operational schedule should
copy. Placing checkpoints at logical work-item boundaries (end of a file's
edit-cluster, gate-green moments) requires content analysis of the transcript,
not usage arithmetic — **E-008 candidate**.

### Conclusions

- **The E-006 conclusions survive contact with final data.** Only the one
  in-flight agent moved (+2 calls, +1.6% cost, savings shifts <1 point); the
  snapshot-while-running risk was real but immaterial this time.
- **Diminishing returns arrive fast, and run length sets the optimum.** Every
  run under ~110 calls peaks at N ≤ 4; the ~160–175-call runs peak at N = 5–8;
  only the two long opus implementers (329, 504 calls) keep gaining past N = 10.
  Optimal N tracks call count at roughly **one checkpoint per 25–40 calls** at
  25k brief, stretching to one per ~55 calls at 100k.
- **The marginal checkpoint is worth a lot less than the first.** For the
  504-call implementer: checkpoint 1 buys 46 points, checkpoint 2 buys 15,
  checkpoint 3 buys 8, checkpoints 5 through 12 together buy 5.3. The N-table's whole
  right half is flat for every agent.
- **Optimal placement beats uniform spacing by a consistent 3–6 points** on
  long runs (uniform is a fine operational default there) but matters
  enormously on short runs with an early orientation spike — the pack
  assembler's optimal-1 (35.0%) beats its uniform-1 (14.6%) by 20 points
  because the single checkpoint must land right after the spike.
- **Is ~10 checkpoints for the 74-minute, 504-call implementer supported?**
  Within the upper-bound model, yes and comfortably: optimal-10 captures 78.4
  of the 79.8 achievable points (98%), and even uniform-10 gets 76.5% — at 25k
  brief the model is nearly indifferent between 10 and 19. But under the
  100k-brief sensitivity (the more honest proxy once catch-up reads are
  priced), the optimum itself falls to N = 9 at 56.7% — so "about 10" is
  defensible for this one agent *specifically because it is long*; the same
  prescription applied to a 160-call reviewer would already be past its
  optimum. Checkpoint budget should scale with expected run length, roughly
  one per ~30 minutes of opus implementer work, pending the E-008
  coherent-boundary analysis for where to put them.
