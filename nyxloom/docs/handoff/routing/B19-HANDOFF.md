# B19 — routing/capability dashboard panel (D-R14)

You are an implementation agent. Implement this package exactly. Every file:line
anchor below is VERIFIED by a prior code-recon — trust them and do not re-derive.

## Where you are
- Repo root of the package: the directory you were started in (a git worktree),
  branch `feat/nyxloom-B19-routing-dashboard`. Python project; source in
  `src/nyxloom/`, tests in `tests/`.
- **Work ONLY in this worktree. Never touch any other checkout.**

## What to build
A **read-only** dashboard page `routing.html` with two tables:
1. **Capability catalog** — one row per model: `model_id`, `source`, per-axis
   scores, per-axis bands, price in/out, context length, `may_review`,
   `may_carve`.
2. **Per-tier resolution** — for each tier: the declared candidate routes in
   order (winner = first, then runners-up), each with `route_id`, `cli`,
   `model`, declared `status`, and a privacy marker.

## scope.touch (edit ONLY these)
- `src/nyxloom/render.py`
- `src/nyxloom/capability_map.py`
- `tests/test_render.py`
- `tests/test_capability_map.py`
- `docs/handoff/routing/B19-LOG.md`, `docs/handoff/routing/B19-REPORT.md` (create)

**DO NOT EDIT (frozen / out of scope):** `src/nyxloom/config.py`,
`src/nyxloom/paths.py`, `src/nyxloom/daemon.py`, `tests/conftest.py`,
`src/nyxloom/storage.py`, `src/nyxloom/types.py`. You do not need any of them.

## LOCKED decisions — do not redesign these

**D-B19-1 — Add the missing catalog READER to `capability_map.py`.**
`capability_map.py` writes the catalog but has **no reader**. Add:
```python
def load_capability_catalog(path: Path | None = None) -> list[CapabilityRecord]:
```
It opens `path or paths.routes_path()`, and returns `[]` when the file is absent,
when the `[capability_catalog]` table is absent, or when `records` is empty.
Rebuild each `CapabilityRecord` using `row.get(...)` for optional fields and
`json.loads(row["raw_json"])` to recover `raw`. This keeps TOML-shape knowledge in
the module that owns the shape. Mirror the existing loader convention
(`CapabilityMapConfig.load`, `capability_map.py:136-146`) — read
`paths.routes_path()` directly with `tomllib`, never via frozen `config.py`.

**D-B19-2 — Availability column = "unknown (not persisted)".**
Health/availability lives in process-local daemon memory (`Daemon._probe_memo` /
`_provider_paused`, `daemon.py:1066-1084`) and is **never written to disk**;
`render.py` has no access to it. Render the column as `unknown — not persisted`
with a short footnote citing `daemon.py:1066`. **Do NOT touch `daemon.py` and do
NOT change `render_all`'s signature** (documented frozen at `render.py:11-12`).

