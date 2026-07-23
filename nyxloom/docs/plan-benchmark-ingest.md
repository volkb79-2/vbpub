# Plan — benchmark-ingest subsystem (capability-routing data foundation)

Status: DRAFT · 2026-07-23 · routing epic (D-R12/D-R13, feeds F014 capability catalog)
Owner-decisions locked with operator this session; see "Decisions" below.

Worktree: create a git worktree for branch `feat/benchmark-ingest` from local main at
`/workspaces/vbpub/.worktrees/benchmark-ingest` and do all work there — never modify the
main `/workspaces/vbpub` checkout directly:
```
git worktree add -b feat/benchmark-ingest .worktrees/benchmark-ingest main
```
Gate the suite against the worktree via the tester-unified image (commit on the branch
FIRST — diff-cov diffs tracked files):
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
 'cd /workspaces/vbpub/.worktrees/benchmark-ingest/nyxloom && PYTHONPATH=src \
  /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q \
  && /opt/tester-venv/bin/python -m coverage json -o /tmp/bi.json \
  && /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main --coverage-json /tmp/bi.json --source src/nyxloom'
```
Gates run SOLO (one tester-unified container at a time — concurrent gates OOM/SIGKILL-137).
`config.py` + `storage.py` are FROZEN CORE — additive fields go on the benchmark/capability
records (outside config.py), never on config schemas.

## Goal
Turn the manual pwmcp scrape of the DeepSWE leaderboard into reusable, self-checking,
automatic ingest that feeds the capability catalog with real per-effort benchmark data —
safely (a DOM/schema change must fail loud, never corrupt our data) and cumulatively
(upstream removing a model must never delete our record for it).

## Decisions (operator-locked 2026-07-23)
- **D-B1 Effort is first-class.** Record key is `(benchmark, version, model, effort)`; each
  `(model, effort)` bands independently. DeepSWE proves the spread is huge (terra 70%→24%
  across max→low; luna 67%→2%). Routing = quality-floor + cheapest-effort-that-clears.
- **D-B2 Version-isolated.** Never merge across benchmark versions (different task sets ⇒
  non-comparable scores). v1.0 and v1.1 coexist as distinct datasets. Cross-version compare
  ONLY if DeepSWE documents an identical task set (unverified — do not assume).
- **D-B3 Accumulate-never-delete.** A refresh upserts scraped rows (stamps `last_seen`) and
  KEEPS rows it didn't see (flag `present_upstream=false`, keep last known scores). A scrape
  can only add/refresh, never subtract. `first_seen` preserved.
- **D-B4 Safety-gated scrape.** A scrape result is validated before it may touch the store;
  on any check failure the refresh is a NO-OP + emits an alert (existing data untouched).
  Checks: row-count floor; required anchor models present with in-range scores; every row
  parses (model non-empty, pass@1∈[0,100], cost≥0, effort∈known∪{null}); canary sentinel
  string present (proves we're on the real page, not an error/placeholder).
- **D-B5 pwmcp-driven, not in-process.** The daemon has no browser; the scrape source talks
  to nyxloom's own pwmcp (nyxloom-prod-pwmcp) — WebSocket/Playwright — exactly as the
  cockpit did. Selectors/interactions are config-driven so a small DOM tweak is a config
  edit, not a code change; a structural change trips D-B4 and fails safe.
- **D-B6 Static-seed fallback.** The committed DeepSWE v1.1 snapshot (captured this session,
  45 rows) is a `static` source so the catalog works with zero live dependency; the
  browser-scrape source refreshes it. Also the AA source (keyed) coexists.

## Data model (additive; lives on the records, not config.py)
`BenchmarkRecord` gains: `benchmark: str`, `version: str|None`, `effort: str|None`,
`first_seen`/`last_seen` (set by the store, not the source), `present_upstream: bool`.
`CapabilityRecord` gains: `effort: str|None` (+ carries version). Axis vocabulary unchanged
(`intelligence`/`coding`/`agentic`); DeepSWE Pass@1 → `scores.coding`.

## Source kinds (benchmark_sources.py `@register_kind`)
- `static` (NEW) — reads a committed data file (the DeepSWE v1.1 snapshot seed). Robust, offline.
- `browser-scrape` (NEW) — pwmcp navigate + configured interactions (toggle "All effort
  levels") + configured extractor → rows; runs D-B4 validation; RAISES on failure (never
  returns partial). Config: pwmcp endpoint, url, steps, row/field extraction, version tag,
  anchor set, canary string.
- `artificial-analysis` (FIX, LIVE-VERIFIED with real key 2026-07-23) — endpoint
  `/api/v2/language/models/free` (the Pro `/language/models` **403s** "requires a Pro subscription"
  for a standard key; the code's `/data/llms/models` is also wrong). Records under `data`. Fields
  CONFIRMED nested: `evaluations.artificial_analysis_{intelligence,coding,agentic}_index` (null for
  some models), `pricing.price_1m_{input,output}_tokens`. model_id: prefer `slug`/`name` — top-level
  `id` is a UUID (bad join key vs DeepSWE names). `context_window_tokens` is NOT top-level in the
  free tier → P1 re-derives the real context key from a live row. Keyed by AA_API_KEY (in secrets.env).
- `json-http` (existing) — unchanged.

## Accumulate-store (NEW small module or capability_map extension)
`merge_dataset(existing, scraped)`: key by `(benchmark,version,model,effort)`; upsert scraped
(refresh scores + `last_seen`, keep `first_seen`), retain unseen (set `present_upstream=false`),
never delete. Persisted as its own managed block (own markers), version-namespaced. The current
`write_capability_catalog` OVERWRITES its block — the merge must happen BEFORE assembly so we
never lose accumulated history.

## Package decomposition (each: implement → commit → SOLO gate 100% diff-cov → review → merge --no-ff)
- **P1 — foundation** (buildable now): data-model fields (D-B1) + `static` source + committed
  DeepSWE v1.1 snapshot seed + AA v2 fix. Files: benchmark_sources.py, capability_map.py, new
  `data/benchmarks/deepswe-v1.1.toml`, tests. High-judgment (frozen-core-adjacent, exact API
  mapping) ⇒ Sonnet/controller, not a cheap route.
- **P2 — versioned accumulate-store** (D-B2/D-B3): merge_dataset + persistence + first/last_seen +
  present_upstream. Tests: new-model, removed-model (kept+flagged), changed-score, new-version-isolation.
- **P3 — safety-gated browser-scrape** (D-B4/D-B5): `browser-scrape` source + validation; tests
  drive a mocked pwmcp/DOM fixture (valid + several broken-DOM cases → MUST raise, no partial write).
- **P4 — scheduled refresh + dashboard** (B20 first consumer + B19 ext): refresh job
  (scrape→validate→merge→assemble→persist; failure ⇒ no-op+alert); dashboard shows effort/version/stale.

## Update 2026-07-23 (operator) — OpenRouter source + unrated-model surfacing
- **OpenRouter benchmark source (P5, in flight via luna@high).** Not a browser-scrape: a clean
  UNAUTHENTICATED JSON API — `GET /api/frontend/v1/catalog/models` (slug→permaslug) then
  `GET /api/frontend/v1/stats/benchmark-scores?permaslug=…` (`data.scores[]`; use the
  `auto-routing` / `endpoint_id=null` canonical row; score 0..1 → ×100). Source kind
  `openrouter-benchmarks`, safeguarded (catalog-size floor + canary-slug → fail-safe raise;
  per-model gaps tolerated). Feeds P2 accumulate-store + **P4 scheduled refresh so our data
  updates periodically.** Wiring into DEFAULT_SOURCES/routes = P4 (not P5).
- **Ingest slug list (config, extensible):** `nvidia/nemotron-3-ultra-550b-a55b`,
  `nvidia/nemotron-3-super-120b-a12b`, `cohere/north-mini-code`, `deepseek/deepseek-v4-pro`,
  `deepseek/deepseek-v4-flash`. Observed (auto-routing, gpqa/tau %): **v4-flash 86.0/74.9 ≈
  v4-pro 86.8/77.2** (flash ≈ pro — good cheap implementer signal); nemotron-ultra has data;
  **nemotron-super + north-mini-code publish NO benchmarks.**
- **D-B7 — unrated-model operator surfacing (operator-locked 2026-07-23).** A benchmark-less model
  must NOT vanish. The capability catalog includes discovered-but-unbenchmarked models as
  **`unrated`** (band 0, empty scores, an `unrated=true` flag); the dashboard shows them
  distinctly; the OPERATOR decides if/how to use one via (a) the existing role-grant and (b) a
  **manual comparison** — a `compares_to` / `manual_band` operator override in `[capability_map]`
  ("treat like luna" / "≈ band 2"). *"Let the user decide if/how to use it, what it compares to."*
  Scope: a capability_map change — today `refresh_catalog` pulls ONLY from benchmark sources, so
  it must ALSO ingest the discovered model list (free_models / the OpenRouter catalog) to surface
  the unrated ones. (New package, after P5.)
- **D-B8 — benchmarks must match OUR task profile, not what a leaderboard ships (operator-locked
  2026-07-23).** The per-model `stats/benchmark-scores` exposes ONLY `gpqa_diamond` (grad-science
  MCQ) + `tau_bench_verified_airline` (airline customer-service agent) across all 12 top models
  sampled — BOTH off-domain for us (Python coding, infra/docker ops, config, DB, API integration,
  code review, system/perf analysis). So the shipped per-model source is a WEAK routing signal and
  MISSES coding entirely. Relevant benchmarks, by axis:
    - coding → **DeepSWE** (have — per-effort CURVE) + **aaData.coding** (`rankings/benchmarks`, AA
      coding composite, effort-labeled: Sol(xhigh) 78.3, Terra(max) 76.7, Fable 76.5, Opus 74.3) +
      SWE-bench Verified.
    - ops/terminal/docker/system → **terminal-bench-v2** (AA agents chart) — on-target for our
      operational work; add as a source.
    - agentic/tool-use → SWE-agentic / terminal-bench (software domain), NOT tau-airline.
    - intelligence → aaData.intelligence, LOW weight (general reasoning, weakly predictive here).
  ACTION: make the BULK `rankings/benchmarks` (aaData incl coding, effort parsed from `aa_name`) the
  PRIMARY OpenRouter signal (new bulk source); demote the shipped per-model gpqa/tau source to a
  minor sanity signal, NOT a routing driver; add a terminal-bench-v2 source. Per-axis routing
  weights reflect task relevance (coding/ops high, intelligence low).
- **D-B9 — realize D-B8 via the AA DIRECT API, not tbench.ai or OpenRouter aaData (operator-locked
  2026-07-23, after Terminal-Bench exploration).** tbench.ai itself = poor data source (no official
  API; results scattered across GitHub run-log repos — laude-institute/harbor-framework — + 3rd-party
  aggregators; no clean per-model effort). BUT Artificial Analysis's `GET /api/v2/data/llms/models`
  (keyed — our AA_API_KEY has access; 580 models) exposes per-model `evaluations.{terminalbench_v2_1,
  terminalbench_hard, livecodebench, scicode, tau2, tau_banking, ifbench, gpqa, aime, math_500,
  mmlu_pro, hle, lcr, artificial_analysis_{coding,intelligence,math}_index}` (per-eval fields 0..1
  fractions; composites 0-100). FILL: terminalbench_hard 432/580, scicode 539, tau2 440, livecodebench
  343, terminalbench_v2_1 170 (thin but present for frontier models we route to: opus-4-8 0.8,
  sonnet-5 0.8, gemini-3-6-flash 0.8). **EFFORT = SLUG SUFFIXES** (`-low`/`-medium`/`-minimal`;
  default=high/max), e.g. gpt-oss-120b 0.3 vs gpt-oss-120b-low 0.1 → real per-effort rows (imperfect
  fill, but better than OpenRouter aaData which collapses to one representative).
  ACTION (SUPERSEDES D-B8's OpenRouter-aaData + separate terminal-bench-source plan): UPGRADE the P1
  AA source into the PRIMARY multi-benchmark source — (1) **FIX endpoint back to `/data/llms/models`**
  (P1 wrongly switched to `/language/models/free` = 200 models MISSING these eval fields — a
  regression); (2) map RELEVANT evals per axis — coding: livecodebench+scicode+aa_coding_index;
  ops/agentic: terminalbench_v2_1+terminalbench_hard+tau2; intelligence: aa_intelligence_index (LOW
  weight); (3) parse EFFORT from slug suffix; (4) keep per-eval scores (not only composites) so
  routing weights by relevance. This ONE source replaces the proposed OpenRouter-bulk + terminal-bench
  sources; OpenRouter per-model gpqa/tau + aaData are now redundant subsets; DeepSWE stays for the
  fine per-effort coding CURVE.

## Open items
- Verify (optional, one pwmcp read) whether DeepSWE v1.0 shares v1.1's exact task set — only then
  is any cross-version comparison legitimate. Default stays version-isolated regardless.
- Pre-existing hazard (not this plan): `nyxloomd/.env` is tracked AND ignored → NTFY tokens are in
  git history; rotate + `git rm --cached` in a separate cleanup.
