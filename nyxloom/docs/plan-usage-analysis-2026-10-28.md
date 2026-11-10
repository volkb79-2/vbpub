<!-- origin: 2026-10-28; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: nyxloom-usage-analysis-plan.md -->
<!-- relation: nyxloom usage analysis plan -->

# nyxloom usage analysis (`nyxloom usage`) - plan, 2026-10-28

Status: plan only. Evidence below was measured on this machine's real transcripts (Claude Code 2.1.311),
fields and counts only, no message content. One-off script (not in repo):
`<controller-scratch>/-workspaces-vbpub/bfe9ee76-aa16-4929-a108-36a00b55a0e5/scratchpad/usage24h.py [hours]`

## 1. What the data actually contains (verified, last 24 h: 146 jsonl files, 141 of them subagents)

Per `type=="assistant"` line (top-level `<sid>.jsonl` and `<sid>/subagents/agent-<id>.jsonl` have the same schema):
- `message.model` (real model id, e.g. claude-sonnet-5-5, claude-fable-5-1, claude-opus-5-5, claude-haiku-5-5;
  `<synthetic>` for local error/placeholder lines -> skip), `requestedModel`, **`effort`** and `perTurnEffort`
  (medium/high/xhigh, PER MESSAGE - so effort and model ARE recoverable per call, and a session can change them mid-run).
- `message.usage`: input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens,
  **`cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`** (both present on every line; the 1h tier
  has a different price), `output_tokens_details.thinking_tokens` (subset of output), `service_tier`, `speed`
  (all "standard" in 24 h: fast/priority never seen, but field exists), `inference_geo`, `iterations`, `fallback_credit`.
- `timestamp`, `requestId`, `message.id`, `sessionId`, `isSidechain`, `agentId`, `attributionAgent`,
  `attributionSkill`, `cwd`, `gitBranch`, `entrypoint`, `version`.
- Extra record types: **`cost-state`** (per session: `totalCostUSD` and `modelUsage{model:{inputTokens,outputTokens,
  thinkingTokens,cacheRead..,cacheCreation5m/1h..,costUSD}}` = Claude Code's OWN priced cost per model - this is the
  price oracle, see 4), `api-request` (querySource, params incl. betas, cacheControl ttl), `ai-title`/`custom-title`/
  `agent-name` (topic labels), `compact` metadata on lifecycle lines.