**D-B19-3 — Mirror the declared route order; never reimplement selection.**
There is no `pick_route()` function. Selection is a 4-line inline loop duplicated
at `reconcile.py:968-971` and `reconcile.py:1193-1196` ("first candidate whose
provider probe is healthy"), and the review path bypasses tiers entirely. So:
- Candidates come from `config.Routes.load().for_tier(tier)` — that list is
  **verbatim TOML array order**, and that order IS the ranking.
- Render `winner = candidates[0]`, `runners-up = candidates[1:]`.
- Put one sentence of page copy stating that dispatch selects the **first
  candidate whose live probe is healthy** (authority: `reconcile.py:968-971`).
- **`RouteDef.status` (e.g. `"fallback-only"`) has ZERO consumers in selection
  logic.** Show it as a *declared attribute* only. Do NOT label it a filter that
  fired — that would be a false claim.

**D-B19-4 — Empty/absent catalog MUST render cleanly.**
Nothing writes the catalog in production yet, so the *normal* live state is "no
`[capability_catalog]` table at all". `render_all` has **no per-page try/except**,
so a `KeyError` here breaks EVERY page. Handle three distinct cases: file absent,
table absent, `records = []`. Render an empty-state row with a matching `colspan`.

**D-B19-5 — Never call `refresh_catalog()` from render.**
`capability_map.refresh_catalog` performs NETWORK fetches
(`capability_map.py:296` → `benchmark_sources.fetch_all`). Render runs
synchronously inside the daemon loop. The panel reads persisted data only.

**D-B19-6 — Privacy comes from routes, not the catalog.**
`CapabilityRecord` has no privacy field. The repo's privacy signal is
`RouteDef.prompt_hints` containing `"free-endpoint"` (see `adapters.py:351-353`,
written at `free_models.py:436`). Derive the privacy marker from that.

## The pattern to copy (read this first)
`_render_quality` at `src/nyxloom/render.py:1226-1307` is the closest twin: a
read-only table page that already calls `config.Routes.load()`. Copy its shape
exactly. Its invariants, all of which your panel MUST reproduce:
- `log.debug("page render", page="routing")` as the first statement.
- Wrap every config/catalog load in `try: ... except Exception: <obj> = None`
  (degrade, never raise).
- Pass every dynamic string through `html.escape(...)`.
- Deterministic ordering — iterate `sorted(...)` everywhere (there is an
  idempotence oracle).
- Empty-state fallback row with a matching `colspan`.
- Compose as `_html_head("Routing") + content + _html_foot()`, then
  `(www / "routing.html").write_text(html_content, encoding="utf-8")`.

Also useful: `_render_config`'s route table (`render.py:1499-1544`) already
renders `route_id / cli / model / variant / effort / status`.

## The FOUR wiring edits in render.py (omitting any is a defect)
1. **Define** `def _render_routing(www: Path, registry: dict[str, Path]) -> None:`
2. **Call it** inside `render_all` — insert a line right after
   `_render_config(www, registry)` at `render.py:585`.
   ⚠️ **THIS IS THE #1 OMISSION RISK.** Without this line the function is never
   executed and the 100% diff-coverage gate fails it as entirely uncovered.
3. **NAV link** — add `<a href="routing.html">Routing</a>` to the `NAV` constant
   at `render.py:290-303`. (`NAV` is a plain `"""` string, NOT an f-string.)
4. **Module docstring** — add a short `routing.html` stanza to the
   "INTERFACE CONTRACT (frozen)" page list at `render.py:9-137`, like every other
   page has.

⚠️ If you add CSS: the `CSS` constant at `render.py:213-288` **is an f-string**, so
every literal brace must be doubled (`{{ }}`). This is the most common way this
file gets broken. The base `table/th/td` styling already gives a bordered table —
you probably need no new CSS at all.

Give the two tables stable hooks: `id="capability-catalog"` and
`id="tier-resolution"` (tests assert on ids, not prose).

## Data shapes you will read
`CapabilityRecord` (`capability_map.py:81-98`), frozen dataclass:
`model_id: str`, `source: str`, `scores: dict[str,float]`,
`price_input: float|None`, `price_output: float|None`, `context_length: int|None`,
`bands: dict[str,int]`, `may_review: bool`, `may_carve: bool`, `raw: dict`.
- `AXES = ("intelligence", "coding", "agentic")` (`capability_map.py:73`). The
  design doc's "reasoning" axis IS `intelligence`; there is no fourth axis.
- `scores` may contain only a SUBSET of the axes; `bands` ALWAYS has all three.
- **`band == 0` means unrated/unknown — distinct from band 1 (lowest rated).**
  Render 0 as `—` or `unrated`, not as a number.

Persisted TOML (written by `write_capability_catalog`, `capability_map.py:228-252`):
```toml
[[capability_catalog.records]]
model_id = "vendor/model-a"
source = "aa"
scores = {agentic = 0.9, coding = 0.7, intelligence = 0.5}
price_input = 1.0
price_output = 2.0
context_length = 128000
bands = {agentic = 3, coding = 3, intelligence = 2}
may_review = true
may_carve = false
raw_json = "{\"vision\": true}"
```
- `price_input` / `price_output` / `context_length` are **omitted entirely when
  None** — always use `row.get(...)`, never `row[...]`.
- `raw` persists as `raw_json`, a JSON **string** — `json.loads` it.
- Empty catalog is written as `[capability_catalog]` + `records = []`.

`RouteDef` (`config.py:447-466`): `route_id, cli, model, variant, effort, sandbox,
argv_max, prompt_hints, probe, resume, dispatch_extra, session_capture,
session_discover, usage_source, status, role_default, raw`.
`Routes.for_tier(tier)` (`config.py:502-503`) returns routes in TOML array order.
`Routes.load()` is at `config.py:474`.

## Behavioral oracles (your tests MUST assert these)
Tests go in `tests/test_render.py` (and `tests/test_capability_map.py` for the
loader). Style: build state → call `render.render_all(registry)` → `read_text()`
the page → assert **substring containment** on the raw HTML. Model on
`test_quality_html_aggregation` (`tests/test_render.py:621-633`) and
`test_timeline_html` (`tests/test_render.py:606-618`).

- **O1 — page exists and is wired.** Add `routing.html` to the page-existence
  oracle `test_render_all_creates_pages` (`tests/test_render.py:112-127`). This
  proves the `render_all` call line exists.
- **O2 — catalog table renders records.** Seed a catalog via
  `capability_map.write_capability_catalog(paths.routes_path(), [...])` (it is
  non-clobbering by contract) in a **local fixture in your own test file**
  (`tests/conftest.py` is FROZEN — never edit it; use the existing `tmp_state` and
  `sample_project` fixtures). Assert `id="capability-catalog"`, the `model_id`,
  a score, and a band appear.
- **O3 — absent catalog renders cleanly.** With NO `[capability_catalog]` table
  (the default `sample_project` state), `render_all` does not raise and
  `routing.html` shows the empty state. Also cover `records = []` separately.
- **O4 — tier resolution mirrors declared order.** With the fixture's tier
  (`sample_project` writes one tier `flash-high` with route `fake-cli`, see
  `tests/conftest.py:50-61`), assert `id="tier-resolution"`, the tier name, and
  the route id appear, and that the first candidate is presented as the winner.
- **O5 — escaping.** A `model_id` containing `<script>` must appear escaped:
  assert `"<script>" not in content` and `"&lt;script&gt;" in content`. (Model on
  `tests/test_render.py:539-554`.)
- **O6 — loader round-trip.** In `tests/test_capability_map.py`: write a catalog
  then `load_capability_catalog` returns equal records; absent file → `[]`;
  absent table → `[]`; empty records → `[]`; a record with `price_input`/
  `price_output`/`context_length` omitted loads with `None` for those.
- **O7 — determinism.** Two successive `render_all` calls produce a byte-identical
  `routing.html` (mirror `test_idempotence`, `tests/test_render.py:654-665`).

## Coverage requirement (hard)
The repo enforces a **100% diff-coverage floor** — every executable line you add
must be exercised by a test. Branches that each need their own test: the
`except Exception:` load arm, absent-file / absent-table / empty-records, empty
tiers (`for_tier` returns `[]`), each optional-field-missing path, and the
`band == 0` rendering branch if you special-case it. Do not leave dead lines.

## Verification you should do (and what you must NOT do)
- Run `python -m py_compile src/nyxloom/render.py src/nyxloom/capability_map.py`
  and, if it works in this environment,
  `python -m pytest tests/test_render.py tests/test_capability_map.py -q`.
- If pytest fails on MISSING DEPENDENCIES, that is expected here — do NOT install
  anything and do NOT try to fix the environment.
- **Do NOT run the full test suite. Do NOT run Docker. Do NOT attempt any gate.**
  A controller runs the authoritative gate afterwards.

## Deliverables
1. The implementation + tests above.
2. `docs/handoff/routing/B19-LOG.md` (what you did, decisions, blockers) and
   `docs/handoff/routing/B19-REPORT.md` (oracle→test mapping, what you verified,
   commit hash).
3. **Commit on the current branch** (do NOT merge, do NOT switch branches, do NOT
   touch `main`). Stage only the files listed in scope.touch. Commit message:
```
feat(nyxloom-B19): routing/capability dashboard panel (D-R14)

<short body>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
4. Then STOP and print: the commit hash, the files changed, and an honest
   statement of what you did and did not verify. Never claim a gate passed.

## If you get stuck
If an oracle genuinely cannot be satisfied without editing a file outside
scope.touch: STOP. Write `docs/handoff/routing/B19-LOG.md` naming exactly which
file you need and why, commit only that file, and print
`BLOCKED: <file> needed for <oracle> because <reason>`.
**Do NOT fake a workaround and do NOT write a test that passes without asserting
the real behavior.**
