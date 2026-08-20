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
