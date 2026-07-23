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

## Open items
- Verify (optional, one pwmcp read) whether DeepSWE v1.0 shares v1.1's exact task set — only then
  is any cross-version comparison legitimate. Default stays version-isolated regardless.
- Pre-existing hazard (not this plan): `nyxloomd/.env` is tracked AND ignored → NTFY tokens are in
  git history; rotate + `git rm --cached` in a separate cleanup.
