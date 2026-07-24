# Session log — benchmark-ingest fan-out (2026-07-24)

**Controller:** Claude Opus 4.8 (1M ctx), running as an autonomous controller.
**Mandate (operator, 2026-07-23→24):** for ~4h, keep developing the agreed benchmark-ingest /
capability-routing features at my discretion; synchronize dispatch to **luna (via codex)** and
**deepseek-v4-flash (direct-vs-reasonix A/B)**; review + gate + merge each; **max fan-out while
guaranteeing (a) non-overlapping files and (b) no guessing of contracts**; agents must be
**resumable** and I must **detect hangs**.

**How to read this doc:** it is both the review log the operator asked for AND my live fleet
registry (so the fan-out survives a context compaction). Newest milestones appended at the
bottom. The **Fleet registry** table is the ground truth for what is dispatched / resumable.

---

## Standing constraints & doctrine in force
- **Gate = authoritative SOLO run** in `tester-unified:local` (built from `vbpub/tester-unified/
  Dockerfile`): `coverage run pytest` + `nyxloom.coverage_gate --base main` needs **suite-green +
  100% diff-coverage of changed lines**. Concurrent gates OOM (SIGKILL-137) → gates run one at a time.
- **Impl agents commit only; exit 0 ≠ ship** (codex always exits 0). The controller reviews +
  gates + merges `--no-ff`. Cheap-model output is re-verified in `tester-unified`, never trusted
  from the agent's own sandbox.
- **`config.py` / `storage.py` are FROZEN CORE** — additive fields go on benchmark/capability
  records, never on config schemas.
- **Effort doctrine:** default **high** (never low, rarely medium, NOT max — max tunnel-visions).
  **Complexity scales the MODEL BAND** (luna→terra→sol→fable), not the effort. Selection is
  multi-dimensional, benchmarks inform-not-decide.
- **Hang detection:** every dispatch is wrapped in `timeout 1200` (20 min hard cap; comparable
  luna packages finish in <5 min, so 20 min of silence is a definitive hang). A timeout converts a
  hang into a *bounded, notifying* completion → I inspect → resume the session (bounded retries) or
  escalate the band. **Resume handles:** codex `exec resume <id>|--last`; reasonix `run -c --dir
  <wt>` / `--resume <path>`; opencode `run -c --dir <wt>` / `-s <id>`.

---

## Milestone 1 — AA-source upgrade MERGED (main `52b9cce8`)
**What:** repointed `ArtificialAnalysisSource` from the thin `/language/models/free` (200 models,
no eval fields) to the rich **`/data/llms/models`** (580 models, per-eval fields). Parses **effort
from the slug suffix** (`{low,medium,high,xhigh,max,minimal}`; `-lite` is NOT an effort → stays in
model_id), maps `intelligence←aa_int_idx`, `coding←aa_coding_idx`, `agentic←mean(available of
terminalbench_hard,tau2)×100`, and preserves every per-eval score in `raw`. Realizes plan decision
**D-B9**. Built by **luna@high** (codex, session 019f9164); controller coverage fixup; SOLO gate
**32/32 diff-cov (100%)**, suite green.

**Insights:**
- *The gate is the arbiter, not the agent's "35 passed".* luna ran pytest in its own codex
  sandbox — a third environment carrying neither the devcontainer nor the app pins. Only
  `tester-unified` (FROM dstdns-app-base) is a ship-signal. `${PIPESTATUS[0]}` capture matters: a
  bare `pytest | tail` masks a non-zero exit (a known false-green trap here).
- *Diff-cov is a `--base main` ratchet, not an absolute floor.* It demands 100% only on lines the
  branch changed, so pre-existing uncovered lines don't block — but every new/edited line must be
  hit. That's what forces edge branches to be tested.
