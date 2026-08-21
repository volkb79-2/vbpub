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
further reads. ~~Tooling TODO: pack.sh range support (path:start-end) so curated packs are
list-driven, not hand-built.~~ **Delivered** (dstdns@363c7f7a, 2026-08-20): `pack.py` replaces
`pack.sh` whole (§4.1 — no shim, no dual path); `pack.sh` is deleted from the tree. E-005 delta first live use: carve reviewer receives pack@6e76813b
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

### E-002 addendum 7 — P113 telemetry (implementer + two-phase reviewer)

**Tools.** `dstdns nyxloom-trove/orientation/jsonl-metrics.py curve` (per-agent growth) and
`pack.py score <out-dir> --transcript <jsonl>` (pack fileset vs an agent's real read-set,
resolved from the pack's own `=== <path> ===` slice headers rather than a hand-written
read-list — the tool E-006 Task B's correction above says should supersede `overlap`).
Four frozen subagent transcripts, package `gate-argv-and-stale-tests` (P113): implementer
`afb3278ab0ac806c1`, code reviewer (two phases, resumed in place) `acf84a863aaed19c8`, carve
reviewer `a48350cc76449f600`, pack assembler `a7ee445e6570bf3a8`.

**Per-agent curve summary.**

| transcript | role | calls | wall min | final context | cache-hit ratio | normalized cost | growth shape |
|---|---|---:|---:|---:|---:|---:|---|
| `afb3278ab0ac806c1` | P113 implementer (opus) | 277 | 20.3 | 262,152 | 0.9898 | 5,516,030.5 | roughly linear (knee call 6, 2.2%, +11,075) |
| `acf84a863aaed19c8` | P113 code reviewer, two-phase | 131 | 15.7 | 187,638 | 0.9440 | 2,827,420.7 | roughly linear (knee call 3, 2.3%, +12,810) |
| `a48350cc76449f600` | P113 carve reviewer | 146 | 18.8 | 217,237 | 0.9516 | 3,269,858.5 | roughly linear (knee call 3, 2.1%, +12,565) |
| `a7ee445e6570bf3a8` | P113 pack assembler | 135 | 9.2 | 139,546 | 0.9772 | 1,740,553.5 | roughly linear (knee call 120, 88.9%, +7,521) |

**Pack coverage (`pack.py score`).** The pack both roles actually received is the hand-built,
implementer-shaped `nyxloom-trove/orientation/gate-argv-and-stale-tests/` (26-item read-list,
19 resolved pack files, ~51k tok). The reviewer-shaped pack `pack.py build --role reviewer`
would have produced (built dry-run against the same handoff + diff range) is scored
separately for comparison.

| pack | scored against | pack files | read-set | ∩ | ∩/read-set | ∩/pack | dead (unused) | missing (outside pack) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| hand pack (implementer-shaped) | P113 implementer | 19 | 30 | 17 | 56.7% | 89.5% | 2 | 13 |
| hand pack (implementer-shaped) | P113 code reviewer | 19 | 25 | 6 | 24.0% | 31.6% | 13 | 19 |
| reviewer-shaped dry-run pack | P113 code reviewer | 22 | 25 | 6 | 24.0% | 27.3% | 16 | 19 |

The implementer's own 2 unused pack files were `workflow_gateway.py` (slice) and `GUIDE.md`
(slice); its 13 missing (read but never packed) were led by `health_server.py` (×3),
`.worktrees/…/tests/test_startup_readiness_wiring.py` (×2, worktree-path duplicate of a
pack file), `test_api_endpoints_coverage.py` (×2), `test_role_matrix_contract.py` (×2) — the
same "consumer files found by the implementer's own grep, not the pack" pattern as E-002
addendums 4/5/6.

**Path-normalization artifact (measurement caveat, both packs).** Both `pack.py score`
tables above undercount real overlap by a few files: the reviewer read `nyxloom.toml`,
`dstdns-P113-LOG.md`, `dstdns-P113-REPORT.md`, `test_legacy_task_routes_gone.py`, and
`test_selftest_probes.py` **via their `.worktrees/p113-gate-argv-and-stale-tests/…` paths**,
which `pack.py score`'s exact-path match reports as "missing" even though the *same file* by
canonical path sits in that pack's "unused" list — e.g. the reviewer-shaped pack's full
`dstdns-P113-LOG.md`/`REPORT.md` entries (packed precisely because "every reviewer reads it
to check claims against reality", E-006 conclusions) were in fact read, just at a path the
scorer doesn't fold together. Normalizing those 5 duplicates would raise the reviewer-shaped
pack's ∩ from 6/22 (27.3%) toward ~11/22 (~50%) — `pack.py score` needs a worktree-path
normalization pass to report this correctly; recorded here as a tooling gap, not fixed in
this pass.

**Self-reported vs measured (implementer).** Source: `dstdns
nyxloom-trove/reports/dstdns-P113-REPORT.md` §"E-002 §12 telemetry" (self) vs the curve/score
above (harness-measured).

| metric | self-reported (REPORT §12) | measured |
|---|---|---|
| tool calls this session | ~103 (Bash/Read/Edit/Write/Agent-tool combined; several Bash calls chained) | 277 assistant calls carrying a `usage` block — **2.7×** the self-report |
| peak context | "stayed well under the ~300k checkpoint threshold" — no number given | final context 262,152 (cache-hit ratio 0.9898) |
| pack coverage | "essentially all" of the 26-item read-list read from the live tree, + 2 named reads outside it (`test_role_enforcement.py`, `test_role_matrix_contract.py`) + `auth.py`/`api_endpoints.py`/`classification_routes.py` + one empirical fakeredis probe | `pack.py score`: read-set 30 files, ∩=17 (56.7% of read-set), 2 pack files unused, 13 read-but-unpacked |

The self-report's "~103 tool calls" and the harness's 277 assistant calls disagree by
~2.7×; the self-report was very likely counting distinct *actions* (with chained
multi-command Bash heredocs collapsed to one) where the harness counts every assistant
message carrying a `usage` block. Left unreconciled here — flagged as a live instance of
this doc's own convention that self-reports are approximate.

**Conclusion.** The implementer-shaped pack served the implementer role well (89.5% of the
pack used, dead weight only 2/19 files) but reproduced E-006's Task B prediction on the
reviewer role almost exactly: 68.4% dead weight (13/19) here vs 75–76% previously measured —
same order of magnitude, same failure mode, a 4th confirmation. **The reviewer-shaped pack
did NOT do meaningfully better on this instance, raw** — 72.7% dead weight (16/22) vs the
implementer-shaped pack's 68.4% (13/19), i.e. numerically *worse* before normalization. The
path-normalization correction above narrows this — reviewer-shaped drops to ~50% dead weight
(11/22) vs implementer-shaped's own normalized ~63% (12/19), so the reviewer-shaped variant
is the better of the two once the worktree-path artifact is corrected for, but the margin is
modest and neither is close to the ~25–32% overlap E-006 called out as the reviewer-tailored
target. Both packs missed the *same* 19-file real
need: `test_authz_boundary.py`, `test_a36c_route_corpus_run.py`,
`scripts/testing-exec.sh`, `scripts/schema-gate.sh`, `scripts/coverage_gate.py`,
`classification_routes.py`, and several contract tests
(`test_prefix_parity.py`, `test_corpus_api.py`, `test_classification_routes_reflected.py`,
`test_openapi_contract.py`, `test_coverage_gate.py`, `test_corpus_ownership.py`). This is a
**new gap class beyond E-002 addendum 5's "gate-adjacent artifacts" rule**: that rule packs
the gate *declarations* (`[gates.*]`/`[lanes.*]` blocks), but P113 widened a gate's argv and
deleted tests, so the reviewer needed the gate *scripts themselves*
(`testing-exec.sh`/`schema-gate.sh`/`coverage_gate.py`) and the security/contract test suites
that consume the changed surface — neither `pack.py build --role reviewer`'s diff-file rule
nor its gate-declaration rule reaches those. Net: **E-006's ~75% dead-weight prediction held
a 4th time**, but the reviewer-shaped variant `pack.py` already implements did not close the
gap on this package — the missing content was a curation-rule gap (gate scripts + consumer
test suites), not a role-shaping problem `--role reviewer` already solves.

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

> **Correction (2026-08-20, filed during the P113 telemetry pass, dstdns@f7b38def).**
> `jsonl-metrics.py overlap`'s `parse_readlist` kept the **whole row** of a read-list line as
> the "path" before dstdns@f7b38def — inflating `pack_fileset` (and therefore the dead-weight
> counts) for any read-list using the two-space-column `<path>[:a-b]  <kind>  <why>` format
> `pack.py` introduced (dstdns@363c7f7a). `pack.py score` (which parses `pack.md`'s own
> `=== <path> ===` slice headers rather than re-parsing a hand-written read-list) is the
> superseding measure going forward.
>
> Re-run against the fixed tool on the two packs Task B actually used — both still present at
> `dstdns nyxloom-trove/orientation/{config-plane-api,auth-config-cutover}/` against the same
> three frozen transcripts:
>
> | reviewer | pack | pack files | read-set | ∩ | read_set∖pack | pack∖read_set (dead) | ∩/read_set | ∩/pack |
> |---|---|---:|---:|---:|---:|---:|---:|---:|
> | P110 code reviewer (`ae091ba`) | config-plane-api | 17 | 26 | 4 | 22 | 13 | 15.4% | 23.5% |
> | P111 carve reviewer (`a7a50a`) | auth-config-cutover | 28 | 34 | 19 | 15 | 9 | 55.9% | 67.9% |
> | P111 code reviewer (`ae3da9`) | auth-config-cutover | 28 | 15 | 7 | 8 | 21 | 46.7% | 25.0% |
>
> **Identical to the original table above, digit for digit.** Both `config-plane-api/read-list.txt`
> and `auth-config-cutover/read-list.txt` predate `pack.py` and are plain one-column path lists
> (no `:a-b` / kind / why columns), so the bug the fix targets never tripped on these two specific
> read-lists — it only inflates read-lists in the newer two-space-column format (e.g.
> `gate-argv-and-stale-tests`, scored directly via `pack.py score` in the P113 addendum below,
> never through `overlap`). **Task B's dead-weight figures (76%/75%/…) stand as originally
> recorded** — the correction is that the *mechanism* for computing them was fragile, not that
> these particular numbers were wrong. `pack.py score` remains the recommended tool for every
> pack built by `pack.py` itself, since it reads the pack's own slice headers rather than a
> hand/script-maintained read-list.

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

## E-008 — coherent checkpoint boundaries + the first-checkpoint threshold (11 transcripts, measured 2026-08-20)

E-007 ended on an explicit open question: its DP places the first checkpoint at
0–4% of the run *purely because early context is cheap to abandon*, and the
usage arithmetic "cannot say at all **where** a checkpoint is coherent". E-008
answers that from transcript CONTENT, and settles the operator's separate
objection that dstdns `CLAUDE.md`'s **~300k** checkpoint threshold "is probably
too high".

**Tool.** `jsonl-metrics.py` gained three subcommands (dstdns@cde23ad5,
`nyxloom-trove/orientation/jsonl-metrics.py`):
`boundaries` (content-detected candidate checkpoint points), `simulate-boundary`
(the E-007 DP **constrained to those boundaries**, next to the unconstrained
optimum and the uniform schedule, plus rule probes), and `threshold` (where the
FIRST checkpoint belongs, in context size and in calls, with median/IQR across
the given transcripts). The five existing subcommands are byte-identical in
behaviour: `curve` on `ade3ee8341f502776` was diffed before and after the change
(identical), and `simulate-multi` on `ae5a8000da305395e` still reproduces
E-007's 35.01 / 38.61 / N=2 / placements [3,53].

**Method note.** Frozen snapshot at **2026-08-20T23:06:14Z**, taken before any
measurement: the nine E-007 JSONLs (copied from the E-007 snapshot, so every
E-006/E-007 row still reproduces exactly) plus two newer finished agents. Two
further agents were rejected on the stated ≥100-call / mtime-stability rule:
`afb3278ab0ac806c1` (P113 implementer) was **still being appended to** at
snapshot time (its size grew between two `stat` calls), and two other subagents
in the same session were in-flight.

```bash
SNAP=<scratch>/e008-snapshot; mkdir -p $SNAP
cp <e007-snapshot>/agent-*.jsonl $SNAP/                       # the nine E-007 transcripts
cp ~/.claude/projects/-workspaces-dstdns/<sess>/subagents/agent-a48350cc76449f600.jsonl \
   ~/.claude/projects/-workspaces-dstdns/<sess>/subagents/agent-a7ee445e6570bf3a8.jsonl $SNAP/
date -u +%Y-%m-%dT%H:%M:%SZ > $SNAP/SNAPSHOT-TS
```

The two new transcripts, by `curve`:

| transcript | role | calls | wall min | final context | cache-hit ratio | normalized cost |
|---|---|---:|---:|---:|---:|---:|
| `a48350cc76449f600` | P113 carve reviewer (opus) | 146 | 18.8 | 217,237 | 0.9516 | 3,269,858.5 |
| `a7ee445e6570bf3a8` | P113 pack assembler (sonnet) | 135 | 9.2 | 139,546 | 0.9772 | 1,740,553.5 |

### Detection rules (what "coherent" is operationalized as)

`python3 jsonl-metrics.py boundaries $SNAP/agent-*.jsonl` — six kinds, by call index:

| kind | rule |
|---|---|
| `gate_green` / `gate_red` / `gate_unknown` | a Bash call that RUNS `testing-exec.sh`, `schema-gate.sh` or `pytest`, with the verdict read off its own `tool_result` |
| `commit` | a Bash `git commit` |
| `edit_cluster_end` | the last edit of a maximal run of edit ops on one file, ended by an edit op on a different file |
| `log_report_write` | an edit whose target matches `*LOG.md` / `*REPORT.md` |

Three heuristics had to be repaired before the numbers meant anything, and each
repair is itself a finding:

1. **Edits mostly do not go through the Edit/Write tools.** The P110
   implementer made 333 Bash calls and 5 Write calls; the P113 carve reviewer
   made 89 Bash calls and zero. Detection therefore covers **Bash-mediated
   writes** (heredoc redirect, `sed -i`, `tee`, `cp`/`mv` destination, a python
   heredoc calling `write_text`/`open(...,"w")`). Adding them took the P110
   implementer from 5 detected edit clusters to 91.
2. **Heredoc bodies must be stripped before matching.** A `python3 - <<'PY'`
   writing a comment that *mentions* pytest into a test file is not a gate run;
   2 of the P110 implementer's 18 apparent pytest runs were text, not execution.
3. **Command splitting must be quote-aware.** Splitting the raw string on `|`
   cuts through a quoted grep alternation (`"^def \|^@pytest\|..."`), orphaning
   a fragment whose first token then looks like a program name — which is how a
   plain grep was counted as a gate run. `simulate-boundary`/`boundaries` use a
   shlex-based splitter; `readset`/`overlap` keep the old regex split verbatim
   so E-006's tables stay reproducible.

Gate verdicts also had to fall back to pytest's own failure markers (`^E   `,
`^FAILED `, `_____ test_x _____`, `Traceback`): 4 of the P110 implementer's
pytest results reached the transcript as filtered excerpts with **no summary
line at all**.

### Task A — boundaries per transcript

`gG/gR/gU` = gate green / red / unknown; `edit` = `edit_cluster_end`; `lr` =
`log_report_write`. "first strong" = first `gate_green` or `commit`.

| transcript | role | calls | total | gG | gR | gU | commit | edit | lr | first strong (call, %run, ctx) | first of any kind |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `ade3ee` | P110 impl (opus) | 504 | 143 | 11 | 18 | 8 | 10 | 91 | 5 | c118, 23.4%, 243,426 | c85, 16.9%, 218,253 |
| `a5398d` | P111 impl (opus) | 329 | 76 | 4 | 5 | 7 | 4 | 54 | 2 | c116, 35.3%, 264,857 | c38, 11.6%, 199,892 |
| `ae091b` | P110 code rev | 173 | 30 | 0 | 0 | 14 | 0 | 16 | 0 | — | c5, 2.9%, 52,033 |
| `a6b116` | P110 repair (sonnet) | 166 | 28 | 0 | 0 | 6 | 1 | 18 | 3 | c164, 98.8%, 140,730 | c30, 18.1%, 69,231 |
| `a7a50a` | P111 carve rev | 162 | 2 | 0 | 0 | 0 | 0 | 2 | 0 | — | c88, 54.3%, 137,405 |
| `ae3da9` | P111 code rev | 157 | 29 | 1 | 2 | 11 | 0 | 15 | 0 | c85, 54.1%, 180,163 | c7, 4.5%, 58,330 |
| `a48350` | P113 carve rev | 146 | 10 | 0 | 0 | 8 | 0 | 2 | 0 | — | c5, 3.4%, 53,795 |
| `a7ee44` | P113 pack asm | 135 | 8 | 0 | 0 | 1 | 0 | 7 | 0 | — | c84, 62.2%, 97,896 |
| `ab163b` | P112 carve rev | 104 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | — | c37, 35.6%, 108,236 |
| `ad4195` | P112 sweep | 81 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — | — |
| `ae5a80` | P112 pack asm | 36 | 2 | 0 | 0 | 1 | 0 | 1 | 0 | — | c17, 47.2%, 66,978 |

Three structural facts fall straight out:

- **Only 4 of 11 runs contain a single `gate_green` or `commit`** — and one of
  those (`a6b116`) has it at 98.8% of the run. Reviewers never commit, and they
  fire the gate with `run_in_background`, so the launch call carries the result
  "Command running in background" and the verdict arrives out of band: 14 of
  `ae091b`'s 14 gate boundaries and 8 of `a48350`'s 8 are `gate_unknown`. **A
  checkpoint rule keyed to "gate-green or commit" would never fire for a
  reviewer.**
- **Boundary density tracks role, not length.** The two opus implementers carry
  76–143 boundaries (one every 3–4 calls); reviewers carry 1–30; the pure
  read-and-tabulate sweep agent (`ad4195`, 81 calls) has **zero** — it never
  writes a file, runs a gate, or commits, so it offers no content-derived
  checkpoint at all.
- **The first coherent boundary is late in context terms.** For the two opus
  implementers it lands at 199,892 (c38) and 218,253 (c85) carried tokens — the
  opening 12–17% of an implementer run is orientation and reading, with nothing
  coherent to cut on. Their first *strong* boundary (gate-green or commit) is
  later still: 243k (c118) and 265k (c116).

### Task B — the DP constrained to detected boundaries

`python3 jsonl-metrics.py simulate-boundary $SNAP/agent-*.jsonl --brief-tokens 25000 --max-n 10`
(all six kinds allowed as placements). "bounded" = DP restricted to boundaries,
"optimal" = E-007's unconstrained DP, "uniform" = E-007's equal-spacing
schedule; gap = optimal − bounded, in savings points.

| transcript | placements avail. | N=1 bounded / opt / unif | N=3 bounded / opt / unif | N=5 bounded / opt / unif | N=10 bounded / opt / unif |
|---|---:|---|---|---|---|
| `ade3ee` | 118 | 46.07 / 46.29 / 44.25 | 67.77 / 68.53 / 64.38 | 71.72 / 73.82 / 71.15 | 74.41 / 78.37 / 76.52 |
| `a5398d` | 63 | 45.78 / 46.53 / 41.85 | 66.73 / 67.41 / 62.18 | 70.72 / 71.82 / 67.54 | 71.80 / 74.43 / 72.26 |
| `ae091b` | 18 | 44.40 / 44.97 / 42.89 | 58.54 / 59.41 / 56.84 | 61.92 / 64.53 / 61.29 | 60.93 / 64.99 / 60.77 |
| `a6b116` | 20 | 31.36 / 31.89 / 28.72 | 41.76 / 49.45 / 39.91 | 42.14 / 51.38 / 42.18 | 36.44 / 47.62 / 39.84 |
| `a7a50a` | 2 | 41.23 / 45.03 / 42.40 | n/a / 57.44 / 53.47 | n/a / 60.99 / 55.82 | n/a / 61.52 / 56.90 |
| `ae3da9` | 17 | 50.18 / 51.26 / 46.37 | 61.13 / 64.19 / 58.37 | 63.91 / 68.40 / 62.73 | 61.74 / 68.23 / 63.39 |
| `a48350` | 8 | 39.66 / 40.90 / 38.63 | 53.01 / 56.91 / 50.86 | 52.81 / 60.21 / 53.65 | n/a / 59.34 / 53.88 |
| `a7ee44` | 8 | 21.77 / 28.47 / 25.10 | 21.67 / 41.78 / 34.88 | 18.79 / 42.65 / 35.90 | n/a / 37.32 / 32.04 |
| `ab163b` | 1 | 41.34 / 42.52 / 39.09 | n/a / 55.64 / 48.67 | n/a / 56.90 / 50.46 | n/a / 53.53 / 48.61 |
| `ad4195` | 0 | n/a / 21.09 / 20.81 | n/a / 31.48 / 23.80 | n/a / 29.65 / 22.49 | n/a / 15.63 / 11.26 |
| `ae5a80` | 2 | 14.60 / 35.01 / 14.60 | n/a / 36.36 / 19.10 | n/a / 30.35 / 14.57 | n/a / 11.59 / −2.21 |

**Coherence is nearly free where boundaries exist.** For the four
boundary-rich runs with well-spread boundaries (`ade3ee`, `a5398d`, `ae091b`,
`ae3da9`) the N=1 gap is **0.22–1.08 points** and the N=3 gap is
**0.68–3.06**; the boundary-constrained schedule also **beats uniform spacing**
at N=1, 3 and 5 on all four (by N=10 uniform overtakes it on three of them —
the boundary set runs out of well-spaced options). The gap becomes material
only where the transcript offers almost nothing to cut on (`ae5a80`: 20.4
points at N=1; `a7ee44`: 20.1 at N=3) or where boundaries exist but bunch
badly (`a6b116`: 20 placements, yet 7.7 points at N=3 and 11.2 at N=10).

**The gap shrinks further at a realistic restart cost.** Same command with
`--brief-tokens 100000` (E-007's honest proxy for brief + catch-up reads): the
N=1 gap is 0.02 (`a5398d`), 0.05 (`ade3ee`), 0.20 (`ae3da9`), 0.27 (`ae091b`),
0.44 (`a7a50a`); at N=3, 0.30 / 0.30 / 1.60 / 1.14 / n/a. A more expensive
checkpoint wants fewer and later ones — exactly the placements boundaries can
supply.

**Restricting to "strong" boundaries only is expensive.** Same command with
`--kinds gate_green,commit,log_report_write`: 7 of 11 transcripts lose every
placement (6 of them had placements under the all-kinds rule), and where
placements survive the cost is real (`a5398d` N=3: 56.36
vs 66.73 all-kinds vs 67.41 unconstrained; `a6b116` N=3: 24.67 vs 41.76).
**Edit-cluster ends are the load-bearing boundary kind**, not gates and commits.

#### The "first gate-green/commit after X% of run" rule

Printed by the same command (rows: chosen call, kind, context, and its
single-checkpoint savings at 25k brief). It resolves on only 3 of 11:

| transcript | X=25% | X=33% | X=50% |
|---|---|---|---|
| `ade3ee` | c137, 27.2%, 261,557 → **41.68%** | c208, 41.3%, 342,920 → 44.86% | c252, 50.0%, 393,978 → 44.16% |
| `a5398d` | c116, 35.3%, 264,857 → **43.73%** | c116, 35.3%, 264,857 → 43.73% | c191, 58.1%, 353,260 → 38.34% |
| `ae3da9` | c85, 54.1%, 180,163 → **44.10%** | c85, 54.1%, 180,163 → 44.10% | c85, 54.1%, 180,163 → 44.10% |
| `a6b116` | c164, 98.8%, 140,730 → **−0.90%** | same | same |
| other 7 | no such boundary | — | — |

Where it resolves on a real work boundary it is good (41.7–44.1%, i.e. 86–94%
of that agent's unconstrained 1-checkpoint optimum). Where the only commit is
the final one it is **worse than not checkpointing** (`a6b116`, −0.90%). And it
is silent on 7 of 11 runs. **A percent-of-run rule is unusable as stated** —
the agent does not know its own run length in advance, and the strong-boundary
kinds it keys on do not exist for reviewers.

### Task C — where does the FIRST checkpoint belong?

`python3 jsonl-metrics.py threshold $SNAP/agent-*.jsonl --briefs 25000,50000,100000 --max-n 10`

Three independent estimators per transcript:

- **marginal** — the first call whose per-call *carrying* cost
  (`cache_read × 0.1`) exceeds what the same call would cost after a restart:
  post-restart carrying (`brief × 0.1`) plus the one-time brief write amortized
  over the remaining calls (`brief × 1.25 / remaining`).
- **DP-first** — the first placement of E-007's unrestricted optimum.
- **bounded-first** — the first placement of the boundary-constrained optimum
  at that agent's best N.

Medians and IQRs across all 11 (context in tokens; `n` excludes runs where the
estimator never fires):

| brief | marginal ctx (calls) | DP-first ctx (calls) | bounded-first ctx (calls) |
|---|---|---|---|
| 25k | n=11, **49,704** (IQR 45.5k–52.5k), call 3 | n=11, 42,433 (IQR 41.3k–42.6k), call 1 | n=10, 83,564 (IQR 60.5k–130.1k), call 34 |
| 50k | n=9, 60,464 (IQR 58.3k–64.0k), call 9 | n=11, 42,687 (IQR 42.5k–54.3k), call 1 | n=10, 101,228 (IQR 67.5k–130.1k), call 34 |
| 100k | n=7, **116,028** (IQR 112.7k–118.4k), call 40 | n=7, 150,458 (IQR 134.4k–156.2k), call 55 | n=10, 130,324 (IQR 114.0k–161.4k), call 68 |

The 25k and 50k rows carry E-007's known model artifact (a checkpoint priced as
one cache_creation write and nothing else makes restarting at call 1 look
optimal). **The 100k row is the honest one**, and all three estimators agree
within a factor of 1.3: the right first checkpoint is at **~115k–150k of
carried context, around call 40–70**.

By role (`threshold` run over each group; 100k brief):

| group | transcripts | marginal ctx (calls) | DP-first ctx (calls) | bounded-first ctx (calls) |
|---|---|---|---|---|
| implementers | `ade3ee`, `a5398d`, `a6b116` | 110,270 (call 20) | 172,848 (call 34) | 199,892 (call 86) |
| reviewers | `ae091b`, `a7a50a`, `ae3da9`, `ab163b`, `a48350` | 116,452 (call 49) | 134,458 (call 61) | 137,405 (call 68) |
| support (sweep/pack) | `ad4195`, `ae5a80`, `a7ee44` | — (never fires) | — | 90,262 (call 64) |

By run length (100k brief): long (≥300 calls) marginal 110,270 / call 20,
bounded-first 209,072 / call 62; mid (135–175) marginal 114,997 / call 51,
bounded-first 130,324 / call 78; short (≤104) — only `ab163b` fires at all
(123,955 / call 49), the other two never justify a checkpoint.

**Implementers cross the marginal threshold at half the call count reviewers
do — call 14–25 vs call 36–65, at the same ~110–116k of context** — because
their context grows about twice as fast per call. But their first *coherent*
boundary at that size is much later (call 38 and 85; 200k and 218k), since an
implementer's opening 12–17% is undivided orientation with nothing to cut on.
That gap between "when it starts paying" and "when it is safe to cut" is the
entire practical content of this experiment.

#### Testing the operator's objection directly

`python3 jsonl-metrics.py simulate-boundary $SNAP/agent-*.jsonl --brief-tokens 25000 --max-n 1 --ctx-rules 120000,200000,300000`
— "first coherent boundary at or above C carried tokens", with the resulting
single-checkpoint savings, next to that transcript's boundary-constrained
1-checkpoint optimum (`bnd N=1`):

| transcript | bnd N=1 | C=120k rule | C=200k rule | C=300k rule |
|---|---:|---|---|---|
| `ade3ee` | 46.07 | c85, 16.9% → 39.09 | c85 → 39.09 | c180, 35.7% → 44.55 |
| `a5398d` | 45.78 | c38, 11.6% → 44.04 | c83, 25.2% → 45.78 | c148, 45.0% → 43.69 |
| `ae3da9` | 50.18 | c60, 38.2% → **50.18** | c125, 79.6% → 14.40 | never reached |
| `ae091b` | 44.40 | c66, 38.2% → 44.34 | c121, 69.9% → 34.12 | never reached |
| `a7a50a` | 41.23 | c88, 54.3% → **41.23** | c156, 96.3% → 1.98 | never reached |
| `a48350` | 39.66 | c66, 45.2% → 39.25 | c137, 93.8% → 3.92 | never reached |
| `a6b116` | 31.36 | c141, 84.9% → 10.69 | never reached | never reached |
| `a7ee44` | 21.77 | c121, 89.6% → 6.63 | never reached | never reached |
| `ab163b` | 41.34 | never reached | never reached | never reached |
| `ad4195` / `ae5a80` | n/a / 14.60 | never reached | never reached | never reached |

**The operator is right, and by a wide margin. 9 of the 11 measured agents
never reach 300k carried context at all** — only the two opus implementers do,
at 36–45% of their runs. The current `CLAUDE.md` threshold therefore fires
**zero times** on 9 of 11 real agents, including every reviewer, while those
reviewers were leaving 39–50 points of restart savings on the table.

A **120k** trigger, by contrast, recovers the boundary-constrained
single-checkpoint optimum essentially exactly on `ae3da9` (50.18 vs 50.18) and
`a7a50a` (41.23 vs 41.23), within 0.5 points on `ae091b` and `a48350`, within
1.7 on `a5398d`, and at 85% on `ade3ee`; it correctly declines to fire on the
three runs too short to benefit. Its two weak results (`a6b116` 10.69,
`a7ee44` 6.63) are both cases where the *only* boundary above 120k is at 85–90%
of the run — late, but still positive, unlike the −0.90% the percent-of-run
rule produces on `a6b116`.

### Recommended threshold rule

> **Arm the checkpoint at ~120k carried context, or after ~60 tool calls,
> whichever comes first; then checkpoint at the NEXT coherent boundary after
> the trigger. Repeat roughly every ~40–55 calls (≈50–70k of context growth),
> always at a coherent boundary, and stop once fewer than ~40 calls of work
> remain.**

- **~120k, not ~300k.** Median honest first-checkpoint context is 116k
  (marginal), 130k (boundary-constrained), 150k (DP), IQRs 113k–161k. 300k is
  above the *final* context of 9 of 11 measured agents.
- **Arm-then-cut, because the two events are not the same call.** Context
  crosses ~120k at call 14–25 for implementers and 36–65 for reviewers, but
  the first coherent boundary at or above that size lands at call 38–85
  (implementers) and 60–88 (reviewers). The trigger says *start looking*; the
  boundary says *cut here*. The ~60-call clause is a safety net for an agent
  whose context grows slowly while it accumulates many cheap calls — in this
  set it changes nothing, because the nearest boundary after call 60 is the
  same one the 120k trigger selects.
- **"Coherent boundary" means, in priority order:** a green gate; a commit; a
  LOG/REPORT write; the end of an edit cluster (last edit on a file before
  moving to a different one). **Never on a red gate** — the failure diagnosis
  in flight is exactly what a brief cannot carry. If none of these is available
  (`ad4195`-shaped read-and-tabulate agents), do not checkpoint: those runs
  have no coherent cut and their savings are the smallest in the set anyway.
- **Cadence.** E-007's per-N tables put the optimum near one checkpoint per
  25–40 calls at 25k brief and one per ~55 at 100k; the boundary-constrained
  optimum for the 504-call implementer places its 10 checkpoints ~40–55 calls
  apart. Under 110 calls, one checkpoint (or none) is the whole optimum.
- **Expect the first checkpoint to be the one that matters.** E-007: checkpoint
  1 buys 46 points on the 504-call implementer, checkpoint 2 buys 15,
  checkpoints 5–12 together buy 5.3.

### Conclusions

- **The ~300k checkpoint threshold is measured wrong, not merely conservative.**
  9 of 11 agents never reach 300k carried context at any point in their run;
  the rule fires zero times on every reviewer in the set while those reviewers
  were leaving 39–50 points of restart savings unclaimed. The honest first
  checkpoint is at **~120k**, where three independent estimators (marginal
  crossover 116k, boundary-constrained DP 130k, unconstrained DP 150k) agree
  within a factor of 1.3.
- **Coherence is close to free — where the transcript offers boundaries.**
  Constraining E-007's DP to content-detected boundaries costs 0.22–1.08
  savings points at N=1 and 0.68–3.06 at N=3 on the four boundary-rich runs,
  and the constrained schedule still beats uniform spacing at N=1/3/5. E-007's
  open worry — that operationally placeable checkpoints would forfeit most of
  the theoretical win — is not borne out.
- **Edit-cluster ends carry the boundary set, not gates and commits.**
  Restricting placements to gate-green/commit/LOG-REPORT strips every placement
  from 7 of 11 runs and costs ~10 points at N=3 where placements survive. Only
  4 of 11 runs contain a single gate-green or commit at all.
- **Reviewers are structurally boundary-poor, and it is a mechanism artifact.**
  They never commit, and they launch gates with `run_in_background`, so the
  launching call carries no verdict — 14/14 and 8/8 of two reviewers' gate
  boundaries are `gate_unknown`. Any rule keyed to "checkpoint at gate-green"
  is a rule reviewers can never satisfy; correlating the background result
  notification back to its launch would recover those boundaries and is the
  obvious next tool increment.
- **"When it starts paying" and "when it is safe to cut" are different calls,
  and the gap is role-dependent.** Implementers cross ~120k at call 14–25 but
  their first coherent boundary at that size is call 38–85; reviewers cross at
  call 36–65 with a boundary at 60–88. The rule therefore has to *arm* on a
  threshold and *cut* on the next boundary, not fire on the threshold itself.
- **Percent-of-run rules do not survive contact with the data.** "First
  gate-green/commit after 25% of the run" resolves on 3 of 11 transcripts,
  produces 86–94% of the achievable single-checkpoint saving where it does, and
  on `a6b116` selects the run's only commit — at 98.8% — for **−0.90%**
  savings. An agent cannot know its run length in advance anyway.
- **Some runs should never be checkpointed.** The 81-call consumer-sweep agent
  has zero boundaries of any kind (it writes nothing, runs nothing, commits
  nothing) and the two pack assemblers turn negative at a 100k restart cost.
  "Do not checkpoint" is a valid output of the rule, not a failure of it.

### Caveats

The three E-007 caveats stand unchanged and still make every savings figure an
**upper bound**: the model prices a checkpoint as one cache_creation write, so
it omits (a) the successor's **catch-up reads** of the files the brief
references, (b) **coordination latency** for noticing, spawning and seeding a
successor, and (c) the risk of landing **mid-reasoning**. E-008 adds four of
its own:

1. **"Coherent" here is weaker than it sounds.** An `edit_cluster_end` proves
   only that the agent is not mid-file — not that it is not mid-diagnosis.
   Since edit-cluster ends are the load-bearing kind (dropping them costs
   10 points at N=3), the boundary set's *quality* is doing less work than its
   *availability*.
2. **Verdict detection is partial.** Reviewers run gates in the background, so
   the launch call has no verdict and lands in `gate_unknown` (14/14 for
   `ae091b`); the real verdict arrives in a later notification this tool does
   not correlate. `gate_green` counts are therefore floors, not totals.
3. **The constrained DP inherits the E-007 replay model.** It re-prices the
   remaining calls with the same per-call context deltas the real run had —
   i.e. it assumes the successor does the same work at the same rate, which is
   exactly what catch-up reads violate.
4. **n=11, one project, one week.** All transcripts come from dstdns P110–P113
   under the same pipeline (carve → pack → implement → review), so role
   behaviour is this pipeline's, not a general property of agents. The
   support-agent group (n=3) is too small to carry its own rule.

### What changes in `CLAUDE.md` (for the controller to apply)

dstdns `CLAUDE.md` § "Long-running agent context discipline" currently states a
single **~300k** subagent checkpoint threshold and the same figure for the
interactive session. Measured, that threshold never fires for 9 of 11 real
agents. The section should be restated around the rule above: **first
checkpoint at the first coherent boundary at or after ~120k carried context (or
~60 tool calls, whichever comes first), then every ~40–55 calls, never on a red
gate, and none with fewer than ~40 calls of work left** — with the note that
implementers hit 120k around call 20–38 and reviewers around call 60–88, and
that a run offering no coherent boundary at all (pure read-and-tabulate sweeps)
should not be checkpointed. The dispatch-prompt checkpoint clause in the
`dispatch` skill carries the same number and must move with it. The
"respawn beats resume past ~300k" guidance is a *separate* decision (resume vs
fresh successor) and is not what this experiment measured — it should be
restated in terms of remaining-work size, not re-anchored to 120k without its
own measurement.

## V3/V4 · 2026-08-20 · pattern (b) snapshot chain, end-to-end — SYNTHETIC scenario, haiku only

Runs the two adoption-gate rows of `design-context-lifecycle.md` §5: **V3** (3-iteration
chain: fork → work → iteration-summary → re-fork) and **V4** (parallel implementer +
reviewer forks off one mid-chain snapshot), plus the one **CONTROL** the chain exists to
beat (resume the working transcript instead of the summary-minted snapshot).

**Why synthetic.** Operator rule: A/B and chain experiments never run on real dstdns work —
a chain experiment must be free to re-run an iteration, and a real package's gate is not a
controlled variable. Scenario, harness and all artifacts live in a throwaway git repo
outside both repos (`…/scratchpad/v3-chain/`, `git init`); nothing here touched dstdns or
vbpub source.

**Model discipline.** Every call is `--model haiku` (`claude-haiku-4-5-20251001`), no
`--effort` flag, identical toolset, `--safe-mode` (no CLAUDE.md / skills / hooks / MCP in
the prefix) and `--exclude-dynamic-system-prompt-sections` — the four L24 prefix invariants,
held flat across all 19 calls. CLI build 2.1.238. No flag was missing and `--fork-session`
behaved correctly headlessly, so there is no BLOCKED row.

### Scenario

A `shop` package: 17 Python files / 1,007 lines + README + `pytest.ini`, no third-party
deps. Three sequential work items of similar size, each 3–4 files, each with a pre-written
test file that FAILS at the baseline commit and must go green:

| item | change | files | dependency on earlier items |
|---|---|---|---|
| WI-1 | migrate builtin exceptions onto the `shop.errors` hierarchy | `validation.py`, `inventory.py`, `orders.py` | — |
| WI-2 | add `utils.round_cents` (Decimal `ROUND_HALF_UP`), route pricing/discounts/tax through it | `utils.py`, `pricing.py`, `discounts.py`, `tax.py` | — |
| WI-3 | `models.OrderSummary` + `Order.totals()` + `shipping.estimate_for_order` + `orders.finalize_order` + `reports.summarize_orders` | `models.py`, `shipping.py`, `orders.py`, `reports.py` | needs WI-1's `OrderError` **and** WI-2's `round_cents` — so the chain must actually carry them |

Gate per item: plain `python3 -m pytest …` inside the scratch repo (no dstdns gate).
Regression floor `tests/test_base.py` must stay green at every commit.

**Orientation payload** is the E-002 model-free pack: verbatim concatenation of every
tracked file, built by script at zero tokens — `notes/orientation-pack.md`, 34,030 B =
**16,819 billed tokens** (2.02 B/tok, consistent with E-002's "bytes÷4 undercounts code").

### Protocol (reproducible)

```bash
# invariants held on EVERY call
COMMON=(--model haiku --output-format json
        --exclude-dynamic-system-prompt-sections --safe-mode --dangerously-skip-permissions)

# S0 — frozen orientation snapshot: read the pack in ONE call, answer "ORIENTED", stop.
claude -p "${COMMON[@]}" --session-id "$S0" "$(cat prompts/orientation.txt)"

# per iteration i:
claude -p "${COMMON[@]}" --resume "$S_{i-1}" --fork-session "$(cat prompts/wi$i.txt)"  # -> W_i, works+commits
claude -p "${COMMON[@]}" --resume "$W_i"                    "$(cat prompts/summary.txt)" # -> notes/summary-i.md
cat prompts/mint.txt notes/summary-$i.md > runs/mint$i.prompt
claude -p "${COMMON[@]}" --resume "$S_{i-1}" --fork-session "$(cat runs/mint$i.prompt)"  # -> S_i, replies ACK
# W_i is then abandoned: never resumed again.
```

`prompts/summary.txt` is the self-compaction contract: ≤45 lines, sections **DONE / SEAMS /
GATE / NEXT**, explicitly *"an INDEX, not an archive — the durable state is the git history
and the files, so an omission costs the next iteration a re-read, never a loss"*, and
*"write nothing the next work item cannot use"*. `prompts/mint.txt` frames the summary as
fact superseding the pack and demands a bare `ACK` with no tool use.

Usage is read only from the JSON result (`jq` on `usage` / per-turn `message.usage`); no
transcript JSONL was ever loaded into a context.

### Chain topology (billed context at freeze)

| snapshot | minted from | summary injected | snapshot size (tok) | Δ vs parent |
|---|---|---|---|---|
| S0 | `--session-id`, pack read | — | 35,818 | — (18,999 system+tools + 16,819 pack) |
| S1 | fork S0 | `summary-1.md`, 1,402 B | 36,684 | **+866** |
| S2 | fork S1 | `summary-2.md`, 1,601 B | 37,509 | **+825** |
| S3 | fork S2 | `summary-3.md`, 1,251 B | 38,234 | **+725** |

Working transcripts at the moment they were abandoned: **W1 53,035 · W2 50,213 · W3 51,988**
tokens. The chain grows ~800 tok/iteration; the thing it refuses to carry is ~50k each time.

### Per-call usage (all `claude-haiku-4-5`)

`first_cc` / `first_cr` = the fork's **first turn** — the only number the V3/V4 oracle is
about. `last_ctx` = billed context on the final turn (= session size at exit).

| call | role | turns | wall s | in | cc | cr | out | first_cc | first_cr | last_ctx |
|---|---|---|---|---|---|---|---|---|---|---|
| `s0-orientation` | mint S0 | 2 | 6 | 18 | 16,811 | 41,045 | 462 | 3,047 | 18,999 | 35,818 |
| `w1-work` | V3 iter 1 (fork S0) | 23 | 86 | 186 | 14,338 | 1,003,708 | 8,525 | **776** | 35,810 | 50,156 |
| `w1-summary` | self-compact W1 | 4 | 18 | 34 | 34,028 | 174,534 | 1,565 | 31,745 | 18,999 | 53,035 |
| `s1-mint` | mint S1 | 1 | 2 | 10 | 17,675 | 18,999 | 168 | 17,675 | 18,999 | 36,684 |
| `w2-work` | V3 iter 2 (fork S1) | 20 | 38 | 66 | 9,946 | 337,366 | 4,903 | **641** | 36,674 | 46,628 |
| `ctl-w2-naive` | **CONTROL** (fork W1) | 15 | 43 | 122 | 42,101 | 828,192 | 3,389 | **34,662** | 18,999 | 61,108 |
| `w2-summary` | self-compact W2 | 5 | 35 | 42 | 31,206 | 213,688 | 2,181 | 28,096 | 18,999 | 50,213 |
| `s2-mint-fix` | mint S2 | 1 | 2 | 10 | 18,500 | 18,999 | 167 | 18,500 | 18,999 | 37,509 |
| `v4-impl-wi3` | V4 implementer (fork S2) | 16 | 57 | 106 | 28,858 | 550,732 | 5,400 | 19,499 | 18,999 | 47,865 |
| `v4-reviewer` | V4 reviewer (fork S2) | 3 | 31 | 26 | 21,118 | 95,369 | 2,691 | 19,049 | 18,999 | 40,125 |
| `probeA-seq-fork-s2` | cache probe | 1 | 1 | 10 | 18,747 | 18,999 | 48 | 18,747 | 18,999 | 37,756 |
| `probeB-seq-fork-s1` | cache probe | 1 | 1 | 10 | 17,948 | 18,999 | 50 | 17,948 | 18,999 | 36,957 |
| `probeC-refork-s2-immediate` | cache probe | 1 | 1 | 10 | **0** | 37,746 | 55 | **0** | 37,746 | 37,756 |
| `v4b-par-fork-1` | V4 re-run, warm | 1 | 1 | 10 | **0** | 37,746 | 46 | **0** | 37,746 | 37,756 |
| `v4b-par-fork-2` | V4 re-run, warm | 1 | 1 | 10 | **0** | 37,746 | 48 | **0** | 37,746 | 37,756 |
| `w3-summary` | self-compact W3 | 5 | 27 | 42 | 32,981 | 219,060 | 2,597 | 29,384 | 18,999 | 51,988 |
| `s3-mint` | mint S3 | 1 | 2 | 10 | 19,225 | 18,999 | 154 | 19,225 | 18,999 | 38,234 |
| `warm-w-fork-s2` | controlled probe | 1 | 3 | 10 | 18,750 | 18,999 | 95 | 18,750 | 18,999 | 37,759 |
| `clean-par-y` | controlled probe, parallel | 1 | 3 | 10 | 18,750 | 18,999 | 72 | 18,750 | 18,999 | 37,759 |
| `clean-par-z` | controlled probe, parallel | 1 | 3 | 10 | 18,750 | 18,999 | 69 | 18,750 | 18,999 | 37,759 |

(`s2-mint` — a discarded first attempt that wrongly re-injected `summary-1` alongside
`summary-2` — and the initial `PROBE-OK` smoke call are in the raw log but not the analysis.
**22 `claude -p` calls total**, budget was 25.)

### V3 — verdict **PASS**

*Oracle a: per-iteration `cache_creation` ≈ summary size (warm) or ≈ snapshot size (cold),
never ≈ transcript size.*

| iteration | fork of | first-turn `cache_creation` | vs snapshot (36–38k) | vs abandoned transcript (~50k) |
|---|---|---|---|---|
| 1 | S0 (warm) | **776** | 2 % | 1.5 % |
| 2 | S1 (warm) | **641** | 1.7 % | 1.3 % |
| 3 | S2 (cold) | **19,499** | 52 % | 39 % |

Cold worst case is bounded by the snapshot body (16,819 tok pack + ~2k of summaries), and
never approaches a transcript. **Refinement to the oracle's wording:** in the *warm* case
creation is not "≈ summary size" but ≈ **the new work prompt only** (641–776 tok) — the
summary's cost is paid once, at mint, and shows up as the permanent +725…+866 tok of
snapshot growth, not per fork. The design's cost model is right; its warm-case constant is
smaller than predicted.

*Oracle b: the final work product is correct.* `python3 -m pytest -q` at the tip
(`2bb6339`): **30 passed, 0 failed** across all four test files. Each iteration also passed
its own gate before committing (18, 23, 30 passed). WI-3 correctly used both `OrderError`
(WI-1) and `round_cents` (WI-2) — i.e. the chain really did carry the two earlier items'
contracts.

*Side oracle, unplanned but decisive:* **zero pack re-reads.** `grep -c orientation-pack` is
0 in all four worker sessions — no fork ever spent a turn re-orienting. The snapshot is
doing the job the raw transcript would otherwise be doing.

### CONTROL — chain vs. naive resume (the cost the chain avoids)

Both ran within ~1 minute of each other, same work item (WI-2), same prompt body, the naive
one operating on a copy of the repo at the post-WI-1 commit so both started from identical
code. Both succeeded (`23 passed`).

| metric | chain (`w2-work`, fork of S1) | naive (`ctl-w2-naive`, fork of W1) | ratio |
|---|---|---|---|
| first-turn `cache_creation` | **641** | **34,662** | **54.1×** |
| first-turn prefix (in+cc+cr) | 37,325 | 53,671 | 1.44× |
| whole call, billed tokens (in+cc+cr) | 347,378 | 870,415 | **2.51×** |
| assistant turns | 35 | 36 | 1.03× |
| mean billed context per turn | 42,681 | 58,017 | 1.36× |
| context at exit | 46,628 | 61,108 | 1.31× |

The turn counts are within 3 % of each other, so the 2.51× is prefix weight, not extra work.
Read the ratios as a family: 1.44× more prefix on turn one compounds into 2.51× over a
20-turn iteration, and the gap widens with every further iteration because the naive branch
carries iteration 1's transcript forever while the chain carries an 825-token summary.

### V4 — verdict **SPLIT: oracle (a) FAIL, oracle (b) PASS**

*Oracle a: both parallel forks show pure reuse (`cache_creation` ≈ 0) within TTL.* **FAIL.**
The real run — implementer and reviewer launched concurrently 16 s after S2 was minted —
each paid `cc ≈ 19,000` with `cr = 18,999`: the system+tools block hit, the snapshot body
did not. A controlled re-test (below) reproduced the miss and could not produce a genuine
hit on S2 at all.

> **Retraction, same run.** An intermediate re-run (`probeC`, `v4b-par-fork-1/2`) *did*
> measure `cache_creation = 0`, `cache_read = 37,746` on two simultaneous forks and briefly
> looked like a PASS. It was an artifact: those three calls reused `runs/probeA.txt`, so
> each was a **byte-identical replay of `probeA`'s entire request**, not a fork with new
> work. The tell is in the number — 37,746 + 10 input = 37,756 = `probeA`'s *full* context
> including its trailing user message, whereas a genuine fork can only read up to the
> *parent's* last message. Recorded here rather than deleted, because it is exactly the
> false-green an A/B on cache numbers invites: **a cache probe must vary the trailing
> message, or it measures request replay.**

Controlled re-test — one warming fork, then two concurrent forks, all three off S2 with
**distinct** trailing prompts, all within ~5 s of each other:

| call | first-turn `cc` | first-turn `cr` |
|---|---|---|
| `warm-w-fork-s2` | 18,750 | 18,999 |
| `clean-par-y` (parallel) | 18,750 | 18,999 |
| `clean-par-z` (parallel) | 18,750 | 18,999 |

Every fork paid the snapshot body once. **Parallel fan-out off a snapshot must be budgeted
as N × snapshot-body creation, not N × 0.** The "warm the cache with a throwaway fork first"
recipe that the artifact suggested does **not** work and is not recommended.

*Oracle b: reviewer independence.* **PASS.** The implementer's prompt carried a token
(`V4IMPL-Q7ZR3X`) it had to echo in its reply and never write to disk. Result: **1**
occurrence in the implementer's session JSON, **0** in the reviewer's, **0** anywhere in the
working tree, **0** in the git history. The reviewer, forked from the same S2, reached its
own verdict (`ACCEPT`, with a correct *unprompted* warning that `shipping.shipping_cost`
still uses banker's `round_money` and will mix with `round_cents` in WI-3) in **2 Bash
calls** (`git log`, `git show`) and 3 turns. Shared grounding, zero cross-contamination —
the reusable-snapshot half of §3's "snapshots are reusable and parallel-friendly" holds even
though the free-cache half did not.

### When the fork cache *did* hit — and the arithmetic that identifies the block

Two of the three chain links reused the snapshot body exactly, and the numbers are
unambiguous about what was reused:

| fork | parent's write | fork's `cr` | check |
|---|---|---|---|
| `w1-work` ← S0 (Δt 8 s) | `s0-orientation` cc 16,811 | 35,810 | 18,999 + 16,811 = **35,810** ✓ |
| `w2-work` ← S1 (Δt 25 s) | `s1-mint` cc 17,675 | 36,674 | 18,999 + 17,675 = **36,674** ✓ |
| `v4-impl` / `v4-reviewer` ← S2 (Δt 16 s) | `s2-mint-fix` cc 18,500 | 18,999 | 18,999 + 18,500 = 37,499 ✗ **missed** |

So the mechanism is real and exact — a fork reads precisely the block its parent's last
request wrote, system+tools plus body — but it was **not reproducible**: S2 and S3 never
served their body to any fork, at Δt from 16 s to 13 min, including immediately after a
fresh write, while S0 and S1 served theirs at Δt 8 s and 25 s. Every `cache_creation` in all
22 calls was reported in the **`ephemeral_1h`** bucket, so the advertised TTL explains
nothing here. The 18,999-token system+tools block hit unconditionally in all 22 calls; only
the conversation body was unreliable.

We could not isolate the cause inside this experiment's budget, and it is not worth
attributing to the CLI: one host, one account, one 10-minute window, shared cache pressure.
The operational conclusion does not depend on the cause:

1. **Budget the chain on bounded context, never on warm cache.** The unconditional,
   always-present win is that a fork's prefix is the snapshot (37k, growing ~800 tok per
   iteration) instead of the transcript (54k after one iteration, and unbounded after
   several). The cache is a bonus you may not get.
2. **Cold worst case is still bounded by the snapshot**, which is the whole point: 18.5–19.5k
   creation, ~37 % of the transcript the chain refused to carry, and it does not grow with
   the work done.
3. **Sequence fan-out immediately after minting anyway** — it costs nothing to try, and when
   it lands (as it did twice here) the fork's marginal creation drops to 641–776 tokens.
4. Any future cache A/B **must vary the trailing message** (see the retraction above).

### Caveats

- Synthetic, small (1,007 lines) and haiku-only. The token *ratios* transfer; the absolute
  sizes do not, and a 16.8k pack is ~8× smaller than a real dstdns orientation pack (E-002:
  42k curated), which makes cold-fork creation proportionally worse there, not better.
- Iterations were 15–23 turns. A real implementer is 300+ turns, where the naive branch's
  1.36× per-turn premium compounds much harder than the 2.51× measured here.
- The control ran once, by budget rule. Its first-turn numbers are exact; its whole-call
  number carries the noise of one agent's turn count (35 vs 36 — small, but n=1).
- `s2-mint` shows the one authoring mistake worth naming: the mint prompt must carry **only
  the current iteration's summary**. Re-injecting an earlier one duplicates content the
  parent snapshot already holds and inflates the chain. The corrected call is `s2-mint-fix`.
- The V4 oracle-(a) FAIL is a **negative result about the prompt cache on this host on this
  afternoon**, not about the snapshot-chain mechanism, which V3 shows working. Re-measure
  before designing anything (B46 `advance-chain`, nyxloomd fan-out) around free forks.
- Summary quality was not adversarially graded here (that is V5's job). The evidence that
  the summaries were sufficient is indirect but strong: zero pack re-reads and WI-3 using
  both predecessors' new APIs without being told them again.

### Totals

22 `claude -p` calls, **all `claude-haiku-4-5-20251001`**, no other model invoked:
input **772** · cache_creation **402,422** · cache_read **3,766,057** · output **32,982** ·
**$1.35**. (Of that, ~$0.09 is the cache-behaviour probing that produced — and then
retracted — the V4 oracle-(a) result.)

Artifacts (throwaway, outside both repos):
`…/scratchpad/v3-chain/` — `prompts/` (the seven prompt files), `runs/*.json` (raw results),
`runs/usage.tsv`, `runs/chain.env` (the snapshot chain S0→S3 + W1→W3), `notes/summary-{1,2,3}.md`,
`notes/orientation-pack.md`, `lib.sh` (the `call` wrapper that enforces the four invariants).

## V1 addendum — first live checkpoint (P113 code reviewer, 2026-08-20)

**Setup.** V1 (design doc §5): "(a) full cycle on a real worker: threshold → checkpoint →
controller compacts with the agent's own prompt → resume → next work item." First live
attempt, run mid-review against the P113 code reviewer — an **Agent-tool subagent**
(`acf84a863aaed19c8`), not a top-level CLI session — at the E-008 ~120k arm point, phase
boundary = the reviewer's own blind-phase/reconcile-phase split.

**Datum** (`scratchpad/v1-live-attempt-p113-reviewer.md`, quoted in full — it is small):
> Checkpoint taken at end of blind phase: ~112 assistant turns, last call cache_read=156,694 /
> cache_creation=2,967 / output=2,820 (>120k arm point; phase boundary = reviewer's coherent
> boundary per E-008). Agent wrote retention prompt (401 words) + standalone scratch findings
> file as instructed. `claude -p --resume acf84a863aaed19c8 --model opus --output-format json
> "/compact <prompt>"` → exit 1 in 1.9s: "--resume requires a valid session ID or session
> title when used with --print ... not a UUID and does not match any session title." ⇒
> Agent-tool subagents are NOT directly CLI-resumable; pattern (a) needs either the probe's
> JSONL→UUID-session transformation or a top-level CLI child (`claude -p --session-id`) as the
> worker. Fallback executed: SendMessage "PHASE 2" (in-place resume, same mechanism as
> P110–P112). Measure phase-2 per-call cache_read (expect ~157k+ per call) from the JSONL
> after completion → the cost the compaction would have removed.

**Measured** (`jsonl-metrics.py simulate`, pinning the checkpoint at the nearest call to the
datum's numbers — 131 calls total in the frozen transcript):

- `simulate --checkpoints 85` lands on **call 111** (85% of 131 calls), `ckpt_context =
  159,663`. The datum's `cache_read 156,694 + cache_creation 2,967 = 159,661`, within 2 tokens
  of the tool's context figure — this pins call 111 as the actual checkpoint call (the tool
  has no fixed-index checkpoint flag, so the nearest-percentage bucket is the closest
  available match; the 2-token gap is the call's small `input` component).
- **Phase 2 = calls 112–131, 20 calls** (not the "9" the dstdns ledger self-reports — see
  discrepancy note below), holding in a **158k–188k context band** the entire way (curve's
  final context = 187,638) — an ~18% climb over 20 calls, far flatter than phase 1's climb
  from 41k→160k over 111 calls. No subcommand exposes a raw per-call `cache_read` column
  without dumping transcript content (forbidden by this task), so per-call context is used as
  the closest measurable proxy: at the transcript's overall 0.944 cache-hit ratio, every
  phase-2 call re-paid roughly **156k–186k tokens of prior context as cache_read**, call after
  call, for 20 consecutive calls.
- Modeled compacted-resume at the pinned checkpoint (`simulate --checkpoints 85`, three brief
  sizes):

  | brief size | actual (real, uncompacted) run cost | modeled restart cost | savings |
  |---|---:|---:|---:|
  | 25k | 2,827,420.7 | 2,037,302.1 | 27.94% |
  | 50k | 2,827,420.7 | 2,118,552.1 | 25.07% |
  | 100k | 2,827,420.7 | 2,281,052.1 | 19.32% |

  Real phase 2 (as actually run, in-place resume, no compaction) cost **~790k more
  normalized-cost units** than a modeled compact-and-resume at the same call would have, under
  the 25k-brief assumption — 27.94% of the whole run's cost, consistent with E-006/E-007's
  finding that checkpointing pays for itself even this late (85% through a run).

**Mechanism finding, stated plainly (per the task instruction).** Agent-tool subagent ids
(`acf84a863aaed19c8`) are **not** CLI session UUIDs, so `claude -p --resume <sid> "/compact
..."` cannot target one directly — the CLI rejected it outright. Pattern (a) as designed
works only against a real CLI session id. Making it work for a subagent needs **either** the
mechanism probe's JSONL→UUID-session transformation **or** running the worker as a top-level
CLI child (`claude -p --session-id ...`) instead of an Agent-tool subagent — **which of the
two is correct is decided by the separate, pending mechanism probe, not here.** The fallback
actually executed was the interim SendMessage-based in-place resume (the same mechanism
P110–P112 used), which is what the measurements above are computed from — this is a
measurement of the *fallback's* cost, not of pattern (a) succeeding.

**Discrepancy worth recording.** dstdns ledger `D-140` self-reports "phase 2 cost 9 calls on a
~157k+ cached prefix" (`dstdns@c8b05a5e`); the measured transcript shows **20** assistant
calls carrying a `usage` block between the checkpoint (call 111) and the final call (131). The
"9" and the measured "20" do not reconcile from the artifacts available here. A plausible
(unverified) read: the self-report's "calls" meant higher-level conversational turns, while
`jsonl-metrics` counts every assistant message carrying a `usage` block, and some turns can
span more than one such message — but this is speculation, not a finding, and should not be
treated as reconciled.

### V1 addendum 2 — mechanism probe result (2026-08-20, controller-run after the probe agent stalled)

Question: can the controller compact+resume an **Agent-tool subagent** headlessly (pattern a)?
Subject: completed subagent `a48350cc76449f600` (P113 carve reviewer), transcript
`~/.claude/projects/<proj>/<session>/subagents/agent-<id>.jsonl` (240 lines, `isSidechain:true`,
`sessionId` = the PARENT session's UUID, plus an `agentId` field).

| attempt | command | result |
|---|---|---|
| 1 | `claude -p --resume a48350cc76449f600 …` | exit 1 — "not a UUID and does not match any session title" |
| 2 | copy → `<projects>/<new-uuid>.jsonl` (symlink), resume `<new-uuid>` | exit 1 — "No conversation found with session ID" |
| 3 | same, with every line's `sessionId` rewritten to `<new-uuid>` via jq | exit 1 — "No conversation found" (re-run by the controller with `timeout -k 5 240 … < /dev/null`; the probe agent's own attempt hung the harness Bash call and never returned — the agent was stopped) |

**Conclusion:** `--resume` resolves sessions through an index the raw JSONL does not populate
(the `isSidechain`/`agentId` shape, a missing first-line session record, or a sessions index
elsewhere — not investigated further tonight). Therefore **pattern (a) is NOT available for
Agent-tool subagents by file transformation**; it IS available for **CLI-child workers** started
with `claude -p --session-id <uuid>` (V3/V4 above ran 22 such sessions with `--resume` and
`--fork-session` behaving correctly headlessly). Operational consequence: a worker that should
checkpoint→compact→resume must be launched as a CLI child (E-001/B1 style), not via the Agent
tool; Agent-tool subagents keep the successor-brief respawn mechanism (E-007). The P113 reviewer
(V1 addendum 1) therefore stands as the baseline datum, not a compaction datum.
Side effect to watch: a symlinked fake session in the projects dir shows up in the operator's
`/resume` list — removed after the probe.

### V1 addendum 3 — FIRST LIVE compact+resume DATUM on a CLI-child implementer (dstdns P116, 2026-08-21)

Subject: dstdns P116 `landscape-plane1` implementer, run as `claude -p --session-id 298cee08-… --model opus
--effort xhigh --dangerously-skip-permissions --output-format json` from the controller session (the
pattern-(a) path V1 addendum 2 said was required). Protocol in the dispatch prompt: ARM ~120k/~60 calls, CUT
at a green boundary, write `reports/dstdns-P116-BRIEF.md` (715 words) + a self-authored
`reports/dstdns-P116-COMPACT.md` (464 words), commit, exit with `CHECKPOINT <hash>`.

Measured (session JSONL `298cee08-…jsonl`, 155 assistant usages):
- Run 1 (fresh → checkpoint): 82 turns, 17 min, $9.79, 142 assistant usages; cache_read climbed
  15.9k → **234.6k** at the cut; the cut landed on a green boundary (feature commit `dstdns@27c87252`,
  1,682 lines, control gate recorded) — the E-008 rule held in practice.
- Compact step: `claude -p --resume <sid> "/compact <COMPACT.md>"` — **exit 0, 0 turns, 133 s**, compact
  boundary written into the session file. (Only stderr: the benign "no stdin data in 3s" warning — pass
  `< /dev/null`.)
- Run 3 (resume with "continue from the brief" + the controller's D-151 decisions, 1.1k words): first
  assistant usage **44.2k cache_creation + 15.9k cache_read (~60k)** vs 236.7k on the last pre-compact
  turn → **~4× context reduction**; the 15.9k cache_read is the fixed prefix (system prompt/tools), the
  44.2k is summary + resume prompt + first reads, all new creation (as V4(a) predicted — a resume with a
  distinct prompt re-pays the body).
- Cost shape: the compact itself is one summarisation call over 235k (not yet priced separately from
  run 3's envelope — TODO read `total_cost_usd` of run2 when the JSON carries it; the run2 envelope
  reported usage zeros).

Comparison points: E-007's successor-brief respawn for P110–P112 cost a fresh ~50–130k orientation per
successor; this compaction resumed at ~60k with ZERO re-orientation tool calls before productive work
(first tool call after resume was an Edit, not a Read — verify in the run-3 transcript at write-up time).
Open: (1) does the compacted agent re-derive facts the summary dropped (watch for re-reads of pack
sections); (2) second checkpoint on the same session — does a chained compact degrade; (3) price the
compact call. Controller-side procedure that worked: Monitor on the child's pid (a nohup'd CLI child does
not notify the harness), verdict from `jq 'last'` on the `--output-format json` array, usage from the
session JSONL, never the transcript.

### V1 addendum 4 — three CHAINED headless compactions on the same CLI-child implementer (dstdns P116, 2026-08-21, `dstdns@ff73f6ea`)

Follow-on to addendum 3, same subject and same session (`298cee08-5dd7-43ed-a060-2bd275f3562a`),
carried to completion. Where addendum 3 covered checkpoint 1 (run 1 → compact → run 3), this
addendum adds checkpoints 2 and 3 and the final DONE, so the datum is now "does a *chain* of
headless compactions on one CLI child hold up" rather than "does one compaction work."

**Per-run table** (`--output-format json`, `jq 'last'`; turns/duration/cost are per-run, not
cumulative — each `claude -p --resume <sid> …` call reports only its own turns):

| run | role | turns | wall | cost | cache_read (cumulative, session-lifetime) | peak context at cut (cache_read+cache_creation of last call) |
|---|---|---:|---:|---:|---:|---:|
| 1 | fresh → checkpoint 1 | 82 | 16.9 min | $9.79 | 11.5M | 234,585 + 2,074 = **236,659** |
| 3 | resume 1 → checkpoint 2 | 134 | 24.2 min | $14.83 | 21.1M | 242,995 + 2,393 = **245,388** |
| 5 | resume 2 → checkpoint 3 | 94 | 26.6 min | $12.65 | 15.7M | 246,167 + 6,356 = **252,523** |
| 7 | resume 3 → DONE | 174 | 46.1 min | $22.10 | 32.2M | 296,693 + 1,032 = **297,725** (final) |

(`cache_read` per run is the CLI's own cumulative-session counter, not new tokens paid in that
run — it grows because the whole prior transcript, chain-compacted or not, is still summed by
the wrapper's own accounting. The context-growth curve below is the metric that isolates each
run's own cost.)

Session-wide, measured directly from the JSONL with `jsonl-metrics.py curve` (not the child's
own §6 telemetry, which was written mid-run-7 at 766 calls — the transcript grew to 795 calls by
the time the DONE result landed a few turns later):

```
calls=795  wall_min=126.7  final_context=297,727  cache_hit_ratio=0.987
totals: cache_read=130,536,022  cache_creation=1,716,957  output=714,226
growth shape: tail-heavy (knee at call 350 = 44.0% of run, +61,622 tok jump)
```

**Peak context before each cut, and first-turn cost after each resume**, from the raw
per-message `usage` blocks around the three `compact_boundary` markers (lines 457, 1189, 1736 of
2691):

| cut | peak before cut (cache_read + cache_creation, last real call of that segment) | first real call after resume (cache_creation + cache_read) | reduction |
|---|---:|---:|---:|
| checkpoint 1 → compact 1 | 234,585 + 2,074 = 236,659 | 44,189 + 15,910 = **60,099** | **3.9×** |
| checkpoint 2 → compact 2 | 242,995 + 2,393 = 245,388 | 42,203 + 19,417 = **61,620** | **4.0×** |
| checkpoint 3 → compact 3 | 246,167 + 6,356 = 252,523 | 43,243 + 15,910 = **59,153** | **4.3×** |

The reduction ratio is stable across all three cuts (3.9×–4.3×) and so is the absolute
post-resume floor (~59–62k every time) — the chain shows no drift or degradation over three
consecutive compactions. The 15.9–19.4k `cache_read` component is the fixed system-prompt/tools
prefix; the 42–44k `cache_creation` component is the resume prompt + brief + first reads, all
paid fresh each time exactly as V4(a) predicted for a distinct-prompt fork/resume.

**The three compact calls** (`claude -p --resume <sid> "/compact <compactN.md>"`, all
`< /dev/null` per addendum 3's fix — the bare form printed a benign "no stdin data" warning on
the first call only):

| compact | exit | turns | wall | cost |
|---|---:|---:|---:|---:|
| 1 (`run2-compact.json`) | 0 | 0 | 133 s | $1.68 |
| 2 (`run4-compact.json`) | 0 | 0 | 177 s | $1.85 |
| 3 (`run6-compact.json`) | 0 | 0 | 148 s | $1.85 |

All three landed in the 130–180 s band the task memo predicted, all `is_error:false`, all
recorded a `compact_boundary` system line in the JSONL. Compact cost is essentially flat
(~$1.7–1.9) regardless of the summarised segment's own size (82–174 turns) — consistent with
compaction cost being dominated by one summarisation call over the pre-cut context rather than
by the segment's turn count.

**Session total**: 4 work runs + 3 compacts = 484 work turns, **$64.75** ($9.79+$14.83+$12.65+
$22.10 work, $1.68+$1.85+$1.85 compact), ~127 wall-minutes, ending DONE at `dstdns@ff73f6ea`.

**Comparison to the successor-brief respawn pattern (P117, same day, qualitative only — no
comparable per-run JSON was captured for P117).** P117's `retired-legacy-move` implementer ran
as an Agent-tool subagent (pattern (a) unavailable per addendum 2) and checkpointed **once**, at
406 calls / ~445k tokens, then was **respawned as a fresh successor** seeded with its brief
(dstdns memory, `dstdns-p106-carve-review-inflight.md` 2026-08-21 ~04:50Z entry) rather than
compacted in place. P116 never let context exceed ~253k before cutting, chained three
compactions, and stayed on one session id throughout. Not head-to-head (different packages,
sizes, and P117 lacks a per-call cost breakdown), but the contrast holds: P117's single late
checkpoint (445k) is 1.8× P116's highest pre-cut peak (253k); P116 is a cheaper way to get the
multi-checkpoint benefit E-007 already favors, without the fresh ~50–130k re-orientation a
successor respawn pays per hop.

**What the summaries lost, and did the retention prompts improve — from the child's own §6
telemetry** (`nyxloom-trove/reports/dstdns-P116-REPORT.md`, quoted):

> **What the summary lost: nothing load-bearing, once.** Each brief named the seams by
> `file:line` without quoting them, which is the deliberate trade — the successor pays a
> handful of targeted re-reads instead of carrying kilobytes of source. Measured cost of resume
> 3: **6 orientation calls**… before the first productive action.
>
> **What it did lose — and this is the honest finding of this section — is skepticism about its
> own earlier claims.** A summary presents prior conclusions as settled fact. Commit 3's LOG
> said the O6 residue sweep was EMPTY; that sentence survived compaction 3 into my working
> belief unchallenged, and it was wrong (§ O6)… **a claim carried across a compaction is a claim
> that has not been re-measured**, and every oracle sentence in the final commit was re-run
> against the final tree rather than copied forward.

The three self-authored prompts (`compact1.md` 464 words, `compact2.md` 598 words, `compact3.md`
567 words) kept the same skeleton every time (identity/worktree/branch/tip, KEEP/DROP, next
action) and grew modestly as the accumulated LOG/oracle surface grew (compact2 added
`config_endpoints.py` routes and the config-plane test harness; compact3 added the O9 clause and
GUIDE §3) — no sign of prompt bloat or degradation across the chain, and the resume-cost table
above (59–62k, flat) confirms it operationally: a third chained compaction cost no more to
resume from than the first.

**Operational recipe, unchanged from addendum 3 and reconfirmed three times:** launch with
`nohup claude -p --session-id <uuid> --model opus --effort xhigh --dangerously-skip-permissions
--output-format json > run.json 2> run.err &`, monitor the pid (a nohup'd child does not notify
the harness); require the agent's first output line at checkpoint to be a bare verdict token
(`CHECKPOINT <hash>` / `DONE <hash>` / `BLOCKED …`), read as a separate step; on `CHECKPOINT`,
run the compact with the agent's own `reports/*-COMPACT.md` as the `/compact` argument and
`< /dev/null` piped in (133–177 s, three for three); resume with a short controller prompt
("continue from the brief" + any new decisions) via the same `--resume <sid>`. Read turn/cost/
usage from the run's own JSON array's last element, never `TaskOutput` or a raw transcript read.

**Open for V5.** Can the CONTROLLER's own top-level session run this same loop on itself — a
self-authored compact prompt written at its own checkpoint, executed by an external helper
process that calls `claude -p --resume <controller-sid> "/compact …"` and then hands control
back — rather than the controller manually invoking `/compact` and re-typing a resume prompt
each time? The mechanism this addendum validates (self-authored prompt, headless `/compact`,
`--resume` back into the same session id) is identical in shape to the controller's own
interactive `/compact`; the open question is only whether an *external* trigger (a wakeup, a
watchdog, or a helper script watching the controller's own context size) can drive it instead of
the operator, which would turn "Checkpoint hand-back" (CLAUDE.md) from an operator-triggered
manual step into the same loop this addendum ran on a worker. Not designed here — B46 territory.
