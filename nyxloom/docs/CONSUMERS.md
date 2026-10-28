# CONSUMERS.md — adopting Nyxloom CLI workflows

Pasteable recipes for the installed Nyxloom command surfaces, including the
per-entry backlog (`nyxloom-trove/backlog/`, one file per issue). Backlog
design authority (WHY it exists, rejected alternatives):
[`backlog-entries-spec.md`](backlog-entries-spec.md).

The [canonical CLI reference](CLI-REFERENCE.md) records every accepted
command, positional, option, default, side effect, and migration from the old
single-command surface. Nyxloom ships `nyxloom`, `nyxloom-harness`,
`nyxloomctl`, and the service-only `nyxloomd` from one wheel.

## Upgrade from Nyxloom 0.10.0

Nyxloom 0.10.0 and the separately installed `cli-extended` wheel both wrote
files under `cli_extended/`. During an upgrade, pip removes the old Nyxloom
files but may keep `cli-extended` marked as satisfied. Complete this one-time
repair from the vbpub checkout root, using the trusted 0.10.1 Nyxloom wheel
and official 0.4.0 `cli-extended` wheel produced by the release workflow:

```bash
python -m pip install ./nyxloom/artifacts/nyxloom-v0.10.1/dist/nyxloom-0.10.1-py3-none-any.whl
python -m pip install --force-reinstall ./libraries/cli-extended/artifacts/cli-extended-v0.4.0/dist/cli_extended-0.4.0-py3-none-any.whl
```