- *The one gap luna left* was `evaluations = {}` (the branch for a model with a valid slug but NO
  `evaluations` block at all — most of AA's 580 models). Its `no-signal` fixture had an evaluations
  dict, so it covered the "no axis → skip" path but never the non-dict reassignment. A one-fixture
  controller fixup (a `no-evals` model) closed 96.9%→100%. Fixing a *coverage* gap inline beats
  re-dispatching a whole codex session; the agent keeps authorship of the impl commit.

## Milestone 2 — harness contracts locked (deepseek direct-vs-reasonix)
- **DIRECT** = opencode's built-in **DeepSeek provider** (→ `api.deepseek.com`, `DEEPSEEK_API_KEY`):
  `opencode run -m deepseek/deepseek-v4-flash --variant high --dir <wt> --auto`. Cost: `opencode stats`.
- **REASONIX** = its `deepseek-flash-high` provider (same model, same endpoint, same key):
  `reasonix run --model deepseek-flash-high --dir <wt> --metrics <json>` (task via stdin). Cost: `--metrics`.
- **auto vs high vs max?** The three `deepseek-flash-*` providers are identical except the `effort`
  field. Chose **high** — `auto` cedes the very knob we're studying; `max` tunnel-visions. Holding
  model+endpoint+effort constant means the A/B **isolates the harness**, which is exactly what the
  operator's parallel OpenRouter providers (config.toml:180-191) were set up to measure.

---

## Fleet registry (live — resumable dispatch state)

Wave 1 (dispatching now). All three touch **disjoint files**; the two P2 builds are the same spec
to two harnesses (merge the winner, discard the loser).

| Job (bg id) | Package | Files (scope) | Harness / model @ effort | Worktree · branch | Resume handle |
|-----|---------|---------------|--------------------------|-------------------|--------|
| `b7273o93v` | **D-B7** unrated-surfacing | `capability_map.py` + test | **luna@high** (codex) | `.worktrees/db7-unrated` · `feat/db7-unrated` | `codex exec resume 019f917c-b9ae-7b31-9c46-8341b5de0017 - < followup` |
| `bry01jm8p` | **P2** accumulate-store | `benchmark_store.py`(new) + test | **deepseek-flash DIRECT** (opencode, `--variant high`) | `.worktrees/p2-store-opencode` · `feat/p2-store-opencode` | `opencode run -c --dir .worktrees/p2-store-opencode` |
| `b03431txu` | **P2** accumulate-store (A/B twin) | `benchmark_store.py`(new) + test | **deepseek-flash via REASONIX** (`deepseek-flash-high`) | `.worktrees/p2-store-reasonix` · `feat/p2-store-reasonix` | `reasonix run -c --dir .worktrees/p2-store-reasonix` |

All three launched cleanly (codex effort=high confirmed; opencode + reasonix both reading context
and implementing). Each wrapped in `timeout 1200`. Early cost signal: reasonix reports strong prompt-
cache reuse inline (16256 cached / 250 new on step 2).

Merge order (serial on `main`, all conflict-free): D-B7 (capability_map) and the winning P2
(benchmark_store) are disjoint, so either order is safe.

---

## Milestone 3 — Wave 1 built; deepseek DIRECT-vs-REASONIX A/B (P2)
All three agents produced code. Outcomes:
- **D-B7 / luna@high (codex):** clean commit `f923dad7`, no BLOCKED, `capability_map.py` +50 /
  test +116. Gating.
- **P2 / deepseek-DIRECT (opencode):** clean commit `1f99b722` (store 154 + test 213 lines).
  **Self-committed to the worktree branch with no issue.** Gating.
- **P2 / deepseek-REASONIX:** wrote the code (store 270 + test 312 lines) but **could NOT
  self-commit** — see finding. Controller salvaged + committed it (`9db99c08`) for a fair gate.

**A/B FINDINGS (deepseek-v4-flash @ high, same model/endpoint/effort — harness isolated):**
| Dimension | opencode (direct) | reasonix |
|---|---|---|
| Worktree git commit | ✅ clean (`1f99b722`) | ❌ **sandbox jails writes to workspace_root; a worktree's `.git` is external → could not write objects; hand-rolled a fake `.git-obj-v2` store, never committed** |
| Per-run cost telemetry | ❌ `opencode stats` is aggregate-only (51 sess / 13 d / $5.97) | ✅ `--metrics` → per-run JSON |
| This run's cost | not isolable from stats | **¥0.288 (~$0.04)**, 78 steps, 6.08M prompt tok (**98.2% cache-hit**), 29.6k out |
| Output size | 154+213 lines | 270+312 lines (more verbose/thorough — gate TBD) |
| Effort knob | `--variant high` | provider `deepseek-flash-high` (effort=high) |

**Takeaways for routing/dispatch doctrine:**
1. **reasonix's worktree-git wall is a CONFIG fix, NOT a disqualifier (operator, 2026-07-24).**
   Widened `~/.reasonix/config.toml [sandbox] allow_write` to include `/workspaces/vbpub/.git` (the
   worktree's external git dir) — reasonix can now self-commit inside a worktree. To be re-validated
   on the next reasonix dispatch. Its implementation quality was never in question; only the sandbox
   default. So reasonix stays a first-class dispatch target alongside opencode/codex.
2. **Cache-hit economics confirmed:** reasonix's 78-step loop still cost only ~$0.04 because
   deepseek-direct cache hits are ~free — cost is driven by cache-miss + output, not raw token count.
3. Reasonix's `--metrics` per-run cost/cache JSON is the better cost-routing signal.

## Deferred controller work (while gates run)
- **Prune:** 94 branches are ancestors of `main`; deferred until the D-B7 gate frees the git refs
  (a `branch -d` sweep mid-gate could race the gate's `--base main` diff). Will exclude the 3 active
  Wave-1 branches, `main`, and `.claude/worktrees/*` agent checkouts; safe flags (`worktree remove`
  without `--force`, `branch -d`) mean no uncommitted work can be lost.
- **B26 backlog item** added (per-handoff processing-trace UI feature) — uncommitted on `main`
  checkout, will commit with the merge batch.
- **Scale SEAL (labs.scale.com)** exploration dispatched to a cheap scout agent (running).

## Milestone 4 — D-B7 merged; worktree prune; Scale SEAL scouted
- **D-B7 merged** to `main` (`fa06f2bd`) — reviewed (faithful, non-hollow tests incl. byte-identical
  idempotence + compares_to-miss grace), gate 25/25 diff-cov, suite green.
- **Reasonix allow_write fix applied** (see amended finding above) — reasonix re-enabled for worktrees.
- **Worktree prune (merge-status-checked):** 87 removed, 22 kept (unmerged/active), 2 refused-dirty
  (`bench/p51-opt-pro-high`, `feat/nyxloom-P47-carve-dispatch-mutex` — left intact for manual review),
  4 excluded (active Wave-1 + main). ~116 → 27 worktrees.
- **Scale SEAL (labs.scale.com) scout verdict → WORTH a source (backlog B27).** NOT a dead-end like
  tbench.ai. Adds two things AA-direct + DeepSWE lack: **SWE-Bench Pro** (non-saturated coding
  resolve-rate, public ~59-62% vs SWE-Verified saturation) and **MCP Atlas** (MCP tool-use/discovery
  vs real Dockerized servers — directly on our agentic/ops axis). Ingest is cheap: **plain `curl` GET
  + regex-extract the embedded `self.__next_f.push` RSC JSON — no Playwright**, then regex-split the
  free-text effort suffix from the model name + a manual company/effort normalization table. Live
  rows through 2026-07, frontier model coverage. Caveats: undocumented internal Next.js serialization
  (build defensively, fail-loud on drift); SKIP the stale Legacy bucket + off-domain Frontier/Safety.
- **B26 amended intent:** the per-handoff trace UI should surface **per-agent cost/cache/step
  metrics** (reasonix `--metrics`-style) on drilldown — those numbers are worth showing on their own.

## Milestone 5 — reasonix validated as a first-class worktree dispatch target
Scale SEAL (B27) was reasonix's first run AFTER the `allow_write` fix, and it exercised the full loop:
- **Self-commit ✅** (`d3f20847`) — the fix works; **26 steps** vs the earlier stuck P2's 78, ~$0.013.
- **First gate RED** (diff-cov 84.7%; 11 defensive branches — except/continue, validation raises,
  skip-continues, and the `_fetch_text` body — uncovered). reasonix's own "42 tests pass" self-report
  did NOT catch the coverage floor: exactly why the controller re-gates and never trusts self-reports.
- **Resume ✅** — `reasonix run -c --dir <wt>` restored the prior session (34k/57k **cache-hit** tokens
  = warm context, not a cold start), added 7 targeted tests (`82aa7c55`), ~$0.011, 19 steps.
- **Net:** reasonix self-committed → gate feedback → resumed → fixed its own coverage, all at
  deepseek-direct cache-hit cost. Resumable-dispatch + hang-bounding (timeout wrapper) both validated.

## Open design decision — findings channel (surfaced, NOT guessed)
The system→user **findings** gap (insights that are neither task events nor blocking decisions —
e.g. "nemotron-ultra free ≈ pro", "deepseek-flash reaches X on coding at ~1/10th the cost") runs into
a real constraint: **`notify.py`'s SPEC §13 injection boundary** forbids interpolating model-authored
prose into a notification PUSH (typed fields + fixed templates only). So a findings *push* cannot
carry a free-text insight. Because the finding's user-facing shape is a product contract, I'm
surfacing it rather than dispatching a guessed taxonomy. Options:
- **A — typed findings:** a `FINDING` event + a `finding_kind` registry (e.g. `model_near_equivalent`
  {model_a,model_b,metric,score_a,score_b}) → fixed-template §13-safe pushes. Pushable, but only
  *enumerated* kinds; no novel free-text.
- **B — dashboard free-text + typed nudge:** findings render as free-text cards in the dashboard
  (where §13's push boundary doesn't apply, like the redacted `blocked_reason` preview); a typed
  one-line push ("N new findings") links there. Rich insight in the UI, §13-safe push.
- **C — both:** typed registry for pushable findings + a generic free-text card for everything else.

**Recommendation: B (or C).** Free-text findings belong in the dashboard, with a typed nudge push —
and this composes with **B26** (per-handoff trace drilldown): a finding is just another dashboard
artifact. Deferred pending an operator call on A/B/C. (Not dispatched — no guessing the contract.)

## Milestone 6 — P4a activated the catalog; live smoke test caught a real bug
- **P4a merged** (`c68ef02e`): `DEFAULT_SOURCES` = artificial-analysis + scale-seal + deepswe-v1.1
  (controller-amended in the DeepSWE per-effort seed with a module-relative path), + a
  `capability-map refresh [--dry-run]` CLI verb. reasonix@high + controller fixups; gate 23/23.
- **Live `capability-map refresh --dry-run` smoke test → assembled 614 records** (AA-direct keyed +
  DeepSWE static, live). BUT **scale-seal yielded 0 rows** and `fetch_all` correctly isolated it
  (fail-safe: the other sources still produced 614, nothing corrupted).
- **Root cause (a spec bug of mine, not reasonix's):** on the live pages the row array is nested in a
  React **component tree** (`NN:["$","div",null,{...}]`), not a bare `NN:[{...}]` chunk. My specced
  extraction used a greedy `[{.*}]` regex over `split(":",1)[1]`, which grabbed an invalid fragment.
  The SOLO gate passed because the tests used clean-array fixtures — it validates extraction *logic*,
  never the live *fetch* (which is monkeypatched). Fix `aaded595`: `_extract_scale_rows` now tries
  `JSONDecoder.raw_decode` at every `[{` and returns the first array whose first element is a row
  dict; also strips a trailing `*` footnote marker. Verified live: 28 / 25 / 14 rows.

**LESSON (candidate memory):** the diff-cov gate and a live smoke test are COMPLEMENTARY, not
redundant — the gate proves the parser is correct on fixtures; a `--dry-run` against the real
endpoints proves the *fetch + real-world shape* works. For any source that scrapes an undocumented
external structure, a live smoke test after merge is mandatory; the gate cannot see shape drift.

## Merge log
| main commit | package | builder | gate |
|---|---|---|---|
| `52b9cce8` | AA source → /data/llms/models + effort/agentic (D-B9) | luna@high + controller fixup | 32/32 diff-cov, suite green |
| `fa06f2bd` | D-B7 unrated-model surfacing (capability_map) | luna@high | 25/25 diff-cov, suite green |
| `0e795cdd` | P2 versioned accumulate-store (benchmark_store) | deepseek-flash@high via **opencode**; reasonix A/B twin also passed | 77/77 diff-cov, suite green |
| `cabdc69a` | B27 Scale SEAL source (benchmark_sources) | deepseek-flash@high via **reasonix** (self-commit + coverage-resume) | 72/72 diff-cov, suite green |
| `c68ef02e` | P4a activate catalog: DEFAULT_SOURCES + `capability-map refresh` CLI + DeepSWE wiring | reasonix@high + controller | 23/23 diff-cov, suite green |
| `d54b8ae1` | Scale SEAL live-extraction fix (RSC component tree + `*` strip) | controller (spec-bug fix) | 6/6 diff-cov, suite green |
| `51906cd6` | P4b accumulate-store threaded through refresh (D-B3 persistence) | luna@high | 12/12 diff-cov, suite green |

**✅ EPIC COMPLETE + LIVE (2026-07-24) — 7 packages merged.** `capability-map refresh --dry-run`
against real endpoints → **681 capability records, zero source errors** (AA-direct 614 keyed +
Scale SEAL 67 [28 mcp-atlas + 25 swe-pro-public + 14 swe-pro-private] + DeepSWE per-effort static).
The full pipeline — multi-source ingest → version/effort-keyed accumulate-store (never-delete) →
capability catalog with unrated-model surfacing → `capability-map refresh` CLI — is functionally
complete and validated on live data. A REAL `capability-map refresh` (no --dry-run) now persists
both `routes.toml` (the capability-map managed block) AND `benchmark-store.toml` (accumulated
history); that write is a deliberate deploy op, left for the operator. Remaining (follow-on, none
blocking): dashboard effort/version/unrated columns (B19/B26); findings-channel (needs operator
A/B/C decision above); scheduled auto-refresh (B20); optional openrouter/pwmcp-scrape sources.

**A/B RESOLVED:** deepseek-v4-flash @ high produced a gate-passing, 100%-diff-covered P2 via BOTH
harnesses (opencode 77/77, reasonix 84/84) — the *model* is clearly capable of mergeable work on a
well-contracted mechanical package. Merged opencode's (leaner: 154+213 vs 270+312 lines; self-committed
out-of-box). Both stay first-class dispatch targets. **Wave 1 complete** (AA + D-B7 + P2 all on main).

**Wave 2 in flight:** Scale SEAL source (B27) → reasonix (`btdx8uvkn`), validating the allow_write fix
on fresh work. Spec is fixture-based (extraction recipe verified against 28 live mcp_atlas rows).