- Subagent attribution: `subagents/agent-<id>.meta.json` = {agentType, description (the dispatcher's short topic),
  toolUseId, spawnDepth, requestShape (e.g. background), requestNonInteractive, model (alias as dispatched, e.g.
  "sonnet"), finished}. `toolUseId` joins to the Agent tool_use block in the parent transcript (parent sid is the
  directory name; for spawnDepth>1 the toolUseId is found in another agent's file). Per-call real model/effort come
  from the lines themselves, meta.model is only the alias.
- **Streaming duplicates**: 5,000+ requestIds appear on 2-24 lines (one line per content block). Measured on 3,678
  multi-line requests: usage identical across all lines (in/out/cache), same message.id. So de-dup key =
  `requestId` (fallback `message.id`), keep last line; summing lines would overcount by ~1.9x. (Existing
  `extract-report` is event-based, see 3; verify it does not double count.)
- Context size per call = input + cache_read + cache_creation (existing `CallRow.context_size`). Observed max 745k
  (sonnet main), 542k fable, 198k opus. Note the 1M-context tier: long-context premium is NOT visible as a field;
  must be inferred from context size > threshold (to verify).
- **Limit state**: percentage is NOT recorded anywhere in transcripts or stats-cache.json (that has only message/
  session/tool-call counts per day). Only rejections are logged: assistant lines with `quotaLimits`
  {status:"rejected", rateLimitType:"five_hour", resetsAt:1793131200 (=2026-10-27T20:00:00Z), overageStatus,
  lowPriorityOffer...} and `apiErrorParams.rate_limit_info`, at 18:15:13Z and 18:16:37Z (3 lines). So: the 100%
  point and the reset time are observable; 96% and everything below are only estimable. `seven_day` also appears in
  strings (2 hits, not examined - check whether a weekly warning/rejection exists). `claude --help` has no usage/
  stats verb; /usage, /cost and /context are interactive-only (not scriptable here). Rate-limit response headers are
  not stored (grep for `unified-` found nothing structured).
- Implied window: reset 20:00Z, 5 h => window 15:00-20:00Z (verify whether windows are anchored to first message
  after previous reset rather than clock-aligned; resetsAt values in successive rejections give the anchor).

## 2. First answer from real data (RAW tokens, unweighted; last 24 h, 6,825 unique requests)
| model | calls | out | cache read | cache write 5m | cache write 1h | max ctx |
| sonnet-5-5 | 5,902 | 5.49M | 1,055.6M | 45.9M | 2.54M | 745k |
| fable-5-1 | 718 | 1.25M | 211.8M | 10.6M | 0.15M | 542k |
| opus-5-5 | 204 | 0.25M | 25.7M | 0.92M | 0 | 198k |
| haiku-5-5 | 1 | ~0 | 0 | 39k | 0 | 39k |
Effort: sonnet medium 5,672 calls / high 230; fable all high; opus all high.
Busiest hours (UTC): 17h (1,099 calls), 16h (1,471), 14h, 23h, 07h. The 15:00-18:15 pre-limit window contains hours
15/16/17: roughly 3,000 calls, ~2.7M output.
Session split: this controller session (bfe9ee76) main = 541 calls (sonnet, 2.5M 1h-cache writes, 0.6M out,
max ctx 745k) vs its subagents = 6,279 calls (6.3M out, 1,094M cache read). i.e. ~92% of calls and ~88% of the
provisional-normalized load came from dispatched subagents, not the controller.
Top subagent topics by provisional normalized load (in+5*out+0.1*read+1.25*c5m+2*c1h, relative ONLY, not limit %):
Wave stream: assay 22.8M (sonnet, 331 calls) > pwmcp OCR sidecar phase 1 17.3M > Glyph-grid console text recognition
14.1M > Fable-high review run-gate 11.5M (fable) > pwmcp canvas capture 8.4M > Populate more console fonts 8.3M >
Fable-high review iobench 7.9M > Implement IO governor recorder v1 7.1M > Fable-high review iogov 6.0M > ... Fable
reviews are few calls but, with real Fable weights (likely several x sonnet), probably rank much higher than
shown: weights are the entire open question. Controller main = 28.0M.
CAVEAT: the weight column is the repo's E-009 ratio guess (input 1, read 0.1, write 1.25, out 5, 1h-write assumed 2)
applied equally to all models; it ignores model price ratios and long-context premiums. Do not read as limit share.

## 3. What nyxloom already has (inventory)
- `nyxloom extract-report <path> [--type report-sheet|report-detailed|csv] [--json]` (cli.py:1368; module
  `src/nyxloom/session_extract/stats.py`, V9/E-009): per-call `CallRow` (tokens incl. 5m/1h split, thinking, model,
  effort, tools, context_size, compaction metadata, profile simulation) and `Block` condensed view, for
  claude_code/codex/opencode. Tests: tests/test_session_extract_stats*.py. Single session file only.
- `tools/jsonl-metrics.py` (1,782 lines): subcommands curve/simulate/simulate-multi/readset/overlap/orient/
  boundaries/threshold; normalized cost weights (input 1.0, read 0.1, create 1.25, output 5.0); per-agent context
  growth curve; for pipeline agents only (E-001..E-008).
- `session_extract/locate.py` (session-id -> path), `sessions.py`, `successor.py`, adapters/{claude_code,codex,opencode}.
- Doctrine: E-009 field inventory; README "Relationship to jsonl-metrics".
GAPS: (a) no cross-session / time-window scan (`--since 24h`); (b) no requestId de-dup (event/uuid based; verify
adapter merge at claude_code.py ~600 covers all multi-line requests); (c) no subagent tree and no meta.json
description/agentType/toolUseId attribution; (d) no model-aware weights or USD, cost-state not read; (e) no
group-by/top-N; (f) no 5 h window / limit-anchor / calibration notion; (g) per-message effort is on CallRow but
never aggregated; (h) no hour-bucket time series for context; (i) no TUI.

## 4. Tokens -> limit usage
Principle: limit consumption is not published as a token formula. Treat it as `weighted_units = sum(tokens_k * w[model,k])`
and calibrate against observed anchors; report both raw and weighted, label weighted as ESTIMATE.
Weight table (configurable, TOML, shipped with provenance fields, NOT memorised):
  `usage-weights.toml`: per model id (+ alias/prefix match): w_input, w_output, w_cache_write_5m, w_cache_write_1h,
  w_cache_read, optional long_context {threshold_tokens, multipliers}, speed/service_tier multipliers
  (priority/fast), `source`, `verified_on`, `url`.
Price sources, in order of trust:
 1. Anthropic pricing page (fetch at implementation time with WebFetch/pwmcp; record URL + date; the Cache TTL
    multipliers and Fable/Opus/Sonnet 5.x prices must be read from the page, not assumed).
 2. **Empirical implied prices from `cost-state` records**: each has per-model tokens by class and `costUSD`. A
    least-squares/NNLS fit across all cost-state records of the last N days gives price per class per model and
    cross-checks the page (also catches hasUnknownModelCost). One command: `usage prices --fit`.
 3. Ratios in E-009 are only a fallback default for unknown models (flagged `unverified`).
USD cost is computable (price x tokens) and is the natural weight; but the subscription limit may weight differently
(e.g. cache reads cheaper/free, model multipliers). So the second layer is calibration:
 - Anchors file `usage-anchors.toml` (operator-editable + `usage anchor add --at <ts> --pct 96 --kind five_hour`):
   known points: 2026-10-27T18:15Z ~96% (operator) and rejected@18:15:13Z/18:16:37Z (=100%, auto-detected from
   `quotaLimits`), reset 20:00Z. Auto-extract all rejections from transcripts as hard 100% anchors.
 - For a window [reset-5h, t]: weighted_units(t)/pct(t) => implied window capacity C; compare C across windows/
   anchors to see if the weight set is consistent (low variance = good weights). With >=3 anchors fit a small
   number of free parameters (model-tier multipliers, cache-read weight) by bounded least squares; report fit
   residuals; never silently overwrite shipped weights - emit `usage-weights.local.toml`.
 - Weekly limit: same with a 7-day window; weekly anchors rare, mark "to be verified" and show only weighted share.
 - Output share = weighted_units / total weighted units in window (always valid, needs no calibration) AND, if C is
   known, estimated % of limit. Per-agent "contribution to the 96%" = share of that window.
 - Window logic: 5 h rolling vs clock-aligned is unverified; implement `--window-model aligned|rolling` fed by the
   resetsAt anchors, test both against anchors.
 - Caveat: other Claude surfaces (claude.ai web, other machines) share the same limit; this machine's transcripts
   give a lower bound, so calibrated C is "local-equivalent" capacity. Say so in the output footer.

## 5. Design (revised 2026-10-28 after operator feedback: tree table + joined diagrams + interactive HTML + PDF)

### 5.1 Layered architecture (one data layer, four renderers)
`scan` (loader) -> `model` (Call/Agent tree) -> `query` (pure functions) -> **`chartdata`** (JSON-serialisable
series, the single contract) -> renderers: table (plain/Rich), json, csv, **html** (single file), **pdf**; the later
TUI is a 5th renderer. Golden JSON of `chartdata` is the test oracle for every renderer.

### 5.2 Data model (stdlib dataclasses, no text fields, ever)
- `Call(request_id, ts, session_id, agent_id|None, model, effort, speed, tier, in, out, cr, cc5, cc1, thinking, ctx,
  project, kind=normal|compaction_summary, compact_pre|post)`.
- `Node` (tree row; `kind` main|subagent|fork|resume): `node_id`, `parent_id` (session root -> agent via toolUseId;
  nested agents via the dispatching agent's file), `session_id`, `agent_id`, `agent_type`, `topic` (meta.description
  for subagents, ai-title/custom-title for the root, truncated, `--no-labels` blanks it), `models` (set, with call
  counts - an agent may switch model), `primary_model`, `efforts` (set), `start`, `end`, `duration`, `calls`,
  `tok{in,out,cr,cc5,cc1,thinking}`, `peak_ctx` (+ts), `compactions[]`, `weighted`, `usd`, `share_of_total`,
  `share_of_window`, plus `self` and `subtree` variants of every numeric (subtotals per subtree are computed once in
  `tree.rollup()`, `self` kept so sorting can use either).
- `Series` (chart layer): `{node_id, label, group, model, effort, points:[(t, ctx, weighted_cum|delta)], events:
  [(t, kind=compaction|model_switch|effort_switch|cache_miss|rejection, detail)]}`; `Bucket` for stacked cost.
- `Window` (5h): start, end, rejection anchors, pct anchors. `Anchor`, `Weights` as in section 4.

### 5.3 Report part 1 - hierarchical TABLE
Rows (indent = depth, collapsible in HTML; plain/PDF render expanded to a depth limit, `--depth N`, `--min-share 1%`
folds small subtrees into an "other (k agents)" row so the table stays one or two pages):
`tree-glyph+topic | model(s) | effort | start-end | dur | calls | peak ctx | in | cache-read | out | c-write 5m/1h |
cost (weighted or USD) | share of window | share of total`.
Controller session row shows self and subtree (subtree = the figure used for sorting by default). Sort keys: cost,
start, duration, calls, peak ctx, out tokens, any column; sort applies among siblings (tree order preserved), with
`--flat` giving a flat ranking. Subtotals: every non-leaf row carries the subtree total; footer = grand total, plus
"covered window" and coverage warnings (pruned files, unknown model weights).

### 5.4 Report part 2 - JOINED diagrams (never one chart per agent; 4 charts max, each top-N + other)
Collapse rule (all charts): rank agents by subtree weighted cost; keep top N (default 12), remaining agents merge into
series "other (k)" (context for "other" is not plotted as a line, only its cost in the stacked chart, so the long tail
costs nothing visually). Colour = model family (Sonnet / Fable / Opus / Haiku, validated colour-blind-safe palette,
via the `dataviz` skill palette at implementation time), lightness/dash = effort, label = topic.
A. **Global swim-lane timeline** (primary): x = time (UTC, window shaded, reset line, rejection markers), one lane
   per top-level session, sub-lanes (packed greedily so concurrent agents do not overlap) per subagent; inside each
   lane an area/line of context size normalised to lane height with the absolute peak annotated; drops of >=30% mark a
   compaction (diamond; real `compactMetadata` shown with pre->post tokens when present, heuristic drop otherwise,
   styled differently); a model change inside an agent = coloured segment break + small tick.
B. **Context-over-time overlay** (per top-level session, small multiples if several, default = busiest 3, `--session`
   to choose): y = context tokens (absolute, shared scale so peaks compare), one line per agent of that session (top-N
   + other omitted), 200k / 1M reference lines (tier thresholds from weights file), compaction diamonds, model colour.
   "Several sessions joined" = `--join-sessions` puts all on one overlaid chart, lines grouped by colour per session.
C. **Stacked weighted cost per time bucket** (5-15 min buckets; auto from span) stacked by agent group
   (group-by selectable: agent/topic/model/session/effort), top-N + other, with the 5h window boundaries and the
   cumulative-% line when calibrated (clearly labelled estimate). This is the chart that answers "what produced the
   96% at 18:15".
D. **Cost-composition bars** (optional small): per top agent, stacked horizontal bar of in / cache-read / cache-write
   5m / 1h / out weighted, showing whether cost is output-heavy or cache-heavy (cheap diagnosis of cache misses).
Peak, drops and growth readable by: peak markers with value, sawtooth drops flagged, growth slope (tok/min) in tooltip.

### 5.5 Part 3 - interactive single-file HTML (`--format html --out usage.html`)
- Self-contained: one `.html`, data embedded as `<script type="application/json" id="chartdata">`, JS+CSS inline,
  no network at load or run time (assert in test: no `http(s)://` in src/href/import except in data labels).
- Library decision: **vanilla JS + hand-rolled SVG/Canvas** by default (4 chart types, tooltips, zoom/pan, brushing is
  ~600-800 lines; zero supply-chain, no licence issue, no 14-day question). Fallback if effort grows: vendor **uPlot**
  (MIT, ~50 KB min, time-series zoom/pan/cursors built in) or d3-scale/d3-zoom subset (ISC). Not Chart.js (heavier,
  weak at swim-lanes). Rule for any vendored lib: licence text inlined in the file header and listed in a
  THIRD-PARTY note, pinned exact version, release >= 14 days old (freshness policy), version + sha256 recorded in
  the repo, `>=`-floor policy not applicable to vendored files but the pin is documented. Licences/versions must be
  checked at implementation time (not asserted here).
- Interactions: hover tooltip (session, agent, topic, model, effort, time, in/out/cache tokens, ctx, weighted cost,
  event type); click row or series = isolate (others dimmed, again = clear; shift-click = multi); drag = zoom time,
  wheel/shift-drag = pan, double-click = reset; brush strip under chart C selects the analysis window and re-computes
  the table subtotals live; filters: model, effort, topic substring, min cost, time window; toggle raw tokens /
  weighted / USD (all three embedded, no recompute server); toggle weights profile if several embedded; tree table with
  expand/collapse, sortable headers, and **bidirectional sync** (hover a row highlights its series; selecting a
  chart region filters rows). Keyboard accessible, light/dark via CSS variables, prints cleanly (print CSS shared with
  the PDF path).
- Privacy: only the embedded numbers/topic labels; `--no-labels` replaces topics with `agent-<n>`; file header states
  generation time, window, weights file hash, "ESTIMATE" banner when weights are unverified.
- Size: 6.8k calls/day; embed per-call points only for top-N agents, decimated (LTTB or per-bucket max, preserving peaks and
  drops explicitly) -> typically < 1 MB; `--full` embeds everything (few MB).

### 5.6 Part 4 - PDF export (`--format pdf --out usage.pdf`)
Options evaluated:
 1. **Print-CSS from the same HTML via a headless browser we operate (pwmcp)**: single renderer, charts identical to
    the web page, tables paginate with CSS (`break-inside: avoid`, repeated thead). Needs a PDF entry point:
    pwmcp CLI today has `inspect --format text|snapshot|screenshot` only (no pdf verb found); Playwright itself has
    `page.pdf()`. Requires a small pwmcp addition (e.g. `inspect --format pdf` or MCP pdf cap) and a way to hand the
    browser a local file (file:// vs served URL, container mount) - both to be checked against pwmcp docs/security.
    Cost: depends on pwmcp being up; output text stays selectable (vector SVG).
 2. **Pure Python**: `matplotlib` 3.11.2 and `reportlab` 5.0.1 are present in this devcontainer but are NOT nyxloom
    dependencies. matplotlib alone can emit multi-page PDF (table as text/`ax.table`, charts as vector) with one
    dependency (BSD-style PSF licence, large wheel, numpy). reportlab (BSD) gives better tables/pagination but the
    charts must still be drawn (reportlab.graphics or matplotlib). Renders offline, deterministic, no browser.
 3. Browser "Print to PDF" by the operator from the HTML (zero code, ship the print CSS regardless).
Recommendation: ship (3)-ready print CSS in WP-H; implement PDF as option 1 behind a renderer interface
`render_pdf(chartdata) -> bytes` if pwmcp gets a pdf verb cheaply (shares 100% of the chart code, no new nyxloom
dependency); otherwise option 2 using **matplotlib only**, as an optional extra `nyxloom[report]` (justification:
vector charts + multipage PDF in one dependency; table drawn by a simple text layout). Do not add reportlab+matplotlib
both. Decision gate at WP-P start: 0.5-day spike checking pwmcp pdf feasibility; pick one, record in the plan.
Layout: A4 landscape, page 1 header + table (folded), page 2 charts A+C, page 3 charts B+D, footer with window,
weights hash, ESTIMATE flag, page numbers.

### 5.7 CLI verbs (register in `cli_registry.py`; generated surface must pass `cli-extended surface check`)
  `nyxloom usage report --since 24h|<ts> [--until] [--project P] [--session S]
      [--format table|json|csv|html|pdf] [--out FILE] [--sort cost|start|dur|calls|peak|out] [--flat | --depth N]
      [--group-by agent|topic|model|effort|session|hour] [--top N=12] [--min-share PCT]
      [--weights raw|usd|limit|FILE] [--join-sessions] [--no-labels] [--window five_hour|seven_day|none]`
   (`--format html|pdf` require `--out`; `table` default to stdout with the tree; `json` = the chartdata contract;
   `csv` = flat node rows plus a second `--csv-series` file for bucketed series.)
  `nyxloom usage timeline --session|--agent ID` (chart B data/text), `usage windows` (5h windows, anchors, per-window
  top contributors), `usage prices --fit`, `usage calibrate`, `usage anchor add|list`.

### 5.8 Edge cases (unchanged list plus new)
compaction (drops are events, not negative cost), resumed agents (one node, multiple bursts), forks (inherited prefix
counted once; `kind=fork`), parallel agents in one session (attribute by agentId never time), cache TTL (5m vs 1h; cold
restarts annotated `cache_miss`), requestId dup conflicts, missing meta.json (older versions -> `unknown`), nested
agents (rollup to root, tree kept; orphan whose parent transcript is outside the window attaches to an
"(unattributed)" node under the session), failed/rejected requests (zero usage, still rejection anchors), unknown
models (raw only + warning), speed/tier multipliers, long-context tier by ctx threshold, pruned transcripts (coverage
warning), UTC everywhere, files modified in window with older lines (filter by line timestamp), cost-state as price
oracle only (never a total). New: agents spanning the window edge (clip calls, show "clipped" flag), model switch
within an agent (series segments + per-model rows in tooltip, node `primary_model` = highest cost), concurrent lane
packing for swim-lanes, decimation must preserve peaks and compaction drops, "other" bucket stable across charts,
sessions with zero calls in window omitted, very wide trees (fold by `--min-share`), tooltips when topic is blank.

## 6. Work packages (S<=0.5d, M~1d, L~2-3d) - CLI first, revised
| WP | content | size |
| U1 | loader + requestId dedup + subagent attribution + Node/Call model + tree rollup; fixtures; real-corpus tie-out to cost-state tokens | M |
| U2 | query layer + hierarchical table renderer (sort, depth, min-share, subtotals) + json/csv + registry entry + surface | M |
| U3 | weights file + raw/weighted/USD modes; U3b price fit from cost-state + pricing-page verification | S + M |
| U4 | windows: rejection anchors, 5h model, share-of-window | M |
| C1 | **chart data layer**: series/events/buckets, top-N+other, decimation preserving peaks, lane packing, compaction + model-switch detection, golden JSON | M-L |
| H1 | **HTML renderer** static: tree table + 4 SVG charts, tooltips, print CSS, no-network assertion | L |
| H2 | HTML interactivity: isolate/highlight, zoom/pan/brush, filters, raw/weighted toggle, table<->chart sync | L |
| P1 | **PDF export**: 0.5d feasibility spike (pwmcp pdf vs matplotlib) then implementation behind `render_pdf` | M |
| U5 | anchors + calibration + residuals + weekly (experimental) | M-L |
| U6 | `usage timeline` text/CSV + hook to extract-report rows | S-M |
| U7 | shared weights with jsonl-metrics, docs, CHANGES, E-0xx entry | S |
| U8 | real-corpus opt-in acceptance + privacy assertions | S |
| T0-T3 | TUI (section 7) after C1 | S, M, M, S-M |
Critical path: U1 -> U2 -> U3 -> C1 -> H1 -> H2 -> P1 (U4 needed before share-of-window and chart C's % line). CLI table
usable after U2 (~2.5 d); HTML after H1 (~+5 d); full CLI+HTML+PDF about 14-17 d one implementer, ~10 d with U/C/H
split across two. Each merged change gets an independent adversarial review (standing rule).

### Test strategy (revised)
- Synthetic transcript generator (no real content committed): streaming dup, 5m/1h split, sidechain + meta.json, nested
  depth 2, resumed agent, fork, compaction (with and without compactMetadata), model switch mid-agent, `<synthetic>`
  line, rejection with quotaLimits, truncated last line, missing meta.
- Property tests: dedup idempotent and order independent; subtree totals == sum of descendants + self; sum(top-N +
  other) == total; decimation keeps global max and every compaction drop; lane packing has no overlaps.
- **Golden JSON of the chart data** (`chartdata` for the fixture set, plus one weights profile), reviewed in diffs;
  every renderer test consumes it.
- Table: golden text for tree/sort/depth/min-share; CSV column set.
- **HTML smoke without screenshots**: load via pwmcp (`inspect --format snapshot`/text, or `browser_evaluate`) and
  assert DOM facts: row count and nesting, sorted order after a simulated click, tooltip text on a dispatched hover event,
  isolate toggles an `.isolated` class, filter reduces row count, raw/weighted toggle changes a known cell, zero
  network requests; plus a static scan of the file (no external URLs, size bound, embedded JSON validates against the
  chartdata schema). No pixels are compared.
- **PDF checks**: page count == expected for fixture, `pdftotext`/pypdf text contains header, window, ESTIMATE flag,
  top row topic, and total; fonts embedded; fail if any page is blank. (pypdf/pdftotext used only in tests, test extra.)
- Privacy test: serialise everything (json/csv/html/pdf text) for a fixture whose message contents are sentinel
  strings and assert none appear; `--no-labels` hides topics.
- Real-corpus acceptance stays opt-in and prints aggregates only.

## 7. TUI: second, thin client (unchanged recommendation, re-sequenced)
CLI-first on the shared query + chartdata layer; Textual (8.2.8 installed locally, not a nyxloom dependency) behind
an optional extra `nyxloom[tui]` with a clean error if missing; cli-extended stays stdlib-only, TUI is a launcher verb
`usage tui`, not a grammar route. Since the interactive HTML now covers drill-down, zoom and filter, the TUI's
distinct value is terminal/SSH use and live refresh: T0 Rich `--watch` of the table (S, after U2), T1 Textual
tree table + window/group/weights toggles over the same JSON (M), T2 context sparklines and event list per node
(M), T3 anchor editor + calibration (S-M). Start T1 only after U3/U4 semantics are stable and C1 exists. Re-evaluate
after H2 whether T1-T3 are still wanted.

## 8. Page layout mockup (HTML; PDF uses the same blocks, paginated)
```
+--------------------------------------------------------------------------------------------------+
| nyxloom usage   window: 2026-10-27 04:05Z - 2026-10-28 04:05Z  [5h window: 15:00-20:00Z, reset]    |
| weights: usage-weights.toml#a1b2c3 (UNVERIFIED -> ESTIMATE)   [raw|weighted|USD]  [flat|tree]      |
| filters: model [all v] effort [all v] topic [________] min cost [0]   time: brushed 15:00-18:15    |
+--------------------------------------------------------------------------------------------------+
| TREE TABLE            sort: cost v                                                               |
| topic / agent            model   eff  start-end(dur)   calls peak  in  c-read  out  w5m/w1h  cost share |
| v controller bfe9ee76    sonnet  med  04:05-04:04 24h   541  745k  1k  199M  0.6M  0/2.5M  28.0   13%    |
|   (subtree)                                              6820 745k 34k 1.29G  6.9M  57M/2.5M 241   100%  |
|   v Wave stream: assay   sonnet  med  14:10-17:50 3h40  331  414k  ..  70.8M 0.14M 12M/0  22.8   11%    |
|       > nested agent ... (collapsed)                                                               |
|   > Fable-high review run-gate  fable high 16:02-16:40 38m 261 462k ...                11.5  6%     |
|   > other (97 agents < 1%)                                                                         |
| total                                                                                              |
+--------------------------------------------------------------------------------------------------+
| A. SWIM-LANES (time ->)  |======window=====|reset                                                  |
| session bfe9  ctrl  ___/\__/\___/\___ (peak 745k)  <> compaction                                  |
|   assay        .....__/\___            fable review  ..../'\_                              |
|   pwmcp OCR    ...../''\_  (colour = model, dash = effort)   other (k) lane collapsed         |
+--------------------------------------------------------------------------------------------------+
| B. CONTEXT OVER TIME (session bfe9 + its top-12 agents, shared y, 200k/1M lines)  hover -> tooltip |
|  tok ^ 745k  /\      /\        | tooltip: assay | sonnet | medium | 17:12:03Z | ctx 412k | out 2k |
|      |  /\ /  \   <>/  \__      |          in/cr/cw5m/cw1h | weighted 0.31 | growth +3.1k/min    |
+--------------------------------------------------------------------------------------------------+
| C. WEIGHTED COST / 10 min, stacked by agent group + cumulative % (calibrated, ESTIMATE) | brush ##   |
|  ||||||||||||||||||||||||||||||||||||||||||||||||||  . . . . . . . . . . 96% @18:15 .             |
+--------------------------------------------------------------------------------------------------+
| D. composition per top agent (in | c-read | c-write | out)   footer: coverage, warnings, privacy    |
+--------------------------------------------------------------------------------------------------+
```

## 9. Immediate next steps (revised)
1. Operator: confirm shared-limit surfaces and more percentage readings with times (each is an anchor).
2. Verify prices from the Anthropic pricing page and fit implied prices from `cost-state` before trusting weights.
3. Carve U1-U2 first (raw, de-duplicated, hierarchical table), then C1/H1; run the PDF spike (pwmcp pdf verb vs
   matplotlib extra) at the start of P1; check uPlot/d3 licence + release age only if vanilla JS proves insufficient.