The second command must follow the Nyxloom install so it restores files that
the old Nyxloom wheel may have removed. For later releases, install Nyxloom's
wheel and the matching official `cli-extended` wheel from the vbpub GitHub
Release assets. Do not resolve the bare `cli-extended` project name from
PyPI. The ownership decision is explained in the
[design guide](DESIGN-GUIDE.md#the-cli-extended-wheel-ownership-boundary).

## Operator CLI quick path

Register and inspect a project, lint a handoff, and review a state-repair
plan:

    project_id=my-project
    project_root=/path/to/project
    nyxloom --help
    nyxloomctl project add "$project_id" "$project_root"
    nyxloomctl project list
    nyxloom lint "$project_root/nyxloom-trove/handoffs/P001.md"
    nyxloomctl status --project-id "$project_id"
    nyxloomctl doctor --project-id "$project_id"
    nyxloomctl resync "$project_id"

The last command is a dry-run. Add --apply only after reviewing the proposed
state transitions. The lower-confidence content-merge channel requires both
--apply and --apply-content-merges. Project-level resume separately dry-runs
resync and refuses unresolved drift unless --force is supplied.

## Report Claude Code usage

`nyxloom usage report` scans local Claude Code transcripts and attributes
deduplicated API request tokens to each session and nested subagent. D-022
chooses this additive command spelling; the command uses the pure
`session_extract.usage` service without project registration or daemon state.
Reports are read-only unless `--out FILE` is supplied, which writes the
selected rendering to that file with mode `0600`; missing parent directories
are created and invalid parents produce a clear error. Operator UI
anchors are the narrow local-state exception: they are written only by the
explicit `usage anchor add` command. JSON uses the frozen
`usage-report-v1` schema and contains an exact call ledger, nodes with self and
subtree totals, typed events, lineage edges, conflicts, provenance and coverage
warnings. It excludes message, thinking and tool content and does not serialize
filesystem paths.

`--no-labels` removes session titles and agent topics. `agent_type` remains,
and `project_id` is a stable hash of the recorded cwd, so the same project can
be correlated across reports without exposing its path. Cross-file conflicts
keep one deterministic call marked `conflicted`; its conflict record identifies
the representative.

```sh
# Last 24 hours: raw tokens, experimental weighted units, local share and USD estimate.
nyxloom usage report --since 24h

# Select whole sessions recorded under this working directory and save JSON.
nyxloom usage report --since 7d --project /path/to/project \
  --format json --out usage-7d.json

# Write the exact call ledger as CSV, ordered by time; rank the table by count.
nyxloom usage report --since 24h --format csv --out usage.csv \
  --sort calls

# Hide session titles and agent topic labels in a table report.
nyxloom usage report --since 24h --no-labels

# Export standalone chart data from the same exact ledger.
nyxloom usage charts --since 24h --out usage-charts.json

# Group weighted buckets by model and remove free-text labels before writing.
nyxloom usage charts --since 7d --group-by model --top 12 \
  --no-labels --out anonymous-model-charts.json

# Inspect the dated official API prices and guarded cost-state reconciliation.
nyxloom usage prices --fit --format json

# Compare a named rolling analysis range with observed account reset intervals.
nyxloom usage windows --since 7d --format json

# Record a reading from the account UI; use the exact displayed values.
nyxloom usage anchor add --kind five_hour --at 2026-10-28T18:15:00Z --percent 96 \
  --reset-at 2026-10-28T20:00:00Z --source settings-usage \
  --coverage-assumption local-only-lower-bound
nyxloom usage anchor list
nyxloom usage calibrate --format json
```

`--since` accepts an ISO-8601 timestamp with a timezone or a positive duration
using seconds, minutes, hours, days, or weeks (`s`, `m`, `h`, `d`, `w`). The
report interval is half-open `[since, until)` in UTC: `--since` is included and
`--until` is excluded. A relative start counts backward from `--until` when
present, otherwise from invocation time. Each request uses its own
timezone-aware record timestamp; missing or invalid times are skipped and
reported as coverage diagnostics. The table marks coverage `partial` when
records or attribution are missing; successful stream and copied-prefix merges
remain visible counts without making coverage partial by themselves. `--project` matches recorded `cwd` exactly or
by a unique basename; `--session` selects one exact loaded session ID.

The report labels three separate measures. Raw token units sum input,
cache-read, cache-write 5-minute, cache-write 1-hour, unclassified cache-write,
and output; thinking is a subset of output and is not counted twice. Weighted
units use the configurable `e009-historical` experimental profile. The default
TOML file records exact model IDs, documented token-class prices and its
retrieval date. API-equivalent USD is an estimate from list prices, not a bill
or a Claude subscription usage value. An unknown model, unknown pricing
modifier, or unclassified cache-write leaves that call's USD value null and
adds an explicit unpriced reason. Supply an alternate TOML with `--weights FILE`;
it must keep schema version 1 and cite the official pricing page.

`nyxloom usage prices --fit` uses `cost-state` only as a diagnostic. It returns
`unfit` without coefficients unless the file sequence, transcript-family
scope, monotonicity, cache TTL split, full rank and residual bound all pass.
An unfit result is expected when records lack 5-minute/1-hour counters or do
not match the deduplicated family ledger.

`nyxloom usage windows` shows a trailing five-hour analysis window ending at
`--until`; `--since` selects the history scanned for observed resets and
threshold events. Account intervals are shown only between consecutive
observed reset timestamps: a five-hour session reset and the account's fixed
weekly reset are separate models. Anthropic documents five-hour
session-based resets and an account-assigned weekly reset time, not a rolling
seven-day token sum ([Max plan limits](https://support.claude.com/en/articles/11049741-what-is-the-max-plan)).
This reset description has been verified for Max plans only; semantics for
other plans remain unverified.
A single rejection's reset time does not identify the start or capacity of a
limit window. Rejections are threshold events, never 100% anchors.

Use `usage anchor add` only for a percentage actually read from the account UI.
Record the limit kind, observation timestamp, displayed reset time, UI source,
and coverage assumption. The versioned state is private local metadata at
`$XDG_STATE_HOME/nyxloom/usage-anchors-v1.json` (or
`~/.local/state/nyxloom/usage-anchors-v1.json`); `--anchor-file` can select a
private test or alternate file. `local-only-lower-bound` means web, other
devices and other shared surfaces may have consumed some of the displayed
allowance. Calibration then reports only a local lower bound on capacity and
leaves estimated limit percentage unknown. An all-account fit is conditional
on the operator's explicit `all-account-usage-observed` assumption. API prices
and weighted local shares do not establish subscription usage.

`usage report` and `usage charts` read the default XDG anchor store and accept
`--anchor-file FILE` to select another one. An invalid or unreadable store
produces a warning; the read-only report continues without anchor data.

The table's `ACTIVE SPAN SUM` adds node-local burst spans separated by gaps
over one hour. Subtree rows sum across nodes; with concurrent subagents,
active span can exceed wall duration.

### Export usage chart data

`nyxloom usage charts` writes a private `usage-charts-v1` JSON document. The
same object appears at `extensions.chart_data` in
`nyxloom usage report --format json`; its chart totals, ownership, and call
lookup come from the full windowed tree before `--depth` or `--min-share` folds
the displayed nodes. The report's `calls` array remains the exact ledger.
`--out` is required and creates a mode-0600 file. Use `--no-labels` when chart
labels should omit session titles and subagent topics; the transformation
happens before serialization. Labeled reports can contain sensitive sidecar
descriptions and should stay local.

The chart section includes four datasets: context swim lanes, joined context
lines for the three highest weighted sessions, weighted cost buckets, and
weighted token-class composition for the highest weighted agents. `--top`
defaults to 12. Ranking uses self weighted units and stable ID tie breaks;
remaining stacked groups are combined in one exact `other` group. Supported
`--group-by` values are `agent`, `topic`, `model`, `effort`, and `session`.
Buckets partition each deduplicated call exactly once and never stack a
parent's subtree with its children. Context decimation affects only chart
points; the report ledger is untouched. Endpoints, peaks, compaction/drop
neighbors, and model/effort switches remain represented.

Weighted units are experimental local scores. They do not report account
usage. The chart includes observed reset boundaries without inferring a window
start from one rejection. Its cumulative percentage line stays unknown unless
the existing calibration identifies a complete interval. Under
`local-only-lower-bound` coverage, the line is labeled as local contribution;
it becomes an account estimate only under an explicit all-account coverage
assumption.

```sh
nyxloom usage report --since 24h --format json --out usage-report.json
nyxloom usage report --since 24h --format json --anchor-file ./usage-anchors.json \
  --out anchored-report.json
nyxloom usage charts --since 24h --top 12 --group-by agent \
  --anchor-file ./usage-anchors.json --out usage-charts.json
nyxloom usage charts --since 24h --no-labels --out anonymous-charts.json
```

See the [chart-data design](DESIGN-GUIDE.md#usage-chart-data) for ranking,
partition, decimation, and window semantics.

### Interactive HTML report

Write one private, self-contained file to explore the same ledger and chart
data in a browser. The report works offline; its CSS, JavaScript, SVG charts,
and versioned data are embedded, and the page blocks network requests.

```sh
nyxloom usage report --since 24h --format html --out usage.html
nyxloom usage report --since 7d --format html --no-labels --out anonymous.html
```

Use the model, effort, topic, and time controls to filter the report. The
hierarchical table recomputes exact call totals and subtree subtotals while the
charts update from those calls. Drag across a timeline to zoom, shift-drag to
pan, or use the keyboard-accessible time controls. Hover context points to see
session, agent, topic, model, effort, token counts, and timestamp. Selecting a
table row or chart series highlights that node in both views. Raw token units,
experimental weighted units, and API-equivalent USD remain separate measures.

The anonymize control changes visible labels for the current page but leaves
the originals in the file. `--no-labels` removes free-text topics before
serialization when the file must not contain them. Labeled HTML files may
contain sensitive sidecar descriptions; keep them local and protect the
mode-0600 output. `project_id` remains a stable cwd hash and `agent_type`
remains available after `--no-labels`. Print CSS includes collapsed table rows and works with A4 or
Letter landscape; long tables continue across pages.

### PDF export

PDF output uses the same `usage-report-v1` data and HTML renderer. It sends the
generated page in memory to the managed PWMCP browser through `BrowserLease`,
waits for the report-ready marker, selects print media, and asks Chromium for a
PDF. The output has selectable vector text and crisp SVG charts. A4 landscape
pagination repeats the table header, keeps each row together where it fits, and
flows the complete hierarchy across as many pages as needed.

Install the optional `report` extra from a wheelhouse containing Nyxloom, the
matching PWMCP client wheel, and its declared dependencies. The PWMCP client is
distributed in its verified GitHub release bundle; use its contract so the
client's Playwright version matches the managed browser service.

```sh
python -m pip install --no-index --find-links ./wheelhouse 'nyxloom[report]'
nyxloom usage report --since 24h --format pdf --out usage.pdf
nyxloom usage report --since 7d --format pdf --no-labels --out anonymous.pdf
```

PDF generation requires the managed PWMCP service. If the optional client or
service is unavailable, Nyxloom reports the remedy and writes no output file.
PDF files use mode `0600`. Labeled reports are sensitive metadata artifacts;
`--no-labels` removes free-text labels before the browser receives the page.
PDF generation sends the generated HTML in memory, without a local file URL.

The shipped price table is retrieved 2026-10-28 from
[Anthropic's official pricing documentation](https://platform.claude.com/docs/en/about-claude/pricing).
Rates below are USD per million tokens. The Haiku tier applies to all request
token classes when prompt length exceeds 100,000 tokens.

| Exact model ID | Prompt tier / mode | Input | Output | Cache write 5m | Cache write 1h | Cache read |
|---|---:|---:|---:|---:|---:|---:|
| `claude-fable-5-1` | standard | 10 | 50 | 12.50 | 20 | 0.25 |
| `claude-opus-5-5` | standard | 4 | 20 | 5 | 8 | 0.20 |
| `claude-opus-5-5` | fast | 8 | 40 | 10 | 16 | 0.40 |
| `claude-sonnet-5-5` | standard | 2 | 10 | 2.50 | 4 | 0.10 |
| `claude-haiku-5-5` | prompt ≤100,000 | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |
| `claude-haiku-5-5` | prompt >100,000 | 0.50 | 2.50 | 0.625 | 1 | 0.05 |

For these listed 4.6+ models, documented US inference multiplies every rate by
1.1; `global` is the standard rate. No priority-tier rate is documented in
this profile, so that modifier is left unpriced. The exact profile and its
source metadata ship in `session_extract/usage/usage-weights.toml`.

The table has one session row per selected session, followed by attributed
subagents and nested subagents. Each row includes self totals and a subtree
subtotal; `--depth N` shows levels 0 through N. `--min-share PCT` folds smaller
subtrees or session roots into explicit “other” rows. Use `--flat` for a global
ranked list; it cannot be combined with `--depth`. JSON's `calls` array remains
the exact ledger even when tree presentation is folded or clipped. CSV has one
report metadata row followed by one row per deduplicated call, ordered by
timestamp/session/source/line/call ID; events and tree rollups are available in
JSON and the table, respectively. When copied sources had different request
IDs, `request_ids` retains every distinct alias. `--no-labels` strips
title/topic labels before JSON, CSV or table rendering. Raw token shares are
local observed activity shares, not account limit percentages. Labelled
reports can include sidecar topic text and should be treated as sensitive local
artifacts; `--no-labels` removes those labels before serialization.

The current CLI inventory is generated from Nyxloom's registered command
grammar in [`docs/SPEC.md`](SPEC.md#generated-cli-surface-and-semantic-review)
and [`docs/cli-surface.json`](cli-surface.json). Each public argument shape,
option spelling, closed choice, and minimum route has a reviewed decision in
[`docs/cli-review.toml`](cli-review.toml) linked to a parser probe. After
changing `cli_registry.py`, regenerate and check the inventory with
`cli-extended surface sync` and `cli-extended surface check`. The runtime floor
is `cli-extended>=0.4.0`, which supplies the project-config and semantic-review
contract used here.

See the [usage report design](DESIGN-GUIDE.md#local-usage-report) for the
deduplication, attribution, and privacy choices, and the
[CLI reference](CLI-REFERENCE.md#current-command-and-option-contract) for all
accepted options and output effects.

## Daemon service entrypoint

The service manager starts the foreground daemon with no arguments. Inspect the
installed entrypoint safely with `--help` or `--version`; both exit before the
registry or daemon starts. Unknown arguments are rejected.

```sh
nyxloomd --help
nyxloomd --version
# The service manager invokes this form:
nyxloomd
```

## Closed command choices

These are the exact values consumers may need to type. `nyxloom --help`,
`nyxloom-harness --help`, and `nyxloomctl --help` generate the full option
catalog for the installed version.

| Command option | Accepted values |
|---|---|
| Common `--log-level` | `error`, `warn`, `info`, `debug` |
| `nyxloom onboard --maturity` | `empty`, `partial`, `mature` |
| `nyxloom onboard --docs` | `present`, `absent` |
| `nyxloom onboard --mode` | `derive-from-code`, `code-good-docs-absent`, `greenfield-define-it` |
| `nyxloom usage report --format` | `table`, `json`, `csv`, `html`, `pdf` |
| `nyxloom usage charts --group-by` | `agent`, `topic`, `model`, `effort`, `session` |
| `nyxloom usage report --sort` | `cost`, `start`, `dur`, `calls`, `peak`, `out` |
| `nyxloom usage report --since` relative duration suffixes | `s`, `m`, `h`, `d`, `w` |
| `nyxloom usage prices --format` | `text`, `json` |
| `nyxloom usage windows --format`, `nyxloom usage calibrate --format` | `text`, `json` |
| `nyxloom usage anchor add --kind` | `five_hour`, `weekly` |
| `nyxloom usage anchor add --source` | `settings-usage`, `account-usage-page` |
| `nyxloom usage anchor add --coverage-assumption` | `local-only-lower-bound`, `all-account-usage-observed` |
| `nyxloom backlog new --type` | `feature`, `bugfix` |
| `nyxloom backlog new --severity` | `low`, `medium`, `high` |
| `nyxloom backlog new --context-estimate` | `small`, `medium`, `large` |
| `nyxloom backlog set-status STATUS` | `open`, `carved`, `fixed`, `withdrawn`, `obsolete` (`merged` is written only by merge auto-tick) |
| `nyxloom backlog list --status` | `open`, `carved`, `merged`, `fixed`, `withdrawn`, `obsolete` |
| `nyxloom-harness extract* --format` | `claude-code`, `codex`, `opencode`, `reasonix`; `extract-report` supports `claude-code`, `codex`, `opencode` |
| `nyxloom-harness extract* --profile` | `all`, `operator-review` (`extract` and `extract-debug`) |
| `nyxloom-harness extract* --gap-marker` | `full`, `inline`, `inline2`, `inline-short`, `none` |
| `nyxloom-harness extract* --show-timestamps` | `pre`, `post`, `both`, `none` |
| `nyxloom-harness extract* --extract-metadata` | `pre`, `post`, `both` |
| `nyxloom-harness extract-report --type` | `report-sheet`, `report-detailed`, `csv` |
| `nyxloom-harness extract-sessions --recurse` | `true`, `false` |
| `nyxloom-harness search --sort-by` | `best`, `date` |
| `nyxloom-harness search --client` | `codex`, `claude`, `opencode` |
| `nyxloom-harness search --word-match` | `any`, `all` |
| `nyxloom-harness search --term-match` | `exact`, `prefix` |
| `nyxloom-harness search --progress` | `auto`, `tty`, `plain`, `quiet`, `rawjson` |
| `nyxloomctl intake-bridge poll --transport` | `mmctl`, `rest` |
| `nyxloomctl finding record/list --kind` | `generic`, `model_near_equivalent`, `cost_crossover` |
| `nyxloomctl finding record --severity` | `info`, `note`, `important` |

Use discuss for a copyable discussion route and decide to write a response:

Replace these example IDs and choice text with an existing open decision and
one of its recorded options:

    nyxloomctl discuss "$project_id" D-001
    nyxloomctl decide "$project_id" D-001 --choose option-a --note "Reviewed"

For managed backlog entries, `list` and `show` are read operations.
`backlog list` renders the current listing in memory when `INDEX.md` is absent;
it does not write. Use the explicit generator when you want a committed index:

    nyxloom backlog index --project-id "$project_id"
    nyxloom backlog list --project-id "$project_id"
    nyxloom backlog show NL-001 --project-id "$project_id"

The command effects and safeguards are recorded in the
[CLI reference](CLI-REFERENCE.md#current-command-and-option-contract).

## Adopt in a project (paste-able)

Add to `nyxloom-trove/nyxloom.toml`:

```toml
[backlog_entries]
dir       = "nyxloom-trove/backlog"   # optional; this is the default
id_prefix = "CIU"                     # your project's issue sequence
```

Then install the optional prompt support only when you want the interactive
editor:

```bash
python -m pip install 'nyxloom[interactive]'
```

The regular `nyxloom` commands do not require Questionary. From the project
checkout, lint and create a managed entry either noninteractively or with the
wizard:

### Check the installed CLI

    nyxloom --version

This prints `nyxloom <version>` on stdout and exits 0 without accessing the
project registry or creating Nyxloom state. Help and version are display-only
paths on all three human CLIs.

```bash
nyxloom lint                 # checks this checkout, including BLG2/BLG3
nyxloom backlog new "clean leaves instance-scoped networks" \
    --type bugfix --severity medium --provenance "consumer P111 F4"
nyxloom backlog new --interactive
nyxloom backlog edit CIU-1
nyxloom backlog index        # regenerate backlog/INDEX.md (lint enforces freshness)
```

The interactive create form accepts the same metadata options as the
scriptable form; supplied values seed prompts. Empty optional answers clear
those fields. Edit preserves the entry body and transition-owned metadata,
validates the full candidate frontmatter, then updates the entry and index.

Commit `backlog/` including `INDEX.md` — it is generated, and `nyxloom lint`
fails when it is stale, so never hand-edit it.

Omitting the `[backlog_entries]` table entirely = feature unused; every
backlog-entry lint rule stays silent.

## Extract a session log

`nyxloom-harness extract` and `extract-lossless` consume session logs from Claude Code, Codex,
Reasonix, and opencode. `extract-sessions` discovers Claude Code, Codex, and
opencode families; `extract-report` supports those same three formats. These
verbs are separate from nyxloom's registered-project commands:
`SESSION_LOG` means a session file/store or bare session ID, not a registered
project ID. These commands do not require project registration or daemon
availability.

Run `nyxloom-harness extract --help` to see the controls grouped by source,
window selection, included content, output, redaction/task context, and live
following.

## Search local session history

Search across discovered Claude Code, Codex, and OpenCode stores. The command
matches any query word by default, ranks sessions by the number and rarity of
their matches, and prints session IDs with client, activity date, score,
matched words, and source path. It never prints transcript text:

```bash
nyxloom-harness search debian iso cloud qcow
nyxloom-harness search debian iso cloud qcow --term-match prefix
nyxloom-harness search debian iso cloud qcow --client codex --source-root ~/.codex/sessions --term-match prefix --word-match all
nyxloom-harness search 'cli-extended gate backlog' --sort-by date --client codex --client claude
nyxloom-harness search debian iso cloud qcow --fast --client codex --source-root ~/.codex/sessions
```

`--sort-by best` is the default and puts sessions matching more distinct
query words first; `date` puts the newest recorded activity first.
Relevance scores reward repeats up to 64 occurrences of each query word in a
session; repetitions beyond that do not change its score.
Repeat `--client` to select a list of harnesses. Repeat `--source-root` to
limit the scan to listed locations; each selected client looks for its own
format in each root. Claude Code and Codex use directory roots. OpenCode
accepts an `opencode.db` file or a directory containing that file. Roots with
an incompatible path type are ignored when `--client` is omitted; if clients
are explicitly selected, provide a compatible root for each one. Without
`--source-root`, configured/default harness stores are used. `--word-match all`
requires every query word, and `--term-match prefix` lets `qcow` match `qcow2`;
defaults are `any` and `exact`. Search words are case-insensitive and
punctuation-delimited, so a hyphenated term contributes each component word.
Search reports progress on stderr and creates no index; when ripgrep is
installed, it uses ripgrep to stream candidate records, and otherwise uses a
bounded-memory Python scan. `--progress quiet` suppresses progress. Pass a
result's session ID to `nyxloom-harness extract`; use the printed source path
if same-ID results need disambiguation.

`--fast` is an approximate raw-text mode for ASCII queries against Codex or
Claude JSONL. It requires ripgrep and an explicit `--client codex` and/or
`--client claude`; it refuses OpenCode and the default all-client selection.
It counts whole tokens emitted by ripgrep without parsing candidate transcript
records for matching or reading matched-file tails. Since it scans serialized
JSON, keys and metadata can match, while JSON-escaped query letters or Unicode
case-fold equivalents can be missed. Its date column is `FILE MTIME (UTC)`, a
filesystem proxy.
Session discovery still reads identifying metadata. Use the default mode when
matching only the usual text-bearing fields matters.

Start with a normal compact brief:

```bash
nyxloom-harness extract /path/to/session.jsonl
```

The default `operator-review` profile selects the newest epoch reported by
the source adapter, keeps up to five assistant checkpoints, and stops at
10,000 words. Checkpoints are classifier-scored prose anchors, not safe
compaction boundaries. Claude Code exposes `/clear` epochs; Codex begins a
new rollout after `/clear`, while OpenCode and Reasonix currently expose a
single epoch. To prepare a broad fresh-agent resume with all prose across
available epochs, use `all`:

```bash
nyxloom-harness extract /path/to/session.jsonl --profile all > full-prose.md
```

The `all` profile removes checkpoint, word, time, and compaction stops. It
includes short operator, Q&A, and assistant prose. API transport errors,
thinking content, tool calls, and compaction prompts/summaries remain
controlled by separate options. A detailed Claude Code or Codex inspection
can include tool calls, without printing tool results. `--tool-calls
none|intent|intent-or-call|call` picks the rendering (Claude Code):
`intent` is the call's own description/intent field, `intent-or-call` falls
back to the one-line truncated call when no intent exists, `call` is the
truncated call. `--tool-errors show|hide` (default `show`) renders FAILED tool
results, truncated, independent of that choice. `--show-tool-calls` and
`--show-tool-call-intent` remain as deprecated aliases with their exact prior
output (name labels, optionally with intent):

```bash
nyxloom-harness extract /path/to/session.jsonl --profile all \
  --tool-calls intent-or-call --tool-errors show > full-with-tool-calls.md
```

A failed call always prints its one-line cleaned call before the truncated
error, in every `--tool-calls` mode. Compression is opt-in per option, applied
before truncation: `--strip-cd-prefix`, `--path-aliases auto|none|NAME=/path`,
`--edit-calls show|collapse|omit`, `--read-calls show|collapse`,
`--effect-calls always|mode` (`always` prints an outside-effect Bash command
even in `intent` mode) and `--timestamps all|gaps|none`
(`--timestamp-gap-minutes`, default 5). `--successor-brief` turns on
`--tool-calls intent-or-call --tool-errors show --ledger --strip-cd-prefix
--path-aliases auto --edit-calls collapse --read-calls collapse --effect-calls
always --timestamps gaps`; an explicit flag wins.

`--preset` names four documented bundles of those options (exact expansions in
`extract --help` and `docs/CLI-REFERENCE.md`; an explicit option always wins):
`watch` follows a live session for an operator (operator messages and assistant
prose only, timestamped, coloured on a terminal, interviews kept compactly; `--jsonl` emits
`{v, ts, role, text, agent?}` lines, a stable versioned format for the VS Code extension), `successor` primes a
fresh agent, `review` audits what an agent did at full fidelity, and `ledger`
prints only external effects, touched files and stop state:

```bash
nyxloom-harness extract SESSION_LOG --preset watch --follow
nyxloom-harness extract SESSION_LOG --preset watch --follow --jsonl
```

To prime a FRESH agent from a stopped Claude Code subagent's transcript, one
command emits the original brief, the extract, a whole-session ledger with
external effects, the stop state and the successor's order as one markdown
document (see the `nyxloom-successor` skill):

```bash
nyxloom-harness extract agent-ID.jsonl --successor-brief --order @ORDER.md
```

Codex interactive prompts and replies remain readable in the result. Every
question and its options appear at the prompt's source position, including an
unanswered prompt or one Codex did not copy into assistant prose. Structured
answers appear with the question they answer, even after other session
activity. For example:

```text
INTERVIEW: Which implementation approach should we use?
- Keep the current worktree
- Create a new worktree

OPERATOR: Keep the current worktree

INTERVIEW: What should change if the current branch is stale?

OPERATOR: Reconcile it with main, preserving the current configuration.
```

The question and offered choices come from Codex's UI request record. The
structured answer is linked using that record's question ID; free text is not
rejected for failing to match an option. An ordinary chat response remains
operator prose without an inferred question link. See the
[design rationale](DESIGN-GUIDE.md#codex-question-replies).

Claude Code `AskUserQuestion` prompts use the same visible markers. The
question appears at the assistant tool-call record, even while it is still
unanswered. When a result has a recognized shape, its record repeats the
question for context and labels the user's answer. Rejected question batches
that state `(No answer provided)` keep that status explicit:

```text
INTERVIEW: Which prompt label should be used?
Header: Prompt
- Labeled prose: Mark the displayed question.
- Plain prose: Keep the bare question.

INTERVIEW: Which prompt label should be used?
Header: Prompt
- Labeled prose: Mark the displayed question.
- Plain prose: Keep the bare question.

OPERATOR: Something else

INTERVIEW: Should an unanswered row be explicit?
Header: Missing answer
- Yes: Show that the operator did not answer.
- No: Leave the answer area blank.
Multiple selections are allowed.

OPERATOR: (No answer provided)
```

See the [Claude Code Q&A rationale](DESIGN-GUIDE.md#claude-code-question-replies).

For a specific span, `--epochs` selects a `/clear` epoch or inclusive range;
`--max-compactions` and `--max-time-minutes` add backward-walk stops. A
checkpoint count is an extraction budget, not a semantic compaction point.

```bash
nyxloom-harness extract /path/to/session.jsonl --epochs 2 --max-compactions 1
nyxloom-harness extract /path/to/session.jsonl --epochs 1:2 --max-time-minutes 180
```

If you only have the session ID, pass it directly. Resolution succeeds only
when nyxloom finds exactly one match; otherwise the error lists what must be
disambiguated. To select an adapter explicitly, `--format` accepts
`claude-code`, `codex`, `opencode`, or `reasonix`; omit it for content-based
detection:

```bash
nyxloom-harness extract 019f0890-43a2-75c2-9143-3f8d10ad4484
nyxloom-harness extract ses_04bd4e9b4ffeBJm48T6v130DS6
```

Codex stores rollouts under `CODEX_HOME` (`~/.codex` by default). Bare UUID
lookup also checks local `~/.codex*` profiles, so a UUID copied between
profiles fails closed instead of selecting the default profile's transcript.
Use the full rollout path to disambiguate the one you want:

```bash
codex_home="${CODEX_HOME:-$HOME/.codex}"
session_id=019f0890-43a2-75c2-9143-3f8d10ad4484
session_file=$(find "$codex_home/sessions" -type f \
  -name "rollout-*-$session_id.jsonl" -print -quit)
test -n "$session_file" || {
  printf 'Codex rollout not found for %s\n' "$session_id" >&2
  exit 1
}
nyxloom-harness extract "$session_file"
```

Codex does not reserve IDs separately for each `CODEX_HOME`. New thread IDs
are UUIDv7 values, and the same identity is repeated in the rollout filename
and its first `session_meta` record. A pre-existing filename is opened for
append, so do not symlink live `sessions` directories between independently
authenticated profiles. To identify a live instance, inspect its process
argument (`codex resume <SESSION_ID>` when resumed) and its `CODEX_HOME`, then
corroborate the currently active ID from the first metadata record of the
rollout path held open by `/proc/<pid>/fd`; the command-line ID can be only the
launch target if the interactive process later switches to a new thread.
If the UUID is present in multiple homes, use the full path; renaming the file
alone does not rewrite its embedded identity.

For an opencode database containing more than one session, use the store path
and select the row explicitly:

```bash
nyxloom-harness extract /path/to/opencode.db --opencode-session ses_04bd4e9b4ffeBJm48T6v130DS6
```

If only the opencode ID is available, pass it as the positional session
reference; nyxloom searches its configured/default stores and resolves it when
there is exactly one match. The explicit flag remains useful with a database
path containing multiple sessions.

Choose the output mode for the job at hand. `--render-markdown` is for
reading and consumes Markdown markers. `--highlight` colors Markdown source
while retaining every `#`, `**`, backtick, and dash. A terminal gets
highlighting by default unless `NO_COLOR` is set; `--color` forces ANSI for
piped output, and `--no-color` disables ANSI without changing the render mode:

```bash
nyxloom-harness extract /path/to/session.jsonl --render-markdown --no-color
nyxloom-harness extract-lossless /path/to/session.jsonl --highlight --no-color
```

The text brief defaults to `[HH:mm:ss]` timestamps before each event, with
source metadata and the machine-readable cursor marker at both ends. Source
creation time is reported as unavailable when the filesystem exposes no
creation timestamp. Change timestamp placement/format and metadata placement
independently:

`--show-timestamps` accepts `pre` (default, before the prose), `post` (after
the prose), `both`, or `none`. `--extract-metadata` accepts `pre`, `post`, or
`both` (default). `--gap-marker` accepts `full`, `inline` (default), `inline2`,
`inline-short`, or `none`; `--blank-lines` defaults to `0`.

```bash
nyxloom-harness extract /path/to/session.jsonl --show-timestamps both \
  --timestamp-format '%Y-%m-%d %H:%M' --extract-metadata pre
nyxloom-harness extract /path/to/session.jsonl --show-timestamps none --extract-metadata post
```

`--follow` uses the same timestamp format and appends an updated post cursor
comment after each emitted event so the saved stream can still be passed to
`--since-file`. With a live stream, choose `post` or `both`; `pre` alone is
refused because its cursor would go stale as the file grows.

The cursor comment includes a source marker, for example
`<!-- nyxloom-extract: format=codex marker=166 -->`. Copy the value after
`marker=` for a raw cursor range. `--until` includes its marker. For normal
snapshot chaining, save the prior output and let `--since-file` read it.

Treat the marker as opaque: ordinal-less Codex rollouts use a namespaced
`response_item-<position>` cursor for surfaced UI prompts, while their legacy
event cursors retain numeric values.

```bash
nyxloom-harness extract /path/to/session.jsonl > snapshot.md
# After more activity is written to the same source log:
nyxloom-harness extract /path/to/session.jsonl --since-file snapshot.md > delta.md
# Or pin an exact historical span using marker values from that source:
nyxloom-harness extract /path/to/session.jsonl --since 166 --until 240 > span.md
# Older ordinal-less Codex rollouts can use a UI-prompt cursor directly:
nyxloom-harness extract /path/to/rollout.jsonl --since response_item-23 > delta.md
```

`extract-debug` takes the same profile and selection/rendering flags as
`extract`, then compares that result with the lossless source view. Use it
when a specific message appears inside an inline gap:

```bash
nyxloom-harness extract-debug /path/to/session.jsonl --profile all --no-color | less
```

`extract-report` has three text shapes: `report-sheet` (default) is the
compact overview, `report-detailed` is a readable one-row-per-call table, and
`csv` is for spreadsheet/script ingestion. JSON is available for the two
report shapes. The old `--detailed` spelling selects CSV.

```bash
nyxloom-harness extract-report /path/to/session.jsonl --type report-detailed
nyxloom-harness extract-report /path/to/session.jsonl --type csv > calls.csv
```

Discover sessions under tool-specific environment roots. Directory scanning
recurses by default (`--recurse true` is explicit); `--recurse false` limits
it to direct children.

```bash
nyxloom-harness extract-sessions codex
CODEX_HOME="$HOME/.codex2" nyxloom-harness extract-sessions codex
nyxloom-harness extract-sessions "$HOME/.codex2/sessions" --recurse false
nyxloom-harness extract-sessions opencode
```

Follow a live session after the initial one-shot result. The two verbs keep
their own semantics: `extract` applies its normal selection rules to new
records, while `extract-lossless` prints every new prose/thinking block.

```bash
nyxloom-harness extract-lossless /path/to/session.jsonl --follow --highlight --bell
```

File-backed follow is incremental for large logs: an advancing file reads its
appended payload plus only bounded prefix/tail fingerprints as needed for
rewrite detection. Unchanged polls read no content, and no poll performs a
whole-file rescan.

The incremental tailer treats a path that is absent before its first open as
startup readiness: a later poll can open it after the producer creates it.
Once the stream is active, a disappeared file or a metadata (`stat()`) I/O
error is a terminal failure. The CLI reports `error: followed session file
disappeared while following: PATH` for disappearance, or an `error: cannot
stat followed session file while following PATH: ...` diagnostic for another
metadata failure, and exits 1; consumers must not interpret either case as
“no new content”.

For live redaction, use `extract`: its `--redact-pattern` applies to both the
initial brief and newly streamed phase-two output.

```bash
nyxloom-harness extract /path/to/session.jsonl --follow --redact-pattern 'API_KEY=[^ ]+'
```

`--strip-stale-wakeups` is only a finished-span transform. The exact
combination `extract --follow --strip-stale-wakeups` is rejected before the
initial extraction because a trailing run cannot be finalized while the
session is still growing. `extract-lossless` remains verbatim and rejects
`--redact-pattern`; choose `extract` when live redaction is required.

To run an operator hook when the stream needs attention, use the typed
environment variables supplied by nyxloom. The hook receives one of
`interview_pending`, `checkpoint_detected`, or `long_block` in
`NYXLOOM_ATTENTION_REASON`, plus the harness, session path, and a short
excerpt:

```bash
nyxloom-harness extract /path/to/session.jsonl --follow --attention-min-chars 4000 \
  --on-attention 'printf "%s: %s\n" "$NYXLOOM_ATTENTION_REASON" "$NYXLOOM_ATTENTION_EXCERPT" >&2'
```

`--notify-project PROJECT_ID` can additionally use the `[notify]` channel of
an already registered project. It is opt-in; run `nyxloomctl project list` to
choose the project ID. The notification includes the flagged excerpt, unlike
the ordinary fixed-template nyxloom notifications.

## File a follow-up on an existing entry

Second reproductions, priority bumps, new evidence — `note`, never a new
entry and never a hand-edit:

```bash
nyxloom backlog note CIU-43 "second repro on 6.3.0: volumes leak too"
```

This appends a dated paragraph under `## Updates` in
`CIU-43-clean-leaves-networks.md` and refreshes the index.

## Close an entry

```bash
nyxloom backlog set-status CIU-23 withdrawn --reason "consumer premise disproved"
nyxloom backlog set-status CIU-36 fixed --reason "shipped in ciu-P08"
```

Terminal statuses (`fixed|withdrawn|obsolete`) require `--reason`; the verb
stamps `closed_date`/`closed_reason` in the entry's frontmatter. `merged` is
never hand-set — the merge flow auto-ticks the entry linked by
`carved_handoff`.

## Promote an idea from the inbox

Quick idea in `4-backlog-inbox.md` (spine) or `backlog.md` (plain) outgrew
the inbox? Promote it — the entry is created, the item is removed from the
inbox, and the index is regenerated in one step:

```bash
nyxloom backlog promote B7
```

## Carve from an entry

Set `carved_handoff` in the entry's frontmatter (or let your carve flow do
it); when that handoff merges, the merge auto-tick sets `status=merged` +
`merge_commit`. A `carved` status with a `carved_handoff` link is all the
auto-tick needs.

When recording CLI diagnostics, preserve the first line at every verb depth:
`NYXLOOM <version> — operator CLI`, using the package metadata version exposed
by `nyxloom.__version__`. `nyxloom --version` is exactly one identity line;
normal command output is unchanged.

The broad `tester-unified` lane is Assay-judged with 100% whole-source line and
branch coverage. The `session-extract` lane additionally carries its declared
mutation and canary checks. Invoke both through the project's `run-gate`
entrypoint; local cockpit test results do not certify a release.
